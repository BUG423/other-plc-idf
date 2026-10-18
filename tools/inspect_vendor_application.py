#!/usr/bin/env python3
"""Read-only ELF/application observations, with no vendor tools or device access.

Only standard ELF structures are decoded. Vendor envelopes retain observed
prefixes and arithmetic length coincidences, without assigning field meanings,
validating signatures, or asserting that an application is deployable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_TABLE_ENTRIES = 200_000
MAX_STRING_BYTES = 4096
MACHINES = {3: "x86", 8: "MIPS", 40: "ARM", 62: "x86-64", 183: "AArch64", 243: "RISC-V"}
# Numeric definitions follow the installed standard /usr/include/elf.h.
ARM_RELOCATIONS = {0: "R_ARM_NONE", 2: "R_ARM_ABS32", 3: "R_ARM_REL32",
                   28: "R_ARM_CALL", 29: "R_ARM_JUMP24", 30: "R_ARM_THM_JUMP24",
                   42: "R_ARM_PREL31", 47: "R_ARM_THM_MOVW_ABS_NC", 48: "R_ARM_THM_MOVT_ABS"}
LIMITATIONS = [
    "Inspection is not native ST compilation, complete linking, packaging, signature validation, download, or runtime validation.",
    "Unknown vendor prefixes and length coincidences are observations, not a validated container schema.",
    "ELF OSABI and matching symbol names do not establish the device OS or runtime ABI compatibility.",
]


class FormatError(ValueError):
    """A recognized ELF structure is malformed, truncated, or exceeds limits."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _region(data: bytes, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise FormatError(f"{label} extends outside file")
    return data[offset:offset + size]


def _string(table: bytes, offset: int, label: str) -> str:
    if not 0 <= offset < len(table):
        raise FormatError(f"{label} string offset is outside its string table")
    end = table.find(b"\x00", offset, offset + MAX_STRING_BYTES + 1)
    if end < 0:
        raise FormatError(f"{label} string has no terminating NUL within inspection limit")
    return table[offset:end].decode("utf-8", errors="replace")


