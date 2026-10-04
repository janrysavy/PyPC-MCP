"""IRQ dispatch is a CPU boundary, not execution of its interrupted opcode.

Runs the production CPU/PIC, RPC handlers and main loop via the existing RAM-only
harness. Synthetic IRQ sources register a line; no instruction/trace logic is mocked.
"""
import unittest

import device
from i8253 import i8253
from test_execution_rpc import HeadlessMachine


class IRQSource(device.Device):
    def __init__(self, irq):
        super().__init__()
        self.irq = irq

    def GetIRQNumber(self): return self.irq
    def GetName(self): return 'synthetic-irq-source'
    def RegisterDevice(self, mappings): pass
    def IO_Write(self, port, value): return False
    def IO_Read(self, port): return 0
    def GetAddressList(self): return []
    def WriteByte(self, offset, value): pass
    def ReadByte(self, offset): return 0
    def Ticks(self): return False


class ClockRecorder(IRQSource):
    def __init__(self):
        super().__init__(-1)
        self.calls = []

    def Ticks(self): return True
    def Tick(self, cycles, clock):
        self.calls.append((cycles, clock))
        return False


def machine(irq=0, halted=False):
    m = HeadlessMachine(devices=[IRQSource(irq)])
    m.load(bytes.fromhex('4090'))  # INC AX; NOP: neither may execute on dispatch.
    m.load(bytes.fromhex('cf'), offset=0x200)  # IRET back to untouched INC AX.
    m.cpu.WriteMemWord(0, (8 + irq) * 4, 0x200)
    m.cpu.WriteMemWord(0, (8 + irq) * 4 + 2, 0x1000)
    m.state.SetFlags(0x202)
    m.state._in_hlt = halted
    m.cpu._io.GetPIC().IO_Write(0x21, 0)
    return m


def step(m):
    m.rpc('execution.step')
    m.pump()


