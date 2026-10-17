"""Pinned MatIEC -> C -> real local Linux scan runtime.

Both profiles use one MAIN/Config0/Res0/scan/instance0: legacy counter-v1 and
explicit typed scalar signals-v1. Real-time local scans and deterministic input
replay execute generated code. No physical I/O or industrial timing is claimed.
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

from plc_idf.open_contract import validate_signals, validate_value, INTEGER_RANGES, structural_st_text

MATIEC_COMMIT = "3a41303bcb4e50403417bc39c63f88981d7d961e"
PROFILE = "open-linux-counter-v1"
SIGNALS_PROFILE = "open-linux-signals-v1"
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


def _json_loads(raw):
    def reject_constant(token):
        raise ValueError("non-finite JSON token: " + token)
    return json.loads(raw, parse_constant=reject_constant)


def _read_json(path):
    try:
        value = _json_loads(Path(path).read_text())
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


def _profile(source, cycle_ms, signals=None):
    if type(cycle_ms) is not int or not 1 <= cycle_ms <= 1000:
        raise OpenLinuxError("cycle_ms must be an integer in 1..1000")
    if not source or "\0" in source:
        raise OpenLinuxError("source must be nonempty UTF-8 ST without NUL")
    clean = structural_st_text(source)
    # This profile deliberately has a single, fully hashed source. Do not permit
    # generated C pragmas, includes, or externally linked POUs to hide inputs.
    if any(char in clean for char in "{}") or re.search(r"(?m)^\s*#|#\s*(?:include|pragma)|\b(?:VAR_EXTERNAL|EXTERNAL)\b", clean, re.I):
        raise OpenLinuxError("external source references and inline C/pragmas are unsupported")
    patterns = [r"\bPROGRAM\s+MAIN\b", r"\bCONFIGURATION\s+Config0\b",
                r"\bRESOURCE\s+Res0\s+ON\s+PLC\b",
                r"\bPROGRAM\s+instance0\s+WITH\s+scan\s*:\s*MAIN\s*;"]
    if signals is None:
        patterns.extend([r"\bCOUNTER\s*:\s*DINT\b", r"\bHEARTBEAT\s*:\s*BOOL\b"])
    else:
        validate_signals(signals)
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
#include <math.h>
#include <float.h>
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
static const char *build_identity = NULL;
@INPUT_DECLARATIONS@
static void request_stop(int sig) { (void)sig; stopping = 1; }
static uint64_t monotonic_ns(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}
static struct timespec as_timespec(uint64_t ns) {
    struct timespec ts = { (time_t)(ns / 1000000000ULL), (long)(ns % 1000000000ULL) };
    return ts;
}
static int print_real(double value) {
    if (!isfinite(value)) { printf("null"); return 0; }
    printf("%.17g", value); return 1;
}
static void input_init(void) {
@INPUT_INITIALIZATION@
}
static void input_apply(void) {
@INPUT_ASSIGNMENTS@
}
static int read_inputs(char *line) {
    char *save = NULL;
    char *token = strtok_r(line, " \t\r\n", &save);
@INPUT_PARSING@
    return token == NULL;
}
static void emit(const char *run_id, const char *binary_hash, const char *state,
                 unsigned long scans, uint64_t elapsed, uint64_t max_late) {
    printf("{\"schema_version\":2,\"run_id\":\"%s\",\"program_hash\":\"@PROGRAM_HASH@\","
           "\"source_sha256\":\"@SOURCE_HASH@\",\"binary_sha256\":\"%s\","
           "\"build_hash\":\"%s\",\"profile\":\"@PROFILE@\","
           "\"runtime\":\"matiec-local-scan-v2\",\"state\":\"%s\",\"scan_count\":%lu,"
           "\"elapsed_ns\":%llu,\"cycle_ns\":%llu,\"max_observed_lateness_ns\":%llu,",
           run_id, binary_hash, build_identity, state, scans,
           (unsigned long long)elapsed, common_ticktime__, (unsigned long long)max_late);
@LEGACY_FIELDS@
    int valid = 1;
    printf("\"values\":{");
@EMIT_VALUES@
    printf("},\"values_valid\":%s}\n", valid ? "true" : "false");
    fflush(stdout);
}
int main(int argc, char **argv) {
    const int replay = argc == 7 && strcmp(argv[3], "--replay") == 0;
    if ((!replay && argc != 6) || strlen(argv[1]) != 32 || strlen(argv[2]) != 64) return 2;
    double duration = strtod(argv[replay ? 5 : 3], NULL);
    double sample_ms = replay ? 1 : strtod(argv[4], NULL);
    build_identity = argv[replay ? 6 : 5];
    if (strlen(build_identity) != 64 || !(duration > 0 && duration <= (replay ? 30 : 3600)
                                         && sample_ms >= 1 && sample_ms <= 10000)) return 2;
    unsigned long frame_count = replay ? strtoul(argv[4], NULL, 10) : 0;
    if (replay && (frame_count == 0 || frame_count > 10000)) return 2;
    // The kernel deadline also bounds ST that never returns from a scan.
    timer_t deadline_timer;
    struct sigevent event = {0};
    event.sigev_notify = SIGEV_SIGNAL; event.sigev_signo = SIGKILL;
    if (timer_create(CLOCK_MONOTONIC, &event, &deadline_timer)) return 5;
    struct itimerspec deadline_spec = {0};
    deadline_spec.it_value = as_timespec((uint64_t)((duration + 1.0) * 1000000000.0));
    if (timer_settime(deadline_timer, 0, &deadline_spec, NULL)) return 5;
    signal(SIGTERM, request_stop); signal(SIGINT, request_stop);
    setvbuf(stdout, NULL, _IOLBF, 0);
    config_init__(); input_init();
    if (!common_ticktime__) return 3;
    if (replay) {
        char frame[8192];
        for (unsigned long tick = 0; tick < frame_count; ++tick) {
            if (!fgets(frame, sizeof(frame), stdin) || !strchr(frame, '\n') || !read_inputs(frame)) return 6;
            uint64_t iec_ns = (uint64_t)tick * common_ticktime__;
            __CURRENT_TIME.tv_sec = (long)(iec_ns / 1000000000ULL);
            __CURRENT_TIME.tv_nsec = (long)(iec_ns % 1000000000ULL);
            input_apply(); config_run__(tick);
            emit(argv[1], argv[2], tick + 1 == frame_count ? "completed" : "replaying", tick + 1, iec_ns, 0);
        }
        return 0;
    }
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
        input_apply(); config_run__(scans);
        ++scans;
        if (now >= next_sample) {
            emit(argv[1], argv[2], "running", scans, now - start, max_late);
            next_sample = now + sample_ns;
        }
        next += common_ticktime__;
        if (next < now) next = now + common_ticktime__;
    }
    emit(argv[1], argv[2], stopping ? "stopped" : "completed", scans,
         monotonic_ns() - start, max_late);
    return 0;
}
'''


def _runtime_metadata(manifest):
    if "runtime" not in manifest:
        if manifest.get("profile") == SIGNALS_PROFILE:
            raise OpenLinuxError("typed signals require runtime sample schema 2")
        return {"sample_schema": 1, "replay": False}
    runtime = manifest["runtime"]
    if (not isinstance(runtime, dict) or type(runtime.get("sample_schema")) is not int
            or runtime["sample_schema"] != 2 or runtime.get("replay") is not True
            or runtime.get("input_protocol") != "plcidf.scalar-frames.v1"):
        raise OpenLinuxError("unsupported or malformed runtime metadata")
    return runtime


def _signals_for(profile, signals=None):
    if profile == PROFILE:
        return {"inputs": {}, "outputs": {"COUNTER": "DINT", "HEARTBEAT": "BOOL"}}
    return validate_signals(signals)


def _main_fields(header):
    """Read only direct fields of MatIEC's generated MAIN program structure."""
    marker = re.search(r"__DECLARE_PROGRAM_TYPE\s*\(\s*MAIN\s*,", header)
    if not marker:
        raise OpenLinuxError("generated C has no direct MAIN program structure")
    depth, cursor = 1, marker.end()
    begin = cursor
    while cursor < len(header) and depth:
        depth += (header[cursor] == "(") - (header[cursor] == ")")
        cursor += 1
    if depth:
        raise OpenLinuxError("generated MAIN structure is incomplete")
    return {name.upper(): kind.upper() for kind, name in re.findall(
        r"__DECLARE_VAR\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)", header[begin:cursor - 1])}


