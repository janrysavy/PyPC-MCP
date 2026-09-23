> [!IMPORTANT]
> This repository is the [`janrysavy/PyPC-MPC`](https://github.com/janrysavy/PyPC-MPC)
> fork focused on AI-assisted reverse engineering of DOS software. Its default
> `ai-re-agent` branch adds a supervised JSON-RPC debugger agent.
>
> **Top methods:** `agent.capabilities`; `emulator.info`; `session.status`;
> `execution.pause/continue/go/step`; register and memory access; keyboard input
> and state; I/O reads; CGA/VGA text, attributes, fonts, and video snapshots. See the
> [complete JSON-RPC API specification](JSON_RPC_API.md) for exact request,
> response, state, safety, and framing semantics.
>
> The current protocol is JSON-RPC 2.0 over a local TCP socket at
> `127.0.0.1:2301` using JSON-lines framing; despite the repository name, it is
> not yet a Model Context Protocol server.
>
> **Host-directory FAT16 mount:** From the repository root, pass a host
> directory with `--host-dir`; it appears inside DOS as the guest `D:` drive.
> The existing `C:` boot image is unchanged.
>
> ```shell
> # Windows PowerShell
> py -3.12 run_pypc.py --host-dir D:\path\to\shared-directory
>
> # Linux, macOS, or WSL2
> python3 run_pypc.py --host-dir /path/to/shared-directory
> ```
>
> The mount accepts DOS 8.3 names. Guest-created, modified, renamed, and
> deleted files and directories are synchronized into the selected directory.
> Symlinks and path escapes are rejected. A guest delete removes only a tracked
> host file whose bytes still match the last guest-synchronized version; an
> external host edit is retained and reported instead. Unknown host-only
> content is never recursively deleted. The FAT image is cached at startup. To
> publish a host edit while DOS remains running, use the DOS control worker
> described below.
>
> In DOS, select the drive with `D:` and run a program, for example:
>
> ```dos
> D:
> PROGRAM
> ```
>
> For the text-only VGA adapter, add `--video vga` at startup. It supports the
> 80×25 and 40×25 text modes, VGA attributes, cursor registers, page flips, and
> plane-2 font access through the JSON-RPC API.

## DOS compiler automation

The Pyro II repository pins both this fork and the private `dostools` compiler
tree as submodules. Its [complete DOS-control guide](https://github.com/janrysavy/pyro221_next/blob/master/docs/PYPC_DOS_CONTROL.md)
has the Windows PowerShell launch, readiness polls, host-edit, DEBUG, and
cleanup sequence. From that repository's root:

```powershell
git submodule update --init tools/pypc/src tools/dostools
python scripts/pypc_dos.py launch --name my-run
```

The launcher clones the DOS boot disk and `tools/dostools/Mount` into new
repository-local scratch directories, checks every copied file hash, creates
`D:\WORK`, copies the parent's prebuilt, source-hash-checked `DOSCTRL.COM`
without requiring NASM, and starts PyPC with `--host-dir`, `--dos-mailbox`,
and JSON-RPC on port 12311. It prints JSON with
the emulator PID and scratch host `D:` directory. `--mount PATH` selects
another DOS 8.3 tool tree; `--with-game` also copies the parent repository's
immutable `bin/` game files into scratch `D:\WORK`. `launch` returns before
the RPC listener and DOS boot are ready; poll `screen` as shown in the complete
guide. Once it shows
the DOS prompt, enter `D:` and `DOSCTRL`, then poll `ready`:

```powershell
python scripts/pypc_dos.py screen --name my-run
python scripts/pypc_dos.py type 'D:' --name my-run
python scripts/pypc_dos.py type 'DOSCTRL' --name my-run
python tools/pypc/src/guest/dos_control.py --rpc-port 12311 ready
```

Edit source files in the printed scratch host `D:` directory. A direct host
edit remains invisible to the running DOS FAT cache until it is published:

```powershell
python scripts/pypc_dos.py sync 'WORK\HELLO.PAS' --name my-run
python tools/pypc/src/guest/dos_control.py --rpc-port 12311 exec 'D:\TP6\TPC.EXE' ' D:\WORK\HELLO.PAS' --output 'D:\WORK\TPC.LOG'
```

`sync` transfers the edited bytes through DOS and verifies guest readback.
Guest writes, renames, and deletes flow to the scratch host directory. A
conflicting external host edit is deliberately retained rather than destroyed;
in that case the worker's `list` or `get` is the authoritative guest view. For
a snapshot restored into a new disk directory, pass `--host-root PATH` to
`sync`. Version-2 disk snapshots preserve the deletion conflict guards, while
version-1 snapshots remain readable.

The client also offers `list`, `put`, `get`, `chdir`, `cwd`, `mkdir`, `rename`,
`delete`, `collect-exec`, and `quit`. `exec` returns the DOS exit code and,
with `--output`, the exact redirected standard-output/error bytes. A normal DOS
child exit becomes the command-line client's host process exit status;
expected DOS, validation, timeout, and controller failures are returned as
structured JSON. A timeout does not cancel the child: use `collect-exec` from a
new client to retrieve the pending result without rerunning it. Use
keyboard/video RPC for interactive children such as `DEBUG.EXE`; the foreground
worker cannot serve other file requests until a child exits. For programs that
write directly to the screen, `video.history` records overwritten text VRAM and
reports loss if its ring fills. See [the guest worker guide](guest/README.md),
[timeout recovery](guest/RECOVERY.md), and
[the JSON-RPC contract](JSON_RPC_API.md).

`quit` exits only `DOSCTRL.COM`; stop the emulator PID returned by `launch`
to release its fixed ports. The complete guide shows both steps.

PyPC is an IBM PC (8088) emulator written in Python.

It boots and you can run e.g. MS-DOS with CheckIt3 or SpaceQuest 3 in it.
You need to have 'GLABIOS.ROM' in the directory from which you start 'main.py'. It requires python 3.12 or later.
When it is running, you can also connect a VNC client to it: you see it then use the original font and see nice graphics.


### via telnet
![main screen via telnet](images/pypc001.png)

### via vnc
![main screen via VNC](images/pypc002.png)

### spacequest 3 via vnc
![main screen via VNC](images/pypc003.png)


Note that this is a manual translation of https://github.com/folkertvanheusden/Dotxt

If you want a quicker version, run it from 'pypy'. For that you may need to invoke this first on the source code of PyPC:

```shell
sed -i 's/import override,/import /g' *py
sed -i 's/@override//g' *py
```

Folkert van Heusden

released under MIT license

### Optional VNC speed overlay

Add `--vnc-speed-overlay` to show `EMU 0.50x` at the top right. This is
emulated CPU clock time divided by monotonic host time, sampled once per
second while VNC requests frames. The nominal clock is 4,770,000 ticks/s,
matching the debugger time conversion. It measures emulator throughput, not
game frames or VNC updates. Pauses count as elapsed time; after a full paused
sample the value is zero. Clock rollback resets the sample to `--.--x`.

The fixed host font occupies eight pixel rows and can cover guest content.
Omit the option to disable it. It copies rendered pixels without writing
guest VRAM and uses ordinary RFB Raw updates, so any VNC viewer works.
Guest video captures and telnet remain unchanged.

### Optional VNC compression

A client may request standard ZRLE encoding (16), with Raw (0) as fallback.
The server respects their order and uses ZRLE for its native 32-bit little-
endian RGB24 format; other negotiated formats use Raw. ZRLE uses lossless
64x64 raw tiles with zlib level 1, one persistent stream per connection.
Clients that request only Raw retain existing behavior. No private protocol
or additional packages are used. Compression reduces bytes at a CPU cost;
for local viewing Raw may have lower latency.

### Optional private LZ4 encoding

Our viewer may additionally request encoding `0x50594c34` (ASCII PYL4), an
unregistered private version-1 encoding. It is sent only when explicitly
advertised by the client and the optional `lz4` package is installed (tested
with 4.4.5). Other clients retain
standard Raw/ZRLE behavior. The server uses it only for the native format.

Each rectangle body is a big-endian uint32 compressed byte count followed
by that many bytes of an independent LZ4 block, without an embedded size
(`store_size=False`). It decompresses to exactly width*height*4 bytes in
B,G,R,padding order. No inter-rectangle dictionary or custom side channel
is used. Client bounds must be checked before allocation/decompression.
The identifier is a local convention, not an IANA registration.
