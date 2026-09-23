"""Paused-machine checkpoint coordinator and frontend display exclusion."""
import threading

from dosmailbox import DOSMailbox

from checkpointbundle import read_bundle, write_bundle
from machinecodec import capture_machine, prepare_machine
from machineinstall import install_machine


class LockedDisplay:
    def __init__(self, display):
        self.display = display
        self.lock = threading.RLock()
        self.epoch = 0

    def GetFrameVersion(self):
        with self.lock:
            getter = getattr(self.display, 'GetFrameVersion', None)
            return (self.epoch, getter() if getter else object())

    def GetClock(self):
        with self.lock:
            return (self.epoch, self.display.GetClock())

    def __getattr__(self, name):
        # Frontends access methods, not mutable guest arrays, through this proxy.
        method = getattr(self.display, name)
        if not callable(method):
            raise AttributeError(name)
        def call(*args, **kwargs):
            with self.lock:
                return method(*args, **kwargs)
        return call


class MachineSnapshots:
    def __init__(self, cpu, display, vnc):
        self.cpu, self.display, self.vnc = cpu, display, vnc

    def export(self, path, disk_mode='auto'):
        # Same order as VNC rendering, then keyboard input exclusion.
        with self.vnc._frame_lock, self.display.lock, self.cpu._devices[1]._state_lock:
            state = capture_machine(self.cpu,
                                    bios_service=self.cpu._interrupt_service_hook is not None,
                                    disk_mode=disk_mode)
        return write_bundle(path, *state)

    def restore(self, path, disk_root, sha256=None, references=None):
        with self.vnc._frame_lock, self.display.lock, self.cpu._devices[1]._state_lock:
            saved = read_bundle(path, sha256)
            saved_has_mailbox = (saved[0].get('version') == 2 and
                                 saved[0].get('configuration', {}).get('dos_mailbox') is True)
            live_has_mailbox = any(type(device) is DOSMailbox for device in self.cpu._devices)
            if saved_has_mailbox != live_has_mailbox:
                raise ValueError('DOS mailbox configuration does not match live machine')
            prepared, rng = prepare_machine(*saved, disk_root, references)
            install_machine(self.cpu, prepared, rng)
            # A restored numeric guest frame version can equal a client's old
            # version even when its pixels differ. Epoch prevents that alias.
            self.display.epoch += 1
            self.vnc._frame_cache = None
            self.vnc._frame_cache_version = None
            self.vnc._frame_snapshot = None
            self.vnc._frame_history = []
