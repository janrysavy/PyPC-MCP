"""XT-IDE disk I/O regressions."""
from pathlib import Path
import tempfile
import unittest

import xtide


class XTIDEDiskIOTests(unittest.TestCase):
    def test_store_sector_buffer_overwrites_lba_without_growing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'disk.img'
            original = b'A' * 512 + b'B' * 512 + b'C' * 512
            image.write_bytes(original)

            device = xtide.XTIDE((str(image),))
            device._target_drive = 0
            device._target_lba = 1
            device._sector_buffer = bytearray(b'Z' * 512)
            device.StoreSectorBuffer()

            written = image.read_bytes()
            self.assertEqual(len(written), len(original))
            self.assertEqual(written[:512], b'A' * 512)
            self.assertEqual(written[512:1024], b'Z' * 512)
            self.assertEqual(written[1024:], b'C' * 512)

    def test_absent_slave_read_and_write_abort_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'disk.img'
            image.write_bytes(b'\0' * 512)
            device = xtide.XTIDE((str(image),))
            device._registers[2] = 1
            device._registers[3] = 1
            device._registers[6] = 0x10  # slave select

            for command in (device.CMDReadMultiple, device.CMDWriteMultiple):
                with self.subTest(command=command.__name__):
                    device._status_register = 0
                    device._error_register = 0
                    command()
                    self.assertEqual(device._error_register & 4, 4)
                    self.assertEqual(device._status_register & 1, 1)


if __name__ == '__main__':
    unittest.main()
