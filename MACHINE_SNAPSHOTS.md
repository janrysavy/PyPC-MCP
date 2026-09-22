# Persistent machine checkpoints

`machine.snapshot.export/import` provide stopped-machine persistence through
JSON-RPC. See `JSON_RPC_API.md` for parameters. The snapshot contains CPU state,
RAM, ROMs, PIT/PIC, DMA, keyboard/PPI, video, XTIDE transfer state, Python RNG,
and disk dependencies. Source-file hashes bind compatibility to the source
loaded at process startup. Unknown motherboard layouts and custom BIOS callbacks
are refused; the standard VGA BIOS service is identified and rebound explicitly.

`machinecodec.prepare_machine` validates a detached motherboard before creating
new backing disks. `machineinstall.install_machine` preserves the live CPU-state,
device, port-map, timer callback and keyboard-lock identities. Existing frontend
and register-access references therefore continue to address restored state.
The saved host RNG is activated before the next guest instruction.

`machinesnapshots` holds VNC-render, display and keyboard locks throughout
restoration, including bundle reading and disk preparation. CPU execution must
already be paused. Newly arriving input is deferred until after installation.
For deterministic automatic experiments, disable viewer keyboard forwarding and
exclude external input/filesystem writers throughout the replay.

## Persistence and failure behavior

Bundles contain JSON plus hashed binary ZIP members, never pickle or executable
payloads. Import never extracts archive paths. Duplicate/unknown members, bad
hashes, duplicate JSON keys and oversized payloads are refused. Export publishes
a completed temporary archive using an atomic no-overwrite hard link; the target
filesystem must support hard links (NTFS/ext4). Failed writes leave no published
archive. Limits are1GiB total uncompressed data and4MiB JSON.

Small flat disks embed by default; explicit references require matching size and
SHA-256 and are copied into new backing files before use. Host-directory disks
embed live FAT bytes, files and sync caches. Existing disks are never overwritten.
Disk materialization failures may leave a partial *new* directory; live state and
existing disks remain unchanged. Host ACLs, permissions, timestamps, external
applications and concurrent host writes are outside the contract.

Import remains paused, increments the host revision and clears pending execution
operations, traces and retained video snapshots. Debugger breakpoints remain
configured; stale CPU breakpoint-skip state is cleared. Host operation identifiers
are not rewound. Network sessions and pending packets are not serialized.
VNC uses a display epoch to force a full next frame. A previously sampled frame
may still arrive after the RPC reply because transmission is asynchronous.
RPC completion is not a cross-connection network delivery barrier. Telnet accepts
clock rewind and equal-clock epoch changes so restored screens repaint.

## Verification

Synthetic replay covers PIT reads, RAM updates, queued input, pending XTIDE
writes and full component/buffer comparison, including a fresh Python process.
Integration tests cover ROM reads, restored INT10 mode query, two disks including
a host directory, checked references, motherboard links and live object identity.
Production-handler tests cover revision/pause/hash refusal, input exclusion,
viewer invalidation and register bindings. Fault controls cover malformed disk
descriptors/references and archive-write failure.33 focused cases pass.

Windows full suite before the final null-descriptor guard:1244 passed,
2 optional LZ4 skipped,246 subtests. Final-head CI is required before merge.
Independent reviews identified BIOS callback, input-exclusion, archive publication,
reference-validation and stale breakpoint-state defects; these are fixed with
regressions. Follow-up found a null disk descriptor raised AttributeError;
explicit type validation now rejects null/list/string/integer descriptors with
ValueError before output creation. The small final guard was locally reviewed.

Two actual Pyro first-floor restart trials are recorded in the parent project's
`re/harness/traces/pypc-snapshot-20260922` and
`re/harness/traces/pypc-snapshot-final-20260922`:
-74b4b3b:3319 instructions/47708 clocks; every manifest field and binary buffer matched.
-a7d7d76:3320 instructions/47707 clocks; every manifest field and binary buffer matched.
The second process restored an export from the first and repeated a bounded10ms
emulated interval. User keyboard input caused a retained negative trial to differ
only in three keyboard fields; disabling forwarding produced full equality.
These are bounded observations, not exhaustive instruction/game-mechanics parity.
The final input-schema guard does not alter valid-machine restore behavior.
