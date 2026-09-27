"""Fresh standard-library live DOS proof with pinned Python assemblers.

This validates the DOS execution pipeline only, not a Pascal reconstruction.
It refuses private compiler mounts and never uploads disks or tool binaries.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from guest.dos_session import dos_session, assembler_command
from guest.dos_control import BASE, DOSControl


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, data):
    staged = path.with_suffix('.pending')
    staged.write_text(json.dumps(data, indent=2) + '\n')
    staged.replace(path)


def assemble_probe(out, stem, exit_code):
    from pynasm import Assembler
    from pydasm import Decoder
    from pydasm.nasm_formatter import NasmFormatter, FormatOptions
    stdout, stderr = b'PYTHON_ONLY_OK\r\n', b'ERROR_CHANNEL\r\n'
    source = f"""bits 16
cpu 8086
org 100h
mov bx,1
mov dx,message_out
mov cx,{len(stdout)}
mov ah,40h
int 21h
mov bx,2
mov dx,message_err
mov cx,{len(stderr)}
mov ah,40h
int 21h
mov ax,{0x4c00|exit_code}
int 21h
message_out: db {','.join(str(x) for x in stdout)}
message_err: db {','.join(str(x) for x in stderr)}
"""
    path = out/(stem+'.asm')
    path.write_text(source)
    binary, listing = out/(stem+'.com'), out/(stem+'.lst')
    argv = [*assembler_command(), '-f', 'bin', str(path), '-o', str(binary), '-l', str(listing)]
    built = subprocess.run(argv, check=True, capture_output=True, timeout=30)
    data = binary.read_bytes()
    assembler = Assembler(optimize=9, compatibility='nasm3')
    assert assembler.assemble(source, filename=str(path)) == data
    coverage = bytearray(len(data))
    for row in assembler.listing:
        if row.size:
            assert row.file_offset is not None
            start, end = row.file_offset, row.file_offset + row.size
            assert row.data == data[start:end]
            assert not any(coverage[start:end])
            coverage[start:end] = b'\1'*row.size
    assert coverage == b'\1'*len(data)
    decoder, disassembly = Decoder(data), []
    expected = ['MOV', 'MOV', 'MOV', 'MOV', 'INT']*2 + ['MOV', 'INT']
    for mnemonic in expected:
        start = decoder.position
        instruction = decoder.decode_next()
        assert instruction.is_valid and instruction.is_complete
        assert instruction.mnemonic.name == mnemonic
        assert bytes(instruction.instruction_bytes) == data[start:decoder.position]
        disassembly.append({'offset': 0x100+start, 'bytes': bytes(instruction.instruction_bytes).hex(),
            'instruction': NasmFormatter().format_instruction(instruction, FormatOptions(ip=0x100+start))})
    assert data[decoder.position:] == stdout+stderr
    return binary, {'source_sha256': sha(path.read_bytes()), 'binary_sha256': sha(data),
        'binary_bytes': len(data), 'listing_sha256': sha(listing.read_bytes()),
        'listing_bytes_covered_exactly_once': sum(coverage), 'api_equals_cli': True,
        'argv': argv, 'assembler_stdout': built.stdout.decode(), 'assembler_stderr': built.stderr.decode(),
        'disassembly': disassembly}


def require_success(result):
    assert result['termination_type'] == 0, result
    assert result['exit_code'] == 7, result
    assert result['output_text'] == 'PYTHON_ONLY_OK\r\nERROR_CHANNEL\r\n', result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pynasm', type=Path, required=True)
    parser.add_argument('--pydasm', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not sys.flags.no_site or sys.flags.optimize:
        parser.error('run with python -S -B and without -O')
    if os.environ.get('PYPC_DOSTOOLS_MOUNT'):
        parser.error('this public probe refuses private compiler mounts')
    pynasm, pydasm = args.pynasm.resolve(), args.pydasm.resolve()
    if not (pynasm/'pynasm/cli.py').is_file() or not (pydasm/'src/pydasm/__init__.py').is_file():
        parser.error('supply the pinned source directories')
    out = args.out.resolve()
    if not out.is_relative_to(ROOT):
        parser.error('retain scratch outputs inside this repository')
    out.mkdir(parents=True, exist_ok=False)
    sys.path[:0] = [str(pynasm), str(pydasm/'src')]
    os.environ['PYTHONPATH'] = os.pathsep.join((str(ROOT), str(pynasm), str(pydasm/'src')))
    os.environ['PATH'] = ''
    os.environ['NASM_COMMAND'] = json.dumps(
        [sys.executable, '-S', '-B', '-m', 'pynasm', '--compatibility', 'nasm3', '-Ox'])
    state = {'status': 'WIP', 'scope': 'DOS_PIPELINE_ONLY', 'started': time.time(),
        'python': sys.version, 'no_site': bool(sys.flags.no_site), 'host_path': os.environ['PATH'],
        'compiler_execution': 'NOT_RUN', 'pascal_reconstruction': 'NOT_RUN',
        'source_sha256': {name: sha((ROOT/name).read_bytes()) for name in
            ('guest/dos_session.py', 'guest/dos_control.py', 'guest/dos_control.asm', 'main.py',
             'ci/probe_python_session.py')}}
    state['assembler_source_sha256'] = {
        path.relative_to(pynasm).as_posix(): sha(path.read_bytes())
        for path in sorted((pynasm/'pynasm').rglob('*.py'))}
    state['decoder_source_sha256'] = {
        path.relative_to(pydasm).as_posix(): sha(path.read_bytes())
        for path in sorted((pydasm/'src/pydasm').rglob('*.py'))}
    write_json(out/'RUN.json', state)
    try:
        positive, pos_info = assemble_probe(out, 'positive', 7)
        negative, neg_info = assemble_probe(out, 'wrongexit', 8)
        state['fresh_assembly'] = {'positive': pos_info, 'wrong_exit': neg_info}
        write_json(out/'RUN.json', state)
        guest = out/'guest'
        guest.mkdir()
        with dos_session(guest) as live:
            state['owned_guest_pid'] = live.process.pid
            state['emulator_argv'] = live.process.args
            write_json(out/'RUN.json', state)
            for path, target in ((positive, r'D:\WORK\POS.COM'), (negative, r'D:\WORK\NEG.COM')):
                live.worker.put(path, target)
                assert live.worker.read_file(target) == path.read_bytes()
            actual = live.execute(r'D:\WORK\POS.COM', output=r'D:\WORK\POS.LOG')
            require_success(actual)
            state['positive'] = actual
            wrong = live.execute(r'D:\WORK\NEG.COM', output=r'D:\WORK\NEG.LOG')
            try:
                require_success(wrong)
            except AssertionError:
                state['wrong_exit_control'] = {'rejected': True, 'actual': wrong}
            else:
                raise AssertionError('fresh wrong-exit control was accepted')
            local = out/'transfer.bin'
            local.write_bytes(bytes(range(256))*33+b'END')
            live.worker.mkdir(r'D:\FILES')
            live.worker.put(local, r'D:\FILES\FIRST.BIN')
            assert live.worker.read_file(r'D:\FILES\FIRST.BIN') == local.read_bytes()
            live.worker.rename(r'D:\FILES\FIRST.BIN', r'D:\FILES\SECOND.BIN')
            assert live.worker.read_file(r'D:\FILES\SECOND.BIN') == local.read_bytes()
            assert (guest/'drive/FILES/SECOND.BIN').read_bytes() == local.read_bytes()
            live.worker.delete(r'D:\FILES\SECOND.BIN')
            assert not (guest/'drive/FILES/SECOND.BIN').exists()
            state['file_roundtrip'] = {'bytes': local.stat().st_size, 'sha256': sha(local.read_bytes()),
                                      'rename_and_delete': True, 'host_mirror': True}
            try:
                live.execute(r'D:\ABSENT.EXE')
            except RuntimeError as error:
                assert 'status=1 error=2' in str(error), str(error)
                state['missing_program_control'] = {'rejected': True, 'error': str(error)}
            else:
                raise AssertionError('missing program accepted')
            try:
                DOSControl(live.rpc, timeout=0.2).exec(r'D:\WAITKEY.COM')
            except TimeoutError:
                assert live.rpc.read(BASE, 1) == b'\1'
            else:
                raise AssertionError('blocking keyboard program unexpectedly completed')
            live.type(' ')
            recovered = DOSControl(live.rpc, timeout=120).collect_exec()
            assert recovered == {'exit_code': 7, 'termination_type': 0}
            state['timeout_recovered'] = recovered
            require_success(live.execute(r'D:\WORK\POS.COM', output=r'D:\WORK\AGAIN.LOG'))
            state['worker_reusable'] = live.worker.ready()
            assert state['worker_reusable']
            write_json(out/'RUN.json', state)
        state['owned_guest_stopped'] = live.process.poll() is not None
        assert state['owned_guest_stopped']
        state['guest_report'] = json.loads((guest/'report.json').read_text())
        state['status'] = 'FINISHED'
    except BaseException as error:
        state.update(status='FAILED', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        state['ended'] = time.time()
        write_json(out/'RUN.json', state)
        print('PYTHON_SESSION_STATUS='+state['status'], flush=True)


if __name__ == '__main__':
    main()
