(*
  AutoShop LiteST body for FB_BESS_AdaptiveSupervisor_Easy521.
  Target: Inovance Easy521-0808TN.
  Create the FB and variables in AutoShop, then paste this BODY only.
  Execute the calling program in a 5 ms constant-cycle task.
*)

IF xReset THEN
    xLow := FALSE;
    iRecoverCount := 0;
    iDirection := 0;
    iUvCount := 0;
    xQLatched := FALSE;
    rAnchorV := 0.0;
    rAnchorPline := 0.0;
    rAnchorQline := 0.0;
    rAnchorPess := 0.0;
    rAnchorQess := 0.0;
    iEstimatorCount := 0;
    rSVP := 0.0;
    rSVQ := 0.0;
    rKP := 0.0;
    rKQ := 0.0;
    rCVP := 0.0;
    rCVQ := 0.0;
    rCKP := 0.0;
    rCKQ := 0.0;
    xInitialized := FALSE;

    rPcmd := 0.0;
    rQcmd := 0.0;
    iMode := 0;
    rSensVP := 0.0;
    rSensVQ := 0.0;
    rInfluenceP := 0.0;
    rInfluenceQ := 0.0;
    rConfidence := 0.0;
    xReady := FALSE;
    RETURN;
END_IF;

(* Fail-safe output and state freeze when the measurement snapshot is invalid. *)
IF (NOT xEnable) OR (NOT xInputValid) THEN
    rPcmd := 0.0;
    rQcmd := 0.0;
    iMode := 0;
    xReady := xInitialized;
    RETURN;
END_IF;

(* Online estimator initialization and 10-scan update window. *)
IF NOT xInitialized THEN
    rAnchorV := rVpu;
    rAnchorPline := rPline;
    rAnchorQline := rQline;
    rAnchorPess := rPess;
    rAnchorQess := rQess;
    iEstimatorCount := 0;
    rSVP := 0.12;
    rSVQ := 0.10;
    rKP := 1.0;
    rKQ := 1.0;
    xInitialized := TRUE;
ELSE
    iEstimatorCount := iEstimatorCount + 1;
    IF iEstimatorCount >= 10 THEN
        rDV := rVpu - rAnchorV;
        rDPL := rPline - rAnchorPline;
        rDQL := rQline - rAnchorQline;
        rDP := rPess - rAnchorPess;
        rDQ := rQess - rAnchorQess;

        IF (ABS(rDV) <= 0.03)
           AND (ABS(rDQ) >= 0.003)
           AND (ABS(rDQ) >= 1.5 * ABS(rDP)) THEN
            rSample := (rDV - rSVP * rDP) / rDQ;
            IF (rSample >= 0.01) AND (rSample <= 0.4) THEN
                rSVQ := (1.0 - 0.08) * rSVQ + 0.08 * rSample;
                rCVQ := MIN(1.0, rCVQ + 0.05);
            END_IF;
        END_IF;

        IF (ABS(rDV) <= 0.03)
           AND (ABS(rDP) >= 0.003)
           AND (ABS(rDP) >= 1.5 * ABS(rDQ)) THEN
            rSample := (rDV - rSVQ * rDQ) / rDP;
            IF (rSample >= 0.01) AND (rSample <= 0.4) THEN
                rSVP := (1.0 - 0.08) * rSVP + 0.08 * rSample;
                rCVP := MIN(1.0, rCVP + 0.05);
            END_IF;
        END_IF;

        IF (ABS(rDP) >= 0.003)
           AND (ABS(rDP) >= 1.5 * ABS(rDQ)) THEN
            rSample := -rDPL / rDP;
            IF (rSample >= 0.2) AND (rSample <= 1.5) THEN
                rKP := (1.0 - 0.08) * rKP + 0.08 * rSample;
                rCKP := MIN(1.0, rCKP + 0.05);
            END_IF;
        END_IF;

        IF (ABS(rDQ) >= 0.003)
           AND (ABS(rDQ) >= 1.5 * ABS(rDP)) THEN
            rSample := -rDQL / rDQ;
            IF (rSample >= 0.2) AND (rSample <= 1.5) THEN
                rKQ := (1.0 - 0.08) * rKQ + 0.08 * rSample;
                rCKQ := MIN(1.0, rCKQ + 0.05);
            END_IF;
        END_IF;

        rAnchorV := rVpu;
        rAnchorPline := rPline;
        rAnchorQline := rQline;
        rAnchorPess := rPess;
        rAnchorQess := rQess;
        iEstimatorCount := 0;
    END_IF;
END_IF;

