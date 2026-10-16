#!/usr/bin/env python3
"""Generate a one-FB-call-per-scan AutoShop acceptance MAIN, never hardware code.

Expectations are fixed engineering vectors, not computed by copying the FB.
--check first checks the vectors against the REAL32 reference mirror.
Without --check, emit ST on stdout (the caller stores it with apply_patch).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from real32_mirror import Real32Mirror


INPUTS = {
    "vpu": "g_rBESS_Vpu", "pline": "g_rBESS_Pline",
    "qline": "g_rBESS_Qline", "loading": "g_rBESS_Loading",
    "soc": "g_rBESS_SOC", "pess": "g_rBESS_Pess", "qess": "g_rBESS_Qess",
    "enable": "g_xBESS_Enable", "input_valid": "g_xBESS_MeasurementsValid",
    "reset": "g_xBESS_Reset",
}
OUTPUTS = {
    "pcmd": "g_rBESS_Pcmd", "qcmd": "g_rBESS_Qcmd", "mode": "g_iBESS_Mode",
    "svp": "g_rBESS_SensVP", "svq": "g_rBESS_SensVQ",
    "kp": "g_rBESS_InfluenceP", "kq": "g_rBESS_InfluenceQ",
    "confidence": "g_rBESS_Confidence", "ready": "g_xBESS_ControllerReady",
}
DEFAULTS = dict(vpu=1.0, pline=0.5, qline=0.2, loading=0.5,
                soc=0.6, pess=0.0, qess=0.0, enable=True, input_valid=True, reset=False)
TOL = 0.000002


@dataclass(frozen=True)
class Phase:
    start: int
    end: int
    inputs: dict = field(default_factory=dict)
    expected: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Case:
    name: str
    phases: tuple[Phase, ...]

    @property
    def scans(self):
        return self.phases[-1].end


def phase(start, end=None, inputs=None, **expected):
    return Phase(start, start if end is None else end, inputs or {}, expected)


UV = dict(vpu=0.94)
SAT = dict(vpu=0.94, qess=0.4)
FWD = dict(pline=1.2, loading=1.1)
REV = dict(pline=-1.2, loading=1.1)
CASES = (
    Case("reset_priority_and_startup", (
        phase(1, inputs=dict(reset=True, enable=False, input_valid=False),
              pcmd=0.0, qcmd=0.0, mode=0, ready=False, svp=0.0, confidence=0.0),
        phase(2, pcmd=0.0, qcmd=0.0, mode=0, ready=True, svp=0.12, svq=0.10, kp=1.0, kq=1.0),)),
    Case("normal", (phase(1, mode=0, pcmd=0.0, qcmd=0.0, ready=True),)),
    Case("low_voltage_reactive_first", (phase(1, inputs=UV, mode=1, pcmd=0.0, qcmd=0.3),)),
    Case("forward_overload", (phase(1, inputs=FWD, mode=2, pcmd=0.16855232923821894, qcmd=0.0),)),
    Case("reverse_overload", (phase(1, inputs=REV, mode=3, pcmd=-0.16855232923821894, qcmd=0.0),)),
    Case("strict_voltage_entry_threshold", (
        phase(1, inputs=dict(vpu=0.95), mode=0, pcmd=0.0, qcmd=0.0),
        phase(2, inputs=dict(vpu=0.949), mode=1, pcmd=0.0),)),
    Case("strict_overload_entry_threshold", (
        phase(1, inputs=dict(pline=1.2, loading=1.0), mode=0, pcmd=0.0),
        phase(2, inputs=dict(pline=1.2, loading=1.0001), mode=2),)),
    Case("soc_discharge_boundary", (
        phase(1, inputs=dict(**FWD, soc=0.12), mode=0, pcmd=0.0),
        phase(2, inputs=dict(**FWD, soc=0.13), mode=2, pcmd=0.16855232923821894),)),
    Case("soc_charge_boundary", (
        phase(1, inputs=dict(**REV, soc=0.88), mode=0, pcmd=0.0),
        phase(2, inputs=dict(**REV, soc=0.87), mode=3, pcmd=-0.16855232923821894),)),
    Case("positive_power_limit", (phase(1, inputs=dict(pline=2.5, qline=0.0, loading=2.0),
                                            mode=2, pcmd=0.8, qcmd=0.0),)),
    Case("negative_power_limit", (phase(1, inputs=dict(pline=-2.5, qline=0.0, loading=2.0),
                                            mode=3, pcmd=-0.8, qcmd=0.0),)),
    Case("reactive_power_limit", (phase(1, inputs=dict(vpu=0.8), mode=1, pcmd=0.0, qcmd=0.4),)),
    Case("reactive_saturation_latch_exactly_10_calls", (
        phase(1, 9, SAT, mode=1, pcmd=0.0, qcmd=0.4),
        phase(10, inputs=SAT, mode=1, pcmd=0.2166666667, qcmd=0.4),)),
    Case("recovery_exactly_10_healthy_calls", (
        phase(1, inputs=UV, mode=1),
        phase(2, 10, dict(vpu=0.98), mode=1, pcmd=0.0),
        phase(11, inputs=dict(vpu=0.98), mode=0, pcmd=0.0, qcmd=0.0),)),
    Case("invalid_snapshot_zero_and_freeze", (
        phase(1, 5, SAT, mode=1, pcmd=0.0),
        phase(6, 25, dict(input_valid=False, vpu=0.0, pline=0.0, qline=0.0,
                         loading=0.0, soc=0.0, pess=0.0, qess=0.0),
              mode=0, pcmd=0.0, qcmd=0.0, ready=True, svp=0.12),
        phase(26, 29, SAT, mode=1, pcmd=0.0),
        phase(30, inputs=SAT, mode=1, pcmd=0.2166666667),)),
    Case("disabled_zero_and_freeze", (
        phase(1, 5, SAT, mode=1, pcmd=0.0),
        phase(6, 25, dict(enable=False), mode=0, pcmd=0.0, qcmd=0.0, ready=True),
        phase(26, 29, SAT, mode=1, pcmd=0.0),
        phase(30, inputs=SAT, mode=1, pcmd=0.2166666667),)),
    Case("invalid_before_initialization", (
        phase(1, 3, dict(input_valid=False), mode=0, pcmd=0.0, qcmd=0.0, ready=False, svp=0.0),
        phase(4, mode=0, pcmd=0.0, qcmd=0.0, ready=True, svp=0.12),)),
    Case("interrupted_recovery_restarts_count", (
        phase(1, inputs=UV, mode=1),
        phase(2, 10, dict(vpu=0.98), mode=1),
        phase(11, inputs=dict(vpu=0.97), mode=1),
        phase(12, 20, dict(vpu=0.98), mode=1),
        phase(21, inputs=dict(vpu=0.98), mode=0),)),
    Case("forward_overload_exit_hysteresis", (
        phase(1, inputs=FWD, mode=2),
        phase(2, inputs=dict(pline=1.2, loading=1.0), mode=2),
        phase(3, inputs=dict(pline=1.2, loading=0.95), mode=2),
        phase(4, inputs=dict(pline=1.2, loading=0.94), mode=0, pcmd=0.0),)),
    Case("reverse_overload_exit_hysteresis", (
        phase(1, inputs=REV, mode=3),
        phase(2, inputs=dict(pline=-1.2, loading=1.0), mode=3),
        phase(3, inputs=dict(pline=-1.2, loading=0.95), mode=3),
        phase(4, inputs=dict(pline=-1.2, loading=0.94), mode=0, pcmd=0.0),)),
    Case("active_estimator_window_and_update", (
        phase(1, svp=0.12, kp=1.0, confidence=0.0),
        phase(2, 10, dict(vpu=1.002, pline=0.49, pess=0.01), svp=0.12, confidence=0.0),
        phase(11, inputs=dict(vpu=1.002, pline=0.49, pess=0.01),
              svp=0.1264, kp=1.0, svq=0.1, confidence=0.025),)),
    Case("reactive_estimator_window_and_update", (
        phase(1, svq=0.1, kq=1.0, confidence=0.0),
        phase(2, 10, dict(vpu=1.0015, qline=0.19, qess=0.01), svq=0.1, confidence=0.0),
        phase(11, inputs=dict(vpu=1.0015, qline=0.19, qess=0.01),
              svq=0.104, kq=1.0, svp=0.12, confidence=0.025),)),
    Case("estimator_rejects_out_of_range_slope", (
        phase(1, svp=0.12, confidence=0.0),
        phase(2, 10, dict(vpu=1.005, pline=0.49, pess=0.01), svp=0.12, confidence=0.0),
        phase(11, inputs=dict(vpu=1.005, pline=0.49, pess=0.01),
              svp=0.12, kp=1.0, confidence=0.0125),)),
    Case("zero_loading_and_zero_line_magnitude", (
        phase(1, inputs=dict(pline=0.0, qline=0.0, loading=0.0), mode=0, pcmd=0.0, qcmd=0.0),
        phase(2, inputs=dict(pline=0.0, qline=0.0, loading=1.1), mode=0, pcmd=0.0, qcmd=0.0),)),
    Case("low_voltage_overrides_overload", (
        phase(1, inputs=dict(**FWD, vpu=0.94), mode=1, pcmd=0.0, qcmd=0.3),)),
    Case("low_voltage_active_support_soc_gate", (
        phase(1, 10, dict(**SAT, soc=0.12), mode=1, pcmd=0.0, qcmd=0.4),
        phase(11, inputs=dict(**SAT, soc=0.13), mode=1, pcmd=0.2166666667),)),
    Case("nonconsecutive_saturation_counter_decays", (
        phase(1, 5, SAT, mode=1, pcmd=0.0),
        phase(6, 8, UV, mode=1, pcmd=0.0, qcmd=0.3),
        phase(9, 15, SAT, mode=1, pcmd=0.0),
        phase(16, inputs=SAT, mode=1, pcmd=0.2166666667),)),
    Case("reset_clears_latch_and_estimator", (
        phase(1, 10, SAT, mode=1, qcmd=0.4),
        phase(11, inputs=dict(reset=True), mode=0, pcmd=0.0, qcmd=0.0,
              ready=False, svp=0.0, svq=0.0, kp=0.0, kq=0.0, confidence=0.0),
        phase(12, mode=0, pcmd=0.0, qcmd=0.0, ready=True, svp=0.12, confidence=0.0),)),
)


def st_value(value):
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    literal = format(float(value), ".12f").rstrip("0").rstrip(".")
    return literal + ".0" if "." not in literal else literal


def phase_condition(p):
    return f"D509 = {p.start}" if p.start == p.end else f"(D509 >= {p.start}) AND (D509 <= {p.end})"


def expectations(expected):
    parts = []
    for key, value in expected.items():
        name = OUTPUTS[key]
        if key == "ready":
            parts.append(name if value else f"NOT {name}")
        elif key == "mode":
            parts.append(f"{name} = {value}")
        else:
            parts.append(f"ABS({name} - {st_value(value)}) <= {st_value(TOL)}")
    return [f"({part})" for part in parts]


def check_vectors():
    count = 0
    for cid, case in enumerate(CASES, 1):
        mirror = Real32Mirror()
        mirror.step(1, .5, .2, .5, .6, 0, 0, reset=True)
        for step in range(1, case.scans + 1):
            matches = [p for p in case.phases if p.start <= step <= p.end]
            assert len(matches) == 1, (cid, step, "non-contiguous phases")
            p = matches[0]
            data = DEFAULTS | p.inputs
            out = mirror.step(*(data[k] for k in ("vpu", "pline", "qline", "loading", "soc", "pess", "qess")),
                              **{k: data[k] for k in ("enable", "input_valid", "reset")})
            for key, expected in p.expected.items():
                actual = getattr(out, key)
                assert actual == expected if key in ("mode", "ready") else abs(actual - expected) <= TOL, \
                    (cid, case.name, step, key, actual, expected)
                count += 1
        print(f"{cid:02d} {case.name}: vectors checked, {case.scans} control calls")
    print(f"Vector precheck: {len(CASES)}/{len(CASES)} cases, {count} fixed assertions; NOT a vendor runtime result.")


def generate(negative_control=False):
    result = ["(* OFFLINE SIMULATOR ONLY. Never download this MAIN to a physical PLC.",
              "   Generated by tests/build_vendor_acceptance.py. Same production FB.",
              "   One FB call per MAIN scan; every case starts with a reset scan.",
              "   D500=passed D501=failed D502=total D503=first failed case",
              "   D504=521 when finished D505=case D506=executed MAIN scans",
              "   D509=case step D510=initialized D514=first failed step",
              "   D523=oracle revision (1 positive; 2 deliberately wrong Q expectation)",
              f"   D601..D{600+len(CASES)}=per-case result (1 pass, 0 fail). *)",
              f"IF D523 <> {2 if negative_control else 1} THEN D510 := 0; D504 := 0; D523 := {2 if negative_control else 1}; END_IF;",
              "IF D504 = 521 THEN RETURN; END_IF;",
              "IF D510 = 0 THEN",
              "    D500 := 0; D501 := 0; D502 := 0; D503 := 0; D504 := 0;",
              "    D505 := 1; D506 := 0; D509 := 0; D514 := -1; D520 := 1; D510 := 1;",
              "END_IF;"]
    result += [f"{INPUTS[k]} := {st_value(v)};" for k, v in DEFAULTS.items()]
    result += ["IF D509 = 0 THEN", "    g_xBESS_Reset := TRUE;", "ELSE", "    CASE D505 OF"]
    for cid, case in enumerate(CASES, 1):
        result.append(f"    {cid}: (* {case.name} *)")
        result.append(f"        D511 := {case.scans};")
        for p in case.phases:
            if p.inputs:
                result.append(f"        IF {phase_condition(p)} THEN")
                result.extend(f"            {INPUTS[k]} := {st_value(v)};" for k, v in p.inputs.items())
                result.append("        END_IF;")
    result += ["    END_CASE;", "END_IF;"]
    main = (Path(__file__).resolve().parent.parent / "MAIN_5ms_body.st").read_text(encoding="utf-8")
    result.extend(main.splitlines()[2:])
    result += ["D506 := D506 + 1;", "D521 := 1;",
               "IF NOT ((g_rBESS_Pcmd >= -0.800002) AND (g_rBESS_Pcmd <= 0.800002)",
               "    AND (g_rBESS_Qcmd >= 0.0) AND (g_rBESS_Qcmd <= 0.400002)",
               "    AND (g_iBESS_Mode >= 0) AND (g_iBESS_Mode <= 3)",
               "    AND (g_rBESS_Confidence >= 0.0) AND (g_rBESS_Confidence <= 1.000002)) THEN",
               "    D521 := 0;", "END_IF;", "IF D509 = 0 THEN",
               "    IF NOT ((g_rBESS_Pcmd = 0.0) AND (g_rBESS_Qcmd = 0.0)",
               "        AND (g_iBESS_Mode = 0) AND (NOT g_xBESS_ControllerReady)",
               "        AND (g_rBESS_SensVP = 0.0) AND (g_rBESS_SensVQ = 0.0)",
               "        AND (g_rBESS_InfluenceP = 0.0) AND (g_rBESS_InfluenceQ = 0.0)",
               "        AND (g_rBESS_Confidence = 0.0)) THEN D521 := 0; END_IF;",
               "ELSE", "    CASE D505 OF"]
    for cid, case in enumerate(CASES, 1):
        result.append(f"    {cid}:")
        for p in case.phases:
            result += [f"        IF {phase_condition(p)} THEN", "            IF NOT ("]
            expected = p.expected | {"qcmd": 0.31} if negative_control and cid == 3 else p.expected
            parts = expectations(expected)
            result.extend("                " + ("AND " if i else "") + part for i, part in enumerate(parts))
            result += ["            ) THEN D521 := 0; END_IF;", "        END_IF;"]
    result += ["    END_CASE;", "END_IF;", "IF D521 = 0 THEN",
               "    D520 := 0;",
               "    IF D503 = 0 THEN D503 := D505; D514 := D509; END_IF;",
               "END_IF;", "IF (D509 > 0) AND (D509 >= D511) THEN",
               "    D502 := D502 + 1;", "    IF D520 = 1 THEN D500 := D500 + 1;",
               "    ELSE D501 := D501 + 1; END_IF;", "    CASE D505 OF"]
    result += [f"    {cid}: D{600+cid} := D520;" for cid in range(1, len(CASES)+1)]
    result += ["    END_CASE;", "    D505 := D505 + 1; D509 := 0; D520 := 1;",
               f"    IF D505 > {len(CASES)} THEN D504 := 521; END_IF;",
               "ELSE", "    D509 := D509 + 1;", "END_IF;"]
    assert len(result) <= 1000, f"LiteST limit exceeded: {len(result)}"
    return "\n".join(result) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--negative-control", action="store_true", help="Deliberately expect 0.31 instead of 0.30 Qcmd in case 3")
    args = parser.parse_args()
    if args.check:
        check_vectors()
    else:
        print(generate(args.negative_control), end="")
