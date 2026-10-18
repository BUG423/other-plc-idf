"""Scenario preflight, real evidence bindings and fail-closed workflow behavior."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from plc_idf.open_project import create_open_project
from plc_idf.verification import verify_project


def sha(data):
    return hashlib.sha256(data).hexdigest()


def scenario(name="count", steps=None):
    return {"name": name, "steps": steps or [
        {"scans": 1, "inputs": {}, "expect": {"COUNTER": 1, "HEARTBEAT": False}},
        {"scans": 2, "expect": {"COUNTER": 3, "HEARTBEAT": False}}]}


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    create_open_project(root)
    (root / "scenarios.json").write_text(json.dumps({
        "schema": "plcidf.scenarios.v1", "scenarios": [scenario()]}))
    return root


def write_scenarios(root, entries):
    (root / "scenarios.json").write_text(json.dumps({
        "schema": "plcidf.scenarios.v1", "scenarios": entries}))


class FakeBackend:
    """A contract double; no compiler/runtime execution is claimed by these tests."""
    def __init__(self, state):
        self.state = state
        self.calls = []
        self.signals = None
        self.build_hook = None
        self.replay_hook = None
        self.fail_build = False
        self.fail_replay = False
        self.generic_output = None

    def build(self, source, output, cycle_ms=20, signals=None):
        self.calls.append("build")
        self.source, self.cycle_ms = source, cycle_ms
        self.signals = signals or {"inputs": {}, "outputs": {"COUNTER": "DINT", "HEARTBEAT": "BOOL"}}
        if self.fail_build:
            return {"ok": False, "status": "failed", "stage": "iec2c", "returncode": 1,
                    "diagnostics": "source.st:6: error: invalid ST expression"}
        text = source.read_text(encoding="utf-8-sig")
        self.identities = {"build_hash": "a" * 64, "program_hash": "b" * 64,
                           "source_sha256": sha(text.encode()), "binary_sha256": "c" * 64}
        result = {"ok": True, "status": "verified", "manifest_path": str(self.state / "build.json"),
                  **self.identities}
        if self.build_hook:
            self.build_hook(result)
        return result

    def deploy(self, manifest, execute=False):
        assert execute
        self.calls.append("deploy")
        return {"ok": True, "status": "deployed", "build_hash": self.identities["build_hash"],
                "program_hash": self.identities["program_hash"],
                "manifest_path": str(self.state / "release.json")}

    def replay(self, manifest, frames, timeout_s=5):
        self.calls.append("replay")
        if self.fail_replay:
            raise TimeoutError("synthetic replay timeout; child already reaped")
        assert timeout_s > 0
        index = self.calls.count("replay")
        run_id = f"{index:032x}"
        values = {key: False if kind == "BOOL" else 0.0 if kind == "REAL" else 0
                  for direction in self.signals.values() for key, kind in direction.items()}
        samples = []
        for scan, frame in enumerate(frames, 1):
            values.update(frame)
            if "COUNTER" in values:
                values["COUNTER"] += 1
            if "HEARTBEAT" in values and values.get("COUNTER", 0) % 10 == 0:
                values["HEARTBEAT"] = not values["HEARTBEAT"]
            if self.generic_output:
                values.update(self.generic_output(values, scan))
            samples.append({"run_id": run_id, "scan_count": scan,
                            "elapsed_ns": (scan - 1) * self.cycle_ms * 1000000,
                            "values": dict(values), **self.identities})
        self.state.mkdir(parents=True, exist_ok=True)
        path = self.state / f"samples-{run_id}.jsonl"
        path.write_text("".join(json.dumps(item) + "\n" for item in samples))
        result = {"ok": True, "status": "completed", "samples": samples,
                  "samples_path": str(path), "run_id": run_id, **self.identities}
        if self.replay_hook:
            self.replay_hook(result)
        return result


@pytest.fixture
def backend(tmp_path):
    return FakeBackend(tmp_path / "runtime-state")


def test_complete_counter_workflow_binds_sources_and_real_evidence(project, backend):
    report = verify_project(project, backend, report_path=".plcidf/verification.json")
    assert report["ok"] and report["status"] == "verified"
    assert report["stage"] == "complete"
    assert backend.calls == ["build", "deploy", "replay"]
    assert report["summary"] == {"scenarios": 1, "passed": 1, "failed": 0, "skipped": 0,
                                 "assertions": 4, "assertions_passed": 4, "assertions_failed": 0}
    for name in ("manifest", "source", "scenarios"):
        identity = report["input_hashes"][name]
        assert identity["sha256"] == sha(Path(identity["path"]).read_bytes())
    evidence = report["scenarios"][0]["evidence"]
    assert evidence["sha256"] == sha(Path(evidence["path"]).read_bytes())
    assert report["scenarios"][0]["steps"][1]["scan"] == 3
    assert report["scenarios"][0]["steps"][1]["actual"] == {"COUNTER": 3, "HEARTBEAT": False}
    assert report["quality"]["timing"] == report["quality"]["physical_io"] == "not-verified"
    assert json.loads((project / ".plcidf/verification.json").read_text()) == report


def test_scenarios_reset_independently(project, backend):
    write_scenarios(project, [scenario("first"), scenario("second")])
    report = verify_project(project, backend)
    assert report["ok"] and report["summary"]["passed"] == 2
    assert len({item["run_id"] for item in report["scenarios"]}) == 2


def test_logic_failures_collect_across_scenarios(project, backend):
    entries = [scenario("wrong", [{"scans": 1, "expect": {"COUNTER": 42}}]), scenario("correct")]
    write_scenarios(project, entries)
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "assertions"
    assert report["summary"]["failed"] == report["summary"]["passed"] == 1
    diagnostic = report["diagnostics"][0]
    assert diagnostic["scenario"] == "wrong" and diagnostic["scan"] == 1
    assert diagnostic["expected"] == 42 and diagnostic["actual"] == 1
    assert backend.calls.count("replay") == 2


@pytest.mark.parametrize("document", [
    {}, [], {"schema": "wrong", "scenarios": [scenario()]},
    {"schema": "plcidf.scenarios.v1", "scenarios": []},
    {"schema": "plcidf.scenarios.v1", "scenarios": [scenario()], "extra": 1},
    {"schema": "plcidf.scenarios.v1", "scenarios": [scenario("same"), scenario("same")]},
    {"schema": "plcidf.scenarios.v1", "scenarios": [{"name": "", "steps": []}]},
    {"schema": "plcidf.scenarios.v1", "scenarios": [{"name": "x", "steps": []}]},
    {"schema": "plcidf.scenarios.v1", "scenarios": [{"name": "x", "steps": [], "x": 1}]},
    {"schema": "plcidf.scenarios.v1", "scenarios": [scenario(str(i)) for i in range(101)]},
])
def test_invalid_scenario_documents_do_not_build(project, backend, document):
    (project / "scenarios.json").write_text(json.dumps(document))
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "scenarios"
    assert backend.calls == []


@pytest.mark.parametrize("step", [
    {"scans": True, "expect": {"COUNTER": 1}},
    {"scans": 1.0, "expect": {"COUNTER": 1}},
    {"scans": 0, "expect": {"COUNTER": 1}},
    {"scans": 10001, "expect": {"COUNTER": 1}},
    {"scans": 1, "expect": {}},
    {"scans": 1, "expect": {"COUNTER": True}},
    {"scans": 1, "expect": {"COUNTER": 2147483648}},
    {"scans": 1, "expect": {"COUNTER": 1.0}},
    {"scans": 1, "expect": {"HEARTBEAT": 1}},
    {"scans": 1, "expect": {"COUNTER": {"approx": 1, "abs_tol": 0}}},
    {"scans": 1, "expect": {"UNKNOWN": 1}},
    {"scans": 1, "inputs": {"UNKNOWN": True}, "expect": {"COUNTER": 1}},
    {"scans": 1, "inputs": [], "expect": {"COUNTER": 1}},
    {"scans": 1, "expect": {"COUNTER": 1}, "delay": 2},
    {"expect": {"COUNTER": 1}},
])
def test_invalid_step_contracts_do_not_build(project, backend, step):
    write_scenarios(project, [scenario("bad", [step])])
    report = verify_project(project, backend)
    assert not report["ok"] and backend.calls == []
    assert report["diagnostics"][-1]["step"] == 1


@pytest.mark.parametrize("text", [
    '{"schema":"plcidf.scenarios.v1","schema":"plcidf.scenarios.v1","scenarios":[]}',
    '{"schema":"plcidf.scenarios.v1","scenarios":[{"name":"x","steps":[{"scans":1,"expect":{"COUNTER":NaN}}]}]}',
    '{"schema":"plcidf.scenarios.v1","scenarios":[{"name":"x","steps":[{"scans":1,"expect":{"COUNTER":1e999}}]}]}',
    '{"schema":"plcidf.scenarios.v1","scenarios":[{"name":"x","steps":[{"scans":1,"expect":{"COUNTER":1,"COUNTER":2}}]}]}',
])
def test_duplicate_keys_and_nonfinite_json_fail_before_build(project, backend, text):
    (project / "scenarios.json").write_text(text)
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "scenarios" and backend.calls == []
    json.dumps(report, allow_nan=False)


def test_frame_limits_are_global_and_per_scenario(project, backend):
    write_scenarios(project, [scenario("big", [
        {"scans": 6000, "expect": {"COUNTER": 6000}},
        {"scans": 6000, "expect": {"COUNTER": 12000}}])])
    assert not verify_project(project, backend)["ok"]
    write_scenarios(project, [scenario(str(i), [{"scans": 10000, "expect": {"COUNTER": 10000}}])
                              for i in range(11)])
    assert not verify_project(project, backend)["ok"]
    assert backend.calls == []


@pytest.mark.parametrize("relative", ["../outside.json", "/tmp/outside-scenarios.json"])
def test_scenario_path_cannot_escape_project(project, backend, relative):
    report = verify_project(project, backend, scenarios_path=relative)
    assert not report["ok"] and backend.calls == []


def test_scenario_symlink_escape_is_rejected(project, backend, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text((project / "scenarios.json").read_text())
    (project / "scenarios.json").unlink()
    (project / "scenarios.json").symlink_to(outside)
    assert not verify_project(project, backend)["ok"] and backend.calls == []


@pytest.mark.parametrize("target", ["main.st", "other.ST", "plcidf.json", "scenarios.json", "../report.json"])
def test_report_cannot_overwrite_input_or_escape_relative_root(project, backend, target):
    before = {p: p.read_bytes() for p in project.iterdir() if p.is_file()}
    report = verify_project(project, backend, report_path=target)
    assert not report["ok"] and backend.calls == []
    assert all(p.read_bytes() == data for p, data in before.items())


def test_report_cannot_alias_source_via_hardlink_or_symlink(project, backend):
    target = project / "report.json"
    target.symlink_to(project / "main.st")
    assert not verify_project(project, backend, report_path=target)["ok"]
    target.unlink()
    os.link(project / "main.st", target)
    assert not verify_project(project, backend, report_path=target)["ok"]
    assert backend.calls == []


def test_explicit_absolute_report_can_live_outside_project(project, backend, tmp_path):
    target = tmp_path / "collected" / "report.json"
    report = verify_project(project, backend, report_path=target)
    assert report["ok"] and json.loads(target.read_text()) == report


def test_compiler_diagnostics_preserved_without_deploy(project, backend):
    backend.fail_build = True
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "build"
    assert "invalid ST expression" in report["build"]["diagnostics"]
    assert backend.calls == ["build"]


def test_wrong_compiler_source_identity_prevents_deployment(project, backend):
    backend.build_hook = lambda result: result.update(source_sha256="f" * 64)
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "build"
    assert backend.calls == ["build"]


def test_source_change_during_build_and_after_replay_are_detected(project, backend):
    source = project / "main.st"
    backend.build_hook = lambda _: source.write_text(source.read_text() + "\n(* changed *)\n")
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "build" and backend.calls == ["build"]
    backend.build_hook = None
    backend.replay_hook = lambda _: source.write_text(source.read_text() + "\n(* changed later *)\n")
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "input_stability"


def test_scenarios_changed_after_replay_cannot_be_reported_current(project, backend):
    backend.replay_hook = lambda _: (project / "scenarios.json").write_text("{}")
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "input_stability"


def test_concurrent_manifest_change_cannot_turn_report_write_into_source_overwrite(project, backend):
    destination = project / "protected-source.txt"
    destination.write_text("preserve this source")
    def point_source_to_report(_):
        manifest = json.loads((project / "plcidf.json").read_text())
        manifest["source"] = destination.name
        (project / "plcidf.json").write_text(json.dumps(manifest))
    backend.replay_hook = point_source_to_report
    report = verify_project(project, backend, report_path=destination)
    assert not report["ok"] and not report["report_written"]
    assert destination.read_text() == "preserve this source"


def test_bom_and_crlf_source_have_raw_and_compiler_normalized_identity(project, backend):
    source = project / "main.st"
    source.write_bytes(b"\xef\xbb\xbf" + source.read_bytes().replace(b"\n", b"\r\n"))
    report = verify_project(project, backend)
    assert report["ok"]
    assert report["input_hashes"]["source"]["sha256"] != report["source_sha256"]
    assert report["input_hashes"]["source"]["normalized_sha256"] == report["source_sha256"]


def test_runtime_exception_stops_remaining_scenarios(project, backend):
    write_scenarios(project, [scenario("first"), scenario("second")])
    backend.fail_replay = True
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "replay"
    assert report["summary"]["failed"] == 1 and report["summary"]["skipped"] == 1
    assert backend.calls.count("replay") == 1


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(program_hash="d" * 64),
    lambda r: r["samples"][0].update(source_sha256="d" * 64),
    lambda r: r["samples"][0].update(scan_count=True),
    lambda r: r["samples"][0].update(scan_count=2),
    lambda r: r["samples"][0].update(elapsed_ns=1),
    lambda r: r["samples"][0].update(run_id="different"),
    lambda r: r["samples"][0]["values"].update(HEARTBEAT=1),
    lambda r: r["samples"].pop(),
    lambda r: r.update(samples_path="relative.jsonl"),
    lambda r: Path(r["samples_path"]).write_text("{}\n"),
])
def test_unverifiable_samples_fail_closed(project, backend, mutation):
    backend.replay_hook = mutation
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "replay"
    assert report["summary"]["passed"] == 0


def test_evidence_bool_cannot_be_replaced_with_equal_python_integer(project, backend):
    def replace_bool_with_integer(result):
        path = Path(result["samples_path"])
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[0]["values"]["HEARTBEAT"] = 0
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    backend.replay_hook = replace_bool_with_integer
    report = verify_project(project, backend)
    assert not report["ok"] and report["stage"] == "replay"
    assert report["diagnostics"][-1]["code"] == "E_EVIDENCE"


@pytest.mark.parametrize("timeout", [True, 0, -1, float("nan"), float("inf"), 31])
def test_invalid_timeout_never_builds(project, backend, timeout):
    report = verify_project(project, backend, timeout_s=timeout)
    assert not report["ok"] and backend.calls == []


def test_report_write_error_never_returns_verified(project, backend):
    parent = project / "not-a-directory"
    parent.write_text("preserve me")
    report = verify_project(project, backend, report_path=parent / "report.json")
    assert not report["ok"] and report["stage"] == "report"
    assert parent.read_text() == "preserve me"


def test_contract_failure_replaces_old_success_report_when_safe(project, backend):
    destination = project / ".plcidf" / "verification.json"
    assert verify_project(project, backend, report_path=destination)["ok"]
    (project / "main.st").write_text("not a complete ST project")
    backend.calls.clear()
    report = verify_project(project, backend, report_path=destination)
    assert not report["ok"] and report["stage"] == "check" and report["report_written"]
    assert not json.loads(destination.read_text())["ok"]
    assert backend.calls == []


def test_ambiguous_manifest_preserves_inputs_and_explicitly_reports_unwritten(project, backend):
    destination = project / ".plcidf" / "verification.json"
    assert verify_project(project, backend, report_path=destination)["ok"]
    old_report = destination.read_bytes()
    (project / "plcidf.json").write_text('{"source":"main.st","source":"other-file"}')
    report = verify_project(project, backend, report_path=destination)
    assert not report["ok"] and not report["report_written"]
    assert "report_error" in report and destination.read_bytes() == old_report


def test_generic_real_expectations_and_held_inputs(project, backend):
    manifest = json.loads((project / "plcidf.json").read_text())
    manifest["signals"] = {"inputs": {"SCALE": "REAL"}, "outputs": {"VALUE": "REAL"}}
    manifest["observables"] = manifest["signals"]["outputs"]
    (project / "plcidf.json").write_text(json.dumps(manifest))
    source = (project / "main.st").read_text().replace(
        "COUNTER : DINT", "COUNTER : DINT; SCALE : REAL; VALUE : REAL")
    (project / "main.st").write_text(source)
    backend.generic_output = lambda values, scan: {"VALUE": values["SCALE"] * 2}
    write_scenarios(project, [scenario("real", [
        {"scans": 1, "inputs": {"SCALE": 1.25}, "expect": {"VALUE": {"approx": 2.5, "abs_tol": .001}}},
        {"scans": 2, "expect": {"VALUE": 2.5}}])])
    report = verify_project(project, backend)
    assert report["ok"], report
    assert backend.signals == manifest["signals"]


@pytest.mark.parametrize("expected", [
    {"approx": 1, "abs_tol": -1}, {"approx": 1, "abs_tol": True},
    {"approx": 1, "abs_tol": 0, "rel_tol": 0}, {"approx": 1e39, "abs_tol": 0},
])
def test_invalid_real_approximation_never_builds(project, backend, expected):
    manifest = json.loads((project / "plcidf.json").read_text())
    manifest["signals"] = {"inputs": {}, "outputs": {"VALUE": "REAL"}}
    manifest["observables"] = manifest["signals"]["outputs"]
    (project / "plcidf.json").write_text(json.dumps(manifest))
    source = (project / "main.st").read_text().replace("COUNTER : DINT", "COUNTER : DINT; VALUE : REAL")
    (project / "main.st").write_text(source)
    write_scenarios(project, [scenario("bad-real", [{"scans": 1, "expect": {"VALUE": expected}}])])
    assert not verify_project(project, backend)["ok"] and backend.calls == []


def test_real_matiec_verification_when_toolchain_is_supplied(project, tmp_path):
    toolchain = os.environ.get("PLCIDF_OPEN_LINUX_TOOLCHAIN")
    if not toolchain:
        pytest.skip("real MatIEC integration needs PLCIDF_OPEN_LINUX_TOOLCHAIN")
    from plc_idf.backends.open_linux import OpenLinuxBackend
    backend = OpenLinuxBackend(toolchain, tmp_path / "real-state")
    report = verify_project(project, backend)
    assert report["ok"], report
