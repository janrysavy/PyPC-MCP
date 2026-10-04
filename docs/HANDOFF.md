# Handoff - PyPy Windows snapshot paths, 2026-10-04

FINISHED local disk snapshot fix: read unfollowed link metadata without
Path.is_junction (PyPy Windows lacks stat.IO_REPARSE_TAG_MOUNT_POINT).
27 disk tests pass on CPython; four new stdlib tests pass on Windows PyPy,
including real junction/symlink refusal and exact ordinary-host roundtrip.
Fresh-process disk test supplies DEVNULL stdin for Windows runner handles.
WIP: review, public CI/PR integration and fresh game snapshot/restart proof.
No CPU/game semantics or snapshot format change.

# Handoff - DOSCTRL persistent RPC, 2026-10-02

FINISHED local controller fix: reuse one JSON-lines TCP channel for mailbox
polling; close it on uncertainty without replaying a request. CLI and dos_session
close explicitly. A real shared-main TP6 batch failed with Windows socket10048
after12 compiles. Independent 500-poll probe measures500 connections before
and1 after; docs/dosctrl_persistent_rpc_20261002.json.52 focused controller
tests pass, including deadline and no-replay controls.
WIP public CI/PR integration and a fresh complete parent TP6 batch. No game
semantics/CPU/device change. Earlier compiler products remain a failed checkpoint.

# Handoff - public input export refreshed, 2026-09-30

FINISHED: PR63 exports the frozen PyPC execution revision ec610f3 and final
merged pynasm 96dc887, plus the unchanged other public pins. Fourteen exporter
tests pass locally. No emulator/runtime source changed. This is an explicit
public-only delivery snapshot, not a full parent prepare or compiler proof.
Fetch the current parent's exact recursive pins through the connector for a
new workspace; the export snapshot must not be silently substituted for them.

FINISHED: all seven CI jobs passed on 9d8f1e9; PR63 merged as 1eea5c0.
No runtime implementation changed. Parent pin integration separately verifies
the complete frozen execution-input subset against retained compiler receipts.
Private compiler inputs are never packaged by this public exporter.

Previous runtime handoff follows:

# Handoff, 2026-09-27

Snapshot archive regression fixed: UART machines produce v3 but the ZIP boundary
accepted only v1/v2, rejecting live exports with invalid machine bundle inventory.
The mailbox/UART test now writes, reads and restores the actual archive, retaining
pending RX bytes. This test failed before accepting v3 at the archive boundary.

COM1 register device, serial RPC and machine snapshot v3 are implemented.
Local Windows suite: 1435 tests passed, 23 skipped, 246 subtests.
Additional fresh-process UART queue restart and API checks passed (9 focused).
Use pytest --capture=sys on Windows: fd capture caused invalid inherited stdin
handles in subprocess tests.
Live BIOS detected 03F8; SYMDEB serial R/BP/G/T/U/DB/Q worked on original Pyro,
and a freshly compiled TP6 keyboard probe returned AX=BX=1E61.
Parent controller/docs are being finalized. Serial emulation is flow-controlled,
not a physical baud/timing model. See README.md and JSON_RPC_API.md.
