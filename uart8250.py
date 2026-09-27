"""COM1 register interface for polled BIOS/DOS serial I/O.

The RPC endpoint is a flow-controlled virtual terminal, not a physical wire:
input waits outside the UART until RBR is consumed; output is immediate. Baud,
parity and stop-bit registers are readable but do not delay or corrupt bytes.
All calls run on the emulator thread, including RPC and snapshot operations.
"""
from collections import deque
import base64
import device

class UART8250(device.Device):
    base = 0x3f8
    capacity = 65536

    def __init__(self):
        super().__init__()
        self.dll, self.dlm, self.ier, self.lcr = 12, 0, 0, 0
        self.mcr, self.scratch, self.errors, self.delta = 0, 0, 0, 0
        self.rx = deque()
        self.tx = deque(maxlen=self.capacity)
        self.tx_next = 0
        self.thre_pending = True

    def GetName(self): return 'COM1 UART'
    def GetIRQNumber(self): return 4
    def GetAddressList(self): return []
    def ReadByte(self, offset): return 0xff
    def WriteByte(self, offset, value): pass
    def Ticks(self): return False
    def RegisterDevice(self, mappings):
        for port in range(self.base, self.base+8): mappings[port] = self

    def modem(self):
        if self.mcr & 16:
            return ((self.mcr & 2)<<3 | (self.mcr & 1)<<5 |
                    (self.mcr & 4)<<4 | (self.mcr & 8)<<4)
        return 0xb0  # virtual terminal asserts CTS, DSR, DCD

    def interrupt(self):
        if self.errors and self.ier & 4: return 6
        if self.rx and self.ier & 1: return 4
        if self.thre_pending and self.ier & 2: return 2
        if self.delta and self.ier & 8: return 0
        return 1

    def notify(self):
        if self.mcr & 8 and self.interrupt() != 1 and self._pic is not None:
            self._pic.RequestInterruptPIC(4)

    def IO_Read(self, port):
        reg=port-self.base
        if reg==0:
            if self.lcr & 128: return self.dll
            value=self.rx.popleft() if self.rx else 0
            self.notify()
            return value
        if reg==1: return self.dlm if self.lcr & 128 else self.ier
        if reg==2:
            value=self.interrupt()
            if value==2: self.thre_pending=False
            return value
        if reg==3: return self.lcr
        if reg==4: return self.mcr
        if reg==5:
            value=0x60 | bool(self.rx) | self.errors
            self.errors=0
            return value
        if reg==6:
            value=self.modem() | self.delta
            self.delta=0
            return value
        if reg==7: return self.scratch
        return 0xff

    def IO_Write(self, port, value):
        reg=port-self.base
        if reg==0:
            if self.lcr & 128: self.dll=value
            elif self.mcr & 16:
                if self.rx: self.errors |= 2
                else: self.rx.append(value)
                self.thre_pending=True
            else:
                self.tx.append(value)
                self.tx_next+=1
                self.thre_pending=True
        elif reg==1:
            if self.lcr & 128: self.dlm=value
            else:
                if value & 2 and not self.ier & 2: self.thre_pending=True
                self.ier=value & 15
        elif reg==3: self.lcr=value
        elif reg==4:
            old=self.modem(); self.mcr=value & 31; new=self.modem()
            self.delta |= ((old^new)>>4)&11
            if old & 64 and not new & 64: self.delta |= 4
        elif reg==7: self.scratch=value
        self.notify()
        return False

    def host_write(self, data):
        if type(data) is not bytes or not 1 <= len(data) <= 4096:
            raise ValueError('serial write requires 1..4096 bytes')
        if self.mcr & 16: raise ValueError('UART is in loopback mode')
        if len(self.rx)+len(data)>self.capacity: raise ValueError('serial input queue full')
        self.rx.extend(data); self.notify()
        return {'accepted':len(data), 'rx_pending':len(self.rx)}

    def host_read(self, offset, count):
        start=self.tx_next-len(self.tx)
        if type(offset) is not int or not start <= offset <= self.tx_next:
            raise ValueError(f'serial output offset unavailable; retained {start}..{self.tx_next}')
        if type(count) is not int or not 1 <= count <= 65536: raise ValueError('max_bytes must be 1..65536')
        data=bytes(self.tx)[offset-start:offset-start+count]
        return {'offset':offset, 'next_offset':offset+len(data),
                'data_base64':base64.b64encode(data).decode('ascii')}

    def status(self):
        return {'port':'COM1', 'base':self.base, 'irq':4, 'divisor':self.dll+256*self.dlm,
                'lcr':self.lcr, 'mcr':self.mcr, 'rx_pending':len(self.rx),
                'tx_start':self.tx_next-len(self.tx), 'tx_next':self.tx_next,
                'transport':'flow-controlled-rpc', 'wire_timing':False}

    def dump(self):
        return {k:(list(v) if isinstance(v,deque) else v) for k,v in vars(self).items() if not k.startswith('_')}

    @classmethod
    def load(cls, fields):
        uart=cls()
        if type(fields) is not dict or fields.keys()!=uart.dump().keys(): raise ValueError('invalid UART schema')
        for k,v in fields.items():
            if k in ('rx','tx'):
                if type(v) is not list or len(v)>cls.capacity or any(type(b) is not int or not 0<=b<=255 for b in v): raise ValueError('invalid UART buffer')
                v=deque(v,maxlen=cls.capacity if k=='tx' else None)
            elif k=='thre_pending':
                if type(v) is not bool: raise ValueError('invalid UART interrupt state')
            elif type(v) is not int or not 0<=v<=(2**63-1 if k=='tx_next' else 255): raise ValueError('invalid UART register')
            setattr(uart,k,v)
        if uart.tx_next<len(uart.tx) or uart.ier>15 or uart.mcr>31 or uart.errors & ~0x1e or uart.delta>15: raise ValueError('inconsistent UART state')
        return uart
