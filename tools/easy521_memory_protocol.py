"""Offline fixed-variable selector-3 READ codec; no transport or device writes.

Research 19 traces vendor ReadMemoryEx -> CReadMemoryCommandEx (vtable 7b304,
execute +3c -> 1b470 -> 1b4f0) to a 12-byte {count,address,bits} body. The
response op2/count/address/bits/data layout is a candidate: its explicit op2
branch is recovered from the separately shipped Simulator, while the vendor
client strips the first 12 payload bytes. No live memory reply is assumed.

Addresses below are existing opaque vendor symbols from the pinned GVT/CSV,
never host pointers, byte offsets or Modbus registers. Public functions only
select fixed profiles; none accepts an arbitrary address/index/write operation.
Report41 adds fixed system diagnostics from the same-project CSV and vendor
SysVarDef/SysDataType. Their bounded structures remain candidate interpretations;
no error clearing, CPU transition or implicit fault diagnosis is performed.
"""

from __future__ import annotations

import math
import struct
from types import MappingProxyType

GVT_SHA256 = "63700c1f9b5a4b1201201442b1d614344d9b6346fd2ad913b2c22796560b95c5"
CSV_SHA256 = "75e89e3c7ef94dcb53129f4e4b1b0ef4bf72947ac4296633276c0f0ac29e44fe"
# symbol, IEC type, width in bits, opaque assigned address, source
PROFILES = MappingProxyType({
    # Report26's native application acceptance reads the pre-existing Pcmd
    # allocation, not a newly guessed address. Frozen VarNameAndAddr.csv line275
    # and independent GVT both give REAL32 / 0x021000f0 (report17/27 bindings).
    "pcmd": ("g_rBESS_Pcmd", "REAL", 32, 0x021000F0, "GVT+CSV"),
    "revision": ("g_rBESS_SensVP", "REAL", 32, 0x02100140, "GVT+CSV"),
    "step": ("g_rBESS_SensVQ", "REAL", 32, 0x02100160, "GVT+CSV"),
    "scan-counter": ("g_iBESS_Mode", "INT", 16, 0x02100130, "GVT+CSV"),
    "confidence": ("g_rBESS_Confidence", "REAL", 32, 0x021001C0, "GVT+CSV"),
    "published-scan": ("g_rBESS_InfluenceP", "REAL", 32, 0x02100180, "GVT+CSV"),
    "published-value": ("g_rBESS_InfluenceQ", "REAL", 32, 0x021001A0, "GVT+CSV"),
    "heartbeat": ("g_xBESS_ControllerReady", "BOOL", 1, 0x02100003, "GVT+CSV"),
    "m8000": ("M8000", "BOOL", 1, 0x06201800, "CSV line3"),
    "m8001": ("M8001", "BOOL", 1, 0x06201801, "CSV line4"),
    "m8013": ("M8013", "BOOL", 1, 0x06201822, "CSV line9"),
    # Report41: actual H530 GetCurErrLst/GetCurErrInst read these system
    # symbols via selector3/op1. Selected project CSV75/76 and SysVarDef pin
    # whole-symbol base addresses; SysDataType pins Quantity first INT16 and
    # 42 records of INT/INT/DINT/DINT. No arithmetic on opaque addresses.
    "cur-error-count": ("_CurErrLst.Quantity", "INT", 16, 0x06200700, "CSV line76+SysDataType"),
    "cur-error-list": ("_CurErrLst", "_sERR_LST", 4048, 0x06200700, "CSV line76+SysDataType"),
    "cur-error-instances": ("_CurErrInst", "DINT[42]", 1344, 0x06201900, "CSV line75+SysVarDef"),
})
_HEADER = struct.Struct("<HBBHHBB")
_ITEM = struct.Struct("<III")


