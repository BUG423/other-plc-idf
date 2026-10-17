# Local REAL threshold and hysteresis example

This complete local Linux memory-only project has BOOL input ENABLE and REAL
input VALUE. BOOL output ALARM sets at VALUE >= 80.0, clears at VALUE <= 70.0,
and preserves its previous state between thresholds. Disabled operation clears
ALARM immediately. REAL output ECHO copies VALUE for observable numeric checks.

Run `plcidf check . --json`, then `plcidf verify . --json`. The three independent
scenarios cover both exact threshold boundaries, hysteresis, disabling, input
hold, and REAL expectations with explicit absolute tolerance. Inputs are applied
before a step's scans, and its expectations refer to the final scan. Every
scenario starts with new initialized state.

This is a complete example, not an additional registered `new` template.
It has no physical I/O bindings, certified safety behavior, or guaranteed
real-time deadlines. Report verification as local-only with source/program hashes.
