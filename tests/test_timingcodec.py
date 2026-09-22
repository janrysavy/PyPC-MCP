"""Cross-process timing-component replay, including mutation controls."""
import copy
import json
from pathlib import Path
import random
import subprocess
import sys

import pytest
from i8253 import i8253
from i8259 import i8259
from timingcodec import (
    dump_pit_state, load_pit_state, dump_pic_state, load_pic_state,
    dump_host_rng_state, load_host_rng_state,
)


@pytest.fixture(autouse=True)
def preserve_rng():
    saved = random.getstate()
    yield
    random.setstate(saved)


class RefreshRecorder:
    def __init__(self):
        self.events = []

    def TickChannel0(self, count):
        self.events.append(count)


def capture(pit, pic):
    return {'pit': dump_pit_state(pit), 'pic': dump_pic_state(pic),
            'rng': dump_host_rng_state()}


def fixture(kind):
    random.seed(314159)
    pic, pit = i8259(), i8253()
    pit.SetPic(pic)
    pit.SetDma(RefreshRecorder())
    if kind == 'initializing':
        pic.IO_Write(0x20, 0x11)  # Slave and ICW4 required.
        pic.IO_Write(0x21, 0x40)  # ICW2; still awaiting ICW3 and ICW4.
        return pit, pic
    pic.IO_Write(0x21, 0)
    pic.RequestInterruptPIC(3)
    pic.SetIRQBeingServiced(3)
    pic.RequestInterruptPIC(3)  # Another edge while in service.
    pic.RequestInterruptPIC(5)
    pic.IO_Write(0x20, 0x0b)  # Read ISR, not IRR.
    for channel, divisor in ((0, 5), (1, 3)):
        pit.IO_Write(0x43, (channel << 6) | 0x34)
        pit.IO_Write(0x40 + channel, divisor)
        pit.IO_Write(0x40 + channel, 0)
    pit.Tick(19, 0)  # Three residual CPU clocks; IRQ0 one clock away.
    pit.IO_Write(0x43, 0)
    assert pit.IO_Read(0x40) == 1  # Hold latched high byte across capture.
    pit.IO_Write(0x43, 0xb4)
    pit.IO_Write(0x42, 0x34)  # Half of a two-byte divisor write.
    if kind == 'bcd_msb':
        pit.IO_Write(0x43, 0x15)  # BCD, mode 2, low-byte access.
        pit.IO_Write(0x40, 9)
        pit.Tick(12, 0)
        pit.IO_Read(0x40)  # Set nonzero counter_prv and consume host RNG.
        assert pit._timers[0].counter_prv == 6
        pit.IO_Write(0x43, 0xa4)  # Channel 2, MSB-only access.
        pit.IO_Write(0x42, 0x12)
        pic.IO_Write(0x20, 0x63)  # Specific EOI: level 3, OCW2=0x63.
    return pit, pic


def replay(pit, pic, kind):
    refresh = RefreshRecorder()
    pit.SetDma(refresh)
    pit.SetPic(pic)
    events = []
    pic.SetTraceHook(events.append)
    pic.SetTraceContext({'segment': 0x1000, 'offset': 0x100}, 19)
    initial = capture(pit, pic)
    reads = []
    if kind == 'initializing':
        pic.IO_Write(0x21, 4)
        pic.IO_Write(0x21, 3)  # Automatic EOI enabled by final ICW4.
        pic.RequestInterruptPIC(2)
        reads.append(pic.GetPendingInterrupt())
        pic.SetIRQBeingServiced(2)
        reads += [pic.IO_Read(0x20), pic.GetInterruptOffset()]
    elif kind == 'bcd_msb':
        reads += [pit.IO_Read(0x42), pit.IO_Read(0x40),
                  pic.GetInterruptLevel(), pic.GetPendingInterrupt()]
        for clocks in (1, 8, 11, 12, 24, 48):
            reads += [pit.Tick(clocks, 0), pit.IO_Read(0x40), pit.IO_Read(0x42)]
    else:
        reads.append(pit.IO_Read(0x40))  # Pending latched high byte.
        pit.IO_Write(0x42, 0x12)  # Complete the interrupted divisor write.
        reads.append(pit.Tick(1, 0))
        reads.append(pic.GetPendingInterrupt())
        pic.SetIRQBeingServiced(0)
        reads.append(pic.IO_Read(0x20))  # ISR contains bits 0 and 3.
        pic.IO_Write(0x20, 0x20)  # EOI highest in service.
        pic.IO_Write(0x20, 0x20)  # Release IRQ3; its later edge survives.
        reads.append(pic.GetPendingInterrupt())
        # Exercise the actual global RNG path, not random.random() alone.
        pit.IO_Write(0x43, 0x14)  # IRQ0 counter, low-byte-only mode 2.
        pit.IO_Write(0x40, 251)
        for _ in range(32):
            pit.Tick(12, 0)
            reads.append(pit.IO_Read(0x40))
    return {'initial': initial, 'reads': reads, 'events': events,
            'refresh': refresh.events, 'final': capture(pit, pic)}


