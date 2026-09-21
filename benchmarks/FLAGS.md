# Flag fast paths

Baseline: `5144476fe37815ec4b28007b98c823d2b749f7b8`.
The production change specializes named flag getters/setters, uses a 256-entry
parity-bit table, and combines the existing Z/S/P update. It does not alter CPU
cycle accounting, interrupts, timers, devices, debugger hooks or instruction
semantics. `legacy_flags.py` contains the exact replaced baseline methods for
in-process A/B measurement; other code is shared by both modes.

## Reproduce synthetic CPU-plus-devices measurements

```
python -m pip install pytest
python -m pytest -q
python benchmarks/benchmark_flags.py --ticks 200000 --repeats 7 > flags.json
```

On Linux, optionally add `--cpu 1` (choose an available CPU). Do not run another
emulator or CPU-intensive task during measurements. Windows/macOS omit `--cpu`.
These deterministic instruction streams include arithmetic, flags-dependent
branches and RAM writes with real PIT, keyboard and VGA device ticks. They
require no game, ROM or disk image. Every sample starts fresh; setup, warmup
and digesting are outside timing. AB/BA order alternates. Full CPU state, RAM
SHA-256 and relevant device state must agree, or the benchmark fails.

Local CPython 3.13.5 / Linux x86-64, CPU affinity 1, 7 samples per mode,
200,000 CPU ticks per sample (median elapsed seconds):

| Workload | Baseline | Fast | Baseline / fast |
| --- | ---: | ---: | ---: |
| Arithmetic | 1.027162 | 0.697827 | 1.47194x |
| Logic and RAM writes | 0.896445 | 0.577861 | 1.55132x |
| Flags-dependent branches | 0.575250 | 0.384394 | 1.49651x |

All final-state comparisons passed. CI reruns tests on Python 3.12/3.13 and
uploads raw benchmark JSON.
Timing has no CI pass threshold: shared runners are noisy.

These are synthetic instruction workloads, not a claim of a 1.5x whole-program
speedup. For live workloads, launch `python benchmarks/flags_rpc.py
--video vga --host-dir PATH_TO_FRESH_SCRATCH_COPY` from the repository root.
This benchmark-only launcher exposes `benchmark.mode` (`legacy`/`fast`) while
paused; active CPU/hardware tracing is rejected. It switches only the state
class, without a per-tick mode wrapper. The normal debugger API is unchanged.
Mount a disposable copy of the application directory: the guest may write its
settings and records. Live sequential A/B samples advance the program and are not
identical-state replays; CPU ticks also include HLT and are not instruction counts.
