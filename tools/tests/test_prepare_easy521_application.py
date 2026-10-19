"""Synthetic fixed-interface candidate tests; no vendor bytes or device I/O."""
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from tools import prepare_easy521_application as app


def record(tag, value):
    return struct.pack("<II", tag, len(value)) + value


def envelope(payload):
    return (record(0x100, b"foid") + record(0x200, b"m" * 32)
            + record(0x201, bytes(4)) + payload)


def synthetic_interface():
    identifier = b"{01234567-89ab-cdef-0123-456789abcdef}"
    instructions = b"synthetic instruction payload"
    index = struct.pack("<3I", 0xffffffff, 0, 1)
    allocation = b"synthetic allocation"
    return {"Output.prg": instructions,
            "ILIndex.idx": struct.pack("<IH", 0x989681, 1) + bytes(20) + struct.pack("<I", 3) + index,
            "VarsInfo.vi": allocation, "Output.sdt": b"synthetic output config",
            "Config.sdt": b"synthetic source config", "变量表.gvt": b"synthetic GVT",
            "VarNameAndAddr.csv": b"synthetic address export",
            "Temp/userprog.foid": envelope(record(0x40000, instructions)
                 + record(0x40100, index) + record(0x280, identifier)),
            "Temp/varsinfo.foid": record(0x28000, identifier) + envelope(
                 record(0x30000, allocation) + record(0x3000f, b"\0") + record(0x30020, bytes(4))),
            "Temp/symbol.sym": (b"bmysMAIN\0SBR_001\0" * 2),
            **{f"Temp/DownLoad/{name}": envelope(("synthetic " + name).encode())
               for name in app.CONFIG_ORDER}}


def synthetic_object():
    # ELF32 little-endian ARM EABI5 REL with two explicit entry symbols.
    shnames = b"\0.shstrtab\0.strtab\0.symtab\0.text\0"
    strings = b"\0MAIN\0SBR_001\0"
    symbols = (bytes(16) + struct.pack("<IIIBBH", 1, 1, 2, 0x12, 0, 4)
               + struct.pack("<IIIBBH", 6, 3, 2, 0x12, 0, 4))
    data, headers = bytearray(52), [bytes(40)]
    for i, (name, payload, kind) in enumerate(((".shstrtab", shnames, 3),
            (".strtab", strings, 3), (".symtab", symbols, 2), (".text", b"\x70\x47" * 2, 1)), 1):
        while len(data) % 4:
            data.append(0)
        offset = len(data)
        data.extend(payload)
        headers.append(struct.pack("<10I", shnames.index(name.encode()), kind,
                      6 if i == 4 else 0, 0, offset, len(payload), 2 if i == 3 else 0,
                      1 if i == 3 else 0, 4, 16 if i == 3 else 0))
    while len(data) % 4:
        data.append(0)
    offset = len(data)
    data.extend(b"".join(headers))
    data[:16] = b"\x7fELF\1\1\1" + bytes(9)
    struct.pack_into("<HHIIIIIHHHHHH", data, 16, 1, 40, 1, 0, 0, offset,
                     0x05000000, 52, 0, 0, 40, 5, 1)
    return bytes(data)


