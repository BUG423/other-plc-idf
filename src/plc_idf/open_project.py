"""A versioned, memory-only ST project for the local Linux runtime.

Checking validates the project contract and required structure. MatIEC remains
the actual ST compiler; this is deliberately not another language parser.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from .config import runtime_asset
from .open_contract import validate_signals
from .project import Diagnostic, MANIFEST, NAME, ProjectCheck, check_project
from .targets import get_profile

MATIEC_COMMIT = "3a41303bcb4e50403417bc39c63f88981d7d961e"
MAX_SOURCE_BYTES = 1024 * 1024
MOTOR_SIGNALS = {
    "inputs": {"START": "BOOL", "STOP": "BOOL", "PERMISSIVE": "BOOL", "FAULT": "BOOL"},
    "outputs": {"MOTOR": "BOOL", "STARTING": "BOOL", "FAULTED": "BOOL"},
}
PROJECT_AGENT_INSTRUCTIONS = """# Working on this local PLC project

Read README.md, plcidf.json, main.st and scenarios.json when present before editing.
Edit the ST implementation and its corresponding requirement-based scenarios.
The default counter project has no scenario file; add explicit acceptance
scenarios before claiming scenario verification.

Run `plcidf check . --json` first, then `plcidf verify . --json`.
The verification report is written to `.plcidf/verification.json` by default.
An expectation is an acceptance requirement: change it only when the user's
requirement changes and explain that change. Never weaken or rewrite expectations
merely to make a failed implementation pass.

This target is a local Linux memory-only runtime. Report results as local-only;
do not switch to Easy521 or operate hardware without an explicitly selected
target and task. No independent chat UI or AI-provider API is required.

