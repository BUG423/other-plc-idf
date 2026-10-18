"""Synthetic record tests; no vendor files, processes, network or device access.

The proprietary preamble is not copied into fixtures. Tests pin a synthetic
preamble hash for record parsing; a separate test ensures unknown profiles fail.
The real observed preamble is exercised only by the recorded private experiment.
"""
import importlib.util
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "vendor_variables", Path(__file__).resolve().parents[1] / "inspect_vendor_variables.py")
variables = importlib.util.module_from_spec(spec)
spec.loader.exec_module(variables)
PREAMBLE = b"Synthetic-GVT-not-vendor-data".ljust(variables.PREAMBLE_BYTES, b"\0")


def fixture(records=None):
    records = records or [("g_Mode", 4, 0x02100200), ("g_Confidence", 32, 0x02100210)]
    def string(text):
        data = text.encode("ascii")
        return bytes([len(data)]) + data
    data = PREAMBLE + struct.pack("<I", len(records))
    for name, kind, address in records:
        data += (struct.pack("<II", kind, kind) + string(name) + string("OFF" if kind == 1 else "0")
                 + b"\x01\x01\0\0" + struct.pack("<I", address) + string("test variable")
                 + b"\xff\xfe\xff\0" + bytes(8))
    return data


class VariableTests(unittest.TestCase):
    def setUp(self):
        self.profile = patch.object(variables, "PREAMBLE_SHA256", hashlib.sha256(PREAMBLE).hexdigest())
        self.profile.start()
        self.addCleanup(self.profile.stop)

    def test_parse_records_uses_gvt_addresses_and_offsets_only(self):
        data = fixture()
        result = variables.parse_gvt(data)
        self.assertEqual(result["count"], 2)
        mode, confidence = result["records"]
        self.assertEqual((mode["name"], mode["type"], mode["width_bits"], mode["address"]),
                         ("g_Mode", "INT", 16, 0x02100200))
        self.assertEqual(mode["sinfo_type_candidate"], 8)
        self.assertNotEqual(mode["sinfo_type_candidate"], mode["type_code"])
        self.assertEqual(mode["compiler_symbol_candidate"], "G_MODE")
        for row in [mode, confidence]:
            self.assertEqual(struct.unpack_from("<I", data, row["address_offset"])[0], row["address"])
        self.assertEqual(mode["record_end_offset"], confidence["record_offset"])
        self.assertEqual(confidence["record_end_offset"], len(data))

    def test_unknown_preamble_and_truncation_are_rejected(self):
        data = fixture()
        for size in range(len(data)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                variables.parse_gvt(data[:size])
        bad = bytearray(data)
        bad[10] ^= 1
        with self.assertRaisesRegex(ValueError, "preamble"):
            variables.parse_gvt(bytes(bad))

    def test_count_trailing_data_and_type_mismatch_are_rejected(self):
        for count in [0, 257, 1, 3]:
            data = bytearray(fixture())
            struct.pack_into("<I", data, variables.PREAMBLE_BYTES, count)
            with self.assertRaises(ValueError):
                variables.parse_gvt(bytes(data))
        with self.assertRaises(ValueError):
            variables.parse_gvt(fixture() + b"trailing")
        data = bytearray(fixture())
        struct.pack_into("<I", data, variables.PREAMBLE_BYTES + 8, 32)
        with self.assertRaises(ValueError):
            variables.parse_gvt(bytes(data))

    def test_duplicate_names_addresses_unknown_types_and_unassigned_addresses_rejected(self):
        for records in [
            [("g_Mode", 4, 0x02100200), ("G_MODE", 4, 0x02100210)],
            [("g_Mode", 4, 0x02100200), ("g_Other", 4, 0x02100200)],
            [("g_Mode", 123, 0x02100200)], [("g_Mode", 4, 0)],
            [("Namespace::g_Mode", 4, 0x02100200)],
        ]:
            with self.subTest(records=records), self.assertRaises(ValueError):
                variables.parse_gvt(fixture(records))

    def test_flags_tail_and_extended_string_encoding_fail_closed(self):
        original = fixture()
        row = variables.parse_gvt(original)["records"][0]
        for offset in [row["flags_offset"], row["tail_offset"], row["name_length_offset"]]:
            malformed = bytearray(original)
            malformed[offset] = 255 if offset == row["name_length_offset"] else malformed[offset] ^ 1
            with self.assertRaises(ValueError):
                variables.parse_gvt(bytes(malformed))

    def test_csv_requires_bom_and_rejects_duplicates_or_bad_widths(self):
        for text in ["g_Mode\t0X2100200\tdescription\t16\ng_mode\t0X2100200\tx\t16\n",
                     "g_Mode\t0X2100200\tdescription\t0\n", "g_Mode\tnot-hex\tx\t16\n"]:
            with self.assertRaises(ValueError):
                variables.parse_address_csv(text.encode("utf-16"))
        with self.assertRaises(ValueError):
            variables.parse_address_csv(b"g_Mode\t0X2100200\tx\t16")

    def test_real_export_unescaped_comment_tabs_do_not_shift_width(self):
        rows = variables.parse_address_csv("internal\t0X2100200\tdescription\tmore text\t3458048\n".encode("utf-16"))
        self.assertEqual(rows["internal"]["width_bits"], 3458048)

    def test_csv_is_cross_check_never_used_to_replace_gvt_address(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gvt, csv = root / "variables.gvt", root / "addresses.csv"
            gvt.write_bytes(fixture())
            csv.write_bytes("g_Mode\t0X2100200\tx\t16\ng_Confidence\t0X2100210\tx\t32\n".encode("utf-16"))
            result = variables.inspect_variables(gvt, address_csv=csv, required_names=["G_MODE"])
            self.assertTrue(result["ok"], result.get("error"))
            self.assertFalse(result["allocator_implemented"])
            self.assertFalse(result["elf_patched"])
            self.assertFalse(result["downloadable"])
            self.assertEqual(result["selected_records"][0]["name"], "g_Mode")
            self.assertEqual(len(result["csv_cross_check"]["checks"]), 2)
            csv.write_bytes("g_Mode\t0X2100300\tx\t16\ng_Confidence\t0X2100210\tx\t32\n".encode("utf-16"))
            result = variables.inspect_variables(gvt, address_csv=csv)
            self.assertFalse(result["ok"])
            self.assertEqual(result["records"][0]["address"], 0x02100200)
            self.assertIn("disagrees", result["error"])

    def test_required_names_do_not_fall_back_to_another_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "variables.gvt"
            path.write_bytes(fixture())
            for names in [[], ["missing"], ["g_Mode", "G_MODE"], ["bad.name"]]:
                self.assertFalse(variables.inspect_variables(path, required_names=names)["ok"])

    def test_symlink_input_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, alias = root / "variables.gvt", root / "alias.gvt"
            source.write_bytes(fixture())
            alias.symlink_to(source)
            self.assertFalse(variables.inspect_variables(alias)["ok"])


if __name__ == "__main__":
    unittest.main()
