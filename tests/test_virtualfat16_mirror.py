"""Host-directory mirroring semantics for guest FAT rename and deletion."""

import pathlib
import tempfile

from virtualfat16 import HostDirectoryFAT16


def root_sector(disk):
    root_start = (disk.partition_start + disk.reserved_sectors +
                  disk.fat_count * disk.fat_sectors)
    return root_start, bytearray(disk.Read(root_start * disk.sector_size,
                                           disk.sector_size))


def test_guest_root_file_rename_removes_old_host_path():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        old_path = root / 'OLD.TXT'
        old_path.write_bytes(b'guest-owned bytes')
        disk = HostDirectoryFAT16(root)
        root_start, sector = root_sector(disk)
        assert sector[:11] == b'OLD     TXT'

        sector[:11] = b'NEW     TXT'
        disk.Write(root_start * disk.sector_size, sector)

        assert not old_path.exists()
        assert (root / 'NEW.TXT').read_bytes() == b'guest-owned bytes'


def test_guest_root_file_delete_removes_host_path():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        path = root / 'DELETE.TXT'
        path.write_bytes(b'delete me')
        disk = HostDirectoryFAT16(root)
        root_start, sector = root_sector(disk)
        assert sector[:11] == b'DELETE  TXT'

        sector[0] = 0xE5
        disk.Write(root_start * disk.sector_size, sector)

        assert not path.exists()


def test_guest_delete_refuses_to_remove_externally_changed_host_file(capsys):
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        path = root / 'CHANGED.TXT'
        path.write_bytes(b'cached guest bytes')
        disk = HostDirectoryFAT16(root)
        path.write_bytes(b'external host edit')
        root_start, sector = root_sector(disk)

        sector[0] = 0xE5
        disk.Write(root_start * disk.sector_size, sector)

        assert path.read_bytes() == b'external host edit'
        assert 'changed outside the guest' in capsys.readouterr().out


def test_guest_delete_does_not_remove_untracked_host_entry():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        tracked = root / 'TRACKED.TXT'
        tracked.write_bytes(b'tracked')
        disk = HostDirectoryFAT16(root)
        untracked = root / 'HOSTONLY.TXT'
        untracked.write_bytes(b'created after mount')
        root_start, sector = root_sector(disk)

        sector[0] = 0xE5
        disk.Write(root_start * disk.sector_size, sector)

        assert not tracked.exists()
        assert untracked.read_bytes() == b'created after mount'
