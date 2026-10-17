"""Durable evidence for operations that must stop when logging is unavailable."""

from __future__ import annotations

import fcntl
import json
import os

from .safety import EvidenceLogger, SafetyError, _now_iso


class EvidenceWriteError(SafetyError):
    """Evidence could not be durably recorded; a new side effect must not start."""


class StrictEvidenceLogger(EvidenceLogger):
    """EvidenceLogger-compatible API with cross-process locking and fsync.

    Write the operation's intent before performing a side effect. A later
    failure to write its result cannot prove that the operation did not occur.
    """

    def log(self, command: str, params: dict | None = None,
            result: dict | None = None, **extra) -> dict:
        record = {"ts": _now_iso(), "command": command,
                  "params": params or {}, "result": result or {}}
        record.update(extra)
        try:
            line = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self.path, "a", encoding="utf-8") as fh:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                try:
                    fh.write(line)
                    fh.flush()
                    os.fsync(fh.fileno())
                finally:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return record
        except (OSError, ValueError, TypeError) as exc:
            raise EvidenceWriteError(f"Evidence write failed ({self.path}): {exc}") from exc

    def ensure_writable(self) -> dict:
        """Persist a preflight event; do this before issuing a state change."""
        return self.log("evidence.preflight", result={"durable": True})
