"""Read-only MSAA table capture and strict parsing of AutoShop online values.

The PowerShell reader exposes UI getters only. UI values do not independently
establish a live physical connection: the caller supplies separate connection
evidence when combining this result with hardware acceptance records.
"""
from __future__ import annotations
import json
import math
import ntpath
from pathlib import Path
import re
import subprocess
from .easy521_probe import VARIABLE_TYPES
from .config import runtime_asset
from .interop import to_windows_unc

SCRIPT_RELATIVE = "plc/tools/read_autoshop_online.ps1"
SCRIPT = runtime_asset(Path.cwd(), SCRIPT_RELATIVE)
POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"


class OnlineReadError(ValueError):
    pass


def _text(value):
    return "" if value is None else str(value).strip()


def _parse_scalar(raw, kind):
    value = _text(raw)
    if kind == "BOOL":
        normalized = value.upper()
        if normalized not in {"ON", "OFF", "TRUE", "FALSE", "0", "1", "真", "假"}:
            raise OnlineReadError("unrecognized BOOL UI value: " + value)
        return normalized in {"ON", "TRUE", "1", "真"}
    if kind == "INT":
        if not re.fullmatch(r"[-+]?\d+", value):
            raise OnlineReadError("unrecognized INT UI value: " + value)
        number = int(value)
        if not -32768 <= number <= 32767:
            raise OnlineReadError("INT UI value is outside 16-bit range")
        return number
    if kind == "REAL":
        try:
            number = float(value)
        except ValueError as exc:
            raise OnlineReadError("unrecognized REAL UI value: " + value) from exc
        if not math.isfinite(number) or abs(number) > 3.4028234663852886e38:
            raise OnlineReadError("REAL UI value must be finite binary32")
        return number
    raise OnlineReadError("unsupported native UI type: " + kind)


def _cells(row):
    return [cell for cell in row.get("children", []) if isinstance(cell, dict) and str(cell.get("role")) == "29"]


def _tables(payload):
    for grid in payload.get("grids", []):
        if not isinstance(grid, dict) or grid.get("visible") is not True:
            continue  # Hidden tables can contain old cached values.
        for entry in grid.get("objects", []):
            if entry.get("object_id") != 4294967292:
                continue
            msaa = entry.get("msaa", {})
            rows = [row for row in msaa.get("children", []) if isinstance(row, dict) and str(row.get("role")) == "28"]
            if not rows:
                continue
            header = _cells(rows[0])
            headings = [_text(cell.get("value") or cell.get("name")) for cell in header]
            names = [index for index, name in enumerate(headings) if name in ("元件名称", "变量名", "变量名称", "名称")]
            values = [index for index, name in enumerate(headings) if name in ("当前值", "在线值", "实时值")]
            if not names or len(values) != 1:
                continue
            types = [index for index, name in enumerate(headings) if name in ("数据类型", "类型")]
            errors = [index for index, name in enumerate(headings) if name in ("错误码", "错误")]
            yield grid, rows[1:], names[-1], values[0], types[0] if types else None, errors[0] if errors else None


