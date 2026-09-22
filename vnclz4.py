"""Optional private PyPC LZ4 rectangle encoding, version 1.

Encoding 0x50594c34 ('PYL4'), explicitly negotiated, is not an IANA assignment.
Body: big-endian uint32 compressed length, then one independent LZ4 block.
Decoded bytes are exactly width*height*4 native B,G,R,padding bytes.
"""
import struct
try:
    import lz4.block as _lz4
except ImportError:
    _lz4 = None

ENCODING = 0x50594c34
AVAILABLE = _lz4 is not None


def encode(pixels, width, height):
    if not AVAILABLE:
        raise RuntimeError('optional lz4 package is unavailable')
    if width < 1 or height < 1 or len(pixels) != width * height * 4:
        raise ValueError('invalid LZ4 rectangle')
    data = _lz4.compress(pixels, mode='fast', acceleration=1, store_size=False)
    return struct.pack('!I', len(data)) + data
