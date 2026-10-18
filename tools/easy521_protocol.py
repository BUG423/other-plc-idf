"""Pure offline codec for explicitly selected, fixed read-only index profiles.

Research report 18 corrects report 15's command association: ff010000/4 belongs
to CReadEthercatCommand, not GetPLCRunMode/CPU scanning state. A live raw u32=0
was observed for that literal request, but its device meaning remains unknown.
The vendor's own GetGCMState path independently reads ff000000/1; Simulator
uses that same index/size but is a separate target, not evidence of compatibility.
See docs/research/firmware/18-runtime-mode-correction-2026-10-19.{md,json}.

Only a complete request and complete response are handled here.  The module
has no USB/socket transport, DLL loading, subprocesses, file I/O, login,
RUN/STOP, variable writes or application download.  Response values remain
raw integers: no RUN/STOP enumeration or state bits have been calibrated.
All codec fixtures are independently constructed, not captured traffic.
"""

from __future__ import annotations

import struct


VENDOR_LITERAL_FF010000_4 = "vendor_literal_ff010000_4"
VENDOR_GCM_STATE = "vendor_gcm_state"
# Compatibility name/string for historical callers and reports. Despite its
# old name it reads the literal EtherCAT-related index, NEVER CPU RUN/STOP.
VENDOR_RUNTIME = "vendor_h530_runtime"
SIMULATOR_CPU_STATUS = "simulator_cpu_status"

# Deliberately private and selected by exact name.  There is no implicit target
# and no arbitrary index/operation option that could turn this into a writer.
_PROFILES = {
    VENDOR_LITERAL_FF010000_4: (0xFF010000, 4),
    VENDOR_GCM_STATE: (0xFF000000, 1),
    VENDOR_RUNTIME: (0xFF010000, 4),
    SIMULATOR_CPU_STATUS: (0xFF000000, 1),
}
_HEADER = struct.Struct("<HBBHHBB")
_READ_BODY = struct.Struct("<II")
_HEADER_BYTES = 10
_REQUEST_BYTES = 18


