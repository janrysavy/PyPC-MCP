"""Mailbox recovery tests. This peer does not execute DOS or emulate its CPU."""
import struct

import pytest

from guest import dos_control as client


class MailboxPeer:
    def __init__(self):
        self.memory = bytearray(8192)
        self.memory[0] = 3
        self.memory[10:14] = b'RUN1'
        self.submissions = 0
        self.writes = []
        self.complete_on_read = False
        self.clock = None
        self.advance_reply_header_to = None
        self.ack_delay = 0
        self.ack_ready_at = None
        self.never_ready_after_ack = False
        self.fail_ack_method = None
        self.operation_delays = {}
        self.deadlines = []
        self.fail_header_write_after_apply = False

    def _rpc_time(self, method, deadline):
        self.deadlines.append((method, deadline))
        if self.clock is None:
            return
        now = self.clock[0]
        delay = self.operation_delays.get(method, 0)
        if deadline is not None and now + delay > deadline:
            self.clock[0] = deadline
            raise TimeoutError(f'{method} exceeded its RPC deadline')
        self.clock[0] += delay

    def finish(self, data=b'\x07\x00', status=0, error=0):
        self.memory[client.DATA:client.DATA + len(data)] = data
        struct.pack_into('<H', self.memory, 4, len(data))
        self.memory[6] = status
        struct.pack_into('<H', self.memory, 8, error)
        self.memory[0] = 2

    def read(self, address, length, *, deadline=None):
        self._rpc_time('memory.read', deadline)
        if (self.ack_ready_at is not None and self.clock is not None
                and self.clock[0] >= self.ack_ready_at):
            self.memory[0] = 3
            self.ack_ready_at = None
        offset = address - client.BASE
        result = bytes(self.memory[offset:offset + length])
        if (length == 16 and result[0] == 2
                and self.advance_reply_header_to is not None):
            self.clock[0] = self.advance_reply_header_to
            self.advance_reply_header_to = None
        if self.complete_on_read and self.memory[0] == 1:
            self.complete_on_read = False
            self.finish()
        return result

    def write(self, address, data, *, deadline=None):
        self._rpc_time('memory.write', deadline)
        self.writes.append((address, data))
        offset = address - client.BASE
        self.memory[offset:offset + len(data)] = data
        if offset == 0 and data[0] == 1:
            self.submissions += 1
            if self.fail_header_write_after_apply:
                self.fail_header_write_after_apply = False
                raise TimeoutError('simulated lost request-write response')

    def call(self, method, params=None, *, deadline=None):
        self._rpc_time(method, deadline)
        assert method in ('execution.pause', 'execution.continue')
        if method == self.fail_ack_method:
            raise ConnectionError('simulated acknowledgement transport failure')
        if method == 'execution.continue' and self.memory[0] == 0:
            if not self.never_ready_after_ack:
                self.ack_ready_at = self.clock[0] + self.ack_delay
                if self.ack_delay == 0:
                    self.memory[0] = 3
        return {}


