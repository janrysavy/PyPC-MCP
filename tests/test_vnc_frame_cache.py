import unittest
import socket

from vncserver import VNCServer


class VersionedDisplay:
    def __init__(self):
        self.version = 1
        self.render_count = 0

    def GetFrameVersion(self):
        return self.version

    def GetFrame(self):
        self.render_count += 1
        return 2, 1, bytearray((0, 0, 0, 255) * 2)


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
            server.RecvExact(client, 16 + 8)
            server.VNCSendFrame(session)
            self.assertEqual(server.RecvExact(client, 4), b'\x00\x00\x00\x00')
        finally:
            stream.close()
            client.close()

    def test_incremental_request_after_version_change_is_full(self):
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
            server.RecvExact(client, 16 + 8)
            server._display.version += 1
            server.VNCSendFrame(session)
            self.assertEqual(server.RecvExact(client, 4), b'\x00\x00\x00\x01')
        finally:
            stream.close()
            client.close()


if __name__ == '__main__':
    unittest.main()
