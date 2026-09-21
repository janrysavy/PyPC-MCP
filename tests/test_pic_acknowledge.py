"""8259A edge-request lifetime, independent of guest ROMs and timing.

Intel 8259A datasheet, Interrupt Sequence (p.7): INTA clears IRR;
EOI clears ISR. A later edge on an in-service input must remain pending.
"""
import unittest

from i8259 import i8259


class PICAcknowledgeTests(unittest.TestCase):
    def pic(self, auto_eoi=False):
        pic = i8259()
        pic.IO_Write(0x20, 0x13)  # ICW1: single, edge-triggered, ICW4 present
        pic.IO_Write(0x21, 8)
        pic.IO_Write(0x21, 3 if auto_eoi else 1)
        pic.IO_Write(0x21, 0)
        return pic

    def read(self, pic, command):
        pic.IO_Write(0x20, command)
        return pic.IO_Read(0x20)

    def test_acknowledge_moves_request_from_irr_to_isr(self):
        for irq in range(8):
            with self.subTest(irq=irq):
                pic = self.pic()
                pic.RequestInterruptPIC(irq)
                self.assertEqual(pic.GetPendingInterrupt(), irq)
                pic.SetIRQBeingServiced(irq)
                self.assertEqual(self.read(pic, 0x0A), 0)
                self.assertEqual(self.read(pic, 0x0B), 1 << irq)
                self.assertEqual(pic.GetPendingInterrupt(), 255)

    def test_second_edge_survives_specific_and_nonspecific_eoi(self):
        for irq in range(8):
            for specific in (False, True):
                with self.subTest(irq=irq, specific=specific):
                    pic = self.pic()
                    pic.RequestInterruptPIC(irq)
                    pic.SetIRQBeingServiced(irq)
                    pic.RequestInterruptPIC(irq)
                    self.assertEqual(pic.GetPendingInterrupt(), 255)
                    pic.IO_Write(0x20, (0x60 | irq) if specific else 0x20)
                    self.assertEqual(self.read(pic, 0x0B), 0)
                    self.assertEqual(pic.GetPendingInterrupt(), irq)
                    pic.SetIRQBeingServiced(irq)
                    pic.IO_Write(0x20, 0x20)
                    self.assertEqual(pic.GetPendingInterrupt(), 255)

    def test_eoi_without_an_active_service_preserves_pending_edge(self):
        pic = self.pic()
        pic.RequestInterruptPIC(3)
        pic.IO_Write(0x20, 0x63)
        self.assertEqual(pic.GetPendingInterrupt(), 3)

    def test_masked_requests_return_no_interrupt_sentinel(self):
        pic = self.pic()
        pic.IO_Write(0x21, 0xFF)
        pic.RequestInterruptPIC(0)
        self.assertEqual(pic.GetPendingInterrupt(), 255)
        self.assertEqual(self.read(pic, 0x0A), 1)
        pic.IO_Write(0x21, 0xFE)
        self.assertEqual(pic.GetPendingInterrupt(), 0)

    def test_auto_eoi_also_consumes_request_at_acknowledge(self):
        pic = self.pic(auto_eoi=True)
        for _ in range(2):
            pic.RequestInterruptPIC(1)
            self.assertEqual(pic.GetPendingInterrupt(), 1)
            pic.SetIRQBeingServiced(1)
            self.assertEqual(self.read(pic, 0x0A), 0)
            self.assertEqual(self.read(pic, 0x0B), 0)
            self.assertEqual(pic.GetPendingInterrupt(), 255)

    def test_trace_retains_second_edge_across_eoi(self):
        pic = self.pic()
        events = []
        pic.SetTraceHook(events.append)
        pic.RequestInterruptPIC(0)
        pic.SetIRQBeingServiced(0)
        pic.RequestInterruptPIC(0)
        pic.IO_Write(0x20, 0x20)
        self.assertEqual([event['kind'] for event in events],
                         ['irq_raise', 'irq_dispatch', 'irq_lower', 'irq_raise'])
        self.assertTrue(all(event['irq'] == 0 for event in events))


if __name__ == '__main__':
    unittest.main()
