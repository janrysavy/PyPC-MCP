"""Actual DOS rename/delete coverage for the host-directory FAT16 mirror."""

import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_dos_live import live_dos  # noqa: E402,F401

pytestmark = pytest.mark.skipif(os.environ.get('PYPC_LIVE_DOS') != '1',
                               reason='set PYPC_LIVE_DOS=1 to boot real DOS')


def test_live_guest_rename_delete_updates_host_mirror(live_dos):
    local = live_dos.directory / 'mirror.bin'
    payload = bytes(range(256)) * 17 + b'MIRROR-END'
    local.write_bytes(payload)
    live_dos.worker.mkdir(r'D:\MIRROR')
    live_dos.worker.put(local, r'D:\MIRROR\FIRST.BIN')
    host_directory = live_dos.directory / 'drive' / 'MIRROR'
    first = host_directory / 'FIRST.BIN'
    second = host_directory / 'SECOND.BIN'
    assert first.read_bytes() == payload

    live_dos.worker.rename(r'D:\MIRROR\FIRST.BIN',
                           r'D:\MIRROR\SECOND.BIN')
    assert not first.exists(), 'the host mirror retained the renamed source path'
    assert second.read_bytes() == payload
    assert live_dos.worker.read_file(r'D:\MIRROR\SECOND.BIN') == payload

    live_dos.worker.delete(r'D:\MIRROR\SECOND.BIN')
    assert not second.exists(), 'the host mirror retained a guest-deleted file'
    assert 'SECOND.BIN' not in {
        entry['name'] for entry in live_dos.worker.list(r'D:\MIRROR\*.*')
    }
    assert live_dos.worker.ready()
