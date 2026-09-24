from typing import override, List, Tuple
import device
import queue
import threading
from functools import wraps


def _locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._state_lock:
            return method(self, *args, **kwargs)
    return call

class Keyboard(device.Device):
    def __init__(self):
        self._irq_nr = 1
        self._kb_reset_irq_delay = 4770  # cycles for 1ms @ 4.77 MHz
        # The XT keyboard raises IRQ1 as soon as a scan code is available.
        # Keep only a small emulated hardware delay; 20 ms made menu input
        # visibly sluggish and queued startup keys behind user input.
        self._kb_key_irq = 4770  # approximately 1 ms at 4.77 MHz
        self._clock_low = False
        self._0x61_bits = 0
        self._last_scan_code = 0
        self._keyboard_buffer = queue.Queue()
        self._pressed_scancodes = set()
        super().__init__()
        self._state_lock = threading.RLock()
        # Derived from _next_interrupt.  The emulator checks the keyboard after
        # every CPU instruction, while host input is rare.  Reading this flag
        # without the lock may defer a newly queued event by one instruction,
        # but cannot lose it: producers set it while holding _state_lock before
        # they publish the completed operation.
        self._interrupt_pending = False

    @override
    def GetIRQNumber(self) -> int:
        return self._irq_nr

    @_locked
    def PushKeyboardScancode(self, scan_code: int):
        if scan_code & 0x80:
            self._pressed_scancodes.discard(scan_code & 0x7f)
        else:
            self._pressed_scancodes.add(scan_code)
        self._keyboard_buffer.put(scan_code)

        self.ScheduleInterrupt(self._kb_key_irq)

    @_locked
    def GetPressedScancodes(self):
        return sorted(self._pressed_scancodes)

    @override
    def GetName(self) -> str:
        return "Keyboard"

    @override
    def RegisterDevice(self, mappings: dict):
        # see PPI
        pass

    @override
    @_locked
    def IO_Write(self, port: int, value: int) -> bool:
        if port == 0x0061:
            self._0x61_bits = value

            if (value & 0x40) == 0x00:
                self._clock_low = True
            elif self._clock_low:
                self._clock_low = False

                self._keyboard_buffer = queue.Queue()
                self._keyboard_buffer.put(0xaa)  # power on reset reply

                # Preserve the existing approximate reset delay.
                self.ScheduleInterrupt(self._kb_reset_irq_delay)

            if (value & 0x80) != 0:
                self._last_scan_code = 0

        return False

    @override
    @_locked
    def IO_Read(self, port: int) -> int:
        if port == 0x60:
            rc = self._last_scan_code

            if self._keyboard_buffer.empty() == False:
                rc = self._keyboard_buffer.get()
                self._last_scan_code = rc

            return rc

        elif port == 0x61:
            return self._0x61_bits

        elif port == 0x64:
            return 0x10

        return 0x00

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
    def Ticks(self) -> bool:
        return True

    @override
    def Tick(self, cycles: int, clock: int) -> bool:
        if not self._interrupt_pending:
            return False
        with self._state_lock:
            if ((self._0x61_bits & 0x80) == 0
                    and super().CheckScheduledInterrupt(cycles)):
                self._pic.RequestInterruptPIC(self._irq_nr)
            self._interrupt_pending = bool(self._next_interrupt)

        return False

    @_locked
    def ScheduleInterrupt(self, cycles_delay):
        result = super().ScheduleInterrupt(cycles_delay)
        self._interrupt_pending = True
        return result

    @_locked
    def CheckScheduledInterrupt(self, cycles):
        result = super().CheckScheduledInterrupt(cycles)
        self._interrupt_pending = bool(self._next_interrupt)
        return result