class InterruptDispatchTraceTests(unittest.TestCase):
    def test_pit_generated_irq_wakes_executed_hlt_and_clocks_devices_once(self):
        pit, recorder = i8253(), ClockRecorder()
        m = HeadlessMachine(devices=[pit, recorder])
        m.load(bytes.fromhex('f440'))  # Execute HLT; then INC AX after IRQ/IRET.
        m.load(bytes.fromhex('cf'), offset=0x200)
        m.cpu.WriteMemWord(0, 32, 0x200)
        m.cpu.WriteMemWord(0, 34, 0x1000)
        m.state.SetFlags(0x202)
        pit.IO_Write(0x43, 0x34)  # Channel0 periodic mode2, one PIT tick.
        pit.IO_Write(0x40, 1)
        pit.IO_Write(0x40, 0)
        pic = m.cpu._io.GetPIC()  # Initially masked; no direct IRQ request.
        m.rpc('hardware.trace.start')
        m.rpc('trace.start', instruction_count=6)
        step(m)  # Actual F4 sets HLT, IP now101.
        self.assertTrue(m.state._in_hlt)
        self.assertEqual(m.state.GetIP(), 0x101)
        step(m)  # Normal PIT clocking raises IRQ0 while masked.
        step(m)  # Still halted, no accepted interrupt.
        self.assertTrue(m.state._in_hlt)
        self.assertEqual(pic.GetPendingInterrupt(), 255)
        self.assertEqual(pic._irr, 1)
        pic.IO_Write(0x21, 0)
        before = m.state.GetAX()
        step(m)  # Wake and dispatch; INC has not executed.
        self.assertFalse(m.state._in_hlt)
        self.assertEqual(m.state.GetAX(), before)
        self.assertEqual(m.state.GetIP(), 0x200)
        self.assertEqual(m.cpu.ReadMemWord(0x2000, 0x8ffa), 0x101)
        self.assertEqual(recorder.calls, [(2, 2), (2, 4), (2, 6), (60, 66)])
        self.assertEqual((pit._timers[0].counter_cur, pit._clock), (1, 2))
        self.assertEqual((pic._isr, pic._irr), (1, 1))  # New PIT edge during entry.
        events = m.rpc('trace.read')['events']
        self.assertEqual([e['kind'] for e in events], ['instruction', 'hlt', 'hlt', 'interrupt_dispatch'])
        self.assertTrue(events[0]['opcode_hex'].startswith('f440'))
        self.assertTrue(events[3]['opcode_hex'].startswith('40'))
        hw = m.rpc('hardware.trace.read')['events']
        self.assertTrue(any(e['kind'] == 'irq_raise' and e['emulated_time'] == 2 for e in hw))
        for _ in range(2): step(m)
        self.assertEqual(m.state.GetAX(), (before + 1) & 0xffff)
        self.assertEqual([e['kind'] for e in m.rpc('trace.read')['events']][-2:], ['instruction', 'instruction'])

    def test_all_pic_lines_and_hlt_wake_have_dispatch_not_opcode(self):
        for irq in range(8):
            for halted in (False, True):
                with self.subTest(irq=irq, halted=halted):
                    m = machine(irq, halted)
                    m.rpc('hardware.trace.start')
                    m.rpc('trace.start', instruction_count=4)
                    m.cpu._io.GetPIC().RequestInterruptPIC(irq)
                    before = m.state.GetAX()
                    step(m)
                    event = m.rpc('trace.read')['events'][0]
                    self.assertEqual(event['kind'], 'interrupt_dispatch')
                    self.assertEqual(event['interrupt'], {'source': 'pic', 'irq': irq, 'vector': 8 + irq})
                    self.assertEqual(event['clock_delta'], 60)  # Existing estimate, not hardware proof.
                    self.assertEqual(m.state.GetAX(), before)
                    self.assertEqual(m.state.GetIP(), 0x200)
                    self.assertEqual(m.state.GetSP(), 0x8ffa)
                    self.assertEqual(m.cpu.ReadMemWord(0x2000, 0x8ffa), 0x100)
                    self.assertTrue(event['opcode_hex'].startswith('4090'))  # Context only.
                    reads = [e['address']['offset'] for e in event['effects'] if e['kind'] == 'memory_read']
                    self.assertEqual(reads, list(range((8 + irq) * 4, (8 + irq) * 4 + 4)))
                    writes = [e for e in event['effects'] if e['kind'] == 'memory_write']
                    self.assertEqual(len(writes), 6)
                    hw = m.rpc('hardware.trace.read')['events']
                    dispatch = [e for e in hw if e['kind'] == 'irq_dispatch']
                    self.assertEqual(len(dispatch), 1)
                    self.assertEqual((dispatch[0]['irq'], dispatch[0]['vector']), (irq, 8 + irq))
                    self.assertEqual(dispatch[0]['emulated_time'], event['clock_before'])
                    self.assertEqual(dispatch[0]['address'], event['address'])
                    for _ in range(3): step(m)
                    events = m.rpc('trace.read')['events']
                    self.assertEqual([e['kind'] for e in events], ['interrupt_dispatch'] + ['instruction'] * 3)
                    self.assertEqual([e['address']['offset'] for e in events], [0x100, 0x200, 0x100, 0x101])
                    self.assertEqual(m.state.GetAX(), (before + 1) & 0xffff)
                    self.assertFalse(any('interrupt' in e for e in events[1:]))
                    self.assertFalse(m.rpc('trace.read')['active'])  # Budget remains four CPU ticks.

    def test_unaccepted_requests_remain_instructions(self):
        for blocked in ('if', 'mask', 'shadow', 'no_request'):
            with self.subTest(blocked=blocked):
                m = machine()
                if blocked != 'no_request': m.cpu._io.GetPIC().RequestInterruptPIC(0)
                if blocked == 'if': m.state.SetFlagI(False)
                if blocked == 'mask': m.cpu._io.GetPIC().IO_Write(0x21, 255)
                if blocked == 'shadow': m.state._inhibit_interrupts = True
                m.rpc('trace.start', instruction_count=1)
                step(m)
                event = m.rpc('trace.read')['events'][0]
                self.assertEqual(event['kind'], 'instruction')
                self.assertNotIn('interrupt', event)
                self.assertEqual(m.state.GetIP(), 0x101)

    def test_dispatch_metadata_survives_each_trace_detail(self):
        for detail in ('csip', 'short', 'normal', 'long'):
            with self.subTest(detail=detail):
                m = machine()
                m.rpc('trace.start', detail=detail, instruction_count=1)
                m.cpu._io.GetPIC().RequestInterruptPIC(0)
                step(m)
                event = m.rpc('trace.read')['events'][0]
                self.assertEqual(event['kind'], 'interrupt_dispatch')
                self.assertEqual(event['interrupt']['vector'], 8)
                self.assertEqual('registers_before' in event, detail != 'csip')
                self.assertEqual('registers_after' in event, detail in ('normal', 'long'))

    def test_idle_hlt_and_software_int_are_not_pic_dispatch(self):
        m = machine(halted=True)
        m.rpc('trace.start', instruction_count=1)
        step(m)
        self.assertEqual(m.rpc('trace.read')['events'][0]['kind'], 'hlt')
        m = machine()
        m.load(bytes.fromhex('cd08'))
        m.rpc('trace.start', instruction_count=1)
        step(m)
        event = m.rpc('trace.read')['events'][0]
        self.assertEqual(event['kind'], 'instruction')
        self.assertNotIn('interrupt', event)
        self.assertEqual(m.state.GetIP(), 0x200)

    def test_remapped_pic_vector_is_reported(self):
        m = machine(3)
        pic = m.cpu._io.GetPIC()
        pic.IO_Write(0x20, 0x13)  # Single PIC, ICW4 follows.
        pic.IO_Write(0x21, 0x20)
        pic.IO_Write(0x21, 0x01)
        pic.IO_Write(0x21, 0)
        m.cpu.WriteMemWord(0, 0x23 * 4, 0x200)
        m.cpu.WriteMemWord(0, 0x23 * 4 + 2, 0x1000)
        m.rpc('trace.start', instruction_count=1)
        pic.RequestInterruptPIC(3)
        step(m)
        event = m.rpc('trace.read')['events'][0]
        self.assertEqual(event['interrupt'], {'source': 'pic', 'irq': 3, 'vector': 0x23})
        self.assertEqual(m.state.GetIP(), 0x200)

    def test_post_instruction_tf_trap_remains_an_instruction(self):
        m = machine()
        m.state.SetFlags(0x302)
        m.cpu.WriteMemWord(0, 4, 0x200)
        m.cpu.WriteMemWord(0, 6, 0x1000)
        before = m.state.GetAX()
        m.rpc('trace.start', instruction_count=1)
        step(m)
        event = m.rpc('trace.read')['events'][0]
        self.assertEqual(event['kind'], 'instruction')
        self.assertNotIn('interrupt', event)
        self.assertEqual(m.state.GetAX(), (before + 1) & 0xffff)
        self.assertEqual(m.state.GetIP(), 0x200)


if __name__ == '__main__':
    unittest.main()
