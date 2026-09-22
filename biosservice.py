"""Identifiable host VGA BIOS service used by main and machine snapshots."""


class VGAInterruptService:
    def __init__(self, video):
        self.video = video

    def __call__(self, number, registers):
        return (number == 0x10 and registers.GetCS() != 0xf000
                and self.video.BiosInterrupt(registers))
