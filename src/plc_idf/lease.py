"""Process-held WSL leases for cooperating PLC-IDF commands.

The kernel lock, rather than a PID or an age threshold, owns the lease. Files
remain in place after release: unlinking a lock file would let two processes
lock different inodes for the same resource. A crashed holder is recovered on
the next successful lock and its metadata is retained in the new record.

This coordinates PLC-IDF processes; an independently started Windows AutoShop
does not participate in these locks.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
import uuid
from contextlib import ExitStack
from pathlib import Path


class LeaseError(RuntimeError):
    """Lease acquisition or persistence failed before an operation could start."""


class LeaseBusy(LeaseError):
    """Another process currently holds this resource."""


def default_lease_directory() -> Path:
    """One namespace per WSL user, shared by every project checkout."""
    return Path("/tmp") / f"plcidf-{os.getuid()}" / "leases"


def device_resource(busid: str, vidpid: str) -> str:
    busid, vidpid = str(busid).strip(), str(vidpid).strip().lower()
    if not re.fullmatch(r"\d+(?:-\d+)+", busid) or not re.fullmatch(
            r"[0-9a-f]{4}:[0-9a-f]{4}", vidpid):
        raise LeaseError("Device lease requires a valid BUSID and VID:PID")
    # BUSID is the actual shared USB location. A changed VID:PID must not make
    # a second lock namespace for the same physical port.
    return f"device:usb:{busid}"


class ResourceLease:
    """Nonblocking context manager holding one exclusive flock until release."""

    def __init__(self, resource: str, *, directory: str | Path | None = None,
                 purpose: str = "", evidence=None):
        if not isinstance(resource, str) or not resource.strip():
            raise LeaseError("Lease resource must be a nonempty string")
        self.resource = resource
        self.directory = Path(directory) if directory is not None else default_lease_directory()
        self.path = self.directory / (hashlib.sha256(resource.encode()).hexdigest() + ".json")
        self.purpose = purpose
        self.evidence = evidence
        self.record: dict | None = None
        self._fh = None

    def _persist(self, record: dict) -> None:
        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def acquire(self):
        if self._fh is not None:
            raise LeaseError("Lease is already held by this instance")
        fh = None
        locked = False
        try:
            self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            fh = os.fdopen(fd, "r+", encoding="utf-8")
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LeaseBusy(f"Resource is busy: {self.resource}") from exc
            locked = True
            previous_text = fh.read()
            try:
                previous = json.loads(previous_text) if previous_text else None
            except ValueError:
                previous = {"state": "unreadable", "metadata_sha256":
                            hashlib.sha256(previous_text.encode()).hexdigest()}
            recovered = bool(previous_text and (not isinstance(previous, dict)
                                               or previous.get("state") != "released"))
            try:
                boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            except OSError:
                boot_id = None
            self._fh = fh
            self.record = {"schema_version": 1, "resource": self.resource,
                           "state": "held", "pid": os.getpid(), "boot_id": boot_id,
                           "acquired_at": time.time(), "purpose": self.purpose,
                           "token": uuid.uuid4().hex, "recovered": recovered}
            if recovered:
                self.record["recovered_from"] = previous
            self._persist(self.record)
            if self.evidence is not None:
                if recovered:
                    self.evidence.log("lease.recovered", params={"resource": self.resource},
                                      result={"previous": previous})
                self.evidence.log("lease.acquire", params={"resource": self.resource},
                                  result=self.record)
            return self
        except BaseException as exc:
            self._fh = None
            if fh is not None:
                if locked:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()
            if isinstance(exc, OSError):
                raise LeaseError(f"Cannot acquire/persist lease {self.resource}: {exc}") from exc
            raise

    def release(self) -> None:
        if self._fh is None:
            return
        fh = self._fh
        try:
            record = dict(self.record, state="released", released_at=time.time())
            self._persist(record)
            self.record = record
            if self.evidence is not None:
                self.evidence.log("lease.release", params={"resource": self.resource},
                                  result={"token": record["token"]})
        finally:
            self._fh = None
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback):
        self.release()


class LeaseSet:
    """Acquire all requested resources, rolling back on any contention/error."""

    def __init__(self, resources, **kwargs):
        self.resources = sorted(set(resources))
        if not self.resources:
            raise LeaseError("At least one resource is required")
        self.kwargs = kwargs
        self.leases: list[ResourceLease] = []
        self._stack = None

    def __enter__(self):
        if self._stack is not None:
            raise LeaseError("Lease set is already held")
        with ExitStack() as stack:
            self.leases = [stack.enter_context(ResourceLease(resource, **self.kwargs))
                           for resource in self.resources]
            self._stack = stack.pop_all()
        return self

    def __exit__(self, *args):
        stack, self._stack = self._stack, None
        if stack is not None:
            return stack.__exit__(*args)


def bridge_lease(busid: str, vidpid: str, **kwargs) -> LeaseSet:
    """Reserve the USB location and the shared AutoShop interactive desktop."""
    return LeaseSet([device_resource(busid, vidpid), "autoshop:interactive-desktop"], **kwargs)
