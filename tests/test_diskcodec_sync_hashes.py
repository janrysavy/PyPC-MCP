"""Snapshot coverage for host-mirror deletion conflict guards."""

import copy
import hashlib

import pytest

from diskcodec import dump_disk_state, restore_disk_state
from virtualfat16 import HostDirectoryFAT16


def make_disk(tmp_path, name='source'):
    root = tmp_path / name
    root.mkdir()
    (root / 'KEEP.TXT').write_bytes(b'guest synchronized bytes')
    return HostDirectoryFAT16(root)


def root_sector(disk):
    root_start = (disk.partition_start + disk.reserved_sectors +
                  disk.fat_count * disk.fat_sectors)
    return root_start, bytearray(disk.Read(root_start * disk.sector_size,
                                           disk.sector_size))


def test_version2_roundtrip_preserves_last_synchronized_hash(tmp_path):
    original = make_disk(tmp_path)
    manifest, buffers = dump_disk_state(original)
    assert manifest['version'] == 2
    assert manifest['synced'][0]['sha256'] == hashlib.sha256(
        b'guest synchronized bytes').hexdigest()

    restored = restore_disk_state(manifest, buffers, tmp_path / 'restored')

    assert restored._synced_hashes == original._synced_hashes
    assert dump_disk_state(restored) == (manifest, buffers)


def test_legacy_version1_recovers_guard_from_guest_image(tmp_path):
    original = make_disk(tmp_path)
    manifest, buffers = dump_disk_state(original)
    legacy = copy.deepcopy(manifest)
    legacy['version'] = 1
    for item in legacy['synced']:
        item.pop('sha256')
    # The host bytes are deliberately different from the guest FAT image.
    (original.directory / 'KEEP.TXT').write_bytes(b'external edit')
    for entry in legacy['entries']:
        if entry.get('path') == 'KEEP.TXT':
            blob = entry['blob']
            buffers = dict(buffers)
            buffers[blob] = b'external edit'
            entry['data'] = {
                'size': len(buffers[blob]),
                'sha256': hashlib.sha256(buffers[blob]).hexdigest(),
            }
            break

    restored = restore_disk_state(legacy, buffers, tmp_path / 'legacy')

    key = next(iter(restored._synced_hashes))
    assert restored._synced_hashes[key] == hashlib.sha256(
        b'guest synchronized bytes').hexdigest()
    assert (restored.directory / 'KEEP.TXT').read_bytes() == b'external edit'
    assert dump_disk_state(restored)[0]['version'] == 2


def test_malformed_guard_hash_is_rejected_before_writing(tmp_path):
    manifest, buffers = dump_disk_state(make_disk(tmp_path))
    manifest = copy.deepcopy(manifest)
    manifest['synced'][0]['sha256'] = 'not-a-sha256'
    destination = tmp_path / 'rejected'

    with pytest.raises(ValueError, match='invalid sync state'):
        restore_disk_state(manifest, buffers, destination)

    assert not destination.exists()


def test_external_edit_conflict_survives_snapshot_restart(tmp_path, capsys):
    original = make_disk(tmp_path)
    host_path = original.directory / 'KEEP.TXT'
    host_path.write_bytes(b'external edit that DOS must not delete')
    root_start, sector = root_sector(original)
    sector[0] = 0xE5
    original.Write(root_start * original.sector_size, sector)
    assert 'changed outside the guest' in capsys.readouterr().out
    assert host_path.read_bytes() == b'external edit that DOS must not delete'

    manifest, buffers = dump_disk_state(original)
    restored = restore_disk_state(manifest, buffers, tmp_path / 'restored')

    with pytest.raises(ValueError, match='changed outside the guest'):
        restored.SyncToHost()
    assert (restored.directory / 'KEEP.TXT').read_bytes() == (
        b'external edit that DOS must not delete')
