# PyPC JSON-RPC 2.0 API

This document is the complete contract for the AI/debug control channel built into
PyPC. It follows the DOSBox-X agent convention of using JSON-RPC 2.0 method names
and JSON-lines framing. It also records the portability audit against the
DOSBox-X debugger-agent API. PyPC supports CGA and VGA text plus standard VGA
graphics modes 12h and 13h; the adapter is selected when the emulator starts.

## Transport

The emulator listens only on the local machine:

```text
TCP 127.0.0.1:2301
```

Use `--rpc-port`, `--telnet-port`, and `--vnc-port` to run another instance
alongside an existing one. Defaults are 2301, 2300, and 5902 respectively.
`--dos-mailbox` optionally reserves physical `D8000h-D9FFFh` for the DOS
control worker. It uses this same RPC endpoint through `memory.read/write`;
see `guest/README.md`.
Ports must be distinct integers in 1..65535. `agent.capabilities.endpoint`
reports the selected RPC port. Each instance should use its own writable disks.

With JSON-RPC enabled (the default), Telnet and VNC are view-only: their keyboard
messages do not enter the guest queue. Use `input.keyboard` or `keyboard.scancode`
for controlled input. This prevents a frontend thread from changing input during
a guarded observation or snapshot, matching MartyPC's controlled-input policy.
For ordinary manual play, `--no-rpc` disables the RPC listener and enables normal
Telnet/VNC keyboard input. There is no concurrent frontend-input/RPC-control mode.

Each request and response is one UTF-8 JSON object terminated by `LF` (`\n`). A
client may keep the connection open and send multiple requests. Requests are
executed by the CPU thread at an instruction boundary. The returned state is
therefore coherent; a request is never served from a concurrently changing CPU
state. The service is local-only and has no authentication.

Every request uses this envelope:

```json
{"jsonrpc":"2.0","id":1,"method":"state.get_registers","params":{}}
```

Successful responses have `result`; failures have `error`:

```json
{"jsonrpc":"2.0","id":1,"result":{}}
{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"..."}}
```

Supported JSON-RPC error codes are `-32700` (invalid JSON), `-32600` (invalid
request), `-32601` (unknown method), `-32602` (invalid parameters), and `-32603`
(handler error). Notifications without an `id` are accepted and produce no reply.

Numbers may be JSON integers or strings accepted by Python `int(value, 0)`, such as
`123`, `"123"`, `"0x7B"`, or `"0b1111011"`.

## Method inventory

| Method | Purpose |
| --- | --- |
| `agent.capabilities` | Return this service's supported methods and limits. |
| `emulator.info` | Alias of `agent.capabilities`. |
| `state.get_registers` | Read the 8088 registers and execution state. |
| `serial.status` | Read COM1 configuration and queue offsets. |
| `serial.write` | Queue host bytes for COM1 receive. |
| `serial.read` | Read COM1 output without consuming it. |
| `state.get` | Alias of `state.get_registers`. |
| `state.observe` | Capture registers, bounded safe memory windows and optional video at one stopped revision. |
| `state.set_registers` | Guarded register write while paused. |
| `session.status` | Report the fixed live PyPC session and execution state. |
| `memory.read` | Read a bounded physical/linear/segmented memory block. |
| `memory.write` | Guarded memory write while paused. |
| `video.text` | Read the active CGA or VGA text screen directly from video memory. |
| `video.snapshot` | Capture immutable video-memory, graphics VRAM, font, and decoded-text bytes. |
| `video.snapshot.read` | Read a bounded component from a retained video snapshot. |
| `video.history.start` | Start an optional ordered text VRAM/font/port write journal. |
| `video.history.read` | Page retained video events and report any overflow. |
| `video.history.stop` | Stop recording while retaining readable events. |
| `io.read` | Read one byte from an emulated I/O port. |
| `io.write` | Write one byte to an emulated I/O port while paused. |
| `input.keyboard` | Queue XT keyboard make/break scan codes. |
| `keyboard.scancode` | Single-event alias of `input.keyboard`. |
| `input.state` | Read currently pressed XT scan codes. |
| `input.joystick` | Set the optional game-port axes/buttons at a paused boundary. |
| `input.joystick.state` | Read configured joystick positions/buttons; empty if absent. |
| `execution.pause` | Stop at the next instruction boundary. |
| `execution.continue` | Resume execution and return an operation id. |
| `execution.go` | Alias of `execution.continue`. |
| `execution.run_until` | Resume execution until a predicate or guest-time limit matches. |
| `execution.wait` | Poll a continue, run-until or step operation. |
| `execution.step` | Accept one CPU Tick; poll its operation for the actual stop. |
| `breakpoints.create` | Create an execution, memory-access, or software-interrupt breakpoint. |
| `breakpoints.list` | List active breakpoints and hit counts. |
| `breakpoints.delete` | Delete a breakpoint. |
| `trace.start` | Start a bounded CPU instruction trace while paused. |
| `trace.read` | Page retained CPU trace events. |
| `trace.stop` | Stop a CPU trace and report its retained event count. |
| `hardware.trace.start` | Start a bounded port-I/O and/or PIC IRQ trace. |
| `hardware.trace.read` | Page retained hardware events and report any overflow. |
| `hardware.trace.stop` | Stop hardware tracing without discarding retained events. |
| `machine.snapshot.export` | Persist a paused complete-machine bundle to a new host archive. |
| `machine.snapshot.import` | Validate and restore a complete-machine bundle into fresh host disk paths while remaining paused. |

