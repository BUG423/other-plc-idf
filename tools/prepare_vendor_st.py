#!/usr/bin/env python3
"""Prepare a restricted MAIN body + explicit globals for the vendor object compiler.

This host-only experimental format adapter does not produce a deployable PLC
project. Soft devices, direct I/O, libraries, functions and local declarations
still require the vendor project preprocessor and are rejected here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

SOURCE_SCHEMA = 'plcidf.vendor-main-source.v1'
REPORT_SCHEMA = 'plcidf.vendor-main-preparation.v1'
MAX_BYTES = 1024 * 1024
HEADER = b'****AS*****\0' + struct.pack('<5I', 1, 3, 1, 0, 0)
_FORMAT_BYTES = b'SclSoftware'
# StLang implicitly uses this declared runtime function for statement lists/control flow.
# Its definition belongs to the vendor runtime at the later, unsupported link stage.
IMPLICIT_DECLARATIONS = 'FUNCTION _HC_EMPTYFUC : VOID\r\nEND_FUNCTION\r\n'
TYPES = frozenset({'BOOL', 'INT', 'DINT', 'REAL'})
CONTROL = frozenset('IF THEN ELSE ELSIF END_IF FOR TO BY DO END_FOR WHILE END_WHILE REPEAT UNTIL END_REPEAT CASE OF END_CASE EXIT RETURN AND OR XOR NOT MOD TRUE FALSE'.split())
DECLARATION = frozenset('PROGRAM END_PROGRAM FUNCTION END_FUNCTION FUNCTION_BLOCK END_FUNCTION_BLOCK VAR VAR_GLOBAL VAR_INPUT VAR_OUTPUT VAR_IN_OUT VAR_TEMP VAR_EXTERNAL END_VAR TYPE END_TYPE CONFIGURATION RESOURCE TASK'.split())
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,62}\Z')
SOFT_DEVICE = re.compile(r'(?:[MDXYSTRC][0-9]+|[XYZ][0-9]+(?:\.[0-9]+)?)\Z', re.I)
NUMBER = re.compile(r'(?<![A-Za-z0-9_])(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?(?![A-Za-z0-9_])')


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_file(path: Path) -> bytes:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError('Input/output paths must not contain symlinks')
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Input must be an ordinary file of at most 1 MiB')
    with path.open('rb') as file:
        data = file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Input exceeds 1 MiB')
    return data


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def parse_globals(data: bytes) -> dict[str, str]:
    def reject(value):
        raise ValueError('Declarations must not contain numeric constants: ' + value)
    document = json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique_pairs,
                          parse_constant=reject)
    if not isinstance(document, dict) or set(document) != {'schema', 'globals'} or document['schema'] != SOURCE_SCHEMA:
        raise ValueError('Expected only schema=plcidf.vendor-main-source.v1 and globals')
    declarations = document['globals']
    if not isinstance(declarations, dict) or not 1 <= len(declarations) <= 256:
        raise ValueError('globals must declare 1 to 256 named variables')
    result, seen = {}, set()
    for name, type_name in declarations.items():
        if not isinstance(name, str) or not NAME.fullmatch(name) or name.upper() in CONTROL | DECLARATION | TYPES | {'MAIN'}:
            raise ValueError('Invalid or reserved global name: ' + str(name))
        if SOFT_DEVICE.fullmatch(name) or name.upper().startswith(('_HC_', '_ST_')):
            raise ValueError('Soft-device/vendor-internal names require vendor preprocessing: ' + name)
        if name.casefold() in seen:
            raise ValueError('Global names must be unique ignoring case: ' + name)
        seen.add(name.casefold())
        if not isinstance(type_name, str) or type_name.upper() not in TYPES:
            raise ValueError('Only BOOL, INT, DINT and REAL globals are supported')
        result[name] = type_name.upper()
    return dict(sorted(result.items(), key=lambda item: item[0].casefold()))


def lexical_code(text: str) -> tuple[str, bool]:
    """Hide comments and quoted content before identifying unsupported syntax."""
    code = list(text)
    i, strings = 0, False
    while i < len(text):
        if text.startswith('//', i):
            end = text.find('\n', i)
            end = len(text) if end < 0 else end
            for j in range(i, end): code[j] = ' '
            i = end
        elif text.startswith('(*', i):
            start, depth = i, 1
            i += 2
            while i < len(text) and depth:
                if text.startswith('(*', i): depth += 1; i += 2
                elif text.startswith('*)', i): depth -= 1; i += 2
                else: i += 1
                if depth > 16:
                    raise ValueError('Comment nesting exceeds 16')
            if depth:
                raise ValueError('Unterminated block comment')
            for j in range(start, i):
                if code[j] not in '\r\n': code[j] = ' '
        elif text[i] in "'\"":
            strings = True
            start, quote = i, text[i]
            i += 1
            while i < len(text):
                if text[i] == '$': i += 2
                elif text[i] == quote:
                    if i + 1 < len(text) and text[i + 1] == quote: i += 2
                    else: i += 1; break
                else: i += 1
            else:
                raise ValueError('Unterminated string literal')
            for j in range(start, min(i, len(text))):
                if code[j] not in '\r\n': code[j] = ' '
        else:
            i += 1
    return ''.join(code), strings


def intermediate_text(body: str, declarations: dict[str, str]) -> str:
    if not body.strip() or '\0' in body or len(body.encode('utf-8')) > MAX_BYTES:
        raise ValueError('MAIN body must be nonempty UTF-8 without NUL, at most 1 MiB')
    code, strings = lexical_code(body)
    if strings:
        raise ValueError('String literals are outside the supported BOOL/INT/DINT/REAL subset')
    if re.search(r'%(?:[IQM])', code, re.I):
        raise ValueError('Direct I/O addresses require vendor project preprocessing')
    identifiers = re.findall(r'\b[A-Za-z_][A-Za-z0-9_]*\b', NUMBER.sub(' ', code))
    known = {name.casefold() for name in declarations}
    for name in identifiers:
        if name.upper() in DECLARATION:
            raise ValueError('Provide only MAIN statements; declaration/program blocks are unsupported')
        if SOFT_DEVICE.fullmatch(name) or name.upper().startswith(('_HC_', '_ST_')):
            raise ValueError('Soft-device/vendor-internal references require vendor preprocessing: ' + name)
        if name.upper() not in CONTROL and name.casefold() not in known:
            raise ValueError('Undeclared variable or unsupported library/function: ' + name)
    for call in re.finditer(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', code):
        if call[1].upper() not in CONTROL:
            raise ValueError('Function/library calls require vendor preprocessing: ' + call[1])
    numeric_hidden = NUMBER.sub(' ', code)
    if re.search(r'[\[\]{}.#]', numeric_hidden):
        raise ValueError('Arrays, fields, typed literals and extended syntax are unsupported')
    body = body.replace('\r\n', '\n').replace('\r', '\n').strip() + '\n'
    text = 'VAR_GLOBAL\n' + ''.join(f'    {name}:{kind};\n' for name, kind in declarations.items()) + 'END_VAR\n' + body
    return IMPLICIT_DECLARATIONS + text.replace('\n', '\r\n')


def encode_sttemp(text: str) -> bytes:
    data = text.encode('utf-8')
    if len(data) > MAX_BYTES:
        raise ValueError('Intermediate payload exceeds 1 MiB')
    payload = bytes((((value + _FORMAT_BYTES[(i + 1) % 11]) & 255) ^ (i & 255)
                     if i % 2 == 0 else (value + _FORMAT_BYTES[(i + 1) % 11]) & 255)
                    for i, value in enumerate(data))
    return HEADER + payload


def prepare_vendor_st(source_path: str | Path, globals_path: str | Path, output_directory: str | Path) -> dict:
    report = {'schema': REPORT_SCHEMA, 'scope': 'vendor-main-intermediate-only', 'ok': False,
              'status': 'failed', 'stage': 'preflight', 'diagnostics': [], 'device_access': False,
              'gui_access': False, 'project_link_verified': False,
              'injected_runtime_declarations': ['_HC_EMPTYFUC:VOID()'], 'files': []}
    try:
        source, globals_file, output = Path(source_path).absolute(), Path(globals_path).absolute(), Path(output_directory).absolute()
        source_bytes, globals_bytes = read_file(source), read_file(globals_file)
        declarations = parse_globals(globals_bytes)
        body = source_bytes.decode('utf-8-sig')
        text = intermediate_text(body, declarations)
        container = encode_sttemp(text)
        for part in (output, *output.parents):
            if part.is_symlink():
                raise ValueError('Input/output paths must not contain symlinks')
        if not output.parent.is_dir():
            raise ValueError('Output parent must already exist')
        report.update(source=str(source), source_sha256=sha(source_bytes), globals_file=str(globals_file),
                      globals_sha256=sha(globals_bytes), globals=declarations,
                      intermediate_text_sha256=sha(text.encode('utf-8')), output_directory=str(output))
        report['stage'] = 'write'
        output.mkdir(exist_ok=False)
        for name, data in [('MAIN.st', container), ('MAIN.cl', b'')]:
            target = output / name
            with target.open('xb') as file:
                file.write(data)
            if read_file(target) != data:
                raise ValueError('Written intermediate file identity changed')
            report['files'].append({'name': name, 'path': str(target), 'bytes': len(data), 'sha256': sha(data)})
        if read_file(source) != source_bytes or read_file(globals_file) != globals_bytes:
            raise ValueError('Source/declarations changed during preparation')
        report.update(ok=True, status='prepared', stage='complete')
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        report['diagnostics'].append({'code': 'E_VENDOR_PREPARE', 'stage': report['stage'], 'message': str(exc)})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='UTF-8 MAIN statements, without PROGRAM or VAR wrapper')
    parser.add_argument('--globals', dest='globals_path', required=True, help='Explicit typed globals JSON')
    parser.add_argument('--output-dir', required=True, help='New exclusive intermediate directory')
    args = parser.parse_args(argv)
    report = prepare_vendor_st(args.source, args.globals_path, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
