# PyPC JSON-RPC 2.0 API

This document is the complete contract for the AI/debug control channel built into
PyPC. It follows the DOSBox-X agent convention of using JSON-RPC 2.0 method names
and JSON-lines framing. It also records the portability audit against the
DOSBox-X debugger-agent API.

## Transport

The emulator listens only on the local machine:

```text
TCP 127.0.0.1:2301
```

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
| `session.status` | Report the fixed live PyPC session and execution state. |
| `state.get_registers` | Read the 8088 registers and execution state. |
| `state.get` | Alias of `state.get_registers`. |
| `state.set_registers` | Guarded register write while paused. |
| `memory.read` | Read a bounded physical/linear/segmented memory block. |
| `memory.write` | Guarded memory write while paused. |
| `video.text` | Read the active CGA text screen directly from VRAM. |
| `video.snapshot` | Capture immutable CGA VRAM/text bytes. |
| `video.snapshot.read` | Read a bounded component from a retained video snapshot. |
| `io.read` | Read one byte from an emulated I/O port. |
| `input.keyboard` | Queue XT keyboard make/break scan codes. |
| `keyboard.scancode` | Single-event alias of `input.keyboard`. |
| `input.state` | Read currently pressed XT scan codes. |
| `execution.pause` | Stop at the next instruction boundary. |
| `execution.continue` | Resume execution. |
| `execution.go` | Alias of `execution.continue`. |
| `execution.step` | Execute exactly one instruction, then pause. |

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
  "address_spaces":["physical","linear","segmented"],
  "limits":{"max_memory_bytes":65536,"max_keyboard_events":32,
    "retained_video_snapshots":8},
  "methods":["agent.capabilities","emulator.info","state.get_registers",
    "state.get","state.set_registers","session.status","memory.read",
    "memory.write","video.text","video.snapshot","video.snapshot.read",
    "io.read","input.keyboard","keyboard.scancode","input.state",
    "execution.pause","execution.continue","execution.go","execution.step"]
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

Register values and `clock` are unsigned integers. `state_revision` increments
after each executed instruction; it identifies the snapshot boundary at which the
read was made. Unlike the full DOSBox-X service, PyPC permits this read while the
CPU is running because the request is executed synchronously at an instruction
boundary.

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
linear address is `segment * 16 + offset`. All accesses must remain within the
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

Reads a 25-row CGA text page directly from the CGA VRAM backing store, including
the current CRTC display offset. With no parameters it reads the active page.
Optional parameters select another bank/page without changing emulated hardware:

```json
{"page":1}
```

or:

```json
{"display_address":"0x0FA0"}
```

The page size is `columns * 25 * 2` bytes. PyPC’s CGA has 16 KiB of text/graphics
RAM, so it exposes four 80-column pages or eight 40-column pages. A text-mode program can
render into one page and flip the CRTC start address to another; `active_page`,
`page`, and `is_active_page` make that flip observable. A page read never changes
the CRTC or the display.

Result:

```json
{
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
raw CGA attribute byte. The low four attribute bits are the foreground palette
index; bits 4..6 are the background palette index; bit 7 is reported as `blink`.
`columns` is `40` or `80`. In graphics modes the text and cell arrays are still
the raw character interpretation of CGA memory and should not be treated as a
graphical screenshot.

## `video.snapshot`

Captures immutable CGA VRAM and decoded text bytes at one CPU-thread boundary.
The snapshot retains raw VRAM, so character attributes and all render pages remain
available even after the live screen changes. At most eight snapshots are retained;
older snapshots expire.

No parameters. Result:

```json
{
  "snapshot_id":"snap-1",
  "state_revision":12345,
  "captured_ticks":123456789,
  "video_mode":8,
  "columns":40,
  "rows":25,
  "display_address":0,
  "active_page":0,
  "text":{"byte_count":999,"sha256":"..."},
  "vram":{"byte_count":16384,"sha256":"..."}
}
```

## `video.snapshot.read`

Reads one retained snapshot component. Parameters:

```json
{"snapshot_id":"snap-1","component":"vram","offset":0,"length":64}
```

`component` is `vram` or `text`. Result includes both whole-component and chunk
metadata:

```json
{
  "snapshot_id":"snap-1","component":"vram","offset":0,
  "byte_count":64,"component_byte_count":16384,
  "component_sha256":"...","data_base64":"...","sha256":"..."
}
```

## `io.read`

Parameters:

```json
{"port":"0x3D8"}
```

`port` is a required 16-bit integer. Result:

```json
{"port":984,"value":9,"state_revision":12345}
```

The read uses the same emulated I/O dispatch as an 8088 `IN` byte operation. It may
have device-specific read side effects. I/O writes are deliberately not exposed
by this first version.

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
{"accepted":2,"state_revision":12345}
```