In the final report include program/source hashes, each scenario's pass/fail
result, and any failed command or unverified behavior. A project check alone
does not prove compilation, execution, physical I/O, or real-time performance.
"""


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
                        cycle_ms: int = 20, template: str = "counter") -> OpenProjectCheck:
    root = Path(path).expanduser().resolve()
    project_name = name if name is not None else root.name
    if not isinstance(project_name, str) or not NAME.fullmatch(project_name):
        raise ValueError("project name must match [A-Za-z_][A-Za-z_0-9]*")
    if type(cycle_ms) is not int or not 1 <= cycle_ms <= 1000:
        raise ValueError("open-linux cycle_ms must be an integer in 1..1000")
    if not isinstance(template, str) or template not in {"counter", "motor"}:
        raise ValueError("open-linux template must be counter or motor")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise FileExistsError(f"refusing to overwrite nonempty project directory: {root}")
    assets = Path(__file__).resolve().parents[2]
    source_asset = runtime_asset(assets, f"examples/open_linux/{template}/main.st")
    source = source_asset.read_text(encoding="utf-8").replace("T#20ms", f"T#{cycle_ms}ms")
    manifest = {
        "schema_version": 2, "name": project_name, "profile_id": "open-linux",
        "compile_target": get_profile("open-linux").as_dict()["compile_target"],
        "source": "main.st", "toolchain": {"matiec_commit": MATIEC_COMMIT},
        "task": {"program": "MAIN", "configuration": "Config0", "resource": "Res0",
                 "name": "scan", "instance": "instance0", "cycle_ms": cycle_ms},
        "io": {"provider": "memory-only", "bindings": {}},
        "observables": {"COUNTER": "DINT", "HEARTBEAT": "BOOL"},
    }
    scenarios = None
    if template == "motor":
        manifest["signals"] = validate_signals(MOTOR_SIGNALS)
        manifest["observables"] = dict(manifest["signals"]["outputs"])
        scenarios = json.loads(runtime_asset(assets,
            "examples/open_linux/motor/scenarios.json").read_text(encoding="utf-8"))
        # The first scan is time zero. Keep the last pre-PT scan false and
        # the first scan at/after PT true, including non-default task periods.
        boundary = scenarios["scenarios"][0]["steps"]
        before_threshold = math.ceil(60 / cycle_ms) - 1
        if before_threshold:
            boundary[1]["scans"] = before_threshold
        else:
            del boundary[1]
        wait_steps = {"stop_priority_requires_new_edge": [4],
                      "permissive_loss_clears_latch": [0, 4],
                      "fault_clears_latch": [0, 4],
                      "released_start_keeps_request_until_stop": [1],
                      "start_while_inhibited_is_consumed": [3]}
        for scenario in scenarios["scenarios"]:
            for index in wait_steps.get(scenario["name"], []):
                scenario["steps"][index]["scans"] = math.ceil(60 / cycle_ms) + 1
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (root / "main.st").write_text(source, encoding="utf-8")
    (root / "AGENTS.md").write_text(PROJECT_AGENT_INSTRUCTIONS, encoding="utf-8")
    if scenarios is not None:
        (root / "scenarios.json").write_text(json.dumps(scenarios, indent=2) + "\n", encoding="utf-8")
        readme = runtime_asset(assets, "examples/open_linux/motor/README.md").read_text(encoding="utf-8")
        readme = readme.replace("20 ms", f"{cycle_ms} ms")
        if cycle_ms != 20:
            threshold_scan = math.ceil(60 / cycle_ms) + 1
            readme = readme.replace(
                "For the boundary scenario scan 1\nis time 0: MOTOR is false through scan 3 (40 ms), then true on scan 4 (60 ms).",
                f"For the boundary scenario scan 1 is time 0: MOTOR remains false through scan "
                f"{threshold_scan - 1} ({(threshold_scan - 2) * cycle_ms} ms), then becomes true "
                f"on scan {threshold_scan} ({(threshold_scan - 1) * cycle_ms} ms).")
        (root / "README.md").write_text(readme, encoding="utf-8")
    else:
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
    signals = None
    if "signals" in data:
        try:
            signals = validate_signals(data["signals"])
        except ValueError as exc:
            error("E_SIGNALS", str(exc))
        if signals is not None and data.get("observables") != signals["outputs"]:
            error("E_OBSERVABLES", "observables must exactly match signals.outputs")
    elif data.get("observables") != {"COUNTER": "DINT", "HEARTBEAT": "BOOL"}:
        error("E_OBSERVABLES", "legacy projects observe COUNTER:DINT and HEARTBEAT:BOOL")
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
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError(f"ST source exceeds the {MAX_SOURCE_BYTES}-byte check limit")
        with path.open("rb") as fh:
            raw = fh.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES:
            raise ValueError(f"ST source exceeds the {MAX_SOURCE_BYTES}-byte check limit")
        text = raw.decode("utf-8-sig")
        result.source_hashes[source] = hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, ValueError) as exc:
        error("E_SOURCE", str(exc), source)
        return result
    # Ignore comments/string literals; their text cannot satisfy declarations.
    # This bounded structural check still delegates language semantics to MatIEC.
    visible = re.sub(r"\(\*.*?\*\)|//[^\n]*|'(?:\$[\s\S]|''|[^'])*'|"
                     r'"(?:\$[\s\S]|""|[^"])*"', " ", text, flags=re.S)
    required = (r"\bPROGRAM\s+MAIN\b", r"\bCONFIGURATION\s+Config0\b",
                r"\bRESOURCE\s+Res0\s+ON\s+PLC\b")
    if any(not re.search(pattern, visible, re.I) for pattern in required):
        error("E_STRUCTURE", "missing required program or resource declaration", source)
    expected_signals = ({**signals["inputs"], **signals["outputs"]} if signals is not None
                        else {"COUNTER": "DINT", "HEARTBEAT": "BOOL"} if "signals" not in data else {})
    try:
        declarations = _direct_declarations(visible)
        for name, type_name in expected_signals.items():
            if declarations.get(name.casefold()) != [type_name]:
                error("E_SIGNAL_DECLARATION", f"{name} must have one direct MAIN declaration of type {type_name}", source)
    except ValueError as exc:
        error("E_STRUCTURE", str(exc), source)
    intervals = re.findall(r"\bTASK\s+scan\s*\(\s*INTERVAL\s*:=\s*T#(\d+)ms\b",
                           visible, re.I)
    if len(intervals) != 1 or type(cycle) is not int or int(intervals[0]) != cycle:
        error("E_TASK_INTERVAL", "source TASK interval must match manifest cycle_ms", source)
    return result


def _direct_declarations(visible: str) -> dict[str, list[str]]:
    """Bounded direct scalar declarations, not a replacement for MatIEC syntax."""
    programs = re.findall(r"\bPROGRAM\s+MAIN\b(.*?)\bEND_PROGRAM\b", visible, re.I | re.S)
    if len(programs) != 1:
        raise ValueError("exactly one PROGRAM MAIN body is required")
    declarations = {}
    blocks = re.findall(r"\bVAR(?:_INPUT|_OUTPUT)?\b(?:\s+(?:CONSTANT|RETAIN|NON_RETAIN))*\s*"
                        r"(.*?)\bEND_VAR\b", programs[0], re.I | re.S)
    if len(blocks) > 128:
        raise ValueError("too many declaration blocks for the bounded project check")
    count = 0
    for block in blocks:
        block = re.sub(r"\bSTRUCT\b.*?\bEND_STRUCT\b", " <compound> ", block, flags=re.I | re.S)
        for statement in block.split(";"):
            match = re.fullmatch(r"\s*([A-Za-z_][A-Za-z_0-9]*(?:\s*,\s*[A-Za-z_][A-Za-z_0-9]*)*)"
                                 r"\s*:\s*([A-Za-z_][A-Za-z_0-9]*)\s*(?::=.*)?", statement, re.S)
            if match:
                for name in match[1].split(","):
                    count += 1
                    if count > 4096:
                        raise ValueError("too many declarations for the bounded project check")
                    declarations.setdefault(name.strip().casefold(), []).append(match[2].upper())
    return declarations


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
