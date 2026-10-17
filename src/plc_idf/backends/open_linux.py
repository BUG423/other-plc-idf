"""Pinned MatIEC -> C -> real local Linux scan runtime.

This initial profile supports one MAIN/Config0/Res0/scan/instance0 and exposes
COUNTER:DINT and HEARTBEAT:BOOL. It is a local development runtime, with no
physical I/O driver and no claim of industrial timing or Easy521 compatibility.
No Windows or PLC interfaces are imported or called.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

MATIEC_COMMIT = "3a41303bcb4e50403417bc39c63f88981d7d961e"
PROFILE = "open-linux-counter-v1"
BUILD_SCHEMA = "plcidf.open-linux.build.v1"
RELEASE_SCHEMA = "plcidf.open-linux.release.v1"
RUN_SCHEMA = "plcidf.open-linux.run.v1"


class OpenLinuxError(ValueError):
    """Invalid, incompatible, changed, or unverifiable local runtime input."""


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write_json(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".plcidf-")
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(record, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
    finally:
        if Path(temp).exists():
            Path(temp).unlink()


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise OpenLinuxError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OpenLinuxError("manifest must be a JSON object")
    return value


def _inside(root, relative):
    if not isinstance(relative, str) or not relative:
        raise OpenLinuxError("bundle path must be nonempty and relative")
    raw = PurePosixPath(relative)
    if raw.is_absolute() or ".." in raw.parts or "\\" in relative:
        raise OpenLinuxError("bundle path must stay inside the bundle")
    root = Path(root).resolve()
    path = root.joinpath(*raw.parts)
    cursor = root
    for part in raw.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise OpenLinuxError("symlink bundle members are refused")
    if not path.resolve().is_relative_to(root):
        raise OpenLinuxError("bundle member escapes its root")
    return path


def _profile(source, cycle_ms):
    if type(cycle_ms) is not int or not 1 <= cycle_ms <= 1000:
        raise OpenLinuxError("cycle_ms must be an integer in 1..1000")
    if not source or "\0" in source:
        raise OpenLinuxError("source must be nonempty UTF-8 ST without NUL")
    clean = re.sub(r"\(\*.*?\*\)", "", source, flags=re.S)
    # This profile deliberately has a single, fully hashed source. Do not permit
    # generated C pragmas, includes, or externally linked POUs to hide inputs.
    if any(char in clean for char in "{}") or re.search(r"(?m)^\s*#|#\s*(?:include|pragma)|\b(?:VAR_EXTERNAL|EXTERNAL)\b", clean, re.I):
        raise OpenLinuxError("external source references and inline C/pragmas are unsupported")
    patterns = [r"\bPROGRAM\s+MAIN\b", r"\bCOUNTER\s*:\s*DINT\b",
                r"\bHEARTBEAT\s*:\s*BOOL\b", r"\bCONFIGURATION\s+Config0\b",
                r"\bRESOURCE\s+Res0\s+ON\s+PLC\b",
                r"\bPROGRAM\s+instance0\s+WITH\s+scan\s*:\s*MAIN\s*;"]
    if not all(re.search(pattern, clean, re.I) for pattern in patterns):
        raise OpenLinuxError("profile requires MAIN with COUNTER:DINT/HEARTBEAT:BOOL and Config0/Res0/scan/instance0")
    if len(re.findall(r"\bCONFIGURATION\b", clean, re.I)) != 1 or len(re.findall(r"\bRESOURCE\b", clean, re.I)) != 1:
        raise OpenLinuxError("profile supports one configuration and one resource")
    tasks = re.findall(r"\bTASK\s+(\w+)\s*\((.*?)\)\s*;", clean, re.I | re.S)
    if len(tasks) != 1 or tasks[0][0].lower() != "scan":
        raise OpenLinuxError("profile supports one periodic task named scan")
    match = re.fullmatch(r"\s*INTERVAL\s*:=\s*T#(\d+)ms\s*,\s*PRIORITY\s*:=\s*0\s*", tasks[0][1], re.I)
    if not match or int(match[1]) != cycle_ms:
        raise OpenLinuxError("ST scan interval must match cycle_ms exactly (T#Nms, priority 0)")
    if len(re.findall(r"\bPROGRAM\b", clean, re.I)) != 2:
        raise OpenLinuxError("profile supports one PROGRAM declaration and one instance")


# No ST application behavior is emulated here. config_init__/config_run__ and
# MAIN's fields come from MatIEC-generated C; this runner only schedules scans,
# supplies IEC time, and serializes the resulting fields.
RUNNER_C = r'''
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <signal.h>
#include <string.h>
#include <time.h>
#include <errno.h>
#include "iec_std_lib.h"
#include "accessor.h"
#include "POUS.h"
extern MAIN_data__ RES0__INSTANCE0;
extern unsigned long long common_ticktime__;
void config_init__(void);
void config_run__(unsigned long tick);
TIME __CURRENT_TIME;
BOOL __DEBUG = 0;
static volatile sig_atomic_t stopping = 0;
static void request_stop(int sig) { (void)sig; stopping = 1; }
static uint64_t monotonic_ns(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}
static struct timespec as_timespec(uint64_t ns) {
    struct timespec ts = { (time_t)(ns / 1000000000ULL), (long)(ns % 1000000000ULL) };
    return ts;
}
static void emit(const char *run_id, const char *binary_hash, const char *state,
                 unsigned long scans, uint64_t elapsed, uint64_t max_late) {
    printf("{\"schema_version\":1,\"run_id\":\"%s\",\"program_hash\":\"@PROGRAM_HASH@\","
           "\"source_sha256\":\"@SOURCE_HASH@\",\"binary_sha256\":\"%s\","
           "\"runtime\":\"matiec-local-scan-v1\",\"state\":\"%s\",\"scan_count\":%lu,"
           "\"counter\":%d,\"heartbeat\":%s,\"elapsed_ns\":%llu,\"cycle_ns\":%llu,"
           "\"max_observed_lateness_ns\":%llu}\n",
           run_id, binary_hash, state, scans, (int)RES0__INSTANCE0.COUNTER.value,
           RES0__INSTANCE0.HEARTBEAT.value ? "true" : "false",
           (unsigned long long)elapsed, common_ticktime__, (unsigned long long)max_late);
    fflush(stdout);
}
int main(int argc, char **argv) {
    if (argc != 5 || strlen(argv[1]) != 32 || strlen(argv[2]) != 64) return 2;
    double duration = strtod(argv[3], NULL), sample_ms = strtod(argv[4], NULL);
    if (!(duration > 0 && duration <= 3600 && sample_ms >= 1 && sample_ms <= 10000)) return 2;
    // The kernel deadline also bounds ST that never returns from a scan.
    // It is independent of cooperative scan-loop checks and SIGTERM handling.
    timer_t deadline_timer;
    struct sigevent event = {0};
    event.sigev_notify = SIGEV_SIGNAL; event.sigev_signo = SIGKILL;
    if (timer_create(CLOCK_MONOTONIC, &event, &deadline_timer)) return 5;
    struct itimerspec deadline_spec = {0};
    deadline_spec.it_value = as_timespec((uint64_t)((duration + 1.0) * 1000000000.0));
    if (timer_settime(deadline_timer, 0, &deadline_spec, NULL)) return 5;
    signal(SIGTERM, request_stop); signal(SIGINT, request_stop);
    setvbuf(stdout, NULL, _IOLBF, 0);
    config_init__();
    if (!common_ticktime__) return 3;
    const uint64_t start = monotonic_ns();
    const uint64_t duration_ns = (uint64_t)(duration * 1000000000.0);
    const uint64_t sample_ns = (uint64_t)(sample_ms * 1000000.0);
    uint64_t next = start, next_sample = start, max_late = 0;
    unsigned long scans = 0;
    while (!stopping && monotonic_ns() - start < duration_ns) {
        struct timespec wake = as_timespec(next);
        int ret;
        while ((ret = clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &wake, NULL)) == EINTR && !stopping) {}
        if (ret && ret != EINTR) return 4;
        if (stopping) break;
        const uint64_t now = monotonic_ns();
        if (now - start >= duration_ns) break;
        if (now > next && now - next > max_late) max_late = now - next;
        __CURRENT_TIME.tv_sec = (long)((now - start) / 1000000000ULL);
        __CURRENT_TIME.tv_nsec = (long)((now - start) % 1000000000ULL);
        config_run__(scans);
        ++scans;
        if (now >= next_sample) {
            emit(argv[1], argv[2], "running", scans, now - start, max_late);
            next_sample = now + sample_ns;
        }
        // Missed deadlines do not trigger an unbounded burst of catch-up scans.
        next += common_ticktime__;
        if (next < now) next = now + common_ticktime__;
    }
    emit(argv[1], argv[2], stopping ? "stopped" : "completed", scans,
         monotonic_ns() - start, max_late);
    return 0;
}
'''


def _process(pid):
    """Observe Linux process starttime, state and argv without sending signals."""
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        argv = Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
        return {"start_ticks": fields[19], "state": fields[0],
                "argv": [item.decode() for item in argv if item]}
    except (OSError, ValueError, IndexError, UnicodeError):
        return None


class OpenLinuxBackend:
    name = "open_linux"

    def __init__(self, toolchain_dir, state_dir):
        self.toolchain_dir = Path(toolchain_dir).expanduser().resolve()
        self.state_dir = Path(state_dir).expanduser().resolve()
        self._children = {}
        if platform.system() != "Linux":
            raise OpenLinuxError("the local scan runtime requires Linux")

    def install(self):
        """Read-only validation of a toolchain created by bootstrap_open_linux.py."""
        root = self.toolchain_dir
        try:
            record = _read_json(root / "toolchain.json")
            if record.get("schema") != "plcidf.open-linux.toolchain.v1" or record.get("matiec_commit") != MATIEC_COMMIT:
                raise OpenLinuxError("unsupported toolchain schema or MatIEC commit")
            compiler = _inside(root, "bin/iec2c")
            if _sha(compiler) != record.get("iec2c_sha256"):
                raise OpenLinuxError("iec2c hash mismatch")
            libraries = record.get("library_sha256")
            if not isinstance(libraries, dict) or not libraries:
                raise OpenLinuxError("toolchain library hashes are missing")
            for path, digest in libraries.items():
                if not isinstance(path, str) or not path.startswith("lib/") or not isinstance(digest, str) or _sha(_inside(root, path)) != digest:
                    raise OpenLinuxError("toolchain library hash mismatch: " + path)
            if not shutil.which("gcc"):
                raise OpenLinuxError("gcc is unavailable")
        except (OpenLinuxError, OSError) as exc:
            return {"ok": False, "status": "unavailable", "error": str(exc),
                    "toolchain_dir": str(root)}
        return {"ok": True, "status": "verified", "toolchain_dir": str(root),
                "matiec_commit": MATIEC_COMMIT, "iec2c_sha256": record["iec2c_sha256"],
                "toolchain_hash": _digest(record), "licenses": record.get("licenses", {})}

    def build(self, source, build_dir, cycle_ms=20):
        """Compile a complete ST file into a fresh immutable-by-hash bundle."""
        installed = self.install()
        if not installed["ok"]:
            raise OpenLinuxError(installed["error"])
        text = Path(source).read_text(encoding="utf-8-sig")
        _profile(text, cycle_ms)
        parent = Path(build_dir).expanduser().resolve()
        parent.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="build-", dir=parent))
        source_path = root / "source.st"
        source_path.write_text(text, encoding="utf-8")
        source_hash = _sha(source_path)
        generated = root / "generated"
        generated.mkdir()
        compiler = self.toolchain_dir / "bin/iec2c"
        command = [str(compiler), "-I", str(self.toolchain_dir / "lib"), "-T", str(generated), str(source_path)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, cwd=root)
        except subprocess.TimeoutExpired:
            return {"ok": False, "status": "failed", "stage": "iec2c", "error": "compiler timed out", "build_dir": str(root)}
        diagnostics = result.stdout + result.stderr
        (root / "iec2c.log").write_text(diagnostics)
        if result.returncode:
            return {"ok": False, "status": "failed", "stage": "iec2c", "returncode": result.returncode,
                    "diagnostics": diagnostics, "source_sha256": source_hash, "build_dir": str(root)}
        generated_names = {path.name.lower(): path for path in generated.iterdir() if path.is_file()}
        for name in ("Config0.c", "Config0.h", "Res0.c", "POUS.h", "POUS.c"):
            if name.lower() not in generated_names:
                raise OpenLinuxError("compiler did not produce the required profile file: " + name)
        config_path, resource_path = generated_names["config0.c"], generated_names["res0.c"]
        config_c = config_path.read_text()
        tick = re.search(r"common_ticktime__\s*=\s*(\d+)", config_c)
        if not tick or int(tick[1]) != cycle_ms * 1000000:
            raise OpenLinuxError("generated task period differs from requested cycle_ms")
        runner_hash = hashlib.sha256(RUNNER_C.encode()).hexdigest()
        gcc_version = subprocess.run(["gcc", "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
        program_hash = _digest({"source_sha256": source_hash, "profile": PROFILE, "cycle_ms": cycle_ms,
                                "toolchain_hash": installed["toolchain_hash"], "runner_sha256": runner_hash,
                                "gcc_version": gcc_version, "machine": platform.machine()})
        runner = RUNNER_C.replace("@PROGRAM_HASH@", program_hash).replace("@SOURCE_HASH@", source_hash)
        (root / "runner.c").write_text(runner)
        command_c = ["gcc", "-std=c11", "-O2", "-I", str(self.toolchain_dir / "lib/C"),
                     "-I", str(generated), str(config_path), str(resource_path),
                     str(root / "runner.c"), "-lm", "-o", str(root / "plc_runtime")]
        compile_inputs = [source_path, root / "runner.c", *sorted(path for path in generated.iterdir() if path.is_file())]
        compile_snapshot = {path.relative_to(root).as_posix(): _sha(path) for path in compile_inputs}
        result_c = subprocess.run(command_c, capture_output=True, text=True, timeout=60, cwd=root)
        (root / "gcc.log").write_text(result_c.stdout + result_c.stderr)
        if result_c.returncode:
            return {"ok": False, "status": "failed", "stage": "gcc", "returncode": result_c.returncode,
                    "diagnostics": result_c.stdout + result_c.stderr, "build_dir": str(root)}
        after_snapshot = {path.relative_to(root).as_posix(): _sha(path) for path in compile_inputs}
        if compile_snapshot != after_snapshot or _sha(source_path) != source_hash:
            raise OpenLinuxError("compiler source or generated inputs changed during GCC build")
        final_toolchain = self.install()
        if not final_toolchain["ok"] or final_toolchain["toolchain_hash"] != installed["toolchain_hash"]:
            raise OpenLinuxError("toolchain changed during build")
        artifacts = []
        for path in [source_path, root / "runner.c", root / "plc_runtime", *sorted(generated.glob("*"))]:
            if path.is_file():
                artifacts.append({"path": path.relative_to(root).as_posix(), "sha256": _sha(path), "size": path.stat().st_size})
        manifest = {"schema": BUILD_SCHEMA, "profile": PROFILE, "status": "verified",
                    "created_at": datetime.now(timezone.utc).isoformat(), "program_hash": program_hash,
                    "source_sha256": source_hash, "source": "source.st", "executable": "plc_runtime",
                    "target": {"os": "linux", "architecture": platform.machine(), "physical_io": False},
                    "cycle_ms": cycle_ms, "toolchain": installed, "gcc_version": gcc_version,
                    "commands": [command, command_c], "artifacts": artifacts,
                    "observation": {"program": "MAIN", "instance": "RES0__INSTANCE0", "counter_type": "DINT", "heartbeat_type": "BOOL"}}
        manifest["build_hash"] = _digest(manifest)
        manifest_path = root / "build.json"
        _write_json(manifest_path, manifest)
        self._verify(manifest_path)
        return {"ok": True, "status": "verified", "manifest_path": str(manifest_path),
                "build_hash": manifest["build_hash"], "program_hash": program_hash,
                "source_sha256": source_hash, "binary_sha256": _sha(root / "plc_runtime"),
                "build_dir": str(root), "diagnostics": diagnostics, "cycle_ms": cycle_ms}

    def _verify(self, manifest_path):
        path = Path(manifest_path).expanduser().absolute()
        if path.is_symlink():
            raise OpenLinuxError("symlink manifest refused")
        manifest = _read_json(path)
        if manifest.get("schema") == RELEASE_SCHEMA:
            if manifest.get("release_hash") != _digest({k: v for k, v in manifest.items() if k != "release_hash"}):
                raise OpenLinuxError("release manifest integrity mismatch")
            if manifest.get("build_manifest") != "build.json":
                raise OpenLinuxError("release must reference its adjacent build.json")
            build_path = _inside(path.parent, manifest.get("build_manifest"))
            build, binary = self._verify(build_path)
            if build["build_hash"] != manifest.get("build_hash") or manifest.get("release_id") != build["build_hash"]:
                raise OpenLinuxError("release build hash/identity mismatch")
            return build, binary
        if manifest.get("schema") != BUILD_SCHEMA or manifest.get("profile") != PROFILE:
            raise OpenLinuxError("unsupported open-linux build schema/profile")
        if manifest.get("build_hash") != _digest({k: v for k, v in manifest.items() if k != "build_hash"}):
            raise OpenLinuxError("build manifest integrity mismatch")
        target = manifest.get("target")
        if not isinstance(target, dict) or manifest.get("status") != "verified" or target.get("os") != "linux" or target.get("architecture") != platform.machine():
            raise OpenLinuxError("unverified or incompatible build target")
        if not isinstance(manifest.get("program_hash"), str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["program_hash"]):
            raise OpenLinuxError("missing or invalid program identity")
        if manifest.get("source") != "source.st" or manifest.get("executable") != "plc_runtime":
            raise OpenLinuxError("unsupported bundle source or executable path")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise OpenLinuxError("empty build bundle")
        paths = set()
        for item in artifacts:
            if not isinstance(item, dict):
                raise OpenLinuxError("malformed artifact record")
            member = _inside(path.parent, item.get("path"))
            if item["path"] in paths or not member.is_file() or _sha(member) != item.get("sha256") or member.stat().st_size != item.get("size"):
                raise OpenLinuxError("changed, missing or duplicate bundle artifact")
            paths.add(item["path"])
        if not {"source.st", "runner.c", "plc_runtime"}.issubset(paths):
            raise OpenLinuxError("required bundle artifacts missing")
        if _sha(path.parent / "source.st") != manifest.get("source_sha256"):
            raise OpenLinuxError("source identity mismatch")
        _profile((path.parent / "source.st").read_text(), manifest.get("cycle_ms"))
        binary = path.parent / "plc_runtime"
        if not os.access(binary, os.X_OK) or binary.read_bytes()[:4] != b"\x7fELF":
            raise OpenLinuxError("bundle executable is not an executable ELF")
        return manifest, binary

    def deploy(self, manifest_path, execute=False):
        """Install a verified local application bundle; no PLC or firmware writes."""
        build, binary = self._verify(manifest_path)
        release_id = build["build_hash"]
        destination = self.state_dir / "releases" / release_id
        result = {"ok": True, "status": "preview", "release_id": release_id,
                  "build_hash": build["build_hash"], "program_hash": build["program_hash"],
                  "manifest_path": str(destination / "release.json")}
        if not execute:
            return result
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self._lock():
            if destination.exists():
                self._verify(destination / "release.json")
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                stage = Path(tempfile.mkdtemp(prefix=".release-", dir=destination.parent))
                try:
                    for item in build["artifacts"]:
                        src = _inside(binary.parent, item["path"])
                        dst = _inside(stage, item["path"])
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)
                    _write_json(stage / "build.json", build)
                    release = {"schema": RELEASE_SCHEMA, "release_id": release_id,
                               "build_hash": build["build_hash"], "build_manifest": "build.json",
                               "created_at": datetime.now(timezone.utc).isoformat()}
                    release["release_hash"] = _digest(release)
                    _write_json(stage / "release.json", release)
                    self._verify(stage / "release.json")
                    os.rename(stage, destination)
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
        result["status"] = "deployed"
        return result

    @contextmanager
    def _lock(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.state_dir / ".operations.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def run(self, manifest_path, duration_s=5.0, sample_interval_ms=100):
        """Launch bounded real ST execution in a separate local process."""
        if isinstance(duration_s, bool) or not isinstance(duration_s, (int, float)) or not math.isfinite(duration_s) or not 0 < duration_s <= 3600:
            raise OpenLinuxError("duration_s must be finite and in (0, 3600]")
        if type(sample_interval_ms) is not int or not 1 <= sample_interval_ms <= 10000:
            raise OpenLinuxError("sample_interval_ms must be an integer in 1..10000")
        release_path = Path(manifest_path).expanduser().absolute()
        release = _read_json(release_path)
        if release.get("schema") != RELEASE_SCHEMA:
            raise OpenLinuxError("run requires a deployed release.json; deploy the build first")
        build, binary = self._verify(release_path)
        expected = self.state_dir / "releases" / build["build_hash"] / "release.json"
        if release_path != expected or release_path.resolve() != expected:
            raise OpenLinuxError("run requires a release installed in this backend state_dir")
        run_id = uuid.uuid4().hex
        root = self.state_dir / "runs" / run_id
        root.mkdir(parents=True, exist_ok=False)
        binary_hash = next(item["sha256"] for item in build["artifacts"] if item["path"] == "plc_runtime")
        if _sha(binary) != binary_hash:
            raise OpenLinuxError("executable changed after manifest verification")
        argv = [str(binary), run_id, binary_hash, str(float(duration_s)), str(sample_interval_ms)]
        with self._lock():
            # Execute the same opened inode whose bytes were just checked. This
            # also avoids a pathname swap between validation and execve.
            executable_fd = os.open(binary, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                with os.fdopen(os.dup(executable_fd), "rb") as checked:
                    if hashlib.sha256(checked.read()).hexdigest() != binary_hash:
                        raise OpenLinuxError("opened executable differs from manifest")
                with (root / "samples.jsonl").open("wb") as out, (root / "stderr.txt").open("wb") as err:
                    proc = subprocess.Popen(argv, executable=f"/proc/self/fd/{executable_fd}",
                                            pass_fds=(executable_fd,), stdout=out, stderr=err,
                                            cwd=root, start_new_session=True)
            finally:
                os.close(executable_fd)
            self._children[proc.pid] = proc
            observed = _process(proc.pid)
            if not observed:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                raise OpenLinuxError("could not observe newly started runtime process")
            record = {"schema": RUN_SCHEMA, "run_id": run_id, "pid": proc.pid,
                      "start_ticks": observed["start_ticks"], "argv": argv,
                      "program_hash": build["program_hash"], "source_sha256": build["source_sha256"],
                      "binary_sha256": binary_hash, "build_hash": build["build_hash"],
                      "duration_s": duration_s, "sample_interval_ms": sample_interval_ms,
                      "manifest_path": str(Path(manifest_path).resolve()),
                      "created_at": datetime.now(timezone.utc).isoformat(),
                      "started_monotonic": time.monotonic(), "hard_deadline_grace_s": 1.0}
            record["record_hash"] = _digest(record)
            try:
                _write_json(root / "run.json", record)
            except BaseException:
                proc.terminate()
                proc.wait(timeout=3)
                raise
        # Wait only for first actual application sample, not its full duration.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            current = self.monitor(run_id)
            if current.get("sample"):
                return {"ok": current["ok"], "status": current["status"], "run_id": run_id,
                        "state_path": str(root / "run.json"), "program_hash": build["program_hash"],
                        "binary_sha256": binary_hash, "sample": current["sample"]}
            if not current.get("process_alive"):
                return current
            time.sleep(.01)
        return {"ok": False, "status": "unverified", "run_id": run_id, "error": "no runtime sample within startup timeout"}

    def _run_record(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise OpenLinuxError("run_id must be 32 lowercase hex characters")
        root = self.state_dir / "runs" / run_id
        record = _read_json(root / "run.json")
        if record.get("schema") != RUN_SCHEMA or record.get("run_id") != run_id or record.get("record_hash") != _digest({k: v for k, v in record.items() if k != "record_hash"}):
            raise OpenLinuxError("run record integrity mismatch")
        return root, record

    def monitor(self, run_id):
        """Return last complete sample, bound to source, binary, program and run."""
        root, record = self._run_record(run_id)
        child = self._children.get(record["pid"])
        exit_code = child.poll() if child else None
        process = _process(record["pid"])
        match = bool(process and process["start_ticks"] == record["start_ticks"] and process["argv"] == record["argv"] and process["state"] != "Z")
        if match:
            try:
                match = _sha(Path(f"/proc/{record['pid']}/exe")) == record["binary_sha256"]
            except OSError:
                match = False
        sample = None
        try:
            with (root / "samples.jsonl").open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 32768))
                lines = handle.read().splitlines(keepends=True)
            for line in reversed(lines):
                if not line.endswith(b"\n"):
                    continue
                try:
                    candidate = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if any(candidate.get(key) != record[key] for key in ("run_id", "program_hash", "source_sha256", "binary_sha256")):
                    raise OpenLinuxError("runtime sample identity mismatch")
                sample = candidate
                break
        except FileNotFoundError:
            pass
        terminal = sample and sample.get("state") in ("stopped", "completed")
        status = sample["state"] if terminal and not match else ("running" if match and sample else "starting" if match else "exited-unverified")
        if not match and not terminal and time.monotonic() - record["started_monotonic"] >= record["duration_s"]:
            status = "faulted"
        bundle_integrity, integrity_error = "verified", None
        try:
            current_build, current_binary = self._verify(record["manifest_path"])
            if current_build["build_hash"] != record["build_hash"] or _sha(current_binary) != record["binary_sha256"]:
                raise OpenLinuxError("deployed bundle differs from the run record")
        except (OpenLinuxError, OSError) as exc:
            bundle_integrity, integrity_error = "changed-or-unavailable", str(exc)
        sample_age_s = (max(0.0, time.monotonic() - record["started_monotonic"] - sample.get("elapsed_ns", 0) / 1e9)
                        if sample else None)
        stale = bool(match and sample and not terminal and sample_age_s > max(.5, 3 * record["sample_interval_ms"] / 1000))
        if stale:
            status = "stale-unverified"
        if bundle_integrity != "verified":
            status = "bundle-changed-unverified"
        return {"ok": bool(sample) and (match or bool(terminal)) and not stale and bundle_integrity == "verified", "status": status,
                "sample_age_s": sample_age_s,
                "bundle_integrity": bundle_integrity, "integrity_error": integrity_error,
                "exit_code": exit_code,
                "run_id": run_id, "process_alive": match,
                "program_hash": record["program_hash"], "source_sha256": record["source_sha256"],
                "binary_sha256": record["binary_sha256"], "sample": sample,
                "quality": {"program_identity": ("verified" if bundle_integrity == "verified" else "historical-sample-only") if sample else "unverified",
                            "bundle_integrity": bundle_integrity,
                            "freshness": "terminal-historical" if terminal else "stale" if stale else "current" if match and sample else "unverified",
                            "execution": "actual-matiec-generated-code" if sample else "unverified",
                            "timing": "observed-local-linux-not-hard-realtime", "physical_io": "not-provided"},
                "samples_path": str(root / "samples.jsonl")}

    def stop(self, run_id):
        """Stop only the process whose starttime, argv and executable still match."""
        with self._lock():
            root, record = self._run_record(run_id)
            current = self.monitor(run_id)
            if not current["process_alive"]:
                if current["status"] in ("stopped", "completed"):
                    return {**current, "already_exited": True}
                return {**current, "ok": False, "status": "identity-mismatch-or-exited",
                        "error": "no signal sent: process identity is not verified"}
            # pidfd ties the signal to this process, closing PID reuse between
            # /proc validation and signal delivery (Linux/Python support required).
            if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
                raise OpenLinuxError("verified stop requires Linux pidfd support")
            try:
                fd = os.pidfd_open(record["pid"])
            except ProcessLookupError:
                return self.monitor(run_id)
            try:
                again = _process(record["pid"])
                if not again or again["start_ticks"] != record["start_ticks"] or again["argv"] != record["argv"]:
                    raise OpenLinuxError("process identity changed before stop; no signal sent")
                signal.pidfd_send_signal(fd, signal.SIGTERM)
            finally:
                os.close(fd)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = self.monitor(run_id)
            if not current["process_alive"]:
                return current
            time.sleep(.02)
        return {**self.monitor(run_id), "ok": False, "status": "stop-unverified"}
