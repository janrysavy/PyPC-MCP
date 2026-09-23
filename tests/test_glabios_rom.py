import hashlib
import json
from pathlib import Path
import subprocess


ROM = Path(__file__).resolve().parents[1] / 'roms/GLABIOS.ROM'
ROOT = Path(__file__).resolve().parents[1]
RECORD = ROOT / 'roms/GLABIOS.build.json'


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
    line = subprocess.check_output(
        ['git', 'ls-tree', 'HEAD', 'firmware/glabios'], cwd=ROOT,
        text=True).strip()
    assert line.split()[2] == record['source']['commit']
    for relative, expected in record['source']['files'].items():
        source = ROOT / relative
        if source.is_file():
            assert hashlib.sha256(source.read_bytes()).hexdigest() == expected
