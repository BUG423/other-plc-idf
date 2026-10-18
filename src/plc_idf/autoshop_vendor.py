"""Calibrated AutoShop 4.12.0.2 download/compare adapter.

Only the download dialog is addressed semantically. Program comparison uses a
measured, strictly guarded desktop layout because this AutoShop menu is owner
drawn. Unsupported versions/layouts and missing evidence fail closed. This
module never requests RUN, opens a project or exits online monitoring.

The caller owns authorization and the shared device/AutoShop lease (normally
``NativeDeploy``). ``runner`` injects the subprocess boundary for offline tests.
Passing those tests is not hardware validation of this adapter.
"""

from __future__ import annotations

import hashlib
import json
import math
import ntpath
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .artifacts import normalize_target
from .config import evidence_path
from .device import find_plc_entry, parse_usbipd_list
from .evidence import EvidenceWriteError
from .interop import RetryPolicy, run_command
from .native_autoshop import parse_editor_title
from .native_release import (CompilationProofRequired, capture_release, compile_baseline, fresh_compile_proof,
                             verify_intent, verify_release)


VERSION = "4.12.0.2"
COMPARE_EQUAL = "当前工程与PLC中程序校验一致！"
COMPARE_DIFFERENT = "当前工程与PLC中工程不一致：ST程序校验不一致！"
DOWNLOAD_DIALOG = "下载设置"
_LAYOUT = {"rect": (-16, -16, 3872, 2096), "screen": (3840, 2160),
           "app_dpi": 96, "system_dpi": 192}
_TITLE = re.compile(r"^AutoShop\s+V(?P<version>\d+(?:\.\d+){1,3})\s+"
                    r"(?P<project>[A-Za-z]:\\.+?)\s+-\s+\[(?P<document>[^\]]+)\]$")


