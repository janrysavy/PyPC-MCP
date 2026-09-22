"""Stopped XTIDE controller component; disk bytes are a separate dependency.

Caller must stop the CPU and filesystem writers. Load binds already-validated
disks in their saved slot order. This module checks count, not disk contents;
the machine snapshot layer must verify/capture every backing disk separately.
No I/O command or disk write is executed during capture or detached load.
"""
import copy
import hashlib

from timingcodec import _integer, _validate
from xtide import XTIDE


SCALARS = {
    'status_register': lambda v: _integer(v, 0, 255),
    'error_register': lambda v: _integer(v, 0, 255),
    'drv': lambda v: _integer(v, 0, 1),
    'sector_buffer_offset': lambda v: _integer(v, 0, 256 * 512),
    'target_lba': lambda v: _integer(v, 0),
    'target_drive': lambda v: _integer(v, 0, 1) or type(v) is int and v == 255,
    'cylinder_count': lambda v: _integer(v, 1, 65535),
    'head_count': lambda v: _integer(v, 1, 16),
    'sectors_per_track': lambda v: _integer(v, 1, 255),
    'registers': lambda v: type(v) is list and len(v) == 8 and all(_integer(n, 0, 255) for n in v),
    'serial_numbers': lambda v: type(v) is list and len(v) <= 2 and all(
        type(s) is str and 1 <= len(s) <= 20 and s.isascii() for s in v),
}


def _layout(device):
    expected = {'_' + name for name in SCALARS} | {'_disks', '_sector_buffer'}
    if type(device) is not XTIDE or set(vars(device)) - {'_b', '_pic'} != expected:
        raise ValueError('XTIDE layout changed; update snapshot schema')


def _validate_payload(manifest, buffer):
    if (type(manifest) is not dict or set(manifest) != {'format', 'version', 'fields', 'buffer'}
            or manifest['format'] != 'pypc.xtide'
            or type(manifest['version']) is not int or manifest['version'] != 1):
        raise ValueError('unsupported XTIDE manifest')
    fields = manifest['fields']
    _validate(fields, SCALARS)
    descriptor = manifest['buffer']
    if (type(buffer) is not bytes or not 512 <= len(buffer) <= 256 * 512
            or len(buffer) % 512 or type(descriptor) is not dict
            or set(descriptor) != {'size', 'sha256'}
            or type(descriptor['size']) is not int or descriptor['size'] != len(buffer)
            or descriptor['sha256'] != hashlib.sha256(buffer).hexdigest()):
        raise ValueError('invalid XTIDE buffer')
    if fields['sector_buffer_offset'] > len(buffer):
        raise ValueError('XTIDE cursor exceeds buffer')
    capacity = fields['cylinder_count'] * fields['head_count'] * fields['sectors_per_track']
    if fields['target_lba'] >= capacity:
        raise ValueError('XTIDE target exceeds geometry')
    target = fields['target_drive']
    if target != 255 and target >= len(fields['serial_numbers']):
        raise ValueError('XTIDE pending write targets absent disk')
    # Do not infer target/length from current command registers. They can be
    # rewritten after a partial write without cancelling its pending target.


def dump_xtide_state(device):
    _layout(device)
    fields = {name: copy.deepcopy(getattr(device, '_' + name)) for name in SCALARS}
    if len(device._disks) != len(fields['serial_numbers']):
        raise ValueError('XTIDE disk inventory changed')
    buffer = bytes(device._sector_buffer)
    manifest = {'format': 'pypc.xtide', 'version': 1, 'fields': fields,
                'buffer': {'size': len(buffer), 'sha256': hashlib.sha256(buffer).hexdigest()}}
    _validate_payload(manifest, buffer)
    return manifest, buffer


def load_xtide_state(manifest, buffer, disks):
    """Construct a detached controller; disks must be separately validated."""
    _validate_payload(manifest, buffer)
    if type(disks) is not list or len(disks) != len(manifest['fields']['serial_numbers']):
        raise ValueError('XTIDE disk inventory mismatch')
    device = XTIDE(list(disks))
    _layout(device)
    for name, value in manifest['fields'].items():
        setattr(device, '_' + name, copy.deepcopy(value))
    device._sector_buffer = bytearray(buffer)
    return device
