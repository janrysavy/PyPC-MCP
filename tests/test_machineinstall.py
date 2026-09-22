import pytest

from machinecodec import capture_machine, prepare_machine
from machineinstall import install_machine
from test_machinecodec import machine, run


def test_install_preserves_live_references_and_replays(tmp_path):
    cpu = machine(tmp_path)
    state = cpu.GetState()
    devices = tuple(cpu._devices)
    lock = devices[1]._state_lock
    ports = dict(cpu._io._io_map)
    tick_methods = tuple(cpu._io._tick_methods)
    original = capture_machine(cpu)
    expected_trace = run(cpu)
    expected = capture_machine(cpu)
    prepared, rng = prepare_machine(*original, tmp_path/'restored')
    cpu.SetIgnoreBreakpoints()
    with lock:
        install_machine(cpu, prepared, rng)
    assert not cpu._ignore_breakpoints
    assert cpu.GetState() is state
    assert tuple(cpu._devices) == devices
    assert cpu._devices[1]._state_lock is lock
    assert cpu._io._io_map == ports
    assert tuple(cpu._io._tick_methods) == tick_methods
    assert cpu._devices[0]._i8237 is cpu._io._i8237
    assert cpu._devices[2]._kb is cpu._devices[1]
    assert cpu._io._i8237._b is cpu._b
    assert capture_machine(cpu) == original
    assert run(cpu) == expected_trace
    assert capture_machine(cpu) == expected
    # The restored keyboard must still reach the PIC used by CPU execution.
    assert cpu._devices[1]._pic is cpu._io._pic


def test_wrong_topology_refuses_before_mutation(tmp_path):
    cpu = machine(tmp_path)
    before = capture_machine(cpu)
    restored, rng = prepare_machine(*before, tmp_path/'restored')
    restored._devices.reverse()
    with pytest.raises(ValueError, match='same motherboard'):
        install_machine(cpu, restored, rng)
    assert capture_machine(cpu) == before
