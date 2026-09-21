"""VGA-compatible text modes backed by the existing CGA display path."""

from typing import override

import cga
import font


CPU_CLOCK_HZ = 4_770_000
BLINK_HALF_PERIOD_CYCLES = CPU_CLOCK_HZ // 2
VGA_DEFAULT_PALETTE_RGB = (
    (0, 0, 0), (0, 0, 170), (0, 170, 0), (0, 170, 170),
    (170, 0, 0), (170, 0, 170), (170, 85, 0), (170, 170, 170),
    (85, 85, 85), (85, 85, 255), (85, 255, 85), (85, 255, 255),
    (255, 85, 85), (255, 85, 255), (255, 255, 85), (255, 255, 255),
)


class VGA(cga.CGA):
    """Implement the VGA interfaces needed by DOS text-mode software.

    This is deliberately a text-mode VGA device.  It keeps the existing CGA
    graphics fallback, while adding the VGA register ports, a 32 KiB text
    window, and plane 2 font storage.  The framebuffer remains 640x400, which
    is the native size of an 80x25 VGA text screen with 8x16 glyphs.
    """

    def __init__(self, palette_per_scanline: bool):
        super().__init__(palette_per_scanline)
        self._ram = bytearray(0x8000)
        self._ram_offset = 0xb8000
        self._planes = [bytearray(0x10000) for _ in range(4)]
        self._sequencer = [0] * 5
        self._sequencer[2] = 0x03  # map mask: text character and attribute planes
        self._sequencer[4] = 0x06  # odd/even addressing, chain-4 disabled
        self._sequencer_reg = 0
        self._graphics = [0] * 9
        self._graphics[6] = 0x0c  # A0000 graphics-memory aperture
        self._graphics_reg = 0
        self._attributes = [0] * 0x15
        self._attributes[:16] = range(16)
        # Text-mode DOS software commonly uses bit 7 as the fourth background
        # color bit. Start VGA in that extended-color mode; software that
        # explicitly enables blinking can still set bit 3 through 0x3c0.
        self._attributes[0x10] = 0x00
        self._attributes[0x14] = 0x00  # color-select register
        self._attribute_reg = 0
        self._attribute_flipflop = False
        # VGA DAC components are six bits wide. Keep the public palette in
        # the BGR tuple order used by MDA, but retain the DAC's RGB ordering
        # for port reads and writes.
        self._palette[:16] = [
            (blue, green, red) for red, green, blue in VGA_DEFAULT_PALETTE_RGB]
        self._palette.extend([(0, 0, 0)] * (256 - len(self._palette)))
        self._dac = []
        for blue, green, red in self._palette:
            self._dac.append((red // 4, green // 4, blue // 4))
        self._dac_write_index = 0
        self._dac_write_component = 0
        self._dac_read_index = 0
        self._dac_read_component = 0
        self._dac_state = 0
        self._dac_pixel_mask = 0xff
        self._misc_output = 0x67  # color display, VGA clock selection
        self._blink_phase = True
        self._cursor_phase = True
        self._cga_mode = self.CGAMode.Text80
        self._graphics_mode = 3
        self._m6845.Write(1, 79)  # 80 displayed text columns
        self._m6845.Write(9, 15)  # 16 scan lines per character
        self._load_default_font()

    @override
    def GetName(self) -> str:
        return 'VGA'

    @override
    def RegisterDevice(self, mappings: dict):
        super().RegisterDevice(mappings)
        for port in range(0x3c0, 0x3d0):
            mappings[port] = self

    @override
    def GetAddressList(self):
        return [(0xa0000, 0x10000), (self._ram_offset, len(self._ram))]

    def _load_default_font(self):
        _, source_height, source = font.Font().get_font()
        for character in range(256):
            source_offset = character * source_height
            target_offset = character * 32
            for row in range(16):
                self._planes[2][target_offset + row] = source[
                    source_offset + min(row // 2, source_height - 1)]

    def GetFontMemory(self):
        return bytes(self._planes[2])

    def GetCursorInfo(self):
        start = self._m6845.Read(10) & 0x1f
        end = self._m6845.Read(11) & 0x1f
        address = (self._cursor_location << 1) & self.GetTextAddressMask()
        columns = self.GetTextColumns()
        position = (address - self._display_address) & self.GetTextAddressMask()
        visible = (self._cursor_location >= 0 and
                   not (self._m6845.Read(10) & 0x20) and
                   self._cursor_phase and start <= end and end < 16)
        return {
            'address': address,
            'start_scanline': start,
            'end_scanline': end,
            'enabled': self._cursor_location >= 0 and not (self._m6845.Read(10) & 0x20),
            'visible': visible,
            'blink_phase': self._cursor_phase,
            'column': (position // 2) % columns,
            'row': (position // (columns * 2)) % 25,
        }

    def _font_offset(self, character, attributes):
        # Sequencer character-map select chooses one of two 8 KiB maps.  The
        # attribute intensity bit selects the second map when they differ.
        map_shift = 2 if attributes & 0x08 else 0
        character_map = (self._sequencer[3] >> map_shift) & 3
        return character_map * 0x2000 + character * 32

    def GetTextColumns(self):
        return 40 if self._cga_mode == self.CGAMode.Text40 else 80

    @override
    def DecodeTextAttribute(self, attributes):
        blink_enabled = bool(self._attributes[0x10] & 0x08)
        if blink_enabled:
            background = (attributes >> 4) & 0x07
        else:
            background = (attributes >> 4) & 0x0f
        return (attributes & 0x0f, background,
                blink_enabled and bool(attributes & 0x80))

    def _text_palette_color(self, color):
        palette_index = self._attributes[color & 0x0f] & 0x3f
        color_select = self._attributes[0x14]
        if self._attributes[0x10] & 0x80:
            palette_index = ((palette_index & 0x0f) |
                             ((color_select & 0x03) << 4))
        palette_index |= (color_select & 0x0c) << 4
        return self._palette[palette_index & self._dac_pixel_mask]

    @staticmethod
    def _expand_dac(value):
        return (value << 2) | (value >> 4)

    def _update_dac_color(self, index):
        red, green, blue = self._dac[index]
        self._palette[index] = (self._expand_dac(blue),
                                self._expand_dac(green),
                                self._expand_dac(red))

    @override
    def ReadByte(self, offset: int) -> int:
        if self._ram_offset <= offset < self._ram_offset + len(self._ram):
            return self._ram[offset - self._ram_offset]
        if 0xa0000 <= offset < 0xb0000:
            plane = self._graphics[4] & 3
            return self._planes[plane][offset - 0xa0000]
        return 0xff

    @override
    def WriteByte(self, offset: int, value: int):
        if self._ram_offset <= offset < self._ram_offset + len(self._ram):
            self._ram[offset - self._ram_offset] = value
            return
        if 0xa0000 <= offset < 0xb0000:
            plane_offset = offset - 0xa0000
            map_mask = self._sequencer[2] & 0x0f
            if map_mask == 0:
                map_mask = 1
            for plane in range(4):
                if map_mask & (1 << plane):
                    self._planes[plane][plane_offset] = value

    def _write_crtc(self, port: int, value: int) -> bool:
        handled = super().IO_Write(port, value)
        if port in (0x3d5, 0x3d7, 0x3d1, 0x3d3) and self._m6845_reg in (12, 13):
            # VGA CRTC start address is expressed in character words.  The
            # text-memory and RPC interfaces use byte offsets.
            word_address = ((self._m6845.Read(12) << 8) |
                            self._m6845.Read(13))
            self._display_address = (word_address << 1) & self.GetTextAddressMask()
        if port in (0x3d5, 0x3d7, 0x3d1, 0x3d3) and self._m6845_reg == 1:
            self._cga_mode = (self.CGAMode.Text40 if value <= 40
                              else self.CGAMode.Text80)
        return handled

    @override
    def IO_Read(self, port: int) -> int:
        if port == 0x3c1:
            index = self._attribute_reg & 0x1f
            return self._attributes[index] if index < len(self._attributes) else 0xff
        if port == 0x3c2:
            return 0
        if port == 0x3c6:
            return self._dac_pixel_mask
        if port == 0x3c7:
            return self._dac_state
        if port == 0x3c8:
            return self._dac_write_index
        if port == 0x3c9:
            red, green, blue = self._dac[self._dac_read_index]
            component = (red, green, blue)[self._dac_read_component]
            self._dac_read_component = (self._dac_read_component + 1) % 3
            if self._dac_read_component == 0:
                self._dac_read_index = (self._dac_read_index + 1) & 0xff
            return component
        if port == 0x3cc:
            return self._misc_output
        if port == 0x3c5:
            return (self._sequencer[self._sequencer_reg]
                    if self._sequencer_reg < len(self._sequencer) else 0xff)
        if port == 0x3cf:
            return (self._graphics[self._graphics_reg]
                    if self._graphics_reg < len(self._graphics) else 0xff)
        if port == 0x3da:
            self._attribute_flipflop = False
            return super().IO_Read(port)
        if port in (0x3d5, 0x3d7, 0x3d1, 0x3d3):
            return self._m6845.Read(self._m6845_reg)
        return super().IO_Read(port)

    @override
    def IO_Write(self, port: int, value: int) -> bool:
        if port == 0x3c0:
            if self._attribute_flipflop:
                index = self._attribute_reg & 0x1f
                if index < len(self._attributes):
                    self._attributes[index] = (value & 0x3f
                                               if index < 0x10 else value)
                self._attribute_flipflop = False
            else:
                self._attribute_reg = value & 0x1f
                self._attribute_flipflop = True
            return False
        if port == 0x3c2:
            self._misc_output = value
            return False
        if port == 0x3c6:
            self._dac_pixel_mask = value & 0xff
            return False
        if port == 0x3c7:
            self._dac_read_index = value
            self._dac_read_component = 0
            self._dac_state = 3
            return False
        if port == 0x3c8:
            self._dac_write_index = value
            self._dac_write_component = 0
            self._dac_state = 0
            return False
        if port == 0x3c9:
            index = self._dac_write_index
            components = list(self._dac[index])
            components[self._dac_write_component] = value & 0x3f
            self._dac[index] = tuple(components)
            self._update_dac_color(index)
            self._dac_write_component = (self._dac_write_component + 1) % 3
            if self._dac_write_component == 0:
                self._dac_write_index = (self._dac_write_index + 1) & 0xff
            return False
        if port == 0x3c4:
            self._sequencer_reg = value & 0x1f
            return False
        if port == 0x3c5:
            if self._sequencer_reg < len(self._sequencer):
                self._sequencer[self._sequencer_reg] = value
            return False
        if port == 0x3ce:
            self._graphics_reg = value & 0x0f
            return False
        if port == 0x3cf:
            if self._graphics_reg < len(self._graphics):
                self._graphics[self._graphics_reg] = value
            return False
        if port in (0x3d4, 0x3d6, 0x3d0, 0x3d2,
                    0x3d5, 0x3d7, 0x3d1, 0x3d3):
            return self._write_crtc(port, value)
        return super().IO_Write(port, value)

    @override
    def RenderTextFrameGraphical(self):
        columns = self.GetTextColumns()
        pixel_width = 2 if columns == 40 else 1
        address_mask = self.GetTextAddressMask()
        cursor = self.GetCursorInfo()
        text_palette = [self._text_palette_color(color) for color in range(16)]
        for y in range(25):
            for x in range(columns):
                offset = (self._display_address + (y * columns + x) * 2) & address_mask
                character = self._ram[offset]
                attributes = self._ram[(offset + 1) & address_mask]
                foreground, background, blink = self.DecodeTextAttribute(attributes)
                if blink and not self._blink_phase:
                    foreground = background
                glyph_offset = self._font_offset(character, attributes)
                for py in range(16):
                    line = self._planes[2][(glyph_offset + py) & 0xffff]
                    pixel_offset = ((y * 16 + py) * 640 +
                                    x * 8 * pixel_width) * 4
                    for glyph_x in range(8):
                        palette = text_palette[
                            foreground if line & (0x80 >> glyph_x) else background]
                        for repeat_x in range(pixel_width):
                            index = pixel_offset + (glyph_x * pixel_width + repeat_x) * 4
                            self._pixels[index + 0] = palette[0]
                            self._pixels[index + 1] = palette[1]
                            self._pixels[index + 2] = palette[2]
                            self._pixels[index + 3] = 255
                if cursor['visible'] and offset == cursor['address']:
                    start = cursor['start_scanline']
                    end = cursor['end_scanline']
                    for py in range(start, end + 1):
                        pixel_offset = ((y * 16 + py) * 640 +
                                        x * 8 * pixel_width) * 4
                        for glyph_x in range(8 * pixel_width):
                            index = pixel_offset + glyph_x * 4
                            palette = text_palette[foreground]
                            self._pixels[index + 0] = palette[0]
                            self._pixels[index + 1] = palette[1]
                            self._pixels[index + 2] = palette[2]
                            self._pixels[index + 3] = 255
        return 640, 400, self._pixels

    @override
    def GetFrame(self):
        if self._cga_mode in (self.CGAMode.Text40, self.CGAMode.Text80):
            return self.RenderTextFrameGraphical()
        return super().GetFrame()

    @override
    def Tick(self, cycles: int, clock: int) -> bool:
        result = super().Tick(cycles, clock)
        phase = bool((clock // BLINK_HALF_PERIOD_CYCLES) & 1)
        self._blink_phase = phase
        self._cursor_phase = phase
        return result
