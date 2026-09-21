"""Execution breakpoint matching for the PyPC debugger channel."""

from dataclasses import dataclass


_REGISTERS = {
    'ax', 'bx', 'cx', 'dx', 'sp', 'bp', 'si', 'di',
    'cs', 'ds', 'es', 'ss', 'ip', 'flags',
}
_OPERATORS = {'eq', 'ne', 'lt', 'le', 'gt', 'ge'}


@dataclass
class _Breakpoint:
    breakpoint_id: str
    address: dict
    physical: int
    length: int
    once: bool
    condition: dict | None
    hit_filter: dict
    hit_count: int = 0

    def matches_address(self, segment: int, offset: int, physical: int) -> bool:
        if self.address['space'] == 'segmented':
            return (self.address['segment'] == segment and
                    self.address['offset'] == offset)
        return self.physical == physical

    def matches_condition(self, registers: dict) -> bool:
        if self.condition is None:
            return True
        actual = registers[self.condition['register']]
        expected = self.condition['value']
        operator = self.condition['operator']
        return {
            'eq': actual == expected,
            'ne': actual != expected,
            'lt': actual < expected,
            'le': actual <= expected,
            'gt': actual > expected,
            'ge': actual >= expected,
        }[operator]

    def selected_hit(self) -> bool:
        self.hit_count += 1
        skip = self.hit_filter['skip']
        every = self.hit_filter['every']
        return self.hit_count > skip and (self.hit_count - skip - 1) % every == 0

    def to_dict(self) -> dict:
        result = {
            'breakpoint_id': self.breakpoint_id,
            'kind': 'execution',
            'address': dict(self.address),
            'length': self.length,
            'once': self.once,
            'hit_filter': dict(self.hit_filter),
            'hit_count': self.hit_count,
        }
        if self.condition is not None:
            result['condition'] = dict(self.condition)
        return result


class BreakpointManager:
    """Manage execution breakpoints at instruction boundaries."""

    def __init__(self):
        self._next_id = 1
        self._breakpoints = {}

    @staticmethod
    def _number(value, name):
        if isinstance(value, bool):
            raise ValueError(f'{name} must be a number')
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value, 0)
        raise ValueError(f'{name} must be a number')

    def create(self, params: dict) -> dict:
        if params.get('kind', 'execution') != 'execution':
            raise ValueError('only execution breakpoints are supported')

        address = params.get('address')
        if not isinstance(address, dict):
            raise ValueError('address must be an object')
        space = address.get('space', 'physical')
        if space == 'segmented':
            segment = self._number(address.get('segment'), 'address.segment')
            offset = self._number(address.get('offset'), 'address.offset')
            if not 0 <= segment <= 0xffff or not 0 <= offset <= 0xffff:
                raise ValueError('segmented address values must be 16-bit')
            normalized_address = {
                'space': 'segmented', 'segment': segment, 'offset': offset,
            }
            physical = ((segment << 4) + offset) & 0xfffff
        elif space in ('physical', 'linear'):
            offset = self._number(address.get('offset'), 'address.offset')
            if not 0 <= offset < 0x100000:
                raise ValueError('linear address must be within 1 MiB')
            normalized_address = {'space': space, 'offset': offset}
            physical = offset
        else:
            raise ValueError('address.space must be physical, linear, or segmented')

        length = self._number(params.get('length', 1), 'length')
        if length != 1:
            raise ValueError('execution breakpoint length must be 1')
        once = params.get('once', False)
        if not isinstance(once, bool):
            raise ValueError('once must be boolean')

        condition = params.get('condition')
        if condition is not None:
            if not isinstance(condition, dict):
                raise ValueError('condition must be an object')
            register = condition.get('register')
            operator = condition.get('operator')
            if register not in _REGISTERS:
                raise ValueError(f'unsupported condition register: {register}')
            if operator not in _OPERATORS:
                raise ValueError(f'unsupported condition operator: {operator}')
            value = self._number(condition.get('value'), 'condition.value')
            if not 0 <= value <= 0xffff:
                raise ValueError('condition.value must be a 16-bit value')
            condition = {'register': register, 'operator': operator, 'value': value}

        hit_filter = params.get('hit_filter', {})
        if not isinstance(hit_filter, dict):
            raise ValueError('hit_filter must be an object')
        skip = self._number(hit_filter.get('skip', 0), 'hit_filter.skip')
        every = self._number(hit_filter.get('every', 1), 'hit_filter.every')
        if skip < 0 or every < 1:
            raise ValueError('hit_filter.skip must be non-negative and every must be positive')

        breakpoint_id = f'bp-{self._next_id}'
        self._next_id += 1
        breakpoint = _Breakpoint(
            breakpoint_id, normalized_address, physical, length, once, condition,
            {'skip': skip, 'every': every},
        )
        self._breakpoints[breakpoint_id] = breakpoint
        return breakpoint.to_dict()

    def list(self) -> list[dict]:
        return [breakpoint.to_dict() for breakpoint in self._breakpoints.values()]

    def has_any(self) -> bool:
        return bool(self._breakpoints)

    def delete(self, breakpoint_id: str) -> None:
        if breakpoint_id not in self._breakpoints:
            raise ValueError('breakpoint_id was not found')
        del self._breakpoints[breakpoint_id]

    def check(self, segment: int, offset: int, registers: dict, skip_id: str | None = None):
        physical = ((segment << 4) + offset) & 0xfffff
        for breakpoint in list(self._breakpoints.values()):
            if breakpoint.breakpoint_id == skip_id:
                continue
            if not breakpoint.matches_address(segment, offset, physical):
                continue
            if not breakpoint.matches_condition(registers):
                continue
            if not breakpoint.selected_hit():
                continue
            result = breakpoint.to_dict()
            if breakpoint.once:
                del self._breakpoints[breakpoint.breakpoint_id]
            return result
        return None
