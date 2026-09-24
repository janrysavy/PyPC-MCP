# Debugger register-collection benchmarks

This experiment changes **debugger overhead**, not guest CPU clocks, opcodes,
hardware models, or normal execution with no execution/interrupt breakpoints.
`execution.run_until` installs an execution breakpoint, so even a time-limited
run benefits. Address/event misses and unconditional hits need no register
snapshot; matching conditional breakpoints still get a fresh snapshot.

## Self-contained CPU/debugger-loop A/B

Use Python 3.12 or newer. No game, ROMs or third-party benchmark packages are
required. From the repository root:

```sh
python -m pytest -q
python benchmarks/benchmark_breakpoint_registers.py --ticks 200000 --repeats 7 > loop.json
```

The benchmark compiles the production main-loop body into a finite loop and
runs actual `inc ax; jmp` instructions with one non-matching breakpoint. Eager
mode restores the two original call sites in a temporary `main.py`; lazy mode
uses this checkout. Both use the same CPU and breakpoint implementation. Each
mode is warmed up; measurement order alternates AB/BA. Registers, flags,
revision and emulated clocks must agree exactly. File/AST loading is untimed.

Measured locally on CPython 3.13.5, Linux x86-64 (seven samples of 200,000 CPU
Ticks): eager median **1.061053 s**, lazy **0.591221 s**, **1.795x** throughput.
Both ended at 1,800,000 emulated clocks with identical registers and flags.
These are synthetic results, not a claim of whole-game speedup. Timings are
host-dependent; the JSON retains every sample and environment information.

## Live DOS application through the JSON-RPC debugger

Use a disposable emulator checkout: its `harddisk.img` is writable. Copy the
complete game directory to a **fresh scratch directory**, including its assets.
Never mount the immutable source `bin/` directory. No game files are included
in this public repository.

Terminal 1, from the disposable checkout:

```sh
python benchmarks/rpc_ab.py --video vga --host-dir /absolute/path/to/scratch-application
```

Boot DOS (Enter acknowledges the GLaBIOS FDC warning), select `D:`, and run
the application executable. Follow its normal startup flow until the workload
begins. Keyboard input can be sent using the normal `input.keyboard` JSON-RPC
method at localhost:2301 or a VNC client.
Close interactive clients, release keys, disable CPU/hardware tracing and
remove existing breakpoints before measuring.

Terminal 2:

```sh
python benchmarks/benchmark_rpc.py --pairs 10 --guest-ms 250 > live-application.json
```

The launcher adds a **benchmark-only** `benchmark.mode` method and a common
wrapper around breakpoint checks; `run_pypc.py` and the public API are unchanged.
Mode changes are only accepted while paused and reject active tracing. Eager
mode materializes registers before every check, reproducing the original
behavior. Lazy mode defers collection. The benchmark runs warmups and ten
AB/BA pairs through actual TCP JSON-lines `execution.run_until`/`execution.wait`.
Each sample is bounded by emulated time, has a wall deadline, records the stop
reason, registers, CPU Tick count and emulated clock delta, and must end at the
time limit rather than an accidental breakpoint. It leaves the emulator paused
in lazy mode. Progress is written to stderr; JSON data goes to stdout.

**Interpretation:** the game advances between samples. This is a live
sequential comparison, **not an identical-state replay**. Compare raw per-sample
clocks and Tick counts as well as timing distributions; game phase, HLT,
interrupts, OS scheduling and RPC polling affect wall time. CPU Ticks are not
retired instructions. The benchmark-only wrapper is present in both modes and
is not used in production. Snapshot hashes identify the observed start/end
video but do not prove mechanic equivalence. Keep live-game results private
when they contain private register/memory/video information.

## Video/VNC hot-path benchmark

The video benchmark isolates renderer and VNC costs without booting a guest:

```sh
python benchmarks/benchmark_video.py --frames 20 --repeats 5 > video.json
```

It measures native-buffer and RGB565 conversion, VGA text/mode 12h/mode 13h,
CGA text/graphics rendering, cached unchanged frames, and the incremental VNC
empty-update and changed-rectangle paths. The renderer benchmark calls
`GetFrame()` repeatedly, while the VNC cases represent repeated framebuffer
requests with no visible change and one changing text cell. Timings are
host-dependent; the useful comparison is between two runs on the same
machine. Native buffers should avoid a list-to-bytes allocation, unchanged
incremental requests should transfer only the four-byte VNC update header, and
small changes should transfer a bounded rectangle rather than the full frame.

## Whole-runtime CPU experiments

`count_runtime_ticks.py` wraps the normal frontend CPU and classifies ordinary,
REP-continuation and HLT Tick calls. Its counts and guest clock are valid, but
the wrapper changes wall speed. Pass normal frontend arguments after `--` and
stop with Ctrl+C, or provide an exact guest-clock boundary:

```powershell
python benchmarks/count_runtime_ticks.py --output counts.json `
  --stop-cycles 47661509 -- --video cga --rpc-port 2301
```

`benchmark_runtime_hotpaths.py` compares the production device path with two
benchmark-only prototypes and refuses any CPU, device or RAM-state difference:

```powershell
$env:PYTHON_JIT='0'
python benchmarks/benchmark_runtime_hotpaths.py --ticks 100000 --repeats 5
```

The Windows 2026-09-24 results are under `benchmarks/results/`. On CPython
3.14.7, enabling its experimental JIT was neutral on the production debugger
loop, 4-6% lower throughput on arithmetic/branch CPU loops and 43% lower on the
logic/memory loop. PyPy 3.12.14/8.0.0 was 19.7-23.1x faster for direct CPU Tick
loops and 3.43x faster for the production debugger-loop benchmark. These are
steady-state synthetic measurements; live boot and compiler timings belong in
the integrating project's evidence.

The idle-keyboard prototype produced 1.24-1.37x throughput while preserving
the sampled final state. It has since become the production implementation,
using a lock-protected pending flag published by input producers. The benchmark
now compares production code against the former locked implementation. Its
concurrency tests prove that a Tick racing an incomplete enqueue can defer the
event by at most one instruction and that the following Tick delivers it. The
empty-PIC shortcut was approximately neutral to 1% faster, so it is not a
priority.

The post-change CPython 3.14 comparison is
`results/2026-09-24-keyboard-fastpath-py314.json`. Against the former locked
path, production throughput improved 1.20x for arithmetic, 1.22x for
logic/memory, and 1.32x for branch workloads with identical sampled final
state.

`benchmark_frontend_quantum.py` compares the inactive per-instruction frontend
checks with the production 64-instruction normal-execution batch. It uses the
real 8088, PIT, keyboard and VGA device path and refuses any final-state or
revision difference. Exact debugger modes never enter the batch.
The CPython 3.14 result in
`results/2026-09-24-frontend-quantum-py314.json` measured 1.05x, 1.06x and
1.09x throughput across the arithmetic, logic/memory and branch workloads.
