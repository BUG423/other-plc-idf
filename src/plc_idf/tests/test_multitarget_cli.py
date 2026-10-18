"""Target routing and contract checks, never Windows or hardware operations."""
import json
from pathlib import Path
import subprocess

import pytest

from plc_idf import cli
from plc_idf.artifacts import ArtifactError, normalize_target
from plc_idf.open_project import check_any_project, create_open_project
from plc_idf.targets import BuildRequestV2, get_profile, local_runtime_locator
from .conftest import write_cfg


@pytest.fixture(autouse=True)
def offline(tmp_path, monkeypatch, clean_env):
    config = write_cfg(tmp_path / "tool.toml", tmp_path / "evidence.jsonl")
    monkeypatch.setenv("PLCIDF_CONFIG", str(config))
    def forbidden(*args, **kwargs):
        raise AssertionError("target routing tests must not invoke external programs")
    monkeypatch.setattr(subprocess, "run", forbidden)


class LocalBackend:
    def __init__(self):
        self.calls = []

    def build(self, source, build_dir, *, cycle_ms):
        self.calls.append(("build", source, build_dir, cycle_ms))
        return {"ok": True, "status": "built", "manifest_path": str(build_dir / "build.json"),
                "build_hash": "example-build-hash"}

    def deploy(self, manifest, *, execute):
        self.calls.append(("deploy", manifest, execute))
        return {"ok": True, "status": "deployed" if execute else "preview",
                "executed": execute}

    def install(self):
        self.calls.append(("install",))
        return {"ok": True, "status": "ready"}

    def run(self, manifest, *, duration_s, sample_interval_ms):
        self.calls.append(("run", manifest, duration_s, sample_interval_ms))
        return {"ok": True, "status": "running", "run_id": "local-run"}

    def stop(self, run_id):
        self.calls.append(("stop", run_id))
        return {"ok": True, "status": "stopped"}

    def monitor(self, run_id):
        self.calls.append(("monitor", run_id))
        return {"ok": True, "status": "running", "scan_count": 3}


@pytest.fixture
def backend(monkeypatch):
    local = LocalBackend()
    monkeypatch.setattr(cli, "_open_backend", lambda args: local)
    return local


def test_profile_does_not_fabricate_a_device_or_change_legacy_usb_rules(tmp_path):
    profile = get_profile("open-linux")
    assert "Windows" not in profile.host_platform
    assert profile.as_dict()["device_instance"] is None
    locator = local_runtime_locator(tmp_path / "runtime")
    assert locator["transport"]["kind"] == "local-filesystem"
    assert locator["identity_verification"] == "not-observed"
    with pytest.raises(ArtifactError):
        normalize_target(locator)
    with pytest.raises(ValueError):
        get_profile("easy521").require("run")


