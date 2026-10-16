#!/usr/bin/env python3
"""Repeatable requirements, differential, boundary and robustness tests."""

from __future__ import annotations

import ast
import csv
import math
import random
import re
import struct
import sys
import time
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
PLC_DIR = TEST_DIR.parent
ROOT = PLC_DIR.parent
sys.path.insert(0, str(TEST_DIR))

from plc_mirror import PlcMirror  # noqa: E402


def load_reference():
    source_path = ROOT / "modbus_realtime.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
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
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source_path), "exec"), namespace)
    return namespace["control"], namespace["CONTROLLER_P"]


REFERENCE_CONTROL, REFERENCE_PARAMS = load_reference()


def assert_number_close(test, actual, expected, label, tolerance=2e-12):
    test.assertTrue(math.isfinite(actual), f"{label} is not finite: {actual}")
    test.assertTrue(
        math.isclose(actual, expected, rel_tol=tolerance, abs_tol=tolerance),
        f"{label}: expected {expected:.17g}, got {actual:.17g}",
    )


def compare_active_step(test, mirror, ref_state, values, label):
    expected = REFERENCE_CONTROL(*values, ref_state, REFERENCE_PARAMS)
    actual = mirror.step(*values)
    actual_outputs = (
        actual.pcmd,
        actual.qcmd,
        float(actual.mode),
        actual.svp,
        actual.svq,
        actual.kp,
        actual.kq,
        actual.confidence,
    )
    expected_outputs = expected[0:3] + expected[4:9]
    for index, (got, want) in enumerate(zip(actual_outputs, expected_outputs)):
        assert_number_close(test, got, want, f"{label} output[{index}]")
    for index, (got, want) in enumerate(zip(mirror.state_vector(), expected[3])):
        assert_number_close(test, got, want, f"{label} state[{index}]")
    return expected[3], actual


