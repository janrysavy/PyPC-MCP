#!/usr/bin/env python3
"""Compare inactive frontend checks with a 64-instruction normal quantum."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmarks import benchmark_flags


def sample(mode, workload, ticks):
    cpu, snapshot = benchmark_flags.machine('fast', workload)
    control = {
        'paused': False, 'breakpoints_active': False,
        'instruction_hooks_active': False, 'memory_watchpoints_active': False,
        'interrupt_breakpoints_active': False, 'step': False,
        'run_until_deadline_clock': None, 'revision': 0,
    }
    tick = cpu.Tick
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        started = time.perf_counter()
        if mode == 'legacy':
            for _ in range(ticks):
                if control['paused'] or control['breakpoints_active']:
                    raise AssertionError('inactive control changed')
                trace_before = None
                if tick() == -1:
                    raise AssertionError('CPU stopped')
                control['revision'] += 1
                memory_stop = (cpu.ConsumeMemoryWriteStop()
                               if control['memory_watchpoints_active'] else None)
                interrupt_stop = (cpu.ConsumeInterruptStop()
                                  if control['interrupt_breakpoints_active'] else None)
                if (memory_stop is not None or interrupt_stop is not None
                        or control['run_until_deadline_clock'] is not None
                        or trace_before is not None or control['step']):
                    raise AssertionError('inactive observer changed')
        else:
            remaining = ticks
            while remaining:
                count = min(64, remaining)
                for _ in range(count):
                    if tick() == -1:
                        raise AssertionError('CPU stopped')
                control['revision'] += count
                remaining -= count
        seconds = time.perf_counter() - started
    finally:
        if gc_enabled:
            gc.enable()
    final = snapshot()
    final['revision'] = control['revision']
    return {'mode': mode, 'seconds': seconds,
            'ticks_per_second': ticks / seconds, 'final': final}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticks', type=int, default=200_000)
    parser.add_argument('--repeats', type=int, default=7)
    args = parser.parse_args()
    if min(args.ticks, args.repeats) < 1:
        parser.error('ticks and repeats must be positive')
    report = {
        'python': sys.version, 'platform': platform.platform(),
        'ticks': args.ticks, 'repeats': args.repeats,
        'quantum_instructions': 64, 'workloads': {},
        'scope': ('Synthetic normal-execution frontend with real 8088, PIT, '
                  'keyboard and VGA ticks. Exact debugger modes are excluded.'),
    }
    for workload in benchmark_flags.PROGRAMS:
        for mode in ('legacy', 'quantum'):
            sample(mode, workload, min(args.ticks, 20_000))
        samples = []
        for repeat in range(args.repeats):
            order = ('legacy', 'quantum') if repeat % 2 == 0 else ('quantum', 'legacy')
            for mode in order:
                samples.append(sample(mode, workload, args.ticks))
        expected = samples[0]['final']
        if any(item['final'] != expected for item in samples):
            raise AssertionError('CPU, RAM or device state differs')
        digest = hashlib.sha256(json.dumps(
            expected, sort_keys=True, default=str).encode()).hexdigest()
        for item in samples:
            del item['final']
        medians = {mode: statistics.median(
            item['seconds'] for item in samples if item['mode'] == mode)
            for mode in ('legacy', 'quantum')}
        report['workloads'][workload] = {
            'samples': samples, 'median_seconds': medians,
            'speedup': medians['legacy'] / medians['quantum'],
            'identical_final_state': True, 'final_state_sha256': digest,
        }
        print(workload, report['workloads'][workload]['speedup'],
              file=sys.stderr, flush=True)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
