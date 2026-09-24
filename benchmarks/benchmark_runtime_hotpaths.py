#!/usr/bin/env python3
"""Measure two exact-state prototypes found by whole-boot profiling.

The prototypes are installed only while this benchmark runs.  ``pic_empty``
returns immediately when the PIC has no unmasked request.  ``keyboard_idle``
avoids two RLock acquisitions when no keyboard interrupt is scheduled.  Every
mode must finish with identical CPU, device and RAM state.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from benchmarks import benchmark_flags
import i8259
import keyboard


MODES = ('baseline', 'pic_empty', 'keyboard_idle', 'both')
ORIGINAL_PIC = i8259.i8259.GetPendingInterrupt
ORIGINAL_KEYBOARD = keyboard.Keyboard.Tick


def pic_empty(self):
    if not (self._irr & ~self._imr & 0xff):
        return 255
    return ORIGINAL_PIC(self)


def keyboard_idle(self, cycles, clock):
    if not self._next_interrupt:
        return False
    return ORIGINAL_KEYBOARD(self, cycles, clock)


@contextmanager
def prototype(mode):
    i8259.i8259.GetPendingInterrupt = (
        pic_empty if mode in ('pic_empty', 'both') else ORIGINAL_PIC)
    keyboard.Keyboard.Tick = (
        keyboard_idle if mode in ('keyboard_idle', 'both')
        else ORIGINAL_KEYBOARD)
    try:
        yield
    finally:
        i8259.i8259.GetPendingInterrupt = ORIGINAL_PIC
        keyboard.Keyboard.Tick = ORIGINAL_KEYBOARD


def sample(mode, workload, ticks):
    with prototype(mode):
        result = benchmark_flags.sample('fast', workload, ticks)
    result['prototype'] = mode
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticks', type=int, default=100_000)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if min(args.ticks, args.repeats) < 1:
        parser.error('ticks and repeats must be positive')

    report = {
        'python': sys.version,
        'platform': platform.platform(),
        'ticks': args.ticks,
        'repeats': args.repeats,
        'workloads': {},
        'scope': ('Synthetic CPU/device benchmark. The idle-keyboard prototype '
                  'does not exercise concurrent host input.'),
    }
    for workload in benchmark_flags.PROGRAMS:
        for mode in MODES:
            sample(mode, workload, min(args.ticks, 20_000))
        samples = []
        for repeat in range(args.repeats):
            order = MODES[repeat % len(MODES):] + MODES[:repeat % len(MODES)]
            for mode in order:
                samples.append(sample(mode, workload, args.ticks))
        expected = samples[0]['final']
        if any(item['final'] != expected for item in samples):
            raise AssertionError('CPU, RAM or device state differs between prototypes')
        final_sha256 = hashlib.sha256(json.dumps(
            expected, sort_keys=True, default=str).encode()).hexdigest()
        for item in samples:
            del item['final']
            del item['mode']
        medians = {
            mode: statistics.median(
                item['seconds'] for item in samples
                if item['prototype'] == mode)
            for mode in MODES
        }
        report['workloads'][workload] = {
            'samples': samples,
            'median_seconds': medians,
            'speedup_vs_baseline': {
                mode: medians['baseline'] / medians[mode] for mode in MODES
            },
            'identical_final_state': True,
            'final_state_sha256': final_sha256,
        }
        print(workload, report['workloads'][workload]['speedup_vs_baseline'],
              file=sys.stderr, flush=True)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
