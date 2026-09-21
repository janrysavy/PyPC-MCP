"""Reference flag methods copied from baseline 5144476fe37815ec4b28007b98c823d2b749f7b8.

Benchmark-only reference; no wrapper is added to the emulation hot loop.
"""
from state8088 import State8088


class LegacyState(State8088):
    def SetZSPFlags(self, v: int):
        assert v >= 0 and v <= 65535
        self.SetFlagZ(v == 0)
        self.SetFlagS(bool(v & 0x80))
        self.SetFlagP(v)

    def SetFlagC(self, state: bool):
        self.SetFlag(0, state)

    def GetFlagC(self) -> bool:
        return self.GetFlag(0)

    def SetFlagP(self, v: int):
        y = v ^ (v >> 1)
        y = y ^ (y >> 2)
        y = y ^ (y >> 4)
        self.SetFlag(2, (y & 1) == 0)

    def GetFlagP(self) -> bool:
        return self.GetFlag(2)

    def SetFlagA(self, state: bool):
        self.SetFlag(4, state)

    def GetFlagA(self) -> bool:
        return self.GetFlag(4)

    def SetFlagZ(self, state: bool):
        self.SetFlag(6, state)

    def GetFlagZ(self) -> bool:
        return self.GetFlag(6)

    def SetFlagS(self, state: bool):
        self.SetFlag(7, state)

    def GetFlagS(self) -> bool:
        return self.GetFlag(7)

    def SetFlagT(self, state: bool):
        self.SetFlag(8, state)

    def GetFlagT(self) -> bool:
        return self.GetFlag(8)

    def SetFlagI(self, state: bool):
        self.SetFlag(9, state)

    def GetFlagI(self) -> bool:
        return self.GetFlag(9)

    def SetFlagD(self, state: bool):
        self.SetFlag(10, state)

    def GetFlagD(self) -> bool:
        return self.GetFlag(10)

    def SetFlagO(self, state: bool):
        self.SetFlag(11, state)

    def GetFlagO(self) -> bool:
        return self.GetFlag(11)
