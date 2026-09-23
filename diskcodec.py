"""Disk dependencies for stopped machine snapshots; no live disk is overwritten.

Capture requires the CPU and external filesystem writers to be stopped.
Payloads are JSON plus named bytes, ready for the machine bundle container.
Flat images can be embedded or referenced by size/hash. Host mounts embed both
their exact live sector image and host files, including stale/deleted guest files.
Version 2 also retains the last guest-synchronized file hashes used to prevent a
guest deletion from destroying a later external host edit. Version 1 snapshots
remain readable; their guard hashes are reconstructed from the embedded FAT
image rather than trusting potentially changed host bytes.
"""
import hashlib
from pathlib import Path, PurePosixPath

from virtualfat16 import HostDirectoryFAT16

DISK_VERSION = 2
SUPPORTED_VERSIONS = (1, DISK_VERSION)


def _descriptor(data):
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def _is_sha256(value):
    return (type(value) is str and len(value) == 64 and
            all(character in '0123456789abcdef' for character in value))


def _check(data, descriptor):
    if (type(data) is not bytes or type(descriptor) is not dict
            or set(descriptor) != {'size', 'sha256'}
            or type(descriptor['size']) is not int
            or descriptor != _descriptor(data)):
        raise ValueError('disk bytes do not match size/hash')


