"""8237 byte transfers follow mode bit 5 without carrying into the page."""
import pytest
from i8237 import i8237
from test_dma_terminal_count import MemoryBus


@pytest.mark.parametrize('channel', range(4))
@pytest.mark.parametrize('direction', ['read', 'write'])
@pytest.mark.parametrize('decrement', [False, True])
@pytest.mark.parametrize('start', [0, 0xffff, 0x2345])
def test_address_direction_and_page_wrap(channel, direction, decrement, start):
    bus = MemoryBus()
    dma = i8237(bus)
    page = 7
    dma.IO_Write(0x0a, channel | 4)
    dma.IO_Write(0x0c, 0)
    dma.IO_Write(channel * 2, start & 255)
    dma.IO_Write(channel * 2, start >> 8)
    dma.IO_Write(channel * 2 + 1, 2)  # three transfers
    dma.IO_Write(channel * 2 + 1, 0)
    dma.IO_Write((0x87, 0x83, 0x81, 0x82)[channel], page)
    dma.IO_Write(0x0b, channel | (0x48 if direction == 'read' else 0x44)
                 | (0x20 if decrement else 0))
    dma.IO_Write(0x0a, channel)
    step = -1 if decrement else 1
    expected_addresses = [(page << 16) | ((start + step * i) & 0xffff)
                          for i in range(3)]
    for i, address in enumerate(expected_addresses):
        bus.memory[address] = 0x51 + i
    for i, address in enumerate(expected_addresses):
        if direction == 'read':
            assert dma.ReceiveFromChannel(channel) == 0x51 + i
        else:
            assert dma.SendToChannel(channel, 0xa1 + i)
            assert bus.memory[address] == 0xa1 + i
        assert dma._channel_address_register[channel].GetValue() == ((start + step * (i + 1)) & 0xffff)
        assert dma._channel_word_count[channel].GetValue() == ((1 - i) & 0xffff)
    assert [item[1] for item in bus.accesses] == expected_addresses
    assert dma._channel_page[channel] == page
    assert dma.IO_Read(8) == 1 << channel
    before = len(bus.accesses)
    assert dma.ReceiveFromChannel(channel) == -1
    assert not dma.SendToChannel(channel, 0)
    assert len(bus.accesses) == before
