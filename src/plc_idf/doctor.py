"""Offline environment and capability diagnostics using only the standard library.

No subprocesses, sockets, USB enumeration, Windows GUI or PLC access. A file
being present is a prerequisite, never proof that a tool or device works.
"""

from __future__ import annotations

from importlib import metadata
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import sys

from . import __version__
from .backends.bridge_autoshop import BridgeAutoshopBackend
from .config import ROOT_MARKER, runtime_asset


def _local_path(value: str) -> Path | None:
    """Resolve standard WSL drive mounts and this distro's own UNC paths."""
    windows = PureWindowsPath(value)
    if windows.drive.startswith("\\\\"):
        share = windows.drive.lstrip("\\").split("\\")
        distro = os.environ.get("WSL_DISTRO_NAME", "Ubuntu")
        if len(share) == 2 and share[0].lower() in {"wsl.localhost", "wsl$"} \
                and share[1].casefold() == distro.casefold():
            return Path("/", *windows.parts[1:])
        return None
    if len(windows.drive) == 2 and windows.drive[1] == ":":
        return Path("/mnt", windows.drive[0].lower(), *windows.parts[1:])
    return Path(value).expanduser()


def _file(value: str | Path) -> dict:
    configured = str(value)
    local = _local_path(configured)
    exists = local.is_file() if local is not None else None
    return {"configured_path": configured,
            "local_path": str(local) if local is not None else None,
            "exists": exists,
            "status": ("not-locally-resolvable" if exists is None else
                       "present" if exists else "missing")}


def _dependency(name: str, expected: str) -> dict:
    try:
        installed = metadata.version(name)
    except metadata.PackageNotFoundError:
        installed = None
    return {"expected": expected, "installed": installed,
            "ok": installed == expected}


def collect_diagnostics(cfg) -> dict:
    """Return a v1 report of local prerequisites and explicitly bounded support."""
    package = Path(__file__).resolve().parent
    root = Path(cfg.paths.project_root)
    editable = False
    distribution_version = None
    try:
        dist = metadata.distribution("plc-idf")
        distribution_version = dist.version
        direct_url = json.loads(dist.read_text("direct_url.json") or "{}")
        editable = direct_url.get("dir_info", {}).get("editable") is True
    except (metadata.PackageNotFoundError, ValueError, OSError):
        pass
    mode = ("editable" if editable else
            "wheel" if (package / "_resources").is_dir() else
            "source-checkout" if (package.parents[1] / ROOT_MARKER).is_file()
            else "unknown")
    tools = Path(cfg.paths.wsl_tools)
    resources = {
        name: _file(tools / name)
        for name in ("e521_monitor.py", "virtual_easy521.py", "e521_regs.py")
    }
    resources["autoshop_ui.ps1"] = _file(
        runtime_asset(root, "plc/tools/autoshop_ui.ps1"))
    dependencies = {"pymodbus": _dependency("pymodbus", "3.16.1"),
                    "pyserial": _dependency("pyserial", "3.5")}
    windows = {
        "powershell": _file(cfg.bridge.powershell_exe),
        "autoshop": _file(cfg.bridge.autoshop_exe),
        "usbipd": _file(cfg.device.usbipd_exe),
        "bridge_script": _file(cfg.bridge.tools_ps1_windows),
    }
    python_supported = sys.version_info >= (3, 12)
    runtime_ready = python_supported and all(value["ok"] for value in dependencies.values())
    monitor_ready = runtime_ready and all(resources[name]["exists"] is True for name
                                         in ("e521_monitor.py", "e521_regs.py"))
    sim_ready = monitor_ready and resources["virtual_easy521.py"]["exists"] is True
    windows_files_present = all(value["exists"] is True for value in windows.values())
    build_verified = getattr(BridgeAutoshopBackend, "build_verification_supported", False) is True
    flash_verified = getattr(BridgeAutoshopBackend, "flash_verification_supported", False) is True
    return {
        "schema_version": 1, "command": "doctor", "ok": monitor_ready and sim_ready,
        "validation": "offline-prerequisites-only", "hardware_checked": False,
        "environment": {"python": platform.python_version(), "executable": sys.executable,
                        "python_supported": python_supported,
                        "platform": platform.system(),
                        "wsl_distro": os.environ.get("WSL_DISTRO_NAME")},
        "distribution": {"mode": mode, "version": __version__,
                         "installed_metadata_version": distribution_version,
                         "package_directory": str(package)},
        "workspace": str(root), "config": str(cfg.source) if cfg.source else None,
        "dependencies": dependencies, "resources": resources, "windows": windows,
        "capabilities": {
            "project": {"scaffold": True, "offline_check": True,
                        "complete_st_compiler": False,
                        "native_project_creation": False},
            "monitor": {"available": monitor_ready, "transports": ["tcp", "serial"],
                        "read_function_codes": [1, 2, 3, 4],
                        "variable_debugger": False, "program_identity_verified": False},
            "sim": {"available": sim_ready,
                    "scenarios": ["steady", "low_voltage", "overload"],
                    "executes_user_st": False},
            "build": {"backend": "bridge_autoshop",
                      "windows_files_present": windows_files_present,
                      "main_working_copy_verification_supported": True,
                      "verified_command": "build --source FILE --working-project WINDOWS_PATH",
                      "scope": "Existing AutoShop MAIN working project; no variable import or native project creation",
                      "result_verification_supported": build_verified,
                      "verification_status": "supported-by-backend" if build_verified else "not-implemented"},
            "flash": {"backend": "bridge_autoshop",
                      "windows_files_present": windows_files_present,
                      "result_verification_supported": flash_verified,
                      "verification_status": "supported-by-backend" if flash_verified else "not-implemented"},
        },
        "limitations": [
            "No Windows commands, GUI actions, network or USB probes were run.",
            "File presence does not verify tool versions, connectivity or PLC identity.",
            "Simulator plays fixed BESS scenarios and does not execute project ST.",
            "Offline project checks do not prove vendor compilation or scan timing.",
        ],
    }


def render_diagnostics(report: dict) -> str:
    lines = [f"PLC-IDF doctor: {'OK' if report['ok'] else '缺少运行条件'}（仅离线检查）",
             f"安装: {report['distribution']['mode']}  Python {report['environment']['python']}",
             f"工作目录: {report['workspace']}"]
    for name, value in report["dependencies"].items():
        lines.append(f"依赖 {name}: {value['installed'] or '缺失'}（要求 {value['expected']}）")
    for name, value in report["resources"].items():
        lines.append(f"资源 {name}: {value['status']}")
    for name, value in report["windows"].items():
        lines.append(f"Windows {name}: {value['status']}")
    for name in ("build", "flash"):
        value = report["capabilities"][name]["result_verification_supported"]
        lines.append(f"{name} 结果校验: {'后端支持，需逐次实测' if value else '未实现'}")
    lines.extend(["monitor: 只读寄存器采集；未检查真机连通性或程序身份。",
                  "sim: 固定 BESS 场景播放器，不执行用户 ST。"])
    return "\n".join(lines)