## `agent.capabilities`

No parameters.

Example request:

```json
{"jsonrpc":"2.0","id":1,"method":"agent.capabilities","params":{}}
```

Result:

```json
{
  "protocol":"JSON-RPC 2.0 over localhost JSON-lines",
  "endpoint":"127.0.0.1:2301",
  "cpu":"8088",
  "memory_bytes":1048576,
  "video_adapters":["CGA","VGA"],
  "address_spaces":["physical","linear","segmented"],
  "limits":{"max_memory_bytes":65536,"max_keyboard_events":32,
    "max_trace_events":65536,"max_observation_windows":16,
    "max_observation_memory_bytes":65536,"retained_video_snapshots":8},
  "methods":["agent.capabilities","emulator.info","state.get_registers",
    "serial.status","serial.write","serial.read",
    "state.get","state.observe","state.set_registers","session.status","memory.read",
    "memory.write","video.text","video.snapshot","video.snapshot.read",
    "video.history.start","video.history.read","video.history.stop",
    "io.read","io.write","input.keyboard","keyboard.scancode","input.state",
    "input.joystick","input.joystick.state",
    "execution.pause","execution.continue","execution.go",
    "execution.run_until","execution.wait","execution.step",
    "breakpoints.create","breakpoints.list","breakpoints.delete",
    "trace.start","trace.read","trace.stop",
    "hardware.trace.start","hardware.trace.read","hardware.trace.stop",
    "machine.snapshot.export","machine.snapshot.import"]
}
```

## `emulator.info`

No parameters. Returns exactly the same result as `agent.capabilities`.

## `session.status`

The PyPC process has one implicit session, so no `session_id` is required. A
caller may provide `{"session_id":"pypc"}` for compatibility; another value is
rejected. Result:

```json
{
  "session_id":"pypc",
  "state":"running",
  "state_revision":12345,
  "clock":123456789,
  "target":{"cpu":"8088","memory_bytes":1048576,"video":"CGA"},
  "last_stop":null
}
```

`state` is `running` or `stopped`. `stopped` means paused by the execution
control API, not that the guest program exited.

## `state.get_registers`

No parameters. `state.get` is an exact alias.

Result:

```json
{
  "general":{"ax":0,"bx":0,"cx":0,"dx":0,"sp":0,"bp":0,"si":0,"di":0},
  "segments":{"cs":61440,"ds":0,"es":0,"ss":0},
  "ip":65520,
  "flags":2,
  "flags_text":"--------",
  "clock":0,
  "in_hlt":false,
  "state_revision":0
}
```

Register values and `clock` are unsigned integers. `state_revision` advances after
each executed instruction and after a successful paused-state mutation through
`state.set_registers`, `memory.write`, or `io.write`. It identifies the snapshot
boundary at which the read was made. Unlike the full DOSBox-X service, PyPC permits
this read while the CPU is running because the request is executed synchronously at
an instruction boundary.

## `state.get`

No parameters. Exact alias of `state.get_registers`; it returns the same result
object.

## `state.set_registers`

Writes one or more 16-bit registers while paused. Every register being changed
must be guarded by its current value, and the whole request must use the current
`state_revision`.

Parameters:

```json
{
  "expected_state_revision":12345,
  "expected":{"ax":30},
  "set":{"ax":31}
}
```

Supported names are `ax`, `bx`, `cx`, `dx`, `sp`, `bp`, `si`, `di`, `cs`, `ds`,
`es`, `ss`, `ip`, and `flags`. A revision or register mismatch rejects the whole
request and performs no write. Result:

```json
{
  "before":{"general":{"ax":30},"state_revision":12345},
  "after":{"general":{"ax":31},"state_revision":12346}
}
```

The abbreviated example represents the complete register objects returned by
`state.get_registers`.

## `state.observe`

A read-only coherent capture. Requires a paused target and the exact current
`expected_state_revision`; the complete request is validated before copying any
bytes. The CPU thread handles the entire observation at one instruction boundary.
It does not advance clocks, alter registers, queue input, consume device data or
allocate a retained video snapshot.

```json
{"expected_state_revision":12345,
 "memory":[{"address":{"space":"segmented","segment":"0x2600","offset":0},"length":32768},
           {"address":"0xB8000","length":4000}],
 "video_text":{},"video_memory":true}
```

`memory` defaults to an empty list: at most16 windows, each1..65536 bytes,
at most65536 bytes in total, within1MiB. Address forms match `memory.read`.
Unknown request/window fields and non-boolean `video_memory` are rejected.
`video_text`, if supplied, must be an object containing only optional `page` or
`display_address`, with the same selection rules and result as `video.text`.
The two selector keys are mutually exclusive, even if equal or null.

