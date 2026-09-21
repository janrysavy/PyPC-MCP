#!/usr/bin/env python3
"""Launch a local emulator with benchmark-only eager/lazy mode switching.

Run this in a disposable checkout (the DOS boot disk is writable), with
--video vga --host-dir pointing to a fresh SCRATCH copy of the game files.
No game data is included. Never mount your immutable source directory.
This adds benchmark.mode to this process only; normal run_pypc.py is unchanged.
"""
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import debugbreakpoints
import debugserver


class ModeController:
    def __init__(self):
        self.mode = 'lazy'

    def wrap_check(self, original):
        def check(manager, *args, **kwargs):
            # Execution and interrupt checks both take registers penultimate;
            # callers in main.py pass all arguments positionally.
            position = 2 if original.__name__ == 'check' else 3
            args = list(args)
            if self.mode == 'eager':
                if len(args) > position:
                    if callable(args[position]):
                        args[position] = args[position]()
                elif callable(kwargs.get('registers')):
                    kwargs['registers'] = kwargs['registers']()
            return original(manager, *args, **kwargs)
        return check

    def wrap_handler(self, handler):
        def dispatch(request):
            if request.get('method') != 'benchmark.mode':
                return handler(request)
            if handler({'method': 'session.status'})['state'] != 'stopped':
                raise ValueError('pause execution before changing benchmark mode')
            control = handler.__globals__.get('control', {})
            if control.get('trace_active') or control.get('hardware_trace_active'):
                raise ValueError('stop CPU and hardware tracing before benchmarking')
            params = request.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('params must be an object')
            mode = params.get('mode', self.mode)
            if mode not in ('eager', 'lazy'):
                raise ValueError('mode must be eager or lazy')
            self.mode = mode
            return {'mode': mode, 'benchmark_only': True}
        return dispatch


def main():
    controller = ModeController()
    for name in ('check', 'check_interrupt'):
        setattr(debugbreakpoints.BreakpointManager, name, controller.wrap_check(
            getattr(debugbreakpoints.BreakpointManager, name)))
    process_pending = debugserver.DebugServer.process_pending

    def process(server, handler, maximum=8):
        return process_pending(server, controller.wrap_handler(handler), maximum)

    debugserver.DebugServer.process_pending = process
    runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')


if __name__ == '__main__':
    main()
