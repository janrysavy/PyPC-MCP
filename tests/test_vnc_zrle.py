import socket
import struct
import unittest
import zlib

from vnczrle import Encoder
from vncpixel import PixelFormat
from vncserver import VNCServer


class ZRLETests(unittest.TestCase):
    def test_native_tiles_and_persistent_stream(self):
        encoder = Encoder()
        decoder = zlib.decompressobj()
        for width, height in ((65, 67), (1, 1), (64, 64)):
            pixels = bytes((n % 251 for n in range(width * height * 4)))
            encoded = encoder.encode(pixels, width, height)
            self.assertEqual(struct.unpack('!I', encoded[:4])[0], len(encoded) - 4)
            data = decoder.decompress(encoded[4:])
            offset = 0
            for y in range(0, height, 64):
                for x in range(0, width, 64):
                    self.assertEqual(data[offset], 0)
                    offset += 1
                    for row in range(y, min(y + 64, height)):
                        for col in range(x, min(x + 64, width)):
                            source = (row * width + col) * 4
                            self.assertEqual(data[offset:offset + 3], pixels[source:source + 3])
                            offset += 3
            self.assertEqual(offset, len(data))

    def test_negotiation_and_pixel_format_fallback(self):
        class Display:
            def GetFrame(self):
                return 2, 1, b'\x01\x02\x03\x00' * 2

        server = VNCServer.__new__(VNCServer)
        server._display = Display()
        server._compatible = False
        for encodings, fmt, expected in (
                ((16, 0), PixelFormat(), 16),
                ((0, 16), PixelFormat(), 0),
                ((16,), PixelFormat(bits=16, depth=16, red_max=31,
                                    green_max=63, blue_max=31,
                                    red_shift=11, green_shift=5), 0)):
            with self.subTest(encodings=encodings, fmt=fmt):
                stream, client = socket.socketpair()
                with stream, client:
                    stream.settimeout(1)
                    client.settimeout(1)
                    session = VNCServer.VNCSession()
                    session.stream = stream
                    session.pixel_format = fmt
                    client.sendall(struct.pack('!BBH', 2, 0, len(encodings)) +
                                   b''.join(struct.pack('!i', e) for e in encodings))
                    self.assertTrue(server.VNCWaitForEvent(session))
                    server.VNCSendFrame(session)
                    header = server.RecvExact(client, 16)
                    self.assertEqual(struct.unpack('!i', header[12:])[0], expected)
                    if expected == 16:
                        size = struct.unpack('!I', server.RecvExact(client, 4))[0]
                        data = zlib.decompressobj().decompress(server.RecvExact(client, size))
                        self.assertEqual(data, b'\0' + b'\x01\x02\x03' * 2)
                    else:
                        server.RecvExact(client, 2 * fmt.bits // 8)

    def test_invalid_dimensions(self):
        for pixels, width, height in ((b'', 0, 1), (b'', 1, 1), (b'', -1, 1)):
            with self.assertRaises(ValueError):
                Encoder().encode(pixels, width, height)
