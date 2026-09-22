# Disk snapshot component

`diskcodec.py` supplies the disk dependency layer for coordinated machine
checkpoints. It does not yet expose a checkpoint RPC or save a complete machine.
CPU/device/RAM coordination and input exclusion remain the machine layer's job.

`dump_disk_state` returns a JSON manifest and a dictionary of binary buffers.
Flat disk files support embed, reference and auto (embed up to 64 MiB; larger
images require an explicit choice). A reference is identified by size and
SHA-256, never just its path. Restore validates it, then copies it into a new
output directory, so subsequent guest writes cannot corrupt the reference.

Host-directory FAT16 disks embed the exact live sector image, all host files
and directories, guest-to-host path mapping, and write-through synchronization
signatures. This preserves unused sector bytes, pending FAT edits and host-only
files; rebuilding the image from the directory would lose those facts. Host
mount reference mode is refused. XTIDE transfer registers, serial identity,
pending buffer and cursor belong to the separate `xtidecodec.py` component.

Capture requires a stopped CPU and no concurrent external filesystem writers.
Restore requires a new output directory and validates all payloads before
creating it. Existing source disks/files are never overwritten. An I/O failure
while materializing may leave a partial new directory; no live machine is
modified by this component. The coordinator must validate every other component
before materialization and install the restored machine only after success.

This captures guest-visible disk bytes and the existing host adapter's sync
state. Host ACLs, timestamps, permissions, open external handles and concurrent
host applications are not serialized. Exact continuation assumes ordinary
writable experiment directories; this component does not claim filesystem
metadata or arbitrary external-host equivalence.

Validation: `python -m pytest tests/test_diskcodec.py -q` passes 16 cases,
including an independent fresh-process load, identical write-through replay,
reference mismatch, unexpected buffers, malformed sync state, duplicate paths,
path traversal and refusal to overwrite an existing destination. Complete
machine restart/continuation remains an explicit pending integration test.
