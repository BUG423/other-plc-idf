#!/usr/bin/env python3
"""Exercise the real AI-facing ST workflow, including deliberate regressions.

Requires an installed PLC-IDF and the pinned Linux toolchain. Every control
result comes from MatIEC-generated code; no PLC or Windows process is used.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain-dir", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    toolchain = args.toolchain_dir.expanduser().resolve()
    calls, checks, traces = [], [], []
    active = None
    with tempfile.TemporaryDirectory(prefix="plcidf-ai-workflow-") as directory:
        root = Path(directory)
        config = root / "tool.toml"
        config.write_text('[paths]\nproject_root = ' + json.dumps(str(root)) + '\n')
        state = root / "runtime"
        project = root / "motor"

        def capture_samples(value):
            if isinstance(value, list):
                for item in value:
                    capture_samples(item)
            elif isinstance(value, dict):
                if isinstance(value.get("path"), str) and "sha256" in value:
                    path = Path(value["path"])
                    if path.suffix == ".jsonl" and path.is_file():
                        raw = path.read_bytes()
                        traces.append({"path_at_execution": str(path),
                                       "sha256": hashlib.sha256(raw).hexdigest(),
                                       "samples": [json.loads(line) for line in raw.splitlines()]})
                for key, item in value.items():
                    if key == "samples_path" and isinstance(item, str):
                        path = Path(item)
                        if path.is_file():
                            raw = path.read_bytes()
                            traces.append({"path_at_execution": item,
                                           "sha256": hashlib.sha256(raw).hexdigest(),
                                           "samples": [json.loads(line) for line in raw.splitlines()]})
                    elif key != "samples":
                        capture_samples(item)

        def call(command, options=(), expected=0):
            argv = [sys.executable, "-m", "plc_idf.cli", command, *map(str, options),
                    "--config", str(config), "--json"]
            if command not in ("new", "check"):
                argv += ["--toolchain-dir", str(toolchain), "--state-dir", str(state)]
            completed = subprocess.run(argv, capture_output=True, text=True, cwd=root, timeout=180)
            try:
                value = json.loads(completed.stdout)
            except ValueError as exc:
                raise AssertionError(f"not one JSON document: {completed.stdout} {completed.stderr}") from exc
            calls.append({"argv": argv, "returncode": completed.returncode,
                          "report": value, "stderr": completed.stderr})
            if completed.returncode != expected:
                raise AssertionError(f"{command} returned {completed.returncode}, expected {expected}: {value}")
            capture_samples(value)
            return value

        try:
            call("new", [project, "--target", "open-linux", "--template", "motor"])
            assert (project / "AGENTS.md").is_file()
            assert call("check", [project])["ok"]
            baseline = call("verify", [project])
            assert baseline["ok"] and baseline["status"] == "verified"
            assert baseline["summary"]["scenarios"] >= 6
            assert json.loads((project / ".plcidf/verification.json").read_text())["ok"]
            checks.append("motor start/hold/stop/interlock/rising edge/TON scenarios execute actual ST")
            source = project / "main.st"
            original = source.read_text()
            scenario_file = project / "scenarios.json"
            original_scenarios = scenario_file.read_text()

            assert "T#60ms" in original
            source.write_text(original.replace("T#60ms", "T#100ms"))
            changed = call("verify", [project], expected=1)
            assert not changed["ok"] and changed["diagnostics"]
            assert changed.get("program_hash") != baseline.get("program_hash")
            checks.append("changing ST delay from 60ms to 100ms fails the original boundary requirements")

            # Model an explicit requirement change in this isolated fixture.
            # Check both sides of the new 100ms boundary, preserving interlocks.
            revised_scenarios = json.loads(original_scenarios)
            for scenario in revised_scenarios["scenarios"]:
                if scenario["name"] == "start_delay_boundary":
                    scenario["steps"][1]["scans"] = 4  # t=80ms: still off
                else:
                    for step in scenario["steps"]:
                        if step["expect"].get("MOTOR") is True:
                            step["scans"] += 2
            scenario_file.write_text(json.dumps(revised_scenarios))
            revised = call("verify", [project])
            assert revised["ok"] and revised["program_hash"] == changed["program_hash"]
            boundary = revised["scenarios"][0]["steps"]
            assert boundary[1]["scan"] == 5 and boundary[1]["actual"]["MOTOR"] is False
            assert boundary[2]["scan"] == 6 and boundary[2]["actual"]["MOTOR"] is True
            checks.append("explicit 100ms requirement passes at scan 6 while remaining off at scan 5")
            source.write_text(original)
            scenario_file.write_text(original_scenarios)

            scenarios = json.loads(original_scenarios)
            scenarios["scenarios"][0]["steps"][0].setdefault("inputs", {})["UNDECLARED_INPUT"] = True
            scenario_file.write_text(json.dumps(scenarios))
            refused = call("verify", [project], expected=1)
            assert not refused["ok"] and refused["diagnostics"]
            assert not refused.get("build")
            checks.append("unknown scenario input is refused before compiling or deploying")
            scenario_file.write_text(original_scenarios)

            invalid, replacements = re.subn(r"(?mi)^(\s*MOTOR\s*:=)[^;]*;", r"\1 ;", original, count=1)
            assert replacements == 1
            source.write_text(invalid)
            compiler_failure = call("verify", [project], expected=1)
            assert not compiler_failure["ok"] and compiler_failure["stage"] == "build"
            assert compiler_failure["build"]["stage"] == "iec2c"
            checks.append("invalid ST returns real MatIEC diagnostics through verify")
            source.write_text(original)

            repaired = call("verify", [project])
            assert repaired["ok"] and repaired["program_hash"] == baseline["program_hash"]
            checks.append("restored ST satisfies the unchanged acceptance scenarios")

            built = call("build", ["--target", "open-linux", "--project", project, "--execute"])
            deployed = call("flash", ["--target", "open-linux", "--manifest", built["manifest_path"], "--execute"])
            started = call("run", ["--manifest", deployed["manifest_path"], "--duration", "3", "--sample-ms", "20"])
            active = started["run_id"]
            assert started["sample"]["values"]["MOTOR"] is False
            observed = call("monitor", ["--target", "open-linux", "--run-id", active])
            assert observed["sample"]["program_hash"] == built["program_hash"]
            stopped = call("stop", ["--run-id", active])
            assert stopped["status"] == "stopped" and not stopped["process_alive"]
            active = None
            checks.append("generic signal project also builds, runs, monitors and stops as a real Linux process")

            analog = root / "analog"
            example = Path(__file__).resolve().parents[1] / "examples/open_linux/analog_hysteresis"
            shutil.copytree(example, analog)
            analog_report = call("verify", [analog])
            assert analog_report["ok"]
            checks.append("REAL threshold and tolerance assertions execute actual ST")

            for cycle_ms in (7, 60, 1000):
                periodic = root / f"motor_{cycle_ms}ms"
                call("new", [periodic, "--target", "open-linux", "--template", "motor",
                             "--cycle-ms", cycle_ms])
                periodic_report = call("verify", [periodic])
                assert periodic_report["ok"] and periodic_report["summary"]["scenarios"] == 6
            checks.append("motor timing scenarios adapt to 7ms, 60ms and 1000ms task periods")
        finally:
            if active:
                call("stop", ["--run-id", active])

    report = {"schema": "plcidf.ai-workflow-check.v1", "result": "passed",
              "timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "checks": checks, "calls": calls, "raw_traces": traces,
              "windows_processes_used": False, "hardware_accessed": False,
              "physical_io_verified": False, "hard_realtime_verified": False,
              "note": "Temporary paths removed; raw traces embedded above retain execution evidence."}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"result": "passed", "checks": checks,
                      "report": str(args.report) if args.report else None}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