class VendorAdapterError(RuntimeError):
    """A calibrated prerequisite was not established; do not replay input."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _same_project(actual, expected):
    return ntpath.normcase(ntpath.normpath(actual)) == ntpath.normcase(ntpath.normpath(expected))


def fresh_log_lines(baseline, current):
    """Require append-only log evidence; a cleared/replaced log is ambiguous.

    In particular, a stale terminal message from before the action cannot pass.
    The vendor may change output panes; that must produce unknown, not success.
    """
    before, after = baseline.splitlines(), current.splitlines()
    if after[:len(before)] != before:
        raise VendorAdapterError("Vendor log was replaced/truncated; freshness is not established")
    return after[len(before):]


def windowed_log_lines(baseline, current, *, window=None):
    """Allow a known communication-pane replacement only with Windows clock proof.

    Vendor log timestamps have one-second resolution. Require events at or
    after the *next* whole second following the pre-action Windows clock read.
    Same-second ambiguity therefore cannot validate an older transfer. A fast
    operation in that ambiguous second remains unknown. No timezone is guessed.
    """
    try:
        return fresh_log_lines(baseline, current)
    except VendorAdapterError:
        if window is None:
            raise
    if not current.splitlines() or not re.fullmatch(r"-+\s+AutoShop : 通讯消息\s+-+", current.splitlines()[0]):
        raise VendorAdapterError("Replaced vendor log is not the calibrated communication pane")
    try:
        start = datetime.fromisoformat(window["started_at"])
        end = datetime.fromisoformat(window["observed_at"])
        if (start.tzinfo is None or end.tzinfo is None or start.utcoffset() != end.utcoffset()
                or not 0 <= (end - start).total_seconds() <= 600):
            raise ValueError("Windows clock window is invalid or changed offset")
        lower = start.replace(microsecond=0) + timedelta(seconds=1)
        selected, previous = [], None
        for line in current.splitlines()[1:]:
            if not line.strip():
                continue
            match = re.fullmatch(r"(?:信息|警告|错误)\s+\((\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\)\s+(.+)", line)
            if not match:
                raise ValueError("Unrecognized timestamped communication record")
            stamp = datetime.strptime(match[1], "%Y-%m-%d %H:%M:%S").replace(tzinfo=start.tzinfo)
            if previous is not None and stamp < previous:
                raise ValueError("Communication log timestamps run backwards")
            previous = stamp
            if stamp > end:
                raise ValueError("Communication record is newer than the observed Windows clock")
            if stamp >= lower:
                selected.append(line)
        return selected
    except (ValueError, KeyError, TypeError) as exc:
        raise VendorAdapterError(f"Communication log cannot be bound to the action clock window: {exc}") from exc


def analyze_download_log(baseline, current, *, window=None):
    lines = windowed_log_lines(baseline, current, window=window)
    starts = [index for index, line in enumerate(lines) if "开始下载" in line]
    if len(starts) > 1:
        return {"status": "unknown", "reason": "More than one fresh download start"}
    if not starts:
        return {"status": "unknown", "reason": "No fresh download start"}
    tail = lines[starts[0] + 1:]
    if any(re.search(r"下载.*(?:失败|错误)|未能.*下载", line) for line in tail):
        return {"status": "failed", "reason": "Vendor reported download failure"}
    required = ("project", "userprog.foid", "stprog.o", "stuserprog.o")
    files = [name for name in required
             if any(re.search(r"下载\s*" + re.escape(name) + r"\s+成功", line) for line in tail)]
    terminal = [i for i, line in enumerate(tail) if re.search(r"下载成功[！!]", line)]
    complete = bool(terminal and len(files) == len(required)
                    and all(any(re.search(r"下载\s*" + re.escape(name) + r"\s+成功", line)
                                for line in tail[:terminal[-1]]) for name in required))
    return {"status": "verified" if complete else "unknown", "files": files,
            "fresh_lines": lines, "run_requested": False}


def parse_inspection(text):
    rows, pid = [], None
    for line in text.splitlines():
        if line.startswith("PID="):
            match = re.match(r"PID=(\d+)\|", line)
            if match:
                pid = int(match[1])
            continue
        fields = line.split("|")
        if len(fields) != 6:
            continue
        try:
            rect = tuple(float(v) for v in fields[5].split(","))
            if len(rect) != 4 or not all(math.isfinite(v) for v in rect):
                continue
            rows.append({"type": fields[0], "name": fields[1], "class": fields[2],
                         "handle": int(fields[3]), "enabled": fields[4] == "True", "rect": rect})
        except ValueError:
            continue
    return {"pid": pid, "rows": rows}


def parse_diagnostics(text):
    match = re.fullmatch(r"AppPID=(\d+) Session=(\d+) Foreground=(\d+) ForegroundPID=(\d+) "
                         r"AppDPI=(\d+) SystemDPI=(\d+) Screen=(\d+)x(\d+)", text.strip())
    if not match:
        raise VendorAdapterError("Unrecognized AutoShop desktop diagnostics")
    names = ("pid", "session", "foreground", "foreground_pid", "app_dpi", "system_dpi", "width", "height")
    return dict(zip(names, map(int, match.groups())))


class _Phase:
    def __init__(self, adapter, name, context, timeout_s):
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 600:
            raise ValueError("timeout_s must be in (0, 600]")
        self.adapter, self.name, self.context = adapter, name, context
        self.deadline, self.evidence = time.monotonic() + timeout_s, []

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Vendor adapter time budget exhausted; input is not replayed")
        return remaining

    def save(self, source, payload):
        directory = self.adapter.evidence_directory
        operation = str(self.context.get("operation_id", "observation"))
        # No caller-controlled filename components are used.
        path = directory / f"{uuid.uuid4().hex}-{self.name}-{source}.json"
        record = {"schema": "plcidf.autoshop-vendor-evidence.v1", "operation_id": operation,
                  "timestamp_utc": datetime.now(timezone.utc).isoformat(), "phase": self.name,
                  "source": source, "context": self.context, "result": payload}
        try:
            data = (_json(record) + "\n").encode("utf-8")
            directory.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except (OSError, ValueError, TypeError) as exc:
            raise EvidenceWriteError(f"Cannot retain vendor evidence: {exc}") from exc
        self.evidence.append({"path": str(path.resolve()), "sha256": _sha(data), "source": source})

    def command(self, argv, source, *, mutation=False):
        self.remaining()
        # Persist exact intent before any input. An uncertain action is never retried.
        if mutation:
            self.save("intent-" + source, {"argv": argv, "run_requested": False})
        try:
            result = run_command(argv, policy=RetryPolicy(timeout_s=min(30, self.remaining()), retries=0),
                                 runner=self.adapter.runner, retryable=False)
        except Exception as exc:
            self.save(source, {"argv": argv, "error": str(exc), "error_type": type(exc).__name__})
            raise
        self.save(source, {"argv": argv, "returncode": result.returncode, "stdout": result.stdout,
                           "stderr": result.stderr, "timed_out": result.timed_out,
                           "attempts": result.attempts})
        if not result.ok:
            raise VendorAdapterError(f"{source} failed; input is not retried: {result.summary()} {result.stderr}")
        self.remaining()
        return result.stdout.lstrip("\ufeff").strip()

    def ui(self, action, value="", *, mutation=False, **parameters):
        argv = [self.adapter.cfg.bridge["powershell_exe"], "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", self.adapter.cfg.bridge["tools_ps1_windows"], "-Action", action,
                "-ExpectedProject", self.adapter.expected_project]
        if value:
            argv += ["-Value", value]
        for key, value in parameters.items():
            argv += ["-" + key, str(value)]
        return self.command(argv, action, mutation=mutation)

    def object(self, action, value="", **parameters):
        result = json.loads(self.ui(action, value, **parameters))
        if not isinstance(result, dict):
            raise VendorAdapterError(f"{action} must return an object")
        return result

    def windows_time(self):
        # Read the host's own UTC offset. Vendor log timestamps are local Windows
        # time; WSL's timezone and file mtimes are not clock attestations.
        raw = self.command([self.adapter.cfg.bridge["powershell_exe"], "-NoProfile", "-Command",
                            "[DateTimeOffset]::Now.ToString('o')"], "windows-time")
        try:
            stamp = datetime.fromisoformat(raw)
            if stamp.tzinfo is None:
                raise ValueError("UTC offset missing")
        except ValueError as exc:
            raise VendorAdapterError("Windows clock read did not return an offset timestamp") from exc
        return stamp.isoformat()

    def log_window(self, baseline, current, started_at):
        # Append-only observations need no wall-clock fallback.
        try:
            fresh_log_lines(baseline, current)
            return None
        except VendorAdapterError:
            return {"started_at": started_at, "observed_at": self.windows_time()}


class AutoShopVendorAdapter:
    def __init__(self, cfg, *, evidence_directory=None, runner=None):
        self.cfg, self.runner = cfg, runner
        self.expected_project = cfg.bridge["windows_project"].rstrip("\\/")
        self.evidence_directory = (Path(evidence_directory).resolve() if evidence_directory is not None
                                   else evidence_path(cfg).parent / "autoshop-vendor")
        self._sessions = {}

    def describe(self):
        return {"name": "autoshop-4.12.0.2-calibrated-desktop", "calibrated": True,
                "capabilities": {"download": True, "vendor_result": True, "program_compare": True,
                                 "runtime_control": False, "connection_identity": False,
                                 "prepared_release": True},
                "version": VERSION, "comparison_layout": _LAYOUT,
                "limits": ["GUI steps calibrated; adapter integration requires live acceptance",
                           "vendor comparison may recompile and invalidate the selected build",
                           "no CPU execution or physical I/O claim"]}

    def _phase(self, name, context, timeout_s, function):
        phase = _Phase(self, name, context, timeout_s)
        observation = {key: context.get(key) for key in ("operation_id", "build_hash", "target", "working_project")}
        observation.update(status="unavailable", run_requested=False)
        try:
            self._context(context)
            observation.update(function(phase))
        except EvidenceWriteError:
            raise
        except Exception as exc:
            observation.update(status="unknown" if name in {"download", "verify-download", "compare"}
                               else "unavailable", reason=str(exc), error_type=type(exc).__name__)
            session = self._sessions.get(context.get("operation_id"), {})
            if name == "download" and session.get("release"):
                observation.update(release=session["release"], release_hash=session["release"]["release_hash"])
                if session.get("terminal"):
                    observation["vendor_result"] = session["terminal"]
        phase.save("decision", observation)
        observation["evidence"] = phase.evidence
        return observation

    def _context(self, context):
        configured = normalize_target({"busid": self.cfg.device["busid"], "vidpid": self.cfg.device["vidpid"]})
        target = normalize_target(context.get("target"))
        if any(target[key] != configured[key] for key in ("busid", "vidpid")):
            raise VendorAdapterError("Both USB BUSID and VID:PID must match the configured target")
        if not _same_project(context.get("working_project", ""), self.expected_project):
            raise VendorAdapterError("Operation names a different working project")
        if not context.get("operation_id") or not context.get("build_hash") or context.get("auto_run") is not False:
            raise VendorAdapterError("A bound operation/build with auto_run=false is required")

    @staticmethod
    def _artifacts(context):
        artifacts = context.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise VendorAdapterError("Bound build artifacts are required")
        for artifact in artifacts:
            path = Path(artifact["path"])
            before = path.stat()
            actual = _sha(path.read_bytes())
            after = path.stat()
            if (actual != artifact["sha256"] or (before.st_ino, before.st_size, before.st_mtime_ns) !=
                    (after.st_ino, after.st_size, after.st_mtime_ns)):
                raise VendorAdapterError(f"Selected build artifact changed (possible vendor auto-compile): {path}")

    def _integrity_result(self, phase, context, outcome):
        # Keep the actual vendor result visible even when its automatic build
        # invalidates our selected artifact hashes. Never adopt the new hashes.
        try:
            self._artifacts(context)
            outcome["artifact_integrity"] = True
        except Exception as exc:
            outcome.update(status="unknown", artifact_integrity=False, reason=str(exc))
        phase.save("artifact-integrity", outcome)
        return outcome

    def _usb(self, phase):
        raw = phase.command([self.cfg.device["usbipd_exe"], "list"], "usbipd-list")
        device = find_plc_entry(self.cfg, parse_usbipd_list(raw))
        if device is None or device.state.lower() not in {"shared", "not shared"}:
            raise VendorAdapterError("Exact configured USB device is absent, ambiguous or attached to WSL")
        return device

    def _editor(self, phase):
        editor = phase.object("editor-read")
        parsed = parse_editor_title(editor.get("title", ""), self.expected_project)
        if parsed["version"] != VERSION or editor.get("read_only") is not False:
            raise VendorAdapterError("Requires calibrated version and an offline writable MAIN editor")
        if not isinstance(editor.get("source"), str):
            raise VendorAdapterError("MAIN source readback is missing")
        expected = phase.context.get("source_text_sha256")
        if expected and _sha(editor["source"].replace("\r\n", "\n").replace("\r", "\n").encode()) != expected:
            raise VendorAdapterError("MAIN source readback differs from the selected source intent")
        return editor

    def _layout(self, phase):
        inspected = parse_inspection(phase.ui("inspect"))
        windows = [row for row in inspected["rows"] if row["type"] == "ControlType.Window"]
        if any(row["class"] == "#32770" and row["enabled"] for row in windows):
            raise VendorAdapterError("Unexpected modal is open; no input sent")
        main = [row for row in windows if row["class"].startswith("Afx:") and row["name"].startswith("AutoShop V")]
        if len(main) != 1:
            raise VendorAdapterError("Expected one exact AutoShop main frame")
        parsed = parse_editor_title(main[0]["name"], self.expected_project)
        if parsed["version"] != VERSION or main[0]["rect"] != _LAYOUT["rect"] or not main[0]["enabled"]:
            raise VendorAdapterError("AutoShop main frame version/maximized rectangle is not calibrated")
        diagnostics = parse_diagnostics(phase.ui("diagnostics"))
        if (diagnostics["pid"] != inspected["pid"] or diagnostics["foreground_pid"] != inspected["pid"]
                or diagnostics["foreground"] != main[0]["handle"]
                or diagnostics["app_dpi"] != _LAYOUT["app_dpi"]
                or diagnostics["system_dpi"] != _LAYOUT["system_dpi"]
                or (diagnostics["width"], diagnostics["height"]) != _LAYOUT["screen"]):
            raise VendorAdapterError("Screen/DPI/foreground differs from the calibrated comparison layout")
        return inspected

    def preflight(self, context, *, timeout_s):
        def check(phase):
            device = self._usb(phase)
            self._editor(phase)
            self._layout(phase)
            self._artifacts(context)
            if context.get("intent"):
                verify_intent(context["intent"])
            self._sessions[context["operation_id"]] = {"context_hash": _sha(_json(context).encode()),
                                                       "download_called": False}
            return {"status": "verified", "usb_state": device.state, "identity_ambiguous": False,
                    "unsaved_changes": False, "auto_run": False}
        return self._phase("preflight", context, timeout_s, check)

    def _session(self, context):
        session = self._sessions.get(context["operation_id"])
        if session is None or session["context_hash"] != _sha(_json(context).encode()):
            raise VendorAdapterError("This exact operation requires a verified preflight")
        return session

    @staticmethod
    def _download_dialog(dialog, expected_project):
        if dialog.get("title") != DOWNLOAD_DIALOG:
            raise VendorAdapterError("Download settings dialog title differs")
        parse_editor_title(dialog.get("project", ""), expected_project)
        controls = dialog.get("controls", [])
        for name, state, kind in (("下载工程源码(不勾选则不能上载工程)", 1, 3),
                                   ("下载配置在工程中的PLC登录密码", 0, 3),
                                   ("下载时，保持型变量保持现有值", 1, 9),
                                   ("下载时，保持型变量重新初始化", 0, 9)):
            matches = [item for item in controls if item.get("name") == name and item.get("class") == "Button"]
            if (len(matches) != 1 or matches[0].get("checked") != state
                    or matches[0].get("button_type") != kind or matches[0].get("enabled") is not True):
                raise VendorAdapterError(f"Download setting is not calibrated: {name}")
        buttons = [item for item in controls if item.get("name") == "确定" and item.get("class") == "Button"
                   and item.get("enabled") is True and item.get("button_type") in (0, 1)]
        if len(buttons) != 1:
            raise VendorAdapterError("Expected one exact enabled download confirmation button")

    def download(self, context, *, timeout_s):
        def perform(phase):
            session = self._session(context)
            if session["download_called"]:
                raise VendorAdapterError("Download was already attempted; do not repeat automatically")
            # Mark before input, including uncertain attempts, and never reset it.
            session["download_called"] = True
            self._usb(phase)
            self._editor(phase)
            self._layout(phase)
            self._artifacts(context)
            before_log = phase.ui("log-data")
            intent = context.get("intent")
            if intent:
                baseline = compile_baseline(intent, check_time=phase.remaining)
                phase.save("compile-baseline", baseline)
                compile_started = phase.windows_time()
            phase.ui("keys", "{F8}", mutation=True)
            dialog = phase.object("dialog", ExpectedDialog=DOWNLOAD_DIALOG)
            self._download_dialog(dialog, self.expected_project)
            session["baseline"] = phase.ui("log-data")
            if intent:
                try:
                    session["release"] = capture_release(intent, baseline,
                        directory=self.evidence_directory / "releases", check_time=phase.remaining)
                except CompilationProofRequired:
                    proof = fresh_compile_proof(before_log, session["baseline"], started_at=compile_started,
                                                observed_at=phase.windows_time(), evidence=phase.evidence)
                    session["release"] = capture_release(intent, baseline, proof,
                        directory=self.evidence_directory / "releases", check_time=phase.remaining)
                phase.save("prepared-release", session["release"])
                # Capture is not consent to changing project/options while it runs.
                self._download_dialog(phase.object("dialog", ExpectedDialog=DOWNLOAD_DIALOG), self.expected_project)
                verify_release(session["release"], live_outputs=True)
                phase.remaining()
            session["started_at"] = phase.windows_time()
            posted = phase.object("button", "确定", mutation=True, ExpectedDialog=DOWNLOAD_DIALOG)
            if posted.get("invocation_posted") is not True or posted.get("dialog") != DOWNLOAD_DIALOG:
                raise VendorAdapterError("Download confirmation was not posted; do not retry")
            while True:
                raw = phase.ui("log-data")
                window = phase.log_window(session["baseline"], raw, session["started_at"])
                analysis = analyze_download_log(session["baseline"], raw, window=window)
                if window is not None:
                    analysis["clock_window"] = window
                if analysis["status"] in {"verified", "failed"}:
                    session["terminal"] = analysis
                    phase.save("vendor-terminal", analysis)
                    if intent:
                        verify_release(session["release"], live_outputs=True)
                        return {"status": "completed" if analysis["status"] == "verified" else "failed",
                                "vendor_result": analysis, "release": session["release"],
                                "release_hash": session["release"]["release_hash"],
                                "binary_consistency": {"status": "unverified", "method": "no-device-byte-binding"}}
                    return self._integrity_result(phase, context,
                        {"status": "completed" if analysis["status"] == "verified" else "failed",
                         "vendor_result": analysis})
                time.sleep(min(0.2, phase.remaining()))
        return self._phase("download", context, timeout_s, perform)

    def verify_download(self, context, download_result, *, timeout_s):
        def verify(phase):
            session = self._session(context)
            if (download_result.get("operation_id") != context["operation_id"]
                    or download_result.get("status") not in {"completed", "verified"}
                    or session.get("terminal", {}).get("status") != "verified"):
                raise VendorAdapterError("No completed download in this exact adapter operation")
            raw = phase.ui("log-data")
            window = phase.log_window(session["baseline"], raw, session["started_at"])
            analysis = analyze_download_log(session["baseline"], raw, window=window)
            if window is not None:
                analysis["clock_window"] = window
            if context.get("intent"):
                release = session.get("release")
                if not release or download_result.get("release_hash") != release["release_hash"]:
                    raise VendorAdapterError("Download result does not bind the prepared release")
                verify_release(release, live_outputs=True)
                return {"status": analysis["status"], "vendor_result": analysis,
                        "release_hash": release["release_hash"]}
            return self._integrity_result(phase, context,
                                          {"status": analysis["status"], "vendor_result": analysis})
        return self._phase("verify-download", context, timeout_s, verify)

    def compare_program(self, context, *, timeout_s):
        def compare(phase):
            self._usb(phase)
            self._editor(phase)
            self._layout(phase)
            release = None
            if context.get("intent"):
                verify_intent(context["intent"])
                session = self._session(context)
                release = session.get("release")
                if not release:
                    raise VendorAdapterError("No prepared release for this operation")
                verify_release(release, live_outputs=True)
            else:
                self._artifacts(context)
            baseline = phase.ui("log-data")
            started_at = phase.windows_time()
            # The owner-drawn menu has no calibrated semantic identifier.
            phase.ui("click", mutation=True, X=520, Y=86)
            self._layout(phase)
            phase.ui("click", mutation=True, X=600, Y=864)
            while True:
                inspected = parse_inspection(phase.ui("inspect"))
                dialogs = [row for row in inspected["rows"] if row["class"] == "#32770" and row["enabled"]]
                if dialogs:
                    if len(dialogs) != 1 or dialogs[0]["name"] != "AutoShop":
                        raise VendorAdapterError("Unexpected or ambiguous program-comparison dialog")
                    dialog = phase.object("dialog", ExpectedDialog="AutoShop")
                    messages = [item.get("name") for item in dialog.get("controls", []) if item.get("class") == "Static"]
                    terminal = [message for message in messages if message in {COMPARE_EQUAL, COMPARE_DIFFERENT}]
                    if len(terminal) != 1:
                        raise VendorAdapterError("Unrecognized vendor program-comparison result")
                    phrase = terminal[0]
                    raw = phase.ui("log-data")
                    window = phase.log_window(baseline, raw, started_at)
                    fresh = windowed_log_lines(baseline, raw, window=window)
                    if not any(phrase in line for line in fresh):
                        raise VendorAdapterError("Comparison dialog lacks an independent fresh log result")
                    outcome = {"status": "verified", "matches": phrase == COMPARE_EQUAL,
                               "independent": True, "method": "vendor-program-compare", "vendor_message": phrase}
                    phase.save("comparison-terminal", outcome)
                    # Acknowledge only this exact message, never a generic success.
                    posted = phase.object("button", "确定", mutation=True, ExpectedDialog="AutoShop", ExpectedText=phrase)
                    if posted.get("invocation_posted") is not True:
                        raise VendorAdapterError("Comparison acknowledgement outcome is unknown")
                    if release:
                        # Comparison may rebuild live files. Verify inputs and
                        # immutable release copies; never replace the release.
                        verify_release(release)
                        outcome.update(release_hash=release["release_hash"], verification_level="project",
                            project_consistency={"status": "verified" if outcome["matches"] else "failed",
                                                 "intent_hash": context["intent"]["intent_hash"]},
                            binary_consistency={"status": "unverified", "method": "vendor-project-compare",
                                                "reason": "Vendor project comparison is not byte readback"})
                        return outcome
                    return self._integrity_result(phase, context, outcome)
                time.sleep(min(0.2, phase.remaining()))
        return self._phase("compare", context, timeout_s, compare)

    def observe_connection(self, *, timeout_s=30):
        """Read USB and existing UI only; there is no calibrated online identity reader.

        USB presence, Shared state, project configuration and readable variable
        values cannot establish the identity/online state of the vendor session.
        """
        target = normalize_target({"busid": self.cfg.device["busid"], "vidpid": self.cfg.device["vidpid"]})
        context = {"operation_id": uuid.uuid4().hex, "target": target, "working_project": self.expected_project}
        phase = _Phase(self, "observe", context, timeout_s)
        result = {"ok": False, "status": "unverified", "online": False, "connection_verified": False,
                  "transport": "usb", "evidence_id": context["operation_id"], "target": target, "vendor_info": {}}
        try:
            device = self._usb(phase)
            result["usb"] = {"busid": device.busid, "vidpid": device.vidpid,
                             "state": device.state, "description": device.description}
            status = phase.object("status")
            title = _TITLE.fullmatch(status.get("title", ""))
            if (not title or "*" in status["title"] or title["version"] != VERSION
                    or not _same_project(title["project"], self.expected_project)):
                raise VendorAdapterError("Existing UI does not show the exact calibrated working project")
            result.update(ui_project_verified=True, document=title["document"],
                          reason="Read-only UI exposes no independently calibrated PLC model/version and online state")
        except EvidenceWriteError:
            raise
        except Exception as exc:
            result.update(reason=str(exc), error_type=type(exc).__name__)
        phase.save("decision", result)
        result["evidence"] = phase.evidence
        return result
