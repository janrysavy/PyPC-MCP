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


if __name__ == '__main__':
    unittest.main()
