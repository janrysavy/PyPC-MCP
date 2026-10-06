"""Measure coherent vs separate observations over one persistent local TCP socket.

The profile is a paused real 8088 + CGA test machine, not a booted game or a
claim about guest execution throughput. Window lengths mirror Pyro's RE driver.
"""
import argparse
import json
from pathlib import Path
import socket
import statistics
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import debugserver
from tests.test_observation_rpc import machine


def benchmark(repeats=100, warmup=25):
    m, _ = machine()
    server = debugserver.DebugServer(0)
    done = threading.Event()
    def service():
        while not done.is_set():
            if server.has_pending():
                server.process_pending(m.namespace['handle_debug'])
            else:
                done.wait(0.0001)
    worker = threading.Thread(target=service, daemon=True)
    worker.start()
    client = socket.create_connection(server._listener.getsockname(), timeout=5)
    reader = client.makefile('rb')
    sequence = 0
    def call(method, **params):
        nonlocal sequence
        sequence += 1
        request = {'jsonrpc':'2.0', 'id':sequence, 'method':method, 'params':params}
        client.sendall(json.dumps(request, separators=(',', ':')).encode() + b'\n')
        response = json.loads(reader.readline())
        if 'error' in response:
            raise RuntimeError(response['error'])
        return response['result']
    windows = [{'address':0x26000, 'length':32768},
               {'address':0xb8000, 'length':4000},
               {'address':0x449, 'length':30},
               {'address':0x417, 'length':39}]
    def separate():
        return {'state_revision':0, 'registers':call('state.get_registers'),
                'memory':[call('memory.read', **w) for w in windows],
                'video_text':call('video.text')}
    def coherent():
        return call('state.observe', expected_state_revision=0,
                    memory=windows, video_text={})
    try:
        original = separate()
        assert coherent() == original
        for _ in range(warmup):
            separate(); coherent()
        timings = {'separate':[], 'coherent':[]}
        # Alternate order to reduce warmup/drift bias.
        for index in range(repeats):
            order = (('separate', separate), ('coherent', coherent))
            if index & 1:
                order = tuple(reversed(order))
            for name, run in order:
                start = time.perf_counter()
                result = run()
                timings[name].append(time.perf_counter() - start)
                assert result == original
        medians = {name:statistics.median(values) for name, values in timings.items()}
        return {'profile':'paused RAM-only 8088 + CGA, persistent localhost JSON-lines TCP; no booted game',
                'python':sys.version, 'window_bytes':[w['length'] for w in windows],
                'warmup_pairs':warmup, 'repeats':repeats, 'response_equality':True,
                'rpc_calls_per_observation':{'separate':6,'coherent':1},
                'median_seconds':medians, 'separate_over_coherent':medians['separate']/medians['coherent'],
                'samples_seconds':timings, 'final_revision':m.control['revision'],
                'final_clock':m.state.GetClock()}
    finally:
        reader.close(); client.close(); done.set(); worker.join(timeout=5)
        server._listener.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=100)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    result = benchmark(args.repeats)
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + '\n', encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k != 'samples_seconds'}, indent=2))
