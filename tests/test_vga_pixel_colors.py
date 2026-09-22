"""INT 10h/0Ch: bit 7 is a color bit in mode 13h, XOR only in mode 12h."""
import pytest

from state8088 import State8088
from vga import VGA


@pytest.mark.parametrize('initial', [0x00, 0x55, 0xff])
@pytest.mark.parametrize('x,y', [(0, 0), (7, 9), (319, 199)])
def test_mode13_all_color_indices_replace_existing_pixel(initial, x, y):
    video = VGA(False)
    assert video.BiosSetMode(0x13)
    address = 0xa0000 + y * 320 + x
    state = State8088()
    state.SetCX(x)
    state.SetDX(y)
    for color in range(256):
        video.WriteByte(address, initial)
        state.SetAX(0x0c00 | color)
        assert video.BiosInterrupt(state)
        assert video.ReadByte(address) == color, (initial, x, y, color)
        # A repeated write is still replacement, not an XOR toggle.
        assert video.BiosInterrupt(state)
        state.SetAH(0x0d)
        assert video.BiosInterrupt(state)
        assert state.GetAL() == color


@pytest.mark.parametrize('initial', [0, 5, 15])
@pytest.mark.parametrize('x,y', [(0, 0), (7, 9), (639, 479)])
def test_mode12_retains_bit7_xor_and_neighbor_pixels(initial, x, y):
    video = VGA(False)
    assert video.BiosSetMode(0x12)
    neighbor = x ^ 1
    assert video.BiosWritePixel(neighbor, y, 3)
    for color in range(16):
        assert video.BiosWritePixel(x, y, initial)
        assert video.BiosWritePixel(x, y, 0x80 | color)
        assert video.BiosReadPixel(x, y) == initial ^ color
        assert video.BiosReadPixel(neighbor, y) == 3
        assert video.BiosWritePixel(x, y, 0x80 | color)
        assert video.BiosReadPixel(x, y) == initial
