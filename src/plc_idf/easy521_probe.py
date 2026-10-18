"""Offline preparation/checking for a memory-only native Easy521 MAIN probe.

This module never imports a transport, invokes AutoShop, or accesses a device.
It checks captured values; callers retain responsibility for current device,
project, download-consistency and online-evidence provenance.
"""
from __future__ import annotations
import csv
import hashlib
import io
import math
from pathlib import Path
import re
from .config import runtime_asset

REPO = Path(__file__).resolve().parents[2]
PROBE_DIRECTORY = runtime_asset(REPO, "examples/easy521_native_probe/MAIN_plus1_body.st").parent
ADDRESS_CSV = runtime_asset(REPO, "plc/easy521/autoshop/BESS_E521_DEV/VarNameAndAddr.csv")
NATIVE_PROJECT = ADDRESS_CSV.parent
VARIABLES_CSV = runtime_asset(REPO, "plc/easy521/MAIN_5ms_variables.csv")
IMPORT_CSV = runtime_asset(REPO, "plc/easy521/autoshop_global_import.csv")

VARIABLE_TYPES = {
    "g_xBESS_Enable": "BOOL", "g_xBESS_Reset": "BOOL", "g_xBESS_MeasurementsValid": "BOOL",
    "g_rBESS_Pcmd": "REAL", "g_rBESS_Qcmd": "REAL", "g_iBESS_Mode": "INT",
    "g_rBESS_Confidence": "REAL", "g_rBESS_SensVP": "REAL", "g_rBESS_SensVQ": "REAL",
    "g_xBESS_ControllerReady": "BOOL", "g_rBESS_InfluenceP": "REAL", "g_rBESS_InfluenceQ": "REAL",
}
OBSERVABLES = {
    "scan_counter": "g_iBESS_Mode", "accumulated": "g_rBESS_Confidence",
    "revision": "g_rBESS_SensVP", "step": "g_rBESS_SensVQ",
    "heartbeat": "g_xBESS_ControllerReady", "published_scan": "g_rBESS_InfluenceP",
    "published_accumulated": "g_rBESS_InfluenceQ",
}


def make_probe_body(revision=1):
    """Return MAIN body only; never emit declarations/configuration or new I/O."""
    if type(revision) is not int or revision not in (1, 2):
        raise ValueError("probe revision must be 1 (+1) or 2 (+2)")
    marker = 52100 + revision
    return f'''(* EASY521 NATIVE MEMORY PROBE, revision {revision}, step +{revision}.
   MAIN body only. Reuse the existing DEV variables and task unchanged.
   No physical I/O, communication instructions, or fbBESS invocation.
   M8002 is read-only: the existing first-RUN-scan system flag.
   This temporary probe replaces algorithm meanings; restore DEV after testing. *)
IF M8002 OR (g_rBESS_SensVP <> {marker}.0) OR (g_iBESS_Mode < 0) OR (g_iBESS_Mode >= 9999) THEN
    g_iBESS_Mode := 0;
    g_rBESS_Confidence := 0.0;
    g_rBESS_InfluenceP := 0.0;
    g_rBESS_InfluenceQ := 0.0;
    g_xBESS_ControllerReady := FALSE;
END_IF;

g_xBESS_Enable := FALSE;
g_xBESS_Reset := TRUE;
g_xBESS_MeasurementsValid := FALSE;
g_rBESS_Pcmd := 0.0;
g_rBESS_Qcmd := 0.0;
g_rBESS_SensVP := {marker}.0;
g_rBESS_SensVQ := {revision}.0;

g_iBESS_Mode := g_iBESS_Mode + 1;
g_rBESS_Confidence := g_rBESS_Confidence + {revision}.0;
IF (g_iBESS_Mode MOD 100) = 0 THEN
    g_rBESS_InfluenceP := g_rBESS_InfluenceP + 100.0;
    g_rBESS_InfluenceQ := g_rBESS_Confidence;
    g_xBESS_ControllerReady := NOT g_xBESS_ControllerReady;
END_IF;
'''


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_table(path, delimiter=","):
    raw = Path(path).read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return list(csv.DictReader(io.StringIO(raw.decode(encoding)), delimiter=delimiter))


