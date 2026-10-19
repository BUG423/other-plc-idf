"""Native transport C-ABI substitutes only; no USB, Windows or PLC access.

The fake implements libusb output pointers, descriptor trees, partial transfers
and failures. This verifies control flow/ABI declarations, not real driver or
Easy521 acceptance. Codec fixtures are constructed independently from requests.
"""

import ctypes as C
import importlib.util
import json
from pathlib import Path
import struct

import pytest

spec = importlib.util.spec_from_file_location(
    "tested_easy521_linux_usb", Path(__file__).parents[1] / "easy521_linux_usb.py")
usb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usb)

# Literal USB configuration bytes from native-descriptors.json, observed on the
# real 0471:a531 on 2026-10-19. They differ from the former Windows claim0-based
# fake: interface0 is CDC control, while endpoints81/02 belong to interface1.
OBSERVED_CONFIGURATION = bytes.fromhex(
    "09024b00020204c001080b000202020107090400000102020105"
    "05240010010524010001042402020524060001070584030a0009"
    "09040100020a0000060705810200020007050202000200")


def set_pointer(pointer, kind, value):
    C.cast(pointer, C.POINTER(kind))[0] = value


class FakeFunction:
    def __init__(self, implementation):
        self.implementation = implementation

    def __call__(self, *args):
        return self.implementation(*args)


