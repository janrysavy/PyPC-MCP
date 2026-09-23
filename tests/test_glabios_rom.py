import hashlib
import json
from pathlib import Path


ROM = Path(__file__).resolve().parents[1] / 'roms/GLABIOS.ROM'
ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / 'roms/GLABIOS.build.json'


def submodule_head(path: Path) -> str:
    marker = (path / '.git').read_text(encoding='utf-8').strip()
    assert marker.startswith('gitdir: ')
    git_dir = Path(marker.removeprefix('gitdir: '))
    if not git_dir.is_absolute():
        git_dir = path / git_dir
    head = (git_dir / 'HEAD').read_text(encoding='ascii').strip()
    if not head.startswith('ref: '):
        return head
    reference = head.removeprefix('ref: ')
    loose = git_dir / reference
    if loose.is_file():
        return loose.read_text(encoding='ascii').strip()
    for line in (git_dir / 'packed-refs').read_text(encoding='ascii').splitlines():
        if line.endswith(' ' + reference):
            return line.split()[0]
    raise AssertionError(f'cannot resolve submodule HEAD {reference}')


def test_glabios_rom_matches_pinned_pypc_build():
    record = json.loads(RECORD.read_text(encoding='utf-8'))
    data = ROM.read_bytes()
    assert len(data) == record['output']['bytes']
    assert sum(data) & 0xff == record['output']['byte_sum_mod_256']
    assert hashlib.sha256(data).hexdigest() == record['output']['sha256']
    assert b'Ver: 0.4.2-8EK' in data
    assert b'04/05/26' in data


def test_build_record_names_the_pinned_source_and_available_inputs():
    record = json.loads(RECORD.read_text(encoding='utf-8'))
    assert submodule_head(ROOT / 'firmware/glabios') == record['source']['commit']
    for relative, expected in record['source']['files'].items():
        source = ROOT / relative
        assert source.is_file(), f'initialize the pinned source submodule: {source}'
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
