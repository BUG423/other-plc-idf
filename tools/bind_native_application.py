#!/usr/bin/env python3
"""Build a fixed existing Easy521 REAL-memory candidate with native Linux tools.

This is a source -> new MAIN.o -> two relocatable links -> existing SInfo binding
pipeline, not a full application packager. The one supported baseline is the
inspected BESS_E521_DEV interface in research 17/22/27. Exact baseline hashes
protect its existing allocation, task records, symbol ids and opaque retention/
period configuration. These hashes do not establish its historical build origin
or identity with any online PLC. No new variable allocator is implemented.

Only g_rBESS_Pcmd:REAL += 1.0/2.0 is compiled. Address/type come from same-project
GVT, all GVT addresses/widths are checked against its CSV, and only that global's
st_value/st_info fields are patched. Original SBR_001 and two pinned libraries
remain unchanged. Complete FOID/configuration assembly and device/runtime ABI
are not verified. No Windows, network, USB, download or CPU-control API exists.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys


def _load(filename):
    spec = importlib.util.spec_from_file_location('_bound_native_' + filename[:-3], Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_native = _load('compile_easy521_native_probe.py')
_link = _load('link_vendor_application.py')
_binder = _load('bind_vendor_globals.py')
PROFILE = 'bess-easy521-existing-pcmd-v1'
# Whole-file identities preserve precisely the existing allocation/task profile.
# Their source paths and independent parsing are published in research 27. No
# constant below is used as a replacement address or generated configuration.
PROJECT_FILES = {
    '变量表.gvt': '63700c1f9b5a4b1201201442b1d614344d9b6346fd2ad913b2c22796560b95c5',
    'VarNameAndAddr.csv': '75e89e3c7ef94dcb53129f4e4b1b0ef4bf72947ac4296633276c0f0ac29e44fe',
    'VarsInfo.vi': '9c7cab9b340bbc5e5a352caa6fa54c3c8514b0c259b9691908995ca48ea1df95',
    'ReAllocVarsInfo.vi': 'df3f619804a92fdb4057192dc43dd748ea778adc52bc498ce80524c014b81119',
    'ILIndex.idx': 'd56b4f7edb98b3da1f9ef85166c5697fe8ae6c6c6987b59dc346e85c52cbde21',
    'Output.prg': '3bfe2324eba14f55199d5bb4d0ee72d265a7453fc78b0c6e66d265ae21adb63b',
    'Output.sdt': 'c07a72afae6f4cac3e87308bab17d0eec1658a764b7e67102de77ec984ca863b',
    'Config.sdt': 'af9e01cb471c8c7d6b4c41605192adeed28f03f6b3570675bea97ae24760f664',
    'Temp/userprog.foid': '02d2a2c53d8f40fb1efcd97887eeddfe2a7c003e65b6f9a3747e13c73362167c',
    'Temp/varsinfo.foid': 'af4e636e0119891cbf6be9db8686ce0fc775025556509a4fb7a6c932bcc0fb1d',
    'Temp/symbol.sym': 'ded220922669b1c5f664cd09c39348ffc816de98fade506a74a0ac32672bef92',
    'STTemp/SBR_001.o': '0427a175b83bd445b632a449e92fde9276b2a703737e7ceadaca08d4f1d6d411',
}
LIBRARIES = {
    'syslib': '2fde5706b01135ac15db5f17b6847dd361894c909ecaa6a3cf9cca2f132782d2',
    'stclib': '047dad6fa718170625f0a78d927df304f3a6f681970009324a7179397958d95e',
}
MAX_INPUT = 4 * 1024 * 1024


def inspect_existing_interface(files: dict[str, bytes]) -> dict:
    """Validate the fixed project interface and derive its existing REAL binding.

    files maps the twelve required relative names to bounded raw bytes. Every
    hash must match the observed profile; GVT remains the sole address source.
    Returns binding provenance, task-index values and unchanged-config checks.
    Retention meaning, period units and runtime task-call semantics are unknown;
    their serialized inputs are pinned, not decoded into invented values.
    """
    if set(files) != set(PROJECT_FILES):
        raise ValueError('The complete fixed project interface file set is required')
    for name, expected in PROJECT_FILES.items():
        if len(files[name]) > MAX_INPUT or _native._sha(files[name]) != expected:
            raise ValueError('Existing project interface changed: ' + name)
    parsed = _binder._variables.parse_gvt(files['变量表.gvt'])
    csv = _binder._variables.parse_address_csv(files['VarNameAndAddr.csv'])
    for row in parsed['records']:
        exported = csv.get(row['name'].casefold())
        if not exported or (row['address'], row['width_bits']) != (exported['address'], exported['width_bits']):
            raise ValueError('GVT/CSV disagreement: ' + row['name'])
    selected = [r for r in parsed['records'] if r['name'] == 'g_rBESS_Pcmd']
    if len(selected) != 1:
        raise ValueError('Existing Pcmd global is missing or duplicated')
    row = selected[0]
    if (row['type'], row['width_bits'], row['initial_text']) != ('REAL', 32, '0'):
        raise ValueError('Pcmd type, width or serialized initial value changed')
    # The observed main/task table has 26 header bytes, then DWORD count 28.
    # Source: matching ILIndex and userprog segment in config research 28.
    indexes = files['ILIndex.idx']
    if (len(indexes) != 142 or struct.unpack_from('<II', indexes) != (10000001, 1)
            or indexes[8:26] != bytes(18) or struct.unpack_from('<I', indexes, 26)[0] != 28):
        raise ValueError('Unknown existing ILIndex header')
    values = list(struct.unpack_from('<28I', indexes, 30))
    expected = [0xffffffff] * 28
    expected[16], expected[19], expected[24] = 0, 1, 2
    if values != expected:
        raise ValueError('Existing task/entry index table changed')
    return {'gvt_records_checked': parsed['count'], 'csv_rows_checked': parsed['count'],
            'binding': {'gvt_name': row['name'], 'elf_name': 'G_RBESS_PCMD',
                        'iec_type': 'REAL', 'width_bits': 32, 'address': row['address'],
                        'gvt_record_offset': row['record_offset'], 'gvt_address_offset': row['address_offset'],
                        'csv_line': csv[row['name'].casefold()]['line'], 'sinfo_type': 8},
            'initial_text': row['initial_text'], 'opaque_gvt_flags_hex': '01010000',
            'retained': None, 'task_period_ms': None,
            'retention_check': 'exact-existing-GVT-Config.sdt-VarsInfo.vi-hashes',
            'period_check': 'exact-existing-Config.sdt-Output.sdt-ILIndex.idx-hashes',
            'index_values': values, 'entry_symbols_preserved': ['MAIN', 'SBR_001'],
            'task_runtime_call_verified': False, 'baseline_build_origin_verified': False}


def build_native_application(*, source, globals_path, project_dir, syslib, stclib,
                             llc, lld, output_dir, timeout_s=10.0,
                             compiler_runner=subprocess.run, linker_runner=subprocess.run) -> dict:
    """Produce new source-linked and existing-global-bound ARM objects offline.

    source/globals_path select the fixed REAL ST profile. project_dir supplies
    the exact twelve existing interface files; syslib/stclib select the pinned
    library branch. llc/lld are trusted Linux executables. output_dir must be
    new, outside all original input directories, with an existing parent. Each
    process is bounded by timeout_s (0.1..30 seconds), never replayed. Originals
    are read-only; the new directory preserves frozen inputs, compiler/link
    reports, bound objects and an aggregate candidate-report.json on failure.

    Returns status existing-interface-native-candidate only after a real compile,
    both links, per-symbol binding and complete hash rechecks. This verifies the
    local source/object/record association, not an online PLC identity, complete
    application packaging, historical baseline build or runtime compatibility.
    All success paths keep downloadable/runtime/device flags false.
    """
    out = Path(output_dir).absolute()
    report = {'schema': 'plcidf.native-bound-application.v1', 'profile': PROFILE,
              'ok': False, 'status': 'failed', 'stage': 'preflight', 'diagnostics': [],
              'source_compilation_verified': False, 'existing_allocation_verified': False,
              'source_to_bound_objects_verified': False, 'runtime_abi_verified': False,
              'complete_application': False, 'downloadable': False, 'device_access': False,
              'windows_access': False, 'target_identity': None, 'outputs': []}
    created = False
    snapshots = {}
    frozen = {}
    try:
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not 0.1 <= timeout_s <= 30):
            raise ValueError('timeout_s must be finite and between 0.1 and 30')
        tool_paths = {Path(p).absolute() for p in (
            __file__, _native.__file__, _native._prepare.__file__,
            _native._inspect.__file__, _link.__file__, _binder.__file__,
            _binder._variables.__file__)}
        tool_bytes = {p: _native._read(p, MAX_INPUT) for p in tool_paths}
        project = Path(project_dir).absolute()
        raw_files = {}
        for relative in PROJECT_FILES:
            p = project / relative
            raw_files[relative] = snapshots[p] = _native._read(p, MAX_INPUT)
        interface = inspect_existing_interface(raw_files)
        lib_paths = {'syslib': Path(syslib).absolute(), 'stclib': Path(stclib).absolute()}
        for role, p in lib_paths.items():
            data = snapshots[p] = _native._read(p, MAX_INPUT)
            if _native._sha(data) != LIBRARIES[role]:
                raise ValueError('Library branch/profile mismatch: ' + role)
        source_path, globals_file = Path(source).absolute(), Path(globals_path).absolute()
        source_bytes = snapshots[source_path] = _native._read(source_path, _native.MAX_SOURCE)
        globals_bytes = snapshots[globals_file] = _native._read(globals_file, _native.MAX_SOURCE)
        _native.parse_probe(source_bytes, globals_bytes, profile=_native.REAL_PROFILE)
        if len({p.resolve() for p in snapshots}) != len(snapshots):
            raise ValueError('Input path aliases are unsupported')
        if out.exists() or out.is_symlink() or not out.parent.is_dir():
            raise ValueError('Output directory must be new with an existing parent')
        if any(out == p.parent or p.parent in out.parents or out in p.parents for p in snapshots):
            raise ValueError('Output must be separate from every original input directory')
        for item in out.parents:
            if item.is_symlink():
                raise ValueError('Output may not contain symlink ancestors')
        out.mkdir()
        created = True
        for relative, data in raw_files.items():
            target = out / 'project-interface' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') as stream:
                stream.write(data)
            target.chmod(0o444)
            frozen[target] = data
        library_dir = out / 'libraries'
        library_dir.mkdir()
        for role, original in lib_paths.items():
            target = library_dir / (role + '.o')
            target.write_bytes(snapshots[original])
            target.chmod(0o444)
            frozen[target] = snapshots[original]
        report.update(interface=interface, project_directory=str(project),
                      inputs=[{'path': str(p), 'bytes': len(b), 'sha256': _native._sha(b)} for p, b in snapshots.items()],
                      output_directory=str(out), new_source_unit='MAIN',
                      reused_precompiled_units=['SBR_001', '_SysLib.o', '_STCLib.o'],
                      tool_components=[{'path': str(p), 'sha256': _native._sha(b)}
                                       for p, b in sorted(tool_bytes.items())])
        report['stage'] = 'compile'
        compiled = _native.compile_probe(source=source_path, globals_path=globals_file,
                                        profile=_native.REAL_PROFILE, llc=llc,
                                        output_dir=out / 'native-main', timeout_s=timeout_s,
                                        runner=compiler_runner)
        report['compiler_report'] = str(out / 'native-main' / 'probe-report.json')
        if not compiled['ok']:
            raise ValueError('Native compiler failed: ' + str(compiled['diagnostics']))
        if [r['sha256'] for r in compiled['inputs']] != [_native._sha(source_bytes), _native._sha(globals_bytes)]:
            raise ValueError('Compiler consumed different source/declarations than the captured batch')
        report['stage'] = 'link'
        linked = _link.link_application(pous=[out / 'native-main' / 'MAIN.o',
                                              out / 'project-interface' / 'STTemp' / 'SBR_001.o'],
                                        syslib=library_dir / 'syslib.o', stclib=library_dir / 'stclib.o',
                                        lld=lld, output_dir=out / 'linked', timeout_s=timeout_s,
                                        runner=linker_runner)
        report['link_report'] = str(out / 'linked' / 'link-report.json')
        if not linked['ok']:
            raise ValueError('Native two-stage link failed: ' + str(linked.get('error')))
        if linked['inputs'][0]['inspection']['sha256'] != compiled['object']['sha256']:
            raise ValueError('Compiled MAIN and ordered link input identity disagree')
        if linked['inputs'][1]['inspection']['sha256'] != PROJECT_FILES['STTemp/SBR_001.o']:
            raise ValueError('Linked existing sub-POU differs from the pinned interface')
        report['stage'] = 'bind'
        bound_dir = out / 'bound'
        bound_dir.mkdir()
        for entry, name in zip(linked['outputs'], ('stuserprog.o', 'stprog.o')):
            data = _native._read(Path(entry['path']), _binder.MAX_OBJECT)
            if _native._sha(data) != entry['inspection']['sha256']:
                raise ValueError('Link output changed before binding')
            changed, mutations = _binder._patch_object(data, [interface['binding']])
            destination = bound_dir / name
            destination.write_bytes(changed)
            destination.chmod(0o444)
            frozen[destination] = changed
            report['outputs'].append({'path': str(destination), 'input_sha256': _native._sha(data),
                                      'sha256': _native._sha(changed), 'bytes': len(changed),
                                      'mutation': mutations, 'elf': _binder._arm_object(changed)})
        report['stage'] = 'verify'
        for p, data in {**snapshots, **frozen, **tool_bytes}.items():
            if _native._read(p, max(MAX_INPUT, len(data))) != data:
                raise ValueError('Original/frozen input or bound output changed: ' + str(p))
        if _native._read(Path(compiled['object']['path']), MAX_INPUT) != _native._read(Path(linked['inputs'][0]['frozen']), MAX_INPUT):
            raise ValueError('Source-compiled object changed after linking')
        for entry in linked['outputs']:
            if _native._sha(_native._read(Path(entry['path']), _binder.MAX_OBJECT)) != entry['inspection']['sha256']:
                raise ValueError('Linked object changed after binding')
        report['source_chain'] = {'source_sha256': _native._sha(source_bytes),
                                  'globals_sha256': _native._sha(globals_bytes),
                                  'ir_sha256': compiled['frozen_inputs'][2]['sha256'],
                                  'pou_sha256': compiled['object']['sha256'],
                                  'compiler_report_sha256': _native._sha(_native._read(Path(report['compiler_report']), MAX_INPUT)),
                                  'link_report_sha256': _native._sha(_native._read(Path(report['link_report']), MAX_INPUT)),
                                  'association': 'observed-native-compile-two-links-and-existing-GVT-binding',
                                  'cryptographic_build_attestation': False}
        report.update(ok=True, status='existing-interface-native-candidate', stage='complete',
                      source_compilation_verified=True, existing_allocation_verified=True,
                      source_to_bound_objects_verified=True)
    except (OSError, ValueError, TypeError, UnicodeError, struct.error, RuntimeError, subprocess.SubprocessError) as exc:
        report['diagnostics'].append({'stage': report['stage'], 'code': 'E_NATIVE_APPLICATION', 'message': str(exc)})
    if created:
        with (out / 'candidate-report.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'globals-path', 'project-dir', 'syslib', 'stclib', 'llc', 'lld', 'output-dir'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--timeout-s', type=float, default=10.0)
    result = build_native_application(**vars(parser.parse_args(argv)))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
