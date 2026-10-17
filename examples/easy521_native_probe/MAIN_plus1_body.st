(* EASY521 NATIVE MEMORY PROBE, revision 1, step +1.
   MAIN body only. Reuse the existing DEV variables and task unchanged.
   No physical I/O, communication instructions, or fbBESS invocation.
   M8002 is read-only: the existing first-RUN-scan system flag.
   This temporary probe replaces algorithm meanings; restore DEV after testing. *)
IF M8002 OR (g_rBESS_SensVP <> 52101.0) OR (g_iBESS_Mode < 0) OR (g_iBESS_Mode >= 9999) THEN
    g_iBESS_Mode := 0;
    g_rBESS_Confidence := 0.0;
    g_rBESS_InfluenceP := 0.0;
    g_rBESS_InfluenceQ := 0.0;
    g_xBESS_ControllerReady := FALSE;
END_IF;

g_xBESS_Enable := FALSE;
g_xBESS_Reset := TRUE;
g_xBESS_MeasurementsValid := FALSE;
g_rBESS_Pcmd := 0.0;
g_rBESS_Qcmd := 0.0;
g_rBESS_SensVP := 52101.0;
g_rBESS_SensVQ := 1.0;

g_iBESS_Mode := g_iBESS_Mode + 1;
g_rBESS_Confidence := g_rBESS_Confidence + 1.0;
IF (g_iBESS_Mode MOD 100) = 0 THEN
    g_rBESS_InfluenceP := g_rBESS_InfluenceP + 100.0;
    g_rBESS_InfluenceQ := g_rBESS_Confidence;
    g_xBESS_ControllerReady := NOT g_xBESS_ControllerReady;
END_IF;
