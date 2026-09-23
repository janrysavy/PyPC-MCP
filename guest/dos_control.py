"""Control DOSCTRL.COM through its PyPC D800:0000 shared-memory mailbox.

This is an experimental local client. Start DOSCTRL.COM from the DOS prompt,
then use this module while PyPC's debugger RPC is reachable on loopback.
The exec CLI prints the original DOS status as JSON and exits with the child's
code. Abnormal termination with a zero DOS code maps to host status 1; inspect
termination_type in the JSON to distinguish it from an ordinary child failure.
Expected DOS, timeout, validation, and controller failures are also emitted as
JSON without a Python traceback.
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
RESUME_CLEANUP_TIMEOUT = 1.0


class DOSClientInputError(ValueError):
    """Invalid caller input that should be reported without a traceback."""


class DOSCommandError(RuntimeError):
    """A RUN1 command completed with a DOS/worker error status."""

    def __init__(self, command: str, status: int, dos_error: int):
        self.command = command
        self.status = status
        self.dos_error = dos_error
        super().__init__(f'DOS {command}: status={status} error={dos_error}')

    def as_error(self) -> dict:
        return {
            'kind': 'dos',
            'command': self.command,
            'status': self.status,
            'dos_error': self.dos_error,
            'message': str(self),
        }


class DOSReplyAcknowledgementError(RuntimeError):
    """A reply was read, but the worker did not confirm its acknowledgement.

    `reply` preserves the exact RUN1 result. Do not rerun the command. The
    mailbox may still contain this reply, or the acknowledgement may have
    taken effect without its confirmation reaching the client.
    """

    def __init__(self, command: str, reply: tuple[int, int, bytes]):
        self.command = command
        self.reply = reply
        self.result: dict = {}
        super().__init__(
            f'DOS {command} reply was read, but acknowledgement was not '
            'confirmed; do not rerun the command')

    def as_error(self) -> dict:
        status, dos_error, payload = self.reply
        return {
            'kind': 'acknowledgement',
            'command': self.command,
            'message': str(self),
            'reply': {
                'status': status,
                'dos_error': dos_error,
                'data_base64': base64.b64encode(payload).decode('ascii'),
            },
        }


class DOSRequestSubmissionError(RuntimeError):
    """The request may have been published, but was not confirmed to the host."""

    def __init__(self, command: str):
        self.command = command
        self.result: dict = {}
        super().__init__(
            f'DOS {command} request publication was not confirmed; it may be '
            'running. Do not rerun it; collect the same command or inspect the '
            'worker before submitting another request.')

    def as_error(self) -> dict:
        return {
            'kind': 'submission_uncertain',
            'command': self.command,
            'message': str(self),
        }


class DOSOutputError(RuntimeError):
    """Capture retrieval failed after EXEC completed and was acknowledged.

    result retains the child's exit_code, termination_type and output_path.
    The chained exception describes the separate file/transport failure.
    Do not resubmit or recollect EXEC: that child has already completed.
    """

    def __init__(self, result: dict):
        self.result = dict(result)
        super().__init__(
            f'DOS child completed, but captured output could not be retrieved '
            f'from {result["output_path"]}; do not rerun or recollect EXEC')

    def as_error(self) -> dict:
        cause = self.__cause__
        if isinstance(cause, DOSCommandError):
            detail = cause.as_error()
        else:
            detail = {'kind': 'timeout' if isinstance(cause, TimeoutError) else 'controller',
                      'message': str(cause)}
        return {'kind': 'output_capture', 'message': str(self), 'cause': detail}


class RPC:
    def __init__(self, port: int):
        self.port = port
        self.sequence = 0
        self.timeout = 20.0

    def call(self, method: str, params: dict | None = None, *,
             deadline: float | None = None) -> dict:
        call_deadline = time.monotonic() + self.timeout
        if deadline is not None:
            call_deadline = min(call_deadline, deadline)

        def remaining_timeout() -> float:
            remaining = call_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('PyPC RPC deadline expired')
            return remaining

        self.sequence += 1
        request_id = self.sequence
        request = {'jsonrpc': '2.0', 'id': request_id,
                   'method': method, 'params': params or {}}
        with socket.create_connection(('127.0.0.1', self.port),
                                      remaining_timeout()) as sock:
            sock.settimeout(remaining_timeout())
            sock.sendall(json.dumps(request).encode('utf-8') + b'\n')
            sock.settimeout(remaining_timeout())
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

    def read(self, address: int, length: int, *,
             deadline: float | None = None) -> bytes:
        result = self.call('memory.read', {'address': address, 'length': length},
                           deadline=deadline)
        data = base64.b64decode(result['data_base64'], validate=True)
        if hashlib.sha256(data).hexdigest() != result['sha256']:
            raise RuntimeError('PyPC memory read hash mismatch')
        return data

    def write(self, address: int, data: bytes, *,
              deadline: float | None = None) -> None:
        self.call('memory.write', {'address': address,
                                   'data_base64': base64.b64encode(data).decode('ascii')},
                  deadline=deadline)


class DOSControl:
    def __init__(self, rpc: RPC, timeout: float = 120):
        if not math.isfinite(timeout) or timeout <= 0:
            raise DOSClientInputError('timeout must be finite and greater than zero')
        self.rpc = rpc
        self.timeout = timeout

    def _resume_after_pause(self, deadline: float) -> None:
        # A timed-out pause/write may have taken effect even when its reply
        # was lost. Give this safety cleanup a short bounded grace so the VM
        # is not left paused when the command's own deadline has expired.
        now = time.monotonic()
        if deadline <= now:
            deadline = now + RESUME_CLEANUP_TIMEOUT
        try:
            self.rpc.call('execution.continue', deadline=deadline)
        except TimeoutError:
            if deadline > time.monotonic():
                raise
            # The continue may have taken effect just as its reply timed out.
            # Retry once under the short safety-cleanup budget.
            self.rpc.call('execution.continue',
                          deadline=time.monotonic() + RESUME_CLEANUP_TIMEOUT)

    @staticmethod
    def _collect_timeout(command: str) -> TimeoutError:
        return TimeoutError(
            f'DOS worker did not return a collectable {command!r} reply; '
            'the request was not cancelled. Collect its reply before '
            'submitting another command.')

    def ready(self) -> bool:
        header = self.rpc.read(BASE, 16,
                               deadline=time.monotonic() + self.timeout)
        return header[10:14] == b'RUN1' and header[0] == 3

    def request(self, command: str, data: bytes = b'') -> tuple[int, int, bytes]:
        if len(command) != 1 or not command.isascii():
            raise DOSClientInputError('one ASCII command byte required')
        if len(data) > MAX_DATA:
            raise DOSClientInputError('request exceeds mailbox data capacity')
        deadline = time.monotonic() + self.timeout
        header = self.rpc.read(BASE, 16, deadline=deadline)
        if header[10:14] != b'RUN1' or header[0] != 3:
            raise RuntimeError('DOS worker is absent or busy')
        try:
            self.rpc.call('execution.pause', deadline=deadline)
            # The CPU remains paused until both data and the command flag are set.
            if data:
                self.rpc.write(BASE + DATA, data, deadline=deadline)
            try:
                self.rpc.write(BASE, bytes((1, ord(command))) +
                               struct.pack('<H', len(data)) + bytes(6),
                               deadline=deadline)
            except Exception as exc:
                # The guest may have accepted the mailbox write even when its
                # JSON-RPC acknowledgement timed out or the connection broke.
                raise DOSRequestSubmissionError(command) from exc
        except BaseException as exc:
            try:
                self._resume_after_pause(deadline)
            except Exception as resume_error:
                if hasattr(exc, 'add_note'):
                    exc.add_note(
                        f'emulator resume attempt failed: {resume_error}')
            raise
        else:
            try:
                self._resume_after_pause(deadline)
            except Exception as exc:
                raise DOSRequestSubmissionError(command) from exc
        return self.collect(command, deadline=deadline)

    def collect(self, command: str, *,
                deadline: float | None = None) -> tuple[int, int, bytes]:
        """Wait for and acknowledge an existing request, without submitting it.

        This also works from a new client after a timeout or disconnect, as
        long as nobody has acknowledged that reply. RUN1 has no request IDs:
        callers must still serialize mailbox access and know which job owns it.
        A timeout does not cancel the DOS child or clear its mailbox.
        """
        if len(command) != 1 or not command.isascii():
            raise DOSClientInputError('one ASCII command byte required')
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise DOSClientInputError('timeout must be finite and greater than zero')
        if deadline is None:
            deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                header = self.rpc.read(BASE, 16, deadline=deadline)
            except TimeoutError as exc:
                raise self._collect_timeout(command) from exc
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
                try:
                    result = (self.rpc.read(BASE + DATA, length, deadline=deadline)
                              if length else b'')
                except TimeoutError as exc:
                    raise self._collect_timeout(command) from exc
                status, error = header[6], struct.unpack_from('<H', header, 8)[0]
                # Start the acknowledgement budget as soon as the exact reply
                # is in hand. Its RPC calls and readiness poll share one end.
                ack_deadline = time.monotonic() + self.timeout
                try:
                    try:
                        self.rpc.call('execution.pause', deadline=ack_deadline)
                        self.rpc.write(BASE, b'\0', deadline=ack_deadline)
                    finally:
                        self._resume_after_pause(ack_deadline)
                    if command == 'Q':
                        return status, error, result
                    while True:
                        if self.rpc.read(BASE, 1, deadline=ack_deadline) == b'\x03':
                            break
                        remaining = ack_deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError(
                                'DOS worker did not acknowledge response')
                        time.sleep(min(0.05, remaining))
                except Exception as exc:
                    raise DOSReplyAcknowledgementError(
                        command, (status, error, result)) from exc
                return status, error, result
            if header[0] != 1:
                raise RuntimeError(f'unexpected DOS worker state: {header[0]}')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.1, remaining))
        raise self._collect_timeout(command)

    @staticmethod
    def _checked_reply(command: str, reply: tuple[int, int, bytes]) -> bytes:
        status, error, result = reply
        if status:
            raise DOSCommandError(command, status, error)
        return result

    def _ok(self, command: str, data: bytes) -> bytes:
        return self._checked_reply(command, self.request(command, data))

    @staticmethod
    def _path(path: str) -> bytes:
        try:
            encoded = path.encode('ascii')
        except UnicodeEncodeError as exc:
            raise DOSClientInputError(
                'DOS path must contain 1..126 ASCII bytes without NUL') from exc
        if not encoded or b'\0' in encoded or len(encoded) > 126:
            raise DOSClientInputError(
                'DOS path must contain 1..126 ASCII bytes without NUL')
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
            raise DOSCommandError('L', status, error)
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
        try:
            encoded = tail.encode('ascii')
        except UnicodeEncodeError as exc:
            raise DOSClientInputError(
                'DOS command tail must be at most 125 ASCII bytes') from exc
        if len(encoded) > 125 or b'\0' in encoded:
            raise DOSClientInputError(
                'DOS command tail must be at most 125 ASCII bytes')
        out_path = self._path(output) if output else b'\0'
        try:
            reply = self.request(
                'X', self._path(program) + encoded + b'\0' + out_path)
        except DOSRequestSubmissionError as exc:
            if output:
                exc.result = {'output_path': output}
            raise
        except DOSReplyAcknowledgementError as exc:
            exc.result = self._exec_acknowledgement_result(exc.reply, output)
            raise
        result = self._checked_reply('X', reply)
        return self._exec_response(result, output)

    def collect_exec(self, output: str | None = None) -> dict:
        """Collect a pending EXEC; output must be the original capture path."""
        # Validate optional metadata before acknowledging the child status.
        if output:
            self._path(output)
        try:
            reply = self.collect('X')
        except DOSReplyAcknowledgementError as exc:
            exc.result = self._exec_acknowledgement_result(exc.reply, output)
            raise
        result = self._checked_reply('X', reply)
        return self._exec_response(result, output)

    @staticmethod
    def _exec_acknowledgement_result(
            reply: tuple[int, int, bytes], output: str | None) -> dict:
        status, dos_error, payload = reply
        if status == 0 and len(payload) == 2:
            result = {'exit_code': payload[0], 'termination_type': payload[1]}
        else:
            result = {'dos_status': status, 'dos_error': dos_error}
            if status == 0:
                result['reply_data_base64'] = base64.b64encode(payload).decode('ascii')
        if output:
            result['output_path'] = output
        return result

    def _exec_response(self, result: bytes, output: str | None) -> dict:
        if len(result) != 2:
            raise RuntimeError('DOS worker returned invalid child status')
        response = {'exit_code': result[0], 'termination_type': result[1]}
        if output:
            response['output_path'] = output
            try:
                data = self.read_file(output)
            except (RuntimeError, OSError, ValueError) as exc:
                # EXEC has already been acknowledged. Preserve its status even
                # when the separate capture read fails or remains pending.
                raise DOSOutputError(response) from exc
            response.update(output_bytes=len(data),
                            output_sha256=hashlib.sha256(data).hexdigest(),
                            output_base64=base64.b64encode(data).decode('ascii'),
                            output_text=data.decode('cp437', errors='replace'))
        return response


def _print_cli_failure(exc: Exception) -> int:
    result = {}
    if isinstance(exc, DOSRequestSubmissionError):
        result = exc.result
        error = exc.as_error()
        exit_code = 1
    elif isinstance(exc, DOSReplyAcknowledgementError):
        result = exc.result
        error = exc.as_error()
        exit_code = 1
    elif isinstance(exc, DOSOutputError):
        result = exc.result
        error = exc.as_error()
        exit_code = 1
    elif isinstance(exc, DOSCommandError):
        error = exc.as_error()
        exit_code = 1
    elif isinstance(exc, DOSClientInputError):
        error = {'kind': 'invalid_request', 'message': str(exc)}
        exit_code = 2
    elif isinstance(exc, TimeoutError):
        error = {'kind': 'timeout', 'message': str(exc)}
        exit_code = 1
    else:
        error = {'kind': 'controller', 'message': str(exc)}
        exit_code = 1
    print(json.dumps({**result, 'error': error}, indent=2))
    return exit_code


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
    try:
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
    except (DOSCommandError, DOSClientInputError, TimeoutError,
            RuntimeError, OSError, ValueError) as exc:
        return _print_cli_failure(exc)
    print(json.dumps(result, indent=2))
    if args.command in ('exec', 'collect-exec'):
        # DOS termination type is independent of AL: never report abnormal
        # termination as a successful host build just because AL is zero.
        return result['exit_code'] or int(result['termination_type'] != 0)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
