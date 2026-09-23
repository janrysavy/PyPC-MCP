import hashlib
from pathlib import Path


ROM = Path(__file__).resolve().parents[1] / 'roms/GLABIOS.ROM'
EXPECTED_SHA256 = '10d07e6052ae7e5ecdceba84a5635ec1482488bee800fe1a2541bfedc95bea33'


def test_glabios_rom_matches_pinned_pypc_build():
    data = ROM.read_bytes()
    assert len(data) == 8192
    assert sum(data) & 0xff == 0
    assert hashlib.sha256(data).hexdigest() == EXPECTED_SHA256
    assert b'Ver: 0.4.2-8EK' in data
    assert b'04/05/26' in data