rSVP := MIN(0.4, MAX(0.01, rSVP));
rSVQ := MIN(0.4, MAX(0.01, rSVQ));
rKP := MIN(1.5, MAX(0.2, rKP));
rKQ := MIN(1.5, MAX(0.2, rKQ));

(* Low-voltage state machine. *)
rVnativeEstimate := rVpu - rSVP * rPess - rSVQ * rQess;
IF NOT xLow THEN
    IF rVpu < 0.95 THEN
        xLow := TRUE;
        iRecoverCount := 0;
    END_IF;
ELSE
    IF (rVpu > 0.975) AND (rVnativeEstimate > 0.95) THEN
        iRecoverCount := MIN(10, iRecoverCount + 1);
    ELSE
        iRecoverCount := 0;
    END_IF;

    IF iRecoverCount >= 10 THEN
        xLow := FALSE;
        iRecoverCount := 0;
        iUvCount := 0;
        xQLatched := FALSE;
    END_IF;
END_IF;

(* Reconstruct feeder behavior without the BESS contribution. *)
rPnative := rPline + rKP * rPess;
rQnative := rQline + rKQ * rQess;
rSnative := SQRT(rPnative * rPnative + rQnative * rQnative);
rSline := SQRT(rPline * rPline + rQline * rQline);
rNativeLoading := 0.0;
rSlimit := 0.0;

IF (rLoading > 0.000001) AND (rSline > 0.000001) THEN
    rSlimit := 0.95 * rSline / rLoading;
    rNativeLoading := rLoading * rSnative / rSline;
END_IF;

(* Forward/reverse overload state machine with hysteresis. *)
IF (iDirection = 0) AND (rNativeLoading > 1.0) THEN
    IF rPnative > 0.0 THEN
        iDirection := 1;
    ELSIF rPnative < 0.0 THEN
        iDirection := -1;
    END_IF;
ELSIF (iDirection <> 0) AND (rNativeLoading < 0.95) THEN
    iDirection := 0;
END_IF;

rPcmd := 0.0;
rQcmd := 0.0;
iMode := 0;

(* Mode 1: low voltage, reactive support first. *)
IF xLow THEN
    rEV := 0.98 - rVpu;
    rRawQ := rQess + 0.75 * rEV / MAX(rSVQ, 0.01);
    rQcmd := MIN(0.4, MAX(0.0, rRawQ));

    IF NOT xQLatched THEN
        IF (rQcmd >= 0.95 * 0.4) AND (rQess >= 0.95 * 0.4) THEN
            iUvCount := MIN(10, iUvCount + 1);
        ELSE
            iUvCount := MAX(0, iUvCount - 1);
        END_IF;

        IF iUvCount >= 10 THEN
            xQLatched := TRUE;
        END_IF;
    END_IF;

    IF xQLatched AND (rSOC > 0.1 + 0.02) THEN
        rPvoltage := MIN(0.8, MAX(0.0, rPess + 0.65 * rEV / MAX(rSVP, 0.01)));
        rPcmd := rPvoltage;

        IF (iDirection > 0) AND (rSlimit > 0.000001) THEN
            rQafter := rQnative - rKQ * rQcmd;
            rPtarget := SQRT(MAX(rSlimit * rSlimit - rQafter * rQafter, 0.0));
            rPover := MAX(0.0, (rPnative - rPtarget) / MAX(rKP, 0.2));
            rPcmd := MAX(rPcmd, MIN(0.8, rPover));
        END_IF;
    END_IF;
    iMode := 1;

(* Modes 2/3: feeder overload without low voltage. *)
ELSIF (iDirection <> 0) AND (rSlimit > 0.000001) THEN
    rPtarget := SQRT(MAX(rSlimit * rSlimit - rQnative * rQnative, 0.0));
    IF (iDirection > 0) AND (rSOC > 0.1 + 0.02) THEN
        rPcmd := MIN(0.8, MAX(0.0, (rPnative - rPtarget) / MAX(rKP, 0.2)));
        iMode := 2;
    ELSIF (iDirection < 0) AND (rSOC < 0.9 - 0.02) THEN
        rPcmd := MAX(-0.8, MIN(0.0, (rPnative + rPtarget) / MAX(rKP, 0.2)));
        iMode := 3;
    END_IF;
ELSE
    iUvCount := 0;
    xQLatched := FALSE;
END_IF;

rSensVP := rSVP;
rSensVQ := rSVQ;
rInfluenceP := rKP;
rInfluenceQ := rKQ;
rConfidence := 0.25 * (rCVP + rCVQ + rCKP + rCKQ);
xReady := TRUE;
