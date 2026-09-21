#! /usr/bin/python3

from typing import List
import base64
import hashlib
import bus
import cga
import debugserver
import debugbreakpoints
import debugtrace
import i8088
import i8253
import i8255
import keyboard
import mda
import rom
import telnet
import time
import vncserver
import xtide

def GetRegisters(state) -> str:
    return f'{state.GetFlagsAsString()} AX:{state.GetAX():04x} BX:{state.GetBX():04x} CX:{state.GetCX():04x} DX:{state.GetDX():04x} SP:{state.GetSP():04x} BP:{state.GetBP():04x} SI:{state.GetSI():04x} DI:{state.GetDI():04x} flags:{state.GetFlags():04x} ES:{state.GetES():04x} CS:{state.GetCS():04x} SS:{state.GetSS():04x} DS:{state.GetDS():04x} IP:{state.GetIP():04x}'


def ParseNumber(value):
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError('number expected')


def ReadTextScreen(scr, display_address=None):
    columns = scr.GetTextColumns()
    rows = []
    if display_address is None:
        display_address = scr._display_address & 0x3fff
    else:
        display_address &= 0x3fff
    for y in range(25):
        row = bytearray()
        for x in range(columns):
            offset = (display_address + (y * columns + x) * 2) & 0x3fff
            character = scr._ram[offset]
            row.append(character if character >= 32 else 32)
        rows.append(row.decode('cp437', errors='replace').rstrip())
    return rows


def ReadTextCells(scr, display_address=None):
    columns = scr.GetTextColumns()
    cells = []
    if display_address is None:
        display_address = scr._display_address & 0x3fff
    else:
        display_address &= 0x3fff
    for y in range(25):
        row = []
        for x in range(columns):
            offset = (display_address + (y * columns + x) * 2) & 0x3fff
            character = scr._ram[offset]
            attribute = scr._ram[(offset + 1) & 0x3fff]
            row.append({
                'code': character,
                'char': bytes((character,)).decode('cp437', errors='replace'),
                'attribute': attribute,
                'foreground': attribute & 0x0f,
                'background': (attribute >> 4) & 0x07,
                'blink': bool(attribute & 0x80),
            })
        cells.append(row)
    return cells

# GIL Status
import sysconfig
status = sysconfig.get_config_var("Py_GIL_DISABLED")
if status is None:
    print("GIL cannot be disabled")
if status == 0:
    print("GIL is active")
if status == 1:
    print("GIL is disabled")

