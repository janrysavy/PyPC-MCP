import socket
import struct
import unittest

from vncspeed import SpeedDisplay
from vncserver import VNCServer


class Display:
    def __init__(self, width=160, height=40):
        self.width, self.height = width, height
        self.pixels = bytearray(b'\x12\x34\x56\xff' * width * height)
        self.version = 1

    def GetFrameVersion(self):
        return self.version

    def GetFrame(self):
        return self.width, self.height, self.pixels


class SpeedTests(unittest.TestCase):
    def setUp(self):
        self.wall = 0
        self.ticks = 0
        self.display = Display()
        self.overlay = SpeedDisplay(self.display, lambda: self.ticks,
                                    lambda: self.wall)

    def test_speed_pause_reset_and_sampling(self):
        first = self.overlay.GetFrameVersion()
        self.wall = .5
        self.ticks = 2_385_000
        self.assertEqual(self.overlay.GetFrameVersion(), first)
        self.wall = 2
        self.ticks = 4_770_000
        self.overlay.GetFrameVersion()
        self.assertEqual(self.overlay.text, 'EMU 0.50x')
        self.wall = 3
        self.overlay.GetFrameVersion()
        self.assertEqual(self.overlay.text, 'EMU 0.00x')
        self.ticks = 0
        self.overlay.GetFrameVersion()
        self.assertEqual(self.overlay.text, 'EMU --.--x')
        self.wall = 4
        self.ticks = 9_540_000
        self.overlay.GetFrameVersion()
        self.assertEqual(self.overlay.text, 'EMU 2.00x')

    def test_only_top_right_pixels_change_and_guest_is_untouched(self):
        original = bytes(self.display.pixels)
        width, height, pixels = self.overlay.GetFrame()
        left = width - len(self.overlay.text) * 8
        self.assertNotEqual(pixels, original)
        self.assertEqual(self.display.pixels, original)
        self.assertEqual(pixels[8 * width * 4:], original[8 * width * 4:])
        for y in range(8):
            start = y * width * 4
            self.assertEqual(pixels[start:start + left * 4],
                             original[start:start + left * 4])
        self.assertIs(self.overlay.GetFrame()[2], pixels)
        self.display.version += 1
        self.overlay.GetFrameVersion()
        self.assertIsNot(self.overlay.GetFrame()[2], pixels)

    def test_small_frame_is_clipped(self):
        for width, height in ((4, 2), (8, 1), (16, 4)):
            display = Display(width, height)
            overlay = SpeedDisplay(display, lambda: 0, lambda: 0)
            self.assertEqual(len(overlay.GetFrame()[2]), width * height * 4)

    def test_standard_raw_incremental_packet_on_stationary_guest(self):
        server = VNCServer.__new__(VNCServer)
        server._display = self.overlay
        server._compatible = False
        session = VNCServer.VNCSession()
        session.incremental = True
        stream, client = socket.socketpair()
        stream.settimeout(1)
        client.settimeout(1)
        session.stream = stream
        try:
            server.VNCSendFrame(session)
            server.RecvExact(client, 16 + 160 * 40 * 4)
            server.VNCSendFrame(session)
            self.assertEqual(server.RecvExact(client, 4), b'\0\0\0\0')
            self.wall = 1
            self.ticks = 4_770_000
            server.VNCSendFrame(session)
            header = server.RecvExact(client, 16)
            self.assertEqual(header[:4], b'\0\0\0\1')
            x, y, width, height, encoding = struct.unpack('!4Hi', header[4:])
            self.assertEqual(encoding, 0)
            self.assertGreaterEqual(x, 160 - 10 * 8)
            self.assertLessEqual(y + height, 8)
            self.assertGreater(width * height, 0)
            self.assertEqual(len(server.RecvExact(client, width * height * 4)),
                             width * height * 4)
        finally:
            stream.close()
            client.close()
