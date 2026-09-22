import select
import socket
import struct
import threading
import unittest

from vncserver import VNCServer


class RequestTests(unittest.TestCase):
    def test_no_frame_before_first_request(self):
        class Display:
            def GetFrame(self):
                return 1, 1, b'\1\2\3\0'

        server = VNCServer.__new__(VNCServer)
        server._display = Display()
        server._compatible = False
        initialized = threading.Event()
        server.VNCSendVersion = lambda stream: None
        server.VNCSecurityHandshake = lambda stream: None
        server.VNCClientServerInit = lambda stream: initialized.set()
        stream, client = socket.socketpair()
        session = VNCServer.VNCSession()
        session.stream = stream
        client.settimeout(1)
        thread = threading.Thread(target=server.VNCClientThread, args=(session,))
        thread.start()
        try:
            self.assertTrue(initialized.wait(1))
            self.assertEqual(select.select([client], [], [], .06)[0], [])
            client.sendall(struct.pack('!BBHHHH', 3, 0, 0, 0, 1, 1))
            packet = server.RecvExact(client, 20)
            self.assertEqual(packet[:4], b'\0\0\0\1')
            self.assertEqual(packet[-4:], b'\1\2\3\0')
        finally:
            client.close()
            thread.join(2)
            stream.close()
        self.assertFalse(thread.is_alive())
