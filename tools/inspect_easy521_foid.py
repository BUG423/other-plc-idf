#!/usr/bin/env python3
"""Inspect the observed Easy521 configuration FOID envelope, entirely offline.

Inputs are existing files, optionally paired with source configuration files;
output is JSON containing lengths, hashes, the three verified header records,
and byte-equality/prefix comparisons. No payload bytes, vendor code, or complete
binary are emitted. This parser covers only the 64-byte configuration envelope
written by the pinned AutoShop.exe function 0x015b2a90/0x015b2dd0. Other files
called ``*.foid`` (including userprog and project-level ecttrans) need not use
this profile. See docs/research/firmware/22-linux-foid-packaging-2026-10-19.md.

The 32-byte field remains opaque: the observed writer copies it from an empty
ACP HString's buffer, with no payload argument to the header generator. The last
DWORD is caller supplied (constant zero or g_GetModuleDefaultVer in the studied
call sites). Neither is authenticated here. A structurally valid envelope is
not a complete application, a checked configuration payload, or a downloadable
image. This tool never executes vendor software or communicates with a PLC.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
from datetime import datetime, timezone


SCHEMA = "plcidf.easy521-foid-inspection.v1"
HEADER_BYTES = 64
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILES = 64
LIMITATIONS = [
    "Only the observed 64-byte configuration envelope is parsed; other FOID formats are rejected.",
    "Metadata is opaque and caller_dword is not a checked compatibility version.",
    "Payload hashes and source-prefix equality do not validate payload semantics or build provenance.",
    "Files are individually checked for read stability; a common build/session is not established.",
    "No compilation, configuration generation, application closure, signature validation, download, or device access occurs.",
]


class FormatError(ValueError):
    """The input does not match the bounded, observed configuration envelope."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_foid(data: bytes) -> dict:
    """Parse one existing configuration envelope without altering it.

    ``data`` is a bytes object, limited to MAX_FILE_BYTES bytes. The returned
    dictionary describes structure and hashes only; its negative capability
    flags are unconditional. FormatError identifies truncated/foreign headers;
    TypeError or ValueError identifies an invalid API argument. There are no
    external side effects, payload interpretation, or signature checks.
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("input exceeds inspection byte limit")
    if len(data) < HEADER_BYTES:
        raise FormatError("configuration FOID header is shorter than 64 bytes")
    # The IDs/lengths are little-endian DWORDs, confirmed by the pinned writer's
    # copies and all twelve frozen configuration outputs. They are a narrow
    # profile, not a general TLV grammar or permission to accept arbitrary tags.
    for offset, expected in ((0, (0x100, 4)), (12, (0x200, 32)), (52, (0x201, 4))):
        actual = struct.unpack_from("<II", data, offset)
        if actual != expected:
            raise FormatError(f"unexpected record ID/length at byte {offset}: {actual!r}")
    if data[8:12] != b"foid":
        raise FormatError("record 0x100 does not contain the observed foid marker")
    return {
        "inspection_status": "parsed",
        "format": "observed-easy521-configuration-foid-64",
        "size": len(data),
        "sha256": _sha(data),
        "header": {
            "size": HEADER_BYTES,
            "sha256": _sha(data[:HEADER_BYTES]),
            "records": [
                {"offset": 0, "id": 0x100, "length": 4, "marker": "foid"},
                {"offset": 12, "id": 0x200, "length": 32,
                 "value_offset": 20, "value_sha256": _sha(data[20:52]),
                 "semantics": "opaque_metadata_origin_not_authenticated"},
                {"offset": 52, "id": 0x201, "length": 4,
                 "value_offset": 60, "caller_dword": struct.unpack_from("<I", data, 60)[0],
                 "semantics": "raw_u32_origin_and_compatibility_unverified"},
            ],
        },
        "payload": {"offset": HEADER_BYTES, "size": len(data) - HEADER_BYTES,
                    "sha256": _sha(data[HEADER_BYTES:]), "semantics_verified": False},
        "complete_application": False,
        "downloadable": False,
        "signature_verified": False,
        "writer_origin_verified": False,
    }


def _identity(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _read_stable(path: Path, budget: int, accounting: dict) -> bytes:
    # O_NOFOLLOW prevents the final component being swapped for a symlink;
    # O_NONBLOCK prevents a substituted FIFO from blocking before fstat rejects
    # it. Parent directories are ordinary caller-supplied paths, not a sandbox.
    before_path = path.lstat()
    if not stat.S_ISREG(before_path.st_mode):
        raise OSError("input must be a regular file, not a symlink or special file")
    if before_path.st_size > budget:
        raise OSError("input exceeds remaining inspection byte limit")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _identity(before_path) != _identity(before):
            raise OSError("input changed before inspection")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(budget)
            # Failed stability checks still consumed this read budget; a caller
            # must not grant a fresh cumulative allowance after such a failure.
            accounting["bytes_read"] += len(data)
        after = os.fstat(descriptor)
        if (len(data) > budget or len(data) != before.st_size
                or _identity(before) != _identity(after)
                or _identity(after) != _identity(path.lstat())):
            raise OSError("input changed during inspection or exceeded the byte limit")
        return data
    finally:
        os.close(descriptor)


def inspect_paths(paths, *, source_pairs=(), max_file_bytes=MAX_FILE_BYTES) -> dict:
    """Read configuration FOIDs and optional explicit (FOID, source) pairs.

    Between 1 and MAX_FILES distinct existing files are accepted, including
    sources. Reading consumes a shared MAX_TOTAL_BYTES budget. Individual read
    and format failures are returned as errors with inspection_ok=False. Invalid
    limits/counts raise ValueError. Source comparisons test equality and prefix
    arithmetic only; they neither select a generator nor infer a coherent build.
    No inputs are written, subprocesses started, or device handles opened.
    """
    if type(max_file_bytes) is not int or not 0 < max_file_bytes <= MAX_FILE_BYTES:
        raise ValueError("max_file_bytes must be a positive integer at most MAX_FILE_BYTES")
    pairs = [(Path(a).absolute(), Path(b).absolute()) for a, b in source_pairs]
    foids = list(dict.fromkeys([Path(p).absolute() for p in paths] + [a for a, _ in pairs]))
    requested = list(dict.fromkeys(foids + [b for _, b in pairs]))
    if not foids or len(requested) > MAX_FILES:
        raise ValueError("supply at least one FOID and at most 64 distinct inputs including sources")
    records, contents = {}, {}
    accounting = {"bytes_read": 0}
    for path in requested:
        record = {"path": str(path), "role": "foid" if path in foids else "source"}
        try:
            remaining = min(max_file_bytes, MAX_TOTAL_BYTES - accounting["bytes_read"])
            if remaining <= 0:
                raise OSError("total inspection byte limit reached")
            data = _read_stable(path, remaining, accounting)
            contents[path] = data
            record.update(size=len(data), sha256=_sha(data), inspection_status="read")
            if path in foids:
                record.update(inspect_foid(data))
        except (OSError, ValueError) as exc:
            record.update(inspection_status="error", error=str(exc))
        records[path] = record
    comparisons = []
    for foid, source in pairs:
        item = {"foid": str(foid), "source": str(source), "payload_semantics_verified": False}
        if records[foid]["inspection_status"] != "parsed" or source not in contents:
            item["error"] = "FOID was not parsed or source was not read"
        else:
            payload, raw_source = contents[foid][HEADER_BYTES:], contents[source]
            item.update(payload_equals_source=payload == raw_source,
                        payload_is_source_prefix=raw_source.startswith(payload),
                        source_is_payload_prefix=payload.startswith(raw_source),
                        payload_bytes=len(payload), source_bytes=len(raw_source),
                        source_bytes_after_payload_prefix=(len(raw_source) - len(payload)
                            if raw_source.startswith(payload) else None))
        comparisons.append(item)
    return {
        "schema": SCHEMA, "tool": "inspect_easy521_foid",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "inspection_ok": all(r["inspection_status"] != "error" for r in records.values()),
        "scope": "offline_observed_configuration_envelope",
        "complete_application": False, "downloadable": False,
        "signature_verified": False,
        "limits": {"max_file_bytes": max_file_bytes, "max_total_bytes": MAX_TOTAL_BYTES,
                   "max_files": MAX_FILES, "bytes_read": accounting["bytes_read"]},
        "files": list(records.values()), "source_comparisons": comparisons,
        "limitations": LIMITATIONS,
    }


def main(argv=None) -> int:
    """Write JSON to stdout or a new report file; return 0/1/2 for pass/error/usage."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--compare-source", nargs=2, action="append", default=[],
                        metavar=("FOID", "SOURCE"))
    parser.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES)
    parser.add_argument("--output", type=Path, help="write a new JSON file; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        inputs = args.paths + [Path(p) for pair in args.compare_source for p in pair]
        if args.output is not None and args.output.resolve() in {p.resolve() for p in inputs}:
            raise ValueError("report output cannot overwrite an input")
        if args.output is not None and args.output.exists():
            raise ValueError("report output already exists")
        report = inspect_paths(args.paths, source_pairs=args.compare_source,
                               max_file_bytes=args.max_file_bytes)
        report["tool_source_sha256"] = _sha(Path(__file__).read_bytes())
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output is None:
            sys.stdout.write(encoded)
        else:
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        return 0 if report["inspection_ok"] else 1
    except (OSError, ValueError) as exc:
        print(f"FOID inspection failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
