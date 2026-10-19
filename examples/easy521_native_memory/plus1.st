(* Easy521 existing-interface laboratory MAIN body, revision +1.
   Target: only the pinned BESS Easy521 interface in research 27/28.
   Interface: g_rBESS_Pcmd : REAL, existing global, no new allocation.
   Unit here: dimensionless scan increments; this replaces its normal process
   meaning and must not be used as a BESS power-command implementation.
   Task: one addition per MAIN invocation. Existing task configuration is
   preserved; the actual invocation period is unknown, not assumed to be 1 ms.
   Initial/retain: the source declares neither. The existing template stores
   initial text 0; online initial value and retention behavior need measurement.
   Range: float32, widened for addition and narrowed on store. Above 2^24,
   +1 can round to the same value. NaN/infinity are not repaired by this probe.
   Stop/fault: no local state machine or reset; the runtime controls invocation.
   This MAIN has no output-terminal instruction. Other inherited units and
   configuration still require a separate whole-application/device review.
*)
g_rBESS_Pcmd := g_rBESS_Pcmd + 1.0;
