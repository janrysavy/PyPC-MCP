from typing import List, Tuple
import i8259
import i8237
import bus
import device

class IO:
    def __init__(self, b: bus.Bus, devices: List[device.Device], test_mode: bool):
        self._b = b
        self._io_map = dict()
        self._pic = i8259.i8259()
        self._i8237 = i8237.i8237(b)
        self._tick_devices = []
        self._tick_methods = []
        self._trace_hook = None

        for device in devices:
            device.SetDma(self._i8237)
            device.SetPic(self._pic)
            device.SetBus(self._b)

            if device.Ticks():
                self._tick_devices.append(device)
                self._tick_methods.append(device.Tick)

        devices.append(self._i8237)
        devices.append(self._pic);

        for device in devices:
            device.RegisterDevice(self._io_map);

        self._devices = devices;

        self._test_mode = test_mode;

    def GetPIC(self) -> i8259:
        return self._pic

    def SetTraceHook(self, hook):
        self._trace_hook = hook

    def In(self, addr: int, b16: bool) -> int:
        if self._test_mode:
            return 65535

        rc = 0xff if not b16 else 0xffff
        handled = False
        if addr in self._io_map:
            rc = self._io_map[addr].IO_Read(addr)
            handled = True

            if b16:
                next_port = (addr + 1) & 0xffff;
                if next_port in self._io_map:
                    temp = self._io_map[next_port].IO_Read(next_port)
                    rc |= temp << 8

        elif addr == 0x0210:  # verify expansion bus data
            rc = 0xa5
            handled = True

        #print(f'IO {addr:04x} not handled for IN')

        if self._trace_hook is not None:
            self._trace_hook('io_read', addr, rc, 2 if b16 else 1, handled)
        return rc

    def Tick(self, ticks: int, clock: int) -> bool:
        if len(self._tick_methods) == 3:
            self._tick_methods[0](ticks, clock)
            self._tick_methods[1](ticks, clock)
            self._tick_methods[2](ticks, clock)
        else:
            for tick in self._tick_methods:
                tick(ticks, clock)
        return False

    def Out(self, addr: int, value: int, b16: bool) -> bool:
        if self._test_mode:
            return False

        rc = False

        if addr in self._io_map:
            rc |= self._io_map[addr].IO_Write(addr, value & 255)

            if b16:
                next_port = (addr + 1) & 0xffff
                if next_port in self._io_map:
                    rc |= self._io_map[next_port].IO_Write(next_port, value >> 8)

            if self._trace_hook is not None:
                self._trace_hook('io_write', addr, value, 2 if b16 else 1, True)
            return rc

        #print(f'IO {addr:04x} not handled for OUT')

        if self._trace_hook is not None:
            self._trace_hook('io_write', addr, value, 2 if b16 else 1, False)
        return False
