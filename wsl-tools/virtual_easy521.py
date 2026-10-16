"""virtual_easy521.py — 虚拟汇川 Easy521 Modbus TCP 从站（软件闭环用）。

在 127.0.0.1 上模拟 Easy521 的 Modbus TCP 从站行为，让真机接入前
监测客户端、解析链路、场景语义先在纯软件侧闭环。

与真机对齐的行为（依据手册确认项）：
    - Modbus TCP 从站监听端口（真机默认 502；本工具默认 1502，避免占用特权端口）
    - 不校验站号：任意 device_id 一律应答（pymodbus SimDevice id=0 即此语义）
    - FC03 保持寄存器 0~17 与 D0~D17 一比一映照，REAL32 大端 ABCD/BADC 可选

只读面：
    FC01 线圈 / FC02 离散输入 / FC03 保持寄存器 / FC04 输入寄存器 全部只读
    （SimData readonly=True，任何写功能码一律回异常响应——本项目上位机只读）。

场景播放器（--scenario）：
    steady       正常态：Vpu=1.0 附近缓波纹、SOC 缓降、Pcmd/Qcmd 轻载非零
    low_voltage  低压：Vpu 1.0 -> 0.91（< VON=0.95）保持若干秒后恢复（> VOFF=0.975）
    overload     过载：loading 越限正向（Pline>0）与反向（Pline<0）各保持一段
    Pcmd/Qcmd 由内置"简化演示规则"生成（阈值对齐 modbus_realtime.py 的
    CONTROLLER_P，但不含完整自适应辨识环节），足以观察端到端数据流。

运行日志：--log 输出 JSONL（时间戳 + 场景 + 发出的寄存器快照 + 解码值）。
Ctrl+C 干净退出。

用法示例：
    python virtual_easy521.py --scenario low_voltage
    python virtual_easy521.py --scenario overload --port 1503 --word-order badc
    python virtual_easy521.py --scenario steady --log run.jsonl --quiet
"""

# =====================【导入与路径】=====================

import argparse
import asyncio
import json
import math
import signal
import sys
import threading
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
    encode_quantities,
)

from pymodbus.server import ModbusTcpServer  # noqa: E402
from pymodbus.simulator import SimData, SimDevice  # noqa: E402
from pymodbus.simulator.simdata import DataType  # noqa: E402
from pymodbus.constants import ExcCodes  # noqa: E402

# =====================【算法阈值（对齐 modbus_realtime.py 的 CONTROLLER_P）】=====================

VON = 0.95       # 低压投入阈值：Vpu < VON 进入低压模式
VOFF = 0.975     # 低压退出阈值：Vpu > VOFF 且持续若干周期后退出
VREF = 0.98      # 电压参考标幺值
LON = 1.0        # 过载投入阈值：loading > LON 进入过载模式
LOFF = 0.95      # 过载退出阈值：loading < LOFF 退出
LTARGET = 0.95   # 线路目标负载率
PMAX = 0.8       # 储能有功限值 pu（任务契约：±0.8）
QMAX = 0.4       # 储能无功限值 pu（任务契约：±0.4）

# 简化演示规则的增益（非真实算法参数，只求输出量级合理、方向正确）
GQ_SIM = 5.0     # 低压时 Qcmd ≈ GQ_SIM * (VREF - Vpu)，0.91pu 时约 0.35
GP_SIM = 4.5     # 低压无功饱和切有功时 Pcmd ≈ GP_SIM * (VREF - Vpu)
GLOAD_SIM = 2.0  # 过载时 Pcmd ≈ GLOAD_SIM * (loading - LTARGET)

#: 工作模式编号（与 modbus_realtime.control 的 mode 语义一致）
MODE_NORMAL, MODE_LOWV, MODE_OVER_FWD, MODE_OVER_REV = 0, 1, 2, 3

#: 状态位定义（FC01 线圈与 FC02 离散输入共用同一套语义，便于两个功能码都有真实内容）
COIL_RUN = 0        # 场景播放中
COIL_LOWV = 1       # 低压模式激活
COIL_OVER_FWD = 2   # 正向过载激活
COIL_OVER_REV = 3   # 反向过载激活
COIL_HEARTBEAT = 4  # 心跳：每秒翻转，验证"活"链路
BIT_COUNT = 64      # 线圈/离散输入各给 64 位，覆盖 0~63

#: 寄存器区大小：真机 D0~D7999 一比一映照，多留一页余量
D_REG_COUNT = 8000

