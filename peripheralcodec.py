"""Detached DMA, keyboard and PPI components; not a full machine snapshot.

Stop the CPU before capture. The machine layer must also hold the keyboard's
_state_lock across the entire machine capture/installation to exclude input
producers, then rebind bus/PIC/DMA/PPI links before resuming execution.
"""
import queue

from i8237 import i8237
from i8255 import i8255
from keyboard import Keyboard
from timingcodec import _envelope, _unpack, _validate, _integer


def _array(value, length, valid):
    return type(value) is list and (length is None or len(value) == length) and all(
        valid(item) for item in value)


def _bool(value):
    return type(value) is bool


def _byte(value):
    return _integer(value, 0, 255)


DMA_FIELDS = {
    '_channel_page', '_channel_address_register', '_channel_word_count',
    '_command', '_channel_mask', '_reached_tc', '_channel_mode', '_ff',
    '_dma_enabled', '_b',
}


def _check_dma(dma):
    if type(dma) is not i8237 or set(vars(dma)) != DMA_FIELDS:
        raise ValueError('DMA layout changed; update snapshot schema')
    if type(dma._ff) is not i8237.FlipFlop or set(vars(dma._ff)) != {'_state'}:
        raise ValueError('DMA flip-flop layout changed')
    for registers in (dma._channel_address_register, dma._channel_word_count):
        for register in registers:
            if (type(register) is not i8237.b16buffer
                    or set(vars(register)) != {'_value', '_base_value', '_f'}
                    or register._f is not dma._ff):
                raise ValueError('DMA register layout/shared flip-flop changed')


def dump_dma_state(dma):
    _check_dma(dma)
    fields = {
        'pages': list(dma._channel_page), 'command': dma._command,
        'masks': list(dma._channel_mask), 'terminal_count': list(dma._reached_tc),
        'modes': list(dma._channel_mode), 'flipflop': dma._ff._state,
        'enabled': dma._dma_enabled,
    }
    for name, registers in (('addresses', dma._channel_address_register),
                            ('counts', dma._channel_word_count)):
        fields[name] = [{'current': r._value, 'base': r._base_value} for r in registers]
    payload = _envelope('i8237', fields)
    load_dma_state(payload)
    return payload


def load_dma_state(payload, bus=None):
    fields = _unpack(payload, 'i8237')
    _validate(fields, {
        'pages': lambda v: _array(v, 4, lambda p: _integer(p, 0, 15)),
        'command': _byte, 'flipflop': _bool, 'enabled': _bool,
        'masks': lambda v: _array(v, 4, _bool),
        'terminal_count': lambda v: _array(v, 4, _bool),
        'modes': lambda v: _array(v, 4, _byte),
        'addresses': lambda v: type(v) is list and len(v) == 4,
        'counts': lambda v: type(v) is list and len(v) == 4,
    })
    if fields['enabled'] != ((fields['command'] & 4) == 0):
        raise ValueError('DMA command/enabled disagree')
    for name in ('addresses', 'counts'):
        for register in fields[name]:
            _validate(register, {'current': lambda v: _integer(v, 0, 65535),
                                 'base': lambda v: _integer(v, 0, 65535)})
    dma = i8237(bus)
    _check_dma(dma)
    dma._channel_page = list(fields['pages'])
    dma._command = fields['command']
    dma._channel_mask = list(fields['masks'])
    dma._reached_tc = list(fields['terminal_count'])
    dma._channel_mode = list(fields['modes'])
    dma._ff._state = fields['flipflop']
    dma._dma_enabled = fields['enabled']
    for name, registers in (('addresses', dma._channel_address_register),
                            ('counts', dma._channel_word_count)):
        for register, values in zip(registers, fields[name]):
            register._value = values['current']
            register._base_value = values['base']
    return dma


KEYBOARD = {
    'irq_nr': lambda v: _integer(v, 0, 7),
    'kb_reset_irq_delay': lambda v: _integer(v, 0),
    'kb_key_irq': lambda v: _integer(v, 0),
    'clock_low': _bool, '0x61_bits': _byte, 'last_scan_code': _byte,
    'clock': lambda v: _integer(v, 0),
    'next_interrupt': lambda v: _array(v, None, _integer),
}
KEYBOARD_EXTRA = {'_keyboard_buffer', '_pressed_scancodes', '_state_lock', '_pic', '_b'}


def _check_keyboard(kb):
    if type(kb) is not Keyboard or set(vars(kb)) != (
            {'_' + field for field in KEYBOARD} | KEYBOARD_EXTRA):
        raise ValueError('keyboard layout changed; update snapshot schema')
    if type(kb._keyboard_buffer) is not queue.Queue or kb._keyboard_buffer.maxsize != 0:
        raise ValueError('unsupported keyboard queue')


def dump_keyboard_state(kb):
    if type(kb) is not Keyboard:
        raise ValueError('expected Keyboard')
    with kb._state_lock:
        _check_keyboard(kb)
        fields = {name: getattr(kb, '_' + name) for name in KEYBOARD}
        fields['next_interrupt'] = list(kb._next_interrupt)
        fields['pressed'] = sorted(kb._pressed_scancodes)
        with kb._keyboard_buffer.mutex:
            fields['queue'] = list(kb._keyboard_buffer.queue)
            fields['unfinished_tasks'] = kb._keyboard_buffer.unfinished_tasks
        payload = _envelope('keyboard', fields)
        load_keyboard_state(payload)
        return payload


def load_keyboard_state(payload):
    fields = _unpack(payload, 'keyboard')
    _validate(fields, dict(KEYBOARD, **{
        'pressed': lambda v: _array(v, None, lambda n: _integer(n, 0, 127)),
        'queue': lambda v: _array(v, None, _byte),
        'unfinished_tasks': lambda v: _integer(v, 0),
    }))
    if fields['pressed'] != sorted(set(fields['pressed'])):
        raise ValueError('pressed scancodes must be unique and sorted')
    if fields['unfinished_tasks'] < len(fields['queue']):
        raise ValueError('keyboard queue task count is inconsistent')
    kb = Keyboard()
    _check_keyboard(kb)
    for name in KEYBOARD:
        value = fields[name]
        setattr(kb, '_' + name, list(value) if name == 'next_interrupt' else value)
    kb._pressed_scancodes = set(fields['pressed'])
    for scancode in fields['queue']:
        kb._keyboard_buffer.put(scancode)
    kb._keyboard_buffer.unfinished_tasks = fields['unfinished_tasks']
    return kb


PPI = {'control': _byte, 'dipswitches_high': _bool, 'use_SW1': _bool,
       'SW1': _byte, 'SW2': _byte}
PPI_LINKS = {'_kb', '_pic', '_b'}


def _check_ppi(ppi):
    if type(ppi) is not i8255 or set(vars(ppi)) - PPI_LINKS != {'_' + f for f in PPI}:
        raise ValueError('PPI layout changed; update snapshot schema')


def dump_ppi_state(ppi):
    _check_ppi(ppi)
    fields = {name: getattr(ppi, '_' + name) for name in PPI}
    _validate(fields, PPI)
    return _envelope('i8255', fields)


def load_ppi_state(payload, keyboard=None):
    fields = _unpack(payload, 'i8255')
    _validate(fields, PPI)
    ppi = i8255(keyboard)
    _check_ppi(ppi)
    for name, value in fields.items():
        setattr(ppi, '_' + name, value)
    return ppi
