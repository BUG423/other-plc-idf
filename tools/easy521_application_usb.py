"""Bounded native Easy521 application-file transactions; preview is zero-I/O.

Uses the verified CDC data-interface1 profile from easy521_linux_usb.py, without
weakening its fixed read entry point. Application requests use the independently
recovered H530 Upload/Download/GCM contracts documented in report26. Executing
an upload-read allocates a device file route, but never sends create/truncate,
file data writes, CPU control or firmware commands. Explicit download/control
functions are separate writers and never retry, reconnect or roll back.

This module does not compile ST, generate a complete application manifest,
verify password policy, infer remote directories or guarantee recovery after
power loss. Transfer ACK is not application activation/execution verification.
Preview never resolves output paths, loads libusb, enumerates, locks or writes.
"""

from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import secrets
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
                if not 10 <= expected <= 512:
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


def main(argv=None):
    """Read-file CLI only; CPU control and application writing are separate APIs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--remote-name", choices=codec.APPLICATION_FILES, required=True)
    parser.add_argument("--credential-hex", required=True, help="exact8B credential; explicit0000000000000000 only for known empty credential")
    parser.add_argument("--linux-device")
    parser.add_argument("--output-path")
    parser.add_argument("--timeout-ms", type=int, default=2500)
    parser.add_argument("--session-timeout-s", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        credential = bytes.fromhex(args.credential_hex)
        report = upload_native(remote_name=args.remote_name, credential=credential,
                               execute=args.execute, linux_device=args.linux_device,
                               output_path=args.output_path, timeout_ms=args.timeout_ms,
                               session_timeout_s=args.session_timeout_s)
        code = 130 if report["status"] == "interrupted" else 0 if report["ok"] else 1
    except ValueError as error:
        report = {**_report("upload-read"), "diagnostics": [{"code": "E_ARGUMENTS", "message": str(error)}]}
        code = 1
    except KeyboardInterrupt:
        report = {**_report("upload-read"), "stage": "interrupted", "request_outcome": "unknown"}
        code = 130
    print(json.dumps(report, allow_nan=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
