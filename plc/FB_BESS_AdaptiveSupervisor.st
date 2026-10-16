(******************************************************************************
 * FB_BESS_AdaptiveSupervisor
 *
 * IEC 61131-3 Structured Text port of:
 *   - modbus_realtime.py::control
 *   - ieee33_bess_adaptive_R2022b.slx / Supervisor Step
 *
 * Required task period: 5 ms.
 * All electrical inputs and P/Q commands are per-unit (pu).
 * Internal variables correspond to the original 20-element delayed state.
 ******************************************************************************)
FUNCTION_BLOCK FB_BESS_AdaptiveSupervisor
VAR_INPUT
    xEnable          : BOOL;   (* Run one controller step when TRUE. *)
    xReset           : BOOL;   (* Synchronous reset; keep TRUE for >= one scan. *)
    xInputValid      : BOOL;   (* FALSE forces zero commands and freezes state. *)
    lrVpu            : LREAL;
    lrPline          : LREAL;
    lrQline          : LREAL;
    lrLoading        : LREAL;
    lrSOC            : LREAL;
    lrPess           : LREAL;
    lrQess           : LREAL;
END_VAR

VAR_OUTPUT
    lrPcmd           : LREAL;
    lrQcmd           : LREAL;
    uiMode           : USINT;  (* 0 normal, 1 low V, 2 fwd overload, 3 rev overload *)
    lrSensVP         : LREAL;
    lrSensVQ         : LREAL;
    lrInfluenceP     : LREAL;
    lrInfluenceQ     : LREAL;
    lrConfidence     : LREAL;
    xReady           : BOOL;
END_VAR

VAR CONSTANT
    (* Exact parameter values from the SLX/Python 35-element parameter vector. *)
    C_VON            : LREAL := 0.95;
    C_VOFF           : LREAL := 0.975;
    C_VREF           : LREAL := 0.98;
    C_LON            : LREAL := 1.0;
    C_LOFF           : LREAL := 0.95;
    C_LTARGET        : LREAL := 0.95;
    C_PMAX           : LREAL := 0.8;
    C_QMAX           : LREAL := 0.4;
    C_SOCMIN         : LREAL := 0.1;
    C_SOCMAX         : LREAL := 0.9;
    C_SMARGIN        : LREAL := 0.02;
    C_QREACH         : LREAL := 0.95;
    C_DWELL          : LREAL := 10.0;
    C_RECOVER        : LREAL := 10.0;
    C_GQ             : LREAL := 0.75;
    C_GP             : LREAL := 0.65;
    C_SVP_INITIAL    : LREAL := 0.12;
    C_SVQ_INITIAL    : LREAL := 0.10;
    C_ALPHA          : LREAL := 0.08;
    C_DMIN           : LREAL := 0.003;
    C_WINDOW         : LREAL := 10.0;
    C_DOM            : LREAL := 1.5;
    C_SVPMIN         : LREAL := 0.01;
    C_SVPMAX         : LREAL := 0.4;
    C_SVQMIN         : LREAL := 0.01;
    C_SVQMAX         : LREAL := 0.4;
    C_DVMAX          : LREAL := 0.03;
    C_KP_INITIAL     : LREAL := 1.0;
    C_KQ_INITIAL     : LREAL := 1.0;
    C_KALPHA         : LREAL := 0.08;
    C_KMIN           : LREAL := 0.2;
    C_KMAX           : LREAL := 1.5;
    C_CINC           : LREAL := 0.05;
    C_EPS            : LREAL := 0.000001;
END_VAR

