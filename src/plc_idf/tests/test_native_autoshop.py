"""Temporary vendor-project fixtures and mocked UI calls; no Windows access."""

import json
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from plc_idf.artifacts import ArtifactError, verify_build_manifest
from plc_idf.config import load_config
from plc_idf.evidence import StrictEvidenceLogger
from plc_idf.native_autoshop import (NativeAutoShop, NativeAutoShopError,
    REQUIRED_ARTIFACTS, analyze_compile_log, parse_editor_title)


WINDOWS_PROJECT = r"E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV"


def title(path=WINDOWS_PROJECT, document="MAIN", dirty=False):
    return f"AutoShop V4.12.0.2  {path} - [{document}{'*' if dirty else ''}]"


def arm_object():
    header = bytearray(52)
    header[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HH", header, 16, 1, 40)
    return bytes(header) + b"fixture-object"


class FakeUI:
    def __init__(self, directory):
        self.directory = directory
        self.source = "x := 1;\n"
        self.dirty = False
        self.window_title = None
        self.mode = "positive"
        self.log = "编译完成: 0 错误, 0 警告"
        self.ops = []
        self.set_mismatch = False
        self.timeout_compile = False
        self.read_only = False

    def __call__(self, argv, timeout=None, cwd=None):
        action = argv[argv.index("-Action") + 1]
        value = argv[argv.index("-Value") + 1] if "-Value" in argv else ""
        self.ops.append((action, value))
        if action == "editor-set":
            assert value.startswith("\\\\wsl.localhost\\")
            local_path = "/" + value.split("\\", 4)[4].replace("\\", "/")
            self.source = Path(local_path).read_text(encoding="utf-8")
            if self.set_mismatch:
                self.source += "changed"
            self.dirty = True
        if action.startswith("editor-"):
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                "title": self.window_title or title(dirty=self.dirty), "source": self.source,
                "read_only": self.read_only}))
        if action == "keys" and value == "^s":
            self.dirty = False
            (self.directory / "MAIN.ST").write_bytes(b"AutoShop-native-source:" + self.source.encode())
        if action == "keys" and value == "{F7}":
            if self.timeout_compile:
                raise subprocess.TimeoutExpired(argv, timeout)
            if self.mode == "positive":
                for relative in REQUIRED_ARTIFACTS:
                    path = self.directory / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(arm_object() if relative.endswith(".o") else
                                     b"Success!\r\n" if relative.endswith(".rst") else b"packaged-output")
            elif self.mode == "bad-elf":
                for relative in REQUIRED_ARTIFACTS:
                    path = self.directory / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"Success!\r\n" if relative.endswith(".rst") else b"wrong-architecture")
        return SimpleNamespace(returncode=0, stderr="", stdout=self.log if action == "log" else "ok")


@pytest.fixture
def native(tmp_path):
    project = tmp_path / "working"
    project.mkdir()
    (project / "MAIN.ST").write_bytes(b"original-native-source")
    config_path = tmp_path / "config.toml"
    config_path.write_text("[bridge]\nenabled=true\nwindows_project='" + WINDOWS_PROJECT + "'\n")
    source = tmp_path / "main.st"
    source.write_text("x := 2;\n")
    fake = FakeUI(project)
    evidence = StrictEvidenceLogger(tmp_path / "evidence.jsonl")
    backend = NativeAutoShop(load_config(config_path), evidence=evidence,
        runner=fake, project_dir=project, lease_directory=tmp_path / "leases")
    return backend, fake, source


def compile_now(backend, source, **kwargs):
    return backend.compile(source, execute=True, authorized=True,
        working_project=WINDOWS_PROJECT, timeout_s=0.03, poll_interval_s=0.001, **kwargs)


def test_diff_preview_reads_only(native):
    backend, fake, source = native
    preview = backend.sync(source)
    assert preview["status"] == "preview" and preview["changed"]
    assert "-x := 1;" in preview["diff"] and "+x := 2;" in preview["diff"]
    assert fake.ops == [("editor-read", "")]
    assert fake.source == "x := 1;\n"


@pytest.mark.parametrize("bad_title", [title(WINDOWS_PROJECT + "_another"),
    title(document="FB"), title(dirty=True), title(r"E:\PLCProjects\ORIGINAL")])
def test_wrong_or_unsaved_editor_refused_before_write(native, bad_title):
    backend, fake, source = native
    fake.window_title = bad_title
    with pytest.raises(NativeAutoShopError):
        backend.sync(source, execute=True, authorized=True)
    assert fake.ops == [("editor-read", "")]


def test_sync_exact_readback_then_save(native):
    backend, fake, source = native
    source.write_bytes(b"x := 2;\r\n")
    result = backend.sync(source, execute=True, authorized=True)
    assert result["status"] == "verified"
    assert fake.source == "x := 2;\n" and not fake.dirty
    actions = [action for action, value in fake.ops]
    assert actions == ["editor-read", "editor-set", "editor-read", "keys", "editor-read"]
    assert b"x := 2;" in (backend.project_dir / "MAIN.ST").read_bytes()


def test_online_editor_refused_before_any_write_or_compile(native):
    backend, fake, source = native
    fake.read_only = True
    with pytest.raises(NativeAutoShopError, match="exit online monitoring"):
        compile_now(backend, source)
    assert fake.ops == [("editor-read", "")]
    assert (backend.project_dir / "MAIN.ST").read_bytes() == b"original-native-source"


