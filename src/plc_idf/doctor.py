"""Offline environment and capability diagnostics using only the standard library.

No subprocesses, sockets, USB enumeration, Windows GUI or PLC access. A file
being present is a prerequisite, never proof that a tool or device works.
"""

from __future__ import annotations

from importlib import metadata
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import struct
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
    try:
        exists = local.is_file() if local is not None else None
    except OSError as exc:
        return {"configured_path": configured, "local_path": str(local), "exists": None,
                "status": "unverified", "reason": str(exc)}
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


def _hash_file(info: dict, *, maximum=32 * 1024 * 1024) -> dict:
    """Hash a bounded local file; never load it as executable code."""
    if info["exists"] is not True:
        return {"status": info["status"], "sha256": None}
    path = Path(info["local_path"])
    try:
        before = path.stat()
        if before.st_size > maximum:
            raise ValueError("file exceeds the offline hash size limit")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            total = 0
            for block in iter(lambda: stream.read(min(1024 * 1024, maximum + 1 - total)), b""):
                total += len(block)
                if total > maximum:
                    raise ValueError("file grew beyond offline hash size limit")
                digest.update(block)
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("file changed during offline inspection")
        return {"status": "read", "sha256": digest.hexdigest(), "bytes": before.st_size}
    except (OSError, ValueError) as exc:
        return {"status": "unverified", "sha256": None, "reason": str(exc)}


def _pe_file_version(info: dict) -> dict:
    """Read bounded PE RT_VERSION metadata, without running Windows tools.

    Disk metadata describes that file, never the currently running process or
    the PLC firmware. Missing, malformed and conflicting resources stay unknown.
    """
    result = {"status": "unverified", "file_version": None,
              "source": "on-disk PE RT_VERSION/VS_FIXEDFILEINFO", "process_version_verified": False}
    if info["exists"] is not True:
        return {**result, "reason": info["status"]}
    try:
        path = Path(info["local_path"])
        before = path.stat()
        with path.open("rb") as stream:
            def read(offset, size):
                if offset < 0 or not 0 <= size <= 65536 or offset + size > before.st_size:
                    raise ValueError("PE metadata range is invalid")
                stream.seek(offset)
                value = stream.read(size)
                if len(value) != size:
                    raise ValueError("PE metadata is truncated")
                return value
            dos = read(0, 64)
            if dos[:2] != b"MZ":
                raise ValueError("file is not a PE executable")
            pe = struct.unpack_from("<I", dos, 60)[0]
            header = read(pe, 24)
            if header[:4] != b"PE\0\0":
                raise ValueError("PE signature is absent")
            count, optional_size = struct.unpack_from("<H", header, 6)[0], struct.unpack_from("<H", header, 20)[0]
            if not 1 <= count <= 96 or optional_size > 4096:
                raise ValueError("PE header sizes are unsupported")
            optional = read(pe + 24, optional_size)
            magic = struct.unpack_from("<H", optional)[0]
            directory = {0x10B: 96, 0x20B: 112}.get(magic)
            if (directory is None or len(optional) < directory + 24
                    or struct.unpack_from("<I", optional, directory - 4)[0] < 3):
                raise ValueError("PE data directory is unsupported")
            resource_rva, resource_size = struct.unpack_from("<II", optional, directory + 16)
            if not resource_rva or resource_size < 16:
                raise ValueError("PE has no version resource")
            sections = read(pe + 24 + optional_size, count * 40)
            def file_offset(rva, size):
                for offset in range(0, len(sections), 40):
                    virtual, raw_size, raw = struct.unpack_from("<III", sections, offset + 12)
                    delta = rva - virtual
                    if 0 <= delta and delta + size <= raw_size:
                        return raw + delta
                raise ValueError("PE resource does not map to file bytes")
            base = file_offset(resource_rva, resource_size)
            budget = 256
            def resource(offset, size):
                if offset < 0 or offset + size > resource_size:
                    raise ValueError("PE resource tree escapes its directory")
                return read(base + offset, size)
            def entries(offset):
                nonlocal budget
                record = resource(offset, 16)
                count = sum(struct.unpack_from("<HH", record, 12))
                budget -= count
                if budget < 0:
                    raise ValueError("PE resource tree is too large")
                data = resource(offset + 16, count * 8)
                return [struct.unpack_from("<II", data, index * 8) for index in range(count)]
            version_roots = [child for name, child in entries(0) if name == 16 and child & 0x80000000]
            if len(version_roots) != 1:
                raise ValueError("PE version resource is absent or ambiguous")
            versions = set()
            for _, name_dir in entries(version_roots[0] & 0x7fffffff):
                if not name_dir & 0x80000000:
                    raise ValueError("PE version language directory is invalid")
                for _, data_offset in entries(name_dir & 0x7fffffff):
                    if data_offset & 0x80000000:
                        raise ValueError("PE version data entry is invalid")
                    data_rva, size = struct.unpack_from("<II", resource(data_offset, 16))
                    if not 92 <= size <= 65536:
                        raise ValueError("PE version value size is invalid")
                    blob = read(file_offset(data_rva, size), size)
                    length, value_length, kind = struct.unpack_from("<HHH", blob)
                    key = "VS_VERSION_INFO\0".encode("utf-16-le")
                    fixed = (6 + len(key) + 3) & ~3
                    if (blob[6:6 + len(key)] != key or kind != 0 or value_length != 52
                            or not fixed + 52 <= length <= len(blob)):
                        raise ValueError("PE fixed version metadata is invalid")
                    signature, structure, high, low = struct.unpack_from("<IIII", blob, fixed)
                    if signature != 0xFEEF04BD or structure != 0x10000:
                        raise ValueError("PE fixed version signature is invalid")
                    versions.add(".".join(map(str, (high >> 16, high & 65535, low >> 16, low & 65535))))
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("PE file changed during offline inspection")
        if len(versions) != 1:
            raise ValueError("PE versions are absent or conflicting")
        return {**result, "status": "read", "file_version": versions.pop()}
    except (OSError, ValueError, struct.error) as exc:
        return {**result, "reason": str(exc)}


