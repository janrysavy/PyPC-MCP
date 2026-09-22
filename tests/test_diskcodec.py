import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from diskcodec import dump_disk_state, restore_disk_state, validate_disk_state
from virtualfat16 import HostDirectoryFAT16


def fixture(tmp_path):
    root = tmp_path/'source'; root.mkdir()
    (root/'PYRO.DAT').write_bytes(b'original data')
    (root/'EMPTY').mkdir()
    (root/'SUB').mkdir(); (root/'SUB'/'FILE.BIN').write_bytes(b'other')
    disk = HostDirectoryFAT16(root)
    # Host-only data and free-sector bytes cannot be reconstructed from FAT files.
    (root/'STALE.TXT').write_bytes(b'host only')
    disk._image[-7:] = b'UNUSED!'
    return disk


def host_files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_exact_host_state_and_write_continuation(tmp_path):
    original = fixture(tmp_path)
    manifest, buffers = dump_disk_state(original)
    restored = restore_disk_state(json.loads(json.dumps(manifest)), buffers, tmp_path/'restored')
    assert bytes(restored._image) == bytes(original._image)
    assert restored._synced_files == original._synced_files
    assert {k: p.relative_to(restored.directory) for k,p in restored._host_paths.items()} == {
        k: p.relative_to(original.directory) for k,p in original._host_paths.items()}
    assert host_files(restored.directory) == host_files(original.directory)
    assert (restored.directory/'EMPTY').is_dir()
    # Find actual allocated file content then replay the same completed sector write.
    start = original._image.index(b'original data')
    for disk in (original, restored):
        disk.Write(start, b'changed! data')
    assert host_files(restored.directory) == host_files(original.directory)
    assert (restored.directory/'PYRO.DAT').read_bytes() == b'changed! data'
    assert dump_disk_state(restored) == dump_disk_state(original)


@pytest.mark.parametrize('mode', ['embed', 'reference', 'auto'])
def test_flat_image_restores_to_separate_file(tmp_path, mode):
    source = tmp_path/'source.img'; source.write_bytes(bytes(range(256))*4)
    manifest, buffers = dump_disk_state(str(source), mode)
    restored = Path(restore_disk_state(manifest, buffers, tmp_path/'output', source if mode == 'reference' else None))
    assert restored.read_bytes() == source.read_bytes()
    restored.write_bytes(b'changed')
    assert len(source.read_bytes()) == 1024


def test_reference_mismatch_writes_nothing(tmp_path):
    source = tmp_path/'source.img'; source.write_bytes(b'before')
    manifest, buffers = dump_disk_state(str(source), 'reference')
    source.write_bytes(b'after!')
    with pytest.raises(ValueError, match='size/hash'):
        restore_disk_state(manifest, buffers, tmp_path/'output', source)
    assert not (tmp_path/'output').exists()


@pytest.mark.parametrize('damage', ['hash','traversal','absolute','parent','duplicate','sync','extra','version','boolsize'])
def test_invalid_host_payload_refused_before_writing(tmp_path, damage):
    manifest, buffers = dump_disk_state(fixture(tmp_path))
    manifest = copy.deepcopy(manifest); buffers = dict(buffers)
    if damage == 'hash': buffers['image'] = b'x' + buffers['image'][1:]
    elif damage == 'traversal': manifest['entries'][0]['path'] = '../ESCAPE'
    elif damage == 'absolute': manifest['paths'][0]['path'] = 'C:/ESCAPE'
    elif damage == 'parent': manifest['entries'][0]['path'] = 'MISSING/FILE'
    elif damage == 'duplicate': manifest['entries'].append(copy.deepcopy(manifest['entries'][0]))
    elif damage == 'sync': manifest['synced'][0]['chain'] = [-1]
    elif damage == 'extra': buffers['unexpected'] = b''
    elif damage == 'version': manifest['version'] = True
    elif damage == 'boolsize': manifest['image']['size'] = True
    with pytest.raises(ValueError): restore_disk_state(manifest, buffers, tmp_path/'output')
    assert not (tmp_path/'output').exists()


def test_storage_policy_and_no_overwrite(tmp_path):
    source = tmp_path/'source.img'; source.write_bytes(b'1234')
    with pytest.raises(ValueError, match='explicit'): dump_disk_state(str(source), embed_limit=3)
    manifest, buffers = dump_disk_state(str(source), 'embed')
    output = tmp_path/'output'; output.mkdir(); (output/'KEEP').write_bytes(b'unchanged')
    with pytest.raises(FileExistsError): restore_disk_state(manifest, buffers, output)
    assert (output/'KEEP').read_bytes() == b'unchanged'
    with pytest.raises(ValueError, match='require embedding'): dump_disk_state(fixture(tmp_path), 'reference')


def test_fresh_process_restores_host_image_and_sync_state(tmp_path):
    manifest, buffers = dump_disk_state(fixture(tmp_path))
    payload = tmp_path/'payload.json'
    payload.write_text(json.dumps({'manifest':manifest, 'buffers':{k:v.hex() for k,v in buffers.items()}}))
    code = '''import json,sys,typing
if not hasattr(typing,'override'): typing.override=lambda f:f
from pathlib import Path
from diskcodec import restore_disk_state,dump_disk_state
p=json.loads(Path(sys.argv[1]).read_text())
d=restore_disk_state(p['manifest'],{k:bytes.fromhex(v) for k,v in p['buffers'].items()},sys.argv[2])
m,b=dump_disk_state(d)
assert m==p['manifest'] and {k:v.hex() for k,v in b.items()}==p['buffers']
print('fresh-process disk state equal')
'''
    result = subprocess.run([sys.executable,'-c',code,str(payload),str(tmp_path/'fresh')],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True,text=True,check=True)
    assert result.stdout.strip() == 'fresh-process disk state equal'
