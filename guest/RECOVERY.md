# Recover a DOS command after a host timeout

A host timeout while waiting for EXEC does not cancel a DOS child. With the
RUN1 mailbox, the child continues running and the worker retains its reply
until the host acknowledges it. Do not submit the command again or clear the
state byte to pretend that the worker is idle: that can discard the result or
duplicate program side effects.

After the original `exec` client has stopped, collect that same pending EXEC:

```powershell
python guest/dos_control.py --rpc-port 12311 --timeout 600 collect-exec --output 'D:\WORK\TPC.LOG'
```

Use the capture path from the original command; omit `--output` when there was
no capture. Prefer an absolute DOS path. The result has the same `exit_code`,
`termination_type`, and optional exact output bytes/hash as `exec`. The CLI
returns the child code to the host, or 1 for non-normal termination with a zero
child code. Collection waits if the child is still running, acknowledges the
existing reply, and allows another command after the worker becomes ready. It
never submits or reruns EXEC. A further timeout before the EXEC reply is
acknowledged leaves that reply available for another collection attempt.

The Python API also provides `DOSControl.collect(command)`, which returns the
raw `(status, dos_error, payload)` tuple for a known pending mailbox command.
`DOSControl.collect_exec(output=None)` decodes an EXEC result. Idle mailboxes,
missing RUN1 markers, and mismatched command bytes are refused without writing
the mailbox. Nonpositive or nonfinite timeout values are rejected.

## Capture retrieval failed after the child completed

EXEC status and capture retrieval are separate operations. After EXEC returns,
the client acknowledges its status and then reads the requested output file.
If that read fails, both `exec` and `collect-exec` raise `DOSOutputError` in the
Python API. Its `result` retains `exit_code`, `termination_type`, and
`output_path`; its chained exception describes the file or transport error.

The CLI returns host status 1 (even if the child returned 0), and emits those
same completed-result fields alongside `error.kind = "output_capture"` and a
structured `error.cause`. For example, a child that returned 7 followed by DOS
access-denied while reading the capture is still reported with `exit_code: 7`
and `termination_type: 0`; the cause has `kind: "dos"`, `command: "R"`,
`status: 1`, and `dos_error: 5`. No output hash or complete bytes are claimed.

Do not rerun or recollect EXEC in this case: the child already completed and
its reply was acknowledged. Once the worker is ready, retrieve the output path
separately with `get`. If the capture read itself timed out with an outstanding
RUN1 `R` request, stop the old collector and collect that read using the Python
`DOSControl.collect('R')` API before submitting another file command. A failed
transport can also leave acknowledgement uncertain; inspect the mailbox or
restore a known snapshot rather than clearing it blindly.

## Limits

RUN1 still has no request IDs or ownership arbitration. Only one controller
may use the mailbox, and it must know which command is pending. Stop the old
collector before starting another. Matching the command byte is a guard against
collecting a different command kind, not proof of job identity. Already
acknowledged replies cannot be recovered from the mailbox, and this API does
not recover an interrupted acknowledgement or cancel a hung child. The timeout
is the polling budget; individual RPC transport operations retain their own
timeout.

The client regression tests exercise the real Python client, CLI subprocess
and TCP transport against deterministic peers. They reproduce timeout -> late
reply -> collect -> next request, and capture errors after completed EXEC,
checking that no second child is submitted. Those peer tests do not execute
the guest CPU, DOS, or a compiler. The separate opt-in live-DOS suite covers
real guest execution and timeout recovery. These client changes do not require
a change to DOSCTRL.COM or its ASM source.
