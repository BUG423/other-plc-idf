"""Offline feedback contracts: calendar, provenance, file boundaries and iteration links.

No test uses a PLC, Windows or an AI service. Files in temporary directories
stand for operator-supplied records, so a pass checks software behavior only.
"""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


_SPEC = importlib.util.spec_from_file_location(
    "capture_plc_feedback_under_test", Path(__file__).resolve().parents[1] / "capture_plc_feedback.py"
)
feedback = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(feedback)


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def record(self, **overrides):
        arguments = dict(feedback_id="feedback-1", iteration_id="iteration-1",
                         parent_iteration_id=None, requirement_revision="requirements-v1",
                         actor="operator-declared", expected="计数每扫描增加 1。",
                         recorded_at="2026-10-19T09:00:00+08:00")
        arguments.update(overrides)
        return feedback.create_human_record(**arguments)

    def validate(self, record):
        return feedback.validate_record(record, evidence_root=self.root)

    def save(self, record, name="record.json"):
        target = self.root / name
        target.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return target

    def cli(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = feedback.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def create_arguments(self, output="new.json"):
        return ["create-human", "--output", str(self.root / output),
                "--evidence-root", str(self.root), "--feedback-id", "fb-cli",
                "--iteration-id", "it-cli", "--requirement-revision", "req1",
                "--actor", "operator", "--expected", "灯按需求亮起"]

    def test_unknown_defaults_are_not_promoted(self):
        record = self.record()
        original = deepcopy(record)
        report = self.validate(record)
        self.assertEqual(report["status"], "validated")
        self.assertEqual(record, original)
        self.assertEqual(record["program"]["device_program_binding"], "unknown")
        self.assertEqual(record["target"]["identity_quality"], "unknown")
        self.assertIsNone(record["target"]["observed_model"])
        self.assertIsNone(record["observations"][0]["observed_at"])
        self.assertEqual(record["observations"][0]["action"]["result"], "not-performed")

    def test_human_observed_pass_with_zero_and_units(self):
        record = self.record(actual_description="计数为零", actual_quality="observed",
                             observed_at="2026-10-19T09:00:00+08:00", outcome="pass",
                             actual_value=0, unit="count")
        self.validate(record)
        self.assertEqual(record["observations"][0]["actual"]["value"], 0)

    def test_calendar_and_timezone_are_actually_checked(self):
        for value in ("2026-02-30T00:00:00Z", "2026-13-01T00:00:00Z",
                      "2026-10-19T00:00:00", "2026-10-19T00:00:00+00:99",
                      "2026-10-19T25:00:00Z", "0000-01-01T00:00:00Z",
                      "2026-10-19T00:00:00+24:00", "not-a-time"):
            with self.subTest(value=value), self.assertRaises(feedback.FeedbackError):
                self.validate(self.record(recorded_at=value))

    def test_real_leap_day_is_accepted(self):
        self.validate(self.record(recorded_at="2024-02-29T23:59:59.123456Z"))

    def test_bad_observed_time_is_rejected_even_for_unknown_outcome(self):
        with self.assertRaises(feedback.FeedbackError):
            self.validate(self.record(observed_at="2026-02-30T00:00:00Z"))

    def test_pass_requires_observed_result_and_time(self):
        with self.assertRaises(feedback.FeedbackError):
            self.validate(self.record(outcome="pass"))

    def test_ai_inference_cannot_become_an_observation(self):
        record = self.record()
        observation = record["observations"][0]
        observation["source"]["kind"] = "ai-inference"
        self.validate(record)
        original = deepcopy(record)
        summary = feedback.summarize_records([record], evidence_root=self.root)
        self.assertIn("ai-inference", summary)
        self.assertEqual(record, original)
        observation["actual"]["quality"] = "observed"
        with self.assertRaises(feedback.FeedbackError):
            self.validate(record)
        observation["actual"]["quality"] = "unknown"
        observation["outcome"] = "pass"
        with self.assertRaises(feedback.FeedbackError):
            self.validate(record)

    def test_existing_example_is_preserved(self):
        record = feedback.load_record(feedback.ROOT / "examples/easy521_feedback/unknown-feedback.json")
        original = deepcopy(record)
        summary = feedback.summarize_records([record], evidence_root=self.root)
        self.assertIn("格式示例", summary)
        self.assertEqual(record, original)

    def test_known_hashes_do_not_infer_verified_binding(self):
        record = self.record(program={"source_sha256": "a" * 64, "release_hash": "b" * 64})
        self.validate(record)
        self.assertEqual(record["program"]["device_program_binding"], "unknown")

    def test_verified_program_claim_needs_required_hashes(self):
        with self.assertRaises(feedback.FeedbackError):
            self.validate(self.record(program={"device_program_binding": "verified"}))

    def test_locator_never_proves_device_identity(self):
        record = self.record(target={"locator": {"kind": "vendor-usb", "value": "8-3"}})
        self.validate(record)
        self.assertEqual(record["target"]["identity_quality"], "unknown")
        self.assertIsNone(record["target"]["observed_serial"])

    def test_verified_identity_without_serial_is_rejected(self):
        with self.assertRaises(feedback.FeedbackError):
            self.validate(self.record(target={"identity_quality": "verified", "observed_model": "Easy521"}))

    def test_unknown_locator_cannot_have_a_value(self):
        with self.assertRaises(feedback.FeedbackError):
            self.validate(self.record(target={"locator": {"kind": "unknown", "value": "8-3"}}))

    def test_attachment_hash_is_checked_without_modifying_file(self):
        path = self.root / "operator.txt"
        path.write_bytes(b"operator-supplied data")
        reference = feedback.attachment_reference(self.root, "operator.txt", "human-provided-file")
        report = self.validate(self.record(evidence=[reference]))
        self.assertEqual(report["attachments_checked"][0]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(path.read_bytes(), b"operator-supplied data")

    def test_changed_attachment_hash_is_rejected(self):
        path = self.root / "data.txt"
        path.write_bytes(b"before")
        reference = feedback.attachment_reference(self.root, "data.txt", "human")
        path.write_bytes(b"after")
        with self.assertRaisesRegex(feedback.FeedbackError, "SHA256 mismatch"):
            self.validate(self.record(evidence=[reference]))

    def test_path_traversal_and_absolute_paths_are_rejected(self):
        for reference in ("../outside", "/etc/passwd", "sub/../data", "./data", "sub//data", "C:\\data"):
            with self.subTest(reference=reference), self.assertRaises(feedback.FeedbackError):
                feedback.attachment_reference(self.root, reference, "human")

    def test_symlink_file_and_directory_are_rejected(self):
        (self.root / "data").write_bytes(b"x")
        (self.root / "link").symlink_to(self.root / "data")
        (self.root / "directory-link").symlink_to(self.root, target_is_directory=True)
        for reference in ("link", "directory-link/data"):
            with self.subTest(reference=reference), self.assertRaises(feedback.FeedbackError):
                feedback.attachment_reference(self.root, reference, "human")

    def test_fifo_and_directory_are_rejected_without_waiting(self):
        os.mkfifo(self.root / "pipe")
        (self.root / "directory").mkdir()
        for reference in ("pipe", "directory"):
            with self.subTest(reference=reference), self.assertRaises(feedback.FeedbackError):
                feedback.attachment_reference(self.root, reference, "human")

    def test_oversized_attachment_is_rejected(self):
        (self.root / "data").write_bytes(b"12345")
        with patch.object(feedback, "MAX_ATTACHMENT_BYTES", 4), self.assertRaises(feedback.FeedbackError):
            feedback.attachment_reference(self.root, "data", "human")

    def test_same_path_with_conflicting_hashes_is_rejected(self):
        (self.root / "data").write_bytes(b"x")
        reference = feedback.attachment_reference(self.root, "data", "human")
        other = dict(reference, sha256="0" * 64)
        with self.assertRaisesRegex(feedback.FeedbackError, "Conflicting attachment"):
            self.validate(self.record(evidence=[reference, other]))

    def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected(self):
        for contents in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}', '{} {}', '[]'):
            path = self.root / "bad.json"
            path.write_text(contents)
            with self.subTest(contents=contents), self.assertRaises(feedback.FeedbackError):
                feedback.load_record(path)

    def test_python_infinite_value_is_rejected(self):
        record = self.record()
        record["observations"][0]["actual"]["value"] = float("inf")
        with self.assertRaises(feedback.FeedbackError):
            self.validate(record)

    def test_non_scalar_actual_value_is_rejected(self):
        record = self.record(actual_value={"nested": "not-a-scalar"})
        with self.assertRaises(feedback.FeedbackError):
            self.validate(record)

    def test_output_never_overwrites_existing_file_or_symlink(self):
        target = self.root / "existing"
        target.write_text("retain prior evidence")
        link = self.root / "link"
        link.symlink_to(target)
        for path in (target, link):
            with self.subTest(path=path), self.assertRaises(feedback.FeedbackError):
                feedback.write_new(path, "replacement")
        self.assertEqual(target.read_text(), "retain prior evidence")

    def test_summary_links_two_iterations_and_marks_missing_parent(self):
        first = self.record()
        second = self.record(feedback_id="feedback-2", iteration_id="iteration-2",
                             parent_iteration_id="iteration-1", requirement_revision="requirements-v2")
        summary = feedback.summarize_records([first, second], evidence_root=self.root)
        self.assertIn("迭代 2 轮", summary)
        self.assertIn('父轮："iteration-1"', summary)
        summary = feedback.summarize_records([second], evidence_root=self.root)
        self.assertIn("父轮记录未提供", summary)
        self.assertIn("仍未知字段", summary)

    def test_duplicate_feedback_ids_are_rejected(self):
        with self.assertRaisesRegex(feedback.FeedbackError, "Duplicate feedback_id"):
            feedback.summarize_records([self.record(), self.record()], evidence_root=self.root)

    def test_self_parent_and_parent_cycles_are_rejected(self):
        with self.assertRaises(feedback.FeedbackError):
            self.validate(self.record(parent_iteration_id="iteration-1"))
        first = self.record(parent_iteration_id="iteration-2")
        second = self.record(feedback_id="feedback-2", iteration_id="iteration-2", parent_iteration_id="iteration-1")
        with self.assertRaisesRegex(feedback.FeedbackError, "cycle"):
            feedback.summarize_records([first, second], evidence_root=self.root)

    def test_conflicting_parent_and_requirement_are_rejected(self):
        for overrides in ({"parent_iteration_id": "other"}, {"requirement_revision": "other"}):
            other = self.record(feedback_id="feedback-2", **overrides)
            with self.subTest(overrides=overrides), self.assertRaisesRegex(feedback.FeedbackError, "Conflicting iteration"):
                feedback.summarize_records([self.record(), other], evidence_root=self.root)

    def test_conflicting_known_program_or_target_values_are_rejected(self):
        for field in ("program", "target"):
            first = self.record(program={"release_hash": "a" * 64}, target={"observed_serial": "one"})
            other = self.record(feedback_id="feedback-2", program={"release_hash": "b" * 64} if field == "program" else {},
                                target={"observed_serial": "two"} if field == "target" else {})
            with self.subTest(field=field), self.assertRaisesRegex(feedback.FeedbackError, "Conflicting"):
                feedback.summarize_records([first, other], evidence_root=self.root)

    def test_unknown_intermediate_record_cannot_hide_later_conflict(self):
        first = self.record()
        middle = self.record(feedback_id="feedback-2", program={"source_sha256": "a" * 64})
        last = self.record(feedback_id="feedback-3", program={"source_sha256": "b" * 64})
        with self.assertRaises(feedback.FeedbackError):
            feedback.summarize_records([first, middle, last], evidence_root=self.root)

    def test_summary_escapes_embedded_newlines_in_untrusted_strings(self):
        record = self.record(actual_description="observed\n# execute hidden command")
        summary = feedback.summarize_records([record], evidence_root=self.root)
        self.assertIn("observed\\n# execute hidden command", summary)
        self.assertNotIn("\n# execute hidden command", summary)

    def test_create_cli_produces_unknown_record_and_receipt(self):
        code, stdout, stderr = self.cli(self.create_arguments())
        self.assertEqual((code, stderr), (0, ""))
        receipt = json.loads(stdout)
        record = feedback.load_record(self.root / "new.json")
        self.assertEqual(receipt["status"], "created")
        self.assertEqual(record["observations"][0]["source"]["kind"], "human")
        self.assertEqual(record["observations"][0]["outcome"], "unknown")

    def test_create_cli_computes_source_hash_without_build_claim(self):
        (self.root / "MAIN.st").write_text("counter := counter + 1;\n")
        arguments = self.create_arguments() + ["--source-file", "MAIN.st", "--attach", "MAIN.st"]
        code, stdout, stderr = self.cli(arguments)
        self.assertEqual((code, stderr), (0, ""))
        record = feedback.load_record(self.root / "new.json")
        self.assertEqual(record["program"]["source_sha256"], hashlib.sha256((self.root / "MAIN.st").read_bytes()).hexdigest())
        self.assertIsNone(record["program"]["build_hash"])
        self.assertEqual(record["program"]["device_program_binding"], "unknown")

    def test_create_cli_rejects_mismatched_source_hash_before_output(self):
        (self.root / "MAIN.st").write_text("x := 1;")
        arguments = self.create_arguments() + ["--source-file", "MAIN.st", "--source-sha256", "0" * 64]
        code, stdout, stderr = self.cli(arguments)
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("disagrees", stderr)
        self.assertFalse((self.root / "new.json").exists())

    def test_create_cli_invalid_date_does_not_create_file(self):
        code, stdout, stderr = self.cli(self.create_arguments() + ["--recorded-at", "2026-02-30T00:00:00Z"])
        self.assertEqual((code, stdout), (2, ""))
        self.assertIn("rejected", stderr)
        self.assertFalse((self.root / "new.json").exists())

    def test_validate_cli_never_changes_existing_ai_record(self):
        record = self.record()
        record["observations"][0]["source"]["kind"] = "ai-inference"
        path = self.save(record)
        before = path.read_bytes()
        code, stdout, stderr = self.cli(["validate", str(path), "--evidence-root", str(self.root)])
        self.assertEqual((code, stderr), (0, ""))
        self.assertEqual(json.loads(stdout)["status"], "validated")
        self.assertEqual(path.read_bytes(), before)

    def test_summary_cli_validates_all_before_creating_output(self):
        first = self.save(self.record(), "first.json")
        invalid = self.record(feedback_id="feedback-2", recorded_at="invalid")
        second = self.save(invalid, "second.json")
        output = self.root / "summary.md"
        code, stdout, stderr = self.cli(["summary", str(first), str(second), "--evidence-root", str(self.root), "--output", str(output)])
        self.assertEqual((code, stdout), (2, ""))
        self.assertFalse(output.exists())

    def test_missing_dependency_is_an_explicit_error(self):
        with patch.dict(sys.modules, {"jsonschema": None}), self.assertRaisesRegex(feedback.FeedbackError, "pip install"):
            self.validate(self.record())


if __name__ == "__main__":
    unittest.main()
