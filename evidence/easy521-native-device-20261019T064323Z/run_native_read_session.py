"""One authorized bench session; temporary host changes are explicitly restored.

Run as this WSL distribution's root, with ordinary-user readers under audit
guards. This experiment is fixed to the freshly observed 0471:a531 at 1-1,
001:002 and its descriptor hash; it is not a generic onboarding command.
It temporarily changes only this USB node's owner and unbinds this device's CDC
control interface. No global module unload, USB reset/configuration change,
PLC variable/mode write or application download is requested. Rebinding the
kernel driver can itself send CDC class control requests; host preparation is
not claimed to have literally zero USB communication. Every child is bounded,
one failed protocol observation stops later observations, and no request is
automatically retried. The final JSON preserves attempts and cleanup results.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import signal
import stat
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SYSFS = Path("/sys/bus/usb/devices/1-1")
NODE = Path("/dev/bus/usb/001/002")
DRIVER = Path("/sys/bus/usb/drivers/cdc_acm")
CONTROL = "1-1:2.0"
DATA = "1-1:2.1"
DESCRIPTOR_SHA256 = "be0da4a2a37147d2e0068bb492bf0eb89ea067e2808ecdcf6a756b0ddbd51351"
UID = 1000
record = {"schema": "plcidf.native-usb-bench-session.v1", "actions": [],
          "observations": [], "protocol_completed": False,
          "plc_application_write_requested": False, "cpu_mode_write_requested": False,
          "temporary_host_preparation": True, "automatic_retries": 0}


def now():
    return datetime.now(timezone.utc).isoformat()


def save():
    (HERE / "native-read-session.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def driver(interface):
    path = Path("/sys/bus/usb/devices") / interface / "driver"
    return str(path.resolve()) if path.exists() else None


def identity():
    return {(name): (SYSFS / name).read_text().strip()
            for name in ("idVendor", "idProduct", "busnum", "devnum", "bConfigurationValue")}


def check_identity():
    if identity() != {"idVendor": "0471", "idProduct": "a531", "busnum": "1",
                      "devnum": "2", "bConfigurationValue": "2"}:
        raise RuntimeError("USB identity/configuration changed; no further action")
    if hashlib.sha256((SYSFS / "descriptors").read_bytes()).hexdigest() != DESCRIPTOR_SHA256:
        raise RuntimeError("USB descriptors changed; no further action")


def restore_host_state():
    """Restore only this recorded device; keep individual restoration failures."""
    cleanup = {"started_at": now(), "errors": []}
    original = record["original_node"]
    try:
        check_identity()
        if driver(CONTROL) is None:
            (DRIVER / "bind").write_text(CONTROL)
        cleanup["drivers"] = {CONTROL: driver(CONTROL), DATA: driver(DATA)}
        cleanup["drivers_restored"] = cleanup["drivers"] == record["original_drivers"]
    except BaseException as error:
        cleanup["errors"].append("driver restore: " + str(error))
    try:
        check_identity()
        if NODE.stat().st_rdev != original["rdev"]:
            raise RuntimeError("Device node replaced; not changing its owner")
        os.chown(NODE, original["uid"], original["gid"])
        os.chmod(NODE, original["mode"])
        after = NODE.stat()
        cleanup["node_restored"] = (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (
            original["uid"], original["gid"], original["mode"])
    except BaseException as error:
        cleanup["errors"].append("permission restore: " + str(error))
    cleanup["finished_at"] = now()
    return cleanup


def terminate(signum, frame):
    """Convert catchable termination into an exception so finally can restore."""
    raise RuntimeError("Session signal " + str(signum) + "; no request replay")


def read_once(index, kind, profile):
    """Run exactly one ordinary-user native read; save raw JSON before return."""
    check_identity()
    argv = ["/usr/sbin/runuser", "-u", pwd.getpwuid(UID).pw_name, "--",
            str(ROOT / ".venv/bin/python"), str(HERE / "guard_native_read.py"),
            kind, profile, "001:002"]
    event = {"index": index, "kind": kind, "profile": profile,
             "started_at": now(), "argv": argv, "timeout_s": 12,
             "request_outcome": "unknown"}
    record["observations"].append(event)
    save()
    try:
        process = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=12)
        event.update(returncode=process.returncode, stderr=process.stderr,
                     stdout=process.stdout, finished_at=now())
        result = json.loads(process.stdout)
        path = HERE / f"{index:02d}-{kind}-{profile}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        event.update(report=path.name, request_outcome=result.get("request_outcome", "unknown"))
        save()
        return result
    except BaseException as error:
        event.update(error=str(error), finished_at=now())
        save()
        raise


def main():
    global record
    if os.geteuid() != 0:
        raise RuntimeError("This explicit host-preparation experiment requires root")
    if sys.argv[1:] == ["--restore-only"]:
        record = json.loads((HERE / "native-read-session.json").read_text())
        cleanup = restore_host_state()
        record.setdefault("recovery_attempts", []).append(cleanup)
        save()
        print(json.dumps({"recovery": cleanup}))
        return 0 if cleanup.get("drivers_restored") and cleanup.get("node_restored") else 1
    if sys.argv[1:]:
        raise RuntimeError("Only the explicit --restore-only recovery option is accepted")
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGALRM):
        signal.signal(signum, terminate)
    # Nine bounded children (including the driver refusal) plus fuser/delays;
    # outer executor uses 150s, leaving recovery time beyond this 120s budget.
    signal.alarm(120)
    check_identity()
    original = NODE.stat()
    record.update(started_at=now(), original_identity=identity(),
                  original_node={"uid": original.st_uid, "gid": original.st_gid,
                                 "mode": stat.S_IMODE(original.st_mode), "rdev": original.st_rdev},
                  original_drivers={CONTROL: driver(CONTROL), DATA: driver(DATA)},
                  source_sha256={str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (Path(__file__).resolve(), HERE / "guard_native_read.py",
                                              ROOT / "tools/easy521_linux_usb.py",
                                              ROOT / "tools/read_easy521_usb_runtime.py",
                                              ROOT / "tools/read_easy521_usb_memory.py")})
    save()
    if any(value != str(DRIVER) for value in record["original_drivers"].values()):
        raise RuntimeError("Expected both original CDC interfaces; no preparation performed")
    control_path = (Path("/sys/bus/usb/devices") / CONTROL).resolve()
    ttys = [Path("/dev") / item.name for item in Path("/sys/class/tty").glob("ttyACM*")
            if (item / "device").resolve() == control_path]
    if len(ttys) != 1 or not ttys[0].exists():
        raise RuntimeError("One existing serial node owned by this control interface is required")
    record["verified_tty"] = str(ttys[0])
    busy = subprocess.run(["/usr/bin/fuser", "-v", str(ttys[0])],
                          capture_output=True, text=True, timeout=5)
    record["tty_in_use_check"] = {"returncode": busy.returncode,
                                  "stdout": busy.stdout, "stderr": busy.stderr}
    if busy.returncode != 1 or busy.stdout.strip():
        save()
        raise RuntimeError("Original CDC serial port is in use or could not be checked")
    permission_changed = unbind_attempted = False
    try:
        permission_changed = True
        record["actions"].append({"action": "temporary-node-owner", "uid": UID, "at": now()})
        save()
        os.chown(NODE, UID, original.st_gid)
        # Prove the reader refuses an occupied driver before any PLC read frame.
        blocked = read_once(0, "runtime", "gcm-state")
        transport = blocked.get("transport_record", {})
        if (blocked.get("ok") is not False or blocked.get("request_outcome") != "not_sent"
                or transport.get("kernel_driver_active_rc") != 1
                or transport.get("write_calls") != 0):
            raise RuntimeError("Occupied-driver rejection was not established; stopping")
        check_identity()
        unbind_attempted = True
        record["actions"].append({"action": "unbind-selected-control-interface",
                                   "interface": CONTROL, "at": now()})
        save()
        (DRIVER / "unbind").write_text(CONTROL)
        if driver(CONTROL) is not None or driver(DATA) is not None:
            raise RuntimeError("Selected CDC interfaces were not released")
        record["drivers_after_unbind"] = {CONTROL: driver(CONTROL), DATA: driver(DATA)}
        plan = [("runtime", "gcm-state"), ("memory", "m8000"), ("memory", "m8001"),
                ("memory", "m8013"), ("memory", "m8013"), ("memory", "m8013"),
                ("memory", "m8013"), ("runtime", "gcm-state")]
        for index, (kind, profile) in enumerate(plan, 1):
            if profile == "m8013" and index > 4:
                time.sleep(0.35)  # New scheduled observations, never retries after failure.
            result = read_once(index, kind, profile)
            if result.get("ok") is not True:
                raise RuntimeError("One native observation failed; no further requests issued")
        record["protocol_completed"] = True
    except BaseException as error:
        record["error"] = str(error)
    finally:
        signal.alarm(0)
        cleanup = restore_host_state()
        record.update(cleanup=cleanup, finished_at=now())
        save()
    print(json.dumps({"protocol_completed": record["protocol_completed"],
                      "error": record.get("error"), "cleanup": record["cleanup"]}))
    return 0 if (record["protocol_completed"] and record["cleanup"].get("drivers_restored")
                 and record["cleanup"].get("node_restored")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
