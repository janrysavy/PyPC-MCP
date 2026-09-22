"""Versioned CPU-state component for future persistent machine snapshots.

This is not a complete machine checkpoint: RAM, devices, disk contents,
configuration and debugger control are owned by the eventual machine layer.
Call only at a stopped CPU Tick boundary. No pickle or executable payloads.
"""
from state8088 import State8088


FORMAT = 'pypc.state8088'
VERSION = 1
BYTE_FIELDS = ('ah', 'al', 'bh', 'bl', 'ch', 'cl', 'dh', 'dl', 'rep_opcode')
WORD_FIELDS = ('si', 'di', 'bp', 'sp', 'ip', 'cs', 'ds', 'es', 'ss',
               'segment_override', 'flags', 'rep_addr')
BOOL_FIELDS = ('segment_override_set', 'in_hlt', 'inhibit_interrupts',
               'rep', 'rep_do_nothing')
COUNTER_FIELDS = ('clock', 'crash_counter')
FIELDS = BYTE_FIELDS + WORD_FIELDS + BOOL_FIELDS + COUNTER_FIELDS + ('rep_mode',)


def dump_cpu_state(state):
    """Return detached JSON data; reject a changed class rather than omit fields."""
    if type(state) is not State8088:
        raise ValueError('expected State8088')
    if set(vars(state)) != {'_' + field for field in FIELDS}:
        raise ValueError('State8088 fields changed; update the snapshot schema')
    fields = {field: getattr(state, '_' + field) for field in FIELDS}
    if type(fields['rep_mode']) is not State8088.RepMode:
        raise ValueError('invalid rep_mode')
    fields['rep_mode'] = fields['rep_mode'].value
    payload = {'format': FORMAT, 'version': VERSION, 'fields': fields}
    load_cpu_state(payload)  # Validate before claiming to have exported state.
    return payload


def load_cpu_state(payload):
    """Validate completely and return a new state; never mutate a live CPU.

    Preserve raw flags, including Reset's zero value; setters that normalize
    flags would change the captured state. The machine layer must validate its
    other components before installing this object or copying its fields.
    """
    if type(payload) is not dict or set(payload) != {'format', 'version', 'fields'}:
        raise ValueError('invalid CPU state envelope')
    if (payload['format'] != FORMAT or type(payload['version']) is not int
            or payload['version'] != VERSION):
        raise ValueError('unsupported CPU state format/version')
    fields = payload['fields']
    if type(fields) is not dict or set(fields) != set(FIELDS):
        raise ValueError('invalid CPU state fields')
    for names, maximum in ((BYTE_FIELDS, 255), (WORD_FIELDS, 65535),
                           (COUNTER_FIELDS, None), (('rep_mode',), 3)):
        for name in names:
            value = fields[name]
            if (type(value) is not int or value < 0
                    or (maximum is not None and value > maximum)):
                raise ValueError('invalid CPU state field: ' + name)
    for name in BOOL_FIELDS:
        if type(fields[name]) is not bool:
            raise ValueError('invalid CPU state field: ' + name)
    state = State8088()
    if set(vars(state)) != {'_' + field for field in FIELDS}:
        raise ValueError('State8088 fields changed; update the snapshot schema')
    for name in FIELDS:
        value = fields[name]
        if name == 'rep_mode':
            value = State8088.RepMode(value)
        setattr(state, '_' + name, value)
    return state
