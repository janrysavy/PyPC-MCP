"""Real game-port I/O, atomic RPC input and persistent charge continuation."""
import copy
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import typing
import unittest

if not hasattr(typing, 'override'):
    typing.override = lambda f: f

from gameport import GamePort


class GamePortTests(unittest.TestCase):
    def test_active_low_buttons_and_axis_charge_boundaries(self):
        device = GamePort()
        device.set_input(dict(joystick=0, x=-1, y=1, buttons=[True, False]))
        device.IO_Write(0x201, 255)
        self.assertEqual(device.IO_Read(0x201), 0xef)
        device.Tick(120, 120)
        self.assertEqual(device.IO_Read(0x201) & 3, 3)
        device.Tick(1, 121)
        self.assertEqual(device.IO_Read(0x201) & 3, 2)
        device.Tick(5246, 5367)
        self.assertEqual(device.IO_Read(0x201) & 3, 2)
        device.Tick(1, 5368)
        self.assertEqual(device.IO_Read(0x201) & 3, 0)
        device.IO_Write(0x201, 0)
        self.assertEqual(device.IO_Read(0x201) & 15, 15)

    def test_invalid_late_fields_leave_all_state_unchanged(self):
        device = GamePort()
        before = device.dump()
        for bad in [dict(joystick=0, x=-1, y=1.01, buttons=[True, False]),
                    dict(joystick=0, x=-1, y=0, buttons=[True, 1]),
                    dict(joystick=True, x=0, y=0, buttons=[False, False]),
                    dict(joystick=0, x=float('nan'), y=0, buttons=[False, False])]:
            with self.assertRaises(ValueError):
                device.set_input(bad)
            self.assertEqual(device.dump(), before)

    def test_position_change_does_not_rearm_an_expired_axis(self):
        device = GamePort()
        device.set_input(dict(joystick=1, x=-1, y=-1, buttons=[False, True]))
        device.IO_Write(0x201, 0)
        device.Tick(200, 200)
        device.set_input(dict(joystick=1, x=1, y=1, buttons=[False, True]))
        device.Tick(1, 201)
        self.assertEqual(device.IO_Read(0x201) & 0x8c, 0)

    def test_component_restore_keeps_an_in_flight_charge(self):
        original = GamePort()
        original.set_input(dict(joystick=0, x=-.4, y=.7, buttons=[True, False]))
        original.IO_Write(0x201, 0)
        original.Tick(500, 500)
        saved = json.loads(json.dumps(original.dump()))
        restored = GamePort.load(saved)
        for ticks in (100, 1000, 1000, 2000):
            original.Tick(ticks, original._clock + ticks)
            restored.Tick(ticks, restored._clock + ticks)
            self.assertEqual(restored.IO_Read(0x201), original.IO_Read(0x201))
            self.assertEqual(restored.dump(), original.dump())
        bad = copy.deepcopy(saved)
        bad['active'][3] = 1
        with self.assertRaises(ValueError):
            GamePort.load(bad)

    def test_production_rpc_refuses_running_and_absent_card(self):
        from test_execution_rpc import HeadlessMachine
        m = HeadlessMachine()
        m.namespace['joystick'] = None
        self.assertEqual(m.rpc('input.joystick.state')['joysticks'], [])
        event = dict(joystick=0, x=-1, y=1, buttons=[True, False])
        with self.assertRaises(ValueError):
            m.rpc('input.joystick', **event)
        device = GamePort()
        m.namespace['joystick'] = device
        clock = m.state.GetClock()
        state = m.rpc('input.joystick', **event)
        self.assertEqual(state['joysticks'][0], event)
        self.assertEqual(m.state.GetClock(), clock)
        revision = state['state_revision']
        with self.assertRaises(ValueError):
            m.rpc('input.joystick', **{**event, 'buttons': [True, 1]})
        self.assertEqual(m.rpc('input.joystick.state')['state_revision'], revision)
        m.control['paused'] = False
        with self.assertRaises(ValueError):
            m.rpc('input.joystick', **event)

    def test_actual_bundle_and_cpu_io_continuation_with_and_without_uart(self):
        import bus, i8088, i8253, i8255, keyboard, vga, xtide
        from uart8250 import UART8250
        from checkpointbundle import read_bundle, write_bundle
        from machinecodec import capture_machine, prepare_machine
        for serial in (False, True):
            with self.subTest(serial=serial), tempfile.TemporaryDirectory() as directory:
                kb = keyboard.Keyboard()
                device = GamePort()
                devices = [i8253.i8253(), kb, i8255.i8255(kb), vga.VGA(False), xtide.XTIDE([])]
                if serial:
                    devices.append(UART8250())
                devices.append(device)
                memory = bus.Bus(1 << 20, devices, [])
                original = i8088.i8088(memory, devices, True)
                state = original.GetState()
                state.SetCS(0x1000); state.SetIP(0); state.SetDS(0x1000)
                # MOV DX,201h; OUT DX,AL; IN AL,DX; MOV [0200h],AL; JMP IN.
                memory._m._m[0x10000:0x1000b] = bytes.fromhex('ba0102eeeca20002ebfa90')
                device.set_input(dict(joystick=0, x=-1, y=.5, buttons=[True, False]))
                for _ in range(5):
                    original.Tick()
                manifest, buffers = capture_machine(original)
                self.assertEqual(manifest['version'], 4)
                path = Path(directory) / 'state.zip'
                digest = write_bundle(path, manifest, buffers)
                restored, rng = prepare_machine(*read_bundle(path, digest), Path(directory) / 'disk')
                def continuation(cpu):
                    samples = []
                    for _ in range(600):
                        cpu.Tick()
                        samples.append(cpu._io.In(0x201, False))
                    return samples
                expected = continuation(original)
                expected_manifest, expected_buffers = capture_machine(original)
                random.setstate(rng.getstate())
                self.assertEqual(continuation(restored), expected)
                actual_manifest, actual_buffers = capture_machine(restored)
                self.assertEqual(actual_manifest, expected_manifest)
                self.assertEqual(actual_buffers, expected_buffers)
                self.assertNotEqual(expected[0] & 3, expected[-1] & 3)
                child = r'''
import hashlib,json,random,sys,typing
if not hasattr(typing,'override'): typing.override=lambda f:f
from pathlib import Path
from checkpointbundle import read_bundle
from machinecodec import prepare_machine,capture_machine
cpu,rng=prepare_machine(*read_bundle(sys.argv[1],sys.argv[2]),Path(sys.argv[3]))
random.setstate(rng.getstate())
samples=[]
for _ in range(600):
    cpu.Tick();samples.append(cpu._io.In(0x201,False))
manifest,buffers=capture_machine(cpu)
print(json.dumps(dict(samples=samples,manifest=manifest,buffers={k:hashlib.sha256(v).hexdigest() for k,v in buffers.items()})))
'''
                completed = subprocess.run(
                    [sys.executable, '-c', child, str(path), digest, str(Path(directory) / 'fresh')],
                    cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                fresh = json.loads(completed.stdout)
                self.assertEqual(fresh['samples'], expected)
                self.assertEqual(fresh['manifest'], expected_manifest)
                self.assertEqual(fresh['buffers'], {k:hashlib.sha256(v).hexdigest() for k,v in expected_buffers.items()})


if __name__ == '__main__':
    unittest.main()
