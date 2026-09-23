"""Exercise the real CLI/RPC/mailbox client against a deterministic worker peer.

The peer models DOSCTRL's handshake, not CPU or DOS execution. In particular,
these tests must not be cited as evidence that a compiler ran in the emulator.
"""
import base64
import hashlib
import json
from pathlib import Path
import socketserver
import struct
import subprocess
import sys
import threading

import pytest

CLIENT = Path(__file__).resolve().parents[1] / 'guest' / 'dos_control.py'
BASE = 0xD8000
DATA = 0x20


class WorkerPeer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, result):
        super().__init__(('127.0.0.1', 0), WorkerHandler)
        self.memory = bytearray(8192)
        self.memory[0] = 3
        self.memory[10:14] = b'RUN1'
        self.child_result = bytes(result)
        self.requests = []

    def dispatch(self, method, params):
        self.requests.append((method, params))
        if method == 'memory.read':
            offset = params['address'] - BASE
            data = bytes(self.memory[offset:offset + params['length']])
            return {'data_base64': base64.b64encode(data).decode('ascii'),
                    'sha256': hashlib.sha256(data).hexdigest()}
        if method == 'memory.write':
            offset = params['address'] - BASE
            data = base64.b64decode(params['data_base64'], validate=True)
            self.memory[offset:offset + len(data)] = data
            return {}
        if method == 'execution.pause':
            return {}
        if method == 'execution.continue':
            if self.memory[0] == 1:
                assert self.memory[1] == ord('X')
                self.memory[DATA:DATA + 2] = self.child_result
                struct.pack_into('<H', self.memory, 4, 2)
                self.memory[0] = 2
            elif self.memory[0] == 0:
                self.memory[0] = 3
            return {}
        raise AssertionError(f'unexpected RPC: {method}')


class WorkerHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.readline())
        try:
            result = self.server.dispatch(request['method'], request['params'])
            reply = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except Exception as exc:
            reply = {'jsonrpc': '2.0', 'id': request['id'],
                     'error': {'code': -32603, 'message': str(exc)}}
        self.wfile.write(json.dumps(reply).encode('utf-8') + b'\n')


@pytest.mark.parametrize(('exit_code', 'termination_type', 'host_code'), [
    (0, 0, 0), (7, 0, 7), (255, 0, 255),
    (0, 1, 1), (0, 2, 1), (0, 3, 1), (23, 2, 23),
])
def test_exec_cli_preserves_child_status(exit_code, termination_type, host_code):
    with WorkerPeer((exit_code, termination_type)) as peer:
        thread = threading.Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        try:
            completed = subprocess.run(
                [sys.executable, str(CLIENT), '--rpc-port',
                 str(peer.server_address[1]), 'exec', r'D:\FAIL.COM'],
                capture_output=True, text=True, timeout=10,
            )
        finally:
            peer.shutdown()
            thread.join(timeout=5)
        assert completed.stderr == ''
        assert json.loads(completed.stdout) == {
            'exit_code': exit_code, 'termination_type': termination_type,
        }
        assert completed.returncode == host_code
        assert peer.memory[0] == 3, 'the command must acknowledge the reply'


def test_ready_cli_is_still_a_successful_query():
    with WorkerPeer((0, 0)) as peer:
        thread = threading.Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        try:
            completed = subprocess.run(
                [sys.executable, str(CLIENT), '--rpc-port',
                 str(peer.server_address[1]), 'ready'],
                capture_output=True, text=True, timeout=10,
            )
        finally:
            peer.shutdown()
            thread.join(timeout=5)
        assert completed.returncode == 0
        assert json.loads(completed.stdout) == {'ready': True}


@pytest.mark.parametrize(('exit_code', 'termination_type', 'host_code'), [
    (0, 0, 0), (7, 0, 7), (0, 2, 1), (255, 0, 255),
])
def test_collect_exec_cli_does_not_submit_a_second_child(exit_code, termination_type, host_code):
    with WorkerPeer((exit_code, termination_type)) as peer:
        peer.memory[0] = 2
        peer.memory[1] = ord('X')
        struct.pack_into('<H', peer.memory, 4, 2)
        peer.memory[DATA:DATA + 2] = peer.child_result
        thread = threading.Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        try:
            completed = subprocess.run(
                [sys.executable, str(CLIENT), '--rpc-port',
                 str(peer.server_address[1]), 'collect-exec'],
                capture_output=True, text=True, timeout=10,
            )
        finally:
            peer.shutdown()
            thread.join(timeout=5)
        assert completed.stderr == ''
        assert json.loads(completed.stdout) == {
            'exit_code': exit_code, 'termination_type': termination_type,
        }
        assert completed.returncode == host_code
        assert peer.memory[0] == 3
        writes = [(p['address'], base64.b64decode(p['data_base64']))
                  for method, p in peer.requests if method == 'memory.write']
        assert writes == [(BASE, b'\0')], 'collection must only acknowledge'
