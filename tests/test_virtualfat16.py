"""Focused tests for the host-directory FAT16 disk and XT-IDE guardrails."""

import pathlib
import struct
import tempfile
import unittest

from virtualfat16 import HostDirectoryFAT16
from xtide import XTIDE


class _ShortDisk:
    def Read(self, offset, length):
        return b'x'

    def Write(self, offset, data):
        pass


class HostDirectoryFAT16Tests(unittest.TestCase):
    def test_builds_fat16_and_syncs_guest_data_to_host(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            host_file = root / 'HELLO.TXT'
            host_file.write_bytes(b'hello')
            (root / 'SUBDIR').mkdir()
            disk = HostDirectoryFAT16(root)

            boot = disk.Read(disk.partition_start * 512, 512)
            self.assertEqual(boot[54:62], b'FAT16   ')
            self.assertEqual(boot[510:512], b'\x55\xaa')
            root_start = (disk.partition_start + disk.reserved_sectors +
                          disk.fat_count * disk.fat_sectors)
            root_data = disk.Read(root_start * 512, disk.root_entries * 32)
            entry = root_data[:32]
            self.assertEqual(entry[:11], b'HELLO   TXT')
            first_cluster = struct.unpack_from('<H', entry, 26)[0]
            data_start = (root_start + disk.root_entries * 32 // 512)
            file_offset = (data_start + (first_cluster - 2) *
                           disk.sectors_per_cluster) * 512
            disk.Write(file_offset, b'world')
            self.assertEqual(host_file.read_bytes(), b'world')

    def test_guest_path_cannot_escape_mount(self):
        with tempfile.TemporaryDirectory() as temporary:
            disk = HostDirectoryFAT16(pathlib.Path(temporary))
            with self.assertRaises(ValueError):
                disk._safe_host_path((b'..         ',))

    def test_xtide_short_reads_are_padded_and_bad_chs_aborts(self):
        disk = XTIDE([_ShortDisk()])
        self.assertEqual(disk.ReadDisk(0, 0, 4), b'x\x00\x00\x00')
        disk._registers[2] = 1
        disk._registers[3] = 0
        disk.CMDReadMultiple()
        self.assertTrue(disk._error_register & 4)
        self.assertTrue(disk._status_register & 1)


if __name__ == '__main__':
    unittest.main()
