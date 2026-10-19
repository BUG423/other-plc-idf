"""Bounded native Easy521 transactions; preview performs no device I/O.

Uses the verified CDC data-interface1 profile from easy521_linux_usb.py, without
weakening its fixed read entry point. Application requests use the independently
recovered H530 Upload/Download/GCM contracts documented in report26. Executing
an upload-read allocates a device file route, but never sends create/truncate,
file data writes, CPU control or firmware commands. Explicit download/control
functions are separate writers and never retry, reconnect or roll back.

This module does not compile ST, generate a complete application manifest,
verify password policy, infer remote directories or guarantee recovery after
power loss. Transfer ACK is not application activation/execution verification.
Read/control preview never resolves output paths or reads local files. Manifest
download preview reads only explicit local files; no preview loads libusb,
enumerates devices, locks, generates random sequences or writes.
"""

from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time


def _adjacent(name):
    spec = importlib.util.spec_from_file_location("_plcidf_application_" + name, Path(__file__).with_name(name + ".py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("Adjacent native application component is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


codec = _adjacent("easy521_application_protocol")
native = _adjacent("easy521_linux_usb")


class _Session:
    """Private single-handle exchange; caller holds location lease.

    Only codec-validated frames are accepted, and read sessions reject writers.
    Transfer timeouts are milliseconds; deadline is a monotonic absolute time.
    Constructor/enter are used only after explicit execute and local validation.
    """

    def __init__(self, *, device, timeout_ms, deadline, report, writing=False, backend_factory=None):
        self.bus, self.address = native._location(device)
        self.timeout_ms, self.deadline, self.report, self.writing = timeout_ms, deadline, report, writing
        self.backend = (backend_factory or native.Libusb1Transport)()
        self.lib = self.backend.lib
        self.context, self.handle = C.c_void_p(), C.c_void_p()
        self.devices = C.POINTER(C.c_void_p)()
        self.initialized = self.claimed = False
        self.interface = None
        self._prior_full_packet_zlp = False

    def _check(self, rc, name):
        self.backend._check(rc, name)

    def _remaining(self):
        remaining = min(self.timeout_ms, math.ceil((self.deadline - time.monotonic()) * 1000))
        if remaining <= 0:
            raise native.NativeUsbError("Application session deadline expired; no reconnect or replay")
        return remaining

    def __enter__(self):
        try:
            self.report["stage"] = "initialize"
            self._check(self.lib.libusb_init(C.byref(self.context)), "libusb_init")
            self.initialized = True
            self.report["stage"] = "enumerate"
            count = self.lib.libusb_get_device_list(self.context, C.byref(self.devices))
            if not 0 <= count <= 4096:
                raise native.NativeUsbError("Invalid or oversized USB device list")
            matches = []
            for index in range(count):
                device, descriptor = self.devices[index], native._DeviceDescriptor()
                self._check(self.lib.libusb_get_device_descriptor(device, C.byref(descriptor)), "device descriptor")
                if (descriptor.idVendor, descriptor.idProduct) == (native.VID, native.PID):
                    matches.append((device, descriptor, self.lib.libusb_get_bus_number(device),
                                    self.lib.libusb_get_device_address(device)))
            if len(matches) != 1 or matches[0][2:] != (self.bus, self.address):
                raise native.NativeUsbError("Select the unique 0471:a531 at the explicit Linux BUS:ADDRESS")
            device, descriptor, _, _ = matches[0]
            if (descriptor.bLength, descriptor.bDescriptorType) != (18, 1):
                raise native.NativeUsbError("USB device descriptor mismatch")
            self.report["target"] = {"linux_device": f"{self.bus:03d}:{self.address:03d}",
                                     "vidpid": native.VIDPID, "serial": None,
                                     "bcd_device": descriptor.bcdDevice,
                                     "application_identity": "unknown", "cpu_identity": "unknown"}
            self.report["stage"] = "descriptors"
            self.report["usb_contract"] = self.backend._configuration(device)
            self.interface = self.report["usb_contract"]["data_interface"]
            self.report["stage"] = "open"
            self._check(self.lib.libusb_open(device, C.byref(self.handle)), "open selected USB device")
            config = (C.c_ubyte * 1)()
            rc = self.lib.libusb_control_transfer(self.handle, 0x80, 8, 0, 0, config, 1,
                                                 min(1000, self._remaining()))
            self.report["configuration_read_rc"] = rc
            if rc != 1 or config[0] != 2:
                raise native.NativeUsbError("Active configuration2 is required; no setting is changed")
            active = self.lib.libusb_kernel_driver_active(self.handle, self.interface)
            self.report["kernel_driver_active_rc"] = active
            if active != 0:
                raise native.NativeUsbError("CDC data interface must be free; driver detach is never automatic")
            self.report["stage"] = "claim"
            rc = self.lib.libusb_claim_interface(self.handle, self.interface)
            self.report["claim_rc"] = rc
            self._check(rc, "claim data interface1")
            self.claimed = True
            return self
        except BaseException:
            self.close()
            raise

    def close(self):
        """Release owned host resources only; never send a PLC cleanup command."""
        if self.claimed:
            self.report["release_rc"] = self.lib.libusb_release_interface(self.handle, self.interface)
            self.claimed = False
        if self.handle:
            self.lib.libusb_close(self.handle)
            self.handle = C.c_void_p()
            self.report["close_called"] = True
        if self.devices:
            self.lib.libusb_free_device_list(self.devices, 1)
            self.devices = C.POINTER(C.c_void_p)()
            self.report["device_list_freed"] = True
        if self.initialized:
            self.lib.libusb_exit(self.context)
            self.initialized = False
            self.report["context_exited"] = True

    def __exit__(self, *args):
        self.close()

    def exchange(self, request, *, expected_data_bytes=None):
        """Issue one validated request; no retry even if timeout reports0 bytes.

        Preserves raw request, returned bytes, transfer codes/counts and unknown
        outcome before bulk OUT. Four chunks suffice for this USB profile's
        largest reply512B; receive has one shared deadline, plus session bound.
        One leading successful zero-length transfer is allowed only after this
        same session accepted a512B response in one512B IN. Evidence: native
        stprog.o read20261019T072430Z saw block0=512B then block1's first IN=0B.
        USB short/ZLP termination can remain after a full receive buffer; this
        allowance consumes IN budget and never causes another OUT or reconnect.
        """
        metadata = codec.parse_request(request)
        if metadata["application_write"] and not self.writing:
            raise native.NativeUsbError("A read-only application session refuses PLC writers")
        record = {"action": metadata["action"], "request_hex": request.hex(),
                  "request_outcome": "not_sent", "write_calls": 0, "read_calls": []}
        self.report["requests"].append(record)
        buffer = (C.c_ubyte * len(request)).from_buffer_copy(request)
        transferred = C.c_int()
        self.report["stage"] = "send-read-request" if not metadata["application_write"] else "send-write-request"
        timeout = self._remaining()
        record.update(write_calls=1, request_outcome="unknown")
        self.report["request_outcome"] = "unknown"
        if metadata["application_write"]:
            self.report["plc_change_possible"] = True
        rc = self.lib.libusb_bulk_transfer(self.handle, 0x02, buffer, len(buffer),
                                          C.byref(transferred), timeout)
        record.update(out_rc=rc, out_transferred=transferred.value)
        self._check(rc, "send application request")
        if transferred.value != len(request):
            raise native.NativeUsbError("Partial application request; unknown outcome and no replay")
        self.report["stage"] = "receive"
        response = bytearray()
        zlp_credit = self._prior_full_packet_zlp
        self._prior_full_packet_zlp = False
        deadline = min(self.deadline, time.monotonic() + self.timeout_ms / 1000)
        for _ in range(4):
            remaining = math.ceil((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise native.NativeUsbError("Application response deadline expired")
            chunk = (C.c_ubyte * 512)()
            transferred = C.c_int()
            rc = self.lib.libusb_bulk_transfer(self.handle, 0x81, chunk, len(chunk),
                                              C.byref(transferred), remaining)
            record["read_calls"].append({"rc": rc, "transferred": transferred.value})
            if not 0 <= transferred.value <= 512:
                raise native.NativeUsbError("Invalid received byte count")
            response.extend(bytes(chunk[:transferred.value]))
            record["response_hex"] = response.hex()
            self._check(rc, "receive application response")
            if not transferred.value:
                if zlp_credit and not response:
                    zlp_credit = False
                    record["read_calls"][-1]["accepted_prior_full_packet_zlp"] = True
                    continue
                raise native.NativeUsbError("Empty application transfer has no remaining prior512B ZLP allowance")
            zlp_credit = False  # Never discard an empty transfer inside a frame.
            if len(response) > 512:
                raise native.NativeUsbError("Oversized application response")
            if len(response) >= 2:
                expected = int.from_bytes(response[:2], "little")
                # Native20261019T080940Z and H530 RVA288c0 confirm a9B,
                # header-only op6 ACK. Actual Download open2/write5 callers
                # also use288c0's no-DWORD success branch. Only these explicit
                # writers accept9B; read-open still requires a real file size.
                minimum = 9 if metadata["action"] in codec.EMPTY_ACK_ACTIONS else 10
                if not minimum <= expected <= 512:
                    raise native.NativeUsbError("Invalid bounded application frame length")
                if len(response) >= expected:
                    if len(response) != expected:
                        raise native.NativeUsbError("Trailing bytes after application response")
                    self.report["stage"] = "validate"
                    result = codec.parse_response(bytes(response), request=request,
                                                  expected_data_bytes=expected_data_bytes)
                    record["decoded"] = result
                    if not result["ok"]:
                        record["request_outcome"] = "device_error_response"
                        self.report["request_outcome"] = "device_error_response"
                        raise native.NativeUsbError(f"Device returned application error {result['error_code_u32']}; no fallback/replay")
                    record["request_outcome"] = "correlated_response"
                    self.report["request_outcome"] = "correlated_response"
                    self._prior_full_packet_zlp = len(response) == 512 and transferred.value == 512
                    return result
        raise native.NativeUsbError("Application response incomplete after four chunks")


def _report(action):
    return {"schema": "plcidf.native-application-transaction.v1", "action": action,
            "ok": False, "status": "failed", "stage": "arguments", "device_access": False,
            "transport_kind": "linux-native-libusb1", "request_outcome": "not_sent",
            "plc_change_possible": False, "application_execution_verified": False,
            "complete_application_verified": False, "requests": [], "diagnostics": []}


def _parameters(timeout_ms, session_timeout_s, execute):
    if type(execute) is not bool or type(timeout_ms) is not int or not 1 <= timeout_ms <= 10000:
        raise ValueError("execute must be boolean and timeout_ms integer1..10000")
    if type(session_timeout_s) not in (int, float) or not math.isfinite(session_timeout_s) or not 0 < session_timeout_s <= 120:
        raise ValueError("session_timeout_s must be finite and in (0,120]")


def upload_native(*, remote_name, credential, execute=False, linux_device=None,
                  output_path=None, timeout_ms=2500, session_timeout_s=30.0,
                  max_file_bytes=codec.MAX_APPLICATION_BYTES, lease_directory=None,
                  backend_factory=None):
    """Read one known application file into a new private local file.

    Args: remote_name is a codec whitelist basename; credential is explicit8B;
        execute defaults to pure preview. Linux BUS:ADDRESS/output_path are
        mandatory for execution. Timeout is ms per send/shared receive budget;
        session_timeout_s bounds all exchanges; max_file_bytes limits allocation.
    Returns: JSON report with size/hash and per-request raw evidence. Success
        means one file was read, not a complete/consistent restorable backup.
    Side effects: Execute claims one device interface, opens a read route and
        reads blocks; saves only a new local output after successful cleanup.
        No read retry, remote write, truncation, state change or rollback exists.
    """
    report = _report("upload-read")
    try:
        _parameters(timeout_ms, session_timeout_s, execute)
        request = codec.build_file_open(remote_name=remote_name, credential=credential, sequence=0x1234)
        if type(max_file_bytes) is not int or not 1 <= max_file_bytes <= codec.MAX_APPLICATION_BYTES:
            raise ValueError("max_file_bytes must be an integer1..8MiB")
        report.update(remote_name=remote_name, open_preview_hex=request.hex(),
                      credential_sha256=hashlib.sha256(credential).hexdigest(),
                      max_file_bytes=max_file_bytes, automatic_retries=0,
                      remote_route_cleanup="unknown; no speculative close command",
                      scope="one existing application file read; transport route state may change; no content writes")
        if not execute:
            return {**report, "ok": True, "status": "preview", "stage": "preview"}
        bus, address = native._location(linux_device)
        if output_path is None:
            raise ValueError("Execution requires an explicit new private output_path")
        output = Path(output_path)
        if output.exists() or output.is_symlink() or not output.parent.is_dir():
            raise ValueError("Output parent must exist and output must be a new nonsymlink path")
        sequence, data = secrets.randbits(16), bytearray()
        deadline = time.monotonic() + session_timeout_s
        report["stage"] = "lease"
        with native.ResourceLease(f"device:linux-usb:{bus}:{address}", directory=lease_directory,
                                  purpose="read one existing Easy521 application file") as lease:
            report["lease_token"] = lease.record["token"]
            report["device_access"] = True
            with _Session(device=linux_device, timeout_ms=timeout_ms, deadline=deadline,
                          report=report, backend_factory=backend_factory) as session:
                opened = session.exchange(codec.build_file_open(
                    remote_name=remote_name, credential=credential, sequence=sequence))
                size = opened["ack_u32"]
                report["remote_file_bytes"] = size
                if size > max_file_bytes:
                    raise ValueError("Opened file exceeds the explicit backup allocation limit; route cleanup is not guessed")
                for block, offset in enumerate(range(0, size, codec.READ_CHUNK_BYTES)):
                    sequence = (sequence + 1) & 0xFFFF
                    reply = session.exchange(codec.build_file_read(block=block, sequence=sequence),
                                             expected_data_bytes=min(codec.READ_CHUNK_BYTES, size - offset))
                    data.extend(bytes.fromhex(reply["data_hex"]))
            if report.get("release_rc") != 0 or report.get("close_called") is not True:
                raise native.NativeUsbError("Host cleanup did not complete; backup is not accepted")
        if len(data) != size:
            raise ValueError("Accumulated file bytes differ from open response")
        report["stage"] = "save-local"
        # xb refuses a concurrent output creation. No vendor binary is placed
        # in public docs; caller must choose the private evidence destination.
        with output.open("xb") as stream:
            stream.write(data)
        report.update(ok=True, status="file-read", stage="complete", output_path=str(output),
                      bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                      request_outcome="correlated_file_data", remote_route_cleanup="unknown; no speculative close command")
        return report
    except (Exception, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            report.update(status="interrupted", interrupted_at_stage=report["stage"],
                          stage="interrupted", request_outcome="unknown")
        report["diagnostics"].append({"code": "E_APPLICATION_UPLOAD", "stage": report["stage"], "message": str(error)})
        return report


def set_gcm_native(*, state, execute=False, linux_device=None, timeout_ms=2500,
                   session_timeout_s=10.0, lease_directory=None, backend_factory=None):
    """Explicit single CPU/application-state write; ACK is not independent readback.

    State0/1 is STOP/RUN; raw2/3 follows the vendor download pre/post sequence.
    Does not silently restart, retry or restore the previous state after failure.
    """
    report = _report("set-gcm")
    try:
        _parameters(timeout_ms, session_timeout_s, execute)
        request = codec.build_gcm_set(state=state, sequence=0x1234)
        report.update(request_preview_hex=request.hex(), requested_state=state)
        if not execute:
            return {**report, "ok": True, "status": "preview", "stage": "preview"}
        bus, address = native._location(linux_device)
        report["stage"] = "lease"
        with native.ResourceLease(f"device:linux-usb:{bus}:{address}", directory=lease_directory,
                                  purpose="single explicit Easy521 SetGCM request"):
            report["device_access"] = True
            with _Session(device=linux_device, timeout_ms=timeout_ms,
                          deadline=time.monotonic() + session_timeout_s, report=report,
                          writing=True, backend_factory=backend_factory) as session:
                result = session.exchange(codec.build_gcm_set(state=state, sequence=secrets.randbits(16)))
            if report.get("release_rc") != 0 or report.get("close_called") is not True:
                raise native.NativeUsbError("Cleanup failed after control request; state outcome remains unknown")
        report.update(ok=True, status="control-acknowledged", stage="complete", ack_u32=result["ack_u32"],
                      ack_payload_present=result["ack_payload_present"], ack_format=result["ack_format"],
                      cpu_state_verified=False)
        return report
    except (Exception, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            report.update(status="interrupted", interrupted_at_stage=report["stage"],
                          stage="interrupted", request_outcome="unknown")
        report["diagnostics"].append({"code": "E_GCM_SET", "stage": report["stage"], "message": str(error)})
        return report


def download_native(*, files, credential, source_sha256, build_sha256, execute=False,
                    linux_device=None, timeout_ms=2500, session_timeout_s=120.0,
                    lease_directory=None, backend_factory=None):
    """Explicit destructive transfer of an ordered, externally reviewed file set.

    Args: files is1..19 records with exactly remote_name/data/sha256. Each data
        value is immutable1..8MiB, total<=32MiB; caller supplies expected SHA256.
        source_sha256/build_sha256 bind the reviewed source/build candidates;
        this API cannot attest that those candidates generated the file bytes.
        credential is explicit8B; execute=False previews without any host I/O.
    Returns: A transfer/ACK report, never complete application or execution proof.
    Side effects: Execute sends GCM raw2, opens/truncates each selected remote
        file and writes its blocks in order, then sends raw3 only after every
        prior ACK. Any error stops immediately with no replay/reconnect/rollback
        or implicit RUN. Complete closure/model/version/packaging acceptance must
        be established separately before executing this low-level writer.
    """
    report = _report("download-files")
    try:
        _parameters(timeout_ms, session_timeout_s, execute)
        for name, value in (("source_sha256", source_sha256), ("build_sha256", build_sha256)):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"{name} must be an explicit lowercase SHA256")
        if not isinstance(files, (list, tuple)) or not 1 <= len(files) <= len(codec.APPLICATION_FILES):
            raise ValueError("files must be an explicit ordered bounded record list")
        frozen, manifest, total, names = [], [], 0, set()
        for entry in files:
            if type(entry) is not dict or set(entry) != {"remote_name", "data", "sha256"}:
                raise ValueError("Each file record must contain exactly remote_name/data/sha256")
            name, data, expected = entry["remote_name"], entry["data"], entry["sha256"]
            if type(data) is not bytes or not 1 <= len(data) <= codec.MAX_APPLICATION_BYTES:
                raise ValueError("Each file must contain immutable1..8MiB bytes")
            if name in names or expected != hashlib.sha256(data).hexdigest():
                raise ValueError("Duplicate filename or data differs from expected SHA256")
            codec.build_file_open(remote_name=name, credential=credential, direction="write",
                                  file_bytes=len(data), sequence=0)
            total += len(data)
            if total > 32 * 1024 * 1024:
                raise ValueError("Ordered application files exceed32MiB")
            names.add(name)
            frozen.append((name, data))
            manifest.append({"remote_name": name, "bytes": len(data), "sha256": expected})
        report.update(source_sha256=source_sha256, build_sha256=build_sha256, files=manifest,
                      credential_sha256=hashlib.sha256(credential).hexdigest(),
                      total_file_bytes=total, automatic_retries=0,
                      transaction_sequence="GCM raw2 -> ordered file-open2/write5 -> GCM raw3",
                      application_closure_verified=False, activation_verified=False)
        if not execute:
            return {**report, "ok": True, "status": "preview", "stage": "preview"}
        bus, address = native._location(linux_device)
        sequence = secrets.randbits(16)
        def next_sequence():
            nonlocal sequence
            value, sequence = sequence, (sequence + 1) & 0xFFFF
            return value
        report["stage"] = "lease"
        with native.ResourceLease(f"device:linux-usb:{bus}:{address}", directory=lease_directory,
                                  purpose="explicit destructive Easy521 application file transfer") as lease:
            report["lease_token"] = lease.record["token"]
            report["device_access"] = True
            with _Session(device=linux_device, timeout_ms=timeout_ms,
                          deadline=time.monotonic() + session_timeout_s, report=report,
                          writing=True, backend_factory=backend_factory) as session:
                session.exchange(codec.build_gcm_set(state=2, sequence=next_sequence()))
                for name, data in frozen:
                    report["current_file"] = name
                    session.exchange(codec.build_file_open(remote_name=name, credential=credential,
                                      direction="write", file_bytes=len(data), sequence=next_sequence()))
                    for block, offset in enumerate(range(0, len(data), codec.WRITE_CHUNK_BYTES)):
                        session.exchange(codec.build_file_write(block=block,
                                         data=data[offset:offset + codec.WRITE_CHUNK_BYTES],
                                         sequence=next_sequence()))
                session.exchange(codec.build_gcm_set(state=3, sequence=next_sequence()))
            if report.get("release_rc") != 0 or report.get("close_called") is not True:
                raise native.NativeUsbError("Cleanup failed after transfer; no application outcome claim")
        report.update(ok=True, status="transfer-acknowledged", stage="complete",
                      request_outcome="all_transfer_requests_acknowledged")
        return report
    except (Exception, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            report.update(status="interrupted", interrupted_at_stage=report["stage"],
                          stage="interrupted", request_outcome="unknown")
        report["diagnostics"].append({"code": "E_APPLICATION_DOWNLOAD", "stage": report["stage"], "message": str(error)})
        return report


# Exact candidate layouts from prepare_easy521_application.py/report28 and
# inspect_easy521_live_application.py/report30. These are two reviewed file-set
# profiles, not arbitrary remote path lists or a feature-selection algorithm.
_CONFIG_FILES = ("syscfg.foid", "ethercat.foid", "modbus.foid", "canlink.foid",
                 "modcfg.foid", "ethip.foid", "EIPIns.foid", "ecttrans.foid",
                 "dataLog.foid", "OpcUAServer.foid", "sftpserver.foid", "Ecam.foid")
_LIVE_FILES = ("userprog.foid", "stprog.o", "stuserprog.o", "symbol.sym", "varsinfo.foid", *_CONFIG_FILES)
_FIXED_FILES = ("project.hcpp", *_LIVE_FILES)
_MANIFEST_PROFILES = {
    ("plcidf.easy521-fixed-application-candidate.v1", "easy521-bess-existing-interface-v1"):
        (_FIXED_FILES, "association", "candidate-prepared"),
    ("plcidf.easy521-live-application-inspection.v1", "easy521-observed-live-interface-v1"):
        (_LIVE_FILES, "source_association", "live-derived-candidate-prepared"),
}


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _candidate_read(path, *, limit):
    """Snapshot a bounded regular local file without following final symlinks.

    O_NONBLOCK prevents FIFO substitution from blocking before fstat. Original,
    opened and final path identities must match. Returns immutable bytes and
    identity for a later fresh recheck; no local file is created or modified.
    """
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Candidate paths cannot contain symlinks: " + str(path))
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= limit:
        raise ValueError("Candidate input must be a nonempty bounded regular file: " + str(path))
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or _file_identity(before) != _file_identity(opened):
            raise ValueError("Candidate input identity changed before reading")
        raw = stream.read(limit + 1)
        if (len(raw) != opened.st_size or _file_identity(opened) != _file_identity(os.fstat(stream.fileno()))
                or _file_identity(opened) != _file_identity(path.lstat())):
            raise ValueError("Candidate input changed during reading")
    return raw, _file_identity(opened)


def _json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON key: " + name)
        result[name] = value
    return result


def _manifest_snapshot(path, *, linux_device, report):
    """Validate one report28/30 candidate and recheck immutable payload bytes.

    The source/object association is a candidate claim, not authenticated build
    provenance. Source archive and inherited symbol policies remain explicit;
    runtime, compatibility and device-acceptance claims are copied unchanged.
    """
    path = Path(path).absolute()
    raw, identity = _candidate_read(path, limit=1024 * 1024)
    report.update(candidate_manifest_path=str(path), candidate_manifest_sha256=hashlib.sha256(raw).hexdigest(),
                  build_sha256_kind="candidate-manifest")
    snapshots = [(path, raw, identity, 1024 * 1024)]
    manifest = json.loads(raw, object_pairs_hook=_json_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite JSON value: " + value)))
    if not isinstance(manifest, dict):
        raise ValueError("Candidate manifest must be an object")
    key = (manifest.get("schema"), manifest.get("profile"))
    if key not in _MANIFEST_PROFILES:
        raise ValueError("Unsupported candidate manifest schema/profile")
    names, association_key, status = _MANIFEST_PROFILES[key]
    if manifest.get("ok") is not True or manifest.get("status") != status:
        raise ValueError("Candidate must identify a successfully prepared file set")
    listed = manifest.get("files")
    if (not isinstance(listed, list) or len(listed) != len(names)
            or type(manifest.get("file_count")) is not int or manifest["file_count"] != len(names)):
        raise ValueError("Candidate file count differs from the fixed ordered profile")
    association = manifest.get(association_key)
    if not isinstance(association, dict) or any(not isinstance(association.get(name), str)
            or not re.fullmatch(r"[0-9a-f]{64}", association[name])
            for name in ("source.st", "stprog.o", "stuserprog.o")):
        raise ValueError("Candidate lacks source/two-native-object SHA256 association")
    if names == _LIVE_FILES:
        target = manifest.get("readback_target")
        if not isinstance(target, dict) or target.get("vidpid") != native.VIDPID:
            raise ValueError("Live candidate needs the observed Easy521 readback target")
        prior_location = native._location(target.get("linux_device"))
        if linux_device is not None and native._location(linux_device) != prior_location:
            raise ValueError("Selected Linux USB location differs from live candidate readback target")
        if manifest.get("project_archive_included") is not False:
            raise ValueError("Live17 profile must explicitly omit the unresolved source archive")
    elif manifest.get("source_recovery_archive") != "vendor-Empty-marker":
        raise ValueError("Fixed18 profile must identify the vendor Empty source-archive marker")
    payloads, total, records = [], len(raw), []
    for index, (name, entry) in enumerate(zip(names, listed)):
        if (not isinstance(entry, dict) or type(entry.get("index")) is not int or entry["index"] != index
                or entry.get("remote_name") != name or not isinstance(entry.get("path"), str)
                or type(entry.get("size")) is not int or not 1 <= entry["size"] <= codec.MAX_APPLICATION_BYTES
                or not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])):
            raise ValueError("Candidate file order/index/path/size/hash differs from the fixed profile")
        total += entry["size"]
        if total > 32 * 1024 * 1024:
            raise ValueError("Candidate manifest/payload snapshot exceeds32MiB")
        file_path = Path(entry["path"])
        if not file_path.is_absolute():
            file_path = path.parent / file_path
        data, identity = _candidate_read(file_path, limit=codec.MAX_APPLICATION_BYTES)
        if len(data) != entry["size"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("Candidate payload differs from size/SHA256: " + name)
        if name in ("stprog.o", "stuserprog.o") and association[name] != entry["sha256"]:
            raise ValueError("Native object differs from candidate source association: " + name)
        if name == "project.hcpp" and data != b"Empty":
            raise ValueError("Fixed18 source archive must contain exactly the vendor Empty marker")
        snapshots.append((file_path, data, identity, codec.MAX_APPLICATION_BYTES))
        payloads.append({"remote_name": name, "data": data, "sha256": entry["sha256"]})
        records.append(dict(entry))
    report["stage"] = "snapshot-recheck"
    for file_path, data, identity, limit in snapshots:
        after, after_identity = _candidate_read(file_path, limit=limit)
        if after != data or after_identity != identity:
            raise ValueError("Candidate manifest/input drift after snapshot: " + str(file_path))
    report.update(candidate_manifest_path=str(path), candidate_manifest_sha256=hashlib.sha256(raw).hexdigest(),
                  build_sha256_kind="candidate-manifest", candidate_files=records,
                  candidate_metadata={k: v for k, v in manifest.items() if k not in ("files", "inputs")},
                  candidate_input_records=manifest.get("inputs"), local_snapshot_rechecked=True,
                  local_snapshot_bytes=total,
                  candidate_provenance_inputs_reopened=False,
                  source_association_authenticated=False, permanent_target_identity="unknown",
                  source_archive_policy={"included": names == _FIXED_FILES,
                      "policy": "vendor-Empty-marker" if names == _FIXED_FILES else "omitted-unresolved-transfer-policy",
                      "plc_source_recovery_verified": False},
                  symbol_metadata_policy={"policy": "inherited-fixed-interface" if names == _FIXED_FILES else "inherited-device-readback",
                      "regenerated_for_new_source": False, "device_enforcement_verified": False})
    return payloads, association["source.st"], hashlib.sha256(raw).hexdigest()


def download_manifest_native(*, manifest_path, credential, execute=False, linux_device=None,
                             timeout_ms=2500, session_timeout_s=120.0,
                             lease_directory=None, backend_factory=None):
    """Snapshot a fixed18/live17 candidate, then call one explicit transaction.

    Preview reads only bounded local manifest/payload files. Execution passes
    immutable bytes once to download_native; no automatic RUN, readback, retry,
    reconnect or rollback occurs. build_sha256 is the candidate manifest hash,
    not a compiler-report hash. Candidate runtime/metadata limitations remain in
    candidate_metadata; transfer ACK never promotes them to device acceptance.
    """
    report = _report("download-manifest")
    try:
        _parameters(timeout_ms, session_timeout_s, execute)
        codec.build_file_open(remote_name="stprog.o", credential=credential, direction="write", file_bytes=1, sequence=0)
        if linux_device is not None or execute:
            native._location(linux_device)
        report["stage"] = "snapshot"
        files, source_sha256, build_sha256 = _manifest_snapshot(manifest_path, linux_device=linux_device, report=report)
        report.update(stage="download", transaction_invoked=True,
                      source_sha256=source_sha256, build_sha256=build_sha256)
        if execute:
            # If an unexpected interruption escapes the low-level API, do not
            # claim that no device access or write occurred. Normally that API
            # returns its own detailed interrupted/unknown report and cleanup.
            report.update(device_access=None, request_outcome="unknown", plc_change_possible=True)
        result = download_native(files=files, credential=credential, source_sha256=source_sha256,
                                 build_sha256=build_sha256, execute=execute, linux_device=linux_device,
                                 timeout_ms=timeout_ms, session_timeout_s=session_timeout_s,
                                 lease_directory=lease_directory, backend_factory=backend_factory)
        report.update(result)
        report["action"] = "download-manifest"
    except (Exception, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            report.update(status="interrupted", interrupted_at_stage=report["stage"], stage="interrupted")
        report["diagnostics"].append({"code": "E_APPLICATION_MANIFEST", "stage": report["stage"], "message": str(error)})
    return report


def _wait_target(target, requested, *, reference=None):
    """Correlate fresh descriptor/location claims, never permanent identity."""
    if (not isinstance(target, dict) or target.get("vidpid") != native.VIDPID
            or native._location(target.get("linux_device")) != requested
            or type(target.get("bcd_device")) is not int):
        raise ValueError("State observation lacks the selected fresh USB location/product descriptor")
    if reference is not None and target["bcd_device"] != reference["bcd_device"]:
        raise ValueError("USB device release descriptor changed between control and state read")


def set_gcm_and_wait_native(*, state, execute=False, linux_device=None, wait_timeout_s=None,
                            timeout_ms=2500, session_timeout_s=30.0,
                            lease_directory=None, backend_factory=None):
    """Send RUN/STOP once, then explicitly wait for bounded independent reads.

    state is0 STOP or1 RUN; other GCM writers have no polling mode. Default
    wait_timeout_s is1/5s, explicitly0.1..10s, starting after the control ACK.
    Every poll is a new fixed read, never a replay of the writer. Transfer
    timeouts split the remaining wait budget over GET_CONFIG/OUT/shared IN;
    libusb enumeration/cleanup have no timeout parameters. Deadline overruns
    are recorded and never accepted as an on-time state confirmation.
    Returns the full control report plus timestamped independent read reports;
    a fresh matching location/VID/PID/release is correlation, not authentication.
    Only an on-time target read marks cpu_state_verified; application execution
    and causal attribution to this writer remain unverified. Preview has no
    device/lease/nonce I/O; execute never prepares USB or clears faults.
    """
    report = _report("set-gcm-wait")
    report.update(state_observations=[], cpu_state_verified=False,
                  requested_linux_device=linux_device,
                  permanent_target_identity="unknown", causal_transition_verified=False,
                  exclusive_wait_session=False, automatic_write_retries=0)
    writer_invoked = False
    try:
        _parameters(timeout_ms, session_timeout_s, execute)
        if type(state) is not int or state not in (0, 1):
            raise ValueError("Explicit wait supports only STOP0 or RUN1")
        wait_timeout_s = (5.0 if state == 1 else 1.0) if wait_timeout_s is None else wait_timeout_s
        if (type(wait_timeout_s) not in (int, float) or not math.isfinite(wait_timeout_s)
                or not 0.1 <= wait_timeout_s <= 10.0):
            raise ValueError("wait_timeout_s must be finite0.1..10 seconds")
        interval_s = 0.01 if state == 1 else 0.1
        initial_delay_s = interval_s if state == 0 and wait_timeout_s > interval_s else 0.0
        maximum_reads = math.ceil(wait_timeout_s / interval_s)
        report.update(requested_state=state, wait_timeout_s=wait_timeout_s,
                      poll_interval_s=interval_s, initial_poll_delay_s=initial_delay_s,
                      maximum_state_reads=maximum_reads,
                      deadline_basis="one wait window starting after control ACK",
                      location_correlation="fresh BUS:ADDRESS/VID:PID/bcdDevice per operation; no permanent identity",
                      control_timeout_ms=timeout_ms, control_session_timeout_s=session_timeout_s)
        if not execute:
            control = set_gcm_native(state=state, execute=False, timeout_ms=timeout_ms,
                                     session_timeout_s=session_timeout_s)
            read_preview = native.read_fixed(kind="runtime", profile="gcm-state", execute=False,
                                             timeout_ms=timeout_ms)
            return {**report, "ok": True, "status": "preview", "stage": "preview",
                    "control_result": control, "state_read_preview": read_preview}
        requested = native._location(linux_device)
        report.update(stage="control", device_access=None, request_outcome="unknown", plc_change_possible=True)
        control_start, control_wall = time.monotonic(), time.time()
        writer_invoked = True
        control = set_gcm_native(state=state, execute=True, linux_device=linux_device,
                                 timeout_ms=timeout_ms, session_timeout_s=session_timeout_s,
                                 lease_directory=lease_directory, backend_factory=backend_factory)
        report.update(control_result=control, control_started_monotonic_s=control_start,
                      control_started_unix_s=control_wall, control_finished_monotonic_s=time.monotonic(),
                      device_access=control.get("device_access"), plc_change_possible=control.get("plc_change_possible"),
                      request_outcome=control.get("request_outcome"), control_request_outcome=control.get("request_outcome"))
        if control.get("ok") is not True:
            report.update(status=control.get("status", "failed"), stage="control-failed")
            return report
        if control.get("status") != "control-acknowledged" or control.get("request_outcome") != "correlated_response":
            raise ValueError("State polling requires the single control request's correlated ACK")
        _wait_target(control.get("target"), requested)
        started = time.monotonic()
        deadline = started + wait_timeout_s
        report.update(stage="state-wait", wait_started_monotonic_s=started,
                      wait_started_unix_s=time.time(), wait_deadline_monotonic_s=deadline,
                      target=control["target"])
        for index in range(maximum_reads):
            # Default vendor STOP policy delays100ms; an explicitly chosen
            # minimum100ms window starts immediately so that sleep cannot use
            # its entire budget before even one independent observation.
            delay_s = interval_s if index else initial_delay_s
            if delay_s:
                pause = min(delay_s, max(0.0, deadline - time.monotonic()))
                if pause:
                    time.sleep(pause)
            remaining_ms = math.floor((deadline - time.monotonic()) * 1000)
            # The unchanged fixed reader can independently block in standard
            # GET_CONFIGURATION, OUT, and shared-deadline IN. Reserve all three
            # within the remaining window instead of restarting a full timeout.
            read_timeout_ms = min(timeout_ms, remaining_ms // 3)
            if read_timeout_ms < 1:
                break
            observation = {"index": index + 1, "started_monotonic_s": time.monotonic(),
                           "started_unix_s": time.time(), "timeout_ms": read_timeout_ms,
                           "requested_linux_device": linux_device}
            report["state_observations"].append(observation)
            report["stage"] = "state-read"
            result = native.read_fixed(kind="runtime", profile="gcm-state", execute=True,
                                       device=linux_device, timeout_ms=read_timeout_ms,
                                       lease_directory=lease_directory, backend_factory=backend_factory)
            finished = time.monotonic()
            observation.update(result=result, finished_monotonic_s=finished,
                               finished_unix_s=time.time(), within_deadline=finished <= deadline)
            report.update(wait_elapsed_s=finished-started, last_read_timeout_ms=read_timeout_ms)
            if result.get("ok") is not True:
                report.update(status="interrupted" if result.get("status") == "interrupted" else "state-read-failed",
                              stage="state-read-failed", request_outcome="unknown")
                return report
            _wait_target(result.get("target"), requested, reference=control["target"])
            if (result.get("transport_kind") != "linux-native-libusb1" or result.get("read_profile") != "gcm-state"
                    or result.get("request_outcome") != "correlated_raw_data"
                    or type(result.get("raw_value")) is not int or result["raw_value"] not in (0, 1, 2, 3)):
                raise ValueError("State read lacks a correlated valid fixed GCM result")
            report["last_observed_state"] = result["raw_value"]
            if finished > deadline:
                report["target_observed_after_deadline"] = result["raw_value"] == state
                break
            if result["raw_value"] == state:
                report.update(ok=True, status="state-confirmed", stage="complete", cpu_state_verified=True,
                              request_outcome="control_acknowledged_and_target_state_observed",
                              state_verification={"raw_value": state, "observation_index": index + 1,
                                  "observed_monotonic_s": finished, "target": result["target"],
                                  "request_hex": result.get("request_hex"),
                                  "scope": "this fresh location/descriptor observation only; no application execution proof"})
                return report
        finished = time.monotonic()
        report.update(status="state-wait-timeout", stage="state-wait-timeout", request_outcome="unknown",
                      wait_elapsed_s=finished-started, wait_deadline_overrun_s=max(0.0, finished-deadline))
    except (Exception, KeyboardInterrupt) as error:
        if report["state_observations"] and "finished_monotonic_s" not in report["state_observations"][-1]:
            report["state_observations"][-1].update(finished_monotonic_s=time.monotonic(),
                finished_unix_s=time.time(), read_outcome="unknown", error=str(error))
        if isinstance(error, KeyboardInterrupt):
            report.update(status="interrupted", interrupted_at_stage=report["stage"], stage="interrupted")
        if writer_invoked:
            report["request_outcome"] = "unknown"
        report["diagnostics"].append({"code": "E_GCM_STATE_WAIT", "stage": report["stage"], "message": str(error)})
    return report


def main(argv=None):
    """Read-compatible CLI plus explicit preview-first run/stop/download actions."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in ("read", "run", "stop", "download", "-h", "--help"):
        argv.insert(0, "read")  # Preserve the original --remote-name read CLI.
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("read", "run", "stop", "download"):
        command = actions.add_parser(action)
        command.add_argument("--execute", action="store_true")
        command.add_argument("--linux-device")
        command.add_argument("--timeout-ms", type=int, default=2500)
        command.add_argument("--session-timeout-s", type=float, default=120.0 if action == "download" else 30.0)
        if action in ("read", "download"):
            command.add_argument("--credential-hex", required=True, help="exact8B credential; explicit0000000000000000 only for known empty credential")
        if action == "read":
            command.add_argument("--remote-name", choices=codec.APPLICATION_FILES, required=True)
            command.add_argument("--output-path")
        elif action == "download":
            command.add_argument("--manifest", required=True)
        else:
            command.add_argument("--wait-state", action="store_true")
            command.add_argument("--wait-timeout-s", type=float)
    args = parser.parse_args(argv)
    try:
        common = {"execute": args.execute, "linux_device": args.linux_device,
                  "timeout_ms": args.timeout_ms, "session_timeout_s": args.session_timeout_s}
        if args.action in ("run", "stop"):
            # Report26's H530 SetGCM mapping: raw0 STOP, raw1 RUN. ACK is not a
            # CPU-state readback; no automatic monitoring transaction follows.
            if args.wait_timeout_s is not None and not args.wait_state:
                raise ValueError("--wait-timeout-s requires explicit --wait-state")
            if args.wait_state:
                report = set_gcm_and_wait_native(state=1 if args.action == "run" else 0,
                                                wait_timeout_s=args.wait_timeout_s, **common)
            else:
                report = set_gcm_native(state=1 if args.action == "run" else 0, **common)
        else:
            credential = bytes.fromhex(args.credential_hex)
            if args.action == "download":
                report = download_manifest_native(manifest_path=args.manifest, credential=credential, **common)
            else:
                report = upload_native(remote_name=args.remote_name, credential=credential,
                                       output_path=args.output_path, **common)
        code = 130 if report["status"] == "interrupted" else 0 if report["ok"] else 1
    except ValueError as error:
        report = {**_report(args.action), "diagnostics": [{"code": "E_ARGUMENTS", "message": str(error)}]}
        code = 1
    except KeyboardInterrupt:
        report = {**_report(args.action), "status": "interrupted", "stage": "interrupted", "request_outcome": "unknown", "device_access": None,
                  "plc_change_possible": args.execute and args.action in ("run", "stop", "download")}
        code = 130
    print(json.dumps(report, allow_nan=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
