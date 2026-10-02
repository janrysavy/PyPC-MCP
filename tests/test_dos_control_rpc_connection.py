"""Mailbox polling reuses a channel; uncertain writes are never replayed."""
import json
import socketserver
import threading
import time

import pytest
from guest.dos_control import RPC


class Peer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, bad_reply=None):
        super().__init__(('127.0.0.1', 0), Handler)
        self.connections = 0
        self.requests = []
        self.bad_reply = bad_reply
        self.lock = threading.Lock()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        with self.server.lock:
            self.server.connections += 1
        while line := self.rfile.readline():
            request = json.loads(line)
            with self.server.lock:
                self.server.requests.append(request)
                number = len(self.server.requests)
            if number == 1 and self.server.bad_reply == 'timeout':
                time.sleep(0.15)  # Write was applied, but the reply is late.
            reply = {'jsonrpc': '2.0', 'id': request['id'], 'result': {'calls': number}}
            if number == 1 and self.server.bad_reply == 'id':
                reply['id'] += 100
            try:
                self.wfile.write(json.dumps(reply).encode() + b'\n')
            except (BrokenPipeError, ConnectionResetError):
                return


@pytest.fixture
def peer_factory():
    peers = []

    def create(bad_reply=None):
        peer = Peer(bad_reply)
        thread = threading.Thread(target=peer.serve_forever, daemon=True)
        thread.start()
        peers.append((peer, thread))
        return peer

    yield create
    for peer, thread in peers:
        peer.shutdown()
        peer.server_close()
        thread.join(timeout=1)


def test_many_polls_use_one_connection_and_close_is_idempotent(peer_factory):
    peer = peer_factory()
    rpc = RPC(peer.server_address[1])
    try:
        for number in range(1, 501):
            assert rpc.call('memory.read', {'address': 0xD8000}) == {'calls': number}
        assert peer.connections == 1
        assert [r['id'] for r in peer.requests] == list(range(1, 501))
        rpc.close()
        rpc.close()
        assert rpc.call('memory.read') == {'calls': 501}
        assert peer.connections == 2
    finally:
        rpc.close()


@pytest.mark.parametrize('failure', ['id', 'timeout'])
def test_uncertain_write_closes_channel_without_replay(peer_factory, failure):
    peer = peer_factory(failure)
    rpc = RPC(peer.server_address[1])
    try:
        expected = TimeoutError if failure == 'timeout' else RuntimeError
        with pytest.raises(expected):
            rpc.call('memory.write', {'address': 0xD8000},
                     deadline=time.monotonic() + (0.03 if failure == 'timeout' else 2))
        assert rpc._socket is None and rpc._stream is None
        assert [r['method'] for r in peer.requests] == ['memory.write']
        assert rpc.call('memory.read') == {'calls': 2}
        assert peer.connections == 2
        assert [r['method'] for r in peer.requests] == ['memory.write', 'memory.read']
    finally:
        rpc.close()
