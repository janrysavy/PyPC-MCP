import copy
import unittest
from uart8250 import UART8250

class SerialTests(unittest.TestCase):
    def test_dlab_and_ordered_terminal_bytes(self):
        u=UART8250();u.IO_Write(0x3fb,0x80);u.IO_Write(0x3f8,12);u.IO_Write(0x3f9,0)
        self.assertEqual(u.IO_Read(0x3f8),12);u.IO_Write(0x3fb,3)
        u.host_write(b'R\r')
        self.assertTrue(u.IO_Read(0x3fd)&1)
        self.assertEqual(bytes([u.IO_Read(0x3f8),u.IO_Read(0x3f8)]),b'R\r')
        self.assertEqual(u.IO_Read(0x3fd),0x60)
        u.IO_Write(0x3f8,255);self.assertEqual(u.host_read(0,1)['data_base64'],'/w==')
        self.assertEqual(u.host_read(0,1),u.host_read(0,1))
    def test_invalid_input_atomic_and_overrun_explicit(self):
        u=UART8250();before=u.dump()
        for data in (b'',b'x'*4097,'R'):
            with self.assertRaises(ValueError):u.host_write(data)
            self.assertEqual(u.dump(),before)
        for _ in range(16):u.host_write(bytes(4096))
        with self.assertRaises(ValueError):u.host_write(b'x')
        for _ in range(65537):u.IO_Write(0x3f8,42)
        with self.assertRaises(ValueError):u.host_read(0,1)
    def test_loopback_registers_and_irq(self):
        u=UART8250();u.IO_Write(0x3fc,0x1f)
        self.assertEqual(u.IO_Read(0x3fe)&0xf0,0xf0)
        u.IO_Write(0x3f9,1);u.IO_Write(0x3f8,65)
        self.assertEqual(u.IO_Read(0x3fa),4)
        u.IO_Write(0x3f8,66);self.assertEqual(u.IO_Read(0x3fd)&3,3)
        self.assertEqual(u.IO_Read(0x3f8),65)
        self.assertEqual(u.IO_Read(0x3fa),1)
    def test_snapshot_pending_bytes(self):
        u=UART8250();u.host_write(b'R\r');u.IO_Write(0x3f8,45)
        restored=UART8250.load(u.dump());self.assertEqual(restored.dump(),u.dump())
        self.assertEqual(restored.IO_Read(0x3f8),ord('R'))
        bad=copy.deepcopy(u.dump());bad['rx']=[256]
        with self.assertRaises(ValueError):UART8250.load(bad)

def test_machine_serial_restore(tmp_path):
    import bus,i8088,i8253,i8255,keyboard,vga,xtide
    from machinecodec import capture_machine,prepare_machine
    kb=keyboard.Keyboard();u=UART8250();u.host_write(b'R\r');u.IO_Write(0x3f8,45)
    devices=[i8253.i8253(),kb,i8255.i8255(kb),vga.VGA(False),xtide.XTIDE([]),u]
    cpu=i8088.i8088(bus.Bus(1048576,devices,[]),devices,True)
    m,b=capture_machine(cpu)
    restored,_=prepare_machine(m,b,tmp_path/'restored')
    assert capture_machine(restored)==(m,b)
    assert restored._io.In(0x3f8,False)==ord('R')

if __name__=='__main__':unittest.main()


def test_real_rpc_handler_validation():
    import ast,base64
    from pathlib import Path
    tree=ast.parse(Path('main.py').read_text(encoding='utf-8'))
    functions=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in ('rpc_params','rpc_number','handle_debug')]
    uart=UART8250();ns={'serial':uart,'base64':base64,'control':{'revision':123}}
    exec(compile(ast.Module(body=functions,type_ignores=[]),'main.py','exec'),ns)
    call=lambda m,p:ns['handle_debug']({'method':m,'params':p})
    assert call('serial.write',{'data_base64':'Ug0='})['accepted']==2
    assert ns['control']['revision']==124
    before=uart.dump()
    for bad in ('!',None,'A'*5465,''):
        try:call('serial.write',{'data_base64':bad})
        except ValueError:pass
        else:raise AssertionError('bad serial input accepted')
        assert uart.dump()==before
        assert ns['control']['revision']==124
    assert call('serial.status',{})['rx_pending']==2


def test_machine_mailbox_and_serial(tmp_path):
    import bus,i8088,i8253,i8255,keyboard,vga,xtide,dosmailbox
    from machinecodec import capture_machine,prepare_machine
    kb=keyboard.Keyboard();u=UART8250();u.host_write(b'pending')
    devices=[i8253.i8253(),kb,i8255.i8255(kb),vga.VGA(False),xtide.XTIDE([]),dosmailbox.DOSMailbox(),u]
    cpu=i8088.i8088(bus.Bus(1048576,devices,[]),devices,True)
    manifest,buffers=capture_machine(cpu)
    # Exercise the public persistence boundary, not just the detached codec.
    from checkpointbundle import write_bundle,read_bundle
    archive=tmp_path/'serial-machine.zip'
    digest=write_bundle(archive,manifest,buffers)
    assert read_bundle(archive,digest)==(manifest,buffers)
    restored,_=prepare_machine(manifest,buffers,tmp_path/'restored')
    assert capture_machine(restored)==(manifest,buffers)


def test_serial_restart_in_new_process(tmp_path):
    import json,subprocess,sys
    u=UART8250();u.host_write(bytes(range(256)))
    for b in b'prompt-':u.IO_Write(0x3f8,b)
    path=tmp_path/'serial.json';path.write_text(json.dumps(u.dump()))
    code="""import json,sys
from uart8250 import UART8250
u=UART8250.load(json.load(open(sys.argv[1])))
assert bytes(u.IO_Read(0x3f8) for _ in range(256))==bytes(range(256))
assert u.host_read(0,7)['data_base64']=='cHJvbXB0LQ=='
print('serial queues survive process restart')
"""
    r=subprocess.run([sys.executable,'-c',code,str(path)],stdin=subprocess.DEVNULL,capture_output=True,text=True,check=True)
    assert 'survive process restart' in r.stdout
