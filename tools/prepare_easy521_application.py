#!/usr/bin/env python3
"""Prepare an offline Easy521 candidate from a fixed, existing interface.

This is deliberately a template-reuse profile, not a general FOID generator.
The caller supplies the exact pinned BESS_E521_DEV interface/configuration and
two newly built ARM linked objects, their build report, and its source. The
output is an ordered 18-file candidate plus a provenance manifest. Unknown
configuration bytes are inherited verbatim; no 32-byte field is fabricated.

The source-recovery file is the vendor's supported five-byte ``Empty`` marker,
not an old source archive falsely associated with new logic. The marker writer
and validator, TLV relationships, and pinned input origins are documented in
firmware report 28. Task period/retain semantics, complete runtime ABI, device
feature selection, and device acceptance remain unverified. No USB, sockets,
Windows programs, child processes, PLC writes, or retries occur here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys

try:
    from tools.inspect_easy521_foid import inspect_foid
    from tools.inspect_vendor_application import parse_elf
except ImportError:
    from inspect_easy521_foid import inspect_foid
    from inspect_vendor_application import parse_elf

SCHEMA = "plcidf.easy521-fixed-application-candidate.v1"
PROFILE = "easy521-bess-existing-interface-v1"
MAX_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024

# These are existing legal configuration/allocation templates, not reference
# executable outputs. Every byte is pinned to the research-28 frozen inputs;
# report 28 preserves their actual independent origins and unknown build batch.
PINNED_HASHES = {
    "Temp/userprog.foid": "02d2a2c53d8f40fb1efcd97887eeddfe2a7c003e65b6f9a3747e13c73362167c",
    "Temp/varsinfo.foid": "af4e636e0119891cbf6be9db8686ce0fc775025556509a4fb7a6c932bcc0fb1d",
    "Temp/symbol.sym": "ded220922669b1c5f664cd09c39348ffc816de98fade506a74a0ac32672bef92",
    "Output.sdt": "c07a72afae6f4cac3e87308bab17d0eec1658a764b7e67102de77ec984ca863b",
    "Temp/DownLoad/EIPIns.foid": "57813a42be8f39abc48e66c6e5c26751360e931c7555c9a8cb93a85dcf1dcb26",
    "Temp/DownLoad/Ecam.foid": "32938d9a01867d7cb0c3ab6a1256457588d5eefacf08cd07843089a5af5c6c80",
    "Temp/DownLoad/OpcUAServer.foid": "f99c783ca7b6dd0106e441f8209d318943461ea7b6ac0c274c18bf9265bbd0cd",
    "Temp/DownLoad/canlink.foid": "d592bea27b9ff13d5794745e4c1e20d0e510b68083a3d331b4f76010d2200c81",
    "Temp/DownLoad/dataLog.foid": "0b3e745e5f801f711a5f6a6d1e00e5c4dd9d049d8068a150def56277ac6d15d4",
    "Temp/DownLoad/ecttrans.foid": "ba4bd8ac6fdd65984a812b82a41ab5bcdd3fc631da44e1038df06f7f00b2d538",
    "Temp/DownLoad/ethercat.foid": "36d2bb6061e52f0ffcc7bdc8bd1253ae0b5230be9d653e7ddbff77156dbbaf1e",
    "Temp/DownLoad/ethip.foid": "ca4029c287a568de2a61710caa8c6ed804e9408238d527c383c484507793a8ac",
    "Temp/DownLoad/modbus.foid": "e0bd53529dac57e11ca837494f21c0f75d89fb5b19b03a53c4ad668aba0d5190",
    "Temp/DownLoad/modcfg.foid": "2421e814e2b3f2f64f0d1017034a9acac603cc349ade037a39946476f2c3ae48",
    "Temp/DownLoad/sftpserver.foid": "b5b4c86bc9ed9ef8529a41e13d1f5a1a4d9c89b7b8a751dc8a7ba9df400a59a2",
    "Temp/DownLoad/syscfg.foid": "a1ee41467a27c3b9a956b378eb4b0096910d94ac14d3eb1f59100da8936cc70c",
    "Output.prg": "3bfe2324eba14f55199d5bb4d0ee72d265a7453fc78b0c6e66d265ae21adb63b",
    "VarsInfo.vi": "9c7cab9b340bbc5e5a352caa6fa54c3c8514b0c259b9691908995ca48ea1df95",
    "ILIndex.idx": "d56b4f7edb98b3da1f9ef85166c5697fe8ae6c6c6987b59dc346e85c52cbde21",
    "Config.sdt": "af9e01cb471c8c7d6b4c41605192adeed28f03f6b3570675bea97ae24760f664",
    "变量表.gvt": "63700c1f9b5a4b1201201442b1d614344d9b6346fd2ad913b2c22796560b95c5",
    "VarNameAndAddr.csv": "75e89e3c7ef94dcb53129f4e4b1b0ef4bf72947ac4296633276c0f0ac29e44fe",
}

# Relative order from pinned AutoShop CaculateAllDownloadFile2, report 16.
# Presence is specific to this candidate, not a claim that all firmware versions
# require this exact configuration subset or accept it without feature checks.
CONFIG_ORDER = ("syscfg.foid", "ethercat.foid", "modbus.foid", "canlink.foid",
                "modcfg.foid", "ethip.foid", "EIPIns.foid", "ecttrans.foid",
                "dataLog.foid", "OpcUAServer.foid", "sftpserver.foid", "Ecam.foid")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse_records(data: bytes, expected_ids: tuple[int, ...]) -> list[dict]:
    """Parse a bounded, explicitly ordered LE u32 tag/length record sequence.

    Values remain opaque bytes. Exact EOF, order, count, and bounds are required;
    callers must provide the observed tags for the particular file format.
    ValueError rejects foreign/truncated records. No I/O or mutation occurs.
    """
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError("record input must be bounded bytes")
    position, records = 0, []
    for tag in expected_ids:
        if len(data) - position < 8:
            raise ValueError("truncated record header")
        found, size = struct.unpack_from("<II", data, position)
        if found != tag or size > len(data) - position - 8:
            raise ValueError("unexpected tag/order or truncated record value")
        records.append({"tag": tag, "offset": position, "value_offset": position + 8,
                        "size": size, "value": data[position + 8:position + 8 + size]})
        position += 8 + size
    if position != len(data):
        raise ValueError("unexpected trailing record bytes")
    return records


def inspect_interface(files: dict[str, bytes]) -> dict:
    """Check immutable user-program/variable/index relationships for this profile.

    The input maps the pinned relative filenames to bytes. Returns hashes,
    offsets and exact copied-payload relationships; period, retention and the
    runtime interpretation of instruction indices remain explicitly unknown.
    Missing/mismatching inputs raise ValueError/KeyError without side effects.
    """
    user = parse_records(files["Temp/userprog.foid"], (0x100, 0x200, 0x201,
                                                        0x40000, 0x40100, 0x280))
    variable = parse_records(files["Temp/varsinfo.foid"],
                             (0x28000, 0x100, 0x200, 0x201, 0x30000, 0x3000f, 0x30020))
    # The nested 64-byte envelope is the same observed layout as configurations.
    inspect_foid(files["Temp/userprog.foid"])
    inspect_foid(files["Temp/varsinfo.foid"][variable[1]["offset"]:])
    uuid = user[-1]["value"]
    if (not re.fullmatch(rb"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                        rb"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}", uuid)
            or uuid != variable[0]["value"]):
        raise ValueError("user-program and variable project identifiers differ")
    if user[3]["value"] != files["Output.prg"]:
        raise ValueError("userprog instruction payload differs from Output.prg")
    index = files["ILIndex.idx"]
    if (len(index) < 30 or struct.unpack_from("<IH", index) != (0x989681, 1)
            or index[6:26] != bytes(20)):
        raise ValueError("unsupported ILIndex header")
    count = struct.unpack_from("<I", index, 26)[0]
    if count > 4096 or len(index) != 30 + 4 * count or user[4]["value"] != index[30:]:
        raise ValueError("userprog instruction-index payload differs from ILIndex")
    if variable[4]["value"] != files["VarsInfo.vi"]:
        raise ValueError("varsinfo allocation payload differs from VarsInfo.vi")
    symbols = files["Temp/symbol.sym"]
    # The two bmys sections have variable-size name tables. No code address or
    # checksum is guessed: this profile inherits the entire pinned symbol file.
    if symbols.count(b"bmys") != 2 or symbols.count(b"MAIN\0") != 2 or symbols.count(b"SBR_001\0") != 2:
        raise ValueError("unsupported fixed symbol-name interface")
    def describe(records):
        return [{k: v for k, v in item.items() if k != "value"}
                | {"sha256": sha(item["value"])} for item in records]
    return {"userprog_records": describe(user), "varsinfo_records": describe(variable),
            "project_identifier": uuid.decode("ascii"), "instruction_index_count": count,
            "instruction_index_nonempty": [{"index": i, "value": v} for i, v in
                enumerate(struct.unpack(f"<{count}I", index[30:])) if v != 0xffffffff],
            "output_instruction_payload_equal": True, "instruction_index_payload_equal": True,
            "variable_allocation_payload_equal": True, "entry_names": ["MAIN", "SBR_001"],
            "task_period_us": None, "retention_semantics": "unknown",
            "runtime_instruction_to_entry_semantics_verified": False}


def assemble_fixed_userprog(files: dict[str, bytes]) -> bytes:
    """Reassemble the unchanged interface wrapper from its checked source files.

    Uses the original 64-byte header verbatim (including opaque metadata), the
    original instruction bytes, the validated ILIndex array, and original UUID.
    This is an exact reconstruction of a frozen wrapper, not instruction-code
    generation or a new UUID allocator. A byte difference is an error.
    """
    inspect_interface(files)
    original = files["Temp/userprog.foid"]
    identifier = parse_records(original, (0x100, 0x200, 0x201, 0x40000, 0x40100, 0x280))[-1]["value"]
    result = original[:64]
    for tag, value in ((0x40000, files["Output.prg"]),
                       (0x40100, files["ILIndex.idx"][30:]), (0x280, identifier)):
        result += struct.pack("<II", tag, len(value)) + value
    if result != original:
        raise ValueError("fixed userprog source reconstruction differs")
    return result


def _read(path: Path, budget: dict) -> bytes:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
        raise ValueError("input must be a bounded ordinary file, without final symlink")
    if before.st_size > MAX_TOTAL_BYTES - budget["read"]:
        raise ValueError("candidate input read budget exceeded")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    def identity(s):
        return (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or identity(before) != identity(opened):
            raise ValueError("input changed before reading")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(MAX_BYTES + 1)
        budget["read"] += len(data)
        if (len(data) != before.st_size or identity(opened) != identity(os.fstat(fd))
                or identity(opened) != identity(path.lstat())):
            raise ValueError("input changed while reading")
        return data
    finally:
        os.close(fd)


def prepare_application(*, template_dir, source, stuserprog, stprog, build_report,
                        output_dir) -> dict:
    """Create a new private candidate directory with explicit ordered files.

    Paths name an exact pinned template directory, the new source and linked
    objects, the caller's existing JSON build report, and an absent output dir.
    Input snapshots, their hashes and the report are preserved in the output.
    The build report is an association artifact, not independently authenticated
    compiler provenance. Any mismatch raises ValueError/OSError. Outputs are
    exclusively created; failed writes are retained, never retried or reused.
    Returns candidate metadata with downloadable/runtime flags always false.
    """
    template = Path(template_dir).absolute()
    output = Path(output_dir).absolute()
    paths = {name: template / name for name in PINNED_HASHES}
    paths.update({"source.st": Path(source).absolute(), "stuserprog.o": Path(stuserprog).absolute(),
                  "stprog.o": Path(stprog).absolute(), "build-report.json": Path(build_report).absolute()})
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("output directory must be absent with an existing parent")
    output_real = output.resolve()
    if output_real.is_relative_to(template.resolve()) or any(output_real == p.resolve() or p.resolve().is_relative_to(output_real)
           or output_real.is_relative_to(p.resolve()) for p in paths.values()):
        raise ValueError("output must not overlap an input path")
    budget = {"read": 0}
    data = {name: _read(path, budget) for name, path in paths.items()}
    for name, wanted in PINNED_HASHES.items():
        if sha(data[name]) != wanted:
            raise ValueError(f"fixed interface/configuration drift: {name}")
    interface = inspect_interface(data)
    for name in CONFIG_ORDER:
        inspect_foid(data[f"Temp/DownLoad/{name}"])
    for name in ("stuserprog.o", "stprog.o"):
        elf = parse_elf(data[name])
        if not all((elf["class_bits"] == 32, elf["endianness"] == "little",
                    elf["machine_id"] == 40, elf["type_id"] == 1,
                    elf["arm_eabi_version"] == 5)):
            raise ValueError("linked objects must be ARM ELF32 LE EABI5 REL")
        if not {"MAIN", "SBR_001"}.issubset(elf["defined_global_weak"]["names"]):
            raise ValueError("linked objects lack fixed MAIN/SBR_001 interface")
    report = json.loads(data["build-report.json"])
    if not isinstance(report, dict):
        raise ValueError("build report must be a JSON object")
    expected = {name: sha(data[name]) for name in
                ("source.st", "stuserprog.o", "stprog.o", "变量表.gvt", "VarNameAndAddr.csv")}
    # Accept only the actual native compiler/link/binder report schema, and
    # cross-check all shared interface hashes and the two resulting objects.
    # A JSON report is still not a cryptographic build attestation.
    source_chain = report.get("source_chain")
    if (report.get("schema") != "plcidf.native-bound-application.v1"
            or report.get("profile") != "bess-easy521-existing-pcmd-v1"
            or report.get("ok") is not True
            or any(report.get(k) is not True for k in ("source_compilation_verified",
                       "existing_allocation_verified", "source_to_bound_objects_verified"))
            or not isinstance(source_chain, dict)
            or source_chain.get("source_sha256") != expected["source.st"]):
        raise ValueError("build report lacks the matching successful native source chain")
    outputs = report.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 2:
        raise ValueError("build report must identify exactly two linked outputs")
    for item, name in zip(outputs, ("stuserprog.o", "stprog.o")):
        if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                or Path(item["path"]).absolute() != paths[name]
                or item.get("sha256") != expected[name] or item.get("bytes") != len(data[name])):
            raise ValueError("build report linked output identity differs")
    inputs = report.get("inputs")
    if not isinstance(inputs, list):
        raise ValueError("build report inputs must be a list")
    for name in ("变量表.gvt", "VarNameAndAddr.csv", "VarsInfo.vi", "ILIndex.idx", "Output.prg",
                 "Output.sdt", "Config.sdt", "Temp/userprog.foid", "Temp/varsinfo.foid", "Temp/symbol.sym"):
        matches = [item for item in inputs if isinstance(item, dict)
                   and str(item.get("path", "")).replace("\\", "/").endswith("/" + name)]
        if len(matches) != 1 or matches[0].get("sha256") != sha(data[name]) or matches[0].get("bytes") != len(data[name]):
            raise ValueError(f"build report fixed interface differs: {name}")
    output.mkdir()
    snapshots = output / "inputs"
    snapshots.mkdir()
    origins = []
    for name, raw in data.items():
        dest = snapshots / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("xb") as stream:
            stream.write(raw)
        dest.chmod(0o444)
        origins.append({"name": name, "source_path": str(paths[name]),
                        "snapshot_path": str(dest), "size": len(raw), "sha256": sha(raw)})
    records = [("project.hcpp", b"Empty", "generated-vendor-no-source-marker"),
               ("userprog.foid", assemble_fixed_userprog(data), "reassembled-fixed-interface-inherited-header"),
               ("stprog.o", data["stprog.o"], "new-linked-object"),
               ("stuserprog.o", data["stuserprog.o"], "new-linked-object"),
               ("symbol.sym", data["Temp/symbol.sym"], "inherited-fixed-interface"),
               ("varsinfo.foid", data["Temp/varsinfo.foid"], "inherited-fixed-interface")]
    records.extend((name, data[f"Temp/DownLoad/{name}"], "inherited-fixed-configuration")
                   for name in CONFIG_ORDER)
    payload_dir = output / "files"
    payload_dir.mkdir()
    manifest_files = []
    for index, (name, raw, origin) in enumerate(records):
        path = payload_dir / name
        with path.open("xb") as stream:
            stream.write(raw)
        path.chmod(0o444)
        manifest_files.append({"index": index, "remote_name": name, "path": str(path),
                               "size": len(raw), "sha256": sha(raw), "origin": origin})
    # Re-read every original and produced artifact; input drift does not promote
    # the already created candidate, and the directory remains evidence.
    for name, path in paths.items():
        if _read(path, budget) != data[name]:
            raise ValueError("input drift after candidate creation")
    for item in manifest_files:
        if sha(_read(Path(item["path"]), budget)) != item["sha256"]:
            raise ValueError("candidate file drift after creation")
    for item in origins:
        if sha(_read(Path(item["snapshot_path"]), budget)) != item["sha256"]:
            raise ValueError("input snapshot drift after creation")
    result = {"schema": SCHEMA, "ok": True, "status": "candidate-prepared",
              "profile": PROFILE, "scope": "fixed-existing-interface-application-candidate",
              "files": manifest_files, "inputs": origins, "interface": interface,
              "association": expected, "source_recovery_archive": "vendor-Empty-marker",
              "candidate_file_set_complete": True, "file_count": len(manifest_files),
              "fixed_userprog_source_reconstruction_equal": True,
              "template_same_original_vendor_build_verified": False,
              "source_compilation_independently_verified": False,
              "native_source_chain_report_checked": True,
              "device_identity": None, "device_feature_selection_verified": False,
              "downloadable": False, "runtime_abi_verified": False,
              "device_accepted": False, "device_executed": False,
              "limitations": ["Only this exact existing allocation/configuration is accepted.",
                  "Task period, retention, runtime entry-index interpretation and firmware compatibility remain unverified.",
                  "Unknown configuration and symbol metadata are inherited, not regenerated or authenticated.",
                  "The 18-file candidate follows observed order, not a device-calibrated feature-selection decision.",
                  "Source is retained on the host; PLC source recovery receives the vendor Empty marker."],
              "tool_sha256": sha(Path(__file__).read_bytes())}
    with (output / "application-candidate.json").open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("template-dir", "source", "stuserprog", "stprog", "build-report", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = prepare_application(**vars(args))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(f"candidate preparation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
