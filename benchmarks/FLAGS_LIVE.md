# Live Pyro II flag-path measurement

The initial whole-emulator run is deliberately reported separately from the
synthetic CPU/device benchmarks in FLAGS.md. With the first playable level on
screen, no movement input, VGA mode and no VNC client, 10 AB/BA pairs of 500 ms
of bounded guest time gave:

| Measurement | Legacy | Fast | Ratio |
| --- | ---: | ---: | ---: |
| Median wall seconds | 1.266883 | 1.199024 | 1.05659x |
| Aggregate CPU ticks / host second | 155012 | 162934 | 1.05111x |

This is approximately **5-6%**, not the synthetic 1.5x figure. Raw per-sample
wall times, CPU ticks, guest clock deltas and poll counts are committed in
`results/pyro-flags-20260921.json`. Host: CPython 3.13.5, Linux x86-64, Intel Xeon
Platinum 8573C. Server affinity was not pinned in this live run. Every measured
operation ended on the emulated-time bound, not a breakpoint or crash. No CPU
or hardware trace was active. The trace used to inspect a keyboard wait was
stopped before measuring. No emulated timer or instruction clock was changed.

The game advances between samples, and its instruction mix varies. This is
not an identical-state replay, exhaustive gameplay validation or an FPS claim.
Host time includes the ordinary debugger loop and TCP polling. CPU ticks include
HLT ticks; no sample endpoint in this run was in HLT, but that does not prove
that there were no HLT ticks inside a sample.

## Reproduce

Use a disposable emulator checkout because DOS can write its disk image.
Make a fresh scratch copy of your game's complete `bin` directory and mount
that copy, never the original. From the emulator repository root:

```
python benchmarks/flags_rpc.py --video vga --host-dir PATH_TO_SCRATCH_COPY
```

Use the ordinary JSON-RPC keyboard method or the emulator's frontend to boot
DOS. With the bundled image, acknowledge the FDC POST warning; the LTEMM scan
can be bypassed with Esc. At the prompt run `D:` then `PYRO22`. For a fresh copy,
End leaves the instruction screen. Select the default mode with Enter, press a
key at the credits prompt, and press Enter at the building title to enter the
first level. Merely seeing the building title is not gameplay: a bounded trace
confirmed that it waits for a keyboard event. Disconnect VNC before measuring.

In a second terminal, from the same checkout:

```
python benchmarks/benchmark_flags_rpc.py --pairs 10 --guest-ms 500 --jsonl pyro-flags.jsonl > pyro-flags.json
```

The JSONL path must be new; each sample is flushed immediately so an interrupted
session retains completed measurements. The final JSON includes register states
and video hashes for local diagnosis: treat it as potentially private. The
public result above omits those fields. Both warmups and alternating modes run
through the real localhost JSON-RPC server. The client uses operation deadlines,
requires emulated-time-limit stops, rejects existing breakpoints, and normally
leaves the guest paused in fast mode. Use a longer `--timeout` on slower hosts.

For a PowerShell scratch copy, for example:

```powershell
$Scratch = Join-Path $env:TEMP ([guid]::NewGuid().ToString())
New-Item -ItemType Directory $Scratch
Copy-Item 'PATH_TO_GAME\bin\*' $Scratch -Recurse
python benchmarks/flags_rpc.py --video vga --host-dir $Scratch
```

The published timings are Linux measurements; Windows was not benchmarked.
