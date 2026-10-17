"""Offline orchestration contracts only, not hardware or adapter calibration.

The fake adapter below writes explicitly marked unit-test observations. Its
passing path proves stage ordering and evidence binding, never PLC success.
"""

import hashlib
import zipfile
from pathlib import Path

import pytest

from plc_idf.artifacts import create_build_manifest
from plc_idf.config import load_config
from plc_idf.evidence import EvidenceWriteError, StrictEvidenceLogger
from plc_idf.lease import bridge_lease
from plc_idf.native_deploy import NativeDeploy, NativeDeployError, verified_build_record


WINDOWS_PROJECT = r"E:\PLCProjects\PLCIDF_LAB\MEMORY_PROBE"
TARGET = {"busid": "6-4", "vidpid": "0471:a531"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FakeAdapter:
    """Only used in tests; no production module supplies a passing adapter."""

    def __init__(self, directory):
        self.directory = directory
        self.calls = []
        self.calibrated = True
        self.changes = {}
        self.hooks = {}
        self.evidence = {}

    def describe(self):
        return {"name": "offline-unit-fixture", "calibrated": self.calibrated,
                "capabilities": {"download": True, "vendor_result": True, "program_compare": True}}

    def observation(self, phase, context):
        hook = self.hooks.get(phase)
        if hook is not None:
            hook(context)
        proof = self.directory / f"{context['operation_id']}-{phase}.txt"
        proof.write_text("FAKE UNIT TEST OBSERVATION: " + phase + "\n" + context["operation_id"])
        result = {"status": "verified", "operation_id": context["operation_id"],
                  "build_hash": context["build_hash"], "target": dict(context["target"]),
                  "working_project": context["working_project"],
                  "evidence": [{"path": str(proof), "sha256": sha(proof), "source": "unit-fixture-" + phase}]}
        if phase == "preflight":
            result.update(usb_state="Shared", identity_ambiguous=False,
                          unsaved_changes=False, auto_run=False)
        elif phase == "download":
            result["status"] = "completed"
        elif phase == "compare":
            result.update(matches=True, independent=True, method="vendor-program-compare")
        result.update(self.changes.get(phase, {}))
        self.evidence[phase] = result["evidence"]
        return result

    def preflight(self, context, *, timeout_s):
        self.calls.append("preflight")
        return self.observation("preflight", context)

    def download(self, context, *, timeout_s):
        self.calls.append("download")
        return self.observation("download", context)

    def verify_download(self, context, download_result, *, timeout_s):
        self.calls.append("vendor")
        assert download_result["operation_id"] == context["operation_id"]
        return self.observation("vendor", context)

    def compare_program(self, context, *, timeout_s):
        self.calls.append("compare")
        return self.observation("compare", context)


@pytest.fixture
def deployment(tmp_path):
    project = tmp_path / "working"
    project.mkdir()
    (project / "MAIN.ST").write_bytes(b"unit native source")
    source = tmp_path / "source.st"
    source.write_text("memory_probe := memory_probe + 1;\n")
    artifacts = []
    for relative in ("STTemp/MAIN.rst", "STTemp/MAIN.o", "Temp/stprog.o", "Output.prg", "Output.sdt"):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith(".o"):
            content = bytearray(52)
            content[:7] = b"\x7fELF\x01\x01\x01"
            content[18:20] = b"\x28\x00"
            path.write_bytes(content)
        else:
            path.write_bytes(b"Success!\r\n" if relative.endswith(".rst") else b"unit application bytes")
        artifacts.append(path)
    manifest = create_build_manifest(project, artifacts, target=TARGET,
        tool_versions={"autoshop": "unit-fixture"}, source_paths=["MAIN.ST"])
    build = {"ok": True, "status": "verified", "manifest": manifest,
             "sync": {"status": "verified", "executed": True, "project": WINDOWS_PROJECT,
                      "source": str(source), "source_sha256": sha(source), "source_text_sha256": sha(source)},
             "diagnostics": {"status": "verified", "error_count": 0, "source": "unit-compiler-log"}}
    backup_zip = tmp_path / "plc-upload.zip"
    with zipfile.ZipFile(backup_zip, "w") as archive:
        archive.writestr("MAIN.ST", "unit upload backup, not a real PLC upload")
    backup = {"kind": "plc-upload", "path": str(backup_zip), "sha256": sha(backup_zip),
              "target": dict(TARGET), "file_count": 1}
    facts = {"power_source": "usb-only", "outputs_isolated": True, "mode_switch": "RUN",
             "application_scope": "memory-only-probe", "source": "unit fixture physical facts",
             "observed_at": "2026-10-17T20:00:00+08:00"}
    config_file = tmp_path / "config.toml"
    config_file.write_text("[bridge]\nenabled=false\nwindows_project='" + WINDOWS_PROJECT + "'\n")
    cfg = load_config(config_file)
    evidence = StrictEvidenceLogger(tmp_path / "evidence.jsonl")
    fake = FakeAdapter(tmp_path)
    backend = NativeDeploy(cfg, evidence=evidence, adapter=fake,
                           project_dir=project, lease_directory=tmp_path / "leases")
    return backend, fake, build, backup, facts


def deploy_now(fixture, **kwargs):
    backend, fake, build, backup, facts = fixture
    arguments = dict(working_project=WINDOWS_PROJECT, backup=backup,
                     preflight=facts, execute=True, authorized=True)
    arguments.update(kwargs)
    return backend.deploy(build, **arguments)


def test_default_and_uncalibrated_adapter_are_unavailable_without_effects(deployment):
    backend, fake, *_ = deployment
    backend.adapter = None
    result = deploy_now(deployment)
    assert result["status"] == "unavailable" and result["download_attempts"] == 0
    backend.adapter = fake
    fake.calibrated = False
    assert deploy_now(deployment)["status"] == "unavailable"
    assert fake.calls == []


def test_preview_never_queries_the_live_adapter(deployment):
    result = deploy_now(deployment, execute=False, authorized=False)
    assert result["status"] == "preview" and result["ok"] and not result["executed"]
    assert deployment[1].calls == []


def test_single_session_authorization_and_usb_power_are_accepted(deployment):
    backend, fake, *_ = deployment
    assert backend.cfg.bridge["enabled"] is False  # No second configuration approval gate.
    result = deploy_now(deployment)
    assert result["ok"] and result["status"] == "verified"
    assert fake.calls == ["preflight", "download", "vendor", "compare"]
    assert result["download_attempts"] == 1
    assert result["runtime"]["status"] == "unobserved" and result["runtime"]["state"] == "unknown"
    assert result["runtime"]["run_requested"] is False
    assert result["plan"]["physical_facts"]["mode_switch"] == "RUN"
    assert result["plan"]["backup"]["restore_tested"] is False


def test_missing_authorization_refuses_before_live_queries(deployment):
    assert deploy_now(deployment, authorized=False)["status"] == "blocked"
    assert deployment[1].calls == []


def test_bare_manifest_or_unverified_compile_cannot_be_deployed(deployment):
    backend, fake, build, backup, facts = deployment
    result = backend.deploy(build["manifest"], working_project=WINDOWS_PROJECT,
        backup=backup, preflight=facts, execute=True, authorized=True)
    assert result["status"] == "blocked"
    build["status"] = "unverified"
    assert deploy_now(deployment)["status"] == "blocked"
    assert fake.calls == []


def test_verified_build_wrapper_roundtrip_and_tampering(deployment):
    backend, fake, build, backup, facts = deployment
    record = verified_build_record(build, WINDOWS_PROJECT)
    assert verified_build_record(record, WINDOWS_PROJECT) == record
    record["compilation"]["diagnostics"]["error_count"] = 1
    with pytest.raises(NativeDeployError, match="integrity"):
        verified_build_record(record, WINDOWS_PROJECT)


@pytest.mark.parametrize("changes", [
    {"usb_state": "Attached"}, {"identity_ambiguous": True}, {"unsaved_changes": True},
    {"auto_run": True}, {"target": {"busid": "6-3", "vidpid": "0471:a531"}},
    {"working_project": r"E:\PLCProjects\ORIGINAL"}, {"build_hash": "0" * 64},
])
def test_wrong_current_target_project_or_settings_stop_before_download(deployment, changes):
    deployment[1].changes["preflight"] = changes
    result = deploy_now(deployment)
    assert not result["ok"] and result["download_attempts"] == 0
    assert deployment[1].calls == ["preflight"]


@pytest.mark.parametrize("changes", [
    {"power_source": "unknown"}, {"outputs_isolated": "true"}, {"mode_switch": "UNKNOWN"},
    {"application_scope": "vendor-application"}, {"observed_at": "yesterday"},
])
def test_missing_physical_facts_or_non_probe_usb_application_are_refused(deployment, changes):
    deployment[4].update(changes)
    result = deploy_now(deployment)
    assert result["status"] == "blocked" and deployment[1].calls == []


def test_24v_may_declare_full_vendor_application_scope(deployment):
    deployment[4].update(power_source="24v", application_scope="vendor-application")
    assert deploy_now(deployment)["ok"]


def test_backup_hash_or_target_change_stops_before_queries(deployment):
    deployment[3]["sha256"] = "0" * 64
    assert deploy_now(deployment)["status"] == "blocked"
    assert deployment[1].calls == []


@pytest.mark.parametrize("field", ["source_sha256", "source_text_sha256"])
def test_sync_requires_recorded_source_hashes(deployment, field):
    del deployment[2]["sync"][field]
    assert deploy_now(deployment)["status"] == "blocked"
    assert deployment[1].calls == []


def test_source_or_artifact_swap_during_preflight_prevents_download(deployment):
    source = Path(deployment[2]["sync"]["source"])
    deployment[1].hooks["preflight"] = lambda context: source.write_text("changed after preflight")
    result = deploy_now(deployment)
    assert result["download_attempts"] == 0 and not result["ok"]


def test_uncertain_download_never_retries_or_advances(deployment):
    deployment[1].changes["download"] = {"status": "unknown"}
    result = deploy_now(deployment)
    assert result["status"] == "unknown" and result["download_attempts"] == 1
    assert deployment[1].calls == ["preflight", "download"]


def test_download_timeout_preserves_unknown_state_without_retry(deployment):
    def timeout(context):
        raise TimeoutError("unit unknown device state")
    deployment[1].hooks["download"] = timeout
    result = deploy_now(deployment)
    assert result["status"] == "unknown" and result["state_may_have_changed"]
    assert deployment[1].calls.count("download") == 1


def test_vendor_unverified_does_not_mean_success(deployment):
    deployment[1].changes["vendor"] = {"status": "unknown"}
    result = deploy_now(deployment)
    assert result["status"] == "unverified" and not result["ok"]
    assert deployment[1].calls == ["preflight", "download", "vendor"]


@pytest.mark.parametrize("changes", [
    {"matches": False}, {"independent": False}, {"method": "cpu-is-running"}, {"status": "unknown"},
])
def test_program_consistency_cannot_be_replaced_by_runtime_or_unknown_state(deployment, changes):
    deployment[1].changes["compare"] = changes
    assert deploy_now(deployment)["status"] == "unverified"


def test_reusing_the_download_log_is_not_independent_evidence(deployment):
    fake = deployment[1]
    fake.hooks["compare"] = lambda context: fake.changes.update(compare={"evidence": fake.evidence["vendor"]})
    assert deploy_now(deployment)["status"] == "unverified"


def test_overwritten_prior_proof_cannot_report_verified(deployment):
    fake = deployment[1]
    def overwrite_vendor_proof(context):
        Path(fake.evidence["vendor"][0]["path"]).write_text("old proof overwritten by another view")
    fake.hooks["compare"] = overwrite_vendor_proof
    result = deploy_now(deployment)
    assert result["status"] == "unknown" and not result["ok"]


def test_old_operation_evidence_cannot_confirm_a_new_attempt(deployment):
    deployment[1].changes["preflight"] = {"operation_id": "old-attempt"}
    result = deploy_now(deployment)
    assert result["download_attempts"] == 0 and not result["ok"]


def test_evidence_failure_before_intent_prevents_effects(deployment, monkeypatch):
    logger = deployment[0].evidence
    original = logger.log
    def fail_intent(command, *args, **kwargs):
        if command == "native_deploy.intent":
            raise EvidenceWriteError("unit disk failure")
        return original(command, *args, **kwargs)
    monkeypatch.setattr(logger, "log", fail_intent)
    result = deploy_now(deployment)
    assert result["download_attempts"] == 0 and result["evidence_write_failed"]
    assert deployment[1].calls == ["preflight"]


def test_evidence_failure_after_download_keeps_unknown_and_stops(deployment, monkeypatch):
    logger = deployment[0].evidence
    original = logger.log
    def fail_result(command, *args, **kwargs):
        if command == "native_deploy.download":
            raise EvidenceWriteError("unit disk failure")
        return original(command, *args, **kwargs)
    monkeypatch.setattr(logger, "log", fail_result)
    result = deploy_now(deployment)
    assert result["status"] == "unknown" and result["evidence_write_failed"]
    assert deployment[1].calls == ["preflight", "download"]


def test_busy_lease_prevents_adapter_queries(deployment):
    backend = deployment[0]
    with bridge_lease("6-4", "0471:a531", directory=backend.lease_directory):
        assert deploy_now(deployment)["status"] == "busy"
    assert deployment[1].calls == []
