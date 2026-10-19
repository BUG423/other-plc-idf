"""Real MatIEC/Clang when present; no vendor executable, Windows or device I/O."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("matiec_probe", Path(__file__).parents[1] / "compile_easy521_matiec_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

BODY = """(* One existing REAL, no task/retention/I/O declarations. *)
IF g_rBESS_Pcmd < REAL#10.0 THEN
    g_rBESS_Pcmd := g_rBESS_Pcmd + REAL#1.0;
ELSE
    g_rBESS_Pcmd := REAL#0.0;
END_IF;
"""


@pytest.fixture
def toolchain():
    root = Path(os.environ.get("PLCIDF_TEST_MATIEC_ROOT", Path.home() / ".cache/plcidf/matiec"))
    clang = os.environ.get("PLCIDF_TEST_CLANG") or shutil.which("clang-20")
    if not (root / "bin/iec2c").is_file() or not clang:
        pytest.skip("Real pinned MatIEC and native Linux clang20 are not available")
    return root, clang


def compile_body(tmp_path, toolchain, body=BODY, name="build", runner=subprocess.run):
    source = tmp_path / (name + ".st")
    source.write_text(body)
    return probe.compile_matiec_probe(source=source, matiec_root=toolchain[0], clang=toolchain[1],
                                       output_dir=tmp_path / name, runner=runner)


def test_real_matiec_if_else_compiles_to_bounded_thumb_main_and_pcmd_only(tmp_path, toolchain):
    result = compile_body(tmp_path, toolchain)
    assert result["ok"], result
    assert result["source_compiled"] and not result["downloadable"] and not result["arm_code_executed"]
    assert result["object"]["elf"]["defined_global_weak"]["names"] == ["MAIN"]
    assert result["object"]["elf"]["undefined_global_weak"]["names"] == ["G_RBESS_PCMD"]
    assert result["interface"]["generated_c_modified"] is False
    assert "Tag_ABI_PCS_R9_use: Unused" in result["arm_attributes"]
    assert "Tag_THUMB_ISA_use: Thumb-2" in result["arm_attributes"]
    assert result["commands"][0]["argv"][0].endswith("iec2c")
    assert (tmp_path / "build/generated/POUS.c").read_text().count("if (") >= 1
    dependencies = result["compilation_dependencies"]
    assert any(entry["path"].endswith("generated/POUS.c") for entry in dependencies)
    assert any(entry["path"].endswith("generated/POUS.h") for entry in dependencies)
    assert any(entry["origin"] == "clang-resource-header" for entry in dependencies)
    assert all(entry["origin"] in {"frozen-generated-or-matiec", "clang-resource-header"}
               for entry in dependencies)
    assert not any(entry["path"].startswith("/usr/include/") for entry in dependencies)
    assert len(result["compiler_component_sha256"]) == 3


def test_same_generated_c_on_linux_obeys_both_branches_and_does_not_reset_external(tmp_path, toolchain):
    result = compile_body(tmp_path, toolchain)
    assert result["ok"], result
    root = tmp_path / "build"
    driver = root / "host-test.c"
    driver.write_text('''#include <stdio.h>
float G_RBESS_PCMD;
void MAIN(void);
int main(void) {
  const float initial[] = {-1.0f, 0.0f, 9.0f, 10.0f, 100.0f};
  for (unsigned i=0; i<5; ++i) {
    G_RBESS_PCMD=initial[i]; MAIN(); printf("%.9g\\n", (double)G_RBESS_PCMD);
  }
  G_RBESS_PCMD=0;
  for (unsigned i=0; i<12; ++i) { MAIN(); printf("%.9g\\n", (double)G_RBESS_PCMD); }
  return 0;
}''')
    binary = root / "host-test"
    result = subprocess.run(["gcc", "-std=c11", "-O2", "-isystem", str(root / "matiec/lib/C"),
                             "-I", str(root / "generated"), str(root / "wrapper.c"), str(driver),
                             "-o", str(binary)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    values = subprocess.run([str(binary)], capture_output=True, text=True, check=True, timeout=5).stdout.splitlines()
    assert list(map(float, values)) == [0, 1, 10, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0, 1]


def test_changed_st_branch_rule_produces_a_different_real_arm_object(tmp_path, toolchain):
    one = compile_body(tmp_path, toolchain, name="one")
    two = compile_body(tmp_path, toolchain, body=BODY.replace("REAL#1.0", "REAL#2.0"), name="two")
    assert one["ok"] and two["ok"]
    assert one["source_sha256"] != two["source_sha256"]
    assert one["object"]["sha256"] != two["object"]["sha256"]


def test_actual_ir_and_assembled_arm_chain_use_strict_nonatomic_byte_accesses(tmp_path, toolchain):
    result = compile_body(tmp_path, toolchain)
    assert result['ok'], result
    assert result['external_alignment_bytes'] == 1 and result['strict_align']
    assert result['access_atomic'] is False and result['concurrent_writer_supported'] is False
    ir = result['alignment_ir']
    assembly = result['arm_assembly']
    assert ir['used_for_arm_codegen'] and assembly['used_for_object_assembly']
    assert ir['inspection']['volatile_loads'] >= 1
    assert assembly['inspection']['byte_loads'] >= 4 * ir['inspection']['volatile_loads']
    assert assembly['inspection']['byte_stores'] >= 4 * ir['inspection']['volatile_stores']
    assert assembly['inspection']['nonbyte_general_register_accesses'] == 0
    commands = [entry['argv'] for entry in result['commands']]
    lowering = next(argv for argv in commands if '-x' in argv and 'ir' in argv)
    assembling = next(argv for argv in commands if '-x' in argv and 'assembler' in argv)
    assert ir['path'] in lowering and assembly['path'] in assembling
    assert '-mno-unaligned-access' in lowering and '-mno-unaligned-access' in assembling
    assert '@G_RBESS_PCMD = external dso_local global float, align 1' in Path(ir['path']).read_text()


def test_same_generated_c_handles_a_single_writer_at_byte_offset_two_without_reset(tmp_path, toolchain):
    result = compile_body(tmp_path, toolchain)
    assert result['ok'], result
    root = tmp_path / 'build'
    driver = root / 'unaligned-host.c'
    driver.write_text(r'''#include <assert.h>
#include <stdint.h>
#include <string.h>
_Alignas(8) unsigned char plc_bytes[16];
__asm__(".globl G_RBESS_PCMD\n.set G_RBESS_PCMD, plc_bytes + 2");
void MAIN(void);
int main(void) {
    assert(((uintptr_t)(plc_bytes+2) & 3) == 2);
    const float initial[] = {-1.0f, 0.0f, 9.0f, 10.0f, 100.0f};
    const float expected[] = {0.0f, 1.0f, 10.0f, 0.0f, 0.0f};
    for(unsigned i=0;i<5;i++) {
        memset(plc_bytes,0xa5,sizeof plc_bytes);
        memcpy(plc_bytes+2,&initial[i],4);
        MAIN();
        float observed; memcpy(&observed,plc_bytes+2,4);
        assert(observed==expected[i]);
        for(unsigned j=0;j<16;j++) if(j<2 || j>=6) assert(plc_bytes[j]==0xa5);
    }
    return 0;
}''')
    binary = root / 'unaligned-host'
    command = ['gcc', '-std=c11', '-O2', '-fsanitize=undefined', '-fno-sanitize-recover=all',
               '-isystem', str(root/'matiec/lib/C'), '-I', str(root/'generated'),
               str(root/'wrapper.c'), str(driver), '-o', str(binary)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert compiled.returncode == 0, compiled.stderr
    executed = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
    assert executed.returncode == 0, executed.stderr


@pytest.mark.parametrize('artifact,stage', [('ir', 'alignment-ir'), ('assembly', 'assembly-audit')])
def test_alignment_ir_or_arm_word_access_is_rejected_before_object_assembly(tmp_path, toolchain, artifact, stage):
    def invalid_alignment(argv, **kwargs):
        completed = subprocess.run(argv, **kwargs)
        if artifact == 'ir' and '-emit-llvm' in argv:
            path = tmp_path/'build/MAIN.ll'
            path.write_text(path.read_text().replace('align 1', 'align 4'))
        elif artifact == 'assembly' and '-x' in argv and 'ir' in argv:
            path = tmp_path/'build/MAIN.s'
            path.write_text('MAIN:\n vldr s0, [r0]\n bx lr\n.Lfunc_end0:\n')
        return completed
    result = compile_body(tmp_path, toolchain, runner=invalid_alignment)
    assert not result['ok'] and result['stage'] == stage
    assert not (tmp_path/'build/MAIN.o').exists()


@pytest.mark.parametrize("body,stage", [
    ("g_rBESS_Pcmd := ;", "matiec"),
    ("unknown_variable := REAL#1.0;", "matiec"),
    ("WHILE g_rBESS_Pcmd < REAL#10.0 DO g_rBESS_Pcmd := g_rBESS_Pcmd + REAL#1.0; END_WHILE;", "generated-interface"),
    ("g_rBESS_Pcmd := SIN(g_rBESS_Pcmd);", "generated-interface"),
    ("VAR local : REAL; END_VAR g_rBESS_Pcmd := local;", "generated-interface"),
])
def test_real_frontend_or_interface_guard_rejects_unsupported_programs(tmp_path, toolchain, body, stage):
    result = compile_body(tmp_path, toolchain, body=body)
    assert not result["ok"] and result["stage"] == stage
    assert not any("--target=armv7a-none-eabi" in command["argv"] for command in result["commands"])


def test_matiec_timeout_is_not_retried_and_preserves_frozen_source(tmp_path, toolchain):
    calls = []
    def timeout(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    result = compile_body(tmp_path, toolchain, runner=timeout)
    assert not result["ok"] and result["diagnostics"][0]["code"] == "E_TIMEOUT" and len(calls) == 1
    assert (tmp_path / "build/body.st").read_text() == BODY


def test_frozen_generated_input_drift_during_clang_is_rejected(tmp_path, toolchain):
    def drift(argv, **kwargs):
        completed = subprocess.run(argv, **kwargs)
        if "--target=armv7a-none-eabi" in argv:
            path = tmp_path / "build/generated/POUS.c"
            path.chmod(0o644)
            path.write_text(path.read_text() + "\n/* concurrent edit */\n")
        return completed
    result = compile_body(tmp_path, toolchain, runner=drift)
    assert not result["ok"] and result["stage"] == "integrity"


def test_dependency_not_snapshotted_before_compile_is_rejected(tmp_path, toolchain):
    def extra_dependency(argv, **kwargs):
        completed = subprocess.run(argv, **kwargs)
        if "--target=armv7a-none-eabi" in argv:
            path = tmp_path / "build/MAIN.d"
            path.write_text(path.read_text() + " /usr/include/unrecorded-host-header.h\n")
        return completed
    result = compile_body(tmp_path, toolchain, runner=extra_dependency)
    assert not result["ok"] and result["stage"] == "compilation-dependencies"
    assert "unsnapshotted dependency" in result["diagnostics"][0]["message"]


def test_generated_interface_guard_does_not_accept_extra_state_or_helper():
    header = probe._HEADER
    with pytest.raises(ValueError): probe.validate_generated(header + "int extra;", "")
    generated = probe._SIGNATURE + "evil(); } // PLCIDF_MAIN_body__()"
    with pytest.raises(ValueError): probe.validate_generated(header, generated)


@pytest.mark.parametrize("timeout", [0, True, 31, float("nan")])
def test_invalid_timeout_prevents_tool_invocation(tmp_path, timeout):
    result = probe.compile_matiec_probe(source=tmp_path / "missing.st", matiec_root=tmp_path,
                                         clang="/missing", output_dir=tmp_path / "build", timeout_s=timeout)
    assert not result["ok"] and not result["commands"] and not (tmp_path / "build").exists()