def test_targets_json_does_not_enumerate_windows_devices(capsys):
    assert cli.main(["targets", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["hardware_checked"] is False
    assert {p["id"] for p in report["profiles"]} == {"easy521", "open-linux"}


def test_create_and_check_open_project_with_nondefault_cycle(tmp_path, capsys):
    root = tmp_path / "native_project"
    assert cli.main(["new", str(root), "--target", "open-linux", "--cycle-ms", "10", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["schema_version"] == 2 and created["profile"] == "open-linux"
    assert created["compiled"] is False
    manifest = json.loads((root / "plcidf.json").read_text())
    assert manifest["task"]["cycle_ms"] == 10
    assert "T#10ms" in (root / "main.st").read_text()
    assert "windows_project" not in manifest and "busid" not in manifest
    assert cli.main(["check", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["compiled"] is False


@pytest.mark.parametrize("mutation,code", [
    (lambda m: m.update(source="../outside.st"), "E_PATH"),
    (lambda m: m.update(profile_id="unknown-target"), "E_TARGET"),
    (lambda m: m["task"].update(cycle_ms=True), "E_TASK"),
    (lambda m: m["task"].update(cycle_ms=30), "E_TASK_INTERVAL"),
    (lambda m: m["io"]["bindings"].update(motor="GPIO17"), "E_IO"),
    (lambda m: m["toolchain"].update(matiec_commit="floating-main"), "E_TOOLCHAIN"),
])
def test_project_contract_rejects_unsupported_or_untracked_inputs(tmp_path, mutation, code):
    root = tmp_path / "project"
    create_open_project(root)
    path = root / "plcidf.json"
    manifest = json.loads(path.read_text())
    mutation(manifest)
    path.write_text(json.dumps(manifest))
    checked = check_any_project(root)
    assert not checked.ok
    assert code in {d.code for d in checked.diagnostics}


def test_open_check_rejects_duplicate_keys_and_source_escape(tmp_path):
    root = tmp_path / "project"
    create_open_project(root)
    path = root / "plcidf.json"
    path.write_text(path.read_text().replace('"schema_version": 2,',
                                            '"schema_version": 2, "schema_version": 2,'))
    assert not check_any_project(root).ok


def test_build_dryrun_does_not_call_compiler_or_require_windows(tmp_path, backend, capsys):
    root = tmp_path / "project"
    create_open_project(root)
    assert cli.main(["build", "--target", "open-linux", "--project", str(root), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["executed"] is False and report["cycle_ms"] == 20
    assert backend.calls == []
    assert not (root / ".plcidf").exists()


def test_build_routes_to_linux_without_bridge_authorization(tmp_path, backend, capsys):
    root = tmp_path / "project"
    create_open_project(root)
    assert cli.main(["build", "--target", "open-linux", "--project", str(root),
                     "--execute", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["target"] == "open-linux"
    assert backend.calls[0][0] == "build" and backend.calls[0][3] == 20


def test_build_refuses_windows_path_and_conflicting_task_before_execution(tmp_path, backend, capsys):
    root = tmp_path / "project"
    create_open_project(root)
    for extra in (["--working-project", "E:\\PLC"], ["--cycle-ms", "10"]):
        assert cli.main(["build", "--target", "open-linux", "--project", str(root),
                         "--execute", "--json", *extra]) == 2
        assert json.loads(capsys.readouterr().out)["ok"] is False
    assert backend.calls == []


def test_build_output_cannot_be_a_source_ancestor(tmp_path):
    source = tmp_path / "project" / "main.st"
    source.parent.mkdir()
    source.write_text("PROGRAM MAIN END_PROGRAM")
    with pytest.raises(ValueError):
        BuildRequestV2(source, tmp_path, "open-linux").validate()


def test_flash_is_preview_until_execute_and_keeps_local_locator(backend, capsys):
    for execute in (False, True):
        args = ["flash", "--target", "open-linux", "--manifest", "bundle.json", "--json"]
        if execute:
            args.append("--execute")
        assert cli.main(args) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["executed"] is execute
        assert report["locator"]["transport"]["kind"] == "local-filesystem"
        assert backend.calls[-1] == ("deploy", "bundle.json", execute)


def test_run_stop_and_monitor_route_only_to_local_backend(backend, capsys):
    assert cli.main(["run", "--manifest", "release.json", "--duration", "1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["run_id"] == "local-run"
    assert cli.main(["monitor", "--target", "open-linux", "--run-id", "local-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["scan_count"] == 3
    assert cli.main(["stop", "--run-id", "local-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "stopped"
    assert [c[0] for c in backend.calls] == ["run", "monitor", "stop"]


def test_monitor_does_not_silently_drop_a_remote_host(backend, capsys):
    assert cli.main(["monitor", "--target", "open-linux", "--run-id", "x",
                     "--host", "192.0.2.1", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert backend.calls == []


def test_open_doctor_only_checks_local_toolchain(backend, capsys):
    assert cli.main(["doctor", "--target", "open-linux", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["hardware_checked"] is False
    assert backend.calls == [("install",)]


@pytest.mark.parametrize("command,extra", [
    ("build", ["--build-dir", "/tmp/native"]),
    ("build", ["--cycle-ms", "20"]),
    ("flash", ["--manifest", "native.json"]),
    ("monitor", ["--run-id", "local-run"]),
    ("doctor", ["--toolchain-dir", "/tmp/compiler"]),
])
def test_easy521_refuses_local_only_options_before_any_process(command, extra, capsys):
    assert cli.main([command, "--target", "easy521", "--json", *extra]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_open_flash_cannot_ignore_a_named_other_project(backend, capsys):
    assert cli.main(["flash", "--target", "open-linux", "--manifest", "x.json",
                     "--project", "another", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert not backend.calls


def test_changed_bundle_pointer_is_rejected_before_deployment(tmp_path, backend, capsys):
    manifest = tmp_path / "build.json"
    manifest.write_text(json.dumps({"build_hash": "new-hash"}))
    pointer = tmp_path / "pointer.json"
    pointer.write_text(json.dumps({"schema": "plcidf.bundle-pointer.v2",
                                   "manifest_path": str(manifest), "build_hash": "old-hash"}))
    assert cli.main(["flash", "--target", "open-linux", "--manifest", str(pointer),
                     "--execute", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert not backend.calls
