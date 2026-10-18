#!/usr/bin/env python3
"""Bounded, read-only firmware structure inventory. Never loads or executes input.

Recognized structures describe a file's declared target, not measured hardware.
This intentionally does not unpack archives, decrypt, recover keys or flash PLCs.
"""

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import zlib


DEFAULT_MAX_BYTES = 64 * 1024 * 1024
HARD_MAX_BYTES = 256 * 1024 * 1024
MAX_CANDIDATES = 4096
MAX_DTB_BYTES = 16 * 1024 * 1024
MAX_DTB_TOKENS = 100000
MAX_VALIDATION_BYTES = 256 * 1024 * 1024
MAX_VALIDATION_ENTRIES = 100000
MACHINES = {3: "x86", 8: "MIPS", 20: "PowerPC", 21: "PowerPC64",
            40: "ARM", 62: "x86-64", 183: "AArch64", 243: "RISC-V"}
UIMAGE_ARCH = {2: "ARM", 3: "x86", 5: "MIPS", 7: "PowerPC",
               22: "AArch64", 24: "x86-64", 26: "RISC-V"}
TEXT_MARKERS = (b"Linux version ", b"U-Boot ", b"sunxi", b"Allwinner",
                b"rockchip", b"Cortex-A", b"STM32")
ARCHIVE_MAGICS = ((b"7z\xbc\xaf\x27\x1c", "7z"),
                  (b"\xfd7zXZ\x00", "xz"), (b"\x1f\x8b\x08", "gzip"),
                  (b"PK\x03\x04", "zip"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ValidationLimit(Exception):
    pass


def charge(budget, byte_count=0, entries=0):
    if budget is not None:
        if byte_count > budget["bytes"] or entries > budget["entries"]:
            raise ValidationLimit()
        budget["bytes"] -= byte_count
        budget["entries"] -= entries


def finding(data, offset, length, kind, grade, details, declared_length=None):
    """length/hash always cover observed bytes; declared length is separate."""
    item = {"kind": kind, "offset": offset, "length": length,
            "sha256": digest(memoryview(data)[offset:offset + length]),
            "evidence_grade": grade, "scope": "offline_file_only", "details": details}
    if declared_length is not None:
        item["declared_length"] = declared_length
        item["complete_in_inspected_region"] = length == declared_length
    return item


def parse_elf(data, offset, budget=None):
    remaining = len(data) - offset
    if remaining < 16:
        return None, "elf_truncated_ident"
    ident = data[offset:offset + 16]
    if ident[4] not in (1, 2) or ident[5] not in (1, 2) or ident[6] != 1:
        return None, "elf_invalid_ident"
    bits = 32 if ident[4] == 1 else 64
    endian = "<" if ident[5] == 1 else ">"
    header_size = 52 if bits == 32 else 64
    if remaining < header_size:
        return None, "elf_truncated_header"
    fields = struct.unpack_from(endian + ("HHIIIIIHHHHHH" if bits == 32 else "HHIQQQIHHHHHH"),
                                data, offset + 16)
    etype, machine, version, entry, phoff, shoff, flags, ehsize, phsize, phnum, shsize, shnum, shstr = fields
    expected_ph, expected_sh = (32, 40) if bits == 32 else (56, 64)
    if (etype not in (1, 2, 3, 4) or not machine or version != 1 or ehsize != header_size
            or phnum > 4096 or shnum > 4096
            or (phnum and (phsize != expected_ph or phoff < ehsize))
            or (shnum and (shsize != expected_sh or shoff < ehsize))
            or (not phnum and phoff) or (not shnum and shoff)
            or (shnum and shstr >= shnum and shstr != 0xffff)):
        return None, "elf_invalid_or_unsupported_tables"
    extent = max(ehsize, phoff + phsize * phnum, shoff + shsize * shnum)
    details = {"bits": bits, "byte_order": "little" if endian == "<" else "big",
               "machine_id": machine, "machine": MACHINES.get(machine, "unknown"),
               "elf_type": etype, "osabi": ident[7], "flags": flags,
               "hardware_identity_verified": False,
               "note": "ELF ABI and ISA describe this object; System V OSABI does not establish Linux."}
    if extent > remaining:
        return finding(data, offset, min(remaining, ehsize), "elf", "header_only_truncated",
                       details, extent), None
    for index in range(phnum):
        charge(budget, entries=1)
        values = struct.unpack_from(endian + ("IIIIIIII" if bits == 32 else "IIQQQQQQ"),
                                    data, offset + phoff + index * phsize)
        ptype, poff, pfilesz, pmemsz = ((values[0], values[1], values[4], values[5])
                                       if bits == 32 else (values[0], values[2], values[5], values[6]))
        if ptype == 1 and pfilesz > pmemsz:
            return None, "elf_invalid_load_segment"
        if pfilesz:
            extent = max(extent, poff + pfilesz)
    for index in range(shnum):
        charge(budget, entries=1)
        values = struct.unpack_from(endian + ("IIIIIIIIII" if bits == 32 else "IIQQQQIIQQ"),
                                    data, offset + shoff + index * shsize)
        if values[1] != 8 and values[5]:  # SHT_NOBITS has no file payload.
            extent = max(extent, values[4] + values[5])
    if extent > remaining:
        return finding(data, offset, ehsize, "elf", "header_only_truncated", details, extent), None
    charge(budget, byte_count=extent)
    return finding(data, offset, extent, "elf", "validated_structure", details, extent), None


def parse_uimage(data, offset, budget=None):
    if len(data) - offset < 64:
        return None, "uimage_truncated_header"
    fields = struct.unpack_from(">7I4B32s", data, offset)
    _, header_crc, timestamp, size, load, entry, payload_crc, os_id, arch, image_type, compression, name = fields
    header = bytearray(data[offset:offset + 64])
    header[4:8] = b"\0" * 4
    if zlib.crc32(header) & 0xffffffff != header_crc:
        return None, "uimage_invalid_header_crc"
    total = 64 + size
    observed = min(total, len(data) - offset)
    charge(budget, byte_count=observed)
    complete = observed == total
    payload_ok = ((zlib.crc32(memoryview(data)[offset + 64:offset + total]) & 0xffffffff) == payload_crc
                  if complete else None)
    details = {"name": name.split(b"\0", 1)[0].decode("ascii", errors="replace"),
               "image_os_id": os_id, "image_os": "Linux" if os_id == 5 else "unknown",
               "image_arch_id": arch, "image_arch": UIMAGE_ARCH.get(arch, "unknown"),
               "image_type": image_type, "compression_id": compression,
               "header_crc32_verified": True, "payload_crc32_verified": payload_ok,
               "note": "CRC checks integrity, not authenticity, bootability or physical board identity."}
    grade = ("validated_structure_and_crc" if payload_ok else
             "header_valid_payload_corrupt" if complete else "header_only_truncated")
    return finding(data, offset, observed, "uimage", grade, details, total), None


def _cstring(data, start, end, limit=256):
    stop = data.find(b"\0", start, min(end, start + limit))
    if stop < 0:
        raise ValueError("unterminated_string")
    return data[start:stop].decode("ascii"), stop + 1


def _dtb_properties(blob, struct_off, struct_size, strings_off, strings_size, budget=None):
    """Validate v17 token nesting and extract bounded declared target properties."""
    cursor, end = struct_off, struct_off + struct_size
    stack, properties, root_seen = [], [], False
    children_started = []
    for _ in range(MAX_DTB_TOKENS):
        charge(budget, entries=1)
        if cursor + 4 > end:
            raise ValueError("missing_end_token")
        token = struct.unpack_from(">I", blob, cursor)[0]
        cursor += 4
        if token == 1:
            if not stack and root_seen:
                raise ValueError("multiple_roots")
            name, cursor = _cstring(blob, cursor, end)
            if not root_seen and name:
                raise ValueError("nonempty_root_name")
            if children_started:
                children_started[-1] = True
            stack.append(name)
            children_started.append(False)
            if len(stack) > 128:
                raise ValueError("depth_limit")
            root_seen = True
            cursor = (cursor + 3) & ~3
        elif token == 2:
            if not stack:
                raise ValueError("unbalanced_nodes")
            stack.pop()
            children_started.pop()
        elif token == 3:
            if not stack or children_started[-1] or cursor + 8 > end:
                raise ValueError("invalid_property_location")
            length, nameoff = struct.unpack_from(">II", blob, cursor)
            cursor += 8
            if nameoff >= strings_size or cursor + length > end:
                raise ValueError("property_out_of_bounds")
            name, _ = _cstring(blob, strings_off + nameoff, strings_off + strings_size)
            if name in ("compatible", "model") and length <= 4096 and len(properties) < 64:
                raw = blob[cursor:cursor + length]
                if raw.endswith(b"\0"):
                    try:
                        values = [value.decode("ascii") for value in raw[:-1].split(b"\0")]
                        if all(values) and all(all(32 <= ord(c) <= 126 for c in value) for value in values):
                            properties.append({"node": "/" + "/".join(stack[1:]),
                                               "property": name, "values": values,
                                               "relative_value_offset": cursor, "length": length})
                    except UnicodeDecodeError:
                        pass
            cursor = (cursor + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            if stack or not root_seen or cursor != end:
                raise ValueError("invalid_end_token")
            return properties
        else:
            raise ValueError("unknown_token")
    raise ValueError("token_limit")


def parse_dtb(data, offset, budget=None):
    if len(data) - offset < 40:
        return None, "dtb_truncated_header"
    _, total, so, stro, reserve, version, compat, boot_cpu, strsize, ssize = struct.unpack_from(">10I", data, offset)
    if (version != 17 or compat != 16 or total < 56 or total > MAX_DTB_BYTES
            or so % 4 or reserve % 8 or reserve < 40 or so < reserve + 16
            or ssize < 16 or so + ssize > total or stro < so + ssize or stro + strsize > total):
        return None, "dtb_invalid_or_unsupported_layout"
    details = {"version": version, "last_compatible_version": compat,
               "boot_cpu_id": boot_cpu, "declared_target_properties": [],
               "note": "A DTB describes an image configuration; it does not identify the connected board."}
    if total > len(data) - offset:
        return finding(data, offset, 40, "dtb", "header_only_truncated", details, total), None
    charge(budget, byte_count=total)
    blob = memoryview(data)[offset:offset + total]
    reserve_end = None
    for pos in range(reserve, min(so, reserve + 16 * 1024) - 15, 16):
        if struct.unpack_from(">QQ", blob, pos) == (0, 0):
            reserve_end = pos + 16
            break
    if reserve_end is None:
        return None, "dtb_missing_or_oversize_reservation_terminator"
    # bytes.find is used for bounded strings; the maximum allocation is 16 MiB.
    try:
        details["declared_target_properties"] = _dtb_properties(
            bytes(blob), so, ssize, stro, strsize, budget)
    except (ValueError, UnicodeDecodeError, struct.error) as error:
        return None, "dtb_invalid_structure:" + str(error)
    return finding(data, offset, total, "dtb", "validated_structure", details, total), None


def inspect_bytes(data, *, max_findings=256):
    if not isinstance(data, bytes) or len(data) > HARD_MAX_BYTES:
        raise ValueError("data must be bytes within HARD_MAX_BYTES")
    if not 1 <= max_findings <= 1024:
        raise ValueError("max_findings must be between 1 and 1024")
    findings, rejected, candidates, limited = [], collections.Counter(), 0, False
    initial_byte_budget = min(MAX_VALIDATION_BYTES, max(1024 * 1024, len(data) * 4))
    budget = {"bytes": initial_byte_budget, "entries": MAX_VALIDATION_ENTRIES}
    parsers = ((b"\x7fELF", parse_elf), (b"\x27\x05\x19\x56", parse_uimage),
               (b"\xd0\x0d\xfe\xed", parse_dtb))
    for magic, parser in parsers:
        if candidates >= MAX_CANDIDATES or not budget["bytes"] or not budget["entries"]:
            limited = True
            break
        cursor = 0
        while True:
            offset = data.find(magic, cursor)
            if offset < 0:
                break
            if candidates >= MAX_CANDIDATES or len(findings) >= max_findings:
                limited = True
                break
            candidates += 1
            try:
                item, reason = parser(data, offset, budget)
            except ValidationLimit:
                limited = True
                budget["bytes"] = 0
                break
            if item:
                findings.append(item)
            else:
                rejected[reason] += 1
            cursor = offset + len(magic)
    # Archive signatures are explicitly weaker than validated structures; no extraction.
    for magic, archive in ARCHIVE_MAGICS:
        offset = data.find(magic)
        if offset >= 0:
            if len(findings) >= max_findings:
                limited = True
                break
            findings.append(finding(data, offset, len(magic), "archive_signature", "magic_only",
                                    {"format": archive, "extracted": False}))
    for marker in TEXT_MARKERS:
        for match in re.finditer(re.escape(marker), data, re.IGNORECASE):
            if len(findings) >= max_findings:
                limited = True
                break
            offset = match.start()
            stop = min(len(data), offset + 120)
            snippet = re.match(rb"[\x20-\x7e]{1,120}", data[offset:stop])
            length = len(snippet[0]) if snippet else len(marker)
            findings.append(finding(data, offset, length, "text_marker", "self_description",
                                    {"marker": marker.decode("ascii"),
                                     "text": data[offset:offset + length].decode("ascii", errors="replace"),
                                     "hardware_identity_verified": False}))
    findings.sort(key=lambda item: (item["offset"], item["kind"]))
    return {"findings": findings, "rejected_candidates": dict(rejected),
            "limits_reached": limited, "structural_candidates_checked": candidates,
            "validation_budget": {"max_bytes": initial_byte_budget,
                                  "max_entries": MAX_VALIDATION_ENTRIES}}


def inspect_path(path, *, max_bytes=DEFAULT_MAX_BYTES, max_findings=256):
    if not 1 <= max_bytes <= HARD_MAX_BYTES:
        raise ValueError("max_bytes must be between 1 and HARD_MAX_BYTES")
    path = Path(path)
    # O_NONBLOCK prevents a supplied FIFO from hanging before fstat rejects it.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("input must be a regular file")
        data = stream.read(max_bytes)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("input changed during inspection")
    if len(data) != min(max_bytes, before.st_size):
        raise ValueError("input has inconsistent regular-file size")
    result = inspect_bytes(data, max_findings=max_findings)
    return {"schema_version": 1, "ok": True, "command": "inspect_firmware_image",
            "input": {"path": str(path.absolute()), "bytes": before.st_size,
                      "inspected_bytes": len(data), "limited_to_prefix": before.st_size > len(data),
                      "sha256": digest(data) if before.st_size == len(data) else None,
                      "inspected_prefix_sha256": digest(data)},
            "limits": {"max_bytes": max_bytes, "max_findings": max_findings,
                       "max_structural_candidates": MAX_CANDIDATES,
                       "dtb_max_bytes": MAX_DTB_BYTES, "dtb_max_tokens": MAX_DTB_TOKENS},
            "hardware_identity": {"status": "unknown",
                                  "reason": "Offline image declarations do not measure physical board identity."},
            "scope": {"executed_input": False, "decrypted": False, "extracted_archives": False,
                      "accessed_hardware": False, "modified_input": False}, **result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", help="Normally acquired local firmware or object file")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--max-findings", type=int, default=256)
    args = parser.parse_args(argv)
    try:
        report = inspect_path(args.image, max_bytes=args.max_bytes, max_findings=args.max_findings)
    except (OSError, ValueError) as error:
        print(json.dumps({"schema_version": 1, "ok": False, "command": "inspect_firmware_image",
                          "error": {"type": type(error).__name__, "message": str(error)}}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
