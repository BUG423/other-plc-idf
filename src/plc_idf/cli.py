"""cli.py — plcidf（PLC-IDF CLI；曾用名 py2plc，旧命令名保留为等价别名）命令行入口。

子命令与状态（2026-10-16）：
    build    桥式 AutoShop 后端编译管线  —— 管道就绪，默认 dry-run，真执行需双因子
    flash    桥式 AutoShop 后端下载管线  —— 管道就绪，默认 dry-run，真执行需双因子
    monitor  只读 Modbus 监测            —— 真实可用（封装 e521_monitor.py）
    sim      虚拟从站 / 一键闭环演示     —— 真实可用（封装 virtual_easy521.py）
    devices  USB 设备在线状态表          —— 真实可用（usbipd list 只读 interop）
    new/init 文本工程脚手架              —— 离线，不生成原生 AutoShop 工程
    check    有限 ST/映射检查             —— 离线，不等于厂商编译
    doctor   环境与能力诊断               —— 仅本地文件/版本，不探测硬件

退出码：0 成功；1 运行失败；2 用法/检查/安全门禁拒绝；124 超时或未就绪。
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from . import __version__
from .backends.base import OperationRequest
from .backends.bridge_autoshop import BridgeAutoshopBackend
from .backends.monitor_modbus import MonitorModbusBackend
from .backends.sim_virtual import SimVirtualBackend
from .config import load_config
from .config import evidence_path
from .artifacts import write_record
from .evidence import StrictEvidenceLogger
from .lease import LeaseError
from .native_autoshop import NativeAutoShop
from .device import probe_devices, render_devices_table
from .doctor import collect_diagnostics, render_diagnostics
from .interop import InteropError
from .project import check_project, create_project
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
        p.add_argument("--json", action="store_true", help="输出版本化 JSON 结果")
        if action == "build":
            p.add_argument("--source", metavar="FILE", help="同步 MAIN 正文并校验真实编译结果（已有工程副本）")
            p.add_argument("--working-project", metavar="WINDOWS_PATH",
                           help="明确的 Windows 工程副本目录，须与配置和当前窗口一致")
            p.add_argument("--manifest", metavar="FILE", help="真实编译通过后保存产物清单")
            p.add_argument("--compile-timeout", type=float, default=30.0,
                           help="等待真实新产物的上限，秒")
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
    m.add_argument("--calibrate", action="store_true", default=None, help="先做 REAL32 字序判别")
    m.add_argument("--project-revision", default=None, help="操作者声明的源码版本（不代表设备程序匹配）")
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
    s.add_argument("--calibrate", action="store_true", default=None,
                   help="闭环监测前先做字序判别")
    s.add_argument("--jsonl", default=None, metavar="FILE",
                   help="[闭环] 监测解码结果落盘 JSONL")
    s.add_argument("--project-revision", default=None, help="操作者声明的源码版本")
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

    # ---- new / init / check：可移植文本工程（离线） ----
    for name in ("new", "init"):
        p = sub.add_parser(name, help="创建内存变量示例文本工程（不生成原生 .hcp）")
        p.add_argument("path", nargs=None if name == "new" else "?",
                       default=".", metavar="PATH", help="新建或已有的空目录")
        p.add_argument("--name", default=None, help="工程名；默认使用目录名")
        p.add_argument("--cycle-ms", type=int, default=5,
                       help="MAIN 周期配置意图（毫秒；不代表实测周期）")
        p.add_argument("--json", action="store_true", help="仅输出版本化 JSON 结果")
        _add_config_option(p)
        p.set_defaults(handler=_cmd_create_project)

    c = sub.add_parser("check", help="离线检查文本工程与映射（有限检查，不执行编译）")
    c.add_argument("path", nargs="?", default=".", metavar="PATH",
                   help="工程目录或 plcidf.json")
    c.add_argument("--json", action="store_true", help="仅输出版本化 JSON 诊断")
    _add_config_option(c)
    c.set_defaults(handler=_cmd_check_project)

    doctor = sub.add_parser("doctor", help="离线环境诊断（不访问 Windows GUI 或 PLC）")
    doctor.add_argument("--json", action="store_true", help="仅输出版本化 JSON 报告")
    _add_config_option(doctor)
    doctor.set_defaults(handler=_cmd_doctor)

    sync = sub.add_parser("sync", help="同步 MAIN 文本到已打开的原生工程（默认只读 diff）")
    sync.add_argument("--source", required=True, metavar="FILE", help="UTF-8 MAIN 正文")
    sync.add_argument("--execute", action="store_true", help="同步并保存到配置指定的工程副本")
    sync.add_argument("--i-have-authorized", action="store_true", help="当次明确授权 Windows 工程变更")
    sync.add_argument("--json", action="store_true", help="仅输出版本化 JSON")
    _add_config_option(sync)
    sync.set_defaults(handler=_cmd_sync)

    return parser


# =====================【build / flash】=====================

def _make_bridge_handler(action: str):
    """生成 build/flash 共用处理器（同一条门禁与 dry-run 语义）。"""

    def handler(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
        if action == "build" and args.source:
            backend = NativeAutoShop(cfg, evidence=StrictEvidenceLogger(evidence_path(cfg)))
            result = backend.compile(args.source, execute=args.execute,
                                     authorized=args.i_have_authorized,
                                     working_project=args.working_project,
                                     timeout_s=args.compile_timeout)
            if result.get("ok") and result.get("manifest") and args.manifest:
                write_record(args.manifest, result["manifest"])
                result["manifest_path"] = str(Path(args.manifest).resolve())
            result["command"] = "build"
            _emit_operation(args, result)
            return 0 if result["ok"] else 1
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
            plan = backend.render_dry_run(action, request, decision)
            if args.json:
                print(json.dumps({"schema_version": 1, "command": action,
                                  "ok": True, "executed": False, "status": "preview",
                                  "plan": plan}, ensure_ascii=False))
            else:
                print(plan)
            return 0
        # 真执行：双因子门禁（配置 bridge.enabled=true 且 --i-have-authorized）
        require_authorization(decision, f"{action} 真执行")
        print(f"[plcidf] {action}: 门禁通过（双因子满足），开始执行 "
              f"{backend.name} 调用链…", file=sys.stderr if args.json else sys.stdout)
        result = (backend.execute_build(request) if action == "build"
                  else backend.execute_flash(request))
        status = ("调用链已完成，结果未核验"
                  if result.outputs.get("verification_status") == "unverified"
                  else ("完成" if result.ok else "失败"))
        if args.json:
            print(json.dumps({"schema_version": 1, "command": action,
                              "ok": result.ok, "message": result.message,
                              "status": result.outputs.get("verification_status", "failed"),
                              "outputs": result.outputs}, ensure_ascii=False))
            return 0 if result.ok else 1
        print(f"[plcidf] {action} {status}: {result.message}")
        if result.ok:
            print(f"[plcidf] 执行步骤: {json.dumps(result.outputs.get('executed', []),
                                               ensure_ascii=False)}")
        return 0 if result.ok else 1

    return handler


def _emit_operation(args, result):
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"[plcidf] {args.command}: {result['status']}")
        diff = result.get("diff") or result.get("sync", {}).get("diff")
        if diff:
            print(diff)
        diagnostics = result.get("diagnostics")
        if diagnostics and diagnostics.get("text"):
            print(diagnostics["text"])
        if result.get("manifest"):
            print(f"[plcidf] build_hash={result['manifest']['build_hash']}")


def _cmd_sync(args, cfg, evidence):
    result = NativeAutoShop(cfg, evidence=StrictEvidenceLogger(evidence_path(cfg))).sync(
        args.source, execute=args.execute, authorized=args.i_have_authorized)
    result["command"] = "sync"
    _emit_operation(args, result)
    return 0 if result["ok"] else 1


# =====================【monitor】=====================

_MONITOR_KEYS = ("host", "port", "transport", "device", "baud", "unit_id",
                 "interval", "timeout", "once", "calibrate", "word_order",
                 "with_bits", "jsonl", "project_revision")


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
                    ("calibrate", "word_order", "jsonl", "project_revision")}
    monitor_opts["once"] = args.once
    print("[plcidf] sim --with-monitor: 起虚拟从站并等待 READY…", file=sys.stderr)
    try:
        out = backend.run_with_monitor(sim_opts, monitor_opts, evidence)
    except KeyboardInterrupt:
        print("\n[plcidf] sim 已停止", file=sys.stderr)
        return 0
    sys.stdout.write(out["monitor_output"])
    rc = out["monitor_rc"]
    state = "OK" if rc == 0 else f"失败 rc={rc}"
    print(f"[plcidf] 闭环结束: 从站 127.0.0.1:{out['sim_port']} 已停止，"
          f"监测一轮 {state}", file=sys.stderr)
    return rc


# =====================【devices】=====================

def _cmd_devices(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
    status = probe_devices(cfg)
    if args.json:
        print(json.dumps({
            "schema_version": 1,
            "command": "devices",
            "usbipd_ok": status.usbipd_ok,
            "error": status.error,
            "plc": ({"busid": status.plc.busid, "vidpid": status.plc.vidpid,
                     "state": status.plc.state, "attached": status.plc.is_attached}
                    if status.plc else None),
            "tty": status.tty,
            "entries": [{"busid": e.busid, "vidpid": e.vidpid, "state": e.state}
                        for e in status.entries],
        }, ensure_ascii=False, indent=2))
    else:
        print(render_devices_table(status, busid_expected=cfg.device["busid"]))
    evidence.log("devices.list",
                 params={"busid": cfg.device["busid"]},
                 result={"usbipd_ok": status.usbipd_ok, "error": status.error,
                         "plc_state": status.plc.state if status.plc else None,
                         "tty": status.tty})
    return 0


# =====================【离线工程与环境诊断】=====================

def _render_project_result(args: argparse.Namespace, result) -> int:
    report = result.as_dict()
    report["command"] = args.command
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        verb = "已创建文本工程" if args.command in {"new", "init"} else "离线检查"
        print(f"[plcidf] {verb}: {result.root}（{'通过' if result.ok else '失败'}）")
        for item in result.diagnostics:
            print(f"{item.path}:{item.line}:{item.column}: {item.severity} "
                  f"{item.code}: {item.message}")
        print("[plcidf] 有限静态检查；厂商编译、原生工程同步与运行周期需单独验证。",
              file=sys.stderr)
    return 0 if result.ok else 2


def _cmd_create_project(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
    return _render_project_result(args, create_project(
        args.path, name=args.name, cycle_ms=args.cycle_ms))


def _cmd_check_project(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
    return _render_project_result(args, check_project(args.path))


def _cmd_doctor(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
    report = collect_diagnostics(cfg)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json
          else render_diagnostics(report))
    return 0 if report["ok"] else 1


def _report_error(args: argparse.Namespace, code: str, message: str, rc: int) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"schema_version": 1, "command": args.command, "ok": False,
                          "diagnostics": [{"code": code, "message": message,
                                           "severity": "error"}]}, ensure_ascii=False))
    else:
        print(f"[plcidf] {message}", file=sys.stderr)
    return rc


# =====================【入口】=====================

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        evidence = make_evidence_logger(cfg)
        return args.handler(args, cfg, evidence)
    except SafetyError as exc:
        return _report_error(args, "E_REFUSED", f"拒绝: {exc}", 2)
    except LeaseError as exc:
        return _report_error(args, "E_BUSY", f"资源租约失败: {exc}", 6)
    except InteropError as exc:
        return _report_error(args, "E_INTEROP", f"interop 失败: {exc}", 1)
    except FileNotFoundError as exc:
        return _report_error(args, "E_FILE", f"文件不存在: {exc}", 1)
    except subprocess.TimeoutExpired as exc:
        return _report_error(args, "E_TIMEOUT", f"{args.command} 操作超时（{exc.timeout}s），已停止等待", 124)
    except TimeoutError as exc:
        return _report_error(args, "E_NOT_READY", f"{args.command} 等待失败或未就绪: {exc}", 124)
    except ValueError as exc:
        return _report_error(args, "E_ARGUMENT", f"参数或配置无效: {exc}", 2)
    except OSError as exc:
        return _report_error(args, "E_IO", f"文件或进程操作失败: {exc}", 1)


if __name__ == "__main__":
    sys.exit(main())
