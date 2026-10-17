"""防止进度脚本覆盖人工报告或把元数据误作验收结论；全离线。"""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "github_progress", Path(__file__).resolve().parents[1] / "update_github_progress.py")
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)


class ProgressTests(unittest.TestCase):
    def test_report_has_unique_markers(self):
        body = progress.render_report([], "now")
        self.assertEqual(body.count(progress.START), 1)
        self.assertEqual(body.count(progress.END), 1)
        self.assertTrue(body.startswith(progress.START))

    def test_main_appends_comment_without_rewriting_human_issue(self):
        requests = []
        tracker = {"number": 12, "state": "open", "body": "人工最新记录"}

        def fake_open(request, timeout):
            requests.append(request)
            if request.get_method() == "POST":
                response = {"id": 100}
            elif "plcidf-tracking" in request.full_url:
                response = [tracker]
            else:
                response = []
            return io.StringIO(json.dumps(response))

        with patch.dict(os.environ, {"GITHUB_REPOSITORY": "a/b", "GITHUB_TOKEN": "fake"}, clear=True):
            with patch.object(progress, "urlopen", side_effect=fake_open):
                progress.main()
        writes = [r for r in requests if r.get_method() != "GET"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].get_method(), "POST")
        self.assertTrue(writes[0].full_url.endswith("/issues/12/comments"))
        self.assertEqual(tracker["body"], "人工最新记录")

    def test_tracker_or_unrelated_issue_event_does_not_call_api(self):
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            for label in ("plcidf-tracking", "unrelated"):
                event.write_text(json.dumps({"issue": {"number": 12, "labels": [{"name": label}]}}))
                with patch.dict(os.environ, {"GITHUB_REPOSITORY": "a/b", "GITHUB_TOKEN": "fake",
                                            "GITHUB_EVENT_PATH": str(event)}, clear=True):
                    with patch.object(progress, "urlopen", side_effect=AssertionError("unexpected API call")):
                        progress.main()

    def test_ignores_pull_requests_and_renders_closed_state_without_acceptance_claim(self):
        issues = [
            {"number": 4, "title": "PR", "state": "closed", "pull_request": {}},
            {"number": 3, "title": "A|B\n[link]<script>", "state": "open",
             "html_url": "https://github.com/a/b/issues/3", "labels": [{"name": "priority:P0"}]},
            {"number": 2, "title": "C", "state": "closed",
             "html_url": "https://github.com/a/b/issues/2", "labels": []},
        ]
        output = progress.render_tasks(issues, "2026-10-17T00:00:00+00:00")
        self.assertIn("任务：2；已关闭：1；未关闭：1", output)
        self.assertIn("仅汇总 GitHub Issue 状态", output)
        self.assertNotIn("A|B", output)
        self.assertNotIn("<script>", output)
        self.assertIn("priority:P0", output)
        self.assertNotIn("#4", output)

    def test_empty_report_does_not_claim_completion(self):
        output = progress.render_tasks([], "now")
        self.assertIn("任务：0", output)
        self.assertIn("验收证据", output)


if __name__ == "__main__":
    unittest.main()
