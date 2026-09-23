import pytest

from i8255 import i8255
from keyboard import Keyboard


def switches(video):
    ppi = i8255(Keyboard(), video)
    low = ppi.IO_Read(0x62)
    ppi.IO_Write(0x61, 0x08)
    high = ppi.IO_Read(0x62)
    return low, high


def test_video_adapter_is_reported_through_xt_switches():
    assert switches('cga') == (0, 2)
    assert switches('vga') == (0, 0)


def test_unknown_video_adapter_is_rejected():
    with pytest.raises(ValueError, match='video must be cga or vga'):
        i8255(Keyboard(), 'mda')
