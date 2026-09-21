"""Decode real RFB bytes, not the server's internal pixel representation."""
import contextlib
import socket
import struct
import unittest
from unittest.mock import Mock

from vncserver import VNCServer


def pixel_format(bits=32, depth=24, big=0, red=255, green=255, blue=255,
                 rshift=16, gshift=8, bshift=0, true=1):
    return struct.pack('>4B3H3B3x', bits, depth, big, true,
                       red, green, blue, rshift, gshift, bshift)


def decode(data, fmt):
    bits, depth, big, true, red, green, blue, rs, gs, bs = struct.unpack('>4B3H3B3x', fmt)
    stride = bits // 8
    return [tuple(((int.from_bytes(data[i:i + stride], 'big' if big else 'little') >> s)
                   & maximum) * 255 // maximum
                  for maximum, s in ((red, rs), (green, gs), (blue, bs)))
            for i in range(0, len(data), stride)]


@contextlib.contextmanager
def connection(compatible=False):
    server = VNCServer.__new__(VNCServer)
    server._compatible = compatible
    server._compatible_width = 5
    server._compatible_height = 2
    # Devices supply B,G,R,padding bytes; test black and asymmetric colors.
    colors = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
    data = bytes(component for red, green, blue in colors
                 for component in (blue, green, red, 255))
    server._display = Mock(GetFrame=lambda: (4, 1, data))
    server._kb = None
    stream, client = socket.socketpair()
    stream.settimeout(1)
    client.settimeout(1)
    session = VNCServer.VNCSession()
    session.stream = stream
    try:
        client.sendall(b'\x01')
        server.VNCClientServerInit(stream)
        init = server.RecvExact(client, 24)
        server.RecvExact(client, int.from_bytes(init[20:24], 'big'))
        yield server, session, client, init[4:20], colors
    finally:
        stream.close()
        client.close()


class VNCPixelFormatTests(unittest.TestCase):
    def read_frame(self, server, session, client, fmt):
        server.VNCSendFrame(session)
        header = server.RecvExact(client, 16)
        self.assertEqual(header[:4], b'\x00\x00\x00\x01')
        x, y, width, height, encoding = struct.unpack('>4Hi', header[4:])
        self.assertEqual((x, y, encoding), (0, 0, 0))
        pixels = server.RecvExact(client, width * height * (fmt[0] // 8))
        return decode(pixels, fmt)

    def test_advertised_native_format_decodes_black_and_primaries(self):
        with connection() as (server, session, client, fmt, colors):
            self.assertEqual(self.read_frame(server, session, client, fmt), colors)

    def test_native_depth_counts_color_bits_not_padding(self):
        with connection() as (_, _, _, fmt, _):
            self.assertEqual((fmt[0], fmt[1]), (32, 24))

    def test_requested_formats_are_honored_on_the_wire(self):
        formats = [pixel_format(big=1),
                   pixel_format(rshift=0, gshift=8, bshift=16),
                   pixel_format(bits=16, depth=16, red=31, green=63, blue=31,
                                rshift=11, gshift=5),
                   pixel_format(bits=16, depth=16, big=1, red=31, green=63, blue=31,
                                rshift=11, gshift=5),
                   pixel_format(bits=8, depth=8, red=7, green=7, blue=3,
                                rshift=5, gshift=2)]
        for fmt in formats:
            with self.subTest(format=fmt.hex()):
                with connection() as (server, session, client, native, colors):
                    client.sendall(b'\x00\x00\x00\x00' + fmt)
                    self.assertTrue(server.VNCWaitForEvent(session))
                    self.assertEqual(self.read_frame(server, session, client, fmt), colors)

    def test_compatible_padding_uses_the_negotiated_format(self):
        fmt = pixel_format(bits=16, depth=16, red=31, green=63, blue=31,
                           rshift=11, gshift=5)
        with connection(compatible=True) as (server, session, client, _, colors):
            client.sendall(b'\x00\x00\x00\x00' + fmt)
            self.assertTrue(server.VNCWaitForEvent(session))
            self.assertEqual(self.read_frame(server, session, client, fmt),
                             colors + [(0, 0, 0)] * 6)

    def test_invalid_format_closes_session_instead_of_silent_misencoding(self):
        with connection() as (server, session, client, _, _):
            client.sendall(b'\x00\x00\x00\x00' + pixel_format(bits=24))
            self.assertFalse(server.VNCWaitForEvent(session))


class PixelEncodingTests(unittest.TestCase):
    def test_native_bytes_are_not_copied(self):
        from vncpixel import NATIVE_FORMAT
        pixels = bytes((0x33, 0x22, 0x11, 0xff))
        self.assertIs(NATIVE_FORMAT.encode_bgra(pixels), pixels)
        self.assertEqual(NATIVE_FORMAT.to_bytes(), pixel_format())

    def test_invalid_fields_are_rejected(self):
        from vncpixel import PixelFormat
        for fmt in (pixel_format(true=0), pixel_format(big=2),
                    pixel_format(red=30), pixel_format(rshift=8),
                    pixel_format(rshift=31), pixel_format(depth=16), b''):
            with self.subTest(format=fmt.hex()):
                with self.assertRaises(ValueError):
                    PixelFormat.from_bytes(fmt)

    def test_asymmetric_color_and_per_session_independence(self):
        from vncpixel import PixelFormat, NATIVE_FORMAT
        pixels = bytes((0x33, 0x22, 0x11, 0xff))
        first, second = VNCServer.VNCSession(), VNCServer.VNCSession()
        first.pixel_format = PixelFormat.from_bytes(pixel_format(big=1))
        self.assertEqual(first.pixel_format.encode_bgra(pixels), b'\x00\x11\x22\x33')
        self.assertIs(second.pixel_format, NATIVE_FORMAT)
        self.assertEqual(second.pixel_format.encode_bgra(pixels), pixels)

    def test_rgb565_quantization_and_empty_frames(self):
        from vncpixel import PixelFormat
        fmt = PixelFormat.from_bytes(pixel_format(bits=16, depth=16, red=31,
                                                   green=63, blue=31,
                                                   rshift=11, gshift=5))
        self.assertEqual(fmt.encode_bgra(b''), b'')
        self.assertEqual(fmt.encode_bgra(bytes((128, 128, 128, 255))), b'\x10\x84')
        with self.assertRaises(ValueError):
            fmt.encode_bgra(b'\x00')


if __name__ == '__main__':
    unittest.main()
