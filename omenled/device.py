"""High-level lighting control.

Holds a local framebuffer of one RGB pixel per LED and renders it as three
plane reports. The device keeps no state we can read back — see
`docs/protocol.md` — so the framebuffer here is the only picture of what is
currently displayed, and a render always writes every LED.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from . import protocol
from .journal import Journal
from .transport import Transport

Pixel = Tuple[int, int, int]


class Lighting:
    """Controls the diamond.

    Use as a context manager; it blanks nothing on exit, so whatever you last
    rendered stays on the monitor after the process ends. That is deliberate —
    a lighting tool that turned the lights off when it exited would be useless.
    """

    def __init__(self, transport: Transport, count: int = protocol.LED_COUNT):
        self.transport = transport
        self.count = count
        self.pixels: List[Pixel] = [(0, 0, 0)] * count

    # -- framebuffer -------------------------------------------------------

    def fill(self, colour: Pixel) -> "Lighting":
        self.pixels = [colour] * self.count
        return self

    def set_led(self, index: int, colour: Pixel) -> "Lighting":
        if not 0 <= index < self.count:
            raise IndexError(f"LED {index} outside 0..{self.count - 1}")
        self.pixels[index] = colour
        return self

    def set_all(self, pixels: Sequence[Pixel]) -> "Lighting":
        if len(pixels) > self.count:
            raise ValueError(f"{len(pixels)} pixels exceeds {self.count} LEDs")
        self.pixels = list(pixels) + [(0, 0, 0)] * (self.count - len(pixels))
        return self

    def gradient(self, start: Pixel, end: Pixel) -> "Lighting":
        return self.set_all(protocol.gradient(start, end, self.count))

    # -- output ------------------------------------------------------------

    def render(self, dry_run: bool = False) -> "Lighting":
        """Write the framebuffer as three plane reports."""
        names = ("red", "green", "blue")
        for name, packet in zip(names, protocol.frame_packets(self.pixels)):
            if dry_run:
                print(f"{name:6} plane: {' '.join(f'{b:02x}' for b in packet)}")
            self.transport.write(packet, label=f"render:{name}", dry_run=dry_run)
        return self

    def off(self, dry_run: bool = False) -> "Lighting":
        self.pixels = [(0, 0, 0)] * self.count
        packet = protocol.off_packet()
        if dry_run:
            print(f"off:          {' '.join(f'{b:02x}' for b in packet)}")
        self.transport.write(packet, label="off", dry_run=dry_run)
        return self

    def __enter__(self) -> "Lighting":
        return self

    def __exit__(self, *exc: object) -> None:
        pass


def connect(session: Optional[str] = None) -> Tuple[Journal, Transport, Lighting]:
    """Open the device and return everything needed to drive it.

    The journal is returned rather than hidden so callers can close it; every
    packet a Lighting sends is logged through it either way.
    """
    journal = Journal(session=session)
    transport = Transport(journal).open()
    return journal, transport, Lighting(transport)
