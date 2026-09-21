"""Lazy register snapshots must not change breakpoint matching semantics."""
import unittest
from unittest.mock import Mock

from debugbreakpoints import BreakpointManager
from test_execution_rpc import HeadlessMachine


class LazyRegisterTests(unittest.TestCase):
    def setUp(self):
        self.manager = BreakpointManager()
        self.address = {'space': 'segmented', 'segment': 0x1000, 'offset': 0x100}

    def create(self, **extra):
        return self.manager.create({'address': self.address, **extra})['breakpoint_id']

    def check(self, registers, offset=0x100, skip_id=None):
        return self.manager.check(0x1000, offset, registers, skip_id)

    def test_address_miss_and_unconditional_hit_do_not_read_registers(self):
        supplier = Mock(side_effect=AssertionError('unnecessary register snapshot'))
        bp = self.create()
        self.assertIsNone(self.check(supplier, offset=0x101))
        self.assertEqual(self.check(supplier)['breakpoint_id'], bp)
        self.assertIsNone(self.check(supplier, skip_id=bp))
        supplier.assert_not_called()

    def test_conditional_address_miss_does_not_read_registers(self):
        self.create(condition={'register': 'ax', 'operator': 'eq', 'value': 42})
        supplier = Mock(side_effect=AssertionError('unnecessary register snapshot'))
        self.assertIsNone(self.check(supplier, offset=0x101))
        supplier.assert_not_called()

    def test_shared_snapshot_is_evaluated_once_and_not_cached_between_checks(self):
        false_bp = self.create(condition={'register': 'ax', 'operator': 'eq', 'value': 1})
        true_bp = self.create(condition={'register': 'ax', 'operator': 'eq', 'value': 2})
        supplier = Mock(return_value={'ax': 2})
        self.assertEqual(self.check(supplier)['breakpoint_id'], true_bp)
        supplier.assert_called_once_with()
        self.assertEqual([bp['hit_count'] for bp in self.manager.list()], [0, 1])
        supplier.return_value = {'ax': 1}
        self.assertEqual(self.check(supplier)['breakpoint_id'], false_bp)
        self.assertEqual(supplier.call_count, 2)

    def test_mapping_compatibility_filter_and_once(self):
        bp = self.create(condition={'register': 'ax', 'operator': 'eq', 'value': 42},
                         hit_filter={'skip': 1, 'every': 2}, once=True)
        self.assertIsNone(self.check({'ax': 41}))
        self.assertIsNone(self.check({'ax': 42}))
        result = self.check({'ax': 42})
        self.assertEqual((result['breakpoint_id'], result['hit_count']), (bp, 2))
        self.assertFalse(self.manager.contains(bp))

    def test_interrupt_filters_and_unconditional_hit_are_lazy(self):
        bp = self.manager.create({'kind': 'interrupt', 'event': {
            'type': 'software_interrupt', 'number': 0x21, 'ah': 0x4c, 'al': 0}})['breakpoint_id']
        supplier = Mock(side_effect=AssertionError('unnecessary register snapshot'))
        for number, ah, al in ((0x10, 0x4c, 0), (0x21, 0x3d, 0), (0x21, 0x4c, 1)):
            self.assertIsNone(self.manager.check_interrupt(number, ah, al, supplier))
        self.assertIsNone(self.manager.check_interrupt(0x21, 0x4c, 0, supplier, bp))
        self.assertEqual(self.manager.check_interrupt(0x21, 0x4c, 0, supplier)['breakpoint_id'], bp)
        supplier.assert_not_called()

    def test_interrupt_conditions_share_one_snapshot(self):
        ids = []
        for value in (1, 2):
            ids.append(self.manager.create({'kind': 'interrupt', 'event': {
                'type': 'software_interrupt', 'number': 0x21}, 'condition': {
                'register': 'bx', 'operator': 'eq', 'value': value}})['breakpoint_id'])
        supplier = Mock(return_value={'bx': 2})
        result = self.manager.check_interrupt(0x21, 0x4c, 0, supplier)
        self.assertEqual(result['breakpoint_id'], ids[1])
        supplier.assert_called_once_with()

    def test_production_loop_does_not_build_snapshot_on_miss(self):
        machine = HeadlessMachine()
        machine.load(b'\x90')
        machine.execution_bp(0x200)
        machine.namespace['rpc_flat_registers'] = Mock(side_effect=AssertionError('eager snapshot'))
        machine.rpc('execution.continue')
        machine.pump()
        self.assertEqual(machine.state.GetIP(), 0x101)
        machine.namespace['rpc_flat_registers'].assert_not_called()

    def test_production_loop_uses_fresh_condition_registers(self):
        machine = HeadlessMachine()
        machine.load(bytes.fromhex('40 eb fd'))  # inc ax; jmp 0100
        machine.rpc('breakpoints.create', address=self.address,
                    condition={'register': 'ax', 'operator': 'eq', 'value': 2})
        machine.rpc('execution.continue')
        for _ in range(5):
            machine.pump()
        self.assertTrue(machine.control['paused'])
        self.assertEqual(machine.state.GetIP(), 0x100)
        self.assertEqual(machine.state.GetAX(), 2)


if __name__ == '__main__':
    unittest.main()
