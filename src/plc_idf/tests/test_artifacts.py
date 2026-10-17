"""Deployment contracts use temporary source trees and have no hardware access."""

import copy
import hashlib
import json

import pytest

from plc_idf.artifacts import (ArtifactError, create_build_manifest,
                               create_deployment_plan, read_record,
                               snapshot_source_tree, verify_build_manifest,
                               verify_deployment_plan, write_record)


TARGET = {"busid": "6-4", "vidpid": "0471:a531", "model": "Easy521-0808TN"}


@pytest.fixture
def build(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.st").write_text("PROGRAM Main\nEND_PROGRAM\n")
    output = tmp_path / "app.bin"
    output.write_bytes(b"compiled application")
    return create_build_manifest(source, [output], target=TARGET,
                                 tool_versions={"compiler": "test-fixture-1"})


def make_plan(build):
    return create_deployment_plan(build, target=TARGET,
                                   backup={"method": "vendor-project-copy"},
                                   verification={"method": "vendor-compare"})


def test_build_plan_roundtrip_and_exact_bytes(build, tmp_path):
    plan = make_plan(build)
    path = write_record(tmp_path / "records" / "plan.json", plan)
    reread = read_record(path)
    assert verify_deployment_plan(reread, target=TARGET,
                                   expected_plan_hash=plan["plan_hash"]) == plan
    assert build["artifacts"][0]["sha256"] == hashlib.sha256(b"compiled application").hexdigest()


def test_source_change_invalidates_build_and_plan(build):
    plan = make_plan(build)
    from pathlib import Path
    (Path(build["source"]["root"]) / "main.st").write_text("PROGRAM Replaced\nEND_PROGRAM")
    with pytest.raises(ArtifactError, match="Source tree has changed"):
        verify_deployment_plan(plan)


def test_source_addition_invalidates_complete_tree_snapshot(build):
    from pathlib import Path
    (Path(build["source"]["root"]) / "hidden.st").write_text("new input")
    with pytest.raises(ArtifactError, match="Source tree has changed"):
        verify_build_manifest(build)


def test_artifact_swap_invalidates_confirmed_plan(build):
    plan = make_plan(build)
    from pathlib import Path
    Path(build["artifacts"][0]["path"]).write_bytes(b"different application")
    with pytest.raises(ArtifactError, match="artifact has changed"):
        verify_deployment_plan(plan, expected_plan_hash=plan["plan_hash"])


def test_wrong_connected_device_refused(build):
    plan = make_plan(build)
    with pytest.raises(ArtifactError, match="Observed device differs"):
        verify_deployment_plan(plan, target=dict(TARGET, busid="6-3"))


def test_plan_field_tamper_is_refused(build):
    plan = make_plan(build)
    plan["verification"] = {"method": "skip"}
    with pytest.raises(ArtifactError, match="integrity"):
        verify_deployment_plan(plan)


def test_recomputed_plan_cannot_reuse_previous_confirmation(build):
    original = make_plan(build)
    changed = copy.deepcopy(original)
    changed["verification"] = {"method": "another-method"}
    content = {key: value for key, value in changed.items() if key != "plan_hash"}
    changed["plan_hash"] = hashlib.sha256(json.dumps(content, ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    with pytest.raises(ArtifactError, match="Previously confirmed"):
        verify_deployment_plan(changed, expected_plan_hash=original["plan_hash"])


def test_changed_build_target_refused_at_plan_creation(build):
    with pytest.raises(ArtifactError, match="differs from the build"):
        create_deployment_plan(build, target=dict(TARGET, busid="6-3"),
                               backup={"method": "copy"}, verification={"method": "compare"})


def test_sources_symlink_and_parent_escape_refused(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside.st"
    outside.write_text("PROGRAM Other")
    (source / "main.st").symlink_to(outside)
    with pytest.raises(ArtifactError, match="symlinks"):
        snapshot_source_tree(source)
    with pytest.raises(ArtifactError, match="within its root"):
        snapshot_source_tree(source, ["../outside.st"])


def test_selection_directory_detects_new_files_but_excludes_outputs(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.st").write_text("source")
    artifact = tmp_path / "app.bin"
    artifact.write_bytes(b"application")
    build = create_build_manifest(tmp_path, [artifact], target=TARGET,
                                  tool_versions={"compiler": "fixture"}, source_paths=["src"])
    (tmp_path / "unrelated.log").write_text("later log")
    assert verify_build_manifest(build)
    (tmp_path / "src" / "added.st").write_text("additional source")
    with pytest.raises(ArtifactError, match="Source tree has changed"):
        verify_build_manifest(build)


def test_record_file_can_be_excluded_from_source_tree(tmp_path):
    (tmp_path / "main.st").write_text("source")
    output = tmp_path / "app.bin"
    output.write_bytes(b"application")
    build = create_build_manifest(tmp_path, [output], target=TARGET,
        tool_versions={"compiler": "fixture"}, exclude_paths=["records"])
    write_record(tmp_path / "records" / "build.json", build)
    assert verify_build_manifest(build)
