"""Exercise production RPC functions and loop using a RAM-only machine.

As with test_keyboard_rpc, AST extraction avoids booting ROMs, disk images,
Telnet and VNC. No CPU, breakpoint, trace, or RPC implementation is mocked.
The original while-loop body is executed once per pump in a one-item loop.
"""
import ast
import base64
import hashlib
import json
import socket
import threading
from pathlib import Path
import time
import unittest

import bus
import debugbreakpoints
import debugtrace
import debugserver
import i8088


class IdleTransport:
    def has_pending(self):
        return False


class HeadlessMachine:
    def __init__(self, main_path=None):
        main_path = main_path or Path(__file__).resolve().parents[1] / 'main.py'
        tree = ast.parse(main_path.read_text(), filename=str(main_path))
        body = next(node.body for node in tree.body if isinstance(node, ast.Try))
        self.memory = bus.Bus(1 << 20, [], [])
        self.cpu = i8088.i8088(self.memory, [], False)
        self.state = self.cpu.GetState()
        for name, value in dict(CS=0x1000, DS=0x2000, ES=0x3000,
                                SS=0x2000, IP=0x100, SP=0x9000, Flags=2).items():
            getattr(self.state, 'Set' + name)(value)
        self.namespace = dict(
            b=self.memory, p=self.cpu, state=self.state,
            breakpoints=debugbreakpoints.BreakpointManager(),
            trace=debugtrace.TraceRecorder(),
            debug=IdleTransport(), base64=base64, hashlib=hashlib,
            time=time, p_cycles=0, p_time=time.time(),
        )
        selected = [node for node in body if isinstance(node, ast.FunctionDef) or (
            isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
            and target.id in ('control', 'register_access') for target in node.targets))]
        module = ast.Module(body=selected, type_ignores=[])
        exec(compile(module, str(main_path), 'exec'), self.namespace)
        self.namespace['control']['paused'] = True
        loop = next(node for node in body if isinstance(node, ast.While))
        once = ast.For(target=ast.Name(id='_pump', ctx=ast.Store()),
                       iter=ast.Tuple(elts=[ast.Constant(0)], ctx=ast.Load()),
                       body=loop.body, orelse=[])
        module = ast.fix_missing_locations(ast.Module(body=[once], type_ignores=[]))
        self.loop = compile(module, str(main_path), 'exec')

    @property
    def control(self):
        return self.namespace['control']

    def pump(self):
        exec(self.loop, self.namespace)

    def rpc(self, method, **params):
        return self.namespace['handle_debug']({'method': method, 'params': params})

    def load(self, data, offset=0x100, segment=0x1000):
        for index, value in enumerate(data):
            self.cpu.WriteMemByte(segment, (offset + index) & 0xffff, value)

    def execution_bp(self, offset):
        return self.rpc('breakpoints.create', address={
            'space': 'segmented', 'segment': 0x1000, 'offset': offset})['breakpoint_id']

    def interrupt_bp(self):
        return self.rpc('breakpoints.create', kind='interrupt', event={
            'type': 'software_interrupt', 'number': 0x21})['breakpoint_id']


