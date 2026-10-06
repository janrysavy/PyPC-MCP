"""Read-only coherent capture at a real CPU instruction boundary."""
import base64
import copy
import pickle
import hashlib
import json
import unittest
import ast
from pathlib import Path

import bus
import cga
import rom
import vga
from tests.test_execution_rpc import HeadlessMachine
from tests import test_execution_rpc as execution_tests


def machine(video='cga'):
    display = vga.VGA(False) if video == 'vga' else cga.CGA(False)
    m = HeadlessMachine()
    m.memory._devices.append(display)
    m.memory.RecreateCache()
    m.namespace.update(scr=display, rom=rom)
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'main.py').read_text())
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('ReadTextScreen', 'ReadTextCells')]
    exec(compile(ast.Module(body=functions, type_ignores=[]), 'main.py', 'exec'), m.namespace)
    return m, display


class ObservationTests(unittest.TestCase):
    def test_equal_separate_reads_preserve_captured_state(self):
        for adapter in ('cga', 'vga'):
            with self.subTest(adapter=adapter):
                m, video = machine(adapter)
                m.load(bytes.fromhex('a3 00 40'))
                video.WriteByte(0xb8000, 65)
                video.WriteByte(0xb8001, 31)
                windows = [{'address': {'space': 'segmented', 'segment': '0x1000', 'offset': 0x100}, 'length': 3},
                           {'address': 0xb8000, 'length': 4000},
                           {'address': 0x400, 'length': 69}]
                before = (m.state.GetClock(), m.state.GetIP(), bytes(m.memory._m._m),
                          pickle.dumps(video.__dict__), copy.deepcopy(m.control))
                result = m.rpc('state.observe', expected_state_revision=0, memory=windows,
                               video_text={}, video_memory=True)
                self.assertEqual(result['registers'], m.rpc('state.get_registers'))
                self.assertEqual(result['memory'], [m.rpc('memory.read', **w) for w in windows])
                self.assertEqual(result['video_text'], m.rpc('video.text'))
                self.assertEqual(base64.b64decode(result['video_memory']['data_base64']), bytes(video._ram))
                after = (m.state.GetClock(), m.state.GetIP(), bytes(m.memory._m._m),
                         pickle.dumps(video.__dict__), m.control)
                self.assertEqual(before, after)
                self.assertEqual({x['state_revision'] for x in result['memory']}, {0})

    def test_preflight_later_invalid_windows_and_options_without_reading(self):
        m, video = machine('vga')
        original = bytes(video._ram)
        def forbidden(*args):
            self.fail('bus/device read invoked during observation')
        m.memory.ReadByte = forbidden
        video.ReadByte = forbidden
        bad = [dict(memory=[{'address': 0x100, 'length': 2}, {'address': 0xfffff, 'length': 2}]),
               dict(memory=[{'address': 0xa0000}]),
               dict(memory=[{'address': 0x100, 'length': 65536}, {'address': 0}]),
               dict(memory=[{'address': 0}] * 17), dict(memory={}),
               dict(memory=[{'address': True}]), dict(memory=[{'address': 0, 'length': 0}]),
               dict(memory=[{'address': 0, 'length': True}]), dict(memory=[None]),
               dict(video_text={'page': 999}), dict(video_text={'page': True}),
               dict(video_text=None), dict(video_text={'unknown': 0}),
               dict(video_memory=1), dict(video_memory=None), dict(unknown=True)]
        for params in bad:
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    m.rpc('state.observe', expected_state_revision=0, **params)
                self.assertEqual(bytes(video._ram), original)
                self.assertEqual(m.control['revision'], 0)
                self.assertEqual(m.state.GetClock(), 0)
        result = m.rpc('state.observe', expected_state_revision=0,
                      memory=[{'address': 0xb8000, 'length': 4000}])
        self.assertEqual(result['memory'][0]['sha256'], hashlib.sha256(original[:4000]).hexdigest())

    def test_cga_mirror_and_crossing_wrap_preserve_bus_priority(self):
        m, video = machine()
        video._ram[-2:] = b'AB'
        video._ram[:3] = b'CDE'
        for address, length, wanted in ((0xbc000, 3, b'CDE'),
                                        (0xbbffe, 5, b'ABCDE'),
                                        (0xbfffe, 2, b'AB')):
            with self.subTest(address=address):
                observed = m.rpc('state.observe', expected_state_revision=0,
                                 memory=[{'address':address, 'length':length}])['memory'][0]
                self.assertEqual(observed, m.rpc('memory.read', address=address, length=length))
                self.assertEqual(observed['data_hex'], wanted.hex())
        image = rom.Rom.__new__(rom.Rom)
        image._offset, image._contents = 0xbc000, [90]
        m.memory._devices.insert(0, image)
        m.memory.RecreateCache()
        observed = m.rpc('state.observe', expected_state_revision=0,
                         memory=[{'address':0xbbffe,'length':5}])['memory'][0]
        self.assertEqual(observed['data_hex'], b'ABZDE'.hex())

    def test_paused_and_revision_guards(self):
        m, _ = machine()
        for expected in (None, True, 1, -1):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                m.rpc('state.observe', expected_state_revision=expected)
        m.control['paused'] = False
        with self.assertRaisesRegex(ValueError, 'paused'):
            m.rpc('state.observe', expected_state_revision=0)

    def test_rom_and_overlay_priority(self):
        m, _ = machine()
        image = rom.Rom.__new__(rom.Rom)
        image._offset = 0x18000
        image._contents = [1, 2, 3]
        m.memory._roms.append(image)
        m.memory.RecreateCache()
        m.memory._m._m[0x17ffe:0x18005] = b'ABCDEFG'
        result = m.rpc('state.observe', expected_state_revision=0,
                      memory=[{'address': 0x17ffe, 'length': 7}])
        self.assertEqual(result['memory'][0]['data_hex'], b'AB\x01\x02\x03FG'.hex())
        # A device at the same ROM address has first priority and must refuse.
        class ConsumingDevice:
            def GetAddressList(self): return [(0x18000, 1)]
            def GetWaitStateCycles(self): return 0
            def ReadByte(self, address): raise AssertionError('consumed device')
        m.memory._devices.insert(0, ConsumingDevice())
        m.memory.RecreateCache()
        with self.assertRaisesRegex(ValueError, 'safe peek'):
            m.rpc('state.observe', expected_state_revision=0,
                  memory=[{'address': 0x17ffe, 'length': 7}])

    def test_step_then_observe_uses_new_epoch(self):
        m, _ = machine()
        m.load(b'\x90')
        m.rpc('execution.step'); m.pump()
        with self.assertRaisesRegex(ValueError, 'revision'):
            m.rpc('state.observe', expected_state_revision=0)
        result = m.rpc('state.observe', expected_state_revision=1)
        self.assertEqual(result['registers']['ip'], 0x101)
        self.assertEqual(result['registers']['state_revision'], 1)

    def test_maximum_total_and_optional_omission(self):
        m, _ = machine()
        result = m.rpc('state.observe', expected_state_revision=0,
                      memory=[{'address': 0, 'length': 65536}])
        self.assertEqual(result['memory'][0]['byte_count'], 65536)
        self.assertNotIn('video_memory', result)
        self.assertNotIn('video_text', result)


