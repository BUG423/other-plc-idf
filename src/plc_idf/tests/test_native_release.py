"""Local provenance tests; synthetic files never attest device byte readback."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from plc_idf.artifacts import create_build_manifest, verify_build_manifest
from plc_idf.native_release import (CompilationProofRequired, NativeReleaseError, capture_release,
    compile_baseline, freeze_intent, fresh_compile_proof, verify_intent, verify_release)


PROJECT = r"E:\PLCProjects\UNIT_NATIVE"
START = "2026-10-18T19:37:37.250000+08:00"
END = "2026-10-18T19:37:41+08:00"
TRACE = ("2026/10/18 19:37:38  开始编译\n统计-- (0)错误, (28)步占用/(200000)总容量\n"
         "2026/10/18 19:37:40  完成程序编译\n2026/10/18 19:37:40  完成编译")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rewrite(path, content):
    path = Path(path)
    stamp = path.stat().st_mtime_ns
    path.write_bytes(content)
    os.utime(path, ns=(stamp + 2_000_000_000, stamp + 2_000_000_000))


@pytest.fixture
def release_fixture(tmp_path):
    project = tmp_path / "working"
    project.mkdir()
    for name in ("MAIN.ST", "VarList.gdt", "Config.cfg"):
        (project / name).write_text("unit native input " + name)
    artifacts = []
    for relative in ("STTemp/MAIN.rst", "STTemp/MAIN.o", "Temp/stprog.o", "Output.prg", "Output.sdt"):
        path = project / relative
        path.parent.mkdir(exist_ok=True, parents=True)
        if relative.endswith(".o"):
            content = bytearray(52)
            content[:7] = b"\x7fELF\x01\x01\x01"
            content[18:20] = b"\x28\x00"
            path.write_bytes(content)
        else:
            path.write_bytes(b"Success!\r\n" if relative.endswith(".rst") else b"unit output")
        artifacts.append(path)
    manifest = create_build_manifest(project, artifacts, target={"busid": "6-4", "vidpid": "0471:a531"},
        tool_versions={"autoshop": "unit-fixture"}, source_paths=["MAIN.ST", "VarList.gdt", "Config.cfg"])
    intent = freeze_intent(manifest, operation_id="unit-operation", working_project=PROJECT)
    baseline = compile_baseline(intent)
    evidence = tmp_path / "compiler-evidence.json"
    evidence.write_text(json.dumps({"source": "UNIT TEST not real vendor evidence", "stdout": TRACE}))
    refs = [{"path": str(evidence), "sha256": sha(evidence), "source": "unit-compile-log"}]
    return project, intent, baseline, refs


def rebuild(fixture):
    project, intent, baseline, refs = fixture
    for relative in baseline["files"]:
        path = project / relative
        rewrite(path, path.read_bytes() + (b"revision" if not relative.endswith(".rst") else b""))
    return fresh_compile_proof("", TRACE, started_at=START, observed_at=END, evidence=refs)


def test_unchanged_pre_confirm_release_does_not_claim_fresh_f8_compile(release_fixture, tmp_path):
    _, intent, baseline, _ = release_fixture
    release = capture_release(intent, baseline, directory=tmp_path / "releases")
    assert release["transition"]["kind"] == "unchanged-pre-confirm"
    assert release["transition"]["compile"] is None
    assert release["intent_build_hash"] == intent["manifest"]["build_hash"]
    assert release["download_binding"]["status"] == "unverified"
    verify_release(release, live_outputs=True)
    verify_build_manifest(intent["manifest"])


def test_real_recompile_requires_fresh_proof_and_keeps_original_intent(release_fixture, tmp_path):
    project, intent, baseline, _ = release_fixture
    original = json.dumps(intent, sort_keys=True)
    proof = rebuild(release_fixture)
    release = capture_release(intent, baseline, proof, directory=tmp_path / "releases")
    assert release["transition"]["kind"] == "vendor-f8-recompile"
    assert json.dumps(intent, sort_keys=True) == original
    assert release["release_hash"] != intent["manifest"]["build_hash"]
    verify_release(release, live_outputs=True)
    with pytest.raises(Exception, match="changed"):
        verify_build_manifest(intent["manifest"])
    stored = Path(release["artifacts"][0]["path"]).parent / "release.json"
    assert json.loads(stored.read_text()) == release


def test_compare_recompile_cannot_overwrite_release(release_fixture, tmp_path):
    project, intent, baseline, _ = release_fixture
    release = capture_release(intent, baseline, directory=tmp_path / "releases")
    old = json.dumps(release, sort_keys=True)
    rebuild(release_fixture)
    verify_release(release)  # Inputs unchanged; frozen files retained.
    assert json.dumps(release, sort_keys=True) == old
    with pytest.raises(NativeReleaseError, match="candidate changed"):
        verify_release(release, live_outputs=True)


@pytest.mark.parametrize("name", ["MAIN.ST", "VarList.gdt", "Config.cfg", "new_config.cfg", "sub/new_variable.gvt"])
def test_any_source_variable_or_configuration_drift_refuses_release(release_fixture, tmp_path, name):
    project, intent, baseline, _ = release_fixture
    path = project / name
    path.parent.mkdir(exist_ok=True)
    path.write_text("unplanned change")
    with pytest.raises(NativeReleaseError, match="drift|selection"):
        capture_release(intent, baseline, directory=tmp_path / "release")


def test_deleting_unclassified_steps_is_input_drift(release_fixture, tmp_path):
    project, *_ = release_fixture
    (project / "steps.dat").write_text("unclassified original bytes")
    artifacts = [project / p for p in release_fixture[2]["files"]]
    manifest = create_build_manifest(project, artifacts, target={"busid": "6-4", "vidpid": "0471:a531"},
        tool_versions={"autoshop": "unit"}, source_paths=["MAIN.ST", "VarList.gdt", "Config.cfg", "steps.dat"])
    intent = freeze_intent(manifest, operation_id="unit", working_project=PROJECT)
    baseline = compile_baseline(intent)
    (project / "steps.dat").unlink()
    with pytest.raises(NativeReleaseError, match="input drift"):
        capture_release(intent, baseline, directory=tmp_path / "release")


def test_incomplete_input_manifest_is_refused(release_fixture):
    project, intent, *_ = release_fixture
    (project / "unrecorded.gvt").write_text("unrecorded native variable definition")
    with pytest.raises(NativeReleaseError, match="cover all"):
        freeze_intent(intent["manifest"], operation_id="new", working_project=PROJECT)


@pytest.mark.parametrize("change", ["intent", "manifest", "baseline"])
def test_tampered_provenance_is_refused(release_fixture, tmp_path, change):
    _, intent, baseline, _ = release_fixture
    if change == "intent":
        intent["working_project"] = "different"
    elif change == "manifest":
        intent["manifest"]["artifacts"][0]["sha256"] = "0" * 64
    else:
        baseline["files"]["STTemp/MAIN.o"]["sha256"] = "0" * 64
    with pytest.raises(NativeReleaseError, match="integrity"):
        capture_release(intent, baseline, directory=tmp_path / "release")


@pytest.mark.parametrize("bad_trace", [
    TRACE.replace("(0)错误", "(1)错误"), TRACE.replace("统计-- (0)错误", "统计-- (?)错误"),
    TRACE.split("统计--")[0], TRACE.replace("19:37:38", "19:37:36"),
    TRACE.replace("19:37:40", "19:37:45"),
    TRACE.replace("19:37:38", "19:37:39").replace("19:37:40", "19:37:38"),
])
def test_incomplete_negative_stale_or_future_compile_is_not_a_release(release_fixture, bad_trace):
    refs = release_fixture[3]
    with pytest.raises(NativeReleaseError):
        fresh_compile_proof("", bad_trace, started_at=START, observed_at=END, evidence=refs)


def test_identical_old_f7_log_is_not_fresh_f8_evidence(release_fixture):
    with pytest.raises(NativeReleaseError, match="unique fresh"):
        fresh_compile_proof(TRACE, TRACE, started_at=START, observed_at=END, evidence=release_fixture[3])


def test_changed_outputs_without_proof_cannot_use_unchanged_branch(release_fixture, tmp_path):
    _, intent, baseline, _ = release_fixture
    rebuild(release_fixture)
    with pytest.raises(CompilationProofRequired):
        capture_release(intent, baseline, directory=tmp_path / "releases")


def test_partial_cached_outputs_do_not_pass_fresh_compile(release_fixture, tmp_path):
    project, intent, baseline, refs = release_fixture
    rewrite(project / "STTemp/MAIN.o", (project / "STTemp/MAIN.o").read_bytes() + b"new")
    proof = fresh_compile_proof("", TRACE, started_at=START, observed_at=END, evidence=refs)
    with pytest.raises(NativeReleaseError, match="not fresh"):
        capture_release(intent, baseline, proof, directory=tmp_path / "releases")


def test_non_arm_and_missing_markers_are_refused(release_fixture, tmp_path):
    project, intent, baseline, _ = release_fixture
    proof = rebuild(release_fixture)
    rewrite(project / "STTemp/MAIN.o", b"not ARM")
    with pytest.raises(NativeReleaseError, match="not ELF32"):
        capture_release(intent, baseline, proof, directory=tmp_path / "releases")


def test_immutable_copy_or_release_record_tampering_is_detected(release_fixture, tmp_path):
    _, intent, baseline, _ = release_fixture
    release = capture_release(intent, baseline, directory=tmp_path / "release")
    path = Path(release["artifacts"][0]["path"])
    path.chmod(0o644)
    path.write_bytes(b"tampered frozen copy")
    with pytest.raises(NativeReleaseError, match="Immutable"):
        verify_release(release)


def test_persisted_release_json_tampering_is_detected(release_fixture, tmp_path):
    _, intent, baseline, _ = release_fixture
    release = capture_release(intent, baseline, directory=tmp_path / "release")
    path = Path(release["record_path"])
    path.chmod(0o644)
    path.write_text('{"status":"invented success"}')
    with pytest.raises(NativeReleaseError, match="record changed"):
        verify_release(release)
    release["download_binding"]["status"] = "verified"
    with pytest.raises(NativeReleaseError, match="integrity"):
        verify_release(release)


def test_capture_timeout_has_no_resume_or_retry(release_fixture, tmp_path):
    _, intent, baseline, _ = release_fixture
    calls = []
    def expired():
        calls.append(1)
        raise TimeoutError("unit time budget expired")
    with pytest.raises(TimeoutError):
        capture_release(intent, baseline, directory=tmp_path / "release", check_time=expired)
    assert calls == [1]
    assert not (tmp_path / "release").exists()
