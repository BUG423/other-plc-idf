"""Fail-closed orchestration for a calibrated vendor download adapter.

This module contains no GUI, subprocess, network or device implementation.
Without a calibrated adapter it returns ``unavailable`` without downloading.
``authorized=True`` carries authorization already obtained by the caller; no
additional approval token or configuration switch is introduced here.

Adapter protocol (all methods receive keyword ``timeout_s`` except describe):

* describe() -> {name: str, calibrated: bool, capabilities:
  {download: bool, vendor_result: bool, program_compare: bool}}.
* preflight(context, *, timeout_s) -> observation below, plus usb_state:
  'Shared'/'Not shared', identity_ambiguous: false, unsaved_changes: false,
  auto_run: false. It must observe the actual open working project and USB.
* download(context, *, timeout_s) -> observation with status 'completed'.
  This is called at most once. It must not request RUN or retry a download.
* verify_download(context, download_result, *, timeout_s) -> observation with
  status 'verified' only for the calibrated vendor completion result.
* compare_program(context, *, timeout_s) -> a separate observation with
  status 'verified', matches: true, independent: true, method:
  'vendor-program-compare' or 'upload-byte-compare'. A download message or
  CPU RUN state alone is not an independent program comparison.

Every observation binds operation_id, build_hash, target, working_project and
evidence:[{path, sha256, source}]. Use retained files for each observation;
evidence is rehashed locally, including again before reporting success. The adapter
must impose hard timeouts on its own external operations; this orchestrator
passes a remaining time budget and never replays an uncertain operation.
"""

from __future__ import annotations

import hashlib
import json
import math
import ntpath
import re
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path, PureWindowsPath

from .artifacts import normalize_target, verify_build_manifest
from .evidence import EvidenceWriteError, StrictEvidenceLogger
from .lease import LeaseBusy, bridge_lease
from .safety import SafetyError


BUILD_SCHEMA = "plcidf.native-build.v1"
DEPLOY_SCHEMA = "plcidf.native-deploy.v1"
_REQUIRED = {"STTemp/MAIN.rst", "STTemp/MAIN.o", "Temp/stprog.o", "Output.prg", "Output.sdt"}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class NativeDeployError(SafetyError):
    """A required fact, identity, artifact or adapter observation is invalid."""


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise NativeDeployError(f"Deployment records must contain JSON values: {exc}") from exc


def _copy(value):
    return json.loads(_json(value))


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _required_hash(value, label):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise NativeDeployError(f"{label} requires a recorded SHA-256")
    return value


def _windows(path):
    if not isinstance(path, str) or not PureWindowsPath(path).is_absolute():
        raise NativeDeployError("An explicit absolute Windows working-project directory is required")
    return ntpath.normcase(ntpath.normpath(path))


def _file(path, expected=None):
    path = Path(path)
    if not path.is_file():
        raise NativeDeployError(f"Required evidence/backup file is missing: {path}")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns):
        raise NativeDeployError(f"File changed while hashing: {path}")
    actual = digest.hexdigest()
    if expected is not None and (not isinstance(expected, str) or not _SHA256.fullmatch(expected)
                                 or expected != actual):
        raise NativeDeployError(f"Evidence/backup hash mismatch: {path}")
    return {"path": str(path.resolve()), "sha256": actual, "bytes": after.st_size}


def _evidence(refs):
    if not isinstance(refs, list) or not refs:
        raise NativeDeployError("A calibrated observation requires durable evidence references")
    checked = []
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get("source"), str) or not ref["source"].strip():
            raise NativeDeployError("Every evidence reference requires a source")
        if not isinstance(ref.get("path"), str) or not ref.get("sha256"):
            raise NativeDeployError("Every evidence reference requires a path and SHA-256")
        checked.append({**ref, **_file(ref["path"], ref["sha256"])})
    return checked