class ObservationSocketTests(unittest.TestCase):
    setUp = execution_tests.SocketRPCTests.setUp
    tearDown = execution_tests.SocketRPCTests.tearDown
    request = execution_tests.SocketRPCTests.request

    def test_invalid_request_then_atomic_capture_on_same_connection(self):
        self.machine.namespace.update(scr=cga.CGA(False), rom=rom)
        self.stream.write(json.dumps({'jsonrpc':'2.0', 'id':'invalid',
                         'method':'state.observe',
                         'params':{'expected_state_revision':1}}).encode() + b'\n')
        error = json.loads(self.stream.readline())
        self.assertEqual(error['error']['code'], -32602)
        result = self.request('state.observe', expected_state_revision=0,
                              memory=[{'address':0x10100,'length':2}])
        self.assertEqual(result['memory'][0]['data_hex'], 'ffff')
        self.assertEqual(result['registers']['ip'], 0x100)
        self.assertEqual(result['state_revision'], 0)




def test_observation_preserves_complete_configured_machine(tmp_path):
    from tests.test_machine_snapshot_rpc import rpc_machine
    from machinecodec import capture_machine
    from statecodec import dump_cpu_state
    h, cpu, _ = rpc_machine(tmp_path)
    helpers, _ = machine()
    h.namespace.update(rom=rom,
                       ReadTextScreen=helpers.namespace['ReadTextScreen'],
                       ReadTextCells=helpers.namespace['ReadTextCells'])
    before = capture_machine(cpu)
    control = copy.deepcopy(h.control)
    result = h.rpc('state.observe', expected_state_revision=0,
                   memory=[{'address':0x10000,'length':32},
                           {'address':0xb8000,'length':4000}],
                   video_text={}, video_memory=True)
    assert capture_machine(cpu) == before
    assert h.control == control
    assert dump_cpu_state(cpu.GetState()) == before[0]['cpu']
    assert result['registers'] == h.rpc('state.get_registers')
    # The board fixture owns real PIC/PIT/keyboard/DMA/XTIDE/video devices,
    # host RNG and pending disk transfers, all authenticated by capture_machine.

if __name__ == '__main__':
    unittest.main()
