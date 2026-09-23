# Experimental DOS control worker

`dos_control.asm` is a DOS COM program for an IBM PC/XT guest. It uses a
16-byte control header at physical `D8000h` and up to 4096 payload bytes at
`D8020h`. Start PyPC with `--dos-mailbox` to reserve `D8000h-D9FFFh` as an
optional mapped device. The host uses existing debugger RPC to pause, write
the request, continue, read the result, and acknowledge it. No COM port is
consumed. The mapped window is included in version-2 machine snapshots.

Build on Windows with `nasm -f bin -o DOSCTRL.COM guest/dos_control.asm`.
Place the COM file on a DOS-mounted drive, select that drive, and run
`DOSCTRL` from the guest prompt. Then call `guest/dos_control.py` from the
host. PyPC's default JSON-RPC port is 2301; the Pyro II scratch launcher uses
12311. For example, after the worker reports ready:

```powershell
python guest/dos_control.py --rpc-port 12311 ready
python guest/dos_control.py --rpc-port 12311 list 'D:\*.*'
python guest/dos_control.py --rpc-port 12311 exec 'D:\TP6\TPC.EXE' ' D:\WORK\HELLO.PAS' --output 'D:\WORK\TPC.LOG'
```

The Pyro II repository's `scripts/pypc_dos.py` stages a fresh DOS system disk,
its pinned private `tools/dostools/Mount` submodule, and an assembled COM in
one repository-local scratch run. Launch it there with
`python scripts/pypc_dos.py launch --name RUN`; its printed `drive` is the
host directory behind guest `D:`. `--mount PATH` accepts another DOS 8.3
tree. Its `sync WORK\\SOURCE.PAS --name RUN` command publishes a host edit
through DOS and checks the guest readback hash.
Use `--host-root PATH` after importing a snapshot into a new host directory.
The FAT16 mount caches its image at startup: editing a mounted host file alone
does not change DOS's view during a run. The explicit sync uses DOS file calls
and preserves the guest's filesystem state. Guest-created files are written
back to the host directory by the mount.

The client has `list`, `put`, `get`, `chdir`, `cwd`, `mkdir`, `rename`, `delete`,
`exec`, and `quit` commands. `put` reads the entire host file before DOS
truncates the target; `get` writes a host copy. `exec` takes a program path and
an optional DOS command tail (include its leading space). `--output` names a
guest file for redirected standard output and error, returned as byte count,
SHA-256, base64, and CP437 text. The worker returns DOS `AH=4Dh` exit code and
termination type. `COMMAND.COM /C ...` can execute shell builtins and batches.
For interactive programs, issue `exec` in one host thread and use keyboard and
video RPC from another while it runs.

Protocol state byte: `3` ready, `1` request, `2` reply, `0` host acknowledgement.
The worker publishes `RUN1` at header offset 10. The command byte is at 1,
request length at 2, reply length at 4, status at 6, and DOS error code at 8.
Commands are `L` (FindFirst), `N` (FindNext), `C` (create/truncate), `W`
(append), `R` (read at offset), `X` (EXEC), `S` (change directory), `G` (get
current directory), `M` (make directory), `D` (delete file), `V` (rename),
`Q` (quit), and `T` (ping). Replies with status
2 mean the directory enumeration ended. `X` returns the DOS `AH=4Dh` exit
code and termination type after its child returns. An optional output path
redirects the child's DOS standard output and error handles to a guest file;
the host retrieves the exact file bytes after exit. The host uses 8.3 guest
paths and transfers file data in bounded chunks.

The worker is foreground and cannot serve new file requests during `EXEC`.
PyPC's keyboard, video, CPU, and snapshot RPC remains usable then. Programs
that write directly to video do not produce captured stdout. Enable
`video.history.start` to retain ordered changed text VRAM/font/port events;
check `lost_events` before calling that stream complete. Graphics VRAM is not
yet journaled. A no-resident-worker shell path is still needed for programs
that require the worker's conventional memory.