def _check_fields(header, profile, signals):
    fields = _main_fields(header)
    contract = _signals_for(profile, signals)
    for direction in ("inputs", "outputs"):
        for name, kind in contract[direction].items():
            if fields.get(name.upper()) != kind:
                raise OpenLinuxError(f"signal {name}:{kind} must name a direct MAIN field with the same type")


def _render_runner(profile, signals=None):
    contract = _signals_for(profile, signals)
    declarations, initialization, assignments, parsing, emission = [], [], [], [], []
    for name, kind in contract["inputs"].items():
        member, image = "RES0__INSTANCE0." + name.upper() + ".value", "image_" + name.upper()
        declarations.append(f"static IEC_{kind} {image};")
        initialization.append(f"    {image} = {member};")
        assignments.append(f"    {member} = {image};")
        parsing.append('    if (!token) return 0;\n    if (strcmp(token, "-") != 0) {')
        if kind == "BOOL":
            parsing.append(f'        if (strcmp(token, "0") && strcmp(token, "1")) return 0;\n        {image} = token[0] == \'1\';')
        elif kind == "REAL":
            parsing.append(f'        char *end = NULL;\n        double value = strtod(token, &end);\n        if (!end || *end || !isfinite(value) || fabs(value) > FLT_MAX) return 0;\n        {image} = (IEC_REAL)value;')
        else:
            minimum, maximum = INTEGER_RANGES[kind]
            parsing.append(f'        char *end = NULL; errno = 0;\n        long long value = strtoll(token, &end, 10);\n        if (errno || !end || *end || value < {minimum}LL || value > {maximum}LL) return 0;\n        {image} = (IEC_{kind})value;')
        parsing.append('    }\n    token = strtok_r(NULL, " \\t\\r\\n", &save);')
    if not contract["inputs"]:
        parsing.append('    if (!token || strcmp(token, "-")) return 0;\n    token = strtok_r(NULL, " \\t\\r\\n", &save);')
    all_values = {**contract["inputs"], **contract["outputs"]}
    for index, (name, kind) in enumerate(all_values.items()):
        member = "RES0__INSTANCE0." + name.upper() + ".value"
        prefix = ("," if index else "") + json.dumps(name) + ":"
        emission.append(f"    printf({json.dumps(prefix)});")
        if kind == "BOOL":
            emission.append(f'    printf("%s", {member} ? "true" : "false");')
        elif kind == "REAL":
            emission.append(f"    valid &= print_real((double){member});")
        elif kind in ("UINT", "UDINT"):
            emission.append(f'    printf("%lu", (unsigned long){member});')
        else:
            emission.append(f'    printf("%ld", (long){member});')
    replacements = {
        "@INPUT_DECLARATIONS@": "\n".join(declarations),
        "@INPUT_INITIALIZATION@": "\n".join(initialization),
        "@INPUT_ASSIGNMENTS@": "\n".join(assignments),
        "@INPUT_PARSING@": "\n".join(parsing), "@EMIT_VALUES@": "\n".join(emission),
        "@PROFILE@": profile,
        "@LEGACY_FIELDS@": ('    printf("\\\"counter\\\":%d,\\\"heartbeat\\\":%s,", (int)RES0__INSTANCE0.COUNTER.value, RES0__INSTANCE0.HEARTBEAT.value ? "true" : "false");' if profile == PROFILE else ""),
    }
    runner = RUNNER_C
    for old, new in replacements.items():
        runner = runner.replace(old, new)
    return runner


