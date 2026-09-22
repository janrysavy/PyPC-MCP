# Live DOS application flag benchmark

The synthetic benchmark in `FLAGS.md` starts every sample from identical CPU
and device state. This client instead measures the complete emulator through
the local JSON-RPC channel while a DOS application runs. Its consecutive
samples advance through different application states, so they are not an
identical-state replay or a frames-per-second measurement.

An earlier Linux run on a private first-level workload reported about 5–6%
better wall-time and CPU-tick throughput with the fast flag methods. The
1.4–1.5x synthetic gains do not predict the same whole-application gain.
Reproduce the live result on your own workload with the commands below.

Use a disposable emulator checkout and a scratch copy of the complete DOS
application directory because the guest can write to its disk and mounted
directory. Start the benchmark-only emulator:

```sh
python benchmarks/flags_rpc.py --video vga --host-dir PATH_TO_SCRATCH_COPY
```

Boot DOS, start the application, and reach the workload you want to measure.
Disconnect interactive VNC clients, remove breakpoints, and stop CPU and
hardware tracing. Then run in a second terminal:

```sh
python benchmarks/benchmark_flags_rpc.py --pairs 10 --guest-ms 500 --jsonl flags-live.jsonl > flags-live.json
```

The JSONL file must not already exist. Each completed sample is flushed to it.
The final JSON contains register states and video hashes, so inspect it before
sharing. The client alternates legacy and fast methods through bounded guest
time, requires the emulated-time-limit stop reason, records CPU ticks and guest
clocks, and normally leaves the emulator paused in fast mode. Use a longer
`--timeout` on slower hosts.
