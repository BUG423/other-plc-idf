"""Offline Easy521 application-file and GCM wire subset, not a firmware codec.

Evidence: research/firmware/26-native-application-protocol-2026-10-19. The actual
H530Communicate Upload/Download virtual execution chains select file protocol4;
the common frame constructor is RVA28050. USB chunk sizes 494/40960 are set by
RVA295d0, overriding the base protocol's 1000-byte defaults. Simulator evidence
only corroborates device-side behavior; it never establishes Easy521 support.

Only observed application basenames are allowed. Credential bytes are explicit
caller input: the first8 body bytes are copied from the caller's narrow password
by the vendor; no password, padding or login policy is guessed here. This module
has no I/O. File open-read changes transport route state but requests no file
creation/truncation/write. Download and SetGCM are deliberately distinct writers.
"""

from __future__ import annotations

import struct

HEADER = struct.Struct("<HBBHHBB")
READ_CHUNK_BYTES = 494
WRITE_CHUNK_BYTES = 40960
MAX_APPLICATION_BYTES = 8 * 1024 * 1024
# The exact remote basenames come from the real AutoShop file vector in report16.
# No directory traversal, firmware/system path, arbitrary command/index or alias
# is exposed. Their existence on a particular PLC is established only by reads.
APPLICATION_FILES = (
    "project.hcpp", "userprog.foid", "stprog.o", "stuserprog.o", "symbol.sym",
    "varsinfo.foid", "syscfg.foid", "ethercat.foid", "modbus.foid", "canlink.foid",
    "modcfg.foid", "ethip.foid", "EIPIns.foid", "ecttrans.foid", "dataLog.foid",
    "webserver.foid", "OpcUAServer.foid", "sftpserver.foid", "Ecam.foid",
)


class ProtocolError(ValueError):
    """Malformed or unsupported fixed application operation."""


def _integer(value, maximum, name):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ProtocolError(f"{name} must be an integer in 0..{maximum}")
    return value


def _file(name):
    if not isinstance(name, str) or name not in APPLICATION_FILES:
        raise ProtocolError("Select an exact application basename; arbitrary paths are unsupported")
    return name.encode("ascii") + b"\x00"


def _frame(selector, operation, sequence, payload):
    _integer(sequence, 65535, "sequence")
    if type(payload) is not bytes or len(payload) > 65525:
        raise ProtocolError("payload must be bounded immutable bytes")
    # RVA28050 zero-initializes reserved bytes6..7 and ordinary flag9. File/GCM
    # execution chains do not set the one-shot flag override used by other cmds.
    return HEADER.pack(10 + len(payload), 1, selector, sequence, 0, operation, 0) + payload


def build_file_open(*, remote_name, credential, sequence, direction="read", file_bytes=0):
    """Build file open-read(op1) or destructive open-write(op2), without I/O.

    Args: credential is exactly8 caller-supplied bytes; no authentication claim.
        file_bytes is zero for reading, explicit bounded length for writing.
    Returns: Frame containing credential8/chunk_u32/file_bytes_u32/name+NUL.
    Raises: ProtocolError on unsupported path/direction/credential/length.
    Side effects: None here; sending direction=write can create/truncate a file.
    Sources: upload1fa00+1fe62, download0d300+0d544; values are little-endian.
    """
    name = _file(remote_name)
    if type(credential) is not bytes or len(credential) != 8:
        raise ProtocolError("credential must be exactly8 explicitly supplied bytes")
    _integer(file_bytes, MAX_APPLICATION_BYTES, "file_bytes")
    if direction == "read" and file_bytes == 0:
        operation, chunk = 1, READ_CHUNK_BYTES
    elif direction == "write" and file_bytes > 0:
        operation, chunk = 2, WRITE_CHUNK_BYTES
    else:
        raise ProtocolError("Read open requires length0; write open requires explicit nonzero file length")
    return _frame(4, operation, sequence, credential + struct.pack("<II", chunk, file_bytes) + name)


def build_file_read(*, block, sequence):
    """Build one selector4/op6 read of the next 494-byte block; no I/O.

    Block is a zero-based sequence, not a byte offset. Source: RVA2823f..2830f.
    The open-read response determines how many blocks are needed. No retry exists.
    """
    return _frame(4, 6, sequence, struct.pack("<I", _integer(block, 65535, "block")))


def build_file_write(*, block, data, sequence):
    """Build destructive selector4/op5 block; caller owns the application transaction.

    data is immutable1..40960 bytes; block is zero-based. No padding, checksum,
    compression or extra zero byte is sent (RVA28110/281e4 pass actual body size).
    """
    if type(data) is not bytes or not 1 <= len(data) <= WRITE_CHUNK_BYTES:
        raise ProtocolError("data must contain1..40960 immutable bytes")
    return _frame(4, 5, sequence, struct.pack("<II", _integer(block, 65535, "block"), len(data)) + data)


