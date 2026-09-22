"""Install a prepared machine while preserving transport/debugger references.

The caller stops the CPU and excludes all display readers and keyboard input.
Only use a detached CPU returned by machinecodec.prepare_machine. Installation
consumes its state: the detached CPU must not subsequently execute.
"""
import random


def install_machine(live, prepared, rng):
    """Replace guest state, preserving live objects referenced by host clients.

    No disk I/O occurs here. Adapter changes are refused before mutation because
    frontend capabilities and port routing belong to the live configuration.
    Host debugger hooks/breakpoints stay attached; the RPC coordinator must clear
    pending operations, trace journals and display caches before returning.
    """
    if (type(live) is not type(prepared)
            or len(live._devices) != len(prepared._devices)
            or any(type(a) is not type(b) for a, b in zip(live._devices, prepared._devices))):
        raise ValueError('restore requires the same motherboard and video adapter')
    # Materialize replacements before touching live state. Preserve all existing
    # motherboard links, input locks and debugger callbacks by object identity.
    preserved = {'_b', '_pic', '_i8237', '_kb', '_state_lock', '_trace_hook'}
    replacements = []
    for old, new in zip(live._devices, prepared._devices):
        fields = dict(vars(new))
        fields.update({k: v for k, v in vars(old).items() if k in preserved})
        replacements.append(fields)
    rng_state = rng.getstate()
    # Validate the RNG before the first mutation, even for non-codec callers.
    random.Random().setstate(rng_state)
    for old, fields in zip(live._devices, replacements):
        old.__dict__.clear()
        old.__dict__.update(fields)
    live._state.__dict__.clear()
    live._state.__dict__.update(vars(prepared._state))
    live._b._size = prepared._b._size
    live._b._m = prepared._b._m
    live._b._roms = prepared._b._roms
    live._b.RecreateCache()
    live._MemMask = prepared._MemMask
    live._terminate_on_off_the_rails = prepared._terminate_on_off_the_rails
    live._io._test_mode = prepared._io._test_mode
    if prepared._interrupt_service_hook is None:
        live.SetInterruptServiceHook(None)
    else:
        video = live._devices[3]
        live.SetInterruptServiceHook(lambda number, registers:
            number == 0x10 and registers.GetCS() != 0xf000 and video.BiosInterrupt(registers))
    live._stop_reason = ''
    live._memory_access_stop = None
    live._interrupt_stop = None
    live._instruction_address = None
    random.setstate(rng_state)
