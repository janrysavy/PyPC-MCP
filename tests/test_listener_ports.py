"""Exercise CLI validation without starting the machine or listeners."""
import argparse
import ast
from pathlib import Path
import sys
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


if __name__ == '__main__':
    unittest.main()
