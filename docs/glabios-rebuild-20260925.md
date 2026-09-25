# GLaBIOS provenance repair, 2026-09-25

The source gitlink at PyPC `781495f73836bebd5d123e8a701119d8aa088798`
points to GLaBIOS `5cd99653737beb31ab9997edf7fdd2036ebd5bb8`, while
`roms/GLABIOS.build.json` still named `a590012aa824918937f34224945149dc0f76fdd3`.
Consequently the existing `test_build_record_names_the_pinned_source_and_available_inputs`
fails in an initialized checkout. The source change is only the PyPC repository
name in `doc/pypc.md`; all three hashed build inputs are unchanged.

Rather than relabel an untested binary, the current pinned sources were rebuilt
inside that same PyPC revision, under CPython 3.13 on Linux. The DOSCTRL worker
ran the pinned tools from private dostools
`9dd362f25c47ffbf9b8d627809712dff50865fcd`. No host assembler was used for this ROM.
The work directory was a fresh `D:\GBUILD`; prior OBJ, EXE, and ROM products were
removed, and the source/GLA2ROM upload digests were verified. Commands reproduce
`firmware/glabios/src/PYPC.BAT` without relying on an inherited PATH:

```dos
D:\MASM50\BIN\MASM.EXE /DVER_NUM="0.4.2" /DVER_DATE="04/05/26" /DARCH_TYPE="E" /DARCH_SUB_TYPE="K" GLABIOS;
D:\MASM50\BIN\LINK.EXE GLABIOS;
D:\GBUILD\GLA2ROM.COM GLABIOS.EXE GLABIOS.ROM
```

All three direct EXEC results had `exit_code=0`, `termination_type=0`.
MASM reported warning A4102 (the segment is near the 64K limit), no severe
errors. LINK reported warning L4021 (no stack segment), expected for ROM output.
Captured output digests, in command order:

- `c866ebb9a1430746e567ae1baf5431c69e899f563d28733d99f098d27dbe8630`
- `1665a05136ff8c40352d9692b925f907aad3e406bbeea04a4edcbbf02a065a35`
- `d9ccccd15718a2d931caec6368335b377c5f6254f76a5a5ea60a8822f6f897e7`

The downloaded ROM was compared byte for byte with `roms/GLABIOS.ROM`: all
8,192 bytes agree, its checksum is zero, and SHA-256 remains
`10d07e6052ae7e5ecdceba84a5635ec1482488bee800fe1a2541bfedc95bea33`.
Only the build record changes; neither ROM bytes nor emulator behavior change.
The original source/ROM integrity tests are retained without weakening them.
