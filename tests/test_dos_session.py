"""Python-only worker selection, fixture wiring, and owned-session failures."""
from __future__ import annotations
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from guest import dos_session as session


class StopBeforeBoot(Exception):
    pass


class SessionTests(unittest.TestCase):
    def fixture_root(self, directory):
        root = Path(directory) / 'repo'
        (root / 'guest').mkdir(parents=True)
        (root / 'roms').mkdir()
        (root / 'guest/dos_control.asm').write_text('bits 16\nint 20h\n')
        (root / 'harddisk.img').write_bytes(b'DISK-UNCHANGED')
        return root

    def test_explicit_python_prefix_never_looks_up_native(self):
        command = [sys.executable, '-S', '/path with spaces/assembler.py', '-Ox']
        with mock.patch.dict(os.environ, {'NASM_COMMAND': json.dumps(command)}, clear=True), \
             mock.patch.object(session.shutil, 'which', side_effect=AssertionError('native lookup')):
            self.assertEqual(session.assembler_command(), command)

    def test_unset_command_uses_native_but_missing_native_is_explicit(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(session.shutil, 'which', return_value='/native path/nasm'):
            self.assertEqual(session.assembler_command(), ['/native path/nasm'])
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(session.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'NASM_COMMAND'):
                session.assembler_command()

    def test_invalid_command_refused_without_native_fallback(self):
        values = ['', 'not json', 'null', 'false', '{}', '[]', '"nasm"',
                  '[1]', '[""]', '["   "]', '["ok", null]', '["bad\\u0000argument"]']
        for value in values:
            with self.subTest(value=value), \
                 mock.patch.dict(os.environ, {'NASM_COMMAND': value}, clear=True), \
                 mock.patch.object(session.shutil, 'which', side_effect=AssertionError('fallback')):
                with self.assertRaises(ValueError):
                    session.assembler_command()

    def test_worker_argv_and_receipt_retain_actual_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fixture_root(tmp)
            output, listing = Path(tmp)/'worker.com', Path(tmp)/'worker.lst'
            prefix = [sys.executable, '-S', '/path with spaces/assembler.py', '-Ox']
            def assemble(argv, **kwargs):
                self.assertEqual(argv[:len(prefix)], prefix)
                self.assertNotIn('shell', kwargs)
                self.assertTrue(kwargs['check'])
                output.write_bytes(b'\xcd\x20')
                listing.write_text('fresh expanded listing\n')
                return subprocess.CompletedProcess(argv, 0, b'new build', b'')
            with mock.patch.object(session, 'ROOT', root), \
                 mock.patch.object(session.subprocess, 'run', side_effect=assemble) as run:
                report = session.build_worker(prefix, output, listing)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(report['binary_bytes'], 2)
            self.assertEqual(report['stdout'], 'new build')
            self.assertEqual(report['argv'][-2:], ['-l', str(listing)])
            self.assertEqual(report['binary_sha256'], session.digest(output))

    def test_assembler_failure_removes_partial_outputs_and_does_not_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fixture_root(tmp)
            output, listing = Path(tmp)/'worker.com', Path(tmp)/'worker.lst'
            output.write_bytes(b'STALE')
            listing.write_text('STALE')
            def fail(argv, **kwargs):
                self.assertFalse(output.exists())
                self.assertFalse(listing.exists())
                output.write_bytes(b'PARTIAL')
                listing.write_text('PARTIAL')
                raise subprocess.CalledProcessError(9, argv)
            with mock.patch.object(session, 'ROOT', root), \
                 mock.patch.object(session.subprocess, 'run', side_effect=fail) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    session.build_worker(['configured-assembler'], output, listing)
            self.assertEqual(run.call_count, 1)
            self.assertFalse(output.exists())
            self.assertFalse(listing.exists())

    def test_zero_exit_without_fresh_outputs_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fixture_root(tmp)
            output, listing = Path(tmp)/'worker.com', Path(tmp)/'worker.lst'
            for empty in (False, True):
                def no_product(argv, **kwargs):
                    if empty:
                        output.write_bytes(b'')
                        listing.write_text('')
                    return subprocess.CompletedProcess(argv, 0, b'', b'')
                with self.subTest(empty=empty), mock.patch.object(session, 'ROOT', root), \
                     mock.patch.object(session.subprocess, 'run', side_effect=no_product):
                    with self.assertRaises((OSError, RuntimeError)):
                        session.build_worker(['assembler'], output, listing)
                self.assertFalse(output.exists())
                self.assertFalse(listing.exists())

    def test_output_aliases_are_refused_before_source_or_output_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fixture_root(tmp)
            source = root/'guest/dos_control.asm'
            original = source.read_bytes()
            for output, listing in ((source, Path(tmp)/'out.lst'), (Path(tmp)/'out',)*2,
                                    (Path(tmp)/'out.com', source)):
                with self.subTest(output=str(output), listing=str(listing)), \
                     mock.patch.object(session, 'ROOT', root), \
                     mock.patch.object(session.subprocess, 'run', side_effect=AssertionError('executed')):
                    with self.assertRaises(ValueError):
                        session.build_worker(['assembler'], output, listing)
            self.assertEqual(source.read_bytes(), original)

    def test_fixture_reaches_configured_assembler_without_native(self):
        # Inspect the actual fixture wiring without importing pytest. Decorator
        # removal changes scheduling only; the exact fixture body still executes.
        tree = ast.parse((ROOT/'tests/test_dos_live.py').read_text())
        fixture = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                       and n.name == 'live_dos')
        fixture.decorator_list = []
        unit = ast.fix_missing_locations(ast.Module(body=[fixture], type_ignores=[]))
        scope = {'dos_session': session.dos_session, 'shutil': session.shutil}
        exec(compile(unit, 'tests/test_dos_live.py', 'exec'), scope)
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fixture_root(tmp)
            scratch = Path(tmp)/'scratch'
            scratch.mkdir()
            factory = mock.Mock()
            factory.mktemp.return_value = scratch
            command = [sys.executable, '-S', '-m', 'pynasm', '-Ox']
            with mock.patch.dict(os.environ, {'NASM_COMMAND': json.dumps(command)}, clear=True), \
                 mock.patch.object(session, 'ROOT', root), \
                 mock.patch.object(session.shutil, 'which', return_value=None), \
                 mock.patch.object(session, 'build_worker', side_effect=StopBeforeBoot) as build:
                with self.assertRaises(StopBeforeBoot):
                    next(scope['live_dos'](factory))
            self.assertEqual(build.call_args.args[0], command)

    def test_nonempty_scratch_is_refused_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            (scratch/'KEEP').write_bytes(b'KEEP')
            with mock.patch.object(session, 'assembler_command', return_value=['assembler']):
                with self.assertRaisesRegex(ValueError, 'empty scratch'):
                    with session.dos_session(scratch):
                        self.fail('entered session')
            self.assertEqual((scratch/'KEEP').read_bytes(), b'KEEP')

    def test_real_session_module_imports_without_site_packages(self):
        result = subprocess.run([sys.executable, '-S', '-B', '-c',
            "import sys; import guest.dos_session; "
            "assert 'pytest' not in sys.modules; print('stdlib import OK')"],
            cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'stdlib import OK\n')

    def test_failed_boot_stops_owned_process_and_preserves_input_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fixture_root(tmp)
            scratch = Path(tmp)/'scratch'
            scratch.mkdir()
            process = mock.Mock()
            process.poll.return_value = None
            def built(command, output, listing):
                Path(output).write_bytes(b'WORKER')
                return {'argv': list(command)}
            with mock.patch.object(session, 'ROOT', root), \
                 mock.patch.dict(os.environ, {'NASM_COMMAND': '["assembler"]'}, clear=True), \
                 mock.patch.object(session, 'build_worker', side_effect=built), \
                 mock.patch.object(session.subprocess, 'Popen', return_value=process) as popen, \
                 mock.patch.object(session.LiveDOS, 'wait', side_effect=RuntimeError('boot failed')), \
                 mock.patch.object(session.LiveDOS, 'screen', return_value='FAILED BOOT'):
                with self.assertRaisesRegex(RuntimeError, 'boot failed'):
                    with session.dos_session(scratch):
                        self.fail('entered session')
            process.terminate.assert_called_once_with()
            process.wait.assert_called_once_with(timeout=5)
            args = popen.call_args.args[0]
            self.assertEqual('-S' in args, bool(sys.flags.no_site))
            self.assertIn('-B', args)
            self.assertNotIn('shell', popen.call_args.kwargs)
            self.assertEqual((root/'harddisk.img').read_bytes(), b'DISK-UNCHANGED')

    def test_assembly_interrupt_removes_partial_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.fixture_root(tmp)
            output, listing = Path(tmp)/'worker.com', Path(tmp)/'worker.lst'
            def interrupt(*args, **kwargs):
                output.write_bytes(b'PARTIAL')
                raise KeyboardInterrupt
            with mock.patch.object(session, 'ROOT', root), \
                 mock.patch.object(session.subprocess, 'run', side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    session.build_worker(['assembler'], output, listing)
            self.assertFalse(output.exists())
            self.assertFalse(listing.exists())


if __name__ == '__main__':
    unittest.main()
