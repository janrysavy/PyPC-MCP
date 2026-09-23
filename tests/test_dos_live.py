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


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def com_probe(exit_code=7, wait_key=False, stdout=b'', stderr=b''):
    """Generate only 8086 instructions, with COM-relative message pointers."""
    code = bytearray(b'\x31\xc0\xcd\x16' if wait_key else b'')
    strings = []
    for handle, text in ((1, stdout), (2, stderr)):
        if not text:
            continue
        code.extend(b'\xbb' + struct.pack('<H', handle) + b'\xba')
        patch = len(code)
        code.extend(b'\0\0\xb9' + struct.pack('<H', len(text)) + b'\xb4\x40\xcd\x21')
        strings.append((patch, text))
    code.extend(b'\xb8' + struct.pack('<H', 0x4C00 | exit_code) + b'\xcd\x21')
    for patch, text in strings:
        struct.pack_into('<H', code, patch, 0x100 + len(code))
        code.extend(text)
    return bytes(code)


def free_ports():
    sockets = []
    try:
        for _ in range(3):
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def key_events(text):
    keys = dict(zip('qwertyuiop', range(0x10, 0x1A)))
    keys.update(zip('asdfghjkl', range(0x1E, 0x27)))
    keys.update(zip('zxcvbnm', range(0x2C, 0x33)))
    keys.update({' ': 0x39, '\n': 0x1C, ':': 0x27})
    events = []
    for char in text.lower():
        if char == ':':
            events.append({'scan_code': 0x2A, 'pressed': True})
        events.extend([{'scan_code': keys[char], 'pressed': True},
                       {'scan_code': keys[char], 'pressed': False}])
        if char == ':':
            events.append({'scan_code': 0x2A, 'pressed': False})
    return events


class LiveDOS:
    def __init__(self, rpc, process, directory, report):
        self.rpc = rpc
        self.worker = DOSControl(rpc, timeout=120)
        self.process = process
        self.directory = directory
        self.report = report

    def save(self):
        (self.directory / 'report.json').write_text(json.dumps(self.report, indent=2) + '\n')

    def screen(self):
        return '\n'.join(self.rpc.call('video.text')['text'])

    def type(self, text):
        events = key_events(text)
        for start in range(0, len(events), 16):
            batch = events[start:start + 16]
            assert self.rpc.call('input.keyboard', {'events': batch})['accepted'] == len(batch)
            time.sleep(0.05)

    def wait(self, condition, label, seconds=120):
        deadline = time.monotonic() + seconds
        last_error = None
        while time.monotonic() < deadline:
            assert self.process.poll() is None, 'emulator process exited during ' + label
            try:
                if condition():
                    return
            except (OSError, RuntimeError) as error:
                last_error = error
            time.sleep(0.2)
        raise AssertionError(f'timed out waiting for {label}; last RPC error: {last_error}')

    def execute(self, program, tail='', output=r'D:\WORK\RUN.LOG'):
        result = self.worker.exec(program, tail, output)
        self.report['commands'].append({'program': program, 'tail': tail, **result})
        self.save()
        return result


@pytest.fixture(scope='module')
def live_dos(tmp_path_factory):
    nasm = shutil.which('nasm')
    assert nasm, 'the explicit live-DOS test requires NASM on PATH'
    directory = tmp_path_factory.mktemp('dos-live')
    system = directory / 'system'
    drive = directory / 'drive'
    system.mkdir()
    mount = os.environ.get('PYPC_DOSTOOLS_MOUNT')
    if mount:
        assert Path(mount).is_dir(), 'PYPC_DOSTOOLS_MOUNT must name a directory'
        shutil.copytree(mount, drive)
    else:
        drive.mkdir()
    (drive / 'WORK').mkdir(exist_ok=True)
    if mount:
        # TCC's documented linker lookup requires it in the current directory.
        shutil.copy2(drive / 'TC201/BIN/TLINK.EXE', drive / 'WORK/TLINK.EXE')
    boot_hash = digest(ROOT / 'harddisk.img')
    shutil.copy2(ROOT / 'harddisk.img', system / 'harddisk.img')
    shutil.copytree(ROOT / 'roms', system / 'roms')
    subprocess.run([nasm, '-f', 'bin', str(ROOT / 'guest/dos_control.asm'),
                    '-o', str(drive / 'DOSCTRL.COM')], check=True, timeout=30)
    (drive / 'EXIT7.COM').write_bytes(com_probe(stdout=b'OUT\r\n', stderr=b'ERR\r\n'))
    (drive / 'EXIT0.COM').write_bytes(com_probe(0))
    (drive / 'WAITKEY.COM').write_bytes(com_probe(wait_key=True))
    rpc_port, telnet_port, vnc_port = free_ports()
    env = os.environ.copy()
    env.setdefault('ProgramData', r'C:\ProgramData')
    env.setdefault('ALLUSERSPROFILE', r'C:\ProgramData')
    report = {'boot_sha256': boot_hash, 'worker_sha256': digest(drive / 'DOSCTRL.COM'),
              'tool_mount_supplied': bool(mount), 'commands': []}
    with (directory / 'stdout.log').open('w') as out, (directory / 'stderr.log').open('w') as err:
        process = subprocess.Popen(
            [sys.executable, '-u', str(ROOT / 'main.py'), '--video', 'vga', '--dos-mailbox',
             '--host-dir', str(drive), '--rpc-port', str(rpc_port), '--telnet-port',
             str(telnet_port), '--vnc-port', str(vnc_port)], cwd=system, env=env,
            stdout=out, stderr=err)
        live = LiveDOS(RPC(rpc_port), process, directory, report)
        live.save()
        try:
            last_key = [0.0]
            def at_prompt():
                screen = live.screen()
                report['last_screen'] = screen
                if re.search(r'(?m)^[A-Z]:(?:\\[^>\n]*)?>\s*$', screen):
                    return True
                lower = screen.lower()
                if any(t in lower for t in ('press any key', 'strike any key',
                                             'enter new date', 'enter new time')):
                    if time.monotonic() - last_key[0] > 2:
                        live.type('\n')
                        last_key[0] = time.monotonic()
                return False
            live.wait(at_prompt, 'DOS command prompt', seconds=180)
            live.type('d:\n')
            live.wait(lambda: re.search(r'(?m)^D:\\[^>\n]*>\s*$', live.screen()), 'D: prompt')
            live.type('dosctrl\n')
            live.wait(live.worker.ready, 'DOSCTRL RUN1 readiness')
            live.worker.chdir(r'D:\WORK')
            report['ready'] = True
            live.save()
            yield live
        finally:
            if process.poll() is None:
                try:
                    report['last_screen'] = live.screen()
                except (OSError, RuntimeError):
                    pass
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            live.save()
            assert digest(ROOT / 'harddisk.img') == boot_hash, 'tracked boot disk was modified'
            print('LIVE_DOS_REPORT=' + json.dumps(report, sort_keys=True))
            print('LIVE_DOS_STDERR=' + (directory / 'stderr.log').read_text(errors='replace')[-4000:])


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
