(* Easy521 / Easy52X: board Y0 indicator demonstration, MAIN BODY ONLY.
   BENCH ONLY: all physical outputs must be confirmed disconnected from loads.
   Y0 is a REAL OUTPUT command, not a memory-only probe or a separate LED GPIO.

   Interface / task:
   - Replace MAIN statements in an explicit disposable vendor working project.
   - Reuse its existing MAIN task; historical DEV configuration is nominal 5 ms.
     Confirm the actual task before compilation. No TASK/VAR/PROGRAM is added.
   - Read-only inputs: M8002 BOOL (first RUN scan), M8013 BOOL (1 s clock).
   - Sole output: Y0 BOOL. No globals, timers, counters, retaining state, FBs,
     motion or communication calls are introduced. No other Y output is written.
   - Y0 must have a single writer and no pulse/axis/force assignment in the project.

   Timing / initial state:
   - First RUN scan commands Y0 FALSE using M8002; this is only one task scan,
     not a visible startup delay and not a promise about pre-RUN hardware state.
   - Later scans copy the current M8013 state. Its documented full period is 1 s.
     The cited manual does not specify startup phase or exact high/low durations.
     Output refresh samples that clock at the existing task/IO timing.
   - On STOP this code no longer executes. The manual describes Y output stopped
     by the controller; this branch is NOT a software STOP-clear implementation.
     Confirm actual mode and indicator behavior separately; no safety guarantee.

   Evidence: official bundled programming manual pp.90,168,248-249;
   instruction manual pp.27-28; Easy user manual pp.137,141,151.
   Exact source identities and limitations are in this directory's README.
   Acceptance requires separate dated compiler/device records; this source
   alone does not establish compilation, download or actual hardware behavior.
*)

IF M8002 THEN
    Y0 := FALSE;
ELSE
    Y0 := M8013;
END_IF;
