"""Single-step must not overwrite memory or interrupt breakpoint stops."""
import ast
from pathlib import Path
import unittest


def finish_step_function(control):
    source = Path(__file__).resolve().parents[1] / 'main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == 'rpc_finish_step'
    ]
    if len(functions) != 1:
        raise AssertionError('rpc_finish_step was not found exactly once')
    namespace = {'control': control}
    module = ast.Module(body=functions, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(source), 'exec'), namespace)
    return namespace['rpc_finish_step']


class StepStopPriorityTests(unittest.TestCase):
    def test_plain_step_reports_step_and_consumes_request(self):
        control = {'step': True}
        finish = finish_step_function(control)
        self.assertTrue(finish(None, None))
        self.assertFalse(control['step'])

    def test_memory_breakpoint_wins_over_step(self):
        control = {'step': True}
        finish = finish_step_function(control)
        self.assertFalse(finish({'breakpoint_id': 'bp-1'}, None))
        self.assertFalse(control['step'])

    def test_interrupt_breakpoint_wins_over_step(self):
        control = {'step': True}
        finish = finish_step_function(control)
        self.assertFalse(finish(None, {'breakpoint_id': 'bp-2'}))
        self.assertFalse(control['step'])

    def test_no_pending_step_is_unchanged(self):
        control = {'step': False}
        finish = finish_step_function(control)
        self.assertFalse(finish(None, None))
        self.assertFalse(control['step'])


if __name__ == '__main__':
    unittest.main()
