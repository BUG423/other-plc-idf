"""Offline link safety tests, plus opt-in real Linux LLD with synthetic ARM ELF.

No proprietary binaries, compiler, Windows processes, sockets, or PLC are used.
Set PLCIDF_TEST_LLD to a local Linux ld.lld to exercise real object linking.
"""

import importlib.util
import json
import os
import stat
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "vendor_link", Path(__file__).resolve().parents[1] / "link_vendor_application.py")
link = importlib.util.module_from_spec(spec)
spec.loader.exec_module(link)


def object_fixture(symbol="entry", runtime="runtime_missing"):
    """A real, minimal ELF32 ARM REL object with a symbolic ABS32 relocation."""
    names = b"\0.shstrtab\0.text\0.strtab\0.symtab\0.rel.text\0"
    strings = b"\0" + symbol.encode() + b"\0" + runtime.encode() + b"\0"
    symbols = (bytes(16) + struct.pack("<IIIBBH", 1, 0, 4, 0x11, 0, 2)
               + struct.pack("<IIIBBH", len(symbol) + 2, 0, 0, 0x10, 0, 0))
    payloads = [names, bytes(4), strings, symbols, struct.pack("<II", 0, (2 << 8) | 2)]
    data = bytearray(52)
    headers = [bytes(40)]
    for i, (name, payload) in enumerate(zip(
            [".shstrtab", ".text", ".strtab", ".symtab", ".rel.text"], payloads), 1):
        while len(data) % 4:
            data += b"\0"
        offset = len(data)
        data += payload
        headers.append(struct.pack("<IIIIIIIIII", names.index(name.encode()),
                                   [3, 1, 3, 2, 9][i - 1], 6 if i == 2 else 0,
                                   0, offset, len(payload), 3 if i == 4 else 4 if i == 5 else 0,
                                   1 if i == 4 else 2 if i == 5 else 0, 4,
                                   16 if i == 4 else 8 if i == 5 else 0))
    while len(data) % 4:
        data += b"\0"
    shoff = len(data)
    data += b"".join(headers)
    data[:16] = b"\x7fELF\x01\x01\x01" + bytes(9)
    struct.pack_into("<HHIIIIIHHHHHH", data, 16, 1, 40, 1, 0, 0, shoff,
                     0x05000000, 52, 0, 0, 40, len(headers), 1)
    return bytes(data)


