"""Actual Modbus TCP wire tests against loopback only; never a PLC."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import ipaddress
import json
import logging
from pathlib import Path
import socket
import struct
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('easy521_d_control', Path(__file__).parents[1] / 'easy521_d_control.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def response(request, pdu, *, transaction=None, unit=None):
    tid = request['transaction_id'] if transaction is None else transaction
    device = request['unit_id'] if unit is None else unit
    return struct.pack('>HHHB', tid, 0, len(pdu) + 1, device) + pdu


def registers(request, words):
    return response(request, bytes([3, 2 * len(words)]) + struct.pack('>' + 'H' * len(words), *words))


class LoopbackServer:
    """Independent minimal MBAP server retaining every actual received request."""
    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self.connections = 0
        self.errors = []
        self.stop = threading.Event()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(4)
        self.listener.settimeout(0.03)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(2)
        self.listener.close()
        if self.thread.is_alive():
            raise AssertionError('loopback server did not stop')
        if self.errors:
            raise AssertionError(self.errors)

    def serve(self):
        try:
            while not self.stop.is_set():
                try:
                    connection, _ = self.listener.accept()
                except socket.timeout:
                    continue
                self.connections += 1
                with connection:
                    connection.settimeout(0.02)
                    buffer = b''
                    closed = False
                    while not self.stop.is_set() and not closed:
                        try:
                            data = connection.recv(4096)
                        except socket.timeout:
                            continue
                        except ConnectionResetError:
                            break
                        if not data:
                            break
                        buffer += data
                        while len(buffer) >= 7:
                            tid, protocol, length, unit = struct.unpack('>HHHB', buffer[:7])
                            if not 2 <= length <= 254 or protocol != 0:
                                raise AssertionError('invalid actual MBAP request')
                            if len(buffer) < 6 + length:
                                break
                            raw, buffer = buffer[:6 + length], buffer[6 + length:]
                            pdu = raw[7:]
                            request = {'transaction_id': tid, 'unit_id': unit, 'pdu': pdu,
                                       'function_code': pdu[0], 'connection': self.connections, 'raw': raw}
                            self.requests.append(request)
                            reply = self.handler(request)
                            close_after = isinstance(reply, tuple)
                            if close_after:
                                reply = reply[0]
                            if reply is not None:
                                connection.sendall(reply)
                            if close_after:
                                closed = True
                                break
        except Exception as exc:
            self.errors.append(repr(exc))


class TestEasy521DControl(unittest.TestCase):
    def setUp(self):
        original = socket.create_connection
        def loopback_only(address, *args, **kwargs):
            if not ipaddress.ip_address(address[0]).is_loopback:
                raise AssertionError('test attempted to access a non-loopback target')
            return original(address, *args, **kwargs)
        guard = patch('socket.create_connection', loopback_only)
        guard.start(); self.addCleanup(guard.stop)
        logger = logging.getLogger('pymodbus')
        previous = logger.level; logger.setLevel(logging.CRITICAL)
        self.addCleanup(logger.setLevel, previous)

    def call(self, action, server, **kwargs):
        return worker.perform(action, host='127.0.0.1', port=server.port, d=100,
                              timeout=0.08, **kwargs)

    def test_read_sends_actual_fc03_with_expected_address_quantity_and_unit(self):
        with LoopbackServer(lambda req: registers(req, [0, 65535])) as server:
            result = self.call('read', server, count=2, unit_id=7)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'read')
        self.assertEqual(result['values'], [0, 65535])
        self.assertEqual(server.connections, 1)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(server.requests[0]['pdu'], b'\x03\x00\x64\x00\x02')
        self.assertEqual(server.requests[0]['unit_id'], 7)
        self.assertEqual(result['write']['calls'], 0)
        self.assertEqual(result['assurance']['model_identity'], 'unverified')

    def test_write_ack_and_readback_share_one_connection_and_one_write(self):
        def handler(req):
            return response(req, req['pdu']) if req['function_code'] == 6 else registers(req, [123])
        with LoopbackServer(handler) as server:
            result = self.call('write', server, value=123, execute=True)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'readback')
        self.assertEqual(result['write']['status'], 'write-ack')
        self.assertTrue(result['readback']['attempted'])
        self.assertTrue(result['readback']['matches'])
        self.assertEqual(result['write']['packet_send_attempts'], 1)
        self.assertEqual(result['connection']['readback_socket'], 'same-as-write')
        self.assertEqual(server.connections, 1)
        self.assertEqual([r['function_code'] for r in server.requests], [6, 3])
        self.assertEqual(server.requests[0]['pdu'], b'\x06\x00\x64\x00\x7b')
        self.assertEqual(server.requests[1]['pdu'], b'\x03\x00\x64\x00\x01')
        self.assertTrue(all(r['connection'] == 1 for r in server.requests))
        self.assertTrue(all(value == 'unverified' for value in result['assurance'].values()))

    def test_differing_readback_is_unknown_without_rewriting(self):
        with LoopbackServer(lambda req: response(req, req['pdu']) if req['function_code'] == 6
                            else registers(req, [124])) as server:
            result = self.call('write', server, value=123, execute=True)
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['readback']['values'], [124])
        self.assertFalse(result['readback']['matches'])
        self.assertIn('cause is not established', result['reason'])
        self.assertEqual([r['function_code'] for r in server.requests], [6, 3])

    def test_write_timeout_is_unknown_and_server_receives_no_repeated_write(self):
        with LoopbackServer(lambda req: None) as server:
            result = self.call('write', server, value=123, execute=True)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['stage'], 'write-ack')
        self.assertEqual(result['write']['calls'], 1)
        self.assertEqual(result['write']['packet_send_attempts'], 1)
        self.assertFalse(result['readback']['attempted'])
        self.assertEqual([r['function_code'] for r in server.requests], [6])
        self.assertEqual(result['error']['type'], 'ModbusIOException')
        self.assertIn('message', result['error'])
        self.assertEqual(result['retries'], 0)

    def test_readback_timeout_keeps_write_ack_and_attempted_state(self):
        with LoopbackServer(lambda req: response(req, req['pdu']) if req['function_code'] == 6 else None) as server:
            result = self.call('write', server, value=123, execute=True)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['stage'], 'readback')
        self.assertEqual(result['write']['status'], 'write-ack')
        self.assertTrue(result['readback']['attempted'])
        self.assertEqual([r['function_code'] for r in server.requests], [6, 3])

    def test_modbus_exception_responses_keep_actual_exception_structure(self):
        for action in ('read', 'write'):
            with self.subTest(action=action):
                with LoopbackServer(lambda req: response(req, bytes([req['function_code'] | 0x80, 2]))) as server:
                    extra = {'value': 1, 'execute': True} if action == 'write' else {}
                    result = self.call(action, server, **extra)
                self.assertEqual(result['status'], 'unknown' if action == 'write' else 'failed')
                record = result['write' if action == 'write' else 'read']['response']
                self.assertTrue(record['is_error'])
                self.assertEqual(record['exception_code'], 2)
                self.assertEqual(record['function_code'], 0x86 if action == 'write' else 0x83)
                self.assertEqual(len(server.requests), 1)

    def test_wrong_write_echo_or_function_is_unknown_and_does_not_trigger_readback(self):
        for pdu in (b'\x06\x00\x65\x00\x7b', b'\x06\x00\x64\x00\x7c', b'\x03\x02\x00\x7b'):
            with self.subTest(pdu=pdu):
                with LoopbackServer(lambda req: response(req, pdu)) as server:
                    result = self.call('write', server, value=123, execute=True)
                self.assertEqual(result['status'], 'unknown')
                self.assertIn('response', result['write'])
                self.assertFalse(result['readback']['attempted'])
                self.assertEqual([r['function_code'] for r in server.requests], [6])

    def test_wrong_transaction_or_unit_cannot_validate_a_write(self):
        for mismatch in ('transaction', 'unit'):
            with self.subTest(mismatch=mismatch):
                def handler(req):
                    changes = {'transaction': req['transaction_id'] + 1} if mismatch == 'transaction' else {'unit': 2}
                    return response(req, req['pdu'], **changes)
                with LoopbackServer(handler) as server:
                    result = self.call('write', server, value=123, execute=True)
                self.assertEqual(result['status'], 'unknown')
                self.assertFalse(result['readback']['attempted'])
                self.assertEqual(len(server.requests), 1)
                self.assertTrue(any(p['direction'] == 'received' for p in result['packets']))

    def test_readback_exception_and_wrong_count_keep_attempted_true(self):
        for failure in ('exception', 'count'):
            with self.subTest(failure=failure):
                def handler(req):
                    if req['function_code'] == 6:
                        return response(req, req['pdu'])
                    return response(req, b'\x83\x02') if failure == 'exception' else registers(req, [123, 456])
                with LoopbackServer(handler) as server:
                    result = self.call('write', server, value=123, execute=True)
                self.assertEqual(result['status'], 'unknown')
                self.assertTrue(result['readback']['attempted'])
                self.assertIn('response', result['readback'])
                self.assertEqual([r['function_code'] for r in server.requests], [6, 3])

    def test_connection_loss_after_ack_does_not_reconnect_for_readback(self):
        with LoopbackServer(lambda req: (response(req, req['pdu']), True)) as server:
            result = self.call('write', server, value=123, execute=True)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['write']['status'], 'write-ack')
        self.assertEqual(server.connections, 1)
        self.assertEqual(sum(r['function_code'] == 6 for r in server.requests), 1)
        self.assertFalse(result['automatic_reconnect'])

    def test_preview_does_not_create_a_client_or_resolve_dns(self):
        with patch.object(worker, '_make_client', side_effect=AssertionError('client in preview')), \
             patch('socket.getaddrinfo', side_effect=AssertionError('DNS in preview')):
            result = worker.perform('write', host='2001:db8::1', d=7999, value=65535)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'preview')
        self.assertFalse(result['connection']['attempted'])
        self.assertEqual(result['packets'], [])

    def test_invalid_arguments_are_refused_without_a_client(self):
        invalid = [dict(host='localhost'), dict(host=''), dict(host='not-an-ip'), dict(host='::1%eth0'),
                   dict(d=-1), dict(d=8000), dict(d=True), dict(d=7999, count=2), dict(count=126),
                   dict(count=0), dict(port=0), dict(port=65536), dict(unit_id=-1), dict(unit_id=256),
                   dict(timeout=0), dict(timeout=float('nan')), dict(timeout=float('inf')), dict(timeout=31),
                   dict(value=-1), dict(value=65536), dict(value=True), dict(count=2)]
        for change in invalid:
            with self.subTest(change=change), patch.object(worker, '_make_client', side_effect=AssertionError('network before validation')):
                args = dict(host='127.0.0.1', d=100, value=1, execute=True)
                args.update(change)
                result = worker.perform('write', **args)
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['stage'], 'arguments')
                self.assertFalse(result['write']['attempted'])

    def test_read_limits_include_d7999_and_max_125_words(self):
        for start, count in ((7999, 1), (7875, 125)):
            with self.subTest(start=start, count=count):
                with LoopbackServer(lambda req: registers(req, [9] * count)) as server:
                    result = worker.perform('read', host='127.0.0.1', port=server.port,
                                            d=start, count=count, timeout=0.08)
                self.assertTrue(result['ok'])
                self.assertEqual(result['values'], [9] * count)

    def test_cli_preview_is_one_json_and_unknown_options_are_structured(self):
        for argv, expected in ((['write', '--host', '127.0.0.1', '--d', '100', '--value', '123'], 0),
                               (['read', '--host', '127.0.0.1', '--d', '100', '--execute'], 2),
                               (['read', '--d', '100'], 2),
                               (['write', '--host', '127.0.0.1', '--d', '100', '--value', '-1'], 2)):
            with self.subTest(argv=argv), patch.object(worker, '_make_client', side_effect=AssertionError('CLI preview/invalid accessed network')):
                output = io.StringIO()
                with redirect_stdout(output):
                    code = worker.main(argv)
                self.assertEqual(code, expected)
                record = json.loads(output.getvalue())
                self.assertEqual(record['schema'], worker.SCHEMA)
                self.assertEqual(record['status'], 'preview' if code == 0 else 'failed')


if __name__ == '__main__':
    unittest.main()
