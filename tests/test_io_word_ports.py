"""8088 word I/O is two ordered byte transfers, even across unmapped ports."""
import unittest

import bus
import i8088
import pc_io


class Port:
    def __init__(self, value, events, pending=False):
        self.value = value
        self.events = events
        self.pending = pending

    def IO_Read(self, address):
        self.events.append(('read', address))
        return self.value

    def IO_Write(self, address, value):
        self.events.append(('write', address, value))
        return self.pending


class WordPortTests(unittest.TestCase):
    def setUp(self):
        self.io = pc_io.IO(bus.Bus(1 << 20, [], []), [], False)
        self.io._io_map.clear()
        self.events = []

    def port(self, address, value=0, pending=False):
        self.io._io_map[address] = Port(value, self.events, pending)

    def test_word_read_low_only_keeps_open_bus_high_byte(self):
        self.port(0x301, 0x12)
        self.assertEqual(self.io.In(0x301, True), 0xff12)
        self.assertEqual(self.events, [('read', 0x301)])

    def test_word_read_high_only_still_dispatches(self):
        self.port(0x302, 0x34)
        self.assertEqual(self.io.In(0x301, True), 0x34ff)
        self.assertEqual(self.events, [('read', 0x302)])

    def test_word_write_high_only_still_dispatches_and_returns_pending(self):
        self.port(0x302, pending=True)
        self.assertTrue(self.io.Out(0x301, 0xabcd, True))
        self.assertEqual(self.events, [('write', 0x302, 0xab)])

    def test_word_transfers_low_then_high_and_wrap_port_number(self):
        for address in (0x300, 0x301, 0xffff):
            with self.subTest(address=address):
                self.events.clear()
                self.io._io_map.clear()
                other = (address + 1) & 0xffff
                self.port(address, 0x12, pending=True)
                self.port(other, 0x34)
                self.assertEqual(self.io.In(address, True), 0x3412)
                self.assertTrue(self.io.Out(address, 0xabcd, True))
                self.assertEqual(self.events, [('read', address), ('read', other),
                    ('write', address, 0xcd), ('write', other, 0xab)])

    def test_byte_does_not_touch_adjacent_port(self):
        self.port(0x302, 0x34, pending=True)
        self.assertEqual(self.io.In(0x301, False), 0xff)
        self.assertFalse(self.io.Out(0x301, 0xab, False))
        self.assertEqual(self.events, [])

    def test_unmapped_word(self):
        self.assertEqual(self.io.In(0x301, True), 0xffff)
        self.assertFalse(self.io.Out(0x301, 0xabcd, True))

    def test_expansion_bus_byte_applies_to_either_half(self):
        self.assertEqual(self.io.In(0x210, False), 0xa5)
        self.assertEqual(self.io.In(0x210, True), 0xffa5)
        self.assertEqual(self.io.In(0x20f, True), 0xa5ff)

    def test_one_trace_event_per_word_reports_either_half_handled(self):
        events, hardware = [], []
        self.io.SetTraceHook(lambda *args: events.append(args))
        self.io.SetHardwareTraceHook(lambda *args: hardware.append(args))
        address = {'space': 'segmented', 'segment': 0x1000, 'offset': 0x100}
        self.io.SetHardwareTraceContext(address, 123)
        self.port(0x302, 0x34)
        self.io.In(0x301, True)
        self.io.Out(0x301, 0xabcd, True)
        expected = [('io_read', 0x301, 0x34ff, 2, True),
                    ('io_write', 0x301, 0xabcd, 2, True)]
        self.assertEqual(events, expected)
        self.assertEqual(hardware, [(*event, address, 123) for event in expected])

    def test_cpu_in_ax_dx_and_out_dx_ax_use_both_ports(self):
        memory = bus.Bus(1 << 20, [], [])
        cpu = i8088.i8088(memory, [], True)
        cpu._io._io_map = {0x302: Port(0x34, self.events)}
        state = cpu.GetState()
        state.SetCS(0x1000)
        state.SetIP(0x100)
        state.SetDX(0x301)
        state.SetFlags(2)
        cpu.WriteMemByte(0x1000, 0x100, 0xed)  # IN AX,DX
        cpu.WriteMemByte(0x1000, 0x101, 0xef)  # OUT DX,AX
        cpu.Tick()
        self.assertEqual(state.GetAX(), 0x34ff)
        cpu.Tick()
        self.assertEqual(self.events, [('read', 0x302), ('write', 0x302, 0x34)])


if __name__ == '__main__':
    unittest.main()
