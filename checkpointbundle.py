"""Persistent machine bundle: JSON manifest and independently hashed ZIP members.

No pickle, executable payloads, or filesystem extraction. Machine compatibility
and component semantics are checked by prepare_machine before installation.
"""
import hashlib
import io
import json
from pathlib import Path
import zipfile

MAX_MANIFEST = 4*1024*1024
MAX_TOTAL = 1024*1024*1024
MAX_MEMBERS = 8192


def _verify(manifest, buffers):
    if (type(manifest) is not dict or manifest.get('format') != 'pypc.machine'
            or type(manifest.get('version')) is not int or manifest['version'] != 1
            or type(manifest.get('buffers')) is not dict or type(buffers) is not dict
            or set(manifest['buffers']) != set(buffers) or len(buffers) >= MAX_MEMBERS):
        raise ValueError('invalid machine bundle inventory')
    total = 0
    for name, data in buffers.items():
        if (type(name) is not str or not name or any(p in ('', '.', '..') for p in name.split('/'))
                or '\\' in name or ':' in name or '\x00' in name or type(data) is not bytes):
            raise ValueError('invalid bundle buffer')
        descriptor = manifest['buffers'][name]
        if (type(descriptor) is not dict or set(descriptor) != {'size','sha256'}
                or type(descriptor['size']) is not int or descriptor['size'] != len(data)
                or descriptor['sha256'] != hashlib.sha256(data).hexdigest()):
            raise ValueError('bundle buffer hash mismatch')
        total += len(data)
    if total > MAX_TOTAL:
        raise ValueError('bundle exceeds size limit')


def write_bundle(path, manifest, buffers):
    """Create a new bundle; never overwrite an existing path."""
    _verify(manifest, buffers)
    encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(encoded) > MAX_MANIFEST:
        raise ValueError('manifest exceeds size limit')
    with Path(path).open('xb') as output:
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
            bundle.writestr('manifest.json', encoded)
            for name, data in sorted(buffers.items()):
                bundle.writestr('buffers/'+name, data)
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_bundle(path, expected_sha256=None):
    """Read bounded members into memory; never extract archive paths to disk."""
    path = Path(path)
    limit = MAX_TOTAL+MAX_MANIFEST+MAX_MEMBERS*256
    with path.open('rb') as source:
        raw = source.read(limit+1)
    if len(raw) > limit:
        raise ValueError('compressed bundle exceeds size limit')
    if expected_sha256 is not None:
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError('bundle file hash mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        entries = bundle.infolist()
        names = [entry.filename for entry in entries]
        if (len(entries) > MAX_MEMBERS or len(set(names)) != len(names)
                or 'manifest.json' not in names
                or any(entry.flag_bits & 1 or entry.is_dir() for entry in entries)
                or sum(entry.file_size for entry in entries) > MAX_TOTAL+MAX_MANIFEST):
            raise ValueError('invalid bundle member inventory')
        if bundle.getinfo('manifest.json').file_size > MAX_MANIFEST:
            raise ValueError('manifest exceeds size limit')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('duplicate manifest key')
                result[key] = value
            return result
        manifest = json.loads(bundle.read('manifest.json'), object_pairs_hook=unique)
        if type(manifest) is not dict or type(manifest.get('buffers')) is not dict:
            raise ValueError('invalid manifest')
        expected = {'manifest.json'} | {'buffers/'+name for name in manifest['buffers']}
        if set(names) != expected:
            raise ValueError('unexpected bundle member')
        buffers = {name: bundle.read('buffers/'+name) for name in manifest['buffers']}
    _verify(manifest, buffers)
    return manifest, buffers
