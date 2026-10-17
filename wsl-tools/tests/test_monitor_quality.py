"""Monitor quality and failure boundaries using fake read-only clients only."""

from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

import e521_monitor as monitor
from e521_regs import encode_quantities


VALUES = {"Vpu": 1.0042, "Pline": 0.4521, "Qline": 0.1833, "loading": 0.4711,
          "SOC": 0.8021, "Pess": 0.3123, "Qess": -0.0521, "Pcmd": 0.3011,
          "Qcmd": 0.0497}


class Response:
    def __init__(self, registers=(), *, error=False):
        self.registers = list(registers)
        self._error = error

    def isError(self):
        return self._error


class Client:
    def __init__(self, words=None, *, error=False, exception=None):
        self.words = encode_quantities(VALUES, "abcd") if words is None else words
        self.error = error
        self.exception = exception
        self.calls = []
        self.closed = False

    def connect(self):
        return True

    def close(self):
        self.closed = True

    def read_holding_registers(self, *, address, count, device_id):
        self.calls.append((address, count, device_id))
        if self.exception:
            raise self.exception
        return Response((self.words + [0])[:count], error=self.error)


def run_client(monkeypatch, tmp_path, client, *extra):
    monkeypatch.setattr(monitor, "build_client", lambda args: (client, "fake://readonly"))
    output = tmp_path / "monitor.jsonl"
    rc = monitor.main(["--once", "--jsonl", str(output), *extra])
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    return rc, rows


def test_inconclusive_calibration_never_falls_back_or_decodes(monkeypatch, tmp_path, capsys):
    client = Client([0] * 18)
    rc, rows = run_client(monkeypatch, tmp_path, client, "--calibrate")
    assert rc == monitor.CALIBRATION_EXIT_CODE == 3
    assert client.closed and len(client.calls) == 1
    row, = rows
    assert row["word_order"] is None and row["word_order_source"] == "unresolved"
    assert row["registers"] == [0] * 18 and row["values"] == {}
    assert row["quality"]["decoded"] is False
    assert row["quality"]["read"] == "ok"  # Read succeeded; interpretation did not.
    assert row["quality"]["word_order"] == "unresolved"
    captured = capsys.readouterr()
    assert "=== Easy521 监测" not in captured.out
    assert "--word-order" in captured.err and "人工已知值" in captured.err


@pytest.mark.parametrize("client", [Client(error=True),
                                    Client(exception=OSError("read disconnected")),
                                    Client([1, 2])])
def test_calibration_read_failure_returns_3_and_preserves_failure(monkeypatch, tmp_path, client):
    rc, rows = run_client(monkeypatch, tmp_path, client, "--calibrate")
    row, = rows
    assert rc == 3 and client.closed and len(client.calls) == 1
    assert row["error"] and row["values"] == {}
    assert row["quality"]["read"] == "error"
    assert row["quality"]["decoded"] is False


def test_manual_order_allows_ambiguous_raw_but_is_never_marked_verified(monkeypatch, tmp_path):
    client = Client([0] * 18)
    rc, rows = run_client(monkeypatch, tmp_path, client, "--calibrate", "--word-order", "abcd",
                          "--project-revision", "operator-source-abc123")
    row, = rows
    assert rc == 0 and len(client.calls) == 1  # Explicit override skips calibration.
    assert row["word_order_source"] == "manual" and row["quality"]["word_order"] == "manual"
    assert row["values"]["Vpu"] == 0.0 and row["range_flags"]["Vpu"] == "out-of-range"
    assert row["project_revision"] == "operator-source-abc123"
    assert row["project_revision_source"] == "operator-declared"
    for field in ("freshness", "program_match", "plc_scan_progress"):
        assert row["quality"][field] == "unverified"


def test_default_order_is_explicitly_assumed_and_not_calibrated(monkeypatch, tmp_path):
    rc, rows = run_client(monkeypatch, tmp_path, Client())
    row, = rows
    assert rc == 0 and row["word_order_source"] == "default"
    assert row["quality"]["word_order"] == "assumed-default"
    assert row["project_revision"] is None
    assert row["project_revision_source"] == "not-provided"