Result contains `state_revision`, `registers` (the existing
`state.get_registers` result) and `memory` (ordered `memory.read`-shaped
results: address, byte_count, data_hex, data_base64, sha256, state_revision).
Requested `video_text` and `video_memory` results share that same revision.
`video_memory` returns the entire text backing store, with the same byte/digest
fields plus `address` and `adapter`; its bytes are separate from the65536-byte
memory-window budget. It is not font memory or graphics-plane memory.

Observation resolves bus mapping priority and copies safe RAM, immutable ROM
and the current adapter's ordinary text backing directly. It deliberately
refuses unsupported MMIO and VGA planar accesses rather than calling a device's
possibly consuming read function or returning hidden underlying RAM. For other
video planes/fonts use the existing immutable `video.snapshot` contract.
`memory.read` retains its existing bus-backed behavior.

The reproducible benchmark `python benchmarks/benchmark_observation_rpc.py`
compares six separate calls with one coherent call over persistent localhost
TCP, requiring identical register/RAM/text results. It uses a paused test
8088+CGA; its results measure observation overhead, not game/emulation speed.

## `memory.read`

Parameters:

| Name | Type | Required | Rules |
| --- | --- | --- | --- |
| `address` | integer, string, or address object | yes | Start address. |
| `length` | integer | no | Defaults to `1`; range `1..65536`. |

An address object has this shape:

```json
{"space":"physical","offset":"0xB8000"}
```

`space` is `physical`, `linear`, or `segmented`. For `segmented`, supply
`{"space":"segmented","segment":"0xB800","offset":"0x0000"}` and the
physical address is `(segment * 16 + offset) & 0xFFFFF`; both fields
must be16-bit values. Physical/linear address forms do not wrap. All accesses must remain within the
1 MiB address space. Device mappings are observed through the emulator bus.

Result:

```json
{
  "address":753664,
  "byte_count":4,
  "data_base64":"SGVsbG8=",
  "data_hex":"48656c6c",
  "sha256":"...",
  "state_revision":12345
}
```

`data_base64`, `data_hex`, and `byte_count` describe the same bytes. `sha256` is
the lowercase SHA-256 digest of the returned bytes.

## `memory.write`

Writes a bounded block while paused. It uses the same `address` object as
`memory.read` and requires base64 data:

```json
{
  "address":{"space":"physical","offset":"0xB8000"},
  "data_base64":"SGk=",
  "expected_sha256":"..."
}
```

`expected_sha256` is optional. When supplied, the write is performed only if it
matches the current bytes. Result:

```json
{
  "address":753664,
  "byte_count":2,
  "before_sha256":"...",
  "after_sha256":"...",
  "state_revision":12346
}
```

The request is atomic with respect to the emulator thread. `data_base64` must
decode to `1..65536` bytes and the range must remain inside 1 MiB.

## `video.text`

Reads a 25-row text page directly from the active adapter's video-memory backing
store, including the current CRTC display offset. With no parameters it reads
the active page.
Optional parameters select another bank/page without changing emulated hardware:

```json
{"page":1}
```

or:

```json
{"display_address":"0x0FA0"}
```

The selector keys `page` and `display_address` are mutually exclusive. Supplying
both returns JSON-RPC error `-32602`, even if their values are equal or null;
no emulated state is changed. This also applies to nested `state.observe.video_text`.

The page size is `columns * 25 * 2` bytes. CGA has 16 KiB of text/graphics RAM,
so it exposes four 80-column pages or eight 40-column pages. VGA has 32 KiB of
text RAM, so it exposes eight 80-column pages or sixteen 40-column pages. A
text-mode program can render into one page and flip the CRTC start
address to another; `active_page`, `page`, and `is_active_page` make that flip
observable. A page read never changes the CRTC or the display.
`display_address` is always a byte offset in the adapter's video memory; the
VGA CRTC's native word-addressed start register is converted before it is
reported here.

Result:

```json
{
  "adapter":"CGA",
  "columns":40,
  "rows":25,
  "page_size_bytes":2000,
  "page_count":8,
  "page":0,
  "active_page":0,
  "is_active_page":true,
  "display_address":0,
  "mode":"Text40",
  "graphics_mode":2,
  "text":["Example text row", "Another row", "..."],
  "cells":[[
    {"code":87,"char":"W","attribute":31,"foreground":15,
     "background":1,"blink":false}
  ]],
  "state_revision":12345
}
```

The example abbreviates the array; `text` always contains exactly 25 strings. Each string is trimmed on the right; CP437
characters are decoded to Unicode replacement-safe text. `cells` always contains
25 rows with `columns` cells per row. Each cell reports the raw character byte and
raw attribute byte. The low four attribute bits are the foreground palette index;
bits 4..6 are the background palette index. On VGA with blink enabled, bit 7 is
reported as `blink`; with VGA blink disabled, bit 7 is the fourth background bit.
`columns` is `40` or `80`. In graphics modes the text and cell arrays are still
the raw character interpretation of video memory and should not be treated as a
graphical screenshot. VGA additionally returns a `cursor` object with the CRTC
cursor address, shape, enable state, and current blink phase.

## `video.snapshot`

