#!/usr/bin/env python3
"""Emit a separate, isolated Easy521 hardware-bench MAIN on stdout.

Requires explicit overwrite permission and physically isolated loads/outputs.
Reuses fixed acceptance vectors, not the simulator-only deployment artifact.
Every executed scan calls the unchanged production FB once. M8002 restarts
the run after STOP/RUN (vendor programming manual, page 137). Revision 3
also invalidates old simulator results. Completion continuously resets and
disables the controller, without any physical I/O or network command writes.
Store emitted ST with apply_patch; do not download the simulator TEST project.
"""

from pathlib import Path

from build_vendor_acceptance import CASES, generate


def generate_bench():
    source = generate()
    start = source.index("IF D523 <> 1 THEN")
    lines = source[start:].splitlines()
    assert lines[1] == "IF D504 = 521 THEN RETURN; END_IF;"
    lines = lines[2:]
    main_path = Path(__file__).resolve().parent.parent / "MAIN_5ms_body.st"
    production_call = main_path.read_text(encoding="utf-8").splitlines()[2:]
    result = [
        "(* ISOLATED HARDWARE BENCH ONLY. Not a field application.",
        "   Explicit overwrite permission and isolated loads required.",
        "   No physical I/O writes and no outbound device commands.",
        "   Same production FB and 28 fixed vendor acceptance vectors.",
        "   M8002 restarts on the first RUN scan; revision 3 rejects stale SIM data.",
        "   D500=passed D501=failed D502=total D503=first failed case",
        "   D504=521 finished D505=case D506=acceptance scans D514=failed step",
        "   D523=3 hardware bench; D524=1 confirms completion reset/zero state.",
        "   D601..D628=per-case result; restore disabled DEV after testing. *)",
        "IF M8002 OR (D523 <> 3) THEN",
        "    D510 := 0; D504 := 0; D523 := 3; D524 := 0;",
    ]
    result.extend(f"    D{600+cid} := 0;" for cid in range(1, len(CASES)+1))
    result += [
        "END_IF;",
        "IF D504 = 521 THEN",
        "    g_xBESS_Enable := FALSE;",
        "    g_xBESS_MeasurementsValid := FALSE;",
        "    g_xBESS_Reset := TRUE;",
    ]
    result.extend("    " + line for line in production_call)
    result += [
        "    D524 := 0;",
        "    IF (g_rBESS_Pcmd = 0.0) AND (g_rBESS_Qcmd = 0.0)",
        "        AND (g_iBESS_Mode = 0) AND (NOT g_xBESS_ControllerReady) THEN",
        "        D524 := 1;",
        "    END_IF;",
        "    RETURN;",
        "END_IF;",
    ]
    result.extend(lines)
    assert len(result) <= 1000, len(result)
    assert "OFFLINE SIMULATOR ONLY" not in "\n".join(result)
    return "\n".join(result) + "\n"


if __name__ == "__main__":
    print(generate_bench(), end="")
