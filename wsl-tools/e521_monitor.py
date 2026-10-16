"""e521_monitor.py — Easy521 只读监测客户端（将来直连真机的工具）。

轮询读取 Easy521 的 Modbus 寄存器契约（FC03 保持寄存器 0~17，REAL32），
终端表格输出 + 可选 JSONL 落盘。这是将来从"虚拟 Easy521"切到真机时唯一
需要直接使用的监测工具。

严格只读（硬边界）：
    本文件全部 Modbus 交互只允许 FC01/FC02/FC03/FC04 读功能码
    （read_coils / read_discrete_inputs / read_holding_registers /
    read_input_registers），不引入任何写调用（FC05/06/15/16 等不出现）。

双传输：
    --transport tcp      Modbus TCP（默认，host+port；真机网口与虚拟从站都走它）
    --transport serial   Modbus RTU over 串口——面向 Easy 系列的 RS485 口，
                         经 USB-RS485 适配器接入 WSL（设备通常是 /dev/ttyUSB0）
                         注意：实测编程 USB 口（CDC 虚拟串口）不应答 RTU，
                         证据见 wsl-probe/usb-rtu/。

字序：
    契约默认 ABCD（大端直排）；官方无记载，BADC（字交换）为备选。
    --calibrate 用有限性/合理范围判据自动判别字序；--word-order 可手动覆盖。

用法示例：
    python e521_monitor.py --host 127.0.0.1 --port 1502            # 连虚拟从站
    python e521_monitor.py --once --calibrate                      # 单次 + 字序判别
    python e521_monitor.py --host 192.168.6.6 --port 502 --calibrate   # 真机首连
    python e521_monitor.py --transport serial --device /dev/ttyACM0    # USB CDC 串口
"""

# =====================【导入与路径】=====================

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# 保证从任意工作目录启动都能找到同目录的契约模块 e521_regs
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e521_regs import (  # noqa: E402
    CONTRACT_REG_COUNT,
    QUANTITIES,
    WORD_ORDER_ABCD,
    WORD_ORDER_BADC,
    WORD_ORDERS,
    calibrate_word_order,
    decode_quantities,
    describe_order,
    in_range,
)

from pymodbus.client import ModbusTcpClient, ModbusSerialClient  # noqa: E402

# =====================【常量】=====================

#: 读功能码超时（秒）
READ_TIMEOUT = 3.0

#: 模式号 -> 含义（虚拟从站写在 D18；真机 D18 未必有效，仅作参考列）
MODE_NAMES = {0: "normal 正常", 1: "low_voltage 低压", 2: "overload-fwd 正过载",
              3: "overload-rev 反过载"}