def host_fixture():
    """Parseable ELF64 host header, only used with an injected fake runner."""
    data = bytearray(b"\x7fELF\x02\x01\x01" + bytes(9) + bytes(48))
    struct.pack_into("<HHIQQQIHHHHHH", data, 16, 3, 62, 1, 0, 0, 0,
                     0, 64, 0, 0, 0, 0, 0)
    return bytes(data)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / "source"
        self.sources.mkdir()
        self.pous = [self.sources / "MAIN.o", self.sources / "SBR_001.o"]
        self.syslib = self.sources / "_SysLib.o"
        self.stclib = self.sources / "_STCLib.o"
        for i, path in enumerate([*self.pous, self.syslib, self.stclib]):
            path.write_bytes(object_fixture(f"entry_{i}"))
        self.lld = self.root / "ld.lld"
        self.lld.write_bytes(host_fixture())
        self.lld.chmod(0o700)
        self.out = self.root / "new output with spaces"
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, "LLD test fixture\n", "")
        Path(argv[-1]).write_bytes(object_fixture("linked_entry"))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def run_link(self, **kwargs):
        arguments = dict(pous=self.pous, syslib=self.syslib, stclib=self.stclib,
                         lld=self.lld, output_dir=self.out, runner=self.runner)
        arguments.update(kwargs)
        return link.link_application(**arguments)

    def test_ordered_two_stage_links_use_only_frozen_copies(self):
        before = {p: p.read_bytes() for p in [*self.pous, self.syslib, self.stclib]}
        result = self.run_link()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "linked")
        self.assertFalse(result["downloadable"])
        self.assertFalse(result["st_compiled"])
        first, second = self.calls[1][0], self.calls[2][0]
        frozen = [r["frozen"] for r in result["inputs"]]
        self.assertEqual(first, [str(self.lld), "-relocatable", *frozen[:-1],
                                 "-o", str(self.out / "stuserprog.o")])
        self.assertEqual(second, [str(self.lld), "-relocatable", str(self.out / "stuserprog.o"),
                                  frozen[-1], "-o", str(self.out / "stprog.o")])
        for _, kwargs in self.calls:
            self.assertEqual(kwargs["timeout"], 10)
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        for record in result["inputs"]:
            self.assertEqual(stat.S_IMODE(Path(record["frozen"]).stat().st_mode), 0o444)
        saved = json.loads((self.out / "link-report.json").read_text())
        self.assertEqual(saved, result)
        self.assertEqual(result["outputs"][1]["inspection"]["elf"]["undefined_global_weak"]["names"],
                         ["runtime_missing"])

    def test_existing_output_cannot_be_reused_or_overwritten(self):
        self.out.mkdir()
        marker = self.out / "keep"
        marker.write_text("evidence")
        with self.assertRaises(ValueError):
            self.run_link()
        self.assertEqual(marker.read_text(), "evidence")
        self.assertEqual(self.calls, [])

    def test_unknown_truncated_wrong_architecture_or_executable_input_is_rejected(self):
        for malformed in [b"MZ-not-an-object", object_fixture()[:40]]:
            self.pous[0].write_bytes(malformed)
            with self.assertRaises(ValueError):
                self.run_link()
        for field, value in [(16, 2), (18, 3)]:
            malformed = bytearray(object_fixture())
            struct.pack_into("<H", malformed, field, value)
            self.pous[0].write_bytes(malformed)
            with self.assertRaises(ValueError):
                self.run_link()
        self.assertFalse(self.out.exists())
        self.assertEqual(self.calls, [])

    def test_vendor_pe_linker_is_rejected_without_execution(self):
        self.lld.write_bytes(b"MZ-not-a-Linux-linker")
        with self.assertRaises(ValueError):
            self.run_link()
        self.assertFalse(self.out.exists())
        self.assertEqual(self.calls, [])

    def test_duplicate_and_symlink_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            self.run_link(pous=[self.pous[0], self.pous[0]])
        alias = self.sources / "alias.o"
        alias.symlink_to(self.pous[0])
        with self.assertRaises(OSError):
            self.run_link(pous=[alias])
        self.assertEqual(self.calls, [])

    def test_output_cannot_be_inside_original_input_tree(self):
        with self.assertRaises(ValueError):
            self.run_link(output_dir=self.sources / "do-not-create")
        self.assertEqual(self.calls, [])

    def test_timeout_is_not_retried_and_partial_output_is_not_success(self):
        def timeout(argv, **kwargs):
            if "--version" in argv:
                return self.runner(argv, **kwargs)
            self.calls.append((argv, kwargs))
            Path(argv[-1]).write_bytes(object_fixture())
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        result = self.run_link(runner=timeout)
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result["commands"][-1]["status"], "timeout")
        self.assertEqual(result["outputs"], [])
        self.assertFalse((self.out / "stprog.o").exists())

    def test_failed_second_stage_retains_failed_evidence(self):
        def failed(argv, **kwargs):
            if argv[-1].endswith("/stprog.o"):
                self.calls.append((argv, kwargs))
                Path(argv[-1]).write_bytes(object_fixture())
                return subprocess.CompletedProcess(argv, 1, "", "deliberate failure")
            return self.runner(argv, **kwargs)
        result = self.run_link(runner=failed)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["outputs"]), 1)
        self.assertEqual(result["commands"][-1]["returncode"], 1)
        self.assertEqual(len(self.calls), 3)

    def test_zero_exit_without_valid_output_is_failure(self):
        def bad_output(argv, **kwargs):
            if "--version" not in argv:
                self.calls.append((argv, kwargs))
                Path(argv[-1]).write_bytes(b"not an object")
                return subprocess.CompletedProcess(argv, 0, "", "")
            return self.runner(argv, **kwargs)
        result = self.run_link(runner=bad_output)
        self.assertFalse(result["ok"])
        self.assertIn("not a valid", result["error"])
        self.assertEqual(len(self.calls), 2)

    def test_mutation_of_original_or_stage_one_cannot_be_reported_linked(self):
        for mutation in ["source", "stage1", "frozen", "linker"]:
            with self.subTest(mutation=mutation):
                output = self.root / f"mutation-{mutation}"
                def mutate(argv, **kwargs):
                    result = self.runner(argv, **kwargs)
                    if argv[-1].endswith("/stprog.o"):
                        path = {"source": self.pous[0], "stage1": output / "stuserprog.o",
                                "frozen": output / "inputs/pou-000.o", "linker": self.lld}[mutation]
                        path.chmod(0o600)
                        path.write_bytes(path.read_bytes() + b"changed")
                    return result
                result = self.run_link(output_dir=output, runner=mutate)
                self.assertFalse(result["ok"])
                self.assertIn("changed", result["error"])
                self.pous[0].write_bytes(object_fixture("entry_0"))
                self.lld.write_bytes(host_fixture())
                self.lld.chmod(0o700)

    def test_timeout_validation_and_empty_pou_list(self):
        for timeout in [0, -1, float("nan"), float("inf"), True, 301]:
            with self.assertRaises(ValueError):
                self.run_link(timeout_s=timeout)
        with self.assertRaises(ValueError):
            self.run_link(pous=[])
        self.assertEqual(self.calls, [])

    def test_aggregate_budget_stops_before_reading_later_objects(self):
        one_size = self.pous[0].stat().st_size
        with patch.object(link, "MAX_TOTAL_BYTES", 2 * one_size - 1):
            with patch.object(link, "_read_regular", wraps=link._read_regular) as read:
                with self.assertRaises(ValueError):
                    self.run_link()
        self.assertEqual(read.call_count, 2)
        self.assertEqual(read.call_args.kwargs["limit"], one_size - 1)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.calls, [])

    @unittest.skipUnless(os.environ.get("PLCIDF_TEST_LLD"), "set PLCIDF_TEST_LLD for native LLD test")
    def test_real_linux_lld_links_synthetic_arm_objects_and_retains_runtime_dependency(self):
        result = self.run_link(lld=Path(os.environ["PLCIDF_TEST_LLD"]), runner=subprocess.run)
        self.assertTrue(result["ok"], result.get("error"))
        final = result["outputs"][-1]["inspection"]["elf"]
        self.assertEqual(final["machine"], "ARM")
        self.assertEqual(final["type"], "REL")
        self.assertEqual(final["undefined_global_weak"]["names"], ["runtime_missing"])
        self.assertEqual(final["relocations"]["entries"], 4)
        self.assertFalse(result["downloadable"])

    @unittest.skipUnless(os.environ.get("PLCIDF_TEST_LLD"), "set PLCIDF_TEST_LLD for native LLD test")
    def test_real_linux_lld_duplicate_symbol_is_a_recorded_failure(self):
        # Distinct input paths can still contain conflicting strong definitions.
        # This exercises the real linker error, rather than manufacturing exit 1.
        self.pous[1].write_bytes(self.pous[0].read_bytes())
        result = self.run_link(lld=Path(os.environ["PLCIDF_TEST_LLD"]), runner=subprocess.run)
        self.assertFalse(result["ok"])
        self.assertFalse(result["downloadable"])
        self.assertEqual(len(result["commands"]), 2)  # version + stage 1, no retry
        self.assertNotEqual(result["commands"][-1]["returncode"], 0)
        self.assertIn("duplicate symbol", result["commands"][-1]["stderr"])
        self.assertFalse((self.out / "stprog.o").exists())


if __name__ == "__main__":
    unittest.main()
