"""Validate the real main.py input handler without booting ROMs or opening ports."""
import ast
from pathlib import Path
import unittest
import keyboard


def input_handler():
    source = Path(__file__).resolve().parents[1] / 'main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
    names = {'rpc_params', 'rpc_number', 'handle_debug'}
    functions = [node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    if {node.name for node in functions} != names:
        raise AssertionError('debugger handler functions were not found')
    kb = keyboard.Keyboard()
    namespace = {'kb': kb, 'control': {'revision': 123}}
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), 'exec'), namespace)
    return kb, namespace['handle_debug']


class KeyboardRPCValidationTests(unittest.TestCase):
    def setUp(self):
        self.kb, self.handler = input_handler()

    def call(self, params, method='input.keyboard'):
        return self.handler({'method': method, 'params': params})

    def snapshot(self):
        return (self.kb.GetPressedScancodes(), list(self.kb._keyboard_buffer.queue))

    def test_invalid_second_event_has_no_side_effects(self):
        bad_events = (None, [], 1, {}, {'scan_code': 128}, {'scan_code': -1},
                      {'scan_code': True}, {'scan_code': 'not-a-number'},
                      {'scan_code': 30, 'pressed': 1},
                      {'scan_code': 30, 'pressed': 'yes'})
        for bad_event in bad_events:
            with self.subTest(event=bad_event):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.call({'events': [{'scan_code': 42}, bad_event]})
                self.assertEqual(self.snapshot(), before)

    def test_rejected_batch_does_not_release_an_existing_key(self):
        self.kb.PushKeyboardScancode(42)
        before = self.snapshot()
        with self.assertRaises(ValueError):
            self.call({'events': [{'scan_code': 42, 'pressed': False},
                                  {'scan_code': 128}]})
        self.assertEqual(self.snapshot(), before)

    def test_valid_batch_preserves_make_break_order(self):
        result = self.call({'events': [{'scan_code': '0x2a'}, {'scan_code': 30},
                                      {'scan_code': 30, 'pressed': False},
                                      {'scan_code': 42, 'pressed': False}]})
        self.assertEqual(result, {'accepted': 4, 'state_revision': 123})
        self.assertEqual(self.snapshot(), ([], [42, 30, 158, 170]))

    def test_single_event_alias_and_default_pressed(self):
        self.call({'scan_code': 30}, method='keyboard.scancode')
        self.call({'scan_code': 30, 'pressed': False})
        self.assertEqual(self.snapshot(), ([], [30, 158]))

    def test_batch_limits(self):
        for events in ([], 'bad', [{'scan_code': 30}] * 33):
            with self.subTest(events=events):
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.call({'events': events})
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.call({'events': [{'scan_code': 30}] * 32})['accepted'], 32)
        self.assertEqual(self.snapshot(), ([30], [30] * 32))


if __name__ == '__main__':
    unittest.main()
