"""IEEE-754 binary32 simulation of the Easy521 AutoShop LiteST controller."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


F = np.float32


@dataclass(frozen=True)
class Output:
    pcmd: float
    qcmd: float
    mode: int
    svp: float
    svq: float
    kp: float
    kq: float
    confidence: float
    ready: bool


class Real32Mirror:
    def __init__(self):
        self.clear()

    def clear(self):
        self.low = False
        self.recover_count = 0
        self.direction = 0
        self.uv_count = 0
        self.q_latched = False
        self.anchor_v = F(0)
        self.anchor_pline = F(0)
        self.anchor_qline = F(0)
        self.anchor_pess = F(0)
        self.anchor_qess = F(0)
        self.estimator_count = 0
        self.svp = F(0)
        self.svq = F(0)
        self.kp = F(0)
        self.kq = F(0)
        self.cvp = F(0)
        self.cvq = F(0)
        self.ckp = F(0)
        self.ckq = F(0)
        self.initialized = False
        self.output = Output(0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, False)

    def state_vector(self):
        return [
            float(self.low), float(self.recover_count), float(self.direction),
            float(self.uv_count), float(self.q_latched), float(self.anchor_v),
            float(self.anchor_pline), float(self.anchor_qline), float(self.anchor_pess),
            float(self.anchor_qess), float(self.estimator_count), float(self.svp),
            float(self.svq), float(self.kp), float(self.kq), float(self.cvp),
            float(self.cvq), float(self.ckp), float(self.ckq), float(self.initialized),
        ]

    def step(self, vpu, pline, qline, loading, soc, pess, qess,
             *, enable=True, input_valid=True, reset=False):
        vpu, pline, qline, loading, soc, pess, qess = map(
            F, (vpu, pline, qline, loading, soc, pess, qess)
        )
        if reset:
            self.clear()
            return self.output
        if not enable or not input_valid:
            old = self.output
            self.output = Output(0.0, 0.0, 0, old.svp, old.svq, old.kp, old.kq,
                                 old.confidence, self.initialized)
            return self.output

        if not self.initialized:
            self.anchor_v = vpu
            self.anchor_pline = pline
            self.anchor_qline = qline
            self.anchor_pess = pess
            self.anchor_qess = qess
            self.estimator_count = 0
            self.svp = F(0.12)
            self.svq = F(0.10)
            self.kp = F(1.0)
            self.kq = F(1.0)
            self.initialized = True
        else:
            self.estimator_count += 1
            if self.estimator_count >= 10:
                dv = F(vpu - self.anchor_v)
                dpl = F(pline - self.anchor_pline)
                dql = F(qline - self.anchor_qline)
                dp = F(pess - self.anchor_pess)
                dq = F(qess - self.anchor_qess)
                if abs(dv) <= F(0.03) and abs(dq) >= F(0.003) and abs(dq) >= F(1.5) * abs(dp):
                    sample = F(F(dv - F(self.svp * dp)) / dq)
                    if F(0.01) <= sample <= F(0.4):
                        self.svq = F(F(F(1.0) - F(0.08)) * self.svq + F(0.08) * sample)
                        self.cvq = F(min(F(1.0), F(self.cvq + F(0.05))))
                if abs(dv) <= F(0.03) and abs(dp) >= F(0.003) and abs(dp) >= F(1.5) * abs(dq):
                    sample = F(F(dv - F(self.svq * dq)) / dp)
                    if F(0.01) <= sample <= F(0.4):
                        self.svp = F(F(F(1.0) - F(0.08)) * self.svp + F(0.08) * sample)
                        self.cvp = F(min(F(1.0), F(self.cvp + F(0.05))))
                if abs(dp) >= F(0.003) and abs(dp) >= F(1.5) * abs(dq):
                    sample = F(-dpl / dp)
                    if F(0.2) <= sample <= F(1.5):
                        self.kp = F(F(F(1.0) - F(0.08)) * self.kp + F(0.08) * sample)
                        self.ckp = F(min(F(1.0), F(self.ckp + F(0.05))))
                if abs(dq) >= F(0.003) and abs(dq) >= F(1.5) * abs(dp):
                    sample = F(-dql / dq)
                    if F(0.2) <= sample <= F(1.5):
                        self.kq = F(F(F(1.0) - F(0.08)) * self.kq + F(0.08) * sample)
                        self.ckq = F(min(F(1.0), F(self.ckq + F(0.05))))
                self.anchor_v = vpu
                self.anchor_pline = pline
                self.anchor_qline = qline
                self.anchor_pess = pess
                self.anchor_qess = qess
                self.estimator_count = 0

        self.svp = F(min(F(0.4), max(F(0.01), self.svp)))
        self.svq = F(min(F(0.4), max(F(0.01), self.svq)))
        self.kp = F(min(F(1.5), max(F(0.2), self.kp)))
        self.kq = F(min(F(1.5), max(F(0.2), self.kq)))

        vnative = F(vpu - F(self.svp * pess) - F(self.svq * qess))
        if not self.low:
            if vpu < F(0.95):
                self.low = True
                self.recover_count = 0
        else:
            if vpu > F(0.975) and vnative > F(0.95):
                self.recover_count = min(10, self.recover_count + 1)
            else:
                self.recover_count = 0
            if self.recover_count >= 10:
                self.low = False
                self.recover_count = 0
                self.uv_count = 0
                self.q_latched = False

        pnative = F(pline + F(self.kp * pess))
        qnative = F(qline + F(self.kq * qess))
        snative = F(np.sqrt(F(F(pnative * pnative) + F(qnative * qnative))))
        sline = F(np.sqrt(F(F(pline * pline) + F(qline * qline))))
        native_loading = F(0)
        slimit = F(0)
        if loading > F(0.000001) and sline > F(0.000001):
            slimit = F(F(F(0.95) * sline) / loading)
            native_loading = F(F(loading * snative) / sline)

        if self.direction == 0 and native_loading > F(1.0):
            if pnative > F(0):
                self.direction = 1
            elif pnative < F(0):
                self.direction = -1
        elif self.direction != 0 and native_loading < F(0.95):
            self.direction = 0

        pcmd = F(0)
        qcmd = F(0)
        mode = 0
        if self.low:
            ev = F(F(0.98) - vpu)
            raw_q = F(qess + F(F(0.75) * ev) / max(self.svq, F(0.01)))
            qcmd = F(min(F(0.4), max(F(0), raw_q)))
            if not self.q_latched:
                if qcmd >= F(F(0.95) * F(0.4)) and qess >= F(F(0.95) * F(0.4)):
                    self.uv_count = min(10, self.uv_count + 1)
                else:
                    self.uv_count = max(0, self.uv_count - 1)
                if self.uv_count >= 10:
                    self.q_latched = True
            if self.q_latched and soc > F(F(0.1) + F(0.02)):
                pvoltage = F(min(F(0.8), max(F(0), F(pess + F(F(0.65) * ev) / max(self.svp, F(0.01))))))
                pcmd = pvoltage
                if self.direction > 0 and slimit > F(0.000001):
                    qafter = F(qnative - F(self.kq * qcmd))
                    target_sq = F(F(slimit * slimit) - F(qafter * qafter))
                    ptarget = F(np.sqrt(max(target_sq, F(0))))
                    pover = F(max(F(0), F(pnative - ptarget) / max(self.kp, F(0.2))))
                    pcmd = F(max(pcmd, min(F(0.8), pover)))
            mode = 1
        elif self.direction != 0 and slimit > F(0.000001):
            target_sq = F(F(slimit * slimit) - F(qnative * qnative))
            ptarget = F(np.sqrt(max(target_sq, F(0))))
            if self.direction > 0 and soc > F(F(0.1) + F(0.02)):
                pcmd = F(min(F(0.8), max(F(0), F(pnative - ptarget) / max(self.kp, F(0.2)))))
                mode = 2
            elif self.direction < 0 and soc < F(F(0.9) - F(0.02)):
                pcmd = F(max(F(-0.8), min(F(0), F(pnative + ptarget) / max(self.kp, F(0.2)))))
                mode = 3
        else:
            self.uv_count = 0
            self.q_latched = False

        confidence = F(F(0.25) * F(F(self.cvp + self.cvq) + F(self.ckp + self.ckq)))
        self.output = Output(
            float(pcmd), float(qcmd), mode, float(self.svp), float(self.svq),
            float(self.kp), float(self.kq), float(confidence), True,
        )
        return self.output
