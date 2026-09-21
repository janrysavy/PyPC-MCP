"""Bounded hardware-event tracing for the PyPC debugger channel."""

from collections import deque


class HardwareTraceRecorder:
    def __init__(self, maximum_capacity: int = 65536):
        self._maximum_capacity = maximum_capacity
        self._events = deque()
        self._capacity = 0
        self._active = False
        self._started = False
        self._next_sequence = 1
        self._dropped = 0
        self._include_io = True
        self._include_irq = True
        self._ports = None
        self._irqs = None

    @property
    def active(self) -> bool:
        return self._active

    def start(self, capacity: int = 4096, include_io: bool = True,
              include_irq: bool = True, ports=None, irqs=None) -> dict:
        if self._active:
            raise ValueError('a hardware trace is already active')
        if (not isinstance(capacity, int) or isinstance(capacity, bool) or
                not 1 <= capacity <= self._maximum_capacity):
            raise ValueError(f'capacity must be 1..{self._maximum_capacity}')
        if not isinstance(include_io, bool) or not isinstance(include_irq, bool):
            raise ValueError('include_io and include_irq must be boolean')
        if not include_io and not include_irq:
            raise ValueError('at least one hardware event class must be enabled')

        normalized_ports = None
        if ports is not None:
            if not isinstance(ports, list):
                raise ValueError('ports must be an array')
            normalized_ports = []
            for item in ports:
                if not isinstance(item, dict):
                    raise ValueError('each port filter must be an object')
                first = self._number(item.get('first'), 'ports.first')
                last = self._number(item.get('last'), 'ports.last')
                if not 0 <= first <= last <= 0xffff:
                    raise ValueError('port filters must be ordered 16-bit ranges')
                normalized_ports.append((first, last))
            if not normalized_ports:
                normalized_ports = None

        normalized_irqs = None
        if irqs is not None:
            if not isinstance(irqs, list):
                raise ValueError('irqs must be an array')
            normalized_irqs = []
            for item in irqs:
                irq = self._number(item, 'irqs')
                if not 0 <= irq <= 15:
                    raise ValueError('irqs must contain values in 0..15')
                normalized_irqs.append(irq)
            if not normalized_irqs:
                normalized_irqs = None

        self._events.clear()
        self._capacity = capacity
        self._active = True
        self._started = True
        self._next_sequence = 1
        self._dropped = 0
        self._include_io = include_io
        self._include_irq = include_irq
        self._ports = normalized_ports
        self._irqs = normalized_irqs
        return self._status()

    @staticmethod
    def _number(value, name):
        if isinstance(value, bool):
            raise ValueError(f'{name} must be an integer')
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value, 0)
        raise ValueError(f'{name} must be an integer')

    def _accepts(self, event: dict) -> bool:
        if event['kind'].startswith('io_'):
            if not self._include_io:
                return False
            if self._ports is not None and not any(
                    first <= event['port'] <= last
                    for first, last in self._ports):
                return False
        else:
            if not self._include_irq:
                return False
            if self._irqs is not None and event['irq'] not in self._irqs:
                return False
        return True

    def capture(self, event: dict) -> None:
        if not self._active or not self._accepts(event):
            return
        captured = dict(event)
        captured['sequence'] = self._next_sequence
        self._next_sequence += 1
        if len(self._events) >= self._capacity:
            self._events.popleft()
            self._dropped += 1
        self._events.append(captured)

    def _status(self):
        first = self._events[0]['sequence'] if self._events else self._next_sequence
        return {
            'active': self._active,
            'capacity': self._capacity,
            'dropped_event_count': self._dropped,
            'first_available_sequence': first,
        }

    def read(self, cursor, limit: int) -> dict:
        if not self._started:
            raise ValueError('no hardware trace has been started')
        if (not isinstance(limit, int) or isinstance(limit, bool) or
                not 1 <= limit <= self._maximum_capacity):
            raise ValueError(f'limit must be 1..{self._maximum_capacity}')

        if cursor is None:
            start_sequence = self._events[0]['sequence'] if self._events else self._next_sequence
        elif isinstance(cursor, str) and cursor.startswith('hardware-'):
            try:
                start_sequence = int(cursor[9:], 10) + 1
            except ValueError as error:
                raise ValueError('cursor must be null or hardware-N') from error
        else:
            raise ValueError('cursor must be null or hardware-N')

        first = self._events[0]['sequence'] if self._events else self._next_sequence
        last = self._events[-1]['sequence'] if self._events else first - 1
        if start_sequence < first or start_sequence > last + 1:
            raise ValueError('cursor is outside the retained hardware trace')

        events = [event for event in self._events
                  if event['sequence'] >= start_sequence][:limit]
        if events:
            last_returned = events[-1]['sequence']
        else:
            last_returned = start_sequence - 1
        has_more = last_returned < last
        next_cursor = f'hardware-{last_returned}' if has_more else None
        return {
            **self._status(), 'events': events, 'next_cursor': next_cursor,
        }

    def stop(self) -> dict:
        if not self._started:
            raise ValueError('no hardware trace has been started')
        self._active = False
        return self._status()