def inspect_probe(source_path, revision, *, variables_csv=VARIABLES_CSV,
                  import_csv=IMPORT_CSV, address_csv=ADDRESS_CSV):
    """Check repository evidence and the exact known probe; not vendor compilation."""
    expected = make_probe_body(revision)
    path = Path(source_path)
    source = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    errors, bindings = [], {}
    if source != expected:
        errors.append("source differs from the reviewed memory-only probe")
    structural = re.sub(r"\(\*.*?\*\)", "", source, flags=re.S)
    writes = set(re.findall(r"\b([A-Za-z_][A-Za-z_0-9.]*)\s*:=", structural))
    if not writes.issubset(VARIABLE_TYPES):
        errors.append("probe writes an unapproved variable or address")
    references = set(re.findall(r"\bg_[A-Za-z_0-9]+\b", structural))
    if not references.issubset(VARIABLE_TYPES):
        errors.append("probe references an unreviewed global")
    if re.search(r"\b(?:PROGRAM|CONFIGURATION|RESOURCE|VAR|fbBESS)\b|\b[XYDM][0-9]+\s*:=", structural, re.I):
        errors.append("probe includes declarations, FB calls or direct soft-element writes")
    declared = {row["name"]: row for row in _read_table(variables_csv)}
    imported = {row["变量名"]: row for row in _read_table(import_csv)}
    address_raw = Path(address_csv).read_bytes()
    address_text = address_raw.decode("utf-16" if address_raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    addresses = {}
    for line in address_text.splitlines():
        cells = line.rstrip("\r").split("\t")
        if len(cells) >= 4:
            addresses[cells[0]] = {"symbol_address": cells[1], "bits": cells[-1]}
    for name, kind in VARIABLE_TYPES.items():
        row, entry, address = declared.get(name), imported.get(name), addresses.get(name)
        if not row or row.get("data_type") != kind or row.get("scope") != "Global":
            errors.append("missing or incompatible existing declaration: " + name)
        if not entry or entry.get("数据类型") != kind or entry.get("软元件地址", "").strip():
            errors.append("global has missing/changed native import type or an explicit I/O binding: " + name)
        width = {"BOOL": "1", "INT": "16", "REAL": "32"}[kind]
        if not address or not re.fullmatch(r"0[xX]210[0-9A-Fa-f]+", address["symbol_address"]) or address["bits"] != width:
            errors.append("global internal symbol evidence is absent or incompatible: " + name)
        bindings[name] = {"type": kind, "native_symbol": address, "explicit_soft_element": None,
                          "modbus_register": None}
    first_scan = addresses.get("M8002")
    if not first_scan or first_scan.get("bits") != "1":
        errors.append("existing read-only M8002 first-scan symbol is absent")
    return {"ok": not errors, "status": "ready-for-vendor-compilation" if not errors else "refused",
            "validation": "offline-source-and-repository-binding-check", "compiled": False,
            "hardware_verified": False, "revision": revision, "marker": 52100 + revision,
            "source_sha256": _sha(path), "source_path": str(path),
            "bindings": bindings, "errors": errors,
            "evidence_hashes": {str(Path(item)): _sha(item) for item in (variables_csv, import_csv, address_csv)},
            "limitations": ["repository bindings are historical, not current device identity",
                            "no D/M Modbus addresses inferred from internal symbol values",
                            "current project bindings and absence of other I/O programs require live review"]}


def _number(value, kind):
    if kind == "BOOL":
        if type(value) is bool:
            return value
        if isinstance(value, str) and value.upper() in ("ON", "OFF", "TRUE", "FALSE"):
            return value.upper() in ("ON", "TRUE")
        raise ValueError("BOOL observation must be boolean or vendor ON/OFF")
    if type(value) is bool:
        raise ValueError("numeric observation cannot be a boolean")
    if kind == "INT":
        if type(value) is int:
            return value
        if isinstance(value, str) and re.fullmatch(r"[-+]?\d+", value):
            return int(value)
        raise ValueError("INT observation must be an integer")
    if not isinstance(value, (int, float, str)):
        raise ValueError("REAL observation must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("REAL observation must be finite")
    return parsed


def assess_probe_samples(samples, revision, *, expected_state="RUN", require_heartbeat=True):
    """Check captured online observations, without making hardware provenance claims.

    Each sample supplies online=true, plc_state=RUN/STOP and values containing
    VARIABLE_TYPES. M8000/M8001 may be included as additional read-only evidence.
    A live Mode/Confidence skew is reported; the slower published pair is strict.
    """
    make_probe_body(revision)
    if expected_state not in ("RUN", "STOP"):
        raise ValueError("expected_state must be RUN or STOP")
    if not isinstance(samples, list) or not 3 <= len(samples) <= 10000:
        raise ValueError("provide 3..10000 ordered online observations")
    rows, errors, warnings = [], [], []
    for index, sample in enumerate(samples, 1):
        if not isinstance(sample, dict) or sample.get("online") is not True or sample.get("plc_state") != expected_state:
            errors.append(f"sample {index}: missing verified-online flag or unexpected PLC state")
            continue
        try:
            values = sample["values"]
            row = {name: _number(values[name], kind) for name, kind in VARIABLE_TYPES.items()}
            count = row["g_iBESS_Mode"]
            if not 1 <= count <= 9999:
                raise ValueError("scan counter is outside its initialized range")
            if row["g_rBESS_SensVP"] != 52100 + revision or row["g_rBESS_SensVQ"] != revision:
                raise ValueError("program revision/step does not match the requested probe")
            if row["g_xBESS_Enable"] or not row["g_xBESS_Reset"] or row["g_xBESS_MeasurementsValid"]:
                raise ValueError("BESS enable/reset/valid memory guards differ from the probe")
            if row["g_rBESS_Pcmd"] != 0 or row["g_rBESS_Qcmd"] != 0:
                raise ValueError("memory command values are not zero")
            published = row["g_rBESS_InfluenceP"]
            if published < 0 or published > 9900 or published % 100 != 0:
                raise ValueError("published scan value is not a valid 100-scan snapshot")
            if row["g_rBESS_InfluenceQ"] != revision * published:
                raise ValueError("published +1/+2 relation is incoherent or belongs to another program")
            if row["g_xBESS_ControllerReady"] != bool((int(published) // 100) % 2):
                raise ValueError("published heartbeat does not match its snapshot phase")
            if row["g_rBESS_Confidence"] != revision * count:
                warnings.append(f"sample {index}: live fields were not a coherent scan snapshot; resample if needed")
            if "M8000" in values and _number(values["M8000"], "BOOL") != (expected_state == "RUN"):
                raise ValueError("read-only M8000 contradicts PLC state")
            if "M8001" in values and _number(values["M8001"], "BOOL") != (expected_state == "STOP"):
                raise ValueError("read-only M8001 contradicts PLC state")
            rows.append(row)
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            errors.append(f"sample {index}: {exc}")
    if len(rows) == len(samples):
        counters = [row["g_iBESS_Mode"] for row in rows]
        published = [row["g_rBESS_InfluenceP"] for row in rows]
        heartbeats = {row["g_xBESS_ControllerReady"] for row in rows}
        if expected_state == "RUN":
            if len(set(counters)) < 2 or len(set(published)) < 2:
                errors.append("RUN reported without observed live and published scan progress")
            if require_heartbeat and heartbeats != {False, True}:
                errors.append("RUN observations do not demonstrate both heartbeat phases")
        elif any(row != rows[0] for row in rows[1:]):
            errors.append("STOP reported while probe memory values continued changing")
    return {"ok": not errors, "status": "observations-consistent" if not errors else "unverified",
            "validation": "offline-captured-online-value-consistency", "revision": revision,
            "expected_state": expected_state, "sample_count": len(samples), "errors": errors,
            "warnings": warnings, "normalized_values": rows,
            "cycle_time": "unverified", "device_identity": "requires-independent-live-evidence",
            "download_integrity": "requires-native-program-comparison"}
