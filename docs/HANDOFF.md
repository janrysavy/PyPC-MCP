# Handoff - CGA mirrored observation aperture, 2026-10-06

Independent review found coherent observation refused CGA BC000..BFFFF even
though the bus maps that upper mirror. Safe peeks now wrap at16 KiB, respecting
higher-priority overlays. Eight new observation tests pass on CPython/PyPy,
including both mirror boundaries and an overlapping ROM. The unchanged-state
test covers its captured CPU clock/IP/RAM/video/control fields, not arbitrary
unattached devices. Follow-up independent review/CI remain pending.

# Handoff - coherent stopped observation, 2026-10-06

FINISHED local state.observe: paused/revision-guarded registers, up to16 safe
memory windows (65536 bytes aggregate), optional text/raw text VRAM in one RPC.
RAM/ROM/text backing peeks respect bus priority; unsafe MMIO/planar accesses
refuse without consuming device reads. No CPU/device/input/snapshot mutation.
Seven new tests pass on Windows CPython/PyPy, including production TCP errors,
CGA/VGA storage, invalid later requests, bus overlays and stale/running guards.
PyPy persistent-TCP benchmark: identical four-window/register/text results;
six calls92.26ms median versus one15.37ms over100 alternating pairs after25
warmups. This paused test-machine result is not gameplay/CPU throughput.
Receipt: tests/evidence/observation-pypy-windows-20261006.json.
Windows full regression:1489 passed,23 skipped,320 subtests (DEVNULL stdin;
pinned GLaBIOS initialized). Independent review and final-head public CI/PR
integration remain WIP. Parent must validate original-game captures after pinning.
Existing CGA nonzero CRTC start word/byte conversion needs a separate fix.

# Handoff - CPU trace interrupt dispatch, 2026-10-04

FINISHED local fix: standalone accepted PIC entry is interrupt_dispatch with
actual IRQ/vector, not execution of interrupted opcode. HLT wake-up is covered;
software INT/TF remain instruction events. Trace budgets still count CPU ticks.
CPU/device cycle semantics unchanged. Seven new tests (24 subtests) pass on
CPython/PyPy;63 related tests pass. The preceding full Windows run reports
1482 passed,23 skipped,298 subtests passed. Focused review found no runtime
defect; its HLT/device evidence gaps are closed by executing F4, a real PIT
edge while masked, unmask/wake and once-per-Tick device-clock assertions.
The follow-up review/CI are pending on this test-only refinement.
The earlier CRT live receipt independently reproduced this defect; a fresh
original Pyro/PyPy run again stores calibration164 and now labels IRQ entry.
Live wire proof awaits parent integration. Guest/viewer closed.
WIP: focused review, final-head CI/PR and parent pin integration.
General PIT phase prediction and physical XT cycle accuracy remain unproven.

# Historical handoff - optional joystick control, 2026-10-04

FINISHED local implementation: --game-port attaches two analog two-button sticks
at201h; input.joystick/state use the shared MartyPC axes/buttons schema.
Paused writes are atomic, do not advance clocks, and active-low button/charge
state survives actual version4 ZIP restoration with and without UART.
Six tests pass on CPython and PyPy, including fresh-process CPU I/O continuation;
44 related codec/archive/install/RPC tests plus15 subtests pass on CPython.
Default launches and version1/2/3 layouts omit this optional hardware.
The charge model follows MartyPC; physical XT timing parity is not established.
FINISHED: focused review addressed; actual TCP guest OUT/IN201h and live v4
export/install during a charge pass under PyPy in the parent workspace.
Pyro calibration/gameplay and physical XT timing remain WIP. Final-head CI
and linear integration are tracked by PR67; parent records the integrated pin.
Follow-up: inventory-order regression fixed;22 RPC/documentation tests plus13
subtests pass. No device behavior changed.
Restore boundary: seventh test verifies production installation preserves the
input device reference and refuses both card-presence mismatches before disk
creation/live mutation. Seven tests pass on CPython and PyPy.

Previous disk fix is integrated at1b97ac1; its old WIP note below is historical.

# Handoff - PyPy Windows snapshot paths, 2026-10-04

FINISHED local disk snapshot fix: read unfollowed link metadata without
Path.is_junction (PyPy Windows lacks stat.IO_REPARSE_TAG_MOUNT_POINT).
27 disk tests pass on CPython; four new stdlib tests pass on Windows PyPy,
including real junction/symlink refusal and exact ordinary-host roundtrip.
Fresh-process disk test supplies DEVNULL stdin for Windows runner handles.
Independent review found no runtime defect; its evidence gap is closed by
removing the constant in the real junction test too and retaining both logs
under tests/evidence/diskcodec-links-*-20261004.txt. 27 + 4 tests pass again.
WIP: final-head public CI/PR integration and fresh game snapshot/restart proof.
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
