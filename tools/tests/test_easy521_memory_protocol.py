"""Synthetic selector3 frames; no vendor binary, process or device access."""
import importlib.util
from pathlib import Path
import struct

import pytest

spec = importlib.util.spec_from_file_location("easy521_memory_protocol", Path(__file__).parents[1]/"easy521_memory_protocol.py")
codec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codec)

REQUEST = bytes.fromhex("16000103341200000100010000004001100220000000")
# Own fixture: op2,count1,SensVP,bits32,LE REAL32=52102.0. Not device traffic.
RESPONSE = bytes.fromhex("1a000103341200000200010000004001100220000000") + struct.pack("<f",52102.0)


def changed(frame, offset, replacement):
    return frame[:offset]+replacement+frame[offset+len(replacement):]


def test_known_vendor_request_vector_and_candidate_value():
    assert codec.build_read_request(profile="revision",sequence=0x1234) == REQUEST
    result=codec.parse_read_response(RESPONSE,request=REQUEST,profile="revision")
    assert result["ok"] and result["value_candidate"]==52102.0
    assert result["hardware_verified"] is False and result["candidate_protocol"] is True
    assert result["scalar_interpretation_calibrated"] is False


@pytest.mark.parametrize("profile",list(codec.PROFILES))
def test_every_profile_is_fixed_and_round_trips_request(profile):
    request=codec.build_read_request(profile=profile,sequence=65535)
    result=codec.parse_read_request(request,profile=profile)
    assert len(request)==22 and result["sequence"]==65535
    assert result["address_semantics"]=="opaque_vendor_symbol"


def test_heartbeat_address_is_actual_bit_allocation_not_table_end_guess():
    info=codec.profile_info("heartbeat")
    assert info["address_u32"]==0x02100003 and info["width_bits"]==1
    assert codec.profile_info("m8013")["address_u32"]==0x06201822


def test_pcmd_fixed_read_uses_independently_allocated_gvt_and_csv_symbol():
    info = codec.profile_info("pcmd")
    assert (info["symbol"], info["iec_type"], info["width_bits"], info["address_u32"]) == (
        "g_rBESS_Pcmd", "REAL", 32, 0x021000F0)
    assert info["allocation_gvt_sha256"] == codec.GVT_SHA256
    request = codec.build_read_request(profile="pcmd", sequence=0x1234)
    assert request.hex() == "1600010334120000010001000000f000100220000000"
    assert candidate_reply("pcmd", struct.pack("<f", 0.125))["value_candidate"] == 0.125


@pytest.mark.parametrize("sequence",[True,-1,65536,1.0,None])
def test_invalid_sequence(sequence):
    with pytest.raises(codec.ProtocolError):codec.build_read_request(profile="revision",sequence=sequence)


@pytest.mark.parametrize("profile",["Y0","0x02100140","write",None,True,[]])
def test_unknown_profiles_and_arbitrary_addresses_are_rejected(profile):
    with pytest.raises(codec.ProtocolError):codec.build_read_request(profile=profile,sequence=0)


@pytest.mark.parametrize("size",range(22))
def test_every_truncated_request(size):
    with pytest.raises(codec.ProtocolError):codec.parse_read_request(REQUEST[:size],profile="revision")


@pytest.mark.parametrize("size",range(26))
def test_every_truncated_response(size):
    with pytest.raises(codec.ProtocolError):codec.parse_read_response(RESPONSE[:size],request=REQUEST,profile="revision")


@pytest.mark.parametrize("offset,replacement",[(0,b"\xff\xff"),(2,b"\x02"),(3,b"\x01"),(6,b"\x01\x00"),(8,b"\x02"),(9,b"\x10"),(10,b"\x02\x00\x00\x00"),(14,b"\x30\x01\x10\x02"),(18,b"\x04\x00\x00\x00")])
def test_request_header_write_address_count_width_mutations(offset,replacement):
    with pytest.raises(codec.ProtocolError):codec.parse_read_request(changed(REQUEST,offset,replacement),profile="revision")


@pytest.mark.parametrize("offset,replacement",[(0,b"\xff\xff"),(2,b"\x02"),(3,b"\x01"),(4,b"\x35\x12"),(8,b"\x05"),(8,b"\x06"),(10,b"\x02\x00\x00\x00"),(14,b"\x30\x01\x10\x02"),(18,b"\x10\x00\x00\x00")])
def test_wrong_response_identity_operation_echo_or_width_is_rejected(offset,replacement):
    with pytest.raises(codec.ProtocolError):codec.parse_read_response(changed(RESPONSE,offset,replacement),request=REQUEST,profile="revision")


def test_trailing_bytes_and_mutable_input_rejected():
    for frame in (RESPONSE+b"\0",bytearray(RESPONSE)):
        with pytest.raises(codec.ProtocolError):codec.parse_read_response(frame,request=REQUEST,profile="revision")


@pytest.mark.parametrize("error",[0,1,0xdeadbeef])
def test_error_is_failed_even_when_numeric_code_looks_successful(error):
    frame=bytes.fromhex("0e000103341200000f00")+struct.pack("<I",error)
    result=codec.parse_read_response(frame,request=REQUEST,profile="revision")
    assert not result["ok"] and result["error_code_u32"]==error


def candidate_reply(profile,data):
    request=codec.build_read_request(profile=profile,sequence=0x1234)
    response=struct.pack("<H",22+len(data))+request[2:8]+b"\x02\x00"+request[10:]+data
    return codec.parse_read_response(response,request=request,profile=profile)


def test_signed_int16_and_bool_candidates():
    assert candidate_reply("scan-counter",struct.pack("<h",-321))["value_candidate"]==-321
    assert candidate_reply("heartbeat",b"\x01")["value_candidate"] is True
    assert candidate_reply("m8001",b"\x00")["value_candidate"] is False


@pytest.mark.parametrize("data",[struct.pack("<f",float("nan")),struct.pack("<f",float("inf"))])
def test_nonfinite_real_cannot_be_reported_as_json_value(data):
    with pytest.raises(codec.ProtocolError):candidate_reply("revision",data)


def test_noncanonical_bool_padding_is_not_silently_discarded():
    with pytest.raises(codec.ProtocolError):candidate_reply("heartbeat",b"\x80")
