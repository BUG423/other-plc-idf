"""Offline CLI integration and bounded runtime failures, with no hardware use."""

import json
from pathlib import Path
import subprocess

import pytest

from plc_idf import cli, doctor
from plc_idf.config import load_config

from .conftest import write_cfg


@pytest.fixture(autouse=True)
def offline_environment(tmp_path, monkeypatch, clean_env):
    cfg = write_cfg(tmp_path / "tool.toml", tmp_path / "events.jsonl")
    monkeypatch.setenv("PLCIDF_CONFIG", str(cfg))

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline CLI must not invoke external processes")

    monkeypatch.setattr(subprocess, "run", forbidden)


def test_new_json_then_check_returns_one_parseable_document(tmp_path, capsys):
    project = tmp_path / "new_project"
    assert cli.main(["new", str(project), "--name", "demo", "--cycle-ms", "10", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["command"] == "new"
    assert created["compiled"] is False and created["native_project_created"] is False
    manifest = json.loads((project / "plcidf.json").read_text())
    assert manifest["name"] == "demo" and manifest["task"]["cycle_ms"] == 10
    before = {item.name: item.read_bytes() for item in project.iterdir()}

    assert cli.main(["check", str(project), "--json"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["schema_version"] == 1 and checked["ok"] is True
    assert checked["command"] == "check"
    assert before == {item.name: item.read_bytes() for item in project.iterdir()}


def test_init_and_check_default_to_cwd(tmp_path, monkeypatch, capsys):
    project = tmp_path / "initialized"
    project.mkdir()
    monkeypatch.chdir(project)
    assert cli.main(["init", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["root"] == str(project)
    assert cli.main(["check", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


@pytest.mark.parametrize("extra", [["--name", "bad name"], ["--cycle-ms", "0"]])
def test_creation_value_errors_are_json_and_do_not_leave_files(tmp_path, capsys, extra):
    target = tmp_path / "invalid"
    assert cli.main(["new", str(target), "--json", *extra]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["diagnostics"][0]["code"] == "E_ARGUMENT"
    assert not target.exists()


def test_nonempty_init_is_a_structured_io_error(tmp_path, capsys):
    original = tmp_path / "original.txt"
    original.write_text("preserve")
    assert cli.main(["init", str(tmp_path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["diagnostics"][0]["code"] == "E_IO"
    assert original.read_text() == "preserve"


def test_check_rejects_undefined_variable_with_source_location(tmp_path, capsys):
    project = tmp_path / "negative"
    assert cli.main(["new", str(project), "--json"]) == 0
    capsys.readouterr()
    (project / "MAIN_body.st").write_text("unknown := 1;\n")

    assert cli.main(["check", str(project / "plcidf.json"), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    item = next(value for value in report["diagnostics"] if value["code"] == "E_ST_VARIABLE")
    assert (item["path"], item["line"], item["column"]) == ("MAIN_body.st", 1, 1)


def test_doctor_is_offline_and_reports_actual_support_boundaries(capsys, monkeypatch):
    monkeypatch.setattr(doctor.metadata, "version",
                        lambda name: {"pymodbus": "3.16.1", "pyserial": "3.5"}[name])
    assert cli.main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["validation"] == "offline-prerequisites-only"
    assert report["hardware_checked"] is False
    assert report["dependencies"]["pymodbus"]["installed"] == "3.16.1"
    assert report["capabilities"]["monitor"]["read_function_codes"] == [1, 2, 3, 4]
    assert report["capabilities"]["sim"]["executes_user_st"] is False
    assert report["capabilities"]["project"]["complete_st_compiler"] is False
    for name in ("powershell", "autoshop", "usbipd", "bridge_script"):
        assert "exists" in report["windows"][name]


def test_doctor_missing_dependency_is_not_healthy(capsys, monkeypatch):
    def missing(name):
        raise doctor.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(doctor.metadata, "version", missing)
    assert cli.main(["doctor", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["dependencies"]["pymodbus"]["installed"] is None
    assert report["capabilities"]["monitor"]["available"] is False


def test_doctor_backend_verification_flags_are_explicit(monkeypatch):
    monkeypatch.setattr(doctor.BridgeAutoshopBackend, "build_verification_supported", True,
                        raising=False)
    monkeypatch.setattr(doctor.BridgeAutoshopBackend, "flash_verification_supported", False,
                        raising=False)
    report = doctor.collect_diagnostics(load_config())
    assert report["capabilities"]["build"]["verification_status"] == "supported-by-backend"
    assert report["capabilities"]["flash"]["verification_status"] == "not-implemented"


def test_doctor_converts_wsl_paths_without_running_wslpath(monkeypatch):
    monkeypatch.setenv("WSL_DISTRO_NAME", "TestDistro")
    assert doctor._local_path(r"E:\Software\AutoShop\AutoShop.exe") == Path("/mnt/e/Software/AutoShop/AutoShop.exe")
    assert doctor._local_path(r"\\wsl.localhost\TestDistro\home\dev\script.ps1") == Path("/home/dev/script.ps1")
    assert doctor._local_path(r"\\other-host\share\script.ps1") is None


@pytest.mark.parametrize("error", [TimeoutError("waiting for READY"),
                                  subprocess.TimeoutExpired(["monitor"], 0.5)])
def test_sim_timeout_returns_124_without_traceback(error, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(cli.SimVirtualBackend, "run_with_monitor", fail)
    assert cli.main(["sim", "--with-monitor", "--once"]) == 124
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "等待" in captured.err or "超时" in captured.err


def test_monitor_timeout_and_process_oserror_are_bounded(monkeypatch, capsys):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["monitor"], 0.1)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert cli.main(["monitor", "--once", "--exec-timeout", "0.1"]) == 124
    assert "Traceback" not in capsys.readouterr().err

    def denied(*args, **kwargs):
        raise PermissionError("test executable is not accessible")

    monkeypatch.setattr(subprocess, "run", denied)
    assert cli.main(["monitor", "--once"]) == 1
    captured = capsys.readouterr()
    assert "操作失败" in captured.err and "Traceback" not in captured.err
