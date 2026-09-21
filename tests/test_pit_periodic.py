"""Periodic event accounting within the emulator's immediate-load PIT model.

Not an OUT/GATE waveform test. Mode 3's readable half-cycle count and BCD
remain outside this model; the recurring event period must nevertheless be N.
"""
import unittest
from i8253 import i8253


class Pic:
    def __init__(self):
        self.requests = []

    def RequestInterruptPIC(self, irq):
        self.requests.append(irq)


class Dma:
    def __init__(self):
        self.refreshes = 0

    def TickChannel0(self, count):
        self.refreshes += count


def make_timer(channel, mode, divisor):
    pit = i8253()
    pic, dma = Pic(), Dma()
    pit.SetPic(pic)
    pit.SetDma(dma)
    pit.IO_Write(0x43, (channel << 6) | 0x30 | (mode << 1))
    pit.IO_Write(0x40 + channel, divisor & 255)
    pit.IO_Write(0x40 + channel, divisor >> 8)
    return pit, pic, dma


class PeriodicTimerTests(unittest.TestCase):
    def test_first_and_subsequent_irq_periods(self):
        for mode in (2, 3, 6, 7):
            for divisor in (3, 18, 1193, 65535, 0):
                with self.subTest(mode=mode, divisor=divisor):
                    pit, pic, _ = make_timer(0, mode, divisor)
                    period = divisor or 65536
                    for event in range(1, 5):
                        self.assertFalse(pit.Tick((period - 1) * 4, 0))
                        self.assertEqual(len(pic.requests), event - 1)
                        self.assertTrue(pit.Tick(4, 0))
                        self.assertEqual(pic.requests, [0] * event)
                        self.assertEqual(pit._timers[0].counter_cur, divisor)

    def test_refresh_delivers_every_elapsed_period_in_batch(self):
        for divisor in (3, 18, 1193, 65535, 0):
            with self.subTest(divisor=divisor):
                pit, pic, dma = make_timer(1, 2, divisor)
                period = divisor or 65536
                self.assertFalse(pit.Tick((7 * period + 2) * 4, 0))
                self.assertEqual(dma.refreshes, 7)
                self.assertEqual(pit._timers[1].counter_cur, period - 2)
                self.assertEqual(pic.requests, [])
                pit.Tick((period - 2) * 4, 0)
                self.assertEqual(dma.refreshes, 8)
                self.assertEqual(pit._timers[1].counter_cur, divisor)

    def test_split_and_batched_refresh_match(self):
        for divisor in (3, 18, 0):
            for mode in (2, 3, 6, 7):
                with self.subTest(divisor=divisor, mode=mode):
                    a, ap, ad = make_timer(1, mode, divisor)
                    b, bp, bd = make_timer(1, mode, divisor)
                    # Include sub-PIT-tick chunks and several large overruns.
                    chunks = [1, 2, 3, 17, 4, 79, 65537, 7, 999999, 5]
                    for cycles in chunks:
                        a.Tick(cycles, 0)
                    b.Tick(sum(chunks), 0)
                    period = divisor or 65536
                    self.assertEqual(ad.refreshes, sum(chunks) // 4 // period)
                    self.assertEqual(ad.refreshes, bd.refreshes)
                    self.assertEqual(a._timers[1].counter_cur, b._timers[1].counter_cur)
                    self.assertEqual(a._clock, b._clock)
                    self.assertEqual(ap.requests, bp.requests)

    def test_fractional_cpu_clock_residue_is_preserved(self):
        pit, pic, _ = make_timer(0, 2, 3)
        for _ in range(11):
            self.assertFalse(pit.Tick(1, 0))
        self.assertEqual(pic.requests, [])
        self.assertEqual(pit._clock, 3)
        self.assertTrue(pit.Tick(1, 0))
        self.assertEqual(pic.requests, [0])
        self.assertEqual(pit._clock, 0)

    def test_zero_divisor_still_means_65536(self):
        pit, pic, _ = make_timer(0, 3, 0)
        self.assertEqual(pit._timers[0].counter_cur, 0)
        self.assertFalse(pit.Tick(65535 * 4, 0))
        self.assertEqual(pit._timers[0].counter_cur & 65535, 1)
        self.assertTrue(pit.Tick(4, 0))
        self.assertEqual(pic.requests, [0])

    def test_irq_is_coalesced_per_tick_but_remainder_is_not_lost(self):
        pit, pic, _ = make_timer(0, 3, 18)
        self.assertTrue(pit.Tick((5 * 18 + 7) * 4, 0))
        self.assertEqual(pic.requests, [0])
        self.assertEqual(pit._timers[0].counter_cur, 11)
        self.assertTrue(pit.Tick(11 * 4, 0))
        self.assertEqual(pic.requests, [0, 0])

    def test_channel_two_never_requests_irq_or_refresh(self):
        pit, pic, dma = make_timer(2, 3, 18)
        self.assertFalse(pit.Tick(18 * 4, 0))
        self.assertEqual(pit._timers[2].counter_cur, 18)
        self.assertEqual(pic.requests, [])
        self.assertEqual(dma.refreshes, 0)

    def test_stopped_channel_waits_for_complete_count(self):
        pit, pic, _ = make_timer(0, 2, 18)
        pit.IO_Write(0x43, 0x34)
        pit.IO_Write(0x40, 3)  # Only LSB; still stopped.
        before = pit._timers[0].counter_cur
        self.assertFalse(pit.Tick(10000, 0))
        self.assertEqual(pit._timers[0].counter_cur, before)
        self.assertEqual(pic.requests, [])
        pit.IO_Write(0x40, 0)
        self.assertTrue(pit.Tick(12, 0))

    def test_nonperiodic_and_bcd_paths_keep_legacy_accounting(self):
        # Compatibility guard, not a claim that the legacy modes are complete.
        for mode, bcd in ((0, False), (1, False), (4, False), (5, False),
                          (2, True), (3, True), (6, True), (7, True)):
            with self.subTest(mode=mode, bcd=bcd):
                pit, pic, _ = make_timer(0, mode, 18)
                pit._timers[0].is_bcd = bcd
                expected_cur, residue, requests = 18, 0, 0
                for cycles in (1, 3, 17, 71, 250, 800001):
                    residue += cycles
                    decrement = residue // 4
                    residue %= 4
                    expected_cur -= decrement
                    if -expected_cur // 18 > 0:
                        requests += 1
                        if mode != 1:
                            expected_cur = 18 - (-expected_cur % 18)
                        else:
                            expected_cur &= 65535
                    pit.Tick(cycles, 0)
                    self.assertEqual(pit._timers[0].counter_cur, expected_cur)
                    self.assertEqual(pit._clock, residue)
                    self.assertEqual(len(pic.requests), requests)

    def test_status_formats_every_mode_without_name_error(self):
        pit = i8253()
        for mode in range(8):
            with self.subTest(mode=mode):
                pit.Command(0x30 | (mode << 1))
                rows = pit.GetStat()
                self.assertEqual(len(rows), 3)
                self.assertIn(f'mode {mode} (', rows[0])
                self.assertTrue(rows[0].startswith('Timer 0:'))


if __name__ == '__main__':
    unittest.main()
