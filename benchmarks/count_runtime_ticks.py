#!/usr/bin/env python3
"""Count CPU Tick categories while running the normal PyPC frontend.

This benchmark changes wall performance by wrapping every CPU tick.  Its
counts and guest clock are useful; its wall time is deliberately not a normal
speed result.  Stop the frontend with Ctrl+C after the workload reaches the
desired boundary.  ``main.py`` handles that interrupt and this wrapper then
writes the counters.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import runpy
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stop-cycles', type=int,
                        help='stop after reaching this emulated CPU clock')
    parser.add_argument('frontend_args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    frontend_args = args.frontend_args
    if frontend_args[:1] == ['--']:
        frontend_args = frontend_args[1:]

    sys.path.insert(0, str(ROOT))
    import i8088

    original_tick = i8088.i8088.Tick
    counters = {
        'ticks': 0,
        'guest_cycles': 0,
        'instruction_ticks': 0,
        'rep_continuation_ticks': 0,
        'hlt_ticks': 0,
        'hlt_cycles': 0,
        'interrupt_dispatch_ticks': 0,
    }

    def counted_tick(cpu):
        state = cpu._state
        was_hlt = state._in_hlt
        was_rep = state._rep
        before = state._clock
        result = original_tick(cpu)
        cycles = state._clock - before
        counters['ticks'] += 1
        counters['guest_cycles'] += cycles
        if was_hlt:
            counters['hlt_ticks'] += 1
            counters['hlt_cycles'] += cycles
        else:
            counters['instruction_ticks'] += 1
        if was_rep:
            counters['rep_continuation_ticks'] += 1
        if result == 60:
            counters['interrupt_dispatch_ticks'] += 1
        if (args.stop_cycles is not None
                and counters['guest_cycles'] >= args.stop_cycles):
            raise KeyboardInterrupt
        return result

    i8088.i8088.Tick = counted_tick
    sys.argv = [str(ROOT / 'main.py'), *frontend_args]
    started = time.perf_counter()
    try:
        runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')
    finally:
        elapsed = time.perf_counter() - started
        report = {
            **counters,
            'requested_stop_cycles': args.stop_cycles,
            'instrumented_wall_seconds': elapsed,
            'average_cycles_per_tick': (
                counters['guest_cycles'] / counters['ticks']
                if counters['ticks'] else None),
            'hlt_tick_fraction': (
                counters['hlt_ticks'] / counters['ticks']
                if counters['ticks'] else None),
            'rep_continuation_fraction': (
                counters['rep_continuation_ticks'] / counters['ticks']
                if counters['ticks'] else None),
            'limits': (
                'The Python wrapper runs on every Tick and changes wall speed. '
                'instruction_ticks means Tick calls entered outside HLT; REP '
                'continuations remain separate Tick calls in that total. An '
                'interrupt dispatch is classified by Tick returning 60 cycles.'),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n',
                               encoding='utf-8')


if __name__ == '__main__':
    main()
