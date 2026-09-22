"""Minimal JSON-RPC 2.0 read/input channel for the emulator."""

import json
import queue
import socket
import threading


class _Request:
    def __init__(self, payload):
        self.payload = payload
        self.response = queue.Queue(maxsize=1)


class DebugServer:
    def __init__(self, port: int = 2301):
        self._requests = queue.Queue()
        self._pending = threading.Event()
        self._pending_lock = threading.Lock()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(('127.0.0.1', port))
        self._listener.listen(8)
        thread = threading.Thread(target=self._accept_loop, daemon=True)
        thread.name = 'debug-server'
        thread.start()
        print(f'JSON-RPC debug channel listening on 127.0.0.1:{port}')

    def _accept_loop(self):
        while True:
            try:
                client, _ = self._listener.accept()
                threading.Thread(
                    target=self._client_loop, args=(client,), daemon=True
                ).start()
            except OSError:
                return

    def _client_loop(self, client):
        try:
            with client:
                pending = b''
                while True:
                    data = client.recv(4096)
                    if not data:
                        return
                    pending += data
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        if not line.strip():
                            continue
                        response = self._submit(line)
                        if response is not None:
                            client.sendall(
                                json.dumps(response, separators=(',', ':')).encode() + b'\n'
                            )
        except OSError:
            return

    def _submit(self, line):
        try:
            payload = json.loads(line.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._error(None, -32700, 'invalid JSON')
        if not isinstance(payload, dict) or payload.get('jsonrpc') != '2.0' or \
                not isinstance(payload.get('method'), str):
            request_id = payload.get('id') if isinstance(payload, dict) else None
            return self._error(request_id, -32600, 'invalid JSON-RPC request')

        request = _Request(payload)
        with self._pending_lock:
            self._requests.put(request)
            self._pending.set()
        response = request.response.get()
        return None if 'id' not in payload else response

    @staticmethod
    def _error(request_id, code, message):
        return {'jsonrpc': '2.0', 'id': request_id,
                'error': {'code': code, 'message': message}}

    def has_pending(self):
        return self._pending.is_set()

    def process_pending(self, handler, maximum: int = 8):
        processed = 0
        while processed < maximum:
            try:
                request = self._requests.get_nowait()
            except queue.Empty:
                break
            request_id = request.payload.get('id')
            try:
                response = {'jsonrpc': '2.0', 'id': request_id,
                            'result': handler(request.payload)}
            except LookupError as error:
                response = self._error(request_id, -32601, str(error))
            except ValueError as error:
                response = self._error(request_id, -32602, str(error))
            except Exception as error:
                response = self._error(request_id, -32603, str(error))
            request.response.put(response)
            processed += 1
        with self._pending_lock:
            if self._requests.empty():
                self._pending.clear()
            else:
                self._pending.set()
