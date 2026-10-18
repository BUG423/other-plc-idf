"""Offline Easy521 diagnostics describe files and gaps, never a live PLC."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PureWindowsPath
import socket
import struct
import subprocess

import pytest

from plc_idf import cli, doctor
from plc_idf.autoshop_vendor import AutoShopVendorAdapter
from plc_idf.config import load_config


def version_blob(version=(4, 12, 0, 0)):
    data = bytearray(92)
    struct.pack_into('<HHH', data, 0, 92, 52, 0)
    key = 'VS_VERSION_INFO\0'.encode('utf-16-le')
    data[6:6 + len(key)] = key
    struct.pack_into('<IIII', data, 40, 0xFEEF04BD, 0x10000,
                     (version[0] << 16) | version[1], (version[2] << 16) | version[3])
    return data


def pe_image():
    data = bytearray(1024)
    data[:2] = b'MZ'; struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<H', data, 134, 1); struct.pack_into('<H', data, 148, 224)
    struct.pack_into('<H', data, 152, 0x10B)
    struct.pack_into('<I', data, 244, 16)
    struct.pack_into('<II', data, 264, 0x1000, 512)
    data[376:384] = b'.rsrc\0\0\0'
    struct.pack_into('<IIII', data, 384, 512, 0x1000, 512, 512)
    for offset, key, child in ((0, 16, 0x80000020), (32, 1, 0x80000040), (64, 0x409, 0x60)):
        struct.pack_into('<H', data, 512 + offset + 14, 1)
        struct.pack_into('<II', data, 512 + offset + 16, key, child)
    struct.pack_into('<IIII', data, 608, 0x1100, 92, 0, 0)
    data[768:860] = version_blob()
    return data


@pytest.fixture
def offline_cfg(tmp_path, monkeypatch, clean_env):
    root = tmp_path / 'project'; root.mkdir()
    windows = tmp_path / 'windows'; windows.mkdir()
    def mapped(value):
        win = PureWindowsPath(str(value))
        return windows.joinpath(*win.parts[1:]) if win.drive == 'E:' else Path(value)
    monkeypatch.setattr(doctor, '_local_path', mapped)
    exe = mapped(r'E:\Unit\AutoShop.exe'); exe.parent.mkdir(); exe.write_bytes(pe_image())
    compiler = mapped(r'E:\Unit\STLang\StLang.exe'); compiler.parent.mkdir(); compiler.write_bytes(b'unit compiler, not executable')
    (compiler.parent / 'Kernel.dll').write_bytes(b'unit dll, never loaded')
    mapped(r'E:\Unit\Project').mkdir()
    for name in ('powershell', 'usbipd', 'configured-ui.ps1'):
        (root / name).write_text('unit local prerequisite')
    helpers = root / 'plc/tools'; helpers.mkdir(parents=True)
    digest = hashlib.sha256(compiler.read_bytes()).hexdigest()
    (helpers / 'compile_vendor_st.ps1').write_text("'E:\\Unit\\STLang\\StLang.exe'='" + digest + "'")
    cfg_file = root / '.plcidf.toml'
    cfg_file.write_text('[paths]\nproject_root="' + root.as_posix() + '"\n[bridge]\n'
                        "windows_project='E:\\Unit\\Project'\nautoshop_exe='E:\\Unit\\AutoShop.exe'\n"
                        + 'powershell_exe="' + str(root / 'powershell') + '"\n'
                        + 'tools_ps1_windows="' + str(root / 'configured-ui.ps1') + '"\n'
                        + '[device]\nusbipd_exe="' + str(root / 'usbipd') + '"\n')
    monkeypatch.setenv('PLCIDF_ROOT', str(root))
    def forbidden(*args, **kwargs):
        pytest.fail('offline doctor attempted a process, network or live device observation')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(AutoShopVendorAdapter, 'observe_connection', forbidden)
    return load_config(cfg_file), root, windows


def test_capabilities_separate_source_object_transfer_binary_and_runtime(offline_cfg):
    cfg, root, _ = offline_cfg
    before = sorted(path.relative_to(root) for path in root.rglob('*'))
    report = doctor.collect_diagnostics(cfg)
    native = report['easy521']
    assert native['historical_results_used'] is False and native['device_actions_performed'] == []
    assert native['current_device'] == 'not-observed' and not report['hardware_checked']
    caps = native['capabilities']
    assert set(caps) == {'source_build', 'vendor_object_only', 'download', 'source_consistency',
                         'device_binary_identity', 'runtime', 'monitor_vendor_usb'}
    assert caps['source_build']['local_prerequisites']['ok'] is True
    assert caps['vendor_object_only']['local_prerequisites']['ok'] is True, caps['vendor_object_only']['local_prerequisites']
    assert not caps['vendor_object_only']['complete_application']
    assert not caps['device_binary_identity']['implemented']
    assert not caps['source_consistency']['device_binary_identity_verified']
    assert not caps['runtime']['cpu_execution_verified'] and not caps['runtime']['control_implemented']
    assert not caps['monitor_vendor_usb']['connection_verified']
    release = caps['download']['prepared_release']
    assert release['implemented'] and release['release_hash'] is None
    assert release['device_binary_binding'] == 'unverified'
    assert native['verification_levels'] == {'local_candidate': 'not-observed', 'project': 'not-observed',
                                             'binary': 'unverified', 'runtime': 'unverified'}
    assert all(item['current_verification'] == 'not-observed' and item['missing_conditions']
               and item['executed_by_doctor'] is False for item in caps.values())
    assert sorted(path.relative_to(root) for path in root.rglob('*')) == before


def test_on_disk_version_is_not_compared_as_a_live_window_version(offline_cfg):
    cfg, _, _ = offline_cfg
    vendor = doctor.collect_diagnostics(cfg)['easy521']['vendor']
    assert vendor['on_disk_autoshop']['file_version'] == '4.12.0.0'
    assert vendor['on_disk_autoshop']['expected_ui_title_version'] == '4.12.0.2'
    assert vendor['on_disk_autoshop']['compatibility_verified'] is False
    assert vendor['running_autoshop_version'] is None and vendor['plc_firmware_version'] is None
    assert vendor['current_layout_verified'] is False and vendor['required_layout']['screen'] == (3840, 2160)


def test_compiler_pin_mismatch_is_a_specific_missing_local_condition(offline_cfg):
    cfg, _, windows = offline_cfg
    (windows / 'Unit/STLang/StLang.exe').write_bytes(b'modified compiler')
    native = doctor.collect_diagnostics(cfg)['easy521']
    compiler = native['vendor']['object_compilers'][0]
    assert compiler['hash_matches'] is False and compiler['sha256'] != compiler['expected_sha256']
    cap = native['capabilities']['vendor_object_only']
    assert cap['status'] == 'missing-local-prerequisites'
    assert 'pinned_compiler_and_kernel' in {item['code'] for item in cap['missing_conditions']}


def test_missing_native_reader_does_not_disable_modbus_or_claim_native_ready(offline_cfg, monkeypatch):
    cfg, root, _ = offline_cfg
    original = doctor.runtime_asset
    monkeypatch.setattr(doctor, 'runtime_asset', lambda base, relative:
                        root / 'absent-reader.ps1' if relative.endswith('read_autoshop_online.ps1') else original(base, relative))
    report = doctor.collect_diagnostics(cfg)
    assert report['capabilities']['monitor']['read_function_codes'] == [1, 2, 3, 4]
    cap = report['easy521']['capabilities']['monitor_vendor_usb']
    assert not cap['local_prerequisites']['checks']['native_reader']
    assert cap['status'] == 'missing-local-prerequisites' and not cap['connection_verified']


def test_object_recommendation_uses_actual_bundled_workers_and_no_deploy(offline_cfg):
    cfg, _, _ = offline_cfg
    cap = doctor.collect_diagnostics(cfg)['easy521']['capabilities']['vendor_object_only']
    assert Path(cap['prepare_argv'][1]).is_file() and Path(cap['compile_argv'][1]).is_file()
    assert '--source' in cap['prepare_argv'] and '--globals' in cap['prepare_argv']
    assert '--input-dir' in cap['compile_argv'] and '--powershell-exe' in cap['compile_argv']
    assert 'flash' not in cap['compile_argv'] and '--execute' not in cap['compile_argv']
    assert cap['globals_schema']['globals'] == {'COUNTER': 'DINT'}


def test_readable_text_explains_primary_native_backend_and_missing_evidence(offline_cfg):
    cfg, _, _ = offline_cfg
    text = doctor.render_diagnostics(doctor.collect_diagnostics(cfg))
    assert 'source_build' in text and 'device_binary_identity' in text
    assert '未核验' in text and 'device_executable_readback' in text


@pytest.mark.parametrize('option', ['--toolchain-dir', '--state-dir'])
def test_easy_doctor_rejects_empty_cross_target_paths(offline_cfg, option, capsys):
    cfg, _, _ = offline_cfg
    assert cli.main(['doctor', '--config', str(cfg.source), option, '', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['diagnostics'][0]['code'] == 'E_ARGUMENT'


def test_pe_version_reads_only_an_actual_rt_version_resource(tmp_path):
    path = tmp_path / 'version.exe'; path.write_bytes(pe_image())
    assert doctor._pe_file_version(doctor._file(path))['file_version'] == '4.12.0.0'
    path.write_bytes(b'not a PE file' + b'\xbd\x04\xef\xfe' + bytes(100))
    assert doctor._pe_file_version(doctor._file(path))['file_version'] is None


@pytest.mark.parametrize('change', ['signature', 'directory', 'large_tree', 'value', 'conflict'])
def test_malformed_or_ambiguous_pe_versions_stay_unverified(tmp_path, change):
    image = pe_image()
    if change == 'signature': image[128:132] = b'NOPE'
    if change == 'directory': struct.pack_into('<I', image, 264, 0xFFFFFFF0)
    if change == 'large_tree': struct.pack_into('<H', image, 526, 257)
    if change == 'value': struct.pack_into('<I', image, 808, 0)
    if change == 'conflict':
        struct.pack_into('<H', image, 590, 2)
        struct.pack_into('<II', image, 600, 0x40C, 0x70)
        struct.pack_into('<IIII', image, 624, 0x1180, 92, 0, 0)
        image[896:988] = version_blob((4, 11, 0, 0))
    path = tmp_path / 'bad.exe'; path.write_bytes(image)
    result = doctor._pe_file_version(doctor._file(path))
    assert result['status'] == 'unverified' and result['file_version'] is None


def test_large_metadata_hash_input_is_not_loaded(tmp_path):
    path = tmp_path / 'large'; path.write_bytes(bytes(16))
    result = doctor._hash_file(doctor._file(path), maximum=8)
    assert result['status'] == 'unverified' and result['sha256'] is None
