# MS-DOS boot profile

`harddisk.img` is a bootable MS-DOS 6 FAT16 disk. Its `CONFIG.SYS` is:

```dos
FILES=30
DEVICE=LTEMM\LTEMM.EXE /n
```

LTEMM is the LIM EMS 4 driver for the emulated Lo-tech 2 MiB EMS board. Its
source and manual are available as `C:\LTEMM\LTEMM.ASM` and
`C:\LTEMM\LTEMM.TXT` in the image. The manual defines `/n` as "Bypass memory
test". The driver still probes the board, registers its device and interrupt,
and reports 128 pages and 2048 KiB; it only omits the page-by-page startup
test. Programs therefore retain EMS while automated cold boots avoid the test.

`tests/test_boot_disk.py` parses the partition and FAT16 structures without
mounting or changing the image. It checks both the exact `CONFIG.SYS` bytes and
the `/n` definition in the bundled manual. A live DOS check can additionally
run `C:\DOS\MEM.EXE /C`; its summary must report 2,097,152 bytes of total and
free expanded memory immediately after boot.
