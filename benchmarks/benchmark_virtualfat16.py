#!/usr/bin/env python3
"""Compare per-file FAT16 write-through with the former full-tree rewrite."""

import argparse
from pathlib import Path
import struct
import sys
import tempfile
import time
import typing

if not hasattr(typing, 'override'):
    typing.override = lambda function: function

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from virtualfat16 import HostDirectoryFAT16


class FullTreeSync(HostDirectoryFAT16):
    def SyncToHost(self, dirty_clusters=frozenset()):
        self._synced_files.clear()
        super().SyncToHost(dirty_clusters)


def sample(disk_type, files, writes):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for index in range(files):
            (root / f'F{index:03}.BIN').write_bytes(bytes((index,)) * 32768)
        target = root / 'TARGET.BIN'
        target.write_bytes(b'\x00' * 32768)
        disk = disk_type(root)
        root_start = (disk.partition_start + disk.reserved_sectors +
                      disk.fat_count * disk.fat_sectors)
        entries = disk.Read(root_start * 512, disk.root_entries * 32)
        entry = next(entries[i:i + 32] for i in range(0, len(entries), 32)
                     if entries[i:i + 11] == b'TARGET  BIN')
        cluster = struct.unpack_from('<H', entry, 26)[0]
        data_start = root_start + disk.root_entries * 32 // 512
        offset = (data_start + (cluster - 2) * disk.sectors_per_cluster) * 512

        start = time.perf_counter()
        for index in range(writes):
            disk.Write(offset, bytes((index & 255,)))
        elapsed = time.perf_counter() - start
        assert target.read_bytes()[0] == (writes - 1) & 255
        return elapsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files', type=int, default=24)
    parser.add_argument('--writes', type=int, default=100)
    args = parser.parse_args()
    if args.files < 1 or args.files > 100 or args.writes < 1:
        parser.error('files must be 1..100 and writes must be positive')
    full = sample(FullTreeSync, args.files, args.writes)
    incremental = sample(HostDirectoryFAT16, args.files, args.writes)
    print(f'full-tree: {full:.3f}s; per-file: {incremental:.3f}s; '
          f'speedup: {full / incremental:.2f}x')


if __name__ == '__main__':
    main()
