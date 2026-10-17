"""Build an sdist/wheel and exercise a fresh installation outside the checkout.

Usage: python tools/check_distribution.py [--report /path/evidence.json]
Requires the development-only ``build`` package. Never accesses PLC hardware.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ASSETS = (
    "wsl-tools/e521_monitor.py",
    "wsl-tools/virtual_easy521.py",
    "wsl-tools/e521_regs.py",
    "plc/tools/autoshop_ui.ps1",
    "examples/open_linux/counter/main.st",
    "tools/bootstrap_open_linux.py",
)
OPEN_LINUX_TEMPLATE = "examples/open_linux/counter/main.st"


def run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 120,
        expected_returncode: int = 0):
    result = subprocess.run(argv, cwd=cwd, env=env, text=True,
                            capture_output=True, timeout=timeout)
    if result.returncode != expected_returncode:
        sys.stderr.write(result.stdout + result.stderr)
        raise RuntimeError(f"Unexpected exit {result.returncode}, expected "
                           f"{expected_returncode}: {argv!r}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--dist-dir", type=Path,
                        help="Keep the built wheel and sdist in this directory")
    args = parser.parse_args()
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PLCIDF_ROOT", "PLCIDF_CONFIG", "PY2PLC_ROOT",
                "PY2PLC_CONFIG", "PLCIDF_OPEN_LINUX_TOOLCHAIN", "PLCIDF_OPEN_LINUX_STATE"):
        env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    with tempfile.TemporaryDirectory(prefix="plcidf-distribution-") as temp:
        work = Path(temp)
        artifacts = work / "artifacts"
        artifacts.mkdir()
        # Default pypa/build flow creates the sdist first, then builds its wheel.
        run([sys.executable, "-m", "build", "--outdir", str(artifacts), str(ROOT)],
            cwd=work, env=env)
        wheel, = artifacts.glob("*.whl")
        sdist, = artifacts.glob("*.tar.gz")
        asset_hashes = {}
        with zipfile.ZipFile(wheel) as archive:
            templates = sorted(name for name in archive.namelist()
                               if name.startswith("plc_idf/_resources/")
                               and name.lower().endswith(".st"))
            if templates != ["plc_idf/_resources/" + OPEN_LINUX_TEMPLATE]:
                raise RuntimeError(f"Wheel must contain one canonical ST template: {templates}")
            for asset in ASSETS:
                source = (ROOT / asset).read_bytes()
                bundled = archive.read("plc_idf/_resources/" + asset)
                if source != bundled:
                    raise RuntimeError(f"Wheel resource differs from source: {asset}")
                asset_hashes[asset] = hashlib.sha256(bundled).hexdigest()
        venv = work / "venv"
        run([sys.executable, "-m", "venv", str(venv)], cwd=work, env=env)
        python = venv / "bin/python"
        run([str(python), "-m", "pip", "install", str(wheel)], cwd=work, env=env)
        project = work / "user-project"
        project.mkdir()
        probe = run([str(python), "-c", "\n".join([
            "import json, pathlib, plc_idf, pymodbus, serial",
            "from plc_idf.config import load_config, runtime_asset, evidence_path",
            "cfg = load_config()",
            "root = pathlib.Path(cfg.paths.project_root)",
            "bridge = runtime_asset(root, 'plc/tools/autoshop_ui.ps1')",
            "bootstrap = runtime_asset(root, 'tools/bootstrap_open_linux.py')",
            "template = runtime_asset(root, 'examples/open_linux/counter/main.st')",
            "package = pathlib.Path(plc_idf.__file__).resolve().parent",
            "installed_templates = sorted(p for p in (package / '_resources').rglob('*')",
            "    if p.is_file() and p.suffix.lower() == '.st')",
            "assert bridge.is_file()",
            "assert bootstrap.is_file() and bootstrap.is_relative_to(package)",
            "assert template.is_file() and template.is_relative_to(package)",
            "assert installed_templates == [template]",
            "assert root == pathlib.Path.cwd()",
            "print(json.dumps({'module': plc_idf.__file__, 'root': str(root),",
            "'monitor_dir': cfg.paths.wsl_tools, 'bridge': str(bridge),",
            "'bridge_windows': cfg.bridge.tools_ps1_windows,",
            "'evidence_path': str(evidence_path(cfg)),",
            "'bootstrap': str(bootstrap), 'st_template': str(template),",
            "'installed_st_templates': [str(p) for p in installed_templates],",
            "'pymodbus': pymodbus.__version__, 'pyserial': serial.__version__}))",
        ])], cwd=project, env=env)
        paths = json.loads(probe.stdout)
        if Path(paths["module"]).is_relative_to(ROOT):
            raise RuntimeError("Distribution test accidentally imported the checkout")
        cli = venv / "bin/plcidf"
        simulation = run([str(cli), "sim", "--with-monitor", "--once", "--quiet"],
                         cwd=project, env=env, timeout=45)
        if "Easy521 监测" not in simulation.stdout or "Vpu" not in simulation.stdout:
            raise RuntimeError("Installed simulator did not produce contract measurements")
        for scenario in ("low_voltage", "overload"):
            run([str(cli), "sim", "--with-monitor", "--once", "--quiet",
                 "--scenario", scenario], cwd=project, env=env, timeout=45)
        # Argument parsing and resource imports are exercised; no device opened.
        run([str(python), str(Path(paths["monitor_dir"]) / "e521_monitor.py"),
             "--help"], cwd=project, env=env)
        run([str(cli), "build"], cwd=project, env=env)
        alias = run([str(venv / "bin/py2plc"), "--version"], cwd=project, env=env)
        # Multi-target commands must work from only the installed distribution.
        targets = json.loads(run([str(cli), "targets", "--json"],
                                 cwd=project, env=env).stdout)
        profiles = {profile["id"]: profile for profile in targets.get("profiles", [])}
        if (targets.get("schema_version") != 2 or targets.get("ok") is not True
                or targets.get("hardware_checked") is not False
                or not {"easy521", "open-linux"}.issubset(profiles)
                or profiles["open-linux"].get("host_platform") != "Linux"):
            raise RuntimeError("Installed target listing does not describe the Linux profile")
        open_project = project / "open_counter"
        created = json.loads(run([str(cli), "new", str(open_project),
                                  "--target", "open-linux", "--json"],
                                 cwd=project, env=env).stdout)
        checked = json.loads(run([str(cli), "check", str(open_project), "--json"],
                                 cwd=open_project, env=env).stdout)
        for name, result in (("new", created), ("check", checked)):
            if (result.get("schema_version") != 2 or result.get("profile") != "open-linux"
                    or result.get("ok") is not True or result.get("compiled") is not False):
                raise RuntimeError(f"Installed {name} did not return the v2 uncompiled project contract")
        open_manifest = json.loads((open_project / "plcidf.json").read_text(encoding="utf-8"))
        if (open_manifest.get("schema_version") != 2
                or open_manifest.get("profile_id") != "open-linux"
                or "windows_project" in open_manifest or "busid" in open_manifest):
            raise RuntimeError("Installed new produced a Windows/USB-coupled project manifest")
        if (open_project / "main.st").read_bytes() != Path(paths["st_template"]).read_bytes():
            raise RuntimeError("Installed new did not use the canonical bundled ST template")
        # --help exercises the installed bootstrap entry without downloading or
        # constructing a toolchain; actual ST compilation has its own CI job.
        run([str(python), paths["bootstrap"], "--help"], cwd=open_project, env=env)
        unavailable_toolchain = work / "deliberately_absent_toolchain"
        local_state = work / "local_runtime_state"
        no_windows = work / "no_windows.toml"
        no_windows.write_text(
            '[bridge]\npowershell_exe = "/intentionally/absent/powershell.exe"\n'
            '[device]\nusbipd_exe = "/intentionally/absent/usbipd.exe"\n', encoding="utf-8")
        doctor_process = run([str(cli), "doctor", "--target", "open-linux", "--json",
                              "--toolchain-dir", str(unavailable_toolchain),
                              "--state-dir", str(local_state), "--config", str(no_windows)],
                             cwd=open_project, env=env, expected_returncode=1)
        doctor = json.loads(doctor_process.stdout)
        if (doctor.get("schema_version") != 2 or doctor.get("target") != "open-linux"
                or doctor.get("command") != "doctor" or doctor.get("ok") is not False
                or doctor.get("status") != "unavailable"
                or doctor.get("hardware_checked") is not False
                or doctor.get("toolchain_dir") != str(unavailable_toolchain)
                or not isinstance(doctor.get("error"), str) or not doctor["error"]
                or doctor.get("target_profile", {}).get("host_platform") != "Linux"):
            raise RuntimeError("Missing Linux toolchain did not produce structured unavailable diagnostics")
        if unavailable_toolchain.exists() or local_state.exists():
            raise RuntimeError("Read-only Linux doctor unexpectedly created toolchain/runtime state")
        evidence = project / "logs/evidence.jsonl"
        rows = [json.loads(line) for line in evidence.read_text().splitlines()]
        report = {
            "schema_version": 1,
            "result": "passed",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "source_revision": run(["git", "rev-parse", "HEAD"],
                                   cwd=ROOT, env=env).stdout.strip(),
            "working_tree_dirty": bool(run(["git", "status", "--porcelain"],
                                           cwd=ROOT, env=env).stdout.strip()),
            "python_version": sys.version,
            "wheel": wheel.name,
            "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "sdist": sdist.name,
            "sdist_sha256": hashlib.sha256(sdist.read_bytes()).hexdigest(),
            "runtime_asset_sha256": asset_hashes,
            "installed_paths": paths,
            "alias_version": alias.stdout.strip(),
            "tested_scenarios": ["steady", "low_voltage", "overload"],
            "open_linux_checks": {
                "targets": targets, "new": created, "check": checked,
                "project_manifest": open_manifest,
                "template_sha256": hashlib.sha256((open_project / "main.st").read_bytes()).hexdigest(),
                "doctor_missing_toolchain": {"returncode": doctor_process.returncode,
                                              "expected_returncode": 1, "result": doctor},
                "bootstrap_help_checked": True,
                "toolchain_downloaded": False, "native_compilation_executed": False,
                "local_runtime_started": False,
            },
            "monitor_output": simulation.stdout,
            "evidence_events": len(rows),
            "hardware_accessed": False,
            "note": "Temporary installation paths are removed after validation.",
        }
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)
                                   + "\n", encoding="utf-8")
        if args.dist_dir:
            args.dist_dir.mkdir(parents=True, exist_ok=True)
            for artifact in (wheel, sdist):
                shutil.copy2(artifact, args.dist_dir / artifact.name)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
