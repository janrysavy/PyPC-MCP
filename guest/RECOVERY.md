# Recover a DOS command after a host timeout

A host timeout does not cancel a DOS child. With the RUN1 mailbox, the child
continues running and the worker retains its reply until the host acknowledges
it. Do not submit the command again or clear the state byte to pretend that the
worker is idle: that can discard the result or duplicate program side effects.

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
never submits or reruns EXEC. A further timeout leaves an unacknowledged job
available for another collection attempt.

The Python API also provides `DOSControl.collect(command)`, which returns the
raw `(status, dos_error, payload)` tuple for a known pending mailbox command.
`DOSControl.collect_exec(output=None)` decodes an EXEC result. Idle mailboxes,
missing RUN1 markers, and mismatched command bytes are refused without writing
the mailbox. Nonpositive or nonfinite timeout values are rejected.

## Limits

RUN1 still has no request IDs or ownership arbitration. Only one controller
may use the mailbox, and it must know which command is pending. Stop the old
collector before starting another. Matching the command byte is a guard against
collecting a different command kind, not proof of job identity. Already
acknowledged replies cannot be recovered, and this API does not recover an
interrupted acknowledgement or cancel a hung child. The timeout is the polling
budget; individual RPC transport operations retain their own timeout.

The regression tests exercise the real Python client, CLI subprocess and TCP
transport against deterministic peers. They reproduce timeout -> late reply ->
collect -> next request, and check that no second child is submitted. They do
not execute the guest CPU, DOS, or a compiler. No change to DOSCTRL.COM or its
ASM source is needed.
