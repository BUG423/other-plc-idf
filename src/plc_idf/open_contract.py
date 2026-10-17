"""Shared scalar signal/value contracts for the local Linux ST backend.

Names preserve their spelling in JSON, but IEC identity is case insensitive.
The contract does not assign physical I/O addresses or accept external C paths.
"""
from __future__ import annotations
from collections.abc import Mapping
import math
import re

SUPPORTED_TYPES = frozenset({"BOOL", "INT", "DINT", "UINT", "UDINT", "REAL"})
INTEGER_RANGES = {"INT": (-32768, 32767), "DINT": (-2147483648, 2147483647),
                  "UINT": (0, 65535), "UDINT": (0, 4294967295)}
REAL_MAX = 3.4028234663852886e38
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
_RESERVED = frozenset("""PROGRAM END_PROGRAM CONFIGURATION END_CONFIGURATION RESOURCE
END_RESOURCE TASK VAR VAR_INPUT VAR_OUTPUT VAR_IN_OUT VAR_GLOBAL VAR_EXTERNAL
END_VAR FUNCTION END_FUNCTION FUNCTION_BLOCK END_FUNCTION_BLOCK IF THEN ELSIF
ELSE END_IF CASE OF END_CASE FOR TO BY DO END_FOR WHILE END_WHILE REPEAT UNTIL
END_REPEAT RETURN EXIT TRUE FALSE AND OR XOR NOT MOD BOOL INT DINT UINT UDINT
REAL STRING TIME ARRAY STRUCT END_STRUCT AT RETAIN CONSTANT EN ENO""".split())


def validate_signals(signals):
    """Return canonical inputs/outputs mappings or raise ValueError.

    At most 64 scalar MAIN fields are accepted. Types normalize to uppercase;
    names keep their original spelling and sort case insensitively. Empty maps
    are valid for programs where only scan execution itself is observed.
    """
    if not isinstance(signals, Mapping) or set(signals) - {"inputs", "outputs"}:
        raise ValueError("signals must contain only inputs and outputs mappings")
    result, seen = {}, set()
    for direction in ("inputs", "outputs"):
        members = signals.get(direction, {})
        if not isinstance(members, Mapping):
            raise ValueError(f"signals.{direction} must be a name/type mapping")
        normalized = {}
        for name, type_name in members.items():
            if (not isinstance(name, str) or not _IDENTIFIER.fullmatch(name)
                    or "__" in name or name.endswith("_") or name.upper() in _RESERVED):
                raise ValueError("signal name must be an IEC identifier: " + repr(name))
            identity = name.casefold()
            if identity in seen:
                raise ValueError("duplicate or overlapping IEC signal: " + name)
            seen.add(identity)
            if not isinstance(type_name, str) or type_name.upper() not in SUPPORTED_TYPES:
                raise ValueError("unsupported scalar signal type: " + repr(type_name))
            normalized[name] = type_name.upper()
        result[direction] = dict(sorted(normalized.items(), key=lambda item: item[0].casefold()))
    if len(seen) > 64:
        raise ValueError("at most 64 input/output signals are supported")
    return result


def validate_value(value, type_name):
    """Return a normalized legal scalar; argument order is value, type_name.

    BOOL accepts bool only. Integer types reject bool and floating values.
    REAL accepts finite numeric values in binary32 range, returning float;
    conversion to the actual IEEE-754 binary32 happens in the generated runtime.
    """
    if not isinstance(type_name, str) or type_name.upper() not in SUPPORTED_TYPES:
        raise ValueError("unsupported scalar value type: " + repr(type_name))
    type_name = type_name.upper()
    if type_name == "BOOL":
        if type(value) is not bool:
            raise ValueError("BOOL values must be true or false")
        return value
    if type_name in INTEGER_RANGES:
        if type(value) is not int:
            raise ValueError(type_name + " values must be integers, excluding booleans")
        minimum, maximum = INTEGER_RANGES[type_name]
        if not minimum <= value <= maximum:
            raise ValueError(type_name + " value is outside its range")
        return value
    if type(value) not in (int, float):
        raise ValueError("REAL values must be finite numbers, excluding booleans")
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError("REAL value is outside binary32 range") from exc
    if not math.isfinite(converted) or abs(converted) > REAL_MAX:
        raise ValueError("REAL value must be finite and within binary32 range")
    return converted


def structural_st_text(source):
    """Blank comments and quoted literals for limited structural checks.

    This is not a parser: the unchanged source still goes through MatIEC. It
    handles nested (* *) comments, // comments, and IEC $ string escapes so
    words inside comments/strings cannot masquerade as a PROGRAM or RESOURCE.
    """
    result = list(source)
    index, length = 0, len(source)
    while index < length:
        if source.startswith("(*", index):
            begin, depth = index, 1
            index += 2
            while index < length and depth:
                if source.startswith("(*", index):
                    depth += 1
                    index += 2
                elif source.startswith("*)", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            for offset in range(begin, index):
                if result[offset] not in "\r\n":
                    result[offset] = " "
        elif source.startswith("//", index):
            begin = index
            while index < length and source[index] not in "\r\n":
                index += 1
            result[begin:index] = " " * (index - begin)
        elif source[index] in ("'", '"'):
            begin, quote = index, source[index]
            index += 1
            while index < length:
                if source[index] == "$":
                    index = min(length, index + 2)
                elif source[index] == quote:
                    index += 1
                    if index < length and source[index] == quote:
                        index += 1
                    else:
                        break
                else:
                    index += 1
            for offset in range(begin, index):
                if result[offset] not in "\r\n":
                    result[offset] = " "
        else:
            index += 1
    return "".join(result)
