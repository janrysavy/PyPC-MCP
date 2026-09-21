"""8088 IDIV regression tests: results/traps, not undefined flags or timing.

Unlike later x86 CPUs, the 8088 traps on quotients -128 and -32768.
This boundary is corroborated by the hardware-derived SingleStepTests/8088
suite. These tests contain only synthetic instruction bytes and operands.
"""
import unittest
import bus
import i8088


class SignedDivisionTests(unittest.TestCase):
    def execute(self, bits, dividend, divisor, prefix=b''):
        memory = bus.Bus(1 << 20, [], [])
        cpu = i8088.i8088(memory, [], False)
        state = cpu.GetState()
        state.SetCS(0x1000)
        state.SetIP(0x0100)
        state.SetSS(0x2000)
        state.SetSP(0x9000)
        state.SetFlags(0x0202)
        state.SetAX(dividend & 0xffff)
        state.SetDX((dividend >> 16) & 0xffff if bits == 16 else 0x5a5a)
        state.SetBX(divisor & 0xffff)
        cpu.WriteMemWord(0, 0, 0x2222)
        cpu.WriteMemWord(0, 2, 0x3333)
        code = prefix + bytes((0xf7 if bits == 16 else 0xf6, 0xfb))
        for offset, value in enumerate(code):
            cpu.WriteMemByte(0x1000, 0x0100 + offset, value)
        cpu.Tick()
        return memory, state, len(code)

    def assert_result(self, bits, dividend, divisor, quotient, remainder, prefix=b''):
        _, state, length = self.execute(bits, dividend, divisor, prefix)
        mask = (1 << bits) - 1
        self.assertEqual((state.GetCS(), state.GetIP()), (0x1000, 0x100 + length))
        self.assertEqual(state.GetSP(), 0x9000)
        self.assertEqual(state.GetBX(), divisor & 0xffff)
        if bits == 8:
            self.assertEqual((state.GetAL(), state.GetAH()),
                             (quotient & mask, remainder & mask))
            self.assertEqual(state.GetDX(), 0x5a5a)
        else:
            self.assertEqual((state.GetAX(), state.GetDX()),
                             (quotient & mask, remainder & mask))

    def test_signed_results(self):
        cases = ((7, 3, 2, 1), (-7, 3, -2, -1),
                 (7, -3, -2, 1), (-7, -3, 2, -1),
                 (-6, 3, -2, 0), (-1, 3, 0, -1), (1, -3, 0, 1))
        for bits in (8, 16):
            for case in cases:
                with self.subTest(bits=bits, case=case):
                    self.assert_result(bits, *case)

    def test_largest_valid_quotients(self):
        for bits in (8, 16):
            limit = (1 << (bits - 1)) - 1
            for sign in (-1, 1):
                with self.subTest(bits=bits, sign=sign):
                    self.assert_result(bits, sign * limit, 1, sign * limit, 0)
                    self.assert_result(bits, sign * limit * limit, limit,
                                       sign * limit, 0)

    def test_rep_prefix_negates_quotient_only(self):
        for bits in (8, 16):
            for prefix in (b'\xf2', b'\xf3'):
                for dividend, divisor, quotient, remainder in (
                        (7, 3, -2, 1), (-7, 3, 2, -1),
                        (7, -3, 2, 1), (-7, -3, -2, -1)):
                    with self.subTest(bits=bits, prefix=prefix, dividend=dividend,
                                      divisor=divisor):
                        self.assert_result(bits, dividend, divisor, quotient,
                                           remainder, prefix)

    def test_divide_errors_preserve_dividend_and_push_next_ip(self):
        for bits in (8, 16):
            limit = 1 << (bits - 1)
            cases = ((8, 0), (limit, 1), (-limit, 1),
                     (limit, -1), (-limit, -1),
                     (-(1 << (bits * 2 - 1)), -1))
            for prefix in (b'', b'\xf2', b'\xf3'):
                for dividend, divisor in cases:
                    with self.subTest(bits=bits, dividend=dividend,
                                      divisor=divisor, prefix=prefix):
                        memory, state, length = self.execute(bits, dividend, divisor, prefix)
                        self.assertEqual((state.GetCS(), state.GetIP()), (0x3333, 0x2222))
                        self.assertEqual(state.GetSP(), 0x8ffa)
                        self.assertEqual(state.GetAX(), dividend & 0xffff)
                        expected_dx = (dividend >> 16) & 0xffff if bits == 16 else 0x5a5a
                        self.assertEqual(state.GetDX(), expected_dx)
                        low = memory.ReadByte(0x28ffa)[0]
                        high = memory.ReadByte(0x28ffb)[0]
                        self.assertEqual(low | high << 8, 0x100 + length)
                        self.assertEqual(state.GetFlags() & 0x0200, 0)


if __name__ == '__main__':
    unittest.main()
