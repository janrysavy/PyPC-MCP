import copy
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest
import bus, i8088, i8253, i8255, keyboard, vga, xtide
from machinecodec import capture_machine, prepare_machine


def machine(tmp_path):
    disk = tmp_path/'disk.img'; disk.write_bytes(bytes(4096))
    kb = keyboard.Keyboard()
    devices = [i8253.i8253(), kb, i8255.i8255(kb), vga.VGA(False), xtide.XTIDE([str(disk)])]
    b = bus.Bus(1048576, devices, [])
    cpu = i8088.i8088(b, devices, True)
    state = cpu.GetState(); state.SetCS(0x1000); state.SetIP(0); state.SetDS(0x1000)
    # Repeated timer reads, RAM stores and increments exercise host RNG and ticks.
    b._m._m[0x10000:0x1000d] = bytes.fromhex('ba4000eca20002ff060202ebf6')
    cpu._io.Out(0x43, 0x36, False); cpu._io.Out(0x40, 17, False); cpu._io.Out(0x40, 0, False)
    kb.PushKeyboardScancode(0x1e)
    controller = devices[4]
    for port, value in ((0x304,1),(0x306,1),(0x308,0),(0x30a,0),(0x30c,0),(0x30e,0xc5)):
        controller.IO_Write(port,value)
    for value in (11,22,33): controller.IO_Write(0x300,value)
    for _ in range(11): cpu.Tick()
    return cpu


def run(cpu, count=200):
    events = []
    cpu.SetMemoryTraceHook(lambda *args: events.append(args))
    for _ in range(count): cpu.Tick()
    cpu.SetMemoryTraceHook(None)
    for index in range(509): cpu._devices[4].IO_Write(0x300,index%251)
    return events


def test_coordinated_continuation(tmp_path):
    original = machine(tmp_path)
    manifest, buffers = capture_machine(original)
    expected_trace = run(original)
    expected, expected_buffers = capture_machine(original)
    restored, rng = prepare_machine(json.loads(json.dumps(manifest)), buffers, tmp_path/'restore')
    random.setstate(rng.getstate())
    assert run(restored) == expected_trace
    actual, actual_buffers = capture_machine(restored)
    assert actual == expected
    assert actual_buffers == expected_buffers
    assert restored._io._pic is restored._devices[6]
    assert restored._devices[0]._i8237 is restored._io._i8237
    assert restored._devices[1]._pic is restored._io._pic
    assert restored._devices[2]._kb is restored._devices[1]
    for index in range(6):
        assert restored._devices[index]._b is restored._b
    for index in range(4):
        assert restored._devices[index]._pic is restored._io._pic


def test_custom_bios_hook_and_excess_disks_are_refused(tmp_path):
    cpu = machine(tmp_path)
    cpu.SetInterruptServiceHook(lambda number, state: True)
    with pytest.raises(ValueError, match='unsupported BIOS'):
        capture_machine(cpu, bios_service=True)
    cpu.SetInterruptServiceHook(None)
    cpu._devices[4]._disks *= 3
    with pytest.raises(ValueError, match='disk inventory'):
        capture_machine(cpu)


def test_rom_bios_and_two_disk_reconstruction(tmp_path):
    import rom
    from biosservice import VGAInterruptService
    from virtualfat16 import HostDirectoryFAT16
    from machineinstall import install_machine
    cpu = machine(tmp_path)
    host = tmp_path/'host'; host.mkdir()
    (host/'PYRO.DAT').write_bytes(b'live host data')
    cpu._devices[4] = xtide.XTIDE([cpu._devices[4]._disks[0], HostDirectoryFAT16(host)])
    # Build real motherboard links after substituting the two-disk controller.
    devices = cpu._devices[:5]
    rom_path = tmp_path/'rom.bin'; rom_path.write_bytes(bytes(range(256))*32)
    rom_device = rom.Rom(str(rom_path), 0xfe000)
    motherboard = bus.Bus(1048576, devices, [rom_device])
    cpu = i8088.i8088(motherboard, devices, True)
    cpu.SetInterruptServiceHook(VGAInterruptService(devices[3]))
    cpu.GetState().SetCS(0x1000)
    original = capture_machine(cpu, bios_service=True)
    restored, rng = prepare_machine(*original, tmp_path/'restored')
    assert restored._b.ReadByte(0xfe07b) == cpu._b.ReadByte(0xfe07b)
    assert restored._interrupt_service_hook.video is restored._devices[3]
    install_machine(cpu, restored, rng)
    assert cpu._interrupt_service_hook.video is cpu._devices[3]
    assert capture_machine(cpu, bios_service=True) == original
    # A real INT 10h mode query must use the restored video adapter.
    cpu.GetState().SetAX(0x0f00)
    assert cpu._interrupt_service_hook(0x10, cpu.GetState())
    assert cpu.GetState().GetAX() == 0x5003
    assert (cpu._devices[4]._disks[1].directory/'PYRO.DAT').read_bytes() == b'live host data'


def test_reference_disk_machine(tmp_path):
    cpu = machine(tmp_path)
    reference = cpu._devices[4]._disks[0]
    manifest, buffers = capture_machine(cpu, disk_mode='reference')
    restored, rng = prepare_machine(manifest, buffers, tmp_path/'restored', {0: reference})
    random.setstate(rng.getstate())
    assert capture_machine(restored, disk_mode='reference') == (manifest, buffers)


@pytest.mark.parametrize('damage', ['ram','source','pit','disk','extra'])
def test_bad_machine_never_materializes_disks(tmp_path, damage):
    original = machine(tmp_path); before, raw = capture_machine(original)
    bad = copy.deepcopy(before); buffers = dict(raw)
    if damage == 'ram': buffers['ram'] = b'wrong'
    elif damage == 'source': bad['source']['i8088.py'] = 'wrong'
    elif damage == 'pit': bad['pit']['version'] = -1
    elif damage == 'disk': bad['disks'][0]['image']['sha256'] = 'wrong'
    else: buffers['unused'] = b''
    with pytest.raises(ValueError): prepare_machine(bad, buffers, tmp_path/'restore')
    assert not (tmp_path/'restore').exists()
    assert capture_machine(original) == (before, raw)


def test_fresh_process_machine_continuation(tmp_path):
    from checkpointbundle import write_bundle
    original = machine(tmp_path)
    before, buffers = capture_machine(original)
    trace = run(original)
    expected, _ = capture_machine(original)
    bundle = tmp_path/'saved.pypc'
    digest = write_bundle(bundle, before, buffers)
    payload = tmp_path/'machine.json'
    payload.write_text(json.dumps({'bundle':str(bundle), 'sha256':digest,
                                   'expected':expected,'trace':trace}))
    code = '''import sys,typing,json,random
if not hasattr(typing,'override'): typing.override=lambda f:f
from pathlib import Path
from machinecodec import prepare_machine,capture_machine
from checkpointbundle import read_bundle
sys.path.insert(0,'tests')
from test_machinecodec import run
data=json.loads(Path(sys.argv[1]).read_text())
cpu,rng=prepare_machine(*read_bundle(data['bundle'],data['sha256']),sys.argv[2])
random.setstate(rng.getstate())
trace=run(cpu)
assert json.loads(json.dumps(trace))==data['trace']
actual,buffers=capture_machine(cpu)
assert actual==data['expected']
assert Path(cpu._devices[4]._disks[0]).read_bytes()[:3]==bytes((11,22,33))
print('fresh-process whole-component continuation equal')
'''
    result=subprocess.run([sys.executable,'-c',code,str(payload),str(tmp_path/'fresh')],
                          cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,check=True)
    assert 'continuation equal' in result.stdout