class ProtocolError(ValueError):
    """Malformed/unsupported candidate frame; code identifies the failed rule."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def profile_info(profile: str) -> dict:
    """Return a copy of fixed symbol/type/bits/address evidence; no I/O.

    Unknown names raise ProtocolError. Addresses remain opaque vendor symbols.
    The returned dictionary cannot change the immutable internal profile table.
    """
    if not isinstance(profile, str) or profile not in PROFILES:
        raise ProtocolError("E_PROFILE", "Select an existing fixed memory-read profile")
    symbol, iec_type, bits, address, source = PROFILES[profile]
    return {"profile": profile, "symbol": symbol, "iec_type": iec_type,
            "width_bits": bits, "address_u32": address, "address_hex": hex(address),
            "address_semantics": "opaque_vendor_symbol", "allocation_source": source,
            "allocation_csv_sha256": CSV_SHA256,
            "allocation_gvt_sha256": GVT_SHA256 if source == "GVT+CSV" else None}


def _sequence(sequence):
    if type(sequence) is not int or not 0 <= sequence <= 65535:
        raise ProtocolError("E_SEQUENCE", "sequence must be an integer in 0..65535")
    return sequence


def response_frame_bytes(profile: str) -> int:
    """Return exact data-frame bytes for a fixed profile; no arbitrary length.

    The22B memory header/item plus ceil(width_bits/8) includes payload. Error
    frames remain14B. Diagnostics yield24/528/190 bytes respectively; transport
    must retain one OUT and its bounded shared IN deadline across USB chunks.
    """
    return 22 + (profile_info(profile)["width_bits"] + 7) // 8


def _header(frame, *, maximum=26):
    if type(frame) is not bytes:
        raise ProtocolError("E_BYTES", "frame must be complete immutable bytes")
    if not 10 <= len(frame) <= maximum:
        raise ProtocolError("E_LENGTH", "frame is truncated or outside the fixed-profile bound")
    size, version, selector, sequence, reserved, operation, flags = _HEADER.unpack_from(frame)
    if size != len(frame):
        raise ProtocolError("E_LENGTH", "declared frame length differs from actual length")
    if version != 1 or selector != 3:
        raise ProtocolError("E_HEADER", "only version1/selector3 memory frames are supported")
    return {"frame_bytes": size, "version": version, "selector": selector,
            "sequence": sequence, "reserved_u16": reserved, "operation": operation, "flags_u8": flags}


def build_read_request(*, profile: str, sequence: int) -> bytes:
    """Build exactly one 22-byte fixed READ candidate; never open a device.

    Args: profile is a fixed table key; sequence is u16 correlation, not auth.
    Returns: header plus count=1/address/bits, all integers little-endian.
    Raises: ProtocolError on unknown profile or invalid sequence.
    Side effects: None; no arbitrary address, I/O or automatic replay exists.
    """
    info = profile_info(profile)
    return _HEADER.pack(22, 1, 3, _sequence(sequence), 0, 1, 0) + _ITEM.pack(
        1, info["address_u32"], info["width_bits"])


def parse_read_request(frame: bytes, *, profile: str) -> dict:
    """Validate one exact fixed request and return metadata, without I/O.

    Rejects writes, flags, arbitrary addresses, count !=1, wrong bit width,
    truncation, trailing bytes and unknown profiles via ProtocolError.
    """
    info = profile_info(profile)
    header = _header(frame, maximum=22)
    if len(frame) != 22:
        raise ProtocolError("E_LENGTH", "request must contain one complete 12-byte item")
    if header["operation"] != 1 or header["flags_u8"] or header["reserved_u16"]:
        raise ProtocolError("E_READ_ONLY", "only ordinary zero-flag read operation1 is supported")
    if _ITEM.unpack_from(frame, 10) != (1, info["address_u32"], info["width_bits"]):
        raise ProtocolError("E_ITEM", "request count/address/bit-width differs from fixed profile")
    return {**header, **info}


def parse_read_response(frame: bytes, *, request: bytes, profile: str) -> dict:
    """Validate a complete reply and decode one fixed scalar/diagnostic candidate.

    The original request is revalidated; version/selector/sequence, item count,
    address, bits and exact payload size must agree. Error op15 stays failed
    even with error code0/1; all other operations except candidate data op2 fail.
    Returns raw bytes plus a BOOL/INT16/REAL32 or fixed diagnostic candidate,
    explicitly uncalibrated/hardware-unverified. Non-finite REALs and noncanonical
    BOOL bytes are refused; no inferred value replaces raw evidence.
    Diagnostic record offsets index returned bytes only, never opaque addresses.
    Structured values use raw_bits_u32=None; no huge integer is named a u32.
    Raises ProtocolError on malformed, uncorrelated or unsupported input. No I/O.
    """
    expected = parse_read_request(request, profile=profile)
    header = _header(frame, maximum=response_frame_bytes(profile))
    if header["sequence"] != expected["sequence"]:
        raise ProtocolError("E_CORRELATION", "response sequence differs from request")
    common = {**header, **profile_info(profile), "hardware_verified": False,
              "candidate_protocol": True, "scalar_interpretation_calibrated": False,
              "evidence_level": "vendor_request_static_and_simulator_reply_candidate"}
    if header["operation"] == 15:
        if len(frame) != 14:
            raise ProtocolError("E_ERROR_LENGTH", "error frame must contain exactly one DWORD")
        return {**common, "ok": False, "status": "device_error",
                "error_code_u32": int.from_bytes(frame[10:], "little")}
    if header["operation"] != 2:
        raise ProtocolError("E_OPERATION", "only candidate memory data op2 is accepted")
    data_bytes = (expected["width_bits"] + 7) // 8
    if len(frame) != 22 + data_bytes:
        raise ProtocolError("E_DATA_LENGTH", "reply has wrong exact scalar data length")
    if _ITEM.unpack_from(frame, 10) != (1, expected["address_u32"], expected["width_bits"]):
        raise ProtocolError("E_ITEM", "reply count/address/bit-width does not echo request")
    raw = frame[22:]
    bits = int.from_bytes(raw, "little", signed=False) if len(raw) <= 4 else None
    if expected["iec_type"] == "BOOL":
        if bits not in (0, 1):
            raise ProtocolError("E_BOOL", "noncanonical candidate BOOL byte")
        value = bool(bits)
    elif expected["iec_type"] == "INT":
        value = struct.unpack("<h", raw)[0]
        if profile == "cur-error-count" and not 0 <= value <= 42:
            raise ProtocolError("E_DIAGNOSTIC_COUNT", "Current-error quantity is outside the documented0..42 capacity")
    elif profile == "cur-error-list":
        quantity = struct.unpack_from("<h", raw)[0]
        if not 0 <= quantity <= 42:
            raise ProtocolError("E_DIAGNOSTIC_COUNT", "Current-error quantity is outside the documented0..42 capacity")
        records = [{"index": index, "error_code_i16": code, "error_code_u16": code & 0xFFFF,
                    "component_id_i16": component, "component_id_u16": component & 0xFFFF,
                    "location_i32": location, "timestamp_i32": timestamp}
                   for index, (code, component, location, timestamp) in enumerate(struct.iter_unpack("<hhii", raw[2:]))]
        value = {"quantity": quantity, "capacity": 42, "entries": records[:quantity],
                 "all_records": records, "layout_source": "report41 SysDataType; matched selected CSV4048bits",
                 "device_error_semantics_verified": False}
    elif profile == "cur-error-instances":
        value = {"instances_i32": list(struct.unpack("<42i", raw)),
                 "address_values_are_opaque": True, "device_error_semantics_verified": False}
    else:
        value = struct.unpack("<f", raw)[0]
        if not math.isfinite(value):
            raise ProtocolError("E_REAL", "non-finite candidate REAL data")
    return {**common, "ok": True, "status": "candidate_data", "data_hex": raw.hex(),
            "raw_bits_u32": bits, "value_candidate": value}
