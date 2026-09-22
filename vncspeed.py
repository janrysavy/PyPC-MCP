"""Optional host-only speed pixels, transported as ordinary RFB rectangles."""
import time

from font import Font


class SpeedDisplay:
    """Wrap a display without modifying its framebuffer or guest memory.

    Speed uses the same nominal 4,770,000 ticks/second as main.py's RPC clock.
    Paused time is included: a stopped clock reports 0.00x after one sample.
    """

    def __init__(self, display, ticks, wall=time.monotonic):
        self.display = display
        self.ticks = ticks
        self.wall = wall
        self.previous = (wall(), ticks())
        self.text = 'EMU --.--x'
        self.version = 0
        self.key = None
        self.frame = None
        self.glyphs = Font().get_font()[2]

    def GetFrameVersion(self):
        now, ticks = self.wall(), self.ticks()
        elapsed = now - self.previous[0]
        delta = ticks - self.previous[1]
        if elapsed < 0 or delta < 0:
            self.text = 'EMU --.--x'
            self.previous = (now, ticks)
        elif elapsed >= 1.0:
            speed = delta / 4_770_000 / elapsed
            self.text = f'EMU {speed:.2f}x'
            self.previous = (now, ticks)
        getter = getattr(self.display, 'GetFrameVersion', None)
        # Displays without a version API must be rendered on every request.
        source = getter() if callable(getter) else object()
        key = (source, self.text)
        if key != self.key:
            self.key = key
            self.version += 1
            self.frame = None
        return self.version

    def GetFrame(self):
        # VNC calls GetFrameVersion first; preserve that sample/version pair.
        if self.key is None:
            self.GetFrameVersion()
        if self.frame is None:
            width, height, original = self.display.GetFrame()
            pixels = bytearray(original)
            text = self.text[-(width // 8):] if width >= 8 else ''
            left = width - len(text) * 8
            for index, character in enumerate(text):
                for y in range(min(8, height)):
                    bits = self.glyphs[ord(character) * 8 + y]
                    for x in range(8):
                        offset = (y * width + left + index * 8 + x) * 4
                        pixels[offset:offset + 4] = (
                            b'\xff\xff\xff\x00' if bits & (128 >> x)
                            else b'\x00\x00\x00\x00')
            self.frame = (width, height, pixels)
        return self.frame
