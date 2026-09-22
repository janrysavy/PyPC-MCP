"""Controller replay uses explicit test disk images, not a production disk codec."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from xtide import XTIDE
from xtidecodec import dump_xtide_state, load_xtide_state


class Disk:
    def __init__(self, data):
        self.data = bytearray(data)
        self.writes = []

    def Read(self, offset, length):
        return bytes(self.data[offset:offset + length])

    def Write(self, offset, data):
        self.writes.append([offset, bytes(data).hex()])
        self.data[offset:offset + len(data)] = data


def command(device, value, drive=1, count=2, sector=17, head=0):
    for port, byte in ((0x304, count), (0x306, sector), (0x308, 0),
                       (0x30a, 0), (0x30c, drive * 16 + head), (0x30e, value)):
        device.IO_Write(port, byte)


def fixture(case):
    disks = [Disk(bytes([i]) * (80 * 512)) for i in (0x12, 0x34)]
    device = XTIDE(disks)
    if case in ('write', 'masterwrite'):
        command(device, 0xc5, drive=0 if case == 'masterwrite' else 1)
        for i in range(513):
            device.IO_Write(0x300, i % 251)
        # Current taskfile/selection no longer identifies the pending write.
        device.IO_Write(0x30c, 0)
        device.IO_Write(0x306, 1)
    elif case in ('read', 'maxread'):
        command(device, 0xc4, count=0 if case == 'maxread' else 2)
        for i in range(517):
            device.IO_Read(0x300)
    elif case == 'identify':
        command(device, 0xec)
        for i in range(23):
            device.IO_Read(0x300)
    elif case == 'error':
        command(device, 0xc4, head=5)
    else:
        raise AssertionError(case)
    return device


def replay(device, case):
    initial, _ = dump_xtide_state(device)
    ports = []
    if case in ('write', 'masterwrite'):
        for i in range(511):
            device.IO_Write(0x300, (i + 513) % 251)
    else:
        count = {'read': 507, 'maxread': 256 * 512 - 517,
                 'identify': 489, 'error': 0}[case]
        ports.extend(device.IO_Read(0x300) for _ in range(count))
    ports.extend(device.IO_Read(port) for port in (0x302, 0x302, 0x30e, 0x30e))
    serials = []
    for drive in (0, 1):
        command(device, 0xec, drive=drive)
        serials.append(bytes(device.IO_Read(0x300) for _ in range(512)).hex())
    final, _ = dump_xtide_state(device)
    return {'initial': initial, 'ports': ports, 'identify': serials, 'final': final,
            'disks': [hashlib.sha256(d.data).hexdigest() for d in device._disks],
            'writes': [d.writes for d in device._disks]}


def fresh(manifest, buffer, disks, case):
    payload = {'manifest': manifest, 'buffer': buffer.hex(), 'case': case,
               'disks': [bytes(d.data).hex() for d in disks]}
    program = '''
import json,sys,typing
if not hasattr(typing,'override'):typing.override=lambda f:f
sys.path.insert(0,'tests')
from test_xtidecodec import Disk,replay
from xtidecodec import load_xtide_state
p=json.load(sys.stdin)
d=load_xtide_state(p['manifest'],bytes.fromhex(p['buffer']),[Disk(bytes.fromhex(b)) for b in p['disks']])
print(json.dumps(replay(d,p['case'])))
'''
    result = subprocess.run([sys.executable, '-c', program], input=json.dumps(payload),
                            capture_output=True, text=True, check=True, timeout=30,
                            env={**os.environ, 'PYTHONHASHSEED': '123'},
                            cwd=Path(__file__).resolve().parents[1])
    return json.loads(result.stdout)


@pytest.mark.parametrize('case', ['write', 'masterwrite', 'read', 'maxread', 'identify', 'error'])
def test_fresh_process_matches_partial_transfer_ports_writes_and_identify(case):
    device = fixture(case)
    manifest, buffer = dump_xtide_state(device)
    assert not any(d.writes for d in device._disks)
    actual = fresh(manifest, buffer, device._disks, case)
    expected = replay(device, case)
    assert actual == expected
    if case in ('write', 'masterwrite'):
        drive = 0 if case == 'masterwrite' else 1
        assert expected['writes'][1 - drive] == []
        assert expected['writes'][drive] == [[16 * 512, bytes(i % 251 for i in range(1024)).hex()]]
    if case == 'error':
        assert expected['ports'][:2] == [4, 0]


@pytest.mark.parametrize('lost', ['cursor', 'target', 'buffer', 'serial', 'status', 'error'])
def test_single_field_mutations_change_specific_continuation(lost):
    case = 'error' if lost in ('status', 'error') else 'write'
    device = fixture(case)
    manifest, buffer = dump_xtide_state(device)
    disks = [Disk(d.data) for d in device._disks]
    expected = replay(device, case)
    if lost == 'cursor':
        manifest['fields']['sector_buffer_offset'] -= 1
    elif lost == 'target':
        manifest['fields']['target_lba'] = 1
    elif lost == 'buffer':
        buffer = bytes([buffer[0] ^ 1]) + buffer[1:]
        manifest['buffer']['sha256'] = hashlib.sha256(buffer).hexdigest()
    elif lost == 'serial':
        manifest['fields']['serial_numbers'][0] = 'changed'
    elif lost == 'status':
        manifest['fields']['status_register'] = 0
    elif lost == 'error':
        manifest['fields']['error_register'] = 0
    actual = fresh(manifest, buffer, disks, case)
    if lost == 'serial':
        assert actual['identify'][0] != expected['identify'][0]
    elif lost == 'status':
        assert actual['ports'][2] != expected['ports'][2]
    elif lost == 'error':
        assert actual['ports'][0] != expected['ports'][0]
    else:
        assert actual['disks'] != expected['disks']


@pytest.mark.parametrize('mutation', [
    lambda m: m.update(version=True),
    lambda m: m['fields'].update(sector_buffer_offset=1025),
    lambda m: m['fields'].update(target_drive=2),
    lambda m: m['fields'].update(target_lba=614 * 4 * 17),
    lambda m: m['fields']['registers'].pop(),
    lambda m: m['fields'].update(serial_numbers=['one']),
    lambda m: m['fields'].update(head_count=0),
    lambda m: m['buffer'].update(sha256='bad'),
])
def test_rejects_malformed_state_without_disk_access(mutation):
    device = fixture('write')
    manifest, buffer = dump_xtide_state(device)
    before = copy.deepcopy(manifest)
    mutation(manifest)
    with pytest.raises(ValueError):
        load_xtide_state(manifest, buffer, device._disks)
    assert dump_xtide_state(device) == (before, buffer)
    assert not any(d.writes for d in device._disks)


def test_detached_mutable_buffers_disk_inventory_and_layout_guards():
    device = fixture('write')
    manifest, buffer = dump_xtide_state(device)
    restored = load_xtide_state(manifest, buffer, device._disks)
    restored._sector_buffer[0] ^= 1
    restored._registers[0] ^= 1
    restored._serial_numbers[0] = 'changed'
    assert dump_xtide_state(device) == (manifest, buffer)
    assert restored._disks is not device._disks
    with pytest.raises(ValueError, match='inventory'):
        load_xtide_state(manifest, buffer, device._disks[:1])
    device._unknown_latch = 1
    with pytest.raises(ValueError, match='layout'):
        dump_xtide_state(device)


def test_file_backed_pending_write_rebinds_to_new_paths_in_fresh_process(tmp_path):
    original = [tmp_path / f'original{i}.img' for i in range(2)]
    rebound = [tmp_path / f'restored{i}.img' for i in range(2)]
    for i, path in enumerate(original):
        path.write_bytes(bytes([0x30 + i]) * (80 * 512))
        rebound[i].write_bytes(path.read_bytes())
    device = XTIDE([str(p) for p in original])
    command(device, 0xc5)
    for i in range(513):
        device.IO_Write(0x300, i % 251)
    manifest, buffer = dump_xtide_state(device)
    before = [p.read_bytes() for p in original]
    payload = {'manifest': manifest, 'buffer': buffer.hex(),
               'paths': [str(p) for p in rebound]}
    program = '''
import json,sys,typing
if not hasattr(typing,'override'):typing.override=lambda f:f
from xtidecodec import load_xtide_state
p=json.load(sys.stdin)
d=load_xtide_state(p['manifest'],bytes.fromhex(p['buffer']),p['paths'])
for i in range(513,1024):d.IO_Write(0x300,i%251)
d.IO_Write(0x30e,0xec)
print(bytes(d.IO_Read(0x300) for _ in range(512)).hex())
'''
    result = subprocess.run([sys.executable, '-c', program], input=json.dumps(payload),
                            text=True, capture_output=True, check=True, timeout=30,
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'PYTHONHASHSEED': '456'})
    assert [p.read_bytes() for p in original] == before
    for i in range(513, 1024):
        device.IO_Write(0x300, i % 251)
    device.IO_Write(0x30e, 0xec)
    expected_identify = bytes(device.IO_Read(0x300) for _ in range(512)).hex()
    assert result.stdout.strip() == expected_identify
    assert [p.read_bytes() for p in rebound] == [p.read_bytes() for p in original]
    assert rebound[0].read_bytes() == before[0]
    assert rebound[1].read_bytes()[16 * 512:18 * 512] == bytes(i % 251 for i in range(1024))


def test_reachable_replaced_buffer_can_extend_past_pending_target_geometry():
    # Existing XTIDE behavior: a read replaces the buffer without cancelling
    # an earlier pending write target. Snapshot validation must preserve this
    # reachable state; repairing the controller's command policy is separate.
    device = XTIDE([Disk(bytes(512 * 80))])
    for port, value in ((0x304, 1), (0x306, 17), (0x308, 613 & 255),
                        (0x30a, 613 >> 8), (0x30c, 3), (0x30e, 0xc5)):
        device.IO_Write(port, value)
    capacity = 614 * 4 * 17
    assert device._target_lba == capacity - 1
    command(device, 0xc4, drive=0, sector=1)
    assert device._target_drive == 0 and len(device._sector_buffer) == 1024
    manifest, buffer = dump_xtide_state(device)
    restored = load_xtide_state(manifest, buffer, [Disk(device._disks[0].data)])
    for controller in (device, restored):
        for i in range(1024):
            controller.IO_Write(0x300, i % 251)
    assert restored._disks[0].writes == device._disks[0].writes
    assert device._disks[0].writes == [[(capacity - 1) * 512,
                                      bytes(i % 251 for i in range(1024)).hex()]]
