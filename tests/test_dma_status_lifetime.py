"""Intel 8237A TC status survives programming until status read/reset.

Order 231466-005, Status Register / Master Clear, printed page 9.
"""
import pytest
from i8237 import i8237
from test_dma_terminal_count import MemoryBus, program, transfer


def completed_channels():
    dma = i8237(MemoryBus())
    # Program every channel before completing any transfer: an unrelated mode
    # write must not hide TC that we intend to test subsequently.
    for channel in range(4):
        program(dma, channel, 'write')
    for channel in range(4):
        assert transfer(dma, channel, 'write') is True
    return dma


@pytest.mark.parametrize('channel', range(4))
@pytest.mark.parametrize('register', ['mode', 'count_low', 'count_high', 'address', 'page', 'mask'])
def test_programming_does_not_acknowledge_terminal_count(channel, register):
    dma = completed_channels()
    dma.IO_Write(0x0c, 0)
    if register == 'mode':
        dma.IO_Write(0x0b, 0x48 | channel)
    elif register.startswith('count'):
        if register == 'count_high':
            dma.IO_Read(channel * 2 + 1)
        dma.IO_Write(channel * 2 + 1, 0x12)
    elif register == 'address':
        dma.IO_Write(channel * 2, 0x34)
    elif register == 'page':
        dma.IO_Write((0x87, 0x83, 0x81, 0x82)[channel], 2)
    else:
        dma.IO_Write(0x0a, 4 | channel)
    assert all(dma.IsChannelTC(ch) for ch in range(4))
    assert dma.IO_Read(8) == 0x0f
    assert dma.IO_Read(8) == 0
    # Consuming status cannot restart non-auto-initialize DMA.
    assert all(dma._channel_mask)


def test_master_clear_acknowledges_all_terminal_counts():
    dma = completed_channels()
    dma.IO_Write(0x0d, 0)
    assert dma.IO_Read(8) == 0
    assert not any(dma.IsChannelTC(ch) for ch in range(4))
    assert all(dma._channel_mask)


def test_count_reprogramming_and_unmask_do_not_inhibit_new_transfer():
    dma = completed_channels()
    dma.IO_Write(0x0c, 0)
    dma.IO_Write(5, 1)
    dma.IO_Write(5, 0)
    dma.IO_Write(0x0a, 2)
    assert dma.IsChannelTC(2)  # sticky old completion, not a transfer inhibit
    assert transfer(dma, 2, 'write') is True
    assert dma._channel_word_count[2].GetValue() == 0
    assert transfer(dma, 2, 'write') is True
    assert dma._channel_mask[2]
