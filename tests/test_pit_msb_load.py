"""Regressions for programming the PIT through its public I/O ports."""
import unittest

from i8253 import i8253


class PITMostSignificantByteTests(unittest.TestCase):
    def test_msb_only_load_and_readback_on_every_channel(self):
        for channel in range(3):
            for value in (0x00, 0x01, 0x12, 0x80, 0xff):
                with self.subTest(channel=channel, value=value):
                    pit = i8253()
                    # Select channel, MSB-only access, binary mode 2.
                    pit.IO_Write(0x43, (channel << 6) | 0x24)
                    pit.IO_Write(0x40 + channel, value)
                    timer = pit._timers[channel]
                    self.assertEqual(timer.counter_ini, value << 8)
                    self.assertEqual(timer.counter_cur, value << 8)
                    self.assertTrue(timer.is_running)
                    self.assertFalse(timer.is_pending)
                    self.assertEqual(timer.latch_n_cur, 1)
                    self.assertEqual(pit.IO_Read(0x40 + channel), value)

    def test_successive_msb_only_writes_replace_high_byte(self):
        pit = i8253()
        pit.IO_Write(0x43, 0xa4)  # channel 2, MSB-only, binary mode 2
        for value in (0xff, 0x12, 0x00, 0x80):
            with self.subTest(value=value):
                pit.IO_Write(0x42, value)
                self.assertEqual(pit._timers[2].counter_ini, value << 8)
                self.assertEqual(pit._timers[2].counter_cur, value << 8)


if __name__ == '__main__':
    unittest.main()
