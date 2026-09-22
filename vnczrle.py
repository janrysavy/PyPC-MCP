"""Lossless ZRLE encoder for native little-endian RGB24-in-32 pixels.

RFC 6143 section 7.7.6. Raw 64x64 tiles retain fast C-level zlib compression.
A compressor belongs to one VNC connection and persists across rectangles.
"""
import struct
import zlib


class Encoder:
    def __init__(self, level=1):
        self.stream = zlib.compressobj(level)

    def encode(self, bgra, width, height):
        if width < 1 or height < 1 or len(bgra) != width * height * 4:
            raise ValueError('invalid ZRLE rectangle')
        source = bytes(bgra)  # C-level strided byte copies outperform memoryview iteration.
        compact = bytearray(width * height * 3)
        compact[0::3] = source[0::4]
        compact[1::3] = source[1::4]
        compact[2::3] = source[2::4]
        tiles = bytearray()
        for y in range(0, height, 64):
            for x in range(0, width, 64):
                tiles.append(0)  # Raw tile; zlib handles repeated glyph patterns.
                for row in range(y, min(y + 64, height)):
                    start = (row * width + x) * 3
                    tiles.extend(compact[start:start + min(64, width - x) * 3])
        data = self.stream.compress(tiles) + self.stream.flush(zlib.Z_SYNC_FLUSH)
        return struct.pack('!I', len(data)) + data
