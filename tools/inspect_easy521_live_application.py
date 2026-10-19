#!/usr/bin/env python3
"""Compare a fixed Easy521 candidate with explicit application-file readbacks.

Offline only: bounded ordinary-file reads, strict record parsing and bounded
zlib expansion. Report 30 documents the observed compressed userprog/varsinfo
formats and AutoShop's actual compression functions/version gate. Raw metadata
is never called a checksum or regenerated. Full vendor payloads are not emitted
in JSON. Optional private candidate creation inherits this device's 15 unchanged
files and replaces only its two ARM objects. The symbol metadata policy needs
explicit review: the vendor source-MD5 generator is not reproduced, and device
enforcement of that field is unknown, rather than proven to reject this reuse.

This is not device discovery, a new allocator, generic configuration generation,
firmware compatibility certification or deployment. No processes, sockets, USB,
Windows, CPU control, remote writes, retries or automatic recovery are available.
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
import zlib

SCHEMA = "plcidf.easy521-live-application-inspection.v1"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_EXPANDED_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
CONFIG_FILES = ("syscfg.foid", "ethercat.foid", "modbus.foid", "canlink.foid",
                "modcfg.foid", "ethip.foid", "EIPIns.foid", "ecttrans.foid",
                "dataLog.foid", "OpcUAServer.foid", "sftpserver.foid", "Ecam.foid")
FILES = ("userprog.foid", "stprog.o", "stuserprog.o", "symbol.sym", "varsinfo.foid", *CONFIG_FILES)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def records(data: bytes, expected: tuple[int, ...]) -> list[dict]:
    """Parse one exact ordered tag/length sequence, requiring complete EOF.

    Each tag and length is a little-endian DWORD. Values stay opaque; the
    caller chooses the specific observed format, never a guessed general TLV
    schema. Foreign tags, truncation, excess size and extra bytes raise
    ValueError. No I/O, decompression or mutation occurs.
    """
    if type(data) is not bytes or len(data) > MAX_FILE_BYTES or len(expected) > 64:
        raise ValueError("records require bounded immutable bytes and at most64 tags")
    offset, result = 0, []
    for tag in expected:
        if len(data) - offset < 8:
            raise ValueError("truncated record header")
        actual, size = struct.unpack_from("<II", data, offset)
        if actual != tag or size > len(data) - offset - 8:
            raise ValueError("foreign tag/order or truncated payload")
        result.append({"tag": tag, "offset": offset, "value": data[offset + 8:offset + 8 + size]})
        offset += 8 + size
    if offset != len(data):
        raise ValueError("trailing bytes after expected record sequence")
    return result


def _record(tag, value):
    return struct.pack("<II", tag, len(value)) + value


def _header(data):
    if len(data) < 64:
        raise ValueError("truncated64-byte FOID header")
    r = records(data[:64], (0x100, 0x200, 0x201))
    if r[0]["value"] != b"foid" or len(r[1]["value"]) != 32 or len(r[2]["value"]) != 4:
        raise ValueError("foreign FOID envelope")
    return {"metadata_sha256": sha(r[1]["value"]),
            "caller_u32": struct.unpack("<I", r[2]["value"])[0]}


def decompress_exact(data: bytes, expected_bytes: int) -> bytes:
    """Expand a single bounded zlib stream to exactly the declared byte count.

    Rejects booleans/negative/oversized declarations, concatenated streams,
    trailing bytes, missing end/checksum, truncation and decompression bombs.
    The sole decompress call permits at most expected_bytes+1 output bytes;
    unbounded flush is never called. Returns bytes, or raises ValueError.
    """
    if (type(data) is not bytes or not 1 <= len(data) <= MAX_FILE_BYTES
            or type(expected_bytes) is not int or not 0 <= expected_bytes <= MAX_EXPANDED_BYTES):
        raise ValueError("compressed stream or declared expansion exceeds bounds")
    try:
        decoder = zlib.decompressobj()
        result = decoder.decompress(data, expected_bytes + 1)
    except zlib.error as exc:
        raise ValueError("invalid zlib stream/checksum") from exc
    if (len(result) != expected_bytes or not decoder.eof
            or decoder.unused_data or decoder.unconsumed_tail):
        raise ValueError("zlib expansion length/EOF/trailing data differs")
    return result


def compress_existing_userprog(data: bytes) -> bytes:
    """Apply the observed two-section userprog transform to an existing wrapper.

    Preserves its original64-byte header and UUID; compresses only tags40000
    and40100 with zlib's default compress, inserts tag40200's six LE length
    DWORDs. Optional tag40120 is outside this fixed profile and rejected. The
    transform matches AutoShop e70570, with no task/opcode generation or I/O.
    """
    _header(data)
    r = records(data, (0x100, 0x200, 0x201, 0x40000, 0x40100, 0x280))
    if any(len(item["value"]) > MAX_EXPANDED_BYTES for item in r[3:5]):
        raise ValueError("uncompressed program section exceeds limit")
    first, second = (zlib.compress(item["value"]) for item in r[3:5])
    table = struct.pack("<6I", len(first), len(r[3]["value"]), len(second), len(r[4]["value"]), 0, 0)
    return (data[:64] + _record(0x40000, first) + _record(0x40100, second)
            + _record(0x40200, table) + _record(0x280, r[-1]["value"]))


def normalize_userprog(data: bytes) -> dict:
    """Decode the observed compressed userprog and return bounded normalized data.

    Length table is exactly six DWORDs; this profile requires the third pair
    zero. Returned raw section bytes are for offline comparison only; reports
    emit their hashes/lengths rather than these vendor payloads.
    """
    header = _header(data)
    r = records(data, (0x100, 0x200, 0x201, 0x40000, 0x40100, 0x40200, 0x280))
    if len(r[5]["value"]) != 24:
        raise ValueError("userprog length table must contain six DWORDs")
    lengths = struct.unpack("<6I", r[5]["value"])
    if lengths[4:] != (0, 0) or lengths[0] != len(r[3]["value"]) or lengths[2] != len(r[4]["value"]):
        raise ValueError("userprog compressed-length table differs or third section unsupported")
    decoded = [decompress_exact(r[3]["value"], lengths[1]),
               decompress_exact(r[4]["value"], lengths[3])]
    return {"header": header, "length_table": list(lengths), "sections": decoded,
            "uuid": r[-1]["value"], "prefix": data[:64]}


def compress_existing_varsinfo(data: bytes) -> bytes:
    """Transform an existing raw varsinfo record, preserving UUID/header/tail.

    AutoShop e70260 changes tag30000 to30001. New payload is LE32 raw length,
    LE32 compressed length and one default-zlib stream. Other bytes are copied
    verbatim, including unknown metadata and flags. No allocation is performed.
    """
    r = records(data, (0x28000, 0x100, 0x200, 0x201, 0x30000, 0x3000f, 0x30020))
    _header(data[r[1]["offset"]:])
    raw = r[4]["value"]
    if len(raw) > MAX_EXPANDED_BYTES:
        raise ValueError("raw allocation section exceeds limit")
    compressed = zlib.compress(raw)
    value = struct.pack("<II", len(raw), len(compressed)) + compressed
    return (data[:r[4]["offset"]] + _record(0x30001, value)
            + data[r[5]["offset"]:])


def normalize_varsinfo(data: bytes) -> dict:
    """Decode the exact length-prefixed compressed varsinfo profile, offline."""
    r = records(data, (0x28000, 0x100, 0x200, 0x201, 0x30001, 0x3000f, 0x30020))
    header = _header(data[r[1]["offset"]:])
    value = r[4]["value"]
    if len(value) < 8:
        raise ValueError("compressed varsinfo lacks length pair")
    raw_bytes, compressed_bytes = struct.unpack_from("<II", value)
    if compressed_bytes != len(value) - 8:
        raise ValueError("varsinfo compressed-length prefix differs")
    return {"header": header, "raw": decompress_exact(value[8:], raw_bytes),
            "length_table": [raw_bytes, compressed_bytes], "uuid": r[0]["value"],
            "prefix": data[:r[4]["offset"]], "tail": data[r[5]["offset"]:]}


def compare_files(template: dict[str, bytes], live: dict[str, bytes]) -> dict:
    """Compare two explicit17-file snapshots without claiming runtime equivalence.

    Configuration payloads/module DWORDs are compared separately from opaque
    metadata. Program wrappers require strict bounded expansion, equal raw
    sections/UUIDs and inherited headers. Returned JSON contains no raw payload.
    Missing/foreign files or malformed wrappers raise ValueError/KeyError.
    """
    if set(template) != set(FILES) or set(live) != set(FILES):
        raise ValueError("exact17-file application observation set is required")
    values = [*template.values(), *live.values()]
    if (any(type(raw) is not bytes or len(raw) > MAX_FILE_BYTES for raw in values)
            or sum(len(raw) for raw in values) > MAX_TOTAL_BYTES):
        raise ValueError("file comparison inputs exceed immutable byte bounds")
    comparisons = []
    for name in CONFIG_FILES:
        left, right = template[name], live[name]
        a, b = _header(left), _header(right)
        offsets = [i for i, (x, y) in enumerate(zip(left, right)) if x != y]
        comparisons.append({"name": name, "template_bytes": len(left), "live_bytes": len(right),
            "template_sha256": sha(left), "live_sha256": sha(right),
            "same_length": len(left) == len(right), "changed_overlap_bytes": len(offsets),
            "first_changed_offsets": offsets[:64], "payload_equal": left[64:] == right[64:],
            "caller_u32_equal": a["caller_u32"] == b["caller_u32"], "caller_u32": b["caller_u32"],
            "only_opaque_metadata_changed": len(left) == len(right) and all(20 <= i < 52 for i in offsets),
            "template_metadata_sha256": a["metadata_sha256"], "live_metadata_sha256": b["metadata_sha256"]})
    user = normalize_userprog(live["userprog.foid"])
    uraw = records(template["userprog.foid"], (0x100, 0x200, 0x201, 0x40000, 0x40100, 0x280))
    user_equal = (user["prefix"] == template["userprog.foid"][:64]
                  and user["uuid"] == uraw[-1]["value"]
                  and user["sections"] == [uraw[3]["value"], uraw[4]["value"]])
    variable = normalize_varsinfo(live["varsinfo.foid"])
    vraw = records(template["varsinfo.foid"], (0x28000, 0x100, 0x200, 0x201, 0x30000, 0x3000f, 0x30020))
    variable_equal = (variable["prefix"] == template["varsinfo.foid"][:vraw[4]["offset"]]
                      and variable["tail"] == template["varsinfo.foid"][vraw[5]["offset"]:]
                      and variable["raw"] == vraw[4]["value"] and variable["uuid"] == user["uuid"])
    return {"configuration_comparisons": comparisons,
        "configuration_payloads_and_versions_equal": all(x["payload_equal"] and x["caller_u32_equal"] for x in comparisons),
        "userprog": {"normalized_equal": user_equal, "length_table": user["length_table"],
            "header_equal": user["prefix"] == template["userprog.foid"][:64],
            "expanded_sections": [{"bytes": len(s), "sha256": sha(s)} for s in user["sections"]],
            "default_zlib_reproduces_live_bytes": compress_existing_userprog(template["userprog.foid"]) == live["userprog.foid"]},
        "varsinfo": {"normalized_equal": variable_equal, "length_table": variable["length_table"],
            "expanded_bytes": len(variable["raw"]), "expanded_sha256": sha(variable["raw"]),
            "default_zlib_reproduces_live_bytes": compress_existing_varsinfo(template["varsinfo.foid"]) == live["varsinfo.foid"]},
        "interface_payload_compatibility_observed": user_equal and variable_equal,
        "symbol_bytes_equal": template["symbol.sym"] == live["symbol.sym"],
        "symbol_vendor_md5_recomputed": False, "runtime_abi_verified": False,
        "device_acceptance_of_new_objects_verified": False}


def _read(path: Path, budget: dict) -> bytes:
    info = path.lstat()
    fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    def identity(s):
        return tuple(getattr(s, name) for name in fields)
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES or info.st_size > MAX_TOTAL_BYTES - budget["read"]:
        raise ValueError("input must be bounded ordinary file within the shared read budget")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        start = os.fstat(fd)
        if identity(info) != identity(start) or not stat.S_ISREG(start.st_mode):
            raise ValueError("input changed before reading")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        budget["read"] += len(data)
        if len(data) != start.st_size or identity(start) != identity(os.fstat(fd)) or identity(start) != identity(path.lstat()):
            raise ValueError("input changed during reading")
        return data
    finally:
        os.close(fd)


def inspect_paths(*, candidate_directory, live_directory, output_directory=None) -> dict:
    """Inspect explicit private snapshots and optionally create a new17-file candidate.

    candidate_directory contains the research28 application-candidate.json and
    files/; live_directory contains17 readbacks plus exactly one native upload
    report per filename. Every reported hash/length and target must agree. If
    output_directory is supplied, it must not exist or overlap either input;
    the new files inherit live bytes except the two source-associated ARM
    objects. All inputs are rechecked. Symbol-metadata and archive-transfer
    policies remain explicitly unresolved. Failure never removes evidence.
    """
    candidate, live_dir = Path(candidate_directory).absolute(), Path(live_directory).absolute()
    output = Path(output_directory).absolute() if output_directory is not None else None
    if output is not None:
        if output.exists() or output.is_symlink() or not output.parent.is_dir():
            raise ValueError("output must be absent with an existing parent")
        if any(output.resolve().is_relative_to(root.resolve()) or root.resolve().is_relative_to(output.resolve())
               for root in (candidate, live_dir)):
            raise ValueError("output must not overlap input directories")
    budget, snapshots = {"read": 0}, {}
    def read(path):
        raw = _read(path, budget)
        snapshots[path] = raw
        return raw
    parent_path = candidate / "application-candidate.json"
    parent_raw = read(parent_path)
    parent = json.loads(parent_raw)
    if (not isinstance(parent, dict) or parent.get("schema") != "plcidf.easy521-fixed-application-candidate.v1"
            or parent.get("ok") is not True or parent.get("profile") != "easy521-bess-existing-interface-v1"):
        raise ValueError("requires the explicit successful fixed-interface parent candidate")
    listed = parent.get("files")
    if not isinstance(listed, list) or len(listed) != 18 or not all(isinstance(x, dict) for x in listed):
        raise ValueError("parent must contain18 explicit file records")
    index = {x.get("remote_name"): x for x in listed}
    if len(index) != 18 or set(index) != {*FILES, "project.hcpp"}:
        raise ValueError("parent file names differ from the fixed candidate")
    template, live, evidence, targets = {}, {}, [], []
    for name in FILES:
        raw = template[name] = read(candidate / "files" / name)
        if index[name].get("sha256") != sha(raw) or index[name].get("size") != len(raw):
            raise ValueError("parent candidate file differs from its manifest: " + name)
        current = live[name] = read(live_dir / name)
        matches = list(live_dir.glob("*-application-" + name + ".json"))
        if len(matches) != 1:
            raise ValueError("require exactly one readback report for " + name)
        evidence_raw = read(matches[0])
        item = json.loads(evidence_raw)
        if (not isinstance(item, dict) or item.get("schema") != "plcidf.native-application-transaction.v1"
                or item.get("ok") is not True or item.get("status") != "file-read"
                or item.get("action") != "upload-read" or item.get("remote_name") != name
                or item.get("bytes") != len(current) or item.get("sha256") != sha(current)
                or item.get("plc_change_possible") is not False or item.get("automatic_retries") != 0
                or item.get("release_rc") != 0 or item.get("close_called") is not True
                or not isinstance(item.get("target"), dict)):
            raise ValueError("readback evidence differs from observed bytes or read-only scope: " + name)
        target = item["target"]
        targets.append(target)
        evidence.append({"name": name, "path": str(matches[0]), "sha256": sha(evidence_raw),
                         "readback_sha256": sha(current), "bytes": len(current)})
    if any(target != targets[0] for target in targets):
        raise ValueError("readback targets differ; do not merge devices into one candidate")
    comparison = compare_files(template, live)
    result = {"schema": SCHEMA, "ok": True, "status": "inspected", "scope": "offline-file-payload-comparison",
              "comparison": comparison, "readback_target": targets[0], "readback_evidence": evidence,
              "parent_candidate_sha256": sha(parent_raw), "candidate_directory": str(candidate),
              "live_directory": str(live_dir), "zlib_runtime_version": zlib.ZLIB_RUNTIME_VERSION,
              "inputs": [{"path": str(p), "bytes": len(raw), "sha256": sha(raw)} for p, raw in snapshots.items()],
              "new_device_access": False, "device_origin_cryptographically_authenticated": False,
              "snapshot_atomicity_verified": False, "downloadable": False,
              "symbol_metadata_policy_review_required": True,
              "symbol_metadata_inherited_from_device": True,
              "symbol_vendor_md5_for_new_source_verified": False,
              "archive_transfer_policy_verified": False,
              "tool_sha256": sha(Path(__file__).read_bytes())}
    if output is not None:
        if not comparison["configuration_payloads_and_versions_equal"] or not comparison["interface_payload_compatibility_observed"]:
            raise ValueError("refuse derived candidate when configuration/interface payloads differ")
        output.mkdir()
        files_dir = output / "files"
        files_dir.mkdir()
        new_records = []
        for item in listed:
            name = item["remote_name"]
            if name == "project.hcpp":
                continue
            is_new = name in ("stprog.o", "stuserprog.o")
            raw = template[name] if is_new else live[name]
            destination = files_dir / name
            with destination.open("xb") as stream:
                stream.write(raw)
            destination.chmod(0o444)
            new_records.append({"index": len(new_records), "remote_name": name, "path": str(destination),
                "size": len(raw), "sha256": sha(raw), "origin": "parent-native-object" if is_new else "this-device-readback-verbatim",
                "symbol_metadata_inherited_from_device": name == "symbol.sym"})
        with (output / "parent-application-candidate.json").open("xb") as stream:
            stream.write(parent_raw)
        for path, raw in snapshots.items():
            if _read(path, budget) != raw:
                raise ValueError("input drift after derived candidate creation")
        for item in new_records:
            if sha(_read(Path(item["path"]), budget)) != item["sha256"]:
                raise ValueError("derived candidate bytes changed")
        result.update(status="live-derived-candidate-prepared", profile="easy521-observed-live-interface-v1",
                      files=new_records, file_count=17, source_association=parent.get("association"),
                      project_archive_included=False,
                      project_archive_reason="read error132 does not establish absence, optionality or required write policy",
                      symbol_metadata_policy_review_required=True, candidate_device_acceptance_verified=False)
        with (output / "live-application-candidate.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
    else:
        for path, raw in snapshots.items():
            if _read(path, budget) != raw:
                raise ValueError("input drift after inspection")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-directory", required=True, type=Path)
    parser.add_argument("--live-directory", required=True, type=Path)
    parser.add_argument("--output-directory", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.report is not None:
            report = args.report.absolute()
            if report.exists() or report.is_symlink() or not report.parent.is_dir():
                raise ValueError("report must be a new nonsymlink file with existing parent")
            if any(report.resolve().is_relative_to(root.resolve()) for root in
                   (args.candidate_directory, args.live_directory)):
                raise ValueError("report must not modify either input directory")
        result = inspect_paths(candidate_directory=args.candidate_directory, live_directory=args.live_directory,
                               output_directory=args.output_directory)
        encoded = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if args.report is None:
            print(encoded, end="")
        else:
            with args.report.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"live application inspection failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
