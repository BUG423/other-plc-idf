#!/usr/bin/env python3
"""Compile an existing vendor STTemp MAIN container to an ARM object, without GUI.

This experimental worker has no device API. It requires the inspected local
Windows StLang installation; ordinary IEC text still needs vendor preprocessing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import struct
import subprocess
import sys

SCHEMA = "plcidf.vendor-object-build.v1"
COMPILERS = {
    r"E:\Software\AutoShop\Bin\STLang\StLang.exe": "b69439a1bb91f8dc6d982760023518671eff80e4cb0e90cc509c56e87e4f424a",
    r"E:\Software\AutoShop\Bin\STLang_New\StLang.exe": "a9e2eee650729aa25c5b8f2fa1b595776c219d63c0b63f73420e59270ea54847",
}
ERROR_TEXT = re.compile(r"\b(error|fatal|failed|failure|exception|lexer|syntax|unresolved|missing)\b|not exist", re.I)
MAX_FILE_BYTES = 32 * 1024 * 1024


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _failed(stage: str, message: str, **extra) -> dict:
    return {"schema": SCHEMA, "scope": "vendor-object-only", "ok": False,
            "status": "failed", "stage": stage, "device_access": False,
            "gui_access": False, "diagnostics": [{"code": "E_VENDOR_OBJECT", "stage": stage,
                                                     "message": message}], **extra}


def windows_path(value: str | Path) -> str:
    """Only ASCII drive paths are implemented by the inspected IPC contract."""
    text = str(value)
    if text.startswith("/mnt/") and len(text) > 7 and text[5].isalpha() and text[6] == "/":
        text = str(PureWindowsPath(text[5].upper() + ":\\", *Path(text[7:]).parts))
    if (not re.fullmatch(r"[A-Za-z]:\\[\x20-\x7e]+", text) or len(text) > 200
            or any(c in text[2:] for c in ':"')):
        raise ValueError("An explicit ASCII Windows drive path of at most 200 characters is required")
    return ntpath.normpath(text)


def local_path(value: str) -> Path:
    path = PureWindowsPath(value)
    if os.name == "nt":
        return Path(value)
    return Path("/mnt") / path.drive[0].lower() / Path(*path.parts[1:])


def _regular_read(path: Path, maximum: int = MAX_FILE_BYTES) -> bytes:
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError(f"Symlinks are unsupported: {path.name}")
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError(f"Missing or oversized ordinary file: {path.name}")
    with path.open("rb") as source:
        data = source.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError(f"File exceeded read bound: {path.name}")
    return data


def inspect_arm_object(data: bytes) -> dict:
    """Require a bounded ELF32 ARM REL with a defined, nonempty MAIN function."""
    if (len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01"
            or struct.unpack_from("<HH", data, 16) != (1, 40)
            or struct.unpack_from("<H", data, 40)[0] != 52):
        raise ValueError("Object is not an ELF32 little-endian ARM relocatable")
    table = struct.unpack_from("<I", data, 32)[0]
    entry, count = struct.unpack_from("<HH", data, 46)
    if entry != 40 or not 1 <= count <= 8192 or table < 52 or table + 40 * count > len(data):
        raise ValueError("Invalid ELF section table")
    sections = [struct.unpack_from("<10I", data, table + i * 40) for i in range(count)]
    for section in sections:
        if section[1] != 8 and section[4] + section[5] > len(data):
            raise ValueError("Truncated ELF section")
    found = False
    for section in sections:
        if section[1] != 2:
            continue
        if section[9] != 16 or section[5] % 16 or section[6] >= count:
            raise ValueError("Invalid ELF symbol table")
        strings = sections[section[6]]
        if strings[1] != 3:
            raise ValueError("Invalid ELF symbol strings")
        names = data[strings[4]:strings[4] + strings[5]]
        for offset in range(section[4], section[4] + section[5], 16):
            name, value, size, info, _, index = struct.unpack_from("<IIIBBH", data, offset)
            if name >= len(names) or (end := names.find(b"\0", name)) == -1:
                raise ValueError("Invalid ELF symbol name")
            if names[name:end] == b"MAIN" and info & 15 == 2:
                found |= 0 < index < count and size > 0 and (value & ~1) + size <= sections[index][5]
    if not found:
        raise ValueError("ARM object has no bounded defined MAIN function")
    return {"class": "ELF32", "byte_order": "little", "machine": "ARM", "type": "REL",
            "entry_symbol": "MAIN", "eabi_flags": f"0x{struct.unpack_from('<I', data, 36)[0]:08x}"}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json(text: str) -> dict:
    if len(text) > 2 * 1024 * 1024:
        raise ValueError("Helper response exceeds 2 MiB")
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite JSON number")
        return number
    result = json.loads(text.lstrip("\ufeff"), object_pairs_hook=_unique_object, parse_float=finite_float,
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Non-finite JSON: {value}")))
    if not isinstance(result, dict):
        raise ValueError("Helper must return a JSON object")
    return result


def _validate_record(record: dict, compiler: str, source: str, build: str) -> None:
    """Fail closed before trusting even a helper success marker."""
    if (record.get("schema") != SCHEMA or record.get("scope") != "vendor-object-only"
            or record.get("ok") is not True or record.get("status") != "verified"
            or record.get("stage") != "complete" or record.get("diagnostics") != []
            or record.get("device_access") is not False or record.get("gui_access") is not False):
        raise ValueError("Helper did not produce an unambiguous verified object-only record")
    if record.get("timed_out") is not False or record.get("output_truncated") is not False or record.get("capture_complete") is not True:
        raise ValueError("Helper completion/diagnostic capture is incomplete")
    if type(record.get("exit_code")) is not int or record["exit_code"] != 1:
        raise ValueError("Unexpected raw StLang exit code")
    if record.get("build_directory") != build:
        raise ValueError("Helper build directory identity mismatch")
    expected_ipc = {"started": 1, "done": 1, "success": 1}
    if record.get("ipc") != expected_ipc or any(type(v) is not int for v in record["ipc"].values()):
        raise ValueError("Mapped task did not complete successfully")
    stdout, stderr = record.get("stdout"), record.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str) or stderr or ERROR_TEXT.search(stdout):
        raise ValueError("Compiler error diagnostics override success markers")
    info = record.get("compiler", {})
    if info.get("path") != compiler or info.get("sha256") != COMPILERS[compiler]:
        raise ValueError("Compiler identity does not match the inspected installation")
    files = record.get("source_files")
    if not isinstance(files, list) or len(files) != 2 or [f.get("name") for f in files] != ["MAIN.st", "MAIN.cl"]:
        raise ValueError("Unexpected source file set")
    for info in files:
        if info.get("path") != ntpath.join(source, info["name"]):
            raise ValueError("Input file escaped the frozen source directory")
        data = _regular_read(local_path(info["path"]))
        copied = _regular_read(local_path(ntpath.join(build, info["name"])))
        if (info.get("sha256") != _sha(data) or info.get("bytes") != len(data) or copied != data):
            raise ValueError("Input identity changed or copy hash mismatch")
    objects = record.get("object_files")
    if not isinstance(objects, list) or len(objects) != 1:
        raise ValueError("Expected exactly one new ARM object")
    info = objects[0]
    if info.get("name") != "MAIN.o" or info.get("path") != ntpath.join(build, "MAIN.o"):
        raise ValueError("Object file escaped the fresh build directory")
    data = _regular_read(local_path(info["path"]))
    if info.get("sha256") != _sha(data) or info.get("bytes") != len(data) or info.get("elf") != inspect_arm_object(data):
        raise ValueError("Object identity or ARM structure mismatch")
    rst = record.get("result_file", {})
    if rst.get("name") != "MAIN.rst" or rst.get("path") != ntpath.join(build, "MAIN.rst"):
        raise ValueError("Result file escaped the fresh build directory")
    if _regular_read(local_path(rst["path"]), 64) != b"Success!\r\n" or rst.get("sha256") != _sha(b"Success!\r\n") or rst.get("bytes") != 10:
        raise ValueError("Result file is not the fresh exact Success CRLF marker")


def compile_vendor_st(compiler_path: str, input_directory: str, build_directory: str, *,
                      timeout_s: float = 4.0, powershell_exe: str = "powershell.exe",
                      helper_path: str | Path | None = None) -> dict:
    """Create a fresh build dir and compile frozen MAIN.st/.cl via bounded host IPC."""
    try:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or not 0.1 <= timeout_s <= 30:
            raise ValueError("timeout_s must be a finite number from 0.1 to 30")
        compiler = windows_path(compiler_path)
        compiler = next((item for item in COMPILERS if ntpath.normcase(item) == ntpath.normcase(compiler)), "")
        if not compiler:
            raise ValueError("Only the two inspected local StLang installations are supported")
        source, build = windows_path(input_directory), windows_path(build_directory)
        if helper_path is not None:
            helper = Path(helper_path)
        else:
            # Repository execution remains standalone; an installed worker uses
            # the wheel's canonical PowerShell resource through the package.
            helper = Path(__file__).resolve().parents[1] / "plc/tools/compile_vendor_st.ps1"
            if not helper.is_file():
                from plc_idf.config import runtime_asset
                helper = runtime_asset(Path.cwd(), "plc/tools/compile_vendor_st.ps1")
        helper_bytes = _regular_read(helper, 1024 * 1024)
        if not shutil.which(powershell_exe):
            raise ValueError("Windows PowerShell is unavailable")
        if os.name == "nt":
            visible_helper = str(helper.resolve())
        else:
            converted = subprocess.run(["wslpath", "-w", str(helper.resolve())], capture_output=True,
                                       text=True, timeout=5, check=True)
            visible_helper = converted.stdout.strip()
        argv = [powershell_exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", visible_helper, "-CompilerPath", compiler, "-InputDirectory", source,
                "-BuildDirectory", build, "-TimeoutMs", str(round(timeout_s * 1000))]
        result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="strict",
                                timeout=timeout_s + 20, check=False)
        record = _strict_json(result.stdout)
        if result.stderr.strip():
            return _failed("helper", "PowerShell emitted unexpected stderr", helper_stderr=result.stderr[:4096], worker_result=record)
        if record.get("ok") is not True:
            if record.get("schema") != SCHEMA or record.get("scope") != "vendor-object-only":
                raise ValueError("Unsupported helper failure response")
            record["ok"] = False
            record["status"] = "failed"
            return record
        if result.returncode != 0:
            raise ValueError("Helper success JSON contradicts helper process return code")
        _validate_record(record, compiler, source, build)
        if _regular_read(helper, 1024 * 1024) != helper_bytes:
            raise ValueError("PowerShell helper changed during compilation")
        record["helper_sha256"] = _sha(helper_bytes)
        return record
    except (OSError, ValueError, TypeError, KeyError, AttributeError, subprocess.SubprocessError) as exc:
        return _failed("host", str(exc))


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--compiler", required=True, help="Inspected E:\\Software\\AutoShop\\Bin\\STLang{,_New}\\StLang.exe")
    parser.add_argument("--input-dir", required=True, help="Frozen STTemp directory containing MAIN.st and MAIN.cl")
    parser.add_argument("--build-dir", required=True, help="New Windows directory; parent must exist")
    parser.add_argument("--timeout-s", type=float, default=4.0)
    parser.add_argument("--powershell-exe", default="powershell.exe",
                        help="Windows PowerShell path; WSL callers may use an absolute /mnt/c path")
    try:
        args = parser.parse_args(argv)
        record = compile_vendor_st(args.compiler, args.input_dir, args.build_dir,
                                   timeout_s=args.timeout_s, powershell_exe=args.powershell_exe)
    except ValueError as exc:
        record = _failed("arguments", str(exc))
    except KeyboardInterrupt:
        record = _failed("interrupted", "Host compilation interrupted; the Windows job closes with its helper")
    print(json.dumps(record, ensure_ascii=False, allow_nan=False))
    return 0 if record["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
