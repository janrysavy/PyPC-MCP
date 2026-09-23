"""Reject malformed RUN1 packets before DOS can act on stale mailbox bytes."""

import os
from pathlib import Path
import struct
import sys

import pytest

from guest.dos_control import BASE, DATA, MAX_DATA

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_dos_live import live_dos  # noqa: E402,F401

pytestmark = pytest.mark.skipif(os.environ.get('PYPC_LIVE_DOS') != '1',
                               reason='set PYPC_LIVE_DOS=1 to boot real DOS')

PATH = b'D:\\WORK\\BOUND.TXT\0'
PROGRAM = b'D:\\EXIT7.COM\0'


@pytest.mark.parametrize('command,payload,declared', [
    ('C', PATH, 0),
    ('C', PATH, len(PATH) - 1),
    ('D', PATH, 0),
    ('D', PATH, len(PATH) - 1),
    ('L', PATH, len(PATH) - 1),
    ('M', b'D:\\WORK\\NEWDIR\0', 0),
    ('S', b'D:\\WORK\0', 0),
    ('R', PATH + struct.pack('<IH', 0, 4), 0),
    ('R', PATH + struct.pack('<IH', 0, 4), len(PATH) + 5),
    ('V', PATH + b'D:\\WORK\\MOVED.TXT\0', len(PATH)),
    ('X', PROGRAM + b'\0\0', 0),
    ('X', PROGRAM + b'\0\0', len(PROGRAM)),
    ('X', PROGRAM + b'\0\0', len(PROGRAM) + 1),
    ('G', b'', MAX_DATA + 1),
    ('G', b'', 65535),
])
def test_live_rejects_truncated_or_oversized_request(live_dos, command, payload, declared):
    live = live_dos
    # Every case gets its own known file contents. The raw packet deliberately
    # leaves a complete old payload beyond the declared request boundary.
    local = live.directory / 'guard.txt'
    local.write_bytes(b'KEEP THIS FILE\r\n')
    live.worker.put(local, PATH[:-1].decode('ascii'))
    assert live.worker.ready()
    live.rpc.call('execution.pause')
    try:
        if payload:
            live.rpc.write(BASE + DATA, payload)
        live.rpc.write(BASE, bytes((1, ord(command))) +
                       struct.pack('<H', declared) + bytes(6))
    finally:
        live.rpc.call('execution.continue')
    reply = live.worker.collect(command)
    assert reply == (1, 0xFFFE, b''), (command, declared, reply)
    assert live.worker.ready()
    assert live.worker.read_file(PATH[:-1].decode('ascii')) == local.read_bytes()


def test_live_valid_request_after_rejections(live_dos):
    # Validation must not break ordinary child execution or captured output.
    result = live_dos.execute(r'D:\EXIT7.COM')
    assert (result['exit_code'], result['termination_type']) == (7, 0)
    assert result['output_text'] == 'OUT\r\nERR\r\n'
