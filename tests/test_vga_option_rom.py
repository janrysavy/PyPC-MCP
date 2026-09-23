import hashlib
from pathlib import Path

import bus
from biosservice import VGAInterruptService
import i8088
import rom
from state8088 import State8088
from vga import VGA


EXPECTED_SHA256 = '682933a13f5079cb940886f648fad51ee9b7e69b41b534e072ed1af60ec9cc39'
ROOT = Path(__file__).resolve().parents[1]


def run_until(cpu, predicate, maximum=100_000):
    for _ in range(maximum):
        assert cpu.Tick() != -1
        if predicate():
            return
    raise AssertionError('CPU did not reach the expected option-ROM state')


def option_rom_machine():
    video = VGA(False)
    motherboard = bus.Bus(1 << 20, [video], [
        rom.Rom(str(ROOT / 'roms/GLABIOS.ROM'), 0xfe000),
        rom.Rom(str(ROOT / 'roms/PYPCVGA.ROM'), 0xc0000),
    ])
    cpu = i8088.i8088(motherboard, [video], True)
    state = cpu.GetState()
    cpu.SetInterruptServiceHook(VGAInterruptService(video))
    for address in range(0x500):
        cpu.WriteMemByte(0, address, 0)
    cpu.WriteMemWord(0, 0x40, 0xf065)  # GLaBIOS INT 10h
    cpu.WriteMemWord(0, 0x42, 0xf000)
    cpu.WriteMemWord(0, 0x74, 0xf0a4)  # GLaBIOS INT 1Dh mode table
    cpu.WriteMemWord(0, 0x76, 0xf000)
    state.SetCS(0xc000)
    state.SetIP(3)
    state.SetSS(0x300)
    state.SetSP(0x100)
    state.SetDS(0)
    state.SetES(0)
    state.SetFlags(2)
    cpu.WriteMemWord(0x300, 0x100, 0x100)
    cpu.WriteMemWord(0x300, 0x102, 0x1000)
    cpu.WriteMemByte(0x1000, 0x100, 0xf4)
    return cpu, state, video


def run_interrupt(cpu, state, offset, ax, bx=0):
    state._in_hlt = False
    state.SetCS(0x1000)
    state.SetIP(offset)
    state.SetSS(0x300)
    state.SetSP(0x200)
    state.SetAX(ax)
    state.SetBX(bx)
    state.SetFlags(2)
    cpu.WriteMemByte(0x1000, offset, 0xcd)
    cpu.WriteMemByte(0x1000, offset + 1, 0x10)
    cpu.WriteMemByte(0x1000, offset + 2, 0xf4)
    run_until(cpu, lambda: (state.GetCS(), state.GetIP(), state.GetInHlt())
              == (0x1000, offset + 3, True))


def test_vga_option_rom_header_size_and_checksum():
    data = (ROOT / 'roms/PYPCVGA.ROM').read_bytes()
    assert data[:3] == b'\x55\xaa\x01'
    assert len(data) == data[2] * 512
    assert sum(data) & 0xff == 0
    assert hashlib.sha256(data).hexdigest() == EXPECTED_SHA256


def test_option_rom_installs_vga_vector_with_glabios_text_fallback():
    data = (ROOT / 'roms/PYPCVGA.ROM').read_bytes()
    video_segment = data.index(bytes.fromhex('c7 06 ae 00 00 b8'))
    vector = data.index(bytes.fromhex('c7 06 40 00'))
    bridge = int.from_bytes(data[vector + 4:vector + 6], 'little')
    assert video_segment < vector
    assert data[vector + 6:vector + 12] == bytes.fromhex('c7 06 42 00 00 c0')
    assert data[bridge:bridge + 5] == bytes.fromhex('ea 65 f0 00 f0')


def test_vga_detection_is_available_to_glabios_post_only():
    service = VGAInterruptService(VGA(False))
    state = State8088()
    state.SetCS(0xf000)
    state.SetAX(0x1a00)
    state.SetBX(0xff00)
    assert service(0x10, state)
    assert state.GetAX() == 0x1a1a
    assert state.GetBX() == 0xff08

    state.SetAX(0x0f00)
    assert not service(0x10, state)


def test_option_rom_executes_and_chains_text_modes_through_glabios():
    cpu, state, video = option_rom_machine()
    run_until(cpu, lambda: (state.GetCS(), state.GetIP()) == (0x1000, 0x100))

    assert cpu.ReadMemWord(0, 0x40) == 0x009c
    assert cpu.ReadMemWord(0, 0x42) == 0xc000
    assert cpu.ReadMemByte(0x40, 0x49) == 3
    assert cpu.ReadMemWord(0x40, 0x4a) == 80
    assert cpu.ReadMemWord(0x40, 0xae) == 0xb800
    assert (video.ReadByte(0xb8000), video.ReadByte(0xb8001)) == (0x20, 0x07)

    run_interrupt(cpu, state, 0x200, 0x0e41, 0x0007)
    assert (video.ReadByte(0xb8000), video.ReadByte(0xb8001)) == (0x41, 0x07)

    run_interrupt(cpu, state, 0x210, 0x0000)
    assert cpu.ReadMemByte(0x40, 0x49) == 0
    assert cpu.ReadMemWord(0x40, 0x4a) == 40
    run_interrupt(cpu, state, 0x220, 0x0f00)
    assert state.GetAX() == 0x2800
