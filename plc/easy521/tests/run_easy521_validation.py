#!/usr/bin/env python3
"""Compatibility and REAL32 numerical tests for the Easy521 AutoShop port."""

from __future__ import annotations

import ast
import csv
import math
import random
import re
import struct
import sys
import unittest
from pathlib import Path

import numpy as np


TEST_DIR = Path(__file__).resolve().parent
TARGET_DIR = TEST_DIR.parent
ROOT = TARGET_DIR.parents[1]
sys.path.insert(0, str(TEST_DIR))

from real32_mirror import Real32Mirror  # noqa: E402


def load_reference():
    path = ROOT / "modbus_realtime.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) and any(a.name == "math" for a in node.names):
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name == "control":
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CONTROLLER_P" for t in node.targets):
            selected.append(node)
    namespace = {}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["control"], namespace["CONTROLLER_P"]


CONTROL, PARAMS = load_reference()


class SourceCompatibilityTests(unittest.TestCase):
    def test_body_uses_only_easy521_supported_data_types(self):
        body = (TARGET_DIR / "FB_BESS_AdaptiveSupervisor_Easy521_body.st").read_text(encoding="utf-8")
        code = re.sub(r"\(\*.*?\*\)", "", body, flags=re.S).upper()
        for forbidden in ("LREAL", "USINT", "UINT", "UDINT", "LINT", "ULINT", "DWORD", "LWORD"):
            self.assertNotRegex(code, rf"\b{forbidden}\b")
        for required in ("ABS(", "SQRT(", "MIN(", "MAX(", "RETURN;"):
            self.assertIn(required, code)
        self.assertEqual(len(re.findall(r"\bIF\b", code)), len(re.findall(r"\bEND_IF\b", code)))
        self.assertEqual(code.count("("), code.count(")"))

    def test_variable_tables_use_supported_types(self):
        allowed = {"BOOL", "INT", "DINT", "REAL", "FB_BESS_AdaptiveSupervisor_Easy521"}
        for filename in ("FB_BESS_AdaptiveSupervisor_Easy521_variables.csv", "MAIN_5ms_variables.csv"):
            with (TARGET_DIR / filename).open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    self.assertIn(row["data_type"], allowed, f"{filename}: {row}")

    def test_real32_modbus_map(self):
        with (TARGET_DIR / "modbus_register_map_real32.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        addresses = []
        for row in rows:
            start, end = map(int, row["holding_registers"].split("-"))
            self.assertEqual(end - start + 1, 2)
            self.assertEqual(int(row["register_count"]), 2)
            addresses.extend((start, end))
        self.assertEqual(addresses, list(range(18)))
        for value in (-1.0, 0.0, 0.95, 1.0, 1.25):
            raw = struct.pack(">f", value)
            regs = struct.unpack(">2H", raw)
            decoded = struct.unpack(">f", struct.pack(">2H", *regs))[0]
            self.assertEqual(decoded, np.float32(value))


class DirectedReal32Tests(unittest.TestCase):
    def test_modes_and_expected_commands(self):
        cases = [
            ((1.0, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0), 0, 0.0, 0.0),
            ((0.94, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0), 1, 0.0, 0.3),
            ((1.0, 1.2, 0.2, 1.1, 0.6, 0.0, 0.0), 2, 0.16855232923821894, 0.0),
            ((1.0, -1.2, 0.2, 1.1, 0.6, 0.0, 0.0), 3, -0.16855232923821894, 0.0),
        ]
        for values, mode, pcmd, qcmd in cases:
            with self.subTest(mode=mode):
                out = Real32Mirror().step(*values)
                self.assertEqual(out.mode, mode)
                self.assertAlmostEqual(out.pcmd, pcmd, delta=2e-6)
                self.assertAlmostEqual(out.qcmd, qcmd, delta=2e-6)

    def test_exact_10_scan_latch_and_recovery(self):
        mirror = Real32Mirror()
        for scan in range(1, 11):
            out = mirror.step(0.94, 0.5, 0.2, 0.5, 0.6, 0.0, 0.4)
            self.assertEqual(out.pcmd == 0.0, scan < 10)
        self.assertTrue(mirror.q_latched)
        mirror = Real32Mirror()
        mirror.step(0.94, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0)
        for scan in range(1, 11):
            out = mirror.step(0.98, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0)
            self.assertEqual(out.mode, 1 if scan < 10 else 0)

    def test_fail_safe_freezes_state(self):
        mirror = Real32Mirror()
        mirror.step(0.9, 0.5, 0.2, 0.8, 0.6, 0.0, 0.4)
        state = mirror.state_vector()
        out = mirror.step(0, 0, 0, 0, 0, 0, 0, input_valid=False)
        self.assertEqual((out.pcmd, out.qcmd, out.mode), (0.0, 0.0, 0))
        self.assertEqual(mirror.state_vector(), state)

    def test_six_second_real32_closed_loop(self):
        mirror = Real32Mirror()
        ts = 0.005
        pess = qess = 0.0
        soc = 0.6
        rows = []

        def disturbance(t):
            if t < 1.0:
                return "normal-1", 1.00, 0.50, 0.15
            if t < 2.0:
                return "low", 0.91, 0.60, 0.15
            if t < 3.0:
                return "recovery", 1.00, 0.50, 0.15
            if t < 4.0:
                return "forward", 1.00, 1.25, 0.20
            if t < 5.0:
                return "normal-2", 1.00, 0.50, 0.15
            return "reverse", 1.00, -1.25, 0.20

        for scan in range(1200):
            phase, vnative, pnative, qnative = disturbance(scan * ts)
            vpu = vnative + 0.12 * pess + 0.10 * qess
            pline = pnative - pess
            qline = qnative - qess
            loading = math.hypot(pline, qline)
            out = mirror.step(vpu, pline, qline, loading, soc, pess, qess)
            rows.append((phase, vpu, pline, soc, pess, qess, out))

            qlim = min(0.4, max(-0.4, out.qcmd))
            pavailable = min(0.8, math.sqrt(max(1.0 - qlim * qlim, 0.0)))
            plim = min(pavailable, max(-pavailable, out.pcmd))
            if soc <= 0.1 and plim > 0.0:
                plim = 0.0
            elif soc >= 0.9 and plim < 0.0:
                plim = 0.0
            pnext = math.exp(-0.005 / 0.1) * pess + (1.0 - math.exp(-0.005 / 0.1)) * plim
            qnext = math.exp(-0.005 / 0.05) * qess + (1.0 - math.exp(-0.005 / 0.05)) * qlim
            smag = math.hypot(pnext, qnext)
            if smag > 1.0:
                pnext /= smag
                qnext /= smag
            dsoc = (-(pnext * 2.0 / 0.95) if pnext >= 0.0 else -(0.95 * pnext * 2.0)) / (3600.0 * 0.5)
            soc = min(0.9, max(0.1, soc + ts * dsoc))
            pess, qess = pnext, qnext

        by_phase = {}
        for row in rows:
            by_phase.setdefault(row[0], []).append(row)
        self.assertEqual({row[-1].mode for row in rows}, {0, 1, 2, 3})
        self.assertGreater(max(row[1] for row in by_phase["low"]), by_phase["low"][0][1] + 0.05)
        self.assertEqual(by_phase["recovery"][8][-1].mode, 1)
        self.assertEqual(by_phase["recovery"][9][-1].mode, 0)
        self.assertLess(min(row[2] for row in by_phase["forward"][40:]), 1.05)
        self.assertGreater(max(row[2] for row in by_phase["reverse"][40:]), -1.05)
        self.assertTrue(0.1 <= soc <= 0.9)


class NumericalComparisonTests(unittest.TestCase):
    SCANS = 100_000

    def test_real32_against_original_double_controller(self):
        rng = random.Random(0x5210808)
        max_p_error = 0.0
        max_q_error = 0.0
        mode_mismatches = 0
        for _ in range(self.SCANS):
            # Independent one-scan cases isolate REAL32 arithmetic from accumulated state divergence.
            values = tuple(map(float, (
                np.float32(rng.uniform(0.82, 1.10)),
                np.float32(rng.uniform(-1.8, 1.8)),
                np.float32(rng.uniform(-0.8, 0.8)),
                np.float32(rng.uniform(0.0, 1.6)),
                np.float32(rng.uniform(0.0, 1.0)),
                np.float32(rng.uniform(-0.8, 0.8)),
                np.float32(rng.uniform(-0.4, 0.4)),
            )))
            expected = CONTROL(*values, [0.0] * 20, PARAMS)
            actual = Real32Mirror().step(*values)
            mode_mismatches += int(actual.mode != int(expected[2]))
            max_p_error = max(max_p_error, abs(actual.pcmd - expected[0]))
            max_q_error = max(max_q_error, abs(actual.qcmd - expected[1]))
            self.assertTrue(-0.800001 <= actual.pcmd <= 0.800001)
            self.assertTrue(-0.000001 <= actual.qcmd <= 0.400001)
            self.assertTrue(all(math.isfinite(x) for x in (
                actual.pcmd, actual.qcmd, actual.svp, actual.svq,
                actual.kp, actual.kq, actual.confidence,
            )))
        self.assertEqual(mode_mismatches, 0)
        self.assertLess(max_p_error, 2e-6)
        self.assertLess(max_q_error, 2e-6)

    def test_long_running_real32_invariants(self):
        rng = random.Random(0xB3550521)
        mirror = Real32Mirror()
        seen = set()
        for scan in range(self.SCANS):
            if scan and scan % 10_000 == 0:
                mirror = Real32Mirror()
            out = mirror.step(
                rng.uniform(0.82, 1.10), rng.uniform(-1.8, 1.8),
                rng.uniform(-0.8, 0.8), rng.uniform(0.0, 1.6),
                rng.uniform(0.0, 1.0), rng.uniform(-0.8, 0.8),
                rng.uniform(-0.4, 0.4),
            )
            seen.add(out.mode)
            self.assertTrue(-0.800001 <= out.pcmd <= 0.800001)
            self.assertTrue(-0.000001 <= out.qcmd <= 0.400001)
            self.assertTrue(0.009999 <= out.svp <= 0.400001)
            self.assertTrue(0.009999 <= out.svq <= 0.400001)
            self.assertTrue(0.199999 <= out.kp <= 1.500001)
            self.assertTrue(0.199999 <= out.kq <= 1.500001)
            self.assertTrue(0.0 <= out.confidence <= 1.000001)
        self.assertEqual(seen, {0, 1, 2, 3})


if __name__ == "__main__":
    unittest.main(verbosity=2)
