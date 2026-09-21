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

    def test_custom_plane_two_font_is_rendered(self):
        self.video.WriteByte(0xb8000, ord('A'))
        self.video.WriteByte(0xb8001, 0x1f)
        self.video.IO_Write(0x3c4, 2)
        self.video.IO_Write(0x3c5, 4)
        self.video.WriteByte(0xa0000 + ord('A') * 32, 0x80)
        width, height, pixels = self.video.GetFrame()
        self.assertEqual((width, height), (640, 400))
        self.assertEqual(tuple(pixels[0:4]), (255, 255, 255, 255))
        self.assertEqual(tuple(pixels[7 * 4:8 * 4]), (127, 0, 0, 255))

    def test_cursor_shape_and_blink_are_rendered(self):
        self.video.WriteByte(0xb8000, ord('A'))
        self.video.WriteByte(0xb8001, 0x1f)
        self.video.IO_Write(0x3d4, 14)
        self.video.IO_Write(0x3d5, 0)
        self.video.IO_Write(0x3d4, 15)
        self.video.IO_Write(0x3d5, 0)
        self.video.IO_Write(0x3d4, 10)
        self.video.IO_Write(0x3d5, 0)
        self.video.IO_Write(0x3d4, 11)
        self.video.IO_Write(0x3d5, 1)
        _, _, pixels = self.video.GetFrame()
        self.assertTrue(self.video.GetCursorInfo()['visible'])
        self.assertEqual(tuple(pixels[7 * 4:8 * 4]), (255, 255, 255, 255))

        self.video.IO_Write(0x3d4, 10)
        self.video.IO_Write(0x3d5, 0x21)
        self.video.GetFrame()
        self.assertFalse(self.video.GetCursorInfo()['enabled'])

    def test_crtc_display_width_selects_text_width(self):
        self.video.IO_Write(0x3d4, 1)
        self.video.IO_Write(0x3d5, 39)
        self.assertEqual(self.video.GetTextColumns(), 40)
        self.video.IO_Write(0x3d5, 79)
        self.assertEqual(self.video.GetTextColumns(), 80)

    def test_crtc_start_address_uses_word_units(self):
        self.video.IO_Write(0x3d4, 12)
        self.video.IO_Write(0x3d5, 0x10)
        self.video.IO_Write(0x3d4, 13)
        self.video.IO_Write(0x3d5, 0x00)
        self.assertEqual(self.video._display_address, 0x2000)


if __name__ == '__main__':
    unittest.main()
