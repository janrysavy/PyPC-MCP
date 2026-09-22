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

Validation: `python -m pytest tests/test_diskcodec.py -q` passes 19 cases,
including an independent fresh-process load, identical write-through replay,
reference mismatch, unexpected buffers, malformed sync state, duplicate paths,
path traversal and refusal to overwrite an existing destination. Complete
machine restart/continuation remains an explicit pending integration test.

## Independent review resolution

Review found that a forged guest-to-host mapping could redirect writes to a
different filename. Validation now requires the host relative path to encode
exactly the saved DOS path key; sync records must have a path mapping. Negative
controls reject redirection and missing mappings before creating output.

The request to rebuild/check sync signatures against current FAT contents was
not applied: they are cached state and can legitimately differ during guest
updates. A new test preserves a stale host-file mapping after deletion and a
changed FAT sector, then observes identical file recreation on the next guest
data write. Requiring the host file to exist would reject this restorable state.

Flat images intentionally accept the string paths supported by XTIDE. Path
objects are not valid XTIDE backing disks. XTIDE also deliberately zero-fills
short image reads; controller geometry need not equal file size. This component
does not claim live whole-machine/XTIDE integration, which remains pending.
The source-level geometry concern therefore remains an integration test to run,
not grounds for silently changing the existing disk read behavior.
