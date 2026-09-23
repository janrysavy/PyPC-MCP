# Experimental DOS control worker

`dos_control.asm` is a DOS COM program for an IBM PC/XT guest. It uses a
16-byte control header at physical `D8000h` and up to 4096 payload bytes at
`D8020h`. PyPC currently treats this unused upper-memory range as RAM. The
host uses its existing debugger RPC to pause, write the request, continue,
read the result, and acknowledge it. No serial port, new emulator device, or
new RPC method is required for this first implementation.

Build on Windows with `nasm -f bin -o DOSCTRL.COM guest/dos_control.asm`.
Place the COM file on a DOS-mounted drive, run it from the prompt, then use
`python guest/dos_control.py --rpc-port PORT ready|list|put|get|exec`.
The parent repository's `scripts/pypc_dos.py` stages a fresh system disk,
the tool mount, and an assembled COM in one repository-local scratch run.

Protocol state byte: `3` ready, `1` request, `2` reply, `0` host acknowledgement.
The worker publishes `RUN1` at header offset 10. The command byte is at 1,
request length at 2, reply length at 4, status at 6, and DOS error code at 8.
Commands are `L` (FindFirst), `N` (FindNext), `C` (create/truncate), `W`
(append), `R` (read at offset), `X` (EXEC), and `T` (ping). Replies with status
2 mean the directory enumeration ended. `X` returns the DOS `AH=4Dh` exit
code and termination type after its child returns. The host uses 8.3 guest
paths and transfers file data in bounded chunks.

The worker is foreground and cannot serve new file requests during `EXEC`.
PyPC's keyboard, video, CPU, and snapshot RPC remains usable then. Programs
that write directly to video do not produce captured stdout here; current VRAM
RPC has no scrollback. `D8000h` has not yet been reserved as a device, so a
guest using that range could interfere with the mailbox. This is a working
prototype, not a universal DOS process service.
