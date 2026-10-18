"""Read-only native UI contracts, using synthetic MSAA captures only."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import subprocess
import pytest
import plc_idf.autoshop_online as online_module
from plc_idf.autoshop_online import (SCRIPT, OnlineReadError, parse_online_snapshot, read_online_snapshot)
from plc_idf.easy521_probe import VARIABLE_TYPES

PROJECT = r"E:\PLCProjects\PLCIDF_LAB_20261017\BESS_E521_DEV"
CONNECTION = {"online": True, "transport": "usb", "evidence_id": "separate-live-USB-record"}
HEADINGS = ["", "元件名称", "元件名称", "数据类型", "显示格式", "当前值", "错误码", "地址", "注释"]


def capture():
    values = {name: ("OFF" if kind == "BOOL" else "0") for name, kind in VARIABLE_TYPES.items()}
    values.update({"g_xBESS_Reset": "ON", "g_iBESS_Mode": "130", "g_rBESS_Confidence": "130.0",
                   "g_rBESS_SensVP": "52101", "g_rBESS_SensVQ": "1", "g_rBESS_InfluenceP": "100",
                   "g_rBESS_InfluenceQ": "100", "g_xBESS_ControllerReady": "ON", "M8000": "ON", "M8001": "OFF"})
    def row(index, fields):
        return {"name": "Row " + str(index), "role": "28", "children": [
            {"name": HEADINGS[i], "value": value, "role": "29"} for i, value in enumerate(fields)]}
    rows = [row(0, HEADINGS)]
    for index, (name, value) in enumerate(values.items(), 1):
        kind = VARIABLE_TYPES.get(name, "BOOL")
        rows.append(row(index, [str(index), "...", name, kind, "", value, "0", "", ""]))
    return {"schema_version": 1, "ok": True, "process_id": 21248, "status": "read-only-ui-probe",
            "title": "AutoShop V4.12.0.2  " + PROJECT + " - [MAIN]", "captured_at_utc": "2026-10-17T14:00:00Z",
            "grids": [{"handle": 5444160, "visible": True, "objects": [{"object_id": 4294967292,
                 "msaa": {"supported": True, "children": rows}}]}]}


def parse(payload, **kwargs):
    return parse_online_snapshot(payload, expected_project=PROJECT, expected_process_id=21248,
                                 connection_evidence=CONNECTION, **kwargs)


def test_native_msaa_values_use_last_name_column_and_current_value_column():
    result = parse(capture())
    assert result["ok"] and result["plc_state"] == "RUN"
    assert result["values"]["g_iBESS_Mode"] == 130
    assert result["values"]["g_xBESS_ControllerReady"] is True
    assert result["values"]["g_rBESS_SensVP"] == 52101.0
    assert set(VARIABLE_TYPES).issubset(result["values"])


def test_read_ui_values_cannot_supply_its_own_usb_connection_provenance():
    result = parse_online_snapshot(capture(), expected_project=PROJECT)
    assert result["values_read"] and not result["online"] and not result["ok"]


def test_hidden_stale_tables_are_refused():
    data = capture(); data["grids"][0]["visible"] = False
    result = parse(data)
    assert not result["ok"] and result["missing_variables"]


def test_exact_process_and_project_are_required():
    data = capture(); data["process_id"] = 1
    with pytest.raises(OnlineReadError, match="process"):
        parse(data)
    data = capture(); data["title"] = data["title"].replace("BESS_E521_DEV", "BESS_E521_DEV_OTHER")
    with pytest.raises(OnlineReadError, match="project"):
        parse(data)


def test_document_can_be_global_variable_table_without_changing_project():
    data = capture(); data["title"] = data["title"].replace("[MAIN]", "[变量表]")
    assert parse(data)["ok"]


@pytest.mark.parametrize("column,value", [(3, "REAL"), (5, "not-an-int"), (6, "5207")])
def test_type_value_and_native_error_fields_fail_closed(column, value):
    data = capture()
    row = next(row for row in data["grids"][0]["objects"][0]["msaa"]["children"]
               if row["children"][2]["value"] == "g_iBESS_Mode")
    row["children"][column]["value"] = value
    result = parse(data)
    assert not result["ok"] and result["errors"]


def test_nan_ui_text_is_not_a_valid_real():
    data = capture()
    row = next(row for row in data["grids"][0]["objects"][0]["msaa"]["children"]
               if row["children"][2]["value"] == "g_rBESS_Confidence")
    row["children"][5]["value"] = "NaN"
    assert not parse(data)["ok"]


def test_contradictory_system_bits_cannot_prove_running():
    data = capture()
    row = next(row for row in data["grids"][0]["objects"][0]["msaa"]["children"]
               if row["children"][2]["value"] == "M8001")
    row["children"][5]["value"] = "ON"
    result = parse(data)
    assert not result["ok"] and result["plc_state"] == "unverified"


def test_no_state_inferred_from_colors_names_or_successful_ui_command():
    data = capture()
    data["grids"][0]["objects"][0]["msaa"]["children"] = [
        row for row in data["grids"][0]["objects"][0]["msaa"]["children"]
        if row["children"][2]["value"] not in ("M8000", "M8001")]
    data["plc_state"] = "RUN"; data["color"] = "green"; data["run_command_succeeded"] = True
    result = parse(data)
    assert result["values_read"] and not result["ok"] and result["plc_state"] == "unverified"


def test_conflicting_visible_grids_are_unverified():
    data = capture(); other = copy.deepcopy(data["grids"][0]); other["handle"] = 99
    row = next(row for row in other["objects"][0]["msaa"]["children"] if row["children"][2]["value"] == "g_iBESS_Mode")
    row["children"][5]["value"] = "131"
    data["grids"].append(other)
    assert not parse(data)["ok"]


def test_reader_invokes_only_dedicated_getter_script_with_timeout(monkeypatch):
    calls = []
    def invoke(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(capture()).encode(), b"")
    monkeypatch.setattr(subprocess, "run", invoke)
    result = read_online_snapshot(expected_project=PROJECT, grid_handle=5444160, connection_evidence=CONNECTION)
    assert result["ok"]
    argv, kwargs = calls[0]
    assert "read_autoshop_online.ps1" in " ".join(argv)
    assert argv[argv.index("-AutoShopProcessId") + 1] == "0"
    assert argv[argv.index("-ExpectedProject") + 1] == PROJECT
    assert "-Action" not in argv and "-Execute" not in argv
    assert kwargs["timeout"] == 20 and len(calls) == 1


def test_reader_explicit_pid_rejects_a_different_captured_process(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(
        argv, 0, json.dumps(capture()).encode(), b""))
    with pytest.raises(OnlineReadError, match="process"):
        read_online_snapshot(expected_project=PROJECT, process_id=999)


@pytest.mark.parametrize("pid", [0, None, True, "21248", -1])
def test_successful_capture_requires_real_positive_process_identity(pid):
    data = capture(); data["process_id"] = pid
    with pytest.raises(OnlineReadError, match="process"):
        parse_online_snapshot(data, expected_project=PROJECT)


@pytest.mark.parametrize("project", ["BESS_E521_DEV", "E:relative", "E:\\PLCProjects\\*", "E:\\PLCProjects\\name\n"])
def test_reader_rejects_nonexact_project_before_invoking_powershell(project, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must not invoke reader"))
    with pytest.raises(OnlineReadError, match="project"):
        read_online_snapshot(expected_project=project)


def test_default_reader_path_uses_runtime_asset_for_wheel_install(monkeypatch, tmp_path):
    packaged = tmp_path / "packaged-read_autoshop_online.ps1"
    packaged.write_text("# fixture")
    located = []
    def locate(root, relative):
        located.append((root, relative))
        return packaged
    monkeypatch.setattr(online_module, "runtime_asset", locate)
    calls = []
    def invoke(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(capture()).encode(), b"")
    monkeypatch.setattr(subprocess, "run", invoke)
    monkeypatch.setattr(online_module, "to_windows_unc", lambda path: str(path))
    read_online_snapshot(expected_project=PROJECT)
    assert located == [(Path.cwd(), "plc/tools/read_autoshop_online.ps1")]
    assert calls[0][calls[0].index("-File") + 1] == str(packaged)


def test_reader_timeout_never_reports_values_or_state(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(subprocess, "run", timeout)
    result = read_online_snapshot(expected_project=PROJECT)
    assert not result["ok"] and not result["online"] and result["plc_state"] == "unverified"


def test_powershell_reader_has_no_input_or_setter_interfaces():
    source = SCRIPT.read_text()
    for forbidden in ("WriteProcessMemory", "VirtualAllocEx", "SendKeys", "PostMessage", "SendMessage",
                      "SetForegroundWindow", "SetCursorPos", "mouse_event", "DoDefaultAction(",
                      "BindingFlags.SetProperty", ".SetValue(", ".Invoke("):
        assert forbidden not in source
    assert "BindingFlags.GetProperty" in source
    assert "$AutoShopProcessId = 0" in source
    assert "ExpectedProject" in source and "$matchesProject.Count -ne 1" in source
    assert "21248" not in source
