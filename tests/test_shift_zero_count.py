"""8088 group-2 flag preservation, corroborated by physical 8088 traces.

Synthetic cases only; no proprietary binaries or external test corpus needed.
The patch must not mask CL to five bits or change nonzero-count behavior.
"""
import pytest
import bus
import i8088

FLAG_BITS = (0, 2, 4, 6, 7, 11)


def machine(bits, mode, form, value, flags, count=0):
    memory = bus.Bus(1 << 20, [], [])
    cpu = i8088.i8088(memory, [], False)
    state = cpu.GetState()
    for name, val in dict(CS=0x1000, DS=0x2000, SS=0x3000, ES=0x4000,
                          IP=0x100, SP=0x9000, BP=0x5000, AX=0x5a00,
                          CX=0xa000 | count, Flags=flags).items():
        getattr(state, 'Set' + name)(val)
    opcode = 0xd2 if bits == 8 else 0xd3
    if form == 'register':
        code = bytes((opcode, 0xc0 | mode << 3))
        getattr(state, 'SetAL' if bits == 8 else 'SetAX')(value)
        read = state.GetAL if bits == 8 else state.GetAX
    else:
        if form == 'stack':
            code = bytes((opcode, 0x46 | mode << 3, 0))
            segment, offset = 0x3000, 0x5000
        else:
            code = bytes((opcode, 0x06 | mode << 3, 0, 0x50))
            segment, offset = 0x2000, 0x5000
            if form == 'override':
                code = b'\x26' + code
                segment = 0x4000
        getattr(cpu, 'WriteMemByte' if bits == 8 else 'WriteMemWord')(segment, offset, value)
        read = lambda: getattr(cpu, 'ReadMemByte' if bits == 8 else 'ReadMemWord')(segment, offset)
    for i, byte in enumerate(code):
        cpu.WriteMemByte(0x1000, 0x100 + i, byte)
    return cpu, state, code, read


@pytest.mark.parametrize('bits', [8, 16])
@pytest.mark.parametrize('mode', range(8))
@pytest.mark.parametrize('form', ['register', 'direct', 'stack', 'override'])
@pytest.mark.parametrize('pattern', ['zero', 'one', 'top', 'ones'])
def test_zero_count_preserves_operand_and_all_flags(bits, mode, form, pattern):
    value = {'zero': 0, 'one': 1, 'top': 1 << (bits - 1), 'ones': (1 << bits) - 1}[pattern]
    for combination in range(64):
        flags = 0xf402 | sum(1 << bit for i, bit in enumerate(FLAG_BITS) if combination & (1 << i))
        cpu, state, code, read = machine(bits, mode, form, value, flags)
        before_flags = state.GetFlags()
        before_ax = state.GetAX()
        cpu.Tick()
        assert read() == value
        assert state.GetAX() == before_ax
        assert state.GetCX() == 0xa000
        assert state.GetIP() == 0x100 + len(code)
        assert state.GetFlags() == before_flags, (mode, bits, form, value, hex(before_flags), hex(state.GetFlags()))


@pytest.mark.parametrize('bits', [8, 16])
@pytest.mark.parametrize('mode', [0, 1, 2, 3, 4, 5, 7])
@pytest.mark.parametrize('count', [1, 8, 32, 255])
def test_nonzero_count_results_and_8088_unmasked_cl(bits, mode, count):
    mask = (1 << bits) - 1
    top = 1 << (bits - 1)
    value = top | 3
    expected, carry = value, 1
    for _ in range(count):
        old, old_carry = expected, carry
        if mode in (0, 2, 4):
            carry = (old >> (bits - 1)) & 1
            inserted = carry if mode == 0 else old_carry if mode == 2 else 0
            expected = ((old << 1) | inserted) & mask
        else:
            carry = old & 1
            inserted = carry if mode == 1 else old_carry if mode == 3 else (old >> (bits - 1)) if mode == 7 else 0
            expected = (old >> 1) | (inserted * top)
    cpu, state, code, read = machine(bits, mode, 'register', value, 0xf003, count)
    cpu.Tick()
    assert read() == expected
    assert state.GetFlagC() == bool(carry)
    assert state.GetCX() == 0xa000 | count
    assert state.GetIP() == 0x100 + len(code)
