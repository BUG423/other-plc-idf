"""Synthetic format fixtures, error paths and false-identity regressions; no hardware."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import random
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib


spec = importlib.util.spec_from_file_location(
    "firmware_inspector", Path(__file__).resolve().parents[1] / "inspect_firmware_image.py")
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)


def elf(bits=32, endian="<", machine=40, **overrides):
    size = 52 if bits == 32 else 64
    fields = dict(etype=1, machine=machine, version=1, entry=0, phoff=0, shoff=0,
                  flags=0, ehsize=size, phsize=0, phnum=0, shsize=0, shnum=0, shstr=0)
    fields.update(overrides)
    ident = b"\x7fELF" + bytes((1 if bits == 32 else 2, 1 if endian == "<" else 2, 1, 0)) + b"\0" * 8
    return ident + struct.pack(endian + ("HHIIIIIHHHHHH" if bits == 32 else "HHIQQQIHHHHHH"),
                               *fields.values())


def uimage(payload=b"example payload", arch=2):
    header = bytearray(struct.pack(">7I4B32s", 0x27051956, 0, 0, len(payload), 0x8000, 0x8000,
                                   zlib.crc32(payload), 5, arch, 2, 0, b"Synthetic test image"))
    struct.pack_into(">I", header, 4, zlib.crc32(header))
    return bytes(header) + payload


def dtb():
    strings = b"compatible\0model\0"
    structure = struct.pack(">I", 1) + b"\0" * 4
    for nameoff, value in [(0, b"testvendor,synthetic-board\0testvendor,synthetic-soc\0"),
                           (11, b"Synthetic board only\0")]:
        structure += struct.pack(">III", 3, len(value), nameoff) + value
        structure += b"\0" * (-len(structure) % 4)
    structure += struct.pack(">II", 2, 9)
    str_off = 56 + len(structure)
    total = str_off + len(strings)
    return struct.pack(">10I", 0xd00dfeed, total, 56, str_off, 40, 17, 16, 0, len(strings),
                       len(structure)) + b"\0" * 16 + structure + strings


def findings(data, kind):
    return [item for item in inspector.inspect_bytes(data)["findings"] if item["kind"] == kind]


class FirmwareImageTests(unittest.TestCase):
    def test_embedded_elf32_offset_and_hash(self):
        image = elf()
        result = findings(b"prefix" + image + b"trailer", "elf")[0]
        self.assertEqual((result["offset"], result["length"]), (6, 52))
        self.assertEqual(result["sha256"], hashlib.sha256(image).hexdigest())
        self.assertEqual(result["details"]["machine"], "ARM")
        self.assertEqual(result["evidence_grade"], "validated_structure")

    def test_big_endian_elf64(self):
        result = findings(elf(64, ">", 183), "elf")[0]
        self.assertEqual(result["details"]["bits"], 64)
        self.assertEqual(result["details"]["byte_order"], "big")
        self.assertEqual(result["details"]["machine"], "AArch64")

    def test_elf_osabi_zero_does_not_claim_linux(self):
        result = findings(elf(), "elf")[0]
        self.assertEqual(result["details"]["osabi"], 0)
        self.assertNotIn("image_os", result["details"])
        self.assertFalse(result["details"]["hardware_identity_verified"])

    def test_elf_load_payload_hash_and_bounds(self):
        header = elf(phoff=52, phsize=32, phnum=1)
        segment = struct.pack("<8I", 1, 84, 0, 0, 4, 8, 5, 4)
        result = findings(header + segment + b"CODE", "elf")[0]
        self.assertEqual(result["length"], 88)
        self.assertTrue(result["complete_in_inspected_region"])
        short = findings(header + segment, "elf")[0]
        self.assertEqual(short["evidence_grade"], "header_only_truncated")
        self.assertEqual(short["declared_length"], 88)

    def test_elf_nobits_not_interpreted_as_file_payload(self):
        header = elf(shoff=52, shsize=40, shnum=1)
        section = struct.pack("<10I", 0, 8, 0, 0, 0x100000, 0x200000, 0, 0, 4, 0)
        result = findings(header + section, "elf")[0]
        self.assertEqual(result["length"], 92)

    def test_invalid_elf_magic_and_header_are_rejected(self):
        for data in [b"\x7fELF" + b"\0" * 60, elf(ehsize=8), elf(version=2),
                     elf(machine=0), elf(phnum=65535), elf(shoff=999, shnum=0)]:
            with self.subTest(data=data):
                self.assertEqual(findings(data, "elf"), [])

    def test_truncated_ident_and_header_do_not_crash(self):
        for stop in [4, 10, 16, 51]:
            self.assertEqual(findings(elf()[:stop], "elf"), [])

    def test_truncated_elf_tables_are_explicit(self):
        result = findings(elf(shoff=52, shsize=40, shnum=1), "elf")[0]
        self.assertFalse(result["complete_in_inspected_region"])
        self.assertEqual(result["length"], 52)

    def test_bad_load_segment_is_rejected(self):
        image = elf(phoff=52, phsize=32, phnum=1) + struct.pack("<8I", 1, 84, 0, 0, 9, 2, 0, 4)
        self.assertEqual(findings(image, "elf"), [])

    def test_uimage_both_crcs_and_embedded_offset(self):
        result = findings(b"123" + uimage(), "uimage")[0]
        self.assertEqual(result["offset"], 3)
        self.assertEqual(result["evidence_grade"], "validated_structure_and_crc")
        self.assertTrue(result["details"]["header_crc32_verified"])
        self.assertTrue(result["details"]["payload_crc32_verified"])
        self.assertEqual(result["details"]["image_os"], "Linux")

    def test_uimage_architecture_ids_match_upstream(self):
        self.assertEqual(findings(uimage(arch=24), "uimage")[0]["details"]["image_arch"], "x86-64")
        self.assertEqual(findings(uimage(arch=26), "uimage")[0]["details"]["image_arch"], "RISC-V")

    def test_uimage_false_magic_and_invalid_header_crc_rejected(self):
        invalid = bytearray(uimage())
        invalid[8] ^= 1
        for data in [b"\x27\x05\x19\x56" + b"\0" * 60, bytes(invalid), uimage()[:32]]:
            self.assertEqual(findings(data, "uimage"), [])

    def test_uimage_corrupt_payload_never_validated(self):
        image = bytearray(uimage())
        image[-1] ^= 1
        result = findings(bytes(image), "uimage")[0]
        self.assertEqual(result["evidence_grade"], "header_valid_payload_corrupt")
        self.assertFalse(result["details"]["payload_crc32_verified"])

    def test_uimage_truncation_separates_observed_and_declared(self):
        image = uimage()
        result = findings(image[:-5], "uimage")[0]
        self.assertEqual(result["length"], len(image) - 5)
        self.assertEqual(result["declared_length"], len(image))
        self.assertIsNone(result["details"]["payload_crc32_verified"])

    def test_dtb_nested_format_and_declared_properties(self):
        result = findings(b"p" * 7 + dtb(), "dtb")[0]
        self.assertEqual(result["offset"], 7)
        self.assertEqual(result["length"], len(dtb()))
        self.assertEqual(result["evidence_grade"], "validated_structure")
        properties = result["details"]["declared_target_properties"]
        self.assertEqual(properties[0]["node"], "/")
        self.assertEqual(properties[0]["values"][0], "testvendor,synthetic-board")
        self.assertEqual(properties[1]["property"], "model")
        start = result["offset"] + properties[0]["relative_value_offset"]
        self.assertTrue((b"p" * 7 + dtb())[start:].startswith(b"testvendor,"))

    def test_dtb_truncated_header_and_body(self):
        self.assertEqual(findings(dtb()[:39], "dtb"), [])
        result = findings(dtb()[:-1], "dtb")[0]
        self.assertEqual(result["evidence_grade"], "header_only_truncated")
        self.assertEqual(result["length"], 40)
        self.assertEqual(result["details"]["declared_target_properties"], [])

    def test_dtb_overlapping_sections_and_wrong_version_rejected(self):
        for field, value in [(3, 56), (5, 16), (6, 99), (1, 0xffffffff), (2, 57)]:
            data = bytearray(dtb())
            struct.pack_into(">I", data, field * 4, value)
            with self.subTest(field=field):
                self.assertEqual(findings(bytes(data), "dtb"), [])

    def test_dtb_invalid_token_rejected(self):
        data = bytearray(dtb())
        struct.pack_into(">I", data, 56, 0xdeadbeef)
        self.assertEqual(findings(bytes(data), "dtb"), [])

    def test_dtb_property_name_and_payload_bounds_checked(self):
        for field_offset, value in [(68, 0xffff), (72, 0xffff)]:
            data = bytearray(dtb())
            struct.pack_into(">I", data, field_offset, value)
            self.assertEqual(findings(bytes(data), "dtb"), [])

    def test_dtb_missing_memory_reservation_terminator_rejected(self):
        data = bytearray(dtb())
        data[40:56] = b"\x01" * 16
        self.assertEqual(findings(bytes(data), "dtb"), [])

    def test_raw_markers_never_identify_hardware(self):
        results = findings(b"Linux version 3.10 synthetic\0sunxi_usb_udc\0Allwinner\0", "text_marker")
        self.assertEqual(len(results), 3)
        self.assertTrue(all(item["evidence_grade"] == "self_description" for item in results))
        self.assertTrue(all(not item["details"]["hardware_identity_verified"] for item in results))

    def test_archive_magic_not_claimed_as_extracted_or_linux(self):
        result = findings(b"7z\xbc\xaf\x27\x1cgarbage", "archive_signature")[0]
        self.assertEqual(result["evidence_grade"], "magic_only")
        self.assertFalse(result["details"]["extracted"])
        self.assertNotIn("image_os", result["details"])

    def test_findings_limit_is_explicit(self):
        result = inspector.inspect_bytes(b"sunxi\0" * 100, max_findings=3)
        self.assertEqual(len(result["findings"]), 3)
        self.assertTrue(result["limits_reached"])

    def test_candidate_limit_is_explicit(self):
        result = inspector.inspect_bytes((b"\x7fELF" + b"\0" * 16) * (inspector.MAX_CANDIDATES + 1))
        self.assertTrue(result["limits_reached"])
        self.assertEqual(result["structural_candidates_checked"], inspector.MAX_CANDIDATES)
        self.assertEqual(result["findings"], [])

    def test_entry_budget_is_shared_across_images(self):
        with patch.object(inspector, "MAX_VALIDATION_ENTRIES", 1):
            result = inspector.inspect_bytes(dtb())
        self.assertTrue(result["limits_reached"])
        self.assertEqual(result["findings"], [])

    def test_byte_budget_is_enforced_before_large_hash_or_crc(self):
        with patch.object(inspector, "MAX_VALIDATION_BYTES", 1):
            result = inspector.inspect_bytes(uimage())
        self.assertTrue(result["limits_reached"])
        self.assertEqual(result["findings"], [])

    def test_filename_does_not_establish_target_and_input_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Easy521_Allwinner_Linux.img"
            path.write_bytes(b"not firmware")
            before = path.stat().st_mtime_ns
            result = inspector.inspect_path(path)
            self.assertEqual(result["findings"], [])
            self.assertEqual(result["hardware_identity"]["status"], "unknown")
            self.assertEqual(path.read_bytes(), b"not firmware")
            self.assertEqual(path.stat().st_mtime_ns, before)

    def test_prefix_limit_does_not_publish_prefix_hash_as_full_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.bin"
            path.write_bytes(elf())
            result = inspector.inspect_path(path, max_bytes=16)
            self.assertEqual(result["input"]["bytes"], 52)
            self.assertEqual(result["input"]["inspected_bytes"], 16)
            self.assertTrue(result["input"]["limited_to_prefix"])
            self.assertIsNone(result["input"]["sha256"])

    def test_empty_file_is_valid_inspection_without_findings(self):
        with tempfile.NamedTemporaryFile() as stream:
            result = inspector.inspect_path(stream.name)
        self.assertTrue(result["ok"])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["input"]["sha256"], hashlib.sha256(b"").hexdigest())

    def test_missing_file_returns_json_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = inspector.main([str(Path(directory) / "missing.bin")])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_directory_and_fifo_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for path in [Path(directory), Path(directory) / "pipe"]:
                if path.name == "pipe":
                    os.mkfifo(path)
                with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                    inspector.inspect_path(path)

    def test_invalid_limits_rejected(self):
        for value in [0, -1, inspector.HARD_MAX_BYTES + 1]:
            with self.assertRaises(ValueError):
                inspector.inspect_path("missing.bin", max_bytes=value)
        for value in [0, -1, 1025]:
            with self.assertRaises(ValueError):
                inspector.inspect_bytes(b"", max_findings=value)

    def test_cli_success_is_one_json_document(self):
        with tempfile.NamedTemporaryFile() as stream:
            stream.write(elf())
            stream.flush()
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = inspector.main([stream.name])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])

    def test_random_looking_input_and_magic_only_false_positives(self):
        data = b"\x7fELF\xd0\x0d\xfe\xed\x27\x05\x19\x56" + bytes(range(256)) * 16
        result = inspector.inspect_bytes(data)
        self.assertEqual(result["findings"], [])
        self.assertEqual(sum(result["rejected_candidates"].values()), 3)

    def test_deterministic_mutations_do_not_escape_bounds_or_crash(self):
        generator = random.Random(20261017)
        for base in [elf(), uimage(), dtb()]:
            for _ in range(100):
                image = bytearray(base)
                for _ in range(generator.randrange(1, 6)):
                    image[generator.randrange(len(image))] = generator.randrange(256)
                result = inspector.inspect_bytes(bytes(image))
                json.dumps(result, allow_nan=False)
                for item in result["findings"]:
                    self.assertGreaterEqual(item["offset"], 0)
                    self.assertGreater(item["length"], 0)
                    self.assertLessEqual(item["offset"] + item["length"], len(image))
                    self.assertEqual(item["scope"], "offline_file_only")


if __name__ == "__main__":
    unittest.main()
