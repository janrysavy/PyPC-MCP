"""Optional ordered history of text VRAM and video-port writes.

The current-screen RPC cannot recover bytes erased before its next poll. This
recorder attaches at device write paths, so it retains intermediate text VRAM
states. A bounded ring reports overflow explicitly; callers must not call an
overflowed stream complete.
"""
from __future__ import annotations

import base64
from collections import deque
import hashlib


class VideoHistory:
    def __init__(self, clock):
        self._clock = clock
        self._screen = None
        self._events = deque()
        self._capacity = 0
        self._next = 1
        self._generation = 0
        self._lost = 0
        self._active = False

    def reset(self):
        if self._screen is not None:
            self._screen._video_history = None
        self._screen = None
        self._events.clear()
        self._active = False
        self._lost = 0

    def start(self, screen, capacity=500000):
        if self._active:
            raise ValueError('video history is already active')
        if isinstance(capacity, bool) or not isinstance(capacity, int) or not 1 <= capacity <= 1000000:
            raise ValueError('capacity must be an integer in 1..1000000')
        self.reset()
        self._generation += 1
        self._screen = screen
        self._capacity = capacity
        self._events = deque(maxlen=capacity)
        self._next = 1
        self._active = True
        screen._video_history = self
        vram = bytes(screen._ram)
        result = {
            'history_id': f'video-{self._generation}',
            'first_sequence': 1,
            'capacity': capacity,
            'clock': self._clock(),
            'adapter': screen.GetName(),
            'columns': screen.GetTextColumns(),
            'display_address': screen._display_address,
            'mode': screen._cga_mode.name,
            'vram_base64': base64.b64encode(vram).decode('ascii'),
            'vram_sha256': hashlib.sha256(vram).hexdigest(),
        }
        if hasattr(screen, 'GetCursorInfo'):
            result['cursor'] = screen.GetCursorInfo()
        if hasattr(screen, '_planes'):
            font = bytes(screen._planes[2])
            result['font_base64'] = base64.b64encode(font).decode('ascii')
            result['font_sha256'] = hashlib.sha256(font).hexdigest()
        return result

    def _append(self, kind, address, old, new):
        if not self._active:
            return
        if len(self._events) == self._capacity:
            self._lost += 1
        self._events.append((self._next, self._clock(), kind, address, old, new))
        self._next += 1

    def text_write(self, offset, old, new):
        if old != new:
            self._append('text', offset, old, new)

    def font_write(self, offset, old, new):
        if old != new:
            self._append('font', offset, old, new)

    def text_clear(self):
        self._append('clear_text', 0, 0, 0)

    def mode_change(self, mode, columns, display_address):
        self._append('mode', mode, columns, display_address)

    def port_write(self, port, value, width):
        # Preserve register-write order. The host can replay CRTC page flips
        # alongside text bytes instead of guessing which screen was visible.
        self._append('port', port, width, value)

    def read(self, history_id, cursor=None, limit=1024):
        if history_id != f'video-{self._generation}' or self._screen is None:
            raise ValueError('video history id is unknown or expired')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 4096:
            raise ValueError('limit must be an integer in 1..4096')
        first = self._next - len(self._events)
        if cursor is None:
            after = first - 1
        elif isinstance(cursor, int) and not isinstance(cursor, bool):
            after = cursor
        else:
            raise ValueError('cursor must be an integer or null')
        if after < first - 1 or after >= self._next:
            raise ValueError('video history cursor is outside the retained range')
        items = []
        for sequence, clock, kind, address, old, new in self._events:
            if sequence <= after:
                continue
            items.append({'sequence': sequence, 'clock': clock, 'kind': kind,
                          'address': address, 'old': old, 'new': new})
            if len(items) == limit:
                break
        next_cursor = items[-1]['sequence'] if items else after
        return {
            'history_id': history_id, 'active': self._active,
            'first_available_sequence': first,
            'next_sequence': self._next,
            'lost_events': self._lost,
            'events': items,
            'next_cursor': next_cursor if next_cursor < self._next - 1 else None,
        }

    def stop(self):
        if self._screen is None:
            raise ValueError('video history has not started')
        self._active = False
        self._screen._video_history = None
        return {'history_id': f'video-{self._generation}',
                'events': self._next - 1, 'lost_events': self._lost}
