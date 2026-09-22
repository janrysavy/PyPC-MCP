"""Intel 8237A Master Clear must clear the command register too."""
import pytest
from test_dma_terminal_count import MemoryBus, program, transfer
from i8237 import i8237


@pytest.mark.parametrize('channel', range(4))
@pytest.mark.parametrize('direction', ['read', 'write'])
@pytest.mark.parametrize('command', [0, 4, 0x44, 0xff])
@pytest.mark.parametrize('reset_value', [0, 0x55, 0xff])
def test_master_clear_restores_controller_enable_but_masks_channels(channel, direction, command, reset_value):
    bus = MemoryBus()
    dma = i8237(bus)
    program(dma, channel, direction)
    bus.memory[0x2345] = 0x5a
    dma.IO_Write(8, command)
    dma.IO_Read(channel * 2)  # leave shared byte pointer at MSB
    dma.IO_Write(0x0d, reset_value)
    assert dma._command == 0
    assert dma._dma_enabled is True
    assert all(dma._channel_mask)
    assert dma.IO_Read(8) == 0
    assert dma.IO_Read(channel * 2) == 0x45  # reset selects LSB
    assert dma.IO_Read(channel * 2) == 0x23
    refused = -1 if direction == 'read' else False
    assert transfer(dma, channel, direction) == refused
    assert bus.accesses == []
    dma.IO_Write(0x0a, channel)  # no command-register write after reset
    expected = 0x5a if direction == 'read' else True
    assert transfer(dma, channel, direction) == expected
    assert len(bus.accesses) == 1
    assert dma.IsChannelTC(channel)
    dma.IO_Write(0x0d, reset_value)
    assert dma.IO_Read(8) == 0
    assert all(dma._channel_mask)
