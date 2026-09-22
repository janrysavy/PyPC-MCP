"""Hardware IRQ entry must advance devices by the cycles charged to the CPU.

These test accounting consistency, not the physical accuracy of the existing
60-cycle interrupt-entry estimate.
"""
import pytest
import bus
import device
import i8088
from i8253 import i8253


class ClockRecorder(device.Device):
    def __init__(self, irq):
        super().__init__()
        self.irq = irq
        self.calls = []

    def GetName(self): return 'clock-recorder'
    def RegisterDevice(self, mappings): pass
    def IO_Write(self, port, value): return False
    def IO_Read(self, port): return 0
    def GetAddressList(self): return []
    def WriteByte(self, offset, value): pass
    def ReadByte(self, offset): return 0
    def Ticks(self): return True
    def GetIRQNumber(self): return self.irq

    def Tick(self, cycles, clock):
        self.calls.append((cycles, clock))
        return False


def machine(irq=0, recorder_count=1):
    pit = i8253()
    recorders = [ClockRecorder(irq if irq else -1)
                 for _ in range(recorder_count)]
    cpu = i8088.i8088(bus.Bus(1 << 20, [], []), [pit, *recorders], True)
    state = cpu.GetState()
    for name, value in dict(CS=0x1000, IP=0x100, SS=0x2000,
                            SP=0x9000, Flags=0x202).items():
        getattr(state, 'Set' + name)(value)
    state._clock = 1000
    cpu.WriteMemByte(0x1000, 0x100, 0x90)  # NOP
    cpu.WriteMemWord(0, (8 + irq) * 4, 0x1234)
    cpu.WriteMemWord(0, (8 + irq) * 4 + 2, 0x3000)
    pic = cpu._io.GetPIC()
    pic.IO_Write(0x21, 0)
    pit.IO_Write(0x43, 0xb4)  # channel 2, binary mode 2
    pit.IO_Write(0x42, 100)
    pit.IO_Write(0x42, 0)
    return cpu, state, pit, pic, recorders


@pytest.mark.parametrize('irq', range(8))
@pytest.mark.parametrize('halted', [False, True])
@pytest.mark.parametrize('recorder_count', [1, 2])
def test_irq_entry_clocks_every_device_once(irq, halted, recorder_count):
    cpu, state, pit, pic, recorders = machine(irq, recorder_count)
    state._in_hlt = halted
    pic.RequestInterruptPIC(irq)
    cycles = cpu.Tick()
    assert cycles == 60  # unchanged emulator estimate
    assert state.GetClock() == 1060
    assert (state.GetCS(), state.GetIP()) == (0x3000, 0x1234)
    assert not state._in_hlt
    assert not state.GetFlagI()
    assert state.GetSP() == 0x8ffa
    assert cpu.ReadMemWord(0x2000, 0x8ffa) == 0x100
    assert pit._timers[2].counter_cur == 85
    for recorder in recorders:
        assert recorder.calls == [(60, 1060)]


@pytest.mark.parametrize('blocked_by', ['if', 'mask', 'shadow', 'no_request'])
def test_nonaccepted_irq_has_no_extra_device_tick(blocked_by):
    cpu, state, pit, pic, recorders = machine()
    if blocked_by != 'no_request': pic.RequestInterruptPIC(0)
    if blocked_by == 'if': state.SetFlagI(False)
    if blocked_by == 'mask': pic.IO_Write(0x21, 255)
    if blocked_by == 'shadow': state._inhibit_interrupts = True
    cycles = cpu.Tick()
    assert state.GetIP() == 0x101
    assert state.GetClock() == 1000 + cycles
    assert recorders[0].calls == [(cycles, 1000 + cycles)]
    assert pit._timers[2].counter_cur == 100 - cycles // 4


def test_pit_edge_during_irq_entry_is_not_lost():
    cpu, state, pit, pic, _ = machine()
    pit.IO_Write(0x43, 0x34)
    pit.IO_Write(0x40, 10)
    pit.IO_Write(0x40, 0)
    pic.RequestInterruptPIC(0)
    cpu.Tick()
    assert pit._timers[0].counter_cur == 5
    assert pic._isr == 1
    assert pic._irr == 1  # next edge generated during entry, not old request
