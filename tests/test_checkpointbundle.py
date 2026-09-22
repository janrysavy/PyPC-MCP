import hashlib
import json
import zipfile

import pytest
from checkpointbundle import read_bundle, write_bundle
from machinecodec import capture_machine, prepare_machine
from test_machinecodec import machine


def test_real_machine_bundle_roundtrip(tmp_path):
    manifest, buffers = capture_machine(machine(tmp_path))
    target = tmp_path/'machine.pypc'
    digest = write_bundle(target, manifest, buffers)
    restored = read_bundle(target, digest)
    assert restored == (manifest, buffers)
    cpu, rng = prepare_machine(*restored, tmp_path/'restored')
    assert capture_machine(cpu)[1] == buffers
    with pytest.raises(FileExistsError): write_bundle(target, manifest, buffers)
    with pytest.raises(ValueError, match='file hash'): read_bundle(target, '0'*64)


def test_write_failure_never_publishes_partial_archive(tmp_path, monkeypatch):
    manifest, buffers = capture_machine(machine(tmp_path))
    original = zipfile.ZipFile.writestr
    def fail_data(self, name, data, *args, **kwargs):
        if name != 'manifest.json':
            raise OSError('simulated disk full')
        return original(self, name, data, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'writestr', fail_data)
    target = tmp_path/'machine.pypc'
    with pytest.raises(OSError, match='disk full'):
        write_bundle(target, manifest, buffers)
    assert not target.exists()
    assert not list(tmp_path.glob('*.pending'))


@pytest.mark.parametrize('damage', ['extra','traversal','duplicate','data','jsonkey'])
def test_invalid_archive_refused(tmp_path, damage):
    data = b'abc'
    manifest = {'format':'pypc.machine','version':1,
                'buffers':{'ram':{'size':3,'sha256':hashlib.sha256(data).hexdigest()}}}
    target = tmp_path/'bad.pypc'
    with zipfile.ZipFile(target,'w') as bundle:
        text = json.dumps(manifest)
        if damage == 'jsonkey': text = text.replace('"version": 1','"version": 1, "version": 1')
        bundle.writestr('manifest.json',text)
        bundle.writestr('buffers/ram',b'xyz' if damage == 'data' else data)
        if damage == 'extra': bundle.writestr('extra',b'')
        if damage == 'traversal': bundle.writestr('../outside',b'')
        if damage == 'duplicate':
            with pytest.warns(UserWarning): bundle.writestr('buffers/ram',data)
    with pytest.raises(ValueError): read_bundle(target)
    assert not (tmp_path.parent/'outside').exists()
