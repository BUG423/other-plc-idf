"""Host-only object-worker contract tests; synthetic ELF and fake worker responses."""
import hashlib
import importlib.util
import ntpath
from pathlib import Path
import struct

import pytest

spec = importlib.util.spec_from_file_location('compile_vendor_st', Path(__file__).parents[1] / 'compile_vendor_st.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def arm_object():
    header = b'\x7fELF\x01\x01\x01' + b'\0'*9
    header += struct.pack('<HHIIIIIHHHHHH', 1, 40, 1, 0, 0, 52, 0x05000000, 52, 0, 0, 40, 4, 0)
    sections = struct.pack('<10I', *([0]*10))
    sections += struct.pack('<10I', 0, 1, 6, 0, 212, 2, 0, 0, 2, 0)
    sections += struct.pack('<10I', 0, 3, 0, 0, 214, 6, 0, 0, 1, 0)
    sections += struct.pack('<10I', 0, 2, 0, 0, 220, 32, 2, 1, 4, 16)
    return header + sections + b'\x70\x47' + b'\0MAIN\0' + b'\0'*16 + struct.pack('<IIIBBH', 1, 1, 2, 0x12, 0, 1)


@pytest.fixture
def valid_record(tmp_path, monkeypatch):
    compiler = next(iter(worker.COMPILERS))
    source, build = r'E:\frozen', r'E:\fresh'
    paths = {}
    def store(folder, name, content):
        path = tmp_path / folder / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        win = ntpath.join(source if folder == 'source' else build, name)
        paths[win] = path
        return {'name': name, 'path': win, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
    st = b'****AS*****\0' + b'\0'*20
    sources = [store('source', 'MAIN.st', st), store('source', 'MAIN.cl', b'')]
    store('build', 'MAIN.st', st); store('build', 'MAIN.cl', b'')
    obj = store('build', 'MAIN.o', arm_object())
    obj['elf'] = worker.inspect_arm_object(arm_object())
    rst = store('build', 'MAIN.rst', b'Success!\r\n')
    monkeypatch.setattr(worker, 'local_path', paths.__getitem__)
    record = {'schema': worker.SCHEMA, 'scope': 'vendor-object-only', 'ok': True,
              'status': 'verified', 'stage': 'complete', 'diagnostics': [],
              'device_access': False, 'gui_access': False, 'timed_out': False,
              'output_truncated': False, 'capture_complete': True, 'exit_code': 1,
              'build_directory': build, 'ipc': {'started': 1, 'done': 1, 'success': 1},
              'stdout': 'success--------------\r\n', 'stderr': '',
              'compiler': {'path': compiler, 'sha256': worker.COMPILERS[compiler]},
              'source_files': sources, 'object_files': [obj], 'result_file': rst}
    return record, compiler, source, build, paths


def test_verified_record_checks_actual_files_and_arm_main(valid_record):
    record, compiler, source, build, _ = valid_record
    worker._validate_record(record, compiler, source, build)


@pytest.mark.parametrize('change', [
    {'stdout': 'lexer error: syntax error, unexpected $end\nsuccess--------------'},
    {'stderr': 'error: bad input'}, {'exit_code': 0}, {'timed_out': True},
    {'capture_complete': False}, {'output_truncated': True}, {'scope': 'full-application'},
    {'ipc': {'started': 1, 'done': 1, 'success': True}},
    {'diagnostics': [{'message': 'an error'}]}, {'build_directory': r'E:\other'},
])
def test_success_markers_do_not_override_failure(valid_record, change):
    record, compiler, source, build, _ = valid_record
    record.update(change)
    with pytest.raises(ValueError):
        worker._validate_record(record, compiler, source, build)


@pytest.mark.parametrize('target', ['source', 'copy', 'object', 'result'])
def test_modified_files_invalidate_record(valid_record, target):
    record, compiler, source, build, paths = valid_record
    key = {'source': ntpath.join(source, 'MAIN.st'), 'copy': ntpath.join(build, 'MAIN.st'),
           'object': ntpath.join(build, 'MAIN.o'), 'result': ntpath.join(build, 'MAIN.rst')}[target]
    paths[key].write_bytes(b'changed')
    with pytest.raises(ValueError):
        worker._validate_record(record, compiler, source, build)


def test_object_path_cannot_escape_fresh_build(valid_record):
    record, compiler, source, build, _ = valid_record
    record['object_files'][0]['path'] = r'E:\other\MAIN.o'
    with pytest.raises(ValueError):
        worker._validate_record(record, compiler, source, build)


@pytest.mark.parametrize('data', [b'', arm_object()[:80], arm_object().replace(b'MAIN', b'FAKE'),
                                 arm_object()[:18] + b'\x03\x00' + arm_object()[20:]])
def test_wrong_or_truncated_object_is_rejected(data):
    with pytest.raises(ValueError):
        worker.inspect_arm_object(data)


@pytest.mark.parametrize('timeout', [True, 0, 31, float('nan'), float('inf')])
def test_invalid_timeout_fails_before_starting_windows(timeout):
    result = worker.compile_vendor_st(next(iter(worker.COMPILERS)), r'E:\frozen', r'E:\new', timeout_s=timeout)
    assert result['ok'] is False and result['stage'] == 'host'


@pytest.mark.parametrize('text', ['{"ok":true,"ok":false}', '{"x":NaN}', '{"x":1e999}', '[]'])
def test_ambiguous_helper_json_is_rejected(text):
    with pytest.raises(ValueError):
        worker._strict_json(text)


def test_arbitrary_executable_not_allowed():
    result = worker.compile_vendor_st(r'E:\other\StLang.exe', r'E:\frozen', r'E:\new')
    assert result['ok'] is False
    assert 'inspected' in result['diagnostics'][0]['message']


def test_wsl_drive_path_translation_and_unc_rejection():
    assert worker.windows_path('/mnt/e/frozen/input') == r'E:\frozen\input'
    with pytest.raises(ValueError):
        worker.windows_path(r'\\server\share\input')
