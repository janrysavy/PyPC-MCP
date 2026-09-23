"""Build and checksum PyPC's 512-byte VGA option ROM."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess


HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'pypcvga.asm'
PRODUCT = HERE / 'PYPCVGA.ROM'
EXPECTED_SHA256 = '682933a13f5079cb940886f648fad51ee9b7e69b41b534e072ed1af60ec9cc39'


def find_nasm(explicit: str) -> Path:
    candidates = [Path(explicit)] if explicit else []
    for root in (os.environ.get('ProgramFiles', r'C:\Program Files'),
                 os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')):
        candidates.append(Path(root) / 'NASM/nasm.exe')
    found = shutil.which('nasm')
    if found:
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError('nasm.exe not found; pass --nasm PATH')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--nasm', default='')
    parser.add_argument('--check', action='store_true',
                        help='rebuild beside the committed ROM and compare it')
    args = parser.parse_args()
    target = HERE / '.PYPCVGA.ROM.check' if args.check else PRODUCT
    target.unlink(missing_ok=True)
    try:
        subprocess.run([find_nasm(args.nasm), '-f', 'bin', str(SOURCE),
                        '-o', str(target)], check=True)
        data = bytearray(target.read_bytes())
        if len(data) != 512 or data[:3] != b'\x55\xaa\x01':
            raise RuntimeError('unexpected VGA option ROM layout')
        data[-1] = (-sum(data[:-1])) & 0xff
        target.write_bytes(data)
        if sum(data) & 0xff:
            raise RuntimeError('VGA option ROM checksum failed')
        digest = hashlib.sha256(data).hexdigest()
        if digest != EXPECTED_SHA256:
            raise RuntimeError(f'unexpected VGA option ROM SHA-256: {digest}')
        if args.check and target.read_bytes() != PRODUCT.read_bytes():
            raise RuntimeError('rebuilt VGA option ROM differs from committed ROM')
        action = 'verified' if args.check else 'built'
        print(f'{PRODUCT.name} {action}: bytes={len(data)} sha256={digest}')
    finally:
        if args.check:
            target.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