class RecordTests(unittest.TestCase):
    def test_strict_order_bounds_and_eof(self):
        valid = record(1, b"abc") + record(2, b"de")
        self.assertEqual(app.parse_records(valid, (1, 2))[1]["value"], b"de")
        for bad in (valid[:-1], valid + b"x", valid[11:] + valid[:11], b"", record(1, b"abc")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                app.parse_records(bad, (1, 2))

    def test_interface_checks_real_payload_relationships(self):
        result = app.inspect_interface(synthetic_interface())
        self.assertTrue(result["variable_allocation_payload_equal"])
        self.assertEqual(result["instruction_index_nonempty"], [{"index": 1, "value": 0}, {"index": 2, "value": 1}])
        self.assertIsNone(result["task_period_us"])
        self.assertFalse(result["runtime_instruction_to_entry_semantics_verified"])
        self.assertEqual(app.assemble_fixed_userprog(synthetic_interface()),
                         synthetic_interface()["Temp/userprog.foid"])

    def test_mismatched_payloads_and_index_headers_are_rejected(self):
        for key in ("Output.prg", "VarsInfo.vi", "ILIndex.idx", "Temp/symbol.sym"):
            data = synthetic_interface()
            data[key] = b"different"
            with self.subTest(key=key), self.assertRaises(ValueError):
                app.inspect_interface(data)

    def test_distinct_identifiers_cannot_be_combined(self):
        data = synthetic_interface()
        data["Temp/varsinfo.foid"] = data["Temp/varsinfo.foid"].replace(b"01234567", b"11234567", 1)
        with self.assertRaisesRegex(ValueError, "identifiers"):
            app.inspect_interface(data)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.template = self.root / "template"
        self.template.mkdir()
        self.files = synthetic_interface()
        self.pins = {name: app.sha(raw) for name, raw in self.files.items()}
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(app, "PINNED_HASHES", self.pins).start()
        for name, raw in self.files.items():
            p = self.template / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        self.source = self.root / "source.st"
        self.source.write_text("g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0;\n")
        self.objects = [self.root / name for name in ("stuserprog.o", "stprog.o")]
        for path in self.objects:
            path.write_bytes(synthetic_object())
        self.build = {"schema": "plcidf.native-bound-application.v1",
            "profile": "bess-easy521-existing-pcmd-v1", "ok": True,
            "source_compilation_verified": True, "existing_allocation_verified": True,
            "source_to_bound_objects_verified": True,
            "source_chain": {"source_sha256": app.sha(self.source.read_bytes())},
            "inputs": [{"path": str(self.template / name), "sha256": app.sha(raw), "bytes": len(raw)}
                       for name, raw in self.files.items()],
            "outputs": [{"path": str(p), "sha256": app.sha(p.read_bytes()), "bytes": p.stat().st_size}
                        for p in self.objects]}
        self.report = self.root / "build-report.json"
        self.save_report()
        self.output = self.root / "candidate"

    def save_report(self):
        self.report.write_text(json.dumps(self.build))

    def prepare(self):
        return app.prepare_application(template_dir=self.template, source=self.source,
            stuserprog=self.objects[0], stprog=self.objects[1], build_report=self.report,
            output_dir=self.output)

    def test_complete_explicit_candidate_with_vendor_empty_marker(self):
        report = self.prepare()
        self.assertEqual(report["file_count"], 18)
        self.assertEqual([x["remote_name"] for x in report["files"]][:6],
                         ["project.hcpp", "userprog.foid", "stprog.o", "stuserprog.o", "symbol.sym", "varsinfo.foid"])
        self.assertEqual((self.output / "files/project.hcpp").read_bytes(), b"Empty")
        self.assertEqual((self.output / "inputs/source.st").read_bytes(), self.source.read_bytes())
        self.assertEqual((self.output / "files/userprog.foid").read_bytes(), self.files["Temp/userprog.foid"])
        self.assertTrue(report["candidate_file_set_complete"])
        for name in ("downloadable", "runtime_abi_verified", "device_executed", "device_accepted",
                     "source_compilation_independently_verified", "template_same_original_vendor_build_verified"):
            self.assertFalse(report[name])
        self.assertTrue((self.output / "application-candidate.json").is_file())

    def test_any_template_drift_is_rejected_before_output_creation(self):
        for name in self.files:
            p = self.template / name
            old = p.read_bytes()
            p.write_bytes(old + b"drift")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "drift"):
                self.prepare()
            self.assertFalse(self.output.exists())
            p.write_bytes(old)

    def test_source_chain_or_status_false_is_rejected(self):
        original = copy.deepcopy(self.build)
        for key in ("ok", "source_compilation_verified", "existing_allocation_verified", "source_to_bound_objects_verified"):
            self.build = copy.deepcopy(original)
            self.build[key] = False
            self.save_report()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.prepare()
        self.build = original
        self.source.write_text("g_rBESS_Pcmd := g_rBESS_Pcmd + 2.0;")
        self.save_report()
        with self.assertRaisesRegex(ValueError, "source chain"):
            self.prepare()

    def test_report_cannot_associate_different_interface_or_output(self):
        original = copy.deepcopy(self.build)
        for category in ("inputs", "outputs"):
            self.build = copy.deepcopy(original)
            self.build[category][0]["sha256"] = "0" * 64
            self.save_report()
            with self.subTest(category=category), self.assertRaises(ValueError):
                self.prepare()

    def test_malformed_report_shapes_are_rejected(self):
        original = copy.deepcopy(self.build)
        for key, value in (("source_chain", []), ("outputs", [{"path": 42}, {}]), ("inputs", {})):
            self.build = copy.deepcopy(original)
            self.build[key] = value
            self.save_report()
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.prepare()

    def test_duplicate_interface_records_are_rejected(self):
        self.build["inputs"].append(self.build["inputs"][0])
        self.save_report()
        with self.assertRaisesRegex(ValueError, "interface differs"):
            self.prepare()

    def test_missing_arm_entry_or_foreign_elf_is_rejected(self):
        for raw in (b"not an ELF", synthetic_object().replace(b"MAIN", b"FAIL")):
            self.objects[0].write_bytes(raw)
            with self.subTest(raw=raw[:8]), self.assertRaises(ValueError):
                self.prepare()

    def test_symlink_fifo_and_existing_output_are_rejected(self):
        original = self.source.read_bytes()
        self.source.unlink()
        self.source.symlink_to(self.report)
        with self.assertRaises(ValueError):
            self.prepare()
        self.source.unlink()
        import os
        os.mkfifo(self.source)
        with self.assertRaises(ValueError):
            self.prepare()
        self.source.unlink()
        self.source.write_bytes(original)
        self.output.mkdir()
        (self.output / "keep").write_text("keep")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual((self.output / "keep").read_text(), "keep")

    def test_output_inside_template_is_rejected(self):
        self.output = self.template / "new"
        with self.assertRaisesRegex(ValueError, "overlap"):
            self.prepare()

    def test_input_mutation_after_output_is_detected_and_evidence_retained(self):
        original = app._read
        changed = False
        def read(path, budget):
            nonlocal changed
            if self.output.exists() and not changed:
                changed = True
                self.source.write_bytes(b"changed")
            return original(path, budget)
        with mock.patch.object(app, "_read", read), self.assertRaisesRegex(ValueError, "input drift"):
            self.prepare()
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / "application-candidate.json").exists())

    def test_budget_enforced_before_new_output(self):
        with mock.patch.object(app, "MAX_TOTAL_BYTES", 32), self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
