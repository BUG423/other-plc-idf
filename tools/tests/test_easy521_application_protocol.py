"""Independently constructed file/GCM frames; no vendor runtime or device I/O."""

import importlib.util
from pathlib import Path
import struct

import pytest

spec = importlib.util.spec_from_file_location("application_codec", Path(__file__).parents[1] / "easy521_application_protocol.py")
codec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codec)


def response(request, operation, body):
    return struct.pack("<HBBHHBB", 10 + len(body), 1, request[3],
                       int.from_bytes(request[4:6], "little"), 0, operation, 0) + body


def test_open_read_matches_vendor_494_byte_usb_profile_and_explicit_credential():
    request = codec.build_file_open(remote_name="stprog.o", credential=b"12345678", sequence=0x1234)
    assert request == bytes.fromhex("23000104341200000100") + b"12345678" + struct.pack("<II", 494, 0) + b"stprog.o\0"
    parsed = codec.parse_request(request)
    assert parsed["action"] == "file-open-read" and not parsed["application_write"]
    result = codec.parse_response(response(request, 6, struct.pack("<I", 1001)), request=request)
    assert result["ok"] and result["ack_u32"] == 1001


def test_open_write_is_distinct_destructive_operation_with_actual_file_length():
    request = codec.build_file_open(remote_name="userprog.foid", credential=b"\0" * 8,
                                    sequence=0, direction="write", file_bytes=123)
    assert request[8] == 2 and request[18:26] == struct.pack("<II", 40960, 123)
    assert codec.parse_request(request)["application_write"]


def test_read_block_is_a_number_and_response_has_exact_count_and_data():
    request = codec.build_file_read(block=3, sequence=7)
    assert request == bytes.fromhex("0e00010407000000060003000000")
    frame = response(request, 5, struct.pack("<II", 3, 3) + b"abc")
    result = codec.parse_response(frame, request=request, expected_data_bytes=3)
    assert result["data_hex"] == "616263" and result["block"] == 3


def test_write_block_has_no_extra_nul_padding_or_invented_checksum():
    request = codec.build_file_write(block=0, data=b"abc", sequence=3)
    assert request == bytes.fromhex("150001040300000005000000000003000000616263")
    assert codec.parse_request(request)["data_bytes"] == 3
    assert codec.parse_response(response(request, 6, b"\0" * 4), request=request)["ok"]


@pytest.mark.parametrize("state", range(4))
def test_gcm_writer_exact_real_chain_and_ack_is_not_state_readback(state):
    request = codec.build_gcm_set(state=state, sequence=0xFFFF)
    assert request == bytes.fromhex("13000101ffff00000200000000ff01000000") + bytes([state])
    assert codec.parse_request(request)["state"] == state
    result = codec.parse_response(response(request, 6, struct.pack("<I", 0)), request=request)
    assert result["ok"] and result["ack_u32"] == 0


@pytest.mark.parametrize("options", [{"remote_name": "../stprog.o"}, {"remote_name": "/default/stprog.o"},
    {"remote_name": "firmware.bin"}, {"remote_name": "stprog.o\0"}, {"credential": b""},
    {"credential": b"0" * 9}, {"credential": "12345678"}, {"sequence": True},
    {"direction": "delete"}, {"file_bytes": 1}, {"direction": "write", "file_bytes": 0},
    {"direction": "write", "file_bytes": 8 * 1024 * 1024 + 1}])
def test_unsupported_file_paths_credentials_and_lengths_are_rejected(options):
    arguments = {"remote_name": "stprog.o", "credential": b"\0" * 8, "sequence": 1, **options}
    with pytest.raises(codec.ProtocolError):
        codec.build_file_open(**arguments)


@pytest.mark.parametrize("mutation", ["selector", "sequence", "operation", "block", "count", "trailing", "short", "remaining"])
def test_bad_block_response_never_yields_data(mutation):
    request = codec.build_file_read(block=2, sequence=5)
    frame = bytearray(response(request, 5, struct.pack("<II", 2, 3) + b"abc"))
    if mutation == "selector": frame[3] = 1
    elif mutation == "sequence": frame[4] = 6
    elif mutation == "operation": frame[8] = 6
    elif mutation == "block": frame[10] = 3
    elif mutation == "count": frame[14] = 4
    elif mutation == "trailing": frame += b"x"
    elif mutation == "short": frame = frame[:-1]
    with pytest.raises(codec.ProtocolError):
        codec.parse_response(bytes(frame), request=request, expected_data_bytes=2 if mutation == "remaining" else 3)


