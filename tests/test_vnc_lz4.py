import socket
import struct
import unittest
from unittest.mock import patch

import vnclz4
from vncserver import VNCServer


class LZ4Tests(unittest.TestCase):
    @unittest.skipUnless(vnclz4.AVAILABLE, 'optional lz4 not installed')
    def test_independent_blocks(self):
        import lz4.block
        pixels = bytes(range(256)) * 64
        for _ in range(2):
            block = vnclz4.encode(pixels, 64, 64)
            self.assertEqual(struct.unpack('!I', block[:4])[0], len(block) - 4)
            self.assertEqual(lz4.block.decompress(block[4:], uncompressed_size=len(pixels)), pixels)

    def test_unavailable_dependency_negotiates_zrle(self):
        server = VNCServer.__new__(VNCServer)
        stream, client = socket.socketpair()
        with stream, client:
            stream.settimeout(1)
            session = VNCServer.VNCSession()
            session.stream = stream
            client.sendall(struct.pack('!BBHiii', 2, 0, 3, vnclz4.ENCODING, 16, 0))
            with patch.object(vnclz4, 'AVAILABLE', False):
                self.assertTrue(server.VNCWaitForEvent(session))
            self.assertEqual(session.encoding, 16)

    @unittest.skipUnless(vnclz4.AVAILABLE, 'optional lz4 not installed')
    def test_invalid_size_refused(self):
        with self.assertRaises(ValueError):
            vnclz4.encode(b'', 1, 1)
