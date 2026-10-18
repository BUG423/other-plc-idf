"""cli.py — plcidf（PLC-IDF CLI；曾用名 py2plc，旧命令名保留为等价别名）命令行入口。

子命令与状态（2026-10-17）：
    targets  Easy521 / open-linux 目标能力，不探测设备
    toolchain 固定 MatIEC 工具链引导与只读校验
    run/stop open-linux 已部署应用的有限扫描执行和核验停止
    verify   编译本地 ST 并按逐扫描场景自动验收，输出 AI 可读诊断
    build/flash/monitor 支持 --target open-linux，本地原生 ST 闭环

    以下 Easy521 默认入口保留，其 Windows/硬件验证与 open-linux 分别报告：
    build    桥式 AutoShop 后端编译管线  —— 管道就绪，默认 dry-run，真执行需双因子
    flash    桥式 AutoShop 后端下载管线  —— 管道就绪，默认 dry-run，真执行需双因子
    monitor  Modbus / vendor-usb 只读快照 —— 厂商UI不足以证明在线/运行时返回未核验
    sim      虚拟从站 / 一键闭环演示     —— 真实可用（封装 virtual_easy521.py）
    devices  USB 设备在线状态表          —— 真实可用（usbipd list 只读 interop）
    new/init 文本工程脚手架              —— 离线，不生成原生 AutoShop 工程
    check    有限 ST/映射检查             —— 离线，不等于厂商编译
    doctor   环境与能力诊断               —— 仅本地文件/版本，不探测硬件

退出码：0 成功；1 运行失败；2 用法/检查/安全门禁拒绝；124 超时或未就绪；130 用户中断。
"""

from __future__ import annotations

import argparse
import json
import math
import ntpath
import os
import shlex
import subprocess
import sys
from pathlib import Path
from time import monotonic

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
from .open_project import check_any_project, create_open_project
from .targets import BuildRequestV2, get_profile, list_profiles, local_runtime_locator
from .safety import (EvidenceLogger, SafetyError, check_bridge_authorization,
                     make_evidence_logger, require_authorization)