#: 虚拟机扩展：D18 = 工作模式（0/1/2/3，与 mode 语义一致），不属于算法契约 0~17
REG_MODE = CONTRACT_REG_COUNT


# =====================【场景曲线】=====================
# 每个场景是 t -> 各量数值的纯函数；t 为本场景循环内的秒数。
# 所有函数返回 (values_dict, mode)；Pcmd/Qcmd 由简化规则统一生成（见 command_rule）。

def _mix(a: float, b: float, k: float) -> float:
    """线性插值：k=0 取 a，k=1 取 b。"""
    return a + (b - a) * min(1.0, max(0.0, k))


def scenario_steady(t: float) -> tuple[dict, int]:
    """① steady：Vpu≈1.0 缓波纹（周期 120s、幅值 0.01，恒 > VOFF，不触发低压）；
    SOC 从 0.80 缓降；线路轻载；Pcmd 给非零基础调度值便于观察数据流。"""
    values = {
        "Vpu": 1.0 + 0.01 * math.sin(2 * math.pi * t / 120.0),
        "Pline": 0.45 + 0.02 * math.sin(2 * math.pi * t / 90.0),
        "Qline": 0.18 + 0.01 * math.sin(2 * math.pi * t / 60.0),
        "loading": 0.48 + 0.02 * math.sin(2 * math.pi * t / 75.0),
        "SOC": max(0.0, 0.80 - 0.0004 * t),
        "Pess": 0.30 + 0.01 * math.sin(2 * math.pi * t / 45.0),
        "Qess": 0.05 + 0.005 * math.sin(2 * math.pi * t / 30.0),
    }
    return values, MODE_NORMAL


def scenario_low_voltage(t: float, hold: float) -> tuple[dict, int]:
    """② low_voltage：1.0(2s) -> 跌至 0.91(1s) -> 保持(默认 10s) -> 恢复 1.0(2s) -> 静默 3s，循环。
    0.91 < VON=0.95 触发低压；恢复到 1.0 > VOFF=0.975 允许退出。"""
    ramp_in, dip, ramp_out, tail = 2.0, 1.0, 2.0, 3.0
    cycle = ramp_in + dip + hold + ramp_out + tail
    t = t % cycle
    if t < ramp_in:
        vpu = 1.0
    elif t < ramp_in + dip:                                # 跌落段
        vpu = _mix(1.0, 0.91, (t - ramp_in) / dip)
    elif t < ramp_in + dip + hold:                         # 保持段
        vpu = 0.91
    elif t < ramp_in + dip + hold + ramp_out:              # 恢复段
        vpu = _mix(0.91, 1.0, (t - ramp_in - dip - hold) / ramp_out)
    else:                                                  # 静默段
        vpu = 1.0
    values = {
        "Vpu": vpu,
        "Pline": 0.42 + 0.01 * math.sin(2 * math.pi * t / 60.0),
        "Qline": 0.20 + 0.01 * math.sin(2 * math.pi * t / 45.0),
        "loading": 0.46 + 0.01 * math.sin(2 * math.pi * t / 50.0),
        "SOC": max(0.0, 0.75 - 0.0004 * t),
        "Pess": 0.0,
        "Qess": 0.0,
    }
    mode = MODE_LOWV if vpu < VON else MODE_NORMAL
    return values, mode


def scenario_overload(t: float) -> tuple[dict, int]:
    """③ overload：轻载(4s) -> 正向过载(8s) -> 轻载(4s) -> 反向过载(8s) -> 轻载(4s)，循环。
    过载判据 loading>LON=1.0，方向由 Pline 符号决定（对齐算法 Pnative 符号逻辑）。"""
    seg = 8.0
    base = 4.0
    cycle = 2 * (base + seg + base)
    t = t % cycle
    if t < base:                                           # 初始轻载
        pline, qline, loading = 0.62, 0.30, 0.85
        mode = MODE_NORMAL
    elif t < base + seg:                                   # 正向过载：loading 至 1.28
        k = (t - base) / seg
        loading = _mix(0.85, 1.28, min(1.0, k * 4))
        pline, qline = math.sqrt(max(loading**2 - 0.36**2, 0.0)), 0.36
        mode = MODE_OVER_FWD if loading > LON else MODE_NORMAL
    elif t < 2 * base + seg:                               # 回到轻载
        pline, qline, loading = 0.62, 0.30, 0.85
        mode = MODE_NORMAL
    else:                                                  # 反向过载：Pline 为负
        k = (t - 2 * base - seg) / seg
        loading = _mix(0.85, 1.22, min(1.0, k * 4))
        pline = -math.sqrt(max(loading**2 - 0.34**2, 0.0))
        qline = 0.34
        mode = MODE_OVER_REV if loading > LON else MODE_NORMAL
    values = {
        "Vpu": 1.0 + 0.006 * math.sin(2 * math.pi * t / 100.0),
        "Pline": pline,
        "Qline": qline,
        "loading": loading,
        "SOC": max(0.0, 0.70 - 0.0004 * t),
        "Pess": 0.0,
        "Qess": 0.0,
    }
    return values, mode


