#!/usr/bin/env python3
"""Preview or perform one explicitly selected fixed index read via Windows libusb0.

This is a WSL-to-Windows driver bridge, not a Linux-native USB transport.  The
first real-device observation is recorded in
evidence/easy521-ai-loop-20261019/single-usb-runtime-query.json: one 18-byte
request returned a correlated 14-byte data frame containing raw u32 zero.
Report 18 corrects that literal index's historical runtime naming: its vendor
command is CReadEthercatCommand; its device meaning remains unknown. The new
gcm-state profile follows the separate vendor GetGCMState ff000000/1B path.
CPU identity, live state calibration and application execution remain unverified.

Preview is the default and performs no subprocess, USB, lease or GUI action.
Execute requires an explicit fresh USB bus location and exactly one matching
0471:a531 device in Windows "Not shared" state.  A cooperating-process lease
guards one helper invocation; the helper rejects an open AutoShop rather than
closing it.  No bind/attach, configuration change, reset, mode/variable write,
application download, automatic replay, or arbitrary frame is supported.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import secrets
import sys

# Direct execution works from any cwd in a source checkout. Installed tools use
# the installed plc_idf package. No caller-controlled path is imported.
_ROOT = Path(__file__).resolve().parents[1]
if (_ROOT / "src" / "plc_idf").is_dir():
    sys.path.insert(0, str(_ROOT / "src"))

from plc_idf.config import runtime_asset
from plc_idf.device import parse_usbipd_list
from plc_idf.interop import (
    POWERSHELL_WSL_PATH, USBIPD_WSL_PATH, RetryPolicy, run_command, to_windows_unc,
)
from plc_idf.lease import ResourceLease, device_resource

# Anchor the companion to this file, never cwd or an ambient easy521_protocol
# module. The codec itself has no I/O or Windows/device imports.
_spec = importlib.util.spec_from_file_location(
    "_plcidf_runtime_read_codec", Path(__file__).with_name("easy521_protocol.py")
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("Cannot locate the adjacent offline protocol codec")
codec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(codec)

SCHEMA = "plcidf.usb-runtime-observation.v1"
HELPER_SCHEMA = "plcidf.usb-runtime-probe.v1"
LIBRARY_SHA256 = "cb6ac96e9abf55586622af809b98af1c989b50d490b2e0770dd7fbe54352520c"
HELPER_SCOPE = "single fixed read request via Windows libusb0; no CPU mode or application change"
SCOPE = "one fixed read through Windows libusb0 driver bridge; raw value only"
VIDPID = "0471:a531"
PROFILES = {"literal": codec.VENDOR_LITERAL_FF010000_4, "gcm-state": codec.VENDOR_GCM_STATE}
PREVIEW_SEQUENCE = 0x1234


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate helper JSON key: {key}")
        result[key] = value
    return result


def _helper_record(stdout: str) -> dict:
    if not isinstance(stdout, str) or len(stdout) > 1024 * 1024:
        raise ValueError("Helper stdout is missing or exceeds 1 MiB")
    def invalid_number(value):
        raise ValueError(f"Unsupported helper JSON number: {value}")
    # Exactly one JSON document; a printed success token, extra document,
    # duplicate key, NaN or Infinity never constitutes evidence of completion.
    record = json.loads(stdout.lstrip("\ufeff"), object_pairs_hook=_unique_json_object,
                        parse_float=invalid_number, parse_constant=invalid_number)
    if not isinstance(record, dict):
        raise ValueError("Helper must return one JSON object")
    return record


def _integer_field(record: dict, name: str, expected: int) -> None:
    value = record.get(name)
    if type(value) is not int or value != expected:
        raise ValueError(f"Helper field {name} must be the exact integer {expected}")


def _hex(value, *, maximum_bytes: int, name: str) -> bytes:
    if (not isinstance(value, str) or len(value) > maximum_bytes * 2
            or not re.fullmatch(r"(?:[0-9a-f]{2})+", value)):
        raise ValueError(f"Invalid or oversized helper {name}")
    return bytes.fromhex(value)


def _validate_transport(record: dict, request: bytes, profile: str) -> dict:
    """Validate transport evidence before the independent codec sees its frame."""
    if (record.get("schema") != HELPER_SCHEMA or record.get("scope") != HELPER_SCOPE
            or record.get("library_sha256") != LIBRARY_SHA256
            or record.get("read_profile") != profile):
        raise ValueError("Helper schema/scope or pinned Windows library identity differs")
    result = record.get("result")
    if (not isinstance(result, dict) or result.get("ok") is not True
            or result.get("stage") != "raw-frame-only"
            or result.get("state_semantics") != "unknown" or result.get("error")):
        raise ValueError("Helper did not complete an unambiguous raw-frame-only read")
    for name, expected in (("write_calls", 1), ("bulk_write_rc", 18),
                           ("configuration_rc", 1), ("claim_rc", 0),
                           ("release_rc", 0), ("close_rc", 0)):
        _integer_field(result, name, expected)
    if result.get("request_hex") != request.hex():
        raise ValueError("Helper sent a different request from the fixed read profile")
    descriptor = _hex(result.get("descriptor_hex"), maximum_bytes=18, name="descriptor_hex")
    if (len(descriptor) != 18 or descriptor[:2] != b"\x12\x01"
            or descriptor[8:12] != bytes.fromhex("710431a5")):
        raise ValueError("Helper descriptor does not match the selected USB product")
    response = _hex(result.get("response_hex"), maximum_bytes=14, name="response_hex")
    reads = result.get("read_rcs")
    if (not isinstance(reads, list) or not 1 <= len(reads) <= 4
            or any(type(count) is not int or not 1 <= count <= 512 for count in reads)
            or sum(reads) != len(response)):
        raise ValueError("Helper read lengths do not describe exactly the returned frame")
    return codec.parse_read_response(response, request=request, profile=PROFILES[profile])


def read_runtime(*, execute: bool = False, busid: str | None = None, profile: str = "literal",
                 runner=None, lease_directory: str | Path | None = None) -> dict:
    """Return one structured preview/observation/failure; never retry a request.

    Args:
        execute: False previews locally. True permits the single fixed USB read.
        busid: Required for execution, explicit Windows USB location (e.g. 8-3).
        profile: "literal" preserves the historical ff010000/4B read;
            "gcm-state" reads the separate vendor ff000000/1B CPU-state candidate.
        runner: subprocess.run-shaped test injection used by run_command.
        lease_directory: Optional cooperating-process lock directory for tests.
    Returns:
        JSON-compatible dict. ``status=observed`` means a correlated raw response,
        never RUN/STOP verification. On timeout after starting the helper,
        ``request_outcome`` stays unknown and no follow-up command is issued.
    Side effects:
        Preview: none beyond importing modules. Execute: local resource lease,
        fresh usbipd list, then at most one bounded Windows helper call.
        The helper claims/releases interface 0 and makes one bulk READ request;
        it does not alter active configuration or close AutoShop.
    """
    request = codec.build_read_request(profile=PROFILES["literal"], sequence=PREVIEW_SEQUENCE)
    report = {
        "schema": SCHEMA, "scope": SCOPE, "ok": False, "status": "failed",
        "stage": "preview", "device_access": False, "gui_access": False,
        "transport_kind": "wsl-windows-libusb0", "linux_native_usb": False,
        "cpu_identity_verified": False, "cpu_state_verified": False,
        "application_execution_verified": False, "state_semantics": "unknown",
        "read_profile": profile, "profile": PROFILES["literal"], "request_hex": request.hex(), "diagnostics": [],
    }
    if not isinstance(profile, str) or profile not in PROFILES:
        return {**report, "stage": "arguments", "diagnostics": [{"code": "E_PROFILE",
                "message": "Choose fixed literal or gcm-state profile; arbitrary indexes are unsupported"}]}
    request = codec.build_read_request(profile=PROFILES[profile], sequence=PREVIEW_SEQUENCE)
    report.update(profile=PROFILES[profile], request_hex=request.hex())
    if execute is False:
        return {**report, "ok": True, "status": "preview", "requested_busid": busid,
                "execution_requires": "--execute --busid BUSID; unique Not shared device; AutoShop closed"}
    try:
        report["stage"] = "preflight"
        if execute is not True or not isinstance(busid, str) or not re.fullmatch(r"\d+(?:-\d+)+", busid):
            raise ValueError("Execution requires an explicit valid --busid")
        # A new u16 correlation value reduces accidental association with a
        # prior call's late response. Collisions remain possible (1/65536), so
        # this is neither authentication nor a guarantee of response freshness.
        sequence = secrets.randbits(16)
        request = codec.build_read_request(profile=PROFILES[profile], sequence=sequence)
        report["request_hex"] = request.hex()
        report["sequence"] = sequence
        report["correlation_scope"] = "random u16; collision reduction only, not authentication or absolute freshness"
        helper = runtime_asset(_ROOT, "plc/tools/read_easy521_usb_runtime.ps1")
        if not helper.is_file() or helper.is_symlink():
            raise ValueError("The fixed runtime PowerShell helper is missing or is a symlink")
        helper_hash = hashlib.sha256(helper.read_bytes()).hexdigest()
        policy = RetryPolicy(timeout_s=25, retries=0, backoff_s=0)
        report["stage"] = "lease"
        with ResourceLease(device_resource(busid, VIDPID), directory=lease_directory,
                           purpose="single fixed USB runtime-index read") as lease:
            report["lease_token"] = lease.record["token"]
            # Enumerate while holding the same location lease used by other
            # cooperating attach/detach commands. External Windows processes do
            # not share that lock: the helper independently rejects AutoShop.
            report["stage"] = "preflight"
            snapshot = run_command([USBIPD_WSL_PATH, "list"], policy=policy,
                                   runner=runner, retryable=False)
            report["usbipd_snapshot"] = asdict(snapshot)
            if not snapshot.ok:
                raise ValueError("Fresh usbipd list failed or timed out")
            entries = parse_usbipd_list(snapshot.stdout)
            matches = [entry for entry in entries if entry.vidpid == VIDPID]
            at_location = [entry for entry in entries if entry.busid == busid]
            if len(matches) != 1 or len(at_location) != 1 or matches[0] is not at_location[0]:
                raise ValueError("Expected exactly one target product at the explicitly selected BUSID")
            target = matches[0]
            if target.state.lower() != "not shared":
                raise ValueError("Target must be Not shared in Windows; no ownership transition is automatic")
            report["target"] = {"busid": busid, "vidpid": VIDPID, "usbipd_state": target.state,
                                "identity_basis": "USB location and product, not CPU identity"}
            report["helper"] = {"path": str(helper), "sha256": helper_hash,
                                "library_sha256": LIBRARY_SHA256}
            argv = [POWERSHELL_WSL_PATH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                    "Bypass", "-File", to_windows_unc(helper), "-Execute", "-Sequence", str(sequence),
                    "-ReadProfile", profile]
            report["stage"] = "transport"
            report["device_access"] = True  # Attempted helper access; outcome may remain unknown.
            report["request_outcome"] = "unknown"
            transport = run_command(argv, policy=policy, runner=runner, retryable=False)
            # Preserve stdout/stderr even when malformed or containing fake
            # success markers. They are untrusted transport evidence only.
            report["raw_transport"] = asdict(transport)
            if not transport.ok:
                raise ValueError("Windows runtime helper failed or timed out; request is not replayed")
            # The source helper is a mutable workspace artifact. Bind this
            # observation to identical bytes before and after invocation; a
            # concurrent edit must not leave an apparently current success.
            # This stability check is not an operating-system attestation.
            report["stage"] = "helper_integrity"
            after_hash = hashlib.sha256(helper.read_bytes()).hexdigest()
            report["helper"]["sha256_after"] = after_hash
            if helper.is_symlink() or after_hash != helper_hash:
                raise ValueError("Runtime helper changed during execution; outcome remains unknown")
            report["stage"] = "validate"
            raw = _helper_record(transport.stdout)
            report["transport_record"] = raw
            decoded = _validate_transport(raw, request, profile)
            report["decoded"] = decoded
            if not decoded["ok"]:
                report["request_outcome"] = "device_error_response"
                raise ValueError("Device returned an error operation, not runtime data")
            report.update(ok=True, status="observed", stage="complete",
                          request_outcome="correlated_raw_data", raw_value=decoded["raw_value"])
            if profile == "gcm-state":
                report["static_cpu_state_interpretation"] = {
                    "value": {0: "STOP", 1: "RUN"}.get(decoded["raw_value"], "unknown"),
                    "basis": "vendor GetGCMState caller/UI mapping; research report 18",
                    "device_calibrated": False,
                }
        return report
    except Exception as error:
        # Never promote a helper marker, raw stdout, or partially decoded reply
        # to success. Interrupts remain exceptional for the CLI's exit 130 path.
        report.update(ok=False, status="failed")
        report["diagnostics"].append({"code": "E_USB_RUNTIME_READ", "stage": report["stage"],
                                      "message": str(error)})
        return report


def main(argv=None) -> int:
    """Emit one JSON object; return 0 for preview/observation, 1 on failure, 130 on interrupt."""
    class JsonArgumentParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError(message)
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform the fixed read once; default is preview")
    parser.add_argument("--busid", help="explicit Windows USB location required for execution")
    parser.add_argument("--profile", choices=tuple(PROFILES), default="literal",
                        help="fixed literal ff010000/4B (default), or vendor gcm-state ff000000/1B")
    try:
        args = parser.parse_args(argv)
        report = read_runtime(execute=args.execute, busid=args.busid, profile=args.profile)
        code = 0 if report["ok"] else 1
    except ValueError as error:
        report = {"schema": SCHEMA, "ok": False, "status": "failed", "stage": "arguments",
                  "scope": SCOPE, "device_access": False,
                  "diagnostics": [{"code": "E_ARGUMENTS", "message": str(error)}]}
        code = 1
    except KeyboardInterrupt:
        report = {"schema": SCHEMA, "ok": False, "status": "failed", "stage": "interrupted",
                  "scope": SCOPE, "request_outcome": "unknown", "state_semantics": "unknown",
                  "diagnostics": [{"code": "E_INTERRUPTED", "message": "Interrupted; no request replay"}]}
        code = 130
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
