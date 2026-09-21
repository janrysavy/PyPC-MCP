"""Exhaustive flag-bit and parity checks plus instruction-level differentials."""
import unittest

from benchmarks.legacy_flags import LegacyState
from state8088 import State8088


class FlagFastPathTests(unittest.TestCase):
    def test_named_getters_cover_every_flags_word(self):
        state = State8088()
        fields = [(getattr(state, 'GetFlag' + name), bit)
                  for name, bit in dict(C=0, P=2, A=4, Z=6, S=7, T=8, I=9, D=10, O=11).items()]
        for flags in range(65536):
            state.SetFlags(flags)
            for getter, bit in fields:
                result = getter()
                self.assertIs(type(result), bool)
                self.assertEqual(result, bool(flags & (1 << bit)))

    def test_named_setters_preserve_every_other_bit(self):
        state = State8088()
        fields = [(getattr(state, 'SetFlag' + name), bit)
                  for name, bit in dict(C=0, A=4, Z=6, S=7, T=8, I=9, D=10, O=11).items()]
        for flags in range(65536):
            for setter, bit in fields:
                for value in (False, True):
                    state.SetFlags(flags)
                    setter(value)
                    self.assertEqual(state.GetFlags(),
                                     (flags & ~(1 << bit)) | (int(value) << bit))

    def test_parity_uses_only_low_byte_for_all_words(self):
        state = State8088()
        for value in range(65536):
            for flags in (0, 0xffff):
                state.SetFlags(flags)
                state.SetFlagP(value)
                parity_bit = 4 if bin(value & 255).count('1') % 2 == 0 else 0
                self.assertEqual(state.GetFlags(), (flags & ~4) | parity_bit)

    def test_zsp_keeps_existing_zero_and_low_byte_sign_semantics(self):
        old, new = LegacyState(), State8088()
        for value in range(65536):
            for flags in (0, 0xffff, 0x891):
                old.SetFlags(flags)
                new.SetFlags(flags)
                old.SetZSPFlags(value)
                new.SetZSPFlags(value)
                self.assertEqual(new.GetFlags(), old.GetFlags())
        for value in (-1, 65536):
            with self.assertRaises(AssertionError):
                new.SetZSPFlags(value)

    def test_instruction_streams_match_reference_each_tick(self):
        from benchmarks.benchmark_flags import machine
        for workload in ('arithmetic', 'logic_memory', 'branch'):
            with self.subTest(workload=workload):
                old_cpu, old_snapshot = machine('legacy', workload)
                new_cpu, new_snapshot = machine('fast', workload)
                for _ in range(2000):
                    old_cpu.Tick()
                    new_cpu.Tick()
                    self.assertEqual(vars(old_cpu.GetState()), vars(new_cpu.GetState()))
                self.assertEqual(old_snapshot(), new_snapshot())


if __name__ == '__main__':
    unittest.main()
