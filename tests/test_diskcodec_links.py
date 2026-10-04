"""Host snapshot link refusal, including Windows PyPy without stat tag constants."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from diskcodec import dump_disk_state, restore_disk_state
from virtualfat16 import HostDirectoryFAT16


class SnapshotLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mount = self.root / 'mount'
        self.mount.mkdir()
        (self.mount / 'DATA.BIN').write_bytes(b'unchanged guest data')
        self.disk = HostDirectoryFAT16(self.mount)

    def test_roundtrip_without_path_junction_or_stat_constant(self):
        with patch.dict(stat.__dict__):
            stat.__dict__.pop('IO_REPARSE_TAG_MOUNT_POINT', None)
            with patch.object(Path, 'is_junction', create=True,
                              side_effect=AttributeError('missing stat constant')):
                manifest, buffers = dump_disk_state(self.disk)
                restored = restore_disk_state(manifest, buffers, self.root / 'restored')
                self.assertEqual(dump_disk_state(restored), (manifest, buffers))
                self.assertEqual((restored.directory / 'DATA.BIN').read_bytes(),
                                 b'unchanged guest data')

    def test_mount_point_tag_refused_without_stat_constant(self):
        original = Path.lstat
        suspect = self.mount / 'DATA.BIN'

        def tagged(path):
            metadata = original(path)
            if path == suspect:
                return SimpleNamespace(st_mode=metadata.st_mode,
                                       st_reparse_tag=0xA0000003)
            return metadata

        with patch.dict(stat.__dict__):
            stat.__dict__.pop('IO_REPARSE_TAG_MOUNT_POINT', None)
            with patch.object(Path, 'lstat', tagged):
                with self.assertRaisesRegex(ValueError, 'linked path'):
                    dump_disk_state(self.disk)

    def test_actual_symlink_refused(self):
        target = self.root / 'target.bin'
        target.write_bytes(b'outside the mounted directory')
        link = self.mount / 'LINK.BIN'
        try:
            link.symlink_to(target)
        except OSError as error:
            self.skipTest('symlink creation unavailable: ' + str(error))
        with self.assertRaisesRegex(ValueError, 'linked path'):
            dump_disk_state(self.disk)

    @unittest.skipUnless(os.name == 'nt', 'Windows directory junction')
    def test_actual_windows_junction_refused(self):
        target = self.root / 'target'
        target.mkdir()
        (target / 'SECRET.BIN').write_bytes(b'outside the mount')
        link = self.mount / 'JUNCTION'
        env = dict(os.environ, PYPC_TEST_JUNCTION=str(link),
                   PYPC_TEST_TARGET=str(target))
        subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                        'New-Item -ItemType Junction -Path $env:PYPC_TEST_JUNCTION '
                        '-Target $env:PYPC_TEST_TARGET | Out-Null'],
                       env=env, check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)
        self.addCleanup(link.rmdir)
        with patch.dict(stat.__dict__):
            stat.__dict__.pop('IO_REPARSE_TAG_MOUNT_POINT', None)
            with self.assertRaisesRegex(ValueError, 'linked path'):
                dump_disk_state(self.disk)
        self.assertEqual((target / 'SECRET.BIN').read_bytes(), b'outside the mount')


if __name__ == '__main__':
    unittest.main()
