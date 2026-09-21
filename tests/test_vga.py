"""Focused tests for the VGA text-mode device."""

import unittest

from state8088 import State8088
from vga import BLINK_HALF_PERIOD_CYCLES, VGA, VGA_DEFAULT_PALETTE_RGB


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
        self.video.IO_Write(0x3c0, 0x10)
        self.video.IO_Write(0x3c0, 0x08)
        self.assertEqual(self.video.DecodeTextAttribute(0x8f), (15, 0, True))
        self.video.IO_Read(0x3da)
        self.video.IO_Write(0x3c0, 0x1f)
        self.assertEqual(self.video.IO_Read(0x3c1), 0xff)

    def test_extended_background_color_is_default(self):
        self.assertEqual(self.video.DecodeTextAttribute(0xaa), (10, 10, False))

    def test_default_palette_matches_vga_rgb_values(self):
        actual_rgb = [(red, green, blue) for blue, green, red
                      in self.video._palette[:16]]
        self.assertEqual(actual_rgb, list(VGA_DEFAULT_PALETTE_RGB))

    def test_dac_palette_write_and_readback(self):
        self.video.IO_Write(0x3c8, 4)
        self.video.IO_Write(0x3c9, 0x3f)
        self.video.IO_Write(0x3c9, 0x20)
        self.video.IO_Write(0x3c9, 0x10)
        self.assertEqual(self.video._palette[4], (65, 130, 255))

        self.video.IO_Write(0x3c7, 4)
        self.assertEqual([self.video.IO_Read(0x3c9) for _ in range(3)],
                         [0x3f, 0x20, 0x10])
        self.assertEqual(self.video.IO_Read(0x3c7), 3)

    def test_attribute_palette_maps_text_colors(self):
        self.video.IO_Read(0x3da)
        self.video.IO_Write(0x3c0, 1)
        self.video.IO_Write(0x3c0, 4)
        self.assertEqual(self.video._text_palette_color(1), self.video._palette[4])

    def test_custom_plane_two_font_is_rendered(self):
        self.video.WriteByte(0xb8000, ord('A'))
        self.video.WriteByte(0xb8001, 0x1f)
        self.video.IO_Write(0x3c4, 2)
        self.video.IO_Write(0x3c5, 4)
        self.video.WriteByte(0xa0000 + ord('A') * 32, 0x80)
        width, height, pixels = self.video.GetFrame()
        self.assertEqual((width, height), (640, 400))
        self.assertEqual(tuple(pixels[0:4]), (255, 255, 255, 255))
        self.assertEqual(tuple(pixels[7 * 4:8 * 4]), (170, 0, 0, 255))

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

    def test_text_page_flip_reads_and_renders_second_bank(self):
        page_address = 80 * 25 * 2
        self.video.WriteByte(0xb8000 + page_address, ord('B'))
        self.video.WriteByte(0xb8000 + page_address + 1, 0x1f)
        self.video.IO_Write(0x3d4, 12)
        self.video.IO_Write(0x3d5, (page_address // 2) >> 8)
        self.video.IO_Write(0x3d4, 13)
        self.video.IO_Write(0x3d5, (page_address // 2) & 0xff)
        self.assertEqual(self.video._display_address, page_address)
        self.assertEqual(self.video.ReadTextByte(self.video._display_address), ord('B'))
        _, _, pixels = self.video.GetFrame()
        self.assertEqual(len(pixels), 640 * 400 * 4)

    def test_mode13_chain4_memory_and_rendering(self):
        self.video.IO_Write(0x3c4, 4)
        self.video.IO_Write(0x3c5, 0x0e)
        self.video.IO_Write(0x3c4, 2)
        self.video.IO_Write(0x3c5, 0x0f)
        self.video.IO_Write(0x3ce, 5)
        self.video.IO_Write(0x3cf, 0x40)
        self.video.IO_Write(0x3ce, 6)
        self.video.IO_Write(0x3cf, 0x05)
        self.assertEqual(self.video._graphics_mode, 0x13)

        for offset, value in enumerate((1, 2, 3, 4)):
            self.video.WriteByte(0xa0000 + offset, value)
        self.assertEqual([self.video.ReadByte(0xa0000 + i)
                          for i in range(4)], [1, 2, 3, 4])

        width, height, pixels = self.video.GetFrame()
        self.assertEqual((width, height), (640, 400))
        self.assertEqual(tuple(pixels[0:4]), (*self.video._palette[1], 255))
        self.assertEqual(tuple(pixels[2 * 4:3 * 4]),
                         (*self.video._palette[2], 255))

    def test_mode12_planar_memory_and_rendering(self):
        self.video.IO_Write(0x3d4, 0x12)
        self.video.IO_Write(0x3d5, 0xdf)
        self.video.IO_Write(0x3c4, 4)
        self.video.IO_Write(0x3c5, 0x06)
        self.video.IO_Write(0x3c4, 2)
        self.video.IO_Write(0x3c5, 0x0f)
        self.video.IO_Write(0x3ce, 5)
        self.video.IO_Write(0x3cf, 0x02)
        self.video.IO_Write(0x3ce, 6)
        self.video.IO_Write(0x3cf, 0x05)
        self.assertEqual(self.video._graphics_mode, 0x12)

        self.video.WriteByte(0xa0000, 0x0a)
        self.video.IO_Write(0x3ce, 4)
        self.video.IO_Write(0x3cf, 1)
        self.assertEqual(self.video.ReadByte(0xa0000), 0xff)

        width, height, pixels = self.video.GetFrame()
        self.assertEqual((width, height), (640, 480))
        self.assertEqual(tuple(pixels[0:4]), (*self.video._palette[10], 255))
        self.assertEqual(tuple(pixels[1 * 4:2 * 4]),
                         (*self.video._palette[10], 255))

    def test_bios_modes_and_write_pixel_xor(self):
        state = State8088()
        self.video.BiosSetMode(0x13)
        self.assertFalse(self.video.BiosSetMode(0x01))
        self.assertEqual(self.video._graphics_mode, 0x13)
        state.SetAX(0x0013)
        self.assertTrue(self.video.BiosInterrupt(state))
        self.assertEqual((self.video._graphics_mode, self.video.GetTextColumns()),
                         (0x13, 80))

        state.SetAX(0x0c05)
        state.SetCX(7)
        state.SetDX(9)
        self.assertTrue(self.video.BiosInterrupt(state))
        state.SetAL(0x85)
        self.assertTrue(self.video.BiosInterrupt(state))
        self.assertEqual(self.video.ReadByte(0xa0000 + 9 * 320 + 7), 0)
        state.SetAH(0x0d)
        self.assertTrue(self.video.BiosInterrupt(state))
        self.assertEqual(state.GetAL(), 0)

        state.SetAX(0x0012)
        self.assertTrue(self.video.BiosInterrupt(state))
        self.assertEqual((self.video._graphics_mode, self.video.GetTextColumns()),
                         (0x12, 80))
        state.SetAX(0x0c0a)
        state.SetCX(3)
        state.SetDX(4)
        self.assertTrue(self.video.BiosInterrupt(state))
        state.SetAL(0x8a)
        self.assertTrue(self.video.BiosInterrupt(state))
        plane_offset = 4 * 80
        self.assertEqual([self.video._planes[p][plane_offset] for p in range(4)],
                         [0, 0, 0, 0])
        state.SetAH(0x0d)
        self.assertTrue(self.video.BiosInterrupt(state))
        self.assertEqual(state.GetAL(), 0)

    def test_mode13_write_uses_graphics_bit_mask(self):
        self.video.BiosSetMode(0x13)
        self.video.IO_Write(0x3ce, 8)
        self.video.IO_Write(0x3cf, 0x0f)
        self.video.WriteByte(0xa0000, 0xff)
        self.assertEqual(self.video._planes[0][0], 0x0f)

    def test_planar_write_mode1_copies_latches_only(self):
        self.video.BiosSetMode(0x12)
        expected = (0x11, 0x22, 0x33, 0x44)
        for plane, value in enumerate(expected):
            self.video._planes[plane][0] = value
        self.video.ReadByte(0xa0000)  # load all four VGA latches
        self.video.IO_Write(0x3ce, 3)
        self.video.IO_Write(0x3cf, 0x19)  # write mode 1, XOR logical op
        self.video.IO_Write(0x3ce, 8)
        self.video.IO_Write(0x3cf, 0x00)
        self.video.WriteByte(0xa0000, 0xa5)
        self.assertEqual(tuple(plane[0] for plane in self.video._planes),
                         expected)

    def test_graphics_memory_snapshot_layouts(self):
        self.video.BiosSetMode(0x13)
        for address, value in enumerate((1, 2, 3, 4)):
            self.video.WriteByte(0xa0000 + address, value)
        snapshot = self.video.GetGraphicsMemorySnapshot()
        self.assertEqual(len(snapshot), 0x10000)
        self.assertEqual(snapshot[:4], bytes((1, 2, 3, 4)))

        self.video.BiosSetMode(0x12)
        for plane in range(4):
            self.video._planes[plane][0] = plane + 1
        snapshot = self.video.GetGraphicsMemorySnapshot()
        self.assertEqual(len(snapshot), 4 * 0x10000)
        self.assertEqual([snapshot[plane * 0x10000] for plane in range(4)],
                         [1, 2, 3, 4])

    def test_blink_phase_is_stable_between_vnc_frames(self):
        self.video.Tick(0, 0)
        first_phase = self.video._blink_phase
        self.video.Tick(0, 4_770_000 // 20)
        self.assertEqual(self.video._blink_phase, first_phase)
        self.video.Tick(0, BLINK_HALF_PERIOD_CYCLES)
        self.assertNotEqual(self.video._blink_phase, first_phase)


if __name__ == '__main__':
    unittest.main()