def command_rule(values: dict, mode: int) -> dict:
    """内置简化演示规则：按工作模式生成非零 Pcmd/Qcmd（阈值与限幅对齐算法参数）。

    注意：这是给链路观察用的简化版，不含 modbus_realtime.control 的自适应辨识环节。
    """
    vpu, loading = values["Vpu"], values["loading"]
    if mode == MODE_LOWV and vpu < VON:
        # 低压：无功调压为主（0..QMAX），电压偏差较深时叠加有功支援（简化掉锁存延时）
        qcmd = min(QMAX, max(0.0, GQ_SIM * (VREF - vpu)))
        pcmd = min(PMAX, max(0.0, GP_SIM * (VREF - vpu)))
        # 无功尚有裕度时先只给无功，贴近"无功优先、饱和切有功"的算法骨架
        if qcmd < 0.9 * QMAX:
            pcmd = 0.0
    elif mode == MODE_OVER_FWD and loading > LON:
        # 正向过载：吸收有功把负载率压回 LTARGET
        pcmd = min(PMAX, max(0.0, GLOAD_SIM * (loading - LTARGET)))
        qcmd = 0.0
    elif mode == MODE_OVER_REV and loading > LON:
        # 反向过载：反送有功
        pcmd = -min(PMAX, max(0.0, GLOAD_SIM * (loading - LTARGET)))
        qcmd = 0.0
    else:
        # 正常态：给非零基础调度值，便于观察端到端数据流
        pcmd = 0.30
        qcmd = 0.05
    values["Pcmd"] = pcmd
    values["Qcmd"] = qcmd
    return values


SCENARIOS = {
    "steady": ("① 稳态：Vpu 缓波纹 + SOC 缓降 + 基础调度", scenario_steady),
    "low_voltage": ("② 低压：Vpu 跌至 0.91（<VON=0.95）保持后恢复", scenario_low_voltage),
    "overload": ("③ 过载：loading 越限正向/反向交替", scenario_overload),
}


# =====================【JSONL 运行日志】=====================

class JsonlLogger:
    """JSONL 运行日志：每行一个 JSON 对象，UTF-8，写后即刷。"""

    def __init__(self, path: str | None):
        self._fh = open(path, "a", encoding="utf-8") if path else None

    def write(self, record: dict) -> None:
        if not self._fh:
            return
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


def _now_iso() -> str:
    """当前 UTC 时间 ISO8601 字符串。"""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# =====================【场景播放线程】=====================

