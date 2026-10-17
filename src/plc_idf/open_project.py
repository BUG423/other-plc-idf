"""A versioned, memory-only ST project for the local Linux runtime.

Checking validates the project contract and required structure. MatIEC remains
the actual ST compiler; this is deliberately not another language parser.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .config import runtime_asset
from .project import Diagnostic, MANIFEST, NAME, ProjectCheck, check_project
from .targets import get_profile

MATIEC_COMMIT = "3a41303bcb4e50403417bc39c63f88981d7d961e"


class OpenProjectCheck(ProjectCheck):
    def as_dict(self) -> dict:
        result = super().as_dict()
        result.update(schema_version=2, profile="open-linux",
                      validation="offline-project-contract-check",
                      limitations=["ST syntax and semantics require the MatIEC compiler",
                                   "No execution, physical I/O or real-time validation"])
        return result


def _json_object(pairs):
    obj = {}
    for name, value in pairs:
        if name in obj:
            raise ValueError(f"duplicate manifest key: {name}")
        obj[name] = value
    return obj


def create_open_project(path: str | Path, *, name: str | None = None,
                        cycle_ms: int = 20) -> OpenProjectCheck:
    root = Path(path).expanduser().resolve()
    project_name = name if name is not None else root.name
    if not isinstance(project_name, str) or not NAME.fullmatch(project_name):
        raise ValueError("project name must match [A-Za-z_][A-Za-z_0-9]*")
    if type(cycle_ms) is not int or not 1 <= cycle_ms <= 1000:
        raise ValueError("open-linux cycle_ms must be an integer in 1..1000")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError(f"refusing to overwrite nonempty project directory: {root}")
    template = runtime_asset(Path(__file__).resolve().parents[2],
                             "examples/open_linux/counter/main.st")
    source = template.read_text(encoding="utf-8").replace("T#20ms", f"T#{cycle_ms}ms")
    manifest = {
        "schema_version": 2, "name": project_name, "profile_id": "open-linux",
        "compile_target": get_profile("open-linux").as_dict()["compile_target"],
        "source": "main.st", "toolchain": {"matiec_commit": MATIEC_COMMIT},
        "task": {"program": "MAIN", "configuration": "Config0", "resource": "Res0",
                 "name": "scan", "instance": "instance0", "cycle_ms": cycle_ms},
        "io": {"provider": "memory-only", "bindings": {}},
        "observables": {"COUNTER": "DINT", "HEARTBEAT": "BOOL"},
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (root / "main.st").write_text(source, encoding="utf-8")
    (root / "README.md").write_text(
        "# Local Linux ST project\n\n"
        "This complete IEC ST source is compiled by MatIEC and executed by the local scan runtime.\n"
        "Edit main.st, then use plcidf build --target open-linux --project . --execute.\n"
        "Task configuration and plcidf.json must agree. Physical I/O and hard real-time timing\n"
        "are not provided by this profile. It is not an Easy521 application package.\n",
        encoding="utf-8")
    return check_open_project(root)


def check_open_project(path: str | Path) -> OpenProjectCheck:
    given = Path(path).expanduser().resolve()
    root = given.parent if given.is_file() else given
    result = OpenProjectCheck(str(root))

    def error(code: str, message: str, source: str = MANIFEST, line: int = 1):
        result.diagnostics.append(Diagnostic(code, message, source, line))

    try:
        raw = (root / MANIFEST).read_bytes()
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_json_object)
        result.source_hashes[MANIFEST] = hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, ValueError) as exc:
        error("E_MANIFEST", str(exc))
        return result
    if not isinstance(data, dict):
        error("E_MANIFEST", "manifest must be an object")
        return result
    result.manifest = data
    if type(data.get("schema_version")) is not int or data["schema_version"] != 2:
        error("E_MANIFEST", "open-linux requires schema_version 2")
    if data.get("profile_id") != "open-linux":
        error("E_TARGET", "supported v2 profile is open-linux")
    if not isinstance(data.get("name"), str) or not NAME.fullmatch(data["name"]):
        error("E_NAME", "invalid project name")
    expected = get_profile("open-linux").as_dict()["compile_target"]
    if data.get("compile_target") != expected:
        error("E_TARGET", "compile_target must match the open-linux profile")
    if data.get("toolchain") != {"matiec_commit": MATIEC_COMMIT}:
        error("E_TOOLCHAIN", "pin the supported MatIEC commit")
    if data.get("io") != {"provider": "memory-only", "bindings": {}}:
        error("E_IO", "this runtime supports memory-only I/O with no physical bindings")
    if data.get("observables") != {"COUNTER": "DINT", "HEARTBEAT": "BOOL"}:
        error("E_OBSERVABLES", "this profile observes COUNTER:DINT and HEARTBEAT:BOOL")
    task = data.get("task")
    if not isinstance(task, dict):
        error("E_TASK", "one periodic MAIN task is required")
        return result
    names = {"program": "MAIN", "configuration": "Config0", "resource": "Res0",
             "name": "scan", "instance": "instance0"}
    if any(task.get(key) != value for key, value in names.items()):
        error("E_TASK", "unsupported program/configuration/resource/task names")
    cycle = task.get("cycle_ms")
    if type(cycle) is not int or not 1 <= cycle <= 1000:
        error("E_TASK", "cycle_ms must be an integer in 1..1000")
    source = data.get("source")
    if not isinstance(source, str) or not source:
        error("E_SOURCE", "a relative ST source path is required")
        return result
    path = root / source
    if Path(source).is_absolute() or not path.resolve().is_relative_to(root):
        error("E_PATH", "source must remain inside the project")
        return result
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        result.source_hashes[source] = hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError) as exc:
        error("E_SOURCE", str(exc), source)
        return result
    # Ignore ST comments when checking the declaration contract, not full syntax.
    visible = re.sub(r"\(\*.*?\*\)|//[^\n]*", " ", text, flags=re.S)
    required = (r"\bPROGRAM\s+MAIN\b", r"\bCONFIGURATION\s+Config0\b",
                r"\bRESOURCE\s+Res0\s+ON\s+PLC\b", r"\bCOUNTER\s*:\s*DINT\b",
                r"\bHEARTBEAT\s*:\s*BOOL\b")
    if any(not re.search(pattern, visible, re.I) for pattern in required):
        error("E_STRUCTURE", "missing required program, resource or observable declaration", source)
    intervals = re.findall(r"\bTASK\s+scan\s*\(\s*INTERVAL\s*:=\s*T#(\d+)ms\b",
                           visible, re.I)
    if len(intervals) != 1 or type(cycle) is not int or int(intervals[0]) != cycle:
        error("E_TASK_INTERVAL", "source TASK interval must match manifest cycle_ms", source)
    return result


def check_any_project(path: str | Path) -> ProjectCheck:
    given = Path(path).expanduser().resolve()
    root = given.parent if given.is_file() else given
    try:
        data = json.loads((root / MANIFEST).read_text(encoding="utf-8-sig"))
        if isinstance(data, dict) and data.get("schema_version") == 2:
            return check_open_project(root)
    except (OSError, UnicodeError, ValueError):
        pass
    return check_project(path)
