"""Immutable local release provenance across vendor-triggered recompilation.

An intent describes the user's chosen build. A release preserves the artifacts
observed after F8 compilation and before download confirmation. Neither local
hashes nor vendor project comparison prove which bytes the PLC received.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .artifacts import snapshot_source_tree, verify_build_manifest


REQUIRED = ("STTemp/MAIN.rst", "STTemp/MAIN.o", "Temp/stprog.o", "Output.prg", "Output.sdt")


class NativeReleaseError(ValueError):
    """The original intent or a fresh, complete release could not be established."""


class CompilationProofRequired(NativeReleaseError):
    """Pre-confirm outputs changed; an unchanged-release claim is impossible."""


def _data(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _hash(value):
    return hashlib.sha256(_data(value)).hexdigest()


def _sealed(record, key):
    record[key] = _hash(record)
    return record


def _check(record, schema, key, expected=None):
    if (not isinstance(record, dict) or record.get("schema") != schema
            or record.get(key) != _hash({k: v for k, v in record.items() if k != key})
            or (expected is not None and record[key] != expected)):
        raise NativeReleaseError(f"{schema} record integrity failed")


def _file(path, *, check_time=lambda: None):
    check_time()
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise NativeReleaseError(f"Required regular file is missing: {path}")
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            check_time()
            digest.update(block)
    after = path.stat()
    stamp = lambda value: (value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if stamp(before) != stamp(after):
        raise NativeReleaseError(f"File changed while reading: {path}")
    return {"path": str(path.resolve()), "size": after.st_size, "sha256": digest.hexdigest(),
            "stamp": list(stamp(after))}


def _references(refs):
    if not isinstance(refs, list) or not refs:
        raise NativeReleaseError("Fresh compilation requires retained evidence")
    for ref in refs:
        if not isinstance(ref.get("source"), str) or not ref["source"]:
            raise NativeReleaseError("Evidence source is missing")
        if _file(ref["path"])["sha256"] != ref.get("sha256"):
            raise NativeReleaseError("Compilation evidence hash changed")


def freeze_intent(manifest, *, operation_id, working_project, build_record_hash=None):
    """Freeze a currently verified build and its complete native input selection.

    Packed project archives and log files are not compiler input files in this
    profile. All other files must already be covered by the original manifest
    or its explicit generated-directory exclusions; unknown inputs fail closed.
    """
    verify_build_manifest(manifest)
    source = manifest["source"]
    root = Path(source["root"])
    excluded = list(source["exclude_paths"])
    excluded += [p.name for p in root.iterdir() if p.suffix.lower() in {".hcp", ".hcpp", ".log"}]
    complete = snapshot_source_tree(root, ["."], exclude_paths=excluded)
    if complete["files"] != source["files"]:
        raise NativeReleaseError("Intent manifest does not cover all native source/variable/configuration inputs")
    record = {"schema": "plcidf.native-intent.v1", "operation_id": operation_id,
              "working_project": working_project, "manifest": json.loads(_data(manifest)), "inputs": complete}
    if build_record_hash is not None:
        if not isinstance(build_record_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", build_record_hash):
            raise NativeReleaseError("Verified prior build record requires its SHA-256")
        record["build_record_hash"] = build_record_hash
    return _sealed(record, "intent_hash")


def verify_intent(intent, *, current_inputs=True):
    _check(intent, "plcidf.native-intent.v1", "intent_hash")
    manifest = intent["manifest"]
    _check(manifest, "plcidf.build.v1", "build_hash")
    inputs = intent["inputs"]
    if (inputs["files"] != manifest["source"]["files"]
            or inputs["tree_sha256"] != manifest["source"]["tree_sha256"]):
        raise NativeReleaseError("Intent inputs no longer bind the selected build")
    if current_inputs:
        actual = snapshot_source_tree(inputs["root"], inputs["paths"], exclude_paths=inputs["exclude_paths"])
        if actual != inputs:
            raise NativeReleaseError("Native source/variable/configuration input drift")
    return intent


def compile_baseline(intent, *, check_time=lambda: None):
    verify_intent(intent)
    root = Path(intent["inputs"]["root"])
    files = {}
    for relative in REQUIRED:
        files[relative] = _file(root / relative, check_time=check_time)
    baseline = _sealed({"schema": "plcidf.native-compile-baseline.v1",
                        "intent_hash": intent["intent_hash"], "files": files}, "baseline_hash")
    _verify_baseline(intent, baseline)
    return baseline


def _verify_baseline(intent, baseline):
    _check(baseline, "plcidf.native-compile-baseline.v1", "baseline_hash")
    if baseline.get("intent_hash") != intent["intent_hash"] or set(baseline["files"]) != set(REQUIRED):
        raise NativeReleaseError("Compile baseline belongs to another or incomplete intent")
    expected = {item["path"]: item for item in intent["manifest"]["artifacts"]}
    root = Path(intent["inputs"]["root"])
    for relative, file in baseline["files"].items():
        path = str((root / relative).resolve())
        original = expected.get(path)
        if (file["path"] != path or not original or file["sha256"] != original["sha256"]
                or file["size"] != original["size"]):
            raise NativeReleaseError("Compile baseline no longer matches selected build artifacts")


def fresh_compile_proof(before, after, *, started_at, observed_at, evidence):
    """Require a fresh timestamped compile start, explicit zero errors and finish.

    Same-second timestamps preceding the pre-F8 clock read are ambiguous. The
    conservative next-second lower bound matches the download log policy.
    """
    _references(evidence)
    start, end = datetime.fromisoformat(started_at), datetime.fromisoformat(observed_at)
    if (start.tzinfo is None or end.tzinfo is None or start.utcoffset() != end.utcoffset()
            or not 0 <= (end - start).total_seconds() <= 600):
        raise NativeReleaseError("Invalid compilation Windows clock window")
    old, new = before.splitlines(), after.splitlines()
    lines = new[len(old):] if new[:len(old)] == old else new
    lower = start.replace(microsecond=0) + timedelta(seconds=1)
    starts = []
    for index, line in enumerate(lines):
        match = re.match(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+开始(?:程序)?编译\s*$", line)
        if match:
            stamp = datetime.strptime(match[1], "%Y/%m/%d %H:%M:%S").replace(tzinfo=start.tzinfo)
            if lower <= stamp <= end:
                starts.append((index, stamp))
    if len(starts) != 1:
        raise NativeReleaseError("No unique fresh vendor compilation start in this F8 window")
    start_index, compile_time = starts[0]
    trace = "\n".join(lines[start_index:])
    # Horizontal whitespace only: a following timestamp must never become an
    # error count by matching across a newline after the Chinese word 错误.
    counts = [int(match[1]) for match in re.finditer(r"统计[ \t]*--[ \t]*\([ \t]*([0-9]+)[ \t]*\)[ \t]*错误", trace)]
    if not counts or any(count != 0 for count in counts):
        raise NativeReleaseError("F8 compilation is incomplete or not explicitly zero-error")
    finishes = re.findall(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+完成(?:程序)?编译", trace)
    if not finishes or any(not compile_time <= datetime.strptime(value, "%Y/%m/%d %H:%M:%S").replace(
            tzinfo=start.tzinfo) <= end for value in finishes):
        raise NativeReleaseError("Compilation completion does not belong to this F8 window")
    return {"status": "verified", "error_count": 0, "source": "fresh-vendor-f8-compile",
            "trace": trace, "started_at": started_at, "observed_at": observed_at,
            "evidence": json.loads(_data(evidence))}


def _exclusive(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o444)


def capture_release(intent, baseline, proof=None, *, directory, check_time=lambda: None):
    """Persist a new release without replacing intent or adopting later rebuilds."""
    verify_intent(intent)
    _verify_baseline(intent, baseline)
    root = Path(intent["inputs"]["root"])
    originals = []
    for relative in REQUIRED:
        current = _file(root / relative, check_time=check_time)
        if relative.endswith(".o"):
            header = (root / relative).read_bytes()[:52]
            if len(header) != 52 or header[:6] != b"\x7fELF\x01\x01" or header[18:20] != b"\x28\x00":
                raise NativeReleaseError(f"Required output is not ELF32 little-endian ARM: {relative}")
        if relative.endswith(".rst") and (root / relative).read_bytes() not in {b"Success!\r\n", b"Success!\n"}:
            raise NativeReleaseError("Fresh vendor compiler marker is not Success!")
        if not current["size"]:
            raise NativeReleaseError(f"Required output is empty: {relative}")
        originals.append({"relative": relative, **current})
    unchanged = all({k: v for k, v in item.items() if k != "relative"} == baseline["files"][item["relative"]]
                    for item in originals)
    kind = "unchanged-pre-confirm" if unchanged else "vendor-f8-recompile"
    if not unchanged:
        if proof is None:
            raise CompilationProofRequired("Pre-confirm outputs changed; fresh compilation proof is required")
        if proof.get("status") != "verified" or proof.get("error_count") != 0:
            raise NativeReleaseError("A fresh verified compilation proof is required")
        # Parse retained proof again rather than trusting status/error booleans.
        fresh_compile_proof("", proof["trace"], started_at=proof["started_at"],
                            observed_at=proof["observed_at"], evidence=proof["evidence"])
        for original in originals:
            if original["stamp"] == baseline["files"][original["relative"]]["stamp"]:
                raise NativeReleaseError(f"Required compile output is not fresh: {original['relative']}")
    else:
        # Explicitly distinguish original verified F7 provenance from fresh F8.
        proof = None
    store = Path(directory).resolve() / uuid.uuid4().hex
    store.mkdir(parents=True, exist_ok=False)
    artifacts = []
    for index, original in enumerate(originals):
        check_time()
        original_path = Path(original["path"])
        content = original_path.read_bytes()
        if hashlib.sha256(content).hexdigest() != original["sha256"]:
            raise NativeReleaseError("Compile output changed while freezing release")
        destination = store / f"{index}-{original_path.name}"
        _exclusive(destination, content)
        artifacts.append({"path": str(destination), "original_path": str(original_path),
                          "relative": original["relative"], "sha256": original["sha256"], "size": original["size"]})
    verify_intent(intent)
    for original in originals:
        if _file(original["path"], check_time=check_time) != {k: v for k, v in original.items() if k != "relative"}:
            raise NativeReleaseError("Compile output changed before release record persistence")
    release = _sealed({"schema": "plcidf.native-release.v1", "created_at": datetime.now(timezone.utc).isoformat(),
                       "record_path": str(store / "release.json"),
                       "operation_id": intent["operation_id"], "working_project": intent["working_project"],
                       "intent": intent, "intent_build_hash": intent["manifest"]["build_hash"],
                       "target": intent["manifest"]["target"], "transition": {"kind": kind,
                       "baseline": baseline, "compile": proof, "outputs": originals}, "artifacts": artifacts,
                       "download_binding": {"status": "unverified", "method": "local-pre-confirm-snapshot",
                                            "reason": "No calibrated device byte binding/readback"}}, "release_hash")
    _exclusive(store / "release.json", _data(release) + b"\n")
    for directory_path in (store, store.parent):
        descriptor = os.open(directory_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return release


def verify_release(release, *, expected_release_hash=None, current_inputs=True, live_outputs=False):
    _check(release, "plcidf.native-release.v1", "release_hash", expected_release_hash)
    if Path(release["record_path"]).read_bytes() != _data(release) + b"\n":
        raise NativeReleaseError("Persisted immutable release record changed")
    intent = verify_intent(release["intent"], current_inputs=current_inputs)
    if (release["intent_build_hash"] != intent["manifest"]["build_hash"]
            or release["operation_id"] != intent["operation_id"] or release["target"] != intent["manifest"]["target"]
            or release["working_project"] != intent["working_project"]):
        raise NativeReleaseError("Release no longer binds the original intent")
    paths = set()
    for artifact in release["artifacts"]:
        observed = _file(artifact["path"])
        if observed["sha256"] != artifact["sha256"] or observed["size"] != artifact["size"]:
            raise NativeReleaseError("Immutable release artifact changed")
        paths.add(artifact["relative"])
        if live_outputs and _file(artifact["original_path"])["sha256"] != artifact["sha256"]:
            raise NativeReleaseError("Live download candidate changed after release capture")
    if paths != set(REQUIRED) or len(release["artifacts"]) != len(REQUIRED):
        raise NativeReleaseError("Release requires each native artifact exactly once")
    transition = release["transition"]
    _verify_baseline(intent, transition["baseline"])
    if transition["kind"] == "vendor-f8-recompile":
        proof = transition["compile"]
        fresh_compile_proof("", proof["trace"], started_at=proof["started_at"],
                            observed_at=proof["observed_at"], evidence=proof["evidence"])
    elif transition["kind"] == "unchanged-pre-confirm" and transition["compile"] is None:
        for item in release["artifacts"]:
            if item["sha256"] != transition["baseline"]["files"][item["relative"]]["sha256"]:
                raise NativeReleaseError("Unchanged release artifact differs from the original build")
    else:
        raise NativeReleaseError("Unknown or ambiguous compile transition")
    return release
