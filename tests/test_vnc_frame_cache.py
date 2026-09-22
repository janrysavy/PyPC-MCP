import unittest
import socket
import struct

from vncserver import VNCServer


class VersionedDisplay:
    def __init__(self):
        self.version = 1
        self.render_count = 0
        self.pixels = bytearray((0, 0, 0, 255) * 8)

    def GetFrameVersion(self):
        return self.version

    def GetFrame(self):
        self.render_count += 1
        return 4, 2, self.pixels


class VNCFrameCacheTests(unittest.TestCase):
    def test_unchanged_frame_is_rendered_once(self):
        server = VNCServer.__new__(VNCServer)
        server._display = VersionedDisplay()

        first = server._get_frame()
        second = server._get_frame()

        self.assertIs(first, second)
        self.assertEqual(server._display.render_count, 1)

    def test_display_version_invalidates_rendered_frame(self):
        server = VNCServer.__new__(VNCServer)
        server._display = VersionedDisplay()

        first = server._get_frame()
        server._display.version += 1
        second = server._get_frame()

        self.assertIsNot(first, second)
        self.assertEqual(server._display.render_count, 2)

    def test_unversioned_display_keeps_legacy_behavior(self):
        class Display:
            def __init__(self):
                self.render_count = 0

            def GetFrame(self):
                self.render_count += 1
                return 1, 1, b'\0\0\0\xff'

        server = VNCServer.__new__(VNCServer)
        server._display = Display()
        server._get_frame()
        server._get_frame()
        self.assertEqual(server._display.render_count, 2)

    def test_incremental_request_with_unchanged_frame_is_empty(self):
        server = VNCServer.__new__(VNCServer)
        server._display = VersionedDisplay()
        server._compatible = False
        server._compatible_width = 640
        server._compatible_height = 400
        stream, client = socket.socketpair()
        stream.settimeout(1)
        client.settimeout(1)
        session = VNCServer.VNCSession()
        session.stream = stream
        session.incremental = True
        try:
            server.VNCSendFrame(session)
            server.RecvExact(client, 16 + 32)
            server.VNCSendFrame(session)
            self.assertEqual(server.RecvExact(client, 4), b'\x00\x00\x00\x00')
        finally:
            stream.close()
            client.close()

    def test_incremental_request_sends_changed_rectangle(self):
        server = VNCServer.__new__(VNCServer)
        server._display = VersionedDisplay()
        server._compatible = False
        server._compatible_width = 640
        server._compatible_height = 400
        stream, client = socket.socketpair()
        stream.settimeout(1)
        client.settimeout(1)
        session = VNCServer.VNCSession()
        session.stream = stream
        session.incremental = True
        try:
            server.VNCSendFrame(session)
            server.RecvExact(client, 16 + 32)
            server._display.pixels[(1 * 4 + 2) * 4] = 0xff
            server._display.version += 1
            server.VNCSendFrame(session)
            header = server.RecvExact(client, 16)
            self.assertEqual(struct.unpack('>4Hi', header[4:]),
                             (2, 1, 1, 1, 0))
            self.assertEqual(server.RecvExact(client, 4),
                             b'\xff\x00\x00\xff')
        finally:
            stream.close()
            client.close()

    def test_many_changed_rows_fall_back_to_full_rectangle(self):
        from vncserver import _diff_rect
        previous = bytearray(40 * 40 * 4)
        current = bytearray(previous)
        for row in range(33):
            current[row * 40 * 4] = 1
        self.assertEqual(_diff_rect(previous, current, 40, 40),
                         (0, 0, 40, 40))

    def test_lagging_session_receives_union_of_missed_changes(self):
        server = VNCServer.__new__(VNCServer)
        server._display = VersionedDisplay()
        server._get_frame()
        first_version = server._frame_cache_version

        server._display.pixels[0] = 1
        server._display.version += 1
        server._get_frame()
        server._display.pixels[(1 * 4 + 3) * 4] = 2
        server._display.version += 1
        server._get_frame()

        self.assertEqual(server._rect_since(first_version,
                                            server._frame_cache_version,
                                            4, 2),
                         (0, 0, 4, 2))


if __name__ == '__main__':
    unittest.main()
