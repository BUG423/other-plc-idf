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
)


def run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 120):
    result = subprocess.run(argv, cwd=cwd, env=env, text=True,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        sys.stderr.write(result.stdout + result.stderr)
        raise RuntimeError(f"Command failed ({result.returncode}): {argv!r}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--dist-dir", type=Path,
                        help="Keep the built wheel and sdist in this directory")
    args = parser.parse_args()
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PLCIDF_ROOT", "PLCIDF_CONFIG", "PY2PLC_ROOT",
                "PY2PLC_CONFIG"):
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
            "assert bridge.is_file()",
            "assert root == pathlib.Path.cwd()",
            "print(json.dumps({'module': plc_idf.__file__, 'root': str(root),",
            "'monitor_dir': cfg.paths.wsl_tools, 'bridge': str(bridge),",
            "'bridge_windows': cfg.bridge.tools_ps1_windows,",
            "'evidence_path': str(evidence_path(cfg)),",
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
