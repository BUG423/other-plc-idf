"""CLI native routing and refusal tests: mocks only, never Windows or a PLC."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from plc_idf import cli, autoshop_online, native_deploy


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("external process in offline CLI test"))


def read_output(capsys):
    return json.loads(capsys.readouterr().out)


def test_verified_build_record_preserves_full_result_and_raw_manifest(tmp_cfg, tmp_path, monkeypatch, capsys):
    calls = []
    full = {"ok": True, "status": "verified", "executed": True,
            "manifest": {"build_hash": "fixture-hash", "artifacts": []},
            "sync": {"status": "verified", "source": "fixture-source"},
            "diagnostics": {"status": "verified", "error_count": 0, "source": "fixture"}}
    class Native:
        def __init__(self, cfg, **kwargs): pass
        def compile(self, source, **kwargs):
            calls.append((source, kwargs))
            return dict(full)
    monkeypatch.setattr(cli, "NativeAutoShop", Native)
    raw, record = tmp_path / "manifest.json", tmp_path / "verified.json"
    assert cli.main(["build", "--config", str(tmp_cfg), "--source", "source.st",
                     "--working-project", r"E:\PLCProjects\BESS_E521_DEV", "--manifest", str(raw),
                     "--build-record", str(record), "--execute", "--i-have-authorized", "--json"]) == 0
    assert json.loads(raw.read_text()) == full["manifest"]
    stored = json.loads(record.read_text())
    assert all(stored[key] == full[key] for key in full)
    assert stored["manifest_path"] == str(raw.resolve())
    assert read_output(capsys)["build_record_path"] == str(record.resolve())
    assert calls[0][1]["authorized"] is True and calls[0][1]["execute"] is True


@pytest.mark.parametrize("status,ok", [("preview", True), ("unverified", False), ("failed", False)])
def test_build_record_is_not_written_for_preview_or_unverified_compile(tmp_cfg, tmp_path, monkeypatch, capsys, status, ok):
    class Native:
        def __init__(self, *args, **kwargs): pass
        def compile(self, *args, **kwargs): return {"ok": ok, "status": status, "manifest": None}
    monkeypatch.setattr(cli, "NativeAutoShop", Native)
    record = tmp_path / "build.json"
    assert cli.main(["build", "--config", str(tmp_cfg), "--source", "source.st", "--build-record", str(record), "--json"]) == (0 if ok else 1)
    assert not record.exists()
    read_output(capsys)


@pytest.mark.parametrize("extra", [["--working-project", "E:\\Project"], ["--compile-timeout", "10"],
                                    ["--manifest", "raw.json"], ["--build-record", "build.json"]])
def test_native_build_options_require_source(tmp_cfg, extra, capsys):
    assert cli.main(["build", "--config", str(tmp_cfg), "--json", *extra]) == 2
    assert read_output(capsys)["ok"] is False


@pytest.mark.parametrize("action,extra", [
    ("build", ["--build-record", "build.json"]),
    ("build", ["--working-project", ""]),
    ("build", ["--i-have-authorized"]),
    ("flash", ["--build-record", "build.json"]),
    ("flash", ["--backup-record", "backup.json"]),
    ("flash", ["--field-facts", "facts.json"]),
    ("flash", ["--working-project", "E:\\Project"]),
    ("flash", ["--working-project", ""]),
    ("flash", ["--deploy-timeout", "10"]),
    ("monitor", ["--working-project", "E:\\Project"]),
    ("monitor", ["--process-id", "0"]),
    ("monitor", ["--grid-handle", "0"]),
    ("monitor", ["--transport", "vendor-usb"]),
    ("monitor", ["--once"]),
])
def test_open_linux_rejects_easy_only_options(tmp_cfg, action, extra, capsys):
    base = [action, "--config", str(tmp_cfg), "--target", "open-linux", "--json"]
    if action == "monitor": base += ["--run-id", "fixture"]
    if action == "flash": base += ["--manifest", "fixture.json"]
    assert cli.main([*base, *extra]) == 2
    assert read_output(capsys)["ok"] is False


def deployment_args(tmp_path):
    paths = []
    for name in ("build", "backup", "facts"):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps({"fixture": name}))
        paths.append(str(path))
    return ["flash", "--build-record", paths[0], "--backup-record", paths[1],
            "--field-facts", paths[2], "--working-project", r"E:\PLCProjects\BESS_E521_DEV", "--json"]


@pytest.mark.parametrize("execute,authorized", [(False, False), (True, True), (True, False)])
def test_native_flash_passes_bound_records_and_existing_authorization_flags(tmp_cfg, tmp_path, monkeypatch, capsys, execute, authorized):
    captured = []
    adapter = object()
    monkeypatch.setattr(cli, "_vendor_adapter", lambda cfg: adapter)
    class Deploy:
        def __init__(self, cfg, **kwargs):
            assert kwargs["adapter"] is adapter
        def deploy(self, record, **kwargs):
            captured.append((record, kwargs))
            return {"ok": authorized or not execute, "status": "verified" if execute and authorized else "preview" if not execute else "blocked"}
    monkeypatch.setattr(native_deploy, "NativeDeploy", Deploy)
    args = deployment_args(tmp_path) + ["--config", str(tmp_cfg), "--deploy-timeout", "75"]
    if execute: args.append("--execute")
    if authorized: args.append("--i-have-authorized")
    assert cli.main(args) == (0 if authorized or not execute else 1)
    assert captured == [({"fixture": "build"}, {"working_project": r"E:\PLCProjects\BESS_E521_DEV",
        "backup": {"fixture": "backup"}, "preflight": {"fixture": "facts"},
        "execute": execute, "authorized": authorized, "timeout_s": 75.0})]
    assert read_output(capsys)["target_profile"] == "easy521"


def test_incomplete_native_flash_records_are_refused_before_adapter(tmp_cfg, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_vendor_adapter", lambda *args: pytest.fail("adapter constructed"))
    assert cli.main(["flash", "--config", str(tmp_cfg), "--build-record", "b.json", "--json"]) == 2
    assert "--backup-record" in read_output(capsys)["diagnostics"][0]["message"]


def test_empty_source_cannot_fall_back_to_legacy_bridge(tmp_cfg, capsys):
    assert cli.main(["build", "--config", str(tmp_cfg), "--source", "", "--json"]) == 2
    assert read_output(capsys)["diagnostics"][0]["code"] == "E_ARGUMENT"


@pytest.mark.parametrize("text", ['[]', '{"key":1,"key":2}', '{"value":NaN}', '{"value":1e999}'])
def test_deploy_record_parser_rejects_nonobjects_duplicates_and_nan(tmp_path, text):
    path = tmp_path / "record.json"; path.write_text(text)
    with pytest.raises(ValueError): cli._read_json_record(path)


@pytest.mark.parametrize("extra", [["--host", "192.0.2.1"], ["--run-id", "r"],
    ["--interval", "1"], ["--word-order", "abcd"], ["--jsonl", "x.jsonl"],
    ["--with-bits"], ["--project-revision", "test"], ["--timeout", "nan"],
    ["--process-id", "-1"], ["--grid-handle", "-1"]])
def test_vendor_monitor_refuses_ignored_or_invalid_options_before_probe(tmp_cfg, extra, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_vendor_adapter", lambda *args: pytest.fail("adapter constructed"))
    assert cli.main(["monitor", "--config", str(tmp_cfg), "--transport", "vendor-usb", "--json", *extra]) == 2
    assert read_output(capsys)["ok"] is False


def test_vendor_monitor_preserves_unverified_values_and_actual_connection_provenance(tmp_cfg, monkeypatch, capsys):
    actual = {"ok": True, "status": "verified", "connection_verified": True,
              "online": True, "transport": "usb", "evidence_id": "fixture-query"}
    observed = []
    class Adapter:
        def observe_connection(self, *, timeout_s):
            observed.append(timeout_s)
            return actual
    monkeypatch.setattr(cli, "_vendor_adapter", lambda cfg: Adapter())
    def read(**kwargs):
        assert kwargs["connection_evidence"] == actual
        assert kwargs["process_id"] == 0 and kwargs["grid_handle"] == 0
        assert kwargs["expected_project"] == r"E:\PLCProjects\BESS_E521_DEV"
        return {"ok": False, "status": "unverified", "values_read": True,
                "plc_state": "unverified", "values": {"g_iBESS_Mode": 0}}
    monkeypatch.setattr(autoshop_online, "read_online_snapshot", read)
    assert cli.main(["monitor", "--config", str(tmp_cfg), "--transport", "vendor-usb"]) == 1
    result = read_output(capsys)
    assert result["values_read"] is True and result["ok"] is False
    assert result["transport"] == "vendor-usb" and observed == [20.0]


def test_vendor_monitor_online_label_without_verified_observation_is_not_trusted(tmp_cfg, monkeypatch, capsys):
    class Adapter:
        def observe_connection(self, **kwargs):
            return {"online": True, "transport": "usb", "evidence_id": "only-an-online-label"}
    monkeypatch.setattr(cli, "_vendor_adapter", lambda cfg: Adapter())
    def read(**kwargs):
        assert kwargs["connection_evidence"]["online"] is False
        return {"ok": False, "status": "unverified", "values_read": True, "plc_state": "unverified"}
    monkeypatch.setattr(autoshop_online, "read_online_snapshot", read)
    assert cli.main(["monitor", "--config", str(tmp_cfg), "--transport", "vendor-usb", "--json"]) == 1
    assert read_output(capsys)["connection_verified"] is False


def test_vendor_monitor_usage_errors_are_machine_json_even_without_json_flag(tmp_cfg, capsys):
    assert cli.main(["monitor", "--config", str(tmp_cfg), "--transport", "vendor-usb", "--timeout", "nan"]) == 2
    assert read_output(capsys)["diagnostics"][0]["code"] == "E_ARGUMENT"


def test_vendor_monitor_passes_only_remaining_total_budget_to_capture(tmp_cfg, monkeypatch, capsys):
    ticks = iter((100.0, 104.0))
    monkeypatch.setattr(cli, "monotonic", lambda: next(ticks))
    class Adapter:
        def observe_connection(self, *, timeout_s):
            assert timeout_s == 10.0
            return {"online": False, "status": "unverified"}
    monkeypatch.setattr(cli, "_vendor_adapter", lambda cfg: Adapter())
    def read(**kwargs):
        assert kwargs["timeout_s"] == 6.0
        return {"ok": False, "status": "unverified", "values_read": True}
    monkeypatch.setattr(autoshop_online, "read_online_snapshot", read)
    assert cli.main(["monitor", "--config", str(tmp_cfg), "--transport", "vendor-usb", "--timeout", "10"]) == 1
    assert read_output(capsys)["values_read"] is True


def test_vendor_monitor_exhausted_budget_never_starts_second_capture(tmp_cfg, monkeypatch, capsys):
    ticks = iter((100.0, 111.0))
    monkeypatch.setattr(cli, "monotonic", lambda: next(ticks))
    class Adapter:
        def observe_connection(self, *, timeout_s):
            return {"online": False, "status": "unverified", "evidence_id": "slow-fixture"}
    monkeypatch.setattr(cli, "_vendor_adapter", lambda cfg: Adapter())
    monkeypatch.setattr(autoshop_online, "read_online_snapshot", lambda **kwargs: pytest.fail("second capture after deadline"))
    assert cli.main(["monitor", "--config", str(tmp_cfg), "--transport", "vendor-usb", "--timeout", "10"]) == 1
    result = read_output(capsys)
    assert result["budget_exhausted"] is True and result["values_read"] is False
    assert result["status"] == "unverified" and not result["connection_verified"]
