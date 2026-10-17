"""Synchronize and compile MAIN in an already open AutoShop working project.

"Native" refers to the vendor project, not a Linux compiler. This adapter never
opens/closes projects or downloads programs. Mutations require authorization,
durable evidence and the shared AutoShop/device lease. Unsupported diagnostics
return unverified rather than interpreting a successful key press as a build.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import ntpath
import re
import shutil
import struct
import tempfile
import time
import uuid
from pathlib import Path, PureWindowsPath

from .artifacts import create_build_manifest
from .evidence import StrictEvidenceLogger
from .interop import RetryPolicy, run_command, to_windows_unc
from .lease import bridge_lease
from .safety import SafetyError, check_bridge_authorization
from .windows_ocr import read_image_text


REQUIRED_ARTIFACTS = ("STTemp/MAIN.rst", "STTemp/MAIN.o", "Temp/stprog.o",
                      "Output.prg", "Output.sdt")
_TITLE = re.compile(r"^AutoShop\s+V(?P<version>\d+(?:\.\d+){1,3})\s+"
                    r"(?P<project>[A-Za-z]:\\.+?)\s+-\s+\[MAIN\s*\]$", re.I)


class NativeAutoShopError(SafetyError):
    """A prerequisite or exact editor verification failed; stop this operation."""


def _text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def windows_project_path(path: str) -> Path:
    """Map an explicit local Windows drive directory into the WSL mount."""
    win = PureWindowsPath(path)
    if not win.is_absolute() or not re.fullmatch(r"[A-Za-z]:", win.drive):
        raise NativeAutoShopError("An absolute local Windows project directory is required")
    return Path("/mnt") / win.drive[0].lower() / Path(*win.parts[1:])


def _visible_path(path: Path) -> str:
    path = path.resolve()
    if len(path.parts) > 3 and path.parts[1] == "mnt" and re.fullmatch(r"[a-zA-Z]", path.parts[2]):
        return str(PureWindowsPath(path.parts[2].upper() + ":\\", *path.parts[3:]))
    return to_windows_unc(path)


def parse_editor_title(title: str, expected_project: str, *, allow_dirty=False) -> dict:
    """Require the whole project directory and the MAIN editor, not a substring."""
    dirty = "*" in title
    match = _TITLE.fullmatch(title.replace("*", "").strip())
    if not match or ntpath.normcase(ntpath.normpath(match["project"])) != ntpath.normcase(
            ntpath.normpath(expected_project)):
        raise NativeAutoShopError("AutoShop must show the exact expected project and MAIN document")
    if dirty and not allow_dirty:
        raise NativeAutoShopError("AutoShop has unsaved editor changes; refusing to replace them")
    return {"version": match["version"], "project": match["project"], "dirty": dirty}


def analyze_compile_log(text: str) -> dict:
    """Interpret explicit error counts only; OCR ambiguity remains unverified."""
    counts = []
    for pattern in (
        r"(?<![\dA-Za-z])\(?\s*([0-9]+)\s*\)?\s*(?:个\s*)?(?:错\s*误|errors?\b)",
        r"(?:\berrors?|错\s*误(?:数)?)\s*[:=]?\s*\(?\s*([0-9]+)\s*\)?",
    ):
        counts.extend(int(match[1]) for match in re.finditer(pattern, text, re.I))
    if counts and max(counts) > 0:
        return {"status": "failed", "error_count": max(counts), "text": text}
    completion = bool(re.search(r"编译\s*(?:完成|结束|成功)|完成\s*(?:程序\s*)?编译|"
                                r"compil(?:e|ed|ation)\s+(?:completed|finished|success)", text, re.I))
    if counts and completion:
        return {"status": "verified", "error_count": 0, "text": text}
    return {"status": "unverified", "error_count": None, "text": text}


class NativeAutoShop:
    def __init__(self, cfg, *, evidence=None, runner=None, project_dir=None,
                 lease_directory=None, ocr_reader=read_image_text):
        self.cfg = cfg
        self.evidence = evidence
        self.runner = runner
        self.expected_project = cfg.bridge["windows_project"].rstrip("\\/")
        self.project_dir = (Path(project_dir) if project_dir is not None
                            else windows_project_path(self.expected_project))
        self.lease_directory = lease_directory
        self.ocr_reader = ocr_reader

    def _argv(self, action: str, value="") -> list[str]:
        argv = [self.cfg.bridge["powershell_exe"], "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", self.cfg.bridge["tools_ps1_windows"], "-Action", action,
                "-ExpectedProject", self.expected_project]
        if value:
            argv += ["-Value", value]
        return argv

    def _ui(self, action: str, value="", *, mutation=False):
        if mutation:
            self.evidence.log("native_autoshop.intent", params={"action": action,
                "project": self.expected_project, "value": value})
        result = run_command(self._argv(action, value),
                             policy=RetryPolicy(timeout_s=30, retries=0),
                             runner=self.runner, retryable=False)
        if self.evidence is not None:
            self.evidence.log("native_autoshop.action", params={"action": action},
                result={"returncode": result.returncode, "timed_out": result.timed_out,
                        "stdout_sha256": _sha(result.stdout.encode()), "stderr": result.stderr})
        if not result.ok:
            raise NativeAutoShopError(f"AutoShop {action} failed; state may be unknown: {result.summary()}")
        return result

    def _editor(self, action="editor-read", value="", *, allow_dirty=False):
        result = self._ui(action, value, mutation=action == "editor-set")
        try:
            editor = json.loads(result.stdout.strip().lstrip("\ufeff"))
            if not isinstance(editor, dict) or not isinstance(editor.get("source"), str) or not isinstance(editor.get("title"), str):
                raise ValueError("editor response needs title/source strings")
        except (ValueError, TypeError) as exc:
            raise NativeAutoShopError(f"Invalid AutoShop editor response: {exc}") from exc
        editor.update(parse_editor_title(editor["title"], self.expected_project,
                                         allow_dirty=allow_dirty))
        return editor

    def _authorize(self, authorized):
        decision = check_bridge_authorization(self.cfg, authorized)
        if not decision.granted:
            raise NativeAutoShopError(decision.reason_text)
        if not isinstance(self.evidence, StrictEvidenceLogger):
            raise NativeAutoShopError("Execution requires StrictEvidenceLogger")
        self.evidence.ensure_writable()

    def _lease(self, purpose):
        return bridge_lease(self.cfg.device["busid"], self.cfg.device["vidpid"],
                            directory=self.lease_directory, purpose=purpose,
                            evidence=self.evidence)

    @staticmethod
    def _source(source) -> tuple[Path, str, str]:
        source = Path(source).resolve()
        try:
            raw = source.read_bytes()
            text = _text(raw.decode("utf-8-sig"))
        except (OSError, UnicodeError) as exc:
            raise NativeAutoShopError(f"Cannot read UTF-8 MAIN source: {exc}") from exc
        if not text.strip() or "\x00" in text:
            raise NativeAutoShopError("MAIN source must be nonempty UTF-8 text without NUL")
        return source, text, _sha(raw)

    def _sync(self, source, *, execute):
        path, wanted, source_hash = self._source(source)
        editor = self._editor()
        before = _text(editor["source"])
        diff = "".join(difflib.unified_diff(before.splitlines(keepends=True),
            wanted.splitlines(keepends=True), fromfile="AutoShop/MAIN", tofile=str(path)))
        result = {"schema_version": 1, "ok": True, "status": "preview",
                  "executed": False, "changed": before != wanted, "diff": diff,
                  "source": str(path), "source_sha256": source_hash,
                  "source_text_sha256": _sha(wanted.encode()),
                  "project": self.expected_project, "autoshop_version": editor["version"]}
        if not execute:
            return result
        if editor.get("read_only") is True:
            raise NativeAutoShopError("AutoShop ST editor is read-only; exit online monitoring before synchronizing")
        if before != wanted:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".st", encoding="utf-8",
                                             newline="\n") as staged:
                staged.write(wanted)
                staged.flush()
                written = self._editor("editor-set", _visible_path(Path(staged.name)), allow_dirty=True)
            if _text(written["source"]) != wanted:
                raise NativeAutoShopError("Editor readback differs from requested source; stopped before save")
            # Query independently; editor-set's response alone is not sufficient.
            if _text(self._editor(allow_dirty=True)["source"]) != wanted:
                raise NativeAutoShopError("Independent editor readback differs; stopped before save")
            self._ui("keys", "^s", mutation=True)
            deadline = time.monotonic() + 5
            while True:
                saved = self._editor(allow_dirty=True)
                if _text(saved["source"]) != wanted:
                    raise NativeAutoShopError("Saved editor contents differ from requested source")
                if not saved["dirty"]:
                    break
                if time.monotonic() >= deadline:
                    raise NativeAutoShopError("AutoShop editor remains unsaved after Ctrl+S")
                time.sleep(0.1)
        result.update(status="verified", executed=True)
        self.evidence.log("native_autoshop.sync", params={"source": str(path)},
                          result={key: value for key, value in result.items() if key != "diff"})
        return result

    def sync(self, source, *, execute=False, authorized=False) -> dict:
        """Preview a MAIN diff; execution saves only after exact text readback."""
        if not execute:
            return self._sync(source, execute=False)
        self._authorize(authorized)
        with self._lease("native-project-sync"):
            return self._sync(source, execute=True)

    def _quarantine(self) -> Path:
        root = self.project_dir.resolve()
        for relative in REQUIRED_ARTIFACTS:
            candidate = root / relative
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
                raise NativeAutoShopError(f"Build artifact path escapes the working project: {relative}")
            if candidate.exists() and not candidate.is_file():
                raise NativeAutoShopError(f"Build artifact is not a regular file: {relative}")
        quarantine = root / ".plcidf" / "quarantine" / uuid.uuid4().hex
        if not quarantine.resolve().is_relative_to(root):
            raise NativeAutoShopError("Quarantine directory escapes the working project")
        self.evidence.log("native_autoshop.quarantine.intent", params={"project": str(root),
                          "artifacts": list(REQUIRED_ARTIFACTS), "destination": str(quarantine)})
        for relative in REQUIRED_ARTIFACTS:
            old = root / relative
            if old.exists():
                destination = quarantine / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(old, destination)
        self.evidence.log("native_autoshop.quarantine", result={"path": str(quarantine)})
        return quarantine

    def _log(self) -> dict:
        try:
            raw = self._ui("log").stdout.strip()
            screenshot = re.search(r"(?m)^SCREENSHOT:(.+)$", raw)
            if screenshot:
                ocr = self.ocr_reader(screenshot[1].strip(),
                    powershell_exe=self.cfg.bridge["powershell_exe"], runner=self.runner)
                result = analyze_compile_log(ocr.text if ocr.ok else "")
                result.update(source="windows-ocr", screenshot=screenshot[1].strip(),
                              ocr=ocr.as_dict())
                return result
            return {**analyze_compile_log(raw), "source": "autoshop-log"}
        except NativeAutoShopError as exc:
            return {"status": "unverified", "source": "unavailable", "error": str(exc)}

    def compile(self, source, *, execute=False, authorized=False, working_project=None,
                target=None, tool_versions=None, timeout_s=60.0, poll_interval_s=0.25) -> dict:
        """Sync, isolate stale products, compile once and verify fresh products.

        working_project is an explicit Windows working-copy directory and must
        equal the already-open configured project. No original is auto-opened.
        The manifest snapshots the vendor project excluding generated/cache
        directories; its source inputs therefore include MAIN.ST and variables.
        """
        if not execute:
            return {"schema_version": 1, "ok": True, "status": "preview",
                    "executed": False, "sync": self._sync(source, execute=False),
                    "required_artifacts": list(REQUIRED_ARTIFACTS)}
        if not working_project or ntpath.normcase(ntpath.normpath(str(working_project))) != ntpath.normcase(
                ntpath.normpath(self.expected_project)):
            raise NativeAutoShopError("Compilation requires the exact explicit working-project directory")
        if not self.project_dir.is_dir():
            raise NativeAutoShopError("Working project directory is not accessible from WSL")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 120:
            raise NativeAutoShopError("Compile timeout must be in (0, 120] seconds")
        if not 0 < poll_interval_s <= 5:
            raise NativeAutoShopError("Compile polling interval must be in (0, 5] seconds")
        self._authorize(authorized)
        with self._lease("native-project-compile"):
            synced = self._sync(source, execute=True)
            quarantine = self._quarantine()
            # Recheck identity after local file operations and before F7.
            self._editor()
            self._ui("keys", "{F7}", mutation=True)
            deadline = time.monotonic() + timeout_s
            previous = None
            stable = False
            while time.monotonic() < deadline:
                files = [self.project_dir / relative for relative in REQUIRED_ARTIFACTS]
                if all(path.is_file() and path.stat().st_size > 0 for path in files):
                    observed = [(path.stat().st_size, path.stat().st_mtime_ns) for path in files]
                    if observed == previous:
                        stable = True
                        break
                    previous = observed
                time.sleep(poll_interval_s)
            log = self._log()
            result = {"schema_version": 1, "ok": False, "status": "unverified",
                      "executed": True, "sync": synced, "quarantine": str(quarantine),
                      "diagnostics": log, "manifest": None,
                      "missing_artifacts": [relative for relative in REQUIRED_ARTIFACTS
                                             if not (self.project_dir / relative).is_file()]}
            if log["status"] == "failed":
                result.update(status="failed", reason="vendor-reported-compile-errors")
            elif not stable:
                result["reason"] = "fresh-complete-artifacts-not-observed"
            else:
                marker = _text((self.project_dir / REQUIRED_ARTIFACTS[0]).read_text(encoding="utf-8-sig"))
                elf_valid = True
                for relative in ("STTemp/MAIN.o", "Temp/stprog.o"):
                    header = (self.project_dir / relative).read_bytes()[:52]
                    elf_valid &= (len(header) == 52 and header[:6] == b"\x7fELF\x01\x01"
                                  and struct.unpack_from("<H", header, 18)[0] == 40)
                if marker != "Success!\n" or not elf_valid:
                    result["reason"] = "compiler-marker-or-arm-elf-invalid"
                elif log["status"] != "verified":
                    result["reason"] = "vendor-link-result-not-verified"
                else:
                    # Reject user edits/window changes that occurred during the build.
                    editor = self._editor()
                    if _sha(_text(editor["source"]).encode()) != synced["source_text_sha256"]:
                        raise NativeAutoShopError("Editor source changed during compilation")
                    versions = dict(tool_versions or {})
                    versions["autoshop"] = synced["autoshop_version"]
                    actual_target = target or {"busid": self.cfg.device["busid"],
                        "vidpid": self.cfg.device["vidpid"]}
                    manifest = create_build_manifest(self.project_dir,
                        [self.project_dir / relative for relative in REQUIRED_ARTIFACTS],
                        target=actual_target, tool_versions=versions,
                        # AutoShop exclusively locks its .hcp file while open.
                        # Capture the actual native source/configuration files,
                        # excluding packed archives and generated/cache trees.
                        source_paths=[p.name for p in self.project_dir.iterdir()
                                      if p.is_file() and p.suffix.lower() not in {".hcp", ".hcpp", ".log"}],
                        exclude_paths=["STTemp", "Temp", ".plcidf", "Compile",
                                       "InteractiveFile", "BackUP", "OnlineVersion"])
                    result.update(ok=True, status="verified", manifest=manifest)
            self.evidence.log("native_autoshop.compile", params={"source": str(source)},
                result={"ok": result["ok"], "status": result["status"],
                        "reason": result.get("reason"), "diagnostics": log,
                        "build_hash": (result["manifest"] or {}).get("build_hash")})
            return result
