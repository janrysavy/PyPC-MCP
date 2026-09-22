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
> The mount accepts DOS 8.3 names. Guest-created and modified files and
> directories are synchronized into the selected directory and its
> subdirectories. Symlinks, path escapes, guest deletions, and renames are not
> supported. Host-side changes made after startup require an emulator restart.
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
