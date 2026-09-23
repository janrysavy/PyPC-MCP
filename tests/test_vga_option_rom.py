import hashlib
from pathlib import Path

from biosservice import VGAInterruptService
from state8088 import State8088
from vga import VGA


EXPECTED_SHA256 = '682933a13f5079cb940886f648fad51ee9b7e69b41b534e072ed1af60ec9cc39'


def test_vga_option_rom_header_size_and_checksum():
    rom = Path(__file__).resolve().parents[1] / 'roms/PYPCVGA.ROM'
    data = rom.read_bytes()
    assert data[:3] == b'\x55\xaa\x01'
    assert len(data) == data[2] * 512
    assert sum(data) & 0xff == 0
    assert hashlib.sha256(data).hexdigest() == EXPECTED_SHA256


def test_option_rom_installs_vga_vector_with_glabios_text_fallback():
    data = (Path(__file__).resolve().parents[1] / 'roms/PYPCVGA.ROM').read_bytes()
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
