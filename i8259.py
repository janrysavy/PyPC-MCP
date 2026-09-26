# programmable interrupt controller (PIC)
from typing import override, List, Tuple
import device

class i8259(device.Device):
    def __init__(self):
        self._int_offset = 8  # TODO updaten bij ICW (OCW?) en dan XT::Tick() de juiste vector
        self._irr = 0  # which irqs are requested
        self._isr = 0  # ...and which are in service
        self._imr = 255  # all irqs masked (disabled)
        self._auto_eoi = False
        self._irq_request_level = 7  # default value? TODO
        self._read_irr = True
        self._has_slave = False
        self._int_in_service = -1  # used by EOI

        self._icw1 = 0
        self._in_init = False
        self._ii_icw2 = False
        self._icw2 = 0
        self._ii_icw3 = False
        self._icw3 = 0
        self._ii_icw4 = False
        self._icw4 = 0
        self._ii_icw4_req = False
        self._ocw2 = 0
        self._ocw3 = 0
        self._trace_hook = None
        self._trace_address = None
        self._trace_clock = 0

    @override
    def GetIRQNumber(self) -> int:
        return -1

    @override
    def GetName(self) -> str:
        return 'i8259'

    @override
    def GetState(self) -> List[str]:
        out = []
        out.append(f'IRR: {self._irr:X2}, ISR: {self._isr:X2}, IMR: {self._imr:X2}, int in serice: {self._int_in_service}')
        out.append(f'auto eoi: {self._auto_eoi}, request level: {self._irq_request_level}')
        out.append(f'read irr: {self._read_irr}')
        out.append(f'in init: {self._in_init}, icw1: {self._icw1:X2}, icw2: {self._ii_icw2}/{self._icw2:X2}, ic3w: {self._ii_icw3}/{self._icw3:X2}, icw4: {self._ii_icw4}/{self._icw4:X2}, icw4 req: {self._ii_icw4_req}')
        out.append(f'ocw2: {self._ocw2:X2}, ocw3: {self._ocw3:X2}')
        return out

    @override
    def RegisterDevice(self, mappings: dict):
        mappings[0x0020] = self
        mappings[0x0021] = self

    def _highest_in_service(self) -> int:
        # Fixed priority: IR0 is highest, IR7 lowest.
        for irq in range(8):
            if self._isr & (1 << irq):
                return irq
        return -1

    def GetPendingInterrupt(self) -> int:
        for irq in range(8):
            mask = 1 << irq
            # An ISR bit blocks its own level and every lower priority level,
            # not higher priority requests (fully nested mode).
            if self._isr & mask:
                break
            if self._irr & mask and not (self._imr & mask):
                return irq
        return 255

    def SetTraceHook(self, hook):
        self._trace_hook = hook

    def SetTraceContext(self, address, clock):
        self._trace_address = address
        self._trace_clock = clock

    def _trace_event(self, kind, interrupt_nr, vector=None):
        if self._trace_hook is None:
            return
        event = {
            'kind': kind, 'irq': interrupt_nr,
            'address': (None if self._trace_address is None
                        else dict(self._trace_address)),
            'emulated_time': self._trace_clock,
        }
        if vector is not None:
            event['vector'] = vector
        self._trace_hook(event)

    def _clear_requests(self, mask):
        for interrupt_nr in range(8):
            bit = 1 << interrupt_nr
            if (mask & bit) and (self._irr & bit):
                self._irr &= ~bit
                self._trace_event('irq_lower', interrupt_nr)

    def GetInterruptLevel(self) -> int:
        return self._irq_request_level

    def RequestInterruptPIC(self, interrupt_nr: int):
        mask = 1 << interrupt_nr
        already_requested = (self._irr & mask) != 0
        self._irr |= mask
        if not already_requested:
            self._trace_event('irq_raise', interrupt_nr)

    def SetIRQBeingServiced(self, interrupt_nr: int):
        self._trace_event(
            'irq_dispatch', interrupt_nr, self._int_offset + interrupt_nr)
        # INTA consumes the request. A new edge during service is a distinct
        # pending request and must survive the eventual EOI.
        self._clear_requests(1 << interrupt_nr)
        if not self._auto_eoi:
            self._isr |= 1 << interrupt_nr
        self._int_in_service = self._highest_in_service()

    @override
    def IO_Read(self, addr: int) -> int:
        rc = 0

        if addr == 0x0020:
            if self._read_irr:
                rc = self._irr
            else:
                rc = self._isr
        elif addr == 0x0021:
            rc = self._imr

        return rc

    @override
    def IO_Write(self, addr: int, value: int) -> bool:
        if addr == 0x0020:
            if value & 0x10:  # ICW1 starts a new initialization sequence.
                self._in_init = True
                self._has_slave = (value & 2) == 0
                self._icw1 = value
                self._ii_icw2 = False
                self._ii_icw3 = False
                self._ii_icw4 = False
                self._ii_icw4_req = (value & 1) == 1
                # Without ICW4 all its functions are cleared, including AEOI.
                if not self._ii_icw4_req:
                    self._icw4 = 0
                    self._auto_eoi = False

                self._read_irr = True  # Initialization selects IRR for command-port reads.
                self._imr = 0  # TODO 255?
                self._isr = 0
                self._clear_requests(0xff)

                self._int_in_service  = -1

            else:  # OCW 2/3
                if (value & 8) == 8:  # OCW3
                    # RR gates RIS: an OCW3 without RR must preserve the selector.
                    if value & 2:
                        self._read_irr = (value & 1) == 0
                    self._ocw3 = value
                else:  # OCW2
                    self._irq_request_level = value & 7
                    self._ocw2 = value

                    # EOI clears an in-service bit, never a pending request.
                    if value & 0x20:
                        irq = (value & 7) if value & 0x40 else self._highest_in_service()
                        if irq != -1:
                            self._isr &= ~(1 << irq)
                        self._int_in_service = self._highest_in_service()

        elif addr == 0x0021:
            if self._in_init:
                if self._ii_icw2 == False:
                    self._icw2 = value
                    self._ii_icw2 = True
                    self._int_offset = value
                    # Single mode omits ICW3; IC4=0 also omits ICW4.
                    if not self._has_slave and not self._ii_icw4_req:
                        self._in_init = False

                elif self._ii_icw3 == False and self._has_slave:
                    self._ii_icw3 = True
                    self._icw3 = value

                    # ignore value: slave-devices are not supported in this emulator

                    if self._ii_icw4_req == False:
                        self._in_init = False

                elif self._ii_icw4 == False:
                    self._ii_icw4 = True
                    self._icw4 = value
                    self._in_init = False
                    new_auto_eoi = (value & 2) == 2
                    if new_auto_eoi != self._auto_eoi:
                        self._auto_eoi = new_auto_eoi
            else:
                self._imr = value

        # when reconfiguring the PIC8259, force an interrupt recheck
        return True

    def GetInterruptOffset(self) -> int:
        return self._int_offset

    def GetInterruptMask(self) -> int:
        return self._imr

    @override
    def GetAddressList(self) -> list[Tuple[int, int]]:
        return []

    @override
    def WriteByte(self, offset: int, value: int):
        pass

    @override
    def ReadByte(self, offset: int) -> int:
        return 0xee

    @override
    def Ticks(self) -> bool:
        return False
