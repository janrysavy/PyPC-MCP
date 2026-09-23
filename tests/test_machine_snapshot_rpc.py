import ast
import threading
from pathlib import Path

import pytest
import debughardware
import debugtrace
import machinesnapshots
import videohistory
import vncserver
from machinecodec import capture_machine
from machinesnapshots import LockedDisplay, MachineSnapshots
from test_execution_rpc import HeadlessMachine
from test_machinecodec import machine, run


def rpc_machine(tmp_path):
    harness = HeadlessMachine()
    cpu = machine(tmp_path)
    display = LockedDisplay(cpu._devices[3])
    vnc = vncserver.VNCServer.__new__(vncserver.VNCServer)
    vnc._display = display
    vnc._frame_lock = threading.RLock()
    harness.namespace.update(p=cpu, b=cpu._b, state=cpu.GetState(), scr=cpu._devices[3],
                             debughardware=debughardware, debugtrace=debugtrace,
                             hardware_trace=debughardware.HardwareTraceRecorder(),
                             machine_snapshots=MachineSnapshots(cpu, display, vnc),
                             video_history=videohistory.VideoHistory(cpu.GetState().GetClock))
    # Bind production register methods to this real motherboard before testing.
    tree = ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text())
    body = next(n.body for n in tree.body if isinstance(n, ast.Try))
    node = next(n for n in body if isinstance(n, ast.Assign) and
                any(isinstance(t, ast.Name) and t.id == 'register_access' for t in n.targets))
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), harness.namespace)
    return harness, cpu, vnc


def test_production_rpc_restore_and_frame_invalidation(tmp_path):
    h, cpu, vnc = rpc_machine(tmp_path)
    path = str(tmp_path/'saved.pypc')
    before = capture_machine(cpu)
    export = h.rpc('machine.snapshot.export', path=path, expected_state_revision=0)
    expected_trace = run(cpu)
    expected = capture_machine(cpu)
    vnc._get_frame()
    previous_version = vnc._frame_cache_version
    h.rpc('trace.start', detail='short', instruction_count=2)
    result = h.rpc('machine.snapshot.import', path=path, disk_root=str(tmp_path/'restore'),
                   sha256=export['sha256'], expected_state_revision=0)
    assert result['state_revision'] == 1
    assert result['stop_reason']['kind'] == 'machine_snapshot_restored'
    assert h.control['paused']
    assert not h.control['trace_active']
    assert capture_machine(cpu) == before
    assert run(cpu) == expected_trace
    assert capture_machine(cpu) == expected
    vnc._get_frame()
    assert vnc._frame_cache_version != previous_version
    assert vnc._rect_since(previous_version, vnc._frame_cache_version, 640, 400) == (0,0,640,400)
    # Existing production register bindings must still address the live state.
    ax = cpu.GetState().GetAX()
    h.rpc('state.set_registers', expected_state_revision=1, expected={'ax':ax}, set={'ax':0x4321})
    assert cpu.GetState().GetAX() == 0x4321


@pytest.mark.parametrize('failure', ['running', 'revision', 'hash'])
def test_rpc_refusal_preserves_machine(tmp_path, failure):
    h, cpu, vnc = rpc_machine(tmp_path)
    path = str(tmp_path/'saved.pypc')
    h.rpc('machine.snapshot.export', path=path, expected_state_revision=0)
    before = capture_machine(cpu)
    if failure == 'running': h.control['paused'] = False
    with pytest.raises(ValueError):
        h.rpc('machine.snapshot.import', path=path, disk_root=str(tmp_path/'restore'),
              expected_state_revision=1 if failure == 'revision' else 0,
              sha256='wrong' if failure == 'hash' else None)
    assert capture_machine(cpu) == before
    assert not (tmp_path/'restore').exists()


def test_input_during_prepare_is_deferred_until_after_install(tmp_path, monkeypatch):
    h, cpu, vnc = rpc_machine(tmp_path)
    path = str(tmp_path/'saved.pypc')
    h.rpc('machine.snapshot.export', path=path, expected_state_revision=0)
    old_queue = capture_machine(cpu)[0]['keyboard']['fields']['queue']
    attempted, delivered = threading.Event(), threading.Event()
    def input_thread():
        attempted.set()
        cpu._devices[1].PushKeyboardScancode(0x30)
        delivered.set()
    producer = threading.Thread(target=input_thread)
    original = machinesnapshots.prepare_machine
    def prepare(*args, **kwargs):
        producer.start()
        assert attempted.wait(1)
        assert not delivered.wait(0.05)
        return original(*args, **kwargs)
    monkeypatch.setattr(machinesnapshots, 'prepare_machine', prepare)
    h.rpc('machine.snapshot.import', path=path, disk_root=str(tmp_path/'restore'), expected_state_revision=0)
    producer.join(1)
    assert delivered.is_set()
    assert capture_machine(cpu)[0]['keyboard']['fields']['queue'] == old_queue + [0x30]


def test_telnet_refreshes_after_clock_rewind(tmp_path, monkeypatch):
    import telnet
    from types import SimpleNamespace
    clocks = iter([10, 10, 4, 4])
    sent = []
    server = telnet.Telnet.__new__(telnet.Telnet)
    server._scr = SimpleNamespace(GetClock=lambda:next(clocks))
    server.SetupTelnetSession = lambda stream:None
    server.PushScreen = lambda stream:sent.append(True)
    monkeypatch.setattr(telnet.select, 'select', lambda *args:([],[],[]))
    with pytest.raises(StopIteration):server.runner(object())
    assert len(sent) == 2
    h, cpu, vnc = rpc_machine(tmp_path)
    display = h.namespace['machine_snapshots'].display
    before = display.GetClock()
    display.epoch += 1
    assert display.GetClock() != before
