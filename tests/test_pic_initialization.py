"""8259A initialization sequencing and ICW/OCW isolation regressions."""
import pytest
from i8259 import i8259


@pytest.mark.parametrize('single', [False, True])
@pytest.mark.parametrize('icw4', [False, True])
def test_all_initialization_sequences_accept_next_mask(single, icw4):
    pic = i8259()
    pic.IO_Write(0x20, 0x10 | (2 if single else 0) | int(icw4))
    pic.IO_Write(0x21, 0x08)
    if not single:
        pic.IO_Write(0x21, 0x04)
    if icw4:
        pic.IO_Write(0x21, 0x01)
    pic.IO_Write(0x21, 0xA5)
    assert pic.IO_Read(0x21) == 0xA5
    assert not pic._in_init


@pytest.mark.parametrize('single', [False, True])
def test_omitting_icw4_clears_previous_auto_eoi(single):
    pic = i8259()
    pic.IO_Write(0x20, 0x13)
    pic.IO_Write(0x21, 0x08)
    pic.IO_Write(0x21, 0x03)  # 8086 mode, automatic EOI
    pic.IO_Write(0x20, 0x10 | (2 if single else 0))
    pic.IO_Write(0x21, 0x08)
    if not single:
        pic.IO_Write(0x21, 0)
    pic.RequestInterruptPIC(1)
    pic.SetIRQBeingServiced(1)
    pic.IO_Write(0x20, 0x0B)
    assert pic.IO_Read(0x20) == 0x02


@pytest.mark.parametrize('ocw', [0x0A, 0x0B, 0x20, 0x61])
def test_operation_commands_preserve_initialization_words(ocw):
    pic = i8259()
    pic.IO_Write(0x20, 0x13)
    pic.IO_Write(0x21, 0x08)
    pic.IO_Write(0x21, 0x01)
    pic.IO_Write(0x20, ocw)
    assert pic._icw1 == 0x13
    assert not pic._has_slave
