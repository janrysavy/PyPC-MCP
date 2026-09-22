# Coordinated machine reconstruction (library stage)

`machinecodec.capture_machine` composes the existing CPU, PIT/PIC, DMA,
keyboard/PPI, video and XTIDE codecs with RAM, ROMs, disk dependencies and the
Python RNG used by PIT noise. Source-file hashes bind the snapshot to the
emulator implementation, independent of checkout path. The supported device
order is the motherboard constructed by `main.py`; unknown topologies are
refused. The caller explicitly identifies the standard VGA BIOS service hook.

`prepare_machine` validates and builds a detached motherboard. CPU/device/RAM
and disk validation precede creation of new backing-disk directories. It returns
a CPU and a validated local RNG; it does not change the live emulator or the
process RNG. A coordinator must exclude input and display readers, activate the
returned RNG, install the machine atomically, and invalidate host display caches
before resuming guest execution. Disk materialization failures can leave only
new partial directories; existing sources and the live machine remain unchanged.

This is not yet exposed by JSON-RPC. `checkpointbundle.py` stores JSON and hashed
binary buffers in a bounded ZIP container; it never extracts archive paths.
Duplicate members/JSON keys, unknown members and bad hashes are refused. Export
requires a new file; import hashes and parses the same bytes, avoiding a second
path read between the hash check and parsing.
Do not claim usable gameplay checkpoints or a restart of the running Pyro
session from these library tests. The live process still runs its prior code.
Debugger breakpoints/traces/operation identifiers and host network connections
are not guest state and are not reconstructed by this module.

## Evidence

Seven tests in `tests/test_machinecodec.py` pass on Windows Python 3.11 with
the repository compatibility shim. A synthetic 8088 program repeatedly reads
PIT ports and updates RAM while devices tick. The captured machine also has
queued keyboard input and a partially supplied XTIDE write. After reconstruction,
200 instructions produce the same memory trace and all captured state/buffer
hashes; completing the pending disk write produces the same bytes.

The same comparison passes from an exported bundle in a fresh Python subprocess.
Six container tests add real-machine round-trip and malformed-archive controls;
all thirteen machine/container tests pass. Five negative controls
damage RAM, source identity, PIT schema, disk identity or buffer inventory; all
are refused before creating the destination directory, and the source machine
capture remains identical. These tests establish this scenario, not exhaustive
instruction/device/gameplay parity. Actual Pyro restart,
RPC integration, atomic installation and independent review remain required.

## Live-object installation primitive

`machineinstall.install_machine` consumes a validated detached machine while
preserving the live CPU state, device, port-map, timer callback and keyboard-lock
identities. This keeps existing frontend and debugger references attached to
the restored state. It rebinds the standard BIOS hook to the live video object,
rebuilds memory routing and activates the saved host RNG. Adapter changes are
refused before mutation. The caller must stop execution, exclude input/display
threads and reset pending debugger operations and host display caches.

Two installation tests pass: full synthetic continuation equals the original
trace/state after restoring into the same live objects, and incompatible
motherboards are refused without changing the source. All fifteen focused
machine/container/installation tests pass. This primitive is not yet wired to
RPC or running transports; those synchronization and restart tests remain open.

## Review corrections

Capture now rejects arbitrary BIOS callbacks instead of silently replacing
one during restoration. `biosservice.VGAInterruptService` is the shared,
identifiable implementation used by main, reconstruction and installation.
Capture also rejects more than two disks, matching the restore contract.
Tests now cover ROM reads, a real restored INT 10h mode query, a two-disk
motherboard with a host-directory disk, checked reference disks, and all
bus/PIC/DMA/keyboard links. Eighteen focused cases pass. These address the
independent review of the initial library; live RPC remains the next slice.

## RPC integration

`machine.snapshot.export/import` now coordinate stopped-machine capture and
installation. VNC and Telnet use a locked display proxy; keyboard input uses its
existing lock. Restore retains live objects, bumps the display epoch and clears
stale host debugger operations/traces/video snapshots. Four production-handler
tests pass, covering replay equality, register bindings, VNC invalidation and
running/revision/hash rejection. Live socket and Pyro restart evidence is still
pending; earlier library-only wording above describes the earlier stage.

## Review hardening and live evidence

The actual Pyro run at head74b4b3b now has a successful fresh-process replay:
3,319 instructions /47,708 emulated clocks, from10E6:6470 to2420:085F.
Every manifest field and binary buffer matched, including CPU, RAM, host RNG,
ports/timers, keyboard, video, both disks and ROMs. Evidence is retained in the
parent Pyro repository under `re/harness/traces/pypc-snapshot-20260922`.
The first repeat received eight external viewer scan codes and differed only
in the keyboard queue; with keyboard forwarding disabled, all state matched.
This establishes that bounded interval, not exhaustive game-mechanics parity.

Review corrections now hold keyboard/display locks throughout preparation,
reject unused/empty reference paths, clear the CPU breakpoint-skip flag, and
publish completed archives atomically without overwriting. Archive destinations
must support hard links (NTFS/ext4); write failure leaves no published archive.
Source identity is captured when the module loads: later source-file edits must
not relabel an already-running emulator. Telnet refresh accepts clock rewind and
uses the display epoch so equal-clock restores also repaint.29 focused tests
pass, including delayed input, failed archive writes and Telnet rewind.

Two review concerns are contract limitations, not silent equivalence claims:
failed disk materialization may leave a partial *new* directory, as documented;
existing disks/live state are unchanged. VNC network writes happen outside the
render lock, so a previously sampled frame can arrive after an import reply.
The new epoch forces a full subsequent frame; RPC completion is not a barrier
on a separate network connection. No network session or packet queue is saved.
The hardened head still requires its own full suite, CI and review confirmation.
