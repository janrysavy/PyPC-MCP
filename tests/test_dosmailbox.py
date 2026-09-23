"""The optional DOS mailbox must be bus visible and restartable."""
import json

import pytest

import bus
import dosmailbox
import i8088
import i8253
import i8255
import keyboard
import vga
import xtide
from checkpointbundle import read_bundle, write_bundle
from machinecodec import capture_machine, prepare_machine


def mailbox_machine(tmp_path):
    disk = tmp_path / 'disk.img'
    disk.write_bytes(bytes(4096))
    keys = keyboard.Keyboard()
    devices = [i8253.i8253(), keys, i8255.i8255(keys),
               vga.VGA(False), xtide.XTIDE([str(disk)]), dosmailbox.DOSMailbox()]
    motherboard = bus.Bus(1048576, devices, [])
    return i8088.i8088(motherboard, devices, True)


def test_mailbox_window_and_snapshot_roundtrip(tmp_path):
    cpu = mailbox_machine(tmp_path)
    base = dosmailbox.DOSMailbox.base
    cpu._b.WriteByte(base, 3)
    cpu._b.WriteByte(base + 0x1020, 0xA5)
    assert cpu._b.ReadByte(base) == (3, 0)
    manifest, buffers = capture_machine(cpu, disk_mode='embed')
    assert manifest['version'] == 2
    assert manifest['configuration']['dos_mailbox'] is True
    assert len(buffers['dos_mailbox']) == 8192
    archive = tmp_path / 'mailbox.pypc'
    digest = write_bundle(archive, manifest, buffers)
    manifest, buffers = read_bundle(archive, digest)
    cpu._b.WriteByte(base, 0)
    restored, _ = prepare_machine(json.loads(json.dumps(manifest)), buffers,
                                  tmp_path / 'restored')
    assert restored._b.ReadByte(base) == (3, 0)
    assert restored._b.ReadByte(base + 0x1020) == (0xA5, 0)
    assert type(restored._devices[5]) is dosmailbox.DOSMailbox


def test_mailbox_buffer_mutation_is_refused(tmp_path):
    cpu = mailbox_machine(tmp_path)
    manifest, buffers = capture_machine(cpu, disk_mode='embed')
    buffers['dos_mailbox'] = bytes(8192-1)
    with pytest.raises(ValueError, match='buffer hash'):
        prepare_machine(manifest, buffers, tmp_path / 'invalid')
    assert not (tmp_path / 'invalid').exists()
