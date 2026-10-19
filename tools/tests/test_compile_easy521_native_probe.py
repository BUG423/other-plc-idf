"""Strict-subset/failure tests and opt-in actual Linux LLVM compilation.

PLCIDF_TEST_LLC identifies a trusted local Linux llc. Real tests compile ARM
objects, then separately execute the SAME IR through an x86 host object/GCC
harness. The latter proves host lowering behavior, never ARM/device execution.
No vendor binary, Windows process or PLC is used.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'native_probe', Path(__file__).resolve().parents[1] / 'compile_easy521_native_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
DECL = b'{"schema":"plcidf.vendor-main-source.v1","globals":{"COUNTER":"DINT"}}'
REAL_DECL = b'{"schema":"plcidf.vendor-main-source.v1","globals":{"g_rBESS_Pcmd":"REAL"}}'


class ParseTests(unittest.TestCase):
    def test_both_opaque_external_profiles_make_only_align1_promises(self):
        for body, declarations, profile, symbol, scalar in (
                (b'COUNTER := COUNTER + 1;', DECL, probe.PROFILE, 'COUNTER', 'i32'),
                (b'g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0;', REAL_DECL, probe.REAL_PROFILE, 'G_RBESS_PCMD', 'float')):
            ir = probe.emit_ir(probe.parse_probe(body, declarations, profile=profile))
            inspected = probe.inspect_alignment_ir(ir, symbol=symbol, scalar=scalar)
            self.assertEqual(inspected['external_alignment_bytes'], 1)
            self.assertFalse(inspected['access_atomic'])
            self.assertFalse(inspected['concurrent_writer_supported'])
            with self.assertRaisesRegex(ValueError, 'align1'):
                probe.inspect_alignment_ir(ir.replace(b'align 1', b'align 4', 1), symbol=symbol, scalar=scalar)

    def test_assembly_audit_refuses_word_or_vfp_external_access_and_helpers(self):
        ir = {'volatile_loads': 1, 'volatile_stores': 1}
        for access in ('vldr s0, [r0]', 'vstr s0, [r0]', 'ldr r1, [r0]',
                       'strh r1, [r0]', 'bl memcpy'):
            assembly = ('MAIN:\n ' + access + '\n.Lfunc_end0:\n').encode()
            with self.assertRaises(ValueError):
                probe.inspect_byte_transfer_assembly(assembly, ir_inspection=ir)
        byte_assembly = ('MAIN:\n' + ' ldrb r1, [r0]\n' * 4 + ' strb r1, [r0]\n' * 4
                         + ' bx lr\n.Lfunc_end0:\n').encode()
        result = probe.inspect_byte_transfer_assembly(byte_assembly, ir_inspection=ir)
        self.assertEqual(result['byte_loads'], 4)
        self.assertEqual(result['byte_stores'], 4)

    def test_complete_profile_accepts_case_and_nested_comments(self):
        source = b'(* %Q0 is only a comment (* nested *) *)\ncounter := COUNTER + 2; // tail\n'
        parsed = probe.parse_probe(source, DECL)
        self.assertEqual(parsed['increment_counts_per_scan'], 2)
        self.assertIsNone(parsed['task_period_ms'])
        self.assertIsNone(parsed['initial_value'])
        self.assertIsNone(parsed['retained'])

    def test_full_body_rejects_other_syntax_and_out_of_range_literals(self):
        for source in (
            'COUNTER := COUNTER + 0;', 'COUNTER := COUNTER + 32768;',
            'COUNTER := COUNTER + -1;', 'COUNTER := COUNTER + 1.0;',
            'COUNTER := COUNTER + DINT#1;', 'COUNTER := COUNTER + 1e0;',
            'COUNTER := COUNTER + 1; COUNTER := 0;',
            'COUNTER := COUNTER + 1; Y0 := TRUE;',
            'COUNTER := COUNTER + 1; garbage',
            'PROGRAM MAIN COUNTER := COUNTER + 1; END_PROGRAM',
            'COUNTER := COUNTER * 1;', 'COUNTER := OTHER + 1;',
            'COUNTER := COUNTER + (1);', 'COUNTER := COUNTER + 1',
            'IF TRUE THEN COUNTER := COUNTER + 1; END_IF;',
            'COUNTER := %MD0 + 1;', '', 'COUNTER := ;',
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                probe.parse_probe(source.encode(), DECL)

    def test_comments_strings_and_utf8_fail_closed(self):
        for source in (b'COUNTER := COUNTER + 1;\0', b'(* unclosed',
                       b"COUNTER := COUNTER + 1; 'hidden'", b'\xff',
                       b'COUNTER := COUNTER + 1;\n' + b' ' * probe.MAX_SOURCE):
            with self.subTest(source=source[:40]), self.assertRaises((ValueError, UnicodeError)):
                probe.parse_probe(source, DECL)

    def test_declaration_profile_is_exact(self):
        for globals_map in ({'COUNTER': 'INT'}, {'OTHER': 'DINT'},
                            {'COUNTER': 'DINT', 'OTHER': 'DINT'}, {}):
            data = json.dumps({'schema': 'plcidf.vendor-main-source.v1', 'globals': globals_map}).encode()
            with self.subTest(globals_map=globals_map), self.assertRaises(ValueError):
                probe.parse_probe(b'COUNTER := COUNTER + 1;', data)
        duplicate = b'{"schema":"plcidf.vendor-main-source.v1","globals":{"COUNTER":"DINT","COUNTER":"DINT"}}'
        with self.assertRaises(ValueError):
            probe.parse_probe(b'COUNTER := COUNTER + 1;', duplicate)

    def test_invalid_ir_profile_is_rejected(self):
        for step in (0, True, -1, 32768):
            with self.subTest(step=step), self.assertRaises(ValueError):
                probe.emit_ir({'profile': probe.PROFILE, 'increment_counts_per_scan': step})

    def test_non_arm_objects_are_rejected(self):
        for data in (b'', b'not an object', Path('/usr/bin/true').read_bytes()):
            with self.assertRaises(ValueError):
                probe.inspect_probe_object(data)

    def test_existing_real_profile_is_explicit_and_exact(self):
        source = b'g_rBESS_Pcmd := g_rBESS_Pcmd + 2.0;'
        parsed = probe.parse_probe(source, REAL_DECL, profile=probe.REAL_PROFILE)
        self.assertEqual(parsed['symbol'], 'G_RBESS_PCMD')
        self.assertEqual(parsed['type'], 'REAL')
        self.assertEqual(parsed['increment_real'], '2.0')
        with self.assertRaises(ValueError):
            probe.parse_probe(source, REAL_DECL)
        for body in (b'g_rBESS_Pcmd := g_rBESS_Pcmd + 3.0;',
                     b'g_rBESS_Pcmd := g_rBESS_Pcmd + 1;',
                     b'g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0; Y0 := TRUE;',
                     b'g_rBESS_Qcmd := g_rBESS_Qcmd + 1.0;'):
            with self.assertRaises(ValueError):
                probe.parse_probe(body, REAL_DECL, profile=probe.REAL_PROFILE)
        with self.assertRaises(ValueError):
            probe.parse_probe(source, DECL, profile=probe.REAL_PROFILE)


class FailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'body.st'
        self.source.write_bytes(b'COUNTER := COUNTER + 1;')
        self.globals = self.root / 'globals.json'
        self.globals.write_bytes(DECL)
        self.llc = self.root / 'llc'
        data = bytearray(64)
        data[:7] = b'\x7fELF\x02\x01\x01'
        struct.pack_into('<H', data, 18, 62)
        self.llc.write_bytes(data)
        self.llc.chmod(0o700)
        self.output = self.root / 'new output'
        self.calls = []

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if '--version' in argv:
            return subprocess.CompletedProcess(argv, 0, 'LLVM version test\n', '')
        return subprocess.CompletedProcess(argv, 1, '', 'synthetic compile failure')

    def compile(self, **kwargs):
        args = dict(source=self.source, globals_path=self.globals, llc=self.llc,
                    output_dir=self.output, runner=self.runner)
        args.update(kwargs)
        return probe.compile_probe(**args)

    def test_invalid_source_prevents_any_process_or_output(self):
        self.source.write_text('COUNTER := COUNTER + 1; Y0 := TRUE;')
        result = self.compile()
        self.assertFalse(result['ok'])
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [])

    def test_existing_output_is_never_reused(self):
        self.output.mkdir()
        marker = self.output / 'keep'
        marker.write_text('original')
        result = self.compile()
        self.assertFalse(result['ok'])
        self.assertEqual(marker.read_text(), 'original')
        self.assertEqual(self.calls, [])

    def test_symlink_input_and_output_are_rejected(self):
        source_link = self.root / 'linked.st'
        source_link.symlink_to(self.source)
        self.assertFalse(self.compile(source=source_link)['ok'])
        self.output.symlink_to(self.root, target_is_directory=True)
        self.assertFalse(self.compile()['ok'])
        self.assertEqual(self.calls, [])

    def test_timeout_is_bounded_and_never_retried(self):
        def runner(argv, **kwargs):
            self.calls.append((argv, kwargs))
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        result = self.compile(runner=runner, timeout_s=0.25)
        self.assertFalse(result['ok'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1]['timeout'], 0.25)
        self.assertTrue((self.output / 'probe-report.json').is_file())

    def test_failed_compile_preserves_frozen_source_and_diagnostics(self):
        result = self.compile()
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'compile')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((self.output / 'body.st').read_bytes(), self.source.read_bytes())
        self.assertIn('synthetic compile failure', result['command']['stderr'])
        self.assertFalse(result['source_compiled'])
        self.assertFalse(result['runtime_abi_verified'])
        self.assertFalse(result['downloadable'])
        self.assertEqual(json.loads((self.output / 'probe-report.json').read_text()), result)
        argv, kwargs = self.calls[1]
        self.assertNotIn(str(self.source), argv)
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertNotIn('shell', kwargs)

    def test_compile_timeout_is_not_replayed(self):
        def runner(argv, **kwargs):
            if '--version' in argv:
                return self.runner(argv, **kwargs)
            self.calls.append((argv, kwargs))
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        result = self.compile(runner=runner, timeout_s=0.5)
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'compile')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(result['command']['attempts'], 1)

    def test_zero_exit_with_diagnostics_is_rejected(self):
        def runner(argv, **kwargs):
            if '--version' in argv:
                return self.runner(argv, **kwargs)
            self.calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, '', 'warning: unexpected target override')
        result = self.compile(runner=runner)
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'compile')
        self.assertEqual(len(self.calls), 2)

    def test_invalid_timeout_and_non_elf_tool_are_rejected(self):
        for timeout in (True, float('nan'), float('inf'), 0, 31):
            with self.subTest(timeout=timeout):
                self.assertFalse(self.compile(timeout_s=timeout)['ok'])
        self.llc.write_bytes(b'#!/bin/sh\nexit 0\n')
        self.assertFalse(self.compile()['ok'])
        self.assertEqual(self.calls, [])


@unittest.skipUnless(os.environ.get('PLCIDF_TEST_LLC'), 'Set PLCIDF_TEST_LLC for actual LLVM compilation')
class RealLLVMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.llc = Path(os.environ['PLCIDF_TEST_LLC']).absolute()
        self.globals = self.root / 'globals.json'
        self.globals.write_bytes(DECL)

    def compile_step(self, step, **kwargs):
        source = self.root / f'plus{step}.st'
        source.write_text(f'COUNTER := COUNTER + {step};\n')
        result = probe.compile_probe(source=source, globals_path=self.globals,
                                     llc=self.llc, output_dir=self.root / f'build-{step}', **kwargs)
        return source, result

    def test_different_st_rules_produce_real_distinct_arm_objects(self):
        hashes = []
        for step in (1, 2, 32767):
            source, result = self.compile_step(step)
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['source_compiled'])
            self.assertFalse(result['runtime_abi_verified'])
            self.assertFalse(result['arm_code_executed'])
            self.assertFalse(result['downloadable'])
            self.assertEqual(result['parsed']['increment_counts_per_scan'], step)
            self.assertEqual(result['object']['inspection']['elf']['undefined_global_weak']['names'], ['COUNTER'])
            self.assertEqual(result['object']['inspection']['elf']['relocations']['entries'], 3)
            self.assertEqual((Path(result['output_directory']) / 'body.st').read_bytes(), source.read_bytes())
            hashes.append(result['object']['sha256'])
        self.assertEqual(len(set(hashes)), 3)

    def test_real_object_disassembly_has_byte_external_transfers_for_both_profiles(self):
        objdump = self.llc.with_name('llvm-objdump')
        if not objdump.is_file():
            self.skipTest('Adjacent llvm-objdump is required for independent object disassembly')
        for profile, body, declaration in (
                (probe.PROFILE, 'COUNTER := COUNTER + 1;', DECL),
                (probe.REAL_PROFILE, 'g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0;', REAL_DECL)):
            source = self.root / (profile + '.st')
            source.write_text(body)
            self.globals.write_bytes(declaration)
            result = probe.compile_probe(source=source, globals_path=self.globals, profile=profile,
                                         llc=self.llc, output_dir=self.root / profile)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['ir_alignment']['external_alignment_bytes'], 1)
            self.assertEqual(result['arm_assembly']['inspection']['nonbyte_general_register_accesses'], 0)
            text = subprocess.run([str(objdump), '-d', '--disassemble-symbols=MAIN', result['object']['path']],
                                  check=True, capture_output=True, text=True, timeout=10).stdout
            self.assertGreaterEqual(text.count('\tldrb'), 4)
            self.assertGreaterEqual(text.count('\tstrb'), 4)
            self.assertNotRegex(text, r'\t(?:vldr|vstr)(?:\.[a-z0-9]+)?\s+[^\n]*\[r[0-9]+')
            self.assertNotRegex(text, r'\t(?:ldr|str)(?:\.w)?\s+[^\n]*\[r[0-9]+')

    def test_source_and_ir_drift_after_real_compiler_are_rejected(self):
        for which in ('source', 'ir'):
            case = self.root / which
            case.mkdir()
            source = case / 'body.st'
            source.write_text('COUNTER := COUNTER + 1;')
            def runner(argv, **kwargs):
                completed = subprocess.run(argv, **kwargs)
                if '-filetype=obj' in argv:
                    changed = source if which == 'source' else case / 'build' / 'MAIN.ll'
                    changed.chmod(0o644)
                    changed.write_text('changed after compile')
                return completed
            result = probe.compile_probe(source=source, globals_path=self.globals,
                                         llc=self.llc, output_dir=case / 'build', runner=runner)
            self.assertFalse(result['ok'])
            self.assertEqual(result['stage'], 'verify')
            self.assertFalse(result['source_compiled'])

    def test_wrong_arm_identity_or_added_helper_is_rejected(self):
        _, result = self.compile_step(1)
        self.assertTrue(result['ok'], result)
        original = Path(result['object']['path']).read_bytes()
        wrong_arch = bytearray(original)
        struct.pack_into('<H', wrong_arch, 18, 62)
        wrong_eabi = bytearray(original)
        struct.pack_into('<I', wrong_eabi, 36, 0x04000000)
        added_helper = original.replace(b'COUNTER\0', b'HELPERS\0')
        self.assertNotEqual(added_helper, original)
        for data in (wrong_arch, wrong_eabi, added_helper):
            with self.assertRaises(ValueError):
                probe.inspect_probe_object(data)

    @unittest.skipUnless(shutil.which('gcc'), 'Host REAL semantic harness requires GCC')
    def test_existing_real_arm_profile_and_host_precision_boundaries(self):
        self.globals.write_bytes(REAL_DECL)
        hashes = []
        for step in (1, 2):
            source = self.root / f'real-plus{step}.st'
            source.write_text(f'g_rBESS_Pcmd := g_rBESS_Pcmd + {step}.0;')
            result = probe.compile_probe(source=source, globals_path=self.globals,
                                         profile=probe.REAL_PROFILE, llc=self.llc,
                                         output_dir=self.root / f'real-build-{step}')
            self.assertTrue(result['ok'], result)
            hashes.append(result['object']['sha256'])
            self.assertEqual(result['object']['inspection']['elf']['undefined_global_weak']['names'], ['G_RBESS_PCMD'])
            with self.assertRaises(ValueError):
                probe.inspect_probe_object(Path(result['object']['path']).read_bytes())
            output = Path(result['output_directory'])
            subprocess.run([str(self.llc), '-mtriple=x86_64-pc-linux-gnu', '-relocation-model=pic',
                            '-filetype=obj', str(output / 'MAIN.ll'), '-o', str(output / 'host.o')],
                           check=True, capture_output=True, text=True, timeout=10)
            harness = output / 'host.c'
            harness.write_text('#include <stdint.h>\n#include <stdio.h>\n#include <string.h>\n'
                               'float G_RBESS_PCMD; extern void MAIN(void);\n'
                               'int main(void){float starts[]={0.0f,-1.5f,16777216.0f,33554432.0f,0x1p-149f};\n'
                               'for(unsigned i=0;i<5;i++){G_RBESS_PCMD=starts[i];for(unsigned j=0;j<3;j++){\n'
                               'MAIN();uint32_t bits;memcpy(&bits,&G_RBESS_PCMD,4);printf("%08x\\n",bits);}}return 0;}\n')
            executable = output / 'host-runner'
            subprocess.run(['gcc', '-std=c11', '-Wall', '-Werror', str(harness), str(output / 'host.o'),
                            '-o', str(executable)], check=True, capture_output=True, text=True, timeout=10)
            observed = subprocess.run([str(executable)], check=True, capture_output=True, text=True, timeout=2).stdout.splitlines()
            expected = []
            for initial in (0.0, -1.5, 16777216.0, 33554432.0, 2.0**-149):
                value = initial
                for _ in range(3):
                    packed = struct.pack('<f', float(value) + step)
                    value = struct.unpack('<f', packed)[0]
                    expected.append(f'{struct.unpack("<I", packed)[0]:08x}')
            self.assertEqual(observed, expected)
        self.assertEqual(len(set(hashes)), 2)

    @unittest.skipUnless(shutil.which('gcc'), 'Host semantic harness requires GCC')
    def test_same_ir_executes_correctly_on_host_including_overflow(self):
        initial_values = [-2147483648, -1, 0, 41, 2147483647]
        for step in (1, 2, 32767):
            _, result = self.compile_step(step)
            self.assertTrue(result['ok'], result)
            output = Path(result['output_directory'])
            # Recompile identical IR for the host. No ARM execution is inferred.
            subprocess.run([str(self.llc), '-mtriple=x86_64-pc-linux-gnu', '-relocation-model=pic',
                            '-filetype=obj', str(output / 'MAIN.ll'), '-o', str(output / 'host.o')],
                           check=True, capture_output=True, text=True, timeout=10)
            harness = output / 'host.c'
            harness.write_text('#include <stdint.h>\n#include <stdio.h>\n#include <inttypes.h>\n'
                               'int32_t COUNTER; extern void MAIN(void);\n'
                               'int main(void) { int32_t starts[] = {INT32_MIN,-1,0,41,INT32_MAX};\n'
                               'for (unsigned i=0;i<5;i++) { COUNTER=starts[i];\n'
                               'for (unsigned j=0;j<3;j++) { MAIN(); printf("%" PRId32 "\\n",COUNTER); }} return 0; }\n')
            executable = output / 'host-runner'
            subprocess.run(['gcc', '-std=c11', '-Wall', '-Werror', str(harness), str(output / 'host.o'),
                            '-o', str(executable)], check=True, capture_output=True, text=True, timeout=10)
            observed = subprocess.run([str(executable)], check=True, capture_output=True,
                                      text=True, timeout=2).stdout.splitlines()
            expected = []
            for initial in initial_values:
                value = initial
                for _ in range(3):
                    value = (value + step + 2**31) % 2**32 - 2**31
                    expected.append(str(value))
            self.assertEqual(observed, expected)


if __name__ == '__main__':
    unittest.main()
