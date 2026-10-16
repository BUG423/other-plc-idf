#!/usr/bin/env python3
"""Offline acceptance checks for the PLC port (no GUI or Modbus required)."""

from __future__ import annotations

import ast
import math
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_SOURCE = ROOT / "modbus_realtime.py"
ST_SOURCE = ROOT / "plc" / "FB_BESS_AdaptiveSupervisor.st"


def load_reference():
    """Load only control() and CONTROLLER_P, avoiding the Tkinter main loop."""
    tree = ast.parse(PYTHON_SOURCE.read_text(encoding="utf-8-sig"))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if any(alias.name == "math" for alias in node.names):
                selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "control":
            selected.append(node)
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == "CONTROLLER_P" for t in node.targets):
                selected.append(node)

    namespace = {}
    module = ast.Module(body=selected, type_ignores=[])
    exec(compile(module, str(PYTHON_SOURCE), "exec"), namespace)
    return namespace["control"], namespace["CONTROLLER_P"]


def assert_close(actual, expected, name, tolerance=1e-11):
    if not math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance):
        raise AssertionError(f"{name}: expected {expected}, got {actual}")


def check_constants(params):
    st_text = ST_SOURCE.read_text(encoding="utf-8")
    constants = {
        name: float(value)
        for name, value in re.findall(
            r"\b(C_[A-Z0-9_]+)\s*:\s*LREAL\s*:=\s*([-+0-9.eE]+)\s*;",
            st_text,
        )
    }
    mapping = {
        "C_VON": 1,
        "C_VOFF": 2,
        "C_VREF": 3,
        "C_LON": 4,
        "C_LOFF": 5,
        "C_LTARGET": 6,
        "C_PMAX": 7,
        "C_QMAX": 8,
        "C_SOCMIN": 9,
        "C_SOCMAX": 10,
        "C_SMARGIN": 11,
        "C_QREACH": 12,
        "C_DWELL": 13,
        "C_RECOVER": 14,
        "C_GQ": 15,
        "C_GP": 16,
        "C_SVP_INITIAL": 17,
        "C_SVQ_INITIAL": 18,
        "C_ALPHA": 19,
        "C_DMIN": 20,
        "C_WINDOW": 21,
        "C_DOM": 22,
        "C_SVPMIN": 23,
        "C_SVPMAX": 24,
        "C_SVQMIN": 25,
        "C_SVQMAX": 26,
        "C_DVMAX": 27,
        "C_KP_INITIAL": 28,
        "C_KQ_INITIAL": 29,
        "C_KALPHA": 30,
        "C_KMIN": 31,
        "C_KMAX": 32,
        "C_CINC": 33,
        "C_EPS": 34,
    }
    if set(constants) != set(mapping):
        raise AssertionError(
            f"PLC constant set differs: missing={set(mapping)-set(constants)}, "
            f"extra={set(constants)-set(mapping)}"
        )
    for name, index in mapping.items():
        assert_close(constants[name], params[index], name)


def step(control, params, values, state):
    return control(*values, state, params)


def check_modes(control, params):
    cases = [
        ("normal", (1.0, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0), 0.0, 0.0, 0.0),
        ("low", (0.94, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0), 0.0, 0.3, 1.0),
        (
            "forward overload",
            (1.0, 1.2, 0.2, 1.1, 0.6, 0.0, 0.0),
            0.16855232923821894,
            0.0,
            2.0,
        ),
        (
            "reverse overload",
            (1.0, -1.2, 0.2, 1.1, 0.6, 0.0, 0.0),
            -0.16855232923821894,
            0.0,
            3.0,
        ),
        ("discharge SOC block", (1.0, 1.2, 0.2, 1.1, 0.11, 0.0, 0.0), 0.0, 0.0, 0.0),
        ("charge SOC block", (1.0, -1.2, 0.2, 1.1, 0.89, 0.0, 0.0), 0.0, 0.0, 0.0),
    ]
    for name, values, expected_p, expected_q, expected_mode in cases:
        result = step(control, params, values, [0.0] * 20)
        assert_close(result[0], expected_p, f"{name} Pcmd")
        assert_close(result[1], expected_q, f"{name} Qcmd")
        assert_close(result[2], expected_mode, f"{name} mode")


def check_sequences(control, params):
    # At 5 ms per scan, 10 saturation scans must latch after exactly 50 ms.
    state = [0.0] * 20
    for scan in range(1, 11):
        result = step(control, params, (0.94, 0.5, 0.2, 0.5, 0.6, 0.0, 0.4), state)
        state = result[3]
        if scan < 10:
            assert_close(result[0], 0.0, f"Q latch scan {scan} Pcmd")
        else:
            assert_close(result[0], 0.21666666666666687, "Q latch scan 10 Pcmd")
            assert_close(state[4], 1.0, "Q latch state")

    # Low voltage must clear only after 10 consecutive healthy scans (50 ms).
    state = [0.0] * 20
    state = step(control, params, (0.94, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0), state)[3]
    for scan in range(1, 11):
        result = step(control, params, (0.98, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0), state)
        state = result[3]
        expected_mode = 1.0 if scan < 10 else 0.0
        assert_close(result[2], expected_mode, f"recovery scan {scan} mode")

    # One clean active-power-dominant window updates sVP and kP only.
    state = [0.0] * 20
    state = step(control, params, (1.0, 0.5, 0.1, 0.5, 0.6, 0.0, 0.0), state)[3]
    for _ in range(9):
        state = step(control, params, (1.0, 0.5, 0.1, 0.5, 0.6, 0.0, 0.0), state)[3]
    result = step(control, params, (1.02, 0.4, 0.1, 0.5, 0.6, 0.1, 0.0), state)
    assert_close(result[4], 0.1264, "estimator sVP")
    assert_close(result[5], 0.1, "estimator sVQ")
    assert_close(result[6], 1.0, "estimator kP")
    assert_close(result[7], 1.0, "estimator kQ")
    assert_close(result[8], 0.025, "estimator confidence")


def main():
    control, params = load_reference()
    check_constants(params)
    check_modes(control, params)
    check_sequences(control, params)
    print("PASS: PLC constants and controller acceptance vectors match the Python/SLX reference.")


if __name__ == "__main__":
    main()