def test_calibrated_json_preserves_legacy_fields_and_collection_provenance(monkeypatch, tmp_path):
    rc, rows = run_client(monkeypatch, tmp_path, Client(), "--calibrate")
    row, = rows
    assert rc == 0 and row["word_order_source"] == "calibrated"
    assert row["quality"]["word_order"] == "heuristic"
    assert row["quality"]["range"] == "within-envelope"
    assert row["registers"] == row["raw"]["holding_registers"]
    assert len(row["registers"]) == 19
    assert {"ts", "transport", "target", "unit_id", "word_order", "registers", "values"} <= row.keys()
    assert row["contract_id"] == monitor.CONTRACT_ID
    assert row["contract_sha256"] == hashlib.sha256(
        Path(monitor.__file__).with_name("e521_regs.py").read_bytes()).hexdigest()
    assert datetime.fromisoformat(row["read_started_at"]) <= datetime.fromisoformat(row["read_completed_at"])
    assert row["ts"] == row["read_completed_at"]
    assert row["timestamp_source"] == "host" and row["read_duration_ms"] >= 0


@pytest.mark.parametrize("client", [Client(error=True), Client([1, 2]),
                                    Client(exception=ConnectionError("lost link"))])
def test_poll_read_failures_record_no_values_and_return_1(monkeypatch, tmp_path, client):
    rc, rows = run_client(monkeypatch, tmp_path, client)
    row, = rows
    assert rc == 1 and row["quality"]["read"] == "error"
    assert row["values"] == {} and row["quality"]["decoded"] is False
    assert client.closed


def test_nonfinite_values_are_valid_json_null_with_quality_flags(monkeypatch, tmp_path):
    values = dict(VALUES, Vpu=float("nan"), SOC=float("inf"))
    rc, rows = run_client(monkeypatch, tmp_path, Client(encode_quantities(values)),
                          "--word-order", "abcd")
    row, = rows
    assert rc == 0  # Transport succeeded; numeric quality remains separate.
    assert row["values"]["Vpu"] is None and row["values"]["SOC"] is None
    assert row["range_flags"]["Vpu"] == row["range_flags"]["SOC"] == "non-finite"
    assert row["quality"]["range"] == "out-of-range"
    json.dumps(row, allow_nan=False)


def test_optional_bit_read_failures_are_reported(monkeypatch, tmp_path):
    client = Client()
    client.read_coils = lambda **kwargs: Response(error=True)
    client.read_discrete_inputs = lambda **kwargs: Response(error=True)
    rc, rows = run_client(monkeypatch, tmp_path, client, "--with-bits")
    row, = rows
    assert rc == 0 and row["quality"]["read"] == "ok"
    assert row["quality"]["bits"] == "partial-error"
    assert len(row["bit_errors"]) == 2


def test_disconnect_after_success_does_not_reuse_previous_values(monkeypatch, tmp_path):
    client = Client()
    original_read = client.read_holding_registers

    def read(**kwargs):
        if client.calls:
            raise ConnectionError("link lost after first sample")
        return original_read(**kwargs)

    sleeps = []

    def sleep(interval):
        sleeps.append(interval)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    client.read_holding_registers = read
    monkeypatch.setattr(monitor, "build_client", lambda args: (client, "fake://readonly"))
    monkeypatch.setattr(monitor.time, "sleep", sleep)
    output = tmp_path / "disconnect.jsonl"
    assert monitor.main(["--word-order", "abcd", "--jsonl", str(output)]) == 0
    first, second = [json.loads(line) for line in output.read_text().splitlines()]
    assert first["values"] and first["quality"]["read"] == "ok"
    assert second["values"] == {} and second["registers"] == []
    assert second["quality"]["read"] == "error"
    assert second["quality"]["freshness"] == "unverified"


def test_invalid_jsonl_path_closes_connection_without_traceback(monkeypatch, tmp_path, capsys):
    client = Client()
    monkeypatch.setattr(monitor, "build_client", lambda args: (client, "fake://readonly"))
    assert monitor.main(["--once", "--jsonl", str(tmp_path)]) == 1
    assert client.closed and "操作失败" in capsys.readouterr().err
