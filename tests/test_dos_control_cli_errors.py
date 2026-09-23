"""CLI failures must be JSON, not Python tracebacks.

The peer models only the RUN1 transport. It does not execute DOS or a CPU.
These tests intentionally fail against the pre-fix client.
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

CLIENT = Path(__file__).resolve().parents[1] / 'guest' / 'dos_control.py'
BASE = 0xD8000


class ErrorPeer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, *, initial_state=3, complete=True):
        super().__init__(('127.0.0.1', 0), ErrorHandler)
        self.memory = bytearray(8192)
        self.memory[0] = initial_state
        self.memory[10:14] = b'RUN1'
        self.complete = complete
        self.writes = []

    def dispatch(self, method, params):
        if method == 'memory.read':
            offset = params['address'] - BASE
            data = bytes(self.memory[offset:offset + params['length']])
            return {'data_base64': base64.b64encode(data).decode('ascii'),
                    'sha256': hashlib.sha256(data).hexdigest()}
        if method == 'memory.write':
            offset = params['address'] - BASE
            data = base64.b64decode(params['data_base64'], validate=True)
            self.writes.append((params['address'], data))
            self.memory[offset:offset + len(data)] = data
            return {}
        if method == 'execution.pause':
            return {}
        if method == 'execution.continue':
            if self.memory[0] == 1 and self.complete:
                assert self.memory[1] == ord('X')
                struct.pack_into('<H', self.memory, 4, 0)
                self.memory[6] = 1
                struct.pack_into('<H', self.memory, 8, 2)
                self.memory[0] = 2
            elif self.memory[0] == 0:
                self.memory[0] = 3
            return {}
        raise AssertionError(f'unexpected RPC: {method}')


class ErrorHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.readline())
        try:
            result = self.server.dispatch(request['method'], request['params'])
            reply = {'jsonrpc': '2.0', 'id': request['id'], 'result': result}
        except Exception as exc:
            reply = {'jsonrpc': '2.0', 'id': request['id'],
                     'error': {'code': -32603, 'message': str(exc)}}
        self.wfile.write(json.dumps(reply).encode('utf-8') + b'\n')


def run_with_peer(peer, *arguments):
    thread = threading.Thread(target=peer.serve_forever, daemon=True)
    thread.start()
    try:
        return subprocess.run(
            [sys.executable, str(CLIENT), '--rpc-port',
             str(peer.server_address[1]), *arguments],
            capture_output=True, text=True, timeout=10,
        )
    finally:
        peer.shutdown()
        thread.join(timeout=5)


def test_dos_launch_error_is_structured_and_acknowledged():
    with ErrorPeer() as peer:
        completed = run_with_peer(peer, 'exec', r'D:\ABSENT.EXE')
    assert completed.returncode == 1
    assert completed.stderr == ''
    assert json.loads(completed.stdout) == {
        'error': {
            'kind': 'dos', 'command': 'X', 'status': 1, 'dos_error': 2,
            'message': 'DOS X: status=1 error=2',
        },
    }
    assert peer.memory[0] == 3, 'a DOS error reply must still be acknowledged'


def test_busy_worker_is_a_structured_controller_error_without_mutation():
    with ErrorPeer(initial_state=1) as peer:
        completed = run_with_peer(peer, 'exec', r'D:\ANY.EXE')
    assert completed.returncode == 1
    assert completed.stderr == ''
    assert json.loads(completed.stdout) == {
        'error': {'kind': 'controller',
                  'message': 'DOS worker is absent or busy'},
    }
    assert peer.writes == []


def test_invalid_tail_is_structured_before_any_connection():
    completed = subprocess.run(
        [sys.executable, str(CLIENT), '--rpc-port', '1', 'exec',
         r'D:\ANY.EXE', ' ' + 'A' * 125],
        capture_output=True, text=True, timeout=10,
    )
    assert completed.returncode == 2
    assert completed.stderr == ''
    assert json.loads(completed.stdout) == {
        'error': {'kind': 'invalid_request',
                  'message': 'DOS command tail must be at most 125 ASCII bytes'},
    }


def test_timeout_is_structured_and_does_not_acknowledge_or_cancel():
    with ErrorPeer(complete=False) as peer:
        completed = run_with_peer(
            peer, '--timeout', '0.2', 'exec', r'D:\WAIT.EXE')
    assert completed.returncode == 1
    assert completed.stderr == ''
    error = json.loads(completed.stdout)['error']
    assert error['kind'] == 'timeout'
    assert 'was not cancelled' in error['message']
    assert peer.memory[0] == 1
    assert peer.writes[-1][1][0] == 1