Captures immutable adapter video memory and decoded text bytes at one CPU-thread
boundary. The snapshot retains raw VRAM, so character attributes and all render
pages remain available even after the live screen changes. VGA snapshots also
retain the 64 KiB plane-2 font memory. In mode 12h or 13h they additionally
retain `graphics_vram`: mode 12h is four concatenated 64 KiB planes in plane
order 0..3; mode 13h is the 64 KiB guest-visible chain-4 A0000h byte order.
At most eight snapshots are retained; older snapshots expire.

No parameters. Result:

```json
{
  "snapshot_id":"snap-1",
  "state_revision":12345,
  "captured_ticks":123456789,
  "adapter":"CGA",
  "video_mode":8,
  "columns":40,
  "rows":25,
  "display_address":0,
  "active_page":0,
  "text":{"byte_count":999,"sha256":"..."},
  "vram":{"byte_count":16384,"sha256":"..."}
}
```

The `graphics_vram` fields appear only in VGA modes 12h and 13h. VGA results
also include a `font` component and `cursor` metadata. The VGA font component
is 65536 bytes and contains plane 2 as exposed through the text-mode font
aperture. A mode 13h snapshot adds:

```json
{
  "graphics_vram":{"byte_count":65536,"sha256":"..."},
  "graphics_vram_layout":"chain4_guest_order"
}
```

For mode 12h, `graphics_vram.byte_count` is 262144 and the layout is
`planes_0_to_3_concatenated`.

## `video.snapshot.read`

Reads one retained snapshot component. Parameters:

```json
{"snapshot_id":"snap-1","component":"vram","offset":0,"length":64}
```

`component` is `vram`, `text`, or `graphics_vram`; VGA additionally supports
`font`. Result includes both whole-component and chunk metadata:

```json
{
  "snapshot_id":"snap-1","component":"vram","offset":0,
  "byte_count":64,"component_byte_count":16384,
  "component_sha256":"...","data_base64":"...","sha256":"..."
}
```

## `video.history.start`, `video.history.read`, `video.history.stop`

`video.text` and `video.snapshot` cannot recover characters that appeared and
were overwritten between requests. `video.history.start` enables an opt-in
ordered journal of changed text VRAM bytes, VGA font-plane bytes, video port
writes, and VGA text clear/mode events. Its `capacity` is 1..1,000,000 events
(default 500,000). The start result includes `history_id`, `clock`, display
metadata, and base64 initial text VRAM with SHA-256. VGA also includes the
initial plane-2 font. The start call is handled at an instruction boundary.

`video.history.read` takes `{"history_id":"video-1","cursor":null,"limit":1024}`.
It returns ordered `events` with `sequence`, emulated `clock`, `kind`, `address`,
`old`, and `new`, plus `next_cursor`, `first_available_sequence`, and
`lost_events`. For `text` and `font`, `address` is the byte offset and `old`/
`new` are byte values. For `port`, `address` is the port, `old` is transfer
width (1 or 2), and `new` is the value. `clear_text` resets text VRAM to zero;
`mode` gives the new mode number, columns, and display offset in those three
fields. Pass `next_cursor` to read the next page. When `next_cursor` is null
but recording continues, pass `next_sequence - 1` in a later poll to receive
only newer events. A cursor older than retained events is rejected. A nonzero
`lost_events` means history is incomplete.

`video.history.stop` disables new recording and returns the event/loss counts;
the retained stream remains readable until another start or machine restore.
Recording is off by default. This is text display-state history, not a DOS
stdout stream or a semantic scrollback parser. Graphics VRAM writes are not
journaled. Clients must drain and persist pages before ring overflow if they
need a complete run. Snapshot restore invalidates the stream.

## `io.read`

Parameters:

```json
{"port":"0x3D8"}
```

`port` is a required 16-bit integer. Result:

```json
{"port":984,"value":9,"state_revision":12346}
```

The read uses the same emulated I/O dispatch as an 8088 `IN` byte operation. It may
have device-specific read side effects. Every successful read advances
`state_revision` once, including ports whose particular read leaves no observable
change; this operation does not infer device purity or execute a CPU instruction.
Invalid port parameters leave the machine and revision unchanged. Reads remain
allowed while running or paused. Use `io.write` for explicit paused-state mutation.

## `io.write`

Writes one byte through the same emulated I/O dispatch as an 8088 `OUT` byte
operation. The emulator must be paused because device writes can change hardware
state immediately. Parameters are:

```json
{"port":"0x3D8","value":"0x09"}
```

The result reports whether a registered device handled the port. Unhandled ports
are still passed through the emulator's normal `OUT` behavior and report
`handled:false`:

```json
{"port":984,"value":9,"handled":true,"state_revision":12346}
```

## `input.keyboard`

Parameters:

```json
{
  "events":[
    {"scan_code":"0x4D","pressed":true},
    {"scan_code":"0x4D","pressed":false}
  ]
}
```

`events` is required and contains `1..32` objects. `scan_code` is an XT make code
from `0x00` through `0x7F`; `pressed:true` queues the make code and `pressed:false`
queues the corresponding break code (`scan_code | 0x80`). Events are queued in
the listed order. Result:

