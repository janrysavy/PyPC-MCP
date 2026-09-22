"""Coordinated guest-machine state; callers own pause, input exclusion and install.

Debugger operations and host transports are deliberately not guest state. Load
constructs a detached machine, so validation failures cannot partly rewind CPU
or device state. Backing disks are materialized only after component validation.
"""
import hashlib
from pathlib import Path

import bus
import i8088
import i8253
import i8255
import keyboard
import rom
import vga
import xtide
from diskcodec import dump_disk_state, validate_disk_state, restore_disk_state
from statecodec import dump_cpu_state, load_cpu_state
from timingcodec import (dump_pic_state, load_pic_state, dump_pit_state, load_pit_state,
                         dump_host_rng_state, load_host_rng_state)
from peripheralcodec import (dump_dma_state, load_dma_state, dump_keyboard_state,
                             load_keyboard_state, dump_ppi_state, load_ppi_state)
from videocodec import dump_video_state, load_video_state
from xtidecodec import dump_xtide_state, load_xtide_state


def source_identity():
    """Bind executable Python sources, independent of checkout path or git state."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(__file__).parent.glob('*.py'))}


def _blob(data):
    return {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def capture_machine(cpu, *, bios_service=False, disk_mode='auto'):
    """Capture the main.py motherboard topology at a stopped instruction boundary.

    Caller holds the keyboard state lock and prevents display readers from
    changing render caches. External filesystem writers must also be excluded.
    """
    devices = cpu._devices
    if (len(devices) != 7 or type(devices[0]) is not i8253.i8253
            or type(devices[1]) is not keyboard.Keyboard
            or type(devices[2]) is not i8255.i8255
            or type(devices[4]) is not xtide.XTIDE
            or devices[5] is not cpu._io._i8237 or devices[6] is not cpu._io._pic
            or cpu._b._devices is not devices or cpu._io._devices is not devices):
        raise ValueError('unsupported motherboard topology')
    if type(bios_service) is not bool or bool(cpu._interrupt_service_hook) != bios_service:
        raise ValueError('BIOS hook configuration mismatch')
    if bios_service and type(devices[3]) is not vga.VGA:
        raise ValueError('BIOS service requires VGA')
    buffers = {'ram': bytes(cpu._b._m._m)}
    video, video_buffers = dump_video_state(devices[3])
    buffers.update({'video/'+k: v for k,v in video_buffers.items()})
    controller, transfer = dump_xtide_state(devices[4]); buffers['xtide'] = transfer
    disks = []
    for index, disk in enumerate(devices[4]._disks):
        manifest, payload = dump_disk_state(disk, disk_mode)
        disks.append(manifest)
        buffers.update({f'disk{index}/'+k: v for k,v in payload.items()})
    roms = []
    for index, device in enumerate(cpu._b._roms):
        if type(device) is not rom.Rom or set(vars(device)) != {'_contents', '_offset'}:
            raise ValueError('unsupported ROM layout')
        key = 'rom'+str(index); buffers[key] = bytes(device._contents)
        roms.append({'offset': device._offset, 'buffer': key})
    return {'format':'pypc.machine', 'version':1, 'source':source_identity(),
            'configuration':{'ram_size':cpu._b._size, 'memory_mask':cpu._MemMask,
                             'run_io':not cpu._io._test_mode,
                             'terminate_on_off_the_rails':cpu._terminate_on_off_the_rails,
                             'bios_service':bios_service},
            'cpu':dump_cpu_state(cpu._state), 'pit':dump_pit_state(devices[0]),
            'keyboard':dump_keyboard_state(devices[1]), 'ppi':dump_ppi_state(devices[2]),
            'video':video, 'xtide':controller, 'pic':dump_pic_state(cpu._io._pic),
            'dma':dump_dma_state(cpu._io._i8237), 'host_rng':dump_host_rng_state(),
            'roms':roms, 'disks':disks, 'buffers':{k:_blob(v) for k,v in buffers.items()}}, buffers


def prepare_machine(manifest, buffers, disk_root, references=None):
    """Validate and construct a detached machine. Return (CPU, local host RNG).

    Installation must activate that RNG before executing any guest instruction.
    No process RNG or live machine is changed here. New disk directories can
    remain after an I/O failure; existing disk paths are never overwritten.
    """
    fields = {'format','version','source','configuration','cpu','pit','keyboard','ppi',
              'video','xtide','pic','dma','host_rng','roms','disks','buffers'}
    if (type(manifest) is not dict or set(manifest) != fields
            or manifest['format'] != 'pypc.machine' or type(manifest['version']) is not int
            or manifest['version'] != 1 or manifest['source'] != source_identity()):
        raise ValueError('machine schema or emulator source mismatch')
    if type(buffers) is not dict or set(buffers) != set(manifest['buffers']):
        raise ValueError('machine buffer inventory mismatch')
    for key, data in buffers.items():
        if type(data) is not bytes or manifest['buffers'][key] != _blob(data):
            raise ValueError('machine buffer hash mismatch')
    config = manifest['configuration']
    if (type(config) is not dict or set(config) != {'ram_size','memory_mask','run_io',
                                                  'terminate_on_off_the_rails','bios_service'}
            or type(config['ram_size']) is not int or config['ram_size'] != 1048576
            or type(config['memory_mask']) is not int or config['memory_mask'] != 0xfffff
            or any(type(config[k]) is not bool for k in ('run_io','terminate_on_off_the_rails','bios_service'))
            or len(buffers.get('ram', b'')) != config['ram_size']):
        raise ValueError('unsupported machine configuration')
    state = load_cpu_state(manifest['cpu'])
    pit = load_pit_state(manifest['pit']); kb = load_keyboard_state(manifest['keyboard'])
    ppi = load_ppi_state(manifest['ppi'], kb)
    video = load_video_state(manifest['video'], {k[6:]:v for k,v in buffers.items() if k.startswith('video/')})
    if config['bios_service'] and type(video) is not vga.VGA:
        raise ValueError('BIOS service requires VGA')
    pic = load_pic_state(manifest['pic']); dma = load_dma_state(manifest['dma'])
    rng = load_host_rng_state(manifest['host_rng'])
    if type(manifest['disks']) is not list or not 0 <= len(manifest['disks']) <= 2:
        raise ValueError('invalid disk inventory')
    references = {} if references is None else references
    payloads = []
    for index, disk in enumerate(manifest['disks']):
        prefix = f'disk{index}/'
        payload = {k[len(prefix):]:v for k,v in buffers.items() if k.startswith(prefix)}
        validate_disk_state(disk, payload, references.get(index)); payloads.append(payload)
    # Validate transfer state before creating disk output directories.
    controller = load_xtide_state(manifest['xtide'], buffers['xtide'], [None]*len(payloads))
    roms = []
    if type(manifest['roms']) is not list:
        raise ValueError('invalid ROM inventory')
    for index, item in enumerate(manifest['roms']):
        if (type(item) is not dict or set(item) != {'offset','buffer'}
                or item['buffer'] != 'rom'+str(index) or type(item['offset']) is not int
                or not 0 <= item['offset'] < 1048576
                or not 2 <= len(buffers.get(item['buffer'],b'')) <= 1048576-item['offset']):
            raise ValueError('invalid ROM descriptor')
        device = rom.Rom.__new__(rom.Rom)
        device._offset = item['offset']; device._contents = list(buffers[item['buffer']])
        roms.append(device)
    expected = {'ram','xtide'} | {'rom'+str(i) for i in range(len(roms))}
    expected |= {'video/'+k for k in manifest['video']['buffers']}
    expected |= {f'disk{i}/'+k for i,payload in enumerate(payloads) for k in payload}
    if set(buffers) != expected:
        raise ValueError('unconsumed machine buffers')
    devices = [pit, kb, ppi, video, controller]
    motherboard = bus.Bus(config['ram_size'], devices, roms)
    cpu = i8088.i8088(motherboard, devices, config['run_io'])
    cpu._state = state; cpu._MemMask = config['memory_mask']
    cpu._terminate_on_off_the_rails = config['terminate_on_off_the_rails']
    motherboard._m._m[:] = buffers['ram']
    # Keep the freshly wired PIC/DMA identities: devices and port maps reference them.
    cpu._io._pic.__dict__.update(pic.__dict__)
    dma._b = motherboard
    cpu._io._i8237.__dict__.update(dma.__dict__)
    if config['bios_service']:
        cpu.SetInterruptServiceHook(lambda number, registers:
            number == 0x10 and registers.GetCS() != 0xf000 and video.BiosInterrupt(registers))
    disk_root = Path(disk_root)
    disk_root.mkdir(parents=False, exist_ok=False)
    controller._disks = [restore_disk_state(disk, payloads[i], disk_root/('disk'+str(i)), references.get(i))
                         for i,disk in enumerate(manifest['disks'])]
    return cpu, rng
