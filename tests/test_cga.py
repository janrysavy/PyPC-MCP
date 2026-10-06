import unittest

from cga import CGA


class CGARenderTests(unittest.TestCase):
    def test_640_mode_renders_all_source_rows(self):
        video = CGA(False)
        video.IO_Write(0x3d8, 0x12)
        video._ram[0] = 0x80
        video._ram[80] = 0x80

        _, _, pixels = video.GetFrame()

        # The first source byte paints the top scanline; the second source
        # row must also be visible instead of being cut off by an early return.
        self.assertNotEqual(tuple(pixels[0:3]), (0, 0, 0))
        second_row = 2 * 640 * 4
        self.assertNotEqual(tuple(pixels[second_row:second_row + 3]),
                            (0, 0, 0))

    def test_nonzero_text_start_matches_page_zero_pixels_and_wrapped_cells(self):
        baseline = CGA(False)
        video = CGA(False)
        for index in range(2000):
            baseline._ram[2*index:2*index+2] = bytes((65 + index % 26, 31))
        video._ram[4000:8000] = baseline._ram[:4000]
        video.IO_Write(0x3d4, 12); video.IO_Write(0x3d5, 0x07)
        video.IO_Write(0x3d4, 13); video.IO_Write(0x3d5, 0xd0)
        self.assertEqual(video._display_address, 4000)
        self.assertEqual(bytes(video.GetFrame()[2]), bytes(baseline.GetFrame()[2]))
        # FFFF is a word address; CGA backing wraps to byte3FFE.
        video.IO_Write(0x3d4, 12); video.IO_Write(0x3d5, 0xff)
        video.IO_Write(0x3d4, 13); video.IO_Write(0x3d5, 0xff)
        self.assertEqual(video._display_address, 0x3ffe)
        video._ram[-2:] = b'Z\x1f'
        video._ram[:2] = b'Y\x1f'
        from tests.test_observation_rpc import machine
        m, _ = machine(); m.namespace['scr'] = video
        observed = m.rpc('state.observe', expected_state_revision=0, video_text={})
        self.assertEqual(observed['video_text']['display_address'], 0x3ffe)
        self.assertEqual([cell['code'] for cell in observed['video_text']['cells'][0][:2]], [90,89])


if __name__ == '__main__':
    unittest.main()
