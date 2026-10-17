"""Synthetic ELF/container and temporary-file tests; never use PLC/Windows."""

import contextlib
import importlib.util
import io
import json
import struct
import tempfile
import unittest
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "vendor_inspection", Path(__file__).resolve().parents[1] / "inspect_vendor_application.py")
inspection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspection)


def elf_fixture(*, bits=32, endian="<", runtime="runtime_missing", relocation_symbol=2):
    """A minimal ELF with one undefined runtime symbol and one relocation."""
    names = ["", ".shstrtab", ".strtab", ".symtab", ".text", ".rel.text", ".ARM.attributes"]
    section_names = b"\x00"
    offsets = {}
    for name in names[1:]:
        offsets[name] = len(section_names)
        section_names += name.encode() + b"\x00"
    strings = b"\x00_CALL_MAIN\x00" + runtime.encode() + b"\x00"
    sym_format = endian + ("IIIBBH" if bits == 32 else "IBBHQQ")
    if bits == 32:
        symbols = (struct.pack(sym_format, 0, 0, 0, 0, 0, 0)
                   + struct.pack(sym_format, 1, 0, 4, 0x12, 0, 4)
                   + struct.pack(sym_format, 12, 0, 0, 0x10, 0, 0))
        relocations = struct.pack(endian + "II", 0, relocation_symbol << 8 | 2)
    else:
        symbols = (struct.pack(sym_format, 0, 0, 0, 0, 0, 0)
                   + struct.pack(sym_format, 1, 0x12, 0, 4, 0, 4)
                   + struct.pack(sym_format, 12, 0x10, 0, 0, 0, 0))
        relocations = struct.pack(endian + "QQ", 0, relocation_symbol << 32 | 2)
    header_size = 52 if bits == 32 else 64
    sections_data = [b"", section_names, strings, symbols, b"\x00\x00\x00\x00", relocations, b"A\x00"]
    data = bytearray(header_size)
    headers = [(0,) * 10]
    for index, (name, payload) in enumerate(zip(names[1:], sections_data[1:]), 1):
        while len(data) % 4:
            data += b"\x00"
        offset = len(data)
        data += payload
        kind = {1: 3, 2: 3, 3: 2, 4: 1, 5: 9, 6: 0x70000003}[index]
        link = 2 if index == 3 else 3 if index == 5 else 0
        info = 1 if index == 3 else 4 if index == 5 else 0
        stride = struct.calcsize(sym_format) if index == 3 else (8 if bits == 32 else 16) if index == 5 else 0
        headers.append((offsets[name], kind, 0, 0, offset, len(payload), link, info, 4, stride))
    while len(data) % 4:
        data += b"\x00"
    shoff = len(data)
    sh_format = endian + ("IIIIIIIIII" if bits == 32 else "IIQQQQIIQQ")
    for section in headers:
        data += struct.pack(sh_format, *section)
    data[:16] = b"\x7fELF" + bytes([1 if bits == 32 else 2, 1 if endian == "<" else 2, 1]) + b"\x00" * 9
    header_format = endian + ("HHIIIIIHHHHHH" if bits == 32 else "HHIQQQIHHHHHH")
    struct.pack_into(header_format, data, 16, 1, 40 if bits == 32 else 183, 1, 0, 0, shoff,
                     0x05000000 if bits == 32 else 0, header_size, 0, 0,
                     struct.calcsize(sh_format), len(headers), 1)
    return bytes(data)


class ElfInspectionTests(unittest.TestCase):
    def test_elf32_arm_symbols_relocations_and_attribute_fingerprint(self):
        result = inspection.inspect_bytes(elf_fixture())
        self.assertEqual(result["inspection_status"], "parsed")
        elf = result["elf"]
        self.assertEqual(elf["machine"], "ARM")
        self.assertEqual(elf["arm_eabi_version"], 5)
        self.assertEqual(elf["undefined_global_weak"]["names"], ["runtime_missing"])
        self.assertEqual(elf["defined_global_weak"]["names"], ["_CALL_MAIN"])
        self.assertEqual(elf["relocations"]["types"], {"R_ARM_ABS32": 1})
        self.assertEqual(elf["relocations"]["referenced_undefined_names"], ["runtime_missing"])
        self.assertFalse(elf["arm_attribute_sections"][0]["decoded"])

    def test_elf64_and_big_endian_use_their_own_layout(self):
        for bits, endian in [(32, ">"), (64, "<"), (64, ">")]:
            with self.subTest(bits=bits, endian=endian):
                elf = inspection.inspect_bytes(elf_fixture(bits=bits, endian=endian))["elf"]
                self.assertEqual(elf["class_bits"], bits)
                self.assertEqual(elf["endianness"], "little" if endian == "<" else "big")
                self.assertEqual(elf["undefined_global_weak"]["unique_count"], 1)
                self.assertEqual(elf["relocations"]["entries"], 1)

    def test_every_truncated_recognized_fixture_is_an_error(self):
        complete = elf_fixture()
        for length in range(1, len(complete)):
            with self.subTest(length=length):
                self.assertEqual(inspection.inspect_bytes(complete[:length])["inspection_status"], "error")

    def test_invalid_class_and_encoding_are_errors(self):
        for index in [4, 5, 6]:
            malformed = bytearray(elf_fixture())
            malformed[index] = 0xff
            self.assertEqual(inspection.inspect_bytes(bytes(malformed))["inspection_status"], "error")

    def test_section_beyond_eof_is_rejected(self):
        data = bytearray(elf_fixture())
        shoff = struct.unpack_from("<I", data, 32)[0]
        struct.pack_into("<I", data, shoff + 4 * 40 + 16, len(data) + 100)
        self.assertIn("outside file", inspection.inspect_bytes(bytes(data))["error"])

    def test_invalid_symbol_name_and_zero_entry_stride_are_rejected(self):
        for mutate in ["name", "stride"]:
            data = bytearray(elf_fixture())
            shoff = struct.unpack_from("<I", data, 32)[0]
            if mutate == "name":
                symbol_offset = struct.unpack_from("<I", data, shoff + 3 * 40 + 16)[0]
                struct.pack_into("<I", data, symbol_offset + 16, 0xffffffff)
            else:
                struct.pack_into("<I", data, shoff + 3 * 40 + 36, 0)
            self.assertEqual(inspection.inspect_bytes(bytes(data))["inspection_status"], "error")

    def test_invalid_relocation_symbol_is_rejected(self):
        result = inspection.inspect_bytes(elf_fixture(relocation_symbol=20))
        self.assertIn("nonexistent symbol", result["error"])

    def test_extended_section_count_is_supported(self):
        data = bytearray(elf_fixture())
        shoff = struct.unpack_from("<I", data, 32)[0]
        struct.pack_into("<H", data, 48, 0)
        struct.pack_into("<I", data, shoff + 20, 7)
        self.assertEqual(inspection.inspect_bytes(bytes(data))["elf"]["section_count"], 7)