try:
    devices: List[object] = []
    devices.append(i8253.i8253())
    kb = keyboard.Keyboard()
    devices.append(kb)
    devices.append(i8255.i8255(kb))
    #scr = mda.MDA()
    scr = cga.CGA(False)
    devices.append(scr)
    devices.append(xtide.XTIDE(('harddisk.img',)));

    roms = []
    roms.append(rom.Rom('roms/GLABIOS.ROM', 0xf000 * 16 + 0xe000))
    roms.append(rom.Rom('roms/ide_xt.bin', 0xd000 * 16 + 0x0000))

    b = bus.Bus(1024 * 1024, devices, roms)
    p = i8088.i8088(b, devices, True)
    state = p.GetState()
    state.SetCS(0xf000)
    state.SetIP(0xfff0)

    t = telnet.Telnet(2300, kb, scr)
    v = vncserver.VNCServer(scr, kb, 5902, False)
    debug = debugserver.DebugServer(2301)
    control = {
        'paused': False, 'step': False, 'revision': 0,
        'snapshot_number': 0, 'snapshots': {},
        'last_stop': None, 'skip_breakpoint_id': None,
        'breakpoints_active': False,
        'memory_watchpoints_active': False,
        'interrupt_breakpoints_active': False,
        'trace_active': False, 'instruction_hooks_active': False,
        'next_operation': 1, 'operations': {}, 'active_operation': None,
        'run_until_id': None,
    }
    breakpoints = debugbreakpoints.BreakpointManager()
    trace = debugtrace.TraceRecorder()

    def rpc_params(request):
        params = request.get('params', {})
        if not isinstance(params, dict):
            raise ValueError('params must be an object')
        return params

    def rpc_number(value, name):
        if isinstance(value, bool):
            raise ValueError(f'{name} must be a number')
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            return int(value, 0)
        raise ValueError(f'{name} must be a number')

    def rpc_address(params):
        address = params.get('address', params.get('offset'))
        if isinstance(address, dict):
            space = address.get('space', 'physical')
            offset = rpc_number(address.get('offset'), 'address.offset')
            if space == 'segmented':
                offset += rpc_number(address.get('segment'), 'address.segment') * 16
            elif space not in ('physical', 'linear'):
                raise ValueError('address.space must be physical, linear, or segmented')
        else:
            offset = rpc_number(address, 'address')
        if offset < 0 or offset >= 1024 * 1024:
            raise ValueError('address is outside the 1 MiB address space')
        return offset

    def rpc_registers():
        return {
            'general': {
                'ax': state.GetAX(), 'bx': state.GetBX(), 'cx': state.GetCX(),
                'dx': state.GetDX(), 'sp': state.GetSP(), 'bp': state.GetBP(),
                'si': state.GetSI(), 'di': state.GetDI(),
            },
            'segments': {
                'cs': state.GetCS(), 'ds': state.GetDS(),
                'es': state.GetES(), 'ss': state.GetSS(),
            },
            'ip': state.GetIP(),
            'flags': state.GetFlags(),
            'flags_text': state.GetFlagsAsString(),
            'clock': state.GetClock(),
            'in_hlt': state.GetInHlt(),
            'state_revision': control['revision'],
        }

    def rpc_flat_registers():
        registers = rpc_registers()
        return {
            **registers['general'], **registers['segments'],
            'ip': registers['ip'], 'flags': registers['flags'],
        }

    def rpc_last_stop(kind, **details):
        stop = {'kind': kind, **details, 'registers': rpc_registers()}
        control['last_stop'] = stop
        operation = control['active_operation']
        if operation is not None:
            operation['done'] = True
            operation['stop_reason'] = stop
            control['active_operation'] = None
        return stop

    def rpc_start_operation(kind):
        if control['active_operation'] is not None:
            raise ValueError('an execution operation is already running')
        operation_id = f'op-{control["next_operation"]}'
        control['next_operation'] += 1
        operation = {'operation_id': operation_id, 'kind': kind, 'done': False}
        control['operations'][operation_id] = operation
        while len(control['operations']) > 256:
            oldest_id, oldest = next(iter(control['operations'].items()))
            if not oldest['done']:
                break
            del control['operations'][oldest_id]
        control['active_operation'] = operation
        return operation

    def rpc_clear_run_until():
        predicate_id = control['run_until_id']
        if predicate_id is not None and breakpoints.contains(predicate_id):
            breakpoints.delete(predicate_id)
        control['run_until_id'] = None
        rpc_refresh_instruction_hooks()

    def rpc_refresh_instruction_hooks():
        control['breakpoints_active'] = breakpoints.has_execution()
        control['memory_watchpoints_active'] = breakpoints.has_memory_access()
        control['interrupt_breakpoints_active'] = breakpoints.has_interrupt()
        control['instruction_hooks_active'] = (
            control['breakpoints_active'] or control['trace_active'])
        p.SetMemoryAccessHook(
            rpc_memory_access if control['memory_watchpoints_active'] else None)
        p.SetInterruptHook(
            rpc_interrupt if control['interrupt_breakpoints_active'] else None)
        p.SetMemoryTraceHook(
            rpc_trace_memory if control['trace_active'] else None)
        p.SetIOTraceHook(
            rpc_trace_io if control['trace_active'] else None)

    def rpc_trace_event():
        registers = rpc_registers()
        physical = ((registers['segments']['cs'] << 4) + registers['ip']) & 0xfffff
        opcode = bytes(b.ReadByte((physical + i) & 0xfffff)[0] for i in range(8))
        return {
            'kind': 'hlt' if registers['in_hlt'] else 'instruction',
            'address': {
                'space': 'segmented', 'segment': registers['segments']['cs'],
                'offset': registers['ip'],
            },
            'physical': physical,
            'opcode_hex': opcode.hex(),
            'clock_before': registers['clock'],
            'registers_before': registers,
            'effects': [],
        }

    def rpc_trace_memory(kind, physical, old, new):
        event = control.get('trace_event')
        if event is None:
            return
        effect = {
            'kind': kind,
            'address': {'space': 'linear', 'offset': physical},
            'byte_count': 1,
        }
        if kind == 'memory_read':
            effect['data_base64'] = base64.b64encode(bytes((new,))).decode('ascii')
        else:
            effect['before_base64'] = (
                None if old is None else base64.b64encode(bytes((old,))).decode('ascii'))
            effect['after_base64'] = base64.b64encode(bytes((new,))).decode('ascii')
        event['effects'].append(effect)

    def rpc_trace_io(kind, port, value, byte_count, handled):
        event = control.get('trace_event')
        if event is None:
            return
        event['effects'].append({
            'kind': kind, 'port': port, 'byte_count': byte_count,
            'value': value, 'handled': bool(handled),
        })

    def rpc_memory_access(kind, physical, old, new, instruction_address):
        if instruction_address is None:
            return None
        access = {
            'kind': kind,
            'address': {'space': 'linear', 'offset': physical},
            'byte_count': 1,
            'instruction_address': {
                'space': 'segmented',
                'segment': instruction_address['segment'],
                'offset': instruction_address['offset'],
            },
            'new_value': new,
        }
        if old is not None:
            access['old_value'] = old
        return breakpoints.check_memory_access(
            kind, physical, access, control['skip_breakpoint_id'])

    def rpc_interrupt(number, ah, al, interrupt_state, instruction_address):
        if instruction_address is None:
            return None
        return breakpoints.check_interrupt(
            number, ah, al, rpc_flat_registers(), control['skip_breakpoint_id'])

    register_access = {
        'ax': (state.GetAX, state.SetAX), 'bx': (state.GetBX, state.SetBX),
        'cx': (state.GetCX, state.SetCX), 'dx': (state.GetDX, state.SetDX),
        'sp': (state.GetSP, state.SetSP), 'bp': (state.GetBP, state.SetBP),
        'si': (state.GetSI, state.SetSI), 'di': (state.GetDI, state.SetDI),
        'cs': (state.GetCS, state.SetCS), 'ds': (state.GetDS, state.SetDS),
        'es': (state.GetES, state.SetES), 'ss': (state.GetSS, state.SetSS),
        'ip': (state.GetIP, state.SetIP), 'flags': (state.GetFlags, state.SetFlags),
    }

    def rpc_require_paused():
        if not control['paused']:
            raise ValueError('target must be paused for this operation')

    def rpc_hash(data):
        return hashlib.sha256(data).hexdigest()

    def rpc_snapshot_component(data):
        return {
            'byte_count': len(data), 'sha256': rpc_hash(data),
        }

    def rpc_video_snapshot():
        vram = bytes(scr._ram)
        text = '\n'.join(ReadTextScreen(scr)).encode('utf-8')
        control['snapshot_number'] += 1
        snapshot_id = f'snap-{control["snapshot_number"]}'
        control['snapshots'][snapshot_id] = {
            'vram': vram, 'text': text,
            'state_revision': control['revision'],
        }
        while len(control['snapshots']) > 8:
            del control['snapshots'][next(iter(control['snapshots']))]
        return {
            'snapshot_id': snapshot_id,
            'state_revision': control['revision'],
            'captured_ticks': state.GetClock(),
            'video_mode': scr._graphics_mode,
            'columns': scr.GetTextColumns(), 'rows': 25,
            'display_address': scr._display_address & 0x3fff,
            'active_page': (scr._display_address & 0x3fff) // (scr.GetTextColumns() * 25 * 2),
            'text': rpc_snapshot_component(text),
            'vram': rpc_snapshot_component(vram),
        }

    def handle_debug(request):
        method = request['method']
        params = rpc_params(request)

        if method in ('agent.capabilities', 'emulator.info'):
            return {
                'protocol': 'JSON-RPC 2.0 over localhost JSON-lines',
                'endpoint': '127.0.0.1:2301',
                'cpu': '8088', 'memory_bytes': 1024 * 1024,
                'address_spaces': ['physical', 'linear', 'segmented'],
                'limits': {'max_memory_bytes': 65536, 'max_keyboard_events': 32,
                           'retained_video_snapshots': 8},
                'methods': [
                    'agent.capabilities', 'emulator.info', 'state.get_registers',
                    'state.get', 'state.set_registers', 'session.status',
                    'memory.read', 'memory.write', 'video.text', 'video.snapshot',
                    'video.snapshot.read', 'io.read', 'input.keyboard',
                    'keyboard.scancode', 'input.state', 'execution.pause',
                    'execution.continue', 'execution.go', 'execution.run_until',
                    'execution.wait', 'execution.step',
                    'breakpoints.create', 'breakpoints.list', 'breakpoints.delete',
                    'trace.start', 'trace.read', 'trace.stop',
                ],
            }

        if method == 'session.status':
            session_id = params.get('session_id')
            if session_id is not None and session_id != 'pypc':
                raise ValueError('session_id must be pypc')
            return {
                'session_id': 'pypc',
                'state': 'stopped' if control['paused'] else 'running',
                'state_revision': control['revision'],
                'clock': state.GetClock(),
                'target': {'cpu': '8088', 'memory_bytes': 1024 * 1024,
                           'video': 'CGA'},
                'last_stop': control['last_stop'],
            }

        if method in ('state.get_registers', 'state.get'):
            return rpc_registers()

        if method == 'state.set_registers':
            rpc_require_paused()
            expected_revision = rpc_number(
                params.get('expected_state_revision'), 'expected_state_revision')
            expected = params.get('expected')
            values = params.get('set')
            if not isinstance(expected, dict) or not isinstance(values, dict) or not values:
                raise ValueError('expected and set must be non-empty objects')
            if expected_revision != control['revision']:
                raise ValueError('expected state revision does not match the live state')
            for name, value in values.items():
                if name not in register_access:
                    raise ValueError(f'unsupported register: {name}')
                if name not in expected:
                    raise ValueError(f'register {name} is not guarded by expected')
                if rpc_number(expected[name], f'expected.{name}') != register_access[name][0]():
                    raise ValueError(f'register precondition mismatch: {name}')
                value = rpc_number(value, f'set.{name}')
                if value < 0 or value > 0xffff:
                    raise ValueError(f'set.{name} must be a 16-bit value')
            before = rpc_registers()
            for name, value in values.items():
                register_access[name][1](rpc_number(value, f'set.{name}'))
            control['revision'] += 1
            return {'before': before, 'after': rpc_registers()}

        if method == 'memory.read':
            address = rpc_address(params)
            length = rpc_number(params.get('length', 1), 'length')
            if length < 1 or length > 65536 or address + length > 1024 * 1024:
                raise ValueError('length must be 1..65536 and stay within memory')
            data = bytes(b.ReadByte(address + i)[0] for i in range(length))
            return {
                'address': address, 'byte_count': length,
                'data_base64': base64.b64encode(data).decode('ascii'),
                'data_hex': data.hex(),
                'sha256': hashlib.sha256(data).hexdigest(),
                'state_revision': control['revision'],
            }

        if method == 'memory.write':
            rpc_require_paused()
            address = rpc_address(params)
            encoded = params.get('data_base64')
            if not isinstance(encoded, str):
                raise ValueError('data_base64 is required')
            try:
                data = base64.b64decode(encoded, validate=True)
            except Exception as error:
                raise ValueError('data_base64 is invalid') from error
            if not data or len(data) > 65536 or address + len(data) > 1024 * 1024:
                raise ValueError('data must contain 1..65536 bytes and stay within memory')
            before = bytes(b.ReadByte(address + i)[0] for i in range(len(data)))
            expected_sha256 = params.get('expected_sha256')
            if expected_sha256 is not None:
                if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
                    raise ValueError('expected_sha256 must be a 64-character hexadecimal SHA-256 value')
                try:
                    int(expected_sha256, 16)
                except ValueError as error:
                    raise ValueError('expected_sha256 must be hexadecimal') from error
                if rpc_hash(before) != expected_sha256.lower():
                    raise ValueError('expected_sha256 does not match current memory')
            for i, value in enumerate(data):
                b.WriteByte(address + i, value)
            after = bytes(b.ReadByte(address + i)[0] for i in range(len(data)))
            control['revision'] += 1
            return {
                'address': address, 'byte_count': len(data),
                'before_sha256': rpc_hash(before), 'after_sha256': rpc_hash(after),
                'state_revision': control['revision'],
            }

        if method == 'video.snapshot':
            return rpc_video_snapshot()

        if method == 'video.snapshot.read':
            snapshot_id = params.get('snapshot_id')
            component = params.get('component')
            if snapshot_id not in control['snapshots']:
                raise ValueError('snapshot_id was not found or has expired')
            if component not in ('vram', 'text'):
                raise ValueError('component must be vram or text')
            offset = rpc_number(params.get('offset', 0), 'offset')
            length = rpc_number(params.get('length'), 'length')
            data = control['snapshots'][snapshot_id][component]
            if offset < 0 or length < 1 or length > 65536 or offset + length > len(data):
                raise ValueError('snapshot range is invalid')
            chunk = data[offset:offset + length]
            return {
                'snapshot_id': snapshot_id, 'component': component,
                'offset': offset, 'byte_count': len(chunk),
                'component_byte_count': len(data),
                'component_sha256': rpc_hash(data),
                'data_base64': base64.b64encode(chunk).decode('ascii'),
                'sha256': rpc_hash(chunk),
            }

        if method == 'video.text':
            columns = scr.GetTextColumns()
            page_size = columns * 25 * 2
            page_count = len(scr._ram) // page_size
            active_address = scr._display_address & 0x3fff
            if 'page' in params:
                page = rpc_number(params['page'], 'page')
                if page < 0 or page >= page_count:
                    raise ValueError(f'page must be 0..{page_count - 1}')
                display_address = page * page_size
            elif 'display_address' in params:
                display_address = rpc_number(params['display_address'], 'display_address')
                if display_address < 0 or display_address >= len(scr._ram):
                    raise ValueError('display_address must be within CGA VRAM')
            else:
                display_address = active_address
            display_address &= 0x3fff
            return {
                'columns': columns, 'rows': 25,
                'page_size_bytes': page_size, 'page_count': page_count,
                'page': display_address // page_size,
                'active_page': active_address // page_size,
                'is_active_page': display_address == active_address,
                'display_address': display_address,
                'mode': scr._cga_mode.name,
                'graphics_mode': scr._graphics_mode,
                'text': ReadTextScreen(scr, display_address),
                'cells': ReadTextCells(scr, display_address),
                'state_revision': control['revision'],
            }

        if method == 'io.read':
            port = rpc_number(params.get('port'), 'port')
            if port < 0 or port > 0xffff:
                raise ValueError('port must be a 16-bit number')
            return {'port': port, 'value': p._io.In(port, False),
                    'state_revision': control['revision']}

        if method == 'io.write':
            rpc_require_paused()
            port = rpc_number(params.get('port'), 'port')
            value = rpc_number(params.get('value'), 'value')
            if port < 0 or port > 0xffff:
                raise ValueError('port must be a 16-bit number')
            if value < 0 or value > 0xff:
                raise ValueError('value must be an 8-bit number')
            handled = p._io.Out(port, value, False)
            control['revision'] += 1
            return {
                'port': port, 'value': value, 'handled': bool(handled),
                'state_revision': control['revision'],
            }

        if method in ('input.keyboard', 'keyboard.scancode'):
            events = params.get('events')
            if events is None:
                events = [params]
            if not isinstance(events, list) or not events or len(events) > 32:
                raise ValueError('events must contain 1..32 keyboard events')
            for event in events:
                if not isinstance(event, dict):
                    raise ValueError('keyboard event must be an object')
                code = rpc_number(event.get('scan_code'), 'scan_code')
                if code < 0 or code > 0x7f:
                    raise ValueError('scan_code must be an XT make code (0..127)')
                pressed = event.get('pressed', True)
                if not isinstance(pressed, bool):
                    raise ValueError('pressed must be boolean')
                kb.PushKeyboardScancode(code if pressed else code | 0x80)
            return {'accepted': len(events), 'state_revision': control['revision']}

        if method == 'input.state':
            return {
                'keyboard': {'pressed_scancodes': kb.GetPressedScancodes()},
                'joysticks': [], 'state_revision': control['revision'],
            }

        if method == 'breakpoints.create':
            result = breakpoints.create(params)
            rpc_refresh_instruction_hooks()
            return result

        if method == 'breakpoints.list':
            return {'breakpoints': breakpoints.list()}

        if method == 'breakpoints.delete':
            breakpoint_id = params.get('breakpoint_id')
            if not isinstance(breakpoint_id, str):
                raise ValueError('breakpoint_id is required')
            breakpoints.delete(breakpoint_id)
            rpc_refresh_instruction_hooks()
            return {'breakpoint_id': breakpoint_id, 'deleted': True}

        if method == 'trace.start':
            rpc_require_paused()
            detail = params.get('detail', 'normal')
            instruction_count = rpc_number(
                params.get('instruction_count', 256), 'instruction_count')
            result = trace.start(detail, instruction_count)
            control['trace_active'] = True
            rpc_refresh_instruction_hooks()
            return result

        if method == 'trace.read':
            limit = rpc_number(params.get('limit', 128), 'limit')
            return trace.read(params.get('cursor'), limit)

        if method == 'trace.stop':
            result = trace.stop()
            control['trace_active'] = False
            rpc_refresh_instruction_hooks()
            return result

        if method == 'execution.wait':
            operation_id = params.get('operation_id')
            if not isinstance(operation_id, str):
                raise ValueError('operation_id is required')
            timeout_ms = rpc_number(params.get('timeout_ms', 0), 'timeout_ms')
            if timeout_ms < 0 or timeout_ms > 60000:
                raise ValueError('timeout_ms must be 0..60000')
            operation = control['operations'].get(operation_id)
            if operation is None:
                raise ValueError('operation_id was not found')
            if not operation['done']:
                return {'running': True}
            return {
                'state': 'stopped',
                'stop_reason': operation['stop_reason'],
            }

        if method == 'execution.pause':
            rpc_clear_run_until()
            control['paused'] = True
            control['step'] = False
            control['skip_breakpoint_id'] = None
            rpc_last_stop('pause')
            return {'paused': True, **rpc_registers()}

        if method in ('execution.continue', 'execution.go'):
            operation = rpc_start_operation('continue')
            control['paused'] = False
            control['step'] = False
            if (control['last_stop'] and
                    control['last_stop'].get('kind') == 'breakpoint'):
                control['skip_breakpoint_id'] = control['last_stop'].get('breakpoint_id')
            control['last_stop'] = None
            return {
                'operation_id': operation['operation_id'], 'state': 'running',
                'paused': False, **rpc_registers(),
            }

        if method == 'execution.run_until':
            rpc_require_paused()
            if 'max_emulated_ns' in params:
                raise ValueError('max_emulated_ns is not supported by this emulator')
            predicate = params.get('predicate')
            if not isinstance(predicate, dict):
                raise ValueError('predicate must be an object')
            if predicate.get('once', False):
                raise ValueError('run_until predicates are always one-shot')
            predicate_params = dict(predicate)
            predicate_params['once'] = True
            predicate_result = breakpoints.create(
                predicate_params, id_prefix='until', private=True)
            try:
                operation = rpc_start_operation('run_until')
            except Exception:
                breakpoints.delete(predicate_result['breakpoint_id'])
                raise
            control['run_until_id'] = predicate_result['breakpoint_id']
            rpc_refresh_instruction_hooks()
            control['paused'] = False
            control['step'] = False
            control['skip_breakpoint_id'] = None
            control['last_stop'] = None
            return {
                'operation_id': operation['operation_id'],
                'predicate_id': predicate_result['breakpoint_id'],
                'state': 'running',
            }

        if method == 'execution.step':
            mode = params.get('mode', 'into')
            if mode != 'into':
                raise ValueError('execution.step supports mode=into only')
            if not control['paused']:
                raise ValueError('execution.step requires a paused emulator')
            rpc_clear_run_until()
            control['step'] = True
            control['paused'] = False
            if (control['last_stop'] and
                    control['last_stop'].get('kind') == 'breakpoint'):
                control['skip_breakpoint_id'] = control['last_stop'].get('breakpoint_id')
            control['last_stop'] = None
            return {'stepping': True, **rpc_registers()}

        raise LookupError(f'unknown method: {method}')

    print('Use: "telnet localhost 2300" to interact with the emulated system')
    print('and/or connect using a VNC client to localhost:5902 (preferred)')

    p_time = time.time()
    p_cycles = 0
    while True:
        if debug.has_pending():
            debug.process_pending(handle_debug)
        if control['paused']:
            time.sleep(0.001)
            continue
        if control['breakpoints_active']:
            breakpoint = breakpoints.check(
                state.GetCS(), state.GetIP(), rpc_flat_registers(),
                control['skip_breakpoint_id'])
            control['skip_breakpoint_id'] = None
            if breakpoint is not None:
                control['paused'] = True
                control['step'] = False
                is_run_until = breakpoint['breakpoint_id'] == control['run_until_id']
                if control['run_until_id'] is not None:
                    rpc_clear_run_until()
                details = {
                    'breakpoint_id': breakpoint['breakpoint_id'],
                    'address': breakpoint['address'],
                    'hit_count': breakpoint['hit_count'],
                }
                if is_run_until:
                    details['predicate_id'] = breakpoint['breakpoint_id']
                rpc_last_stop('run_until' if is_run_until else 'breakpoint', **details)
                continue
        if control['instruction_hooks_active'] and control['trace_active']:
            trace_before = rpc_trace_event()
        else:
            trace_before = None
        control['trace_event'] = trace_before
        # print(f'{state.GetCS():04x}:{state.GetIP():04x} {GetRegisters(state)}')
        rc = p.Tick()
        if rc == -1:
            control['trace_event'] = None
            break
        control['revision'] += 1
        memory_stop = (p.ConsumeMemoryWriteStop()
                       if control['memory_watchpoints_active'] else None)
        if memory_stop is not None:
            control['paused'] = True
            is_run_until = memory_stop['breakpoint_id'] == control['run_until_id']
            if control['run_until_id'] is not None:
                rpc_clear_run_until()
            details = {
                'breakpoint_id': memory_stop['breakpoint_id'],
                'address': memory_stop['address'],
                'length': memory_stop['length'],
                'hit_count': memory_stop['hit_count'],
                'access': memory_stop['access'],
            }
            if is_run_until:
                details['predicate_id'] = memory_stop['breakpoint_id']
            rpc_last_stop('run_until' if is_run_until else 'breakpoint', **details)
            rpc_refresh_instruction_hooks()
        interrupt_stop = (p.ConsumeInterruptStop()
                          if control['interrupt_breakpoints_active'] else None)
        if memory_stop is None and interrupt_stop is not None:
            control['paused'] = True
            is_run_until = interrupt_stop['breakpoint_id'] == control['run_until_id']
            if control['run_until_id'] is not None:
                rpc_clear_run_until()
            details = {
                'breakpoint_id': interrupt_stop['breakpoint_id'],
                'event': interrupt_stop['event'],
                'hit_count': interrupt_stop['hit_count'],
            }
            if is_run_until:
                details['predicate_id'] = interrupt_stop['breakpoint_id']
            rpc_last_stop('run_until' if is_run_until else 'breakpoint', **details)
            rpc_refresh_instruction_hooks()
        control['skip_breakpoint_id'] = None
        if trace_before is not None:
            trace_before['clock_after'] = state.GetClock()
            trace_before['clock_delta'] = (
                trace_before['clock_after'] - trace_before['clock_before'])
            if trace.detail in ('normal', 'long'):
                trace_before['registers_after'] = rpc_registers()
            elif trace.detail == 'csip':
                trace_before.pop('registers_before', None)
            trace.capture(trace_before)
            control['trace_event'] = None
            control['trace_active'] = trace.active
            rpc_refresh_instruction_hooks()
        if control['step']:
            control['step'] = False
            control['paused'] = True
            rpc_last_stop('step')
        cur_cycles = state.GetClock()
        c_diff = cur_cycles - p_cycles
        if c_diff >= 4700000:
            p_cycles = cur_cycles
            now = time.time()
            ##print(f'\033[1;82H{100 * c_diff / (now - p_time) / 4700000:.2f}%', end='')
            print(f'{100 * c_diff / (now - p_time) / 4700000:.2f}%')
            p_time = now

except KeyboardInterrupt as ki:
    print('exit')
