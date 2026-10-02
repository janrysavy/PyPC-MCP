"""Standard-library real-DOS session used by both pytest and Python callers.

No package installation, shell, or native assembler is needed when NASM_COMMAND
is an explicit Python assembler argv. The original live boot and worker checks
remain mandatory. Only scratch copies are mounted.
"""
from __future__ import annotations

from contextlib import contextmanager
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

from guest.dos_control import BASE, DOSControl, RPC

ROOT = Path(__file__).resolve().parents[1]


def assembler_command():
    """Select an explicit argv without invoking a shell or falling back on error."""
    configured = os.environ.get('NASM_COMMAND')
    if configured is not None:
        try:
            command = json.loads(configured)
        except json.JSONDecodeError as error:
            raise ValueError('NASM_COMMAND must be a nonempty JSON string array') from error
        if (not isinstance(command, list) or not command
                or any(not isinstance(arg, str) or not arg.strip() or '\0' in arg
                       for arg in command)):
            raise ValueError('NASM_COMMAND must be a nonempty JSON string array')
        return command
    native = shutil.which('nasm')
    if not native:
        raise RuntimeError('configure NASM_COMMAND with a Python assembler argv or provide native NASM')
    return [native]


def build_worker(command, output, listing):
    """Build freshly; retain the actual argv and reject stale/partial products."""
    output, listing = Path(output), Path(listing)
    source = ROOT / 'guest/dos_control.asm'
    def aliases(a, b):
        return a.resolve() == b.resolve() or (a.exists() and b.exists() and a.samefile(b))
    if aliases(output, listing) or aliases(output, source) or aliases(listing, source):
        raise ValueError('worker, listing, and source must be different files')
    if (not isinstance(command, list) or not command
            or any(not isinstance(arg, str) or not arg.strip() or '\0' in arg
                   for arg in command)):
        raise ValueError('assembler command must be a nonempty string array')
    argv = [*command, '-f', 'bin', str(source), '-o', str(output), '-l', str(listing)]
    output.unlink(missing_ok=True)
    listing.unlink(missing_ok=True)
    try:
        result = subprocess.run(argv, check=True, capture_output=True, timeout=30)
        data = output.read_bytes()
        rows = listing.read_bytes()
        if not data or not rows:
            raise RuntimeError('assembler produced an empty worker or listing')
    except BaseException:
        output.unlink(missing_ok=True)
        listing.unlink(missing_ok=True)
        raise
    return {'argv': argv, 'source_sha256': digest(source),
            'binary_sha256': hashlib.sha256(data).hexdigest(), 'binary_bytes': len(data),
            'listing_sha256': hashlib.sha256(rows).hexdigest(),
            'stdout': result.stdout.decode('utf-8', errors='replace'),
            'stderr': result.stderr.decode('utf-8', errors='replace')}


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
                last_error = None
            except (OSError, RuntimeError) as error:
                last_error = error
            time.sleep(0.2)
        raise AssertionError(f'timed out waiting for {label}; last RPC error: {last_error}')

    def execute(self, program, tail='', output=r'D:\WORK\RUN.LOG'):
        result = self.worker.exec(program, tail, output)
        self.report['commands'].append({'program': program, 'tail': tail, **result})
        self.save()
        return result


@contextmanager
def dos_session(directory):
    """Boot a real scratch DOS guest; yield LiveDOS; always stop our process.

    The caller supplies an existing empty directory. NASM_COMMAND can name a
    Python assembler argv; otherwise native NASM must be present on PATH.
    Compiler mounts remain opt-in through PYPC_DOSTOOLS_MOUNT.
    """
    nasm = assembler_command()
    directory = Path(directory).resolve()
    if not directory.is_dir() or any(directory.iterdir()):
        raise ValueError('DOS session requires an existing empty scratch directory')
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
    build = build_worker(nasm, drive / 'DOSCTRL.COM', directory / 'DOSCTRL.lst')
    (drive / 'EXIT7.COM').write_bytes(com_probe(stdout=b'OUT\r\n', stderr=b'ERR\r\n'))
    (drive / 'EXIT0.COM').write_bytes(com_probe(0))
    (drive / 'WAITKEY.COM').write_bytes(com_probe(wait_key=True))
    rpc_port, telnet_port, vnc_port = free_ports()
    env = os.environ.copy()
    env.setdefault('ProgramData', r'C:\ProgramData')
    env.setdefault('ALLUSERSPROFILE', r'C:\ProgramData')
    report = {'boot_sha256': boot_hash, 'worker_sha256': digest(drive / 'DOSCTRL.COM'),
              'tool_mount_supplied': bool(mount), 'vga_post_seen': False,
              'commands': [], 'worker_build': build}
    with (directory / 'stdout.log').open('w') as out, (directory / 'stderr.log').open('w') as err:
        process = subprocess.Popen(
            [sys.executable, *(['-S'] if sys.flags.no_site else []), '-B', '-u', str(ROOT / 'main.py'), '--video', 'vga', '--dos-mailbox',
             '--host-dir', str(drive), '--rpc-port', str(rpc_port), '--telnet-port',
             str(telnet_port), '--vnc-port', str(vnc_port)], cwd=system, env=env,
            stdout=out, stderr=err)
        live = LiveDOS(RPC(rpc_port), process, directory, report)
        try:
            live.save()
            def at_prompt():
                screen = live.screen()
                report['last_screen'] = screen
                if re.search(r'Video\s+\[ VGA \]', screen):
                    report['vga_post_seen'] = True
                if re.search(r'(?m)^[A-Z]:(?:\\[^>\n]*)?>\s*$', screen):
                    return True
                lower = screen.lower()
                if any(t in lower for t in ('press any key', 'press the any key', 'strike any key',
                                             'enter new date', 'enter new time')):
                    raise AssertionError('unexpected interactive BIOS/DOS boot prompt: ' + screen)
                return False
            live.wait(at_prompt, 'DOS command prompt', seconds=180)
            assert report['vga_post_seen'], 'VGA POST identification was not observed'
            int10 = live.rpc.call('memory.read', {'address': 0x40, 'length': 4})
            assert int10['data_hex'] == '9c0000c0', 'VGA option ROM did not install INT 10h'
            report['int10_vector_hex'] = int10['data_hex']
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
            live.rpc.close()
            live.save()
            assert digest(ROOT / 'harddisk.img') == boot_hash, 'tracked boot disk was modified'
            print('LIVE_DOS_REPORT=' + json.dumps(report, sort_keys=True))
            print('LIVE_DOS_STDERR=' + (directory / 'stderr.log').read_text(errors='replace')[-4000:])


