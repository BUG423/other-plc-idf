# Local motor request and delay example

This complete IEC ST project runs only in the local Linux memory I/O runtime.
Its scan period is 20 ms and its TON start delay is 60 ms. It has no physical
motor connection and makes no safety certification or hard real-time claim.

Inputs are BOOL `START`, `STOP`, `PERMISSIVE`, and `FAULT`. Outputs are BOOL
`MOTOR`, `STARTING`, and `FAULTED`. `FAULTED` reflects `FAULT OR NOT PERMISSIVE`;
it clears when those present conditions clear, while the start request remains
cleared until a new START rising edge.

START rising while permitted latches a request. Releasing START preserves it.
STOP has priority over START in the same scan. STOP, FAULT, or lost PERMISSIVE
clears the request and MOTOR in that scan and resets the TON. Holding START
high across recovery never restarts the output: release it and provide a new
rising edge. The delay applies again after that new edge.

Run `plcidf check . --json`, then `plcidf verify . --json`. The six scenarios
cover the delay boundary, stop priority, lost permission, fault recovery, a
released start latch, and an inhibited start. For the boundary scenario scan 1
is time 0: MOTOR is false through scan 3 (40 ms), then true on scan 4 (60 ms).

Scenario steps apply the input changes before scanning; omitted inputs retain
their last values. Each step's expectations apply to its last scan. Each
scenario starts a new initialized runtime. Keep expectations tied to the
requested behavior and report local-only results with the program/source hashes.
