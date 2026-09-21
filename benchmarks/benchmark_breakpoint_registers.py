#!/usr/bin/env python3
"""Compare eager/lazy register collection using production RPC and CPU code.

No game files, ROMs, network server, or extra packages are needed. Both modes
run in this interpreter, alternate order, and must produce the same CPU state.
The eager mode restores the pre-optimization calls in main.py in a temporary
copy. This measures a synthetic debugger loop, not whole-game performance.
"""
import argparse
import ast
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
from test_execution_rpc import HeadlessMachine


def run(main_path, ticks):
    machine = HeadlessMachine(main_path)
    machine.load(bytes.fromhex('40 eb fd'))  # inc ax; jmp back (flags exercised)
    machine.execution_bp(0x200)  # deliberately not reached
    machine.rpc('execution.continue')
    # Execute the unchanged production loop body in one compiled for-loop.
    # Avoid per-tick exec/pump overhead from the unit-test helper.
    tree = ast.parse(main_path.read_text(), filename=str(main_path))
    body = next(node.body for node in tree.body if isinstance(node, ast.Try))
    loop = next(node for node in body if isinstance(node, ast.While))
    finite = ast.For(target=ast.Name(id='_benchmark_tick', ctx=ast.Store()),
                     iter=ast.Call(func=ast.Name(id='range', ctx=ast.Load()),
                                   args=[ast.Constant(ticks)], keywords=[]),
                     body=loop.body, orelse=[])
    module = ast.fix_missing_locations(ast.Module(body=[finite], type_ignores=[]))
    code = compile(module, str(main_path), 'exec')
    started = time.perf_counter()
    exec(code, machine.namespace)
    elapsed = time.perf_counter() - started
    registers = machine.rpc('state.get_registers')
    assert machine.control['revision'] == ticks
    assert not machine.control['paused']
    return elapsed, registers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticks', type=int, default=200_000)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.ticks < 1 or args.repeats < 1:
        parser.error('ticks and repeats must be positive')
    source = (ROOT / 'main.py').read_text()
    old = 'rpc_flat_registers(),'
    new = 'rpc_flat_registers,'
    if source.count(new) != 2 or old in source:
        parser.error('run from the lazy-register PR checkout')
    results = {'eager': [], 'lazy': []}
    expected = None
    with tempfile.TemporaryDirectory() as directory:
        paths = {mode: Path(directory) / (mode + '.py') for mode in results}
        paths['eager'].write_text(source.replace(new, old))
        paths['lazy'].write_text(source)
        for path in paths.values():
            run(path, min(args.ticks, 10_000))
        for repeat in range(args.repeats):
            order = ('eager', 'lazy') if repeat % 2 == 0 else ('lazy', 'eager')
            for mode in order:
                elapsed, registers = run(paths[mode], args.ticks)
                if expected is None:
                    expected = registers
                assert registers == expected, (mode, registers, expected)
                results[mode].append(elapsed)
    medians = {mode: statistics.median(values) for mode, values in results.items()}
    print(json.dumps({
        'benchmark': 'production CPU/debugger loop; inc ax / jmp; one address miss',
        'python': sys.version, 'platform': platform.platform(),
        'ticks_per_sample': args.ticks, 'repeats': args.repeats,
        'wall_seconds': results, 'median_seconds': medians,
        'speedup': medians['eager'] / medians['lazy'],
        'identical_final_registers_and_clock': expected,
        'scope': 'synthetic; not end-to-end Pyro gameplay',
    }, indent=2))


if __name__ == '__main__':
    main()
