"""Synthetic envelope tests; no vendor bytes, Windows, USB, or PLC are used."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "inspect_easy521_foid", Path(__file__).parents[1] / "inspect_easy521_foid.py")
foid = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(foid)


def envelope(payload=b"synthetic configuration", *, metadata=None, caller=0):
    # Deliberately synthetic tags/values, based only on the published observed
    # layout. No captured vendor payload or opaque metadata is redistributed.
    if metadata is None:
        metadata = bytes(range(32))
    return (struct.pack("<II", 0x100, 4) + b"foid"
            + struct.pack("<II", 0x200, 32) + metadata
            + struct.pack("<III", 0x201, 4, caller) + payload)


class EnvelopeTests(unittest.TestCase):
    def test_parses_structure_and_hashes_without_authentication_claim(self):
        data = envelope(caller=1)
        result = foid.inspect_foid(data)
        self.assertEqual(result["header"]["records"][2]["caller_dword"], 1)
        self.assertEqual(result["header"]["records"][1]["value_sha256"],
                         hashlib.sha256(bytes(range(32))).hexdigest())
        self.assertEqual(result["payload"]["sha256"], hashlib.sha256(data[64:]).hexdigest())
        self.assertFalse(result["payload"]["semantics_verified"])
        for key in ("complete_application", "downloadable", "signature_verified"):
            self.assertFalse(result[key])
        self.assertNotIn("payload_hex", json.dumps(result))

    def test_every_truncated_header_is_rejected(self):
        for size in range(64):
            with self.subTest(size=size), self.assertRaises(foid.FormatError):
                foid.inspect_foid(envelope()[:size])

    def test_foreign_magic_or_layout_is_rejected(self):
        for offset in (0, 4, 8, 12, 16, 52, 56):
            data = bytearray(envelope())
            data[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(foid.FormatError):
                foid.inspect_foid(bytes(data))
        data = b"".join(envelope()[i:i + 4][::-1] for i in range(0, 64, 4))
        with self.assertRaises(foid.FormatError):
            foid.inspect_foid(data)

    def test_opaque_metadata_is_never_required_to_be_zero_or_a_checksum(self):
        a = foid.inspect_foid(envelope(metadata=b"\0" * 32))
        b = foid.inspect_foid(envelope(metadata=b"x" * 32))
        self.assertNotEqual(a["header"]["sha256"], b["header"]["sha256"])
        self.assertEqual(a["payload"], b["payload"])
        self.assertFalse(b["signature_verified"])

    def test_caller_dword_is_not_constrained_to_current_sample_defaults(self):
        self.assertEqual(foid.inspect_foid(envelope(caller=0xffffffff))
                         ["header"]["records"][2]["caller_dword"], 0xffffffff)

    def test_empty_payload_is_only_a_structural_result(self):
        result = foid.inspect_foid(envelope(b""))
        self.assertEqual(result["payload"]["size"], 0)
        self.assertFalse(result["downloadable"])

    def test_api_limits_and_types(self):
        with self.assertRaises(TypeError):
            foid.inspect_foid("not bytes")
        with mock.patch.object(foid, "MAX_FILE_BYTES", 63), self.assertRaises(ValueError):
            foid.inspect_foid(envelope())


class PathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input.foid"
        self.input.write_bytes(envelope())

    def test_explicit_source_comparison_distinguishes_exact_prefix_and_other(self):
        source = self.root / "config.dat"
        payload = envelope()[64:]
        for value, exact, prefix in ((payload, True, True),
                                     (payload + b"editor tail", False, True),
                                     (b"other source", False, False)):
            source.write_bytes(value)
            result = foid.inspect_paths([], source_pairs=[(self.input, source)])
            self.assertTrue(result["inspection_ok"])
            comparison = result["source_comparisons"][0]
            self.assertEqual(comparison["payload_equals_source"], exact)
            self.assertEqual(comparison["payload_is_source_prefix"], prefix)
            self.assertFalse(comparison["payload_semantics_verified"])

    def test_same_name_does_not_select_or_validate_payload(self):
        foreign = self.root / "syscfg.foid"
        foreign.write_bytes(b"foreign format")
        result = foid.inspect_paths([foreign])
        self.assertFalse(result["inspection_ok"])
        self.assertEqual(result["files"][0]["inspection_status"], "error")

    def test_symlink_directory_and_fifo_are_rejected_before_read(self):
        link = self.root / "link.foid"
        link.symlink_to(self.input)
        fifo = self.root / "pipe.foid"
        os.mkfifo(fifo)
        result = foid.inspect_paths([link, self.root, fifo])
        self.assertFalse(result["inspection_ok"])
        self.assertTrue(all(r["inspection_status"] == "error" for r in result["files"]))
        self.assertEqual(result["limits"]["bytes_read"], 0)

    def test_oversized_file_is_rejected_without_opening(self):
        with mock.patch.object(foid.os, "open") as opened:
            result = foid.inspect_paths([self.input], max_file_bytes=63)
        opened.assert_not_called()
        self.assertFalse(result["inspection_ok"])

    def test_total_budget_is_applied_before_next_file(self):
        second = self.root / "second.foid"
        second.write_bytes(envelope())
        with mock.patch.object(foid, "MAX_TOTAL_BYTES", len(envelope())):
            result = foid.inspect_paths([self.input, second])
        self.assertFalse(result["inspection_ok"])
        self.assertEqual(result["files"][0]["inspection_status"], "parsed")
        self.assertEqual(result["files"][1]["inspection_status"], "error")

    def test_input_identity_change_is_reported(self):
        real_fstat = foid.os.fstat
        calls = 0
        second = self.root / "second.foid"
        second.write_bytes(envelope())
        original_size = self.input.stat().st_size

        def changed(fd):
            nonlocal calls
            value = real_fstat(fd)
            calls += 1
            if calls == 2:
                # Changing the actual file tests fstat identity checking, rather
                # than teaching a mock to echo the expected implementation.
                self.input.write_bytes(envelope(b"different payload"))
                return real_fstat(fd)
            return value

        with mock.patch.object(foid.os, "fstat", side_effect=changed), \
                mock.patch.object(foid, "MAX_TOTAL_BYTES", original_size):
            result = foid.inspect_paths([self.input, second])
        self.assertFalse(result["inspection_ok"])
        self.assertIn("changed", result["files"][0]["error"])
        self.assertEqual(result["limits"]["bytes_read"], original_size)
        self.assertIn("total inspection", result["files"][1]["error"])

    def test_invalid_limits_and_input_counts_raise(self):
        for limit in (True, 0, -1, foid.MAX_FILE_BYTES + 1):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                foid.inspect_paths([self.input], max_file_bytes=limit)
        with self.assertRaises(ValueError):
            foid.inspect_paths([])
        with self.assertRaises(ValueError):
            foid.inspect_paths([self.root / str(i) for i in range(65)])

    def test_cli_writes_new_report_and_never_overwrites_input_or_existing_report(self):
        report = self.root / "report.json"
        self.assertEqual(foid.main([str(self.input), "--output", str(report)]), 0)
        content = report.read_bytes()
        parsed = json.loads(content)
        self.assertTrue(parsed["inspection_ok"])
        self.assertIn("tool_source_sha256", parsed)
        original = self.input.read_bytes()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(foid.main([str(self.input), "--output", str(self.input)]), 2)
            self.assertEqual(foid.main([str(self.input), "--output", str(report)]), 2)
        self.assertEqual(report.read_bytes(), content)
        self.assertEqual(self.input.read_bytes(), original)

    def test_cli_invalid_envelope_returns_failure_json(self):
        self.input.write_bytes(b"not a configuration envelope")
        output = io.StringIO()
        with redirect_stdout(output):
            code = foid.main([str(self.input)])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output.getvalue())["inspection_ok"])


if __name__ == "__main__":
    unittest.main()
