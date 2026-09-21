"""Synthetic DMA transfer regressions using the real bus and I/O registers."""
import unittest

import bus
import i8237


class DMATransferTests(unittest.TestCase):
    def configure(self, channel, address=0x1234, count=1, page=3):
        memory = bus.Bus(1 << 20, [], [])
        dma = i8237.i8237(memory)
        dma.IO_Write(0x0c, 0)
        for port, value in ((2 * channel, address), (2 * channel + 1, count)):
            dma.IO_Write(port, value & 0xff)
            dma.IO_Write(port, value >> 8)
        dma.IO_Write((0x87, 0x83, 0x81, 0x82)[channel], page)
        dma.IO_Write(0x0a, channel)
        return memory, dma

    def test_receive_all_channels_and_terminal_count(self):
        for channel in range(4):
            with self.subTest(channel=channel):
                memory, dma = self.configure(channel)
                memory.WriteByte(0x31234, 0x41)
                memory.WriteByte(0x31235, 0x42)
                self.assertEqual(dma.ReceiveFromChannel(channel), 0x41)
                self.assertFalse(dma.IsChannelTC(channel))
                self.assertEqual(dma.ReceiveFromChannel(channel), 0x42)
                self.assertTrue(dma.IsChannelTC(channel))
                self.assertEqual(dma._channel_word_count[channel].GetValue(), 0xffff)
                self.assertEqual(dma._channel_address_register[channel].GetValue(), 0x1236)
                self.assertEqual(dma.ReceiveFromChannel(channel), -1)

    def test_send_all_channels_and_terminal_count(self):
        for channel in range(4):
            with self.subTest(channel=channel):
                memory, dma = self.configure(channel)
                self.assertTrue(dma.SendToChannel(channel, 0x61))
                self.assertFalse(dma.IsChannelTC(channel))
                self.assertTrue(dma.SendToChannel(channel, 0x62))
                self.assertTrue(dma.IsChannelTC(channel))
                self.assertEqual((memory.ReadByte(0x31234)[0], memory.ReadByte(0x31235)[0]), (0x61, 0x62))
                self.assertEqual(dma._channel_word_count[channel].GetValue(), 0xffff)
                self.assertFalse(dma.SendToChannel(channel, 0x63))
                self.assertEqual(memory.ReadByte(0x31236)[0], 0xff)

    def test_disabled_and_masked_transfers_do_not_mutate(self):
        for channel in range(4):
            for port, value in ((8, 4), (0x0a, channel | 4)):
                with self.subTest(channel=channel, port=port):
                    memory, dma = self.configure(channel)
                    dma.IO_Write(port, value)
                    self.assertEqual(dma.ReceiveFromChannel(channel), -1)
                    self.assertFalse(dma.SendToChannel(channel, 0x51))
                    self.assertEqual(dma._channel_address_register[channel].GetValue(), 0x1234)
                    self.assertEqual(dma._channel_word_count[channel].GetValue(), 1)
                    self.assertEqual(memory.ReadByte(0x31234)[0], 0xff)

    def test_address_wrap_keeps_page(self):
        for direction in ('send', 'receive'):
            with self.subTest(direction=direction):
                memory, dma = self.configure(2, address=0xffff)
                if direction == 'send':
                    self.assertTrue(dma.SendToChannel(2, 0x71))
                    self.assertTrue(dma.SendToChannel(2, 0x72))
                else:
                    memory.WriteByte(0x3ffff, 0x71)
                    memory.WriteByte(0x30000, 0x72)
                    self.assertEqual(dma.ReceiveFromChannel(2), 0x71)
                    self.assertEqual(dma.ReceiveFromChannel(2), 0x72)
                self.assertEqual(memory.ReadByte(0x3ffff)[0], 0x71)
                self.assertEqual(memory.ReadByte(0x30000)[0], 0x72)
                self.assertEqual(dma._channel_address_register[2].GetValue(), 1)


if __name__ == '__main__':
    unittest.main()
