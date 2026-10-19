"""Real MatIEC/Clang and self-authored host stubs; no PLC/Windows/vendor code."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("io_probe", Path(__file__).parents[1] / "compile_easy521_io_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

BODY = """(* Existing caller scheduling; no retained local state or timer.
M8002/M8013 are BOOL input snapshots; Y0 is the sole output commit.
First RUN scan clears Y0; later calls copy the one-second clock. *)
IF M8002 THEN
    Y0 := FALSE;
ELSE
    Y0 := M8013;
END_IF;
"""

# Test code owns every callback; nothing loads a vendor runtime. Noncanonical
# low bytes check the exact observed r0-low8 handling, not a general BOOL policy.
HOST_STUB = r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
extern void MAIN(void);
static uint32_t first_value, clock_value;
static unsigned written, writes, count;
static char trace[16];
static void check(int good) { if (!good) exit(90); }
static void event(char c) { check(count<15); trace[count++]=c; trace[count]=0; }
uint32_t _HC_LD_DEV_MEM_READ_BITS(void *opaque) {
  const uint32_t *p=(const uint32_t *)opaque;
  check(p[0]==UINT32_MAX && p[2]==1);
  if (p[1]==UINT32_C(0x06201802)) { event('R'); return first_value; }
  check(p[1]==UINT32_C(0x06201822)); event('C'); return clock_value;
}
uint32_t _HC_INTERRUPTFCBYST(void) { event('H'); return UINT32_C(0xdeadbeef); }
uint32_t _HC_LD_DEV_MEM_WRITE_BITS(void *opaque) {
  const uint32_t *p=(const uint32_t *)opaque;
  const unsigned char *bytes=(const unsigned char *)opaque;
  check(p[0]==UINT32_MAX && p[1]==UINT32_C(0x00080000) && p[2]==1);
  check(bytes[13]==0 && bytes[14]==0 && bytes[15]==0);
  event('W'); written=bytes[12]; ++writes; return 99; /* ignored, not success proof */
}
int main(void) {
  const uint32_t cases[][3]={{1,0,0},{1,1,0},{0,0,0},{0,1,1},{0x100,0x101,1},
                            {0xff,0,0},{0,0xff,255},{0x101,1,0}};
  for (unsigned i=0;i<sizeof(cases)/sizeof(cases[0]);++i) {
    first_value=cases[i][0]; clock_value=cases[i][1]; count=writes=0; trace[0]=0;
    MAIN();
    check(writes==1 && written==cases[i][2]);
    check(strcmp(trace, (first_value & 255) ? "RHW" : "RHCW")==0);
    printf("%u %u %u %s\n",first_value,clock_value,written,trace);
  }
  return 0;
}
'''


@pytest.fixture
def toolchain():
    root = Path(os.environ.get("PLCIDF_TEST_MATIEC_ROOT", Path.home() / ".cache/plcidf/matiec"))
    clang = os.environ.get("PLCIDF_TEST_CLANG") or shutil.which("clang-20")
    if not (root / "bin/iec2c").is_file() or not clang:
        pytest.skip("pinned real MatIEC and native Linux clang20 are unavailable")
    return root, clang


def compile_body(tmp_path, toolchain, body=BODY, name="build", runner=subprocess.run):
    inputs = tmp_path / "inputs"
    inputs.mkdir(exist_ok=True)
    source = inputs / (name + ".st")
    source.write_text(body)
    return probe.compile_io_probe(source=source, matiec_root=toolchain[0], clang=toolchain[1],
                                  output_dir=tmp_path / name, runner=runner)


def test_real_object_has_only_notype_helper_function_addresses_and_abs32(tmp_path, toolchain):
    result = compile_body(tmp_path, toolchain)
    assert result["ok"], result
    obj = result["object"]
    assert obj["elf"]["defined_global_weak"]["names"] == ["MAIN"]
    assert set(obj["runtime_helpers"]) == set(probe.HELPERS)
    assert all(x["info"] == 0x10 and x["section"] == 0 for x in obj["runtime_helpers"].values())
    assert obj["elf"]["relocations"]["types"] == {"R_ARM_ABS32": 3, "R_ARM_PREL31": 1}
    assert not any(x["size"] and x["name"] in {".rodata", ".data", ".bss"} for x in obj["elf"]["sections"])
    assert "Tag_ABI_PCS_R9_use: Unused" in result["arm_attributes"]
    assert result["interface"]["assignments_per_path"] == [1, 1]
    assert result["interface"]["generated_c_modified"] is False
    assert result["source_compiled"] and not result["downloadable"] and not result["arm_code_executed"]
    assert not result["physical_IO_write_requested"] and not result["runtime_abi_verified"]
    assert len(result["compiler_component_sha256"]) == 5
    assert any(x["path"].endswith("generated/POUS.c") for x in result["compilation_dependencies"])
    assert not any(x["path"].startswith("/usr/include/") for x in result["compilation_dependencies"])


def test_same_wrapper_and_generated_c_obey_descriptor_order_and_single_write_on_host(tmp_path, toolchain):
    result = compile_body(tmp_path, toolchain)
    assert result["ok"], result
    root = tmp_path / "build"
    harness = root / "host-stub.c"
    harness.write_text(HOST_STUB)
    binary = root / "host-stub"
    built = subprocess.run(["gcc", "-std=c11", "-O2", "-isystem", str(root / "matiec/lib/C"),
        "-I", str(root / "generated"), str(root / "wrapper.c"), str(harness), "-o", str(binary)],
        capture_output=True, text=True, timeout=15)
    assert built.returncode == 0, built.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
    assert run.returncode == 0, run.stderr
    assert run.stdout.splitlines() == ["1 0 0 RHW", "1 1 0 RHW", "0 0 0 RHCW", "0 1 1 RHCW",
                                     "256 257 1 RHCW", "255 0 0 RHW", "0 255 255 RHCW", "257 1 0 RHW"]


def test_changed_real_st_logic_produces_different_arm_object(tmp_path, toolchain):
    normal = compile_body(tmp_path, toolchain, name="normal")
    inverted = compile_body(tmp_path, toolchain, BODY.replace("Y0 := M8013;", "Y0 := NOT M8013;"), name="inverted")
    assert normal["ok"] and inverted["ok"], (normal, inverted)
    assert normal["source_sha256"] != inverted["source_sha256"]
    assert normal["object"]["sha256"] != inverted["object"]["sha256"]


@pytest.mark.parametrize("body,stage", [
    ("Y0 := ;", "matiec"), ("Y1 := TRUE;", "matiec"),
    ("M8013 := TRUE; Y0 := FALSE;", "generated-interface"),
    ("Y0 := Y0;", "generated-interface"),
    ("IF M8002 THEN Y0 := FALSE; END_IF;", "generated-interface"),
    ("Y0 := FALSE; Y0 := TRUE;", "generated-interface"),
    ("WHILE M8013 DO Y0 := TRUE; END_WHILE;", "generated-interface"),
    ("VAR local : BOOL; END_VAR Y0 := local;", "generated-interface"),
])
def test_real_frontend_or_bounded_interface_guard_refuses_unsupported_code(tmp_path, toolchain, body, stage):
    result = compile_body(tmp_path, toolchain, body)
    assert not result["ok"] and result["stage"] == stage, result
    assert not any("--target=armv7a-none-eabi" in item["argv"] for item in result["commands"])


def test_timeout_stops_without_retry(tmp_path, toolchain):
    calls = []
    def timeout(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    result = compile_body(tmp_path, toolchain, runner=timeout)
    assert not result["ok"] and result["diagnostics"][0]["code"] == "E_TIMEOUT" and len(calls) == 1
    assert (tmp_path / "build/body.st").read_text() == BODY


def test_generated_source_drift_during_clang_is_rejected(tmp_path, toolchain):
    def drift(argv, **kwargs):
        completed = subprocess.run(argv, **kwargs)
        if "--target=armv7a-none-eabi" in argv:
            path = tmp_path / "build/generated/POUS.c"
            path.chmod(0o644)
            path.write_text(path.read_text() + "\n/* changed */\n")
        return completed
    result = compile_body(tmp_path, toolchain, runner=drift)
    assert not result["ok"] and result["stage"] == "integrity", result


def test_unsnapshotted_dependency_is_rejected(tmp_path, toolchain):
    def drift(argv, **kwargs):
        completed = subprocess.run(argv, **kwargs)
        if "--target=armv7a-none-eabi" in argv:
            p = tmp_path / "build/MAIN.d"
            p.write_text(p.read_text() + " /usr/include/undeclared.h\n")
        return completed
    result = compile_body(tmp_path, toolchain, runner=drift)
    assert not result["ok"] and result["stage"] == "compilation-dependencies"


def _symbol_info_offset(raw, wanted):
    shoff = struct.unpack_from("<I", raw, 32)[0]
    shnum = struct.unpack_from("<H", raw, 48)[0]
    sections = [struct.unpack_from("<10I", raw, shoff + i * 40) for i in range(shnum)]
    for table in sections:
        if table[1] != 2: continue
        strings = sections[table[6]]
        names = raw[strings[4]:strings[4] + strings[5]]
        for position in range(table[4], table[4] + table[5], 16):
            offset = struct.unpack_from("<I", raw, position)[0]
            if names[offset:names.find(b"\0", offset)].decode() == wanted:
                return position + 12
    raise AssertionError("test helper missing")


@pytest.mark.parametrize("symbol_type", [0x11, 0x12, 0x18])
def test_function_pointer_helper_cannot_become_object_func_or_relc_variable(tmp_path, toolchain, symbol_type):
    result = compile_body(tmp_path, toolchain)
    assert result["ok"], result
    raw = bytearray((tmp_path / "build/MAIN.o").read_bytes())
    raw[_symbol_info_offset(raw, "_HC_LD_DEV_MEM_READ_BITS")] = symbol_type
    with pytest.raises(ValueError):
        probe.inspect_io_object(bytes(raw))


@pytest.mark.parametrize("mutation", ["call-relocation", "extra-allocated-section"])
def test_unobserved_relocations_or_allocated_sections_are_rejected(tmp_path, toolchain, mutation):
    result = compile_body(tmp_path, toolchain)
    assert result["ok"], result
    raw = bytearray((tmp_path / "build/MAIN.o").read_bytes())
    if mutation == "call-relocation":
        section = next(s for s in result["object"]["elf"]["sections"] if s["name"] == ".rel.text")
        raw[section["offset"] + 4] = 28  # R_ARM_CALL is outside this observed pointer-call profile.
    else:
        section = next(s for s in result["object"]["elf"]["sections"] if s["name"] == ".ARM.attributes")
        shoff = struct.unpack_from("<I", raw, 32)[0]
        struct.pack_into("<I", raw, shoff + section["index"] * 40 + 8, 2)  # SHF_ALLOC
    with pytest.raises(ValueError):
        probe.inspect_io_object(bytes(raw))


@pytest.mark.parametrize("timeout", [0, True, 31, float("nan")])
def test_invalid_timeout_has_no_output_or_tool_calls(tmp_path, timeout):
    result = probe.compile_io_probe(source="/missing", matiec_root="/missing", clang="/missing",
                                    output_dir=tmp_path / "build", timeout_s=timeout)
    assert not result["ok"] and not result["commands"] and not (tmp_path / "build").exists()


def test_generated_validator_is_bounded_and_has_no_st_translation():
    good = probe.SIGNATURE + " __SET_VAR(data__->,Y0,,__BOOL_LITERAL(FALSE)); goto __end; __end: return; } // PLCIDF_MAIN_body__()"
    assert probe.validate_generated(probe.HEADER, good)["assignments_per_path"] == [1, 1]
    bad = good.replace("__BOOL_LITERAL(FALSE)", "!" * 40 + "__BOOL_LITERAL(FALSE)")
    with pytest.raises(ValueError):
        probe.validate_generated(probe.HEADER, bad)
    with pytest.raises(ValueError):
        probe.validate_generated(probe.HEADER + "int extra;", good)