class ScenarioPlayer:
    """后台线程：按节拍把场景曲线写入 pymodbus 运行时存储（见 _locate_runtime）。"""

    def __init__(self, args, blocks: dict, logger: JsonlLogger):
        self.args = args
        self.blocks = blocks          # {"h": 活列表, "i": 活列表, "c": 位打包列表, "d": 位打包列表}
        self.logger = logger
        self._stop = threading.Event()
        self._t0 = time.monotonic()
        self._last_log = 0.0
        self.snapshot: dict = {}      # 最近一次发出的解码值（供状态打印/测试参考）

    # ---- 运行时定位 ----
    # pymodbus 3.16 说明：SimData 构建期会把 values 拷贝进 SimRuntime.block，
    # 协议地址 == 块内列表下标（起始地址 0），因此运行期必须直接写"活列表"：
    #   block["h"][2] -> FC03 保持寄存器 uint16 列表
    #   block["i"][2] -> FC04 输入寄存器 uint16 列表（与 FC03 镜像）
    #   block["c"][2] -> FC01 线圈，uint16 位打包槽（线圈 k 在槽 k>>4 的第 k&15 位）
    #   block["d"][2] -> FC02 离散输入，同上线圈
    @staticmethod
    def _locate_runtime(server: "ModbusTcpServer") -> dict:
        device = server.context.devices[0]
        return {
            "h": device.block["h"][2],
            "i": device.block["i"][2],
            "c": device.block["c"][2],
            "d": device.block["d"][2],
        }

    def stop(self) -> None:
        self._stop.set()

    # ---- 位读写助手 ----
    @staticmethod
    def _set_bit(packed: list, index: int, value: bool) -> None:
        slot, off = index >> 4, index & 15
        cur = int(packed[slot]) & 0xFFFF
        packed[slot] = (cur | (1 << off)) if value else (cur & ~(1 << off))

    # ---- 主循环 ----
    def run(self) -> None:
        order = self.args.word_order
        tick = self.args.tick
        names = [q[0] for q in QUANTITIES]
        while not self._stop.is_set():
            loop_t = time.monotonic() - self._t0
            if self.args.scenario == "low_voltage":
                values, mode = scenario_low_voltage(loop_t, self.args.hold_dip)
            elif self.args.scenario == "overload":
                values, mode = scenario_overload(loop_t)
            else:
                values, mode = scenario_steady(loop_t)
            command_rule(values, mode)

            # REAL32 -> 寄存器字（ABCD 或 BADC），写 FC03 并镜像到 FC04
            words = encode_quantities({k: values[k] for k in names}, order)
            words.append(mode)                      # D18 = 工作模式（虚拟机扩展）
            self.blocks["h"][0:len(words)] = words
            self.blocks["i"][0:len(words)] = words

            # 状态位
            hb_slot = time.monotonic_ns() // 1_000_000_000 % 2 == 0
            self._set_bit(self.blocks["c"], COIL_RUN, True)
            self._set_bit(self.blocks["c"], COIL_LOWV, mode == MODE_LOWV)
            self._set_bit(self.blocks["c"], COIL_OVER_FWD, mode == MODE_OVER_FWD)
            self._set_bit(self.blocks["c"], COIL_OVER_REV, mode == MODE_OVER_REV)
            self._set_bit(self.blocks["c"], COIL_HEARTBEAT, hb_slot)
            # 离散输入镜像同一组状态语义（DI0 运行 / DI1 低压 / DI2 正过载 / DI3 反过载）
            self._set_bit(self.blocks["d"], COIL_RUN, True)
            self._set_bit(self.blocks["d"], COIL_LOWV, mode == MODE_LOWV)
            self._set_bit(self.blocks["d"], COIL_OVER_FWD, mode == MODE_OVER_FWD)
            self._set_bit(self.blocks["d"], COIL_OVER_REV, mode == MODE_OVER_REV)

            self.snapshot = dict(values)
            self.snapshot["mode"] = mode

            # JSONL 日志 + 周期状态行（默认 1s 一条，避免刷屏/刷盘过密）
            now = time.monotonic()
            if now - self._last_log >= self.args.log_interval:
                self._last_log = now
                self.logger.write({
                    "ts": _now_iso(),
                    "event": "tick",
                    "scenario": self.args.scenario,
                    "t": round(loop_t, 3),
                    "mode": mode,
                    "word_order": order,
                    "registers": list(words),
                    "values": {k: round(values[k], 6) for k in names},
                })
                if not self.args.quiet:
                    print(f"[t={loop_t:7.1f}s] mode={mode} "
                          + " ".join(f"{k}={values[k]:+.4f}" for k in
                                     ("Vpu", "loading", "SOC", "Pcmd", "Qcmd")))
                    sys.stdout.flush()
            self._stop.wait(tick)


# =====================【服务器装配与主循环】=====================

#: 全部写功能码（FC05/06/15/16/22/23）——只读面一律拒绝
_WRITE_FUNC_CODES = (5, 6, 15, 16, 22, 23)


async def _reject_writes(func_code: int, *_args):
    """SimDevice.action 钩子：写功能码一律回 ILLEGAL_ADDRESS 异常响应。

    pymodbus 3.16 说明：寄存器块 SimData(readonly=True) 能拒绝 FC06/16，
    但位块（FC05/15）的运行时 flags 不含 readonly 标志（库内 __create_block_bits
    的实现缺口），因此统一用 action 钩子兜底拒绝；返回真值即异常响应。
    """
    if func_code in _WRITE_FUNC_CODES:
        return ExcCodes.ILLEGAL_ADDRESS
    return 0  # 读请求放行


