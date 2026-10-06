"""Shared stepped-execution and real-mode addressing contract."""
import unittest
from tests.test_execution_rpc import HeadlessMachine
from tests.test_observation_rpc import machine


class SharedContractTests(unittest.TestCase):
    def test_step_acknowledgement_is_entry_and_wait_is_completion(self):
        m = HeadlessMachine(); m.load(b'\x90\x90')
        accepted = m.rpc('execution.step')
        self.assertEqual(accepted['ip'], 0x100)
        self.assertTrue(accepted['stepping'])
        self.assertEqual(m.rpc('execution.wait', operation_id=accepted['operation_id']), {'running':True})
        with self.assertRaises(ValueError):
            m.rpc('execution.step')
        with self.assertRaises(ValueError):
            m.rpc('execution.continue')
        m.pump()
        done = m.rpc('execution.wait', operation_id=accepted['operation_id'])
        self.assertEqual(done['stop_reason']['kind'], 'step')
        self.assertEqual(done['stop_reason']['registers']['ip'], 0x101)
        self.assertEqual(done['stop_reason']['registers']['state_revision'], 1)

    def test_step_pause_completion_preserves_entry(self):
        m = HeadlessMachine(); m.load(b'\x90')
        accepted = m.rpc('execution.step')
        m.rpc('execution.pause')
        done = m.rpc('execution.wait', operation_id=accepted['operation_id'])
        self.assertEqual(done['stop_reason']['kind'], 'pause')
        self.assertEqual(done['stop_reason']['registers']['ip'], 0x100)

    def test_step_preserves_actual_watchpoint_completion(self):
        for kind in ('memory_write', 'interrupt'):
            with self.subTest(kind=kind):
                m = HeadlessMachine()
                if kind == 'memory_write':
                    m.load(bytes.fromhex('a3 00 40'))
                    bp = m.rpc('breakpoints.create', kind=kind, address={'space':'linear','offset':0x24000})
                else:
                    m.load(bytes.fromhex('cd 21'))
                    bp = m.rpc('breakpoints.create', kind=kind,
                               event={'type':'software_interrupt', 'number':0x21})
                accepted = m.rpc('execution.step'); m.pump()
                done = m.rpc('execution.wait', operation_id=accepted['operation_id'])
                self.assertEqual(done['stop_reason']['kind'], 'breakpoint')
                self.assertEqual(done['stop_reason']['breakpoint_id'], bp['breakpoint_id'])

    def test_segmented_word_validation_and_20bit_wrap(self):
        m, _ = machine()
        m.memory._m._m[0] = 42
        wrapped = {'space':'segmented','segment':'0xffff','offset':'0x10'}
        self.assertEqual(m.rpc('memory.read', address=wrapped)['data_hex'], '2a')
        self.assertEqual(m.rpc('state.observe', expected_state_revision=0,
                              memory=[{'address':wrapped}])['memory'][0]['address'], 0)
        for segment, offset in ((-1,16),(0x10000,0),(1,-1),(0,0x10000),(True,0),(0,False)):
            address = {'space':'segmented','segment':segment,'offset':offset}
            with self.subTest(address=address), self.assertRaises(ValueError):
                m.rpc('memory.read', address=address)
        for address in (-1,0x100000):
            with self.subTest(address=address), self.assertRaises(ValueError):
                m.rpc('memory.read', address=address)


if __name__ == '__main__':
    unittest.main()