class RequirementsAndStaticTests(unittest.TestCase):
    def test_st_structure_is_balanced_and_complete(self):
        text = (PLC_DIR / "FB_BESS_AdaptiveSupervisor.st").read_text(encoding="utf-8")
        code = re.sub(r"\(\*.*?\*\)", "", text, flags=re.S)
        self.assertEqual(code.count("("), code.count(")"))
        self.assertEqual(len(re.findall(r"\bIF\b", code)), len(re.findall(r"\bEND_IF\b", code)))
        self.assertEqual(len(re.findall(r"\bVAR(?:_INPUT|_OUTPUT|\s+CONSTANT)?\b", code)), 4)
        self.assertEqual(len(re.findall(r"\bEND_VAR\b", code)), 4)
        self.assertEqual(code.count("FUNCTION_BLOCK"), 2)  # opening + END_FUNCTION_BLOCK token
        for required in (
            "xInputValid",
            "lrEstimatorCount",
            "lrVnativeEstimate",
            "lrNativeLoading",
            "lrQLatched",
            "lrConfidence",
        ):
            self.assertIn(required, code)

    def test_all_34_used_parameters_match_reference_vector(self):
        text = (PLC_DIR / "FB_BESS_AdaptiveSupervisor.st").read_text(encoding="utf-8")
        parsed = {
            name: float(value)
            for name, value in re.findall(
                r"\b(C_[A-Z0-9_]+)\s*:\s*LREAL\s*:=\s*([-+0-9.eE]+)\s*;",
                text,
            )
        }
        mirror_values = {
            f"C_{name}": value
            for name, value in vars(PlcMirror).items()
            if name.isupper() and isinstance(value, (float, int))
        }
        self.assertEqual(parsed, mirror_values)
        expected = REFERENCE_PARAMS[1:]
        self.assertEqual(len(parsed), len(expected))
        ordered_names = [
            "C_VON", "C_VOFF", "C_VREF", "C_LON", "C_LOFF", "C_LTARGET",
            "C_PMAX", "C_QMAX", "C_SOCMIN", "C_SOCMAX", "C_SMARGIN", "C_QREACH",
            "C_DWELL", "C_RECOVER", "C_GQ", "C_GP", "C_SVP_INITIAL", "C_SVQ_INITIAL",
            "C_ALPHA", "C_DMIN", "C_WINDOW", "C_DOM", "C_SVPMIN", "C_SVPMAX",
            "C_SVQMIN", "C_SVQMAX", "C_DVMAX", "C_KP_INITIAL", "C_KQ_INITIAL",
            "C_KALPHA", "C_KMIN", "C_KMAX", "C_CINC", "C_EPS",
        ]
        for name, want in zip(ordered_names, expected):
            self.assertEqual(parsed[name], want, name)

    def test_modbus_map_is_contiguous_and_binary64(self):
        with (PLC_DIR / "modbus_register_map.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 9)
        ranges = []
        for row in rows:
            start, end = map(int, row["holding_registers"].split("-"))
            self.assertEqual(int(row["register_count"]), 4)
            self.assertEqual(end - start + 1, 4)
            self.assertEqual(row["encoding"], "IEEE-754 binary64 big-endian")
            ranges.extend(range(start, end + 1))
        self.assertEqual(ranges, list(range(36)))

    def test_binary64_register_round_trip(self):
        for value in (-2.0, -0.0, 0.1, 0.95, 1.0, 12345.6789):
            raw = struct.pack(">d", value)
            registers = struct.unpack(">4H", raw)
            decoded = struct.unpack(">d", struct.pack(">4H", *registers))[0]
            self.assertEqual(decoded, value)


class DirectedBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.mirror = PlcMirror()
        self.ref_state = [0.0] * 20

    def run_step(self, values, label="directed"):
        self.ref_state, output = compare_active_step(self, self.mirror, self.ref_state, values, label)
        return output

    def test_four_operating_modes(self):
        independent_cases = [
            ((1.00, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0), 0, 0.0, 0.0),
            ((0.94, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0), 1, 0.0, 0.3),
            ((1.00, 1.2, 0.2, 1.1, 0.6, 0.0, 0.0), 2, 0.16855232923821894, 0.0),
            ((1.00, -1.2, 0.2, 1.1, 0.6, 0.0, 0.0), 3, -0.16855232923821894, 0.0),
        ]
        for index, (values, mode, pcmd, qcmd) in enumerate(independent_cases):
            with self.subTest(mode=mode):
                mirror = PlcMirror()
                state, output = compare_active_step(self, mirror, [0.0] * 20, values, f"mode-{index}")
                self.assertEqual(output.mode, mode)
                assert_number_close(self, output.pcmd, pcmd, "Pcmd")
                assert_number_close(self, output.qcmd, qcmd, "Qcmd")
                self.assertEqual(len(state), 20)

    def test_low_voltage_entry_and_exit_strict_boundaries(self):
        output = self.run_step((0.95, 0.5, 0.1, 0.5, 0.6, 0.0, 0.0), "VON exact")
        self.assertEqual(output.mode, 0)
        output = self.run_step((math.nextafter(0.95, -math.inf), 0.5, 0.1, 0.5, 0.6, 0.0, 0.0), "VON below")
        self.assertEqual(output.mode, 1)
        for scan in range(10):
            output = self.run_step((0.975, 0.5, 0.1, 0.5, 0.6, 0.0, 0.0), f"VOFF exact {scan}")
            self.assertEqual(output.mode, 1)
        for scan in range(9):
            output = self.run_step((math.nextafter(0.975, math.inf), 0.5, 0.1, 0.5, 0.6, 0.0, 0.0), f"recovery {scan}")
            self.assertEqual(output.mode, 1)
        output = self.run_step((math.nextafter(0.975, math.inf), 0.5, 0.1, 0.5, 0.6, 0.0, 0.0), "recovery 10")
        self.assertEqual(output.mode, 0)

    def test_overload_hysteresis_boundaries(self):
        output = self.run_step((1.0, 1.0, 0.0, 1.0, 0.6, 0.0, 0.0), "LON exact")
        self.assertEqual(output.mode, 0)
        output = self.run_step((1.0, 1.0, 0.0, math.nextafter(1.0, math.inf), 0.6, 0.0, 0.0), "LON above")
        self.assertEqual(output.mode, 2)
        output = self.run_step((1.0, 1.0, 0.0, 0.95, 0.6, 0.0, 0.0), "LOFF exact")
        self.assertEqual(output.mode, 2)
        output = self.run_step((1.0, 1.0, 0.0, math.nextafter(0.95, -math.inf), 0.6, 0.0, 0.0), "LOFF below")
        self.assertEqual(output.mode, 0)

    def test_soc_margin_strict_boundaries(self):
        discharge_threshold = PlcMirror.SOCMIN + PlcMirror.SMARGIN
        for soc, expected_mode in (
            (discharge_threshold, 0),
            (math.nextafter(discharge_threshold, math.inf), 2),
        ):
            mirror = PlcMirror()
            _, output = compare_active_step(
                self, mirror, [0.0] * 20, (1.0, 1.2, 0.2, 1.1, soc, 0.0, 0.0), f"SOC discharge {soc}"
            )
            self.assertEqual(output.mode, expected_mode)
        charge_threshold = PlcMirror.SOCMAX - PlcMirror.SMARGIN
        for soc, expected_mode in (
            (charge_threshold, 0),
            (math.nextafter(charge_threshold, -math.inf), 3),
        ):
            mirror = PlcMirror()
            _, output = compare_active_step(
                self, mirror, [0.0] * 20, (1.0, -1.2, 0.2, 1.1, soc, 0.0, 0.0), f"SOC charge {soc}"
            )
            self.assertEqual(output.mode, expected_mode)

    def test_q_saturation_latches_at_exactly_50_ms(self):
        for scan in range(1, 11):
            output = self.run_step((0.94, 0.5, 0.2, 0.5, 0.6, 0.0, 0.4), f"Q latch {scan}")
            self.assertEqual(output.mode, 1)
            self.assertEqual(output.pcmd == 0.0, scan < 10)
        self.assertEqual(self.mirror.q_latched, 1.0)
        self.assertEqual(self.mirror.uv_count, 10.0)

    def test_q_saturation_counter_decays_and_requires_both_actual_and_command(self):
        for scan in range(6):
            self.run_step((0.90, 0.5, 0.2, 0.5, 0.6, 0.0, 0.4), f"rise {scan}")
        self.assertEqual(self.mirror.uv_count, 6.0)
        for scan in range(3):
            self.run_step((0.94, 0.5, 0.2, 0.5, 0.6, 0.0, 0.0), f"decay {scan}")
        self.assertEqual(self.mirror.uv_count, 3.0)
        for scan in range(7):
            self.run_step((0.90, 0.5, 0.2, 0.5, 0.6, 0.0, 0.4), f"relatch {scan}")
        self.assertEqual(self.mirror.q_latched, 1.0)

    def test_estimator_active_and_reactive_dominant_windows(self):
        base = (1.0, 0.5, 0.1, 0.5, 0.6, 0.0, 0.0)
        self.run_step(base, "est P init")
        for scan in range(9):
            self.run_step(base, f"est P hold {scan}")
        output = self.run_step((1.02, 0.4, 0.1, 0.5, 0.6, 0.1, 0.0), "est P update")
        assert_number_close(self, output.svp, 0.1264, "sVP update")
        assert_number_close(self, output.kp, 1.0, "kP update")
        assert_number_close(self, output.confidence, 0.025, "P confidence")

        mirror = PlcMirror()
        ref_state = [0.0] * 20
        base = (1.0, 0.5, 0.1, 0.5, 0.6, 0.0, 0.0)
        ref_state, _ = compare_active_step(self, mirror, ref_state, base, "est Q init")
        for scan in range(9):
            ref_state, _ = compare_active_step(self, mirror, ref_state, base, f"est Q hold {scan}")
        ref_state, output = compare_active_step(
            self, mirror, ref_state, (1.02, 0.5, 0.0, 0.5, 0.6, 0.0, 0.1), "est Q update"
        )
        assert_number_close(self, output.svq, 0.108, "sVQ update")
        assert_number_close(self, output.kq, 1.0, "kQ update")
        assert_number_close(self, output.confidence, 0.025, "Q confidence")

    def test_estimator_rejects_small_large_and_out_of_range_samples(self):
        cases = [
            ("below DMIN", (1.0002, 0.498, 0.1, 0.5, 0.6, 0.002, 0.0)),
            ("above DVMAX", (1.04, 0.4, 0.1, 0.5, 0.6, 0.1, 0.0)),
            ("sample above SVPMAX", (1.03, 0.49, 0.1, 0.5, 0.6, 0.01, 0.0)),
            ("k sample above KMAX", (1.001, 0.3, 0.1, 0.5, 0.6, 0.1, 0.0)),
        ]
        for label, changed in cases:
            with self.subTest(label=label):
                mirror = PlcMirror()
                state = [0.0] * 20
                base = (1.0, 0.5, 0.1, 0.5, 0.6, 0.0, 0.0)
                state, _ = compare_active_step(self, mirror, state, base, label + " init")
                for scan in range(9):
                    state, _ = compare_active_step(self, mirror, state, base, f"{label} hold {scan}")
                state, output = compare_active_step(self, mirror, state, changed, label + " change")
                self.assertGreaterEqual(output.svp, PlcMirror.SVPMIN)
                self.assertLessEqual(output.svp, PlcMirror.SVPMAX)
                self.assertGreaterEqual(output.kp, PlcMirror.KMIN)
                self.assertLessEqual(output.kp, PlcMirror.KMAX)

    def test_zero_loading_and_zero_line_power_are_division_safe(self):
        for values in (
            (1.0, 0.0, 0.0, 0.0, 0.6, 0.0, 0.0),
            (1.0, 0.0, 0.0, 1.2, 0.6, 0.0, 0.0),
            (1.0, 1e-7, 0.0, 1.2, 0.6, 0.0, 0.0),
        ):
            output = self.run_step(values, "zero guard")
            self.assertEqual(output.mode, 0)
            self.assertTrue(math.isfinite(output.pcmd))
            self.assertTrue(math.isfinite(output.qcmd))

    def test_reset_disable_and_invalid_input_are_fail_safe_and_freeze_state(self):
        for _ in range(4):
            self.run_step((0.9, 0.5, 0.2, 0.8, 0.6, 0.0, 0.4), "prepare state")
        before = self.mirror.state_vector()
        output = self.mirror.step(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, input_valid=False)
        self.assertEqual((output.pcmd, output.qcmd, output.mode), (0.0, 0.0, 0))
        self.assertEqual(self.mirror.state_vector(), before)
        output = self.mirror.step(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, enable=False)
        self.assertEqual((output.pcmd, output.qcmd, output.mode), (0.0, 0.0, 0))
        self.assertEqual(self.mirror.state_vector(), before)
        output = self.mirror.step(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, reset=True)
        self.assertEqual((output.pcmd, output.qcmd, output.mode, output.ready), (0.0, 0.0, 0, False))
        self.assertEqual(self.mirror.state_vector(), [0.0] * 20)


class RandomDifferentialAndPropertyTests(unittest.TestCase):
    RANDOM_SCANS = 100_000

    def test_100k_scan_differential_and_invariants(self):
        rng = random.Random(0xB3552026)
        mirror = PlcMirror()
        ref_state = [0.0] * 20
        seen_modes = set()
        for scan in range(self.RANDOM_SCANS):
            if scan and scan % 10_000 == 0:
                mirror = PlcMirror()
                ref_state = [0.0] * 20

            # Broad but finite engineering envelope, including abnormal values.
            values = (
                rng.uniform(0.82, 1.10),
                rng.uniform(-1.8, 1.8),
                rng.uniform(-0.8, 0.8),
                rng.uniform(0.0, 1.6),
                rng.uniform(0.0, 1.0),
                rng.uniform(-0.8, 0.8),
                rng.uniform(-0.4, 0.4),
            )
            ref_state, output = compare_active_step(self, mirror, ref_state, values, f"random scan {scan}")
            seen_modes.add(output.mode)
            self.assertGreaterEqual(output.pcmd, -PlcMirror.PMAX - 1e-14)
            self.assertLessEqual(output.pcmd, PlcMirror.PMAX + 1e-14)
            self.assertGreaterEqual(output.qcmd, -1e-14)
            self.assertLessEqual(output.qcmd, PlcMirror.QMAX + 1e-14)
            self.assertGreaterEqual(output.svp, PlcMirror.SVPMIN)
            self.assertLessEqual(output.svp, PlcMirror.SVPMAX)
            self.assertGreaterEqual(output.svq, PlcMirror.SVQMIN)
            self.assertLessEqual(output.svq, PlcMirror.SVQMAX)
            self.assertGreaterEqual(output.kp, PlcMirror.KMIN)
            self.assertLessEqual(output.kp, PlcMirror.KMAX)
            self.assertGreaterEqual(output.kq, PlcMirror.KMIN)
            self.assertLessEqual(output.kq, PlcMirror.KMAX)
            self.assertGreaterEqual(output.confidence, 0.0)
            self.assertLessEqual(output.confidence, 1.0 + 1e-14)
        self.assertEqual(seen_modes, {0, 1, 2, 3})


class ClosedLoopPlantSimulationTests(unittest.TestCase):
    def test_six_second_bess_and_feeder_scenario(self):
        """Exercise the controller with the SLX BESS dynamics in closed loop."""
        ts = 0.005
        srated = 1.0
        pmax = 0.8
        qmax = 0.4
        socmin = 0.1
        socmax = 0.9
        energy_mwh = 0.5
        eta_dis = 0.95
        eta_ch = 0.95
        tau_p = 0.1
        tau_q = 0.05
        sbase_mw = 2.0
        line_limit_pu = 1.0
        physical_svp = 0.12
        physical_svq = 0.10
        physical_kp = 1.0
        physical_kq = 1.0

        mirror = PlcMirror()
        ref_state = [0.0] * 20
        pess = 0.0
        qess = 0.0
        soc = 0.6
        traces = []

        def disturbance(t):
            if t < 1.0:
                return "normal-1", 1.00, 0.50, 0.15
            if t < 2.0:
                return "low-voltage", 0.91, 0.60, 0.15
            if t < 3.0:
                return "recovery", 1.00, 0.50, 0.15
            if t < 4.0:
                return "forward-overload", 1.00, 1.25, 0.20
            if t < 5.0:
                return "normal-2", 1.00, 0.50, 0.15
            return "reverse-overload", 1.00, -1.25, 0.20

        for scan in range(round(6.0 / ts)):
            t = scan * ts
            phase, vnative, pnative, qnative = disturbance(t)
            vpu = vnative + physical_svp * pess + physical_svq * qess
            pline = pnative - physical_kp * pess
            qline = qnative - physical_kq * qess
            sline = math.sqrt(pline * pline + qline * qline)
            loading = sline / line_limit_pu
            values = (vpu, pline, qline, loading, soc, pess, qess)
            ref_state, output = compare_active_step(
                self, mirror, ref_state, values, f"closed-loop scan {scan}"
            )
            traces.append((phase, t, vpu, pline, qline, loading, soc, pess, qess, output))

            # Exact BESS Equivalent Model equations from the SLX chart.
            qlim = min(qmax, max(-qmax, output.qcmd))
            pavailable = min(pmax, math.sqrt(max(srated * srated - qlim * qlim, 0.0)))
            plim = min(pavailable, max(-pavailable, output.pcmd))
            if soc <= socmin and plim > 0.0:
                plim = 0.0
            elif soc >= socmax and plim < 0.0:
                plim = 0.0
            ap = math.exp(-ts / max(tau_p, ts))
            aq = math.exp(-ts / max(tau_q, ts))
            pnext = ap * pess + (1.0 - ap) * plim
            qnext = aq * qess + (1.0 - aq) * qlim
            smag = math.sqrt(pnext * pnext + qnext * qnext)
            if smag > srated:
                pnext /= smag
                qnext /= smag
            if soc <= socmin and pnext > 0.0:
                pnext = 0.0
            elif soc >= socmax and pnext < 0.0:
                pnext = 0.0
            if pnext >= 0.0:
                dsoc = -(pnext * sbase_mw / eta_dis) / (3600.0 * energy_mwh)
            else:
                dsoc = -(eta_ch * pnext * sbase_mw) / (3600.0 * energy_mwh)
            soc = min(socmax, max(socmin, soc + ts * dsoc))
            pess, qess = pnext, qnext

        by_phase = {}
        for row in traces:
            by_phase.setdefault(row[0], []).append(row)

        all_modes = {row[-1].mode for row in traces}
        self.assertEqual(all_modes, {0, 1, 2, 3})
        self.assertTrue(all(math.isfinite(value) for row in traces for value in row[2:9]))
        self.assertTrue(all(socmin <= row[6] <= socmax for row in traces))

        low = by_phase["low-voltage"]
        self.assertTrue(all(row[-1].mode == 1 for row in low))
        self.assertGreater(max(row[2] for row in low), low[0][2] + 0.05)
        self.assertGreater(max(row[8] for row in low), 0.35)
        self.assertGreater(max(row[-1].pcmd for row in low), 0.1)

        recovery = by_phase["recovery"]
        self.assertEqual(recovery[8][-1].mode, 1)   # 45 ms: still low-voltage mode
        self.assertEqual(recovery[9][-1].mode, 0)   # 50 ms: recovered

        forward = by_phase["forward-overload"]
        self.assertTrue(any(row[-1].mode == 2 for row in forward))
        self.assertGreater(max(row[7] for row in forward), 0.20)
        self.assertLess(min(row[3] for row in forward[40:]), 1.05)

        reverse = by_phase["reverse-overload"]
        self.assertTrue(any(row[-1].mode == 3 for row in reverse))
        self.assertLess(min(row[7] for row in reverse), -0.20)
        self.assertGreater(max(row[3] for row in reverse[40:]), -1.05)


if __name__ == "__main__":
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    elapsed = time.perf_counter() - started
    print(f"\nExecuted {result.testsRun} test methods including {RandomDifferentialAndPropertyTests.RANDOM_SCANS:,} differential scans in {elapsed:.3f} s")
    raise SystemExit(0 if result.wasSuccessful() else 1)
