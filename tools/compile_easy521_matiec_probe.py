"""Real MatIEC ST -> C -> native Clang ARM probe for one existing REAL global.

Input is an ST statement body; actual parsing/type checking/code generation is
performed by the pinned MatIEC iec2c, never a handwritten ST regex/LLVM emitter.
A restricted generated-C interface check permits stateless REAL assignments,
IF/ELSE and ordinary expressions for g_rBESS_Pcmd only. The wrapper exports
vendor-shaped void MAIN(void), using the external G_RBESS_PCMD symbol. Separate
GVT/CSV binding and full application packaging remain required.

No timers, persistent locals, function blocks, forced variables, physical I/O,
vendor arithmetic equivalence, initialization/retention or task period support
is claimed. MatIEC's external pointer/flags are initialized per invocation;
the existing PLC REAL is never initialized by the wrapper. No device/Windows
access exists. ARM ABI properties are structural checks, not runtime acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess


def _adjacent(name):
    spec = importlib.util.spec_from_file_location("_matiec_probe_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _adjacent("compile_easy521_native_probe")
MATIEC_COMMIT = "3a41303bcb4e50403417bc39c63f88981d7d961e"
PROFILE = "matiec-existing-pcmd-stateless-real-v1"
_HEADER = '''#ifdef DISABLE_EN_ENO_PARAMETERS
#include "iec_std_FB_no_ENENO.h"
#else
#include "iec_std_FB.h"
#endif
__DECLARE_PROGRAM_TYPE(PLCIDF_MAIN,
__DECLARE_EXTERNAL(REAL,G_RBESS_PCMD)
)
'''
_SIGNATURE = "static void PLCIDF_MAIN_body__(PLCIDF_MAIN_data__ *data__) {"
WRAPPER = r'''/* MatIEC adapter: existing REAL allocation and caller scheduling only.
 * Per-call initialization binds pointers/flags; it does not reset PLC memory.
 * This stateless profile has no local retention, force, timer or I/O contract.
 * ST behavior comes exclusively from the unmodified generated POUS.c below. */
#include "iec_std_lib.h"
#include "accessor.h"
extern volatile IEC_REAL G_RBESS_PCMD;
static IEC_REAL *__GET_GLOBAL_G_RBESS_PCMD(void) {
    return (IEC_REAL *)&G_RBESS_PCMD;
}
static IEC_BYTE __IS_GLOBAL_G_RBESS_PCMD_FORCED(void) { return 0; }
/* The single permitted external is REAL. Preserve externally observable
 * accesses while retaining MatIEC's force-flag tests (global force unsupported).
 * These accessor adapters do not translate the generated ST expressions. */
#undef __GET_EXTERNAL
#define __GET_EXTERNAL(name, ...) ((*(volatile IEC_REAL *)(name.value)) __VA_ARGS__)
#undef __SET_EXTERNAL
#define __SET_EXTERNAL(prefix,name,suffix,new_value) \
    { if (!(prefix name.flags & __IEC_FORCE_FLAG || __IS_GLOBAL_##name##_FORCED())) \
        (*(volatile IEC_REAL *)(prefix name.value)) suffix = new_value; }
#include "POUS.h"
#include "POUS.c"
void MAIN(void) {
    PLCIDF_MAIN_data__ data = {0};
    PLCIDF_MAIN_init__(&data, 0);
    PLCIDF_MAIN_body__(&data);
}
'''


def validate_generated(header, generated):
    """Validate pinned MatIEC's C interface/body subset; never translate ST.

    Reject extra PROGRAM/FUNCTION declarations, locals/FB instances, helper
    calls and unsupported control forms before ARM compilation. MatIEC remains
    responsible for ST syntax, names and types. Accepted C is compiled unchanged.
    """
    if "".join(header.split()) != "".join(_HEADER.split()):
        raise ValueError("Generated program must expose exactly one external REAL G_RBESS_PCMD")
    if generated.count(_SIGNATURE) != 1:
        raise ValueError("Expected one actual MatIEC program body")
    body = generated.split(_SIGNATURE, 1)[1].split("} // PLCIDF_MAIN_body__()", 1)[0]
    body = re.sub(r"//[^\n]*|/\*.*?\*/", " ", body, flags=re.S)
    allowed = {"if", "else", "goto", "return", "__end", "data__", "G_RBESS_PCMD",
               "__GET_EXTERNAL", "__SET_EXTERNAL", "__REAL_LITERAL"}
    tokens = re.compile(r"\s+|[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?|[A-Za-z_][A-Za-z_0-9]*|->|<=|>=|==|!=|&&|\|\||[{}(),;:+*/<>!\-]")
    offset = 0
    while offset < len(body):
        match = tokens.match(body, offset)
        if match is None:
            raise ValueError("Generated C contains an unsupported expression token")
        token = match[0]
        if re.match(r"[A-Za-z_]", token) and token not in allowed:
            raise ValueError("Generated C helper/state/control is outside the stateless REAL profile: " + token)
        offset = match.end()
    if "__SET_EXTERNAL" not in body:
        raise ValueError("Expected an actual assignment to the fixed external REAL")
    return {"generated_body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "generated_c_modified": False, "external_symbol": "G_RBESS_PCMD",
            "local_state": False, "force_supported": False, "task_period_ms": None}


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _run(argv, *, cwd, timeout_s, runner, report):
    result = runner(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, timeout=timeout_s, check=False)
    record = {"argv": list(argv), "returncode": result.returncode,
              "stdout": result.stdout, "stderr": result.stderr, "timeout_s": timeout_s}
    report["commands"].append(record)
    if result.returncode:
        raise ValueError(f"{report['stage']} failed with exit{result.returncode}")
    return record


def compile_matiec_probe(*, source, matiec_root, clang, output_dir,
                         readelf="/usr/bin/readelf", timeout_s=10.0, runner=subprocess.run):
    """Compile actual ST statements to a new audited Thumb MAIN.o directory.

    Parameters identify bounded source, pinned MatIEC installation, trusted
    native Linux clang/readelf and a new output directory. timeout_s (0.1..30)
    bounds every process; no retry occurs. Returns JSON-compatible report with
    input/generated/header/tool/object hashes and raw diagnostics. Success is
    restricted source compilation, not address binding or a deployable app.
    Writes only a new output directory; inputs remain unchanged and are rehashed.
    """
    report = {"schema": "plcidf.easy521-matiec-probe.v1", "profile": PROFILE,
              "ok": False, "status": "failed", "stage": "preflight", "commands": [],
              "device_access": False, "windows_access": False, "downloadable": False,
              "runtime_abi_verified": False, "arm_code_executed": False,
              "source_compiled": False, "diagnostics": []}
    output, created = Path(output_dir).absolute(), False
    try:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0.1 <= timeout_s <= 30:
            raise ValueError("timeout_s must be finite0.1..30 seconds")
        source = Path(source).absolute()
        root = Path(matiec_root).absolute()
        compilers = [Path(clang).resolve(strict=True), Path(readelf).resolve(strict=True)]
        body = base._read(source, base.MAX_SOURCE)
        text = body.decode("utf-8-sig")
        if "\0" in text or "{" in text or "}" in text:
            raise ValueError("ST source cannot contain NUL or implementation-specific pragmas")
        marker_path = root / "toolchain.json"
        marker_data = base._read(marker_path, 1024 * 1024)
        marker = json.loads(marker_data)
        if marker.get("schema") != "plcidf.open-linux.toolchain.v1" or marker.get("matiec_commit") != MATIEC_COMMIT:
            raise ValueError("Unsupported MatIEC schema/commit")
        iec2c = root / "bin/iec2c"
        snapshots = {source: body, marker_path: marker_data,
                     iec2c: base._read(iec2c, 32 * 1024 * 1024)}
        component_paths = (Path(__file__).absolute(), Path(base.__file__).absolute(),
                           Path(base._inspect.__file__).absolute())
        for path in component_paths:
            snapshots[path] = base._read(path, 1024 * 1024)
        if _digest(snapshots[iec2c]) != marker.get("iec2c_sha256"):
            raise ValueError("MatIEC compiler hash differs from installation manifest")
        for compiler in compilers:
            binary = base._read(compiler, 256 * 1024 * 1024)
            if binary[:7] != b"\x7fELF\x02\x01\x01":
                raise ValueError("Compiler/inspection tool must be trusted native Linux ELF64")
            snapshots[compiler] = binary
        libraries = marker.get("library_sha256", {})
        if not isinstance(libraries, dict) or not libraries:
            raise ValueError("MatIEC library hashes are required")
        for name, digest in libraries.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or not name.startswith("lib/"):
                raise ValueError("Invalid MatIEC library manifest path")
            data = base._read(root / name, 4 * 1024 * 1024)
            if _digest(data) != digest:
                raise ValueError("MatIEC library hash mismatch: " + name)
            snapshots[root / name] = data
        if (output.exists() or not output.parent.is_dir()
                or any(path.is_symlink() for path in (output, *output.parents))):
            raise ValueError("Output must be a new nonsymlink directory with existing parent")
        report.update(source_sha256=_digest(body), matiec_commit=MATIEC_COMMIT,
                      source_path=str(source), output_directory=str(output),
                      toolchain_manifest=marker,
                      toolchain_manifest_sha256=_digest(marker_data),
                      tool_hashes={str(path): _digest(snapshots[path]) for path in (iec2c, *compilers)},
                      compiler_component_sha256={str(path): _digest(snapshots[path]) for path in component_paths},
                      wrapper_sha256=_digest(WRAPPER.encode()))
        output.mkdir()
        created = True
        generated = output / "generated"
        generated.mkdir()
        frozen_root = output / "matiec"
        artifact_snapshots = {}
        for name in libraries:
            frozen = frozen_root / name
            frozen.parent.mkdir(parents=True, exist_ok=True)
            frozen.write_bytes(snapshots[root / name])
            frozen.chmod(0o444)
            artifact_snapshots[frozen] = snapshots[root / name]
        program = ("PROGRAM PLCIDF_MAIN\nVAR_EXTERNAL\n g_rBESS_Pcmd : REAL;\nEND_VAR\n" + text + "\nEND_PROGRAM\n").encode()
        (output / "body.st").write_bytes(body)
        (output / "source.st").write_bytes(program)
        (output / "wrapper.c").write_text(WRAPPER)
        for path in (output / "body.st", output / "source.st", output / "wrapper.c"):
            artifact_snapshots[path] = base._read(path, 1024 * 1024)
            path.chmod(0o444)
        report["stage"] = "matiec"
        _run([str(iec2c), "-I", str(frozen_root / "lib"), "-T", str(generated), str(output / "source.st")],
             cwd=output, timeout_s=timeout_s, runner=runner, report=report)
        header = base._read(generated / "POUS.h", 1024 * 1024).decode()
        generated_c = base._read(generated / "POUS.c", 1024 * 1024).decode()
        report["stage"] = "generated-interface"
        report["interface"] = validate_generated(header, generated_c)
        for path in generated.iterdir():
            if not path.is_file():
                raise ValueError("Unexpected generated directory")
            artifact_snapshots[path] = base._read(path, 1024 * 1024)
            path.chmod(0o444)
        report["stage"] = "clang-resource-directory"
        resource = _run([str(compilers[0]), "-print-resource-dir"], cwd=output,
                        timeout_s=timeout_s, runner=runner, report=report)["stdout"].strip()
        resource_headers = Path(resource) / "include"
        if not Path(resource).is_absolute() or not resource_headers.is_dir():
            raise ValueError("Clang resource header directory is unavailable")
        resource_snapshots = {}
        total = 0
        for path in sorted(resource_headers.rglob("*.h")):
            data = base._read(path, 4 * 1024 * 1024)
            total += len(data)
            if total > 32 * 1024 * 1024:
                raise ValueError("Clang resource headers exceed32MiB")
            resource_snapshots[path] = _digest(data)
        report["clang_resource_headers"] = {"directory": str(resource_headers), "sha256": {str(p): h for p, h in resource_snapshots.items()}}
        report["stage"] = "clang-arm"
        argv = [str(compilers[0]), "--target=armv7a-none-eabi", "-mthumb", "-mcpu=cortex-a8", "-mfpu=neon",
                "-mfloat-abi=softfp", "-ffixed-r9", "-ffreestanding", "-fno-builtin", "-fno-pic",
                "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-O2", "-std=c11",
                "-Werror=literal-range",
                "-MD", "-MF", str(output / "MAIN.d"), "-MT", "PLCIDF_MAIN",
                "-isystem", str(frozen_root / "lib/C"), "-I", str(generated), "-c", str(output / "wrapper.c"),
                "-o", str(output / "MAIN.o")]
        _run(argv, cwd=output, timeout_s=timeout_s, runner=runner, report=report)
        report["stage"] = "compilation-dependencies"
        # -MD includes compiler system headers as well as project headers. Each
        # observed input must have been snapshotted before compilation; an
        # unrecorded host libc/sysroot is rejected rather than hashed afterward.
        dependency_data = base._read(output / "MAIN.d", 1024 * 1024)
        dependency_text = dependency_data.decode().replace("\\\n", "")
        if not dependency_text.startswith("PLCIDF_MAIN:"):
            raise ValueError("Unexpected Clang dependency target")
        dependencies = []
        for name in dict.fromkeys(shlex.split(dependency_text[len("PLCIDF_MAIN:"):])):
            path = Path(name)
            if not path.is_absolute():
                path = output / path
            if path in artifact_snapshots:
                digest, origin = _digest(artifact_snapshots[path]), "frozen-generated-or-matiec"
            elif path in resource_snapshots:
                digest, origin = resource_snapshots[path], "clang-resource-header"
            else:
                raise ValueError("Compilation used an unsnapshotted dependency: " + str(path))
            dependencies.append({"path": str(path), "sha256": digest, "origin": origin})
        if not dependencies:
            raise ValueError("Clang reported no compilation dependencies")
        report["compilation_dependencies"] = dependencies
        report["dependency_file_sha256"] = _digest(dependency_data)
        artifact_snapshots[output / "MAIN.d"] = dependency_data
        report["stage"] = "object-abi"
        data = base._read(output / "MAIN.o", base.MAX_OBJECT)
        artifact_snapshots[output / "MAIN.o"] = data
        report["object"] = base.inspect_probe_object(data, profile=base.REAL_PROFILE)
        report["object"].update(path=str(output / "MAIN.o"), sha256=_digest(data))
        attributes = _run([str(compilers[1]), "-A", str(output / "MAIN.o")], cwd=output,
                          timeout_s=timeout_s, runner=runner, report=report)["stdout"]
        required = ("Tag_ABI_PCS_R9_use: Unused", "Tag_THUMB_ISA_use: Thumb-2", "Tag_FP_arch: VFPv3", "Tag_CPU_arch_profile: Application")
        if any(line not in attributes for line in required) or "Tag_ABI_VFP_args: VFP registers" in attributes:
            raise ValueError("ARM object attributes differ from reserved-R9/Thumb/VFP/base-AAPCS profile")
        report["arm_attributes"] = attributes
        report["stage"] = "integrity"
        for path, before in snapshots.items():
            if base._read(path, max(len(before), 1)) != before:
                raise ValueError("Source/compiler/MatIEC input changed during build: " + str(path))
        for path, before in artifact_snapshots.items():
            if base._read(path, max(len(before), 1)) != before:
                raise ValueError("Frozen/generated compilation input changed during build: " + str(path))
        for path, digest in resource_snapshots.items():
            if _digest(base._read(path, 4 * 1024 * 1024)) != digest:
                raise ValueError("Clang resource header changed during build")
        report["generated"] = [{"path": str(p), "sha256": _digest(base._read(p, 1024 * 1024))}
                               for p in sorted(generated.iterdir()) if p.is_file()]
        report.update(ok=True, status="compiled", stage="complete", source_compiled=True,
                      arithmetic="actual MatIEC REAL/C semantics; no fast-math; not vendor helper equivalence")
    except subprocess.TimeoutExpired as error:
        report["diagnostics"].append({"code": "E_TIMEOUT", "stage": report["stage"], "message": str(error)})
    except Exception as error:
        report["diagnostics"].append({"code": "E_MATIEC_PROBE", "stage": report["stage"], "message": str(error)})
    if created:
        (output / "matiec-probe-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "matiec-root", "clang", "output-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--readelf", default="/usr/bin/readelf")
    parser.add_argument("--timeout-s", type=float, default=10.0)
    result = compile_matiec_probe(**vars(parser.parse_args(argv)))
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
