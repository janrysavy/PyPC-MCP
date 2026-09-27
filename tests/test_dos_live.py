"""Opt-in real DOS integration tests, never a mocked worker.

PYPC_LIVE_DOS=1 python -m pytest -q -s tests/test_dos_live.py
Set PYPC_DOSTOOLS_MOUNT to a licensed local Mount tree to test its compilers.
Only scratch copies are mounted. Do not upload scratch disks or tool binaries.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import time

import pytest

from guest.dos_control import BASE, DOSControl, RPC

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get('PYPC_LIVE_DOS') != '1',
                               reason='set PYPC_LIVE_DOS=1 to boot real DOS')


# Re-export these names for callers that used the original test helper module.
from guest.dos_session import (LiveDOS, com_probe, digest, dos_session,
                               free_ports, key_events)


@pytest.fixture(scope='module')
def live_dos(tmp_path_factory):
    with dos_session(tmp_path_factory.mktemp('dos-live')) as live:
        yield live


def test_live_exit_capture_files_and_missing_program(live_dos):
    live = live_dos
    completed = subprocess.run(
        [sys.executable, str(ROOT / 'guest/dos_control.py'), '--rpc-port', str(live.rpc.port),
         'exec', r'D:\EXIT7.COM', '--output', r'D:\WORK\EXIT7.LOG'],
        capture_output=True, text=True, timeout=150)
    assert completed.stderr == ''
    result = json.loads(completed.stdout)
    assert completed.returncode == 7
    assert (result['exit_code'], result['termination_type']) == (7, 0)
    assert result['output_text'] == 'OUT\r\nERR\r\n'
    assert result['output_sha256'] == hashlib.sha256(b'OUT\r\nERR\r\n').hexdigest()
    live.report['cli_exit_capture'] = result
    local = live.directory / 'transfer.bin'
    local.write_bytes(bytes(range(256)) * 33 + b'END')
    live.worker.mkdir(r'D:\FILES')
    live.worker.put(local, r'D:\FILES\FIRST.BIN')
    assert live.worker.read_file(r'D:\FILES\FIRST.BIN') == local.read_bytes()
    live.worker.rename(r'D:\FILES\FIRST.BIN', r'D:\FILES\SECOND.BIN')
    assert live.worker.read_file(r'D:\FILES\SECOND.BIN') == local.read_bytes()
    assert 'SECOND.BIN' in {entry['name'] for entry in live.worker.list(r'D:\FILES\*.*')}
    live.worker.delete(r'D:\FILES\SECOND.BIN')
    assert 'SECOND.BIN' not in {entry['name'] for entry in live.worker.list(r'D:\FILES\*.*')}
    with pytest.raises(RuntimeError, match=r'status=1 error=2\b'):
        live.worker.exec(r'D:\ABSENT.EXE')
    assert live.worker.ready()
    assert live.execute(r'D:\EXIT0.COM')['exit_code'] == 0
    live.report['file_roundtrip_sha256'] = digest(local)
    live.save()


def test_live_timeout_collect_and_reuse(live_dos):
    live = live_dos
    with pytest.raises(TimeoutError):
        DOSControl(live.rpc, timeout=0.2).exec(r'D:\WAITKEY.COM')
    assert live.rpc.read(BASE, 1) == b'\x01'
    live.type(' ')
    result = DOSControl(live.rpc, timeout=120).collect_exec()
    assert result == {'exit_code': 7, 'termination_type': 0}
    assert live.worker.ready()
    assert live.execute(r'D:\EXIT0.COM')['exit_code'] == 0
    live.report['timeout_recovered'] = result
    live.save()


def test_live_optional_dostools_compilers(live_dos):
    if not os.environ.get('PYPC_DOSTOOLS_MOUNT'):
        pytest.skip('set PYPC_DOSTOOLS_MOUNT to run licensed compiler inputs')
    live = live_dos
    cases = [
        ('TPTEST', '.PAS', "program TPTEST; begin Writeln('TP6_OK'); Halt(7); end.\r\n",
         r'D:\TP6\TPC.EXE', r' D:\WORK\TPTEST.PAS', '.EXE', 'TP6_OK\r\n'),
        ('TCTEST', '.C', '#include <stdio.h>\r\nint main(void) { puts("TC_OK"); return 7; }\r\n',
         r'D:\TC201\BIN\TCC.EXE', r' -ID:\TC201\INCLUDE -LD:\TC201\LIB TCTEST.C', '.EXE', 'TC_OK\r\n'),
    ]
    assembly = ("CODESEG SEGMENT PARA PUBLIC 'CODE'\r\nASSUME CS:CODESEG\r\nORG 100h\r\n"
                'start: mov ax,4c07h\r\nint 21h\r\nCODESEG ENDS\r\nEND start\r\n')
    cases += [
        ('TASMTST', '.ASM', assembly, r'D:\TASM\BIN\TASM.EXE', ' TASMTST.ASM', '.COM', ''),
        ('MASMTST', '.ASM', assembly, r'D:\MASM50\BIN\MASM.EXE',
         ' MASMTST.ASM,MASMTST.OBJ,NUL,NUL;', '.COM', ''),
    ]
    for name, extension, source, compiler, tail, product_extension, expected in cases:
        local = live.directory / (name + extension)
        local.write_bytes(source.encode('ascii'))
        live.worker.put(local, 'D:\\WORK\\' + name + extension)
        built = live.execute(compiler, tail, 'D:\\WORK\\' + name + '.LOG')
        assert (built['exit_code'], built['termination_type']) == (0, 0), built
        if product_extension == '.COM':
            linked = live.execute(r'D:\TASM\LINK301\TLINK.EXE',
                                  f' /t {name}.OBJ,{name}.COM', r'D:\WORK\LINK.LOG')
            assert (linked['exit_code'], linked['termination_type']) == (0, 0), linked
        program = 'D:\\WORK\\' + name + product_extension
        artifact = live.worker.read_file(program)
        assert artifact, 'compiler produced an empty artifact'
        result = live.execute(program)
        assert (result['exit_code'], result['termination_type']) == (7, 0), result
        assert result['output_text'] == expected, result
        live.report.setdefault('compilers', {})[name] = {
            'source_sha256': digest(local), 'artifact_sha256': hashlib.sha256(artifact).hexdigest(),
            'artifact_bytes': len(artifact), 'exit_code': result['exit_code']}
        live.save()
