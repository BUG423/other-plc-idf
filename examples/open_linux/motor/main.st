(* Local memory-only teaching example. This is not a safety-certified controller. *)
PROGRAM MAIN
VAR
    START : BOOL := FALSE;
    STOP : BOOL := FALSE;
    PERMISSIVE : BOOL := FALSE;
    FAULT : BOOL := FALSE;
    MOTOR : BOOL := FALSE;
    STARTING : BOOL := FALSE;
    FAULTED : BOOL := FALSE;
    requested : BOOL := FALSE;
    start_edge : R_TRIG;
    startup_delay : TON;
END_VAR

(* Evaluate the edge even while inhibited: a held START cannot restart later. *)
start_edge(CLK := START);
FAULTED := FAULT OR NOT PERMISSIVE;
IF STOP OR FAULTED THEN
    requested := FALSE;
ELSIF start_edge.Q THEN
    requested := TRUE;
END_IF;

startup_delay(IN := requested, PT := T#60ms);
MOTOR := requested AND startup_delay.Q;
STARTING := requested AND NOT startup_delay.Q;
END_PROGRAM

CONFIGURATION Config0
RESOURCE Res0 ON PLC
    TASK scan (INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH scan : MAIN;
END_RESOURCE
END_CONFIGURATION
