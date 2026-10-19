"""Synthetic fixed-interface tests and opt-in genuine LLVM/LLD pipeline tests.

No proprietary fixture is embedded. Synthetic hashes and GVT preamble are
explicitly substituted for tests; the private real-project experiment in report
27 separately exercises the unmodified production profile. No device is used.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bound_native', Path(__file__).resolve().parents[1] / 'bind_native_application.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
PREAMBLE = b'Synthetic-native-project-profile'.ljust(build._binder._variables.PREAMBLE_BYTES, b'\0')


def gvt_fixture(kind=32, initial='0'):
    def string(value):
        raw = value.encode('ascii')
        return bytes([len(raw)]) + raw
    return (PREAMBLE + struct.pack('<I', 1) + struct.pack('<II', kind, kind)
            + string('g_rBESS_Pcmd') + string(initial) + b'\1\1\0\0'
            + struct.pack('<I', 0x02100320) + string('synthetic REAL storage')
            + b'\xff\xfe\xff\0' + bytes(8))


class SyntheticProject(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.source_dir = self.root / 'sources'
        self.source_dir.mkdir()
        self.source = self.source_dir / 'body.st'
        self.source.write_text('g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0;')
        self.globals = self.source_dir / 'globals.json'
        self.globals.write_text('{"schema":"plcidf.vendor-main-source.v1","globals":{"g_rBESS_Pcmd":"REAL"}}')
        self.files = {name: ('Synthetic:' + name).encode() for name in build.PROJECT_FILES}
        self.files['变量表.gvt'] = gvt_fixture()
        self.files['VarNameAndAddr.csv'] = 'g_rBESS_Pcmd\t0X2100320\tsynthetic\t32\n'.encode('utf-16')
        values = [0xffffffff] * 28
        values[16], values[19], values[24] = 0, 1, 2
        self.files['ILIndex.idx'] = struct.pack('<II', 10000001, 1) + bytes(18) + struct.pack('<I28I', 28, *values)
        self.pins = {}
        self.install_files()
        self.pin_patch = patch.object(build, 'PROJECT_FILES', self.pins)
        self.pin_patch.start()
        self.addCleanup(self.pin_patch.stop)
        preamble_patch = patch.object(build._binder._variables, 'PREAMBLE_SHA256', hashlib.sha256(PREAMBLE).hexdigest())
        preamble_patch.start()
        self.addCleanup(preamble_patch.stop)
        self.library_dir = self.root / 'libs'
        self.library_dir.mkdir()
        self.syslib = self.library_dir / 'sys.o'
        self.stclib = self.library_dir / 'stc.o'
        self.syslib.write_bytes(b'Synthetic syslib')
        self.stclib.write_bytes(b'Synthetic stclib')
        self.library_pins = {'syslib': hashlib.sha256(self.syslib.read_bytes()).hexdigest(),
                             'stclib': hashlib.sha256(self.stclib.read_bytes()).hexdigest()}
        lib_patch = patch.object(build, 'LIBRARIES', self.library_pins)
        lib_patch.start()
        self.addCleanup(lib_patch.stop)

    def install_files(self):
        self.pins.clear()
        for relative, raw in self.files.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.pins[relative] = hashlib.sha256(raw).hexdigest()

    def call(self, **kwargs):
        args = dict(source=self.source, globals_path=self.globals, project_dir=self.project,
                    syslib=self.syslib, stclib=self.stclib, llc='/usr/bin/true', lld='/not/invoked',
                    output_dir=self.root / 'candidate')
        args.update(kwargs)
        return build.build_native_application(**args)

    def test_address_comes_from_gvt_and_keeps_unknown_runtime_fields(self):
        result = build.inspect_existing_interface(self.files)
        self.assertEqual(result['binding']['address'], 0x02100320)
        self.assertEqual(result['binding']['iec_type'], 'REAL')
        self.assertEqual(result['binding']['width_bits'], 32)
        self.assertEqual(result['binding']['sinfo_type'], 8)
        self.assertIsNone(result['retained'])
        self.assertIsNone(result['task_period_ms'])
        self.assertFalse(result['task_runtime_call_verified'])

    def test_missing_or_changed_interface_is_rejected(self):
        missing = dict(self.files)
        del missing['Config.sdt']
        with self.assertRaises(ValueError):
            build.inspect_existing_interface(missing)
        changed = dict(self.files)
        changed['VarsInfo.vi'] += b'changed retention config'
        with self.assertRaises(ValueError):
            build.inspect_existing_interface(changed)

    def test_csv_must_agree_and_never_supplies_an_alternate_address(self):
        self.files['VarNameAndAddr.csv'] = 'g_rBESS_Pcmd\t0X2100990\tsynthetic\t32\n'.encode('utf-16')
        self.install_files()
        with self.assertRaisesRegex(ValueError, 'disagreement'):
            build.inspect_existing_interface(self.files)

    def test_wrong_scalar_type_or_initial_value_is_rejected(self):
        for kind, initial in ((4, '0'), (32, '1')):
            self.files['变量表.gvt'] = gvt_fixture(kind, initial)
            self.files['VarNameAndAddr.csv'] = f'g_rBESS_Pcmd\t0X2100320\tsynthetic\t{16 if kind == 4 else 32}\n'.encode('utf-16')
            self.install_files()
            with self.assertRaisesRegex(ValueError, 'type, width or serialized initial'):
                build.inspect_existing_interface(self.files)

    def test_task_table_is_explicitly_checked(self):
        malformed = bytearray(self.files['ILIndex.idx'])
        struct.pack_into('<I', malformed, 30 + 19 * 4, 99)
        self.files['ILIndex.idx'] = bytes(malformed)
        self.install_files()
        with self.assertRaisesRegex(ValueError, 'task/entry'):
            build.inspect_existing_interface(self.files)

    def test_invalid_st_prevents_any_process_or_output(self):
        self.source.write_text('g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0; Y0 := TRUE;')
        def forbidden(*args, **kwargs):
            self.fail('Invalid ST must not reach a process')
        result = self.call(compiler_runner=forbidden, linker_runner=forbidden)
        self.assertFalse(result['ok'])
        self.assertFalse((self.root / 'candidate').exists())

    def test_changed_library_and_existing_output_are_refused(self):
        self.syslib.write_bytes(b'wrong library')
        result = self.call()
        self.assertFalse(result['ok'])
        self.assertFalse((self.root / 'candidate').exists())
        self.syslib.write_bytes(b'Synthetic syslib')
        out = self.root / 'candidate'
        out.mkdir()
        (out / 'keep').write_text('original')
        self.assertFalse(self.call()['ok'])
        self.assertEqual((out / 'keep').read_text(), 'original')

    def test_compile_failure_never_reaches_linker_or_claims_source_success(self):
        calls = []
        def compiler(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, 'LLVM version synthetic\n', '') if '--version' in argv else subprocess.CompletedProcess(argv, 1, '', 'synthetic failure')
        def forbidden(*args, **kwargs):
            self.fail('Failed compilation must not reach linker')
        result = self.call(compiler_runner=compiler, linker_runner=forbidden)
        self.assertFalse(result['ok'])
        self.assertFalse(result['source_compilation_verified'])
        self.assertFalse(result['downloadable'])
        self.assertEqual(len(calls), 2)
        saved = json.loads((self.root / 'candidate' / 'candidate-report.json').read_text())
        self.assertEqual(saved, result)

    def test_invalid_timeout_does_not_create_output(self):
        for timeout in (True, float('nan'), float('inf'), 0, 31):
            self.assertFalse(self.call(timeout_s=timeout)['ok'])
        self.assertFalse((self.root / 'candidate').exists())


@unittest.skipUnless(os.environ.get('PLCIDF_TEST_LLC') and os.environ.get('PLCIDF_TEST_LLD'),
                     'Set PLCIDF_TEST_LLC and PLCIDF_TEST_LLD for real synthetic pipeline')
class RealNativePipeline(unittest.TestCase):
    install_files = SyntheticProject.install_files
    call = SyntheticProject.call

    def setUp(self):
        SyntheticProject.setUp(self)
        self.llc = str(Path(os.environ['PLCIDF_TEST_LLC']).absolute())
        self.lld = str(Path(os.environ['PLCIDF_TEST_LLD']).absolute())
        for name, path in (('SBR_001', self.project / 'STTemp/SBR_001.o'),
                           ('test_syslib_entry', self.syslib), ('test_stclib_entry', self.stclib)):
            ir = self.root / (name + '.ll')
            ir.write_text(f'define void @{name}() nounwind {{ ret void }}\n')
            subprocess.run([self.llc, '-mtriple=thumbv7-none-eabi', '-filetype=obj',
                            str(ir), '-o', str(path)], check=True, capture_output=True, text=True, timeout=10)
        self.files['STTemp/SBR_001.o'] = (self.project / 'STTemp/SBR_001.o').read_bytes()
        self.pins['STTemp/SBR_001.o'] = hashlib.sha256(self.files['STTemp/SBR_001.o']).hexdigest()
        self.library_pins.update(syslib=hashlib.sha256(self.syslib.read_bytes()).hexdigest(),
                                 stclib=hashlib.sha256(self.stclib.read_bytes()).hexdigest())

    def test_two_real_source_compile_link_and_bind_chains_differ(self):
        hashes = []
        for step in (1, 2):
            self.source.write_text(f'g_rBESS_Pcmd := g_rBESS_Pcmd + {step}.0;')
            result = self.call(llc=self.llc, lld=self.lld, output_dir=self.root / f'candidate-{step}')
            self.assertTrue(result['ok'], result)
            self.assertTrue(result['source_compilation_verified'])
            self.assertTrue(result['existing_allocation_verified'])
            self.assertTrue(result['source_to_bound_objects_verified'])
            self.assertFalse(result['runtime_abi_verified'])
            self.assertFalse(result['complete_application'])
            self.assertFalse(result['downloadable'])
            self.assertEqual(len(result['outputs']), 2)
            for entry in result['outputs']:
                binding = entry['mutation']['bindings'][0]
                self.assertEqual(binding['after']['st_value'], 0x02100320)
                self.assertEqual(binding['after']['st_info'], 0x18)
                self.assertEqual(binding['after']['st_shndx'], 0)
                self.assertTrue(entry['mutation']['all_nonselected_bytes_unchanged'])
            hashes.append(result['outputs'][-1]['sha256'])
        self.assertEqual(len(set(hashes)), 2)

    def test_original_project_drift_during_real_build_is_rejected(self):
        def runner(argv, **kwargs):
            result = subprocess.run(argv, **kwargs)
            if '-filetype=obj' in argv:
                (self.project / 'Config.sdt').write_bytes(b'changed during compile')
            return result
        result = self.call(llc=self.llc, lld=self.lld, compiler_runner=runner)
        self.assertFalse(result['ok'])
        self.assertEqual(result['stage'], 'verify')
        self.assertFalse(result['source_to_bound_objects_verified'])


if __name__ == '__main__':
    unittest.main()
