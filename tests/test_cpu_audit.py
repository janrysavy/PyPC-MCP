"""Synthetic 8088 regression fixtures; no guest application or ROM is needed.

These check architectural values and debugger memory effects, not timing or
undefined flags. Both normal and boundary operands are intentional fixtures.
"""
import unittest

import bus
import i8088


class CPUAuditTests(unittest.TestCase):
    def machine(self, code, *, ip=0x100, sp=0x9000):
        memory = bus.Bus(1 << 20, [], [])
        cpu = i8088.i8088(memory, [], False)
        state = cpu.GetState()
        for name, value in dict(CS=0x1000, DS=0x2000, ES=0x3000,
                                SS=0x2000, IP=ip, SP=sp, Flags=2).items():
            getattr(state, 'Set' + name)(value)
        for offset, byte in enumerate(code):
            cpu.WriteMemByte(0x1000, (ip + offset) & 0xffff, byte)
        return cpu, state

    def test_group_inc_wrap_sets_zero(self):
        for code in (b'\xff\xc0', b'\xff\x06\x00\x40'):
            with self.subTest(code=code.hex()):
                cpu, state = self.machine(code)
                state.SetAX(0xffff)
                state.SetFlagC(True)
                cpu.WriteMemWord(0x2000, 0x4000, 0xffff)
                cpu.Tick()
                value = state.GetAX() if code[1] == 0xc0 else cpu.ReadMemWord(0x2000, 0x4000)
                self.assertEqual(value, 0)
                self.assertTrue(state.GetFlagZ())
                self.assertTrue(state.GetFlagC())

    def test_group_inc_dec_sp(self):
        for modrm, delta in ((0xc4, 1), (0xcc, -1)):
            for before in (0, 0x7fff, 0x8000, 0x9000, 0xffff):
                with self.subTest(modrm=hex(modrm), before=hex(before)):
                    cpu, state = self.machine(bytes((0xff, modrm)), sp=before)
                    cpu.Tick()
                    self.assertEqual(state.GetSP(), (before + delta) & 0xffff)
                    self.assertEqual(state.GetFlagZ(), ((before + delta) & 0xffff) == 0)

    def test_push_sp_encodings_and_wrap(self):
        for code in (b'\x54', b'\xff\xf4'):
            for before in (0, 1, 2, 0x9000, 0xffff):
                with self.subTest(code=code.hex(), before=hex(before)):
                    cpu, state = self.machine(code, sp=before)
                    cpu.Tick()
                    expected = (before - 2) & 0xffff
                    self.assertEqual(state.GetSP(), expected)
                    self.assertEqual(cpu.ReadMemWord(0x2000, expected), expected)

    def test_near_call_does_not_rewrite_aliased_operand(self):
        cpu, state = self.machine(bytes.fromhex('ff 16 fe 8f'))
        cpu.WriteMemWord(0x2000, 0x8ffe, 0x2222)
        cpu.Tick()
        self.assertEqual(state.GetIP(), 0x2222)
        self.assertEqual(state.GetSP(), 0x8ffe)
        self.assertEqual(cpu.ReadMemWord(0x2000, 0x8ffe), 0x104)

    def test_far_call_reads_pointer_before_stack_writes(self):
        cpu, state = self.machine(bytes.fromhex('ff 1e fc 8f'))
        cpu.WriteMemWord(0x2000, 0x8ffc, 0x2222)
        cpu.WriteMemWord(0x2000, 0x8ffe, 0x3333)
        cpu.Tick()
        self.assertEqual((state.GetCS(), state.GetIP()), (0x3333, 0x2222))
        self.assertEqual(state.GetSP(), 0x8ffc)
        self.assertEqual(cpu.ReadMemWord(0x2000, 0x8ffc), 0x104)
        self.assertEqual(cpu.ReadMemWord(0x2000, 0x8ffe), 0x1000)

    def test_read_only_group_operands_do_not_emit_writes(self):
        for modrm in (0x16, 0x1e, 0x26, 0x2e, 0x36):
            with self.subTest(modrm=hex(modrm)):
                cpu, state = self.machine(bytes((0xff, modrm, 0, 0x40)))
                cpu.WriteMemWord(0x2000, 0x4000, 0x2222)
                cpu.WriteMemWord(0x2000, 0x4002, 0x3333)
                accesses = []
                cpu.SetMemoryTraceHook(lambda *event: accesses.append(event))
                cpu.Tick()
                writes = [event for event in accesses if event[0] == 'memory_write'
                          and 0x24000 <= event[1] < 0x24004]
                self.assertEqual(writes, [])

    def test_mov_immediate_is_write_only(self):
        for code in (bytes.fromhex('c6 06 34 12 42'), bytes.fromhex('c7 06 34 12 cd ab')):
            with self.subTest(code=code.hex()):
                cpu, state = self.machine(code)
                accesses = []
                cpu.SetMemoryTraceHook(lambda *event: accesses.append(event))
                cpu.Tick()
                self.assertFalse(any(event[0] == 'memory_read' for event in accesses))
                self.assertEqual(len(accesses), 1 if code[0] == 0xc6 else 2)
                self.assertEqual(state.GetIP(), 0x100 + len(code))

    def test_lea_does_not_read_memory(self):
        cpu, state = self.machine(bytes.fromhex('8d 06 34 12'))
        accesses = []
        cpu.SetMemoryTraceHook(lambda *event: accesses.append(event))
        cpu.Tick()
        self.assertEqual(state.GetAX(), 0x1234)
        self.assertEqual(accesses, [])

    def test_rep_comparison_prefix_order(self):
        for opcode in (0xa6, 0xa7, 0xae, 0xaf):
            for rep in (0xf2, 0xf3):
                for prefix in ((rep, 0x26), (0x26, rep)):
                    with self.subTest(opcode=hex(opcode), prefix=prefix):
                        cpu, state = self.machine(bytes((*prefix, opcode)))
                        state.SetCX(3)
                        state.SetSI(0x4000)
                        state.SetDI(0x5000)
                        # REPNE stops on equality; REPE stops on inequality.
                        state.SetAX(0x4141)
                        cpu.WriteMemWord(0x3000, 0x4000, 0x4141)
                        cpu.WriteMemWord(0x3000, 0x5000, 0x4141 if rep == 0xf2 else 0x4242)
                        cpu.Tick()
                        self.assertEqual(state.GetCX(), 2)
                        self.assertEqual(state.GetIP(), 0x103)
                        self.assertFalse(cpu.IsProcessingRep())

    def test_jcxz_wraps_ip(self):
        for start, disp in ((0, 0x80), (0xfffc, 0x10)):
            with self.subTest(start=hex(start), displacement=hex(disp)):
                cpu, state = self.machine(bytes((0xe3, disp)), ip=start)
                state.SetCX(0)
                cpu.Tick()
                signed = disp if disp < 128 else disp - 256
                self.assertEqual(state.GetIP(), (start + 2 + signed) & 0xffff)

    def test_software_interrupt_clears_trap_flag(self):
        cpu, state = self.machine(bytes.fromhex('cd 21'))
        state.SetFlags(0x302)
        cpu.WriteMemWord(0, 0x21 * 4, 0x2222)
        cpu.WriteMemWord(0, 0x21 * 4 + 2, 0x3333)
        cpu.WriteMemWord(0, 4, 0x4444)
        cpu.WriteMemWord(0, 6, 0x5555)
        cpu.Tick()
        self.assertEqual((state.GetCS(), state.GetIP()), (0x3333, 0x2222))
        self.assertEqual(state.GetSP(), 0x8ffa)
        self.assertFalse(state.GetFlagT())
        self.assertFalse(state.GetFlagI())
        self.assertEqual(cpu.ReadMemWord(0x2000, 0x8ffe), 0x302)


if __name__ == '__main__':
    unittest.main()
