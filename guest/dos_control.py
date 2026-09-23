"""Control DOSCTRL.COM through its PyPC D800:0000 shared-memory mailbox.

This is an experimental local client. Start DOSCTRL.COM from the DOS prompt,
then use this module while PyPC's debugger RPC is reachable on loopback.
The exec CLI prints the original DOS status as JSON and exits with the child's
code. Abnormal termination with a zero DOS code maps to host status 1; inspect
termination_type in the JSON to distinguish it from an ordinary child failure.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import socket
import struct
import time

BASE = 0xD8000
DATA = 0x20
MAX_DATA = 4096


class RPC:
    def __init__(self, port: int):
        self.port = port
        self.sequence = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self.sequence += 1
        request_id = self.sequence
        request = {'jsonrpc': '2.0', 'id': request_id,
                   'method': method, 'params': params or {}}
        with socket.create_connection(('127.0.0.1', self.port), 20) as sock:
            sock.settimeout(20)
            sock.sendall(json.dumps(request).encode('utf-8') + b'\n')
            with sock.makefile('rb') as stream:
                line = stream.readline(16 * 1024 * 1024 + 1)
        if not line.endswith(b'\n') or len(line) > 16 * 1024 * 1024:
            raise RuntimeError('incomplete or oversized PyPC reply')
        reply = json.loads(line)
        if reply.get('id') != request_id:
            raise RuntimeError('PyPC reply id mismatch')
        if 'error' in reply:
            raise RuntimeError(f'{method}: {reply["error"]}')
        return reply['result']

    def read(self, address: int, length: int) -> bytes:
        result = self.call('memory.read', {'address': address, 'length': length})
        data = base64.b64decode(result['data_base64'], validate=True)
        if hashlib.sha256(data).hexdigest() != result['sha256']:
            raise RuntimeError('PyPC memory read hash mismatch')
        return data

    def write(self, address: int, data: bytes) -> None:
        self.call('memory.write', {'address': address,
                                   'data_base64': base64.b64encode(data).decode('ascii')})


class DOSControl:
    def __init__(self, rpc: RPC, timeout: float = 120):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be finite and greater than zero')
        self.rpc = rpc
        self.timeout = timeout

    def ready(self) -> bool:
        header = self.rpc.read(BASE, 16)
        return header[10:14] == b'RUN1' and header[0] == 3

    def request(self, command: str, data: bytes = b'') -> tuple[int, int, bytes]:
        if len(command) != 1 or not command.isascii():
            raise ValueError('one ASCII command byte required')
        if len(data) > MAX_DATA:
            raise ValueError('request exceeds mailbox data capacity')
        header = self.rpc.read(BASE, 16)
        if header[10:14] != b'RUN1' or header[0] != 3:
            raise RuntimeError('DOS worker is absent or busy')
        self.rpc.call('execution.pause')
        try:
            # The CPU remains paused until both data and the command flag are set.
            if data:
                self.rpc.write(BASE + DATA, data)
            self.rpc.write(BASE, bytes((1, ord(command))) +
                           struct.pack('<H', len(data)) + bytes(6))
        finally:
            self.rpc.call('execution.continue')
        return self.collect(command)

    def collect(self, command: str) -> tuple[int, int, bytes]:
        """Wait for and acknowledge an existing request, without submitting it.

        This also works from a new client after a timeout or disconnect, as
        long as nobody has acknowledged that reply. RUN1 has no request IDs:
        callers must still serialize mailbox access and know which job owns it.
        A timeout does not cancel the DOS child or clear its mailbox.
        """
        if len(command) != 1 or not command.isascii():
            raise ValueError('one ASCII command byte required')
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('timeout must be finite and greater than zero')
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            header = self.rpc.read(BASE, 16)
            if header[10:14] != b'RUN1':
                raise RuntimeError('DOS worker is absent')
            if header[0] == 3:
                raise RuntimeError('no pending DOS worker reply to collect')
            if header[1] != ord(command):
                raise RuntimeError('pending DOS worker command does not match')
            if header[0] == 2:
                length = struct.unpack_from('<H', header, 4)[0]
                if length > MAX_DATA:
                    raise RuntimeError('DOS worker returned oversized data')
                result = self.rpc.read(BASE + DATA, length) if length else b''
                status, error = header[6], struct.unpack_from('<H', header, 8)[0]
                self.rpc.call('execution.pause')
                try:
                    self.rpc.write(BASE, b'\0')
                finally:
                    self.rpc.call('execution.continue')
                if command == 'Q':
                    return status, error, result
                while time.monotonic() < deadline:
                    if self.rpc.read(BASE, 1) == b'\x03':
                        break
                    time.sleep(0.05)
                else:
                    raise TimeoutError('DOS worker did not acknowledge response')
                return status, error, result
            if header[0] != 1:
                raise RuntimeError(f'unexpected DOS worker state: {header[0]}')
            time.sleep(0.1)
        raise TimeoutError(
            f'DOS worker did not complete {command!r}; the request was not '
            'cancelled. Collect its reply before submitting another command.')

    @staticmethod
    def _checked_reply(command: str, reply: tuple[int, int, bytes]) -> bytes:
        status, error, result = reply
        if status:
            raise RuntimeError(f'DOS {command}: status={status} error={error}')
        return result

    def _ok(self, command: str, data: bytes) -> bytes:
        return self._checked_reply(command, self.request(command, data))

    @staticmethod
    def _path(path: str) -> bytes:
        encoded = path.encode('ascii')
        if not encoded or b'\0' in encoded or len(encoded) > 126:
            raise ValueError('DOS path must contain 1..126 ASCII bytes without NUL')
        return encoded + b'\0'

    def list(self, pattern: str) -> list[dict]:
        entries = []
        status, error, data = self.request('L', self._path(pattern))
        while status == 0:
            if len(data) != 43:
                raise RuntimeError('DOS worker returned invalid DTA')
            entries.append({
                'name': data[30:43].split(b'\0', 1)[0].decode('cp437'),
                'attributes': data[21],
                'size': struct.unpack_from('<I', data, 26)[0],
                'time': struct.unpack_from('<H', data, 22)[0],
                'date': struct.unpack_from('<H', data, 24)[0],
            })
            status, error, data = self.request('N')
        if status != 2:
            raise RuntimeError(f'DOS list: status={status} error={error}')
        return entries

    def put(self, local: Path, guest: str) -> dict:
        path = self._path(guest)
        data = local.read_bytes()
        self._ok('C', path)
        step = MAX_DATA - len(path) - 128
        for start in range(0, len(data), step):
            piece = data[start:start + step]
            result = self._ok('W', path + piece)
            if len(result) != 2 or struct.unpack('<H', result)[0] != len(piece):
                raise RuntimeError('DOS worker reported a short append')
        return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def read_file(self, guest: str) -> bytes:
        path = self._path(guest)
        data = bytearray()
        while True:
            request = path + struct.pack('<IH', len(data), MAX_DATA)
            piece = self._ok('R', request)
            if not piece:
                break
            data.extend(piece)
        return bytes(data)

    def get(self, guest: str, local: Path) -> dict:
        data = self.read_file(guest)
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def cwd(self) -> str:
        result = self._ok('G', b'')
        if not result.endswith(b'\0'):
            raise RuntimeError('DOS worker returned invalid cwd')
        return result[:-1].decode('ascii')

    def chdir(self, path: str) -> None:
        self._ok('S', self._path(path))

    def mkdir(self, path: str) -> None:
        self._ok('M', self._path(path))

    def delete(self, path: str) -> None:
        self._ok('D', self._path(path))

    def rename(self, old: str, new: str) -> None:
        self._ok('V', self._path(old) + self._path(new))

    def quit(self) -> None:
        self._ok('Q', b'')

    def exec(self, program: str, tail: str = '', output: str | None = None) -> dict:
        encoded = tail.encode('ascii')
        if len(encoded) > 125 or b'\0' in encoded:
            raise ValueError('DOS command tail must be at most 125 ASCII bytes')
        out_path = self._path(output) if output else b'\0'
        result = self._ok('X', self._path(program) + encoded + b'\0' + out_path)
        return self._exec_response(result, output)

    def collect_exec(self, output: str | None = None) -> dict:
        """Collect a pending EXEC; output must be the original capture path."""
        # Validate optional metadata before acknowledging the child status.
        if output:
            self._path(output)
        result = self._checked_reply('X', self.collect('X'))
        return self._exec_response(result, output)

    def _exec_response(self, result: bytes, output: str | None) -> dict:
        if len(result) != 2:
            raise RuntimeError('DOS worker returned invalid child status')
        response = {'exit_code': result[0], 'termination_type': result[1]}
        if output:
            data = self.read_file(output)
            response.update(output_path=output, output_bytes=len(data),
                            output_sha256=hashlib.sha256(data).hexdigest(),
                            output_base64=base64.b64encode(data).decode('ascii'),
                            output_text=data.decode('cp437', errors='replace'))
        return response


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rpc-port', type=int, default=2301)
    parser.add_argument('--timeout', type=float, default=120)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('ready')
    sub.add_parser('cwd')
    sub.add_parser('quit')
    collecting = sub.add_parser('collect-exec',
                                help='collect a pending EXEC without rerunning it')
    collecting.add_argument('--output', help='the original DOS capture path, if any')
    listing = sub.add_parser('list')
    listing.add_argument('pattern')
    changing = sub.add_parser('chdir')
    changing.add_argument('path')
    making = sub.add_parser('mkdir')
    making.add_argument('path')
    deleting = sub.add_parser('delete')
    deleting.add_argument('path')
    renaming = sub.add_parser('rename')
    renaming.add_argument('old')
    renaming.add_argument('new')
    putting = sub.add_parser('put')
    putting.add_argument('local', type=Path)
    putting.add_argument('guest')
    getting = sub.add_parser('get')
    getting.add_argument('guest')
    getting.add_argument('local', type=Path)
    running = sub.add_parser('exec')
    running.add_argument('program')
    running.add_argument('tail', nargs='?', default='')
    running.add_argument('--output', help='DOS path for captured standard handles')
    args = parser.parse_args()
    worker = DOSControl(RPC(args.rpc_port), args.timeout)
    if args.command == 'ready':
        result = {'ready': worker.ready()}
    elif args.command == 'list':
        result = worker.list(args.pattern)
    elif args.command == 'cwd':
        result = {'cwd': worker.cwd()}
    elif args.command == 'chdir':
        worker.chdir(args.path)
        result = {'cwd': worker.cwd()}
    elif args.command == 'mkdir':
        worker.mkdir(args.path)
        result = {'created': args.path}
    elif args.command == 'delete':
        worker.delete(args.path)
        result = {'deleted': args.path}
    elif args.command == 'rename':
        worker.rename(args.old, args.new)
        result = {'old': args.old, 'new': args.new}
    elif args.command == 'quit':
        worker.quit()
        result = {'quit': True}
    elif args.command == 'put':
        result = worker.put(args.local, args.guest)
    elif args.command == 'get':
        result = worker.get(args.guest, args.local)
    elif args.command == 'collect-exec':
        result = worker.collect_exec(args.output)
    else:
        result = worker.exec(args.program, args.tail, args.output)
    print(json.dumps(result, indent=2))
    if args.command in ('exec', 'collect-exec'):
        # DOS termination type is independent of AL: never report abnormal
        # termination as a successful host build just because AL is zero.
        return result['exit_code'] or int(result['termination_type'] != 0)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