class FakeLibusb:
    """C-compatible substitute, including memory lifetime for descriptor trees."""

    def __init__(self, *, targets=1, bus=1, address=4, configuration=2,
                 kernel=0, claim=0, release=0, send_rc=0, send_bytes=None,
                 receive_rc=0, chunks=None, response=None, value=1,
                 endpoints=(0x81, 0x02), attributes=2, packet=512,
                 alternative=0, alternate_count=1, configuration_read=None,
                 descriptor_rc=0, open_rc=0, init_rc=0, list_rc=None):
        self.targets, self.bus, self.address = targets, bus, address
        self.configuration = configuration
        self.kernel, self.claim, self.release = kernel, claim, release
        self.send_rc, self.send_bytes = send_rc, send_bytes
        self.receive_rc, self.chunks, self.response = receive_rc, chunks, response
        self.value, self.configuration_read = value, configuration_read
        self.descriptor_rc, self.open_rc, self.init_rc = descriptor_rc, open_rc, init_rc
        self.list_rc = list_rc
        self.calls, self.sent, self.received = [], [], 0
        self.devices = (C.c_void_p * (targets + 1))(*range(101, 101 + targets), None)
        self._from_observed_configuration()
        # Override specific fields for negative fixtures; all other control,
        # data, IAD and union fields still come from the observed byte stream.
        self.endpoints = (usb._Endpoint * len(endpoints))()
        for endpoint, number in zip(self.endpoints, endpoints):
            endpoint.bLength, endpoint.bDescriptorType = 7, 5
            endpoint.bEndpointAddress, endpoint.bmAttributes = number, attributes
            endpoint.wMaxPacketSize = packet
        self.alternates = (usb._InterfaceDescriptor * alternate_count)()
        for alt in self.alternates:
            C.memmove(C.byref(alt), C.byref(self.observed_interfaces[1]), C.sizeof(alt))
            alt.bAlternateSetting, alt.bNumEndpoints = alternative, len(endpoints)
            alt.endpoint = self.endpoints
        self.interfaces[1].num_altsetting = alternate_count
        self.interfaces[1].altsetting = self.alternates
        self.config.bConfigurationValue = configuration
        for name in (
            "init", "exit", "get_device_list", "free_device_list", "get_device_descriptor",
            "get_bus_number", "get_device_address", "get_active_config_descriptor",
            "free_config_descriptor", "open", "close", "kernel_driver_active",
            "claim_interface", "release_interface", "control_transfer", "bulk_transfer",
        ):
            def implementation(*args, name=name):
                self.calls.append((name, args))
                return getattr(self, "_" + name)(*args)
            setattr(self, "libusb_" + name, FakeFunction(implementation))

    def _from_observed_configuration(self):
        """Decode independent captured descriptor bytes into a libusb C tree."""
        raw, offset, descriptors = OBSERVED_CONFIGURATION, 0, []
        while offset < len(raw):
            size = raw[offset]
            descriptors.append(raw[offset:offset + size])
            offset += size
        self.config = usb._Configuration()
        (self.config.bLength, self.config.bDescriptorType, self.config.wTotalLength,
         self.config.bNumInterfaces, self.config.bConfigurationValue, self.config.iConfiguration,
         self.config.bmAttributes, self.config.MaxPower) = struct.unpack("<BBHBBBBB", descriptors[0])
        self.observed_interfaces = (usb._InterfaceDescriptor * 2)()
        self.interfaces = (usb._Interface * 2)()
        self.endpoint_storage, self.extra_storage = [], []
        config_extra, interface_extras, interface_endpoints = [], {0: [], 1: []}, {0: [], 1: []}
        current = None
        for descriptor in descriptors[1:]:
            if descriptor[1] == 4:
                current = descriptor[2]
                for (name, _), value in zip(usb._InterfaceDescriptor._fields_[:9], descriptor):
                    setattr(self.observed_interfaces[current], name, value)
            elif descriptor[1] == 5:
                endpoint = usb._Endpoint()
                (endpoint.bLength, endpoint.bDescriptorType, endpoint.bEndpointAddress,
                 endpoint.bmAttributes, endpoint.wMaxPacketSize, endpoint.bInterval) = struct.unpack("<BBBBHB", descriptor)
                interface_endpoints[current].append(endpoint)
            elif current is None:
                config_extra.append(descriptor)
            else:
                interface_extras[current].append(descriptor)
        for number, interface in enumerate(self.observed_interfaces):
            endpoints = (usb._Endpoint * len(interface_endpoints[number]))(*interface_endpoints[number])
            self.endpoint_storage.append(endpoints)
            interface.endpoint = endpoints
            self.set_extras(interface, b"".join(interface_extras[number]))
            self.interfaces[number].num_altsetting = 1
            self.interfaces[number].altsetting = C.pointer(interface)
        self.config.interface = self.interfaces
        self.set_extras(self.config, b"".join(config_extra))

    def set_extras(self, owner, value):
        raw = (C.c_ubyte * len(value)).from_buffer_copy(value)
        self.extra_storage.append(raw)
        owner.extra, owner.extra_length = raw, len(value)

    def _init(self, context):
        set_pointer(context, C.c_void_p, 301)
        return self.init_rc

    def _exit(self, context):
        pass

    def _get_device_list(self, context, devices):
        set_pointer(devices, C.POINTER(C.c_void_p), C.cast(self.devices, C.POINTER(C.c_void_p)))
        return self.targets if self.list_rc is None else self.list_rc

    def _free_device_list(self, devices, unref):
        assert unref == 1

    def _get_device_descriptor(self, device, output):
        descriptor = usb._DeviceDescriptor()
        descriptor.bLength, descriptor.bDescriptorType = 18, 1
        descriptor.idVendor, descriptor.idProduct = usb.VID, usb.PID
        descriptor.bcdDevice = 0x0310
        set_pointer(output, usb._DeviceDescriptor, descriptor)
        return self.descriptor_rc

    def _get_bus_number(self, device):
        return self.bus

    def _get_device_address(self, device):
        return self.address

    def _get_active_config_descriptor(self, device, output):
        set_pointer(output, C.POINTER(usb._Configuration), C.pointer(self.config))
        return 0

    def _free_config_descriptor(self, descriptor):
        pass

    def _open(self, device, output):
        if not self.open_rc:
            set_pointer(output, C.c_void_p, 501)
        return self.open_rc

    def _close(self, handle):
        pass

    def _kernel_driver_active(self, handle, interface):
        assert interface == 1
        return self.kernel

    def _claim_interface(self, handle, interface):
        assert interface == 1
        return self.claim

    def _release_interface(self, handle, interface):
        assert interface == 1
        return self.release

    def _control_transfer(self, handle, direction, request, value, index, data, length, timeout):
        assert (direction, request, value, index, length) == (0x80, 8, 0, 0, 1)
        assert 1 <= timeout <= 1000
        data[0] = self.configuration
        return 1 if self.configuration_read is None else self.configuration_read

    def _bulk_transfer(self, handle, endpoint, data, length, transferred, timeout):
        assert 1 <= timeout <= 10000
        if endpoint == 0x02:
            self.sent.append(bytes(data[:length]))
            set_pointer(transferred, C.c_int, length if self.send_bytes is None else self.send_bytes)
            return self.send_rc
        assert endpoint == 0x81 and length == 512 and len(self.sent) == 1
        if self.chunks is not None:
            chunk = self.chunks[self.received] if self.received < len(self.chunks) else b""
        elif self.response is not None:
            chunk = self.response
        else:
            request = self.sent[0]
            sequence = int.from_bytes(request[4:6], "little")
            if request[3] == 1:
                width = int.from_bytes(request[14:18], "little")
                raw = int(self.value).to_bytes(width, "little")
                chunk = struct.pack("<HBBHHBB", 10 + len(raw), 1, 1, sequence, 0, 5, 0) + raw
            else:
                width = int.from_bytes(request[18:22], "little")
                raw = (bytes([int(self.value)]) if width == 1 else
                       struct.pack("<h", int(self.value)) if width == 16 else
                       struct.pack("<f", float(self.value)))
                chunk = struct.pack("<HBBHHBB", 22 + len(raw), 1, 3, sequence, 0, 2, 0) + request[10:22] + raw
        for index, byte in enumerate(chunk):
            data[index] = byte
        set_pointer(transferred, C.c_int, len(chunk))
        self.received += 1
        return self.receive_rc

    @property
    def names(self):
        return [name for name, args in self.calls]


