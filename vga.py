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
        self._graphics[8] = 0xff  # bit mask
        self._graphics_reg = 0
        self._latches = [0] * 4
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

    def GetGraphicsMemorySnapshot(self):
        """Return the active A0000h view in a stable debugger format."""
        if self._graphics_mode == 0x13:
            # Mode 13h exposes the four planar bytes interleaved by chain-4:
            # guest address low bits select the plane and the remaining bits
            # select its byte offset.
            return bytes(self._planes[address & 3][(address >> 2) & 0xffff]
                         for address in range(0x10000))
        if self._graphics_mode == 0x12:
            # Mode 12h is planar. Preserve all four 64 KiB planes rather than
            # the currently selected read plane so snapshots are lossless.
            return b''.join(bytes(plane) for plane in self._planes)
        return None

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

    def _clear_graphics_memory(self):
        for plane in self._planes:
            plane[:] = b'\x00' * len(plane)

    def _write_crtc_registers(self, values):
        for index, value in values.items():
            self._m6845.Write(index, value)
        self._m6845_reg = 0

    def BiosSetMode(self, mode):
        """Handle standard VGA INT 10h mode selection for the local BIOS."""
        mode &= 0xff
        if mode not in (0x03, 0x12, 0x13):
            return False
        self._mark_frame_dirty()
        self._display_address = 0
        self._cursor_location = -1
        self._attributes[:16] = range(16)
        self._attributes[0x10] = 0x00
        self._attributes[0x12] = 0x0f
        self._attributes[0x14] = 0x00
        self._graphics = [0] * 9
        self._graphics[7] = 0x0f
        self._graphics[8] = 0xff
        self._sequencer = [0, 1, 0x0f, 0, 0x06]
        crtc_common = {1: 79, 9: 15, 12: 0, 13: 0, 14: 0, 15: 0}

        if mode == 0x13:
            self._attributes[0x10] = 0x01  # color graphics
            self._graphics[5] = 0x40
            self._graphics[6] = 0x05
            self._sequencer[4] = 0x0e
            self._write_crtc_registers({**crtc_common, 1: 39, 9: 0, 18: 0xc7})
            self._graphics_mode = 0x13
            self._cga_mode = self.CGAMode.G320
            self._set_frame_size(640, 400)
            self._clear_graphics_memory()
            return True

        if mode == 0x12:
            self._attributes[0x10] = 0x01  # color graphics
            self._graphics[6] = 0x05
            self._write_crtc_registers({**crtc_common, 18: 0xdf})
            self._graphics_mode = 0x12
            self._cga_mode = self.CGAMode.G640
            self._set_frame_size(640, 480)
            self._clear_graphics_memory()
            return True

        if mode == 0x03:
            self._graphics[5] = 0x10
            self._graphics[6] = 0x0c
            self._write_crtc_registers({**crtc_common, 18: 0xdf})
            self._graphics_mode = 3
            self._cga_mode = self.CGAMode.Text80
            self._set_frame_size(640, 400)
            self._ram[:] = b'\x00' * len(self._ram)
            return True

        return False

    def BiosWritePixel(self, x, y, color):
        # INT 10h/0Ch uses bit 7 as XOR only in the 16-color mode.
        # In mode 13h it is part of the full 8-bit palette index.
        xor = self._graphics_mode == 0x12 and bool(color & 0x80)
        color &= 0x0f if self._graphics_mode == 0x12 else 0xff
        if self._graphics_mode == 0x13:
            if 0 <= x < 320 and 0 <= y < 200:
                address = 0xa0000 + y * 320 + x
                self.WriteByte(address, color)
                return True
        elif self._graphics_mode == 0x12:
            if 0 <= x < 640 and 0 <= y < 480:
                plane_offset = y * 80 + (x >> 3)
                bit = 0x80 >> (x & 7)
                if xor:
                    color ^= sum(((self._planes[plane][plane_offset] & bit) != 0)
                                 << plane for plane in range(4))
                for plane in range(4):
                    if color & (1 << plane):
                        self._planes[plane][plane_offset] |= bit
                    else:
                        self._planes[plane][plane_offset] &= ~bit
                return True
        return False

    def BiosInterrupt(self, state):
        """Service the small standard INT 10h subset needed for VGA modes."""
        if state.GetAH() == 0x00:
            return self.BiosSetMode(state.GetAL())
        if state.GetAH() == 0x0f:
            state.SetAL(self._graphics_mode & 0xff)
            state.SetAH(40 if self._graphics_mode == 0x13 else 80)
            state.SetBX(state.GetBX() & 0xff00)
            return True
        if state.GetAH() == 0x0c:
            return self.BiosWritePixel(state.GetCX(), state.GetDX(),
                                       state.GetAL())
        if state.GetAH() == 0x0d:
            color = self.BiosReadPixel(state.GetCX(), state.GetDX())
            if color is None:
                return False
            state.SetAL(color)
            return True
        return False

    def BiosReadPixel(self, x, y):
        if self._graphics_mode == 0x13:
            if 0 <= x < 320 and 0 <= y < 200:
                return self.ReadByte(0xa0000 + y * 320 + x)
        elif self._graphics_mode == 0x12:
            if 0 <= x < 640 and 0 <= y < 480:
                plane_offset = y * 80 + (x >> 3)
                bit = 0x80 >> (x & 7)
                return sum(((self._planes[plane][plane_offset] & bit) != 0)
                           << plane for plane in range(4))
        return None

    def _set_frame_size(self, width, height):
        if self._gf_width == width and self._gf_height == height:
            return
        self._gf_width = width
        self._gf_height = height
        self._pixels = bytearray((0, 0, 0, 255)) * (width * height)

    def _update_graphics_mode(self):
        """Infer standard VGA mode 12h/13h from the programmed registers."""
        # Standard VGA graphics modes select the A0000h aperture, disable
        # odd/even addressing, and select graphics mode in GC register 6.
        memory_map_a000 = (self._graphics[6] & 0x0d) == 0x05
        chain4 = bool(self._sequencer[4] & 0x08)
        packed_256 = bool(self._graphics[5] & 0x40)
        if memory_map_a000 and chain4 and packed_256:
            self._graphics_mode = 0x13
            self._cga_mode = self.CGAMode.G320
            self._set_frame_size(640, 400)
        elif (memory_map_a000 and not chain4 and not packed_256 and
              self._m6845.Read(18) >= 0xdf):
            self._graphics_mode = 0x12
            self._cga_mode = self.CGAMode.G640
            self._set_frame_size(640, 480)
        elif self._graphics_mode in (0x12, 0x13):
            self._graphics_mode = 3
            self._cga_mode = self.CGAMode.Text80
            self._set_frame_size(640, 400)

    def _graphics_pixel_color(self, color):
        return self._text_palette_color(color)

    def _load_latches(self, plane_offset):
        for plane in range(4):
            self._latches[plane] = self._planes[plane][plane_offset & 0xffff]

    @staticmethod
    def _rotate_byte(value, count):
        count &= 7
        if not count:
            return value & 0xff
        return ((value >> count) | (value << (8 - count))) & 0xff

    def _write_planar_byte(self, plane_offset, value, plane_mask=None):
        """Apply the VGA graphics-controller write operation to one byte."""
        write_mode = self._graphics[5] & 0x03
        rotate = self._graphics[3] & 0x07
        bit_mask = self._graphics[8]
        enable_set_reset = self._graphics[1] & 0x0f
        set_reset = self._graphics[0] & 0x0f
        logical_op = (self._graphics[3] >> 3) & 0x03
        rotated = self._rotate_byte(value, rotate)

        for plane in range(4):
            latch = self._latches[plane]
            if write_mode == 0:
                if enable_set_reset & (1 << plane):
                    source = 0xff if set_reset & (1 << plane) else 0x00
                else:
                    source = rotated
                write_mask = bit_mask
            elif write_mode == 1:
                source = latch
                write_mask = 0xff
            elif write_mode == 2:
                source = 0xff if value & (1 << plane) else 0x00
                write_mask = bit_mask
            elif write_mode == 3:
                source = 0xff if set_reset & (1 << plane) else 0x00
                write_mask = bit_mask & rotated
            else:
                continue

            if write_mode == 1:
                # Write mode 1 is the latch copy path. The host byte,
                # rotate, logical-op, and bit-mask fields are ignored.
                result = latch
            else:
                if logical_op == 1:
                    source &= latch
                elif logical_op == 2:
                    source |= latch
                elif logical_op == 3:
                    source ^= latch
                result = (source & write_mask) | (latch & ~write_mask)
            enabled_planes = self._sequencer[2] & 0x0f
            if plane_mask is not None:
                enabled_planes &= plane_mask
            if enabled_planes & (1 << plane):
                self._planes[plane][plane_offset & 0xffff] = result & 0xff

    def _read_planar_byte(self, offset):
        if self._graphics_mode == 0x13:
            plane = offset & 3
            plane_offset = (offset >> 2) & 0xffff
        else:
            plane = self._graphics[4] & 3
            plane_offset = offset & 0xffff
        self._load_latches(plane_offset)
        if self._graphics[5] & 0x08:
            compare = self._graphics[2] & 0x0f
            dont_care = self._graphics[7] & 0x0f
            result = 0
            for bit in range(8):
                color = sum(((self._latches[p] >> (7 - bit)) & 1) << p
                            for p in range(4))
                if ((color ^ compare) & dont_care) == 0:
                    result |= 1 << (7 - bit)
            return result
        return self._latches[plane]

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
            if self._graphics_mode in (0x12, 0x13):
                return self._read_planar_byte(offset - 0xa0000)
            plane = self._graphics[4] & 3
            return self._planes[plane][offset - 0xa0000]
        return 0xff

    @override
    def WriteByte(self, offset: int, value: int):
        if self._ram_offset <= offset < self._ram_offset + len(self._ram):
            index = offset - self._ram_offset
            value &= 0xff
            if self._ram[index] != value:
                self._ram[index] = value
                self._mark_frame_dirty()
            return
        if 0xa0000 <= offset < 0xb0000:
            plane_offset = offset - 0xa0000
            if self._graphics_mode == 0x13:
                plane = plane_offset & 3
                planar_offset = (plane_offset >> 2) & 0xffff
                if (self._sequencer[2] & (1 << plane) and
                        self._graphics[5] & 0x03 == 0 and
                        self._graphics[3] & 0x1f == 0 and
                        self._graphics[8] == 0xff and
                        self._graphics[1] & (1 << plane) == 0):
                    # Standard mode 13h uses a direct chain-4 byte path. Keep
                    # it cheap; unusual raster operations use the full VGA
                    # latch/write-mode implementation below.
                    self._planes[plane][planar_offset] = value
                    self._mark_frame_dirty()
                else:
                    self._write_planar_byte(planar_offset, value,
                                            1 << plane)
                    self._mark_frame_dirty()
                return
            if self._graphics_mode == 0x12:
                self._write_planar_byte(plane_offset, value)
                self._mark_frame_dirty()
                return
            map_mask = self._sequencer[2] & 0x0f
            if map_mask == 0:
                map_mask = 1
            for plane in range(4):
                if map_mask & (1 << plane):
                    self._planes[plane][plane_offset] = value
            self._mark_frame_dirty()

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
        self._update_graphics_mode()
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
        self._mark_frame_dirty()
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
            self._update_graphics_mode()
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
            self._update_graphics_mode()
            return False
        if port == 0x3ce:
            self._graphics_reg = value & 0x0f
            return False
        if port == 0x3cf:
            if self._graphics_reg < len(self._graphics):
                self._graphics[self._graphics_reg] = value
            self._update_graphics_mode()
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
        rgba_palette = [bytes((*palette, 255)) for palette in text_palette]
        row_bytes = 8 * pixel_width * 4
        glyph_cache = {}
        for y in range(25):
            for x in range(columns):
                offset = (self._display_address + (y * columns + x) * 2) & address_mask
                character = self._ram[offset]
                attributes = self._ram[(offset + 1) & address_mask]
                foreground, background, blink = self.DecodeTextAttribute(attributes)
                if blink and not self._blink_phase:
                    foreground = background
                glyph_offset = self._font_offset(character, attributes)
                glyph_key = (glyph_offset, foreground, background, pixel_width)
                glyph = glyph_cache.get(glyph_key)
                if glyph is None:
                    rendered = bytearray(row_bytes * 16)
                    for py in range(16):
                        line = self._planes[2][(glyph_offset + py) & 0xffff]
                        row_offset = py * row_bytes
                        for glyph_x in range(8):
                            color = foreground if line & (0x80 >> glyph_x) else background
                            pixel = rgba_palette[color]
                            pixel_offset = row_offset + glyph_x * pixel_width * 4
                            rendered[pixel_offset:pixel_offset + 4] = pixel
                            if pixel_width == 2:
                                rendered[pixel_offset + 4:pixel_offset + 8] = pixel
                    glyph = bytes(rendered)
                    glyph_cache[glyph_key] = glyph
                for py in range(16):
                    pixel_offset = ((y * 16 + py) * 640 +
                                    x * 8 * pixel_width) * 4
                    source_offset = py * row_bytes
                    self._pixels[pixel_offset:pixel_offset + row_bytes] = glyph[
                        source_offset:source_offset + row_bytes]
                if cursor['visible'] and offset == cursor['address']:
                    start = cursor['start_scanline']
                    end = cursor['end_scanline']
                    cursor_row = rgba_palette[foreground] * (8 * pixel_width)
                    for py in range(start, end + 1):
                        pixel_offset = ((y * 16 + py) * 640 +
                                        x * 8 * pixel_width) * 4
                        self._pixels[pixel_offset:pixel_offset + row_bytes] = cursor_row
        return 640, 400, self._pixels

    def RenderMode13FrameGraphical(self):
        self._set_frame_size(640, 400)
        row_size = 640 * 4
        # Mode 13h doubles each source pixel in both dimensions. Build the
        # complete 2-pixel horizontal block once per palette entry, then each
        # source scanline only needs one assignment per source pixel and two
        # row copies instead of four nested pixel assignments.
        pixel_blocks = {
            color: bytes((*self._palette[color & self._dac_pixel_mask], 255)) * 2
            for color in range(256)
        }
        for y in range(200):
            row_offset = y * 320
            line = bytearray(row_size)
            for x in range(320):
                color = self._planes[x & 3][((row_offset + x) >> 2) & 0xffff]
                pixel_offset = x * 8
                line[pixel_offset:pixel_offset + 8] = pixel_blocks[
                    color & self._dac_pixel_mask]
            output_offset = y * 2 * row_size
            self._pixels[output_offset:output_offset + row_size] = line
            self._pixels[output_offset + row_size:output_offset + 2 * row_size] = line
        return 640, 400, self._pixels

    def RenderMode12FrameGraphical(self):
        self._set_frame_size(640, 480)
        text_palette = [self._graphics_pixel_color(color) for color in range(16)]
        rgba_palette = [bytes((*palette, 255)) for palette in text_palette]
        byte_cache = {}
        for y in range(480):
            row_offset = y * 80
            output_offset = y * 640 * 4
            for byte_x in range(80):
                plane_offset = row_offset + byte_x
                key = tuple(self._planes[p][plane_offset] for p in range(4))
                colors = byte_cache.get(key)
                if colors is None:
                    chunk = bytearray(32)
                    for bit in range(8):
                        color = sum(((key[p] >> (7 - bit)) & 1) << p
                                    for p in range(4))
                        chunk[bit * 4:bit * 4 + 4] = rgba_palette[color]
                    colors = bytes(chunk)
                    byte_cache[key] = colors
                pixel_index = output_offset + byte_x * 32
                self._pixels[pixel_index:pixel_index + 32] = colors
        return 640, 480, self._pixels

    @override
    def GetFrame(self):
        if self._graphics_mode == 0x12:
            return self.RenderMode12FrameGraphical()
        if self._graphics_mode == 0x13:
            return self.RenderMode13FrameGraphical()
        if self._cga_mode in (self.CGAMode.Text40, self.CGAMode.Text80):
            return self.RenderTextFrameGraphical()
        return super().GetFrame()

    @override
    def Tick(self, cycles: int, clock: int) -> bool:
        result = super().Tick(cycles, clock)
        phase = bool((clock // BLINK_HALF_PERIOD_CYCLES) & 1)
        if phase != self._blink_phase:
            self._mark_frame_dirty()
        self._blink_phase = phase
        self._cursor_phase = phase
        return result
