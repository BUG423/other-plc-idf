(* AutoShop MAIN/ST body. Configure this program block for a 5 ms constant cycle. *)

fbBESS(
    xEnable := g_xBESS_Enable,
    xReset := g_xBESS_Reset,
    xInputValid := g_xBESS_MeasurementsValid,
    rVpu := g_rBESS_Vpu,
    rPline := g_rBESS_Pline,
    rQline := g_rBESS_Qline,
    rLoading := g_rBESS_Loading,
    rSOC := g_rBESS_SOC,
    rPess := g_rBESS_Pess,
    rQess := g_rBESS_Qess
);

g_rBESS_Pcmd := fbBESS.rPcmd;
g_rBESS_Qcmd := fbBESS.rQcmd;
g_iBESS_Mode := fbBESS.iMode;
g_rBESS_SensVP := fbBESS.rSensVP;
g_rBESS_SensVQ := fbBESS.rSensVQ;
g_rBESS_InfluenceP := fbBESS.rInfluenceP;
g_rBESS_InfluenceQ := fbBESS.rInfluenceQ;
g_rBESS_Confidence := fbBESS.rConfidence;
g_xBESS_ControllerReady := fbBESS.xReady;

PLCIDF_NEGATIVE_TEST := ;
