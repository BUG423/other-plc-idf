# Working on this local PLC project

Read README.md, plcidf.json, main.st and scenarios.json when present before editing.
Edit the ST implementation and its corresponding requirement-based scenarios.
The default counter project has no scenario file; add explicit acceptance
scenarios before claiming scenario verification.

Run `plcidf check . --json` first, then `plcidf verify . --json`.
The verification report is written to `.plcidf/verification.json` by default.
An expectation is an acceptance requirement: change it only when the user's
requirement changes and explain that change. Never weaken or rewrite expectations
merely to make a failed implementation pass.

This target is a local Linux memory-only runtime. Report results as local-only;
do not switch to Easy521 or operate hardware without an explicitly selected
target and task. No independent chat UI or AI-provider API is required.

In the final report include program/source hashes, each scenario's pass/fail
result, and any failed command or unverified behavior. A project check alone
does not prove compilation, execution, physical I/O, or real-time performance.