def parse_elf(data: bytes) -> dict:
    """Parse bounded ELF32/ELF64 headers, sections, symbols and REL/RELA tables."""
    if len(data) < 16 or data[:4] != b"\x7fELF":
        raise FormatError("truncated or invalid ELF identification")
    bits = {1: 32, 2: 64}.get(data[4])
    endian = {1: "<", 2: ">"}.get(data[5])
    if bits is None or endian is None or data[6] != 1:
        raise FormatError("unsupported ELF class, byte order, or identification version")
    header_format = endian + ("HHIIIIIHHHHHH" if bits == 32 else "HHIQQQIHHHHHH")
    header_size = 16 + struct.calcsize(header_format)
    fields = struct.unpack(header_format, _region(data, 16, header_size - 16, "ELF header"))
    (kind, machine, version, entry, phoff, shoff, flags, ehsize,
     phentsize, phnum, shentsize, shnum, shstrndx) = fields
    if version != 1 or ehsize < header_size or ehsize > len(data):
        raise FormatError("invalid ELF version or header size")
    section_format = endian + ("IIIIIIIIII" if bits == 32 else "IIQQQQIIQQ")
    section_size = struct.calcsize(section_format)

    def section_at(index):
        values = struct.unpack(section_format, _region(data, shoff + index * shentsize,
                                                        section_size, "section header"))
        return dict(zip(("name_offset", "type", "flags", "address", "offset", "size",
                         "link", "info", "alignment", "entry_size"), values))

    if shoff:
        if shentsize < section_size:
            raise FormatError("section header entry is too small")
        zero = section_at(0)
        if shnum == 0:
            shnum = zero["size"]
        if shstrndx == 0xffff:
            shstrndx = zero["link"]
        if phnum == 0xffff:
            phnum = zero["info"]
    elif shnum or shstrndx:
        raise FormatError("section count/name index exists without a section table")
    if shnum > MAX_TABLE_ENTRIES or phnum > MAX_TABLE_ENTRIES:
        raise FormatError("ELF header table exceeds inspection limits")
    _region(data, shoff, shnum * shentsize, "section header table")
    if phnum:
        if phentsize < (32 if bits == 32 else 56):
            raise FormatError("program header entry is too small")
        _region(data, phoff, phnum * phentsize, "program header table")
    sections = [section_at(index) for index in range(shnum)]
    for section in sections:
        # NULL section fields may encode extended counts; NOBITS has no bytes.
        if section["type"] not in {0, 8}:
            _region(data, section["offset"], section["size"], "section contents")
    if shstrndx >= len(sections) and shstrndx != 0:
        raise FormatError("section-name table index is invalid")
    name_table = b""
    if shstrndx:
        named = sections[shstrndx]
        if named["type"] != 3:
            raise FormatError("section-name table is not STRTAB")
        name_table = _region(data, named["offset"], named["size"], "section-name table")
    for index, section in enumerate(sections):
        section["index"] = index
        section["name"] = _string(name_table, section["name_offset"], "section name") if name_table else ""

    symbol_tables = {}
    undefined, defined = [], []
    total_symbols = 0
    for index, section in enumerate(sections):
        if section["type"] not in {2, 11}:  # SYMTAB / DYNSYM
            continue
        sym_format = endian + ("IIIBBH" if bits == 32 else "IBBHQQ")
        item_size = struct.calcsize(sym_format)
        stride = section["entry_size"]
        if stride < item_size or section["size"] % stride:
            raise FormatError("invalid symbol-table entry size")
        count = section["size"] // stride
        total_symbols += count
        if total_symbols > MAX_TABLE_ENTRIES:
            raise FormatError("symbol table exceeds inspection limits")
        if section["link"] >= len(sections) or sections[section["link"]]["type"] != 3:
            raise FormatError("symbol-table string-table link is invalid")
        strings = sections[section["link"]]
        table = _region(data, strings["offset"], strings["size"], "symbol string table")
        symbols = []
        for number in range(count):
            values = struct.unpack(sym_format, _region(data, section["offset"] + number * stride,
                                                        item_size, "symbol entry"))
            if bits == 32:
                name, value, size, info, other, ndx = values
            else:
                name, info, other, ndx, value, size = values
            if ndx == 0xffff:
                raise FormatError("extended symbol section indices are not supported")
            if 0 < ndx < 0xff00 and ndx >= len(sections):
                raise FormatError("symbol refers to a nonexistent section")
            symbol = {"name": _string(table, name, "symbol name"), "binding": info >> 4,
                      "section_index": ndx}
            symbols.append(symbol)
            if symbol["name"] and symbol["binding"] in {1, 2}:
                (undefined if ndx == 0 else defined).append(symbol["name"])
        symbol_tables[index] = symbols

    relocation_counts = Counter()
    relocation_sections = []
    referenced_undefined = set()
    for section in sections:
        if section["type"] not in {4, 9}:  # RELA / REL
            continue
        has_addend = section["type"] == 4
        rel_format = endian + (("IIi" if has_addend else "II") if bits == 32
                               else ("QQq" if has_addend else "QQ"))
        item_size = struct.calcsize(rel_format)
        stride = section["entry_size"]
        if stride < item_size or section["size"] % stride:
            raise FormatError("invalid relocation-table entry size")
        count = section["size"] // stride
        if sum(relocation_counts.values()) + count > MAX_TABLE_ENTRIES:
            raise FormatError("relocation table exceeds inspection limits")
        if section["link"] not in symbol_tables or section["info"] >= len(sections):
            raise FormatError("relocation symbol-table or target-section link is invalid")
        symbols = symbol_tables[section["link"]]
        for number in range(count):
            values = struct.unpack(rel_format, _region(data, section["offset"] + number * stride,
                                                        item_size, "relocation entry"))
            r_info = values[1]
            symbol_index = r_info >> (8 if bits == 32 else 32)
            r_type = r_info & (0xff if bits == 32 else 0xffffffff)
            if symbol_index >= len(symbols):
                raise FormatError("relocation references a nonexistent symbol")
            label = ARM_RELOCATIONS.get(r_type, f"ARM_TYPE_{r_type}") if machine == 40 else f"TYPE_{r_type}"
            relocation_counts[label] += 1
            symbol = symbols[symbol_index]
            if symbol["section_index"] == 0 and symbol["name"] and symbol["binding"] in {1, 2}:
                referenced_undefined.add(symbol["name"])
        relocation_sections.append({"name": section["name"], "entries": count,
                                    "target_section": sections[section["info"]]["name"],
                                    "explicit_addends": has_addend})
    attributes = []
    for section in sections:
        if section["name"] == ".ARM.attributes":
            raw = _region(data, section["offset"], section["size"], "ARM attributes")
            attributes.append({"size": len(raw), "sha256": _sha(raw), "decoded": False})
    return {
        "class_bits": bits, "endianness": "little" if endian == "<" else "big",
        "machine_id": machine, "machine": MACHINES.get(machine, "unknown"),
        "type_id": kind, "type": {1: "REL", 2: "EXEC", 3: "DYN", 4: "CORE"}.get(kind, "unknown"),
        "osabi_id": data[7], "abi_version": data[8], "flags_hex": f"0x{flags:x}",
        "arm_eabi_version": (flags >> 24) & 0xff if machine == 40 else None,
        "entry_address": entry, "program_header_count": phnum, "section_count": shnum,
        "sections": [{key: section[key] for key in ("index", "name", "type", "offset", "size", "flags")}
                     for section in sections],
        "arm_attribute_sections": attributes,
        "undefined_global_weak": {"entries": len(undefined), "unique_count": len(set(undefined)),
                                   "names": sorted(set(undefined))},
        "defined_global_weak": {"entries": len(defined), "unique_count": len(set(defined)),
                                 "names": sorted(set(defined))},
        "relocations": {"entries": sum(relocation_counts.values()),
                        "types": dict(sorted(relocation_counts.items())),
                        "sections": relocation_sections,
                        "referenced_undefined_names": sorted(referenced_undefined)},
    }