def test_mismatched_readback_never_saves(native):
    backend, fake, source = native
    fake.set_mismatch = True
    with pytest.raises(NativeAutoShopError, match="readback differs"):
        backend.sync(source, execute=True, authorized=True)
    assert ("keys", "^s") not in fake.ops


def test_execute_requires_authorization_and_strict_evidence(native):
    backend, fake, source = native
    with pytest.raises(NativeAutoShopError):
        backend.sync(source, execute=True)
    backend.evidence = None
    with pytest.raises(NativeAutoShopError, match="StrictEvidenceLogger"):
        backend.sync(source, execute=True, authorized=True)
    assert fake.ops == []


def test_compile_positive_is_bound_to_new_complete_artifacts(native):
    backend, fake, source = native
    # Old success/products must be moved, even if their byte hashes later match.
    for relative in REQUIRED_ARTIFACTS:
        path = backend.project_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"OLD")
    result = compile_now(backend, source)
    assert result["ok"] and result["status"] == "verified"
    assert verify_build_manifest(result["manifest"])
    assert result["manifest"]["tool_versions"]["autoshop"] == "4.12.0.2"
    assert len(result["manifest"]["artifacts"]) == 5
    for relative in REQUIRED_ARTIFACTS:
        assert (Path(result["quarantine"]) / relative).read_bytes() == b"OLD"
    assert fake.ops.count(("keys", "{F7}")) == 1


def test_compile_binds_nested_native_download_candidates(native):
    backend, fake, source = native
    candidate = backend.project_dir / "PLC" / "userprog.foid"
    candidate.parent.mkdir()
    candidate.write_bytes(b"original nested vendor candidate")
    result = compile_now(backend, source)
    manifest = result["manifest"]
    assert result["ok"] and verify_build_manifest(manifest)
    assert "PLC/userprog.foid" in {item["path"] for item in manifest["source"]["files"]}
    candidate.write_bytes(b"changed nested vendor candidate")
    with pytest.raises(ArtifactError):
        verify_build_manifest(manifest)


def test_negative_compile_cannot_fall_back_to_old_success(native):
    backend, fake, source = native
    for relative in REQUIRED_ARTIFACTS:
        path = backend.project_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"Success!\r\n" if relative.endswith(".rst") else arm_object())
    fake.mode = "negative"
    fake.log = "编译结束: 1 错误, 0 警告"
    result = compile_now(backend, source)
    assert not result["ok"] and result["status"] == "failed"
    assert result["manifest"] is None and len(result["missing_artifacts"]) == 5
    assert (Path(result["quarantine"]) / "STTemp/MAIN.rst").exists()


def test_empty_or_ambiguous_log_is_unverified_even_with_artifacts(native):
    backend, fake, source = native
    fake.log = ""
    result = compile_now(backend, source)
    assert not result["ok"] and result["reason"] == "vendor-link-result-not-verified"
    assert result["manifest"] is None


def test_bad_elf_refused(native):
    backend, fake, source = native
    fake.mode = "bad-elf"
    result = compile_now(backend, source)
    assert not result["ok"] and result["reason"] == "compiler-marker-or-arm-elf-invalid"


def test_compile_requires_explicit_working_directory_before_sync(native):
    backend, fake, source = native
    with pytest.raises(NativeAutoShopError, match="explicit working-project"):
        backend.compile(source, execute=True, authorized=True)
    assert fake.ops == []


def test_compile_timeout_never_replays_f7(native):
    backend, fake, source = native
    fake.timeout_compile = True
    with pytest.raises(NativeAutoShopError, match="state may be unknown"):
        compile_now(backend, source)
    assert fake.ops.count(("keys", "{F7}")) == 1


def test_log_counts_and_ocr_ambiguity():
    assert analyze_compile_log("编译完成 0 错误 0 警告")["status"] == "verified"
    assert analyze_compile_log("Compilation finished: 1 errors")["status"] == "failed"
    assert analyze_compile_log("编译完成 O 错误")["status"] == "unverified"
    assert analyze_compile_log("")["status"] == "unverified"
    assert analyze_compile_log("0 warnings")["status"] == "unverified"
    assert analyze_compile_log("统计-- (0)错误, (28)步占用/(200000)总容量\n"
        "2026/10/17 18:23:05 完成程序编译")["status"] == "verified"
    assert analyze_compile_log("统计-- (1)错误\n完成编译")["status"] == "failed"
    assert analyze_compile_log("Compilation completed\nerrors0")["status"] == "verified"


def test_compile_count_does_not_consume_next_line_timestamp():
    log = "统计-- (0)错误\n2026/10/18 19:00:00 完成程序编译"
    result = analyze_compile_log(log)
    assert result["status"] == "verified" and result["error_count"] == 0
    assert analyze_compile_log("错误\n0\n完成编译")["status"] == "unverified"


def test_exact_title_parser_does_not_accept_path_prefix():
    assert parse_editor_title(title(), WINDOWS_PROJECT)["version"] == "4.12.0.2"
    with pytest.raises(NativeAutoShopError):
        parse_editor_title(title(WINDOWS_PROJECT + "_UNRELATED"), WINDOWS_PROJECT)


def test_actual_autoshop_unsaved_title_has_space_before_star():
    actual = f'AutoShop V4.12.0.2  {WINDOWS_PROJECT} - [MAIN *]'
    assert parse_editor_title(actual, WINDOWS_PROJECT, allow_dirty=True)['dirty'] is True
    with pytest.raises(NativeAutoShopError, match='unsaved'):
        parse_editor_title(actual, WINDOWS_PROJECT)