class ExecutionRPCTests(unittest.TestCase):
    def test_continue_interrupt_with_other_execution_breakpoint(self):
        machine = HeadlessMachine()
        machine.load(bytes.fromhex('cd 21'))
        machine.cpu.WriteMemWord(0, 0x84, 0x200)
        machine.cpu.WriteMemWord(0, 0x86, 0x1000)
        interrupt = machine.interrupt_bp()
        target = machine.execution_bp(0x200)
        machine.rpc('execution.continue')
        machine.pump()
        self.assertEqual(machine.control['last_stop']['breakpoint_id'], interrupt)
        machine.rpc('execution.continue')
        machine.pump()
        self.assertEqual(machine.state.GetIP(), 0x200)
        machine.pump()
        self.assertEqual(machine.control['last_stop']['breakpoint_id'], target)

    def test_step_preserves_memory_breakpoint_reason(self):
        machine = HeadlessMachine()
        machine.load(bytes.fromhex('a3 00 40'))
        breakpoint = machine.rpc('breakpoints.create', kind='memory_write',
                                address={'space': 'linear', 'offset': 0x24000})
        machine.rpc('execution.step')
        machine.pump()
        self.assertEqual(machine.control['last_stop']['kind'], 'breakpoint')
        self.assertEqual(machine.control['last_stop']['breakpoint_id'], breakpoint['breakpoint_id'])
        self.assertFalse(machine.control['step'])

    def test_step_preserves_interrupt_breakpoint_reason(self):
        machine = HeadlessMachine()
        machine.load(bytes.fromhex('cd 21'))
        breakpoint = machine.interrupt_bp()
        machine.rpc('execution.step')
        machine.pump()
        self.assertEqual(machine.control['last_stop']['kind'], 'breakpoint')
        self.assertEqual(machine.control['last_stop']['breakpoint_id'], breakpoint)
        self.assertFalse(machine.control['step'])

    def test_run_until_can_leave_current_execution_breakpoint(self):
        machine = HeadlessMachine()
        machine.load(b'\x90\x90\x90')
        breakpoint = machine.execution_bp(0x100)
        machine.rpc('execution.continue')
        machine.pump()
        self.assertEqual(machine.control['last_stop']['breakpoint_id'], breakpoint)
        machine.rpc('execution.run_until', predicate={
            'address': {'space': 'segmented', 'segment': 0x1000, 'offset': 0x102}})
        for _ in range(3):
            machine.pump()
        self.assertEqual(machine.control['last_stop']['kind'], 'run_until')
        self.assertEqual(machine.state.GetIP(), 0x102)

    def test_continue_does_not_skip_next_memory_write(self):
        machine = HeadlessMachine()
        machine.load(bytes.fromhex('a3 00 40 a3 00 40 90'))
        breakpoint = machine.rpc('breakpoints.create', kind='memory_write',
                                address={'space': 'linear', 'offset': 0x24000})
        machine.rpc('execution.continue')
        machine.pump()
        self.assertEqual(machine.control['last_stop']['hit_count'], 1)
        machine.rpc('execution.continue')
        machine.pump()
        self.assertTrue(machine.control['paused'])
        self.assertEqual(machine.control['last_stop']['hit_count'], 2)
        self.assertEqual(machine.control['last_stop']['breakpoint_id'], breakpoint['breakpoint_id'])

    def test_trace_fetch_wrap_matches_cpu(self):
        machine = HeadlessMachine()
        machine.state.SetIP(0xffff)
        machine.load(bytes.fromhex('b8 34 12'), offset=0xffff)
        machine.rpc('trace.start', instruction_count=1)
        machine.rpc('execution.step')
        machine.pump()
        event = machine.rpc('trace.read')['events'][0]
        self.assertTrue(event['opcode_hex'].startswith('b83412'), event['opcode_hex'])
        self.assertEqual(machine.state.GetAX(), 0x1234)


class SocketRPCTests(unittest.TestCase):
    def setUp(self):
        self.machine = HeadlessMachine()
        self.server = debugserver.DebugServer(0)
        self.machine.namespace['debug'] = self.server
        self.stop = threading.Event()
        self.errors = []
        def drive():
            try:
                while not self.stop.is_set():
                    self.machine.pump()
            except BaseException as error:
                self.errors.append(error)
        self.worker = threading.Thread(target=drive, daemon=True)
        self.worker.start()
        self.client = socket.create_connection(self.server._listener.getsockname(), timeout=3)
        self.stream = self.client.makefile('rwb', buffering=0)
        self.request_id = 0

    def tearDown(self):
        self.stop.set()
        self.stream.close()
        self.client.close()
        self.server._listener.close()
        self.worker.join(timeout=3)
        self.assertFalse(self.worker.is_alive())
        self.assertEqual(self.errors, [])

    def request(self, method, **params):
        self.request_id += 1
        payload = {'jsonrpc': '2.0', 'id': self.request_id,
                   'method': method, 'params': params}
        self.stream.write(json.dumps(payload).encode() + b'\n')
        response = json.loads(self.stream.readline())
        self.assertEqual(response['id'], self.request_id)
        self.assertNotIn('error', response, response)
        return response['result']

    def test_upload_run_trace_over_real_json_lines_socket(self):
        code = bytes.fromhex('b8 34 12 40 90')
        self.request('memory.write', address=0x10100,
                     data_base64=base64.b64encode(code).decode(),
                     expected_sha256=hashlib.sha256(b'\xff' * len(code)).hexdigest())
        self.request('trace.start', instruction_count=16)
        result = self.request('execution.run_until', predicate={
            'address': {'space': 'segmented', 'segment': 0x1000, 'offset': 0x104}},
            max_emulated_ns=100000)
        for _ in range(100):
            status = self.request('execution.wait', operation_id=result['operation_id'])
            if not status.get('running'):
                break
            time.sleep(0.001)
        self.assertEqual(status['stop_reason']['kind'], 'run_until')
        registers = self.request('state.get_registers')
        self.assertEqual(registers['general']['ax'], 0x1235)
        self.assertEqual(registers['ip'], 0x104)
        trace = self.request('trace.read')
        self.assertEqual(trace['event_count'], 2)
        self.assertEqual(self.request('memory.read', address=0x10100,
                                      length=len(code))['data_hex'], code.hex())

    def test_parse_error_does_not_close_connection(self):
        self.stream.write(b'not-json\n')
        response = json.loads(self.stream.readline())
        self.assertEqual(response['error']['code'], -32700)
        self.assertEqual(self.request('state.get_registers')['ip'], 0x100)


if __name__ == '__main__':
    unittest.main()
