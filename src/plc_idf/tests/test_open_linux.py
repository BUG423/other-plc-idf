"""Local ST backend contracts plus optional real pinned-compiler integration.

Set PLCIDF_OPEN_LINUX_TOOLCHAIN to a bootstrapped directory for the real tests.
These tests never contact Windows or a physical PLC.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import pytest

from plc_idf.backends.open_linux import (OpenLinuxBackend, OpenLinuxError,
    _profile, _inside, _digest, _write_json)

EXAMPLE = Path(__file__).resolve().parents[3] / "examples/open_linux/counter/main.st"


@pytest.fixture
def backend(tmp_path):
    return OpenLinuxBackend(tmp_path / "missing-toolchain", tmp_path / "state")


def test_constructor_and_install_are_readonly(backend, tmp_path):
    assert not backend.state_dir.exists()
    result = backend.install()
    assert result["ok"] is False and result["status"] == "unavailable"
    assert not backend.state_dir.exists()
    assert not backend.toolchain_dir.exists()


def test_complete_example_is_supported():
    _profile(EXAMPLE.read_text(), 20)


@pytest.mark.parametrize("cycle", [True, 0, -1, 1001, 20.0, None])
def test_invalid_cycle_rejected(cycle):
    with pytest.raises(OpenLinuxError):
        _profile(EXAMPLE.read_text(), cycle)


@pytest.mark.parametrize("change", [
    lambda text: text.replace("T#20ms", "T#10ms"),
    lambda text: text.replace("PRIORITY := 0", "PRIORITY := 1"),
    lambda text: text + '\n{#include "/tmp/other.c"}\n',
    lambda text: text.replace("COUNTER : DINT", "COUNTER : INT"),
    lambda text: text.replace("TASK scan", "TASK other"),
    lambda text: text + "\nRESOURCE Second ON PLC\nEND_RESOURCE\n",
])
def test_profile_refuses_untracked_or_incompatible_inputs(change):
    with pytest.raises(OpenLinuxError):
        _profile(change(EXAMPLE.read_text()), 20)


@pytest.mark.parametrize("relative", ["/bin/sh", "../outside", "a/../../x", "a\\outside", ""])
def test_bundle_paths_stay_inside(tmp_path, relative):
    with pytest.raises(OpenLinuxError):
        _inside(tmp_path, relative)


def test_bundle_symlinks_refused(tmp_path):
    (tmp_path / "outside").symlink_to("/bin/sh")
    with pytest.raises(OpenLinuxError):
        _inside(tmp_path, "outside")


@pytest.mark.parametrize("run_id", ["../x", "1", "a" * 31, "A" * 32])
def test_run_lookup_does_not_accept_arbitrary_paths(backend, run_id):
    with pytest.raises(OpenLinuxError):
        backend.monitor(run_id)


def test_plain_executable_cannot_be_run_as_manifest(backend, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"executable": "/bin/sh"}))
    with pytest.raises(OpenLinuxError):
        backend.run(path)


@pytest.fixture(scope="module")
def real_toolchain():
    root = os.environ.get("PLCIDF_OPEN_LINUX_TOOLCHAIN")
    if not root:
        pytest.skip("real MatIEC integration requires PLCIDF_OPEN_LINUX_TOOLCHAIN")
    assert (Path(root) / "toolchain.json").is_file()
    return Path(root)


@pytest.fixture
def real_backend(real_toolchain, tmp_path):
    return OpenLinuxBackend(real_toolchain, tmp_path / "state")


@pytest.fixture
def built(real_backend, tmp_path):
    result = real_backend.build(EXAMPLE, tmp_path / "builds", cycle_ms=20)
    assert result["ok"], result
    return result


def test_real_st_runs_then_completes(real_backend, built):
    preview = real_backend.deploy(built["manifest_path"])
    assert preview["status"] == "preview"
    assert not real_backend.state_dir.exists()
    deployed = real_backend.deploy(built["manifest_path"], execute=True)
    started = real_backend.run(deployed["manifest_path"], duration_s=.65, sample_interval_ms=20)
    assert started["ok"] and started["sample"]["counter"] >= 1
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = real_backend.monitor(started["run_id"])
        if current["status"] == "completed":
            break
        time.sleep(.025)
    assert current["ok"] and current["status"] == "completed"
    assert current["sample"]["counter"] == current["sample"]["scan_count"] >= 25
    samples = [json.loads(line) for line in Path(current["samples_path"]).read_text().splitlines()]
    assert {row["heartbeat"] for row in samples} == {True, False}
    assert all(row["program_hash"] == built["program_hash"] for row in samples)
    assert current["quality"]["execution"] == "actual-matiec-generated-code"
    assert current["bundle_integrity"] == "verified"


def test_real_syntax_error_fails_in_upstream_compiler(real_backend, tmp_path):
    invalid = tmp_path / "invalid.st"
    invalid.write_text(EXAMPLE.read_text().replace("COUNTER := COUNTER + 1;", "COUNTER := ;"))
    result = real_backend.build(invalid, tmp_path / "builds", cycle_ms=20)
    assert not result["ok"] and result["status"] == "failed"
    assert result["stage"] == "iec2c" and result["returncode"] != 0
    assert "error" in result["diagnostics"].lower()
    assert not (Path(result["build_dir"]) / "plc_runtime").exists()
    assert not (Path(result["build_dir"]) / "build.json").exists()


def test_real_stop_is_verified_and_idempotent(real_backend, built):
    deployed = real_backend.deploy(built["manifest_path"], execute=True)
    started = real_backend.run(deployed["manifest_path"], duration_s=5, sample_interval_ms=20)
    stopped = real_backend.stop(started["run_id"])
    assert stopped["ok"] and stopped["status"] == "stopped"
    assert stopped["sample"]["scan_count"] >= 1
    again = real_backend.stop(started["run_id"])
    assert again["ok"] and again["already_exited"]


def test_real_build_cannot_bypass_deploy(real_backend, built):
    with pytest.raises(OpenLinuxError, match="deployed"):
        real_backend.run(built["manifest_path"])


def test_real_changed_binary_refused_before_deploy(real_backend, built):
    binary = Path(built["build_dir"]) / "plc_runtime"
    binary.write_bytes(binary.read_bytes() + b"tamper")
    with pytest.raises(OpenLinuxError, match="artifact"):
        real_backend.deploy(built["manifest_path"], execute=True)
    assert not (real_backend.state_dir / "releases").exists()


def test_real_release_outside_current_state_refused(real_backend, built, tmp_path):
    deployed = real_backend.deploy(built["manifest_path"], execute=True)
    other = OpenLinuxBackend(real_backend.toolchain_dir, tmp_path / "other-state")
    with pytest.raises(OpenLinuxError, match="state_dir"):
        other.run(deployed["manifest_path"])


def test_real_monitor_invalidates_changed_bundle(real_backend, built):
    deployed = real_backend.deploy(built["manifest_path"], execute=True)
    started = real_backend.run(deployed["manifest_path"], duration_s=2, sample_interval_ms=20)
    try:
        source = Path(deployed["manifest_path"]).parent / "source.st"
        source.write_text(source.read_text() + "\n(* changed *)\n")
        result = real_backend.monitor(started["run_id"])
        assert not result["ok"] and result["bundle_integrity"] == "changed-or-unavailable"
        assert result["quality"]["program_identity"] == "historical-sample-only"
        assert result["sample"]["run_id"] == started["run_id"]
    finally:
        real_backend.stop(started["run_id"])


def test_real_pid_reuse_guard_does_not_signal_another_process(real_backend, built):
    deployed = real_backend.deploy(built["manifest_path"], execute=True)
    started = real_backend.run(deployed["manifest_path"], duration_s=5, sample_interval_ms=20)
    record_path = Path(started["state_path"])
    original = record_path.read_text()
    unrelated = subprocess.Popen(["sleep", "5"])
    try:
        record = json.loads(original)
        record["pid"] = unrelated.pid
        record["record_hash"] = _digest({key: value for key, value in record.items() if key != "record_hash"})
        _write_json(record_path, record)
        result = real_backend.stop(started["run_id"])
        assert not result["ok"] and result["status"] == "identity-mismatch-or-exited"
        assert unrelated.poll() is None
    finally:
        record_path.write_text(original)
        real_backend.stop(started["run_id"])
        unrelated.terminate()
        unrelated.wait(timeout=2)


def test_real_nonreturning_st_has_kernel_deadline(real_backend, tmp_path):
    source = tmp_path / "hang.st"
    source.write_text(EXAMPLE.read_text().replace("COUNTER := COUNTER + 1;", "WHILE TRUE DO\nCOUNTER := 1;\nEND_WHILE;"))
    built = real_backend.build(source, tmp_path / "builds", cycle_ms=20)
    assert built["ok"], built
    deployed = real_backend.deploy(built["manifest_path"], execute=True)
    start = time.monotonic()
    started = real_backend.run(deployed["manifest_path"], duration_s=.1, sample_interval_ms=20)
    assert not started["ok"]
    assert time.monotonic() - start < 3
    observed = real_backend.monitor(started["run_id"])
    assert not observed["process_alive"] and observed["status"] == "faulted"


def test_startup_preserves_unverified_sample_failure(real_backend, built, monkeypatch):
    deployed = real_backend.deploy(built["manifest_path"], execute=True)
    real_monitor = real_backend.monitor
    run_ids = []
    def unverified(run_id):
        run_ids.append(run_id)
        current = real_monitor(run_id)
        if current.get("sample"):
            return {**current, "ok": False, "status": "exited-unverified", "process_alive": False}
        return current
    monkeypatch.setattr(real_backend, "monitor", unverified)
    try:
        result = real_backend.run(deployed["manifest_path"], duration_s=.1, sample_interval_ms=20)
        assert not result["ok"] and result["status"] == "exited-unverified"
    finally:
        monkeypatch.setattr(real_backend, "monitor", real_monitor)
        for run_id in set(run_ids):
            real_backend.stop(run_id)


def test_real_mid_build_source_mutation_is_refused(real_backend, tmp_path, monkeypatch):
    real_run = subprocess.run
    def mutate_after_gcc(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if argv[0] == "gcc" and "-o" in argv:
            runner = Path(kwargs["cwd"]) / "runner.c"
            runner.write_text(runner.read_text() + "\n/* changed during build */\n")
        return result
    monkeypatch.setattr(subprocess, "run", mutate_after_gcc)
    with pytest.raises(OpenLinuxError, match="changed during"):
        real_backend.build(EXAMPLE, tmp_path / "builds", cycle_ms=20)


def test_real_abnormal_exit_does_not_promote_last_running_sample(real_backend, tmp_path):
    source = tmp_path / "later-hang.st"
    source.write_text(EXAMPLE.read_text().replace("COUNTER := COUNTER + 1;",
        "IF COUNTER >= 1 THEN\nWHILE TRUE DO\nCOUNTER := 1;\nEND_WHILE;\nEND_IF;\nCOUNTER := COUNTER + 1;"))
    built = real_backend.build(source, tmp_path / "builds", cycle_ms=20)
    assert built["ok"], built
    deployed = real_backend.deploy(built["manifest_path"], execute=True)
    started = real_backend.run(deployed["manifest_path"], duration_s=.1, sample_interval_ms=20)
    assert started["ok"] and started["sample"]["state"] == "running"
    time.sleep(1.25)
    observed = real_backend.monitor(started["run_id"])
    assert not observed["ok"] and observed["status"] == "faulted"
    assert not observed["process_alive"] and observed["sample"]["state"] == "running"


def test_real_source_change_changes_executed_behavior(real_backend, built, tmp_path):
    source = tmp_path / "step-three.st"
    source.write_text(EXAMPLE.read_text().replace("COUNTER := COUNTER + 1;", "COUNTER := COUNTER + 3;"))
    changed = real_backend.build(source, tmp_path / "changed-builds", cycle_ms=20)
    assert changed["ok"] and changed["program_hash"] != built["program_hash"]
    deployed = real_backend.deploy(changed["manifest_path"], execute=True)
    started = real_backend.run(deployed["manifest_path"], duration_s=.15, sample_interval_ms=20)
    try:
        time.sleep(.2)
        observed = real_backend.monitor(started["run_id"])
        assert observed["ok"] and observed["status"] == "completed"
        assert observed["sample"]["counter"] == 3 * observed["sample"]["scan_count"]
    finally:
        real_backend.stop(started["run_id"])