def fresh_process(snapshot, kind):
    program = '''
import json, sys, typing
if not hasattr(typing, 'override'):
    typing.override = lambda method: method
sys.path.insert(0, 'tests')
from test_timingcodec import replay
from timingcodec import load_pit_state, load_pic_state, load_host_rng_state
import random
data = json.load(sys.stdin)
snapshot = data['snapshot']
pit = load_pit_state(snapshot['pit'])
pic = load_pic_state(snapshot['pic'])
random.setstate(load_host_rng_state(snapshot['rng']).getstate())
print(json.dumps(replay(pit, pic, data['kind'])))
'''
    result = subprocess.run(
        [sys.executable, '-c', program], text=True, capture_output=True,
        input=json.dumps({'snapshot': snapshot, 'kind': kind}),
        cwd=Path(__file__).resolve().parents[1], timeout=30, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize('kind', ['active', 'initializing', 'bcd_msb'])
def test_fresh_process_replays_ports_events_and_rng(kind):
    pit, pic = fixture(kind)
    saved = capture(pit, pic)
    expected = replay(pit, pic, kind)
    assert fresh_process(saved, kind) == expected
    if kind == 'active':
        assert expected['reads'][:5] == [0, True, 0, 9, 3]
        assert expected['final']['pit']['fields']['timers'][2]['counter_ini'] == 0x1234
        assert sum(expected['refresh']) > 0
    elif kind == 'initializing':
        assert expected['reads'] == [2, 0, 0x40]
        assert expected['final']['pic']['fields']['auto_eoi'] is True
    else:
        assert expected['reads'][:4] == [0x12, 6, 3, 3]
        assert saved['pic']['fields']['ocw2'] == 0x63
        assert saved['pit']['fields']['timers'][0]['is_bcd'] is True


@pytest.mark.parametrize('mutation', ['clock', 'irr', 'rng'])
def test_replay_detects_lost_phase_request_or_rng(mutation):
    pit, pic = fixture('active')
    saved = capture(pit, pic)
    expected = replay(pit, pic, 'active')
    if mutation == 'clock':
        saved['pit']['fields']['clock'] = 0
    elif mutation == 'irr':
        saved['pic']['fields']['irr'] &= ~(1 << 3)
    else:
        random.seed(271828)
        saved['rng'] = dump_host_rng_state()
    actual = fresh_process(saved, 'active')
    # A real continuation difference, independent of initial-state comparison.
    assert actual['reads'] != expected['reads']


def test_raw_negative_counter_and_integer_defaults_are_preserved():
    pit, pic = i8253(), i8259()
    pit.SetPic(pic)
    pit.IO_Write(0x43, 0x10)  # Existing approximate mode 0 can go negative.
    pit.IO_Write(0x40, 3)
    pit.Tick(16, 0)
    assert pit._timers[0].counter_cur == -1
    saved = dump_pit_state(pit)
    loaded = load_pit_state(json.loads(json.dumps(saved)))
    assert dump_pit_state(loaded) == saved
    assert type(loaded._timers[1].is_running) is int
    assert type(loaded._timers[0].is_running) is bool


def test_rng_gaussian_cache_and_decode_do_not_mutate_global_state():
    random.seed(1234)
    random.gauss(0, 1)  # Fill cached second Gaussian.
    before = random.getstate()
    saved = dump_host_rng_state()
    assert random.getstate() == before
    loaded = load_host_rng_state(json.loads(json.dumps(saved)))
    assert random.getstate() == before
    assert loaded.getstate() == before
    assert loaded.gauss(0, 1) == random.gauss(0, 1)


@pytest.mark.parametrize('component,mutation', [
    ('pit', lambda p: p['fields'].update(clock=4)),
    ('pit', lambda p: p['fields']['timers'].pop()),
    ('pit', lambda p: p['fields']['timers'][2].update(latched_count=-1)),
    ('pit', lambda p: p['fields']['timers'][2].update(is_running=2)),
    ('pit', lambda p: p['fields']['timers'][2].pop('counter_prv')),
    ('pit', lambda p: p['fields']['timers'][2].update(latch_type=1)),
    ('pit', lambda p: p['fields']['timers'][2].update(latch_n_cur=0)),
    ('pit', lambda p: p['fields']['timers'][2].update(is_pending=True)),
    ('pic', lambda p: p['fields'].update(irr=True)),
    ('pic', lambda p: p['fields'].update(ii_icw4=1)),
    ('pic', lambda p: p['fields'].update(int_in_service=-2)),
    ('pic', lambda p: p['fields'].update(isr=0, int_in_service=7)),
    ('rng', lambda p: p['fields']['words'].__setitem__(624, 625)),
    ('rng', lambda p: p['fields']['words'].__setitem__(0, -1)),
    ('rng', lambda p: p['fields'].update(gauss_next='0.5')),
])
def test_malformed_state_rejected_without_global_mutation(component, mutation):
    pit, pic = fixture('active')
    saved = capture(pit, pic)
    bad = copy.deepcopy(saved[component])
    mutation(bad)
    before = copy.deepcopy(bad)
    with pytest.raises(ValueError):
        {'pit': load_pit_state, 'pic': load_pic_state,
         'rng': load_host_rng_state}[component](bad)
    assert bad == before
    assert capture(pit, pic) == saved


@pytest.mark.parametrize('component', ['pit', 'pic', 'rng'])
@pytest.mark.parametrize('mutation', [
    lambda p: p.update(version=True), lambda p: p.update(version=2),
    lambda p: p.update(format='dosbox-x'), lambda p: p.update(extra=0),
])
def test_envelope_rejects_incompatible_schema(component, mutation):
    pit, pic = fixture('active')
    bad = capture(pit, pic)[component]
    mutation(bad)
    with pytest.raises(ValueError):
        {'pit': load_pit_state, 'pic': load_pic_state,
         'rng': load_host_rng_state}[component](bad)


@pytest.mark.parametrize('target', ['pit', 'timer', 'pic'])
def test_unknown_runtime_field_refuses_export(target):
    pit, pic = fixture('active')
    obj = {'pit': pit, 'timer': pit._timers[0], 'pic': pic}[target]
    obj.new_latch = 123
    with pytest.raises(ValueError, match='layout changed'):
        dump_pic_state(pic) if target == 'pic' else dump_pit_state(pit)


def test_unknown_class_default_refuses_export_and_load(monkeypatch):
    pit, _ = fixture('active')
    saved = dump_pit_state(pit)
    monkeypatch.setattr(i8253.Timer, 'new_latch', 0, raising=False)
    with pytest.raises(ValueError, match='layout changed'):
        dump_pit_state(pit)
    with pytest.raises(ValueError, match='layout changed'):
        load_pit_state(saved)


def test_unprogrammed_read_phase_underflow_remains_serializable():
    pit = i8253()
    pit.IO_Read(0x40)
    assert pit._timers[0].latch_n_cur == 65535
    saved = dump_pit_state(pit)
    assert dump_pit_state(load_pit_state(saved)) == saved
