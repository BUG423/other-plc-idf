"""Kernel-backed contention and crash recovery, with no Windows/PLC access."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from plc_idf.evidence import EvidenceWriteError, StrictEvidenceLogger
from plc_idf.lease import (LeaseBusy, LeaseSet, ResourceLease, bridge_lease,
                           device_resource)


def child_env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    return env


def test_parallel_process_refused_then_release_allows_acquisition(tmp_path):
    code = """
import sys
from plc_idf.lease import LeaseBusy, ResourceLease
try:
    with ResourceLease('same-device', directory=sys.argv[1]):
        sys.exit(0)
except LeaseBusy:
    sys.exit(23)
"""
    def run_child():
        return subprocess.run([sys.executable, "-c", code, str(tmp_path)],
                              env=child_env(), capture_output=True, timeout=10)
    with ResourceLease("same-device", directory=tmp_path):
        assert run_child().returncode == 23
    assert run_child().returncode == 0


def test_crashed_holder_recovers_with_owner_evidence(tmp_path):
    directory = tmp_path / "leases"
    code = """
import os, sys
from plc_idf.lease import ResourceLease
lease = ResourceLease('device', directory=sys.argv[1], purpose='crash fixture').acquire()
os._exit(0)
"""
    crashed = subprocess.run([sys.executable, "-c", code, str(directory)],
                             env=child_env(), capture_output=True, timeout=10)
    assert crashed.returncode == 0, crashed.stderr
    evidence = StrictEvidenceLogger(tmp_path / "evidence.jsonl")
    with ResourceLease("device", directory=directory, evidence=evidence) as recovered:
        assert recovered.record["recovered"] is True
        assert recovered.record["recovered_from"]["purpose"] == "crash fixture"
    assert any(row["command"] == "lease.recovered" for row in evidence.read_all())


def test_lease_set_rolls_back_when_second_resource_busy(tmp_path):
    with ResourceLease("z-busy", directory=tmp_path):
        with pytest.raises(LeaseBusy):
            with LeaseSet(["a-free", "z-busy"], directory=tmp_path):
                pytest.fail("Should not acquire partial set")
        with ResourceLease("a-free", directory=tmp_path):
            pass


def test_autoshop_is_shared_across_different_devices(tmp_path):
    with bridge_lease("6-4", "0471:a531", directory=tmp_path):
        with pytest.raises(LeaseBusy):
            with bridge_lease("6-3", "0471:a531", directory=tmp_path):
                pytest.fail("AutoShop must have one owner")


def test_same_physical_usb_port_cannot_get_second_lease_on_changed_vidpid():
    assert device_resource("6-4", "0471:a531") == device_resource("6-4", "1234:abcd")


def test_release_does_not_unlink_lock_inode(tmp_path):
    lease = ResourceLease("resource", directory=tmp_path)
    with lease:
        inode = lease.path.stat().st_ino
    assert lease.path.stat().st_ino == inode
    assert json.loads(lease.path.read_text())["state"] == "released"


def test_failed_evidence_stops_acquisition_and_releases_kernel_lock(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("file")
    evidence = StrictEvidenceLogger(blocked / "evidence.jsonl")
    with pytest.raises(EvidenceWriteError):
        with ResourceLease("device", directory=tmp_path / "leases", evidence=evidence):
            pytest.fail("No operation may begin without durable intent")
    with ResourceLease("device", directory=tmp_path / "leases"):
        pass


def test_strict_evidence_sync_failure_refused(tmp_path, monkeypatch):
    evidence = StrictEvidenceLogger(tmp_path / "evidence.jsonl")
    def fail_sync(fd):
        raise OSError("disk cannot persist")
    monkeypatch.setattr("plc_idf.evidence.os.fsync", fail_sync)
    with pytest.raises(EvidenceWriteError, match="disk cannot persist"):
        evidence.ensure_writable()


def test_strict_evidence_success_is_readable(tmp_path):
    evidence = StrictEvidenceLogger(tmp_path / "evidence.jsonl")
    evidence.ensure_writable()
    evidence.log("intent", params={"device": "6-4"})
    assert [item["command"] for item in evidence.read_all()] == ["evidence.preflight", "intent"]