VAR
    (* Persistent controller state: same order/meaning as Python state[0..19]. *)
    lrLow            : LREAL;
    lrRecoverCount   : LREAL;
    lrDirection      : LREAL;
    lrUvCount        : LREAL;
    lrQLatched       : LREAL;
    lrAnchorV        : LREAL;
    lrAnchorPline    : LREAL;
    lrAnchorQline    : LREAL;
    lrAnchorPess     : LREAL;
    lrAnchorQess     : LREAL;
    lrEstimatorCount : LREAL;
    lrSVP            : LREAL;
    lrSVQ            : LREAL;
    lrKP             : LREAL;
    lrKQ             : LREAL;
    lrCVP            : LREAL;
    lrCVQ            : LREAL;
    lrCKP            : LREAL;
    lrCKQ            : LREAL;
    lrInitialized    : LREAL;

    (* Scan-local working values. *)
    lrDV             : LREAL;
    lrDPL            : LREAL;
    lrDQL            : LREAL;
    lrDP             : LREAL;
    lrDQ             : LREAL;
    lrSample         : LREAL;
    lrVnativeEstimate: LREAL;
    lrPnative        : LREAL;
    lrQnative        : LREAL;
    lrSnative        : LREAL;
    lrSline          : LREAL;
    lrNativeLoading  : LREAL;
    lrSlimit         : LREAL;
    lrEV             : LREAL;
    lrRawQ           : LREAL;
    lrPvoltage       : LREAL;
    lrQafter         : LREAL;
    lrPtarget        : LREAL;
    lrPover          : LREAL;
END_VAR

(* A reset reproduces the all-zero Unit Delay initial condition in Simulink. *)
IF xReset THEN
    lrLow := 0.0;
    lrRecoverCount := 0.0;
    lrDirection := 0.0;
    lrUvCount := 0.0;
    lrQLatched := 0.0;
    lrAnchorV := 0.0;
    lrAnchorPline := 0.0;
    lrAnchorQline := 0.0;
    lrAnchorPess := 0.0;
    lrAnchorQess := 0.0;
    lrEstimatorCount := 0.0;
    lrSVP := 0.0;
    lrSVQ := 0.0;
    lrKP := 0.0;
    lrKQ := 0.0;
    lrCVP := 0.0;
    lrCVQ := 0.0;
    lrCKP := 0.0;
    lrCKQ := 0.0;
    lrInitialized := 0.0;

    lrPcmd := 0.0;
    lrQcmd := 0.0;
    uiMode := 0;
    lrSensVP := 0.0;
    lrSensVQ := 0.0;
    lrInfluenceP := 0.0;
    lrInfluenceQ := 0.0;
    lrConfidence := 0.0;
    xReady := FALSE;
    RETURN;
END_IF;

(* Safe integration behavior: never send a stale command after invalid I/O. *)
IF (NOT xEnable) OR (NOT xInputValid) THEN
    lrPcmd := 0.0;
    lrQcmd := 0.0;
    uiMode := 0;
    xReady := (lrInitialized >= 0.5);
    RETURN;
END_IF;

(* Online estimator initialization/update. *)
IF lrInitialized < 0.5 THEN
    lrAnchorV := lrVpu;
    lrAnchorPline := lrPline;
    lrAnchorQline := lrQline;
    lrAnchorPess := lrPess;
    lrAnchorQess := lrQess;
    lrEstimatorCount := 0.0;
    lrSVP := C_SVP_INITIAL;
    lrSVQ := C_SVQ_INITIAL;
    lrKP := C_KP_INITIAL;
    lrKQ := C_KQ_INITIAL;
    lrInitialized := 1.0;
