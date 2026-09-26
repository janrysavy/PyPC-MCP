"""Reserved 8253 counter selection must not escape into a host IndexError.

SC=11 is illegal on the 8253 (Intel 8253/8253-5, printed page 3-54).
This is a defensive no-op policy, not an implementation of 8254 read-back.
"""
import copy
import pytest
import bus
import i8088
from i8253 import i8253


def programmed_pit():
    pit = i8253()
    for counter in range(3):
        pit.IO_Write(0x43, (counter << 6) | 0x36)
        pit.IO_Write(0x40 + counter, 0x34 + counter)
        pit.IO_Write(0x40 + counter, 0x12)
        pit.IO_Write(0x43, counter << 6)
        # Leave a latched read halfway through, including a non-default phase.
        pit.IO_Read(0x40 + counter)
    return pit


@pytest.mark.parametrize('command', range(0xc0, 0x100))
def test_reserved_counter_selection_preserves_all_channels(command):
    pit = programmed_pit()
    before = copy.deepcopy([vars(timer) for timer in pit._timers])
    pit.IO_Write(0x43, command)
    assert [vars(timer) for timer in pit._timers] == before


def test_guest_out_readback_probe_does_not_crash_cpu():
    pit = i8253()
    memory = bus.Bus(1 << 20, [], [])
    cpu = i8088.i8088(memory, [pit], True)
    state = cpu.GetState()
    state.SetCS(0x1000)
    state.SetIP(0x100)
    state.SetFlags(2)
    state.SetAL(0xe2)  # An 8254-style status read-back command.
    cpu.WriteMemByte(0x1000, 0x100, 0xe6)  # OUT 43h, AL
    cpu.WriteMemByte(0x1000, 0x101, 0x43)
    cpu.Tick()
    assert state.GetIP() == 0x102