def _validate_sample(sample, record, *, scan_count=None):
    if not isinstance(sample, dict):
        raise OpenLinuxError("runtime sample must be an object")
    identity_keys = ["run_id", "program_hash", "source_sha256", "binary_sha256"]
    if record.get("sample_schema", 1) >= 2:
        identity_keys.append("build_hash")
    if any(sample.get(key) != record[key] for key in identity_keys):
        raise OpenLinuxError("runtime sample identity mismatch")
    if type(sample.get("scan_count")) is not int or sample["scan_count"] < 1:
        raise OpenLinuxError("runtime scan count is invalid")
    if scan_count is not None and sample["scan_count"] != scan_count:
        raise OpenLinuxError("replay samples must contain exactly one consecutive scan per frame")
    if type(sample.get("elapsed_ns")) is not int or sample["elapsed_ns"] < 0 or type(sample.get("cycle_ns")) is not int or sample["cycle_ns"] <= 0:
        raise OpenLinuxError("runtime sample timing fields are invalid")
    if type(sample.get("max_observed_lateness_ns")) is not int or sample["max_observed_lateness_ns"] < 0:
        raise OpenLinuxError("runtime observed lateness is invalid")
    supported_states = ("replaying", "completed") if record.get("schema") == "plcidf.open-linux.replay.v1" else ("running", "stopped", "completed")
    if sample.get("state") not in supported_states:
        raise OpenLinuxError("runtime sample state is unsupported")
    if "cycle_ms" in record and sample["cycle_ns"] != record["cycle_ms"] * 1000000:
        raise OpenLinuxError("runtime cycle differs from its bound task")
    if record.get("sample_schema", 1) >= 2:
        if sample.get("schema_version") != 2 or sample.get("profile") != record.get("profile"):
            raise OpenLinuxError("runtime sample schema/profile mismatch")
        if sample.get("values_valid") is not True:
            raise OpenLinuxError("runtime produced an invalid/non-finite signal value")
        contract = _signals_for(record.get("profile", PROFILE), record.get("signals"))
        values = sample.get("values")
        expected = {**contract["inputs"], **contract["outputs"]}
        if not isinstance(values, dict) or set(values) != set(expected):
            raise OpenLinuxError("runtime values do not match the signal contract")
        for name, kind in expected.items():
            validate_value(values[name], kind)
    return sample


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

    def build(self, source, build_dir, cycle_ms=20, signals=None):
        """Compile a complete ST file into a fresh immutable-by-hash bundle."""
        installed = self.install()
        if not installed["ok"]:
            raise OpenLinuxError(installed["error"])
        normalized_signals = validate_signals(signals) if signals is not None else None
        profile = SIGNALS_PROFILE if normalized_signals is not None else PROFILE
        text = Path(source).read_text(encoding="utf-8-sig")
        _profile(text, cycle_ms, normalized_signals)
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
        _check_fields(generated_names["pous.h"].read_text(), profile, normalized_signals)
        runner_template = _render_runner(profile, normalized_signals)
        runner_hash = hashlib.sha256(runner_template.encode()).hexdigest()
        gcc_version = subprocess.run(["gcc", "--version"], capture_output=True, text=True, check=True).stdout.splitlines()[0]
        program_hash = _digest({"source_sha256": source_hash, "profile": profile, "signals": normalized_signals, "cycle_ms": cycle_ms,
                                "toolchain_hash": installed["toolchain_hash"], "runner_sha256": runner_hash,
                                "gcc_version": gcc_version, "machine": platform.machine()})
        runner = runner_template.replace("@PROGRAM_HASH@", program_hash).replace("@SOURCE_HASH@", source_hash)
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
        manifest = {"schema": BUILD_SCHEMA, "profile": profile, "status": "verified",
                    "created_at": datetime.now(timezone.utc).isoformat(), "program_hash": program_hash,
                    "source_sha256": source_hash, "source": "source.st", "executable": "plc_runtime",
                    "target": {"os": "linux", "architecture": platform.machine(), "physical_io": False},
                    "cycle_ms": cycle_ms, "toolchain": installed, "gcc_version": gcc_version,
                    "commands": [command, command_c], "artifacts": artifacts,
                    "observation": {"program": "MAIN", "instance": "RES0__INSTANCE0", "counter_type": "DINT", "heartbeat_type": "BOOL"}}
        manifest["runtime"] = {"sample_schema": 2, "replay": True, "input_protocol": "plcidf.scalar-frames.v1"}
        if normalized_signals is not None:
            manifest["signals"] = normalized_signals
            manifest["observation"] = {"program": "MAIN", "instance": "RES0__INSTANCE0"}
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
        if manifest.get("schema") != BUILD_SCHEMA or manifest.get("profile") not in (PROFILE, SIGNALS_PROFILE):
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
        contract = None
        if manifest["profile"] == SIGNALS_PROFILE:
            contract = validate_signals(manifest.get("signals"))
            if contract != manifest.get("signals"):
                raise OpenLinuxError("manifest signals must use their normalized mapping")
        elif "signals" in manifest:
            raise OpenLinuxError("legacy counter profile cannot contain a signal mapping")
        _profile((path.parent / "source.st").read_text(), manifest.get("cycle_ms"), contract)
        if _runtime_metadata(manifest)["sample_schema"] >= 2:
            if "generated/POUS.h" not in paths:
                raise OpenLinuxError("typed runtime requires its generated MAIN type metadata")
            _check_fields((path.parent / "generated/POUS.h").read_text(), manifest["profile"], contract)
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
        sample_schema = _runtime_metadata(build)["sample_schema"]
        if sample_schema >= 2:
            argv.append(build["build_hash"])
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
                      "sample_schema": sample_schema, "profile": build["profile"], "signals": build.get("signals"),
                      "cycle_ms": build["cycle_ms"],
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
            if current.get("sample_error"):
                return current
            if current.get("sample"):
                return {"ok": current["ok"], "status": current["status"], "run_id": run_id,
                        "state_path": str(root / "run.json"), "program_hash": build["program_hash"],
                        "binary_sha256": binary_hash, "source_sha256": build["source_sha256"],
                        "build_hash": build["build_hash"], "sample": current["sample"]}
            if not current.get("process_alive"):
                return current
            time.sleep(.01)
        return {"ok": False, "status": "unverified", "run_id": run_id, "error": "no runtime sample within startup timeout"}

    def replay(self, release_manifest, frames, timeout_s=5):
        """Execute one deterministic IEC scan per frame in a fresh deployed ELF.

        The first scan uses IEC time 0; following scans add exactly cycle_ms.
        Inputs omitted by a frame retain their previous input image, initially
        read from ST initialization. This does not drive any physical I/O.
        """
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not math.isfinite(timeout_s) or not 0 < timeout_s <= 30):
            raise OpenLinuxError("replay timeout_s must be finite and in (0, 30]")
        if not isinstance(frames, list) or not 1 <= len(frames) <= 10000:
            raise OpenLinuxError("replay requires 1..10000 input frames")
        release_path = Path(release_manifest).expanduser().absolute()
        release = _read_json(release_path)
        if release.get("schema") != RELEASE_SCHEMA:
            raise OpenLinuxError("replay requires a deployed release.json; deploy the build first")
        build, binary = self._verify(release_path)
        expected = self.state_dir / "releases" / build["build_hash"] / "release.json"
        if release_path != expected or release_path.resolve() != expected:
            raise OpenLinuxError("replay requires a release installed in this backend state_dir")
        runtime = _runtime_metadata(build)
        if runtime.get("sample_schema") != 2 or runtime.get("replay") is not True:
            raise OpenLinuxError("this older runtime has no deterministic replay; rebuild it first")
        contract = _signals_for(build["profile"], build.get("signals"))
        input_names = {name.casefold(): name for name in contract["inputs"]}
        normalized_frames, protocol_lines = [], []
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                raise OpenLinuxError(f"frame {index + 1} must be an input mapping")
            normalized = {}
            for key, value in frame.items():
                if not isinstance(key, str) or key.casefold() not in input_names:
                    raise OpenLinuxError(f"frame {index + 1} writes an unknown/non-input signal: {key!r}")
                name = input_names[key.casefold()]
                if name in normalized:
                    raise OpenLinuxError(f"frame {index + 1} repeats an IEC input name: {key}")
                normalized[name] = validate_value(value, contract["inputs"][name])
            normalized_frames.append(normalized)
            tokens = []
            for name, kind in contract["inputs"].items():
                value = normalized.get(name)
                if name not in normalized:
                    tokens.append("-")
                elif kind == "BOOL":
                    tokens.append("1" if value else "0")
                else:
                    tokens.append(str(value))
            protocol_lines.append(" ".join(tokens) if tokens else "-")
        run_id = uuid.uuid4().hex
        root = self.state_dir / "replays" / run_id
        root.mkdir(parents=True, exist_ok=False)
        input_path, samples_path = root / "frames.txt", root / "samples.jsonl"
        input_path.write_text("\n".join(protocol_lines) + "\n")
        _write_json(root / "frames.json", {"frames": normalized_frames})
        binary_hash = next(item["sha256"] for item in build["artifacts"] if item["path"] == "plc_runtime")
        record = {"schema": "plcidf.open-linux.replay.v1", "run_id": run_id,
                  "program_hash": build["program_hash"], "build_hash": build["build_hash"],
                  "source_sha256": build["source_sha256"], "binary_sha256": binary_hash,
                  "profile": build["profile"], "signals": build.get("signals"), "sample_schema": 2,
                  "frame_count": len(frames), "cycle_ms": build["cycle_ms"],
                  "frames_sha256": _digest(normalized_frames), "timeout_s": timeout_s,
                  "release_manifest": str(release_path), "created_at": datetime.now(timezone.utc).isoformat()}
        record["record_hash"] = _digest(record)
        _write_json(root / "replay.json", record)
        argv = [str(binary), run_id, binary_hash, "--replay", str(len(frames)),
                str(float(timeout_s)), build["build_hash"]]
        started, timed_out = time.monotonic(), False
        with self._lock():
            executable_fd = os.open(binary, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                with os.fdopen(os.dup(executable_fd), "rb") as checked:
                    if hashlib.sha256(checked.read()).hexdigest() != binary_hash:
                        raise OpenLinuxError("opened executable differs from replay manifest")
                with input_path.open("rb") as inputs, samples_path.open("wb") as out, (root / "stderr.txt").open("wb") as err:
                    proc = subprocess.Popen(argv, executable=f"/proc/self/fd/{executable_fd}",
                                            pass_fds=(executable_fd,), stdin=inputs, stdout=out,
                                            stderr=err, cwd=root, start_new_session=True)
            finally:
                os.close(executable_fd)
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=3)
        except BaseException:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=3)
            raise
        samples, errors = [], []
        if samples_path.stat().st_size > 128 * 1024 * 1024:
            errors.append("replay evidence exceeds the 128 MiB parsing limit")
            raw = b""
        else:
            raw = samples_path.read_bytes()
        for index, line in enumerate(raw.splitlines(keepends=True), 1):
            try:
                if not line.endswith(b"\n"):
                    raise OpenLinuxError("replay output contains an incomplete sample")
                sample = _json_loads(line)
                _validate_sample(sample, record, scan_count=index)
                if sample.get("cycle_ns") != build["cycle_ms"] * 1000000 or sample.get("elapsed_ns") != (index - 1) * build["cycle_ms"] * 1000000:
                    raise OpenLinuxError("replay sample IEC time differs from its deterministic scan")
                samples.append(sample)
            except (OpenLinuxError, ValueError, UnicodeError) as exc:
                errors.append(f"sample {index}: {exc}")
                break
        try:
            current_build, current_binary = self._verify(release_path)
            if current_build["build_hash"] != build["build_hash"] or _sha(current_binary) != binary_hash:
                raise OpenLinuxError("deployed release changed during replay")
        except (OpenLinuxError, OSError) as exc:
            errors.append(str(exc))
        complete = (not timed_out and proc.returncode == 0 and not errors and len(samples) == len(frames)
                    and samples[-1].get("state") == "completed")
        status = "completed" if complete else "timeout" if timed_out else "failed"
        result = {"ok": complete, "status": status, "run_id": run_id,
                  "program_hash": build["program_hash"], "build_hash": build["build_hash"],
                  "source_sha256": build["source_sha256"], "binary_sha256": binary_hash,
                  "samples": samples, "samples_path": str(samples_path),
                  "frames_path": str(root / "frames.json"), "record_path": str(root / "replay.json"),
                  "frame_count": len(frames), "returncode": proc.returncode,
                  "wall_duration_s": time.monotonic() - started, "errors": errors,
                  "quality": {"execution": "actual-matiec-generated-code", "physical_io": "not-provided",
                              "timebase": "deterministic-one-frame-one-IEC-scan", "program_identity": "verified" if complete else "unverified"}}
        _write_json(root / "result.json", result)
        return result

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
        sample, sample_error = None, None
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
                    candidate = _json_loads(line)
                except (ValueError, UnicodeError) as exc:
                    sample_error = str(exc)
                    break
                try:
                    sample = _validate_sample(candidate, record)
                except ValueError as exc:
                    sample, sample_error = candidate if isinstance(candidate, dict) else None, str(exc)
                break
        except FileNotFoundError:
            pass
        terminal = sample and not sample_error and sample.get("state") in ("stopped", "completed")
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
                        if sample and not sample_error else None)
        stale = bool(match and sample and sample_age_s is not None and not terminal and sample_age_s > max(.5, 3 * record["sample_interval_ms"] / 1000))
        if stale:
            status = "stale-unverified"
        if sample_error:
            status = "sample-unverified"
        if bundle_integrity != "verified":
            status = "bundle-changed-unverified"
        return {"ok": bool(sample) and not sample_error and (match or bool(terminal)) and not stale and bundle_integrity == "verified", "status": status,
                "sample_error": sample_error,
                "sample_age_s": sample_age_s,
                "bundle_integrity": bundle_integrity, "integrity_error": integrity_error,
                "exit_code": exit_code,
                "run_id": run_id, "process_alive": match,
                "program_hash": record["program_hash"], "source_sha256": record["source_sha256"],
                "binary_sha256": record["binary_sha256"], "build_hash": record["build_hash"], "sample": sample,
                "quality": {"program_identity": ("verified" if bundle_integrity == "verified" else "historical-sample-only") if sample and not sample_error else "unverified",
                            "signal_values": "verified" if sample and not sample_error else "unverified",
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
