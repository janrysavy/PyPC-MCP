"""Exercise CLI validation and real listener bindings in an isolated process."""
import argparse
import ast
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


class ListenerPortTests(unittest.TestCase):
    def parse(self, *args):
        path = Path(__file__).resolve().parents[1] / 'main.py'
        tree = ast.parse(path.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == 'ParseArguments')
        namespace = {'argparse': argparse}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
        with patch.object(sys, 'argv', ['main.py', *args]):
            return namespace['ParseArguments']()

    def test_defaults_and_isolated_ports(self):
        result = self.parse()
        self.assertEqual((result.rpc_port, result.telnet_port, result.vnc_port),
                         (2301, 2300, 5902))
        result = self.parse('--rpc-port', '12301', '--telnet-port', '12300',
                            '--vnc-port', '15902')
        self.assertEqual((result.rpc_port, result.telnet_port, result.vnc_port),
                         (12301, 12300, 15902))

    def test_invalid_or_colliding_ports_are_rejected(self):
        for args in (('--rpc-port', '0'), ('--vnc-port', '65536'),
                     ('--rpc-port', '2300')):
            with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                self.parse(*args)
            self.assertEqual(error.exception.code, 2)

    def test_process_binds_selected_ports_and_advertises_rpc_endpoint(self):
        # Another process can claim a reserved port after it is released.
        # Retry connection/bind failures with a fresh set; assertions are not retried.
        for attempt in range(3):
            try:
                self.exercise_process()
                return
            except ConnectionError:
                if attempt == 2:
                    raise

    def exercise_process(self):
        root = Path(__file__).resolve().parents[1]
        # Reserve distinct free ports together; release immediately before launch.
        reservations = [socket.socket() for _ in range(3)]
        try:
            for sock in reservations:
                sock.bind(('127.0.0.1', 0))
            ports = [sock.getsockname()[1] for sock in reservations]
        finally:
            for sock in reservations:
                sock.close()
        temporary_root = root / '.test-tmp'
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            scratch = Path(directory)
            shutil.copy2(root / 'harddisk.img', scratch / 'harddisk.img')
            shutil.copytree(root / 'roms', scratch / 'roms')
            code = ('import runpy,sys,typing; '
                    'typing.override=getattr(typing,"override",lambda f:f); '
                    'root=sys.argv.pop(1); sys.path.insert(0,root); '
                    'runpy.run_path(root+"/main.py",run_name="__main__")')
            with (scratch / 'process.log').open('w+b') as log:
                process = subprocess.Popen(
                    [sys.executable, '-u', '-c', code, str(root),
                     '--rpc-port', str(ports[0]), '--telnet-port', str(ports[1]),
                     '--vnc-port', str(ports[2])], cwd=scratch,
                    stdout=log, stderr=log)
                try:
                    deadline = time.monotonic() + 15
                    for port in ports:
                        while True:
                            try:
                                connection = socket.create_connection(('127.0.0.1', port), timeout=1)
                                connection.close()
                                break
                            except OSError:
                                if process.poll() is not None or time.monotonic() >= deadline:
                                    log.seek(0)
                                    raise ConnectionError(log.read().decode(errors='replace'))
                                time.sleep(0.02)
                    with socket.create_connection(('127.0.0.1', ports[0]), timeout=3) as client:
                        client.sendall(b'{"jsonrpc":"2.0","id":1,"method":"agent.capabilities"}\n')
                        with client.makefile('rb') as stream:
                            reply = json.loads(stream.readline())
                    self.assertEqual(reply['result']['endpoint'], f'127.0.0.1:{ports[0]}')
                    # Current server greetings identify which listener owns a port;
                    # this does not assert complete protocol conformance.
                    for port, banner in ((ports[1], b'\xff\xf4\x25'),
                                         (ports[2], b'RFB 003.008\n')):
                        with socket.create_connection(('127.0.0.1', port), timeout=3) as client:
                            received = bytearray()
                            while len(received) < len(banner):
                                chunk = client.recv(len(banner) - len(received))
                                if not chunk:
                                    raise ConnectionError('listener closed before greeting completed')
                                received.extend(chunk)
                            self.assertEqual(received, banner)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == '__main__':
    unittest.main()
