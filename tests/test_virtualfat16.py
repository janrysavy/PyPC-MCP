"""Focused tests for the host-directory FAT16 disk and XT-IDE guardrails."""

import os
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

    def test_writing_one_file_does_not_rewrite_unchanged_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            unchanged = root / 'UNCHANGE.TXT'
            changed = root / 'CHANGED.TXT'
            unchanged.write_bytes(b'original')
            changed.write_bytes(b'old')
            disk = HostDirectoryFAT16(root)
            os.utime(unchanged, (946684800, 946684800))
            old_mtime = unchanged.stat().st_mtime_ns

            root_start = (disk.partition_start + disk.reserved_sectors +
                          disk.fat_count * disk.fat_sectors)
            root_data = disk.Read(root_start * 512, disk.root_entries * 32)
            entry = next(root_data[i:i + 32] for i in range(0, len(root_data), 32)
                         if root_data[i:i + 11] == b'CHANGED TXT')
            cluster = struct.unpack_from('<H', entry, 26)[0]
            data_start = root_start + disk.root_entries * 32 // 512
            disk.Write((data_start + (cluster - 2) * disk.sectors_per_cluster) * 512,
                       b'new')

            self.assertEqual(changed.read_bytes(), b'new')
            self.assertEqual(unchanged.read_bytes(), b'original')
            self.assertEqual(unchanged.stat().st_mtime_ns, old_mtime)

    def test_new_file_data_is_synced_when_directory_size_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            disk = HostDirectoryFAT16(root)
            fat_start = disk.partition_start + disk.reserved_sectors
            root_start = fat_start + disk.fat_count * disk.fat_sectors
            data_start = root_start + disk.root_entries * 32 // 512
            entry = bytearray(32)
            entry[:11] = b'NEW     TXT'
            entry[11] = 0x20
            struct.pack_into('<H', entry, 26, 2)

            for fat_index in range(disk.fat_count):
                disk.Write((fat_start + fat_index * disk.fat_sectors) * 512 + 4,
                           b'\xff\xff')
            disk.Write(root_start * 512, entry)
            disk.Write(data_start * 512, b'hello')
            struct.pack_into('<I', entry, 28, 5)
            disk.Write(root_start * 512, entry)

            self.assertEqual((root / 'NEW.TXT').read_bytes(), b'hello')

    def test_write_spanning_two_clusters_updates_host_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            host_file = root / 'SPLIT.BIN'
            host_file.write_bytes(b'a' * 2049)
            disk = HostDirectoryFAT16(root)
            root_start = (disk.partition_start + disk.reserved_sectors +
                          disk.fat_count * disk.fat_sectors)
            root_data = disk.Read(root_start * 512, disk.root_entries * 32)
            cluster = struct.unpack_from('<H', root_data, 26)[0]
            data_start = root_start + disk.root_entries * 32 // 512
            file_offset = (data_start + (cluster - 2) *
                           disk.sectors_per_cluster) * 512

            disk.Write(file_offset + 2047, b'XY')
            expected = b'a' * 2047 + b'XY'
            self.assertEqual(host_file.read_bytes(), expected)

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
