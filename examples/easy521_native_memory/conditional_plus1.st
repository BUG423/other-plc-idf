(* MatIEC stateless REAL laboratory body: conditional increment/reset, step 1.
   Target/interface: pinned Easy521 BESS allocation; existing g_rBESS_Pcmd REAL.
   Unit: dimensionless experiment value, replacing its normal process meaning.
   Task period and retention are inherited, not known; one evaluation per MAIN
   invocation. The adapter binds the existing variable without resetting it.
   Transition: values below 10 advance by 1, values at/above 10 reset to 0.
   Starting at 0: 0,1,...,10,0. Actual initial value is measured separately.
   A negative initial value is not clamped; NaN compares false and resets to 0.
   Runtime STOP controls invocation. No timer, local state, force or I/O exists
   in this body. Whole-application/physical behavior requires device testing.
*)
IF g_rBESS_Pcmd < REAL#10.0 THEN
    g_rBESS_Pcmd := g_rBESS_Pcmd + REAL#1.0;
ELSE
    g_rBESS_Pcmd := REAL#0.0;
END_IF;