def _observations(data: bytes) -> dict:
    # A bounded prefix is deliberately smaller than the file; never emit a
    # complete small vendor binary as a hexadecimal "header".
    prefix_size = min(16, len(data) // 2)
    observations = {"prefix_hex": data[:prefix_size].hex(), "prefix_bytes": prefix_size,
                    "embedded_elf_magic_offsets": [], "length_equalities": []}
    cursor = 0
    while len(observations["embedded_elf_magic_offsets"]) < 16:
        position = data.find(b"\x7fELF", cursor)
        if position < 0:
            break
        observations["embedded_elf_magic_offsets"].append(position)
        cursor = position + 4
    for offset in range(0, min(32, len(data) - 3), 4):
        for order in ("little", "big"):
            value = int.from_bytes(data[offset:offset + 4], order)
            for relationship, expected in (("equals_file_size", len(data)),
                                             ("equals_bytes_after_word", len(data) - offset - 4)):
                if value == expected:
                    observations["length_equalities"].append({"offset": offset,
                        "width": 4, "endianness": order, "value": value,
                        "arithmetic_relationship": relationship,
                        "field_semantics_confirmed": False})
    return observations


def inspect_bytes(data: bytes) -> dict:
    """Inspect bytes already supplied by a caller; never execute any input."""
    result = {"inspection_status": "observed", "size": len(data), "sha256": _sha(data),
              "format": "unknown", "observations": _observations(data)}
    if data.startswith(b"\x7fELF") or (data and b"\x7fELF".startswith(data)):
        result["format"] = "elf"
        try:
            result["elf"] = parse_elf(data)
            result["inspection_status"] = "parsed"
        except FormatError as exc:
            result.update(inspection_status="error", error=str(exc))
    elif data.startswith(b"****AS*****\x00"):
        result["format"] = "observed-autoshop-AS-prefix"
    elif data.startswith(b"AutoShop\x00"):
        result["format"] = "observed-AutoShop-prefix"
    elif data.startswith(b"SA\x00\x01"):
        result["format"] = "observed-SA-prefix"
    elif data in {b"Success!\r\n", b"Success!\n"}:
        result.update(format="compiler-success-marker", marker="Success!",
                      marker_does_not_prove_build=True)
    return result


def _read(path: Path, max_file_bytes: int) -> tuple[dict, bytes | None]:
    record = {"path": str(path.resolve())}
    try:
        if not path.is_file():
            raise OSError("input is not a regular file")
        before = path.stat()
        if before.st_size > max_file_bytes:
            raise OSError(f"input exceeds {max_file_bytes}-byte inspection limit")
        with path.open("rb") as fh:
            data = fh.read(max_file_bytes + 1)
        after = path.stat()
        if len(data) > max_file_bytes or (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns):
            raise OSError("input changed during inspection or exceeded the limit")
        record.update(inspect_bytes(data))
        return record, data
    except OSError as exc:
        record.update(inspection_status="error", format="unreadable", error=str(exc))
        return record, None


def compare_records(left: dict, right: dict, left_data: bytes, right_data: bytes) -> dict:
    overlap = min(len(left_data), len(right_data))
    first_difference = next((offset for offset in range(overlap)
                             if left_data[offset] != right_data[offset]),
                            overlap if len(left_data) != len(right_data) else None)
    changed = sum(a != b for a, b in zip(left_data, right_data))
    result = {"base": left["path"], "candidate": right["path"],
              "same_sha256": left["sha256"] == right["sha256"],
              "size_delta": len(right_data) - len(left_data),
              "changed_overlap_bytes": changed, "first_difference_offset": first_difference,
              "base_format": left["format"], "candidate_format": right["format"]}
    if "elf" in left and "elf" in right:
        a, b = left["elf"], right["elf"]
        result["elf"] = {"same_machine_class_endianness": all(a[key] == b[key] for key in (
            "machine_id", "class_bits", "endianness")), "same_flags": a["flags_hex"] == b["flags_hex"],
            "relocation_entry_delta": b["relocations"]["entries"] - a["relocations"]["entries"]}
        for category in ("undefined_global_weak", "defined_global_weak"):
            old, new = set(a[category]["names"]), set(b[category]["names"])
            result["elf"][category] = {"removed_names": sorted(old - new), "added_names": sorted(new - old)}
    return result


def inspect_paths(paths, *, comparisons=(), max_file_bytes=MAX_FILE_BYTES) -> dict:
    """Inspect many files and optional (base, candidate) pairs in one report."""
    if type(max_file_bytes) is not int or not 0 < max_file_bytes <= MAX_TOTAL_BYTES:
        raise ValueError("max_file_bytes must be a positive integer at most MAX_TOTAL_BYTES")
    pairs = [(str(Path(a).resolve()), str(Path(b).resolve())) for a, b in comparisons]
    requested = list(dict.fromkeys([str(Path(path).resolve()) for path in paths]
                                  + [path for pair in pairs for path in pair]))
    if not requested or len(requested) > 64:
        raise ValueError("supply between 1 and 64 distinct inputs")
    records, contents = {}, {}
    total = 0
    for raw in requested:
        if total >= MAX_TOTAL_BYTES:
            records[raw] = {"path": raw, "inspection_status": "error", "format": "unreadable",
                            "error": "total inspection byte limit reached"}
            continue
        record, data = _read(Path(raw), min(max_file_bytes, MAX_TOTAL_BYTES - total))
        records[raw] = record
        if data is not None:
            contents[raw] = data
            total += len(data)
    same_hash = defaultdict(list)
    stems = defaultdict(list)
    dependencies = []
    for path, record in records.items():
        if "sha256" in record:
            same_hash[record["sha256"]].append(path)
        stems[Path(path).stem.lower()].append(path)
    elf_records = [record for record in records.values() if "elf" in record]
    for consumer in elf_records:
        wanted = set(consumer["elf"]["undefined_global_weak"]["names"])
        for provider in elf_records:
            if consumer is provider:
                continue
            matched = wanted & set(provider["elf"]["defined_global_weak"]["names"])
            if matched:
                dependencies.append({"consumer": consumer["path"], "provider": provider["path"],
                    "matched_names": sorted(matched), "abi_compatibility_confirmed": False})
    diffs = []
    for left, right in pairs:
        if left in contents and right in contents:
            diffs.append(compare_records(records[left], records[right], contents[left], contents[right]))
        else:
            diffs.append({"base": left, "candidate": right, "error": "one or both inputs could not be read"})
    return {"schema_version": SCHEMA_VERSION, "tool": "inspect_vendor_application",
            "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "inspection_ok": all(record["inspection_status"] != "error" for record in records.values()),
            "limits": {"max_file_bytes": max_file_bytes, "max_total_bytes": MAX_TOTAL_BYTES,
                       "max_table_entries": MAX_TABLE_ENTRIES},
            "files": list(records.values()), "comparisons": diffs,
            "associations": {
                "identical_content": [{"sha256": key, "paths": values} for key, values in same_hash.items()
                                      if len(values) > 1],
                "same_basename_stem_only": [{"stem": key, "paths": values} for key, values in stems.items()
                                             if len(values) > 1],
                "symbol_name_intersections": dependencies,
            }, "limitations": LIMITATIONS}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--compare", nargs=2, action="append", default=[], metavar=("BASE", "CANDIDATE"))
    parser.add_argument("--output", type=Path, help="write JSON report; input paths can never be overwritten")
    parser.add_argument("--max-file-bytes", type=int, default=MAX_FILE_BYTES)
    args = parser.parse_args(argv)
    try:
        inputs = args.paths + [Path(path) for pair in args.compare for path in pair]
        if args.output is not None and args.output.resolve() in {path.resolve() for path in inputs}:
            raise ValueError("report output cannot overwrite an input")
        report = inspect_paths(args.paths, comparisons=args.compare, max_file_bytes=args.max_file_bytes)
        report["tool_source_sha256"] = _sha(Path(__file__).read_bytes())
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output is None:
            sys.stdout.write(encoded)
        else:
            args.output.write_text(encoded, encoding="utf-8")
        return 0 if report["inspection_ok"] else 1
    except (OSError, ValueError) as exc:
        print(f"inspection failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