def _native_diagnostics(cfg, resources, windows) -> dict:
    """Describe implemented paths and unmet evidence, never historical results."""
    from .autoshop_vendor import AutoShopVendorAdapter
    adapter = AutoShopVendorAdapter(cfg).describe()
    package = Path(__file__).resolve().parent
    modules = {name: _file(package / (name + ".py")) for name in
               ("native_autoshop", "native_deploy", "native_release", "autoshop_vendor", "autoshop_online")}
    version = _pe_file_version(windows["autoshop"])
    expected_version = adapter.get("version")
    version["expected_ui_title_version"] = expected_version
    version["compatibility_verified"] = False
    version["note"] = "Disk FileVersion and UI title versions can differ; the active calibrated UI still needs its own check"
    worker = resources["compile_vendor_st.ps1"]
    compilers = []
    if worker["exists"] is True:
        try:
            path = Path(worker["local_path"])
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("compiler helper exceeds metadata inspection limit")
            source = path.read_text(encoding="utf-8-sig")
            pins = re.findall(r"'([A-Za-z]:\\[^']*\\StLang\.exe)'\s*=\s*'([0-9a-f]{64})'", source)
            for compiler, expected_hash in pins:
                info = _file(compiler)
                actual = _hash_file(info)
                compilers.append({**info, **actual, "expected_sha256": expected_hash,
                                  "hash_matches": actual["sha256"] == expected_hash,
                                  "kernel": _file(str(PureWindowsPath(compiler).parent / "Kernel.dll"))})
        except (OSError, UnicodeError, ValueError) as exc:
            compilers.append({"status": "unverified", "hash_matches": False, "reason": str(exc)})
    for info in resources.values():
        if info["configured_path"].endswith((".ps1", "prepare_vendor_st.py", "compile_vendor_st.py")):
            info["integrity"] = _hash_file(info)
    project_path = _local_path(cfg.bridge["windows_project"])
    try:
        project_exists = project_path.is_dir() if project_path is not None else None
    except OSError:
        project_exists = None
    common = {"powershell": windows["powershell"]["exists"] is True,
              "configured_ui_script": windows["bridge_script"]["exists"] is True,
              "autoshop_executable": windows["autoshop"]["exists"] is True}

    def capability(implemented, scope, checks, conditions, command=None, **extra):
        missing = [{"code": key, "status": "missing", "message": "Local prerequisite is unavailable: " + key}
                   for key, present in checks.items() if present is not True]
        missing.extend({"code": key, "status": state, "message": message}
                       for key, state, message in conditions)
        result = {"implemented": implemented, "scope": scope,
                  "status": "not-implemented" if not implemented else
                            "missing-local-prerequisites" if not all(checks.values()) else "requires-observation",
                  "local_prerequisites": {"ok": all(checks.values()), "checks": checks},
                  "current_verification": "not-observed", "missing_conditions": missing,
                  "executed_by_doctor": False, **extra}
        if command is not None:
            result["next_command"] = command
        return result

    project = cfg.bridge["windows_project"]
    source_command = ["plcidf", "build", "--source", "MAIN_body.st", "--working-project", project,
                      "--build-record", "build-record.json", "--json"]
    values_command = ["plcidf", "monitor", "--transport", "vendor-usb", "--working-project", project,
                      "--once", "--timeout", "10", "--json"]
    object_ready = any(item.get("hash_matches") is True and item.get("kernel", {}).get("exists") is True
                       for item in compilers)
    parent = PureWindowsPath(project).parent
    input_windows = str(parent / "plcidf-st-input-NEW")
    input_local = _local_path(input_windows)
    prepared_command = [sys.executable, resources["prepare_vendor_st.py"]["local_path"] or "prepare_vendor_st.py",
                        "--source", "MAIN_body.st", "--globals", "globals.json", "--output-dir",
                        str(input_local) if input_local is not None else "NEW_VENDOR_INPUT_DIRECTORY"]
    chosen_compiler = next((item["configured_path"] for item in compilers if item.get("hash_matches")),
                           r"E:\Software\AutoShop\Bin\STLang_New\StLang.exe")
    object_command = [sys.executable, resources["compile_vendor_st.py"]["local_path"] or "compile_vendor_st.py",
                      "--compiler", chosen_compiler, "--input-dir", input_windows,
                      "--build-dir", str(parent / "plcidf-object-NEW"), "--timeout-s", "4",
                      "--powershell-exe", cfg.bridge["powershell_exe"]]
    capabilities = {
        "source_build": capability(True, "MAIN body into an existing vendor project and a complete vendor application",
            {**common, "native_autoshop_module": modules["native_autoshop"]["exists"] is True,
             "working_project_directory": project_exists is True},
            [("active_project_main", "not-observed", "The exact working project must be open with a clean writable MAIN editor"),
             ("fresh_vendor_build", "not-observed", "Fresh required application artifacts, vendor zero-error diagnostics and source hashes must agree")],
            source_command, creates_native_project=False, imports_variables=False, gui_required=True),
        "vendor_object_only": capability(True, "ST body/container and explicit globals to one ARM relocatable MAIN.o; no application link or download",
            {"powershell": common["powershell"], "object_helper": worker["exists"] is True,
             "prepare_python": resources["prepare_vendor_st.py"]["exists"] is True,
             "compile_python": resources["compile_vendor_st.py"]["exists"] is True,
             "pinned_compiler_and_kernel": object_ready},
            [("supported_input_subset", "not-observed", "The preparer must accept the body and explicit global definitions; it does not import a complete AutoShop project"),
             ("fresh_object_build", "not-observed", "Use a new output directory and verify all diagnostics, IPC completion, input hashes and ARM MAIN entry")],
            gui_required=False, windows_compiler_required=True, complete_application=False),
        "download": capability(True, "Single native application download with recorded build, backup and field facts",
            {**common, "usbipd": windows["usbipd"]["exists"] is True,
             "native_deploy_module": modules["native_deploy"]["exists"] is True},
            [("current_target_and_layout", "not-observed", "Exact USB identity, active project, vendor version and desktop layout must match"),
             ("verified_records", "not-observed", "A current verified build record, PLC upload backup hashes and field facts are required"),
             ("operation_result", "not-observed", "Fresh vendor completion must bind this operation's prepared local release; the retained snapshot must still verify")],
            auto_run=False, current_download_verified=False),
        "source_consistency": capability(True, "Vendor project/source comparison; this is not an independent device binary hash",
            {**common, "vendor_adapter_module": modules["autoshop_vendor"]["exists"] is True},
            [("fresh_source_comparison", "not-observed", "A new vendor project comparison must match the frozen intent and retained release snapshot; vendor recompilation does not prove device byte equality")],
            device_binary_identity_verified=False),
        "device_binary_identity": capability(False, "Independently bind device executable bytes/hash to the selected build",
            {}, [("device_executable_readback", "not-implemented", "No calibrated executable-byte upload or device application hash reader is implemented")]),
        "runtime": capability(True, "Parse observed RUN/STOP bits and check probe scan/heartbeat samples",
            {"native_reader": resources["read_autoshop_online.ps1"]["exists"] is True,
             "online_parser_module": modules["autoshop_online"]["exists"] is True},
            [("current_power_and_mode", "not-observed", "Current power and physical mode facts were not collected by doctor"),
             ("live_session_identity", "not-observed", "A live device identity must accompany current runtime observations"),
             ("scan_progress_and_program_binding", "not-observed", "Fresh coherent scan/heartbeat progression and matching program identity are required")],
            control_implemented=False, cpu_execution_verified=False, real_time_verified=False),
        "monitor_vendor_usb": capability(True, "Bounded MSAA online-table values with separate connection/runtime quality",
            {"powershell": common["powershell"], "usbipd": windows["usbipd"]["exists"] is True,
             "native_reader": resources["read_autoshop_online.ps1"]["exists"] is True},
            [("online_device_identity", "not-implemented" if adapter.get("capabilities", {}).get("connection_identity") is not True else "not-observed",
              "USB presence, readable values and an online label do not independently identify the live vendor device session"),
             ("visible_values_and_state", "not-observed", "The exact project must expose readable current values; missing M8000/M8001 leaves runtime state unverified")],
            values_command, connection_verified=False, runtime_verified=False),
    }
    capabilities["vendor_object_only"].update(
        prepare_argv=prepared_command, compile_argv=object_command,
        globals_schema={"schema": "plcidf.vendor-main-source.v1", "globals": {"COUNTER": "DINT"}},
        example_body="COUNTER := COUNTER + 1;",
        output_directory_rule="Replace NEW with a fresh name; each output directory must not already exist",
        supported_explicit_global_types=["BOOL", "INT", "DINT", "REAL"])
    capabilities["download"]["prepared_release"] = {
        "implemented": (adapter.get("capabilities", {}).get("prepared_release") is True
                        and modules["native_release"]["exists"] is True),
        "scope": "Track a locally frozen intent and candidate application snapshot with hashes",
        "current_verification": "not-observed", "release_hash": None,
        "device_binary_binding": "unverified"}
    return {"schema": "plcidf.easy521-capability-diagnostics.v1", "target": "easy521",
            "scope": "local-files-and-implementation-metadata-only", "current_device": "not-observed",
            "verification_levels": {"local_candidate": "not-observed", "project": "not-observed",
                                    "binary": "unverified", "runtime": "unverified"},
            "modules": modules, "working_project": {"configured_path": project,
                "local_path": str(project_path) if project_path is not None else None,
                "directory_exists": project_exists, "active_window_verified": False},
            "vendor": {"adapter_metadata": adapter, "on_disk_autoshop": version,
                       "running_autoshop_version": None, "plc_firmware_version": None,
                       "current_layout_verified": False,
                       "required_layout": adapter.get("comparison_layout"),
                       "object_compilers": compilers},
            "capabilities": capabilities,
            "next_steps": [{"id": "review_missing_conditions", "action": "Inspect each capability's missing_conditions; local readiness is not device verification"},
                           {"id": "prepare_vendor_object_input", "argv": prepared_command, "starts_download": False,
                            "purpose": "Prepare the restricted body and explicit globals locally; output is intermediate input, not a PLC application"},
                           {"id": "compile_vendor_object", "argv": object_command, "starts_download": False,
                            "purpose": "Optional Windows host-only object compilation after input preparation; no GUI or PLC transport"},
                           {"id": "preview_source_build", "argv": source_command, "starts_download": False,
                            "purpose": "Review an existing working project's MAIN change; preview may read Windows UI"},
                           {"id": "read_current_values", "argv": values_command, "starts_download": False,
                            "purpose": "Request one bounded read-only snapshot; inspect values_read, connection_verified and plc_state separately"},
                           {"id": "binary_and_runtime_gap", "action": "Establish documented executable readback and fresh scan evidence before claiming program-bound execution"}],
            "device_actions_performed": [], "historical_results_used": False}


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
    for name in ("autoshop_ui.ps1", "read_autoshop_online.ps1", "compile_vendor_st.ps1"):
        resources[name] = _file(runtime_asset(root, "plc/tools/" + name))
    for name in ("prepare_vendor_st.py", "compile_vendor_st.py"):
        resources[name] = _file(runtime_asset(root, "tools/" + name))
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
    native = _native_diagnostics(cfg, resources, windows)
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
        "health_scope": "local-monitor-and-simulator-prerequisites",
        "easy521": native,
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
            "build": {"backend": "native_autoshop",
                      "windows_files_present": windows_files_present,
                      "main_working_copy_verification_supported": True,
                      "verified_command": "build --source FILE --working-project WINDOWS_PATH",
                      "scope": "Existing AutoShop MAIN working project; no variable import or native project creation",
                      "result_verification_supported": True,
                      "verification_status": "implemented-not-observed",
                      "legacy_bridge": {"backend": "bridge_autoshop", "result_verification_supported": build_verified,
                                        "verification_status": "supported-by-backend" if build_verified else "not-implemented"}},
            "flash": {"backend": "native_deploy/autoshop_vendor",
                      "windows_files_present": windows_files_present,
                      "result_verification_supported": True,
                      "verification_status": "implemented-not-observed",
                      "legacy_bridge": {"backend": "bridge_autoshop", "result_verification_supported": flash_verified,
                                        "verification_status": "supported-by-backend" if flash_verified else "not-implemented"}},
        },
        "limitations": [
            "No Windows commands, GUI actions, network or USB probes were run.",
            "On-disk version metadata does not verify the running process, connectivity or PLC identity.",
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
    version = report["easy521"]["vendor"]["on_disk_autoshop"]
    lines.append(f"AutoShop 磁盘版本: {version['file_version'] or '未识别'}；标定窗口版本 {version['expected_ui_title_version']}；当前进程/布局未核验")
    for name, value in report["easy521"]["capabilities"].items():
        lines.append(f"Easy521 {name}: {value['status']}（当前未核验）")
        for condition in value["missing_conditions"]:
            lines.append(f"  {condition['code']}: {condition['message']}")
    lines.extend(["monitor: 只读寄存器采集；未检查真机连通性或程序身份。",
                  "sim: 固定 BESS 场景播放器，不执行用户 ST。"])
    return "\n".join(lines)
