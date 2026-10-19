"""Pure offline synthetic ELF/GVT tests; no vendor binaries or device access.

The GVT preamble is synthetic and its hash is patched only in tests. Addresses
are deliberately different from the recorded real sample, detecting accidental
hardcoding. Inputs, association claims and linker reports are local fixtures,
not evidence of source compilation or PLC compatibility.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "bind_globals_tool", Path(__file__).resolve().parents[1] / "bind_vendor_globals.py")
bind = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bind)
PREAMBLE = b"SYNTHETIC binding fixture; not a vendor archive".ljust(bind._variables.PREAMBLE_BYTES, b"\0")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def gvt_fixture(records):
    def string(text):
        raw = text.encode("ascii")
        return bytes([len(raw)]) + raw
    data = PREAMBLE + struct.pack("<I", len(records))
    for name, kind, address in records:
        data += (struct.pack("<II", kind, kind) + string(name) + string("0")
                 + b"\1\1\0\0" + struct.pack("<I", address) + string("fixture")
                 + b"\xff\xfe\xff\0" + bytes(8))
    return data


def object_fixture(names=None, runtime="runtime_missing"):
    names = list(names if names is not None else [row[1] for row in bind.SYMBOL_MAP])
    shstrings = b"\0.shstrtab\0.text\0.strtab\0.symtab\0.rel.text\0.bss\0"
    strings = bytearray(b"\0entry\0")
    symbols = bytes(16) + struct.pack("<IIIBBH", 1, 0, 4, 0x12, 0, 2)
    for name in [*names, runtime]:
        offset = len(strings)
        strings += name.encode() + b"\0"
        symbols += struct.pack("<IIIBBH", offset, 0, 0, 0x10, 0, 0)
    relocations = b"".join(struct.pack("<II", i * 4, ((i + 2) << 8) | 2)
                           for i in range(len(names) + 1))
    text = bytes(range((len(names) + 1) * 4))
    payloads = [shstrings, text, bytes(strings), symbols, relocations, b""]
    data, headers = bytearray(52), [bytes(40)]
    for i, (name, payload) in enumerate(zip(
            [".shstrtab", ".text", ".strtab", ".symtab", ".rel.text", ".bss"], payloads), 1):
        while len(data) % 4:
            data += b"\0"
        offset = len(data)
        data += payload
        headers.append(struct.pack("<IIIIIIIIII", shstrings.index(name.encode()),
                                   [3, 1, 3, 2, 9, 8][i - 1], 6 if i == 2 else 0,
                                   0, offset, 64 if i == 6 else len(payload),
                                   3 if i == 4 else 4 if i == 5 else 0,
                                   1 if i == 4 else 2 if i == 5 else 0, 4,
                                   16 if i == 4 else 8 if i == 5 else 0))
    while len(data) % 4:
        data += b"\0"
    shoff = len(data)
    data += b"".join(headers)
    data[:16] = b"\x7fELF\1\1\1" + bytes(9)
    struct.pack_into("<HHIIIIIHHHHHH", data, 16, 1, 40, 1, 0, 0, shoff,
                     0x05000000, 52, 0, 0, 40, len(headers), 1)
    return bytes(data)


def symbol_offset(data, number=2):
    shoff = struct.unpack_from("<I", data, 32)[0]
    table = struct.unpack_from("<IIIIIIIIII", data, shoff + 4 * 40)
    return table[4] + 16 * number


class BindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        pin = patch.object(bind._variables, "PREAMBLE_SHA256", sha(PREAMBLE))
        pin.start()
        self.addCleanup(pin.stop)
        self.source = self.root / "MAIN body.st"
        self.source.write_text("(* synthetic source declaration, not compiled *)\n")
        self.records = [(name, 4 if kind == "INT" else 32, 0x02100200 + i * 0x30)
                        for i, (name, _, kind) in enumerate(bind.SYMBOL_MAP)]
        self.gvt = self.root / "variables.gvt"
        self.gvt.write_bytes(gvt_fixture(self.records))
        self.csv = self.root / "addresses.csv"
        self.write_csv()
        self.link_path = self.root / "link-report.json"
        self.manifest_path = self.root / "association.json"
        self.out = self.root / "new output directory"
        self.originals, self.frozen = [], []
        for role in ("pou-000", "syslib", "stclib"):
            original, frozen = self.root / (role + ".o"), self.root / (role + "-copy.o")
            original.write_bytes(object_fixture())
            frozen.write_bytes(original.read_bytes())
            self.originals.append(original)
            self.frozen.append(frozen)
        self.stage_paths = [self.root / "stuserprog.o", self.root / "stprog.o"]
        self.stage_paths[0].write_bytes(object_fixture())
        self.stage_paths[1].write_bytes(object_fixture(runtime="second_stage_runtime"))
        self.link = {"schema": "plcidf.vendor-relocatable-link.v1", "ok": True, "status": "linked",
                     "scope": "two-stage-relocatable-object-link", "downloadable": False,
                     "st_compiled": False, "runtime_verified": False,
                     "linker": {"path": "/synthetic/ld.lld"},
                     "inputs": [{"role": role, "original": str(original), "frozen": str(frozen),
                                 "inspection": {"sha256": sha(original.read_bytes())}}
                                for role, original, frozen in zip(("pou-000", "syslib", "stclib"), self.originals, self.frozen)],
                     "outputs": []}
        self.refresh_outputs()

    def write_csv(self, records=None):
        records = self.records if records is None else records
        self.csv.write_bytes("".join(f"{name}\t{hex(address)}\tfixture\t{16 if kind == 4 else 32}\n"
                                     for name, kind, address in records).encode("utf-16"))

    def refresh_outputs(self):
        self.link["outputs"] = [{"stage": i, "path": str(path),
                                 "inspection": {"sha256": sha(path.read_bytes())}}
                                for i, path in enumerate(self.stage_paths, 1)]
        self.link["commands"] = [{"argv": argv, "attempt": 1, "returncode": 0, "timeout_s": 10}
                                 for argv in [["/synthetic/ld.lld", "--version"],
                                              ["/synthetic/ld.lld", "-relocatable", *map(str, self.frozen[:-1]), "-o", str(self.stage_paths[0])],
                                              ["/synthetic/ld.lld", "-relocatable", str(self.stage_paths[0]), str(self.frozen[-1]), "-o", str(self.stage_paths[1])]]]
        self.seal()

    def seal(self):
        self.link_path.write_bytes(bind._canonical(self.link))
        self.manifest = {"schema": "plcidf.global-binding-inputs.v1", "profile": bind.PROFILE,
                         "source_sha256": sha(self.source.read_bytes()), "gvt_sha256": sha(self.gvt.read_bytes()),
                         "address_csv_sha256": sha(self.csv.read_bytes()), "link_report_sha256": sha(self.link_path.read_bytes()),
                         "link_inputs": [{"role": r["role"], "sha256": r["inspection"]["sha256"]}
                                         for r in self.link["inputs"]]}
        self.write_manifest()

    def write_manifest(self):
        self.manifest.pop("manifest_hash", None)
        self.manifest["manifest_hash"] = sha(bind._canonical(self.manifest))
        self.manifest_path.write_bytes(bind._canonical(self.manifest))

    def run_binding(self, **kwargs):
        arguments = dict(source=self.source, gvt=self.gvt, address_csv=self.csv,
                         link_report=self.link_path, association_manifest=self.manifest_path,
                         output_dir=self.out)
        arguments.update(kwargs)
        return bind.bind_globals(**arguments)

    def assert_refused(self, message=None):
        with self.assertRaisesRegex(ValueError, message or ".*"):
            self.run_binding()
        self.assertFalse(self.out.exists())

    def test_success_changes_only_fixed_fields_and_preserves_all_other_bytes(self):
        paths = [self.source, self.gvt, self.csv, self.link_path, self.manifest_path,
                 *self.originals, *self.frozen, *self.stage_paths]
        before = {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in paths}
        result = self.run_binding()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "existing-symbol-bindings-applied")
        self.assertFalse(result["downloadable"])
        self.assertFalse(result["source_compilation_verified"])
        self.assertFalse(result["runtime_abi_verified"])
        self.assertEqual(result["association_verified"], "hash-only-caller-declared-batch")
        self.assertEqual(len(result["outputs"]), 2)
        for original, output in zip(self.stage_paths, result["outputs"]):
            old, new = before[original][0], Path(output["path"]).read_bytes()
            self.assertEqual(len(old), len(new))
            allowed = set()
            for i, row in enumerate(output["bindings"]):
                offset = row["symbol_entry_offset"]
                allowed.update([*range(offset + 4, offset + 8), offset + 12])
                self.assertEqual(struct.unpack_from("<I", new, offset + 4)[0], self.records[i][2])
                self.assertEqual(new[offset + 12], 0x18)
                self.assertEqual(struct.unpack_from("<H", new, offset + 14)[0], 0)
            self.assertTrue(all(a == b for i, (a, b) in enumerate(zip(old, new)) if i not in allowed))
            self.assertEqual(output["remaining_undefined_names"],
                             ["runtime_missing" if original == self.stage_paths[0] else "second_stage_runtime"])
            actual_differences = [{"offset": i, "before": a, "after": b}
                                  for i, (a, b) in enumerate(zip(old, new)) if a != b]
            self.assertEqual(actual_differences, output["changed_bytes"])
            self.assertEqual(stat.S_IMODE(Path(output["path"]).stat().st_mode), 0o444)
        self.assertEqual({p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in paths}, before)
        self.assertEqual(json.loads((self.out / "binding-report.json").read_text()), result)

    def test_missing_or_duplicate_target_symbols_are_rejected(self):
        names = [row[1] for row in bind.SYMBOL_MAP]
        for malformed in (names[:-1], names + [names[0]]):
            with self.subTest(names=malformed):
                self.stage_paths[1].write_bytes(object_fixture(malformed))
                self.refresh_outputs()
                self.assert_refused("missing or duplicate")

    def test_nonzero_value_size_type_visibility_and_non_und_rejected(self):
        for relative, fmt, value in [(4, "I", 1), (8, "I", 4), (12, "B", 0x18),
                                     (12, "B", 0x20), (13, "B", 1), (14, "H", 2)]:
            with self.subTest(field=relative, value=value):
                data = bytearray(object_fixture())
                struct.pack_into("<" + fmt, data, symbol_offset(data) + relative, value)
                self.stage_paths[0].write_bytes(data)
                self.refresh_outputs()
                self.assert_refused("unexpected")

    def test_pe_truncation_wrong_architecture_endian_or_executable_rejected(self):
        bad = [b"MZ-not-an-object", object_fixture()[:35]]
        for offset, value in [(4, 2), (5, 2), (16, 2), (18, 3)]:
            data = bytearray(object_fixture());data[offset] = value;bad.append(bytes(data))
        for data in bad:
            with self.subTest(length=len(data), prefix=data[:6]):
                self.stage_paths[0].write_bytes(data)
                self.refresh_outputs()
                self.assert_refused()

    def test_overlapping_symtab_and_text_is_rejected(self):
        data = bytearray(object_fixture())
        shoff = struct.unpack_from("<I", data, 32)[0]
        # Alias text onto symbol bytes while keeping both individually in bounds.
        struct.pack_into("<I", data, shoff + 2 * 40 + 16, symbol_offset(data, 0))
        self.stage_paths[0].write_bytes(data)
        self.refresh_outputs()
        self.assert_refused("overlapping")

    def test_bad_symbol_stride_or_second_symbol_table_rejected(self):
        for mutate in ("stride", "second"):
            data = bytearray(object_fixture());shoff = struct.unpack_from("<I", data, 32)[0]
            if mutate == "stride":struct.pack_into("<I", data, shoff + 4 * 40 + 36, 32)
            else:struct.pack_into("<I", data, shoff + 6 * 40 + 4, 2)
            self.stage_paths[0].write_bytes(data);self.refresh_outputs();self.assert_refused()

    def test_gvt_missing_wrong_type_case_and_csv_conflicts_are_rejected(self):
        variants = [self.records[:-1], [("G_IBESS_MODE", 4, self.records[0][2]), *self.records[1:]],
                    [(self.records[0][0], 32, self.records[0][2]), *self.records[1:]]]
        for records in variants:
            self.gvt.write_bytes(gvt_fixture(records));self.write_csv(records);self.seal()
            self.assert_refused("fixed global")
        self.gvt.write_bytes(gvt_fixture(self.records))
        conflict = [(self.records[0][0], 4, 0x02100500), *self.records[1:]]
        self.write_csv(conflict);self.seal();self.assert_refused("disagrees")

    def test_gvt_preamble_duplicates_and_out_of_profile_address_rejected(self):
        bad = [b"X" + gvt_fixture(self.records)[1:],
               gvt_fixture([self.records[0], self.records[0], *self.records[1:]]),
               gvt_fixture([(self.records[0][0], 4, 0), *self.records[1:]])]
        for data in bad:
            self.gvt.write_bytes(data);self.seal();self.assert_refused()

    def test_source_gvt_csv_link_record_and_manifest_hash_drift_rejected(self):
        for path in [self.source, self.gvt, self.csv, self.link_path]:
            old = path.read_bytes();path.write_bytes(old + b" ")
            self.assert_refused("association hash")
            path.write_bytes(old)
        self.manifest["source_sha256"] = "0" * 64
        self.manifest_path.write_bytes(bind._canonical(self.manifest))
        self.assert_refused("manifest integrity")

    def test_original_frozen_and_linked_artifact_drift_rejected(self):
        for path in [self.originals[0], self.frozen[1], self.stage_paths[1]]:
            old = path.read_bytes();path.write_bytes(old + b" ")
            self.assert_refused("hash mismatch")
            path.write_bytes(old)

    def test_manifest_missing_reordered_roles_and_report_failed_rejected(self):
        original = json.loads(self.manifest_path.read_bytes())
        for change in ("missing", "reordered", "boolhash"):
            self.manifest = json.loads(json.dumps(original))
            if change == "missing":self.manifest["link_inputs"].pop()
            elif change == "reordered":self.manifest["link_inputs"].reverse()
            else:self.manifest["link_inputs"][0]["sha256"] = True
            self.write_manifest();self.assert_refused()
        self.link["ok"] = "true";self.seal();self.assert_refused("not a successful")

    def test_existing_directory_and_symlink_output_never_overwritten(self):
        self.out.mkdir();marker = self.out / "keep";marker.write_text("old evidence")
        with self.assertRaisesRegex(ValueError, "must be new"):self.run_binding()
        self.assertEqual(marker.read_text(), "old evidence")
        self.assertEqual(list(self.out.iterdir()), [marker])
        alias = self.root / "out-link";alias.symlink_to(self.out)
        with self.assertRaises(ValueError):self.run_binding(output_dir=alias)

    def test_symlink_fifo_and_aggregate_budget_rejected_before_creation(self):
        alias = self.root / "source-alias.st";alias.symlink_to(self.source)
        with self.assertRaises(OSError):self.run_binding(source=alias)
        fifo = self.root / "fifo";os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "regular"):self.run_binding(source=fifo)
        with patch.object(bind, "MAX_TOTAL", 20), self.assertRaises(ValueError):self.run_binding()
        self.assertFalse(self.out.exists())

    def test_json_duplicate_keys_and_nonfinite_values_rejected(self):
        for raw in (b'{"schema":1,"schema":2}', b'{"schema":NaN}'):
            self.manifest_path.write_bytes(raw);self.assert_refused()

    def test_failed_replayed_or_misordered_link_commands_are_rejected(self):
        for key, value in [("returncode", 1), ("returncode", False), ("attempt", 2),
                           ("status", "timeout"), ("argv", ["unexpected", "command"])]:
            self.refresh_outputs()
            self.link["commands"][1][key] = value;self.seal()
            self.assert_refused("link commands")

    def test_inspection_shape_and_duplicate_original_or_frozen_paths_rejected(self):
        self.link["outputs"][0]["inspection"] = "bad inspection"
        self.seal();self.assert_refused("inspection")
        self.refresh_outputs()
        self.link["inputs"][0]["frozen"] = self.link["inputs"][0]["original"]
        self.seal();self.assert_refused("must be distinct")

    def test_final_input_drift_preserves_failure_report_without_success(self):
        original_read = bind._read
        def mutate_after_create(path, limit):
            result = original_read(path, limit)
            if Path(path) == self.out / "stuserprog.o":
                self.source.write_bytes(b"changed during output")
            return result
        with patch.object(bind, "_read", side_effect=mutate_after_create):result = self.run_binding()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["downloadable"])
        self.assertIn("input changed", result["error"])
        self.assertEqual(json.loads((self.out / "binding-report.json").read_bytes()), result)


if __name__ == "__main__":
    unittest.main()
