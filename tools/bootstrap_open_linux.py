#!/usr/bin/env python3
"""Build the pinned upstream MatIEC locally; no sudo or system installation.

The optional --local-autotools downloads Ubuntu build-tool packages and extracts
only inside --directory. Upstream code/binaries are never added to this repo.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tarfile
import urllib.request

MATIEC_COMMIT = "3a41303bcb4e50403417bc39c63f88981d7d961e"
ARCHIVE_SHA256 = "21d249d9c65ff1a26404cfe0969a14a0c12d05e53871e26d77bdd4e8fd0a89a2"
URL = "https://codeload.github.com/beremiz/matiec/tar.gz/" + MATIEC_COMMIT


def run_bounded(argv, *, cwd=None, env=None, log=None, timeout=60):
    """Bound the whole build process group, including make/compiler children."""
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=log or subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate(timeout=3)
        raise RuntimeError(f"{argv[0]} timed out after {timeout}s; process group terminated") from exc
    if proc.returncode:
        raise RuntimeError(f"{argv[0]} failed ({proc.returncode}); " + (output or "see toolchain-build.log")[-2000:])
    return output or ""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bootstrap(directory: Path, local_autotools: bool = False, jobs: int = 2):
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("gcc", "g++", "make", "bison", "flex", "m4", "perl"):
        if not shutil.which(name):
            raise RuntimeError(f"missing build dependency: {name}; no system installation attempted")
    archive = directory / "matiec.tar.gz"
    if not archive.exists():
        with urllib.request.urlopen(URL, timeout=60) as response:
            data = response.read(16 * 1024 * 1024 + 1)
        if len(data) > 16 * 1024 * 1024:
            raise RuntimeError("upstream archive exceeds size bound")
        archive.write_bytes(data)
    if sha(archive) != ARCHIVE_SHA256:
        raise RuntimeError("pinned MatIEC source archive SHA-256 mismatch")
    source = directory / ("matiec-" + MATIEC_COMMIT)
    if not source.exists():
        with tarfile.open(fileobj=io.BytesIO(archive.read_bytes()), mode="r:gz") as tar:
            tar.extractall(directory, filter="data")
    # Verify every tracked upstream member before rebuilding an existing tree.
    # Configure/build products are additional files and do not alter this check.
    with tarfile.open(archive, mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile():
                relative = Path(member.name).relative_to("matiec-" + MATIEC_COMMIT)
                expected = tar.extractfile(member).read()
                if (source / relative).read_bytes() != expected:
                    raise RuntimeError("existing upstream source was modified: " + str(relative))
    env = os.environ.copy()
    dependency_packages = []
    if local_autotools:
        if not shutil.which("apt-get") or not shutil.which("dpkg-deb"):
            raise RuntimeError("--local-autotools requires Ubuntu/Debian apt-get and dpkg-deb")
        debs = directory / "debs"
        debs.mkdir(exist_ok=True)
        run_bounded(["apt-get", "download", "autoconf", "automake", "autotools-dev"], cwd=debs, timeout=90)
        prefix = directory / "autotools"
        for deb in sorted(debs.glob("*.deb")):
            run_bounded(["dpkg-deb", "-x", str(deb), str(prefix)], timeout=30)
            dependency_packages.append({"file": deb.name, "sha256": sha(deb)})
        usr = prefix / "usr"
        automakes = sorted((usr / "bin").glob("automake-*"))
        aclocals = sorted((usr / "bin").glob("aclocal-*"))
        if len(automakes) != 1 or len(aclocals) != 1:
            raise RuntimeError("cannot identify one extracted automake/aclocal version")
        automake_version = automakes[0].name.removeprefix("automake-")
        config = usr / "share/autoconf/autom4te.cfg"
        config.write_text(config.read_text().replace("/usr/share/autoconf", str(usr / "share/autoconf")))
        env.update({
            "PATH": str(usr / "bin") + os.pathsep + env.get("PATH", ""),
            "AUTOCONF": str(usr / "bin/autoconf"), "AUTOM4TE": str(usr / "bin/autom4te"),
            "AUTOHEADER": str(usr / "bin/autoheader"), "AUTOMAKE": str(automakes[0]),
            "ACLOCAL": str(aclocals[0]),
            "AC_MACRODIR": str(usr / "share/autoconf"),
            "autom4te_perllibdir": str(usr / "share/autoconf"),
            "autom4te_buildauxdir": str(usr / "share/autoconf/build-aux"),
            "PERL5LIB": str(usr / ("share/automake-" + automake_version)),
            "ACLOCAL_AUTOMAKE_DIR": str(usr / ("share/aclocal-" + automake_version)),
            "AUTOMAKE_LIBDIR": str(usr / ("share/automake-" + automake_version)),
            "trailer_m4": str(usr / "share/autoconf/autoconf/trailer.m4"),
        })
    elif not shutil.which("autoreconf"):
        raise RuntimeError("missing autoreconf; use --local-autotools on Ubuntu without sudo")
    log_path = directory / "toolchain-build.log"
    with log_path.open("w") as log:
        commands = [([str(Path(env["AUTOCONF"]).with_name("autoreconf")) if "AUTOCONF" in env else "autoreconf", "-i"], 90),
                    (["./configure"], 90), (["make", "-j" + str(jobs)], 300)]
        for argv, timeout in commands:
            run_bounded(argv, cwd=source, env=env, log=log, timeout=timeout)
    (directory / "bin").mkdir(exist_ok=True)
    shutil.copy2(source / "iec2c", directory / "bin/iec2c")
    shutil.copytree(source / "lib", directory / "lib", dirs_exist_ok=True)
    (directory / "licenses").mkdir(exist_ok=True)
    shutil.copy2(source / "COPYING", directory / "licenses/MATIEC-COPYING")
    # Runtime C headers carry their own LGPLv3-or-later notices; preserve headers.
    versions = {}
    for tool in ("gcc", "g++", "make", "bison", "flex"):
        versions[tool] = run_bounded([tool, "--version"], timeout=10).splitlines()[0]
    record = {"schema": "plcidf.open-linux.toolchain.v1", "matiec_commit": MATIEC_COMMIT,
              "source_url": URL, "source_archive_sha256": ARCHIVE_SHA256,
              "iec2c_sha256": sha(directory / "bin/iec2c"),
              "library_sha256": {str(p.relative_to(directory)): sha(p) for p in sorted((directory / "lib").rglob("*")) if p.is_file()},
              "build_tools": versions, "local_build_packages": dependency_packages,
              "licenses": {"compiler": "GPL-3.0-or-later (upstream COPYING)",
                           "C_runtime_headers": "Mixed LGPL-2.0-or-later / LGPL-3.0-or-later; individual upstream header notices apply"}}
    (directory / "toolchain.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "status": "installed", "toolchain_dir": str(directory),
            "manifest_path": str(directory / "toolchain.json"), "matiec_commit": MATIEC_COMMIT}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--local-autotools", action="store_true")
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error("--jobs must be 1..16")
    try:
        result = bootstrap(args.directory, args.local_autotools, args.jobs)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "status": "failed", "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
