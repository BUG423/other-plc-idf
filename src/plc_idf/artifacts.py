"""Versioned source/build/deployment records with SHA-256 integrity checks.

Hashes bind a plan to the bytes observed when it was created. They are not
signatures: retain the expected plan hash separately when confirming a plan.
Creation records inputs and outputs; it does not certify a compiler result or
prove that a PLC accepted an application.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .safety import SafetyError


class ArtifactError(SafetyError):
    """Invalid or changed source, artifact, build record, or deployment plan."""


def _canonical(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ArtifactError(f"Record must contain JSON values: {exc}") from exc


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _copy_json(value):
    return json.loads(_canonical(value))


def normalize_target(target: Mapping) -> dict:
    if not isinstance(target, Mapping):
        raise ArtifactError("Target must be a mapping with BUSID and VID:PID")
    normalized = _copy_json(dict(target))
    busid, vidpid = normalized.get("busid"), normalized.get("vidpid")
    if not isinstance(busid, str) or not re.fullmatch(r"\d+(?:-\d+)+", busid):
        raise ArtifactError("Target requires an explicit USB BUSID")
    if not isinstance(vidpid, str) or not re.fullmatch(
            r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}", vidpid):
        raise ArtifactError("Target requires an explicit VID:PID")
    normalized["vidpid"] = vidpid.lower()
    return normalized


def _relative(raw: str | Path) -> str:
    path = PurePosixPath(str(raw).replace(os.sep, "/"))
    if path.is_absolute() or ".." in path.parts:
        raise ArtifactError(f"Source selection must stay within its root: {raw}")
    return str(path)


def _file_record(path: Path) -> dict:
    try:
        if path.is_symlink() or not path.is_file():
            raise ArtifactError(f"Expected a regular file, without symlinks: {path}")
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino, after.st_size, after.st_mtime_ns):
            raise ArtifactError(f"File changed while hashing: {path}")
        return {"path": str(path), "size": after.st_size, "sha256": digest.hexdigest()}
    except OSError as exc:
        raise ArtifactError(f"Cannot hash file {path}: {exc}") from exc


def snapshot_source_tree(source_root: str | Path, paths=None, *, exclude_paths=()) -> dict:
    """Hash selected files/directories; omitted paths selects the complete root.

    Selections and exclusions are relative to source_root and are retained so
    verification also detects newly added source files. Symlinks are refused.
    Pass explicit source selections when generated files share the directory.
    """
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise ArtifactError(f"Source root is not a directory: {root}")
    if isinstance(paths, (str, Path)) or isinstance(exclude_paths, (str, Path)):
        raise ArtifactError("Source selections/exclusions must be lists of relative paths")
    selections = sorted(set(_relative(path) for path in (["."] if paths is None else paths)))
    excluded = sorted(set(_relative(path) for path in exclude_paths))
    if not selections:
        raise ArtifactError("At least one source path is required")
    records = {}

    def is_excluded(relative: str) -> bool:
        return any(relative == item or relative.startswith(item + "/") or item == "."
                   for item in excluded)

    def walk(path: Path):
        relative = path.relative_to(root).as_posix()
        if is_excluded(relative):
            return
        # Also catch a symlink in a selected path's parent (e.g. link/main.st).
        cursor = root
        for part in path.relative_to(root).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ArtifactError(f"Source symlinks are not accepted: {cursor}")
        if path.is_dir():
            for child in sorted(path.iterdir()):
                walk(child)
        else:
            record = _file_record(path)
            record["path"] = relative
            records[relative] = record

    try:
        for selection in selections:
            walk(root / selection)
    except OSError as exc:
        raise ArtifactError(f"Cannot snapshot source tree: {exc}") from exc
    files = [records[key] for key in sorted(records)]
    if not files:
        raise ArtifactError("Source selection contains no regular files")
    return {"root": str(root), "paths": selections, "exclude_paths": excluded,
            "files": files, "tree_sha256": _digest(files)}


def create_build_manifest(source_root: str | Path, artifact_paths, *, target: Mapping,
                          tool_versions: Mapping, source_paths=None,
                          exclude_paths=()) -> dict:
    """Record source bytes, artifact bytes, tool versions, and the chosen target.

    Artifacts inside source_root are automatically excluded from the source
    snapshot. The caller must establish compile success separately.
    """
    root = Path(source_root).resolve()
    artifacts = [_file_record(Path(path).absolute()) for path in artifact_paths]
    if not artifacts or len({item["path"] for item in artifacts}) != len(artifacts):
        raise ArtifactError("Build requires at least one distinct artifact")
    if any(item["size"] == 0 for item in artifacts):
        raise ArtifactError("Build artifacts must not be empty")
    if not isinstance(tool_versions, Mapping) or not tool_versions or not all(
            isinstance(k, str) and k and isinstance(v, str) and v.strip()
            for k, v in tool_versions.items()):
        raise ArtifactError("Explicit tool version strings are required")
    excluded = list(exclude_paths)
    for artifact in artifacts:
        path = Path(artifact["path"])
        if path.is_relative_to(root):
            excluded.append(path.relative_to(root).as_posix())
    record = {"schema": "plcidf.build.v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "source": snapshot_source_tree(root, source_paths, exclude_paths=excluded),
              "artifacts": sorted(artifacts, key=lambda item: item["path"]),
              "target": normalize_target(target), "tool_versions": dict(tool_versions)}
    record["build_hash"] = _digest(record)
    return record


def _verify_digest(record: dict, schema: str, hash_key: str, expected_hash=None) -> None:
    if not isinstance(record, dict) or record.get("schema") != schema:
        raise ArtifactError(f"Unsupported record schema; expected {schema}")
    content = {key: value for key, value in record.items() if key != hash_key}
    actual = _digest(content)
    if record.get(hash_key) != actual:
        raise ArtifactError(f"Record integrity check failed: {hash_key}")
    if expected_hash is not None and actual != expected_hash:
        raise ArtifactError(f"Previously confirmed {hash_key} no longer matches")


def verify_build_manifest(manifest: dict, *, source_root: str | Path | None = None,
                          expected_build_hash: str | None = None) -> dict:
    """Rehash both source selections and every artifact, refusing any change."""
    _verify_digest(manifest, "plcidf.build.v1", "build_hash", expected_build_hash)
    try:
        normalize_target(manifest["target"])
        source = manifest["source"]
        current = snapshot_source_tree(source_root or source["root"], source["paths"],
                                       exclude_paths=source["exclude_paths"])
        if source["tree_sha256"] != current["tree_sha256"] or source["files"] != current["files"]:
            raise ArtifactError("Source tree has changed since the build was recorded")
        artifacts = manifest["artifacts"]
        if not artifacts:
            raise ArtifactError("Build manifest contains no artifacts")
        for artifact in artifacts:
            if _file_record(Path(artifact["path"])) != artifact:
                raise ArtifactError(f"Build artifact has changed: {artifact['path']}")
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactError(f"Malformed build manifest: {exc}") from exc
    return manifest


def create_deployment_plan(build_manifest: dict, *, target: Mapping,
                           backup: Mapping, verification: Mapping) -> dict:
    """Bind a build to a device and explicit backup/verification procedures."""
    verify_build_manifest(build_manifest)
    target = normalize_target(target)
    if target != build_manifest["target"]:
        raise ArtifactError("Deployment target differs from the build target")
    if not isinstance(backup, Mapping) or not backup or not isinstance(verification, Mapping) or not verification:
        raise ArtifactError("Explicit backup and verification procedures are required")
    record = {"schema": "plcidf.deploy.v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "build": _copy_json(build_manifest), "target": target,
              "backup": _copy_json(dict(backup)), "verification": _copy_json(dict(verification))}
    record["plan_hash"] = _digest(record)
    return record


def verify_deployment_plan(plan: dict, *, target: Mapping | None = None,
                           source_root: str | Path | None = None,
                           expected_plan_hash: str | None = None) -> dict:
    """Validate the confirmed plan, current source/artifacts, and observed target.

    Pass the current observed identity as target immediately before download;
    without it this checks the record and bytes only, not a connected device.
    """
    _verify_digest(plan, "plcidf.deploy.v1", "plan_hash", expected_plan_hash)
    try:
        verify_build_manifest(plan["build"], source_root=source_root)
        if normalize_target(plan["target"]) != plan["build"]["target"]:
            raise ArtifactError("Plan target does not match its build")
        if target is not None and normalize_target(target) != plan["target"]:
            raise ArtifactError("Observed device differs from the planned target")
        if not plan["backup"] or not plan["verification"]:
            raise ArtifactError("Plan backup and verification procedures are missing")
    except (KeyError, TypeError) as exc:
        raise ArtifactError(f"Malformed deployment plan: {exc}") from exc
    return plan


def write_record(path: str | Path, record: dict) -> Path:
    """Atomically persist a manifest/plan; errors propagate before side effects."""
    path = Path(path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as fh:
            temporary = Path(fh.name)
            fh.write(_canonical(record) + b"\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return path
    except OSError as exc:
        raise ArtifactError(f"Cannot persist record {path}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_record(path: str | Path) -> dict:
    try:
        with Path(path).open(encoding="utf-8") as fh:
            record = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ArtifactError(f"Cannot read record {path}: {exc}") from exc
    if not isinstance(record, dict):
        raise ArtifactError("Expected a JSON object record")
    return record