```json
{"accepted":2,"state_revision":12346}
```

Keyboard requests are allowed while running or paused. The complete batch
is validated before any event is enqueued. Each successfully validated batch
increments `state_revision` once without advancing the CPU. Invalid batches
leave the queue, pressed state and revision unchanged. `keyboard.scancode`
defaults omitted `pressed` to true.

## `input.state`

No parameters. Result reports raw XT make codes currently held according to the
keyboard device, plus configured joystick positions/buttons. Without `--game-port`,
the joystick list is empty:

```json
{
  "keyboard":{"pressed_scancodes":[77]},
  "joysticks":[],
  "state_revision":12345
}
```

The implementation returns scan-code integers; the fork’s named-key layer is not
portable to this XT-only keyboard model.

`pressed_scancodes` is the host-enqueued make/break state, not proof that DOS
or the target consumed a key.

## `input.joystick` and `input.joystick.state`

Launch with `--game-port` to attach two analog two-button sticks at I/O port201h.
Setting input requires a paused machine; reading state does not. A complete update is:

```json
{"joystick":0,"x":-1,"y":1,"buttons":[true,false]}
```

Indices are 0 and 1. Axes are finite numbers from -1 to +1: left/up to right/down.
Buttons are exactly two booleans. All fields are validated before mutation.
Both methods return `joysticks` (two records in the above format) and
`state_revision`; setting input increments the revision without advancing the CPU.
An absent card rejects writes and returns an empty list for reads.

The device follows MartyPC's configured 100-kilohm potentiometer model:
25.2 + 550 * (axis + 1) microseconds per charge. PyPC samples expiration at its
instruction/device boundaries using 4.77 CPU cycles per microsecond. Buttons
are active low. OUT201h starts all four one-shots; movement does not rearm an
expired axis. This is emulator-model compatibility, not physical XT timing proof.
Default launches omit the card and retain their existing hardware configuration.

## `keyboard.scancode`

Single-event convenience alias. It accepts the same event fields directly:

```json
{"jsonrpc":"2.0","id":2,"method":"keyboard.scancode",
 "params":{"scan_code":"0x4F","pressed":true}}
```

The result is the same as `input.keyboard` with `accepted:1`.

## `breakpoints.create`

Creates an execution breakpoint checked before each 8088 instruction, a
`memory_read`, `memory_write`, or combined `memory_access` breakpoint checked
after the instruction performs the access, or an `interrupt` breakpoint checked
before a software interrupt handler. Execution addresses may be `physical`,
`linear`, or `segmented`; memory access addresses may be `linear` or
`segmented`:

```json
{
  "kind":"execution",
  "address":{"space":"segmented","segment":"0x1000","offset":"0x0020"},
  "once":true,
  "condition":{"register":"ax","operator":"eq","value":"0x004c"},
  "hit_filter":{"skip":0,"every":1}
}
```

Supported condition registers are the 8088 general, segment, `ip`, and `flags`
registers. Supported operators are `eq`, `ne`, `lt`, `le`, `gt`, and `ge`.
`hit_count` counts condition matches; `skip` suppresses the first matches and
`every` selects subsequent matches. A one-shot breakpoint is removed when it
stops execution. The result is the normalized breakpoint descriptor, including
its `breakpoint_id`. Memory descriptors accept a positive contiguous `length`
within the 1 MiB address space and do not accept register conditions. A stop
includes `access` with `kind`, the actual linear byte address, byte count, old
and new values, and the accessing instruction address. Reads report identical
old and new values. Device-backed writes expose no old value when the device
cannot be read without side effects. An `interrupt` descriptor uses
an event selector such as `{"type":"software_interrupt","number":"0x21",
"ah":"0x4c"}`; its stop reports the actual `AH`/`AL` and
`phase:"before_handler"`. Interrupt conditions use the same register operators
as execution breakpoints.

## `breakpoints.list`

No parameters. Returns `{ "breakpoints": [...] }`, including normalized addresses,
conditions, hit filters, and current hit counts.

## `breakpoints.delete`

Parameters:

```json
{"breakpoint_id":"bp-1"}
```

Returns `{ "breakpoint_id":"bp-1", "deleted":true }`.

When a breakpoint stops execution, `session.status.last_stop` contains
`kind:"breakpoint"`, the breakpoint id, normalized address, hit count, and the
coherent register snapshot. Resuming an execution or pre-dispatch software-INT
stop skips that breakpoint for one CPU Tick so execution can proceed. Memory
watchpoints stop after the access; resuming does not suppress the next access.

## `execution.pause`

No parameters. Requests that execution stop at the next instruction boundary.
When the request returns, no further CPU instruction is executed until a continue
or step request. If already stopped, pause is read-only: it preserves the last stop,
operation results, revision and breakpoint resume context. Result:

```json
{"paused":true,"general":{},"segments":{},"ip":0,"flags":0,
 "flags_text":"","clock":0,"in_hlt":false,"state_revision":12345}
```

The register fields in the result are exactly those of `state.get_registers`.

## `execution.continue`

No parameters. Resumes execution and returns the current register object with
`paused:false`, plus an operation handle:

