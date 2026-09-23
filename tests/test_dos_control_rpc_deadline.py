"""JSON-RPC socket waits must stop at the DOS controller deadline."""
import json
import socketserver
import threading
import time

import pytest

from guest import dos_control as client


class SlowRPCPeer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, delay):
        super().__init__(('127.0.0.1', 0), SlowRPCHandler)
        self.delay = delay


class SlowRPCHandler(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.readline())
        time.sleep(self.server.delay)
        try:
            self.wfile.write(json.dumps({
                'jsonrpc': '2.0', 'id': request['id'], 'result': {},
            }).encode() + b'\n')
        except (BrokenPipeError, ConnectionResetError):
            pass


def test_rpc_read_uses_remaining_absolute_deadline():
    with SlowRPCPeer(delay=0.7) as peer:
        thread = threading.Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        try:
            rpc = client.RPC(peer.server_address[1])
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                rpc.call('execution.pause', deadline=started + 0.1)
            elapsed = time.monotonic() - started
            assert elapsed < 0.6
        finally:
            peer.shutdown()
            thread.join(timeout=1)


def test_rpc_does_not_connect_after_deadline(monkeypatch):
    def unexpected_connect(*args, **kwargs):
        raise AssertionError('expired RPC deadline must refuse before connect')

    monkeypatch.setattr(client.socket, 'create_connection', unexpected_connect)
    with pytest.raises(TimeoutError, match='deadline expired'):
        client.RPC(1).call('memory.read', deadline=time.monotonic() - 1)


def test_rpc_recomputes_socket_timeout_after_connect_and_send(monkeypatch):
    clock = [0.0]
    deadline = 0.1
    connection_timeouts = []
    socket_timeouts = []

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def readline(self, limit):
            assert socket_timeouts[-1] == pytest.approx(0.01)
            clock[0] += 0.03
            raise client.socket.timeout('deadline elapsed during response read')

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def settimeout(self, timeout):
            socket_timeouts.append(timeout)

        def sendall(self, data):
            clock[0] += 0.03

        def makefile(self, mode):
            assert mode == 'rb'
            return FakeStream()

    def create_connection(address, timeout):
        connection_timeouts.append(timeout)
        clock[0] += 0.06
        return FakeSocket()

    monkeypatch.setattr(client.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(client.socket, 'create_connection', create_connection)
    with pytest.raises(TimeoutError):
        client.RPC(1).call('execution.pause', deadline=deadline)

    assert connection_timeouts == [pytest.approx(deadline)]
    assert socket_timeouts == [pytest.approx(0.04), pytest.approx(0.01)]
