"""A capture failure must not erase an already acknowledged child status.

Library tests inject a failed file read; CLI tests use an actual subprocess and
TCP RUN1 peer. The peer does not emulate DOS or execute the child.
"""
from pathlib import Path
import struct
import sys

import pytest

from guest.dos_control import DOSCommandError, DOSControl

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_dos_control_cli_errors import ErrorPeer, run_with_peer  # noqa: E402
import json

OUTPUT = r'D:\WORK\RUN.LOG'


@pytest.mark.parametrize('collecting', [False, True])
@pytest.mark.parametrize('failure', [DOSCommandError('R', 1, 5),
                                    TimeoutError('output read timed out')])
def test_library_capture_error_retains_completed_result(monkeypatch, collecting, failure):
    worker = DOSControl(None)
    monkeypatch.setattr(worker, 'request', lambda *args: (0, 0, b'\x07\x00'))
    monkeypatch.setattr(worker, 'collect', lambda *args: (0, 0, b'\x07\x00'))

    def fail_read(path):
        assert path == OUTPUT
        raise failure

    monkeypatch.setattr(worker, 'read_file', fail_read)
    with pytest.raises(RuntimeError) as caught:
        if collecting:
            worker.collect_exec(OUTPUT)
        else:
            worker.exec(r'D:\PROGRAM.EXE', output=OUTPUT)
    assert caught.value.result == {
        'exit_code': 7, 'termination_type': 0, 'output_path': OUTPUT,
    }
    assert caught.value.__cause__ is failure


class OutputErrorPeer(ErrorPeer):
    def __init__(self, exit_code):
        super().__init__()
        self.exit_code = exit_code
        self.commands = []

    def dispatch(self, method, params):
        if method == 'execution.continue' and self.memory[0] == 1:
            command = chr(self.memory[1])
            self.commands.append(command)
            if command == 'X':
                struct.pack_into('<H', self.memory, 4, 2)
                self.memory[6] = 0
                self.memory[0x20:0x22] = bytes((self.exit_code, 0))
            elif command == 'R':
                struct.pack_into('<H', self.memory, 4, 0)
                self.memory[6] = 1
                struct.pack_into('<H', self.memory, 8, 5)
            else:
                raise AssertionError(command)
            self.memory[0] = 2
            return {}
        return super().dispatch(method, params)


@pytest.mark.parametrize('exit_code', [0, 7])
def test_cli_capture_error_is_failure_but_preserves_child_status(exit_code):
    with OutputErrorPeer(exit_code) as peer:
        completed = run_with_peer(peer, 'exec', r'D:\PROGRAM.EXE', '--output', OUTPUT)
    result = json.loads(completed.stdout)
    assert completed.stderr == ''
    assert completed.returncode != 0, 'lost capture must not be a successful host job'
    assert result['exit_code'] == exit_code
    assert result['termination_type'] == 0
    assert result['output_path'] == OUTPUT
    assert result['error']['kind'] == 'output_capture'
    assert result['error']['cause']['dos_error'] == 5
    assert peer.commands == ['X', 'R'], 'never rerun the completed DOS child'
    assert peer.memory[0] == 3
