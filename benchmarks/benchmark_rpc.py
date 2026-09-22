#!/usr/bin/env python3
"""Measure bounded guest execution through the real JSON-lines TCP debugger.

Launch benchmarks/rpc_ab.py, boot a DOS application in a scratch mount, reach
the workload,
pause it, then run this client. A/B samples alternate AB/BA without rebooting.
The game advances between samples: this is NOT an identical-state replay.
"""
import argparse
import json
import platform
import socket
import statistics
import sys
import time


class Client:
    def __init__(self, host, port, timeout):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.stream = self.sock.makefile('rwb', buffering=0)
        self.request_id = 0

    def call(self, method, **params):
        self.request_id += 1
        self.stream.write(json.dumps({'jsonrpc': '2.0', 'id': self.request_id,
                                     'method': method, 'params': params}).encode() + b'\n')
        line = self.stream.readline()
        if not line:
            raise RuntimeError('debugger connection closed')
        response = json.loads(line)
        if response.get('id') != self.request_id:
            raise RuntimeError('mismatched JSON-RPC response id')
        if 'error' in response:
            raise RuntimeError(response['error'])
        return response['result']

    def close(self):
        self.stream.close()
        self.sock.close()


def sample(client, duration_ns, timeout, mode):
    client.call('benchmark.mode', mode=mode)
    before = client.call('state.get_registers')
    start = time.perf_counter()
    operation = client.call('execution.run_until', predicate={'address': {
        'space': 'segmented', 'segment': 0xffff, 'offset': 0xffff}},
        max_emulated_ns=duration_ns)
    deadline = start + timeout
    polls = 0
    while True:
        status = client.call('execution.wait', operation_id=operation['operation_id'])
        polls += 1
        if not status.get('running'):
            break
        if time.perf_counter() >= deadline:
            client.call('execution.pause')
            raise TimeoutError('guest execution did not finish before the deadline')
        time.sleep(0.002)
    wall = time.perf_counter() - start
    stop = status.get('stop_reason', {})
    if stop.get('kind') != 'emulated_time_limit':
        raise RuntimeError(f'unexpected guest stop: {stop}')
    after = client.call('state.get_registers')
    ticks = after['state_revision'] - before['state_revision']
    clocks = after['clock'] - before['clock']
    return {'mode': mode, 'wall_seconds': wall, 'cpu_ticks': ticks,
            'clock_cycles': clocks, 'ticks_per_second': ticks / wall,
            'guest_seconds_per_wall_second': clocks / 4_770_000 / wall,
            'polls': polls, 'before': before, 'after': after, 'stop_reason': stop}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=2301)
    parser.add_argument('--pairs', type=int, default=10)
    parser.add_argument('--guest-ms', type=int, default=250)
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    if min(args.pairs, args.guest_ms, args.timeout) <= 0:
        parser.error('pairs, guest-ms and timeout must be positive')
    client = Client(args.host, args.port, args.timeout)
    samples = []
    try:
        client.call('execution.pause')
        if client.call('breakpoints.list')['breakpoints']:
            raise RuntimeError('remove existing breakpoints before benchmarking')
        client.call('benchmark.mode')  # also rejects active CPU/hardware tracing
        initial_video = client.call('video.snapshot')
        for mode in ('eager', 'lazy'):
            sample(client, min(args.guest_ms, 20) * 1_000_000, args.timeout, mode)
        for pair in range(args.pairs):
            order = ('eager', 'lazy') if pair % 2 == 0 else ('lazy', 'eager')
            for mode in order:
                result = sample(client, args.guest_ms * 1_000_000, args.timeout, mode)
                samples.append(result)
                print(json.dumps({'sample': len(samples), 'mode': mode,
                                  'wall_seconds': result['wall_seconds'],
                                  'cpu_ticks': result['cpu_ticks']}), file=sys.stderr, flush=True)
        final_video = client.call('video.snapshot')
    finally:
        try:
            client.call('execution.pause')
            client.call('benchmark.mode', mode='lazy')
        finally:
            client.close()
    summaries = {}
    for mode in ('eager', 'lazy'):
        selected = [s for s in samples if s['mode'] == mode]
        summaries[mode] = {
            'median_wall_seconds': statistics.median(s['wall_seconds'] for s in selected),
            'median_ticks_per_second': statistics.median(s['ticks_per_second'] for s in selected),
            'total_ticks_per_second': sum(s['cpu_ticks'] for s in selected) /
                                      sum(s['wall_seconds'] for s in selected)}
    print(json.dumps({'python': sys.version, 'platform': platform.platform(),
                      'guest_ms_per_sample': args.guest_ms, 'pairs': args.pairs,
                      'samples': samples, 'summary': summaries,
                      'wall_time_speedup': summaries['eager']['median_wall_seconds'] /
                                           summaries['lazy']['median_wall_seconds'],
                      'tick_throughput_speedup': summaries['lazy']['total_ticks_per_second'] /
                                                 summaries['eager']['total_ticks_per_second'],
                      'initial_video': initial_video, 'final_video': final_video,
                      'limitations': 'Sequential live-game phases, not identical-state replay. '
                                     'CPU ticks include HLT and are not retired instruction counts. '
                                     'A benchmark-only wrapper is present in both modes.'}, indent=2))


if __name__ == '__main__':
    main()
