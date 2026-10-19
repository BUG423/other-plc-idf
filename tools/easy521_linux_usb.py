"""Single fixed Easy521 read using Linux libusb-1.0, with no Windows bridge.

Input is a fixed runtime/memory profile and an explicit Linux BUS:ADDRESS.
Output preserves raw USB evidence plus the existing strict codec result. The
protocol contract comes from research reports 15, 18 and 19. The Linux USB
profile is independently pinned to the 2026-10-19 native descriptor observation:
evidence/easy521-native-device-20261019T064323Z/native-descriptors.json
(raw descriptors SHA256 be0da4a2a37147d2e0068bb492bf0eb89ea067e2808ecdcf6a756b0ddbd51351).
It has CDC control interface 0 and bulk data interface 1, linked by IAD/union;
the Windows helper's claim0 must not be treated as a Linux bulk interface.
Linux transport itself still requires hardware acceptance; memory response and
scalar meanings remain candidates. This is not a downloader or a firmware API.

Default preview never loads libusb, enumerates USB, acquires a lease or starts
a process. Execution claims/releases an already configured interface and sends
one host-to-device *read request*. It does not write PLC variables/CPU mode,
change configuration/alternate setting, detach drivers, clear halt, reset or
retry/replay. USB transport resource operations are not literally side-effect
free. ctypes declarations follow the stable libusb-1.0 public C ABI:
https://libusb.sourceforge.io/api-1.0/group__libusb__syncio.html
https://libusb.sourceforge.io/api-1.0/group__libusb__dev.html
"""

from __future__ import annotations

import ctypes as C
import importlib.util
import math
from pathlib import Path
import re
import secrets
import sys
import time

_ROOT = Path(__file__).resolve().parents[1]
if (_ROOT / "src" / "plc_idf").is_dir():
    sys.path.insert(0, str(_ROOT / "src"))
from plc_idf.lease import ResourceLease