# =====================【参数表】=====================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plcidf",
        description="PLC-IDF 多目标工具链：Easy521 AutoShop 桥接 / "
                    "Linux 原生 ST 编译与本地运行（默认 dry-run）",
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
            ("build", "按目标编译 ST 或 AutoShop 工程（默认 dry-run）"),
            ("flash", "按目标部署本地应用或调用 PLC 下载桥接（默认 dry-run）")):
        p = sub.add_parser(action, help=help_text,
                           formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        p.add_argument("--project", default=None, metavar="NAME",
                       help="工程名（BESS_E521_DEV/TEST/BENCH；默认取配置 bridge.project）")
        p.add_argument("--execute", action="store_true",
                       help="真执行调用链（默认 dry-run 只打印）")
        p.add_argument("--i-have-authorized", action="store_true",
                       help="操作者当次显式授权；旧桥接与原生构建还需 bridge.enabled=true")
        p.add_argument("--json", action="store_true", help="输出版本化 JSON 结果")
        p.add_argument("--target", choices=("easy521", "open-linux"), default="easy521",
                       help="编译/部署目标；本地 Linux 包不适用于 Easy521")
        p.add_argument("--toolchain-dir", metavar="DIR", help="open-linux 工具链目录")
        p.add_argument("--state-dir", metavar="DIR", help="open-linux 本机运行时目录")
        if action == "build":
            p.add_argument("--source", metavar="FILE", help="同步 MAIN 正文并校验真实编译结果（已有工程副本）")
            p.add_argument("--working-project", metavar="WINDOWS_PATH",
                           help="明确的 Windows 工程副本目录，须与配置和当前窗口一致")
            p.add_argument("--manifest", metavar="FILE", help="真实编译通过后保存产物清单")
            p.add_argument("--build-record", metavar="FILE",
                           help="Easy521 真实编译通过后保存完整 verified 编译记录，供原生部署使用")
            p.add_argument("--compile-timeout", type=float, default=None,
                           help="等待真实新产物的上限，秒")
            p.add_argument("--build-dir", metavar="DIR", help="open-linux 构建输出目录")
            p.add_argument("--cycle-ms", type=int, default=None,
                           help="open-linux 扫描周期（需与 ST 的 TASK 声明一致）")
        else:
            p.add_argument("--manifest", metavar="FILE", help="open-linux 构建清单（部署输入）")
            p.add_argument("--build-record", metavar="FILE", help="Easy521 完整 verified 编译记录")
            p.add_argument("--backup-record", metavar="FILE", help="Easy521 PLC 上载备份及哈希记录 JSON")
            p.add_argument("--field-facts", metavar="FILE", help="Easy521 当前供电、输出隔离和物理开关观察记录 JSON")
            p.add_argument("--working-project", metavar="WINDOWS_PATH", help="Easy521 精确的 Windows 工作工程目录")
            p.add_argument("--deploy-timeout", type=float, default=None,
                           help="Easy521 单次下载及独立校验总期限，秒（最长600秒）")
        _add_config_option(p)
        p.set_defaults(handler=_make_bridge_handler(action), action=action)

    # ---- monitor：只读监测透传 ----
    m = sub.add_parser(
        "monitor", help="观测本地 ST、只读 Modbus 或厂商 USB 在线变量表",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    m.add_argument("--host", default=None, help="[tcp] PLC 地址")
    m.add_argument("--port", type=int, default=None, help="[tcp] 端口")
    m.add_argument("--transport", choices=("tcp", "serial", "vendor-usb"), default=None)
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
    m.add_argument("--target", choices=("easy521", "open-linux"), default="easy521")
    m.add_argument("--run-id", help="open-linux 运行实例 ID")
    m.add_argument("--state-dir", help="open-linux 本机运行时目录")
    m.add_argument("--toolchain-dir", help="open-linux 工具链目录")
    m.add_argument("--working-project", help="vendor-usb 精确的 Windows 工程目录；默认配置值")
    m.add_argument("--process-id", type=int, default=None, help="vendor-usb AutoShop PID；0自动选择唯一精确工程")
    m.add_argument("--grid-handle", type=int, default=None, help="vendor-usb 限定当前可见监测表句柄")
    m.add_argument("--json", action="store_true", help="输出结构化运行快照（vendor-usb总是JSON）")
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
        p = sub.add_parser(name, help="创建目标文本工程（Easy521 或 open-linux）")
        p.add_argument("path", nargs=None if name == "new" else "?",
                       default=".", metavar="PATH", help="新建或已有的空目录")
        p.add_argument("--name", default=None, help="工程名；默认使用目录名")
        p.add_argument("--cycle-ms", type=int, default=None,
                       help="MAIN 周期配置意图（毫秒；不代表实测周期）")
        p.add_argument("--target", choices=("easy521", "open-linux"), default="easy521")
        p.add_argument("--template", choices=("counter", "motor"), default=None,
                       help="open-linux 工程模板；motor 包含启停、联锁、定时器及验收场景")
        p.add_argument("--json", action="store_true", help="仅输出版本化 JSON 结果")
        _add_config_option(p)
        p.set_defaults(handler=_cmd_create_project)

    c = sub.add_parser("check", help="离线检查文本工程与映射（有限检查，不执行编译）")
    c.add_argument("path", nargs="?", default=".", metavar="PATH",
                   help="工程目录或 plcidf.json")
    c.add_argument("--json", action="store_true", help="仅输出版本化 JSON 诊断")
    _add_config_option(c)
    c.set_defaults(handler=_cmd_check_project)

    doctor = sub.add_parser("doctor", help="离线依赖、厂商能力与缺失条件诊断（不访问 GUI 或 PLC）")
    doctor.add_argument("--json", action="store_true", help="仅输出版本化 JSON 报告")
    doctor.add_argument("--target", choices=("easy521", "open-linux"), default="easy521")
    doctor.add_argument("--toolchain-dir", help="open-linux 工具链目录")
    doctor.add_argument("--state-dir", help="open-linux 本机运行时目录")
    _add_config_option(doctor)
    doctor.set_defaults(handler=_cmd_doctor)

    sync = sub.add_parser("sync", help="同步 MAIN 文本到已打开的原生工程（默认只读 diff）")
    sync.add_argument("--source", required=True, metavar="FILE", help="UTF-8 MAIN 正文")
    sync.add_argument("--execute", action="store_true", help="同步并保存到配置指定的工程副本")
    sync.add_argument("--i-have-authorized", action="store_true", help="当次明确授权 Windows 工程变更")
    sync.add_argument("--json", action="store_true", help="仅输出版本化 JSON")
    _add_config_option(sync)
    sync.set_defaults(handler=_cmd_sync)

    targets = sub.add_parser("targets", help="列出目标与能力（不探测设备）")
    targets.add_argument("--json", action="store_true")
    _add_config_option(targets)
    targets.set_defaults(handler=_cmd_targets)

    toolchain = sub.add_parser("toolchain", help="检查或构建 open-linux 本地工具链")
    toolchain.add_argument("--target", choices=("open-linux",), default="open-linux")
    toolchain.add_argument("--toolchain-dir", help="工具链目录")
    toolchain.add_argument("--state-dir", help="本机运行时目录")
    toolchain.add_argument("--install", action="store_true", help="下载固定版本并构建到工具链目录")
    toolchain.add_argument("--local-autotools", action="store_true", help="需要时下载/解包 autotools；不安装系统包")
    toolchain.add_argument("--json", action="store_true")
    _add_config_option(toolchain)
    toolchain.set_defaults(handler=_cmd_toolchain)

    verify = sub.add_parser(
        "verify", help="真实编译并逐扫描验收本地 ST 工程（执行于 Linux，无需 PLC）")
    verify.add_argument("path", nargs="?", default=".", metavar="PATH")
    verify.add_argument("--target", choices=("open-linux",), default="open-linux")
    verify.add_argument("--scenarios", help="工程内的场景 JSON；默认 scenarios.json")
    verify.add_argument("--report", help="保存验收报告；默认工程内 .plcidf/verification.json")
    verify.add_argument("--build-dir", help="本地构建目录")
    verify.add_argument("--toolchain-dir", help="open-linux 工具链目录")
    verify.add_argument("--state-dir", help="本机运行时目录")
    verify.add_argument("--timeout", type=float, default=5.0,
                        help="每个场景的执行期限，秒（最长 30 秒）")
    verify.add_argument("--json", action="store_true", help="仅输出结构化验收结果")
    _add_config_option(verify)
    verify.set_defaults(handler=_cmd_verify)

    for command in ("run", "stop"):
        p = sub.add_parser(command, help="open-linux 本机 ST 运行控制")
        p.add_argument("--target", choices=("open-linux",), default="open-linux")
        p.add_argument("--toolchain-dir", help="工具链目录")
        p.add_argument("--state-dir", help="本机运行时目录")
        p.add_argument("--json", action="store_true")
        if command == "run":
            p.add_argument("--manifest", required=True, help="已部署的本地应用清单")
            p.add_argument("--duration", type=float, default=5.0, help="有限运行时间，秒")
            p.add_argument("--sample-ms", type=int, default=100, help="观测采样间隔，毫秒")
        else:
            p.add_argument("--run-id", required=True)
        _add_config_option(p)
        p.set_defaults(handler=_cmd_open_control)

    return parser


# =====================【build / flash】=====================

def _make_bridge_handler(action: str):
    """生成 build/flash 共用处理器（同一条门禁与 dry-run 语义）。"""

    def handler(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
        if args.target == "open-linux":
            return _cmd_open_operation(args, cfg, evidence)
        if args.toolchain_dir is not None or args.state_dir is not None or (action == "build" and
                (args.build_dir is not None or args.cycle_ms is not None)) or (action == "flash" and args.manifest is not None):
            raise ValueError("open-linux-only options cannot be used for an Easy521 operation")
        if action == "build" and args.source is not None:
            if not args.source or any(value == "" for value in (args.manifest, args.build_record)):
                raise ValueError("native source and record paths must be nonempty")
            if args.project is not None:
                raise ValueError("Easy521 --source uses --working-project; --project would be ignored")
            destinations = [Path(value).expanduser().resolve() for value in (args.manifest, args.build_record) if value]
            if len(set(destinations)) != len(destinations) or Path(args.source).expanduser().resolve() in destinations:
                raise ValueError("build records and raw manifest must have distinct paths from the source")
            backend = NativeAutoShop(cfg, evidence=StrictEvidenceLogger(evidence_path(cfg)))
            result = backend.compile(args.source, execute=args.execute,
                                     authorized=args.i_have_authorized,
                                     working_project=args.working_project,
                                     timeout_s=args.compile_timeout if args.compile_timeout is not None else 30.0)
            if result.get("ok") and result.get("manifest") and args.manifest:
                write_record(Path(args.manifest).expanduser(), result["manifest"])
                result["manifest_path"] = str(Path(args.manifest).expanduser().resolve())
            if result.get("ok") is True and result.get("status") == "verified" and args.build_record:
                write_record(Path(args.build_record).expanduser(), result)
                result["build_record_path"] = str(Path(args.build_record).expanduser().resolve())
            result["command"] = "build"
            _emit_operation(args, result)
            return 0 if result["ok"] else 1
        if action == "build" and any(getattr(args, key) is not None for key in
                ("working_project", "manifest", "compile_timeout", "build_record")):
            raise ValueError("Easy521 native build options require --source")
        if action == "flash" and any(getattr(args, key) is not None for key in
                ("build_record", "backup_record", "field_facts", "working_project", "deploy_timeout")):
            return _cmd_native_deploy(args, cfg, evidence)
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


def _read_json_record(path):
    """Read a record as an object; reject duplicate keys and nonfinite numbers."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key in record: " + key)
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("nonfinite JSON number in record: " + value)
    def finite(value):
        number = float(value)
        return number if math.isfinite(number) else invalid(value)
    record = json.loads(Path(path).expanduser().read_text(encoding="utf-8-sig"),
                        object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)
    if not isinstance(record, dict):
        raise ValueError("deployment record must be a JSON object: " + str(path))
    return record


def _cmd_native_deploy(args, cfg, evidence):
    if args.project is not None:
        raise ValueError("Easy521 native flash requires --working-project; --project would be ignored")
    missing = ["--" + key.replace("_", "-") for key in
               ("build_record", "backup_record", "field_facts", "working_project") if not getattr(args, key)]
    if missing:
        raise ValueError("Easy521 native flash requires " + ", ".join(missing))
    timeout = args.deploy_timeout if args.deploy_timeout is not None else 120.0
    if not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ValueError("Easy521 deploy timeout must be finite and in (0, 600] seconds")
    build, backup, facts = (_read_json_record(value) for value in
                            (args.build_record, args.backup_record, args.field_facts))
    from .native_deploy import NativeDeploy
    result = NativeDeploy(cfg, evidence=StrictEvidenceLogger(evidence_path(cfg)),
                          adapter=_vendor_adapter(cfg)).deploy(
        build, working_project=args.working_project, backup=backup, preflight=facts,
        execute=args.execute, authorized=args.i_have_authorized,
        timeout_s=timeout)
    result.update(command="flash", target_profile="easy521")
    _emit_operation(args, result)
    return 0 if result.get("ok") is True else 1


def _vendor_adapter(cfg):
    from .autoshop_vendor import AutoShopVendorAdapter
    return AutoShopVendorAdapter(cfg)


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
    if args.target == "open-linux":
        if not args.run_id:
            raise ValueError("open-linux monitor requires --run-id")
        # Reject Easy521 connection parameters instead of silently dropping them.
        if any(getattr(args, key, None) is not None for key in
               ("host", "port", "transport", "device", "baud", "unit_id", "word_order",
                "interval", "timeout", "jsonl", "project_revision", "exec_timeout",
                "working_project", "process_id", "grid_handle")) \
                or args.once or args.calibrate or args.with_bits or args.extra:
            raise ValueError("Modbus transport/options are not supported by open-linux monitor")
        result = _open_backend(args).monitor(args.run_id)
        return _emit_open(args, result)
    if args.transport == "vendor-usb":
        return _cmd_vendor_usb_monitor(args, cfg, evidence)
    if any(getattr(args, key) is not None for key in ("working_project", "process_id", "grid_handle")):
        raise ValueError("native AutoShop project/process/grid options require --transport vendor-usb")
    if any(value is not None for value in (args.run_id, args.state_dir, args.toolchain_dir)) or args.json:
        raise ValueError("local runtime options require --target open-linux")
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


def _cmd_vendor_usb_monitor(args, cfg, evidence):
    """One bounded read-only native snapshot, with independently queried USB."""
    if any(getattr(args, key) is not None for key in
           ("host", "port", "device", "baud", "unit_id", "word_order", "interval",
            "jsonl", "project_revision", "exec_timeout", "run_id", "state_dir", "toolchain_dir")) \
            or args.calibrate or args.with_bits or args.extra:
        raise ValueError("vendor-usb monitor does not accept Modbus/local-runtime/passthrough options")
    timeout = args.timeout if args.timeout is not None else 20.0
    if not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("vendor-usb timeout must be finite and in (0, 30] seconds")
    process_id, grid_handle = args.process_id or 0, args.grid_handle or 0
    if process_id < 0 or grid_handle < 0:
        raise ValueError("vendor-usb process ID and grid handle must be nonnegative")
    project = args.working_project if args.working_project is not None else cfg.bridge["windows_project"]
    if ntpath.normcase(ntpath.normpath(project)) != ntpath.normcase(ntpath.normpath(cfg.bridge["windows_project"])):
        raise ValueError("vendor-usb working project must match the exact configured project")
    from .autoshop_online import read_online_snapshot
    deadline = monotonic() + timeout
    connection = _vendor_adapter(cfg).observe_connection(timeout_s=timeout)
    if not isinstance(connection, dict):
        raise ValueError("vendor connection observation must be a JSON object")
    connection = dict(connection)
    connection_verified = (connection.get("ok") is True and connection.get("status") == "verified"
                           and connection.get("connection_verified") is True
                           and connection.get("online") is True and connection.get("transport") == "usb"
                           and bool(connection.get("evidence_id")))
    # The production adapter establishes these facts from fresh observations;
    # an online label alone is deliberately insufficient at the CLI boundary.
    if not connection_verified:
        connection["online"] = False
    # A successful UI property read cannot establish USB/device provenance.
    remaining = deadline - monotonic()
    if remaining <= 0:
        connection_verified = False
        result = {"schema_version": 1, "ok": False, "status": "unverified",
                  "values_read": False, "online": False, "plc_state": "unverified",
                  "connection_evidence": connection, "budget_exhausted": True,
                  "errors": ["vendor-usb total time budget exhausted before UI value capture"]}
    else:
        result = read_online_snapshot(expected_project=project, process_id=process_id,
                                      grid_handle=grid_handle, timeout_s=remaining,
                                      powershell_exe=cfg.bridge["powershell_exe"],
                                      connection_evidence=connection)
    result.update(command="monitor", target="easy521", transport="vendor-usb")
    result["connection_verified"] = connection_verified
    evidence.log("monitor.vendor-usb", params={"project": project, "process_id": process_id,
                 "grid_handle": grid_handle}, result=result)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result.get("ok") is True else 1


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
    if args.target == "open-linux":
        return _render_project_result(args, create_open_project(
            args.path, name=args.name,
            cycle_ms=args.cycle_ms if args.cycle_ms is not None else 20,
            template=args.template or "counter"))
    if args.template is not None:
        raise ValueError("--template requires --target open-linux")
    return _render_project_result(args, create_project(
        args.path, name=args.name, cycle_ms=args.cycle_ms if args.cycle_ms is not None else 5))


def _cmd_check_project(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
    return _render_project_result(args, check_any_project(args.path))


def _cmd_doctor(args: argparse.Namespace, cfg, evidence: EvidenceLogger) -> int:
    if args.target == "open-linux":
        result = _open_backend(args).install()
        result.update(command="doctor", target="open-linux", hardware_checked=False,
                      target_profile=get_profile("open-linux").as_dict())
        return _emit_open(args, result)
    if args.toolchain_dir is not None or args.state_dir is not None:
        raise ValueError("local toolchain/runtime paths require --target open-linux")
    report = collect_diagnostics(cfg)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json
          else render_diagnostics(report))
    return 0 if report["ok"] else 1


def _open_paths(args):
    cache = Path.home() / ".cache" / "plcidf"
    toolchain = Path(args.toolchain_dir or os.environ.get(
        "PLCIDF_OPEN_LINUX_TOOLCHAIN", str(cache / "matiec"))).expanduser().resolve()
    state = Path(args.state_dir or os.environ.get(
        "PLCIDF_OPEN_LINUX_STATE", str(cache / "local-runtime"))).expanduser().resolve()
    return toolchain, state


def _open_backend(args):
    from .backends.open_linux import OpenLinuxBackend
    toolchain, state = _open_paths(args)
    return OpenLinuxBackend(toolchain_dir=toolchain, state_dir=state)


def _emit_open(args, result):
    result = dict(result)
    result.setdefault("schema_version", 2)
    result.update(command=args.command, target="open-linux")
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        print(f"[plcidf] {args.command} / open-linux: {result.get('status', 'unknown')}")
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if result.get("ok") is True else 1


def _cmd_open_operation(args, cfg, evidence):
    if args.build_record is not None or args.i_have_authorized or (args.command == "flash" and
            any(getattr(args, key) is not None for key in
                ("backup_record", "field_facts", "working_project", "deploy_timeout"))):
        raise ValueError("Easy521 vendor records/authorization options cannot be used for open-linux")
    profile = get_profile(args.target)
    profile.require(args.command)
    backend = _open_backend(args)
    if args.command == "flash":
        if args.project is not None:
            raise ValueError("open-linux flash uses --manifest, not --project")
        if not args.manifest:
            raise ValueError("open-linux flash requires --manifest")
        result = backend.deploy(_open_manifest_path(args.manifest), execute=args.execute)
        result["locator"] = local_runtime_locator(_open_paths(args)[1])
    else:
        if args.compile_timeout is not None:
            raise ValueError("--compile-timeout is an AutoShop option; open-linux compiler stages have bounded timeouts")
        if args.working_project is not None:
            raise ValueError("open-linux does not use a Windows working project")
        if args.source and args.project:
            raise ValueError("choose --source or --project for open-linux build")
        cycle_ms = args.cycle_ms
        signals = None
        if args.source:
            source = Path(args.source).expanduser().resolve()
            project_root = source.parent
            cycle_ms = 20 if cycle_ms is None else cycle_ms
        else:
            checked = check_any_project(args.project or ".")
            if not checked.ok or not checked.manifest or checked.manifest.get("profile_id") != "open-linux":
                raise ValueError("build requires a valid open-linux project or explicit --source")
            project_root = Path(checked.root)
            source = project_root / checked.manifest["source"]
            declared_cycle = checked.manifest["task"]["cycle_ms"]
            if cycle_ms is not None and cycle_ms != declared_cycle:
                raise ValueError("--cycle-ms conflicts with the project manifest")
            cycle_ms = declared_cycle
            signals = checked.manifest.get("signals")
        request = BuildRequestV2(source=source,
                                 build_dir=Path(args.build_dir).expanduser().resolve()
                                 if args.build_dir else project_root / ".plcidf" / "build",
                                 profile_id=args.target)
        request.validate()
        if not args.execute:
            return _emit_open(args, {"ok": True, "status": "preview", "executed": False,
                                    "source": str(source), "build_dir": str(request.build_dir),
                                    "cycle_ms": cycle_ms, "signals": signals,
                                    "profile": profile.as_dict()})
        build_options = {"cycle_ms": cycle_ms}
        if signals is not None:
            build_options["signals"] = signals
        result = backend.build(source, request.build_dir, **build_options)
        if result.get("ok") and args.manifest:
            # A build bundle's manifest stays with its artifacts. Do not copy it
            # to another directory and accidentally change relative resolution.
            result["manifest_pointer"] = str(Path(args.manifest).expanduser().resolve())
            write_record(args.manifest, {"schema": "plcidf.bundle-pointer.v2",
                                        "manifest_path": result["manifest_path"],
                                        "build_hash": result["build_hash"]})
    return _emit_open(args, result)


def _cmd_verify(args, cfg, evidence):
    from .verification import verify_project
    get_profile(args.target).require("verify")
    given = Path(args.path).expanduser().resolve()
    root = given.parent if given.is_file() else given
    result = verify_project(
        args.path, _open_backend(args), scenarios_path=args.scenarios,
        build_dir=args.build_dir,
        report_path=args.report or root / ".plcidf" / "verification.json",
        timeout_s=args.timeout)
    return _emit_open(args, result)


def _cmd_open_control(args, cfg, evidence):
    get_profile(args.target).require(args.command)
    backend = _open_backend(args)
    if args.command == "run":
        result = backend.run(_open_manifest_path(args.manifest), duration_s=args.duration,
                             sample_interval_ms=args.sample_ms)
    else:
        result = backend.stop(args.run_id)
    return _emit_open(args, result)


def _open_manifest_path(value):
    """Resolve optional CLI build pointers without moving bundle-relative files."""
    path = Path(value).expanduser()
    if path.is_file():
        record = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and record.get("schema") == "plcidf.bundle-pointer.v2":
            if set(record) != {"schema", "manifest_path", "build_hash"}:
                raise ValueError("malformed local bundle pointer")
            if not isinstance(record["manifest_path"], str) or not isinstance(record["build_hash"], str):
                raise ValueError("bundle pointer fields must be strings")
            target = Path(record["manifest_path"])
            if not target.is_absolute():
                raise ValueError("bundle pointer requires an absolute manifest_path")
            actual = json.loads(target.read_text(encoding="utf-8"))
            if not isinstance(actual, dict) or not record["build_hash"] or actual.get("build_hash") != record["build_hash"]:
                raise ValueError("bundle pointer no longer matches the recorded build")
            return str(target)
    return value


def _cmd_targets(args, cfg, evidence):
    result = {"schema_version": 2, "command": "targets", "ok": True,
              "hardware_checked": False, "profiles": list_profiles()}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_toolchain(args, cfg, evidence):
    if args.install:
        from .config import runtime_asset
        script = runtime_asset(cfg.paths.project_root, "tools/bootstrap_open_linux.py")
        argv = [sys.executable, str(script), "--directory", str(_open_paths(args)[0])]
        if args.local_autotools:
            argv.append("--local-autotools")
        print("[plcidf] building pinned MatIEC toolchain in its own directory", file=sys.stderr)
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        if completed.returncode:
            return _emit_open(args, {"ok": False, "status": "failed",
                                    "diagnostics": {"stdout": completed.stdout,
                                                    "stderr": completed.stderr,
                                                    "returncode": completed.returncode}})
    return _emit_open(args, _open_backend(args).install())


def _report_error(args: argparse.Namespace, code: str, message: str, rc: int) -> int:
    if getattr(args, "json", False) or (args.command == "monitor" and
                                       getattr(args, "transport", None) == "vendor-usb"):
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
    except KeyboardInterrupt:
        return _report_error(args, "E_INTERRUPTED", f"{args.command} 已由用户中断", 130)
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
