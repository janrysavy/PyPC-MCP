"""PIT, PIC and host RNG components for future stopped-machine snapshots.

These preserve the current emulator, including its timer approximations and
randomized unlatched reads. They do not assert physical 8253/8259 accuracy.
Loading creates detached objects; the machine layer must validate all other
components, rebind device references and install RNG state before resuming.
Debug trace callbacks/context are host instrumentation, not guest PIC state.
"""
import math
import random

from i8253 import i8253
from i8259 import i8259


def _integer(value, low=None, high=None):
    return (type(value) is int and (low is None or value >= low)
            and (high is None or value <= high))


def _flag(value):
    # Timer defaults are integer zero; subsequent port writes store bools.
    return type(value) in (bool, int) and value in (0, 1)


TIMER = {
    'counter_cur': _integer, 'counter_prv': _integer,
    'counter_ini': lambda v: _integer(v, 0, 65535),
    'latched_count': lambda v: v is None or _integer(v, 0, 65535),
    'mode': lambda v: _integer(v, 0, 7),
    'latch_type': lambda v: _integer(v, 0, 3),
    'latch_n': lambda v: _integer(v, 0, 2),
    'latch_n_cur': lambda v: _integer(v, 0, 65535),
    'is_running': _flag, 'is_pending': _flag, 'is_bcd': _flag,
}
PIC = {
    name: (lambda v: _integer(v, 0, 255)) for name in (
        'int_offset', 'irr', 'isr', 'imr', 'icw1', 'icw2', 'icw3', 'icw4',
        'ocw2', 'ocw3')
}
PIC.update({name: (lambda v: type(v) is bool) for name in (
    'auto_eoi', 'read_irr', 'has_slave', 'in_init', 'ii_icw2', 'ii_icw3',
    'ii_icw4', 'ii_icw4_req')})
PIC.update(irq_request_level=lambda v: _integer(v, 0, 7),
           int_in_service=lambda v: _integer(v, -1, 7))
PIC_HOST = {'_trace_hook', '_trace_address', '_trace_clock'}
PIT_LINKS = {'_pic', '_b', '_i8237'}


def _validate(fields, spec):
    if type(fields) is not dict or set(fields) != set(spec):
        raise ValueError('invalid timing component fields')
    for name, valid in spec.items():
        if not valid(fields[name]):
            raise ValueError('invalid timing component field: ' + name)


def _envelope(kind, fields):
    return {'format': 'pypc.' + kind, 'version': 1, 'fields': fields}


def _unpack(payload, kind):
    if (type(payload) is not dict or set(payload) != {'format', 'version', 'fields'}
            or payload['format'] != 'pypc.' + kind
            or type(payload['version']) is not int or payload['version'] != 1):
        raise ValueError('unsupported timing component format/version')
    return payload['fields']


def _check_pic_layout(pic):
    if type(pic) is not i8259 or set(vars(pic)) != (
            {'_' + name for name in PIC} | PIC_HOST):
        raise ValueError('PIC layout changed; update snapshot schema')


def _validate_pic(fields):
    _validate(fields, PIC)
    # Every ISR writer refreshes this cached value, including initialization.
    highest = next((irq for irq in range(8) if fields['isr'] & (1 << irq)), -1)
    if fields['int_in_service'] != highest:
        raise ValueError('PIC service cache disagrees with ISR')


def dump_pic_state(pic):
    _check_pic_layout(pic)
    fields = {name: getattr(pic, '_' + name) for name in PIC}
    _validate_pic(fields)
    return _envelope('i8259', fields)


def load_pic_state(payload):
    fields = _unpack(payload, 'i8259')
    _validate_pic(fields)
    pic = i8259()
    _check_pic_layout(pic)
    for name, value in fields.items():
        setattr(pic, '_' + name, value)
    return pic


def _check_timer_layout(timer):
    # Timer fields currently default on the class, not in each __dict__.
    if (type(timer) is not i8253.Timer
            or set(i8253.Timer.__annotations__) != set(TIMER)
            or {name for name in vars(i8253.Timer)
                if not name.startswith('__')} != set(TIMER)
            or not set(vars(timer)) <= set(TIMER)):
        raise ValueError('PIT timer layout changed; update snapshot schema')


def _check_pit_layout(pit):
    if (type(pit) is not i8253
            or set(vars(pit)) - PIT_LINKS != {'_timers', '_clock', '_irq_nr'}):
        raise ValueError('PIT layout changed; update snapshot schema')


def dump_pit_state(pit):
    _check_pit_layout(pit)
    timers = []
    for timer in pit._timers:
        _check_timer_layout(timer)
        timers.append({name: getattr(timer, name) for name in TIMER})
    payload = _envelope('i8253', {
        'clock': pit._clock, 'irq_nr': pit._irq_nr, 'timers': timers,
    })
    load_pit_state(payload)
    return payload


def load_pit_state(payload):
    fields = _unpack(payload, 'i8253')
    _validate(fields, {
        'clock': lambda v: _integer(v, 0, 3),
        'irq_nr': lambda v: _integer(v, 0, 7),
        'timers': lambda v: type(v) is list and len(v) == 3,
    })
    for channel, timer in enumerate(fields['timers']):
        _validate(timer, TIMER)
        width = (0, 1, 1, 2)[timer['latch_type']]
        if timer['latch_n'] != width:
            raise ValueError('PIT byte width disagrees with access mode')
        # Reads before programming can underflow the initial zero phase; keep
        # that existing behavior. Programmed modes always cycle through 1..N.
        if width and not 1 <= timer['latch_n_cur'] <= width:
            raise ValueError('PIT byte phase disagrees with access mode')
        if channel != 0 and timer['is_pending']:
            raise ValueError('only PIT channel zero sets is_pending')
    pit = i8253()
    _check_pit_layout(pit)
    pit._clock = fields['clock']
    pit._irq_nr = fields['irq_nr']
    for timer, values in zip(pit._timers, fields['timers']):
        _check_timer_layout(timer)
        for name, value in values.items():
            setattr(timer, name, value)
    # No calls to Tick or port writes: they would consume latches or raise IRQs.
    return pit


def dump_host_rng_state():
    """Capture the module-global RNG used by i8253.AddNoiseToLSB."""
    version, words, gaussian = random.getstate()
    payload = _envelope('python_random', {
        'state_version': version, 'words': list(words), 'gauss_next': gaussian,
    })
    load_host_rng_state(payload)
    return payload


def load_host_rng_state(payload):
    """Return a validated local Random object; do not alter the process RNG.

    The stopped machine layer must eventually call random.setstate(rng.getstate())
    exactly once, after validating every component and before any guest execution.
    """
    fields = _unpack(payload, 'python_random')
    _validate(fields, {
        'state_version': lambda v: type(v) is int and v == 3,
        'words': lambda v: type(v) is list and len(v) == 625,
        'gauss_next': lambda v: v is None or (type(v) is float and math.isfinite(v)),
    })
    words = fields['words']
    if (not all(_integer(v, 0, 0xffffffff) for v in words[:624])
            or not _integer(words[624], 0, 624)):
        raise ValueError('invalid host RNG words/index')
    rng = random.Random(0)
    rng.setstate((3, tuple(words), fields['gauss_next']))
    return rng
