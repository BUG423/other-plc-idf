"""Distribution-related path resolution; no Windows or PLC access."""

import importlib.util
import os
from pathlib import Path
import sys

import pytest

import plc_idf.config as config


REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "_plcidf_check_distribution", REPO_ROOT / "tools/check_distribution.py")
distribution_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(distribution_check)


def test_installed_package_uses_workspace_and_bundled_assets(tmp_path, monkeypatch,
                                                          clean_env):
    package = tmp_path / "venv/lib/python3.12/site-packages/plc_idf"
    package.mkdir(parents=True)
    fake_module = package / "config.py"
    fake_module.touch()
    monitor = package / "_resources/wsl-tools/e521_monitor.py"
    bridge = package / "_resources/plc/tools/autoshop_ui.ps1"
    for asset in (monitor, bridge):
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.touch()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(config, "__file__", str(fake_module))
    monkeypatch.chdir(work)

    cfg = config.load_config()

    assert config.find_project_root() == work
    assert Path(cfg.paths.wsl_tools) == monitor.parent
    assert config.evidence_path(cfg) == work / "logs/evidence.jsonl"
    assert str(bridge).replace("/", "\\") in cfg.bridge.tools_ps1_windows


def test_nearest_workspace_config_is_found_without_source_marker(tmp_path,
                                                                monkeypatch,
                                                                clean_env):
    workspace = tmp_path / "user-project"
    child = workspace / "src/control"
    child.mkdir(parents=True)
    (workspace / config.CONFIG_NAME).write_text(
        '[monitor]\nhost = "192.168.10.10"\n', encoding="utf-8")
    monkeypatch.chdir(child)

    cfg = config.load_config()

    assert Path(cfg.paths.project_root) == workspace
    assert cfg.source == workspace / config.CONFIG_NAME
    assert cfg.monitor.host == "192.168.10.10"
    assert (Path(cfg.paths.wsl_tools) / "e521_regs.py").is_file()


def test_explicit_tool_directory_is_resolved_from_workspace(tmp_path, clean_env):
    cfg_path = tmp_path / config.CONFIG_NAME
    cfg_path.write_text('[paths]\nwsl_tools = "custom-tools"\n', encoding="utf-8")

    cfg = config.load_config(cfg_path, project_root=tmp_path)

    assert Path(cfg.paths.wsl_tools) == tmp_path / "custom-tools"


def test_loading_config_never_mutates_defaults(tmp_path, clean_env):
    first = config.load_config(project_root=tmp_path / "first")
    second = config.load_config(project_root=tmp_path / "second")

    assert config.DEFAULTS["paths"] == {"project_root": None, "wsl_tools": None}
    assert config.DEFAULTS["bridge"]["tools_ps1_windows"] is None
    assert first.paths.project_root != second.paths.project_root


def test_usb_memory_preview_imports_companion_from_another_cwd(tmp_path):
    preview = distribution_check.preview_usb_memory_reader(
        Path(sys.executable), REPO_ROOT / "tools/read_easy521_usb_memory.py",
        cwd=tmp_path, env=dict(os.environ))
    assert preview["device_access"] is False and preview["status"] == "preview"
    assert preview["symbol"]["address_semantics"] == "opaque_vendor_symbol"


@pytest.mark.parametrize("kind", ["memory", "runtime"])
def test_native_usb_preview_imports_without_loading_libusb(tmp_path, kind):
    reader = REPO_ROOT / f"tools/read_easy521_usb_{kind}.py"
    check = (distribution_check.preview_usb_memory_reader if kind == "memory"
             else distribution_check.preview_usb_runtime_reader)
    preview = check(Path(sys.executable), reader, cwd=tmp_path,
                    env=dict(os.environ), transport="linux-native")
    assert preview["transport_kind"] == "linux-native-libusb1"
    assert preview["linux_native_usb"] is True
    assert preview["device_access"] is False


def test_application_preview_imports_without_any_external_access(tmp_path):
    preview = distribution_check.preview_usb_application_reader(
        Path(sys.executable), REPO_ROOT / "tools/easy521_application_usb.py",
        cwd=tmp_path, env=dict(os.environ))
    assert preview["status"] == "preview" and preview["requests"] == []
    assert preview["device_access"] is False and preview["plc_change_possible"] is False
    frame = bytes.fromhex(preview["open_preview_hex"])
    assert frame[8] == 1 and frame[26:] == b"userprog.foid\0"


def test_native_application_dependencies_import_and_parse_without_compiler(tmp_path):
    result = distribution_check.check_native_application_imports(
        Path(sys.executable), REPO_ROOT / "tools", cwd=tmp_path, env=dict(os.environ))
    assert result["parsed_profiles"] == ["counter-dint-increment-v1", "bess-pcmd-real-increment-v1"]
    assert result["native_compilation_executed"] is False and result["device_access"] is False


@pytest.mark.parametrize("access", [
    "import subprocess; subprocess.run(['must-not-run'])",
    "import socket; socket.socket()",
    "from plc_idf.interop import run_command; run_command(['must-not-run'])",
    "from plc_idf.lease import ResourceLease; ResourceLease('must-not-lock')",
    "import ctypes; ctypes.CDLL('must-not-load')",
    "import ctypes.util; ctypes.util.find_library('must-not-discover')",
])
def test_usb_memory_preview_guard_rejects_external_access(tmp_path, capsys, access):
    reader = tmp_path / "bad_preview.py"
    reader.write_text(access + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Unexpected exit"):
        distribution_check.preview_usb_memory_reader(
            Path(sys.executable), reader, cwd=tmp_path, env=dict(os.environ))
    assert "preview external access is forbidden" in capsys.readouterr().err


@pytest.mark.parametrize("access", [
    "import subprocess; subprocess.run(['must-not-run'])",
    "import socket; socket.socket()",
    "from plc_idf.lease import ResourceLease; ResourceLease('must-not-lock')",
    "import secrets; secrets.randbits(16)",
    "import ctypes; ctypes.CDLL('must-not-load')",
    "import ctypes.util; ctypes.util.find_library('must-not-discover')",
    "from pathlib import Path; Path('must-not-probe').exists()",
    "open('must-not-write', 'w')",
    "import os; os.mkdir('must-not-create')",
])
def test_application_preview_guard_rejects_external_and_filesystem_access(tmp_path, capsys, access):
    reader = tmp_path / "bad_application_preview.py"
    reader.write_text(access + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Unexpected exit"):
        distribution_check.preview_usb_application_reader(
            Path(sys.executable), reader, cwd=tmp_path, env=dict(os.environ))
    assert "application preview external access is forbidden" in capsys.readouterr().err
    assert not (tmp_path / "must-not-write").exists()
    assert not (tmp_path / "must-not-create").exists()