def build_device(word_order: str) -> SimDevice:
    """构建只读数据面：FC01/02 位块 + FC03/04 寄存器块。

    双重只读保险：
        1) 寄存器块 readonly=True（库原生支持，FC06/16 被拒）；
        2) SimDevice.action 钩子拒绝一切写功能码（覆盖位块的库缺口）。
    与"本项目上位机对 PLC 只读"的边界一致。
    """
    hr = SimData(0, values=[0] * D_REG_COUNT, datatype=DataType.REGISTERS, readonly=True)
    ir = SimData(0, values=[0] * D_REG_COUNT, datatype=DataType.REGISTERS, readonly=True)
    co = SimData(0, values=[0] * (BIT_COUNT // 16), datatype=DataType.BITS, readonly=True)
    di = SimData(0, values=[0] * (BIT_COUNT // 16), datatype=DataType.BITS, readonly=True)
    # 四元组顺序固定：<线圈, 离散输入, 保持寄存器, 输入寄存器>
    # SimDevice id=0 + SimCore 回退语义 => 任意站号均应答（对齐 Easy521 不校验站号）
    return SimDevice(0, simdata=([co], [di], [hr], [ir]), action=_reject_writes)


async def _serve(args) -> int:
    """装配并运行服务器（必须在本函数所在的事件循环里构造 server，见探针结论）。"""
    device = build_device(args.word_order)
    server = ModbusTcpServer(device, address=(args.host, args.port))

    logger = JsonlLogger(args.log)
    player = None
    try:
        # 先打横幅再起播放线程，避免状态行与 READY 交错
        print(f"[virtual-easy521] 场景: {SCENARIOS[args.scenario][0]}")
        print(f"[virtual-easy521] 字序: {args.word_order.upper()}  节拍: {args.tick}s  "
              f"站号: 不校验（对齐 Easy521）")
        print(f"[virtual-easy521] 监听 {args.host}:{args.port}  "
              f"只读面: FC01/FC02/FC03/FC04（写功能码一律拒绝）")
        print("READY")   # 供脚本/测试探测就绪
        sys.stdout.flush()

        blocks = ScenarioPlayer._locate_runtime(server)
        player = ScenarioPlayer(args, blocks, logger)
        threading.Thread(target=player.run, daemon=True, name="scenario").start()

        await server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        if player:
            player.stop()
        logger.close()   # JSONL 日志收尾（flush 已逐行做，这里只关句柄）
        try:
            await server.shutdown()
        except Exception:
            pass
    return 0


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="virtual_easy521.py",
        description="虚拟汇川 Easy521 Modbus TCP 从站：场景播放 + 只读面 + JSONL 运行日志",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址（约束：仅回环，不碰局域网）")
    parser.add_argument("--port", type=int, default=1502,
                        help="监听端口（真机为 502；这里默认 1502 免特权）")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="steady",
                        help="播放场景")
    parser.add_argument("--word-order", choices=list(WORD_ORDERS), default=WORD_ORDER_ABCD,
                        help="REAL32 字序：abcd=大端直排（契约），badc=字交换（备选）")
    parser.add_argument("--tick", type=float, default=0.1,
                        help="场景刷新节拍（秒）")
    parser.add_argument("--hold-dip", type=float, default=10.0,
                        help="low_voltage 场景 0.91pu 保持时长（秒）")
    parser.add_argument("--log", default=None, metavar="FILE",
                        help="JSONL 运行日志输出文件（时间戳+寄存器快照）")
    parser.add_argument("--log-interval", type=float, default=1.0,
                        help="日志写盘间隔（秒）")
    parser.add_argument("--quiet", action="store_true",
                        help="不打印周期性状态行")
    args = parser.parse_args(argv)
    if args.tick <= 0 or args.log_interval <= 0:
        parser.error("--tick / --log-interval 必须为正数")
    if not (args.host.startswith("127.") or args.host == "localhost"):
        parser.error("安全约束：本工具只允许绑定回环地址（127.x / localhost）")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    state = {"player": None}

    def _handle_stop(signum, frame):   # Ctrl+C / kill -> 优雅停机（finally 收尾日志与从站）
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    try:
        return asyncio.run(_serve(args))
    except KeyboardInterrupt:
        print("\n[virtual-easy521] 收到 Ctrl+C，退出")
        return 0


if __name__ == "__main__":
    sys.exit(main())
