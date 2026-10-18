"""Offline evidence checks; these are synthetic observations, not PLC execution."""
from __future__ import annotations
import csv
import io
from pathlib import Path
import pytest
from plc_idf.easy521_probe import (ADDRESS_CSV, IMPORT_CSV, PROBE_DIRECTORY, VARIABLE_TYPES,
    assess_probe_samples, inspect_probe, make_probe_body)


def sample(count, revision=1, state="RUN"):
    published = (count // 100) * 100
    values = {"g_xBESS_Enable": False, "g_xBESS_Reset": True,
              "g_xBESS_MeasurementsValid": False, "g_rBESS_Pcmd": 0.0, "g_rBESS_Qcmd": 0.0,
              "g_iBESS_Mode": count, "g_rBESS_Confidence": float(revision * count),
              "g_rBESS_SensVP": float(52100 + revision), "g_rBESS_SensVQ": float(revision),
              "g_xBESS_ControllerReady": bool((published // 100) % 2),
              "g_rBESS_InfluenceP": float(published), "g_rBESS_InfluenceQ": float(revision * published),
              "M8000": state == "RUN", "M8001": state == "STOP"}
    return {"online": True, "plc_state": state, "values": values}


@pytest.mark.parametrize("revision", [1, 2])
def test_known_body_matches_reviewed_existing_bindings(revision):
    path = PROBE_DIRECTORY / f"MAIN_plus{revision}_body.st"
    assert path.read_text() == make_probe_body(revision)
    result = inspect_probe(path, revision)
    assert result["ok"] and result["compiled"] is False and result["hardware_verified"] is False
    assert all(entry["modbus_register"] is None for entry in result["bindings"].values())
    assert set(result["bindings"]) == set(VARIABLE_TYPES)


@pytest.mark.parametrize("extra", ["Y0 := TRUE;\n", "D14 := 1;\n", "fbBESS(xEnable := TRUE);\n", "PROGRAM MAIN\n"])
def test_changed_body_or_physical_write_is_refused(tmp_path, extra):
    path = tmp_path / "bad.st"
    path.write_text(make_probe_body(1) + extra)
    result = inspect_probe(path, 1)
    assert not result["ok"] and result["status"] == "refused"


def test_explicit_binding_in_import_table_is_refused(tmp_path):
    rows = list(csv.DictReader(io.StringIO(IMPORT_CSV.read_text())))
    rows[0]["软元件地址"] = "Y0"
    target = tmp_path / "changed.csv"
    with target.open("w", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    result = inspect_probe(PROBE_DIRECTORY / "MAIN_plus1_body.st", 1, import_csv=target)
    assert not result["ok"] and any("explicit I/O binding" in error for error in result["errors"])


def test_changed_internal_symbol_address_is_refused(tmp_path):
    text = ADDRESS_CSV.read_text(encoding="utf-16").replace("g_iBESS_Mode\t0X2100130", "g_iBESS_Mode\t0X6990130")
    path = tmp_path / "addresses.csv"
    path.write_text(text, encoding="utf-16")
    result = inspect_probe(PROBE_DIRECTORY / "MAIN_plus1_body.st", 1, address_csv=path)
    assert not result["ok"] and any("internal symbol" in error for error in result["errors"])


@pytest.mark.parametrize("revision", [1, 2])
def test_running_observations_require_revision_progress_and_heartbeat(revision):
    result = assess_probe_samples([sample(20, revision), sample(130, revision), sample(270, revision)], revision)
    assert result["ok"] and result["cycle_time"] == "unverified"
    assert result["device_identity"] == "requires-independent-live-evidence"


def test_reported_run_with_frozen_values_is_unverified():
    result = assess_probe_samples([sample(130), sample(130), sample(130)], 1)
    assert not result["ok"] and any("progress" in error for error in result["errors"])


def test_old_variant_readback_cannot_validate_new_variant():
    result = assess_probe_samples([sample(20), sample(130), sample(270)], 2)
    assert not result["ok"] and any("revision/step" in error for error in result["errors"])


def test_wrong_accumulator_step_is_rejected():
    rows = [sample(20, 2), sample(130, 2), sample(270, 2)]
    rows[1]["values"]["g_rBESS_InfluenceQ"] = 100.0
    result = assess_probe_samples(rows, 2)
    assert not result["ok"] and any("+1/+2" in error for error in result["errors"])


def test_non_atomic_live_ui_fields_are_flagged_without_overriding_latched_evidence():
    rows = [sample(20), sample(130), sample(270)]
    rows[1]["values"]["g_rBESS_Confidence"] = 131.0
    result = assess_probe_samples(rows, 1)
    assert result["ok"] and result["warnings"]


def test_stop_requires_stable_memory_and_actual_stop_status():
    result = assess_probe_samples([sample(130, state="STOP") for _ in range(3)], 1, expected_state="STOP")
    assert result["ok"]
    changed = [sample(130, state="STOP"), sample(140, state="STOP"), sample(150, state="STOP")]
    result = assess_probe_samples(changed, 1, expected_state="STOP")
    assert not result["ok"] and any("continued changing" in error for error in result["errors"])


@pytest.mark.parametrize("name,value", [("g_xBESS_Enable", True), ("g_rBESS_Pcmd", 1.0),
    ("g_rBESS_SensVP", float("nan")), ("g_iBESS_Mode", 0), ("g_iBESS_Mode", True),
    ("M8000", False)])
def test_inconsistent_or_unsafe_observations_fail_closed(name, value):
    rows = [sample(20), sample(130), sample(270)]
    rows[1]["values"][name] = value
    assert not assess_probe_samples(rows, 1)["ok"]


def test_offline_or_missing_value_observations_are_not_execution_proof():
    rows = [sample(20), sample(130), sample(270)]
    rows[0]["online"] = False
    del rows[1]["values"]["g_rBESS_Pcmd"]
    assert not assess_probe_samples(rows, 1)["ok"]


def test_vendor_boolean_and_numeric_text_can_be_checked():
    rows = [sample(20), sample(130), sample(270)]
    for row in rows:
        row["values"] = {name: ("ON" if value else "OFF") if type(value) is bool else str(value)
                         for name, value in row["values"].items()}
    assert assess_probe_samples(rows, 1)["ok"]
