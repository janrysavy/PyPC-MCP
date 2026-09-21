"""The optional A/B wrapper must use the production handlers safely."""
import unittest
from unittest.mock import Mock

from benchmarks.pyro_rpc_ab import ModeController
from debugbreakpoints import BreakpointManager
from test_execution_rpc import HeadlessMachine


class BenchmarkModeTests(unittest.TestCase):
    def test_mode_changes_require_paused_execution(self):
        machine = HeadlessMachine()
        machine.namespace['scr'] = Mock(GetName=lambda: 'RAM-only-test')
        controller = ModeController()
        dispatch = controller.wrap_handler(machine.namespace['handle_debug'])
        self.assertEqual(dispatch({'method': 'benchmark.mode'})['mode'], 'lazy')
        self.assertEqual(dispatch({'method': 'benchmark.mode', 'params': {
            'mode': 'eager'}})['mode'], 'eager')
        dispatch({'method': 'execution.continue'})
        with self.assertRaisesRegex(ValueError, 'pause'):
            dispatch({'method': 'benchmark.mode', 'params': {'mode': 'lazy'}})
        self.assertEqual(controller.mode, 'eager')

    def test_active_traces_and_invalid_modes_are_rejected(self):
        machine = HeadlessMachine()
        machine.namespace['scr'] = Mock(GetName=lambda: 'RAM-only-test')
        dispatch = ModeController().wrap_handler(machine.namespace['handle_debug'])
        with self.assertRaisesRegex(ValueError, 'mode must'):
            dispatch({'method': 'benchmark.mode', 'params': {'mode': 'invalid'}})
        with self.assertRaisesRegex(ValueError, 'params must'):
            dispatch({'method': 'benchmark.mode', 'params': []})
        dispatch({'method': 'trace.start', 'params': {'instruction_count': 1}})
        with self.assertRaisesRegex(ValueError, 'stop CPU'):
            dispatch({'method': 'benchmark.mode'})

    def test_eager_mode_evaluates_both_check_kinds_even_on_miss(self):
        controller = ModeController()
        manager = BreakpointManager()
        for name, prefix in (('check', (0x1000, 0x100)),
                             ('check_interrupt', (0x21, 0x4c, 0))):
            check = controller.wrap_check(getattr(BreakpointManager, name))
            supplier = Mock(return_value={'ax': 0})
            controller.mode = 'lazy'
            self.assertIsNone(check(manager, *prefix, supplier, None))
            supplier.assert_not_called()
            controller.mode = 'eager'
            self.assertIsNone(check(manager, *prefix, supplier, None))
            supplier.assert_called_once_with()
            self.assertIsNone(check(manager, *prefix, registers={'ax': 0}))


if __name__ == '__main__':
    unittest.main()
