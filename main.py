#! /usr/bin/python3

from typing import List
import base64
import hashlib
import bus
import cga
import debugserver
import debugbreakpoints
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
    }
    breakpoints = debugbreakpoints.BreakpointManager()

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
        return stop

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
                    'execution.continue', 'execution.go', 'execution.step',
                    'breakpoints.create', 'breakpoints.list', 'breakpoints.delete',
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
            control['breakpoints_active'] = True
            return result

        if method == 'breakpoints.list':
            return {'breakpoints': breakpoints.list()}

        if method == 'breakpoints.delete':
            breakpoint_id = params.get('breakpoint_id')
            if not isinstance(breakpoint_id, str):
                raise ValueError('breakpoint_id is required')
            breakpoints.delete(breakpoint_id)
            control['breakpoints_active'] = breakpoints.has_any()
            return {'breakpoint_id': breakpoint_id, 'deleted': True}

        if method == 'execution.pause':
            control['paused'] = True
            control['step'] = False
            control['skip_breakpoint_id'] = None
            rpc_last_stop('pause')
            return {'paused': True, **rpc_registers()}

        if method in ('execution.continue', 'execution.go'):
            control['paused'] = False
            control['step'] = False
            if (control['last_stop'] and
                    control['last_stop'].get('kind') == 'breakpoint'):
                control['skip_breakpoint_id'] = control['last_stop'].get('breakpoint_id')
            control['last_stop'] = None
            return {'paused': False, **rpc_registers()}

        if method == 'execution.step':
            mode = params.get('mode', 'into')
            if mode != 'into':
                raise ValueError('execution.step supports mode=into only')
            if not control['paused']:
                raise ValueError('execution.step requires a paused emulator')
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
                rpc_last_stop(
                    'breakpoint', breakpoint_id=breakpoint['breakpoint_id'],
                    address=breakpoint['address'], hit_count=breakpoint['hit_count'])
                continue
        # print(f'{state.GetCS():04x}:{state.GetIP():04x} {GetRegisters(state)}')
        rc = p.Tick()
        if rc == -1:
            break
        control['revision'] += 1
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
