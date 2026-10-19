#!/usr/bin/env python3
"""Compile one explicitly limited ST memory probe with a local Linux LLVM llc.

Profile counter-dint-increment-v1 accepts only one external COUNTER:DINT and the
complete statement COUNTER := COUNTER + <decimal 1..32767>;. Comments are allowed.
Profile bess-pcmd-real-increment-v1 accepts only the existing g_rBESS_Pcmd:REAL
and g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0; (or 2.0). The fixed ELF symbol is
G_RBESS_PCMD, independently observed in the project in reports 17/27. This does
not assign its address: a separate same-project GVT/CSV binding is required.
The independent frontend emits LLVM IR; llc produces a Thumb-2 ARM ET_REL object.
This is neither MatIEC nor a replacement for the vendor's full ST compiler.

The MAIN/global-memory shape is compared with the existing user-owned vendor
probe in research 23. Vendor helper arithmetic, variable binding, task scheduling,
physical I/O, packaging and execution are NOT reproduced or established here.
Overflow is explicitly modulo 2**32 for this research profile, not an assertion
about all vendor ST arithmetic. No Windows, network or device APIs are present.
Opaque runtime bindings provide no four-byte alignment guarantee. Both profiles
therefore use align1 and strict-align byte transfers; these are non-atomic and
require one writer. See research40; this does not verify physical runtime ABI.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys


def _load(filename):
    spec = importlib.util.spec_from_file_location('_native_probe_' + filename[:-3], Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_prepare = _load('prepare_vendor_st.py')
_inspect = _load('inspect_vendor_application.py')
SCHEMA = 'plcidf.easy521-native-memory-probe.v1'
PROFILE = 'counter-dint-increment-v1'
REAL_PROFILE = 'bess-pcmd-real-increment-v1'
MAX_SOURCE = 64 * 1024
MAX_OBJECT = 4 * 1024 * 1024
TRIPLE = 'thumbv7-none-eabi'


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read(path, limit):
    """Read a bounded ordinary file; reject symlinks, truncation and stat drift."""
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError('Symlink paths are unsupported: ' + str(path))
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError('Not a bounded ordinary file: ' + str(path))
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if len(data) > limit or any(getattr(before, k) != getattr(after, k) for k in fields):
        raise ValueError('File changed while being read: ' + str(path))
    return data


def parse_probe(source: bytes, declarations: bytes, *, profile=PROFILE) -> dict:
    """Parse the entire supported ST body and explicit globals, or raise ValueError.

    Inputs are UTF-8 bytes, each at most 64 KiB. profile explicitly selects
    COUNTER:DINT counts/scan or existing g_rBESS_Pcmd:REAL +1.0/+2.0 per call.
    Return values include the fixed symbol and arithmetic representation.
    No addresses, initialization, retention or task period are assigned here.
    """
    if max(len(source), len(declarations)) > MAX_SOURCE:
        raise ValueError('Source/declarations exceed 64 KiB')
    globals_map = _prepare.parse_globals(declarations)
    if profile not in (PROFILE, REAL_PROFILE):
        raise ValueError('Unsupported native probe profile')
    expected = [('COUNTER', 'DINT')] if profile == PROFILE else [('G_RBESS_PCMD', 'REAL')]
    if len(globals_map) != 1 or [(k.upper(), v) for k, v in globals_map.items()] != expected:
        raise ValueError('Declarations differ from the fixed native probe profile')
    text = source.decode('utf-8-sig')
    if '\0' in text:
        raise ValueError('NUL is forbidden')
    code, strings = _prepare.lexical_code(text)
    if strings:
        raise ValueError('String literals are unsupported')
    if profile == REAL_PROFILE:
        match = re.fullmatch(r'[ \t\r\n]*g_rBESS_Pcmd[ \t\r\n]*:=[ \t\r\n]*g_rBESS_Pcmd[ \t\r\n]*\+[ \t\r\n]*([12]\.0)[ \t\r\n]*;[ \t\r\n]*', code, re.I)
        if match is None:
            raise ValueError('Expected only g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0; or + 2.0;')
        return {'profile': REAL_PROFILE, 'symbol': 'G_RBESS_PCMD',
                'source_name': 'g_rBESS_Pcmd', 'type': 'REAL',
                'increment_real': match[1], 'arithmetic': 'f32-to-f64-add-to-f32',
                'initial_value': None, 'retained': None, 'task_period_ms': None}
    # Anchored full-body grammar deliberately accepts no declarations, calls,
    # device names, second statement, alternate operators or numeric suffixes.
    match = re.fullmatch(r'[ \t\r\n]*COUNTER[ \t\r\n]*:=[ \t\r\n]*COUNTER[ \t\r\n]*\+[ \t\r\n]*([0-9]{1,5})[ \t\r\n]*;[ \t\r\n]*', code, re.I)
    if match is None or not 1 <= int(match[1]) <= 32767:
        raise ValueError('Expected only COUNTER := COUNTER + <decimal 1..32767>;')
    return {'profile': PROFILE, 'symbol': 'COUNTER', 'type': 'DINT',
            'increment_counts_per_scan': int(match[1]), 'overflow': 'modulo-2**32',
            'initial_value': None, 'retained': None, 'task_period_ms': None}


def emit_ir(parsed: dict) -> bytes:
    """Lower a parsed research probe to target-independent LLVM IR, without I/O.

    The external i32 storage mirrors only the inspected COUNTER memory shape.
    No nsw/nuw flags are used: the DINT profile's add is modulo 2**32. REAL
    preserves the observed f32 -> f64 add -> f32 conversion without fast math.
    Volatile memory accesses retain each scan's load/store. align1 deliberately
    makes no unproved allocation-alignment promise; strict-align code generation
    must split external accesses into non-atomic bytes. Retention/runtime binding
    and synchronization with concurrent readers/writers remain unverified.
    """
    if parsed.get('profile') == REAL_PROFILE:
        increment = {'1.0': '1.000000e+00', '2.0': '2.000000e+00'}.get(parsed.get('increment_real'))
        if increment is None or parsed.get('symbol') != 'G_RBESS_PCMD' or parsed.get('type') != 'REAL':
            raise ValueError('Invalid parsed REAL probe')
        # Vendor self-authored REAL probe in report 27 widens f32 to f64,
        # calls _ST_DOUBLE_ADD, then narrows to f32. Preserve those conversion
        # steps without adding fast-math flags or copying vendor machine code.
        return f'''source_filename = "plcidf-native-bess-pcmd-probe"
@G_RBESS_PCMD = external global float, align 1

define void @MAIN() nounwind {{
entry:
  %before = load volatile float, ptr @G_RBESS_PCMD, align 1
  %wide = fpext float %before to double
  %after_wide = fadd double %wide, {increment}
  %after = fptrunc double %after_wide to float
  store volatile float %after, ptr @G_RBESS_PCMD, align 1
  ret void
}}
'''.encode('ascii')
    increment = parsed['increment_counts_per_scan']
    if (parsed.get('profile') != PROFILE or type(increment) is not int
            or not 1 <= increment <= 32767):
        raise ValueError('Invalid parsed probe')
    return f'''source_filename = "plcidf-native-memory-probe"
@COUNTER = external global i32, align 1

define void @MAIN() nounwind {{
entry:
  %before = load volatile i32, ptr @COUNTER, align 1
  %after = add i32 %before, {increment}
  store volatile i32 %after, ptr @COUNTER, align 1
  ret void
}}
'''.encode('ascii')


def inspect_alignment_ir(data: bytes, *, symbol='G_RBESS_PCMD', scalar='float') -> dict:
    """Audit actual bounded LLVM IR for the one external scalar's align1 accesses.

    This inspects generated IR, never translates ST. Reject references that are
    not an align1 external declaration/volatile scalar load/store, and require
    an actual write. No physical address, atomicity or runtime claim follows.
    """
    if symbol not in ('G_RBESS_PCMD', 'COUNTER') or scalar not in ('float', 'i32') or len(data) > MAX_OBJECT:
        raise ValueError('Unsupported alignment IR profile or size')
    lines = [line.strip() for line in data.decode('utf-8').splitlines() if '@' + symbol in line]
    declaration = re.compile(r'@' + symbol + r' = external(?: dso_local)? global ' + scalar + r', align 1')
    load = re.compile(r'%[-\w.]+ = load volatile ' + scalar + r', ptr @' + symbol + r', align 1(?:, .*)?')
    store = re.compile(r'store volatile ' + scalar + r' .+, ptr @' + symbol + r', align 1(?:, .*)?')
    counts = {'declarations': 0, 'loads': 0, 'stores': 0}
    for line in lines:
        key = ('declarations' if declaration.fullmatch(line) else 'loads' if load.fullmatch(line)
               else 'stores' if store.fullmatch(line) else None)
        if key is None:
            raise ValueError('External IR access is not an explicit align1 volatile scalar: ' + line)
        counts[key] += 1
    if counts['declarations'] != 1 or counts['stores'] < 1:
        raise ValueError('Expected one align1 external declaration and at least one actual store')
    return {'external_alignment_bytes': 1, 'external_symbol': symbol,
            'volatile_loads': counts['loads'], 'volatile_stores': counts['stores'],
            'access_atomic': False, 'concurrent_writer_supported': False}


def inspect_byte_transfer_assembly(data: bytes, *, ir_inspection: dict) -> dict:
    """Audit one actual LLVM/Clang ARM MAIN assembly for strict byte transfers.

    Reject helper calls and non-byte memory accesses through general registers.
    Known stack operations and PC-relative literal loads remain permitted. This
    bounded compiler-output audit is not a general ARM disassembler; unsupported
    emitted forms fail closed. It proves the inspected assembly's access shape,
    not execution or physical-device atomicity.
    """
    if len(data) > MAX_OBJECT:
        raise ValueError('ARM assembly exceeds the bounded probe size')
    lines = data.decode('utf-8').splitlines()
    starts = [i for i, line in enumerate(lines) if re.fullmatch(r'MAIN:\s*(?:@.*)?', line)]
    if len(starts) != 1:
        raise ValueError('Expected one ARM MAIN assembly label')
    counts = {'byte_loads': 0, 'byte_stores': 0, 'stack_or_literal_accesses': 0}
    ended = False
    for line in lines[starts[0] + 1:]:
        if re.match(r'\s*\.Lfunc_end[0-9]+:', line):
            ended = True
            break
        code = line.split('@', 1)[0].strip()
        if not code or code.startswith('.') or code.endswith(':'):
            continue
        parts = code.split(None, 1)
        opcode, operands = parts[0].lower(), parts[1] if len(parts) > 1 else ''
        if opcode in ('bl', 'blx', 'bl.w', 'blx.w'):
            raise ValueError('Strict byte-transfer probe cannot call a helper')
        if opcode.startswith(('ldrb', 'ldrsb')):
            counts['byte_loads'] += 1
        elif opcode.startswith('strb'):
            counts['byte_stores'] += 1
        elif opcode.startswith(('push', 'pop', 'vpush', 'vpop')):
            counts['stack_or_literal_accesses'] += 1
        elif opcode.startswith(('ldr', 'str', 'vld', 'vst', 'ldm', 'stm')):
            stack_or_pc = re.search(r'\[(?:sp|pc)(?:,|\])', operands)
            literal = opcode.startswith('vldr') and re.search(r',\s*\.LCPI[0-9_]+', operands)
            if not stack_or_pc and not literal:
                raise ValueError('Non-byte memory access through an unproved-alignment base: ' + code)
            counts['stack_or_literal_accesses'] += 1
    if not ended:
        raise ValueError('ARM MAIN assembly is missing its bounded end label')
    if (counts['byte_loads'] < 4 * ir_inspection['volatile_loads']
            or counts['byte_stores'] < 4 * ir_inspection['volatile_stores']):
        raise ValueError('ARM byte transfers do not cover every align1 scalar access')
    return {**counts, 'nonbyte_general_register_accesses': 0,
            'access_atomic': False, 'concurrent_writer_supported': False,
            'scope': 'bounded-generated-MAIN-assembly-not-runtime-execution'}


def inspect_probe_object(data: bytes, *, profile=PROFILE) -> dict:
    """Require ARM EABI5 REL, a bounded Thumb MAIN and one fixed external.

    This validates an object structure and expected relocation categories. It
    does not disassemble or execute ARM instructions, certify their semantics,
    or validate the device runtime ABI. Unsupported object shapes are rejected.
    """
    elf = _inspect.parse_elf(data)
    if (elf['class_bits'], elf['endianness'], elf['machine_id'], elf['type_id'], elf['arm_eabi_version']) != (32, 'little', 40, 1, 5):
        raise ValueError('Expected ELF32 little-endian ARM EABI5 ET_REL')
    if profile not in (PROFILE, REAL_PROFILE):
        raise ValueError('Unsupported native probe object profile')
    symbol = 'COUNTER' if profile == PROFILE else 'G_RBESS_PCMD'
    if elf['defined_global_weak']['names'] != ['MAIN'] or elf['undefined_global_weak']['names'] != [symbol]:
        raise ValueError('Expected only defined MAIN and the fixed external variable')
    expected = {'R_ARM_THM_MOVW_ABS_NC': 1, 'R_ARM_THM_MOVT_ABS': 1,
                'R_ARM_PREL31': 1}
    if elf['relocations']['types'] != expected or elf['relocations']['referenced_undefined_names'] != [symbol]:
        raise ValueError('Expected absolute Thumb MOVW/MOVT COUNTER relocations')
    if struct.unpack_from('<H', data, 46)[0] != 40:
        raise ValueError('Expected ordinary ELF32 section headers')
    table = struct.unpack_from('<I', data, 32)[0]
    count = struct.unpack_from('<H', data, 48)[0]
    sections = [struct.unpack_from('<10I', data, table + i * 40) for i in range(count)]
    main = []
    for section in sections:
        if section[1] != 2:
            continue
        strings = sections[section[6]]
        names = data[strings[4]:strings[4] + strings[5]]
        for offset in range(section[4], section[4] + section[5], section[9]):
            name, value, size, info, _, index = struct.unpack_from('<IIIBBH', data, offset)
            if names[name:names.find(b'\0', name)] == b'MAIN':
                if (info != 0x12 or not value & 1 or not 0 < index < count
                        or not size or (value & ~1) + size > sections[index][5]):
                    raise ValueError('MAIN is not a bounded global Thumb function')
                if sections[index][1] != 1 or sections[index][2] & 6 != 6:
                    raise ValueError('MAIN must reside in allocated executable PROGBITS')
                main.append({'value': value, 'bytes': size, 'section': index})
    if len(main) != 1:
        raise ValueError('Expected one MAIN function')
    return {'elf': elf, 'main': main[0], 'arm_code_executed': False}


def compile_probe(*, source, globals_path, llc, output_dir, timeout_s=10.0, profile=PROFILE,
                  runner=subprocess.run) -> dict:
    """Compile once with trusted native Linux llc and preserve an audit report.

    Paths identify source, explicit declarations, a caller-trusted Linux ELF llc
    and an output directory that must not exist. profile selects one exact ST
    grammar and external symbol. timeout_s bounds each process
    in seconds (0.1..30). No process is retried. Original inputs are read-only;
    a new output directory holds frozen source/declarations, IR, MAIN.o and JSON.
    Partial results remain on failure. Success means this restricted source was
    compiled and the object structure validated; deployment/runtime stay false.
    runner is injectable only for offline failure testing.
    """
    report = {'schema': SCHEMA, 'profile': profile, 'scope': 'offline-native-st-memory-probe',
              'ok': False, 'status': 'failed', 'stage': 'preflight', 'diagnostics': [],
              'device_access': False, 'windows_access': False, 'source_compiled': False,
              'runtime_abi_verified': False, 'downloadable': False, 'arm_code_executed': False}
    created = False
    output = Path(output_dir).absolute()
    try:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or not 0.1 <= timeout_s <= 30:
            raise ValueError('timeout_s must be a finite 0.1..30 seconds')
        paths = [Path(p).absolute() for p in (source, globals_path, llc)]
        blobs = [_read(paths[0], MAX_SOURCE), _read(paths[1], MAX_SOURCE), _read(paths[2], 256 * 1024 * 1024)]
        if len(set(paths)) != 3:
            raise ValueError('Input paths must be distinct')
        binary = blobs[2]
        if (binary[:7] != b'\x7fELF\x02\x01\x01' or len(binary) < 64
                or struct.unpack_from('<H', binary, 18)[0] not in (62, 183)
                or not os.access(paths[2], os.X_OK)):
            raise ValueError('llc must be an executable trusted native Linux ELF')
        for item in (output, *output.parents):
            if item.is_symlink():
                raise ValueError('Output paths may not contain symlinks')
        if output.exists() or not output.parent.is_dir():
            raise ValueError('Output directory must be new with an existing parent')
        parsed = parse_probe(blobs[0], blobs[1], profile=profile)
        ir = emit_ir(parsed)
        alignment = inspect_alignment_ir(ir, symbol=parsed['symbol'],
                                         scalar='float' if profile == REAL_PROFILE else 'i32')
        components = [Path(__file__).absolute(), Path(_prepare.__file__).absolute(),
                      Path(_inspect.__file__).absolute()]
        component_blobs = [_read(p, MAX_OBJECT) for p in components]
        report.update(parsed=parsed, ir_alignment=alignment,
                      access_atomic=False, concurrent_writer_supported=False,
                      inputs=[{'path': str(p), 'bytes': len(b), 'sha256': _sha(b)} for p, b in zip(paths[:2], blobs[:2])],
                      compiler={'path': str(paths[2]), 'bytes': len(binary), 'sha256': _sha(binary)},
                      output_directory=str(output), frontend_components=[
                          {'path': str(p), 'bytes': len(b), 'sha256': _sha(b)}
                          for p, b in zip(components, component_blobs)])
        output.mkdir()
        created = True
        frozen = [('body.st', blobs[0]), ('globals.json', blobs[1]), ('MAIN.ll', ir)]
        report['frozen_inputs'] = []
        for name, data in frozen:
            target = output / name
            with target.open('xb') as stream:
                stream.write(data)
            target.chmod(0o444)
            report['frozen_inputs'].append({'path': str(target), 'bytes': len(data), 'sha256': _sha(data)})
        report['stage'] = 'compiler-version'
        version = runner([str(paths[2]), '--version'], stdin=subprocess.DEVNULL, capture_output=True,
                         text=True, timeout=timeout_s, check=False)
        if version.returncode or version.stderr.strip() or 'LLVM version' not in version.stdout:
            raise ValueError('llc --version did not identify LLVM cleanly')
        report['compiler']['version'] = version.stdout.splitlines()[0]
        argv = [str(paths[2]), '-mtriple=' + TRIPLE, '-mattr=+v7,+thumb2,+vfp3,+neon,+reserve-r9,+strict-align',
                '-float-abi=soft', '-relocation-model=static', '-filetype=obj', '-O=2',
                str(output / 'MAIN.ll'), '-o', str(output / 'MAIN.o')]
        report['stage'] = 'compile'
        report['command'] = {'argv': argv, 'timeout_s': timeout_s, 'attempts': 1}
        result = runner(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                        timeout=timeout_s, check=False)
        report['command'].update(returncode=result.returncode, stdout=result.stdout[:4096], stderr=result.stderr[:4096])
        if result.returncode or result.stdout.strip() or result.stderr.strip():
            raise ValueError('llc failed or emitted unexpected diagnostics')
        object_bytes = _read(output / 'MAIN.o', MAX_OBJECT)
        report['stage'] = 'verify'
        if _read(output / 'MAIN.ll', MAX_SOURCE) != ir or _read(paths[0], MAX_SOURCE) != blobs[0]:
            raise ValueError('Source or frozen IR changed before paired assembly lowering')
        # The paired assembly uses the exact same frozen IR/backend flags as
        # the object, changing only output file type. It is not handwritten or
        # copied vendor code; runtime/device verification remains false.
        asm_argv = [('-filetype=asm' if item == '-filetype=obj' else
                     str(output / 'MAIN.s') if item == str(output / 'MAIN.o') else item) for item in argv]
        report['stage'] = 'assembly-audit'
        assembly_result = runner(asm_argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                 timeout=timeout_s, check=False)
        report['assembly_command'] = {'argv': asm_argv, 'timeout_s': timeout_s, 'attempts': 1,
                                      'returncode': assembly_result.returncode,
                                      'stdout': assembly_result.stdout[:4096], 'stderr': assembly_result.stderr[:4096]}
        if assembly_result.returncode or assembly_result.stdout.strip() or assembly_result.stderr.strip():
            raise ValueError('llc assembly lowering failed or emitted unexpected diagnostics')
        assembly = _read(output / 'MAIN.s', MAX_OBJECT)
        assembly_inspection = inspect_byte_transfer_assembly(assembly, ir_inspection=alignment)
        (output / 'MAIN.s').chmod(0o444)
        report['arm_assembly'] = {'path': str(output / 'MAIN.s'), 'sha256': _sha(assembly),
                                  'inspection': assembly_inspection,
                                  'paired_codegen_same_ir_and_flags': True}
        report['stage'] = 'verify'
        inspection = inspect_probe_object(object_bytes, profile=profile)
        for path, data in zip(paths, blobs):
            if _read(path, 256 * 1024 * 1024 if path == paths[2] else MAX_SOURCE) != data:
                raise ValueError('Input/compiler changed during compilation')
        for name, data in frozen:
            if _read(output / name, MAX_SOURCE) != data:
                raise ValueError('Frozen source or IR changed during compilation')
        for path, data in zip(components, component_blobs):
            if _read(path, MAX_OBJECT) != data:
                raise ValueError('Frontend component changed during compilation')
        if _read(output / 'MAIN.s', MAX_OBJECT) != assembly:
            raise ValueError('ARM assembly changed during compilation')
        (output / 'MAIN.o').chmod(0o444)
        report.update(object={'path': str(output / 'MAIN.o'), 'bytes': len(object_bytes),
                              'sha256': _sha(object_bytes), 'inspection': inspection},
                      ok=True, status='restricted-source-compiled', stage='complete', source_compiled=True)
    except (OSError, ValueError, TypeError, UnicodeError, subprocess.SubprocessError, struct.error) as exc:
        report['diagnostics'].append({'code': 'E_NATIVE_PROBE', 'stage': report['stage'], 'message': str(exc)})
    if created:
        try:
            with (output / 'probe-report.json').open('x', encoding='utf-8') as stream:
                json.dump(report, stream, ensure_ascii=False, allow_nan=False, indent=2)
                stream.write('\n')
        except OSError as exc:
            report.update(ok=False, source_compiled=False, status='failed', stage='report')
            report['diagnostics'].append({'code': 'E_NATIVE_PROBE', 'stage': 'report', 'message': str(exc)})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--globals', dest='globals_path', required=True)
    parser.add_argument('--llc', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--timeout-s', type=float, default=10.0)
    parser.add_argument('--profile', choices=(PROFILE, REAL_PROFILE), default=PROFILE)
    record = compile_probe(**vars(parser.parse_args(argv)))
    print(json.dumps(record, ensure_ascii=False, allow_nan=False))
    return 0 if record['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
