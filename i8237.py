from typing import override, List, Tuple
import device

# DMA
class i8237(device.Device):
    class FlipFlop:
        def __init__(self):
            self._state = False

        def get_state(self) -> bool:
            rc = self._state
            self._state = not self._state
            return rc

        def reset(self):
            self._state = False

    class b16buffer:
        def __init__(self, f):
            self._value: int = 0
            self._base_value: int = 0
            self._f = f

        def Put(self, v: int):
            assert v >= 0 and v <= 255
            low_high = self._f.get_state()

            # A CPU byte write updates current and base independently. The
            # other byte can differ after DMA has advanced the current value.
            mask = 0x00ff if low_high else 0xff00
            bits = v << 8 if low_high else v
            self._value = (self._value & mask) | bits
            self._base_value = (self._base_value & mask) | bits

        def GetBaseValue(self) -> int:
            return self._base_value

        def GetValue(self) -> int:
            return self._value

        def SetValue(self, v: int):
            self._value = v

        def Get(self) -> int:
            low_high = self._f.get_state()

            if low_high:
                return self._value >> 8

            return self._value & 0xff

    def __init__(self, b):
        self._channel_page: List[int] = [ 0 ] * 4
        self._channel_address_register: List[b16buffer] = [ None ] * 4
        self._channel_word_count: List[b16buffer] = [ None ] * 4
        self._command: int = 0
        self._channel_mask: List[bool] = [ False ] * 4
        self._reached_tc: List[bool] = [ False ] * 4
        self._channel_mode: List[int] = [ 0 ] * 4
        self._ff = self.FlipFlop()
        self._dma_enabled = True

        for i in range(4):
            self._channel_address_register[i] = self.b16buffer(self._ff)
            self._channel_word_count[i] = self.b16buffer(self._ff)
            self._reached_tc[i] = False

        self._b = b

    @override
    def GetIRQNumber(self) -> int:
        return -1

    @override
    def GetName(self) -> str:
        return "i8237"

    @override
    def RegisterDevice(self, mappings: dict):
        for i in range(0x10):
            mappings[i] = self
        mappings[0x81] = self
        mappings[0x82] = self
        mappings[0x83] = self
        mappings[0x87] = self

    def _advance_channel(self, channel, transfers=1):
        """Account completed transfers; TC status is not a transfer inhibit.

        The batched refresh path must match individual transfers, including
        multiple auto-initialize periods, without looping per refresh request.
        """
        if transfers <= 0:
            return
        address_reg = self._channel_address_register[channel]
        count_reg = self._channel_word_count[channel]
        address = address_reg.GetValue()
        count = count_reg.GetValue()
        step = -1 if self._channel_mode[channel] & 0x20 else 1
        until_tc = count + 1
        if transfers < until_tc:
            address += step * transfers
            count -= transfers
        else:
            self._reached_tc[channel] = True
            if self._channel_mode[channel] & 0x10:
                # Reload both registers at TC. Page latches are external to
                # the 8237 and the CPU byte-pointer flip-flop is unaffected.
                remaining = (transfers - until_tc) % (count_reg.GetBaseValue() + 1)
                address = address_reg.GetBaseValue() + step * remaining
                count = count_reg.GetBaseValue() - remaining
            else:
                address += step * until_tc
                count = 0xffff
                self._channel_mask[channel] = True
        address_reg.SetValue(address & 0xffff)
        count_reg.SetValue(count)

    def TickChannel0(self, n):
        # RAM refresh is a DMA request too: controller/channel masks apply.
        if self._dma_enabled and not self._channel_mask[0]:
            self._advance_channel(0, n)

    @override
    def IO_Read(self, addr: int) -> int:
        v = 0
        if addr in (0, 2, 4, 6):
            v = self._channel_address_register[addr // 2].Get()
        elif addr in (1, 3, 5, 7):
            v = self._channel_word_count[addr // 2].Get()
        elif addr == 8:  # status register
            for i in range(4):
                if self._reached_tc[i]:
                    self._reached_tc[i] = False
                    v |= 1 << i
        return v

    def reset_masks(self, state: bool):
        for i in range(4):
            self._channel_mask[i] = state

    @override
    def IO_Write(self, addr: int, value: int) -> bool:
        if addr in (0, 2, 4, 6):
            self._channel_address_register[addr // 2].Put(value)
        elif addr in (1, 3, 5, 7):
            self._channel_word_count[addr // 2].Put(value)
        elif addr == 8:
            self._command = value
            self._dma_enabled = (self._command & 4) == 0
        elif addr == 0x0a:  # mask
            self._channel_mask[value & 3] = (value & 4) == 4  # dreq enable/disable
        elif addr == 0x0b:  # mode register
            self._channel_mode[value & 3] = value
            # TC status is sticky until a status read or master clear;
            # programming a mode must not acknowledge completed transfers.
        elif addr == 0x0c:  # reset flipflop
            self._ff.reset()
        elif addr == 0x0d:  # master reset
            self._command = 0
            self._dma_enabled = True
            self.reset_masks(True)
            self._ff.reset()
            for i in range(4):
                self._reached_tc[i] = False
        elif addr == 0x0e:  # reset masks
            self.reset_masks(False)
        elif addr == 0x0f:  # multiple mask
            for i in range(4):
                self._channel_mask[i] = (value & (1 << i)) != 0
        elif addr == 0x87:
            self._channel_page[0] = value & 0x0f
        elif addr == 0x83:
            self._channel_page[1] = value & 0x0f
        elif addr == 0x81:
            self._channel_page[2] = value & 0x0f
        elif addr == 0x82:
            self._channel_page[3] = value & 0x0f

        return False

    def ReceiveFromChannel(self, channel: int) -> int:
        if not self._dma_enabled or self._channel_mask[channel]:
            return -1
        address = self._channel_address_register[channel].GetValue()
        full_addr = (self._channel_page[channel] << 16) | address
        rc = self._b.ReadByte(full_addr)[0]
        self._advance_channel(channel)
        return rc

    def IsChannelTC(self, channel: int) -> bool:
        return self._reached_tc[channel]

    # used by devices (floppy etc) to send data to memory
    def SendToChannel(self, channel: int, value: int) -> bool:
        if not self._dma_enabled or self._channel_mask[channel]:
            return False
        address = self._channel_address_register[channel].GetValue()
        full_addr = (self._channel_page[channel] << 16) | address
        self._b.WriteByte(full_addr, value)
        self._advance_channel(channel)
        return True

    @override
    def GetAddressList(self):
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
