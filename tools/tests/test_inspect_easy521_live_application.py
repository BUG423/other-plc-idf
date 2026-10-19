"""Synthetic live-wrapper comparisons and bounded zlib; no captured binaries."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import zlib

from tools import inspect_easy521_live_application as live


def record(tag, data):
    return struct.pack("<II", tag, len(data)) + data


def header():
    return record(0x100, b"foid") + record(0x200, b"x" * 32) + record(0x201, bytes(4))


def fixture():
    identifier = b"{01234567-89ab-cdef-0123-456789abcdef}"
    user = header() + record(0x40000, b"synthetic instruction bytes" * 2)
    user += record(0x40100, struct.pack("<4I", 0, 1, 2, 0xffffffff)) + record(0x280, identifier)
    variable = record(0x28000, identifier) + header() + record(0x30000, b"synthetic allocation" * 8)
    variable += record(0x3000f, b"\0") + record(0x30020, bytes(4))
    template = {"userprog.foid": user, "varsinfo.foid": variable, "symbol.sym": b"old symbol fixture",
                "stprog.o": b"new program fixture", "stuserprog.o": b"new user fixture",
                **{name: header() + ("synthetic " + name).encode() for name in live.CONFIG_FILES}}
    device = dict(template)
    device["userprog.foid"] = live.compress_existing_userprog(user)
    device["varsinfo.foid"] = live.compress_existing_varsinfo(variable)
    for name in live.CONFIG_FILES:
        raw = bytearray(device[name])
        raw[26:28] = b"yz"
        device[name] = bytes(raw)
    device["stprog.o"], device["stuserprog.o"] = b"device program fixture", b"device user fixture"
    device["symbol.sym"] = b"device symbol fixture"
    return template, device


class CompressionTests(unittest.TestCase):
    def test_exact_single_stream_and_zero_output(self):
        for data in (b"", b"abc", b"\0" * 10000):
            self.assertEqual(live.decompress_exact(zlib.compress(data), len(data)), data)

    def test_truncation_checksum_trailing_or_concatenation_are_rejected(self):
        raw = zlib.compress(b"hello")
        for stream in (raw[:-1], raw + b"x", raw + zlib.compress(b"extra"), raw[:-1] + bytes([raw[-1] ^ 1])):
            with self.subTest(stream=stream), self.assertRaises(ValueError):
                live.decompress_exact(stream, 5)

    def test_untrusted_sizes_and_expansion_bomb_are_rejected(self):
        stream = zlib.compress(b"x" * (1024 * 1024))
        for size in (-1, True, 1.5, "5", live.MAX_EXPANDED_BYTES + 1, 4):
            with self.subTest(size=size), self.assertRaises(ValueError):
                live.decompress_exact(stream, size)
        with self.assertRaises(ValueError):
            live.decompress_exact(zlib.compress(b"abc"), 4)

    def test_wrappers_preserve_headers_uuid_tail_and_length_table(self):
        template, device = fixture()
        user = live.normalize_userprog(device["userprog.foid"])
        raw_user = live.records(template["userprog.foid"], (0x100, 0x200, 0x201, 0x40000, 0x40100, 0x280))
        self.assertEqual(user["sections"], [raw_user[3]["value"], raw_user[4]["value"]])
        self.assertEqual(user["prefix"], template["userprog.foid"][:64])
        self.assertEqual(user["length_table"][4:], [0, 0])
        variable = live.normalize_varsinfo(device["varsinfo.foid"])
        self.assertEqual(variable["uuid"], user["uuid"])
        result = live.compare_files(template, device)
        self.assertTrue(result["userprog"]["default_zlib_reproduces_live_bytes"])
        self.assertTrue(result["varsinfo"]["default_zlib_reproduces_live_bytes"])

    def test_userprog_bad_length_pair_or_extra_section_cannot_pass(self):
        _, device = fixture()
        raw = device["userprog.foid"]
        r = live.records(raw, (0x100, 0x200, 0x201, 0x40000, 0x40100, 0x40200, 0x280))
        for index in (0, 1, 2, 3, 4, 5):
            data = bytearray(raw)
            offset = r[5]["offset"] + 8 + index * 4
            data[offset] ^= 1
            with self.subTest(index=index), self.assertRaises(ValueError):
                live.normalize_userprog(bytes(data))

    def test_varsinfo_bad_lengths_or_stream_cannot_pass(self):
        _, device = fixture()
        raw = device["varsinfo.foid"]
        r = live.records(raw, (0x28000, 0x100, 0x200, 0x201, 0x30001, 0x3000f, 0x30020))
        for delta in (8, 12, 16):
            data = bytearray(raw)
            data[r[4]["offset"] + delta] ^= 1
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                live.normalize_varsinfo(bytes(data))

    def test_records_reject_wrong_order_truncated_and_extra_data(self):
        raw = record(1, b"a") + record(2, b"b")
        for data in (raw[:-1], raw + b"x", record(2, b"a"), b""):
            with self.subTest(data=data), self.assertRaises(ValueError):
                live.records(data, (1, 2))

    def test_comparison_distinguishes_payload_version_and_opaque_metadata(self):
        template, device = fixture()
        result = live.compare_files(template, device)
        self.assertTrue(result["configuration_payloads_and_versions_equal"])
        self.assertTrue(all(x["only_opaque_metadata_changed"] for x in result["configuration_comparisons"]))
        self.assertTrue(all(x["first_changed_offsets"] == [26, 27] for x in result["configuration_comparisons"]))
        for offset in (60, 64):
            changed = dict(device)
            raw = bytearray(changed["syscfg.foid"])
            raw[offset] ^= 1
            changed["syscfg.foid"] = bytes(raw)
            self.assertFalse(live.compare_files(template, changed)["configuration_payloads_and_versions_equal"])

    def test_public_comparison_api_checks_type_and_byte_budgets(self):
        template, device = fixture()
        wrong = dict(device)
        wrong["stprog.o"] = bytearray(b"mutable")
        with self.assertRaises(ValueError):
            live.compare_files(template, wrong)
        with mock.patch.object(live, "MAX_TOTAL_BYTES", 32), self.assertRaises(ValueError):
            live.compare_files(template, device)


class FileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.candidate, self.device = self.root / "candidate", self.root / "device"
        self.candidate.mkdir()
        (self.candidate / "files").mkdir()
        self.device.mkdir()
        self.template, self.live = fixture()
        records = []
        for number, name in enumerate(("project.hcpp", *live.FILES)):
            raw = b"Empty" if name == "project.hcpp" else self.template[name]
            p = self.candidate / "files" / name
            p.write_bytes(raw)
            records.append({"index": number, "remote_name": name, "path": str(p), "size": len(raw), "sha256": live.sha(raw)})
        self.manifest = {"schema": "plcidf.easy521-fixed-application-candidate.v1", "ok": True,
                         "profile": "easy521-bess-existing-interface-v1", "files": records,
                         "association": {"source.st": "0" * 64}}
        (self.candidate / "application-candidate.json").write_text(json.dumps(self.manifest))
        self.target = {"linux_device": "001:002", "vidpid": "0471:a531", "serial": None}
        for number, (name, raw) in enumerate(self.live.items()):
            (self.device / name).write_bytes(raw)
            report = {"schema": "plcidf.native-application-transaction.v1", "ok": True, "status": "file-read",
                      "action": "upload-read", "remote_name": name, "bytes": len(raw), "sha256": live.sha(raw),
                      "plc_change_possible": False, "automatic_retries": 0, "release_rc": 0,
                      "close_called": True, "target": self.target}
            (self.device / f"{number:02d}-application-{name}.json").write_text(json.dumps(report))
        self.output = self.root / "derived"

    def inspect(self, *, create=False):
        return live.inspect_paths(candidate_directory=self.candidate, live_directory=self.device,
                                  output_directory=self.output if create else None)

    def test_inspection_emits_no_payload_and_does_not_create_outputs(self):
        result = self.inspect()
        self.assertTrue(result["comparison"]["interface_payload_compatibility_observed"])
        self.assertFalse(self.output.exists())
        self.assertNotIn("synthetic instruction", json.dumps(result))
        self.assertFalse(result["downloadable"])

    def test_live_candidate_inherits15_device_files_and_only_replaces_two_objects(self):
        result = self.inspect(create=True)
        self.assertEqual(result["file_count"], 17)
        for name in live.FILES:
            expected = self.template[name] if name in ("stprog.o", "stuserprog.o") else self.live[name]
            self.assertEqual((self.output / "files" / name).read_bytes(), expected)
        self.assertFalse((self.output / "files/project.hcpp").exists())
        self.assertTrue(result["symbol_metadata_policy_review_required"])
        self.assertTrue(result["symbol_metadata_inherited_from_device"])
        self.assertFalse(result["symbol_vendor_md5_for_new_source_verified"])
        self.assertFalse(result["archive_transfer_policy_verified"])
        self.assertFalse(result["candidate_device_acceptance_verified"])
        self.assertFalse(result["downloadable"])

    def test_readback_report_hash_or_target_disagreement_is_rejected(self):
        p = next(self.device.glob("*-application-userprog.foid.json"))
        original = json.loads(p.read_text())
        for field, value in (("sha256", "0" * 64), ("target", {"linux_device": "002:003"}), ("plc_change_possible", True)):
            changed = dict(original)
            changed[field] = value
            p.write_text(json.dumps(changed))
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.inspect(create=True)
            self.assertFalse(self.output.exists())
        p.write_text(json.dumps(original))

    def test_duplicate_readback_evidence_and_parent_hash_drift_are_rejected(self):
        report = next(self.device.glob("*-application-userprog.foid.json"))
        extra = self.device / "extra-application-userprog.foid.json"
        extra.write_bytes(report.read_bytes())
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.inspect()
        extra.unlink()
        (self.candidate / "files/stprog.o").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.inspect()

    def test_different_configuration_payload_refuses_candidate(self):
        name = "syscfg.foid"
        raw = self.live[name] + b"changed"
        (self.device / name).write_bytes(raw)
        p = next(self.device.glob("*-application-" + name + ".json"))
        report = json.loads(p.read_text())
        report.update(bytes=len(raw), sha256=live.sha(raw))
        p.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, "payloads differ"):
            self.inspect(create=True)
        self.assertFalse(self.output.exists())

    def test_existing_output_overlap_symlink_and_fifo_are_rejected(self):
        self.output.mkdir()
        with self.assertRaises(ValueError):
            self.inspect(create=True)
        self.output.rmdir()
        self.output = self.device / "new"
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.inspect(create=True)
        p = self.device / "userprog.foid"
        p.unlink()
        p.symlink_to(self.device / "varsinfo.foid")
        with self.assertRaises(ValueError):
            self.inspect()
        p.unlink()
        import os
        os.mkfifo(p)
        with self.assertRaises(ValueError):
            self.inspect()

    def test_read_budget_and_input_drift_preserve_failure_evidence(self):
        with mock.patch.object(live, "MAX_TOTAL_BYTES", 10), self.assertRaises(ValueError):
            self.inspect()
        original = live._read
        changed = False
        def read(path, budget):
            nonlocal changed
            if self.output.exists() and not changed:
                changed = True
                (self.device / "stprog.o").write_bytes(b"changed")
            return original(path, budget)
        with mock.patch.object(live, "_read", read), self.assertRaisesRegex(ValueError, "input drift"):
            self.inspect(create=True)
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / "live-application-candidate.json").exists())


if __name__ == "__main__":
    unittest.main()
