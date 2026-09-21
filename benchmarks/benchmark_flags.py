#!/usr/bin/env python3
"""A/B flag fast paths using real 8088, PIT, keyboard and VGA ticks.

No private binary, ROM, disk, or optional package is required. Every sample
starts from identical state. AB/BA order, untimed warmup and final-state checks
prevent a dispatch-only microbenchmark from being mistaken for CPU throughput.
"""
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bus
import i8088
import i8253
import keyboard
import vga
from benchmarks.legacy_flags import LegacyState

BASE_COMMIT = '5144476fe37815ec4b28007b98c823d2b749f7b8'
PROGRAMS = {
    # INC AX; ADD AX,1234; ADC BX,7fff; SUB DX,1; CMP BX,AX; JMP start
    'arithmetic': '40 05 34 12 81 d3 ff 7f 83 ea 01 39 c3 eb f1',
    # MOV [200],AX; XOR AX,5aa5; AND BX,0fff; OR DX,AX; TEST AX,DX; JMP
    'logic_memory': 'a3 00 02 35 a5 5a 81 e3 ff 0f 09 c2 85 d0 eb f0',
    # INC AX; TEST AL,1; JZ skip; CLC; STC; CMC; JMP start
    'branch': '40 a8 01 74 01 f8 f9 f5 eb f6',
}


def machine(mode, workload):
    if mode not in ('legacy', 'fast'):
        raise ValueError('mode must be legacy or fast')
    pit, keys, screen = i8253.i8253(), keyboard.Keyboard(), vga.VGA(False)
    devices = [pit, keys, screen]
    memory = bus.Bus(1 << 20, devices, [])
    cpu = i8088.i8088(memory, devices, True)
    state = cpu.GetState()
    if mode == 'legacy':
        state.__class__ = LegacyState
    for name, value in {'CS': 0x1000, 'DS': 0x2000, 'SS': 0x3000,
                        'SP': 0xff00, 'IP': 0x100, 'Flags': 2}.items():
        getattr(state, 'Set' + name)(value)
    for index, value in enumerate(bytes.fromhex(PROGRAMS[workload])):
        cpu.WriteMemByte(0x1000, 0x100 + index, value)
    pit.IO_Write(0x43, 0x34)
    pit.IO_Write(0x40, 0x20)
    pit.IO_Write(0x40, 0)
    keys.PushKeyboardScancode(0x1e)
    keys.PushKeyboardScancode(0x9e)

    def snapshot():
        cpu_state = dict(vars(state))
        cpu_state['_rep_mode'] = state._rep_mode.name
        return {'cpu': cpu_state, 'pit': [dict(vars(t)) for t in pit._timers],
                'pit_clock': pit._clock, 'pic': dict(vars(cpu._io._pic)),
                'keyboard_pending': keys._next_interrupt,
                'video': [screen._clock, screen._last_update, screen._pulse_vsync,
                          screen._blink_phase, screen._cursor_phase],
                'ram_sha256': hashlib.sha256(memory._m._m).hexdigest()}
    return cpu, snapshot


def sample(mode, workload, ticks):
    cpu, snapshot = machine(mode, workload)
    tick = cpu.Tick
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        start = time.perf_counter()
        for _ in range(ticks):
            tick()
        seconds = time.perf_counter() - start
    finally:
        if gc_enabled:
            gc.enable()
    return {'mode': mode, 'seconds': seconds, 'ticks': ticks,
            'ticks_per_second': ticks / seconds, 'final': snapshot()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticks', type=int, default=200_000)
    parser.add_argument('--repeats', type=int, default=7)
    parser.add_argument('--cpu', type=int, help='optional Linux CPU affinity')
    args = parser.parse_args()
    if min(args.ticks, args.repeats) <= 0:
        parser.error('ticks and repeats must be positive')
    if args.cpu is not None:
        if not hasattr(os, 'sched_setaffinity'):
            parser.error('--cpu requires sched_setaffinity; omit on Windows/macOS')
        os.sched_setaffinity(0, {args.cpu})
    report = {'python': sys.version, 'platform': platform.platform(),
              'base_commit': BASE_COMMIT, 'cpu_affinity':
                  sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
              'ticks': args.ticks, 'repeats': args.repeats, 'workloads': {}}
    for workload in PROGRAMS:
        for mode in ('legacy', 'fast'):
            sample(mode, workload, min(args.ticks, 20_000))
        samples = []
        for repeat in range(args.repeats):
            order = ('legacy', 'fast') if repeat % 2 == 0 else ('fast', 'legacy')
            for mode in order:
                samples.append(sample(mode, workload, args.ticks))
        final = samples[0]['final']
        if any(s['final'] != final for s in samples):
            raise AssertionError('CPU, RAM or device state differs between modes')
        for result in samples:
            del result['final']
        medians = {mode: statistics.median(s['seconds'] for s in samples
                                          if s['mode'] == mode)
                   for mode in ('legacy', 'fast')}
        report['workloads'][workload] = {
            'samples': samples, 'median_seconds': medians,
            'speedup': medians['legacy'] / medians['fast'],
            'identical_final_state': True, 'final': final}
        print(f'{workload}: {medians["legacy"] / medians["fast"]:.4f}x',
              file=sys.stderr, flush=True)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