class ContainerAndReportTests(unittest.TestCase):
    def test_unknown_and_empty_are_observations_not_validated_containers(self):
        for data in [b"", b"\xff" * 56, b"MZexample-only", b"arbitrary opaque input"]:
            result = inspection.inspect_bytes(data)
            self.assertEqual(result["format"], "unknown")
            self.assertEqual(result["inspection_status"], "observed")
            self.assertLessEqual(result["observations"]["prefix_bytes"], len(data) // 2)
            self.assertNotIn("elf", result)

    def test_vendor_prefix_and_length_are_observations_without_field_semantics(self):
        data = bytearray(b"SA\x00\x01" + b"\x00" * 36)
        struct.pack_into(">I", data, 20, len(data))
        result = inspection.inspect_bytes(bytes(data))
        self.assertEqual(result["format"], "observed-SA-prefix")
        match = next(item for item in result["observations"]["length_equalities"]
                     if item["offset"] == 20 and item["endianness"] == "big")
        self.assertEqual(match["arithmetic_relationship"], "equals_file_size")
        self.assertFalse(match["field_semantics_confirmed"])
        self.assertEqual(inspection.inspect_bytes(b"****AS*****\x00payload")["format"], "observed-autoshop-AS-prefix")

    def test_success_marker_does_not_prove_a_build(self):
        marker = inspection.inspect_bytes(b"Success!\r\n")
        self.assertTrue(marker["marker_does_not_prove_build"])
        self.assertNotIn("build_verified", marker)

    def test_multi_input_diff_and_identical_content_relationship(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new, duplicate = root / "old.o", root / "new.o", root / "same.o"
            old.write_bytes(elf_fixture(runtime="old_import"))
            new.write_bytes(elf_fixture(runtime="new_import"))
            duplicate.write_bytes(old.read_bytes())
            result = inspection.inspect_paths([old, duplicate], comparisons=[(old, new)])
            self.assertTrue(result["inspection_ok"])
            self.assertEqual(len(result["files"]), 3)
            diff = result["comparisons"][0]
            self.assertFalse(diff["same_sha256"])
            self.assertEqual(diff["elf"]["undefined_global_weak"], {
                "removed_names": ["old_import"], "added_names": ["new_import"]})
            self.assertEqual(len(result["associations"]["identical_content"]), 1)

    def test_unreadable_oversized_and_malformed_inputs_do_not_hide_other_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid, malformed, large = root / "valid", root / "bad.o", root / "large"
            valid.write_bytes(b"unknown")
            malformed.write_bytes(b"\x7fELF")
            large.write_bytes(b"x" * 100)
            result = inspection.inspect_paths([valid, malformed, large, root / "missing"], max_file_bytes=32)
            self.assertFalse(result["inspection_ok"])
            self.assertEqual([item["inspection_status"] for item in result["files"]],
                             ["observed", "error", "error", "error"])

    def test_cli_json_and_input_overwrite_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.o"
            source.write_bytes(elf_fixture())
            before = source.read_bytes()
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(inspection.main([str(source)]), 0)
            report = json.loads(out.getvalue())
            self.assertEqual(len(report["tool_source_sha256"]), 64)
            with contextlib.redirect_stderr(err):
                self.assertEqual(inspection.main([str(source), "--output", str(source)]), 2)
            self.assertEqual(source.read_bytes(), before)
            self.assertIn("cannot overwrite an input", err.getvalue())


if __name__ == "__main__":
    unittest.main()
