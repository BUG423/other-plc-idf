(******************************************************************************
 * Assign this program to a cyclic PLC task with an interval of exactly 5 ms.
 ******************************************************************************)
PROGRAM PRG_BESS_Control
VAR
    fbSupervisor : FB_BESS_AdaptiveSupervisor;
END_VAR

fbSupervisor(
    xEnable := xBESS_Enable,
    xReset := xBESS_Reset,
    xInputValid := xBESS_MeasurementsValid,
    lrVpu := lrBESS_Vpu,
    lrPline := lrBESS_Pline,
    lrQline := lrBESS_Qline,
    lrLoading := lrBESS_Loading,
    lrSOC := lrBESS_SOC,
    lrPess := lrBESS_Pess,
    lrQess := lrBESS_Qess
);

lrBESS_Pcmd := fbSupervisor.lrPcmd;
lrBESS_Qcmd := fbSupervisor.lrQcmd;
uiBESS_Mode := fbSupervisor.uiMode;
lrBESS_SensVP := fbSupervisor.lrSensVP;
lrBESS_SensVQ := fbSupervisor.lrSensVQ;
lrBESS_InfluenceP := fbSupervisor.lrInfluenceP;
lrBESS_InfluenceQ := fbSupervisor.lrInfluenceQ;
lrBESS_Confidence := fbSupervisor.lrConfidence;
xBESS_ControllerReady := fbSupervisor.xReady;

END_PROGRAM
