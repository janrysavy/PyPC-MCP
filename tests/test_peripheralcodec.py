"""Fresh-process peripheral replay and deterministic input/capture contention."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest
import bus
from i8237 import i8237
from i8255 import i8255
from keyboard import Keyboard
from peripheralcodec import (
    dump_dma_state, load_dma_state, dump_keyboard_state, load_keyboard_state,
    dump_ppi_state, load_ppi_state,
)
from timingcodec import dump_pic_state, load_pic_state
from i8259 import i8259


def put_word(dma, port, value):
    dma.IO_Write(12, 0)
    dma.IO_Write(port, value & 255)
    dma.IO_Write(port, value >> 8)


def dma_fixture(decrement):
    memory = bus.Bus(1 << 20, [], [])
    memory._m._m[:] = bytes(range(256)) * 4096
    dma = i8237(memory)
    for channel in range(4):
        put_word(dma, 2 * channel, 0 if decrement else 65535)
        put_word(dma, 2 * channel + 1, 2)
        dma.IO_Write((0x87, 0x83, 0x81, 0x82)[channel], channel + 1)
        dma.IO_Write(11, channel | 0x50 | (0x20 if decrement else 0))
        dma.ReceiveFromChannel(channel)  # Current/base registers now differ.
    dma.TickChannel0(5)  # Cross automatic reload, leaving TC pending.
    dma.IO_Write(12, 0)
    dma.IO_Write(2, 0x34)  # Half-written channel 1 address; shared FF is high.
    return dma, memory


def dma_replay(dma, memory):
    initial = dump_dma_state(dma)
    dma.IO_Write(2, 0x12)
    # The byte pointer is shared even when low/high reads use different ports.
    operations = [dma.IO_Read(0), dma.IO_Read(1)]
    accesses = []
    old_read, old_write = memory.ReadByte, memory.WriteByte
    def read(address):
        result = old_read(address)
        accesses.append(['read', address, result[0]])
        return result
    def write(address, value):
        accesses.append(['write', address, value])
        return old_write(address, value)
    memory.ReadByte, memory.WriteByte = read, write
    for channel in range(4):
        for n in range(7):
            operations += [dma.ReceiveFromChannel(channel),
                           dma.SendToChannel(channel, 0x70 + n)]
        operations += [dma.IO_Read(8), dma.IO_Read(8)]
    dma.IO_Write(8, 4)  # Controller disabled, no memory access.
    operations += [dma.ReceiveFromChannel(2), dma.SendToChannel(2, 99)]
    dma.IO_Write(13, 0)  # Reset masks/status/FF, preserve base/current/page.
    operations.append(dma.ReceiveFromChannel(0))
    return {'initial': initial, 'final': dump_dma_state(dma),
            'operations': operations, 'accesses': accesses,
            'ram': memory._m._m.hex()}


def input_fixture(reset):
    kb, pic = Keyboard(), i8259()
    kb.SetPic(pic)
    pic.IO_Write(0x21, 0)
    ppi = i8255(kb)
    for scan in (42, 30, 158):
        kb.PushKeyboardScancode(scan)
    kb.Tick(100, 100)
    assert ppi.IO_Read(0x60) == 42
    ppi.IO_Write(0x61, 0x48)  # High DIP-switch selection, no reset edge.
    if reset:
        ppi.IO_Write(0x61, 8)
        ppi.IO_Write(0x61, 0x48)  # Reset reply replaces queue, retains IRQ schedule.
    return kb, ppi, pic


def input_capture(kb, ppi, pic):
    return {'keyboard': dump_keyboard_state(kb), 'ppi': dump_ppi_state(ppi),
            'pic': dump_pic_state(pic)}


def input_replay(kb, ppi, pic):
    initial = input_capture(kb, ppi, pic)
    events = []
    pic.SetTraceHook(events.append)
    reads = [ppi.IO_Read(0x62), ppi.IO_Read(0x63)]
    for cycles in (4669, 1, 4770, 4770, 4770):
        kb.Tick(cycles, 0)
        reads += [pic.GetPendingInterrupt(), ppi.IO_Read(0x60)]
        if pic.GetPendingInterrupt() == 1:
            pic.SetIRQBeingServiced(1)
            pic.IO_Write(0x20, 0x20)
    kb.PushKeyboardScancode(170)
    ppi.IO_Write(0x61, 0xc8)  # Inhibit IRQs and clear last scancode.
    kb.Tick(10000, 0)
    blocked = dump_keyboard_state(kb)
    ppi.IO_Write(0x61, 0x40)
    kb.Tick(4770, 0)
    reads += [ppi.IO_Read(0x60), ppi.IO_Read(0x62), pic.GetPendingInterrupt()]
    return {'initial': initial, 'reads': reads, 'events': events,
            'blocked': blocked, 'final': input_capture(kb, ppi, pic)}


def fresh_process(data):
    code = '''
import json, sys, typing
if not hasattr(typing, 'override'):
    typing.override = lambda method: method
sys.path.insert(0, 'tests')
from test_peripheralcodec import *
data = json.load(sys.stdin)
if data['kind'] == 'dma':
    memory = bus.Bus(1 << 20, [], [])
    memory._m._m[:] = bytes.fromhex(data['ram'])
    dma = load_dma_state(data['state'], memory)
    result = dma_replay(dma, memory)
else:
    state = data['state']
    pic = load_pic_state(state['pic'])
    kb = load_keyboard_state(state['keyboard'])
    kb.SetPic(pic)
    ppi = load_ppi_state(state['ppi'], kb)
    result = input_replay(kb, ppi, pic)
print(json.dumps(result))
'''
    result = subprocess.run([sys.executable, '-c', code], input=json.dumps(data),
                            capture_output=True, text=True, check=True, timeout=30,
                            cwd=Path(__file__).resolve().parents[1])
    return json.loads(result.stdout)


@pytest.mark.parametrize('decrement', [False, True])
def test_dma_fresh_process_preserves_transfers_and_shared_byte_phase(decrement):
    dma, memory = dma_fixture(decrement)
    saved = {'kind': 'dma', 'state': dump_dma_state(dma), 'ram': memory._m._m.hex()}
    assert saved['state']['fields']['flipflop'] is True
    expected = dma_replay(dma, memory)
    assert fresh_process(saved) == expected
    assert expected['operations'][-3:] == [-1, False, -1]
    assert len(expected['accesses']) == 56
    bad = copy.deepcopy(saved)
    bad['state']['fields']['flipflop'] = False
    assert fresh_process(bad)['accesses'] != expected['accesses']


@pytest.mark.parametrize('reset', [False, True])
def test_input_fresh_process_preserves_queue_and_irq_delays(reset):
    kb, ppi, pic = input_fixture(reset)
    saved = {'kind': 'input', 'state': input_capture(kb, ppi, pic)}
    assert saved['state']['keyboard']['fields']['pressed'] == [42]
    assert saved['state']['keyboard']['fields']['queue'] == ([170] if reset else [30, 158])
    expected = input_replay(kb, ppi, pic)
    assert fresh_process(saved) == expected
    assert expected['reads'][:3] == [2, 0x99, 255]
    assert expected['reads'][4] == 1  # IRQ exactly one further cycle later.
    assert expected['final']['keyboard']['fields']['pressed'] == []
    bad = copy.deepcopy(saved)
    bad['state']['keyboard']['fields']['next_interrupt'][0] += 1
    assert fresh_process(bad)['reads'] != expected['reads']


def test_idle_keyboard_tick_avoids_state_lock():
    kb = Keyboard()

    class UnexpectedLock:
        def __enter__(self):
            raise AssertionError('idle Tick acquired the keyboard state lock')
        def __exit__(self, *args):
            pass

    kb._state_lock = UnexpectedLock()
    assert kb.Tick(100, 100) is False


def test_pending_flag_survives_snapshot_and_delivers_irq():
    kb, pic = Keyboard(), i8259()
    kb.SetPic(pic)
    pic.IO_Write(0x21, 0)
    kb.PushKeyboardScancode(42)
    saved = dump_keyboard_state(kb)
    loaded = load_keyboard_state(saved)
    loaded.SetPic(pic)
    assert loaded._interrupt_pending is True
    loaded.Tick(4770, 0)
    assert pic.GetPendingInterrupt() == 1
    assert loaded._interrupt_pending is False


@pytest.mark.parametrize('synchronized', [True, False])
def test_keyboard_capture_cannot_observe_half_enqueued_input(monkeypatch, synchronized):
    kb = Keyboard()
    queued, release, attempt, finished = (threading.Event() for _ in range(4))
    lock = kb._state_lock
    blocked = []
    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == 'snapshot':
                acquired = lock.acquire(blocking=False)
                blocked.append(not acquired)
                attempt.set()
                if acquired:
                    return
            lock.acquire()
        def __exit__(self, *args):
            lock.release()
    kb._state_lock = ObservedLock()
    put = kb._keyboard_buffer.put
    def blocked_put(value):
        put(value)
        queued.set()
        if not release.wait(5):
            raise RuntimeError('test producer was not released')
    monkeypatch.setattr(kb._keyboard_buffer, 'put', blocked_put)
    results, errors = [], []
    def producer():
        try:
            if synchronized:
                kb.PushKeyboardScancode(42)
            else:
                # Negative control: deliberately bypass the enqueue lock.
                kb.PushKeyboardScancode.__wrapped__(kb, 42)
        except BaseException as error:
            errors.append(error)
    def snapshot():
        try:
            results.append(dump_keyboard_state(kb))
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    writer = threading.Thread(target=producer, daemon=True)
    reader = threading.Thread(target=snapshot, name='snapshot', daemon=True)
    writer.start()
    try:
        assert queued.wait(5)
        reader.start()
        assert attempt.wait(5)
        assert blocked == [synchronized]
        if synchronized:
            assert not finished.is_set()
        else:
            assert finished.wait(5)
    finally:
        release.set()
        writer.join(5)
        if reader.ident is not None:
            reader.join(5)
    assert not writer.is_alive() and not reader.is_alive()
    assert errors == []
    fields = results[0]['fields']
    assert (fields['pressed'], fields['queue'], fields['next_interrupt']) == (
        [42], [42], [4770] if synchronized else [])


@pytest.mark.parametrize('kind,mutation', [
    ('dma', lambda p: p['fields'].update(enabled=False)),
    ('dma', lambda p: p['fields']['pages'].__setitem__(0, 16)),
    ('dma', lambda p: p['fields']['counts'][3].update(base=-1)),
    ('dma', lambda p: p['fields']['addresses'].pop()),
    ('keyboard', lambda p: p['fields'].update(pressed=[42, 42])),
    ('keyboard', lambda p: p['fields'].update(queue=[256])),
    ('keyboard', lambda p: p['fields'].update(unfinished_tasks=0)),
    ('keyboard', lambda p: p['fields'].update(next_interrupt=[True])),
    ('keyboard', lambda p: p['fields'].update(next_interrupt=[-1])),
    ('ppi', lambda p: p['fields'].update(dipswitches_high=1)),
])
def test_malformed_state_rejected_without_mutating_source(kind, mutation):
    dma, _ = dma_fixture(False)
    kb, ppi, _ = input_fixture(False)
    dump, load, obj = {'dma': (dump_dma_state, load_dma_state, dma),
                       'keyboard': (dump_keyboard_state, load_keyboard_state, kb),
                       'ppi': (dump_ppi_state, load_ppi_state, ppi)}[kind]
    saved = dump(obj)
    bad = copy.deepcopy(saved)
    mutation(bad)
    before = copy.deepcopy(bad)
    with pytest.raises(ValueError):
        load(bad)
    assert bad == before
    assert dump(obj) == saved


def test_dma_restores_one_shared_flipflop_without_payload_aliases():
    dma, _ = dma_fixture(False)
    payload = dump_dma_state(dma)
    loaded = load_dma_state(payload)
    for register in loaded._channel_address_register + loaded._channel_word_count:
        assert register._f is loaded._ff
    loaded._channel_page[0] = 9
    loaded._channel_address_register[0]._value = 7
    assert dump_dma_state(dma) == payload
    assert payload['fields']['pages'][0] == 1


def test_disabled_masked_dma_and_half_read_register_roundtrip():
    dma, memory = dma_fixture(False)
    dma.IO_Write(8, 4)
    dma.IO_Write(15, 0b1010)
    dma.IO_Write(12, 0)
    low = dma.IO_Read(2)
    saved = dump_dma_state(dma)
    loaded = load_dma_state(json.loads(json.dumps(saved)), memory)
    assert dump_dma_state(loaded) == saved
    assert loaded.IO_Read(2) == dma.IO_Read(2)
    assert low == 0x34
    assert loaded.ReceiveFromChannel(0) == -1
    assert loaded.SendToChannel(0, 12) is False


@pytest.mark.parametrize('action', ['read', 'irq', 'producer', 'reset'])
def test_keyboard_operations_wait_for_enqueue(action, monkeypatch):
    kb, pic = Keyboard(), i8259()
    kb.SetPic(pic)
    pic.IO_Write(0x21, 0)
    queued, release, attempt = (threading.Event() for _ in range(3))
    lock, blocked = kb._state_lock, []
    class ObservedLock:
        def __enter__(self):
            if threading.current_thread().name == 'contender' and not blocked:
                acquired = lock.acquire(blocking=False)
                blocked.append(not acquired)
                attempt.set()
                if acquired:
                    return
            lock.acquire()
        def __exit__(self, *args):
            lock.release()
    kb._state_lock = ObservedLock()
    put = kb._keyboard_buffer.put
    def blocked_put(value):
        put(value)
        if value == 42:
            queued.set()
            if not release.wait(5):
                raise RuntimeError('producer was not released')
    monkeypatch.setattr(kb._keyboard_buffer, 'put', blocked_put)
    results, errors = [], []
    def producer():
        try:
            kb.PushKeyboardScancode(42)
        except BaseException as error:
            errors.append(error)
    def contender():
        try:
            if action == 'read':
                results.append(kb.IO_Read(0x60))
            elif action == 'irq':
                kb.Tick(4770, 0)
            elif action == 'producer':
                kb.PushKeyboardScancode(30)
            else:
                kb.IO_Write(0x61, 0)
                kb.IO_Write(0x61, 0x40)
        except BaseException as error:
            errors.append(error)
    writer = threading.Thread(target=producer, daemon=True)
    other = threading.Thread(target=contender, name='contender', daemon=True)
    writer.start()
    try:
        assert queued.wait(5)
        other.start()
        if action == 'irq':
            # The lock-free empty check linearizes this Tick before the
            # producer publishes its completed schedule operation.
            assert not attempt.wait(0.05)
            assert blocked == []
        else:
            assert attempt.wait(5)
            assert blocked == [True]
    finally:
        release.set()
        writer.join(5)
        if other.ident is not None:
            other.join(5)
    if action == 'irq':
        # Publishing the pending flag makes the very next Tick deliver it.
        kb.Tick(4770, 0)
    assert not writer.is_alive() and not other.is_alive()
    assert errors == []
    state = dump_keyboard_state(kb)['fields']
    if action == 'read':
        assert results == [42]
        assert state['queue'] == [] and state['next_interrupt'] == [4770]
    elif action == 'irq':
        assert pic.GetPendingInterrupt() == 1
        assert state['queue'] == [42] and state['next_interrupt'] == []
    elif action == 'producer':
        assert state['pressed'] == [30, 42]
        assert state['queue'] == [42, 30]
        assert state['next_interrupt'] == [4770, 4770]
    else:
        assert state['queue'] == [170] and state['next_interrupt'] == [4770, 4770]
        assert state['clock_low'] is False
