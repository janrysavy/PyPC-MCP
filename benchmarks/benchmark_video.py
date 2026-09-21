"""Small repeatable benchmarks for the emulator video/VNC hot paths.

The benchmark deliberately separates three costs:

* converting the renderer's native BGRX buffer for VNC;
* rendering VGA text and graphics frames;
* sending unchanged and small-change VNC updates through the server's frame
  path.

Run from the repository root with Python 3.12+:

    python benchmarks/benchmark_video.py --frames 20 --repeats 5

The output is JSON so before/after runs can be compared without relying on
wall-clock claims embedded in the source tree.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from dataclasses import dataclass

from cga import CGA
from vga import VGA
from vncpixel import NATIVE_FORMAT, PixelFormat
from vncserver import VNCServer


@dataclass
class Sink:
    bytes_sent: int = 0

    def sendall(self, data):
        self.bytes_sent += len(data)


def timed(function, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        function()
        samples.append(time.perf_counter() - start)
    return {
        'samples_seconds': samples,
        'median_seconds': statistics.median(samples),
        'min_seconds': min(samples),
    }


def make_video(mode):
    video = VGA(False)
    video.BiosSetMode(mode)
    if mode == 0x13:
        for plane in range(4):
            video._planes[plane][:] = bytes(
                ((index * 17 + plane * 29) & 0xff)
                for index in range(0x10000))
    elif mode == 0x12:
        for plane in range(4):
            video._planes[plane][:] = bytes(
                ((index * 13 + plane * 37) & 0xff)
                for index in range(0x10000))
    else:
        for index in range(0, len(video._ram), 2):
            video._ram[index] = (index // 2) & 0xff
            video._ram[index + 1] = ((index // 16) ^ 0x1f) & 0xff
    return video


def make_cga(mode):
    video = CGA(False)
    if mode == 'text':
        video.IO_Write(0x3d8, 1)
        for index in range(0, len(video._ram), 2):
            video._ram[index] = (index // 2) & 0xff
            video._ram[index + 1] = ((index // 16) ^ 0x1f) & 0xff
    else:
        video.IO_Write(0x3d8, 0x02 if mode == '320' else 0x12)
        for index in range(len(video._ram)):
            video._ram[index] = (index * 23) & 0xff
    return video


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frames', type=int, default=20)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()

    native = bytearray((index * 37) & 0xff for index in range(640 * 400 * 4))
    legacy_list = list(native)
    rgb565 = PixelFormat(bits=16, depth=16, red_max=31, green_max=63,
                         blue_max=31, red_shift=11, green_shift=5,
                         blue_shift=0)

    results = {
        'native_bytearray_encode': timed(
            lambda: [NATIVE_FORMAT.encode_bgra(native)
                     for _ in range(args.frames)], args.repeats),
        'legacy_list_native_encode': timed(
            lambda: [NATIVE_FORMAT.encode_bgra(legacy_list)
                     for _ in range(args.frames)], args.repeats),
        'rgb565_encode': timed(
            lambda: [rgb565.encode_bgra(native)
                     for _ in range(args.frames)], args.repeats),
    }

    for mode, name in ((3, 'vga_text_render'),
                       (0x13, 'vga_mode13_render'),
                       (0x12, 'vga_mode12_render')):
        video = make_video(mode)
        results[name] = timed(
            lambda: [video.GetFrame() for _ in range(args.frames)],
            args.repeats)

    for mode in ('text', '320', '640'):
        video = make_cga(mode)
        results[f'cga_{mode}_render'] = timed(
            lambda: [video.GetFrame() for _ in range(args.frames)],
            args.repeats)

    cached_video = make_video(3)
    cached_server = VNCServer.__new__(VNCServer)
    cached_server._display = cached_video
    cached_server._frame_cache = None
    cached_server._frame_cache_version = None
    cached_server._get_frame()  # warm the cache before measuring steady state
    results['vga_text_cached_vnc_render'] = timed(
        lambda: [cached_server._get_frame() for _ in range(args.frames)],
        args.repeats)

    incremental_video = make_video(3)
    incremental_server = VNCServer.__new__(VNCServer)
    incremental_server._display = incremental_video
    incremental_server._compatible = False
    incremental_server._compatible_width = 640
    incremental_server._compatible_height = 400
    incremental_server._frame_cache = None
    incremental_server._frame_cache_version = None
    incremental_session = VNCServer.VNCSession()
    incremental_session.stream = Sink()
    incremental_session.incremental = True
    incremental_server.VNCSendFrame(incremental_session)
    incremental_session.stream.bytes_sent = 0
    results['vnc_incremental_unchanged'] = timed(
        lambda: [incremental_server.VNCSendFrame(incremental_session)
                 for _ in range(args.frames)], args.repeats)
    results['vnc_incremental_unchanged']['bytes_per_sample'] = \
        incremental_session.stream.bytes_sent // args.repeats

    changed_video = make_video(3)
    changed_server = VNCServer.__new__(VNCServer)
    changed_server._display = changed_video
    changed_server._compatible = False
    changed_server._compatible_width = 640
    changed_server._compatible_height = 400
    changed_server._frame_cache = None
    changed_server._frame_cache_version = None
    changed_session = VNCServer.VNCSession()
    changed_session.stream = Sink()
    changed_session.incremental = True
    changed_server.VNCSendFrame(changed_session)
    changed_session.stream.bytes_sent = 0
    change_index = [0]

    def send_small_change():
        cell = change_index[0] % 80
        changed_video.WriteByte(0xb8000 + cell * 2,
                                (ord('A') + change_index[0]) & 0xff)
        changed_server.VNCSendFrame(changed_session)
        change_index[0] += 1

    results['vnc_incremental_small_change'] = timed(
        send_small_change, args.repeats * args.frames)
    results['vnc_incremental_small_change']['bytes_per_update'] = \
        changed_session.stream.bytes_sent // (args.repeats * args.frames)

    video = make_video(3)
    frame = video.GetFrame()
    results['native_vnc_payload_bytes'] = {
        'bytes_per_frame': len(frame[2]),
        'frames': args.frames,
        'total_bytes': len(frame[2]) * args.frames,
    }
    results['parameters'] = vars(args)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
