"""cli.py — plcidf（PLC-IDF CLI；曾用名 py2plc，旧命令名保留为等价别名）命令行入口。

子命令与状态（2026-10-16）：
    build    桥式 AutoShop 后端编译管线  —— 管道就绪，默认 dry-run，真执行需双因子
    flash    桥式 AutoShop 后端下载管线  —— 管道就绪，默认 dry-run，真执行需双因子
    monitor  只读 Modbus 监测            —— 真实可用（封装 e521_monitor.py）
    sim      虚拟从站 / 一键闭环演示     —— 真实可用（封装 virtual_easy521.py）
    devices  USB 设备在线状态表          —— 真实可用（usbipd list 只读 interop）

退出码约定：0 成功；1 运行失败（interop/子进程）；2 用法/安全门禁拒绝。
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys

from . import __version__
from .backends.base import OperationRequest
from .backends.bridge_autoshop import BridgeAutoshopBackend
from .backends.monitor_modbus import MonitorModbusBackend
from .backends.sim_virtual import SimVirtualBackend
from .config import load_config
from .device import probe_devices, render_devices_table
from .interop import InteropError
from .safety import (EvidenceLogger, SafetyError, check_bridge_authorization,
                     make_evidence_logger, require_authorization)


# =====================【参数表】=====================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plcidf",
        description="BESS Easy521 工具链：只读监测 / 虚拟从站 / 桥式 AutoShop "
                    "build-flash 管线（默认 dry-run，真执行需双因子授权）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", metavar="FILE",
                        help=".plcidf.toml 路径（默认自动发现，缺省用内置默认值）")
    parser.add_argument("--version", action="version",
                        version=f"plcidf {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="命令")

    def _add_config_option(p: argparse.ArgumentParser) -> None:
        """每个子命令都接受 --config（写在子命令后亦可；缺省继承全局）。"""
        p.add_argument("--config", metavar="FILE", default=argparse.SUPPRESS,
                       help=".plcidf.toml 路径（同全局 --config，位置更自由）")

    # ---- build / flash：桥式管线（默认 dry-run） ----
    for action, help_text in (
            ("build", "编译 AutoShop 工程（桥式后端，默认 dry-run 打印调用链）"),
            ("flash", "下载工程到 PLC（桥式后端，默认 dry-run 打印调用链）")):
        p = sub.add_parser(action, help=help_text,
                           formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        p.add_argument("--project", default=None, metavar="NAME",
                       help="工程名（BESS_E521_DEV/TEST/BENCH；默认取配置 bridge.project）")
        p.add_argument("--execute", action="store_true",
                       help="真执行调用链（默认 dry-run 只打印）")
        p.add_argument("--i-have-authorized", action="store_true",
                       help="操作者当次显式授权；真执行还需配置 bridge.enabled=true"
                            "（双因子，缺一即拒）")
        _add_config_option(p)
        p.set_defaults(handler=_make_bridge_handler(action), action=action)

    # ---- monitor：只读监测透传 ----
    m = sub.add_parser(
        "monitor", help="只读 Modbus 监测（封装 wsl-tools/e521_monitor.py，"
                        "硬编码 FC01/02/03/04）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    m.add_argument("--host", default=None, help="[tcp] PLC 地址")
    m.add_argument("--port", type=int, default=None, help="[tcp] 端口")
    m.add_argument("--transport", choices=("tcp", "serial"), default=None)
    m.add_argument("--device", default=None, help="[serial] 串口路径")
    m.add_argument("--baud", type=int, default=None, help="[serial] 波特率")
    m.add_argument("--unit-id", dest="unit_id", type=int, default=None, help="站号")
    m.add_argument("--interval", type=float, default=None, help="轮询间隔（秒）")
    m.add_argument("--timeout", type=float, default=None, help="单次读超时（秒）")
    m.add_argument("--once", action="store_true", help="只读一轮即退出")
    m.add_argument("--calibrate", action="store_true", help="先做 REAL32 字序判别")
    m.add_argument("--word-order", dest="word_order",
                   choices=("abcd", "badc"), default=None, help="手动指定字序")
    m.add_argument("--with-bits", dest="with_bits", action="store_true",
                   help="同时读 FC01/FC02（只读）")
    m.add_argument("--jsonl", default=None, metavar="FILE", help="解码结果落盘 JSONL")
    m.add_argument("--exec-timeout", dest="exec_timeout", type=float, default=None,
                   help="监测子进程硬超时（秒，--once 建议设；轮询模式不限）")
    m.add_argument("extra", nargs=argparse.REMAINDER, metavar="-- 透传参数",
                   help="其余参数原样透传给 e521_monitor.py（写作: -- 参数）")
    _add_config_option(m)
    m.set_defaults(handler=_cmd_monitor)

    # ---- sim：虚拟从站 / 一键闭环 ----
    s = sub.add_parser(
        "sim", help="起虚拟 Easy521 从站（封装 virtual_easy521.py）；"
                    "--with-monitor 一键闭环演示",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    s.add_argument("--host", default=None, help="监听地址（仅回环）")
    s.add_argument("--port", type=int, default=None,
                   help="监听端口；--with-monitor 缺省自动选空闲端口")
    s.add_argument("--scenario", choices=("steady", "low_voltage", "overload"),
                   default=None, help="播放场景")
    s.add_argument("--word-order", dest="word_order",
                   choices=("abcd", "badc"), default=None, help="REAL32 字序")
    s.add_argument("--tick", type=float, default=None, help="场景刷新节拍（秒）")
    s.add_argument("--hold-dip", dest="hold_dip", type=float, default=None,
                   help="low_voltage 0.91pu 保持时长（秒）")
    s.add_argument("--log", default=None, metavar="FILE", help="从站 JSONL 运行日志")
    s.add_argument("--quiet", action="store_true", help="从站不打印周期状态行")
    s.add_argument("--with-monitor", dest="with_monitor", action="store_true",
                   help="一键闭环：起从站 -> 等就绪 -> 只读监测 -> 收尾")
    s.add_argument("--once", action="store_true",
                   help="监测一轮即收（与 --with-monitor 搭配；缺省则监测持续到 Ctrl+C）")
    s.add_argument("--calibrate", action="store_true",
                   help="闭环监测前先做字序判别")
    s.add_argument("--jsonl", default=None, metavar="FILE",
                   help="[闭环] 监测解码结果落盘 JSONL")
    s.add_argument("--ready-timeout", dest="ready_timeout", type=float,
                   default=None, help="等从站 READY 的上限（秒）")
    _add_config_option(s)
    s.set_defaults(handler=_cmd_sim)

    # ---- devices：USB 设备在线状态 ----
    d = sub.add_parser(
        "devices", help="USB 设备在线状态表（usbipd list 只读 interop + "
                        "/dev/ttyACM* 探测）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    d.add_argument("--json", action="store_true", help="附加机器可读 JSON 输出")
    _add_config_option(d)
    d.set_defaults(handler=_cmd_devices)

    return parser


# =====================【build / flash】=====================

def _make_bridge_handler(action: str):
    """生成 build/flash 共用处理器（同一条门禁与 dry-run 语义）。"""

    def handler(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
        request = OperationRequest(
            project=args.project or cfg.bridge["project"],
            windows_project=cfg.bridge["windows_project"],
            dry_run=not args.execute,
            authorized=args.i_have_authorized,
        )
        decision = check_bridge_authorization(cfg, args.i_have_authorized)
        backend = BridgeAutoshopBackend(cfg, evidence)
        evidence.log(f"bridge.{action}",
                     params={"project": request.project, "argv": sys.argv[1:]},
                     result={"mode": "execute" if not request.dry_run else "dry-run",
                             "authorized": decision.granted,
                             "reasons": decision.reasons})
        if request.dry_run:
            print(backend.render_dry_run(action, request, decision))
            return 0
        # 真执行：双因子门禁（配置 bridge.enabled=true 且 --i-have-authorized）
        require_authorization(decision, f"{action} 真执行")
        print(f"[plcidf] {action}: 门禁通过（双因子满足），开始执行 "
              f"{backend.name} 调用链…")
        result = (backend.execute_build(request) if action == "build"
                  else backend.execute_flash(request))
        print(f"[plcidf] {action} {'完成' if result.ok else '失败'}: {result.message}")
        if result.ok:
            print(f"[plcidf] 执行步骤: {json.dumps(result.outputs.get('executed', []),
                                               ensure_ascii=False)}")
        return 0 if result.ok else 1

    return handler


# =====================【monitor】=====================

_MONITOR_KEYS = ("host", "port", "transport", "device", "baud", "unit_id",
                 "interval", "timeout", "once", "calibrate", "word_order",
                 "with_bits", "jsonl")


def _cmd_monitor(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
    opts = {key: getattr(args, key) for key in _MONITOR_KEYS}
    extra = [a for a in (args.extra or []) if a != "--"]
    if extra:
        opts["extra"] = extra
    backend = MonitorModbusBackend(cfg)
    argv = backend.build_command(opts)      # 组装时过只读闸门
    evidence.log("monitor.run", params={"argv": argv},
                 result={"mode": "standalone"})
    print(f"[plcidf] monitor -> {shlex.join(argv)}", file=sys.stderr)
    try:
        rc = subprocess.run(argv, check=False,
                            timeout=args.exec_timeout).returncode
    except KeyboardInterrupt:
        print("\n[plcidf] monitor 已停止", file=sys.stderr)
        return 0
    except subprocess.TimeoutExpired:
        print(f"[plcidf] monitor 超过 --exec-timeout={args.exec_timeout}s 硬超时，"
              "已终止", file=sys.stderr)
        rc = 124
    evidence.log("monitor.done", params={}, result={"rc": rc})
    return rc


# =====================【sim】=====================

def _cmd_sim(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
    if args.once and not args.with_monitor:
        print("[plcidf] 用法错误: --once 需与 --with-monitor 搭配"
              "（单独起从站请直接运行 plcidf sim）", file=sys.stderr)
        return 2
    sim_opts = {key: getattr(args, key) for key in
                ("host", "port", "scenario", "word_order", "tick", "hold_dip",
                 "log", "quiet", "ready_timeout")}
    backend = SimVirtualBackend(cfg)

    if not args.with_monitor:
        argv = backend.build_command(sim_opts)
        evidence.log("sim.start", params={"argv": argv},
                     result={"mode": "foreground"})
        print(f"[plcidf] sim -> {shlex.join(argv)}", file=sys.stderr)
        try:
            return subprocess.run(argv, check=False).returncode
        except KeyboardInterrupt:
            print("\n[plcidf] sim 已停止", file=sys.stderr)
            return 0

    # 一键闭环：起从站 -> 等就绪 -> 只读监测 -> 收尾
    monitor_opts = {key: getattr(args, key) for key in
                    ("calibrate", "word_order", "jsonl")}
    monitor_opts["once"] = args.once
    print("[plcidf] sim --with-monitor: 起虚拟从站并等待 READY…", file=sys.stderr)
    out = backend.run_with_monitor(sim_opts, monitor_opts, evidence)
    sys.stdout.write(out["monitor_output"])
    rc = out["monitor_rc"]
    state = "OK" if rc == 0 else f"失败 rc={rc}"
    print(f"[plcidf] 闭环结束: 从站 127.0.0.1:{out['sim_port']} 已停止，"
          f"监测一轮 {state}", file=sys.stderr)
    return rc


# =====================【devices】=====================

def _cmd_devices(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
    status = probe_devices(cfg)
    print(render_devices_table(status, busid_expected=cfg.device["busid"]))
    if args.json:
        print(json.dumps({
            "usbipd_ok": status.usbipd_ok,
            "error": status.error,
            "plc": ({"busid": status.plc.busid, "vidpid": status.plc.vidpid,
                     "state": status.plc.state, "attached": status.plc.is_attached}
                    if status.plc else None),
            "tty": status.tty,
            "entries": [{"busid": e.busid, "vidpid": e.vidpid, "state": e.state}
                        for e in status.entries],
        }, ensure_ascii=False, indent=2))
    evidence.log("devices.list",
                 params={"busid": cfg.device["busid"]},
                 result={"usbipd_ok": status.usbipd_ok, "error": status.error,
                         "plc_state": status.plc.state if status.plc else None,
                         "tty": status.tty})
    return 0


# =====================【入口】=====================

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    evidence = make_evidence_logger(cfg)
    try:
        return args.handler(args, cfg, evidence)
    except SafetyError as exc:
        print(f"[plcidf] 拒绝: {exc}", file=sys.stderr)
        return 2
    except InteropError as exc:
        print(f"[plcidf] interop 失败: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"[plcidf] 文件不存在: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
