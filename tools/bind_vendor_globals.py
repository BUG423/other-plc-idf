#!/usr/bin/env python3
"""Apply eight observed Easy521 ordinary-global bindings to existing ARM objects.

This Linux-only, offline post-link experiment reproduces a narrow SInfo update
from firmware reports 16/17 (2026-10-19): STRelocation.dll 0x10003d09..3d1c
writes st_value and the low st_info nibble, leaving st_shndx UND. Addresses come
only from the pinned GVT parser, with an independent CSV agreement check. Type
8 is the vendor ordinary-global category, NOT the GVT INT/REAL type code.

Inputs are a source file, GVT, CSV, successful two-stage link report, and an
explicit association manifest. The latter has these exact keys::

  {"schema": "plcidf.global-binding-inputs.v1",
   "profile": "easy521-eight-existing-globals-v1",
   "source_sha256": "...", "gvt_sha256": "...",
   "address_csv_sha256": "...", "link_report_sha256": "...",
   "link_inputs": [{"role": "pou-000", "sha256": "..."},
                   {"role": "syslib", "sha256": "..."},
                   {"role": "stclib", "sha256": "..."}],
   "manifest_hash": "..."}

manifest_hash is SHA-256 of UTF-8 JSON of all other keys, sorted keys, no extra
whitespace, ensure_ascii=False, allow_nan=False. Include every ordered POU and
both libraries from the link report. These hashes verify a caller-declared
batch association, NOT source-to-POU compilation. In particular this manifest
is not a signature or a substitute for missing compiler provenance. Originals,
frozen link inputs and both linked outputs must still match recorded hashes.

Only the explicit eight-name map below is supported. There is no general name
conversion, allocator, ST compiler, relocation filtering, task/config/FOID
generator, download, CPU control, network, subprocess or vendor DLL execution.
Outputs remain ET_REL, with vendor-bound UND entries and unresolved runtime
dependencies. Success always reports downloadable=False and
source_compilation_verified=False. Proprietary objects stay in private storage.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_variables = _load("_binding_gvt", "inspect_vendor_variables.py")
_inspection = _load("_binding_elf", "inspect_vendor_application.py")
PROFILE = "easy521-eight-existing-globals-v1"
# Fixed ASCII names actually compared in reports 16/17, not str.upper() rules.
SYMBOL_MAP = (
    ("g_iBESS_Mode", "G_IBESS_MODE", "INT"),
    ("g_rBESS_Confidence", "G_RBESS_CONFIDENCE", "REAL"),
    ("g_rBESS_InfluenceP", "G_RBESS_INFLUENCEP", "REAL"),
    ("g_rBESS_InfluenceQ", "G_RBESS_INFLUENCEQ", "REAL"),
    ("g_rBESS_Pcmd", "G_RBESS_PCMD", "REAL"),
    ("g_rBESS_Qcmd", "G_RBESS_QCMD", "REAL"),
    ("g_rBESS_SensVP", "G_RBESS_SENSVP", "REAL"),
    ("g_rBESS_SensVQ", "G_RBESS_SENSVQ", "REAL"),
)
MAX_OBJECT = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_DOCUMENT = 4 * 1024 * 1024


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _read(path, limit):
    # O_NONBLOCK avoids hanging on a FIFO before fstat can reject it. Ancestor
    # directories are caller-trusted; this is not a hostile-filesystem sandbox.
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("input is not a bounded regular file: " + str(path))
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len(data) > limit or stamp(before) != stamp(after):
        raise ValueError("input changed or exceeded its byte budget: " + str(path))
    return data


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key: " + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError("nonfinite JSON constant: " + value)
    value = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(value, dict):
        raise ValueError("JSON record must be an object")
    return value


def _hash_field(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("expected a lowercase SHA-256")
    return value


def _inspection_hash(record):
    inspected = record.get("inspection")
    if not isinstance(inspected, dict):
        raise ValueError("missing or malformed recorded ELF inspection")
    return _hash_field(inspected.get("sha256"))


def _arm_object(data):
    parsed = _inspection.parse_elf(data)
    if (parsed["class_bits"], parsed["endianness"], parsed["type_id"], parsed["machine_id"]) != (32, "little", 1, 40):
        raise ValueError("only ELF32 little-endian ARM ET_REL is supported")
    return parsed


def _bindings(gvt, csv):
    parsed = _variables.parse_gvt(gvt)
    exported = _variables.parse_address_csv(csv)
    rows = {row["name"]: row for row in parsed["records"]}
    # Check EVERY GVT row. CSV may contain extra system entries, as the actual
    # export does; it must never fill in a missing or conflicting GVT record.
    for row in rows.values():
        entry = exported.get(row["name"].casefold())
        if not entry or (row["address"], row["width_bits"]) != (entry["address"], entry["width_bits"]):
            raise ValueError("address CSV disagrees with GVT: " + row["name"])
    selected = []
    for gvt_name, elf_name, kind in SYMBOL_MAP:
        row = rows.get(gvt_name)
        if row is None or row["type"] != kind:
            raise ValueError("missing or unsupported fixed global: " + gvt_name)
        selected.append({"gvt_name": gvt_name, "elf_name": elf_name, "iec_type": kind,
                         "width_bits": row["width_bits"], "address": row["address"],
                         "gvt_record_offset": row["record_offset"],
                         "gvt_address_offset": row["address_offset"],
                         "csv_line": exported[gvt_name.casefold()]["line"], "sinfo_type": 8})
    return selected


def _patch_object(data, bindings):
    parsed = _arm_object(data)
    header = struct.unpack_from("<HHIIIIIHHHHHH", data, 16)
    _, _, _, _, phoff, shoff, _, ehsize, _, phnum, shentsize, count, shstrndx = header
    if ehsize != 52 or phoff or phnum or shentsize != 40 or not 1 <= count <= 4096 or shstrndx == 0xffff:
        raise ValueError("unsupported ELF header, extended counts or program headers")
    sections = [struct.unpack_from("<IIIIIIIIII", data, shoff + 40 * i) for i in range(count)]
    # Reject section aliases/overlaps, especially a SYMTAB overlapping code,
    # strings or ELF headers: a legal field offset alone is insufficient proof.
    regions = [(0, 52), (shoff, shoff + count * 40)]
    for sec in sections:
        if sec[1] not in (0, 8) and sec[5]:
            regions.append((sec[4], sec[4] + sec[5]))
    regions.sort()
    if any(right[0] < left[1] for left, right in zip(regions, regions[1:])):
        raise ValueError("overlapping ELF sections or header tables are unsupported")
    candidates = [i for i, sec in enumerate(sections) if sec[1] == 2]
    if len(candidates) != 1 or any(sec[1] in (11, 18) for sec in sections):
        raise ValueError("exactly one SYMTAB and no DYNSYM/extended symbol indices are required")
    index = candidates[0]
    table = sections[index]
    if (parsed["sections"][index]["name"] != ".symtab" or table[9] != 16 or table[5] % 16
            or not 1 <= table[7] <= table[5] // 16 or data[table[4]:table[4] + 16] != bytes(16)):
        raise ValueError("unsupported symbol table name or entry stride")
    strings = sections[table[6]]
    names = data[strings[4]:strings[4] + strings[5]]
    symbols = {}
    for number in range(table[5] // 16):
        offset = table[4] + number * 16
        fields = struct.unpack_from("<IIIBBH", data, offset)
        name_offset = fields[0]
        end = names.find(b"\0", name_offset)
        if end < 0:
            raise ValueError("unterminated symbol name")
        name = names[name_offset:end]
        symbols.setdefault(name, []).append((number, offset, fields))
    patched = bytearray(data)
    changes, allowed = [], set()
    for binding in bindings:
        entries = symbols.get(binding["elf_name"].encode("ascii"), [])
        if len(entries) != 1:
            raise ValueError("missing or duplicate fixed ELF symbol: " + binding["elf_name"])
        number, offset, fields = entries[0]
        _, value, size, info, other, ndx = fields
        # This exact before-state is observed in the Linux outputs in report16.
        # Refuse already patched symbols rather than silently adopting them.
        if number < table[7] or (value, size, info, other, ndx) != (0, 0, 0x10, 0, 0):
            raise ValueError("unexpected nonzero/type/binding/visibility/non-UND symbol: " + binding["elf_name"])
        struct.pack_into("<I", patched, offset + 4, binding["address"])
        patched[offset + 12] = (info & 0xf0) | binding["sinfo_type"]
        permitted = [*range(offset + 4, offset + 8), offset + 12]
        allowed.update(permitted)
        changes.append({**binding, "symbol_index": number, "symbol_entry_offset": offset,
                        "before": {"st_value": value, "st_info": info, "st_shndx": ndx},
                        "after": {"st_value": binding["address"], "st_info": patched[offset + 12], "st_shndx": ndx},
                        "allowed_byte_offsets": permitted})
    changed = [{"offset": i, "before": a, "after": b}
               for i, (a, b) in enumerate(zip(data, patched)) if a != b]
    if not {item["offset"] for item in changed} <= allowed or len(patched) != len(data):
        raise ValueError("modifications escaped the st_value/st_info byte allowlist")
    _inspection.parse_elf(bytes(patched))
    undefined = parsed["undefined_global_weak"]["names"]
    bound_names = {row["elf_name"] for row in bindings}
    return bytes(patched), {"bindings": changes, "changed_bytes": changed,
                           "only_allowed_symbol_fields_changed": True,
                           "vendor_bound_undefined_names": sorted(bound_names),
                           "remaining_undefined_names": sorted(set(undefined) - bound_names),
                           "all_nonselected_bytes_unchanged": True}


def bind_globals(*, source, gvt, address_csv, link_report, association_manifest,
                 output_dir):
    """Validate a declared input batch and write two new, restricted objects.

    All parameters are local file/directory paths. ``source`` is the declared
    MAIN source whose exact byte hash is pinned in ``association_manifest``;
    it is NOT compiled or verified against POU machine code. ``gvt`` supplies
    existing addresses, and ``address_csv`` must independently agree for all
    parsed records. ``link_report`` must be this repository's successful
    two-stage linker report, and the manifest must pin every ordered link input
    plus the source/GVT/CSV/report byte hashes. Both original and frozen link
    inputs and both stage outputs are rehashed. No reference ELF supplies data.

    ``output_dir`` must not exist, and its parent must exist. Inputs are bounded
    regular files without final symlinks, read-only and rechecked for drift.
    Preflight errors raise ValueError/OSError without creating output files.
    After directory creation, ordinary I/O/integrity errors return ``ok=False``
    with a retained failed report; partial files are diagnostic, never reused.
    Success creates stuserprog.o, stprog.o and binding-report.json exclusively,
    makes them read-only, and returns that JSON-compatible report. No originals
    are modified. The only intended ELF mutations are the eight st_value DWORDs
    and st_info bytes. Both success and failure remain non-downloadable, with
    hash-only batch association and source_compilation_verified=False.
    """
    snapshots = {}
    remaining = MAX_TOTAL

    def capture(path, limit=MAX_OBJECT):
        nonlocal remaining
        path = Path(path).absolute()
        if path not in snapshots:
            data = _read(path, min(limit, remaining))
            remaining -= len(data)
            snapshots[path] = data
        elif len(snapshots[path]) > limit:
            raise ValueError("cached input exceeds file-specific limit")
        return snapshots[path]

    paths = {"source": Path(source).absolute(), "gvt": Path(gvt).absolute(),
             "address_csv": Path(address_csv).absolute(), "link_report": Path(link_report).absolute(),
             "association_manifest": Path(association_manifest).absolute()}
    if len({p.resolve() for p in paths.values()}) != len(paths):
        raise ValueError("source, allocation and record paths must be distinct")
    documents = {name: capture(path, MAX_DOCUMENT) for name, path in paths.items()}
    manifest = _json(documents["association_manifest"])
    required = {"schema", "profile", "source_sha256", "gvt_sha256", "address_csv_sha256",
                "link_report_sha256", "link_inputs", "manifest_hash"}
    if set(manifest) != required or manifest["schema"] != "plcidf.global-binding-inputs.v1" or manifest["profile"] != PROFILE:
        raise ValueError("unsupported association manifest schema/fields/profile")
    if _hash_field(manifest["manifest_hash"]) != _sha(_canonical({k: v for k, v in manifest.items() if k != "manifest_hash"})):
        raise ValueError("association manifest integrity check failed")
    for name in ("source", "gvt", "address_csv", "link_report"):
        if _hash_field(manifest[name + "_sha256"]) != _sha(documents[name]):
            raise ValueError("association hash mismatch: " + name)
    bindings = _bindings(documents["gvt"], documents["address_csv"])
    link = _json(documents["link_report"])
    if (link.get("schema") != "plcidf.vendor-relocatable-link.v1" or link.get("ok") is not True
            or link.get("status") != "linked" or link.get("scope") != "two-stage-relocatable-object-link"
            or link.get("downloadable") is not False or link.get("st_compiled") is not False
            or link.get("runtime_verified") is not False):
        raise ValueError("link report is not a successful restricted two-stage link")
    inputs = link.get("inputs")
    declared = manifest["link_inputs"]
    if not isinstance(inputs, list) or not 3 <= len(inputs) <= 258 or not isinstance(declared, list) or len(declared) != len(inputs):
        raise ValueError("manifest must pin every ordered POU/library input")
    roles = [f"pou-{i:03}" for i in range(len(inputs) - 2)] + ["syslib", "stclib"]
    link_paths = set()
    for record, entry, role in zip(inputs, declared, roles):
        if (not isinstance(record, dict) or not isinstance(entry, dict)
                or set(entry) != {"role", "sha256"} or record.get("role") != role or entry["role"] != role):
            raise ValueError("ordered link roles or manifest entries are invalid")
        expected = _hash_field(entry["sha256"])
        if _inspection_hash(record) != expected:
            raise ValueError("declared link input differs from report: " + role)
        for key in ("original", "frozen"):
            path = record.get(key)
            if not isinstance(path, str) or not Path(path).is_absolute():
                raise ValueError("link input paths must be explicit absolute paths")
            resolved = Path(path).resolve()
            if resolved in link_paths:
                raise ValueError("original and frozen link input paths must be distinct")
            link_paths.add(resolved)
            raw = capture(path)
            if _sha(raw) != expected:
                raise ValueError("original/frozen link input hash mismatch: " + role)
            _arm_object(raw)
    outputs = link.get("outputs")
    if not isinstance(outputs, list) or len(outputs) != 2:
        raise ValueError("link report must contain both stage outputs")
    prepared = []
    for record, stage, filename in zip(outputs, (1, 2), ("stuserprog.o", "stprog.o")):
        if not isinstance(record, dict) or type(record.get("stage")) is not int or record["stage"] != stage:
            raise ValueError("link output stage is invalid or reordered")
        path = record.get("path")
        if not isinstance(path, str) or not Path(path).is_absolute() or Path(path).name != filename:
            raise ValueError("linked output has an invalid path or filename")
        raw = capture(path)
        if _sha(raw) != _inspection_hash(record):
            raise ValueError("linked output hash mismatch: " + filename)
        patched, mutation = _patch_object(raw, bindings)
        prepared.append((filename, raw, patched, mutation))

    commands = link.get("commands")
    linker = link.get("linker")
    if not isinstance(commands, list) or len(commands) != 3 or not isinstance(linker, dict) or not isinstance(linker.get("path"), str):
        raise ValueError("link report must retain the version and two successful command records")
    expected_argv = [[linker["path"], "--version"],
                     [linker["path"], "-relocatable", *[r["frozen"] for r in inputs[:-1]], "-o", outputs[0]["path"]],
                     [linker["path"], "-relocatable", outputs[0]["path"], inputs[-1]["frozen"], "-o", outputs[1]["path"]]]
    for command, argv in zip(commands, expected_argv):
        if (not isinstance(command, dict) or command.get("argv") != argv
                or type(command.get("returncode")) is not int or command["returncode"] != 0
                or type(command.get("attempt")) is not int or command["attempt"] != 1
                or command.get("status") in ("timeout", "failed") or command.get("partial_output_untrusted") is True):
            raise ValueError("contradictory, reordered or unsuccessful link commands")

    out = Path(output_dir).absolute()
    if out.exists() or out.is_symlink() or not out.parent.is_dir():
        raise ValueError("output_dir must be new with an existing parent")
    if any(out.resolve() == path.resolve() or out.resolve() in path.resolve().parents for path in snapshots):
        raise ValueError("output directory must not contain an input")

    def recheck():
        for path, raw in snapshots.items():
            if _read(path, len(raw)) != raw:
                raise ValueError("input changed during binding: " + str(path))

    recheck()
    report = {"schema": "plcidf.vendor-global-binding.v1", "profile": PROFILE,
              "ok": False, "status": "failed", "scope": "eight-existing-global-symbol-fields",
              "downloadable": False, "source_compilation_verified": False,
              "runtime_abi_verified": False, "allocator_implemented": False,
              "association_verified": "hash-only-caller-declared-batch",
              "association_manifest_hash": manifest["manifest_hash"],
              "tool_sha256": _sha(_read(Path(__file__), MAX_DOCUMENT)),
              "inputs": {name: {"path": str(path), "sha256": _sha(documents[name])}
                         for name, path in paths.items()},
              "link_inputs": declared, "outputs": [],
              "limitations": ["Source-to-POU compilation is not proven by caller-supplied association hashes.",
                              "Only the eight fixed ordinary INT/REAL globals and existing GVT allocation profile are supported.",
                              "Vendor type-8 symbols remain UND; this is not standard absolute-symbol resolution.",
                              "Relocation filtering, complete runtime/configuration packaging and deployment are not implemented."]}
    out.mkdir()
    try:
        for filename, raw, patched, mutation in prepared:
            target = out / filename
            with target.open("xb") as stream:
                stream.write(patched)
            target.chmod(0o444)
            if _read(target, MAX_OBJECT) != patched:
                raise ValueError("new output changed during binding")
            report["outputs"].append({"path": str(target), "input_sha256": _sha(raw),
                                      "sha256": _sha(patched), "size": len(patched), **mutation})
        recheck()
        for item, (_, _, patched, _) in zip(report["outputs"], prepared):
            if _read(item["path"], MAX_OBJECT) != patched:
                raise ValueError("new output changed before report completion")
        report.update(ok=True, status="existing-symbol-bindings-applied")
    except (OSError, ValueError) as exc:
        report["error"] = str(exc)
    report_path = out / "binding-report.json"
    with report_path.open("xb") as stream:
        stream.write(_canonical(report) + b"\n")
    report_path.chmod(0o444)
    return report


def main(argv=None):
    """Run the local-file CLI; print JSON and return 0 on binding or 2 on refusal.

    Preflight errors create no output directory. JSON status only describes
    local file processing and never authorizes or attempts device operations.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "gvt", "address-csv", "link-report", "association-manifest", "output-dir"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    try:
        report = bind_globals(**vars(args))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report = {"ok": False, "status": "refused", "downloadable": False,
                  "source_compilation_verified": False, "error": str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
