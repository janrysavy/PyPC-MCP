"""Intel 8259A status selector: RR gates RIS, and ICW1 restores IRR."""
import pytest
from i8259 import i8259


@pytest.mark.parametrize('irq', [0, 3, 7])
@pytest.mark.parametrize('select', [0x0a, 0x0b])
@pytest.mark.parametrize('command', [0x08, 0x09, 0x28, 0x29, 0x48, 0x49, 0x68, 0x69])
def test_rr_zero_preserves_read_selection(irq, select, command):
    pic = i8259()
    pic.RequestInterruptPIC((irq + 1) % 8)
    pic.SetIRQBeingServiced((irq + 1) % 8)
    pic.RequestInterruptPIC(irq)
    pic.IO_Write(0x20, select)
    expected = 1 << (irq if select == 0x0a else (irq + 1) % 8)
    assert pic.IO_Read(0x20) == expected
    pic.IO_Write(0x20, command)
    assert pic.IO_Read(0x20) == expected
    assert pic.IO_Read(0x20) == expected


@pytest.mark.parametrize('select', [0x0a, 0x0b])
@pytest.mark.parametrize('icw1', [0x11, 0x13])
def test_initialization_restores_irr_selection(select, icw1):
    pic = i8259()
    pic.IO_Write(0x20, select)
    pic.IO_Write(0x20, icw1)
    pic.IO_Write(0x21, 0x08)
    if not icw1 & 2:
        pic.IO_Write(0x21, 0x04)
    pic.IO_Write(0x21, 0x01)
    pic.RequestInterruptPIC(3)
    assert pic.IO_Read(0x20) == 0x08
    pic.IO_Write(0x20, 0x0b)
    assert pic.IO_Read(0x20) == 0
    pic.IO_Write(0x20, 0x0a)
    assert pic.IO_Read(0x20) == 0x08