ELSE
    lrEstimatorCount := lrEstimatorCount + 1.0;
    IF lrEstimatorCount >= C_WINDOW THEN
        lrDV := lrVpu - lrAnchorV;
        lrDPL := lrPline - lrAnchorPline;
        lrDQL := lrQline - lrAnchorQline;
        lrDP := lrPess - lrAnchorPess;
        lrDQ := lrQess - lrAnchorQess;

        IF (ABS(lrDV) <= C_DVMAX)
           AND (ABS(lrDQ) >= C_DMIN)
           AND (ABS(lrDQ) >= C_DOM * ABS(lrDP)) THEN
            lrSample := (lrDV - lrSVP * lrDP) / lrDQ;
            IF (lrSample >= C_SVQMIN) AND (lrSample <= C_SVQMAX) THEN
                lrSVQ := (1.0 - C_ALPHA) * lrSVQ + C_ALPHA * lrSample;
                lrCVQ := MIN(1.0, lrCVQ + C_CINC);
            END_IF;
        END_IF;

        IF (ABS(lrDV) <= C_DVMAX)
           AND (ABS(lrDP) >= C_DMIN)
           AND (ABS(lrDP) >= C_DOM * ABS(lrDQ)) THEN
            lrSample := (lrDV - lrSVQ * lrDQ) / lrDP;
            IF (lrSample >= C_SVPMIN) AND (lrSample <= C_SVPMAX) THEN
                lrSVP := (1.0 - C_ALPHA) * lrSVP + C_ALPHA * lrSample;
                lrCVP := MIN(1.0, lrCVP + C_CINC);
            END_IF;
        END_IF;

        IF (ABS(lrDP) >= C_DMIN)
           AND (ABS(lrDP) >= C_DOM * ABS(lrDQ)) THEN
            lrSample := -lrDPL / lrDP;
            IF (lrSample >= C_KMIN) AND (lrSample <= C_KMAX) THEN
                lrKP := (1.0 - C_KALPHA) * lrKP + C_KALPHA * lrSample;
                lrCKP := MIN(1.0, lrCKP + C_CINC);
            END_IF;
        END_IF;

        IF (ABS(lrDQ) >= C_DMIN)
           AND (ABS(lrDQ) >= C_DOM * ABS(lrDP)) THEN
            lrSample := -lrDQL / lrDQ;
            IF (lrSample >= C_KMIN) AND (lrSample <= C_KMAX) THEN
                lrKQ := (1.0 - C_KALPHA) * lrKQ + C_KALPHA * lrSample;
                lrCKQ := MIN(1.0, lrCKQ + C_CINC);
            END_IF;
        END_IF;

        lrAnchorV := lrVpu;
        lrAnchorPline := lrPline;
        lrAnchorQline := lrQline;
        lrAnchorPess := lrPess;
        lrAnchorQess := lrQess;
        lrEstimatorCount := 0.0;
    END_IF;
END_IF;

lrSVP := MIN(C_SVPMAX, MAX(C_SVPMIN, lrSVP));
lrSVQ := MIN(C_SVQMAX, MAX(C_SVQMIN, lrSVQ));
lrKP := MIN(C_KMAX, MAX(C_KMIN, lrKP));
lrKQ := MIN(C_KMAX, MAX(C_KMIN, lrKQ));

(* Low-voltage state machine. *)
lrVnativeEstimate := lrVpu - lrSVP * lrPess - lrSVQ * lrQess;
IF lrLow < 0.5 THEN
    IF lrVpu < C_VON THEN
        lrLow := 1.0;
        lrRecoverCount := 0.0;
    END_IF;
ELSE
    IF (lrVpu > C_VOFF) AND (lrVnativeEstimate > C_VON) THEN
        lrRecoverCount := MIN(C_RECOVER, lrRecoverCount + 1.0);
    ELSE
        lrRecoverCount := 0.0;
    END_IF;

    IF lrRecoverCount >= C_RECOVER THEN
        lrLow := 0.0;
        lrRecoverCount := 0.0;
        lrUvCount := 0.0;
        lrQLatched := 0.0;
    END_IF;
END_IF;

(* Reconstruct feeder loading without the BESS contribution. *)
lrPnative := lrPline + lrKP * lrPess;
lrQnative := lrQline + lrKQ * lrQess;
lrSnative := SQRT(lrPnative * lrPnative + lrQnative * lrQnative);
lrSline := SQRT(lrPline * lrPline + lrQline * lrQline);
lrNativeLoading := 0.0;
lrSlimit := 0.0;

