"""8253 count latch snapshots running counts, not a new timer mode."""
import pytest
from i8253 import i8253


def configured(channel, access=3, count=0xeabc):
    pit = i8253()
    pit.IO_Write(0x43, (channel << 6) | (access << 4) | 4)
    if access in (1, 3):
        pit.IO_Write(0x40 + channel, count & 255)
    if access in (2, 3):
        pit.IO_Write(0x40 + channel, count >> 8)
    return pit


@pytest.mark.parametrize('channel', range(3))
@pytest.mark.parametrize('access', [1, 2, 3])
@pytest.mark.parametrize('ignored_bits', [0, 15])
def test_snapshot_stays_stable_while_counter_runs(channel, access, ignored_bits):
    pit = configured(channel, access)
    timer = pit._timers[channel]
    original = timer.counter_cur
    pit.IO_Write(0x43, (channel << 6) | ignored_bits)
    pit.Tick(4 * 7, 0)
    assert timer.counter_cur == original - 7
    if access in (1, 3):
        assert pit.IO_Read(0x40 + channel) == original & 255
    if access in (2, 3):
        pit.Tick(4 * 260, 0) if access == 3 else pit.Tick(4 * 300, 0)
        assert pit.IO_Read(0x40 + channel) == (original >> 8) & 255
    assert timer.mode == 2
    assert timer.latch_type == access
    assert not timer.is_bcd
    assert timer.is_running


@pytest.mark.parametrize('channel', range(3))
@pytest.mark.parametrize('read_low_first', [False, True])
def test_second_latch_ignored_until_snapshot_consumed(channel, read_low_first):
    pit = configured(channel)
    port = 0x40 + channel
    pit.IO_Write(0x43, channel << 6)
    if read_low_first:
        assert pit.IO_Read(port) == 0xbc
    pit.Tick(4 * 0x321, 0)
    pit.IO_Write(0x43, channel << 6)
    if not read_low_first:
        assert pit.IO_Read(port) == 0xbc
    assert pit.IO_Read(port) == 0xea
    # Full consumption rearms latch for a new snapshot.
    pit.IO_Write(0x43, channel << 6)
    expected = 0xeabc - 0x321
    pit.Tick(4 * 0x200, 0)
    assert pit.IO_Read(port) == expected & 255
    assert pit.IO_Read(port) == expected >> 8


@pytest.mark.parametrize('channel', range(3))
def test_new_mode_discards_unread_latch(channel):
    pit = configured(channel)
    pit.IO_Write(0x43, channel << 6)
    pit.IO_Write(0x43, (channel << 6) | 0x34)
    pit.IO_Write(0x40 + channel, 0x78)
    pit.IO_Write(0x40 + channel, 0x56)
    pit.IO_Write(0x43, channel << 6)
    pit.Tick(80, 0)
    assert pit.IO_Read(0x40 + channel) == 0x78
    assert pit.IO_Read(0x40 + channel) == 0x56


def test_latches_are_independent_and_dont_use_live_read_noise():
    pit = i8253()
    for channel in range(3):
        pit.IO_Write(0x43, (channel << 6) | 0x34)
        pit.IO_Write(0x40 + channel, 0x40 + channel)
        pit.IO_Write(0x40 + channel, 0x70 + channel)
        pit.IO_Write(0x43, channel << 6)
    pit.AddNoiseToLSB = lambda nr: pytest.fail('latched reads cannot sample live noise')
    pit.Tick(400, 0)
    for channel in reversed(range(3)):
        assert pit.IO_Read(0x40 + channel) == 0x40 + channel
        assert pit.IO_Read(0x40 + channel) == 0x70 + channel
