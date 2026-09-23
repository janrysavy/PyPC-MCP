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

## VGA bridge

XT switch bits `00` tell the BIOS that an EGA or VGA option ROM is responsible
for display initialization. When `--video vga` is selected, PyPC maps the
committed `roms/PYPCVGA.ROM` at `C000:0000`. Its source and deterministic NASM
builder are `roms/pypcvga.asm` and `roms/build_pypcvga.py`.

The option ROM initializes mode 3 state, installs its INT 10h vector, and
chains text calls to GLaBIOS's fixed `F000:F065` entry. PyPC handles its native
VGA calls before vector dispatch, including standard `AX=1A00h` VGA detection.
This lets POST report `Video [ VGA ]` while DOS retains GLaBIOS's tested text
services. The bridge is tied to the pinned GLaBIOS v0.4.2 layout and must be
updated if that firmware entry point moves.
