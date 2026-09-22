#!/usr/bin/env python3
"""Live legacy/fast flag A/B measurement through the JSON-lines debugger.

First launch benchmarks/flags_rpc.py and reach a stable DOS workload. The
program advances between bounded guest-time samples: this is not replay.
Treat the full output and optional incremental JSONL as potentially private.
"""
import argparse
import json
from pathlib import Path
import platform
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.benchmark_rpc import Client


def timed_call(client, deadline, method, **params):
    remaining = deadline - time.perf_counter()
    if remaining <= 0:
        raise TimeoutError('benchmark operation exceeded its deadline')
    client.sock.settimeout(remaining)
    return client.call(method, **params)


def sample(client, guest_ns, timeout, mode):
    deadline = time.perf_counter() + timeout
    timed_call(client, deadline, 'benchmark.mode', mode=mode)
    before = timed_call(client, deadline, 'state.get_registers')
    start = time.perf_counter()
    operation = timed_call(client, deadline, 'execution.run_until',
        predicate={'address': {'space': 'segmented', 'segment': 0xffff, 'offset': 0xffff}},
        max_emulated_ns=guest_ns)
    polls = 0
    while True:
        status = timed_call(client, deadline, 'execution.wait',
                            operation_id=operation['operation_id'])
        polls += 1
        if not status.get('running'):
            break
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError('guest execution exceeded its deadline')
        time.sleep(min(0.01, remaining))
    elapsed = time.perf_counter() - start
    stop = status.get('stop_reason', {})
    if stop.get('kind') != 'emulated_time_limit':
        raise RuntimeError(f'unexpected guest stop: {stop}')
    after = timed_call(client, deadline, 'state.get_registers')
    ticks = after['state_revision'] - before['state_revision']
    clocks = after['clock'] - before['clock']
    if ticks <= 0 or clocks <= 0:
        raise RuntimeError('guest did not advance')
    return {'mode': mode, 'wall_seconds': elapsed, 'cpu_ticks': ticks,
            'clock_cycles': clocks, 'ticks_per_second': ticks / elapsed,
            'polls': polls, 'before': before, 'after': after, 'stop_reason': stop}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=2301)
    parser.add_argument('--pairs', type=int, default=10)
    parser.add_argument('--guest-ms', type=int, default=500)
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--jsonl', type=Path, help='flush each sample to a fresh evidence file')
    args = parser.parse_args()
    if min(args.pairs, args.guest_ms, args.timeout) <= 0:
        parser.error('pairs, guest-ms and timeout must be positive')
    journal = args.jsonl.open('x', encoding='utf-8') if args.jsonl else None
    client = None
    samples = []
    try:
        client = Client(args.host, args.port, args.timeout)
        client.call('execution.pause')
        if client.call('breakpoints.list')['breakpoints']:
            raise RuntimeError('remove all breakpoints before benchmarking')
        client.call('benchmark.mode', mode='fast')  # also rejects active tracing
        initial = client.call('video.snapshot')
        for mode in ('legacy', 'fast'):
            sample(client, min(args.guest_ms, 50) * 1_000_000, args.timeout, mode)
        for pair in range(args.pairs):
            order = ('legacy', 'fast') if pair % 2 == 0 else ('fast', 'legacy')
            for mode in order:
                result = sample(client, args.guest_ms * 1_000_000, args.timeout, mode)
                result['pair'] = pair
                samples.append(result)
                if journal:
                    journal.write(json.dumps(result) + '\n')
                    journal.flush()
                print(json.dumps({key: result[key] for key in
                                  ('pair', 'mode', 'wall_seconds', 'cpu_ticks')}),
                      file=sys.stderr, flush=True)
        final = client.call('video.snapshot')
    finally:
        if journal:
            journal.close()
        if client:
            try:
                client.sock.settimeout(3)
                client.call('execution.pause')
                client.call('benchmark.mode', mode='fast')
            finally:
                client.close()
    summary = {}
    for mode in ('legacy', 'fast'):
        selected = [s for s in samples if s['mode'] == mode]
        summary[mode] = {
            'median_wall_seconds': statistics.median(s['wall_seconds'] for s in selected),
            'min_wall_seconds': min(s['wall_seconds'] for s in selected),
            'max_wall_seconds': max(s['wall_seconds'] for s in selected),
            'total_ticks_per_second': sum(s['cpu_ticks'] for s in selected) /
                                      sum(s['wall_seconds'] for s in selected)}
    print(json.dumps({'python': sys.version, 'platform': platform.platform(),
        'pairs': args.pairs, 'guest_ms': args.guest_ms, 'samples': samples,
        'summary': summary, 'initial_video': initial, 'final_video': final,
        'wall_time_speedup': summary['legacy']['median_wall_seconds'] /
                              summary['fast']['median_wall_seconds'],
        'tick_throughput_speedup': summary['fast']['total_ticks_per_second'] /
                                  summary['legacy']['total_ticks_per_second'],
        'limitations': 'Sequential live application phases, not identical-state replay. '
                       'CPU ticks include HLT and are not retired instructions. '
                       'Timings include normal debugger-loop and TCP polling costs.'}, indent=2))


if __name__ == '__main__':
    main()
