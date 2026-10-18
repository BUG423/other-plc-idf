"""Synthetic static-contract vectors only; these tests never communicate.

Offsets and profile conflicts are documented in firmware research report 15.
No vendor binary, device capture, state enumeration, or transport is required.
"""

import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "easy521_protocol", Path(__file__).parents[1] / "easy521_protocol.py"
)
codec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codec)

VENDOR_REQUEST = bytes.fromhex("12000101341200000100000001ff04000000")
VENDOR_DATA = bytes.fromhex("0e00010134120000050078563412")
SIM_REQUEST = bytes.fromhex("12000101341200000100000000ff01000000")
SIM_DATA = bytes.fromhex("0b000101341200000500a5")


def replace(frame, offset, replacement):
    return frame[:offset] + replacement + frame[offset + len(replacement):]


def test_vendor_request_matches_independently_transcribed_static_vector():
    assert codec.build_read_request(profile=codec.VENDOR_RUNTIME, sequence=0x1234) == VENDOR_REQUEST
    parsed = codec.parse_read_request(VENDOR_REQUEST, profile=codec.VENDOR_RUNTIME)
    assert parsed["index_u32"] == 0xFF010000
    assert parsed["requested_data_bytes"] == 4


def test_simulator_profile_is_explicit_and_different():
    assert codec.build_read_request(profile=codec.SIMULATOR_CPU_STATUS, sequence=0x1234) == SIM_REQUEST
    assert codec.parse_read_response(SIM_DATA, request=SIM_REQUEST, profile=codec.SIMULATOR_CPU_STATUS)["raw_value"] == 0xA5
    with pytest.raises(codec.ProtocolError, match="index or requested"):
        codec.parse_read_request(SIM_REQUEST, profile=codec.VENDOR_RUNTIME)
    with pytest.raises(codec.ProtocolError, match="index or requested"):
        codec.parse_read_response(VENDOR_DATA, request=VENDOR_REQUEST, profile=codec.SIMULATOR_CPU_STATUS)


def test_raw_vendor_response_never_claims_run_or_stop():
    result = codec.parse_read_response(VENDOR_DATA, request=VENDOR_REQUEST, profile=codec.VENDOR_RUNTIME)
    assert result["ok"] and result["status"] == "data"
    assert result["raw_value"] == 0x12345678
    assert result["payload_hex"] == "78563412"
    assert result["state_semantics"] == "unknown" and result["device_verified"] is False


@pytest.mark.parametrize("code", [0, 1, 0xDEADBEEF])
def test_device_error_never_becomes_data_even_for_success_looking_numbers(code):
    frame = bytes.fromhex("0e000101341200000f00") + code.to_bytes(4, "little")
    result = codec.parse_read_response(frame, request=VENDOR_REQUEST, profile=codec.VENDOR_RUNTIME)
    assert result["ok"] is False and result["status"] == "device_error"
    assert result["error_code_u32"] == code and "raw_value" not in result


@pytest.mark.parametrize("sequence", [0, 1, 65534, 65535])
def test_sequence_boundaries_and_wrap(sequence):
    request = codec.build_read_request(profile=codec.VENDOR_RUNTIME, sequence=sequence)
    assert codec.parse_read_request(request, profile=codec.VENDOR_RUNTIME)["sequence"] == sequence
    assert codec.next_sequence(sequence) == ((sequence + 1) % 65536)


@pytest.mark.parametrize("sequence", [True, False, -1, 65536, 1.0, "1", None])
def test_invalid_sequence_fails_before_build(sequence):
    with pytest.raises(codec.ProtocolError) as exc:
        codec.build_read_request(profile=codec.VENDOR_RUNTIME, sequence=sequence)
    assert exc.value.code == "E_SEQUENCE"


@pytest.mark.parametrize("profile", ["", "easy521", "VENDOR_RUNTIME", None, True, []])
def test_unknown_profiles_have_no_default(profile):
    with pytest.raises(codec.ProtocolError) as exc:
        codec.build_read_request(profile=profile, sequence=1)
    assert exc.value.code == "E_PROFILE"


@pytest.mark.parametrize("size", range(18))
def test_every_truncated_request_fails(size):
    with pytest.raises(codec.ProtocolError):
        codec.parse_read_request(VENDOR_REQUEST[:size], profile=codec.VENDOR_RUNTIME)