IF (lrLoading > C_EPS) AND (lrSline > C_EPS) THEN
    lrSlimit := C_LTARGET * lrSline / lrLoading;
    lrNativeLoading := lrLoading * lrSnative / lrSline;
END_IF;

(* Forward/reverse overload state machine with hysteresis. *)
IF (lrDirection = 0.0) AND (lrNativeLoading > C_LON) THEN
    IF lrPnative > 0.0 THEN
        lrDirection := 1.0;
    ELSIF lrPnative < 0.0 THEN
        lrDirection := -1.0;
    END_IF;
ELSIF (lrDirection <> 0.0) AND (lrNativeLoading < C_LOFF) THEN
    lrDirection := 0.0;
END_IF;

lrPcmd := 0.0;
lrQcmd := 0.0;
uiMode := 0;

(* Mode 1: low voltage. Reactive support first, then active support. *)
IF lrLow > 0.5 THEN
    lrEV := C_VREF - lrVpu;
    lrRawQ := lrQess + C_GQ * lrEV / MAX(lrSVQ, C_SVQMIN);
    lrQcmd := MIN(C_QMAX, MAX(0.0, lrRawQ));

    IF lrQLatched < 0.5 THEN
        IF (lrQcmd >= C_QREACH * C_QMAX)
           AND (lrQess >= C_QREACH * C_QMAX) THEN
            lrUvCount := MIN(C_DWELL, lrUvCount + 1.0);
        ELSE
            lrUvCount := MAX(0.0, lrUvCount - 1.0);
        END_IF;

        IF lrUvCount >= C_DWELL THEN
            lrQLatched := 1.0;
        END_IF;
    END_IF;

    IF (lrQLatched > 0.5) AND (lrSOC > C_SOCMIN + C_SMARGIN) THEN
        lrPvoltage := MIN(C_PMAX,
                          MAX(0.0, lrPess + C_GP * lrEV / MAX(lrSVP, C_SVPMIN)));
        lrPcmd := lrPvoltage;

        IF (lrDirection > 0.0) AND (lrSlimit > C_EPS) THEN
            lrQafter := lrQnative - lrKQ * lrQcmd;
            lrPtarget := SQRT(MAX(lrSlimit * lrSlimit - lrQafter * lrQafter, 0.0));
            lrPover := MAX(0.0, (lrPnative - lrPtarget) / MAX(lrKP, C_KMIN));
            lrPcmd := MAX(lrPcmd, MIN(C_PMAX, lrPover));
        END_IF;
    END_IF;
    uiMode := 1;

(* Modes 2/3: no low voltage, feeder overload only. *)
ELSIF (lrDirection <> 0.0) AND (lrSlimit > C_EPS) THEN
    lrPtarget := SQRT(MAX(lrSlimit * lrSlimit - lrQnative * lrQnative, 0.0));
    IF (lrDirection > 0.0) AND (lrSOC > C_SOCMIN + C_SMARGIN) THEN
        lrPcmd := MIN(C_PMAX,
                      MAX(0.0, (lrPnative - lrPtarget) / MAX(lrKP, C_KMIN)));
        uiMode := 2;
    ELSIF (lrDirection < 0.0) AND (lrSOC < C_SOCMAX - C_SMARGIN) THEN
        lrPcmd := MAX(-C_PMAX,
                      MIN(0.0, (lrPnative + lrPtarget) / MAX(lrKP, C_KMIN)));
        uiMode := 3;
    END_IF;
ELSE
    lrUvCount := 0.0;
    lrQLatched := 0.0;
END_IF;

lrSensVP := lrSVP;
lrSensVQ := lrSVQ;
lrInfluenceP := lrKP;
lrInfluenceQ := lrKQ;
lrConfidence := 0.25 * (lrCVP + lrCVQ + lrCKP + lrCKQ);
xReady := TRUE;

END_FUNCTION_BLOCK
