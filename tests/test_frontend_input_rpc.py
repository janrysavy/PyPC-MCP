"""Actual frontend input callbacks under the production launch ownership policy."""
import ast
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import telnet
import vncserver
from machinecodec import capture_machine
from tests.machine_fixture import rpc_machine
from tests.test_execution_rpc import HeadlessMachine


def frontends(kb, no_rpc, display):
    path = Path(__file__).resolve().parents[1] / 'main.py'
    tree = ast.parse(path.read_text())
    body = next(n.body for n in tree.body if isinstance(n, ast.Try))
    names = ('frontend_keyboard', 't', 'v', 'debug')
    assignments = [n for n in body if isinstance(n, ast.Assign) and
                   any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
    assert len(assignments) == len(names)
    namespace = dict(kb=kb, arguments=SimpleNamespace(no_rpc=no_rpc, telnet_port=2300,
                                                    vnc_port=5902, rpc_port=2301),
                     frontend_display=display, vnc_display=display,
                     telnet=telnet, vncserver=vncserver,
                     debugserver=SimpleNamespace(DebugServer=lambda port: ('rpc', port)))
    # Keep real constructors/key maps, but do not launch listeners for this owner test.
    with patch('threading.Thread.start'):
        exec(compile(ast.Module(body=assignments, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['t'], namespace['v'], namespace['debug']


class FrontendInputTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / '.test-tmp'
        scratch.mkdir(exist_ok=True)
        temporary = TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.h, self.cpu, vnc = rpc_machine(self.root)
        self.kb = self.cpu._devices[1]
        self.h.namespace['kb'] = self.kb
        self.display = vnc._display

    def test_controlled_frontends_cannot_mutate_machine_or_stale_guards(self):
        t, v, debug = frontends(self.kb, False, self.display)
        self.assertEqual(debug, ('rpc', 2301))
        before = capture_machine(self.cpu)
        controls = copy.deepcopy(self.h.control)
        def all_vnc_keys():
            for key in v._key_map:
                v.PushChar(key, True)
                v.PushChar(key, False)
        for producer in (lambda: t.push(ord('a')),
                         all_vnc_keys):
            producer()
            self.assertEqual(capture_machine(self.cpu), before)
            self.assertEqual(self.h.control, controls)
        self.assertEqual(self.h.rpc('state.observe', expected_state_revision=0)['state_revision'], 0)
        self.h.rpc('machine.snapshot.export', path=str(self.root / 'unchanged.pypc'),
                   expected_state_revision=0)
        self.assertEqual(capture_machine(self.cpu), before)

    def test_controlled_rpc_keyboard_still_changes_epoch_and_guest_queue(self):
        frontends(self.kb, False, self.display)
        accepted = self.h.rpc('input.keyboard', events=[{'scan_code': 77},
                                                       {'scan_code': 77, 'pressed': False}])
        self.assertEqual(accepted, {'accepted': 2, 'state_revision': 1})
        self.assertEqual(list(self.kb._keyboard_buffer.queue)[-2:], [77, 205])
        with self.assertRaisesRegex(ValueError, 'expected state revision'):
            self.h.rpc('state.observe', expected_state_revision=0)

    def test_uncontrolled_frontends_retain_normal_keyboard_input(self):
        t, v, debug = frontends(self.kb, True, self.display)
        self.assertIsNone(debug)
        before = capture_machine(self.cpu)
        t.push(ord('a'))
        v.PushChar(ord('a'), True)
        v.PushChar(ord('a'), False)
        self.assertEqual(list(self.kb._keyboard_buffer.queue)[-4:], [30, 158, 30, 158])
        v.PushChar(0xffe1, True)
        v.PushChar(0xffe1, False)
        self.assertEqual(list(self.kb._keyboard_buffer.queue)[-2:], [42, 170])
        self.assertNotEqual(capture_machine(self.cpu)[0]['keyboard'], before[0]['keyboard'])

    def test_cpu_loop_runs_without_rpc_listener(self):
        h = HeadlessMachine()
        h.load(bytes.fromhex('eb fe'))
        h.namespace['debug'] = None
        h.control['paused'] = False
        h.pump()
        self.assertEqual(h.control['revision'], 64)
        self.assertGreater(h.state.GetClock(), 0)


if __name__ == '__main__':
    unittest.main()