def execute(tmp_path, fake=None, **kwargs):
    fake = FakeLibusb() if fake is None else fake
    return usb.read_fixed(execute=True, device=kwargs.pop("device", "001:004"),
                          lease_directory=tmp_path, backend_factory=lambda: usb.Libusb1Transport(library=fake),
                          **kwargs)


def diagnostic_frame(profile, raw, sequence=0x1234):
    request = usb._MEMORY.build_read_request(profile=profile, sequence=sequence)
    return struct.pack("<H", 22+len(raw)) + request[2:8] + b"\x02\0" + request[10:] + raw


@pytest.mark.parametrize("profile,raw,frame_bytes", [
    ("cur-error-count", struct.pack("<h", 1), 24),
    ("cur-error-list", struct.pack("<h", 1)+struct.pack("<hhii",5056,0,1,2)+b"\0"*492, 528),
    ("cur-error-instances", b"\0"*168, 190)])
def test_native_fixed_diagnostics_support_only_exact_bound_and_one_out(tmp_path, monkeypatch, profile, raw, frame_bytes):
    monkeypatch.setattr(usb.secrets, "randbits", lambda count: 0x1234)
    frame = diagnostic_frame(profile, raw)
    assert len(frame) == frame_bytes
    fake = FakeLibusb(chunks=[frame[i:i+512] for i in range(0,len(frame),512)])
    result = execute(tmp_path, fake, kind="memory", profile=profile)
    assert result["ok"] and len(fake.sent) == 1 and fake.received == (frame_bytes+511)//512
    assert result["data_hex"] == raw.hex() and result["cpu_state_verified"] is False
    assert fake.names[-4:] == ["release_interface", "close", "free_device_list", "exit"]


def test_large_diagnostic_allowance_does_not_leak_to_small_existing_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(usb.secrets, "randbits", lambda count: 0x1234)
    frame = diagnostic_frame("cur-error-list", b"\0"*506)
    fake = FakeLibusb(chunks=[frame[:512],frame[512:]])
    result = execute(tmp_path, fake, kind="memory", profile="pcmd")
    assert not result["ok"] and len(fake.sent) == 1 and fake.received == 1
    assert result["request_outcome"] == "unknown"


@pytest.mark.parametrize("tail", [b"", b"\0"*17])
def test_diagnostic_second_chunk_missing_or_trailing_stops_without_replay(tmp_path, monkeypatch, tail):
    monkeypatch.setattr(usb.secrets, "randbits", lambda count: 0x1234)
    frame = diagnostic_frame("cur-error-list", b"\0"*506)
    fake = FakeLibusb(chunks=[frame[:512],tail])
    result = execute(tmp_path, fake, kind="memory", profile="cur-error-list")
    assert not result["ok"] and len(fake.sent) == 1 and fake.received == 2
    assert result["request_outcome"] == "unknown" and result["transport_record"]["close_called"]


