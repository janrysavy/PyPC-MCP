"""8259A fixed-priority IRR/ISR lifecycle, including nested interrupts."""
import pytest

from i8259 import i8259


def configured(auto_eoi=False):
    pic = i8259()
    pic.IO_Write(0x20, 0x13)  # single, edge-triggered, ICW4 follows
    pic.IO_Write(0x21, 8)
    pic.IO_Write(0x21, 3 if auto_eoi else 1)
    pic.IO_Write(0x21, 0)
    return pic


def read_register(pic, command):
    pic.IO_Write(0x20, command)
    return pic.IO_Read(0x20)


@pytest.mark.parametrize('irq', range(8))
def test_acknowledge_moves_request_from_irr_to_isr(irq):
    pic = configured()
    pic.RequestInterruptPIC(irq)
    assert read_register(pic, 0x0a) == 1 << irq
    assert pic.GetPendingInterrupt() == irq
    pic.SetIRQBeingServiced(irq)
    assert read_register(pic, 0x0a) == 0
    assert read_register(pic, 0x0b) == 1 << irq


@pytest.mark.parametrize('irq', range(8))
@pytest.mark.parametrize('specific', [False, True])
def test_new_edge_during_service_survives_eoi(irq, specific):
    pic = configured()
    pic.RequestInterruptPIC(irq)
    pic.SetIRQBeingServiced(irq)
    pic.RequestInterruptPIC(irq)
    assert pic.GetPendingInterrupt() == 255
    pic.IO_Write(0x20, 0x60 | irq if specific else 0x20)
    assert read_register(pic, 0x0b) == 0
    assert read_register(pic, 0x0a) == 1 << irq
    assert pic.GetPendingInterrupt() == irq


@pytest.mark.parametrize('servicing', range(8))
@pytest.mark.parametrize('requesting', range(8))
def test_only_higher_priority_requests_can_preempt(servicing, requesting):
    pic = configured()
    pic.RequestInterruptPIC(servicing)
    pic.SetIRQBeingServiced(servicing)
    pic.RequestInterruptPIC(requesting)
    assert pic.GetPendingInterrupt() == (requesting if requesting < servicing else 255)


@pytest.mark.parametrize('specific', [False, True])
def test_nested_eoi_preserves_outer_service(specific):
    pic = configured()
    for irq in (5, 3, 0):
        pic.RequestInterruptPIC(irq)
        assert pic.GetPendingInterrupt() == irq
        pic.SetIRQBeingServiced(irq)
    assert read_register(pic, 0x0b) == 0x29
    pic.RequestInterruptPIC(4)
    pic.RequestInterruptPIC(6)
    for irq, remaining, highest in ((0, 0x28, 3), (3, 0x20, 5)):
        pic.IO_Write(0x20, 0x60 | irq if specific else 0x20)
        assert read_register(pic, 0x0b) == remaining
        assert pic._int_in_service == highest
        assert pic.GetPendingInterrupt() == (4 if highest == 5 else 255)
    pic.SetIRQBeingServiced(4)
    pic.IO_Write(0x20, 0x64 if specific else 0x20)
    assert pic.GetPendingInterrupt() == 255
    pic.IO_Write(0x20, 0x65 if specific else 0x20)
    assert pic.GetPendingInterrupt() == 6


def test_specific_eoi_can_clear_outer_level_without_clearing_inner():
    pic = configured()
    for irq in (5, 1):
        pic.RequestInterruptPIC(irq)
        pic.SetIRQBeingServiced(irq)
    pic.IO_Write(0x20, 0x65)
    assert read_register(pic, 0x0b) == 2
    assert pic._int_in_service == 1
    pic.RequestInterruptPIC(2)
    assert pic.GetPendingInterrupt() == 255


@pytest.mark.parametrize('mask', [0xff, 0x55, 0xaa, 0x01])
def test_all_pending_requests_masked_returns_no_interrupt_sentinel(mask):
    pic = configured()
    pic.IO_Write(0x21, mask)
    for irq in range(8):
        if mask & (1 << irq):
            pic.RequestInterruptPIC(irq)
    assert pic.GetPendingInterrupt() == 255
    assert read_register(pic, 0x0a) == mask


@pytest.mark.parametrize('irq', range(8))
def test_auto_eoi_allows_next_edge_without_an_eoi_command(irq):
    pic = configured(auto_eoi=True)
    pic.RequestInterruptPIC(irq)
    pic.SetIRQBeingServiced(irq)
    assert read_register(pic, 0x0a) == 0
    assert read_register(pic, 0x0b) == 0
    assert pic._int_in_service == -1
    pic.RequestInterruptPIC(irq)
    assert pic.GetPendingInterrupt() == irq


def test_trace_records_ack_and_new_request_not_spurious_eoi_lower():
    pic = configured()
    events = []
    pic.SetTraceHook(events.append)
    pic.RequestInterruptPIC(1)
    pic.SetIRQBeingServiced(1)
    pic.RequestInterruptPIC(1)
    pic.IO_Write(0x20, 0x20)
    assert [(e['kind'], e['irq']) for e in events] == [
        ('irq_raise', 1), ('irq_dispatch', 1), ('irq_lower', 1), ('irq_raise', 1)]
