"""Video history must retain overwritten text and report lost events."""
import base64
import unittest

import bus
import pc_io
from vga import VGA
from videohistory import VideoHistory


class VideoHistoryTests(unittest.TestCase):
    def setUp(self):
        self.clock = 100
        self.screen = VGA(False)
        self.history = VideoHistory(lambda: self.clock)

    def test_overwritten_character_and_mode_clear_are_retained(self):
        initial = self.history.start(self.screen, 16)
        self.assertEqual(base64.b64decode(initial['vram_base64'])[0], 0)
        self.screen.WriteByte(0xb8000, ord('A'))
        self.clock += 1
        self.screen.WriteByte(0xb8000, ord('B'))
        self.screen.BiosSetMode(3)
        result = self.history.read(initial['history_id'])
        self.assertEqual([(event['old'], event['new']) for event in result['events']
                          if event['kind'] == 'text'], [(0, 65), (65, 66)])
        self.assertEqual([event['kind'] for event in result['events']][-2:],
                         ['clear_text', 'mode'])
        self.assertEqual(self.screen.ReadByte(0xb8000), 0)
        self.assertEqual(result['lost_events'], 0)

    def test_vga_font_and_port_writes_are_ordered(self):
        bus_instance = bus.Bus(1024 * 1024, [self.screen], [])
        io = pc_io.IO(bus_instance, [self.screen], False)
        initial = self.history.start(self.screen, 16)
        io.SetVideoWriteHook(self.screen, self.history.port_write)
        io.Out(0x3d4, 12, False)
        io.Out(0x3d5, 1, False)
        self.screen.IO_Write(0x3c4, 2)
        self.screen.IO_Write(0x3c5, 4)
        self.screen.WriteByte(0xa0000, 0xa5)
        events = self.history.read(initial['history_id'])['events']
        self.assertEqual([event['kind'] for event in events[:2]], ['port', 'port'])
        self.assertTrue(any(event['kind'] == 'font' and event['new'] == 0xa5
                            for event in events))

    def test_ring_overflow_is_explicit_and_old_cursor_refused(self):
        initial = self.history.start(self.screen, 2)
        for value in (65, 66, 67):
            self.screen.WriteByte(0xb8000, value)
        result = self.history.read(initial['history_id'])
        self.assertEqual([event['sequence'] for event in result['events']], [2, 3])
        self.assertEqual(result['lost_events'], 1)
        with self.assertRaises(ValueError):
            self.history.read(initial['history_id'], cursor=0)


if __name__ == '__main__':
    unittest.main()
