#!/usr/bin/env python3
"""Read or write one raw Easy521 D register through Modbus TCP.

Examples (host must be an explicit IPv4/IPv6 literal):
  python tools/easy521_d_control.py read --host 192.0.2.10 --d 100 --count 2
  python tools/easy521_d_control.py write --host 192.0.2.10 --d 100 --value 7
  python tools/easy521_d_control.py write --host 192.0.2.10 --d 100 --value 7 --execute

Write defaults to offline preview. Execution uses one connection, one FC06 and
one FC03 readback, with retries=0 and no reconnect. A matching immediate readback
proves only the observed uint16 word, not model identity, CPU execution, durable
storage, named-variable binding, output control or ST application deployment.
Requires the existing pymodbus==3.16.1 for network operations. No DNS hostnames,
X/Y, CPU mode commands or uncalibrated 32-bit encodings are supported.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import ipaddress
import json
import math
import sys

SCHEMA = 'plcidf.easy521-d-control.v1'
MAX_TIMEOUT = 30.0


def _error(exc):
    result = {'type': type(exc).__name__, 'module': type(exc).__module__, 'message': str(exc),
              'args': [arg if type(arg) in (str, int, bool) or arg is None else repr(arg) for arg in exc.args]}
    for key in ('errno', 'strerror', 'fcode', 'function_code', 'transaction_id', 'dev_id', 'exception_code'):
        value = getattr(exc, key, None)
        if type(value) in (str, int, bool):
            result[key] = value
    return result


def _response(pdu):
    result = {'type': type(pdu).__name__, 'is_error': bool(pdu.isError())}
    for key in ('function_code', 'transaction_id', 'dev_id', 'address', 'exception_code'):
        value = getattr(pdu, key, None)
        if type(value) is int:
            result[key] = value
    registers = getattr(pdu, 'registers', None)
    if isinstance(registers, list):
        result['registers'] = list(registers)
    return result


def _arguments(action, host, port, unit_id, d, count, value, timeout, execute):
    if action not in ('read', 'write'):
        raise ValueError('action must be read or write')
    if not isinstance(host, str) or not host or '%' in host:
        raise ValueError('host must be an explicit unscoped IPv4/IPv6 literal')
    host = str(ipaddress.ip_address(host))
    for name, number, low, high in (('port', port, 1, 65535), ('unit-id', unit_id, 0, 255),
                                    ('D number', d, 0, 7999), ('count', count, 1, 125)):
        if type(number) is not int or not low <= number <= high:
            raise ValueError(f'{name} must be an integer in [{low}, {high}]')
    if d + count > 8000:
        raise ValueError('D range must not cross D7999')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError('timeout must be finite and in (0, 30] seconds per connection/request')
    if type(execute) is not bool:
        raise ValueError('execute must be an explicit boolean')
    if action == 'write':
        if count != 1 or type(value) is not int or not 0 <= value <= 65535:
            raise ValueError('write requires one uint16 value in [0, 65535]')
    elif value is not None or execute:
        raise ValueError('read does not accept a value or execute flag')
    return host


def _make_client(host, *, port, timeout, trace_packet):
    import pymodbus
    from pymodbus.client import ModbusTcpClient
    if pymodbus.__version__ != '3.16.1':
        raise ValueError('network operations require the pinned pymodbus==3.16.1')

    class OneConnectionClient(ModbusTcpClient):
        """Permit only one socket-connection attempt, including library calls."""
        _connection_attempted = False

        def connect(self):
            if self.socket is not None:
                return True
            if self._connection_attempted:
                return False
            self._connection_attempted = True
            return super().connect()

    return OneConnectionClient(host, port=port, timeout=timeout, retries=0,
                               reconnect_delay=0, reconnect_delay_max=0, trace_packet=trace_packet)


def perform(action, *, host, d, count=1, value=None, port=502, unit_id=1,
            timeout=3.0, execute=False, client_factory=None):
    """Return one JSON-compatible operation record; never retry a write.

    ``client_factory`` is a test seam. Preview returns before creating a client.
    Once the write call begins, every unsuccessful outcome is ``unknown``.
    """
    result = {'schema': SCHEMA, 'ok': False, 'status': 'failed', 'stage': 'arguments',
              'command': action, 'timestamp_utc': datetime.now(timezone.utc).isoformat(),
              'retries': 0, 'automatic_reconnect': False,
              'packet_capture_scope': 'library send attempts and received buffers; not proof of remote receipt',
              'connection': {'attempted': False, 'established': False},
              'write': {'status': 'not-started', 'attempted': False, 'calls': 0, 'packet_send_attempts': 0},
              'readback': {'status': 'not-started', 'attempted': False}, 'packets': [],
              'assurance': {'model_identity': 'unverified', 'cpu_execution': 'unverified',
                            'persistence': 'unverified', 'application_changed': 'unverified',
                            'named_variable_binding': 'unverified'}}
    client = None
    write_started = False

    def trace_packet(sending, packet):
        if sending and len(packet) >= 8 and packet[7] == 6:
            result['write']['packet_send_attempts'] += 1
        if len(result['packets']) < 32:
            result['packets'].append({'direction': 'sent' if sending else 'received',
                                      'hex': packet[:1024].hex(), 'bytes': len(packet),
                                      'truncated': len(packet) > 1024})
        else:
            result['packet_capture_truncated'] = True
        return packet

    def read_words(stage, quantity):
        result['stage'] = stage
        result[stage] = {**result.get(stage, {}), 'attempted': True, 'status': 'unknown'}
        response = client.read_holding_registers(d, count=quantity, device_id=unit_id,
                                               no_response_expected=False)
        record = _response(response)
        result[stage].update(status='failed', response=record)
        if record['is_error'] or record.get('function_code') != 3:
            raise ValueError('FC03 returned an exception or a different function code')
        registers = record.get('registers')
        if (not isinstance(registers, list) or len(registers) != quantity
                or any(type(word) is not int or not 0 <= word <= 65535 for word in registers)):
            raise ValueError('FC03 register count or uint16 values differ from the request')
        result[stage].update(status='read', values=registers)
        return registers

    try:
        host = _arguments(action, host, port, unit_id, d, count, value, timeout, execute)
        result.update(target={'host': host, 'port': port, 'unit_id': unit_id},
                      address={'area': 'D', 'number': d, 'raw_holding_register': d, 'count': count},
                      timeout_s=timeout, timeout_scope='per connection/request')
        if action == 'write':
            result['requested_value'] = value
            if not execute:
                result.update(ok=True, status='preview', stage='preview')
                return result
        result['stage'] = 'connect'
        client = (client_factory or _make_client)(host, port=port, timeout=timeout, trace_packet=trace_packet)
        result['connection']['attempted'] = True
        if not client.connect():
            raise ConnectionError('The single Modbus TCP connection attempt failed')
        result['connection']['established'] = True
        initial_socket = client.socket
        if action == 'read':
            registers = read_words('read', count)
            result.update(ok=True, status='read', values=registers)
            return result
        result['stage'] = 'write-ack'
        write_started = True
        result['write'].update(status='unknown', attempted=True, calls=1)
        response = client.write_register(d, value, device_id=unit_id, no_response_expected=False)
        record = _response(response)
        result['write']['response'] = record
        if (record['is_error'] or record.get('function_code') != 6
                or record.get('address') != d or record.get('registers') != [value]):
            raise ValueError('FC06 exception or write echo differs from the requested address/value')
        result['write']['status'] = 'write-ack'
        if client.socket is None or client.socket is not initial_socket:
            raise ConnectionError('The original connection is no longer available; readback will not reconnect')
        result['connection']['readback_socket'] = 'same-as-write'
        result['readback']['attempted'] = True
        registers = read_words('readback', 1)
        result['readback'].update(attempted=True, matches=registers == [value])
        if registers != [value]:
            result.update(status='unknown', reason='Readback differs. The PLC program or another writer could have changed the word; the cause is not established.')
            result['readback']['status'] = 'different'
            return result
        result['readback']['status'] = 'readback'
        result.update(ok=True, status='readback', value=registers[0])
        return result
    except (Exception, KeyboardInterrupt) as exc:
        result.update(ok=False, status='unknown' if write_started else 'failed', error=_error(exc))
        if isinstance(exc, KeyboardInterrupt):
            result['interrupted'] = True
        return result
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as exc:
                result['close_error'] = _error(exc)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def main(argv=None):
    parser = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('read', 'write'):
        command = sub.add_parser(action)
        command.add_argument('--host', required=True, help='Explicit numeric IPv4/IPv6 address; no DNS')
        command.add_argument('--port', type=int, default=502)
        command.add_argument('--unit-id', type=int, default=1)
        command.add_argument('--timeout', type=float, default=3.0, help='Seconds per connection/request, in (0, 30]')
        command.add_argument('--d', type=int, required=True, help='Raw D number 0..7999, not 40001 notation')
        if action == 'read':
            command.add_argument('--count', type=int, default=1, help='1..125 without crossing D7999')
        else:
            command.add_argument('--value', type=int, required=True, help='One unsigned 16-bit word')
            command.add_argument('--execute', action='store_true', help='Send one FC06 and read back once; default offline preview')
    try:
        args = vars(parser.parse_args(argv))
        action = args.pop('action')
        result = perform(action, **args)
    except ValueError as exc:
        result = {'schema': SCHEMA, 'ok': False, 'status': 'failed', 'stage': 'arguments', 'error': _error(exc)}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 130 if result.get('interrupted') else 0 if result['ok'] else 2 if result['stage'] == 'arguments' else 1


if __name__ == '__main__':
    sys.exit(main())
