(* MatIEC stateless REAL laboratory body: conditional increment/reset, step 2.
   Target/interface: pinned Easy521 BESS allocation; existing g_rBESS_Pcmd REAL.
   Unit: dimensionless experiment value, not an operating power command.
   Cycle/retain/initialization are inherited and independently measured. The
   adapter never resets external memory when it initializes local pointers.
   Transition: values below 10 advance by 2; values at/above 10 reset to 0.
   From 0: 0,2,4,6,8,10,0. From an odd value, an intermediate 11 is possible.
   Negative initial values are not clamped; NaN compares false and resets to 0.
   Runtime STOP controls invocation. No timer, local state, force or terminal
   instruction is implemented here; device behavior is a separate acceptance.
*)
IF g_rBESS_Pcmd < REAL#10.0 THEN
    g_rBESS_Pcmd := g_rBESS_Pcmd + REAL#2.0;
ELSE
    g_rBESS_Pcmd := REAL#0.0;
END_IF;