@pytest.mark.parametrize("size", range(14))
def test_every_truncated_response_fails(size):
    with pytest.raises(codec.ProtocolError):
        codec.parse_read_response(VENDOR_DATA[:size], request=VENDOR_REQUEST, profile=codec.VENDOR_RUNTIME)


@pytest.mark.parametrize("frame", [
    VENDOR_REQUEST + b"\x00",
    replace(VENDOR_REQUEST, 0, b"\x11\x00"),
    replace(VENDOR_REQUEST, 0, b"\xff\xff"),
    replace(VENDOR_REQUEST, 2, b"\x02"),
    replace(VENDOR_REQUEST, 3, b"\x04"),
    replace(VENDOR_REQUEST, 6, b"\x01\x00"),
    replace(VENDOR_REQUEST, 8, b"\x02"),  # A write can never be accepted here.
    replace(VENDOR_REQUEST, 9, b"\x10"),
    replace(VENDOR_REQUEST, 10, b"\x00\x00\x00\xff"),
    replace(VENDOR_REQUEST, 14, b"\x01\x00\x00\x00"),
])
def test_request_field_mutations_and_tail_fail(frame):
    with pytest.raises(codec.ProtocolError):
        codec.parse_read_request(frame, profile=codec.VENDOR_RUNTIME)


@pytest.mark.parametrize("frame", [
    VENDOR_DATA + b"\x00",
    replace(VENDOR_DATA, 0, b"\x0d\x00"),
    replace(VENDOR_DATA, 0, b"\xff\xff"),
    replace(VENDOR_DATA, 2, b"\x00"),
    replace(VENDOR_DATA, 3, b"\x81"),
    replace(VENDOR_DATA, 4, b"\x35\x12"),
    replace(VENDOR_DATA, 8, b"\x06"),  # Length/ack is not the runtime DWORD.
    replace(VENDOR_DATA, 8, b"\x03"),  # Other vendor read branch is unsupported.
    replace(VENDOR_DATA, 8, b"\x01"),
    replace(SIM_DATA, 8, b"\x0f"),  # Error must contain a full DWORD.
    SIM_DATA,  # Valid Simulator length is invalid for vendor runtime.
])
def test_response_corruption_and_non_data_operations_fail_closed(frame):
    with pytest.raises(codec.ProtocolError):
        codec.parse_read_response(frame, request=VENDOR_REQUEST, profile=codec.VENDOR_RUNTIME)


def test_vendor_u32_cannot_satisfy_simulator_u8_profile():
    with pytest.raises(codec.ProtocolError) as exc:
        codec.parse_read_response(VENDOR_DATA, request=SIM_REQUEST, profile=codec.SIMULATOR_CPU_STATUS)
    assert exc.value.code == "E_DATA_LENGTH"


def test_response_opaque_fields_are_preserved_without_inventing_echo_rule():
    frame = replace(replace(VENDOR_DATA, 6, b"\xaa\xbb"), 9, b"\xf2")
    result = codec.parse_read_response(frame, request=VENDOR_REQUEST, profile=codec.VENDOR_RUNTIME)
    assert result["reserved_u16"] == 0xBBAA and result["flags_u8"] == 0xF2


@pytest.mark.parametrize("value", [None, "bytes", bytearray(VENDOR_REQUEST), memoryview(VENDOR_REQUEST), 18])
def test_only_immutable_bytes_are_accepted(value):
    with pytest.raises(codec.ProtocolError) as exc:
        codec.parse_read_request(value, profile=codec.VENDOR_RUNTIME)
    assert exc.value.code == "E_BYTES"


def test_response_revalidates_original_request_instead_of_trusting_sequence_only():
    poisoned_request = replace(VENDOR_REQUEST, 8, b"\x02")
    with pytest.raises(codec.ProtocolError) as exc:
        codec.parse_read_response(VENDOR_DATA, request=poisoned_request, profile=codec.VENDOR_RUNTIME)
    assert exc.value.code == "E_OPERATION"


def test_profile_must_be_explicit_in_public_api():
    with pytest.raises(TypeError):
        codec.build_read_request(sequence=0)