@pytest.mark.parametrize("code", [0, 1, 0x41, 0x84])
def test_error_reply_never_becomes_file_size_or_control_success(code):
    request = codec.build_file_open(remote_name="userprog.foid", credential=b"\0" * 8, sequence=0)
    result = codec.parse_response(response(request, 15, struct.pack("<I", code)), request=request)
    assert not result["ok"] and result["error_code_u32"] == code


@pytest.mark.parametrize("offset,value", [(3, 3), (8, 15), (9, 1), (6, 1), (18, 0), (22, 1)])
def test_arbitrary_operation_flags_chunk_or_length_cannot_pass_request_guard(offset, value):
    request = bytearray(codec.build_file_open(remote_name="stprog.o", credential=b"\0" * 8, sequence=0))
    request[offset] = value
    with pytest.raises(codec.ProtocolError):
        codec.parse_request(bytes(request))


@pytest.mark.parametrize("state", [-1, 4, True, "RUN"])
def test_unsupported_state_is_not_silently_coerced_to_stop(state):
    with pytest.raises(codec.ProtocolError):
        codec.build_gcm_set(state=state, sequence=0)


def test_actual_gcm_header9_empty_ack_preserves_missing_fields_instead_of_inventing_zero():
    request = bytes.fromhex("130001014f9d00000200000000ff0100000002")
    frame = bytes.fromhex("090001014f9d000006")
    result = codec.parse_response(frame, request=request)
    assert result["ok"] and result["ack_format"] == "gcm-header9-empty"
    assert result["ack_u32"] is None and result["flags"] is None and result["ack_payload_present"] is False


@pytest.mark.parametrize("mutation", ["size", "version", "selector", "sequence", "operation", "error", "trailing", "truncated"])
def test_header9_gcm_ack_rejects_uncorrelated_other_operation_and_bad_length(mutation):
    request = codec.build_gcm_set(state=2, sequence=0x9D4F)
    frame = bytearray.fromhex("090001014f9d000006")
    if mutation == "size": frame[0] = 10
    elif mutation == "version": frame[2] = 2
    elif mutation == "selector": frame[3] = 4
    elif mutation == "sequence": frame[4] ^= 1
    elif mutation == "operation": frame[8] = 5
    elif mutation == "error": frame[8] = 15
    elif mutation == "trailing": frame += b"\0"
    elif mutation == "truncated": frame = frame[:-1]
    with pytest.raises(codec.ProtocolError): codec.parse_response(bytes(frame), request=request)


@pytest.mark.parametrize("original_request", [codec.build_file_open(remote_name="stprog.o", credential=b"\0" * 8, sequence=7),
    codec.build_file_read(block=0, sequence=7)])
def test_header9_ack_cannot_become_read_open_file_size_or_read_block(original_request):
    frame = struct.pack("<HBBHHB", 9, 1, 4, 7, 0, 6)
    with pytest.raises(codec.ProtocolError): codec.parse_response(frame, request=original_request)


@pytest.mark.parametrize("original_request", [
    codec.build_file_open(remote_name="stprog.o", credential=b"\0" * 8, sequence=7, direction="write", file_bytes=3),
    codec.build_file_write(block=0, data=b"abc", sequence=7)])
def test_actual_download_callers_accept_header9_success_without_consuming_u32(original_request):
    frame = struct.pack("<HBBHHB", 9, 1, 4, 7, 0, 6)
    result = codec.parse_response(frame, request=original_request)
    assert result["ok"] and result["ack_u32"] is None and result["ack_payload_present"] is False
    assert result["ack_format"].startswith(codec.parse_request(original_request)["action"])


@pytest.mark.parametrize("size", [10, 11, 12, 13])
def test_other_short_gcm_ack_lengths_are_not_guessed_from_host_permissiveness(size):
    request = codec.build_gcm_set(state=2, sequence=7)
    frame = struct.pack("<HBBHHBB", size, 1, 1, 7, 0, 6, 0) + b"\0" * (size - 10)
    with pytest.raises(codec.ProtocolError): codec.parse_response(frame, request=request)
