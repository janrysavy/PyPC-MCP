"""Mixed disk references and guarded restore on a complete machine, no pytest."""
import copy
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from checkpointbundle import read_bundle, write_bundle
from diskcodec import dump_disk_state
from machinecodec import capture_machine, prepare_machine
from tests.machine_fixture import rpc_machine, run
from virtualfat16 import HostDirectoryFAT16


class SnapshotContractTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / '.test-tmp'
        scratch.mkdir(exist_ok=True)
        self.directory = TemporaryDirectory(dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.h, self.cpu, _ = rpc_machine(self.root)
        host = self.root / 'host'; host.mkdir()
        (host / 'PYRO.DAT').write_bytes(b'original data')
        controller = self.cpu._devices[4]
        host_disk = HostDirectoryFAT16(host)
        controller._disks.append(host_disk)
        controller._serial_numbers.append(str(hash(str(host_disk))))
        # Extend the fixture inventory while retaining its pending disk0 write.
        self.reference = self.root / 'immutable.img'
        self.reference.write_bytes(Path(self.cpu._devices[4]._disks[0]).read_bytes())

    def export(self, name='saved.pypc'):
        return self.h.rpc('machine.snapshot.export', path=str(self.root/name),
                          expected_state_revision=0, disk_mode='reference-files')

    def test_mixed_policy_preserves_complete_host_state_and_legacy_refusal(self):
        saved = capture_machine(self.cpu, disk_mode='reference-files')
        self.assertEqual([d['mode'] for d in saved[0]['disks']], ['reference','embed'])
        self.assertNotIn('disk0/image', saved[1])
        self.assertIn('disk1/image', saved[1])
        self.assertEqual(dump_disk_state(self.cpu._devices[4]._disks[1], 'reference-files'),
                         dump_disk_state(self.cpu._devices[4]._disks[1], 'embed'))
        with self.assertRaisesRegex(ValueError, 'require embedding'):
            capture_machine(self.cpu, disk_mode='reference')
        expected = capture_machine(self.cpu)
        restored, rng = prepare_machine(*saved, self.root/'restore', {0:str(self.reference)})
        random.setstate(rng.getstate())
        self.assertEqual(capture_machine(restored), expected)
        self.assertNotEqual(restored._devices[4]._disks[0], str(self.reference))

    def test_reference_mismatch_refuses_before_any_machine_or_disk_mutation(self):
        exported = self.export()
        before = capture_machine(self.cpu)
        self.reference.write_bytes(b'x' * self.reference.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'size/hash'):
            self.h.rpc('machine.snapshot.import', path=exported['path'],
                       disk_root=str(self.root/'refused'), expected_state_revision=0,
                       expected_sha256=exported['sha256'], references={'0':str(self.reference)})
        self.assertEqual(capture_machine(self.cpu), before)
        self.assertFalse((self.root/'refused').exists())
        self.assertEqual(self.h.control['revision'], 0)

    def test_hash_alias_and_breakpoint_flag_preflight(self):
        exported = self.export()
        before = capture_machine(self.cpu)
        controls = copy.deepcopy(self.h.control)
        invalid = [dict(), dict(sha256=None), dict(sha256=True),
                   dict(sha256='wrong'), dict(expected_sha256=None), dict(expected_sha256=True),
                   dict(expected_sha256='f'*63), dict(expected_sha256=' '*60+'abcd'),
                   dict(expected_sha256='g'*64), dict(expected_sha256='0'*64),
                   dict(expected_sha256=exported['sha256'], sha256=exported['sha256']),
                   dict(preserve_breakpoints=1, expected_sha256=exported['sha256']),
                   dict(preserve_breakpoints=None, expected_sha256=exported['sha256'])]
        for params in invalid:
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.h.rpc('machine.snapshot.import', path=exported['path'],
                           disk_root=str(self.root/'refused'), expected_state_revision=0,
                           references={'0':str(self.reference)}, **params)
            self.assertEqual(capture_machine(self.cpu), before)
            self.assertEqual(self.h.control, controls)
            self.assertFalse((self.root/'refused').exists())

    def test_preserved_definitions_and_hitcounts_or_explicit_clear(self):
        exported = self.export()
        self.assertEqual(exported['bytes'], Path(exported['path']).stat().st_size)
        manager = self.h.namespace['breakpoints']
        bp = self.h.rpc('breakpoints.create', address={'space':'segmented','segment':0x1000,'offset':0})
        manager.check(0x1000, 0, {})
        definitions = self.h.rpc('breakpoints.list')
        self.assertEqual(definitions['breakpoints'][0]['hit_count'], 1)
        self.h.rpc('machine.snapshot.import', path=exported['path'],
                   disk_root=str(self.root/'first'), expected_state_revision=0,
                   expected_sha256=exported['sha256'].upper(), references={'0':str(self.reference)})
        self.assertEqual(self.h.rpc('breakpoints.list'), definitions)
        self.h.rpc('machine.snapshot.import', path=exported['path'],
                   disk_root=str(self.root/'second'), expected_state_revision=1,
                   sha256=exported['sha256'], references={'0':str(self.reference)},
                   preserve_breakpoints=False)
        self.assertEqual(self.h.rpc('breakpoints.list'), {'breakpoints':[]})
        self.assertFalse(self.h.control['breakpoints_active'])

    def test_fresh_process_continuation_and_reference_write_isolation(self):
        saved = capture_machine(self.cpu, disk_mode='reference-files')
        checkpoint = self.root/'saved.pypc'; digest = write_bundle(checkpoint, *saved)
        expected_trace = run(self.cpu)
        expected = capture_machine(self.cpu)
        reference_hash = hashlib.sha256(self.reference.read_bytes()).hexdigest()
        child = '''import sys,random,json
from checkpointbundle import read_bundle,write_bundle
from machinecodec import prepare_machine,capture_machine
from tests.machine_fixture import run
cpu,rng=prepare_machine(*read_bundle(sys.argv[1],sys.argv[2]),sys.argv[3],{0:sys.argv[4]})
random.setstate(rng.getstate())
trace=run(cpu)
write_bundle(sys.argv[5],*capture_machine(cpu))
open(sys.argv[6],'w').write(json.dumps(trace))
'''
        out, trace = self.root/'after.pypc', self.root/'trace.json'
        result = subprocess.run([sys.executable,'-c',child,str(checkpoint),digest,
                                 str(self.root/'fresh'),str(self.reference),str(out),str(trace)],
                                cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,stderr=subprocess.STDOUT, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace'))
        self.assertEqual(read_bundle(out), expected)
        self.assertEqual(json.loads(trace.read_text()), json.loads(json.dumps(expected_trace)))
        self.assertEqual(hashlib.sha256(self.reference.read_bytes()).hexdigest(), reference_hash)


if __name__ == '__main__':
    unittest.main()