## `input.state`

No parameters. Result reports raw XT make codes currently held according to the
keyboard device, plus an empty joystick list because PyPC has no joystick device:

```json
{
  "keyboard":{"pressed_scancodes":[77]},
  "joysticks":[],
  "state_revision":12345
}
```

The implementation returns scan-code integers; the fork’s named-key layer is not
portable to this XT-only keyboard model.

## `keyboard.scancode`

Single-event convenience alias. It accepts the same event fields directly:

```json
{"jsonrpc":"2.0","id":2,"method":"keyboard.scancode",
 "params":{"scan_code":"0x4F","pressed":true}}
```

The result is the same as `input.keyboard` with `accepted:1`.

## `execution.pause`

No parameters. Requests that execution stop at the next instruction boundary.
When the request returns, no further CPU instruction is executed until a continue
or step request. Result:

```json
{"paused":true,"general":{},"segments":{},"ip":0,"flags":0,
 "flags_text":"","clock":0,"in_hlt":false,"state_revision":12345}
```

The register fields in the result are exactly those of `state.get_registers`.

## `execution.continue`

No parameters. Resumes execution. Result is the current register object with
`paused:false` added. `execution.go` is an exact alias.

## `execution.go`

No parameters. Exact alias of `execution.continue`.

## `execution.step`

Accepts optional `{"mode":"into"}`. Requires the emulator to be paused. It
authorizes exactly one `p.Tick()` call and then pauses again at the following
instruction boundary. `mode:"over"` is rejected because PyPC does not yet have
temporary breakpoint support.

Result is the current register object with `stepping:true` added. Calling it while
running returns `-32602`.

## Minimal Python client

```python
import json
import socket

def rpc(sock, request):
    sock.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
    return json.loads(sock.recv(1024 * 1024).splitlines()[0])

with socket.create_connection(("127.0.0.1", 2301)) as sock:
    print(rpc(sock, {"jsonrpc":"2.0", "id":1,
                     "method":"video.text", "params":{}}))
    print(rpc(sock, {"jsonrpc":"2.0", "id":2,
                     "method":"input.keyboard",
                     "params":{"events":[{"scan_code":"0x50", "pressed":True},
                                             {"scan_code":"0x50", "pressed":False}]}}))
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
| `execution.run_until`, `execution.wait` | Deferred | No operation/predicate scheduler yet. |
| `execution.pause` | Implemented | Pauses at an instruction boundary. |
| `execution.step` | Implemented (`into`) | `over` needs temporary breakpoints. |
| `state.get_registers` | Implemented | Native 8088 state is directly available. |
| `state.set_registers` | Implemented | Guarded 16-bit register mutation. |
| `input.keyboard` | Implemented | XT scan-code batch injection. |
| `input.joystick` | Deferred | No PyPC joystick device. |
| `input.state` | Implemented (XT subset) | Raw pressed scan codes; no named-key/joystick layer. |
| `dos.memory_map` | Deferred | No DOS loader/MCB metadata model. |
| `checkpoints.create/list/restore/delete` | Deferred | No complete machine snapshot serializer. |
| `video.snapshot` | Implemented (CGA subset) | Immutable raw VRAM/text snapshot; no VGA fonts/DAC/frame. |
| `video.snapshot.read` | Implemented (`vram`, `text`) | Bounded retained-component reads. |
| `memory.read` | Implemented | Bus-backed 1 MiB reads. |
| `memory.write` | Implemented | Paused, SHA-guarded bus writes. |
| `breakpoints.create/list/delete` | Deferred | Existing CPU hooks are not yet RPC-safe/normalized. |
| `debug.output.read` | Deferred | No bounded diagnostic-output ring. |
| `debugger.execute_command` | Deferred | Deliberately no raw debugger command escape hatch. |
| `trace.start/read/stop` | Deferred | No bounded CPU trace ring. |
| `hardware.trace.start/read/stop` | Deferred | No hardware event recorder. |
| `dos.trace.start/read/stop` | Deferred | No DOS file-operation recorder. |

Deferred methods are intentionally omitted from the `methods` capability list and
currently return JSON-RPC `-32601`.
