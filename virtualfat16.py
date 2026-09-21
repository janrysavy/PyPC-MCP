"""A host-directory-backed FAT16 disk for the emulated XT-IDE bus."""

from pathlib import Path
import os
import struct
import zlib


class HostDirectoryFAT16:
    """Expose a host directory as a writable FAT16 disk.

    The FAT16 structures and file data are cached in memory. Guest writes are
    synchronized to files below ``directory`` after each completed disk write.
    The mount is restricted to DOS 8.3 names, which is suitable for legacy DOS
    programs.
    """

    sector_size = 512
    partition_start = 17
    partition_sectors = 41735
    total_sectors = partition_start + partition_sectors
    sectors_per_cluster = 4
    reserved_sectors = 1
    fat_count = 2
    root_entries = 512
    fat_sectors = 41

    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()
        if not self.directory.is_dir():
            raise ValueError(f'host directory does not exist: {self.directory}')
        self.identity = f'host:{self.directory}'
        self._image = bytearray(self.total_sectors * self.sector_size)
        self._host_paths = {}
        self._build()

    @staticmethod
    def _reserved_dos_base(base):
        return (base in {'CON', 'PRN', 'AUX', 'NUL'} or
                (len(base) == 4 and base[:3] in {'COM', 'LPT'} and
                 base[3] in '123456789'))

    @staticmethod
    def _dos_name(path):
        name = path.name
        if name in ('.', '..') or name.count('.') > 1:
            raise ValueError(f'host file is not an 8.3 DOS name: {name}')
        if '.' in name:
            base, extension = name.rsplit('.', 1)
        else:
            base, extension = name, ''
        base = base.upper()
        extension = extension.upper()
        if not base or len(base) > 8 or len(extension) > 3:
            raise ValueError(f'host file is not an 8.3 DOS name: {name}')
        if HostDirectoryFAT16._reserved_dos_base(base):
            raise ValueError(f'host file uses a reserved DOS name: {name}')
        allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$%'-_@~!#()&"
        if any(char not in allowed for char in base + extension):
            raise ValueError(f'host file contains unsupported DOS characters: {name}')
        return (base.ljust(8) + extension.ljust(3)).encode('ascii')

    @staticmethod
    def _display_name(dos_name):
        text = dos_name.decode('ascii').rstrip(' ')
        base, extension = text[:8].rstrip(), text[8:].rstrip()
        if HostDirectoryFAT16._reserved_dos_base(base):
            raise ValueError(f'guest path uses a reserved DOS name: {text}')
        return f'{base}.{extension}' if extension else base

    @staticmethod
    def _put16(buffer, offset, value):
        struct.pack_into('<H', buffer, offset, value)

    @staticmethod
    def _put32(buffer, offset, value):
        struct.pack_into('<I', buffer, offset, value)

    def _sector(self, lba):
        start = lba * self.sector_size
        return memoryview(self._image)[start:start + self.sector_size]

    def _allocate(self, fat, count, next_cluster):
        clusters = list(range(next_cluster, next_cluster + count))
        if not clusters or clusters[-1] >= len(fat):
            raise ValueError('host directory does not fit in virtual FAT16 volume')
        for index, cluster in enumerate(clusters):
            fat[cluster] = (clusters[index + 1]
                            if index + 1 < len(clusters) else 0xffff)
        return clusters, next_cluster + count

    def _scan_tree(self, host_path, relative=()):
        entries = []
        names = set()
        for path in sorted(host_path.iterdir(), key=lambda item: item.name.upper()):
            if path.is_symlink():
                raise ValueError(f'symlinks are not supported in host directory: {path}')
            dos_name = self._dos_name(path)
            if dos_name in names:
                raise ValueError(f'duplicate DOS name: {path}')
            names.add(dos_name)
            child_relative = relative + (dos_name,)
            if path.is_dir():
                entries.append({
                    'name': dos_name, 'kind': 'dir', 'host': path,
                    'relative': child_relative,
                    'entries': self._scan_tree(path, child_relative),
                })
            elif path.is_file():
                entries.append({
                    'name': dos_name, 'kind': 'file', 'host': path,
                    'relative': child_relative, 'data': path.read_bytes(),
                })
            else:
                raise ValueError(f'unsupported host directory entry: {path}')
        return entries

    def _write_directory_entry(self, target, offset, entry, start_cluster):
        target[offset:offset + 11] = entry['name']
        target[offset + 11] = 0x10 if entry['kind'] == 'dir' else 0x20
        self._put16(target, offset + 26, start_cluster)
        self._put32(target, offset + 28,
                    0 if entry['kind'] == 'dir' else len(entry.get('data', b'')))

    def _build(self):
        root = {
            'name': None, 'kind': 'root', 'host': self.directory,
            'relative': (), 'entries': self._scan_tree(self.directory),
        }
        root_sectors = (self.root_entries * 32) // self.sector_size
        data_start = (self.partition_start + self.reserved_sectors +
                      self.fat_count * self.fat_sectors + root_sectors)
        cluster_size = self.sectors_per_cluster * self.sector_size
        cluster_count = ((self.partition_sectors - self.reserved_sectors -
                          self.fat_count * self.fat_sectors - root_sectors) /
                         self.sectors_per_cluster)
        fat = [0] * (int(cluster_count) + 2)
        fat[0] = 0xfff8
        fat[1] = 0xffff
        next_cluster = 2

        def allocate_tree(entry):
            nonlocal next_cluster
            if entry['kind'] == 'root':
                for child in entry['entries']:
                    allocate_tree(child)
                return
            if entry['kind'] == 'dir':
                entries_per_cluster = cluster_size // 32
                needed = max(1, (len(entry['entries']) + 2 +
                                 entries_per_cluster - 1) // entries_per_cluster)
                entry['clusters'], next_cluster = self._allocate(
                    fat, needed, next_cluster)
                for child in entry['entries']:
                    allocate_tree(child)
                return
            needed = max(1, (len(entry['data']) + cluster_size - 1) // cluster_size)
            entry['clusters'], next_cluster = self._allocate(
                fat, needed, next_cluster)

        allocate_tree(root)

        mbr = self._sector(0)
        partition = mbr[446:462]
        partition[0] = 0
        partition[1:4] = bytes((0, 1, 1))
        partition[4] = 0x06
        partition[5:8] = bytes((0xff, 0xff, 0xff))
        self._put32(partition, 8, self.partition_start)
        self._put32(partition, 12, self.partition_sectors)
        mbr[510:512] = b'\x55\xaa'

        boot = self._sector(self.partition_start)
        boot[0:3] = b'\xeb\x3c\x90'
        boot[3:11] = b'MSDOS5.0'
        self._put16(boot, 11, self.sector_size)
        boot[13] = self.sectors_per_cluster
        self._put16(boot, 14, self.reserved_sectors)
        boot[16] = self.fat_count
        self._put16(boot, 17, self.root_entries)
        self._put16(boot, 19, self.partition_sectors)
        boot[21] = 0xf8
        self._put16(boot, 22, self.fat_sectors)
        self._put16(boot, 24, 17)
        self._put16(boot, 26, 4)
        self._put32(boot, 28, self.partition_start)
        boot[36] = 0x80
        boot[38] = 0x29
        self._put32(boot, 39, zlib.crc32(str(self.directory).encode('utf-8')))
        boot[43:54] = b'PYPC HOST  '
        boot[54:62] = b'FAT16   '
        boot[510:512] = b'\x55\xaa'

        fat_start = self.partition_start + self.reserved_sectors
        fat_bytes = bytearray(self.fat_sectors * self.sector_size)
        for index, value in enumerate(fat):
            self._put16(fat_bytes, index * 2, value)
        first_fat = fat_start * self.sector_size
        self._image[first_fat:first_fat + len(fat_bytes)] = fat_bytes
        second_fat = (fat_start + self.fat_sectors) * self.sector_size
        self._image[second_fat:second_fat + len(fat_bytes)] = fat_bytes
        root_start = fat_start + self.fat_count * self.fat_sectors

        def write_tree(entry, parent_cluster=0):
            if entry['kind'] == 'root':
                target = bytearray(self.root_entries * 32)
                self._write_directory(entry, target, 0)
                start = root_start * self.sector_size
                self._image[start:start + len(target)] = target
            else:
                target = bytearray(len(entry['clusters']) * cluster_size)
                self._write_directory(entry, target, parent_cluster)
                for index, cluster in enumerate(entry['clusters']):
                    start = (data_start + (cluster - 2) * self.sectors_per_cluster) * self.sector_size
                    self._image[start:start + cluster_size] = target[
                        index * cluster_size:(index + 1) * cluster_size]
            for child in entry['entries']:
                if child['kind'] == 'file':
                    for index, cluster in enumerate(child['clusters']):
                        start = (data_start + (cluster - 2) * self.sectors_per_cluster) * self.sector_size
                        chunk = child['data'][index * cluster_size:(index + 1) * cluster_size]
                        self._image[start:start + len(chunk)] = chunk
                else:
                    parent_cluster = (entry['clusters'][0]
                                      if entry['kind'] != 'root' else 0)
                    write_tree(child, parent_cluster)

        write_tree(root)
        self._remember_host_paths(root)

    def _write_directory(self, entry, directory_data, parent_cluster):
        entries = entry['entries']
        if entry['kind'] != 'root':
            entries = [
                {'name': b'.          ', 'kind': 'dir', 'data': b'', 'clusters': entry['clusters']},
                {'name': b'..         ', 'kind': 'dir', 'data': b'',
                 'clusters': ([parent_cluster] if parent_cluster else [])},
            ] + entries
        for index, child in enumerate(entries):
            offset = index * 32
            if offset + 32 > len(directory_data):
                raise ValueError('directory is too large for its FAT16 allocation')
            start_cluster = (child.get('clusters') or [0])[0]
            self._write_directory_entry(directory_data, offset, child, start_cluster)

    def _remember_host_paths(self, entry):
        for child in entry['entries']:
            self._host_paths[child['relative']] = child['host']
            if child['kind'] == 'dir':
                self._remember_host_paths(child)

    def _fat_value(self, cluster):
        self._validate_cluster(cluster)
        fat_start = (self.partition_start + self.reserved_sectors) * self.sector_size
        return struct.unpack_from('<H', self._image, fat_start + cluster * 2)[0]

    def _max_cluster(self):
        data_sectors = (self.partition_sectors - self.reserved_sectors -
                        self.fat_count * self.fat_sectors -
                        (self.root_entries * 32) // self.sector_size)
        return 1 + data_sectors // self.sectors_per_cluster

    def _validate_cluster(self, cluster):
        if cluster < 2 or cluster > self._max_cluster():
            raise ValueError(f'invalid FAT16 cluster: {cluster}')

    def _cluster_bytes(self, cluster):
        self._validate_cluster(cluster)
        data_start = (self.partition_start + self.reserved_sectors +
                      self.fat_count * self.fat_sectors +
                      (self.root_entries * 32) // self.sector_size)
        start = (data_start + (cluster - 2) * self.sectors_per_cluster) * self.sector_size
        return bytes(self._image[start:start + self.sectors_per_cluster * self.sector_size])

    def _read_chain(self, start_cluster, size):
        if size == 0:
            return b''
        data = bytearray()
        cluster = start_cluster
        seen = set()
        while cluster >= 2 and cluster not in seen and len(data) < size:
            seen.add(cluster)
            data.extend(self._cluster_bytes(cluster))
            next_cluster = self._fat_value(cluster)
            if next_cluster >= 0xfff8:
                break
            cluster = next_cluster
        if len(data) < size:
            raise ValueError('guest FAT chain is invalid')
        return bytes(data[:size])

    def _safe_host_path(self, relative):
        if any(part in (b'.          ', b'..         ') for part in relative):
            raise ValueError('guest path contains a traversal component')
        path = self.directory.joinpath(*[self._display_name(part) for part in relative])
        return self._validate_host_path(path)

    def _validate_host_path(self, path):
        resolved = path.resolve(strict=False)
        root_text = os.path.normcase(str(self.directory))
        resolved_text = os.path.normcase(str(resolved))
        try:
            inside = os.path.commonpath((root_text, resolved_text)) == root_text
        except ValueError:
            inside = False
        if not inside:
            raise ValueError('guest path escapes the host mount root')
        return path

    def _directory_entries(self, data, relative, visited):
        entries = []
        for offset in range(0, len(data), 32):
            item = data[offset:offset + 32]
            if item[0] == 0:
                break
            if item[0] == 0xe5 or item[11] == 0x0f:
                continue
            name = bytes(item[:11])
            if name in (b'.          ', b'..         ') or item[11] & 0x08:
                continue
            start_cluster = struct.unpack_from('<H', item, 26)[0]
            child_relative = relative + (name,)
            if item[11] & 0x10:
                if start_cluster in visited:
                    continue
                visited.add(start_cluster)
                child_data = bytearray()
                cluster = start_cluster
                local_seen = set()
                while cluster >= 2 and cluster not in local_seen:
                    local_seen.add(cluster)
                    child_data.extend(self._cluster_bytes(cluster))
                    next_cluster = self._fat_value(cluster)
                    if next_cluster >= 0xfff8:
                        break
                    cluster = next_cluster
                entries.append(('dir', child_relative, bytes(child_data)))
            else:
                size = struct.unpack_from('<I', item, 28)[0]
                entries.append(('file', child_relative, size, start_cluster))
        return entries

    def _sync_directory(self, data, relative, visited):
        for entry in self._directory_entries(data, relative, visited):
            if entry[0] == 'dir':
                _, child_relative, child_data = entry
                host_path = self._host_paths.get(child_relative) or self._safe_host_path(child_relative)
                host_path = self._validate_host_path(host_path)
                if host_path.is_symlink():
                    raise ValueError(f'guest write targets a host symlink: {host_path}')
                host_path.mkdir(parents=True, exist_ok=True)
                self._host_paths[child_relative] = host_path
                self._sync_directory(child_data, child_relative, visited)
            else:
                _, child_relative, size, start_cluster = entry
                host_path = self._host_paths.get(child_relative) or self._safe_host_path(child_relative)
                host_path = self._validate_host_path(host_path)
                if host_path.is_symlink():
                    raise ValueError(f'guest write targets a host symlink: {host_path}')
                host_path.parent.mkdir(parents=True, exist_ok=True)
                host_path.write_bytes(self._read_chain(start_cluster, size))
                self._host_paths[child_relative] = host_path

    def SyncToHost(self):
        root_start = (self.partition_start + self.reserved_sectors +
                      self.fat_count * self.fat_sectors)
        root_size = self.root_entries * 32
        root_data = bytes(self._image[root_start * self.sector_size:
                                      root_start * self.sector_size + root_size])
        self._sync_directory(root_data, (), set())

    def Read(self, offset, length):
        if offset < 0 or length < 0 or offset + length > len(self._image):
            raise ValueError('virtual disk read is outside the disk')
        return bytes(self._image[offset:offset + length])

    def Write(self, offset, data):
        if offset < 0 or offset + len(data) > len(self._image):
            raise ValueError('virtual disk write is outside the disk')
        self._image[offset:offset + len(data)] = data
        try:
            self.SyncToHost()
        except (OSError, ValueError) as error:
            print(f'HostDirectoryFAT16: ignored invalid guest filesystem write: {error}')
