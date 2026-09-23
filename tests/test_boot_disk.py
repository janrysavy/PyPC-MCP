"""The tracked DOS image must retain its noninteractive fast boot profile."""
from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]


class FAT16:
    def __init__(self, image):
        self.image = image
        partition = struct.unpack_from('<I', image, 446 + 8)[0]
        self.volume = partition * 512
        self.sector_size = struct.unpack_from('<H', image, self.volume + 11)[0]
        self.cluster_sectors = image[self.volume + 13]
        reserved = struct.unpack_from('<H', image, self.volume + 14)[0]
        fat_count = image[self.volume + 16]
        self.root_entries = struct.unpack_from('<H', image, self.volume + 17)[0]
        fat_sectors = struct.unpack_from('<H', image, self.volume + 22)[0]
        self.fat = self.volume + reserved * self.sector_size
        self.root = self.volume + (reserved + fat_count * fat_sectors) * self.sector_size
        self.data = self.root + self.root_entries * 32

    def _cluster_data(self, cluster):
        result = bytearray()
        visited = set()
        cluster_size = self.cluster_sectors * self.sector_size
        while 2 <= cluster < 0xfff8 and cluster not in visited:
            visited.add(cluster)
            offset = self.data + (cluster - 2) * cluster_size
            result.extend(self.image[offset:offset + cluster_size])
            cluster = struct.unpack_from('<H', self.image, self.fat + cluster * 2)[0]
        return bytes(result)

    @staticmethod
    def _entry(directory, dos_name):
        stem, dot, extension = dos_name.upper().partition('.')
        assert len(stem) <= 8 and len(extension) <= 3
        target = stem.encode('ascii').ljust(8) + extension.encode('ascii').ljust(3)
        for offset in range(0, len(directory), 32):
            entry = directory[offset:offset + 32]
            if not entry or entry[0] == 0:
                break
            if entry[0] != 0xe5 and entry[11] != 0x0f and entry[:11] == target:
                return (entry[11], struct.unpack_from('<H', entry, 26)[0],
                        struct.unpack_from('<I', entry, 28)[0])
        raise FileNotFoundError(dos_name)

    def read(self, *parts):
        directory = self.image[self.root:self.root + self.root_entries * 32]
        for index, part in enumerate(parts):
            attributes, cluster, size = self._entry(directory, part)
            contents = self._cluster_data(cluster)
            if index + 1 == len(parts):
                assert not attributes & 0x10
                return contents[:size]
            assert attributes & 0x10
            directory = contents
        raise ValueError('a DOS path is required')


def test_boot_config_uses_ltemm_documented_no_test_switch():
    disk = FAT16((ROOT / 'harddisk.img').read_bytes())
    assert disk.read('CONFIG.SYS') == (
        b'FILES=30\r\nDEVICE=LTEMM\\LTEMM.EXE /n\r\n')
    manual = disk.read('LTEMM', 'LTEMM.TXT')
    assert b'/n      - Bypass memory test' in manual