```json
{"operation_id":"op-1","state":"running","paused":false}
```

The operation completes when execution reaches a breakpoint, a run-until
predicate, or an explicit `execution.pause`. `execution.go` is an exact alias.

## `execution.go`

No parameters. Exact alias of `execution.continue`.

## `execution.run_until`

Requires a paused emulator. Parameters contain one `execution`, `memory_read`,
`memory_write`, `memory_access`, or `interrupt` predicate using the same fields
as `breakpoints.create` (memory predicates cannot use conditions):

```json
{
  "predicate":{
    "kind":"execution",
    "address":{"space":"segmented","segment":"0x1000","offset":"0x0020"},
    "condition":{"register":"ax","operator":"eq","value":"0x004c"}
  }
}
```

The predicate is private and one-shot. The result contains `operation_id`,
`predicate_id`, and `state:"running"`. Optional positive `max_emulated_ns`
sets a guest-time deadline using the 8088's emulated cycle clock. If the
deadline wins, the stop kind is `emulated_time_limit` and the stop includes
requested, start, deadline, actual, reached, and overshoot nanoseconds.

## `execution.wait`

Polls an operation returned by `execution.continue`, `execution.run_until` or
`execution.step`. Required `operation_id` identifies that operation. Optional
`timeout_ms` defaults to 0 and must be in 0..60000; the single-threaded server
does not block inside the handler. Use repeated polls capped by the controller
wall deadline. A pending operation returns:

```json
{"running":true}
```

A completed operation returns `state:"stopped"` and its retained `stop_reason`.
Read actual completed registers from `stop_reason.registers`; accepted step or
continue replies describe entry state. Later stops can change
`session.status.last_stop` without changing this operation's result. Up to 256
operations are retained; unknown or evicted IDs are invalid parameters.

## `trace.start`

Requires a paused emulator. Starts a bounded trace for `instruction_count` steps
(default `256`, maximum `65536`):

```json
{"detail":"normal","instruction_count":128}
```

Supported detail levels are `csip`, `short`, `normal`, and `long`. Events include
`kind` (`instruction`, `hlt`, or `interrupt_dispatch`), the segmented and physical instruction address,
eight raw opcode bytes, and the emulated clock interval. HLT events represent
clock advancement while the CPU is waiting for an interrupt; their opcode bytes
are context, not an executed instruction. A standalone accepted PIC interrupt
is `interrupt_dispatch` with `interrupt: {source: "pic", irq: 0, vector: 8}`
(numbers reflect the accepted line and configured vector). Its address/opcode
identify the interrupted context, including HLT wake-up; that opcode did not
execute in this event. The event retains interrupt stack/vector memory effects
and its clock cost. Software INT and post-instruction TF traps stay `instruction`
events because an opcode executed. The historical `instruction_count` budget
counts CPU Tick boundaries, including dispatch and HLT; it is not a count of
executed opcodes. All levels except `csip` include the
register snapshot before the event; `normal` and `long` also include the snapshot
after it. Every event also has ordered `effects`. Data-memory effects are
`memory_read` or `memory_write` with a linear address, byte count, and base64
payload; I/O effects are `io_read` or `io_write` with port, byte count, value,
and whether the port was handled by a device. Instruction fetches are not
reported as memory effects.

## `trace.read`

Reads retained events using a cursor:

```json
{"cursor":null,"limit":128}
```

The result contains `events`, `event_count`, `active`, `detail`, and an optional
`next_cursor` such as `trace-128`. Optional `limit` defaults to 128 and must
be in 1..256. Cursors are local to the current trace and must identify an offset
within retained events. Reading or stopping before a trace starts is refused;
starting another active trace is refused. A new start replaces the old trace.

## `trace.stop`

Stops recording without discarding retained events and returns `active:false` and
the final `event_count`. A completed instruction-count trace can still be read
and stopped.

## `hardware.trace.start`

Starts a bounded hardware recorder. It may run while the CPU is running or
paused. It records port I/O and/or PIC IRQ transitions without changing the
emulated machine:

```json
{
  "capacity":1024,
  "include_io":true,
  "include_irq":true,
  "ports":[{"first":"0x3d0","last":"0x3df"}],
  "irqs":[1]
}
```

`capacity` is `1..65536`. Empty `ports` or `irqs` arrays mean all values. Port
filters apply to I/O events and IRQ filters apply to IRQ events. At least one
event class must be enabled. The result reports `active`, `capacity`,
`dropped_event_count`, and `first_available_sequence`.

## `hardware.trace.read`

Reads retained hardware events with a nullable `cursor` and bounded `limit`:

```json
{"cursor":null,"limit":128}
```

I/O events have `kind` (`io_read` or `io_write`), `emulated_time`, guest
instruction `address`, `port`, `byte_count`, and `value`. IRQ events have
`kind` (`irq_raise`, `irq_lower`, or `irq_dispatch`), `emulated_time`, guest
instruction `address`, and `irq`; dispatch events also include the interrupt
`vector`. Results include `active`, `capacity`, `dropped_event_count`,
`first_available_sequence`, `events`, and `next_cursor`. Cursors use the form
`hardware-N` and expire when the bounded recorder overwrites old events.

