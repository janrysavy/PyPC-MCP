"""Video components for a future stopped-machine snapshot bundle.

Caller must quiesce CPU and renderers. This component does not synchronize
them, advance the device, replay port writes or regenerate cached pixels.
Return a JSON manifest plus separate immutable binary buffers; a bundle can
compress those buffers without expanding them into JSON/base64 strings.
"""
import copy
import hashlib

from cga import CGA
from mda import MDA
from m6845 import M6845
from vga import VGA
from timingcodec import _integer, _validate


CLASSES = {'mda': MDA, 'cga': CGA, 'vga': VGA}
LINKS = {'_pic', '_b'}


def _bool(v):
    return type(v) is bool


def _byte(v):
    return _integer(v, 0, 255)


def _array(v, count, valid=_byte):
    return type(v) is list and len(v) == count and all(valid(item) for item in v)


def _spec(kind):
    spec = {name: (lambda v: _integer(v, 0)) for name in
            ('clock', 'last_update', 'frame_version')}
    spec.update({
        'hsync': _bool,
        'display_address': lambda v: _integer(v, 0, 65535),
        'ram_offset': lambda v: type(v) is int and v == (0xb0000 if kind == 'mda' else 0xb8000),
        'gf_width': lambda v: type(v) is int and v == 640,
        'gf_height': lambda v: type(v) is int and v in ((400, 480) if kind == 'vga' else (400,)),
        'palette': lambda v: _array(v, 256 if kind == 'vga' else 16,
                                     lambda row: _array(row, 3)),
    })
    if kind != 'mda':
        spec.update({
            'm6845_reg': _byte, 'graphics_mode': _byte,
            'cursor_location': lambda v: _integer(v, -1, 65535),
            'cga_mode': lambda v: _integer(v, 0, 3),
            'color_configuration': lambda v: _integer(v, 0, 3),
            'color_configuration_changed': _bool,
            'color_update_line_count': lambda v: _integer(v, 0, 200),
            'render_version': lambda v: _integer(v, 0),
            'pulse_vsync': _bool, 'palette_per_scanline': _bool,
            'palette_index': lambda v: _array(v, 200, lambda n: _integer(n, 0, 3)),
        })
    if kind == 'vga':
        spec.update({
            'sequencer': lambda v: _array(v, 5),
            'sequencer_reg': lambda v: _integer(v, 0, 31),
            'graphics': lambda v: _array(v, 9),
            'graphics_reg': lambda v: _integer(v, 0, 15),
            'latches': lambda v: _array(v, 4),
            'attributes': lambda v: _array(v, 21),
            'attribute_reg': lambda v: _integer(v, 0, 31),
            'attribute_flipflop': _bool,
            'dac': lambda v: _array(v, 256, lambda row: _array(row, 3, lambda n: _integer(n, 0, 63))),
            'dac_write_index': _byte, 'dac_read_index': _byte,
            'dac_write_component': lambda v: _integer(v, 0, 2),
            'dac_read_component': lambda v: _integer(v, 0, 2),
            'dac_state': lambda v: type(v) is int and v in (0, 3),
            'dac_pixel_mask': _byte, 'misc_output': _byte,
            'blink_phase': _bool, 'cursor_phase': _bool,
        })
    return spec


def _layout(video, kind):
    expected = {'_' + name for name in _spec(kind)} | {'_ram', '_pixels', '_font'}
    if kind != 'mda':
        expected.add('_m6845')
        if type(video._m6845) is not M6845 or set(vars(video._m6845)) != {'_registers'}:
            raise ValueError('CRTC layout changed; update snapshot schema')
    if kind == 'vga':
        expected.add('_planes')
    if type(video) is not CLASSES[kind] or set(vars(video)) - LINKS != expected:
        raise ValueError('video layout changed; update snapshot schema')


def _buffer_sizes(kind, fields):
    sizes = {'ram': 32768 if kind == 'vga' else 16384,
             'font': 2048, 'pixels': fields['gf_width'] * fields['gf_height'] * 4}
    if kind == 'vga':
        sizes.update({'plane' + str(i): 65536 for i in range(4)})
    return sizes


