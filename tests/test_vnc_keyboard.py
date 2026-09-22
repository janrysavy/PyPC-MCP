"""Drive the real VNC message decoder with standard keyboard packets."""
import socket
import struct
import unittest
from unittest.mock import patch

from vncserver import VNCServer


class Keyboard:
    def __init__(self):
        self.codes = []

    def PushKeyboardScancode(self, code):
        self.codes.append(code)


class VNCKeyboardTests(unittest.TestCase):
    def test_scroll_lock_standard_press_and_release(self):
        keyboard = Keyboard()
        with patch('vncserver.threading.Thread.start'):
            server = VNCServer(None, keyboard, 0, False)
        session = server.VNCSession()
        client, session.stream = socket.socketpair()
        with client, session.stream:
            for pressed in (1, 0):
                client.sendall(struct.pack('!BB2xI', 4, pressed, 0xff14))
                self.assertTrue(server.VNCWaitForEvent(session))
        self.assertEqual(keyboard.codes, [0x46, 0xc6])

    def test_unknown_keysym_does_not_become_scroll_lock(self):
        keyboard = Keyboard()
        with patch('vncserver.threading.Thread.start'):
            server = VNCServer(None, keyboard, 0, False)
        server.PushChar(0xffffffff, True)
        server.PushChar(0xffffffff, False)
        self.assertEqual(keyboard.codes, [])


if __name__ == '__main__':
    unittest.main()