## `hardware.trace.stop`

Stops recording without discarding retained events. The result reports the
final recorder metadata; drain retained events with `hardware.trace.read`.

## `execution.step`

Accepts optional `{"mode":"into"}`. Requires the emulator to be paused. It
authorizes one `p.Tick()` call, subject to a breakpoint or pause winning first.
A Tick can execute an opcode, dispatch a PIC interrupt, or advance HLT waiting;
it is not necessarily an executed instruction. Step-over (`mode:"over"`) is
unsupported.

Result is the entry register object with `stepping:true` and `operation_id`
added. It acknowledges the request before execution. Use `execution.wait`
with that ID to obtain the actual completed `step`, breakpoint or pause stop
and its final registers; the acknowledgement is not post-step evidence. Calling it while
running returns `-32602`.

## Minimal Python client

Run from this repository root. The existing transport buffers complete JSON
lines, validates reply IDs and propagates errors; one socket `recv()` does not
guarantee a complete reply.

```python
from guest.dos_control import RPC

rpc = RPC(2301)
try:
    print(rpc.call("video.text"))
    print(rpc.call("input.keyboard", {"events":[
        {"scan_code":"0x50", "pressed":True},
        {"scan_code":"0x50", "pressed":False}]}))
finally:
    rpc.close()
```

## DOSBox-X API portability audit

The following is the complete method inventory from the DOSBox-X debugger-agent
API, classified for this PyPC transport:

| DOSBox-X method | PyPC status | Reason |
| --- | --- | --- |
| `agent.capabilities` | Implemented | Runtime method and limit discovery. |
| `session.status` | Implemented | Fixed implicit `pypc` session. |
| `session.start`, `session.stop` | Deferred | PyPC is attached to one already-created machine/image. |
| `execution.continue` | Implemented | Resumes the CPU loop. |
| `execution.run_until` | Implemented (execution predicates and guest-time limit) | Private one-shot execution predicate using the breakpoint matcher, with an optional emulated-time deadline. |
| `execution.wait` | Implemented (bounded polling) | Polls continue/run-until operations; timeout is non-blocking. |
| `execution.pause` | Implemented | Pauses at an instruction boundary. |
| `execution.step` | Implemented (`into`) | `over` needs temporary breakpoints. |
| `state.get_registers` | Implemented | Native 8088 state is directly available. |
| `state.set_registers` | Implemented | Guarded 16-bit register mutation. |
| `input.keyboard` | Implemented | XT scan-code batch injection. |
| `input.joystick` / `input.joystick.state` | Implemented (optional game port) | Paused analog input and read-only state. |
| `input.state` | Implemented | Raw pressed scan codes and configured joystick state; no named-key layer. |
| `dos.memory_map` | Deferred | No DOS loader/MCB metadata model. |
| `checkpoints.create/list/restore/delete` | Deferred | No complete machine snapshot serializer. |
| `video.snapshot` | Implemented (CGA and VGA text/12h/13h) | Immutable raw VRAM/text snapshot; VGA also includes plane-2 fonts and lossless graphics VRAM for modes 12h/13h. |
| `video.snapshot.read` | Implemented (`vram`, `text`, VGA `font`/`graphics_vram`) | Bounded retained-component reads. |
| `video.history.start/read/stop` | Implemented (text state) | Ordered changed VRAM/font/port events; overflow is explicit, graphics VRAM excluded. |
| `memory.read` | Implemented | Bus-backed 1 MiB reads. |
| `memory.write` | Implemented | Paused, SHA-guarded bus writes. |
| `io.write` | Implemented | Paused byte write through the emulated I/O bus. |
| `breakpoints.create` | Implemented (execution, memory-read/write/access, and interrupt subset) | Pre-instruction execution, bounded exact data-memory access, and semantic software-interrupt stops. |
| `breakpoints.list/delete` | Implemented | Lists or removes normalized execution, memory-access, and interrupt breakpoints. |
| `trace.start/read/stop` | Implemented (CPU subset) | Bounded instruction addresses, opcode bytes, clock intervals, register snapshots, and ordered data-memory/I/O effects. |
| `debug.output.read` | Deferred | No bounded diagnostic-output ring. |
| `debugger.execute_command` | Deferred | Deliberately no raw debugger command escape hatch. |
| `hardware.trace.start/read/stop` | Implemented (I/O and PIC subset) | Bounded filtered port-I/O and IRQ transition/dispatch events. |
| `dos.trace.start/read/stop` | Deferred | No DOS file-operation recorder. |

Deferred methods are intentionally omitted from the `methods` capability list and
currently return JSON-RPC `-32601`.

## Persistent machine snapshots

Both methods require a paused machine and `expected_state_revision` equal to
`session.status.state_revision`. Paths name files on the emulator host.

- `machine.snapshot.export`: `path` must be a new file. Optional `disk_mode`
  is `auto` (default), `embed`, `reference`, or `reference-files`. Returns
  `path`, archive `bytes`/`sha256` and current `state_revision`. The ZIP contains JSON plus hashed binary payloads.