def test_all_diagnostic_previews_do_not_load_or_touch_devices(monkeypatch):
    def forbidden(*args, **kwargs):pytest.fail("diagnostic preview accessed device or lease")
    monkeypatch.setattr(usb.C,"CDLL",forbidden)
    monkeypatch.setattr(usb,"ResourceLease",forbidden)
    monkeypatch.setattr(usb.secrets,"randbits",forbidden)
    for profile in ("cur-error-count","cur-error-list","cur-error-instances"):
        result=usb.read_fixed(kind="memory",profile=profile)
        assert result["ok"] and result["status"]=="preview" and result["device_access"] is False


def test_preview_never_loads_library_enumerates_uses_random_or_acquires_lease(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("preview accessed an external resource")
    monkeypatch.setattr(usb.C, "CDLL", forbidden)
    monkeypatch.setattr(usb, "ResourceLease", forbidden)
    monkeypatch.setattr(usb.secrets, "randbits", forbidden)
    for kind, profile in (("runtime", "gcm-state"), ("memory", "revision")):
        result = usb.read_fixed(kind=kind, profile=profile, backend_factory=forbidden)
        assert result["ok"] and result["status"] == "preview" and not result["device_access"]
        assert result["linux_native_usb"] and not result["linux_hardware_acceptance"]


@pytest.mark.parametrize("device", [None, "", "7-2", "001:004;exit", True, 104, "0:4", "1:0", "256:4", "1:128"])
def test_invalid_explicit_location_prevents_any_library_access(tmp_path, device):
    fake = FakeLibusb()
    result = execute(tmp_path, fake, device=device)
    assert not result["ok"] and not result["device_access"] and not fake.calls


@pytest.mark.parametrize("options", [{"timeout_ms": 0}, {"timeout_ms": 10001}, {"timeout_ms": True},
                                    {"timeout_ms": 2.5}, {"kind": "download"}, {"profile": "arbitrary"}])
def test_invalid_contract_or_unbounded_timeout_is_rejected(tmp_path, options):
    fake = FakeLibusb()
    assert not execute(tmp_path, fake, **options)["ok"] and not fake.calls


def test_runtime_gcm_raw_result_and_static_mapping_are_not_calibrated(tmp_path):
    fake = FakeLibusb(value=1)
    result = execute(tmp_path, fake)
    assert result["ok"] and result["status"] == "observed" and result["raw_value"] == 1
    assert result["request_outcome"] == "correlated_raw_data"
    assert result["static_cpu_state_interpretation"]["value"] == "RUN"
    assert result["static_cpu_state_interpretation"]["device_calibrated"] is False
    assert not result["cpu_state_verified"] and not result["application_execution_verified"]
    assert not result["linux_hardware_acceptance"]
    assert result["target"]["linux_device"] == "001:004" and result["target"]["serial"] is None
    assert fake.names.count("bulk_transfer") == 2 and len(fake.sent) == 1
    assert fake.names[-4:] == ["release_interface", "close", "free_device_list", "exit"]
    assert fake.names.index("free_config_descriptor") < fake.names.index("open")
    assert fake.libusb_close.restype is None
    assert fake.libusb_get_device_list.restype is C.c_ssize_t
    contract = result["transport_record"]["usb_contract"]
    assert contract["control_interface"] == 0 and contract["data_interface"] == contract["interface"] == 1
    assert contract["cdc_union_hex"] == "0524060001" and contract["iad_hex"] == "080b000202020107"
    assert contract["contract_basis"] == usb.NATIVE_DESCRIPTOR_EVIDENCE
    ownership = [(name, args[1]) for name, args in fake.calls
                 if name in ("kernel_driver_active", "claim_interface", "release_interface")]
    assert ownership == [("kernel_driver_active", 1), ("claim_interface", 1), ("release_interface", 1)]


@pytest.mark.parametrize("kind,profile,value", [("runtime", "literal", 0x12345678),
    ("memory", "revision", 3.5), ("memory", "scan-counter", -123),
    ("memory", "heartbeat", 1), ("memory", "m8013", 0)])
def test_existing_fixed_profiles_preserve_raw_or_candidate_semantics(tmp_path, kind, profile, value):
    result = execute(tmp_path, FakeLibusb(value=value), kind=kind, profile=profile)
    assert result["ok"]
    if kind == "runtime":
        assert result["raw_value"] == value and "static_cpu_state_interpretation" not in result
    else:
        assert result["value_candidate"] == value and result["candidate_protocol"]
        assert not result["scalar_interpretation_calibrated"] and not result["hardware_verified"]


@pytest.mark.parametrize("options", [{"targets": 0}, {"targets": 2}, {"address": 5},
    {"configuration": 1}, {"endpoints": (0x03, 0x81)}, {"attributes": 3}, {"packet": 0},
    {"packet": 1024}, {"alternative": 1}, {"alternate_count": 2},
    {"kernel": 1}, {"kernel": -12}, {"claim": -6}, {"configuration_read": -7},
    {"configuration_read": 0}, {"descriptor_rc": -1}, {"open_rc": -3}])
def test_identity_configuration_endpoint_or_ownership_failure_prevents_request(tmp_path, options):
    fake = FakeLibusb(**options)
    result = execute(tmp_path, fake)
    assert not result["ok"] and not fake.sent and result["request_outcome"] == "not_sent"
    assert fake.names[-2:] == ["free_device_list", "exit"]
    if "claim_interface" in fake.names:
        assert "release_interface" not in fake.names  # claim failed, no ownership
    assert set(fake.names) <= {"init", "exit", "get_device_list", "free_device_list",
        "get_device_descriptor", "get_bus_number", "get_device_address",
        "get_active_config_descriptor", "free_config_descriptor", "open", "close",
        "control_transfer", "kernel_driver_active", "claim_interface"}


@pytest.mark.parametrize("mutation", ["bulk-on-control", "missing-union", "wrong-union-master",
    "wrong-union-slave", "duplicate-union", "union-on-data", "missing-iad", "wrong-iad-first",
    "wrong-iad-count", "duplicate-iad", "duplicate-endpoint", "duplicate-interface",
    "wrong-control-class", "wrong-data-class", "wrong-control-endpoint", "wrong-control-packet",
    "malformed-extra", "missing-data-interface"])
def test_cdc_association_and_endpoint_mismatch_never_claims_or_sends(tmp_path, mutation):
    fake = FakeLibusb()
    control, data = fake.observed_interfaces[0], fake.alternates[0]
    control_raw = C.string_at(control.extra, control.extra_length)
    iad = bytes.fromhex("080b000202020107")
    union = bytes.fromhex("0524060001")
    if mutation == "bulk-on-control":
        control.bNumEndpoints, control.endpoint = data.bNumEndpoints, data.endpoint
    elif mutation == "missing-union":
        fake.set_extras(control, control_raw.replace(union, b""))
    elif mutation == "wrong-union-master":
        fake.set_extras(control, control_raw.replace(union, bytes.fromhex("0524060101")))
    elif mutation == "wrong-union-slave":
        fake.set_extras(control, control_raw.replace(union, bytes.fromhex("0524060000")))
    elif mutation == "duplicate-union":
        fake.set_extras(control, control_raw + union)
    elif mutation == "union-on-data":
        fake.set_extras(control, control_raw.replace(union, b""))
        fake.set_extras(data, union)
    elif mutation == "missing-iad":
        fake.set_extras(fake.config, b"")
    elif mutation == "wrong-iad-first":
        fake.set_extras(fake.config, bytes.fromhex("080b010202020107"))
    elif mutation == "wrong-iad-count":
        fake.set_extras(fake.config, bytes.fromhex("080b000102020107"))
    elif mutation == "duplicate-iad":
        fake.set_extras(fake.config, iad + iad)
    elif mutation == "duplicate-endpoint":
        fake.endpoints[1].bEndpointAddress = fake.endpoints[0].bEndpointAddress
    elif mutation == "duplicate-interface":
        data.bInterfaceNumber = 0
    elif mutation == "wrong-control-class":
        control.bInterfaceClass = 10
    elif mutation == "wrong-data-class":
        data.bInterfaceClass = 2
    elif mutation == "wrong-control-endpoint":
        fake.endpoint_storage[0][0].bEndpointAddress = 0x83
    elif mutation == "wrong-control-packet":
        fake.endpoint_storage[0][0].wMaxPacketSize = 64
    elif mutation == "malformed-extra":
        fake.set_extras(control, control_raw + b"\x00\x24")
    elif mutation == "missing-data-interface":
        fake.config.bNumInterfaces = 1
    result = execute(tmp_path, fake)
    assert not result["ok"] and result["stage"] == "descriptors"
    assert result["request_outcome"] == "not_sent" and not fake.sent
    assert "claim_interface" not in fake.names and "open" not in fake.names
    assert fake.names[-3:] == ["free_config_descriptor", "free_device_list", "exit"]


def test_iad_parser_accepts_libusb_retention_with_control_interface_extras(tmp_path):
    fake = FakeLibusb()
    control = fake.observed_interfaces[0]
    iad = C.string_at(fake.config.extra, fake.config.extra_length)
    functions = C.string_at(control.extra, control.extra_length)
    fake.set_extras(fake.config, b"")
    fake.set_extras(control, iad + functions)
    assert execute(tmp_path, fake)["ok"]


@pytest.mark.parametrize("options", [{"send_rc": -7, "send_bytes": 5}, {"send_bytes": 17},
    {"send_rc": -9, "send_bytes": 0}])
def test_partial_or_errored_out_transfer_is_unknown_and_never_replayed(tmp_path, options):
    fake = FakeLibusb(**options)
    result = execute(tmp_path, fake)
    assert not result["ok"] and result["request_outcome"] == "unknown"
    assert len(fake.sent) == 1 and fake.received == 0
    record = result["transport_record"]
    assert record["bulk_out_transferred"] == options["send_bytes"]
    assert fake.names[-4:] == ["release_interface", "close", "free_device_list", "exit"]


def test_receive_timeout_keeps_partial_bytes_and_does_not_issue_second_in(tmp_path):
    fake = FakeLibusb(receive_rc=-7, response=bytes.fromhex("0b000101"))
    result = execute(tmp_path, fake)
    assert not result["ok"] and result["request_outcome"] == "unknown"
    assert fake.received == 1 and len(fake.sent) == 1
    assert result["transport_record"]["response_hex"] == "0b000101"
    assert result["transport_record"]["read_calls"] == [{"rc": -7, "transferred": 4}]


def test_fragmented_response_has_one_request_and_bounded_receive_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(usb.secrets, "randbits", lambda count: 0x1234)
    frame = bytes.fromhex("0b00010134120000050001")
    fake = FakeLibusb(chunks=[frame[:1], frame[1:5], frame[5:]])
    result = execute(tmp_path, fake)
    assert result["ok"] and fake.received == 3 and len(fake.sent) == 1
    receive_timeouts = [args[-1] for name, args in fake.calls
                        if name == "bulk_transfer" and args[1] == 0x81]
    assert receive_timeouts == sorted(receive_timeouts, reverse=True)
    assert all(1 <= timeout <= 2500 for timeout in receive_timeouts)


@pytest.mark.parametrize("response", [b"", bytes.fromhex("0000"), bytes.fromhex("ff00"),
    bytes.fromhex("0b0001013412000005000100"), bytes.fromhex("0b00010135120000050001"),
    bytes.fromhex("0b00010134120000030001")])
def test_malformed_trailing_or_uncorrelated_frame_never_becomes_observation(tmp_path, response, monkeypatch):
    monkeypatch.setattr(usb.secrets, "randbits", lambda count: 0x1234)
    fake = FakeLibusb(response=response)
    result = execute(tmp_path, fake)
    assert not result["ok"] and result["request_outcome"] == "unknown"
    assert "raw_value" not in result and len(fake.sent) == 1


def test_error_frame_is_explicitly_failed_even_when_code_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(usb.secrets, "randbits", lambda count: 0x1234)
    fake = FakeLibusb(response=bytes.fromhex("0e000101341200000f0000000000"))
    result = execute(tmp_path, fake)
    assert not result["ok"] and result["request_outcome"] == "device_error_response"
    assert result["decoded"]["error_code_u32"] == 0 and "raw_value" not in result


def test_release_failure_cannot_be_accepted_as_success(tmp_path):
    fake = FakeLibusb(release=-1)
    result = execute(tmp_path, fake)
    assert not result["ok"] and result["request_outcome"] == "unknown" and "raw_value" not in result
    assert fake.names[-4:] == ["release_interface", "close", "free_device_list", "exit"]


def test_lease_contention_prevents_library_load(tmp_path):
    fake = FakeLibusb()
    with usb.ResourceLease("device:linux-usb:1:4", directory=tmp_path):
        result = execute(tmp_path, fake)
    assert not result["ok"] and result["stage"] == "lease" and not fake.calls


def test_four_incomplete_chunks_are_not_retried_or_replayed(tmp_path):
    fake = FakeLibusb(chunks=[b"\x0b", b"\x00", b"\x01", b"\x01", b"ignored"])
    result = execute(tmp_path, fake)
    assert not result["ok"] and fake.received == 4 and len(fake.sent) == 1


def test_shared_receive_deadline_prevents_a_later_read(tmp_path, monkeypatch):
    times = iter([100.0, 100.0, 102.501])
    monkeypatch.setattr(usb.time, "monotonic", lambda: next(times))
    fake = FakeLibusb(chunks=[b"\x0b"])
    result = execute(tmp_path, fake)
    assert not result["ok"] and fake.received == 1 and len(fake.sent) == 1
    assert result["request_outcome"] == "unknown"
    assert result["transport_record"]["response_hex"] == "0b"


def test_raising_receive_call_still_releases_all_owned_resources(tmp_path):
    fake = FakeLibusb()
    original = fake.libusb_bulk_transfer.implementation
    def interrupted(*args):
        if args[1] == 0x81:
            raise KeyboardInterrupt()
        return original(*args)
    fake.libusb_bulk_transfer.implementation = interrupted
    with pytest.raises(KeyboardInterrupt):
        execute(tmp_path, fake)
    assert len(fake.sent) == 1
    assert fake.names[-4:] == ["release_interface", "close", "free_device_list", "exit"]


def test_library_initialization_failure_does_not_exit_unowned_context(tmp_path):
    fake = FakeLibusb(init_rc=-1)
    result = execute(tmp_path, fake)
    assert not result["ok"] and fake.names == ["init"]


@pytest.mark.parametrize("suffix", ["runtime", "memory"])
def test_existing_reader_native_cli_preview_never_touches_external_resources(suffix, capsys, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "native_cli_" + suffix, Path(__file__).parents[1] / f"read_easy521_usb_{suffix}.py")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    def forbidden(*args, **kwargs):
        pytest.fail("native preview accessed a resource")
    monkeypatch.setattr(C, "CDLL", forbidden)
    monkeypatch.setattr(reader, "run_command", forbidden)
    monkeypatch.setattr(reader, "ResourceLease", forbidden)
    assert reader.main(["--transport", "linux-native"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "preview" and output["linux_native_usb"]
    assert reader.main(["--transport", "linux-native", "--execute"]) == 1
    output = json.loads(capsys.readouterr().out)
    assert not output["device_access"]
    assert reader.main(["--transport", "linux-native", "--busid", "7-2"]) == 1
    assert json.loads(capsys.readouterr().out)["stage"] == "arguments"
    assert reader.main(["--linux-device", "001:004"]) == 1
    assert json.loads(capsys.readouterr().out)["stage"] == "arguments"


@pytest.mark.parametrize("suffix", ["runtime", "memory"])
def test_existing_reader_can_use_native_transport_without_any_windows_call(tmp_path, suffix, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "native_api_" + suffix, Path(__file__).parents[1] / f"read_easy521_usb_{suffix}.py")
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    def forbidden(*args, **kwargs):
        pytest.fail("native path accessed Windows")
    monkeypatch.setattr(reader, "run_command", forbidden)
    fake = FakeLibusb(value=1)
    function = getattr(reader, "read_" + suffix)
    result = function(execute=True, transport="linux-native", linux_device="001:004",
                      profile="gcm-state" if suffix == "runtime" else "revision",
                      lease_directory=tmp_path,
                      native_backend_factory=lambda: usb.Libusb1Transport(library=fake))
    assert result["ok"] and result["linux_native_usb"] and len(fake.sent) == 1
