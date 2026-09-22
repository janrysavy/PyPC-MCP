"""RFB true-color pixel formats (RFC 6143 sections 7.4 and 7.5.1).

Display devices produce B,G,R,padding bytes. The native wire format describes
that layout exactly and avoids conversion; other client formats are per-session.
"""
from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class PixelFormat:
    bits: int = 32
    depth: int = 24
    big_endian: int = 0
    true_color: int = 1
    red_max: int = 255
    green_max: int = 255
    blue_max: int = 255
    red_shift: int = 16
    green_shift: int = 8
    blue_shift: int = 0

    def __post_init__(self):
        if self.bits not in (8, 16, 32) or not 1 <= self.depth <= self.bits:
            raise ValueError('VNC requires 8, 16 or 32 bits per pixel and a valid depth')
        if self.big_endian not in (0, 1) or self.true_color != 1:
            raise ValueError('VNC supports true-color formats with a boolean byte order')
        mask = 0
        useful_bits = 0
        for maximum, shift in self.channels:
            if maximum < 1 or maximum > 65535 or maximum & (maximum + 1):
                raise ValueError('VNC color maxima must be 2**n - 1')
            if shift < 0 or shift + maximum.bit_length() > self.bits:
                raise ValueError('VNC color field exceeds the pixel width')
            field = maximum << shift
            if field & mask:
                raise ValueError('VNC color fields must not overlap')
            mask |= field
            useful_bits += maximum.bit_length()
        if useful_bits > self.depth:
            raise ValueError('VNC color fields exceed the declared depth')

    @property
    def channels(self):
        return ((self.red_max, self.red_shift), (self.green_max, self.green_shift),
                (self.blue_max, self.blue_shift))

    @classmethod
    def from_bytes(cls, data):
        if len(data) != 16:
            raise ValueError('VNC pixel format must contain 16 bytes')
        return cls(*struct.unpack('>4B3H3B3x', data))

    def to_bytes(self):
        return struct.pack('>4B3H3B3x', self.bits, self.depth, self.big_endian,
                           self.true_color, self.red_max, self.green_max,
                           self.blue_max, self.red_shift, self.green_shift,
                           self.blue_shift)

    def encode_bgra(self, pixels):
        if len(pixels) % 4:
            raise ValueError('display framebuffer must contain four-byte pixels')
        if (self.bits == 32 and not self.big_endian and
                self.channels == ((255, 16), (255, 8), (255, 0))):
            # socket.sendall accepts bytes-like objects. Keep the renderer's
            # bytearray/memoryview alive and avoid a per-frame list/bytes copy.
            if isinstance(pixels, (bytes, bytearray, memoryview)):
                return pixels
            return bytes(pixels)
        try:
            source = memoryview(pixels).cast('B')
        except TypeError:
            # Keep compatibility with callers that still provide a sequence
            # of channel integers rather than a bytes-like framebuffer.
            source = memoryview(bytes(pixels))
        if self.bits == 32 and all(maximum == 255 and shift % 8 == 0
                                   for maximum, shift in self.channels):
            # Common 32-bit endian/channel permutations use C-level slice copies.
            output = bytearray(len(source))
            for source_byte, (_, shift) in zip((2, 1, 0), self.channels):
                destination_byte = 3 - shift // 8 if self.big_endian else shift // 8
                output[destination_byte::4] = source[source_byte::4]
            return bytes(output)
        output = bytearray()
        palette = {}
        stride = self.bits // 8
        byte_order = 'big' if self.big_endian else 'little'
        for offset in range(0, len(source), 4):
            color = (source[offset], source[offset + 1], source[offset + 2])
            encoded = palette.get(color)
            if encoded is None:
                blue, green, red = color
                value = 0
                for component, (maximum, shift) in zip((red, green, blue), self.channels):
                    value |= ((component * maximum + 127) // 255) << shift
                encoded = value.to_bytes(stride, byte_order)
                palette[color] = encoded
            output.extend(encoded)
        return bytes(output)


NATIVE_FORMAT = PixelFormat()