def _codec(filename):
    spec = importlib.util.spec_from_file_location(
        "_plcidf_native_" + filename.removesuffix(".py"), Path(__file__).with_name(filename))
    if spec is None or spec.loader is None:
        raise RuntimeError("The adjacent fixed read codec is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RUNTIME = _codec("easy521_protocol.py")
_MEMORY = _codec("easy521_memory_protocol.py")
_RUNTIME_PROFILES = {"literal": _RUNTIME.VENDOR_LITERAL_FF010000_4,
                     "gcm-state": _RUNTIME.VENDOR_GCM_STATE}
VID, PID = 0x0471, 0xA531
VIDPID = "0471:a531"
SONAME = "libusb-1.0.so.0"
NATIVE_DESCRIPTOR_EVIDENCE = "evidence/easy521-native-device-20261019T064323Z/native-descriptors.json"

# These are host-ABI C structures, not packed USB wire frames. Pointer alignment
# and sizeof(int) must follow ctypes' native layout, as in libusb.h. Do not add
# _pack_=1: the descriptor tree contains aligned pointers on 32/64-bit Linux.
class _DeviceDescriptor(C.Structure):
    _fields_ = [("bLength", C.c_uint8), ("bDescriptorType", C.c_uint8),
                ("bcdUSB", C.c_uint16), ("bDeviceClass", C.c_uint8),
                ("bDeviceSubClass", C.c_uint8), ("bDeviceProtocol", C.c_uint8),
                ("bMaxPacketSize0", C.c_uint8), ("idVendor", C.c_uint16),
                ("idProduct", C.c_uint16), ("bcdDevice", C.c_uint16),
                ("iManufacturer", C.c_uint8), ("iProduct", C.c_uint8),
                ("iSerialNumber", C.c_uint8), ("bNumConfigurations", C.c_uint8)]


class _Endpoint(C.Structure):
    _fields_ = [("bLength", C.c_uint8), ("bDescriptorType", C.c_uint8),
                ("bEndpointAddress", C.c_uint8), ("bmAttributes", C.c_uint8),
                ("wMaxPacketSize", C.c_uint16), ("bInterval", C.c_uint8),
                ("bRefresh", C.c_uint8), ("bSynchAddress", C.c_uint8),
                ("extra", C.POINTER(C.c_ubyte)), ("extra_length", C.c_int)]


class _InterfaceDescriptor(C.Structure):
    _fields_ = [(name, C.c_uint8) for name in (
        "bLength", "bDescriptorType", "bInterfaceNumber", "bAlternateSetting",
        "bNumEndpoints", "bInterfaceClass", "bInterfaceSubClass",
        "bInterfaceProtocol", "iInterface")] + [
        ("endpoint", C.POINTER(_Endpoint)), ("extra", C.POINTER(C.c_ubyte)),
        ("extra_length", C.c_int)]


class _Interface(C.Structure):
    _fields_ = [("altsetting", C.POINTER(_InterfaceDescriptor)),
                ("num_altsetting", C.c_int)]


class _Configuration(C.Structure):
    _fields_ = [("bLength", C.c_uint8), ("bDescriptorType", C.c_uint8),
                ("wTotalLength", C.c_uint16), ("bNumInterfaces", C.c_uint8),
                ("bConfigurationValue", C.c_uint8), ("iConfiguration", C.c_uint8),
                ("bmAttributes", C.c_uint8), ("MaxPower", C.c_uint8),
                ("interface", C.POINTER(_Interface)),
                ("extra", C.POINTER(C.c_ubyte)), ("extra_length", C.c_int)]


class NativeUsbError(RuntimeError):
    """Transport failure; raw call results remain in the observation dictionary."""


def _location(device):
    if not isinstance(device, str) or not re.fullmatch(r"[0-9]{1,3}:[0-9]{1,3}", device):
        raise ValueError("Select an explicit Linux BUS:ADDRESS, e.g. 001:004; this is not a Windows BUSID")
    bus, address = map(int, device.split(":"))
    if not (1 <= bus <= 255 and 1 <= address <= 127):
        raise ValueError("Linux USB bus must be 1..255 and address 1..127")
    return bus, address


def _contract(kind, profile, sequence):
    if kind == "runtime" and isinstance(profile, str) and profile in _RUNTIME_PROFILES:
        selected = _RUNTIME_PROFILES[profile]
        return _RUNTIME.build_read_request(profile=selected, sequence=sequence), 14
    if kind == "memory" and isinstance(profile, str) and profile in _MEMORY.PROFILES:
        return _MEMORY.build_read_request(profile=profile, sequence=sequence), 26
    raise ValueError("Choose runtime literal/gcm-state or a fixed memory profile; arbitrary reads are unsupported")


class Libusb1Transport:
    """Own one libusb context and one bounded, non-replayed fixed read exchange.

    ``library`` is a ctypes-compatible fake only for offline tests. Construction
    loads the stable Linux soname, but does not initialize/enumerate USB. Call
    ``read`` only under the location lease held by ``read_fixed``. No writable
    PLC frame or arbitrary request can be passed to this public interface.
    """

    def __init__(self, *, library=None):
        if not sys.platform.startswith("linux"):
            raise NativeUsbError("Native transport requires Linux")
        self.lib = library if library is not None else C.CDLL(SONAME)
        ptr = C.c_void_p
        byteptr = C.POINTER(C.c_ubyte)
        declarations = {
            "libusb_init": ([C.POINTER(ptr)], C.c_int),
            "libusb_exit": ([ptr], None),
            "libusb_get_device_list": ([ptr, C.POINTER(C.POINTER(ptr))], C.c_ssize_t),
            "libusb_free_device_list": ([C.POINTER(ptr), C.c_int], None),
            "libusb_get_device_descriptor": ([ptr, C.POINTER(_DeviceDescriptor)], C.c_int),
            "libusb_get_bus_number": ([ptr], C.c_uint8),
            "libusb_get_device_address": ([ptr], C.c_uint8),
            "libusb_get_active_config_descriptor": ([ptr, C.POINTER(C.POINTER(_Configuration))], C.c_int),
            "libusb_free_config_descriptor": ([C.POINTER(_Configuration)], None),
            "libusb_open": ([ptr, C.POINTER(ptr)], C.c_int),
            "libusb_close": ([ptr], None),
            "libusb_kernel_driver_active": ([ptr, C.c_int], C.c_int),
            "libusb_claim_interface": ([ptr, C.c_int], C.c_int),
            "libusb_release_interface": ([ptr, C.c_int], C.c_int),
            "libusb_control_transfer": ([ptr, C.c_uint8, C.c_uint8, C.c_uint16,
                                          C.c_uint16, byteptr, C.c_uint16, C.c_uint], C.c_int),
            "libusb_bulk_transfer": ([ptr, C.c_ubyte, byteptr, C.c_int,
                                       C.POINTER(C.c_int), C.c_uint], C.c_int),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.lib, name)
            function.argtypes, function.restype = args, result

    @staticmethod
    def _check(rc, operation):
        if rc != 0:
            raise NativeUsbError(f"{operation} failed with libusb code {rc}; no retry")

    @staticmethod
    def _extra_descriptors(pointer, length, owner):
        """Parse bounded USB descriptor TLVs retained by libusb, without I/O.

        IAD may be retained in configuration/interface extras by the libusb
        parser. CDC functional descriptors must belong to the control interface.
        Their byte values come from NATIVE_DESCRIPTOR_EVIDENCE, not an inferred
        permission to send CDC class requests or change line coding.
        """
        if not 0 <= length <= 4096 or (length and not pointer):
            raise NativeUsbError(f"Invalid {owner} extra descriptor buffer")
        raw = C.string_at(pointer, length) if length else b""
        descriptors, offset = [], 0
        while offset < len(raw):
            size = raw[offset]
            if size < 2 or offset + size > len(raw):
                raise NativeUsbError(f"Malformed {owner} extra descriptor TLV")
            descriptors.append(raw[offset:offset + size])
            offset += size
        return descriptors

    def _configuration(self, device):
        descriptor = C.POINTER(_Configuration)()
        self._check(self.lib.libusb_get_active_config_descriptor(device, C.byref(descriptor)),
                    "get active configuration descriptor")
        try:
            config = descriptor.contents
            if (config.bConfigurationValue != 2 or config.bNumInterfaces != 2
                    or config.bLength != 9 or config.bDescriptorType != 2):
                raise NativeUsbError("Observed configuration 2 with two CDC interfaces is required; no setting changed")
            extras = {"configuration": self._extra_descriptors(
                config.extra, config.extra_length, "configuration")}
            selected = {}
            for index in range(config.bNumInterfaces):
                interface = config.interface[index]
                if interface.num_altsetting != 1:
                    raise NativeUsbError("Each observed CDC interface has only alternate setting 0")
                alt = interface.altsetting[0]
                number = alt.bInterfaceNumber
                if (number not in (0, 1) or number in selected or alt.bAlternateSetting != 0
                        or alt.bLength != 9 or alt.bDescriptorType != 4):
                    raise NativeUsbError("Expected unique control0/data1 interfaces with alternate setting 0")
                selected[number] = alt
                extras[f"interface{number}"] = self._extra_descriptors(
                    alt.extra, alt.extra_length, f"interface{number}")
            if set(selected) != {0, 1}:
                raise NativeUsbError("Expected both CDC control interface 0 and data interface 1")
            control, data = selected[0], selected[1]
            if ((control.bInterfaceClass, control.bInterfaceSubClass, control.bInterfaceProtocol) != (2, 2, 1)
                    or (data.bInterfaceClass, data.bInterfaceSubClass, data.bInterfaceProtocol) != (10, 0, 0)):
                raise NativeUsbError("CDC control/data interface classes differ from observed Easy521 profile")
            associations = [item for items in extras.values() for item in items if item[1] == 11]
            if associations != [bytes.fromhex("080b000202020107")]:
                raise NativeUsbError("Expected the observed single CDC IAD linking interfaces 0 and 1")
            unions = [(owner, item) for owner, items in extras.items() for item in items
                      if item[1] == 0x24 and len(item) >= 3 and item[2] == 6]
            if unions != [("interface0", bytes.fromhex("0524060001"))]:
                raise NativeUsbError("Expected one control-interface CDC union with master0/slave1")
            endpoints = {}
            for number, alt in selected.items():
                expected_count = 1 if number == 0 else 2
                if alt.bNumEndpoints != expected_count:
                    raise NativeUsbError("Unexpected CDC endpoint count or duplicated endpoints")
                entries = []
                for ep in alt.endpoint[:alt.bNumEndpoints]:
                    if ep.bLength != 7 or ep.bDescriptorType != 5:
                        raise NativeUsbError("Unexpected endpoint descriptor shape")
                    entries.append({"address": ep.bEndpointAddress, "attributes": ep.bmAttributes,
                                    "max_packet_bytes": ep.wMaxPacketSize})
                endpoints[number] = entries
            if endpoints[0] != [{"address": 0x84, "attributes": 3, "max_packet_bytes": 10}]:
                raise NativeUsbError("Control interface 0 must expose only observed interrupt IN 0x84")
            expected_bulk = [{"address": address, "attributes": 2, "max_packet_bytes": 512}
                             for address in (0x02, 0x81)]
            if sorted(endpoints[1], key=lambda ep: ep["address"]) != expected_bulk:
                raise NativeUsbError("Data interface 1 must expose unique bulk OUT 0x02 / IN 0x81, 512 bytes")
            return {"configuration": 2, "control_interface": 0, "data_interface": 1, "interface": 1,
                    "alternate_setting": 0, "endpoints": endpoints[1],
                    "control_endpoints": endpoints[0],
                    "iad_hex": associations[0].hex(), "cdc_union_hex": unions[0][1].hex(),
                    "contract_basis": NATIVE_DESCRIPTOR_EVIDENCE,
                    "windows_claim0_is_not_linux_bulk_interface": True}
        finally:
            self.lib.libusb_free_config_descriptor(descriptor)

    def read(self, *, device, kind, profile, sequence, timeout_ms, record):
        """Send one fixed read and collect one frame, preserving partial results.

        Args: device is Linux BUS:ADDRESS; kind/profile select the fixed codecs;
            sequence is u16 correlation; timeout_ms is a positive finite bound
            for sending and a separate shared receive budget, in milliseconds;
            record is mutated with stage, raw bytes, return codes and cleanup.
        Returns: Complete immutable response bytes, still requiring codec checks.
        Raises: NativeUsbError/ValueError on selection, USB or framing failure.
        Side effects: USB enumeration/open, standard GET_CONFIGURATION, exclusive
            interface claim/release and one read-request bulk OUT. Never retries.
            Enumeration/open/claim are libusb OS resource calls without timeout
            parameters; only USB transfers use the explicit timeout values.
        """
        bus, address = _location(device)
        request, maximum = _contract(kind, profile, sequence)
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 10000:
            raise ValueError("timeout_ms must be an integer in 1..10000")
        context, handle = C.c_void_p(), C.c_void_p()
        devices = C.POINTER(C.c_void_p)()
        initialized = claimed = False
        response = bytearray()
        record.update(stage="initialize", request_hex=request.hex(), write_calls=0,
                      request_outcome="not_sent", read_calls=[], library_soname=SONAME,
                      transfer_timeout_ms=timeout_ms, automatic_retries=0)
        try:
            self._check(self.lib.libusb_init(C.byref(context)), "libusb_init")
            initialized = True
            record["stage"] = "enumerate"
            count = self.lib.libusb_get_device_list(context, C.byref(devices))
            if count < 0 or count > 4096:
                raise NativeUsbError(f"USB enumeration failed or exceeded bound: {count}")
            matches = []
            for index in range(count):
                candidate = devices[index]
                descriptor = _DeviceDescriptor()
                self._check(self.lib.libusb_get_device_descriptor(candidate, C.byref(descriptor)),
                            "get device descriptor")
                if descriptor.idVendor == VID and descriptor.idProduct == PID:
                    matches.append((candidate, descriptor,
                                    self.lib.libusb_get_bus_number(candidate),
                                    self.lib.libusb_get_device_address(candidate)))
            if len(matches) != 1 or matches[0][2:] != (bus, address):
                raise NativeUsbError("Expected exactly one 0471:a531 at the explicitly selected Linux BUS:ADDRESS")
            selected, descriptor, _, _ = matches[0]
            if descriptor.bLength != 18 or descriptor.bDescriptorType != 1:
                raise NativeUsbError("Unexpected USB device descriptor shape")
            record["target"] = {"linux_device": f"{bus:03d}:{address:03d}", "vidpid": VIDPID,
                                "bcd_device": descriptor.bcdDevice, "serial": None,
                                "identity_basis": "fresh USB location and product; no CPU/application identity"}
            record["descriptor"] = {name: getattr(descriptor, name)
                                    for name, _ in _DeviceDescriptor._fields_}
            record["stage"] = "descriptors"
            record["usb_contract"] = self._configuration(selected)
            record["stage"] = "open"
            self._check(self.lib.libusb_open(selected, C.byref(handle)), "open selected device")
            configuration = (C.c_ubyte * 1)()
            rc = self.lib.libusb_control_transfer(handle, 0x80, 8, 0, 0, configuration, 1,
                                                 min(timeout_ms, 1000))
            record["configuration_read_rc"] = rc
            if rc != 1 or configuration[0] != 2:
                raise NativeUsbError("GET_CONFIGURATION must return active configuration 2; no setting changed")
            data_interface = record["usb_contract"]["interface"]
            active = self.lib.libusb_kernel_driver_active(handle, data_interface)
            record["kernel_driver_active_rc"] = active
            if active != 0:
                raise NativeUsbError("Data interface 1 must have no active kernel driver; drivers are never detached")
            record["stage"] = "claim"
            rc = self.lib.libusb_claim_interface(handle, data_interface)
            record["claim_rc"] = rc
            self._check(rc, "claim data interface 1")
            claimed = True
            record["stage"] = "send-read-request"
            out = (C.c_ubyte * len(request)).from_buffer_copy(request)
            transferred = C.c_int()
            record.update(write_calls=1, request_outcome="unknown")
            rc = self.lib.libusb_bulk_transfer(handle, 0x02, out, len(out),
                                              C.byref(transferred), timeout_ms)
            record.update(bulk_out_rc=rc, bulk_out_transferred=transferred.value)
            self._check(rc, "send fixed read request")
            if transferred.value != len(request):
                raise NativeUsbError("Partial read request transmission; outcome unknown and no replay")
            record["stage"] = "receive"
            deadline = time.monotonic() + timeout_ms / 1000
            for _ in range(4):
                remaining = math.ceil((deadline - time.monotonic()) * 1000)
                if remaining <= 0:
                    raise NativeUsbError("Receive deadline expired; no retry")
                chunk = (C.c_ubyte * 512)()
                transferred = C.c_int()
                rc = self.lib.libusb_bulk_transfer(handle, 0x81, chunk, len(chunk),
                                                  C.byref(transferred), remaining)
                record["read_calls"].append({"rc": rc, "transferred": transferred.value})
                if not 0 <= transferred.value <= len(chunk):
                    raise NativeUsbError("libusb returned an invalid transfer length")
                response.extend(bytes(chunk[:transferred.value]))
                record["response_hex"] = response.hex()
                # libusb reports partial transferred bytes even on timeout.
                # Keep them but never treat an errored call as a completed read.
                self._check(rc, "receive fixed response")
                if not transferred.value:
                    raise NativeUsbError("Empty USB transfer; response incomplete")
                if len(response) > maximum:
                    raise NativeUsbError("Response exceeds the fixed profile frame limit")
                if len(response) >= 2:
                    expected = int.from_bytes(response[:2], "little")
                    if not 10 <= expected <= maximum:
                        raise NativeUsbError("Declared response length is outside the fixed profile bound")
                    if len(response) >= expected:
                        if len(response) != expected:
                            raise NativeUsbError("Trailing response bytes are unsupported")
                        record["stage"] = "raw-frame-only"
                        return bytes(response)
            raise NativeUsbError("Response incomplete after four chunks; request is never replayed")
        finally:
            # Keep cleanup separate from protocol outcome. A valid raw frame
            # plus failed release is not reported as an accepted observation.
            if claimed:
                record["release_rc"] = self.lib.libusb_release_interface(handle, data_interface)
            if handle:
                self.lib.libusb_close(handle)
                record["close_called"] = True  # libusb_close has void return.
            if devices:
                self.lib.libusb_free_device_list(devices, 1)
                record["device_list_freed"] = True
            if initialized:
                self.lib.libusb_exit(context)
                record["context_exited"] = True


def read_fixed(*, kind="runtime", profile="gcm-state", execute=False, device=None,
               timeout_ms=2500, lease_directory=None, backend_factory=None):
    """Return one JSON-compatible preview/observation/failure, without retries.

    Args: kind/profile choose the fixed read; execute=False has no USB/lease I/O;
        device is explicit Linux BUS:ADDRESS, mandatory only for execution;
        timeout_ms is 1..10000 ms per send/shared receive budget; lease_directory
        and backend_factory allow isolated offline tests.
    Returns: Existing runtime/memory observation schema with linux_native_usb=True.
        Raw/candidate values never assert CPU, application or physical I/O state.
    Side effects: Execute holds a local location lease and calls native libusb.
        Failures after sending begins keep request_outcome=unknown. No reconnect,
        fallback Windows helper, variable write, download or request replay occurs.
    """
    report = {"schema": f"plcidf.usb-{kind}-observation.v1", "ok": False,
              "status": "failed", "stage": "arguments", "device_access": False,
              "gui_access": False, "transport_kind": "linux-native-libusb1",
              "linux_native_usb": True, "linux_hardware_acceptance": False,
              "cpu_identity_verified": False, "cpu_state_verified": False,
              "application_execution_verified": False, "state_semantics": "unknown",
              "read_profile": profile, "requested_linux_device": device,
              "scope": "one fixed read using Linux libusb-1.0; no PLC state/application write",
              "diagnostics": []}
    try:
        request, _ = _contract(kind, profile, 0x1234)
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 10000:
            raise ValueError("timeout_ms must be an integer in 1..10000")
        if type(execute) is not bool:
            raise ValueError("execute must be an explicit boolean")
        report.update(request_hex=request.hex(), transfer_timeout_ms=timeout_ms)
        if kind == "memory":
            report.update(symbol=_MEMORY.profile_info(profile), candidate_protocol=True,
                          hardware_verified=False, scalar_interpretation_calibrated=False)
        if execute is False:
            return {**report, "ok": True, "status": "preview", "stage": "preview",
                    "execution_requires": "--transport linux-native --execute --linux-device BUS:ADDRESS; observed CDC configuration 2; free data interface 1"}
        bus, address = _location(device)
        sequence = secrets.randbits(16)
        request, _ = _contract(kind, profile, sequence)
        report.update(sequence=sequence, request_hex=request.hex(), stage="lease",
                      request_outcome="not_sent",
                      correlation_scope="random u16; not authentication or guaranteed freshness")
        # Linux bus/address differs from Windows physical BUSID. Do not invent
        # an equivalence between namespaces; libusb claim enforces OS ownership.
        with ResourceLease(f"device:linux-usb:{bus}:{address}", directory=lease_directory,
                           purpose="single fixed Easy521 Linux USB read") as lease:
            report["lease_token"] = lease.record["token"]
            report["stage"] = "load-library"
            backend = (backend_factory or Libusb1Transport)()
            transport = {}
            report["transport_record"] = transport
            report["device_access"] = True
            frame = backend.read(device=device, kind=kind, profile=profile,
                                 sequence=sequence, timeout_ms=timeout_ms, record=transport)
            report["stage"] = "validate"
            if transport.get("release_rc") != 0 or transport.get("close_called") is not True:
                raise NativeUsbError("Interface release/close did not complete successfully")
            if kind == "runtime":
                decoded = _RUNTIME.parse_read_response(frame, request=request,
                                                       profile=_RUNTIME_PROFILES[profile])
            else:
                decoded = _MEMORY.parse_read_response(frame, request=request, profile=profile)
            report["decoded"] = decoded
            if not decoded["ok"]:
                report["request_outcome"] = "device_error_response"
                raise NativeUsbError("Device returned an error frame; no value observation")
            report.update(ok=True, stage="complete", target=transport["target"])
            if kind == "runtime":
                report.update(status="observed", raw_value=decoded["raw_value"],
                              request_outcome="correlated_raw_data")
                if profile == "gcm-state":
                    report["static_cpu_state_interpretation"] = {
                        "value": {0: "STOP", 1: "RUN"}.get(decoded["raw_value"], "unknown"),
                        "basis": "vendor GetGCMState static mapping; report 18",
                        "device_calibrated": False}
            else:
                report.update(status="candidate-observed", value_candidate=decoded["value_candidate"],
                              data_hex=decoded["data_hex"], request_outcome="correlated_candidate_data")
        return report
    except Exception as error:
        transport = report.get("transport_record", {})
        # Codec validation has its own stage; earlier errors should expose the
        # actual native step, rather than the caller's last "load-library".
        if report["stage"] != "validate":
            report["stage"] = transport.get("stage", report["stage"])
        if report.get("request_outcome") != "device_error_response":
            report["request_outcome"] = transport.get("request_outcome", report.get("request_outcome", "not_sent"))
        report.update(ok=False, status="failed")
        report["diagnostics"].append({"code": "E_LINUX_USB_READ",
                                      "stage": report["stage"],
                                      "message": str(error)})
        return report
