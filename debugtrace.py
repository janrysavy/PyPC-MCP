"""Bounded instruction tracing for the PyPC debugger channel."""


class TraceRecorder:
    def __init__(self, capacity: int = 65536):
        self._capacity = capacity
        self._events = []
        self._active = False
        self._started = False
        self._remaining = 0
        self._detail = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def started(self) -> bool:
        return self._started

    @property
    def detail(self):
        return self._detail

    def start(self, detail: str, instruction_count: int) -> dict:
        if detail not in ('csip', 'short', 'normal', 'long'):
            raise ValueError('detail must be csip, short, normal, or long')
        if not isinstance(instruction_count, int) or isinstance(instruction_count, bool):
            raise ValueError('instruction_count must be an integer')
        if instruction_count < 1 or instruction_count > self._capacity:
            raise ValueError(f'instruction_count must be 1..{self._capacity}')
        if self._active:
            raise ValueError('a CPU trace is already active')
        self._events = []
        self._active = True
        self._started = True
        self._remaining = instruction_count
        self._detail = detail
        return {
            'active': True, 'detail': detail,
            'instruction_count': instruction_count,
        }

    def capture(self, event: dict) -> None:
        if not self._active:
            return
        event = dict(event)
        event['sequence'] = len(self._events)
        self._events.append(event)
        self._remaining -= 1
        if self._remaining == 0:
            self._active = False

    def read(self, cursor, limit: int) -> dict:
        if not self._started:
            raise ValueError('no CPU trace has been started')
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 256:
            raise ValueError('limit must be 1..256')
        if cursor is None:
            offset = 0
        elif isinstance(cursor, str) and cursor.startswith('trace-'):
            try:
                offset = int(cursor[6:], 10)
            except ValueError as error:
                raise ValueError('cursor must be null or trace-N') from error
        else:
            raise ValueError('cursor must be null or trace-N')
        if offset < 0 or offset > len(self._events):
            raise ValueError('cursor is outside the retained trace')
        end = min(offset + limit, len(self._events))
        next_cursor = f'trace-{end}' if self._active or end < len(self._events) else None
        return {
            'active': self._active, 'detail': self._detail,
            'event_count': len(self._events),
            'events': self._events[offset:end], 'next_cursor': next_cursor,
        }

    def stop(self) -> dict:
        if not self._started:
            raise ValueError('no CPU trace has been started')
        self._active = False
        return {'active': False, 'event_count': len(self._events)}
