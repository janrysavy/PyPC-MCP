"""8237 current/base register pairs and automatic block restart at TC."""
import pytest
from i8237 import i8237
from test_dma_terminal_count import MemoryBus


def put_word(dma, port, value):
    dma.IO_Write(0x0c, 0)
    dma.IO_Write(port, value & 255)
    dma.IO_Write(port, value >> 8)


def read_word(dma, port):
    dma.IO_Write(0x0c, 0)
    return dma.IO_Read(port) | (dma.IO_Read(port) << 8)


def setup(channel=2, direction='read', decrement=False, address=0xffff, count=2, auto=True):
    bus = MemoryBus()
    dma = i8237(bus)
    dma.IO_Write(0x0a, channel | 4)
    put_word(dma, channel * 2, address)
    put_word(dma, channel * 2 + 1, count)
    dma.IO_Write((0x87, 0x83, 0x81, 0x82)[channel], 7)
    mode = 0x48 if direction == 'read' else 0x44
    dma.IO_Write(0x0b, mode | channel | (0x10 if auto else 0) | (0x20 if decrement else 0))
    dma.IO_Write(0x0a, channel)
    return bus, dma


@pytest.mark.parametrize('channel', range(4))
@pytest.mark.parametrize('direction', ['read', 'write'])
@pytest.mark.parametrize('decrement', [False, True])
@pytest.mark.parametrize('address', [0, 0xffff])
@pytest.mark.parametrize('count', [0, 2])
@pytest.mark.parametrize('read_status', [False, True])
def test_three_blocks_reload_without_status_ack(channel, direction, decrement, address, count, read_status):
    bus, dma = setup(channel, direction, decrement, address, count)
    step = -1 if decrement else 1
    addresses = [(7 << 16) | ((address + step * i) & 0xffff) for i in range(count + 1)]
    for i, addr in enumerate(addresses):
        bus.memory[addr] = 0x40 + i
    for block in range(3):
        for i in range(count + 1):
            if direction == 'read':
                assert dma.ReceiveFromChannel(channel) == 0x40 + i
            else:
                assert dma.SendToChannel(channel, 0x80 + block * 4 + i)
            remaining = (i + 1) % (count + 1)
            assert read_word(dma, channel * 2) == ((address + step * remaining) & 0xffff)
            assert read_word(dma, channel * 2 + 1) == count - remaining
        assert dma.IsChannelTC(channel)
        assert dma._channel_mask[channel] is False
        if read_status:
            assert dma.IO_Read(8) == 1 << channel
            assert dma.IO_Read(8) == 0
    assert [entry[1] for entry in bus.accesses] == addresses * 3
    assert dma._channel_page[channel] == 7


@pytest.mark.parametrize('high_byte', [False, True])
def test_partial_address_write_keeps_independent_unwritten_base_byte(high_byte):
    _, dma = setup(address=0x12ff, count=1)
    dma.ReceiveFromChannel(2)  # current address 1300, base still 12ff
    dma.IO_Write(0x0c, 0)
    if high_byte:
        dma.IO_Read(4)  # select MSB using the one shared flip-flop
    dma.IO_Write(4, 0x34)
    assert dma._channel_address_register[2].GetValue() == (0x3400 if high_byte else 0x1334)
    dma.ReceiveFromChannel(2)
    assert read_word(dma, 4) == (0x34ff if high_byte else 0x1234)


@pytest.mark.parametrize('high_byte', [False, True])
def test_partial_count_write_keeps_independent_unwritten_base_byte(high_byte):
    _, dma = setup(address=0x1234, count=0x0100)
    dma.ReceiveFromChannel(2)  # current count 00ff, base count 0100
    dma.IO_Write(0x0c, 0)
    if high_byte:
        dma.IO_Read(5)
    dma.IO_Write(5, 0)
    # Current is 00ff (high write) or 0000 (low write).
    for _ in range(256 if high_byte else 1):
        dma.ReceiveFromChannel(2)
    assert read_word(dma, 5) == (0 if high_byte else 0x0100)
    assert read_word(dma, 4) == 0x1234


def test_reload_does_not_toggle_cpu_byte_pointer():
    _, dma = setup(count=0, address=0x1234)
    dma.IO_Write(0x0c, 0)
    assert dma.IO_Read(4) == 0x34
    dma.ReceiveFromChannel(2)
    assert dma.IO_Read(4) == 0x12  # still MSB after the automatic reload


@pytest.mark.parametrize('auto', [False, True])
@pytest.mark.parametrize('decrement', [False, True])
@pytest.mark.parametrize('count', [0, 2, 0xffff])
@pytest.mark.parametrize('transfers', [0, 1, 3, 11, 131075])
def test_refresh_batch_obeys_reload_and_terminal_mask(auto, decrement, count, transfers):
    _, dma = setup(channel=0, count=count, address=0xfffe, auto=auto, decrement=decrement)
    dma.TickChannel0(transfers)
    used = transfers % (count + 1) if auto else min(transfers, count + 1)
    assert read_word(dma, 0) == ((0xfffe + (-1 if decrement else 1) * used) & 0xffff)
    assert read_word(dma, 1) == ((count - used) & 0xffff)
    assert dma.IsChannelTC(0) == (transfers >= count + 1)
    assert dma._channel_mask[0] == (not auto and transfers >= count + 1)


@pytest.mark.parametrize('port,value', [(8, 4), (0x0a, 4)])
def test_disabled_refresh_does_not_advance(port, value):
    _, dma = setup(channel=0, address=0x1234)
    dma.IO_Write(port, value)
    dma.TickChannel0(100)
    assert read_word(dma, 0) == 0x1234
    assert read_word(dma, 1) == 2
    assert not dma.IsChannelTC(0)