def verified_build_record(compilation_result, working_project):
    """Wrap an actual verified compile result; a bare artifact list is refused.

    This is a reproducibility record, not a signed compiler attestation. The
    caller remains responsible for sourcing the result from the vendor build.
    """
    result = _copy(compilation_result)
    if not isinstance(result, dict):
        raise NativeDeployError("A verified vendor compilation result is required")
    if result.get("schema") == BUILD_SCHEMA:
        content = {key: value for key, value in result.items() if key != "record_hash"}
        if result.get("record_hash") != _digest(content):
            raise NativeDeployError("Vendor build record integrity check failed")
        if _windows(result.get("working_project")) != _windows(working_project):
            raise NativeDeployError("Vendor build record names a different working project")
        result = result.get("compilation")
    if not isinstance(result, dict) or result.get("ok") is not True or result.get("status") != "verified":
        raise NativeDeployError("The vendor compilation must be explicitly verified")
    manifest = result.get("manifest")
    verify_build_manifest(manifest)
    sync, diagnostics = result.get("sync"), result.get("diagnostics")
    if (not isinstance(sync, dict) or sync.get("status") != "verified"
            or sync.get("executed") is not True
            or _windows(sync.get("project")) != _windows(working_project)):
        raise NativeDeployError("Verified source synchronization to the exact working project is required")
    _file(sync.get("source", ""), _required_hash(sync.get("source_sha256"), "Synchronized source"))
    _required_hash(sync.get("source_text_sha256"), "Synchronized source text")
    if (not isinstance(diagnostics, dict) or diagnostics.get("status") != "verified"
            or type(diagnostics.get("error_count")) is not int or diagnostics["error_count"] != 0
            or not isinstance(diagnostics.get("source"), str) or not diagnostics["source"]):
        raise NativeDeployError("A verified zero-error vendor compilation result is required")
    root = Path(manifest["source"]["root"]).resolve()
    paths = {}
    for artifact in manifest["artifacts"]:
        path = Path(artifact["path"]).resolve()
        if not path.is_relative_to(root):
            raise NativeDeployError("Vendor artifacts must belong to the bound working project")
        paths[path.relative_to(root).as_posix()] = path
    if not _REQUIRED.issubset(paths):
        raise NativeDeployError("The vendor build is missing required application artifacts")
    if paths["STTemp/MAIN.rst"].read_bytes() not in {b"Success!\r\n", b"Success!\n"}:
        raise NativeDeployError("The vendor compiler success marker no longer matches")
    for name in ("STTemp/MAIN.o", "Temp/stprog.o"):
        with paths[name].open("rb") as fh:
            header = fh.read(52)
        if len(header) != 52 or header[:6] != b"\x7fELF\x01\x01" or header[18:20] != b"\x28\x00":
            raise NativeDeployError("Vendor application objects must be ELF32 little-endian ARM")
    record = {"schema": BUILD_SCHEMA, "working_project": working_project,
              "compilation": {"ok": True, "status": "verified", "manifest": manifest,
                              "sync": sync, "diagnostics": diagnostics}}
    record["record_hash"] = _digest(record)
    return record


