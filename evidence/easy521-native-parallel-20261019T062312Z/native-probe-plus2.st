(* Linux-only research probe; no physical I/O or PLC deployment.
   Interface: external COUNTER:DINT, unit counts; modulo 2**32 arithmetic.
   One MAIN call updates COUNTER once. Task period/initial value/retention
   are not established by this source or by the object compiler.
   No start/stop/fault state machine is implemented in this memory-only probe.
   The increment is 2 counts per invocation; assignment executes every call. *)
COUNTER := COUNTER + 2;
