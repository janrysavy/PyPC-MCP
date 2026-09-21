# Periodic PIT event accounting

Baseline: 5144476fe37815ec4b28007b98c823d2b749f7b8.

With a nonzero divisor N, the previous code loaded `counter_cur = N`, then
computed events as `-counter_cur // N` after subtracting elapsed clocks.
At zero this gives zero events. An event only arrives at -N, so repeated
small Tick calls produce a 2N period. Large batches also lose the initial
period, and event totals depend on how the caller partitions elapsed time.
The encoded zero divisor is a separate case: it already produced a 65536
clock period. Do not infer that every DOS BIOS timer was running half-speed.

The fix is restricted to binary periodic modes 2/3, including encodings 6/7.
It counts the event at expiry, accounts for every additional elapsed period,
and retains the remainder. DMA refresh receives the full elapsed-event count;
the existing one-IRQ-request-per-Tick coalescing is unchanged. Channel 2 does
not request IRQ0 or DMA. The original four-CPU-clocks-per-PIT-clock conversion
and fractional residue are unchanged. The zero reload encoding remains zero.

The period contract is also documented in the manufacturer's compatible
82C54 datasheet, FN2970 Rev 6.00, page 13, Mode 2 and Mode 3 descriptions;
the mode encoding table is on page 8:
https://www.renesas.com/en/document/dst/82c54-datasheet?r=496456

This is a correction inside the emulator's existing immediate-load event
model, not a cycle-accurate 8253 rewrite. It does not implement GATE/OUT
waveforms, the extra initial load edge, mode-3 half-cycle counter readback,
latching/read-back commands, proper BCD counting, or one-shot refinements.
Other modes and BCD retain their existing approximation. A compatibility
test checks those unchanged paths; it does not certify their accuracy.

A separate directly reproduced diagnostics bug is fixed here: `GetStat()`
referenced an undefined `_mode_names`. A status test covers all eight mode
encodings and all three returned channel rows.

## Reproduce

```
python -m pip install pytest
python -m pytest tests/test_pit_periodic.py -q
python -m pytest -q
```

The initial negative-control run reported 41 failures including failed
subtests. The patched full suite passed 95 tests plus 234 subtests locally
on CPython 3.13.5. Coverage includes four successive IRQ periods, nonzero
and zero divisors, odd divisors, aliases, batched DMA refresh, partition
invariance, fractional clocks, IRQ coalescing, channel 2 and partial loads.

This patch changes guest timer behavior for affected divisors and is not a
host-performance optimization. It was not included in PR #13's live Pyro
speed measurements. Unit regressions are not proof of full-game timing
compatibility; keep those claims separate.
