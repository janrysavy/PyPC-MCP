"""Optional reserved upper-memory window for the DOS control worker."""
from __future__ import annotations

from typing import override

import device


class DOSMailbox(device.Device):
    base = 0xD8000
    size = 0x2000

    def __init__(self, contents: bytes | None = None):
        super().__init__()
        if contents is not None and (type(contents) is not bytes or len(contents) != self.size):
            raise ValueError('DOS mailbox image must be exactly 8192 bytes')
        self._memory = bytearray(contents if contents is not None else self.size)

    @override
    def GetName(self) -> str:
        return 'DOS mailbox'

    @override
    def RegisterDevice(self, mappings: dict):
        pass

    @override
    def IO_Write(self, port: int, value: int):
        return False

    @override
    def IO_Read(self, port: int) -> int:
        return 0xff

    @override
    def GetAddressList(self):
        return [(self.base, self.size)]

    @override
    def WriteByte(self, offset: int, value: int):
        self._memory[offset - self.base] = value & 0xff

    @override
    def ReadByte(self, offset: int) -> int:
        return self._memory[offset - self.base]

    @override
    def Ticks(self) -> bool:
        return False

    @override
    def GetIRQNumber(self) -> int:
        return -1
