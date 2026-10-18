"""Generic MAIN contracts and generated acceptance assets; no compiler/device."""

import json
import math
from pathlib import Path

import pytest

from plc_idf import open_project as project_mod
from plc_idf.open_contract import validate_signals, validate_value
from plc_idf.open_project import check_open_project, create_open_project


EXAMPLES = Path(__file__).resolve().parents[3] / "examples" / "open_linux"


def update_manifest(root, mutate):
    path = root / "plcidf.json"
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))


def test_default_counter_keeps_legacy_contract_and_adds_agent_instructions(tmp_path):
    root = tmp_path / "counter"
    result = create_open_project(root)
    assert result.ok and result.as_dict()["compiled"] is False
    assert "signals" not in result.manifest
    assert result.manifest["observables"] == {"COUNTER": "DINT", "HEARTBEAT": "BOOL"}
    assert not (root / "scenarios.json").exists()
    instructions = (root / "AGENTS.md").read_text()
    assert "plcidf check . --json" in instructions and "plcidf verify . --json" in instructions
    assert "program/source hashes" in instructions and "local-only" in instructions


def test_motor_generates_typed_contract_and_six_independent_scenarios(tmp_path):
    root = tmp_path / "motor"
    checked = create_open_project(root, template="motor")
    assert checked.ok, checked.as_dict()
    assert checked.manifest["observables"] == checked.manifest["signals"]["outputs"]
    assert set(checked.manifest["signals"]["inputs"]) == {"START", "STOP", "PERMISSIVE", "FAULT"}
    assert set(checked.manifest["signals"]["outputs"]) == {"MOTOR", "STARTING", "FAULTED"}
    scenarios = json.loads((root / "scenarios.json").read_text())
    assert scenarios["schema"] == "plcidf.scenarios.v1" and len(scenarios["scenarios"]) == 6
    boundary = scenarios["scenarios"][0]["steps"]
    assert [step["scans"] for step in boundary[:3]] == [1, 2, 1]
    assert [step["expect"]["MOTOR"] for step in boundary[:3]] == [False, False, True]
    assert (root / "main.st").read_bytes() == (EXAMPLES / "motor/main.st").read_bytes()


@pytest.mark.parametrize("cycle", [1, 10, 25, 1000])
def test_motor_period_changes_keep_the_physical_time_delay_boundary(tmp_path, cycle):
    root = tmp_path / f"motor_{cycle}"
    checked = create_open_project(root, template="motor", cycle_ms=cycle)
    assert checked.ok, checked.as_dict()
    assert f"T#{cycle}ms" in (root / "main.st").read_text()
    steps = json.loads((root / "scenarios.json").read_text())["scenarios"][0]["steps"]
    total, previous_time = 0, None
    for step in steps:
        assert type(step["scans"]) is int and step["scans"] > 0
        total += step["scans"]
        time_ms = (total - 1) * cycle
        if step["expect"]["MOTOR"]:
            assert previous_time < 60 <= time_ms
            break
        previous_time = time_ms
    else:
        pytest.fail("No delayed motor-on expectation")


def test_unknown_template_fails_before_creating_files(tmp_path):
    root = tmp_path / "unsupported"
    with pytest.raises(ValueError, match="template"):
        create_open_project(root, template="analog")
    assert not root.exists()


def test_explicit_signals_replace_counter_only_observables(tmp_path):
    root = tmp_path / "generic"
    create_open_project(root)
    update_manifest(root, lambda data: data.update(
        signals={"inputs": {"Input": "REAL"}, "outputs": {"Result": "REAL"}},
        observables={"Result": "REAL"}))
    (root / "main.st").write_text("""PROGRAM MAIN
VAR
 INPUT : REAL := 0.0;
 RESULT : REAL := 0.0;
END_VAR
RESULT := INPUT;
END_PROGRAM
CONFIGURATION Config0
RESOURCE Res0 ON PLC
 TASK scan (INTERVAL := T#20ms, PRIORITY := 0);
 PROGRAM instance0 WITH scan : MAIN;
END_RESOURCE
END_CONFIGURATION
""")
    checked = check_open_project(root)
    assert checked.ok, checked.as_dict()
    assert checked.as_dict()["compiled"] is False