@pytest.fixture
def peer(monkeypatch):
    # Deterministic wall clock: no long sleeps or timing-sensitive assertions.
    now = [0.0]
    monkeypatch.setattr(client.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(client.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    result = MailboxPeer()
    result.clock = now
    return result


def leave_timed_out_exec(peer):
    worker = client.DOSControl(peer, timeout=0.25)
    with pytest.raises(TimeoutError):
        worker.exec(r'D:\JOB.EXE')
    assert peer.submissions == 1
    assert peer.memory[0] == 1
    assert not worker.ready()
    return worker


def test_collect_late_reply_then_submit_next_job_without_reexecution(peer):
    worker = leave_timed_out_exec(peer)
    peer.finish()
    with pytest.raises(RuntimeError, match='absent or busy'):
        worker.exec(r'D:\JOB.EXE')
    assert client.DOSControl(peer).collect_exec() == {'exit_code': 7, 'termination_type': 0}
    assert peer.submissions == 1, 'collect must never submit or rerun the child'
    assert worker.ready()
    peer.complete_on_read = True
    assert worker.exec(r'D:\NEXT.EXE')['exit_code'] == 7
    assert peer.submissions == 2


def test_collect_can_wait_for_still_running_job(peer):
    leave_timed_out_exec(peer)
    peer.complete_on_read = True
    assert client.DOSControl(peer).collect('X') == (0, 0, b'\x07\x00')
    assert peer.submissions == 1
    assert peer.memory[0] == 3


def test_collect_sleep_is_capped_by_remaining_125ms_deadline(peer):
    peer.memory[0] = 1
    peer.memory[1] = ord('X')
    with pytest.raises(TimeoutError, match='request was not cancelled'):
        client.DOSControl(peer, timeout=0.125).collect('X')
    assert peer.clock[0] == 0.125


def test_collect_timeout_preserves_job_for_another_attempt(peer):
    worker = leave_timed_out_exec(peer)
    before = bytes(peer.memory)
    with pytest.raises(TimeoutError):
        worker.collect('X')
    assert bytes(peer.memory) == before
    peer.finish()
    assert worker.collect_exec()['exit_code'] == 7
    assert peer.submissions == 1


def test_acknowledgement_wait_gets_its_own_budget(peer):
    peer.finish()
    peer.memory[1] = ord('X')
    # The reply arrives just before the execution deadline. The worker takes
    # another 50 ms to observe the acknowledgement, crossing that deadline.
    peer.advance_reply_header_to = 0.24
    peer.ack_delay = 0.05
    assert client.DOSControl(peer, timeout=0.25).collect('X') == (
        0, 0, b'\x07\x00')
    assert peer.memory[0] == 3


def test_acknowledgement_budget_covers_pause_and_mailbox_write(peer):
    peer.finish()
    peer.memory[1] = ord('X')
    peer.operation_delays['execution.pause'] = 0.14
    peer.operation_delays['memory.write'] = 0.14
    with pytest.raises(client.DOSReplyAcknowledgementError) as caught:
        client.DOSControl(peer, timeout=0.25).collect('X')
    assert caught.value.reply == (0, 0, b'\x07\x00')
    assert peer.clock[0] == 0.25
    assert ('execution.pause', 0.25) in peer.deadlines
    assert ('memory.write', 0.25) in peer.deadlines
    assert ('execution.continue', 1.25) in peer.deadlines


def test_dos_wait_rpc_calls_share_the_remaining_operation_deadline(peer):
    peer.complete_on_read = True
    peer.advance_reply_header_to = 0.24
    peer.ack_delay = 0.05
    assert client.DOSControl(peer, timeout=0.25).exec(r'D:\JOB.EXE') == {
        'exit_code': 7,
        'termination_type': 0,
    }
    assert all(deadline is not None for _, deadline in peer.deadlines)
    assert ('execution.continue', 0.25) in peer.deadlines
    assert ('execution.continue', 0.49) in peer.deadlines


def test_expired_pause_call_gets_a_bounded_resume_attempt(peer):
    peer.operation_delays['execution.pause'] = 0.3
    with pytest.raises(TimeoutError, match='execution.pause'):
        client.DOSControl(peer, timeout=0.25).exec(r'D:\JOB.EXE')
    assert peer.submissions == 0
    assert ('execution.pause', 0.25) in peer.deadlines
    assert ('execution.continue', 1.25) in peer.deadlines


def test_lost_request_publication_reply_warns_without_rerunning(peer, capsys):
    peer.fail_header_write_after_apply = True
    worker = client.DOSControl(peer, timeout=0.25)
    with pytest.raises(client.DOSRequestSubmissionError) as caught:
        worker.exec(r'D:\JOB.EXE', output=r'D:\JOB.LOG')

    assert caught.value.command == 'X'
    assert caught.value.result == {'output_path': r'D:\JOB.LOG'}
    assert peer.submissions == 1
    assert peer.memory[0] == 1
    assert 'Do not rerun it' in str(caught.value)
    assert client._print_cli_failure(caught.value) == 1
    response = client.json.loads(capsys.readouterr().out)
    assert response['output_path'] == r'D:\JOB.LOG'
    assert response['error']['kind'] == 'submission_uncertain'


def test_acknowledgement_timeout_retains_the_completed_raw_reply(peer):
    peer.finish()
    peer.memory[1] = ord('X')
    peer.never_ready_after_ack = True
    with pytest.raises(client.DOSReplyAcknowledgementError) as caught:
        client.DOSControl(peer, timeout=0.25).collect('X')
    assert caught.value.reply == (0, 0, b'\x07\x00')
    assert caught.value.as_error()['kind'] == 'acknowledgement'
    assert peer.memory[0] == 0


def test_collect_exec_acknowledgement_error_retains_child_status(peer):
    peer.finish()
    peer.memory[1] = ord('X')
    peer.never_ready_after_ack = True
    with pytest.raises(client.DOSReplyAcknowledgementError) as caught:
        client.DOSControl(peer, timeout=0.25).collect_exec(r'D:\JOB.LOG')
    assert caught.value.result == {
        'exit_code': 7,
        'termination_type': 0,
        'output_path': r'D:\JOB.LOG',
    }


def test_acknowledgement_transport_failure_retains_the_completed_raw_reply(peer):
    peer.finish()
    peer.memory[1] = ord('X')
    peer.fail_ack_method = 'execution.pause'
    with pytest.raises(client.DOSReplyAcknowledgementError) as caught:
        client.DOSControl(peer).collect('X')
    assert caught.value.reply == (0, 0, b'\x07\x00')
    assert peer.memory[0] == 2


@pytest.mark.parametrize('state,command,marker', [
    (3, 'X', b'RUN1'), (2, 'L', b'RUN1'), (2, 'X', b'BAD!'),
])
def test_collect_refuses_idle_wrong_command_and_absent_worker_without_writes(peer, state, command, marker):
    peer.finish()
    peer.memory[0] = state
    peer.memory[1] = ord(command)
    peer.memory[10:14] = marker
    with pytest.raises(RuntimeError):
        client.DOSControl(peer).collect_exec()
    assert not peer.writes


def test_collect_dos_exec_error_acknowledges_before_raising(peer):
    worker = leave_timed_out_exec(peer)
    peer.finish(b'', status=1, error=2)
    with pytest.raises(RuntimeError, match='status=1 error=2'):
        worker.collect_exec()
    assert worker.ready()
    assert peer.submissions == 1


def test_collect_checks_reply_command_again_while_waiting(peer):
    worker = leave_timed_out_exec(peer)
    peer.memory[1] = ord('L')
    writes = list(peer.writes)
    with pytest.raises(RuntimeError):
        worker.collect('X')
    assert peer.writes == writes


def test_collect_output_uses_same_exact_byte_result_as_exec(peer, monkeypatch):
    worker = leave_timed_out_exec(peer)
    peer.finish()
    output = b'failure\r\n\x82\x00'
    def read_file(path):
        assert path == r'D:\JOB.LOG'
        assert worker.ready(), 'acknowledge before issuing file operations'
        return output
    monkeypatch.setattr(worker, 'read_file', read_file)
    result = worker.collect_exec(r'D:\JOB.LOG')
    assert result['exit_code'] == 7
    assert result['output_bytes'] == len(output)
    assert result['output_base64'] == client.base64.b64encode(output).decode('ascii')
    assert result['output_sha256'] == client.hashlib.sha256(output).hexdigest()


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf'), -float('inf')])
def test_invalid_timeout_rejected_before_submitting_a_command(peer, timeout):
    with pytest.raises(ValueError, match='timeout'):
        client.DOSControl(peer, timeout)
    assert not peer.writes
