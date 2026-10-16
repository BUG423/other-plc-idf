"""Independent executable mirror of the Structured Text function block.

This implementation deliberately uses named persistent state, matching the PLC
FB layout rather than the Python reference's 20-element list.  It is used only
for offline differential and property tests; it is not runtime controller code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ControllerOutput:
    pcmd: float
    qcmd: float
    mode: int
    svp: float
    svq: float
    kp: float
    kq: float
    confidence: float
    ready: bool


class PlcMirror:
    VON = 0.95
    VOFF = 0.975
    VREF = 0.98
    LON = 1.0
    LOFF = 0.95
    LTARGET = 0.95
    PMAX = 0.8
    QMAX = 0.4
    SOCMIN = 0.1
    SOCMAX = 0.9
    SMARGIN = 0.02
    QREACH = 0.95
    DWELL = 10.0
    RECOVER = 10.0
    GQ = 0.75
    GP = 0.65
    SVP_INITIAL = 0.12
    SVQ_INITIAL = 0.10
    ALPHA = 0.08
    DMIN = 0.003
    WINDOW = 10.0
    DOM = 1.5
    SVPMIN = 0.01
    SVPMAX = 0.4
    SVQMIN = 0.01
    SVQMAX = 0.4
    DVMAX = 0.03
    KP_INITIAL = 1.0
    KQ_INITIAL = 1.0
    KALPHA = 0.08
    KMIN = 0.2
    KMAX = 1.5
    CINC = 0.05
    EPS = 0.000001

    def __init__(self):
        self._clear_state()
        self.output = ControllerOutput(0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, False)

    def _clear_state(self):
        self.low = 0.0
        self.recover_count = 0.0
        self.direction = 0.0
        self.uv_count = 0.0
        self.q_latched = 0.0
        self.anchor_v = 0.0
        self.anchor_pline = 0.0
        self.anchor_qline = 0.0
        self.anchor_pess = 0.0
        self.anchor_qess = 0.0
        self.estimator_count = 0.0
        self.svp = 0.0
        self.svq = 0.0
        self.kp = 0.0
        self.kq = 0.0
        self.cvp = 0.0
        self.cvq = 0.0
        self.ckp = 0.0
        self.ckq = 0.0
        self.initialized = 0.0

    def state_vector(self):
        return [
            self.low,
            self.recover_count,
            self.direction,
            self.uv_count,
            self.q_latched,
            self.anchor_v,
            self.anchor_pline,
            self.anchor_qline,
            self.anchor_pess,
            self.anchor_qess,
            self.estimator_count,
            self.svp,
            self.svq,
            self.kp,
            self.kq,
            self.cvp,
            self.cvq,
            self.ckp,
            self.ckq,
            self.initialized,
        ]

    def step(
        self,
        vpu,
        pline,
        qline,
        loading,
        soc,
        pess,
        qess,
        *,
        enable=True,
        input_valid=True,
        reset=False,
    ):
        if reset:
            self._clear_state()
            self.output = ControllerOutput(0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, False)
            return self.output

        if not enable or not input_valid:
            previous = self.output
            self.output = ControllerOutput(
                0.0,
                0.0,
                0,
                previous.svp,
                previous.svq,
                previous.kp,
                previous.kq,
                previous.confidence,
                self.initialized >= 0.5,
            )
            return self.output

        if self.initialized < 0.5:
            self.anchor_v = vpu
            self.anchor_pline = pline
            self.anchor_qline = qline
            self.anchor_pess = pess
            self.anchor_qess = qess
            self.estimator_count = 0.0
            self.svp = self.SVP_INITIAL
            self.svq = self.SVQ_INITIAL
            self.kp = self.KP_INITIAL
            self.kq = self.KQ_INITIAL
            self.initialized = 1.0
        else:
            self.estimator_count += 1.0
            if self.estimator_count >= self.WINDOW:
                dv = vpu - self.anchor_v
                dpl = pline - self.anchor_pline
                dql = qline - self.anchor_qline
                dp = pess - self.anchor_pess
                dq = qess - self.anchor_qess

                if (
                    abs(dv) <= self.DVMAX
                    and abs(dq) >= self.DMIN
                    and abs(dq) >= self.DOM * abs(dp)
                ):
                    sample = (dv - self.svp * dp) / dq
                    if self.SVQMIN <= sample <= self.SVQMAX:
                        self.svq = (1.0 - self.ALPHA) * self.svq + self.ALPHA * sample
                        self.cvq = min(1.0, self.cvq + self.CINC)

                if (
                    abs(dv) <= self.DVMAX
                    and abs(dp) >= self.DMIN
                    and abs(dp) >= self.DOM * abs(dq)
                ):
                    sample = (dv - self.svq * dq) / dp
                    if self.SVPMIN <= sample <= self.SVPMAX:
                        self.svp = (1.0 - self.ALPHA) * self.svp + self.ALPHA * sample
                        self.cvp = min(1.0, self.cvp + self.CINC)

                if abs(dp) >= self.DMIN and abs(dp) >= self.DOM * abs(dq):
                    sample = -dpl / dp
                    if self.KMIN <= sample <= self.KMAX:
                        self.kp = (1.0 - self.KALPHA) * self.kp + self.KALPHA * sample
                        self.ckp = min(1.0, self.ckp + self.CINC)

                if abs(dq) >= self.DMIN and abs(dq) >= self.DOM * abs(dp):
                    sample = -dql / dq
                    if self.KMIN <= sample <= self.KMAX:
                        self.kq = (1.0 - self.KALPHA) * self.kq + self.KALPHA * sample
                        self.ckq = min(1.0, self.ckq + self.CINC)

                self.anchor_v = vpu
                self.anchor_pline = pline
                self.anchor_qline = qline
                self.anchor_pess = pess
                self.anchor_qess = qess
                self.estimator_count = 0.0

        self.svp = min(self.SVPMAX, max(self.SVPMIN, self.svp))
        self.svq = min(self.SVQMAX, max(self.SVQMIN, self.svq))
        self.kp = min(self.KMAX, max(self.KMIN, self.kp))
        self.kq = min(self.KMAX, max(self.KMIN, self.kq))

        vnative_estimate = vpu - self.svp * pess - self.svq * qess
        if self.low < 0.5:
            if vpu < self.VON:
                self.low = 1.0
                self.recover_count = 0.0
        else:
            if vpu > self.VOFF and vnative_estimate > self.VON:
                self.recover_count = min(self.RECOVER, self.recover_count + 1.0)
            else:
                self.recover_count = 0.0
            if self.recover_count >= self.RECOVER:
                self.low = 0.0
                self.recover_count = 0.0
                self.uv_count = 0.0
                self.q_latched = 0.0

        pnative = pline + self.kp * pess
        qnative = qline + self.kq * qess
        snative = math.sqrt(pnative * pnative + qnative * qnative)
        sline = math.sqrt(pline * pline + qline * qline)
        native_loading = 0.0
        slimit = 0.0
        if loading > self.EPS and sline > self.EPS:
            slimit = self.LTARGET * sline / loading
            native_loading = loading * snative / sline

        if self.direction == 0.0 and native_loading > self.LON:
            if pnative > 0.0:
                self.direction = 1.0
            elif pnative < 0.0:
                self.direction = -1.0
        elif self.direction != 0.0 and native_loading < self.LOFF:
            self.direction = 0.0

        pcmd = 0.0
        qcmd = 0.0
        mode = 0

        if self.low > 0.5:
            ev = self.VREF - vpu
            raw_q = qess + self.GQ * ev / max(self.svq, self.SVQMIN)
            qcmd = min(self.QMAX, max(0.0, raw_q))
            if self.q_latched < 0.5:
                if qcmd >= self.QREACH * self.QMAX and qess >= self.QREACH * self.QMAX:
                    self.uv_count = min(self.DWELL, self.uv_count + 1.0)
                else:
                    self.uv_count = max(0.0, self.uv_count - 1.0)
                if self.uv_count >= self.DWELL:
                    self.q_latched = 1.0

            if self.q_latched > 0.5 and soc > self.SOCMIN + self.SMARGIN:
                pvoltage = min(
                    self.PMAX,
                    max(0.0, pess + self.GP * ev / max(self.svp, self.SVPMIN)),
                )
                pcmd = pvoltage
                if self.direction > 0.0 and slimit > self.EPS:
                    qafter = qnative - self.kq * qcmd
                    ptarget = math.sqrt(max(slimit * slimit - qafter * qafter, 0.0))
                    pover = max(0.0, (pnative - ptarget) / max(self.kp, self.KMIN))
                    pcmd = max(pcmd, min(self.PMAX, pover))
            mode = 1
        elif self.direction != 0.0 and slimit > self.EPS:
            ptarget = math.sqrt(max(slimit * slimit - qnative * qnative, 0.0))
            if self.direction > 0.0 and soc > self.SOCMIN + self.SMARGIN:
                pcmd = min(
                    self.PMAX,
                    max(0.0, (pnative - ptarget) / max(self.kp, self.KMIN)),
                )
                mode = 2
            elif self.direction < 0.0 and soc < self.SOCMAX - self.SMARGIN:
                pcmd = max(
                    -self.PMAX,
                    min(0.0, (pnative + ptarget) / max(self.kp, self.KMIN)),
                )
                mode = 3
        else:
            self.uv_count = 0.0
            self.q_latched = 0.0

        confidence = 0.25 * (self.cvp + self.cvq + self.ckp + self.ckq)
        self.output = ControllerOutput(
            pcmd,
            qcmd,
            mode,
            self.svp,
            self.svq,
            self.kp,
            self.kq,
            confidence,
            True,
        )
        return self.output
