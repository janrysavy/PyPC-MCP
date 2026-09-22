# TIMER

from typing import override, List, Tuple
import device
import i8237
import random


_mode_names = (
    "interrupt on terminal count", "hardware retriggerable one-shot",
    "rate generator", "square wave generator",
    "software triggered strobe", "hardware triggered strobe",
    "rate generator (alias)", "square wave generator (alias)",
)


class i8253(device.Device):
    class Timer:
        counter_cur: int = 0
        counter_prv: int = 0
        counter_ini: int = 0
        latched_count: int | None = None
        mode: int = 0
        latch_type: int = 0
        latch_n: int = 0
        latch_n_cur: int = 0
        is_running: int = 0
        is_pending: int = 0
        is_bcd: int = 0

    def __init__(self):
        self._timers = [ None ] * 3
        self._irq_nr = 0
        self._i8237 = None
        self._clock = 0

        for i in range(3):
            self._timers[i] = i8253.Timer()

    @override
    def GetStat(self) -> List[str]:
        out_ = []
        for i in range(3):
            t = self._timers[i]
            out_.append(f'Timer {i}: counter cur/prv/ini {t.counter_cur}/{t.counter_prv}/{t.counter_ini}, mode {t.mode} ({_mode_names[t.mode]}) running {t.is_running} pending {t.is_pending}, BCD: {t.is_bcd}')
        return out_

    @override
    def GetIRQNumber(self):
        return self._irq_nr

    @override
    def GetName(self):
        return 'i8253'

    @override
    def RegisterDevice(self, mappings: dict):
        mappings[0x0040] = self
        mappings[0x0041] = self
        mappings[0x0042] = self
        mappings[0x0043] = self

    @override
    def IO_Read(self, port: int) -> int:
        if port == 0x0040:
            return self.GetCounter(0)

        if port == 0x0041:
            return self.GetCounter(1)

        if port == 0x0042:
            return self.GetCounter(2)

        return 0xaa

    @override
    def IO_Write(self, port: int, value: int) -> bool:
        if port == 0x0040:
            self.LatchCounter(0, value)
        elif port == 0x0041:
            self.LatchCounter(1, value)
        elif port == 0x0042:
            self.LatchCounter(2, value)
        elif port == 0x0043:
            self.Command(value)
        else:
            print(f'error {port:04x}')

        return self._timers[0].is_pending or self._timers[1].is_pending or self._timers[2].is_pending

    @override
    def GetAddressList(self) -> list[Tuple[int, int]]:
        return [ ]

    @override
    def WriteByte(self, offset: int, value: int):
        pass

    @override
    def ReadByte(self, offset: int) -> int:
        return 0xee

    @override
    def SetDma(self, dma_instance: i8237.i8237):
        self._i8237 = dma_instance

    def LatchCounter(self, nr: int, v: int):
        if self._timers[nr].latch_n_cur > 0:
            if self._timers[nr].latch_type == 1:
                self._timers[nr].counter_ini &= 0xff00
                self._timers[nr].counter_ini |= v
            elif self._timers[nr].latch_type == 2:
                self._timers[nr].counter_ini &= 0x00ff
                self._timers[nr].counter_ini |= v << 8
            elif self._timers[nr].latch_type == 3:
                if self._timers[nr].latch_n_cur == 2:
                    self._timers[nr].counter_ini &= 0xff00
                    self._timers[nr].counter_ini |= v
                else:
                    self._timers[nr].counter_ini &= 0x00ff
                    self._timers[nr].counter_ini |= v << 8

            self._timers[nr].latch_n_cur -= 1
            self._timers[nr].latch_n_cur &= 0xffff

            if self._timers[nr].latch_n_cur == 0:
                self._timers[nr].latch_n_cur = self._timers[nr].latch_n  # restart setup
                self._timers[nr].counter_cur = self._timers[nr].counter_ini
                self._timers[nr].is_running = True
                self._timers[nr].is_pending = False

    # TODO WHY?!?!
    def AddNoiseToLSB(self, nr: int) -> int:
        current_prv = self._timers[nr].counter_prv

        self._timers[nr].counter_prv = self._timers[nr].counter_cur

        if abs(self._timers[nr].counter_cur - current_prv) >= 2:
            return (self._timers[nr].counter_cur ^ 1 if random.choice([True, False]) else self._timers[nr].counter_cur) & 0xff

        return self._timers[nr].counter_cur & 0xff

    def GetCounter(self, nr: int) -> int:
        timer = self._timers[nr]
        snapshot = timer.latched_count
        count = timer.counter_cur if snapshot is None else snapshot
        rc = 0

        low_byte = timer.latch_type == 1 or (
            timer.latch_type == 3 and timer.latch_n_cur == 2)
        high_byte = timer.latch_type == 2 or (
            timer.latch_type == 3 and timer.latch_n_cur != 2)
        if low_byte:
            rc = self.AddNoiseToLSB(nr) if snapshot is None else count & 0xff
        elif high_byte:
            rc = (count >> 8) & 0xff

        # Retain the 8253's existing shared read/write byte phase.
        timer.latch_n_cur = (timer.latch_n_cur - 1) & 0xffff
        if timer.latch_n_cur == 0:
            timer.latch_n_cur = timer.latch_n
            timer.latched_count = None

        return rc

    def Command(self, v: int):
        nr    = v >> 6
        # SC=11 is reserved on the 8253. Ignore unsupported commands,
        # including 8254 read-back probes, rather than indexing counter 3.
        if nr == 3:
            return
        latch = (v >> 4) & 3
        mode  = (v >> 1) & 7
        type  = v & 1

        if latch == 0:
            # A pending snapshot is held until its entire programmed read.
            # RL=00 does not change mode, BCD, access width, or byte phase.
            if nr < 3:
                timer = self._timers[nr]
                if timer.latched_count is None and timer.latch_type != 0:
                    timer.latched_count = timer.counter_cur & 0xffff
            return

        if latch != 0:
            self._timers[nr].latched_count = None
            self._timers[nr].mode = mode
            self._timers[nr].latch_type = latch
            self._timers[nr].is_running = False
            self._timers[nr].is_bcd = type == 1

            self._timers[nr].counter_ini = 0

            if self._timers[nr].latch_type == 1 or self._timers[nr].latch_type == 2:
                self._timers[nr].latch_n = 1
            elif self._timers[nr].latch_type == 3:
                self._timers[nr].latch_n = 2

            self._timers[nr].latch_n_cur = self._timers[nr].latch_n

    @override
    def Ticks(self) -> bool:
        return True

    @override
    def Tick(self, ticks: int, ignored):
        self._clock += ticks
        if self._clock < 4:
            return False

        interrupt = False

        n_to_subtract = self._clock // 4

        for i, timer in enumerate(self._timers):
            if timer.is_running == False:
                continue

            counter_ini = timer.counter_ini
            divider = 0x10000 if counter_ini == 0 else counter_ini
            periodic = timer.mode in (2, 3, 6, 7) and not timer.is_bcd
            if periodic:
                # Binary periodic modes produce one event per divisor, not
                # only after another full divisor beyond zero. Preserve every
                # elapsed period when a CPU tick spans multiple PIT events.
                remaining = (timer.counter_cur or divider) - n_to_subtract
                if remaining > 0:
                    timer.counter_cur = remaining
                    continue
                n_interrupts = 1 + (-remaining // divider)
                timer.counter_cur = (divider - (-remaining % divider)) & 0xffff
            else:
                # Other modes and BCD retain their existing approximation.
                timer.counter_cur -= n_to_subtract
                n_interrupts = -timer.counter_cur // divider

            if n_interrupts > 0:
                # timer 1 is RAM refresh counter
                if i == 1:
                    self._i8237.TickChannel0(n_interrupts)

                if not periodic:
                    if timer.mode != 1:
                        timer.counter_cur = counter_ini - (-timer.counter_cur % divider)
                    else:
                        timer.counter_cur &= 0xffff

                if i == 0:
                    timer.is_pending = True
                    interrupt = True

        self._clock -= n_to_subtract * 4

        if interrupt:
            self._pic.RequestInterruptPIC(self._irq_nr)  # Timers are on IRQ0

        return interrupt
