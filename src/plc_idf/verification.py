"""One bounded, source-bound ST build/deploy/replay verification workflow.

Scenario assertions concern deterministic logic under virtual IEC scan time.
They do not validate physical I/O, target hardware or real-time deadlines.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

from .open_project import check_open_project
from .project import MANIFEST

SCENARIO_SCHEMA = "plcidf.scenarios.v1"
REPORT_SCHEMA = "plcidf.verification.v1"
MAX_SCENARIOS = 100
MAX_FRAMES_PER_SCENARIO = 10000
MAX_TOTAL_FRAMES = 100000
MAX_SCENARIO_BYTES = 8 * 1024 * 1024
MAX_EVIDENCE_BYTES = 128 * 1024 * 1024
MAX_EVIDENCE_LINE = 1024 * 1024
HASH_KEYS = ("build_hash", "program_hash", "source_sha256", "binary_sha256")
LEGACY_SIGNALS = {"inputs": {}, "outputs": {"COUNTER": "DINT", "HEARTBEAT": "BOOL"}}


class VerificationError(ValueError):
    def __init__(self, code, message, **context):
        super().__init__(message)
        self.code, self.context = code, context


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _constant(value):
    raise ValueError("non-finite JSON number: " + value)


def _float(value):
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError("overflowing JSON number: " + value)
    return converted


def _json(text):
    return json.loads(text, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path, limit=None):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise VerificationError("E_FILE", "input must be a regular file", file=str(path))
        if limit is not None and before.st_size > limit:
            raise VerificationError("E_LIMIT", "input exceeds the byte limit", file=str(path))
        data = stream.read() if limit is None else stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or len(data) != before.st_size:
        raise VerificationError("E_INPUT_CHANGED", "input changed during reading", file=str(path))
    if limit is not None and len(data) > limit:
        raise VerificationError("E_LIMIT", "input exceeds the byte limit", file=str(path))
    return data


def _within(root, supplied):
    candidate = Path(supplied).expanduser()
    candidate = candidate if candidate.is_absolute() else root / candidate
    if not candidate.resolve().is_relative_to(root):
        raise VerificationError("E_PATH", "scenario path must stay inside the project", file=str(candidate))
    return candidate.absolute()


def _report_destination(root, supplied, protected):
    if supplied is None:
        return None
    candidate = Path(supplied).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
        if not candidate.resolve().is_relative_to(root):
            raise VerificationError("E_REPORT_PATH", "relative report path must stay inside the project")
    candidate = candidate.absolute()
    # Reject symlinks at every explicit component, including an existing target.
    for component in [candidate, *candidate.parents]:
        if component.is_symlink():
            raise VerificationError("E_REPORT_PATH", "report path must not traverse symlinks", file=str(candidate))
    if candidate.suffix.lower() == ".st" or candidate.resolve() in {p.resolve() for p in protected}:
        raise VerificationError("E_REPORT_PATH", "report must not overwrite an input or ST source", file=str(candidate))
    if candidate.exists():
        if not candidate.is_file():
            raise VerificationError("E_REPORT_PATH", "report target must be a regular file", file=str(candidate))
        for path in protected:
            if path.exists() and os.path.samefile(candidate, path):
                raise VerificationError("E_REPORT_PATH", "report aliases a protected input", file=str(candidate))
    return candidate


def _write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Recheck the destination after any build/replay calls that may take time.
    if any(part.is_symlink() for part in [path, *path.parents]):
        raise VerificationError("E_REPORT_PATH", "report path changed to a symlink", file=str(path))
    fd, temporary = tempfile.mkstemp(prefix=".verification-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + " must be a finite number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(name + " must be a finite number")
    return value


def _expectation(value, type_name, validate_value):
    if isinstance(value, dict):
        if type_name not in ("REAL", "LREAL") or set(value) != {"approx", "abs_tol"}:
            raise ValueError("approx/abs_tol is supported only for REAL/LREAL outputs")
        expected = validate_value(value["approx"], type_name)
        tolerance = _number(value["abs_tol"], "abs_tol")
        if tolerance < 0:
            raise ValueError("abs_tol must be nonnegative")
        return {"approx": expected, "abs_tol": tolerance}
    return validate_value(value, type_name)


def _scenarios(document, signals, validate_value, path):
    if not isinstance(document, dict) or set(document) != {"schema", "scenarios"} or document.get("schema") != SCENARIO_SCHEMA:
        raise VerificationError("E_SCENARIOS", "expected a scenarios.v1 object with only schema/scenarios", file=str(path))
    entries = document["scenarios"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_SCENARIOS:
        raise VerificationError("E_SCENARIOS", "scenario count must be in 1..100", file=str(path))
    names, normalized, total = set(), [], 0
    for scenario_index, entry in enumerate(entries, 1):
        context = {"file": str(path), "scenario": entry["name"] if isinstance(entry, dict)
                   and isinstance(entry.get("name"), str) else scenario_index}
        if not isinstance(entry, dict) or set(entry) != {"name", "steps"}:
            raise VerificationError("E_SCENARIO", "each scenario requires only name/steps", **context)
        name = entry["name"]
        if not isinstance(name, str) or not name.strip() or name != name.strip() or len(name) > 128 or name in names:
            raise VerificationError("E_SCENARIO", "scenario names must be nonempty, trimmed and unique", **context)
        names.add(name)
        steps = entry["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_FRAMES_PER_SCENARIO:
            raise VerificationError("E_STEPS", "scenario needs a bounded nonempty steps list", **context)
        frame_count, result_steps = 0, []
        for step_index, step in enumerate(steps, 1):
            context["step"] = step_index
            if not isinstance(step, dict) or not {"scans", "expect"}.issubset(step) or set(step) - {"scans", "inputs", "expect"}:
                raise VerificationError("E_STEP", "step requires scans/expect and optional inputs only", **context)
            count = step["scans"]
            if type(count) is not int or not 1 <= count <= MAX_FRAMES_PER_SCENARIO:
                raise VerificationError("E_SCANS", "scans must be an integer in 1..10000", **context)
            frame_count += count
            total += count
            if frame_count > MAX_FRAMES_PER_SCENARIO or total > MAX_TOTAL_FRAMES:
                raise VerificationError("E_LIMIT", "scenario or total frame limit exceeded", **context)
            inputs, expected = step.get("inputs", {}), step["expect"]
            if not isinstance(inputs, dict) or set(inputs) - signals["inputs"].keys():
                raise VerificationError("E_INPUT_SIGNAL", "unknown input signal or malformed inputs", **context)
            if not isinstance(expected, dict) or not expected or set(expected) - signals["outputs"].keys():
                raise VerificationError("E_OUTPUT_SIGNAL", "expect must name at least one declared output", **context)
            try:
                checked_inputs = {key: validate_value(value, signals["inputs"][key]) for key, value in inputs.items()}
                checked_expected = {key: _expectation(value, signals["outputs"][key], validate_value)
                                    for key, value in expected.items()}
            except (ValueError, TypeError, OverflowError) as error:
                raise VerificationError("E_VALUE", str(error), **context) from error
            result_steps.append({"scans": count, "inputs": checked_inputs,
                                 "expect": checked_expected, "last_scan": frame_count})
        normalized.append({"name": name, "steps": result_steps, "frames": frame_count})
    return normalized


def _stable(bindings):
    for binding in bindings.values():
        if _sha(_read(binding["path"])) != binding["sha256"]:
            raise VerificationError("E_INPUT_CHANGED", "verification input changed", file=binding["path"])


def _hashes(result, expected=None):
    if not isinstance(result, dict):
        raise VerificationError("E_IDENTITY", "backend result must be an object")
    values = {}
    for key in HASH_KEYS:
        value = result.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise VerificationError("E_IDENTITY", "missing or invalid backend identity: " + key)
        if expected is not None and value != expected[key]:
            raise VerificationError("E_IDENTITY", "backend identity mismatch: " + key,
                                    expected=expected[key], actual=value)
        values[key] = value
    return values


def _samples(result, frame_count, identities, signals, cycle_ms, validate_value):
    _hashes(result, identities)
    run_id = result.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise VerificationError("E_IDENTITY", "replay run_id is missing")
    samples = result.get("samples")
    if not isinstance(samples, list) or len(samples) != frame_count:
        raise VerificationError("E_SAMPLES", "replay must return exactly one sample per frame",
                                expected=frame_count, actual=len(samples) if isinstance(samples, list) else None)
    all_signals = {**signals["inputs"], **signals["outputs"]}
    for index, sample in enumerate(samples, 1):
        if not isinstance(sample, dict) or type(sample.get("scan_count")) is not int or sample["scan_count"] != index:
            raise VerificationError("E_SCAN", "sample scan_count must be contiguous from 1", scan=index)
        if sample.get("run_id") != run_id:
            raise VerificationError("E_IDENTITY", "sample run_id mismatch", scan=index)
        _hashes(sample, identities)
        elapsed = sample.get("elapsed_ns")
        if type(elapsed) is not int or elapsed != (index - 1) * cycle_ms * 1000000:
            raise VerificationError("E_IEC_TIME", "sample virtual IEC time mismatch", scan=index)
        values = sample.get("values")
        if not isinstance(values, dict) or set(values) != set(all_signals):
            raise VerificationError("E_SAMPLE_VALUES", "sample values must contain all declared signals", scan=index)
        try:
            for name, type_name in all_signals.items():
                validate_value(values[name], type_name)
        except (ValueError, TypeError, OverflowError) as error:
            raise VerificationError("E_SAMPLE_VALUES", str(error), scan=index) from error
    evidence_path = result.get("samples_path")
    if not isinstance(evidence_path, str) or not evidence_path or not Path(evidence_path).is_absolute():
        raise VerificationError("E_EVIDENCE", "replay requires an absolute samples evidence path")
    path = Path(evidence_path)
    if path.is_symlink():
        raise VerificationError("E_EVIDENCE", "symlink sample evidence refused", file=str(path))
    digest = hashlib.sha256()
    count, total = 0, 0
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_EVIDENCE_BYTES:
            raise VerificationError("E_EVIDENCE", "sample evidence is not a bounded regular file", file=str(path))
        while True:
            line = stream.readline(MAX_EVIDENCE_LINE + 1)
            if not line:
                break
            count += 1
            total += len(line)
            if count > frame_count or len(line) > MAX_EVIDENCE_LINE or not line.endswith(b"\n"):
                raise VerificationError("E_EVIDENCE", "sample evidence contains excess, partial or oversized lines", file=str(path))
            digest.update(line)
            try:
                actual = _json(line.decode("utf-8"))
            except (ValueError, UnicodeError) as error:
                raise VerificationError("E_EVIDENCE", str(error), file=str(path), scan=count) from error
            # Python equality equates True with 1; evidence equality must retain
            # scalar types, including BOOL versus integer observations.
            if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(samples[count - 1], sort_keys=True, allow_nan=False):
                raise VerificationError("E_EVIDENCE", "persisted sample differs from replay result", file=str(path), scan=count)
        after = os.fstat(stream.fileno())
    if count != frame_count or total != before.st_size or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise VerificationError("E_EVIDENCE", "sample evidence is incomplete or changed while reading", file=str(path))
    return samples, {"path": str(path), "sha256": digest.hexdigest(), "bytes": total, "samples": count}


def verify_project(project, backend, *, scenarios_path=None, build_dir=None,
                   report_path=None, timeout_s=5):
    """Check all scenarios first, then build, deploy and replay real ST once each.

    The backend is supplied by the caller. replay() must be synchronous, bounded
    by timeout_s and responsible for terminating/reaping its own child process.
    No failed preflight starts a build or deployment. Runtime exceptions abort
    remaining scenarios; assertion failures are collected across scenarios.
    """
    supplied = Path(project).expanduser().absolute()
    root = (supplied.parent if supplied.is_file() else supplied).resolve()
    report = {"schema": REPORT_SCHEMA, "ok": False, "status": "failed", "stage": "check",
              "project": str(root), "created_at": datetime.now(timezone.utc).isoformat(),
              "summary": {"scenarios": 0, "passed": 0, "failed": 0, "skipped": 0,
                          "assertions": 0, "assertions_passed": 0, "assertions_failed": 0},
              "diagnostics": [], "scenarios": [], "input_hashes": {},
              "report_written": False,
              "quality": {"logic_assertions": "unverified", "timing": "not-verified",
                          "physical_io": "not-verified", "hardware": "not-verified",
                          "iec_time": "deterministic-virtual-scan-time"}}
    destination, bindings, evidence_bindings = None, {}, {}
    current_scenario = None
    try:
        _number(timeout_s, "timeout_s")
        if not 0 < timeout_s <= 30:
            raise VerificationError("E_TIMEOUT", "timeout_s must be in (0, 30]")
        # Establish a safe failure-report location before project checking. This
        # replaces an earlier success when the new source fails its contract.
        # If the manifest itself is unreadable/ambiguous, protect unknown source
        # paths by declining the write and reporting that explicitly instead.
        if report_path is not None:
            try:
                protective = _json(_read(root / MANIFEST, MAX_SCENARIO_BYTES).decode("utf-8-sig"))
                if not isinstance(protective, dict) or not isinstance(protective.get("source"), str) or not protective["source"]:
                    raise ValueError("cannot identify the protected source from the project manifest")
                raw_scenario = Path(scenarios_path if scenarios_path is not None else "scenarios.json").expanduser()
                protected_scenario = raw_scenario if raw_scenario.is_absolute() else root / raw_scenario
                destination = _report_destination(root, report_path,
                                                  [root / MANIFEST, root / protective["source"], protected_scenario])
                report["report_path"] = str(destination)
            except Exception as error:
                report["report_error"] = {"code": "E_REPORT_PATH", "message": str(error)}
                raise VerificationError("E_REPORT_PATH", "cannot safely write the requested verification report: " + str(error)) from error
        checked = check_open_project(root)
        if not checked.ok or not isinstance(checked.manifest, dict):
            report["diagnostics"] = checked.as_dict().get("diagnostics", [])
            raise VerificationError("E_PROJECT", "project contract check failed", file=str(root / MANIFEST))
        manifest = checked.manifest
        source = root / manifest["source"]
        if not source.resolve().is_relative_to(root):
            raise VerificationError("E_PATH", "project source escapes its root", file=str(source))
        scenario_file = _within(root, scenarios_path if scenarios_path is not None else "scenarios.json")
        destination = _report_destination(root, report_path, [root / MANIFEST, source, scenario_file])
        if destination is not None:
            report["report_path"] = str(destination)
        for key, path in (("manifest", root / MANIFEST), ("source", source), ("scenarios", scenario_file)):
            data = _read(path, MAX_SCENARIO_BYTES if key == "scenarios" else None)
            bindings[key] = {"path": str(path), "sha256": _sha(data)}
            if key != "scenarios":
                recorded = checked.source_hashes.get(MANIFEST if key == "manifest" else manifest["source"])
                if recorded != bindings[key]["sha256"]:
                    raise VerificationError("E_INPUT_CHANGED", "input changed after project checking", file=str(path))
            if key == "source":
                text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
                bindings[key]["normalized_sha256"] = _sha(text.encode("utf-8"))
            if key == "scenarios":
                scenario_data = data
        report["input_hashes"] = bindings
        from .open_contract import validate_signals, validate_value
        signals = validate_signals(manifest["signals"] if "signals" in manifest else LEGACY_SIGNALS)
        report["signals"] = signals
        report["stage"] = "scenarios"
        try:
            document = _json(scenario_data.decode("utf-8-sig"))
        except (ValueError, UnicodeError) as error:
            raise VerificationError("E_SCENARIOS_JSON", str(error), file=str(scenario_file)) from error
        scenarios = _scenarios(document, signals, validate_value, scenario_file)
        summary = report["summary"]
        summary.update(scenarios=len(scenarios), skipped=len(scenarios),
                       assertions=sum(len(step["expect"]) for item in scenarios for step in item["steps"]))
        _stable(bindings)
        report["stage"] = "build"
        output = Path(build_dir).expanduser() if build_dir is not None else root / ".plcidf" / "build"
        if not output.is_absolute():
            output = root / output
            if not output.resolve().is_relative_to(root):
                raise VerificationError("E_BUILD_PATH", "relative build path must stay inside the project")
        if source.resolve().is_relative_to(output.resolve()):
            raise VerificationError("E_BUILD_PATH", "build directory must not contain the source")
        built = backend.build(source, output, cycle_ms=manifest["task"]["cycle_ms"],
                              signals=manifest.get("signals"))
        report["build"] = built
        if not isinstance(built, dict) or built.get("ok") is not True or built.get("status") != "verified":
            raise VerificationError("E_BUILD", "ST compilation failed; see build diagnostics")
        identities = _hashes(built)
        report.update(identities)
        if identities["source_sha256"] != bindings["source"]["normalized_sha256"]:
            raise VerificationError("E_SOURCE_IDENTITY", "compiled source differs from checked input",
                                    expected=bindings["source"]["normalized_sha256"], actual=identities["source_sha256"])
        _stable(bindings)
        if not isinstance(built.get("manifest_path"), str) or not built["manifest_path"]:
            raise VerificationError("E_BUILD", "build manifest path is missing")
        report["stage"] = "deploy"
        deployed = backend.deploy(built["manifest_path"], execute=True)
        report["deployment"] = deployed
        if not isinstance(deployed, dict) or deployed.get("ok") is not True or deployed.get("status") != "deployed":
            raise VerificationError("E_DEPLOY", "local deployment did not complete")
        if any(deployed.get(key) != identities[key] for key in ("build_hash", "program_hash")):
            raise VerificationError("E_IDENTITY", "deployment identity differs from build")
        release = deployed.get("manifest_path")
        if not isinstance(release, str) or not release:
            raise VerificationError("E_DEPLOY", "deployed release manifest path is missing")
        for item in scenarios:
            current_scenario = {"name": item["name"], "status": "failed", "steps": []}
            report["scenarios"].append(current_scenario)
            summary["skipped"] -= 1
            report["stage"] = "replay"
            _stable(bindings)
            frames = [dict(step["inputs"]) for step in item["steps"] for _ in range(step["scans"])]
            replayed = backend.replay(release, frames, timeout_s=timeout_s)
            if not isinstance(replayed, dict) or replayed.get("ok") is not True or replayed.get("status") != "completed":
                current_scenario["replay"] = replayed
                raise VerificationError("E_REPLAY", "scenario replay failed", scenario=item["name"], file=str(scenario_file))
            samples, evidence = _samples(replayed, len(frames), identities, signals,
                                         manifest["task"]["cycle_ms"], validate_value)
            current_scenario.update(run_id=replayed["run_id"], evidence=evidence)
            evidence_bindings[item["name"]] = {"path": evidence["path"], "sha256": evidence["sha256"]}
            report["stage"] = "assertions"
            failed = False
            for step_index, step in enumerate(item["steps"], 1):
                values = samples[step["last_scan"] - 1]["values"]
                actual = {key: values[key] for key in step["expect"]}
                checks = []
                for name, expected in step["expect"].items():
                    observed = actual[name]
                    passed = (abs(observed - expected["approx"]) <= expected["abs_tol"]
                              if isinstance(expected, dict) else observed == expected)
                    checks.append({"signal": name, "expected": expected, "actual": observed, "passed": passed})
                    summary["assertions_passed" if passed else "assertions_failed"] += 1
                    if not passed:
                        failed = True
                        report["diagnostics"].append({"code": "E_ASSERTION", "message": "output assertion failed",
                            "file": str(scenario_file), "scenario": item["name"], "step": step_index,
                            "scan": step["last_scan"], "signal": name, "expected": expected, "actual": observed})
                current_scenario["steps"].append({"step": step_index, "scan": step["last_scan"],
                    "scans": step["scans"], "inputs": step["inputs"], "expected": step["expect"],
                    "actual": actual, "assertions": checks, "status": "failed" if any(not c["passed"] for c in checks) else "verified"})
            current_scenario["status"] = "failed" if failed else "verified"
            summary["failed" if failed else "passed"] += 1
            current_scenario = None
        report["stage"] = "input_stability"
        _stable(bindings)
        _stable(evidence_bindings)
        report["ok"] = summary["failed"] == 0 and summary["assertions_failed"] == 0
        report["status"] = "verified" if report["ok"] else "failed"
        report["stage"] = "complete" if report["ok"] else "assertions"
        report["quality"]["logic_assertions"] = report["status"]
    except Exception as error:
        if current_scenario is not None:
            report["summary"]["failed"] += 1
        diagnostic = {"code": getattr(error, "code", "E_VERIFICATION"), "message": str(error),
                      "stage": report["stage"]}
        diagnostic.update(getattr(error, "context", {}))
        if current_scenario is not None:
            diagnostic.setdefault("scenario", current_scenario["name"])
        report["diagnostics"].append(diagnostic)
    if destination is not None:
        try:
            latest = _json(_read(root / MANIFEST, MAX_SCENARIO_BYTES).decode("utf-8-sig"))
            if not isinstance(latest, dict) or not isinstance(latest.get("source"), str) or not latest["source"]:
                raise VerificationError("E_REPORT_PATH", "cannot identify the current source for report protection")
            raw_scenario = Path(scenarios_path if scenarios_path is not None else "scenarios.json").expanduser()
            current_scenario_path = raw_scenario if raw_scenario.is_absolute() else root / raw_scenario
            _report_destination(root, destination,
                                [root / MANIFEST, root / latest["source"], current_scenario_path])
            report["report_written"] = True
            _write_report(destination, report)
        except Exception as error:
            report.update(ok=False, status="failed", stage="report", report_written=False)
            report["quality"]["logic_assertions"] = "unverified"
            report["report_error"] = {"code": "E_REPORT", "message": str(error), "file": str(destination)}
            report["diagnostics"].append(report["report_error"])
    return report
