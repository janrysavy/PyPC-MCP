"""Focused tests for the VGA text-mode device."""

import unittest

from vga import VGA


class VGATextTests(unittest.TestCase):
    def setUp(self):
        self.video = VGA(False)

    def test_text_memory_and_render_sizes(self):
        self.video.WriteByte(0xb8000, ord('A'))
        self.video.WriteByte(0xb8001, 0x1f)
        width, height, pixels = self.video.GetFrame()
        self.assertEqual((width, height), (640, 400))
        self.assertEqual(len(pixels), width * height * 4)
        self.assertEqual(self.video.ReadByte(0xb8000), ord('A'))

        self.video.IO_Write(0x3d8, 0)
        self.assertEqual(self.video.GetTextColumns(), 40)
        width, height, pixels = self.video.GetFrame()
        self.assertEqual((width, height), (640, 400))
        self.assertEqual(len(pixels), width * height * 4)

    def test_vga_register_ports_and_font_plane(self):
        self.video.IO_Write(0x3c4, 2)
        self.video.IO_Write(0x3c5, 4)
        self.video.WriteByte(0xa0000, 0xa5)
        self.video.IO_Write(0x3ce, 4)
        self.video.IO_Write(0x3cf, 2)
        self.assertEqual(self.video.ReadByte(0xa0000), 0xa5)

        self.video.IO_Read(0x3da)
        self.video.IO_Write(0x3c0, 0x10)
        self.video.IO_Write(0x3c0, 0x00)
        self.assertEqual(self.video.DecodeTextAttribute(0x8f), (15, 8, False))
        self.video.IO_Read(0x3da)
        self.video.IO_Write(0x3c0, 0x1f)
        self.assertEqual(self.video.IO_Read(0x3c1), 0xff)

    def test_crtc_display_width_selects_text_width(self):
        self.video.IO_Write(0x3d4, 1)
        self.video.IO_Write(0x3d5, 39)
        self.assertEqual(self.video.GetTextColumns(), 40)
        self.video.IO_Write(0x3d5, 79)
        self.assertEqual(self.video.GetTextColumns(), 80)


if __name__ == '__main__':
    unittest.main()