def _now_iso() -> str:
    """当前 UTC 时间 ISO8601 字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# =====================【客户端构建（只读，两个传输共用读路径）】=====================

def build_client(args):
    """按 --transport 构建 pymodbus 客户端。

    只构建、只用于读；本函数与整个文件都不会调用任何写方法。
    单元测试通过替换本模块的 ModbusSerialClient/ModbusTcpClient 符号注入假客户端。
    """
    if args.transport == "tcp":
        client = ModbusTcpClient(args.host, port=args.port, timeout=args.timeout)
        target = f"tcp://{args.host}:{args.port}"
    else:
        client = ModbusSerialClient(
            port=args.device,
            framer="rtu",            # RTU 帧格式（RS485 / USB-485 适配器场景）
            baudrate=args.baud,
            bytesize=args.bytesize,
            parity=args.parity,
            stopbits=args.stopbits,
            timeout=args.timeout,
        )
        target = f"serial://{args.device}@{args.baud},{args.bytesize}" \
                 f"{args.parity}{args.stopbits}"
    return client, target


def connect_client(client) -> bool:
    """建立连接；失败返回 False（不抛异常，由调用方决定重试/退出）。"""
    try:
        return bool(client.connect())
    except Exception as exc:   # 串口不存在、TCP 拒连等都在这里落地
        print(f"[monitor] 连接失败: {exc}", file=sys.stderr)
        return False


# =====================【只读读取】=====================

def read_contract(client, unit_id: int, with_bits: bool = False) -> dict | None:
    """读一次契约寄存器（FC03 0~17，多读 1 个到 D18 取工作模式）+ 可选位量。

    返回:
        {
          "registers": [19 个 uint16],      # FC03 原始字
          "coils": [...], "discrete": [...],  # 仅 with_bits 时存在
          "error": None,
        }
    失败返回 {"error": 中文原因, ...}。全函数只出现读功能码。
    """
    out: dict = {"error": None}
    rr = client.read_holding_registers(
        address=0, count=CONTRACT_REG_COUNT + 1, device_id=unit_id)
    if rr is None or rr.isError():
        out["error"] = f"FC03 读保持寄存器失败: {rr}"
        return out
    out["registers"] = list(rr.registers)

    if with_bits:
        rc = client.read_coils(address=0, count=8, device_id=unit_id)
        rd = client.read_discrete_inputs(address=0, count=8, device_id=unit_id)
        out["coils"] = list(rc.bits[:8]) if rc is not None and not rc.isError() else None
        out["discrete"] = list(rd.bits[:8]) if rd is not None and not rd.isError() else None
    return out


# =====================【字序校准】=====================

def run_calibration(client, unit_id: int, manual_order: str | None) -> str:
    """读一帧寄存器并做字序判别，打印过程与结论，返回后续会话采用的字序。

    manual_order 非 None 时跳过自动判别（尊重手动覆盖）。
    """
    if manual_order:
        print(f"[calibrate] 使用手动指定字序: {describe_order(manual_order)}（跳过自动判别）")
        return manual_order

    rr = client.read_holding_registers(
        address=0, count=CONTRACT_REG_COUNT, device_id=unit_id)
    if rr is None or rr.isError():
        print(f"[calibrate] FC03 读取失败，无法判别，先按默认 ABCD 继续: {rr}",
              file=sys.stderr)
        return WORD_ORDER_ABCD

    words = list(rr.registers)
    rep = calibrate_word_order(words)

    # 两种字序的逐量对照表
    def _fmt(v: float) -> str:
        """定宽格式化：正常量级用定点，垃圾值（|v|>1e6 或 <1e-4）用科学计数。"""
        if v != 0.0 and (abs(v) >= 1e6 or abs(v) < 1e-4):
            return f"{v:>14.4e}"
        return f"{v:>14.4f}"

    print(f"[calibrate] FC03 原始字 0~17: {words}")
    hdr = f"    {'量名':<10}{'原始字(w0,w1)':<18}{'ABCD 解码':>14}{'BADC 解码':>14}  判定"
    print(hdr)
    for name, off, _cn, _unit, _lo, _hi in QUANTITIES:
        w0, w1 = words[off], words[off + 1]
        va, vb = rep["detail"][name]["abcd"], rep["detail"][name]["badc"]
        oa, ob = rep["detail"][name]["abcd_ok"], rep["detail"][name]["badc_ok"]
        verdict = ("ABCD" if oa and not ob else
                   "BADC" if ob and not oa else
                   "两可" if oa and ob else "均不可信")
        print(f"    {name:<10}{f'({w0:#06x},{w1:#06x})':<18}"
              f"{_fmt(va)}{_fmt(vb)}  {verdict}")
    print(f"[calibrate] 得分: ABCD={rep['scores']['abcd']:.1f}  "
          f"BADC={rep['scores']['badc']:.1f}"
          f"（判据：有限性 + 非微小垃圾值 + 契约合理范围，满分 9.0）")

    if rep["decided"]:
        print(f"[calibrate] 结论：字序判定为 {rep['best'].upper()} —— "
              f"{describe_order(rep['best'])}")
    else:
        print("[calibrate] 结论：两种字序得分并列（寄存器可能全零/初始态），"
              "暂按默认 ABCD 继续；建议等数据活跃后重跑 --calibrate")
    print("[calibrate] 如需强制覆盖，可加 --word-order {abcd,badc}")
    return rep["best"]


# =====================【表格渲染】=====================

def render_table(target: str, unit_id: int, word_order: str, frame: dict) -> str:
    """渲染一次轮询的终端表格：量名 | 寄存器 | 原始字 | 解码值 | 单位 | 范围标记。"""
    words = frame["registers"]
    values = decode_quantities(words, word_order)
    ts = _now_iso()
    lines = [
        f"=== Easy521 监测 @ {target}  unit={unit_id}  {ts}",
        f"--- 字序: {describe_order(word_order)}",
        f"{'量名':<10}{'寄存器':<10}{'原始字(w0,w1)':<20}{'解码值':>12}  "
        f"{'单位':<5}{'范围检查'}",
    ]
    for name, off, cn, unit, _lo, _hi in QUANTITIES:
        w0, w1 = words[off], words[off + 1]
        v = values[name]
        flag = "ok" if in_range(name, v) else "OUT-OF-RANGE!"
        lines.append(f"{name:<10}{f'{off}-{off + 1}':<10}{f'({w0:#06x},{w1:#06x})':<20}"
                     f"{v:>12.5f}  {unit:<5}{flag}  # {cn}")
    if len(words) > CONTRACT_REG_COUNT:
        mode = int(words[CONTRACT_REG_COUNT]) & 0xFFFF
        lines.append(f"{'D18(mode)':<10}{CONTRACT_REG_COUNT:<10}"
                     f"{'-':<20}{mode:>12}  {'':<5}{MODE_NAMES.get(mode, '?')}")
    if frame.get("coils") is not None:
        co = "".join("1" if b else "0" for b in frame["coils"])
        di = "".join("1" if b else "0" for b in (frame.get("discrete") or []))
        lines.append(f"FC01 线圈 0~7 : {co}   (bit0运行 bit1低压 bit2正过载 bit3反过载 bit4心跳)")
        lines.append(f"FC02 离散 0~7 : {di}")
    lines.append(f"FC03 原始字   : {words}")
    return "\n".join(lines)


def jsonl_record(target: str, transport: str, unit_id: int, word_order: str,
                 frame: dict) -> dict:
    """构造一行 JSONL 记录（监测落盘与将来真机采集同一格式）。"""
    values = decode_quantities(frame["registers"], word_order)
    rec = {
        "ts": _now_iso(),
        "transport": transport,
        "target": target,
        "unit_id": unit_id,
        "word_order": word_order,
        "registers": frame["registers"],
        "values": {k: round(v, 6) for k, v in values.items()},
    }
    if frame.get("coils") is not None:
        rec["coils"] = frame["coils"]
    if frame.get("discrete") is not None:
        rec["discrete"] = frame["discrete"]
    return rec


# =====================【主轮询循环】=====================

def poll_loop(args) -> int:
    """主循环：连接 -> （可选校准）-> 轮询 -> 表格/JSONL；--once 单次后退出。"""
    client, target = build_client(args)
    if not connect_client(client):
        print(f"[monitor] 无法连接 {target}，退出（--once 模式返回码 1）", file=sys.stderr)
        return 1

    word_order = args.word_order or WORD_ORDER_ABCD
    exit_code = 0
    jsonl_fh = open(args.jsonl, "a", encoding="utf-8") if args.jsonl else None
    try:
        if args.word_order:
            # 手动指定优先：跳过自动判别
            word_order = args.word_order
            print(f"[monitor] 字序（手动指定，跳过判别）: {describe_order(word_order)}")
        elif args.calibrate:
            word_order = run_calibration(client, args.unit_id, manual_order=None)
        else:
            word_order = WORD_ORDER_ABCD
            print("[monitor] 字序: 默认 ABCD（未指定 --calibrate；首次接真机建议先跑 --calibrate）")

        seq = 0
        while True:
            frame = read_contract(client, args.unit_id, with_bits=args.with_bits)
            if frame["error"]:
                print(f"[monitor] 第 {seq} 轮读取失败: {frame['error']}", file=sys.stderr)
                if args.once:
                    exit_code = 1
                    break
            else:
                print(render_table(target, args.unit_id, word_order, frame))
                if jsonl_fh:
                    jsonl_fh.write(json.dumps(jsonl_record(
                        target, args.transport, args.unit_id, word_order, frame),
                        ensure_ascii=False) + "\n")
                    jsonl_fh.flush()
                if args.once:
                    break
            seq += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[monitor] 收到 Ctrl+C，退出")
    finally:
        if jsonl_fh:
            jsonl_fh.close()
        try:
            client.close()
        except Exception:
            pass
    return exit_code


# =====================【命令行】=====================

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="e521_monitor.py",
        description="Easy521 只读监测客户端：FC01/02/03/04 只读，REAL32 解码，"
                    "字序自动判别，TCP / 串口（RTU）双传输",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--transport", choices=("tcp", "serial"), default="tcp",
                        help="传输方式：tcp=Modbus TCP；serial=Modbus RTU over 串口"
                             "（RS485 经 USB-485 适配器，如 /dev/ttyUSB0）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="[tcp] PLC/虚拟从站地址；真机即 PLC 的 IP")
    parser.add_argument("--port", type=int, default=1502,
                        help="[tcp] 端口；虚拟从站默认 1502，真机 Easy521 为 502")
    parser.add_argument("--device", default="/dev/ttyUSB0", metavar="PATH",
                        help="[serial] 串口设备路径（USB-RS485 适配器常见为 /dev/ttyUSB0）")
    parser.add_argument("--baud", type=int, default=9600,
                        help="[serial] 波特率")
    parser.add_argument("--bytesize", type=int, choices=(7, 8), default=8,
                        help="[serial] 数据位")
    parser.add_argument("--parity", choices=("N", "E", "O"), default="N",
                        help="[serial] 校验位")
    parser.add_argument("--stopbits", type=float, choices=(1, 1.5, 2), default=1,
                        help="[serial] 停止位")
    parser.add_argument("--unit-id", type=int, default=1,
                        help="站号（Easy521 不校验站号，任意值均可）")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="轮询间隔（秒）")
    parser.add_argument("--timeout", type=float, default=READ_TIMEOUT,
                        help="单次读超时（秒）")
    parser.add_argument("--once", action="store_true",
                        help="只读一轮就退出（演示/冒烟用）")
    parser.add_argument("--calibrate", action="store_true",
                        help="启动时先做 REAL32 字序自动判别（两种字序都解，打印结论）")
    parser.add_argument("--word-order", choices=list(WORD_ORDERS), default=None,
                        help="手动指定字序，跳过自动判别（默认自动/ABCD）")
    parser.add_argument("--with-bits", action="store_true",
                        help="同时读 FC01 线圈与 FC02 离散输入（只读）")
    parser.add_argument("--jsonl", default=None, metavar="FILE",
                        help="把每轮解码结果追加写入 JSONL 文件")
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval 必须为正数")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    return poll_loop(args)


if __name__ == "__main__":
    sys.exit(main())