def parse_online_snapshot(payload, *, expected_project, expected_process_id=None,
                          required_variables=None, connection_evidence=None):
    """Parse a capture; no state inferred from colors, command results or icons."""
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("ok") is not True:
        raise OnlineReadError("reader did not return a successful schema-1 UI capture")
    if type(payload.get("process_id")) is not int or payload["process_id"] <= 0:
        raise OnlineReadError("AutoShop capture must identify a positive process ID")
    if expected_process_id is not None and payload.get("process_id") != expected_process_id:
        raise OnlineReadError("AutoShop capture process differs from the expected process")
    match = re.fullmatch(r"AutoShop\s+V[\d.]+\s+(?P<project>[A-Za-z]:\\.+?)\s+-\s+\[[^\]]+\]", _text(payload.get("title")), re.I)
    if not match or ntpath.normcase(ntpath.normpath(match["project"])) != ntpath.normcase(ntpath.normpath(expected_project)):
        raise OnlineReadError("AutoShop capture project differs from the exact expected project")
    required = dict(VARIABLE_TYPES if required_variables is None else required_variables)
    supported = {**required, "M8000": "BOOL", "M8001": "BOOL"}
    candidates, errors = {}, []
    for grid, rows, name_col, value_col, type_col, error_col in _tables(payload):
        for row in rows:
            cells = _cells(row)
            if len(cells) <= max(name_col, value_col):
                continue
            name = _text(cells[name_col].get("value"))
            if name not in supported:
                continue
            raw = cells[value_col].get("value")
            if raw is None or not _text(raw):
                continue
            kind = supported[name]
            declared = _text(cells[type_col].get("value")) if type_col is not None and type_col < len(cells) else ""
            if declared and declared.upper() not in (kind, "BIT" if kind == "BOOL" else kind):
                errors.append(name + ": displayed type differs from the expected native type")
                continue
            error = _text(cells[error_col].get("value")) if error_col is not None and error_col < len(cells) else ""
            if error not in ("", "0", "0x0", "0x0000", "无错误"):
                errors.append(name + ": native table reports error " + error)
                continue
            try:
                value = _parse_scalar(raw, kind)
            except OnlineReadError as exc:
                errors.append(name + ": " + str(exc))
                continue
            candidates.setdefault(name, []).append({"value": value, "raw": raw, "grid_handle": grid["handle"], "row": row.get("name")})
    values, evidence = {}, {}
    for name, items in candidates.items():
        if any(item["value"] != items[0]["value"] for item in items[1:]):
            errors.append(name + ": conflicting values across visible tables; select a single grid")
        else:
            values[name], evidence[name] = items[0]["value"], items
    missing = sorted(set(required) - values.keys())
    if missing:
        errors.append("missing readable online variables: " + ", ".join(missing))
    plc_state, state_source = "unverified", None
    if "M8000" in values and "M8001" in values:
        flags = values["M8000"], values["M8001"]
        if flags == (True, False):
            plc_state = "RUN"
        elif flags == (False, True):
            plc_state = "STOP"
        else:
            errors.append("M8000/M8001 do not form a valid RUN/STOP state")
        state_source = "MSAA online-table M8000/M8001 values"
    online = (isinstance(connection_evidence, dict) and connection_evidence.get("online") is True
              and connection_evidence.get("transport") == "usb" and bool(connection_evidence.get("evidence_id")))
    values_read = not errors
    verified = values_read and online and plc_state != "unverified"
    return {"schema_version": 1, "ok": verified, "status": "online-values-read" if verified else "unverified",
            "values_read": values_read, "online": bool(online), "plc_state": plc_state,
            "state_source": state_source, "values": values, "raw_values": evidence,
            "missing_variables": missing, "errors": errors,
            "captured_at_utc": payload.get("captured_at_utc"), "process_id": payload.get("process_id"),
            "project": match["project"], "connection_evidence": connection_evidence,
            "limitations": ["UI table snapshots need not be atomic across rows",
                            "USB/device provenance is supplied separately, not established by this parser",
                            "no PLC state inferred from pixel colors, icons or UI request return codes"]}


def read_online_snapshot(*, expected_project, process_id=0, grid_handle=0,
                         script_path=None, powershell_exe=POWERSHELL, timeout_s=20,
                         connection_evidence=None, required_variables=None):
    """Execute only the dedicated read-only PowerShell reader, with no retries."""
    if type(process_id) is not int or process_id < 0 or type(grid_handle) is not int or grid_handle < 0:
        raise OnlineReadError("process and grid handles must be nonnegative integers (0 selects automatically)")
    if not isinstance(expected_project, str) or not re.match(r"^[A-Za-z]:\\", expected_project) or any(char in expected_project for char in "*?\x00\r\n"):
        raise OnlineReadError("expected project must be an exact absolute Windows path")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 30:
        raise OnlineReadError("read-only UI timeout must be finite and in (0, 30]")
    path = Path(script_path if script_path is not None else runtime_asset(Path.cwd(), SCRIPT_RELATIVE)).resolve()
    if not path.is_file():
        raise OnlineReadError("dedicated read-only PowerShell reader is unavailable")
    argv = [str(powershell_exe), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", to_windows_unc(path),
            "-AutoShopProcessId", str(process_id), "-ExpectedProject", expected_project, "-GridHandle", str(grid_handle)]
    try:
        result = subprocess.run(argv, capture_output=True, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "unverified", "online": False, "plc_state": "unverified", "errors": ["read-only UI capture timed out"]}
    raw = result.stdout.decode("utf-8-sig").strip()
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise OnlineReadError("read-only UI output was not one JSON document") from exc
    if result.returncode:
        raise OnlineReadError("read-only UI capture failed: " + _text(payload.get("error")))
    return parse_online_snapshot(payload, expected_project=expected_project,
                                 expected_process_id=process_id or None, required_variables=required_variables,
                                 connection_evidence=connection_evidence)
