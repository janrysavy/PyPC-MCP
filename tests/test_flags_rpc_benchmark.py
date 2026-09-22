"""Check live flag benchmark accounting against the JSON-RPC contract."""
import unittest

from benchmarks.benchmark_flags_rpc import sample


class Socket:
    def settimeout(self, seconds):
        self.timeout = seconds


class Client:
    def __init__(self, stop_kind='emulated_time_limit'):
        self.sock = Socket()
        self.calls = []
        self.stop_kind = stop_kind
        self.register_reads = 0

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == 'benchmark.mode':
            return {'mode': params['mode']}
        if method == 'state.get_registers':
            self.register_reads += 1
            return ({'state_revision': 10, 'clock': 100} if self.register_reads == 1
                    else {'state_revision': 17, 'clock': 203})
        if method == 'execution.run_until':
            return {'operation_id': 'run-1'}
        if method == 'execution.wait':
            return {'running': False, 'stop_reason': {'kind': self.stop_kind}}
        raise AssertionError(method)


class LiveFlagsBenchmarkTests(unittest.TestCase):
    def test_sample_counts_ticks_and_requires_guest_time_bound(self):
        client = Client()
        result = sample(client, 50_000_000, 2, 'fast')
        self.assertEqual((result['cpu_ticks'], result['clock_cycles']), (7, 103))
        self.assertEqual(result['polls'], 1)
        self.assertEqual(client.calls[2][1]['max_emulated_ns'], 50_000_000)
        self.assertGreater(client.sock.timeout, 0)

    def test_unexpected_stop_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'unexpected guest stop'):
            sample(Client('breakpoint'), 50_000_000, 2, 'fast')


if __name__ == '__main__':
    unittest.main()
