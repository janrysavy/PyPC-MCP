#!/usr/bin/env python3
"""Benchmark-only launcher; normal debugger API is unchanged.

Use a disposable checkout and --host-dir pointing to a fresh scratch copy.
Adds benchmark.mode (legacy/fast), accepted only while paused and not tracing.
Only the state class changes at a paused boundary: there is no per-Tick wrapper.
"""
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import debugserver
from state8088 import State8088
from benchmarks.legacy_flags import LegacyState


def main():
    original = debugserver.DebugServer.process_pending

    def process(server, handler, maximum=8):
        def dispatch(request):
            if request.get('method') != 'benchmark.mode':
                return handler(request)
            if handler({'method': 'session.status'})['state'] != 'stopped':
                raise ValueError('pause before switching benchmark mode')
            control = handler.__globals__['control']
            if control['trace_active'] or control['hardware_trace_active']:
                raise ValueError('stop tracing before benchmarking')
            params = request.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('params must be an object')
            mode = params.get('mode', 'fast')
            if mode not in ('legacy', 'fast'):
                raise ValueError('mode must be legacy or fast')
            state = handler.__globals__['p'].GetState()
            state.__class__ = LegacyState if mode == 'legacy' else State8088
            return {'mode': mode, 'benchmark_only': True}
        return original(server, dispatch, maximum)

    debugserver.DebugServer.process_pending = process
    runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')


if __name__ == '__main__':
    main()
