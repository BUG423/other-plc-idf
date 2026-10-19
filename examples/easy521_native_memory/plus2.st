(* Easy521 existing-interface laboratory MAIN body, revision +2.
   Target: only the pinned BESS Easy521 interface in research 27/28.
   Interface: g_rBESS_Pcmd : REAL, existing global, no new allocation.
   Unit here: dimensionless scan increments, replacing its normal process
   meaning; this is not a BESS power-command implementation.
   Task: add 2 per MAIN invocation. Existing task configuration is preserved;
   the invocation period remains unknown. Timing cannot be inferred from this
   source alone, and host samples need not occur once per PLC scan.
   Initial/retain: unchanged template, initial text 0; actual initialization,
   retained value and restart behavior require independent device observation.
   Range: float32 -> float64 addition -> float32. At sufficiently large values
   +2 also rounds away; this is not an unbounded integer scan counter.
   Stop/fault: runtime invocation control, no local reset or recovery logic.
   MAIN has no output-terminal instruction; inherited units/configuration
   still require a separate whole-application/device review.
*)
g_rBESS_Pcmd := g_rBESS_Pcmd + 2.0;
