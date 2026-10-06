"""Side-effecting RPC operations must invalidate guarded complete-machine captures."""
import base64
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from checkpointbundle import read_bundle
from machinecodec import capture_machine
from tests.machine_fixture import rpc_machine
from tests.test_execution_rpc import HeadlessMachine
from tests import test_execution_rpc as execution_tests
from uart8250 import UART8250


class DeviceIOEpochTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / '.test-tmp'
        scratch.mkdir(exist_ok=True)
        temporary = TemporaryDirectory(dir=scratch)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.h, self.cpu, _ = rpc_machine(self.root, com1=True)
        self.uart = self.cpu._devices[5]

    def assert_stale_refusals_preserve_complete_state(self, old_revision, archive):
        before = capture_machine(self.cpu)
        controls = copy.deepcopy(self.h.control)
        with self.assertRaisesRegex(ValueError, 'expected state revision'):
            self.h.rpc('state.observe', expected_state_revision=old_revision)
        for method in ('export', 'import'):
            with self.assertRaisesRegex(ValueError, 'expected state revision'):
                self.h.rpc('machine.snapshot.' + method,
                           path=archive['path'] if method == 'import' else str(self.root / 'stale.pypc'),
                           expected_sha256=archive['sha256'], disk_root=str(self.root / 'refused'),
                           expected_state_revision=old_revision)
            self.assertEqual(capture_machine(self.cpu), before)
            self.assertEqual(self.h.control, controls)
            self.assertFalse((self.root / 'stale.pypc').exists())
            self.assertFalse((self.root / 'refused').exists())

    def test_serial_enqueue_and_consuming_port_read_invalidate_snapshot_tokens(self):
        archive = self.h.rpc('machine.snapshot.export', path=str(self.root / 'original.pypc'),
                             expected_state_revision=0)
        before = self.h.rpc('state.get_registers')
        accepted = self.h.rpc('serial.write', data_base64=base64.b64encode(b'R\r').decode())
        self.assert_stale_refusals_preserve_complete_state(0, archive)
        self.assertEqual(accepted, {'accepted': 2, 'rx_pending': 2, 'state_revision': 1})
        self.assertEqual(list(self.uart.rx), [ord('R'), 13])
        read = self.h.rpc('io.read', port='0x3f8')
        self.assertEqual(list(self.uart.rx), [13])
        self.assert_stale_refusals_preserve_complete_state(1, archive)
        self.assertEqual(read, {'port': 0x3f8, 'value': ord('R'), 'state_revision': 2})
        current = self.h.rpc('state.observe', expected_state_revision=2)
        self.assertEqual(current['registers']['clock'], before['clock'])
        self.assertEqual(current['registers']['ip'], before['ip'])
        fresh = self.h.rpc('machine.snapshot.export', path=str(self.root / 'fresh.pypc'),
                           expected_state_revision=2)
        manifest, _ = read_bundle(fresh['path'], fresh['sha256'])
        self.assertEqual(manifest['serial'], capture_machine(self.cpu)[0]['serial'])

    def test_rejected_serial_and_port_parameters_preserve_complete_state(self):
        before = capture_machine(self.cpu)
        controls = copy.deepcopy(self.h.control)
        invalid = [('serial.write', {'data_base64': data})
                   for data in (None, '', '!', 'A' * 5465)]
        invalid.extend(('io.read', {'port': port}) for port in (None, True, -1, 65536))
        for method, params in invalid:
            with self.subTest(method=method, params=params), self.assertRaises(ValueError):
                self.h.rpc(method, **params)
            self.assertEqual(capture_machine(self.cpu), before)
            self.assertEqual(self.h.control, controls)
        for _ in range(16):
            self.uart.host_write(bytes(4096))
        full = capture_machine(self.cpu)
        with self.assertRaisesRegex(ValueError, 'queue full'):
            self.h.rpc('serial.write', data_base64='QQ==')
        self.assertEqual(capture_machine(self.cpu), full)
        self.assertEqual(self.h.control, controls)
        self.uart.IO_Write(0x3fc, 16)
        loopback = capture_machine(self.cpu)
        with self.assertRaisesRegex(ValueError, 'loopback mode'):
            self.h.rpc('serial.write', data_base64='QQ==')
        self.assertEqual(capture_machine(self.cpu), loopback)
        self.assertEqual(self.h.control, controls)

    def test_running_device_calls_preserve_execution_operation_and_cpu_boundary(self):
        operation = self.h.rpc('execution.continue')
        before = self.h.rpc('state.get_registers')
        accepted = self.h.rpc('serial.write', data_base64='QQ==')
        self.assertEqual(accepted['state_revision'], before['state_revision'] + 1)
        read = self.h.rpc('io.read', port=0x3f8)
        self.assertEqual(read['state_revision'], before['state_revision'] + 2)
        self.assertEqual(read['value'], ord('A'))
        self.assertFalse(self.h.control['paused'])
        self.assertEqual(self.h.rpc('execution.wait', operation_id=operation['operation_id']),
                         {'running': True})
        after = self.h.rpc('state.get_registers')
        self.assertEqual(after['clock'], before['clock'])
        self.assertEqual(after['ip'], before['ip'])

    def test_successful_port_read_advances_conservatively_without_inferred_purity(self):
        before = capture_machine(self.cpu)
        read = self.h.rpc('io.read', port=0x3fd)
        self.assertEqual(read, {'port': 0x3fd, 'value': 0x60, 'state_revision': 1})
        self.assertEqual(capture_machine(self.cpu), before)
        with self.assertRaisesRegex(ValueError, 'expected state revision'):
            self.h.rpc('state.observe', expected_state_revision=0)


class DeviceIORunningSocketTests(unittest.TestCase):
    # Use the same JSON-lines socket and production-loop worker as execution tests.
    tearDown = execution_tests.SocketRPCTests.tearDown
    request = execution_tests.SocketRPCTests.request

    def setUp(self):
        uart = UART8250()
        machine = HeadlessMachine(devices=[uart], run_IO=True)
        machine.namespace['serial'] = uart
        execution_tests.SocketRPCTests.setUp(self, machine=machine)

    def test_device_requests_are_serviced_while_cpu_actually_runs(self):
        self.machine.load(bytes.fromhex('eb fe'))
        operation = self.request('execution.continue')
        accepted = self.request('serial.write', data_base64='Ug0=')
        self.assertEqual(accepted['accepted'], 2)
        self.assertGreater(accepted['state_revision'], operation['state_revision'])
        read = self.request('io.read', port=0x3f8)
        self.assertEqual(read['value'], ord('R'))
        self.assertGreater(read['state_revision'], accepted['state_revision'])
        self.assertEqual(self.request('execution.wait', operation_id=operation['operation_id']),
                         {'running': True})
        registers = self.request('state.get_registers')
        self.assertFalse(self.machine.control['paused'])
        self.assertGreater(registers['clock'], operation['clock'])
        self.request('execution.pause')
        self.assertEqual(list(self.machine.namespace['serial'].rx), [13])


if __name__ == '__main__':
    unittest.main()