class NativeDeploy:
    """Single-attempt download/verification using explicitly calibrated adapters."""

    def __init__(self, cfg, *, evidence=None, adapter=None, project_dir=None, lease_directory=None):
        self.cfg, self.evidence, self.adapter = cfg, evidence, adapter
        self.project_dir = Path(project_dir).resolve() if project_dir is not None else None
        self.lease_directory = lease_directory

    def _prepare(self, build_record, working_project, backup, facts):
        if _windows(working_project) != _windows(self.cfg.bridge["windows_project"]):
            raise NativeDeployError("Working project must exactly match the configured project")
        record = verified_build_record(build_record, working_project)
        manifest = record["compilation"]["manifest"]
        win = PureWindowsPath(working_project)
        if self.project_dir is None:
            if not re.fullmatch(r"[A-Za-z]:", win.drive):
                raise NativeDeployError("Default working-project mapping requires a local Windows drive")
            expected_root = Path("/mnt") / win.drive[0].lower() / Path(*win.parts[1:])
        else:
            expected_root = self.project_dir
        if Path(manifest["source"]["root"]).resolve() != expected_root.resolve():
            raise NativeDeployError("Build source root differs from the explicit working copy")
        target = normalize_target(manifest["target"])
        configured = normalize_target({"busid": self.cfg.device["busid"], "vidpid": self.cfg.device["vidpid"]})
        if any(target[key] != configured[key] for key in ("busid", "vidpid")):
            raise NativeDeployError("Build USB BUSID and VID:PID must both match the configured target")
        facts = _copy(facts)
        if not isinstance(facts, dict) or facts.get("power_source") not in {"usb-only", "24v"}:
            raise NativeDeployError("Record the observed power_source as usb-only or 24v")
        if facts.get("outputs_isolated") is not True or facts.get("mode_switch") not in {"RUN", "STOP", "REMOTE"}:
            raise NativeDeployError("Record isolated outputs and the actual physical mode-switch position")
        if facts.get("application_scope") not in {"memory-only-probe", "vendor-application"}:
            raise NativeDeployError("Record the application's declared validation scope")
        if facts["power_source"] == "usb-only" and facts["application_scope"] != "memory-only-probe":
            raise NativeDeployError("USB-only experiments are limited to memory-only probe applications")
        if not isinstance(facts.get("source"), str) or not facts["source"].strip():
            raise NativeDeployError("Physical facts require their observation source")
        try:
            observed = datetime.fromisoformat(facts.get("observed_at", "").replace("Z", "+00:00"))
            if observed.tzinfo is None:
                raise ValueError("timezone required")
        except (TypeError, ValueError, AttributeError) as exc:
            raise NativeDeployError("Physical facts require an ISO timestamp with timezone") from exc
        backup = _copy(backup)
        if not isinstance(backup, dict) or backup.get("kind") != "plc-upload":
            raise NativeDeployError("A recorded PLC upload backup is required")
        if normalize_target(backup.get("target")) != target:
            raise NativeDeployError("PLC upload backup belongs to a different target")
        backup.update(_file(backup.get("path", ""), _required_hash(backup.get("sha256"), "PLC upload backup")))
        try:
            with zipfile.ZipFile(backup["path"]) as archive:
                files = [item for item in archive.infolist() if not item.is_dir()]
                if not files:
                    raise NativeDeployError("PLC upload backup ZIP contains no files")
                if "file_count" in backup and (type(backup["file_count"]) is not int
                                               or backup["file_count"] != len(files)):
                    raise NativeDeployError("PLC upload backup file count changed")
                backup.update(file_count=len(files), restore_tested=False)
        except zipfile.BadZipFile as exc:
            raise NativeDeployError("PLC upload backup is not a readable ZIP") from exc
        context = {"operation_id": uuid.uuid4().hex, "build_hash": manifest["build_hash"],
                   "build_record_hash": record["record_hash"], "target": target,
                   "working_project": working_project, "source_root": str(expected_root.resolve()),
                   "backup": backup, "physical_facts": facts, "auto_run": False,
                   "artifacts": manifest["artifacts"], "source_tree_sha256": manifest["source"]["tree_sha256"]}
        context["plan_hash"] = _digest(context)
        return record, context

    def _availability(self):
        if self.adapter is None:
            return {"available": False, "reason": "No calibrated vendor download adapter is installed"}
        if not callable(getattr(self.adapter, "describe", None)):
            return {"available": False, "reason": "Adapter does not declare calibrated capabilities"}
        try:
            description = _copy(self.adapter.describe())
        except Exception as exc:
            return {"available": False, "reason": f"Adapter calibration metadata unavailable: {exc}"}
        if (not isinstance(description, dict) or description.get("calibrated") is not True
                or not isinstance(description.get("name"), str) or not description["name"]
                or not isinstance(description.get("capabilities"), dict)
                or not all(description["capabilities"].get(name) is True
                           for name in ("download", "vendor_result", "program_compare"))
                or not all(callable(getattr(self.adapter, name, None))
                           for name in ("preflight", "download", "verify_download", "compare_program"))):
            return {"available": False, "reason": "Vendor download/result/program-comparison adapter is not calibrated",
                    "description": description}
        return {"available": True, "description": description}

    @staticmethod
    def _observation(observation, context):
        observation = _copy(observation)
        if not isinstance(observation, dict):
            raise NativeDeployError("Adapter observation must be a JSON object")
        for key in ("operation_id", "build_hash"):
            if observation.get(key) != context[key]:
                raise NativeDeployError(f"Adapter observation does not match {key}")
        if (normalize_target(observation.get("target")) != context["target"]
                or _windows(observation.get("working_project")) != _windows(context["working_project"])):
            raise NativeDeployError("Adapter observation targets a different PLC or working project")
        if observation.get("status") not in {"verified", "completed", "failed", "unknown", "unavailable"}:
            raise NativeDeployError("Adapter observation has an unsupported status")
        observation["evidence"] = _evidence(observation.get("evidence"))
        if observation.get("run_requested", False) is not False:
            raise NativeDeployError("This download operation must never request RUN")
        return observation

    def deploy(self, build_record, *, working_project, backup, preflight, execute=False,
               authorized=False, timeout_s=120.0):
        """Bind facts and a verified build, then perform one download and checks.

        preflight is an observation record, not an approval: power_source,
        outputs_isolated, mode_switch, application_scope, source, observed_at.
        USB-only is accepted for memory-only probes. Physical I/O, retention,
        real-time behavior and CPU execution require their own evidence.
        """
        result = {"schema": DEPLOY_SCHEMA, "schema_version": 1, "ok": False,
                  "status": "blocked", "stage": "prepare", "executed": False,
                  "download_attempts": 0, "state_may_have_changed": False,
                  "download": {"status": "not-run"}, "vendor_verification": {"status": "not-run"},
                  "program_consistency": {"status": "not-run"},
                  "runtime": {"status": "unobserved", "state": "unknown", "run_requested": False,
                              "stop_requested": False, "request_source": "orchestrator"},
                  "validation_limits": ["physical-io-unverified", "power-loss-retention-unverified",
                                        "real-time-unverified", "cpu-execution-unverified"]}
        try:
            if type(execute) is not bool:
                raise NativeDeployError("execute must be an explicit boolean")
            if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 600:
                raise NativeDeployError("Deployment time budget must be in (0, 600] seconds")
            record, context = self._prepare(build_record, working_project, backup, preflight)
            result["plan"] = _copy(context)
            result.update({key: context[key] for key in (
                "operation_id", "build_hash", "plan_hash", "target", "working_project")})
            result["adapter"] = self._availability()
            if not result["adapter"]["available"]:
                result.update(status="unavailable", reason=result["adapter"]["reason"])
                return result
            if not execute:
                result.update(ok=True, status="preview")
                return result
            if authorized is not True:
                raise NativeDeployError("Caller must pass the authorization already obtained for this operation")
            if not isinstance(self.evidence, StrictEvidenceLogger):
                raise NativeDeployError("Deployment requires durable StrictEvidenceLogger")
            self.evidence.ensure_writable()
            deadline = time.monotonic() + timeout_s

            def call(method, *arguments, mutation=False):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Deployment time budget exhausted")
                if mutation:
                    result.update(executed=True, download_attempts=1, state_may_have_changed=True)
                observed = method(_copy(context), *arguments, timeout_s=remaining)
                if time.monotonic() > deadline:
                    raise TimeoutError("Adapter exceeded the deployment time budget")
                return self._observation(observed, context)

            with bridge_lease(context["target"]["busid"], context["target"]["vidpid"],
                              directory=self.lease_directory, purpose="native-vendor-deploy",
                              evidence=self.evidence):
                result["stage"] = "preflight"
                observed = call(self.adapter.preflight)
                result["observed_preflight"] = observed
                self.evidence.log("native_deploy.preflight",
                                  params={"operation_id": context["operation_id"]}, result=observed)
                if (observed["status"] != "verified" or observed.get("identity_ambiguous") is not False
                        or str(observed.get("usb_state", "")).strip().lower() not in {"shared", "not shared"}
                        or observed.get("unsaved_changes") is not False or observed.get("auto_run") is not False):
                    result.update(status="unavailable", reason="Current USB/project/download settings are not verified")
                    return result
                # Rehash immediately before side effects; adapter preflight can
                # take time and must not silently change the active source.
                verified_build_record(record, working_project)
                _file(context["backup"]["path"], context["backup"]["sha256"])
                self.evidence.log("native_deploy.intent", params=context,
                                  result={"authorized": True, "auto_run": False})
                result["stage"] = "download"
                result["download"] = call(self.adapter.download, mutation=True)
                self.evidence.log("native_deploy.download", params={"operation_id": context["operation_id"]},
                                  result=result["download"])
                if result["download"]["status"] not in {"completed", "verified"}:
                    result.update(status="failed" if result["download"]["status"] == "failed" else "unknown",
                                  reason="Download completion was not established; do not automatically repeat")
                    return result
                result["stage"] = "vendor-verification"
                result["vendor_verification"] = call(self.adapter.verify_download, _copy(result["download"]))
                self.evidence.log("native_deploy.vendor-verification", result=result["vendor_verification"])
                if result["vendor_verification"]["status"] != "verified":
                    result.update(status="unverified", reason="Vendor download result is not verified")
                    return result
                result["stage"] = "program-consistency"
                comparison = call(self.adapter.compare_program)
                result["program_consistency"] = comparison
                valid = (comparison["status"] == "verified" and comparison.get("matches") is True
                         and comparison.get("independent") is True
                         and comparison.get("method") in {"vendor-program-compare", "upload-byte-compare"})
                vendor_hashes = {ref["sha256"] for ref in result["vendor_verification"]["evidence"]}
                if not any(ref["sha256"] not in vendor_hashes for ref in comparison["evidence"]):
                    valid = False
                if comparison.get("method") == "upload-byte-compare":
                    artifacts = {item["path"]: item["sha256"] for item in context["artifacts"]}
                    expected = artifacts.get(comparison.get("artifact_path"))
                    valid &= bool(expected and comparison.get("expected_sha256") == expected
                                  and comparison.get("observed_sha256") == expected
                                  and any(ref["sha256"] == expected for ref in comparison["evidence"]))
                self.evidence.log("native_deploy.program-consistency", result=comparison)
                if not valid:
                    result.update(status="unverified", reason="Independent program consistency was not established")
                    return result
                # A comparison against changing local files cannot validate the
                # program that was originally selected for download.
                verified_build_record(record, working_project)
                _file(context["backup"]["path"], context["backup"]["sha256"])
                for observation in (observed, result["download"], result["vendor_verification"], comparison):
                    _evidence(observation["evidence"])
                result.update(ok=True, status="verified", stage="complete")
                self.evidence.log("native_deploy.complete", params={"operation_id": context["operation_id"]},
                                  result={"ok": True, "build_hash": context["build_hash"],
                                          "program_consistency": "verified", "runtime": result["runtime"]})
                return result
        except Exception as exc:
            result.update(ok=False, status="unknown" if result["download_attempts"] else
                          "busy" if isinstance(exc, LeaseBusy) else "blocked",
                          reason=str(exc), error_type=type(exc).__name__)
            if isinstance(exc, EvidenceWriteError):
                result["evidence_write_failed"] = True
            return result
