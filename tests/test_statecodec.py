"""CPU component persistence; no claim of full device/disk snapshots."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import bus
import i8088
from state8088 import State8088
from statecodec import dump_cpu_state, load_cpu_state


def test_every_field_roundtrips_without_flag_normalization():
    state = State8088()
    restored = load_cpu_state(json.loads(json.dumps(dump_cpu_state(state))))
    assert vars(restored) == vars(state)
    for name, value in vars(state).items():
        if type(value) is bool:
            setattr(state, name, True)
        elif type(value) is int:
            setattr(state, name, 17)
    state._clock = 2 ** 80 + 37  # No float/JSON-safe-integer truncation.
    state._rep_mode = State8088.RepMode.REPNZ
    restored = load_cpu_state(json.loads(json.dumps(dump_cpu_state(state))))
    assert vars(restored) == vars(state)
    restored.SetAX(0)
    assert state.GetAX() == 0x1111


@pytest.mark.parametrize('field,value', [
    ('al', -1), ('ah', 256), ('ip', 65536), ('cs', True),
    ('flags', 1.0), ('clock', -1), ('crash_counter', '1'),
    ('rep_mode', 4), ('rep_mode', False), ('rep', 1),
])
def test_malformed_field_is_rejected_without_mutation(field, value):
    state = State8088()
    payload = dump_cpu_state(state)
    payload['fields'][field] = value
    before = copy.deepcopy(payload)
    with pytest.raises(ValueError):
        load_cpu_state(payload)
    assert payload == before
    assert vars(state) == vars(State8088())


@pytest.mark.parametrize('mutation', [
    lambda p: p.update(version=2),
    lambda p: p.update(version=True),
    lambda p: p.update(format='dosbox-x'),
    lambda p: p.update(extra=0),
    lambda p: p['fields'].pop('inhibit_interrupts'),
    lambda p: p['fields'].update(new_field=0),
    lambda p: p.update(fields=[]),
])
def test_incompatible_schema_is_rejected(mutation):
    payload = dump_cpu_state(State8088())
    mutation(payload)
    with pytest.raises(ValueError):
        load_cpu_state(payload)


def test_unclassified_runtime_field_refuses_export():
    state = State8088()
    state._new_execution_latch = True
    with pytest.raises(ValueError, match='fields changed'):
        dump_cpu_state(state)


def machine():
    memory = bus.Bus(1 << 20, [], [])
    cpu = i8088.i8088(memory, [], False)
    return cpu, memory


def continue_cpu(cpu, memory, ticks):
    accesses = []
    cpu.SetMemoryTraceHook(lambda *args: accesses.append(args))
    frames = []
    for _ in range(ticks):
        cycles = cpu.Tick()
        frames.append({'cycles': cycles, 'state': dump_cpu_state(cpu.GetState())})
    return {'frames': frames, 'accesses': accesses, 'ram': memory._m._m.hex()}


@pytest.mark.parametrize('code,pause_after,ticks', [
    ('36 f3 a4 90 f4', 2, 7),  # SS override, partway through REP MOVSB
    ('fb 90 f4', 1, 4),        # STI's one-instruction interrupt inhibition
    ('f4', 1, 3),              # Halted CPU still advances its cycle count
])
def test_fresh_process_continuation_matches(code, pause_after, ticks):
    cpu, memory = machine()
    state = cpu.GetState()
    for name, value in dict(CS=0x1000, IP=0x100, DS=0x2000, SS=0x3000,
                            ES=0x4000, SI=0x200, DI=0x300, CX=5,
                            SP=0x9000, Flags=2).items():
        getattr(state, 'Set' + name)(value)
    for offset, byte in enumerate(bytes.fromhex(code)):
        cpu.WriteMemByte(0x1000, 0x100 + offset, byte)
    for offset, byte in enumerate(b'ABCDE'):
        cpu.WriteMemByte(0x3000, 0x200 + offset, byte)
    for _ in range(pause_after):
        cpu.Tick()
    if code.startswith('36'):
        assert state._rep and state.GetCX() == 3
    elif code.startswith('fb'):
        assert state._inhibit_interrupts
    else:
        assert state._in_hlt
    captured = {'cpu': dump_cpu_state(state), 'ram': memory._m._m.hex(),
                'ticks': ticks}
    expected = continue_cpu(cpu, memory, ticks)
    # stdin carries only JSON. No pickle, temp directory, or imported live object.
    program = '''
import json, sys, typing
if not hasattr(typing, 'override'):
    typing.override = lambda method: method
sys.path.insert(0, 'tests')
from test_statecodec import machine, continue_cpu
from statecodec import load_cpu_state
data = json.load(sys.stdin)
cpu, memory = machine()
cpu._state = load_cpu_state(data['cpu'])
memory._m._m[:] = bytes.fromhex(data['ram'])
print(json.dumps(continue_cpu(cpu, memory, data['ticks'])))
'''
    completed = subprocess.run(
        [sys.executable, '-c', program], input=json.dumps(captured),
        text=True, capture_output=True, check=True, timeout=30,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert json.loads(completed.stdout) == json.loads(json.dumps(expected))
    if code.startswith('36'):
        assert memory._m._m[0x40300:0x40305] == b'ABCDE'
