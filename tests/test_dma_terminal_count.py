"""Non-auto-initialize DMA must remain stopped after a status read."""
import pytest

from i8237 import i8237


class MemoryBus:
    def __init__(self):
        self.memory = bytearray(1 << 20)
        self.accesses = []

    def ReadByte(self, address):
        self.accesses.append(('read', address))
        return self.memory[address], 0

    def WriteByte(self, address, value):
        self.accesses.append(('write', address, value))
        self.memory[address] = value


def program(dma, channel, direction, address=0x2345):
    dma.IO_Write(0x0a, 4 | channel)
    dma.IO_Write(0x0c, 0)
    dma.IO_Write(channel * 2, address & 0xff)
    dma.IO_Write(channel * 2, address >> 8)
    dma.IO_Write(channel * 2 + 1, 0)  # zero means one byte
    dma.IO_Write(channel * 2 + 1, 0)
    dma.IO_Write(0x0b, (0x48 if direction == 'read' else 0x44) | channel)
    dma.IO_Write(0x0a, channel)


def transfer(dma, channel, direction):
    if direction == 'read':
        return dma.ReceiveFromChannel(channel)
    return dma.SendToChannel(channel, 0x5a)


@pytest.mark.parametrize('channel', range(4))
@pytest.mark.parametrize('direction', ['read', 'write'])
def test_status_read_does_not_restart_exhausted_dma(channel, direction):
    bus = MemoryBus()
    dma = i8237(bus)
    program(dma, channel, direction)
    bus.memory[0x2345] = 0x5a
    expected = 0x5a if direction == 'read' else True
    refused = -1 if direction == 'read' else False

    assert transfer(dma, channel, direction) == expected
    assert dma.IsChannelTC(channel)
    assert transfer(dma, channel, direction) == refused
    assert dma.IO_Read(8) == 1 << channel
    assert dma.IO_Read(8) == 0  # status remains read-to-clear
    assert transfer(dma, channel, direction) == refused
    assert len(bus.accesses) == 1
    assert dma._channel_address_register[channel].GetValue() == 0x2346
    assert dma._channel_word_count[channel].GetValue() == 0xffff
    assert dma._channel_mask[channel] is True


@pytest.mark.parametrize('channel', range(4))
@pytest.mark.parametrize('direction', ['read', 'write'])
def test_completed_channel_can_be_reprogrammed_and_unmasked(channel, direction):
    bus = MemoryBus()
    dma = i8237(bus)
    program(dma, channel, direction)
    transfer(dma, channel, direction)
    dma.IO_Read(8)
    program(dma, channel, direction, 0x3456)
    bus.memory[0x3456] = 0x5a
    assert transfer(dma, channel, direction) == (0x5a if direction == 'read' else True)
    assert len(bus.accesses) == 2
    assert bus.accesses[-1][1] == 0x3456
