#!/usr/bin/env python3
"""Exercise real ST through the installed CLI, compiler and local runtime.

The compiler must already be bootstrapped. This check never invokes Windows,
opens a PLC connection, or uses the fixed Modbus scenario simulator.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--toolchain-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    toolchain = args.toolchain_dir.expanduser().resolve()
    steps = []
    checks = []
    running = set()

    with tempfile.TemporaryDirectory(prefix="plcidf-native-cli-") as directory:
        root = Path(directory)
        state = root / "runtime"
        project = root / "project"
        config = root / "tool.toml"
        config.write_text('[paths]\nproject_root = ' + json.dumps(str(root)) + '\n')

        def call(command, options=(), expected=0):
            argv = [sys.executable, "-m", "plc_idf.cli", command, *map(str, options),
                    "--config", str(config), "--json"]
            if command not in ("new", "check", "targets"):
                argv += ["--toolchain-dir", str(toolchain), "--state-dir", str(state)]
            result = subprocess.run(argv, capture_output=True, text=True, timeout=180,
                                    cwd=root)
            try:
                report = json.loads(result.stdout)
            except ValueError as exc:
                raise AssertionError(f"CLI did not emit a single JSON document: {result.stdout} {result.stderr}") from exc
            steps.append({"argv": argv, "returncode": result.returncode,
                          "report": report, "stderr": result.stderr})
            if result.returncode != expected:
                raise AssertionError(f"{command} returned {result.returncode}, expected {expected}: {report}")
            return report

        def observe_until(run_id, predicate, timeout=3.0):
            deadline = time.monotonic() + timeout
            history = []
            while time.monotonic() < deadline:
                observed = call("monitor", ["--target", "open-linux", "--run-id", run_id])
                history.append(observed)
                if predicate(observed, history):
                    return observed, history
                time.sleep(.025)
            raise AssertionError(f"runtime condition was not observed: {history[-1:]}")

        try:
            assert call("doctor", ["--target", "open-linux"])["ok"]
            assert len(call("targets")["profiles"]) >= 2
            call("new", [project, "--target", "open-linux", "--name", "NativeDemo"])
            checked = call("check", [project])
            assert checked["compiled"] is False and checked["schema_version"] == 2
            source = project / "main.st"
            original = source.read_text()

            source.write_text(original.replace("COUNTER := COUNTER + 1;", "COUNTER := ;"))
            negative = call("build", ["--target", "open-linux", "--project", project,
                                      "--build-dir", root / "negative", "--execute"], expected=1)
            assert negative["ok"] is False and negative["status"] == "failed"
            assert negative["stage"] == "iec2c"
            checks.append("invalid ST rejected by actual MatIEC")
            source.write_text(original)

            pointer = root / "build-pointer.json"
            built = call("build", ["--target", "open-linux", "--project", project,
                                   "--build-dir", root / "builds", "--manifest", pointer,
                                   "--execute"])
            assert built["ok"] and built["status"] == "verified"
            manifest = built["manifest_path"]
            build_record = json.loads(Path(manifest).read_text())
            assert build_record["target"]["os"] == "linux"
            assert all(not any(arg.lower().endswith((".exe", ".dll")) for arg in command)
                       for command in build_record["commands"])
            refused = call("run", ["--manifest", manifest, "--duration", ".2"], expected=2)
            assert refused["ok"] is False
            checks.append("undeployed build cannot run")

            preview = call("flash", ["--target", "open-linux", "--manifest", pointer])
            assert preview["status"] == "preview" and not (state / "releases").exists()
            deployed = call("flash", ["--target", "open-linux", "--manifest", pointer, "--execute"])
            assert deployed["status"] == "deployed"
            release = deployed["manifest_path"]
            started = call("run", ["--manifest", release, "--duration", "2", "--sample-ms", "20"])
            run_id = started["run_id"]
            running.add(run_id)
            assert started["sample"]["program_hash"] == built["program_hash"]
            observed, history = observe_until(run_id, lambda o, h: o.get("sample", {}).get("counter", 0) >= 12)
            samples = [started["sample"], *(item["sample"] for item in history)]
            assert all(s["counter"] == s["scan_count"] for s in samples)
            stopped = call("stop", ["--run-id", run_id])
            assert stopped["status"] == "stopped" and stopped["process_alive"] is False
            raw_samples = [json.loads(line) for line in Path(stopped["samples_path"]).read_text().splitlines()]
            assert all(s["counter"] == s["scan_count"] for s in raw_samples)
            assert {s["heartbeat"] for s in raw_samples} == {False, True}
            running.remove(run_id)
            checks.append("real generated ST scans, changes counter and heartbeat, and stops")

            # A changed ST expression must produce changed executed behavior.
            source.write_text(original.replace("COUNTER := COUNTER + 1;", "COUNTER := COUNTER + 2;"))
            built_two = call("build", ["--target", "open-linux", "--project", project,
                                       "--build-dir", root / "builds", "--execute"])
            assert built_two["program_hash"] != built["program_hash"]
            released_two = call("flash", ["--target", "open-linux", "--manifest", built_two["manifest_path"], "--execute"])
            started_two = call("run", ["--manifest", released_two["manifest_path"],
                                       "--duration", ".35", "--sample-ms", "20"])
            run_two = started_two["run_id"]
            running.add(run_two)
            final, history_two = observe_until(run_two, lambda o, h: o["status"] == "completed")
            for sample in [started_two["sample"], *(o["sample"] for o in history_two)]:
                assert sample["counter"] == 2 * sample["scan_count"]
                assert sample["program_hash"] == built_two["program_hash"]
            assert final["process_alive"] is False
            running.remove(run_two)
            checks.append("changing ST +1 to +2 changes the executed scan results")

            # Historical samples remain available, but changing a deployed byte
            # must not report a currently intact application or permit restart.
            binary = Path(released_two["manifest_path"]).parent / "plc_runtime"
            binary.write_bytes(binary.read_bytes() + b"changed")
            changed = call("monitor", ["--target", "open-linux", "--run-id", run_two], expected=1)
            assert changed["ok"] is False
            call("run", ["--manifest", released_two["manifest_path"], "--duration", ".2"], expected=2)
            checks.append("changed deployed bundle is refused and historical evidence is distinguished")

            report = {"schema_version": 1, "result": "passed",
                      "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      "toolchain": str(toolchain), "checks": checks, "steps": steps,
                      "source_examples": {"increment_1_sha256": hashlib.sha256(original.encode()).hexdigest(),
                                          "increment_2_program_hash": built_two["program_hash"]},
                      "increment_1_samples": raw_samples,
                      "hardware_accessed": False, "windows_processes_used": False,
                      "physical_io_verified": False, "hard_realtime_verified": False,
                      "note": "Temporary application/release/run paths are removed after validation."}
        finally:
            for run_id in running:
                try:
                    call("stop", ["--run-id", run_id])
                except Exception:
                    pass
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"result": report["result"], "checks": checks,
                      "report": str(args.report) if args.report else None}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
