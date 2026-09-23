# GLaBIOS firmware

PyPC ships an 8 KiB GLaBIOS system ROM so a recursive clone can boot without
an assembler. The source is pinned as the `firmware/glabios` submodule to the
`pypc` branch of `janrysavy/GLaBIOS`. That branch starts at stable tag `v0.4.2`
and adds the PyPC profile.

The profile disables the nonexistent floppy controller, full RAM test,
keyboard reset delay, and POST beep. It fixes conventional memory at 640 KB
for both CGA and VGA. This removes the false FDC error and its interactive
`Press the Any Key` gate.

To rebuild with MASM 5 and LINK on a DOS `PATH`:

```dos
cd firmware\glabios\src
PYPC.BAT
```

Copy the resulting `GLABIOS.ROM` to `roms\GLABIOS.ROM`. The committed ROM is
8,192 bytes, has an 8-bit sum of zero, and has SHA-256
`10d07e6052ae7e5ecdceba84a5635ec1482488bee800fe1a2541bfedc95bea33`.
The ROM identifies itself as GLaBIOS `0.4.2`, dated `04/05/26`.