@pytest.mark.parametrize("mutation,code", [
    (lambda data: data["observables"].update(EXTRA="BOOL"), "E_OBSERVABLES"),
    (lambda data: data["signals"]["inputs"].update(START="STRING"), "E_SIGNALS"),
    (lambda data: data["signals"]["outputs"].update(start="BOOL"), "E_SIGNALS"),
    (lambda data: data["io"]["bindings"].update(MOTOR="physical-output"), "E_IO"),
])
def test_invalid_signal_contracts_are_rejected(tmp_path, mutation, code):
    root = tmp_path / "motor"
    create_open_project(root, template="motor")
    update_manifest(root, mutation)
    assert code in {item.code for item in check_open_project(root).diagnostics}


@pytest.mark.parametrize("replacement", [
    "MOTOR : DINT := 0;",
    "(* MOTOR : BOOL := FALSE; *)",
    "MOTOR : ARRAY[0..1] OF BOOL;",
    "state : STRUCT MOTOR : BOOL; END_STRUCT;",
    "MOTOR AT %QX0.0 : BOOL;",
    "MOTOR : BOOL; motor : BOOL;",
])
def test_exported_fields_require_one_direct_main_scalar_declaration(tmp_path, replacement):
    root = tmp_path / "motor"
    create_open_project(root, template="motor")
    source = root / "main.st"
    source.write_text(source.read_text().replace("MOTOR : BOOL := FALSE;", replacement))
    assert "E_SIGNAL_DECLARATION" in {item.code for item in check_open_project(root).diagnostics}


def test_other_pou_or_string_literal_cannot_supply_a_missing_main_field(tmp_path):
    root = tmp_path / "motor"
    create_open_project(root, template="motor")
    source = root / "main.st"
    text = source.read_text().replace("    MOTOR : BOOL := FALSE;\n", "")
    text = "FUNCTION_BLOCK Other\nVAR MOTOR : BOOL; END_VAR\nEND_FUNCTION_BLOCK\n" + text
    text = text.replace("    START : BOOL", "    text : STRING := 'VAR MOTOR : BOOL; END_VAR';\n    START : BOOL")
    source.write_text(text)
    assert "E_SIGNAL_DECLARATION" in {item.code for item in check_open_project(root).diagnostics}


def test_direct_source_check_has_a_byte_limit(tmp_path, monkeypatch):
    root = tmp_path / "motor"
    create_open_project(root, template="motor")
    monkeypatch.setattr(project_mod, "MAX_SOURCE_BYTES", 128)
    checked = check_open_project(root)
    assert "E_SOURCE" in {item.code for item in checked.diagnostics}


@pytest.mark.parametrize("example", ["motor", "analog_hysteresis"])
def test_complete_examples_have_typed_scenario_inputs_and_expectations(example):
    root = EXAMPLES / example
    checked = check_open_project(root)
    assert checked.ok, checked.as_dict()
    signals = validate_signals(checked.manifest["signals"])
    document = json.loads((root / "scenarios.json").read_text())
    assert document["schema"] == "plcidf.scenarios.v1"
    seen, approximate_real_expectations = set(), 0
    for scenario in document["scenarios"]:
        assert scenario["name"] and scenario["name"] not in seen
        seen.add(scenario["name"])
        for step in scenario["steps"]:
            assert type(step["scans"]) is int and step["scans"] > 0
            for name, value in step["inputs"].items():
                validate_value(value, signals["inputs"][name])
            for name, value in step["expect"].items():
                kind = signals["outputs"][name]
                if isinstance(value, dict):
                    assert kind == "REAL" and set(value) == {"approx", "abs_tol"}
                    validate_value(value["approx"], kind)
                    assert math.isfinite(value["abs_tol"]) and value["abs_tol"] >= 0
                    approximate_real_expectations += 1
                else:
                    validate_value(value, kind)
    if example == "analog_hysteresis":
        assert signals["outputs"]["ECHO"] == "REAL" and approximate_real_expectations > 0
