"""LOCK is an instruction prefix, not an independently stepped opcode."""
import pytest

import bus
import i8088


PREFIXES = [
    (b'\xf0', 0x2000),
    (b'\xf0\x26', 0x3000), (b'\x26\xf0', 0x3000),
    (b'\xf0\x36', 0x4000), (b'\x36\xf0', 0x4000),
]


def machine(code, trap=False, ip=0x100):
    memory = bus.Bus(1 << 20, [], [])
    cpu = i8088.i8088(memory, [], False)
    state = cpu.GetState()
    for name, value in dict(CS=0x1000, DS=0x2000, ES=0x3000,
                            SS=0x4000, IP=ip, SP=0x9000,
                            Flags=0x103 if trap else 3).items():
        getattr(state, 'Set' + name)(value)
    for offset, byte in enumerate(code):
        cpu.WriteMemByte(0x1000, (ip + offset) & 0xffff, byte)
    for segment in (0x2000, 0x3000, 0x4000):
        cpu.WriteMemWord(segment, 0x1234, 0)
    return cpu, state


@pytest.mark.parametrize('prefix,segment', PREFIXES)
@pytest.mark.parametrize('word', [False, True])
def test_lock_and_segment_prefixes_complete_in_one_tick(prefix, segment, word):
    code = prefix + bytes((0xff if word else 0xfe, 0x06, 0x34, 0x12))
    cpu, state = machine(code)
    cpu.Tick()
    assert state.GetIP() == 0x100 + len(code)
    assert state.GetFlagC()  # INC preserves carry
    for check_segment in (0x2000, 0x3000, 0x4000):
        assert cpu.ReadMemWord(check_segment, 0x1234) == (1 if check_segment == segment else 0)
    assert not state._segment_override_set


@pytest.mark.parametrize('prefix,segment', PREFIXES)
def test_trap_observes_completed_locked_instruction(prefix, segment):
    code = prefix + bytes.fromhex('ff 06 34 12')
    cpu, state = machine(code, trap=True)
    cpu.WriteMemWord(0, 4, 0x2222)
    cpu.WriteMemWord(0, 6, 0x3333)
    cpu.Tick()
    assert (state.GetCS(), state.GetIP()) == (0x3333, 0x2222)
    assert cpu.ReadMemWord(segment, 0x1234) == 1
    assert cpu.ReadMemWord(0x4000, state.GetSP()) == 0x100 + len(code)
    assert not state.GetFlagT()


def test_lock_prefix_instruction_fetch_wraps_ip():
    cpu, state = machine(bytes.fromhex('f0 ff 06 34 12'), ip=0xffff)
    cpu.Tick()
    assert state.GetIP() == 4
    assert cpu.ReadMemWord(0x2000, 0x1234) == 1