- `machine.snapshot.import`: `path`, new `disk_root` directory, required archive
  legacy `sha256` or canonical `expected_sha256`, and optional `references` mapping string disk indices `0`/`1` to
  source image paths. Referenced images must match saved size and SHA-256.
  Returns the new revision and `machine_snapshot_restored` stop reason.

`reference-files` stores flat-file disks as size/hash references while embedding
host-mapped FAT disks completely, including their live image, host file contents,
sync hashes and pending synchronization state. It does not reference arbitrary
host directories. Legacy policies are unchanged: `reference` still refuses host
mounts. Import copies each verified file reference into a new writable disk;
subsequent guest writes cannot change the immutable reference.

Every import requires one independently retained archive digest: canonical
`expected_sha256` or legacy `sha256`,64 hexadecimal characters. New shared clients
use the canonical name. Supplying both aliases is rejected. The former unguarded
RPC import is intentionally no longer accepted; internal codec APIs are unchanged.
Optional `preserve_breakpoints` is boolean, default `true`: retain permanent host
breakpoint definitions and hit counters, clear transient predicates/operations
and journals. `false` clears all breakpoints after successful installation.
All new flags/hash fields are validated before reading/restoring the archive;
refused requests retain the machine and existing debugger configuration.

Import reconstructs CPU/RAM/ROM/device state, host RNG, disk bytes and host-FAT
synchronization state. It remains paused; execution resumes only by a later
execution request. Restored disks use new paths under `disk_root`. No existing
disk is overwritten. Input/display locks exclude frontend mutation during the
state transaction; external filesystem writers must be stopped by the caller.
Emulator source hashes must match and the live video adapter type must match.
When `--dos-mailbox` is enabled, a version-2 machine bundle embeds its 8192-byte
window. Import requires the same mailbox setting as the live machine and
validates the window hash before installing any guest state. Without the option,
version-1 bundle layout is unchanged.

With `--game-port`, version-4 bundles also retain axes, buttons and all four
in-flight one-shots, with or without the optional UART. Import requires the
same game-port presence as the live machine before disk/state installation.
Existing version-1/2/3 layouts are unchanged when the card is absent.

By default debugger breakpoints remain configured; `preserve_breakpoints:false`
clears them. Pending operations, instruction and
hardware trace journals and retained video snapshots are cleared. Revision and
operation identifiers remain host-session identities and are not rewound.
The VNC frame cache is invalidated with a new display epoch. Network sessions
are not serialized. Live external input after restore changes subsequent play.

Bundles have a 1 GiB total uncompressed data limit and a 4 MiB JSON limit.
Malformed bundles/hashes are rejected before live installation. Disk I/O failure
can leave a partial new output directory; the live machine stays unchanged.
Host ACLs, permissions, timestamps and external applications are not captured.

Archive publication uses an atomic hard link from a completed temporary file;
export destinations must support hard links (NTFS/ext4). A failed archive write
leaves no published archive. Disk materialization remains a separate operation
that may leave a partial new directory. VNC is asynchronous: an old in-flight
frame can arrive after the RPC reply; the subsequent epoch refresh sends a full
frame. RPC completion is not a cross-connection network delivery barrier.


## `serial.status`

COM1 is present at 3F8h, IRQ4. Returns `port`, `base`, `irq`, `divisor`, `lcr`,
`mcr`, `rx_pending`, `tx_start`, `tx_next`, `transport`, and `wire_timing`.
The virtual terminal is flow-controlled: queued input waits until the guest
reads its receive register. Transmit is immediate. Divisor/parity/stop registers
are implemented, but physical baud timing, framing/parity errors and electrical
flow control are not simulated (`wire_timing:false`). This supports polled BIOS
INT 14h and DOS COM1 programs; it is not a serial timing validation instrument.

## `serial.write`

Params: `{"data_base64":"Ug0="}`. Queues the bytes `R` and CR on COM1 and returns
`{"accepted":2,"rx_pending":2,"state_revision":12346}`. Accepts 1..4096 bytes,
validates the entire input before mutation, and refuses a full 65536-byte input queue or UART loopback mode.
Each successfully accepted batch advances `state_revision` once without executing
the CPU; rejected input preserves the UART, machine and revision. Calls remain
supported while running or paused. These serial and port-read methods are PyPC
capabilities; MartyPC currently refuses them as unsupported.
A network timeout after submission is uncertain: do not blindly resend.

## `serial.read`

Params: `{"offset":0,"max_bytes":4096}`. Returns `offset`, `next_offset`, and
`data_base64`. Reads are non-consuming and repeatable, so retrying a read is safe.
Output retains the last 65536 bytes; an offset before `tx_start` fails explicitly.
Counts must be 1..65536. The UART and both queues are included in machine snapshot
version 3, including offsets and pending interrupts. RPC executes on the CPU
thread; capture/restore does not race a separate serial socket worker.

For SYMDEB, configure COM1 with MODE (or BIOS INT 14h), then enter `= COM1` at
the console once. Send debugger commands with CR over `serial.write`; replies
and prompts arrive through `serial.read`. `= CON` restores console control.
No separate TCP serial listener or physical host COM port is required.