def build_gcm_set(*, state, sequence):
    """Build selector1/op2/indexff000000/length1 and one explicit state byte.

    State0/1 has the vendor STOP/RUN mapping; raw2/raw3 is the application's
    before/after-download ordering, not a verified transactional commit guarantee.
    Source: SetGCMState50d0 -> CGCMStateCommand.dcb0 -> de00 -> c7d0.
    Sending this frame changes PLC state. This function itself does no I/O.
    """
    _integer(state, 3, "state")
    return _frame(1, 2, sequence, struct.pack("<II", 0xFF000000, 1) + bytes([state]))


def parse_request(frame):
    """Validate this module's exact allowlisted requests; no device/file I/O."""
    header = _header(frame, maximum=WRITE_CHUNK_BYTES + 18)
    if header["reserved"] or header["flags"]:
        raise ProtocolError("Only ordinary zero-reserved/zero-flag requests are supported")
    selector, operation = header["selector"], header["operation"]
    if selector == 1 and operation == 2:
        if len(frame) != 19 or frame[10:18] != struct.pack("<II", 0xFF000000, 1) or frame[18] > 3:
            raise ProtocolError("Only the exact single-byte GCM writer is supported")
        return {**header, "action": "gcm-set", "state": frame[18], "application_write": True}
    if selector != 4:
        raise ProtocolError("Only application file selector4 or exact GCM writer is supported")
    if operation in (1, 2):
        if not 28 <= len(frame) <= 128:
            raise ProtocolError("Invalid fixed file-open body size")
        chunk, length = struct.unpack_from("<II", frame, 18)
        try:
            remote_name = frame[26:-1].decode("ascii")
        except UnicodeError as error:
            raise ProtocolError("Filename is not ASCII") from error
        if frame[-1] != 0 or _file(remote_name) != frame[26:]:
            raise ProtocolError("Filename is not an exact allowlisted NUL-terminated basename")
        if operation == 1 and (chunk != READ_CHUNK_BYTES or length):
            raise ProtocolError("Read-open chunk/length differs from vendor USB profile")
        if operation == 2 and (chunk != WRITE_CHUNK_BYTES or not 1 <= length <= MAX_APPLICATION_BYTES):
            raise ProtocolError("Write-open chunk/length differs from vendor USB profile")
        return {**header, "action": "file-open-read" if operation == 1 else "file-open-write",
                "remote_name": remote_name, "file_bytes": length, "application_write": operation == 2}
    if operation == 6 and len(frame) == 14:
        return {**header, "action": "file-read", "block": struct.unpack_from("<I", frame, 10)[0],
                "application_write": False}
    if operation == 5 and len(frame) >= 19:
        block, length = struct.unpack_from("<II", frame, 10)
        if not 1 <= length <= WRITE_CHUNK_BYTES or length != len(frame) - 18:
            raise ProtocolError("Write block length differs from actual bounded data")
        return {**header, "action": "file-write", "block": block, "data_bytes": length,
                "application_write": True}
    raise ProtocolError("Unsupported application operation or malformed payload")


def _header(frame, *, maximum):
    if type(frame) is not bytes or not 10 <= len(frame) <= maximum:
        raise ProtocolError("Expected one complete bounded immutable frame")
    size, version, selector, sequence, reserved, operation, flags = HEADER.unpack_from(frame)
    if size != len(frame) or version != 1:
        raise ProtocolError("Frame length/version differs from fixed protocol")
    return {"sequence": sequence, "selector": selector, "operation": operation,
            "reserved": reserved, "flags": flags}


def parse_response(frame, *, request, expected_data_bytes=None):
    """Correlate response and decode file size/block or ACK; no I/O.

    Open/ACK/error replies are exactly14 bytes. Read block data has u32 block,
    u32 byte-count and1..494 actual bytes. Optional expected_data_bytes binds it
    to the known open size so short/trailing/duplicate blocks cannot pass.
    Unknown response reserved/flags are preserved, matching report15's common
    response checks; they never authorize additional requests or operations.
    """
    original = parse_request(request)
    header = _header(frame, maximum=READ_CHUNK_BYTES + 18)
    if any(header[name] != original[name] for name in ("selector", "sequence")):
        raise ProtocolError("Response selector/sequence does not correlate with request")
    if header["operation"] == 15:
        if len(frame) != 14:
            raise ProtocolError("Error response must contain one u32")
        return {**header, "ok": False, "error_code_u32": struct.unpack_from("<I", frame, 10)[0]}
    if original["action"] != "file-read":
        if header["operation"] != 6 or len(frame) != 14:
            raise ProtocolError("Open/write/GCM request requires exact op6/u32 acknowledgement")
        return {**header, "ok": True, "ack_u32": struct.unpack_from("<I", frame, 10)[0]}
    if header["operation"] != 5 or len(frame) < 19:
        raise ProtocolError("Read block requires op5 with block/count/data")
    block, length = struct.unpack_from("<II", frame, 10)
    if block != original["block"] or not 1 <= length <= READ_CHUNK_BYTES or len(frame) != 18 + length:
        raise ProtocolError("Read block number, length or actual payload differs")
    if expected_data_bytes is not None and length != expected_data_bytes:
        raise ProtocolError("Read block length does not match the opened file's remaining bytes")
    return {**header, "ok": True, "block": block, "data_hex": frame[18:].hex(), "data_bytes": length}