def _relative(name):
    if (type(name) is not str or not name or '\\' in name or ':' in name
            or '\x00' in name or name != PurePosixPath(name).as_posix()
            or PurePosixPath(name).is_absolute()
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('invalid relative host path')
    for part in name.split('/'):
        if part.endswith(('.', ' ')):
            raise ValueError('ambiguous host path')
        HostDirectoryFAT16._dos_name(Path(part))
    return name


def _key(key):
    if type(key) is not list or not key:
        raise ValueError('invalid guest path key')
    parts = []
    for value in key:
        if type(value) is not str:
            raise ValueError('invalid guest path component')
        try:
            raw = bytes.fromhex(value)
            if len(raw) != 11:
                raise ValueError('invalid DOS name size')
            name = HostDirectoryFAT16._display_name(raw)
            if HostDirectoryFAT16._dos_name(Path(name)) != raw:
                raise ValueError('noncanonical DOS name')
        except (UnicodeError, ValueError) as error:
            raise ValueError('invalid guest path component') from error
        parts.append(raw)
    return tuple(parts)


def dump_disk_state(disk, mode='auto', embed_limit=64*1024*1024):
    if (mode not in ('auto', 'embed', 'reference')
            or type(embed_limit) is not int or embed_limit < 0):
        raise ValueError('invalid disk storage policy')
    if type(disk) is str:
        data = Path(disk).read_bytes()
        if mode == 'auto':
            if len(data) > embed_limit:
                raise ValueError('large disk needs explicit embed/reference policy')
            mode = 'embed'
        return ({'format': 'pypc.disk', 'version': DISK_VERSION,
                 'kind': 'file', 'mode': mode, 'image': _descriptor(data)},
                {'image': data} if mode == 'embed' else {})
    if type(disk) is not HostDirectoryFAT16:
        raise ValueError('unsupported disk type')
    if mode == 'reference':
        raise ValueError(
            'host mounts require embedding their live image and sync state')
    expected_layout = {
        'directory', 'identity', '_image', '_host_paths', '_synced_files',
        '_synced_hashes',
    }
    if set(vars(disk)) != expected_layout:
        raise ValueError('host disk layout changed; update snapshot schema')
    if set(disk._synced_hashes) != set(disk._synced_files):
        raise ValueError('host disk sync hashes do not match sync records')
    if any(not _is_sha256(value) for value in disk._synced_hashes.values()):
        raise ValueError('host disk contains an invalid sync hash')
    if mode == 'auto' and len(disk._image) > embed_limit:
        raise ValueError('large host disk needs explicit embed policy')
    buffers = {'image': bytes(disk._image)}
    entries = []
    for path in sorted(disk.directory.rglob('*')):
        if (path.is_symlink()
                or (hasattr(path, 'is_junction') and path.is_junction())):
            raise ValueError('host mount contains linked path')
        name = _relative(path.relative_to(disk.directory).as_posix())
        if path.is_dir():
            entries.append({'path': name, 'kind': 'directory'})
        elif path.is_file():
            blob = 'file-' + str(len(entries))
            buffers[blob] = path.read_bytes()
            entries.append({'path': name, 'kind': 'file', 'blob': blob,
                            'data': _descriptor(buffers[blob])})
        else:
            raise ValueError('unsupported host entry')
    paths = [
        {'key': [part.hex() for part in key],
         'path': _relative(path.relative_to(disk.directory).as_posix())}
        for key, path in disk._host_paths.items()
    ]
    synced = [
        {'key': [part.hex() for part in key], 'size': size,
         'chain': list(chain), 'sha256': disk._synced_hashes[key]}
        for key, (size, chain) in disk._synced_files.items()
    ]
    manifest = {
        'format': 'pypc.disk', 'version': DISK_VERSION, 'kind': 'host',
        'mode': 'embed', 'image': _descriptor(buffers['image']),
        'entries': entries, 'paths': paths, 'synced': synced,
    }
    validate_disk_state(manifest, buffers)
    return manifest, buffers


def validate_disk_state(manifest, buffers, reference=None):
    """Validate all data without filesystem writes; return image bytes."""
    if (type(manifest) is not dict
            or manifest.get('format') != 'pypc.disk'
            or type(manifest.get('version')) is not int
            or manifest['version'] not in SUPPORTED_VERSIONS
            or type(buffers) is not dict):
        raise ValueError('invalid disk envelope')
    version = manifest['version']
    common = {'format', 'version', 'kind', 'mode', 'image'}
    host = manifest.get('kind') == 'host'
    host_fields = {'entries', 'paths', 'synced'} if host else set()
    if set(manifest) != common | host_fields:
        raise ValueError('invalid disk fields')
    if (manifest['kind'] not in ('file', 'host')
            or manifest['mode'] not in ('embed', 'reference')):
        raise ValueError('invalid disk kind/mode')
    if host and manifest['mode'] != 'embed':
        raise ValueError('host disk must be embedded')
    if manifest['mode'] == 'reference':
        if reference is None or buffers:
            raise ValueError(
                'reference image required, without embedded buffers')
        image = Path(reference).read_bytes()
    else:
        image = buffers.get('image')
    _check(image, manifest['image'])
    names = {}
    expected_buffers = {'image'} if manifest['mode'] == 'embed' else set()
    if host:
        if len(image) != HostDirectoryFAT16.total_sectors * 512:
            raise ValueError('host disk geometry mismatch')
        for field in ('entries', 'paths', 'synced'):
            if type(manifest[field]) is not list:
                raise ValueError('invalid host list')
        for entry in manifest['entries']:
            if (type(entry) is not dict
                    or entry.get('kind') not in ('file', 'directory')):
                raise ValueError('invalid host entry')
            keys = {'path', 'kind'} | (
                {'blob', 'data'} if entry['kind'] == 'file' else set())
            if set(entry) != keys:
                raise ValueError('invalid host entry fields')
            name = _relative(entry['path'])
            if name.casefold() in names:
                raise ValueError('duplicate host path')
            names[name.casefold()] = entry['kind']
            if entry['kind'] == 'file':
                blob = entry['blob']
                if type(blob) is not str or blob in expected_buffers:
                    raise ValueError('duplicate/invalid disk buffer')
                expected_buffers.add(blob)
                _check(buffers.get(blob), entry['data'])
        for name in names:
            for parent in PurePosixPath(name).parents:
                if (str(parent) != '.'
                        and names.get(str(parent)) != 'directory'):
                    raise ValueError('missing host parent directory')
        seen = set()
        for item in manifest['paths']:
            if type(item) is not dict or set(item) != {'key', 'path'}:
                raise ValueError('invalid host path mapping')
            key = _key(item['key'])
            _relative(item['path'])
            path_key = tuple(
                HostDirectoryFAT16._dos_name(Path(part))
                for part in item['path'].split('/'))
            if path_key != key:
                raise ValueError('guest key and host path disagree')
            if key in seen:
                raise ValueError('duplicate guest path')
            seen.add(key)
        mapped = seen
        seen = set()
        sync_fields = {'key', 'size', 'chain'} | (
            {'sha256'} if version >= 2 else set())
        for item in manifest['synced']:
            if type(item) is not dict or set(item) != sync_fields:
                raise ValueError('invalid sync record')
            key = _key(item['key'])
            if (key in seen or key not in mapped
                    or type(item['size']) is not int or item['size'] < 0
                    or type(item['chain']) is not list
                    or any(type(cluster) is not int
                           or not 2 <= cluster < 65528
                           for cluster in item['chain'])
                    or (version >= 2 and not _is_sha256(item['sha256']))):
                raise ValueError('invalid sync state')
            seen.add(key)
    if set(buffers) != expected_buffers:
        raise ValueError('unexpected disk buffers')
    return image


def _legacy_synced_hash(disk, size, chain):
    """Recover a v1 guard from guest sectors, never from possibly edited host bytes."""
    if size == 0:
        data = b''
    else:
        data = b''.join(disk._cluster_bytes(cluster) for cluster in chain)
        if len(data) < size:
            raise ValueError('legacy sync state is shorter than its file size')
        data = data[:size]
    return hashlib.sha256(data).hexdigest()


def restore_disk_state(manifest, buffers, destination, reference=None):
    """Restore only to a new directory, preserving original disks and host files.

    A reference is validated then copied too: future guest writes never change
    the reference backing file. Callers must validate every machine component
    before invoking this materialization step.
    """
    image = validate_disk_state(manifest, buffers, reference)
    destination = Path(destination)
    destination.mkdir(parents=False, exist_ok=False)
    if manifest['kind'] == 'file':
        image_path = destination / 'disk.img'
        image_path.write_bytes(image)
        return str(image_path)
    for entry in sorted(
            manifest['entries'],
            key=lambda item: (item['path'].count('/'), item['path'])):
        path = destination / entry['path']
        if entry['kind'] == 'directory':
            path.mkdir()
        else:
            path.write_bytes(buffers[entry['blob']])
    disk = HostDirectoryFAT16.__new__(HostDirectoryFAT16)
    disk.directory = destination.resolve()
    disk.identity = 'host:' + str(disk.directory)
    disk._image = bytearray(image)
    disk._host_paths = {
        _key(item['key']): disk.directory / item['path']
        for item in manifest['paths']
    }
    disk._synced_files = {
        _key(item['key']): (item['size'], tuple(item['chain']))
        for item in manifest['synced']
    }
    if manifest['version'] >= 2:
        disk._synced_hashes = {
            _key(item['key']): item['sha256']
            for item in manifest['synced']
        }
    else:
        disk._synced_hashes = {
            _key(item['key']): _legacy_synced_hash(
                disk, item['size'], tuple(item['chain']))
            for item in manifest['synced']
        }
    return disk