def dump_video_state(video):
    kind = next((name for name, cls in CLASSES.items() if type(video) is cls), None)
    if kind is None:
        raise ValueError('unsupported video device')
    _layout(video, kind)
    fields = {name: copy.deepcopy(getattr(video, '_' + name)) for name in _spec(kind)}
    fields['palette'] = [list(row) for row in fields['palette']]
    if kind != 'mda':
        if type(fields['cga_mode']) is not CGA.CGAMode:
            raise ValueError('invalid CGA mode')
        fields['cga_mode'] = fields['cga_mode'].value
        fields['crtc'] = list(video._m6845._registers)
    if kind == 'vga':
        fields['dac'] = [list(row) for row in fields['dac']]
        if type(video._planes) is not list or len(video._planes) != 4:
            raise ValueError('invalid VGA planes')
    if (type(video._font) is not tuple or len(video._font) != 3
            or video._font[:2] != (8, 8)):
        raise ValueError('unsupported fallback font')
    buffers = {'ram': bytes(video._ram), 'font': bytes(video._font[2]),
               'pixels': bytes(video._pixels)}
    if kind == 'vga':
        buffers.update({'plane' + str(i): bytes(plane)
                        for i, plane in enumerate(video._planes)})
    manifest = {'format': 'pypc.video', 'version': 1, 'kind': kind, 'fields': fields,
                'buffers': {name: {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                            for name, data in buffers.items()}}
    _validate_manifest(manifest, buffers)
    return manifest, buffers


def _validate_manifest(manifest, buffers):
    if (type(manifest) is not dict or set(manifest) != {'format', 'version', 'kind', 'fields', 'buffers'}
            or manifest['format'] != 'pypc.video'
            or type(manifest['version']) is not int or manifest['version'] != 1
            or type(manifest['kind']) is not str or manifest['kind'] not in CLASSES):
        raise ValueError('unsupported video manifest')
    kind, fields = manifest['kind'], manifest['fields']
    spec = _spec(kind)
    if kind != 'mda':
        spec['crtc'] = lambda v: _array(v, 18)
    _validate(fields, spec)
    if kind == 'vga':
        # BIOS/register inference selects G320 for 13h, but the inherited
        # CGA mode port selects G640 for the same byte. Both are reachable.
        modes = {0x12: (3,), 0x13: (2, 3)}
        if fields['graphics_mode'] in modes and fields['cga_mode'] not in modes[fields['graphics_mode']]:
            raise ValueError('inconsistent VGA graphics/CGA modes')
        # Do not require frame height to match the mode: port 3D8 changes
        # the mode without resizing cached pixels; GetFrame does that later.
    sizes = _buffer_sizes(kind, fields)
    descriptors = manifest['buffers']
    if (type(buffers) is not dict or set(buffers) != set(sizes)
            or type(descriptors) is not dict or set(descriptors) != set(sizes)):
        raise ValueError('invalid video buffer inventory')
    for name, size in sizes.items():
        data, descriptor = buffers[name], descriptors[name]
        if (type(data) is not bytes or len(data) != size
                or type(descriptor) is not dict or set(descriptor) != {'size', 'sha256'}
                or type(descriptor['size']) is not int or descriptor['size'] != size
                or descriptor['sha256'] != hashlib.sha256(data).hexdigest()):
            raise ValueError('invalid video buffer: ' + name)


def load_video_state(manifest, buffers):
    """Validate all bytes before constructing a detached, unbound device."""
    _validate_manifest(manifest, buffers)
    kind, fields = manifest['kind'], manifest['fields']
    video = MDA() if kind == 'mda' else CLASSES[kind](fields['palette_per_scanline'])
    _layout(video, kind)
    for name in _spec(kind):
        value = copy.deepcopy(fields[name])
        if name == 'cga_mode':
            value = CGA.CGAMode(value)
        elif name in ('palette', 'dac'):
            value = [tuple(row) for row in value]
        setattr(video, '_' + name, value)
    video._ram = bytearray(buffers['ram'])
    video._font = (8, 8, tuple(buffers['font']))
    video._pixels = bytearray(buffers['pixels'])
    if kind != 'mda':
        video._m6845._registers = list(fields['crtc'])
    if kind == 'vga':
        video._planes = [bytearray(buffers['plane' + str(i)]) for i in range(4)]
    return video
