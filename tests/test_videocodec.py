"""Cross-process video continuation; binary buffers stay outside the manifest."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from cga import CGA
from mda import MDA
from vga import VGA, BLINK_HALF_PERIOD_CYCLES
from videocodec import dump_video_state, load_video_state


def register(video, port, index, value):
    video.IO_Write(port, index)
    video.IO_Write(port + 1, value)


def fixture(kind, write_mode=1):
    if kind == 'mda':
        video = MDA()
        video.IO_Read(0x3ba)
        video.WriteByte(0xb0000, 65)
        video.WriteByte(0xb0001, 0x1e)
    elif kind == 'cga':
        video = CGA(True)
        video.IO_Write(0x3d8, 2)
        video.IO_Write(0x3d9, 0x30)
        video.WriteByte(0xb8000, 0xa5)
        video.Tick(1, 16 * 304)
    else:
        video = VGA(False)
        if kind == 'text':
            video.WriteByte(0xb8002, 65)
            video.WriteByte(0xb8003, 0x9e)
            register(video, 0x3c4, 2, 4)
            for row in range(16):
                video.WriteByte(0xa0000 + 65 * 32 + row, 0x80 if row == 0 else 0)
            register(video, 0x3c4, 2, 3)
            for index, value in ((12, 0), (13, 1), (14, 0), (15, 2), (10, 0), (11, 1)):
                register(video, 0x3d4, index, value)
        elif kind == 'planar':
            video.BiosSetMode(0x12)
            for plane, value in enumerate((0x12, 0x34, 0x56, 0x78)):
                register(video, 0x3c4, 2, 1 << plane)
                video.WriteByte(0xa0000, value)
            register(video, 0x3c4, 2, 15)
            video.ReadByte(0xa0000)  # Load all latches before the checkpoint.
            register(video, 0x3ce, 5, write_mode)
        else:
            video.BiosSetMode(0x13)
            for offset in range(16):
                video.WriteByte(0xa0000 + offset, 0x10 + offset)
        video.Tick(1, BLINK_HALF_PERIOD_CYCLES + 304 * 17)
        video.IO_Write(0x3c8, 14)
        video.IO_Write(0x3c9, 7)  # Partial RGB write: green is next.
        video.IO_Write(0x3c7, 4)
        video.IO_Read(0x3c9)     # Partial RGB read: green is next.
        video.IO_Read(0x3da)
        video.IO_Write(0x3c0, 0x10)  # Attribute data byte is next.
    video.GetFrame()
    return video


def frame(video):
    width, height, pixels = video.GetFrame()
    return [width, height, hashlib.sha256(pixels).hexdigest()]


def replay(video, kind):
    initial, _ = dump_video_state(video)
    ports, observations = [], []
    if kind not in ('mda', 'cga'):
        # Finish pending operations before any status read clears the AC latch.
        video.IO_Write(0x3c0, 8)
        video.IO_Write(0x3c9, 9)
        video.IO_Write(0x3c9, 11)
        ports += [video.IO_Read(0x3c9), video.IO_Read(0x3c9)]
        if kind != 'text':
            video.WriteByte(0xa0001, 0xa5)
            observations = [plane[1] for plane in video._planes]
            ports.append(video.ReadByte(0xa0001))
        ports += [video.IO_Read(0x3da), video.IO_Read(0x3c1)]
    else:
        port = 0x3ba if kind == 'mda' else 0x3da
        ports += [video.IO_Read(port), video.IO_Read(port)]
    frames = [frame(video)]
    clock = video._clock
    for elapsed in (304, BLINK_HALF_PERIOD_CYCLES, 304 * 217):
        clock += elapsed
        video.Tick(elapsed, clock)
        frames.append(frame(video))
    final, _ = dump_video_state(video)
    return {'initial': initial, 'ports': ports, 'memory': observations,
            'frames': frames, 'final': final}


def fresh(manifest, buffers, kind):
    # Hex is IPC test plumbing only; production components return raw bytes.
    data = {'manifest': manifest, 'buffers': {k: v.hex() for k, v in buffers.items()},
            'kind': kind}
    program = '''
import json, sys, typing
if not hasattr(typing, 'override'):
    typing.override = lambda method: method
sys.path.insert(0, 'tests')
from test_videocodec import replay
from videocodec import load_video_state
data = json.load(sys.stdin)
video = load_video_state(data['manifest'], {k: bytes.fromhex(v) for k,v in data['buffers'].items()})
print(json.dumps(replay(video, data['kind'])))
'''
    result = subprocess.run([sys.executable, '-c', program], input=json.dumps(data),
                            capture_output=True, text=True, timeout=30, check=True,
                            cwd=Path(__file__).resolve().parents[1])
    return json.loads(result.stdout)


@pytest.mark.parametrize('kind', ['mda', 'cga', 'text', 'planar', 'chain4'])
def test_fresh_process_matches_video_ports_planes_and_frames(kind):
    video = fixture(kind)
    manifest, buffers = dump_video_state(video)
    expected = replay(video, kind)
    assert fresh(manifest, buffers, kind) == expected
    if kind == 'planar':
        assert expected['memory'] == [0x12, 0x34, 0x56, 0x78]
    elif kind == 'mda':
        assert expected['ports'] == [9, 0]
    if kind not in ('mda', 'cga'):
        assert expected['final']['fields']['attributes'][16] == 8
        assert expected['final']['fields']['dac'][14] == [7, 9, 11]


@pytest.mark.parametrize('mode', [0, 2, 3])
def test_other_planar_write_modes_continue_from_saved_latches(mode):
    video = fixture('planar', mode)
    manifest, buffers = dump_video_state(video)
    assert fresh(manifest, buffers, 'planar') == replay(video, 'planar')


@pytest.mark.parametrize('lost', ['attribute_flipflop', 'dac_write_component', 'latches', 'font'])
def test_lost_state_changes_continuation(lost):
    kind = 'planar' if lost == 'latches' else 'text'
    video = fixture(kind)
    manifest, buffers = dump_video_state(video)
    expected = replay(video, kind)
    if lost == 'font':
        data = bytearray(buffers['plane2'])
        data[65 * 32] = 0x40
        buffers['plane2'] = bytes(data)
        manifest['buffers']['plane2']['sha256'] = hashlib.sha256(data).hexdigest()
    else:
        manifest['fields'][lost] = ([0] * 4 if lost == 'latches'
                                    else False if lost == 'attribute_flipflop' else 0)
    actual = fresh(manifest, buffers, kind)
    # Ignore initial state; demand a difference after executing the continuation.
    assert (actual['frames'], actual['memory']) != (expected['frames'], expected['memory'])


@pytest.mark.parametrize('buffer', ['ram', 'font', 'pixels', 'plane0', 'plane2'])
def test_corrupt_buffers_are_rejected_before_live_state_changes(buffer):
    video = fixture('text')
    manifest, buffers = dump_video_state(video)
    before = copy.deepcopy(manifest)
    damaged = dict(buffers)
    data = bytearray(damaged[buffer])
    data[0] ^= 1
    damaged[buffer] = bytes(data)
    with pytest.raises(ValueError, match='invalid video buffer'):
        load_video_state(manifest, damaged)
    assert manifest == before
    assert dump_video_state(video) == (manifest, buffers)


@pytest.mark.parametrize('mutation', [
    lambda m: m.update(version=True), lambda m: m.update(kind='dosbox-x'),
    lambda m: m['fields'].update(dac_read_component=3),
    lambda m: m['fields'].update(gf_height=1000000),
    lambda m: m['fields']['crtc'].pop(),
    lambda m: m['buffers']['pixels'].update(size=1),
    lambda m: m['fields'].pop('latches'),
])
def test_malformed_manifest_is_rejected(mutation):
    manifest, buffers = dump_video_state(fixture('text'))
    mutation(manifest)
    with pytest.raises(ValueError):
        load_video_state(manifest, buffers)


def test_detached_buffers_and_layout_guard():
    video = fixture('text')
    manifest, buffers = dump_video_state(video)
    loaded = load_video_state(manifest, buffers)
    loaded._ram[0] ^= 1
    loaded._planes[2][0] ^= 1
    loaded._palette[0] = (1, 2, 3)
    assert dump_video_state(video) == (manifest, buffers)
    video._new_latch = 1
    with pytest.raises(ValueError, match='layout changed'):
        dump_video_state(video)
