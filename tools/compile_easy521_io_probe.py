#!/usr/bin/env python3
"""Compile one stateless MatIEC BOOL program through the observed Easy521 shim.

Inputs are a statement body using read-only M8002/M8013 and exactly one Y0
assignment per execution path, a pinned MatIEC installation, native Clang and
readelf. Real MatIEC performs ST parsing/type checking/C generation. A bounded
validator checks generated C but never translates ST. The stack-local MatIEC
context is connected to encoded-operand runtime calls from report34; encoded
bit operands are never cast to RAM pointers. The interrupt hook has a 32-bit
return candidate shape and its result is explicitly ignored, not guessed void.

This is an OFFLINE compilation/ABI-shape probe. The physical helper typedefs,
padding/error/refresh/force behavior and acceptance of new code remain unknown.
Zero-filled local padding is this shim's deterministic choice, not a recovered
vendor guarantee. No device/Windows/network/download/control API exists here.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import struct
import subprocess


def _adjacent(name):
    spec = importlib.util.spec_from_file_location("_io_probe_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = _adjacent("compile_easy521_matiec_probe")
base = engine.base
PROFILE = "matiec-easy521-three-bool-descriptor-io-v1"
HELPERS = ("_HC_LD_DEV_MEM_READ_BITS", "_HC_LD_DEV_MEM_WRITE_BITS", "_HC_INTERRUPTFCBYST")
HEADER = '''#ifdef DISABLE_EN_ENO_PARAMETERS
#include "iec_std_FB_no_ENENO.h"
#else
#include "iec_std_FB.h"
#endif
__DECLARE_PROGRAM_TYPE(PLCIDF_MAIN,
__DECLARE_VAR(BOOL,M8002)
__DECLARE_VAR(BOOL,M8013)
__DECLARE_VAR(BOOL,Y0)
)
'''
SIGNATURE = "static void PLCIDF_MAIN_body__(PLCIDF_MAIN_data__ *data__) {"
WRAPPER = r'''/* Fixed descriptor ABI candidate; report34 records evidence/unknowns.
 * Read function returns r0, consumed as a low-byte BOOL. No result slot is read.
 * All calls are indirect so helper symbols stay NOTYPE with ABS32 relocations.
 * -mno-movt and volatile field stores avoid unverified .rodata/global state.
 * The leading -1/count1/encoded operands are this exact observed I/O subset.
 * Value+12 is a byte store, matching ARM; padding is explicitly local zero.
 * Simulator reads u16 here; physical width/padding/error semantics are unknown.
 * This MAIN has one writer: Y0. It is not a safety/forced-output implementation. */
#include <stdint.h>
#include <stddef.h>
#include "iec_std_lib.h"
#include "accessor.h"
#include "POUS.h"
#include "POUS.c"
typedef struct { uint32_t leading, operand, count; } ReadDescriptor;
typedef struct { uint32_t leading, operand, count; uint8_t value, padding[3]; } WriteDescriptor;
_Static_assert(sizeof(ReadDescriptor)==12 && offsetof(ReadDescriptor,operand)==4
    && offsetof(ReadDescriptor,count)==8, "read descriptor layout changed");
_Static_assert(sizeof(WriteDescriptor)==16 && offsetof(WriteDescriptor,value)==12,
    "write descriptor layout changed");
extern uint32_t _HC_LD_DEV_MEM_READ_BITS(void *);
extern uint32_t _HC_LD_DEV_MEM_WRITE_BITS(void *);
extern uint32_t _HC_INTERRUPTFCBYST(void);
static inline uint8_t read_one_bit(uint32_t operand) {
    volatile ReadDescriptor descriptor;
    descriptor.leading=UINT32_MAX; descriptor.operand=operand; descriptor.count=1;
    uint32_t (*function)(void *)=_HC_LD_DEV_MEM_READ_BITS;
    __asm__ volatile("" : "+r"(function));
    return (uint8_t)function((void *)&descriptor);
}
void MAIN(void) {
    PLCIDF_MAIN_data__ data;
    PLCIDF_MAIN_init__(&data, 0); /* initializes local BOOLs/flags only */
    const uint8_t first=read_one_bit(UINT32_C(0x06201802));
    uint32_t (*hook)(void)=_HC_INTERRUPTFCBYST;
    __asm__ volatile("" : "+r"(hook));
    (void)hook(); /* observed interrupt-program hook; return intentionally unused */
    data.M8002.value=first;
    data.M8013.value=first ? 0 : read_one_bit(UINT32_C(0x06201822));
    PLCIDF_MAIN_body__(&data); /* MatIEC-generated C is compiled unchanged */
    volatile WriteDescriptor descriptor;
    descriptor.leading=UINT32_MAX; descriptor.operand=UINT32_C(0x00080000); descriptor.count=1;
    descriptor.value=(uint8_t)data.Y0.value;
    descriptor.padding[0]=0; descriptor.padding[1]=0; descriptor.padding[2]=0;
    uint32_t (*writer)(void *)=_HC_LD_DEV_MEM_WRITE_BITS;
    __asm__ volatile("" : "+r"(writer));
    (void)writer((void *)&descriptor); /* original ARM caller also ignores status */
}
'''


class _GeneratedBody:
    """Validate only generated BOOL C macros and per-path assignment counts."""
    def __init__(self, text):
        token = re.compile(r"\s+|[A-Za-z_][A-Za-z_0-9]*|->|&&|\|\||==|!=|[01]|[{}(),;!]")
        self.tokens, offset = [], 0
        while offset < len(text):
            match = token.match(text, offset)
            if match is None:
                raise ValueError("unsupported generated BOOL C token")
            if not match[0].isspace():
                self.tokens.append(match[0])
            offset = match.end()
        if len(self.tokens) > 4096:
            raise ValueError("generated BOOL body exceeds4096 tokens")
        self.position = 0

    def peek(self):
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self, value=None):
        token = self.peek()
        if token is None or value is not None and token != value:
            raise ValueError("unsupported generated BOOL C structure")
        self.position += 1
        return token

    def expression(self, level=0):
        if level > 32:
            raise ValueError("BOOL expression nesting exceeds32")
        def primary():
            unary = 0
            while self.peek() == "!":
                self.take("!"); unary += 1
                if unary > 32:
                    raise ValueError("BOOL unary nesting exceeds32")
            if self.peek() == "(":
                self.take("(")
                self.expression(level + 1)
                self.take(")")
            elif self.peek() == "__GET_VAR":
                for token in ("__GET_VAR", "(", "data__", "->"):
                    self.take(token)
                if self.take() not in {"M8002", "M8013"}:
                    raise ValueError("only M8002/M8013 input reads are supported")
                self.take(",")
                self.take(")")
            elif self.peek() == "__BOOL_LITERAL":
                self.take("__BOOL_LITERAL")
                self.take("(")
                if self.take() not in {"TRUE", "FALSE", "0", "1"}:
                    raise ValueError("unknown generated BOOL literal")
                self.take(")")
            else:
                raise ValueError("unsupported generated BOOL expression")
        primary()
        # Only BOOL operators with no side effects are admitted. This parser
        # validates token consumption, not evaluation or translation of the ST.
        while self.peek() in {"&&", "||", "==", "!="}:
            self.take()
            primary()

    def block(self, depth=0):
        if depth > 16:
            raise ValueError("BOOL statement nesting exceeds16")
        minimum = maximum = 0
        while self.peek() not in (None, "}"):
            if self.peek() == ";":
                self.take(";")
            elif self.peek() == "if":
                self.take("if"); self.take("("); self.expression(); self.take(")"); self.take("{")
                yes = self.block(depth + 1); self.take("}")
                no = (0, 0)
                if self.peek() == "else":
                    self.take("else"); self.take("{"); no = self.block(depth + 1); self.take("}")
                minimum += min(yes[0], no[0]); maximum += max(yes[1], no[1])
            elif self.peek() == "__SET_VAR":
                for token in ("__SET_VAR", "(", "data__", "->", ",", "Y0", ",", ","):
                    self.take(token)
                self.expression(); self.take(")"); self.take(";")
                minimum += 1; maximum += 1
            else:
                raise ValueError("only IF/ELSE and Y0 assignment are supported")
        return minimum, maximum


def validate_generated(header: str, generated: str) -> dict:
    """Require exact three-BOOL interface and one Y0 assignment on every path.

    MatIEC remains the language frontend. This bounded generated-C check rejects
    input writes, Y0 feedback/local retention, helper calls, loops, early RETURN,
    extra variables/POUs and incomplete or repeated output assignments. Returns
    metadata only; no code is rewritten and no I/O occurs.
    """
    if len(header) > 65536 or len(generated) > 1024 * 1024:
        raise ValueError("generated interface/body exceeds bounds")
    if "".join(header.split()) != "".join(HEADER.split()) or generated.count(SIGNATURE) != 1:
        raise ValueError("requires exactly M8002/M8013/Y0 BOOL program interface")
    body = generated.split(SIGNATURE, 1)[1].split("} // PLCIDF_MAIN_body__()", 1)[0]
    body = re.sub(r"//[^\n]*|/\*.*?\*/", " ", body, flags=re.S)
    trailer = re.search(r"goto\s+__end\s*;\s*__end\s*:\s*return\s*;\s*\Z", body)
    if trailer is None:
        raise ValueError("unexpected generated BOOL body trailer")
    parser = _GeneratedBody(body[:trailer.start()])
    counts = parser.block()
    if parser.peek() is not None or counts != (1, 1):
        raise ValueError("each generated execution path must assign Y0 exactly once")
    return {"generated_body_sha256": engine._digest(body.encode()), "generated_c_modified": False,
            "read_only_inputs": ["M8002", "M8013"], "sole_output": "Y0", "assignments_per_path": [1, 1],
            "local_state": False, "force_supported": False, "task_period_ms": None}


def inspect_io_object(data: bytes) -> dict:
    """Require bounded Thumb MAIN and only three NOTYPE runtime helper imports.

    Rejects function-pointer objects/RELC variable bindings, additional globals,
    data/rodata/BSS allocation and unobserved call relocation categories. Helpers
    must be UND/global/NOTYPE with zero value and size, as the real device object.
    ABS32/PREL31 structure is evidence of matching shape, not loader acceptance.
    """
    elf = base._inspect.parse_elf(data)
    if (elf["class_bits"], elf["endianness"], elf["machine_id"], elf["type_id"], elf["arm_eabi_version"]) != (32, "little", 40, 1, 5):
        raise ValueError("I/O object must be ARM32 LE EABI5 REL")
    if elf["defined_global_weak"]["names"] != ["MAIN"] or set(elf["undefined_global_weak"]["names"]) != set(HELPERS):
        raise ValueError("I/O object must define MAIN and import only the three runtime helpers")
    if elf["defined_global_weak"]["entries"] != 1 or elf["undefined_global_weak"]["entries"] != 3:
        raise ValueError("duplicate or extra global symbol entries are unsupported")
    if set(elf["relocations"]["types"]) - {"R_ARM_ABS32", "R_ARM_PREL31"}:
        raise ValueError("unobserved external call relocation category")
    if any(s["size"] and s["flags"] & 2 and s["name"] not in {".text", ".ARM.exidx"}
           for s in elf["sections"]):
        raise ValueError("fixed I/O shim must not add allocated data/state/rodata sections")
    shoff = struct.unpack_from("<I", data, 32)[0]
    shnum = struct.unpack_from("<H", data, 48)[0]
    sections = [struct.unpack_from("<10I", data, shoff + i * 40) for i in range(shnum)]
    if sum(section[1] == 2 for section in sections) != 1:
        raise ValueError("requires one explicit ELF symbol table")
    symbols, helpers, main = [], {}, None
    for section in sections:
        if section[1] != 2:
            continue
        strings = sections[section[6]]
        names = data[strings[4]:strings[4] + strings[5]]
        for offset in range(section[4], section[4] + section[5], 16):
            name, value, size, info, other, index = struct.unpack_from("<IIIBBH", data, offset)
            symbol = names[name:names.find(b"\0", name)].decode("ascii")
            entry = {"name": symbol, "value": value, "size": size, "info": info, "other": other, "section": index}
            symbols.append(entry)
            if symbol in HELPERS:
                if (value, size, info, other, index) != (0, 0, 0x10, 0, 0):
                    raise ValueError("runtime helper must remain UND/global/NOTYPE, not a variable or FUNC import")
                helpers[symbol] = entry
            if symbol == "MAIN":
                if info != 0x12 or not value & 1 or not 1 <= index < len(sections) or not 8 <= size <= 4096:
                    raise ValueError("MAIN must be a bounded global Thumb function")
                target = sections[index]
                if (value & ~1) + size > target[5]:
                    raise ValueError("MAIN exceeds its code section")
                main = entry
    if set(helpers) != set(HELPERS) or main is None:
        raise ValueError("fixed runtime symbols missing")
    referenced = set()
    for section in sections:
        if section[1] != 9:
            continue
        for offset in range(section[4], section[4] + section[5], 8):
            _, info = struct.unpack_from("<II", data, offset)
            name = symbols[info >> 8]["name"]
            if name in HELPERS:
                if info & 255 != 2:
                    raise ValueError("runtime helper must use function-address ABS32 relocation")
                referenced.add(name)
    if referenced != set(HELPERS):
        raise ValueError("runtime helper function addresses are not all referenced")
    return {"elf": elf, "MAIN": main, "runtime_helpers": helpers,
            "external_pointer_kind": "UND/global/NOTYPE with R_ARM_ABS32",
            "encoded_operand_is_pointer": False, "physical_runtime_abi_verified": False}


def compile_io_probe(*, source, matiec_root, clang, output_dir, readelf="/usr/bin/readelf",
                     timeout_s=10.0, runner=subprocess.run) -> dict:
    """Run bounded real MatIEC/Clang compilation into a new private directory.

    Reuses31's audited file reader, command runner, pinned MatIEC commitment and
    ELF parser. The independent BOOL wrapper/interface and object validation
    never alter31's default REAL profile. Source, tools, MatIEC libraries,
    generated C, actual -MD dependencies and Clang resource headers are hashed
    before use and rechecked. Every process runs once with0.1..30s timeout.
    Failure preserves partial evidence; no device or host runtime is invoked.
    """
    report = {"schema": "plcidf.easy521-matiec-io-probe.v1", "profile": PROFILE,
        "ok": False, "status": "failed", "stage": "preflight", "commands": [], "diagnostics": [],
        "source_compiled": False, "device_access": False, "windows_access": False,
        "downloadable": False, "runtime_abi_verified": False, "arm_code_executed": False,
        "physical_IO_write_requested": False, "force_supported": False, "interrupt_support_verified": False,
        "value_padding_policy": "local zero-filled bytes; physical ABI guarantee unknown"}
    output, created = Path(output_dir).absolute(), False
    try:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0.1 <= timeout_s <= 30:
            raise ValueError("timeout_s must be finite0.1..30 seconds")
        source, root = Path(source).absolute(), Path(matiec_root).absolute()
        body = base._read(source, base.MAX_SOURCE)
        text = body.decode("utf-8-sig")
        if "\0" in text or "{" in text or "}" in text:
            raise ValueError("ST source cannot contain NUL or implementation-specific pragmas")
        marker_path = root / "toolchain.json"
        marker_data = base._read(marker_path, 1024 * 1024)
        marker = json.loads(marker_data)
        if marker.get("schema") != "plcidf.open-linux.toolchain.v1" or marker.get("matiec_commit") != engine.MATIEC_COMMIT:
            raise ValueError("unsupported MatIEC schema/commit")
        iec2c = root / "bin/iec2c"
        compilers = [Path(clang).resolve(strict=True), Path(readelf).resolve(strict=True)]
        snapshots = {source: body, marker_path: marker_data, iec2c: base._read(iec2c, 32 * 1024 * 1024)}
        components = tuple(Path(p).absolute() for p in (__file__, engine.__file__, base.__file__, base._prepare.__file__, base._inspect.__file__))
        for path in components:
            snapshots[path] = base._read(path, 1024 * 1024)
        if engine._digest(snapshots[iec2c]) != marker.get("iec2c_sha256"):
            raise ValueError("MatIEC compiler hash differs from installation manifest")
        for compiler in compilers:
            binary = base._read(compiler, 256 * 1024 * 1024)
            if binary[:7] != b"\x7fELF\x02\x01\x01":
                raise ValueError("compiler/inspection tools must be native Linux ELF64")
            snapshots[compiler] = binary
        libraries = marker.get("library_sha256")
        if not isinstance(libraries, dict) or not libraries:
            raise ValueError("MatIEC library hashes are required")
        for name, digest in libraries.items():
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or not name.startswith("lib/"):
                raise ValueError("invalid MatIEC library manifest path")
            raw = base._read(root / name, 4 * 1024 * 1024)
            if engine._digest(raw) != digest:
                raise ValueError("MatIEC library hash mismatch")
            snapshots[root / name] = raw
        if output.exists() or not output.parent.is_dir() or any(p.is_symlink() for p in (output, *output.parents)):
            raise ValueError("output must be a new nonsymlink directory with existing parent")
        if any(output == p.parent or p.parent in output.parents or output in p.parents for p in snapshots):
            raise ValueError("output must not overlap original input directories")
        report.update(source_sha256=engine._digest(body), source_path=str(source), output_directory=str(output),
            matiec_commit=engine.MATIEC_COMMIT, toolchain_manifest_sha256=engine._digest(marker_data),
            toolchain_manifest=marker, tool_hashes={str(p):engine._digest(snapshots[p]) for p in (iec2c, *compilers)},
            compiler_component_sha256={str(p):engine._digest(snapshots[p]) for p in components},
            wrapper_sha256=engine._digest(WRAPPER.encode()))
        output.mkdir(); created = True
        generated = output / "generated"; generated.mkdir()
        frozen = output / "matiec"
        artifacts = {}
        for name in libraries:
            p = frozen / name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(snapshots[root / name]); p.chmod(0o444); artifacts[p] = snapshots[root / name]
        program = ("PROGRAM PLCIDF_MAIN\nVAR_INPUT\n M8002 : BOOL;\n M8013 : BOOL;\nEND_VAR\n"
                   "VAR_OUTPUT\n Y0 : BOOL;\nEND_VAR\n" + text + "\nEND_PROGRAM\n").encode()
        for name, raw in (("body.st", body), ("source.st", program), ("wrapper.c", WRAPPER.encode())):
            p = output / name; p.write_bytes(raw); p.chmod(0o444); artifacts[p] = raw
        def run(argv):
            return engine._run(argv, cwd=output, timeout_s=timeout_s, runner=runner, report=report)
        report["stage"] = "matiec"
        run([str(iec2c), "-I", str(frozen / "lib"), "-T", str(generated), str(output / "source.st")])
        header = base._read(generated / "POUS.h", 1024 * 1024).decode()
        code = base._read(generated / "POUS.c", 1024 * 1024).decode()
        report["stage"] = "generated-interface"
        report["interface"] = validate_generated(header, code)
        for path in generated.iterdir():
            if not path.is_file():
                raise ValueError("unexpected generated directory")
            artifacts[path] = base._read(path, 1024 * 1024); path.chmod(0o444)
        report["stage"] = "clang-resource-directory"
        resource = Path(run([str(compilers[0]), "-print-resource-dir"])["stdout"].strip()) / "include"
        if not resource.is_absolute() or not resource.is_dir():
            raise ValueError("Clang resource headers unavailable")
        resource_hashes, total = {}, 0
        for path in sorted(resource.rglob("*.h")):
            raw = base._read(path, 4 * 1024 * 1024); total += len(raw)
            if total > 32 * 1024 * 1024:
                raise ValueError("Clang resource headers exceed32MiB")
            resource_hashes[path] = engine._digest(raw)
        report["clang_resource_headers"] = {"directory": str(resource), "sha256": {str(p):h for p,h in resource_hashes.items()}}
        report["stage"] = "clang-arm"
        run([str(compilers[0]), "--target=armv7a-none-eabi", "-mthumb", "-mcpu=cortex-a8", "-mfpu=neon",
             "-mfloat-abi=softfp", "-ffixed-r9", "-mno-movt", "-ffreestanding", "-fno-builtin", "-fno-pic",
             "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-fno-stack-protector", "-O2", "-std=c11",
             "-Werror=literal-range", "-MD", "-MF", str(output / "MAIN.d"), "-MT", "PLCIDF_MAIN",
             "-isystem", str(frozen / "lib/C"), "-I", str(generated), "-c", str(output / "wrapper.c"),
             "-o", str(output / "MAIN.o")])
        report["stage"] = "compilation-dependencies"
        dependency_data = base._read(output / "MAIN.d", 1024 * 1024)
        dependency_text = dependency_data.decode().replace("\\\n", "")
        if not dependency_text.startswith("PLCIDF_MAIN:"):
            raise ValueError("unexpected dependency target")
        dependencies = []
        for name in dict.fromkeys(shlex.split(dependency_text[len("PLCIDF_MAIN:"):])):
            path = Path(name)
            if not path.is_absolute(): path = output / path
            if path in artifacts:
                digest, origin = engine._digest(artifacts[path]), "frozen-generated-or-matiec"
            elif path in resource_hashes:
                digest, origin = resource_hashes[path], "clang-resource-header"
            else:
                raise ValueError("unsnapshotted compilation dependency: " + str(path))
            dependencies.append({"path": str(path), "sha256": digest, "origin": origin})
        if not dependencies: raise ValueError("no compilation dependencies")
        report["compilation_dependencies"] = dependencies
        report["dependency_file_sha256"] = engine._digest(dependency_data)
        artifacts[output / "MAIN.d"] = dependency_data
        report["stage"] = "object-abi"
        obj = base._read(output / "MAIN.o", base.MAX_OBJECT); artifacts[output / "MAIN.o"] = obj
        report["object"] = inspect_io_object(obj)
        report["object"].update(path=str(output / "MAIN.o"), sha256=engine._digest(obj))
        attributes = run([str(compilers[1]), "-A", str(output / "MAIN.o")])["stdout"]
        required = ("Tag_ABI_PCS_R9_use: Unused", "Tag_THUMB_ISA_use: Thumb-2", "Tag_CPU_arch_profile: Application")
        if any(item not in attributes for item in required) or "Tag_ABI_VFP_args: VFP registers" in attributes:
            raise ValueError("ARM attributes differ from fixed reserved-R9/base-AAPCS profile")
        report["arm_attributes"] = attributes
        report["stage"] = "integrity"
        for path, raw in {**snapshots, **artifacts}.items():
            if base._read(path, max(len(raw), 1)) != raw:
                raise ValueError("source/tool/frozen/generated input changed during compilation: " + str(path))
        for path, digest in resource_hashes.items():
            if engine._digest(base._read(path, 4 * 1024 * 1024)) != digest:
                raise ValueError("Clang resource header changed during compilation")
        report["generated"] = [{"path": str(p), "sha256": engine._digest(raw)} for p,raw in artifacts.items() if p.parent == generated]
        report.update(ok=True, status="compiled", stage="complete", source_compiled=True,
            semantics="actual MatIEC generated BOOL C with one descriptor output commit; no physical runtime proof")
    except subprocess.TimeoutExpired as exc:
        report["diagnostics"].append({"code": "E_TIMEOUT", "stage": report["stage"], "message": str(exc)})
    except Exception as exc:
        report["diagnostics"].append({"code": "E_IO_PROBE", "stage": report["stage"], "message": str(exc)})
    if created:
        (output / "io-probe-report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "matiec-root", "clang", "output-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--readelf", default="/usr/bin/readelf")
    parser.add_argument("--timeout-s", type=float, default=10.0)
    result = compile_io_probe(**vars(parser.parse_args(argv)))
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
