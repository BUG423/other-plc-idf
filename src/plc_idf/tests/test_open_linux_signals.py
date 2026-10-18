"""Typed I/O and deterministic replay, using the real pinned ST compiler."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import time

import pytest
import plc_idf.backends.open_linux as runtime_module
from plc_idf.backends.open_linux import OpenLinuxBackend, OpenLinuxError, _digest, _write_json, _profile
from plc_idf.open_contract import validate_signals, validate_value, REAL_MAX

SIGNALS = {"inputs": {"START": "BOOL", "PERMIT": "BOOL", "LEVEL": "REAL"},
           "outputs": {"MOTOR": "BOOL", "ALARM": "BOOL", "DELAYED": "BOOL", "ECHO": "REAL"}}
SOURCE = """PROGRAM MAIN
VAR
START : BOOL := FALSE;
PERMIT : BOOL := TRUE;
LEVEL : REAL := 0.0;
MOTOR : BOOL := FALSE;
ALARM : BOOL := FALSE;
DELAYED : BOOL := FALSE;
ECHO : REAL := 0.0;
TIMER : TON;
END_VAR
MOTOR := START AND PERMIT;
ALARM := LEVEL >= 10.5;
ECHO := LEVEL;
TIMER(IN := START, PT := T#60ms);
DELAYED := TIMER.Q;
END_PROGRAM
CONFIGURATION Config0
RESOURCE Res0 ON PLC
TASK scan (INTERVAL := T#20ms, PRIORITY := 0);
PROGRAM instance0 WITH scan : MAIN;
END_RESOURCE
END_CONFIGURATION
"""


def test_signals_normalize_types_and_preserve_names():
    assert validate_signals({"outputs": {"Motor": "bool"}, "inputs": {"z": "int", "A": "REAL"}}) == {
        "inputs": {"A": "REAL", "z": "INT"}, "outputs": {"Motor": "BOOL"}}
    assert validate_signals({}) == {"inputs": {}, "outputs": {}}


@pytest.mark.parametrize("mapping", [None, [], {"unknown": {}}, {"inputs": []},
    {"inputs": {"START": "BOOL", "start": "BOOL"}},
    {"inputs": {"START": "BOOL"}, "outputs": {"start": "BOOL"}},
    {"outputs": {"a.b": "BOOL"}}, {"outputs": {"9a": "INT"}},
    {"outputs": {"a__b": "INT"}}, {"outputs": {"a_": "INT"}},
    {"outputs": {"IF": "BOOL"}}, {"outputs": {"MOTOR": "STRING"}},
    {"outputs": {"MOTOR": None}}, {"outputs": {str(i): "BOOL" for i in range(65)}},
    {"outputs": {"signal" + str(i): "BOOL" for i in range(65)}},
])
def test_invalid_signals_rejected(mapping):
    with pytest.raises(ValueError):
        validate_signals(mapping)


@pytest.mark.parametrize("value,kind,expected", [(True, "BOOL", True), (-32768, "INT", -32768),
    (32767, "INT", 32767), (-2147483648, "DINT", -2147483648),
    (2147483647, "DINT", 2147483647), (65535, "UINT", 65535),
    (4294967295, "UDINT", 4294967295), (1, "REAL", 1.0), (REAL_MAX, "REAL", REAL_MAX)])
def test_legal_scalar_boundaries(value, kind, expected):
    assert validate_value(value, kind) == expected


@pytest.mark.parametrize("value,kind", [(1, "BOOL"), (False, "INT"), (1.0, "DINT"),
    (32768, "INT"), (-32769, "INT"), (2147483648, "DINT"),
    (-1, "UINT"), (65536, "UINT"), (4294967296, "UDINT"),
    (True, "REAL"), (math.nan, "REAL"), (math.inf, "REAL"),
    (REAL_MAX * 2, "REAL"), (10 ** 400, "REAL"), ("1", "REAL")])
def test_invalid_scalar_boundaries_rejected(value, kind):
    with pytest.raises(ValueError):
        validate_value(value, kind)


def test_comments_and_literal_words_do_not_create_fake_resources():
    source = "// PROGRAM fake RESOURCE fake\n(* nested (* PROGRAM *) RESOURCE *)\n" + SOURCE
    source = source.replace("START : BOOL", "NOTE : STRING := 'PROGRAM RESOURCE';\nSTART : BOOL")
    _profile(source, 20, SIGNALS)


@pytest.fixture(scope="module")
def toolchain():
    path = os.environ.get("PLCIDF_OPEN_LINUX_TOOLCHAIN")
    if not path:
        pytest.skip("real typed ST execution requires PLCIDF_OPEN_LINUX_TOOLCHAIN")
    return Path(path)


@pytest.fixture
def backend(toolchain, tmp_path):
    return OpenLinuxBackend(toolchain, tmp_path / "state")


def build_release(backend, tmp_path, source=SOURCE, signals=SIGNALS):
    path = tmp_path / "main.st"
    path.write_text(source)
    build = backend.build(path, tmp_path / "builds", signals=signals)
    assert build["ok"], build
    deployed = backend.deploy(build["manifest_path"], execute=True)
    return build, deployed


@pytest.fixture
def released(backend, tmp_path):
    return build_release(backend, tmp_path)


def test_true_interlock_real_threshold_ton_and_initial_retention(backend, released):
    build, deployed = released
    frames = [{}, {"START": True, "LEVEL": 11.0}, {}, {}, {}, {"PERMIT": False}, {"START": False}]
    replay = backend.replay(deployed["manifest_path"], frames)
    assert replay["ok"] and replay["status"] == "completed", replay
    rows = replay["samples"]
    assert [row["elapsed_ns"] for row in rows] == [i * 20_000_000 for i in range(7)]
    assert rows[0]["values"]["PERMIT"] is True  # Actual ST initialized input.
    assert [row["values"]["MOTOR"] for row in rows] == [False, True, True, True, True, False, False]
    assert [row["values"]["DELAYED"] for row in rows] == [False, False, False, False, True, True, False]
    assert [row["values"]["ALARM"] for row in rows] == [False, True, True, True, True, True, True]
    assert all(row["values"]["LEVEL"] == 11.0 for row in rows[1:])
    assert [row["scan_count"] for row in rows] == list(range(1, 8))
    assert all(row[key] == replay[key] for row in rows for key in
               ("run_id", "program_hash", "build_hash", "source_sha256", "binary_sha256"))
    assert [json.loads(line) for line in Path(replay["samples_path"]).read_text().splitlines()] == rows
    again = backend.replay(deployed["manifest_path"], frames)
    assert [row["values"] for row in again["samples"]] == [row["values"] for row in rows]
    manifest = json.loads(Path(build["manifest_path"]).read_text())
    assert manifest["profile"] == "open-linux-signals-v1"
    assert manifest["signals"] == validate_signals(SIGNALS)


def test_generic_realtime_run_monitor_stop(backend, released):
    _, deployed = released
    started = backend.run(deployed["manifest_path"], duration_s=3, sample_interval_ms=20)
    assert started["ok"] and started["sample"]["values"]["MOTOR"] is False
    time.sleep(.08)
    observed = backend.monitor(started["run_id"])
    assert observed["ok"] and observed["sample"]["scan_count"] > 1
    stopped = backend.stop(started["run_id"])
    assert stopped["ok"] and stopped["status"] == "stopped"
    assert stopped["sample"]["values"]["PERMIT"] is True


def test_first_scan_zero_time_and_real_max_round_trip(backend, released):
    _, deployed = released
    replay = backend.replay(deployed["manifest_path"], [{"START": True, "LEVEL": REAL_MAX}, {}, {}, {}])
    assert replay["ok"], replay
    assert [row["values"]["DELAYED"] for row in replay["samples"]] == [False, False, False, True]
    assert all(row["values"]["ECHO"] == REAL_MAX for row in replay["samples"])


@pytest.mark.parametrize("bad_frames", [[], [{}] * 10001, [None], [{"MOTOR": True}],
    [{"unknown": False}], [{"START": 1}], [{"LEVEL": float("nan")}],
    [{"START": True, "start": False}]])
def test_bad_frames_rejected_without_starting_runtime(backend, released, bad_frames):
    _, deployed = released
    with pytest.raises(ValueError):
        backend.replay(deployed["manifest_path"], bad_frames)
    assert not (backend.state_dir / "replays").exists()


@pytest.mark.parametrize("timeout", [0, 31, True, None, float("inf")])
def test_invalid_timeout_rejected(backend, released, timeout):
    with pytest.raises(ValueError):
        backend.replay(released[1]["manifest_path"], [{}], timeout_s=timeout)


def test_build_cannot_bypass_deployment(backend, released):
    with pytest.raises(ValueError, match="deployed"):
        backend.replay(released[0]["manifest_path"], [{}])


def test_only_direct_main_field_types_are_accepted(backend, tmp_path):
    source = "FUNCTION_BLOCK DECOY\nVAR MOTOR : DINT; END_VAR\nMOTOR := 1;\nEND_FUNCTION_BLOCK\n" + SOURCE
    path = tmp_path / "shadowed.st"
    path.write_text(source)
    with pytest.raises(ValueError, match="direct MAIN field"):
        backend.build(path, tmp_path / "builds", signals={"outputs": {"MOTOR": "DINT"}})


def test_signal_mapping_is_bound_to_program_hash(backend, tmp_path):
    first, _ = build_release(backend, tmp_path)
    second_signals = {"inputs": SIGNALS["inputs"], "outputs": {"MOTOR": "BOOL"}}
    second = backend.build(tmp_path / "main.st", tmp_path / "builds", signals=second_signals)
    assert second["ok"] and first["program_hash"] != second["program_hash"]


def test_all_integer_signal_types_real_c_execution(backend, tmp_path):
    declarations = "I:INT:=0; D:DINT:=0; U:UINT:=0; UD:UDINT:=0;\nOI:INT:=0; OD:DINT:=0; OU:UINT:=0; OUD:UDINT:=0;"
    source = SOURCE.replace(SOURCE[SOURCE.index("START : BOOL"):SOURCE.index("END_VAR")], declarations + "\n")
    body = "OI:=I; OD:=D; OU:=U; OUD:=UD;\n"
    source = source[:source.index("MOTOR :=")] + body + source[source.index("END_PROGRAM"):]
    signals = {"inputs": {"I": "INT", "D": "DINT", "U": "UINT", "UD": "UDINT"},
               "outputs": {"OI": "INT", "OD": "DINT", "OU": "UINT", "OUD": "UDINT"}}
    _, release = build_release(backend, tmp_path, source, signals)
    result = backend.replay(release["manifest_path"], [{"I": -32768, "D": -2147483648, "U": 65535, "UD": 4294967295}])
    assert result["ok"], result
    assert result["samples"][0]["values"] == {"I": -32768, "D": -2147483648, "U": 65535, "UD": 4294967295,
                                                    "OI": -32768, "OD": -2147483648, "OU": 65535, "OUD": 4294967295}


def test_nonreturning_scan_replay_is_killed_with_timeout(backend, tmp_path):
    source = SOURCE.replace("MOTOR := START AND PERMIT;", "WHILE TRUE DO MOTOR := FALSE; END_WHILE;")
    _, release = build_release(backend, tmp_path, source)
    before = time.monotonic()
    result = backend.replay(release["manifest_path"], [{}], timeout_s=.1)
    assert not result["ok"] and result["status"] == "timeout"
    assert result["returncode"] != 0 and time.monotonic() - before < 2


def test_nonfinite_output_never_becomes_valid_replay_or_monitor(backend, tmp_path):
    source = SOURCE.replace("LEVEL : REAL := 0.0;", "LEVEL : REAL := 3.0E38;").replace("ECHO := LEVEL;", "ECHO := LEVEL * LEVEL;")
    _, release = build_release(backend, tmp_path, source)
    result = backend.replay(release["manifest_path"], [{}])
    assert not result["ok"] and result["status"] == "failed"
    assert "non-finite" in str(result["errors"])
    run = backend.run(release["manifest_path"], duration_s=.1, sample_interval_ms=20)
    assert not run["ok"] and run["status"] == "sample-unverified"
    time.sleep(.15)
    observed = backend.monitor(run["run_id"])
    assert not observed["ok"] and observed["sample"]["values"]["ECHO"] is None


@pytest.mark.parametrize("runtime", [None, [], "bad", {}, {"sample_schema": True}])
def test_malformed_runtime_metadata_rejected(backend, released, runtime):
    path = Path(released[0]["manifest_path"])
    record = json.loads(path.read_text())
    record["runtime"] = runtime
    record["build_hash"] = _digest({key: value for key, value in record.items() if key != "build_hash"})
    _write_json(path, record)
    with pytest.raises(OpenLinuxError, match="runtime"):
        backend.deploy(path)


def test_every_replay_sample_identity_is_checked(backend, released, monkeypatch):
    parse = runtime_module._json_loads
    def corrupt_second(raw):
        value = parse(raw)
        if isinstance(value, dict) and value.get("scan_count") == 2:
            value["program_hash"] = "0" * 64
        return value
    monkeypatch.setattr(runtime_module, "_json_loads", corrupt_second)
    result = backend.replay(released[1]["manifest_path"], [{}, {}, {}])
    assert not result["ok"] and "identity mismatch" in str(result["errors"])


@pytest.mark.parametrize("field,value", [("elapsed_ns", None), ("elapsed_ns", "bad"),
    ("elapsed_ns", True), ("cycle_ns", 0), ("max_observed_lateness_ns", -1),
    ("state", "pretend-success"), ("values_valid", False)])
def test_malformed_latest_sample_fails_closed(backend, released, field, value):
    run = backend.run(released[1]["manifest_path"], duration_s=.02, sample_interval_ms=20)
    time.sleep(.06)
    observed = backend.monitor(run["run_id"])
    sample = observed["sample"]
    sample[field] = value
    with Path(observed["samples_path"]).open("a") as out:
        out.write(json.dumps(sample) + "\n")
    invalid = backend.monitor(run["run_id"])
    assert not invalid["ok"] and invalid["status"] == "sample-unverified"


def test_python_nan_json_is_not_accepted_as_a_valid_observation(backend, released):
    run = backend.run(released[1]["manifest_path"], duration_s=.02, sample_interval_ms=20)
    time.sleep(.06)
    observed = backend.monitor(run["run_id"])
    sample = observed["sample"]
    sample["values"]["LEVEL"] = math.nan
    with Path(observed["samples_path"]).open("a") as out:
        out.write(json.dumps(sample) + "\n")
    invalid = backend.monitor(run["run_id"])
    assert not invalid["ok"] and invalid["status"] == "sample-unverified"


def test_replay_interruption_kills_and_reaps_child(backend, tmp_path, monkeypatch):
    source = SOURCE.replace("MOTOR := START AND PERMIT;", "WHILE TRUE DO MOTOR := FALSE; END_WHILE;")
    _, release = build_release(backend, tmp_path, source)
    popen, children = runtime_module.subprocess.Popen, []
    def interrupted_process(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        wait = child.wait
        first = True
        def interrupt_once(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                raise KeyboardInterrupt()
            return wait(*args, **kwargs)
        child.wait = interrupt_once
        return child
    monkeypatch.setattr(runtime_module.subprocess, "Popen", interrupted_process)
    with pytest.raises(KeyboardInterrupt):
        backend.replay(release["manifest_path"], [{}], timeout_s=5)
    assert len(children) == 1 and children[0].poll() is not None
