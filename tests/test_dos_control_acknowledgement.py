"""Acknowledgement failures must retain the DOS result already observed."""
import json
import struct

from test_dos_control_cli_errors import ErrorPeer, run_with_peer


class AckNeverReadyPeer(ErrorPeer):
    def dispatch(self, method, params):
        if method == 'execution.continue' and self.memory[0] == 1:
            assert self.memory[1] == ord('X')
            struct.pack_into('<H', self.memory, 4, 2)
            self.memory[6] = 0
            self.memory[0x20:0x22] = b'\x07\x00'
            self.memory[0] = 2
            return {}
        if method == 'execution.continue' and self.memory[0] == 0:
            # Simulate an acknowledgement that the worker has not confirmed.
            return {}
        return super().dispatch(method, params)


def test_cli_preserves_child_status_when_acknowledgement_times_out():
    with AckNeverReadyPeer() as peer:
        completed = run_with_peer(
            peer, '--timeout', '0.2', 'exec', r'D:\PROGRAM.EXE',
            '--output', r'D:\PROGRAM.LOG')

    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert completed.stderr == ''
    assert result['exit_code'] == 7
    assert result['termination_type'] == 0
    assert result['output_path'] == r'D:\PROGRAM.LOG'
    assert result['error']['kind'] == 'acknowledgement'
    assert result['error']['reply'] == {
        'status': 0,
        'dos_error': 0,
        'data_base64': 'BwA=',
    }
    assert peer.memory[0] == 0
