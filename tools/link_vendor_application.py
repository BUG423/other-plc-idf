#!/usr/bin/env python3
"""Link existing AutoShop ARM objects with a local Linux LLVM linker only.

This is an offline, bounded reproduction of two relocatable-link commands found
in AutoShop 4.12's H530Converter.dll. It neither compiles ST nor constructs FOID,
authenticates a PLC, transfers files, or proves runtime compatibility. See
docs/research/firmware/16-application-transaction-2026-10-19.md for the call sites.
The vendor's later SInfo symbol injection is NOT reproduced: the observed
project's eight global-variable UND entries still require vendor-specific type
and address values even when code and relocation sections match byte for byte.

Inputs are copied to a new directory before linking. Their original paths are
never passed to a linker. Each command runs once with a timeout; a failed or
partial output is retained as diagnostic evidence and never reported as linked.
Only metadata is suitable for sharing: the frozen proprietary objects themselves
must remain private. Nothing imports or executes a vendor executable or DLL.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

# Reuse the repository's bounded, standard-library ELF parser. Loading this file
# by path also works when the tool is imported with importlib from a test runner.
_spec = importlib.util.spec_from_file_location(
    "_plcidf_vendor_link_inspection", Path(__file__).with_name("inspect_vendor_application.py"))
_inspection = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_inspection)

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_POUS = 256
LIMITATIONS = [
    "Existing ARM objects are linked; ST compilation and complete application packaging are not implemented.",
    "Vendor ST relocation/SInfo injection is not implemented: global-variable UND st_info/st_value address bindings remain unbound.",
    "The caller supplies the POU order and exact library branch; no project option or library selection is inferred.",
    "Unresolved runtime symbols are retained by relocatable linking; matching names do not establish runtime ABI compatibility.",
    "No vendor executable/DLL, Windows process, network, USB, PLC, download, or CPU control is used.",
    "A linked object is not evidence of a downloadable or executable PLC application.",
    "Large projects needing AutoShop's 30000-character intermediate-chunk path are rejected rather than reordered.",
]


def _read_regular(path: Path, *, limit: int) -> bytes:
    """Read a bounded regular file without following its final symlink.

    A stat check before/after catches concurrent replacement or modification.
    This is an integrity check for a local experiment, not a hostile filesystem
    sandbox. Parent directories and the chosen linker are trusted by the caller.
    """
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError(f"not a bounded regular file: {path}")
        data = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if len(data) > limit or (before.st_dev, before.st_ino, before.st_size,
                           before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError(f"input changed while reading: {path}")
    return data


def _arm_object(data: bytes, label: str) -> dict:
    result = _inspection.inspect_bytes(data)
    elf = result.get("elf", {})
    # Both installed ARM branches and the observed POU objects are ELF32 LE,
    # ET_REL, EM_ARM. Reject x86 simulator objects and executable/container files.
    if (result["inspection_status"] != "parsed" or elf.get("class_bits") != 32
            or elf.get("endianness") != "little" or elf.get("machine_id") != 40
            or elf.get("type_id") != 1):
        raise ValueError(f"not a valid little-endian ELF32 ARM ET_REL object: {label}")
    return result


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def link_application(*, pous: Sequence[Path | str], syslib: Path | str,
                     stclib: Path | str, lld: Path | str, output_dir: Path | str,
                     timeout_s: float = 10.0,
                     runner: Callable = subprocess.run) -> dict:
    """Execute the two observed links once and return an auditable JSON record.

    ``pous`` is the explicit ordered POU object list, not source files. ``syslib``
    and ``stclib`` are explicit _SysLib.o/_STCLib.o paths from one selected ARM
    branch. ``lld`` is a trusted local Linux ld.lld executable (its symlink name
    is preserved because the LLVM multicall binary uses argv[0]). ``output_dir``
    must not exist. ``timeout_s`` bounds each subprocess, in seconds, with no
    retries. ``runner`` is injectable for tests and has subprocess.run's API.

    Invalid inputs raise ValueError/OSError before an output directory is made.
    Once created, failures return ``ok=False`` and a persistent link-report.json;
    partial files are kept, never reused. Success has status ``linked`` and
    ``downloadable=False``. Outputs and frozen inputs are made read-only; their
    hashes are reported. Original input files are only read and rechecked.
    """
    if not 1 <= len(pous) <= MAX_POUS:
        raise ValueError(f"provide 1..{MAX_POUS} explicitly ordered POU objects")
    if not isinstance(timeout_s, (int, float)) or isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 300:
        raise ValueError("timeout_s must be finite and in (0, 300] seconds")
    originals = [Path(x).absolute() for x in [*pous, syslib, stclib]]
    if len({str(p.resolve()) for p in originals}) != len(originals):
        raise ValueError("POU and library paths must be distinct")
    snapshots = []
    remaining = MAX_TOTAL_BYTES
    for original in originals:
        # Enforce the aggregate budget before allocating the next snapshot, not
        # after reading up to 258 individually valid but collectively huge files.
        data = _read_regular(original, limit=min(MAX_INPUT_BYTES, remaining))
        snapshots.append(data)
        remaining -= len(data)
    inspections = [_arm_object(data, str(path)) for path, data in zip(originals, snapshots)]
    out = Path(output_dir).absolute()
    if out.exists() or out.is_symlink():
        raise ValueError("output_dir must be a new directory")
    # Reject paths in an input's directory tree: this tool has no reason to
    # create output under a vendor installation or the live project workspace.
    if any(out == p.parent or p.parent in out.parents for p in originals):
        raise ValueError("output_dir must be separate from every input directory")
    linker = Path(lld).absolute()
    # Hash the resolved multicall target but invoke ld.lld by its original name.
    # An ELF host binary is required; .exe/.dll PE images cannot be executed here.
    linker_bytes = _read_regular(linker.resolve(strict=True), limit=128 * 1024 * 1024)
    host = _inspection.parse_elf(linker_bytes)
    if host["machine_id"] not in {62, 183} or host["type_id"] not in {2, 3}:
        raise ValueError("lld must be a trusted Linux x86-64/AArch64 ELF executable")
    if not os.access(linker, os.X_OK):
        raise ValueError("lld is not executable")
    # The original CString assembler chunks above 30000 characters. This small
    # tool deliberately handles the observed small-project case only. Preserve
    # order and refuse the unsupported chunk branch instead of approximating it.
    if sum(len(str(p)) + 3 for p in originals[:-1]) > 30000:
        raise ValueError("input list needs the unsupported vendor chunking branch")

    out.mkdir()  # No parents=True: the caller deliberately chooses a private root.
    frozen_dir = out / "inputs"
    frozen_dir.mkdir()
    report = {"schema": "plcidf.vendor-relocatable-link.v1", "ok": False,
              "status": "failed", "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "two-stage-relocatable-object-link", "downloadable": False,
              "st_compiled": False, "runtime_verified": False,
              "output_dir": str(out), "limitations": LIMITATIONS.copy(),
              "linker": {"path": str(linker), "resolved_path": str(linker.resolve()),
                         "sha256": _sha(linker_bytes)},
              "inputs": [], "commands": [], "outputs": []}
    frozen = []

    def invoke(argv):
        command = {"argv": argv, "timeout_s": timeout_s, "attempt": 1}
        report["commands"].append(command)
        try:
            completed = runner(argv, cwd=str(out), stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=timeout_s,
                               check=False)
            command.update(returncode=completed.returncode, stdout=completed.stdout,
                           stderr=completed.stderr)
        except subprocess.TimeoutExpired:
            command.update(status="timeout", partial_output_untrusted=True)
            raise RuntimeError("local linker timed out; command was not replayed")
        if completed.returncode != 0:
            raise RuntimeError(f"local linker exited {completed.returncode}")
        return completed.stdout

    try:
        for i, (original, data, inspected) in enumerate(zip(originals, snapshots, inspections)):
            role = f"pou-{i:03}" if i < len(pous) else "syslib" if i == len(pous) else "stclib"
            dest = frozen_dir / f"{role}.o"
            with dest.open("xb") as stream:
                stream.write(data)
            dest.chmod(0o444)
            frozen.append(dest)
            report["inputs"].append({"role": role, "original": str(original),
                                     "frozen": str(dest), "inspection": inspected})
        version = invoke([str(linker), "--version"])
        if "LLD" not in version:
            raise RuntimeError("executable did not identify itself as LLVM LLD")
        report["linker"]["version"] = version.strip()
        stage1, stage2 = out / "stuserprog.o", out / "stprog.o"
        # H530Converter.dll 0x100f6869: POU list (provided order), _SysLib.o.
        # 0x100f6b1a: ONLY the stage-1 object, then _STCLib.o. Both invoke
        # 0x100f6cf0 with '-relocatable %s -o "%s"'; no final executable link.
        for stage, inputs, target in [(1, frozen[:-1], stage1),
                                       (2, [stage1, frozen[-1]], stage2)]:
            invoke([str(linker), "-relocatable", *map(str, inputs), "-o", str(target)])
            data = _read_regular(target, limit=MAX_INPUT_BYTES)
            inspection = _arm_object(data, str(target))
            target.chmod(0o444)
            report["outputs"].append({"stage": stage, "path": str(target),
                                      "inspection": inspection})
        # A successful subprocess cannot silently authorize changed input bytes,
        # a changed linker, or stage-1 replacement before stage 2 consumes it.
        for path, data in zip(originals, snapshots):
            if _read_regular(path, limit=MAX_INPUT_BYTES) != data:
                raise RuntimeError(f"original input changed during experiment: {path}")
        for path, data in zip(frozen, snapshots):
            if _read_regular(path, limit=MAX_INPUT_BYTES) != data:
                raise RuntimeError(f"frozen input changed during experiment: {path}")
        if _sha(_read_regular(linker.resolve(strict=True), limit=128 * 1024 * 1024)) != report["linker"]["sha256"]:
            raise RuntimeError("linker changed during experiment")
        for record in report["outputs"]:
            if _sha(_read_regular(Path(record["path"]), limit=MAX_INPUT_BYTES)) != record["inspection"]["sha256"]:
                raise RuntimeError("linked output changed during experiment")
        report.update(ok=True, status="linked")
    except (OSError, ValueError, RuntimeError) as exc:
        report["error"] = str(exc)
    # Exclusive creation and a fresh directory prevent reuse of a previous
    # success record. A failed partial output remains explicitly failed.
    with (out / "link-report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pou", action="append", required=True,
                        help="POU object; repeat in the explicitly chosen vendor order")
    parser.add_argument("--syslib", required=True, help="explicit ARM _SysLib.o")
    parser.add_argument("--stclib", required=True, help="matching branch ARM _STCLib.o")
    parser.add_argument("--lld", required=True, help="local Linux ld.lld, never a vendor EXE")
    parser.add_argument("--output-dir", required=True, help="new private output directory")
    parser.add_argument("--timeout", type=float, default=10, help="seconds per command; no retries")
    args = parser.parse_args(argv)
    try:
        result = link_application(pous=args.pou, syslib=args.syslib, stclib=args.stclib,
                                  lld=args.lld, output_dir=args.output_dir, timeout_s=args.timeout)
    except (ValueError, OSError) as exc:
        result = {"ok": False, "status": "rejected", "error": str(exc), "downloadable": False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
