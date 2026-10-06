"""One stdlib-only complete motherboard fixture for pytest and bare PyPy."""
import ast
import threading
from pathlib import Path
import bus, i8088, i8253, i8255, keyboard, vga, xtide
import debughardware
import debugtrace
import videohistory
import vncserver
from machinesnapshots import LockedDisplay, MachineSnapshots
from tests.test_execution_rpc import HeadlessMachine


def machine(tmp_path):
    disk = tmp_path/'disk.img'; disk.write_bytes(bytes(4096))
    kb = keyboard.Keyboard()
    devices = [i8253.i8253(), kb, i8255.i8255(kb), vga.VGA(False), xtide.XTIDE([str(disk)])]
    b = bus.Bus(1048576, devices, [])
    cpu = i8088.i8088(b, devices, True)
    state = cpu.GetState(); state.SetCS(0x1000); state.SetIP(0); state.SetDS(0x1000)
    # Repeated timer reads, RAM stores and increments exercise host RNG and ticks.
    b._m._m[0x10000:0x1000d] = bytes.fromhex('ba4000eca20002ff060202ebf6')
    cpu._io.Out(0x43, 0x36, False); cpu._io.Out(0x40, 17, False); cpu._io.Out(0x40, 0, False)
    kb.PushKeyboardScancode(0x1e)
    controller = devices[4]
    for port, value in ((0x304,1),(0x306,1),(0x308,0),(0x30a,0),(0x30c,0),(0x30e,0xc5)):
        controller.IO_Write(port,value)
    for value in (11,22,33): controller.IO_Write(0x300,value)
    for _ in range(11): cpu.Tick()
    return cpu


def run(cpu, count=200):
    events = []
    cpu.SetMemoryTraceHook(lambda *args: events.append(args))
    for _ in range(count): cpu.Tick()
    cpu.SetMemoryTraceHook(None)
    for index in range(509): cpu._devices[4].IO_Write(0x300,index%251)
    return events


def rpc_machine(tmp_path):
    harness = HeadlessMachine()
    cpu = machine(tmp_path)
    display = LockedDisplay(cpu._devices[3])
    vnc = vncserver.VNCServer.__new__(vncserver.VNCServer)
    vnc._display = display
    vnc._frame_lock = threading.RLock()
    harness.namespace.update(p=cpu, b=cpu._b, state=cpu.GetState(), scr=cpu._devices[3],
                             debughardware=debughardware, debugtrace=debugtrace,
                             hardware_trace=debughardware.HardwareTraceRecorder(),
                             machine_snapshots=MachineSnapshots(cpu, display, vnc),
                             video_history=videohistory.VideoHistory(cpu.GetState().GetClock))
    # Bind production register methods to this real motherboard before testing.
    tree = ast.parse((Path(__file__).resolve().parents[1]/'main.py').read_text())
    body = next(n.body for n in tree.body if isinstance(n, ast.Try))
    node = next(n for n in body if isinstance(n, ast.Assign) and
                any(isinstance(t, ast.Name) and t.id == 'register_access' for t in n.targets))
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'main.py', 'exec'), harness.namespace)
    return harness, cpu, vnc