class ProtocolError(ValueError):
    """Malformed or unsupported frame; ``code`` identifies the failed rule."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _profile(name: str) -> tuple[int, int]:
    if not isinstance(name, str) or name not in _PROFILES:
        raise ProtocolError("E_PROFILE", "Select an explicit, supported read-only profile")
    return _PROFILES[name]


def _sequence(value: int) -> int:
    if type(value) is not int or not 0 <= value <= 0xFFFF:
        raise ProtocolError("E_SEQUENCE", "sequence must be an integer in 0..65535")
    return value


def _header(frame: bytes, *, max_bytes: int) -> dict:
    # A strict immutable input prevents a caller changing a checked bytearray
    # between identity checks and payload decoding.  Cap before struct parsing.
    if type(frame) is not bytes:
        raise ProtocolError("E_BYTES", "frame must be immutable bytes")
    if not _HEADER_BYTES <= len(frame) <= max_bytes:
        raise ProtocolError("E_LENGTH", "frame is truncated or exceeds this read profile")
    size, version, selector, sequence, reserved, operation, flags = _HEADER.unpack_from(frame)
    if size != len(frame):
        raise ProtocolError("E_LENGTH", "declared length differs from the complete frame length")
    if version != 1:
        raise ProtocolError("E_VERSION", "this codec supports version 1 only")
    if selector != 1:
        raise ProtocolError("E_SELECTOR", "this codec supports index selector 1 only")
    return {
        "frame_bytes": size,
        "version": version,
        "selector": selector,
        "sequence": sequence,
        "reserved_u16": reserved,
        "operation": operation,
        "flags_u8": flags,
    }


def next_sequence(sequence: int) -> int:
    """Return the next u16 sequence, wrapping 65535 to 0; no session is stored.

    Args:
        sequence: Previous integer sequence in 0..65535 (bool is rejected).
    Returns:
        Next sequence according to vendor RVA 0x28085's 16-bit increment.
    Raises:
        ProtocolError: Sequence has an invalid type or range.
    Side effects: None.  A transport must own its own session/ordering policy.
    """
    return (_sequence(sequence) + 1) & 0xFFFF


def build_read_request(*, profile: str, sequence: int) -> bytes:
    """Construct one fixed read request without opening or changing any device.

    Args:
        profile: Explicit vendor literal (u32), vendor GCM (u8), or Simulator
            CPU status (u8). The legacy VENDOR_RUNTIME name is still accepted.
        sequence: Integer correlation value in 0..65535; not an authenticity token.
    Returns:
        Exactly 18 bytes: 10-byte header followed by LE index and data length.
    Raises:
        ProtocolError: Unknown profile or invalid sequence; no fallback profile.
    Side effects: None.  Returned bytes are a static-contract candidate only.
    """
    index, data_bytes = _profile(profile)
    seq = _sequence(sequence)
    # RVA 0x28050 zeroes the allocation, writes version/selector/sequence/op,
    # then copies the eight-byte body at +10. Only an ordinary read is
    # represented here; no one-shot flags, session or login is synthesized.
    return _HEADER.pack(_REQUEST_BYTES, 1, 1, seq, 0, 1, 0) + _READ_BODY.pack(index, data_bytes)


def parse_read_request(frame: bytes, *, profile: str) -> dict:
    """Validate a complete ordinary read request against an explicit profile.

    Args:
        frame: Immutable complete 18-byte request, without trailing USB data.
        profile: Required profile defining the exact index and byte count.
    Returns:
        Header metadata plus profile, index_u32 and requested_data_bytes.
    Raises:
        ProtocolError: Length, version, selector, operation, flags, reserved,
            index or data count is outside this deliberately narrow subset.
    Side effects: None.  Successful parsing does not prove device compatibility.
    """
    index, data_bytes = _profile(profile)
    header = _header(frame, max_bytes=_REQUEST_BYTES)
    if len(frame) != _REQUEST_BYTES:
        raise ProtocolError("E_LENGTH", "read request must contain both complete DWORD fields")
    if header["operation"] != 1:
        raise ProtocolError("E_OPERATION", "only read operation 1 is supported")
    if header["reserved_u16"] or header["flags_u8"]:
        raise ProtocolError("E_REQUEST_FLAGS", "only ordinary zero-reserved/zero-flag requests are supported")
    actual_index, actual_count = _READ_BODY.unpack_from(frame, _HEADER_BYTES)
    if (actual_index, actual_count) != (index, data_bytes):
        raise ProtocolError("E_PROFILE_FIELDS", "index or requested byte count differs from the selected profile")
    return {
        **header,
        "profile": profile,
        "index_u32": index,
        "requested_data_bytes": data_bytes,
    }


def parse_read_response(frame: bytes, *, request: bytes, profile: str) -> dict:
    """Associate a complete response with its validated request and decode raw data.

    Args:
        frame: Immutable complete response.  No chunk concatenation or retry.
        request: Original immutable request, revalidated against ``profile``.
        profile: Required profile; vendor and Simulator cannot be interchanged.
    Returns:
        A dict with ``ok``/``status`` (data or device_error), raw value or error,
        header metadata, and explicit static/unverified evidence scope.  A well
        formed error operation remains failed even when its numeric code is 0/1.
    Raises:
        ProtocolError: Malformed, uncorrelated or unsupported response, including
            operation 6 (ack/length), which must not become a runtime value.
    Side effects: None.  No RUN/STOP interpretation or device identity claim.
    """
    expected = parse_read_request(request, profile=profile)
    # The largest permitted result/error payload is one DWORD.  A malicious
    # declared size never causes allocation, reads, blocking or truncation here.
    header = _header(frame, max_bytes=_HEADER_BYTES + 4)
    if header["sequence"] != expected["sequence"]:
        raise ProtocolError("E_CORRELATION", "response sequence differs from the request")

    # Vendor RVA 0x28610 checks version, selector and sequence, not flags or
    # reserved bytes.  Preserve those as opaque values; inventing an echo rule
    # would reject replies without static or live evidence for that rule.
    operation = header["operation"]
    if operation == 5:
        required = expected["requested_data_bytes"]
    elif operation == 0x0F:
        required = 4
    else:
        raise ProtocolError("E_OPERATION", "response is neither read data (5) nor a device error (15)")
    if len(frame) != _HEADER_BYTES + required:
        raise ProtocolError("E_DATA_LENGTH", "response payload length differs from its exact profile/operation")

    raw = frame[_HEADER_BYTES:]
    value = int.from_bytes(raw, "little", signed=False)
    # Vendor dd30 accepts only wire bytes 0..3 as EnumGCMState. Other bytes
    # produce enum=-1 and communication result=2 there. Do not label such a
    # value as a successful GCM observation. Simulator remains independent.
    if operation == 5 and profile == VENDOR_GCM_STATE and value not in (0, 1, 2, 3):
        raise ProtocolError("E_STATE_VALUE", "vendor GCM state is outside its statically accepted 0..3 range")
    result = {
        **header,
        "profile": profile,
        "index_u32": expected["index_u32"],
        "payload_hex": raw.hex(),
        "evidence_level": "static_contract_only",
        "device_verified": False,
        "state_semantics": "unknown",
    }
    if operation == 0x0F:
        return {**result, "ok": False, "status": "device_error", "error_code_u32": value}
    return {**result, "ok": True, "status": "data", "raw_value": value}
