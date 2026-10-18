#!/usr/bin/env python3
"""Read existing AutoShop global-variable allocations without a vendor process.

This bounded reader supports ONE observed scalar GVT serialization profile, not
arbitrary AutoShop archives or a variable allocator. Names, declared scalar type
and the already assigned address are parsed in record order from the GVT itself.
An optional independent VarNameAndAddr.csv cross-check is never a source of the
parsed values. ELF/reference-symbol files are deliberately not accepted inputs.

The SInfo type 8 and ASCII uppercase-name candidate come from the static/current
profile evidence in firmware report 17, not from the scalar data-type code.
Nothing patches ELF, creates a project, compiles, connects or transfers data.
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

MAX_BYTES = 1024 * 1024
MAX_VARIABLES = 256
# This preamble includes the observed 6.0 archive/LVT class-tree profile, but not
# the variable count or any variable records. Pinning it avoids treating an
# arbitrary MFC archive with coincidental strings as a validated GVT layout.
PREAMBLE_BYTES = 0xd9
PREAMBLE_SHA256 = "551997c6cc365daf1275ee90349f48cfc2c5f2d05225732e0877fb435bfeb03f"
SCALAR_TYPES = {1: ("BOOL", 1), 4: ("INT", 16), 32: ("REAL", 32)}
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
LIMITATIONS = [
    "Parses existing assigned scalar globals in one pinned GVT serialization profile; it does not allocate addresses for new variables.",
    "Address values are opaque vendor symbol addresses, not proven byte addresses, direct I/O addresses or Modbus registers.",
    "SInfo type 8 is the observed ordinary-global symbol category; it is not the BOOL/INT/REAL scalar code.",
    "ASCII uppercase compiler names are candidates for the observed ordinary-global profile; Unicode, aliases, namespaces, arrays and instances are unsupported.",
    "No ELF patch, complete application build, download, runtime ABI or device execution is verified.",
]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path):
    path = Path(path).absolute()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
            raise ValueError("input must be an ordinary file of at most 1 MiB")
        data = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    if (len(data) > MAX_BYTES or (before.st_ino, before.st_size, before.st_mtime_ns,
                                before.st_ctime_ns) != (after.st_ino, after.st_size,
                                                       after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError("input changed while reading")
    return data


def parse_gvt(data: bytes) -> dict:
    """Parse one complete supported GVT, or raise ValueError without scanning.

    The count at 0xd9 is followed immediately by records; all field lengths,
    offsets, flags, tail fields and EOF must agree. ASCII short CArchive strings
    only are supported. Unknown headers/types/string encodings fail closed.
    Returns records with byte offsets and already assigned addresses. No state or
    filesystem is modified, and no reference symbol table is read.
    """
    if len(data) > MAX_BYTES or len(data) < PREAMBLE_BYTES + 4:
        raise ValueError("GVT size is outside supported bounds")
    if _sha(data[:PREAMBLE_BYTES]) != PREAMBLE_SHA256:
        raise ValueError("unsupported GVT preamble/version/class-tree profile")
    position = PREAMBLE_BYTES

    def take(size):
        nonlocal position
        if size < 0 or position + size > len(data):
            raise ValueError("truncated GVT record")
        start = position
        position += size
        return data[start:position]

    def u32():
        return struct.unpack("<I", take(4))[0]

    def short_string():
        nonlocal position
        prefix_offset = position
        count = take(1)[0]
        if count == 255:
            raise ValueError("extended/Unicode GVT strings are unsupported here")
        raw = take(count)
        if any(value < 32 or value > 126 for value in raw):
            raise ValueError("only printable ASCII short GVT strings are supported")
        return raw.decode("ascii"), prefix_offset

    count = u32()
    if not 1 <= count <= MAX_VARIABLES:
        raise ValueError("GVT variable count must be 1..256")
    records, seen, addresses = [], set(), set()
    for index in range(count):
        start = position
        kind, repeated_kind = u32(), u32()
        if kind not in SCALAR_TYPES or repeated_kind != kind:
            raise ValueError("unsupported or inconsistent scalar GVT type codes")
        name, name_offset = short_string()
        if not IDENTIFIER.fullmatch(name) or name.casefold() in seen:
            raise ValueError("invalid or case-insensitive duplicate global name")
        seen.add(name.casefold())
        initial, _ = short_string()
        # The current ordinary scalar records contain four flag bytes 01 01 00
        # 00. Their general meaning is not inferred; any other profile is refused.
        flags_offset = position
        if take(4) != b"\x01\x01\0\0":
            raise ValueError("unsupported scalar GVT flags")
        address_offset = position
        address = u32()
        if not 0x02100000 <= address < 0x02110000 or address in addresses:
            raise ValueError("unassigned/out-of-profile or duplicate scalar address")
        addresses.add(address)
        comment, _ = short_string()
        tail_offset = position
        # An empty Unicode CString marker then two zero DWORDs, observed after
        # every record. Require the exact tail rather than searching for a name.
        if take(12) != b"\xff\xfe\xff\0" + bytes(8):
            raise ValueError("unsupported GVT record tail")
        type_name, bits = SCALAR_TYPES[kind]
        records.append({"index": index, "name": name, "type": type_name,
                        "type_code": kind, "width_bits": bits, "initial_text": initial,
                        "comment": comment, "address": address, "address_hex": hex(address),
                        "record_offset": start, "name_length_offset": name_offset,
                        "flags_offset": flags_offset, "address_offset": address_offset,
                        "tail_offset": tail_offset, "record_end_offset": position,
                        "compiler_symbol_candidate": name.upper(),
                        "sinfo_type_candidate": 8})
    if position != len(data):
        raise ValueError("trailing bytes or inconsistent GVT count")
    return {"profile": "autoshop-gvt-6.0-observed-scalar-v1", "count": count,
            "preamble_bytes": PREAMBLE_BYTES, "preamble_sha256": PREAMBLE_SHA256,
            "count_offset": PREAMBLE_BYTES, "records": records}


def parse_address_csv(data: bytes) -> dict:
    """Parse the independent headerless UTF-16 address export.

    This table cross-checks a parsed GVT; it must not fill missing GVT fields.
    Duplicate names, invalid hexadecimal addresses and invalid widths are refused.
    """
    if len(data) > MAX_BYTES or not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ValueError("address CSV must be bounded UTF-16 with a BOM")
    text = data.decode("utf-16")
    rows = {}
    for number, line in enumerate(text.splitlines(), 1):
        if number > 4096:
            raise ValueError("too many address CSV rows")
        cells = line.split("\t")
        # Real system-variable descriptions contain unescaped tabs; name/address
        # are the first two cells and width is the final cell, not always cell 4.
        if not 4 <= len(cells) <= 16 or not cells[0] or cells[0].casefold() in rows:
            raise ValueError("invalid/duplicate address CSV row")
        if not re.fullmatch(r"0[xX][0-9A-Fa-f]{1,8}", cells[1]) or not re.fullmatch(r"[0-9]{1,10}", cells[-1]):
            raise ValueError("invalid address CSV address/width")
        width = int(cells[-1])
        if not 1 <= width <= 0xffffffff:
            raise ValueError("invalid address CSV width")
        rows[cells[0].casefold()] = {"name": cells[0], "address": int(cells[1], 16),
                                      "width_bits": width, "line": number}
    return rows


def inspect_variables(gvt_path, *, address_csv=None, required_names=None) -> dict:
    """Read and optionally cross-check existing records, returning JSON metadata.

    ``required_names`` selects existing ASCII globals by case-insensitive name;
    missing or duplicate selections are errors. An optional CSV must agree on
    both the assigned address and bit width for every GVT record. ``ok`` means
    these local records parsed/agree, never allocation or application verification.
    Only the specified files are read; this function creates no files/processes.
    """
    result = {"schema": "plcidf.vendor-existing-variables.v1", "ok": False,
              "status": "refused", "scope": "existing-project-allocation-inspection",
              "allocator_implemented": False, "elf_patched": False, "downloadable": False,
              "limitations": LIMITATIONS.copy()}
    try:
        data = _read(gvt_path)
        parsed = parse_gvt(data)
        result.update(gvt={"path": str(Path(gvt_path).absolute()), "size": len(data),
                           "sha256": _sha(data)}, **parsed)
        records = {row["name"].casefold(): row for row in parsed["records"]}
        if required_names is not None:
            names = list(required_names)
            if (not names or len(names) > MAX_VARIABLES or any(not isinstance(n, str)
                    or not IDENTIFIER.fullmatch(n) for n in names)
                    or len({n.casefold() for n in names}) != len(names)):
                raise ValueError("required names must be distinct supported identifiers")
            if any(name.casefold() not in records for name in names):
                raise ValueError("required global is absent from GVT")
            result["selected_records"] = [records[name.casefold()] for name in names]
        if address_csv is not None:
            csv_data = _read(address_csv)
            exported = parse_address_csv(csv_data)
            checks = []
            for row in parsed["records"]:
                candidate = exported.get(row["name"].casefold())
                if not candidate or candidate["address"] != row["address"] or candidate["width_bits"] != row["width_bits"]:
                    raise ValueError("independent address CSV disagrees with GVT: " + row["name"])
                checks.append({"name": row["name"], "csv_line": candidate["line"], "matches": True})
            result["csv_cross_check"] = {"path": str(Path(address_csv).absolute()),
                                         "sha256": _sha(csv_data), "checks": checks}
            if _read(address_csv) != csv_data:
                raise ValueError("address CSV changed during inspection")
        if _read(gvt_path) != data:
            raise ValueError("GVT changed during inspection")
        result.update(ok=True, status="existing-records-inspected")
    except (OSError, ValueError, UnicodeError, TypeError) as exc:
        result["error"] = str(exc)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gvt", required=True)
    parser.add_argument("--address-csv", help="independent cross-check; not an address input")
    parser.add_argument("--name", action="append", help="select existing global; may repeat")
    args = parser.parse_args(argv)
    result = inspect_variables(args.gvt, address_csv=args.address_csv, required_names=args.name)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
