"""Optional IBM analog game port, with restartable one-shot charge state.

This follows MartyPC's configured potentiometer model (25.2us + 0.011us/ohm),
sampled at PyPC's instruction/device boundaries. It is not physical XT timing
proof. Axis -1 is minimum resistance, +1 maximum; buttons are active low.
"""
import math
from device import Device


def validate_input(params):
    """Preflight a complete two-button stick update before touching hardware."""
    index = params.get('joystick')
    if type(index) is not int or index not in (0, 1):
        raise ValueError('joystick must be 0 or 1')
    axes = []
    for name in ('x', 'y'):
        value = params.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or not -1 <= value <= 1:
            raise ValueError('joystick axes must be finite in -1..1')
        axes.append(float(value))
    buttons = params.get('buttons')
    if type(buttons) is not list or len(buttons) != 2 or any(type(b) is not bool for b in buttons):
        raise ValueError('two boolean joystick buttons required')
    return index, axes, list(buttons)


class GamePort(Device):
    def __init__(self):
        super().__init__()
        self._axes = [0.0] * 4
        self._buttons = [False] * 4
        self._elapsed = 0
        self._active = [False] * 4

    def set_input(self, params):
        index, axes, buttons = validate_input(params)
        self._axes[index * 2:index * 2 + 2] = axes
        self._buttons[index * 2:index * 2 + 2] = buttons

    def input_state(self):
        return [dict(joystick=i, x=self._axes[i * 2], y=self._axes[i * 2 + 1],
                     buttons=list(self._buttons[i * 2:i * 2 + 2])) for i in range(2)]

    def GetName(self):
        return 'GamePort'

    def RegisterDevice(self, mappings):
        mappings[0x201] = self

    def IO_Write(self, port, value):
        self._elapsed = 0
        self._active = [True] * 4
        return True

    def IO_Read(self, port):
        return sum(int(active) << i for i, active in enumerate(self._active)) | sum(
            int(not pressed) << (i + 4) for i, pressed in enumerate(self._buttons))

    def Ticks(self):
        return True

    def Tick(self, cycles, clock):
        self._clock = clock
        self._elapsed += cycles
        for i, position in enumerate(self._axes):
            threshold = math.ceil((25.2 + 550.0 * (position + 1.0)) * 4.77)
            if self._active[i] and self._elapsed >= threshold:
                self._active[i] = False
        return False

    def GetAddressList(self):
        return []

    def WriteByte(self, offset, value):
        return 0

    def ReadByte(self, offset):
        return 0

    def GetIRQNumber(self):
        return -1

    def dump(self):
        if self._next_interrupt:
            raise ValueError('game port cannot schedule an interrupt')
        return dict(axes=list(self._axes), buttons=list(self._buttons),
                    elapsed=self._elapsed, active=list(self._active), clock=self._clock)

    @classmethod
    def load(cls, saved):
        if type(saved) is not dict or set(saved) != {'axes', 'buttons', 'elapsed', 'active', 'clock'}:
            raise ValueError('invalid game port snapshot schema')
        axes, buttons, active = saved['axes'], saved['buttons'], saved['active']
        if (type(axes) is not list or len(axes) != 4 or
                any(type(v) not in (int, float) or not math.isfinite(v) or not -1 <= v <= 1 for v in axes) or
                any(type(values) is not list or len(values) != 4 or
                    any(type(v) is not bool for v in values) for values in (buttons, active)) or
                any(type(saved[k]) is not int or saved[k] < 0 for k in ('elapsed', 'clock'))):
            raise ValueError('invalid game port snapshot state')
        device = cls()
        device._axes = list(map(float, axes))
        device._buttons = list(buttons)
        device._active = list(active)
        device._elapsed, device._clock = saved['elapsed'], saved['clock']
        return device
