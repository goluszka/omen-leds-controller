"""High-level lighting control.

Holds a local framebuffer of one RGB pixel per LED and renders it as three
plane reports. The device keeps no state we can read back — see
`docs/protocol.md` — so the framebuffer here is the only picture of what is
currently displayed, and a render always writes every LED.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, List, Optional, Sequence, Tuple, Union

from . import protocol
from .journal import Journal, NullJournal
from .transport import Transport

Pixel = Tuple[int, int, int]


class RenderRejected(Exception):
    """The device refused a plane write, so the LEDs show a partial frame."""


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

    def render(self, dry_run: bool = False, verify: bool = True) -> "Lighting":
        """Write the framebuffer as three plane reports, in red-green-blue order.

        The order is load-bearing, not stylistic. The device tracks frame state
        across the three writes and rejects a plane sent out of sequence with
        status 0xf0, leaving the LEDs showing a partial frame. `verify` reads
        each acknowledgement back so a rejection is reported rather than
        silently displayed.
        """
        names = ("red", "green", "blue")
        for name, packet in zip(names, protocol.frame_packets(self.pixels)):
            if dry_run:
                print(f"{name:6} plane: {' '.join(f'{b:02x}' for b in packet)}")
            self.transport.write(packet, label=f"render:{name}", dry_run=dry_run)
            if verify and not dry_run:
                reply = protocol.parse_reply(
                    self.transport.read(timeout_ms=150, label=f"render:{name}"))
                if reply is not None and not reply.ok:
                    raise RenderRejected(
                        f"the {name} plane was rejected ({reply}). Planes must be "
                        "written red, green, blue in order; send `off` to reset "
                        "the device's frame state and try again."
                    )
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


@contextmanager
def open_lighting(journal: Union[bool, Journal, NullJournal] = False,
                  session: Optional[str] = None) -> Iterator[Lighting]:
    """Open the monitor's lighting for the duration of a `with` block.

        with open_lighting() as lights:
            lights.fill((0, 255, 0)).render()

    Journalling is off by default. Logging every packet to disk is right for
    reverse engineering and wrong for a library someone depends on, where
    unexpected file writes are a defect. Pass `journal=True` to record a session
    under the user data directory, or supply your own Journal.

    The lighting is left as rendered when the block exits — a lighting tool that
    blanked the panel on exit would be useless. Call `off()` explicitly.
    """
    log = Journal(session=session) if journal is True else (
        journal if journal else NullJournal())
    transport = Transport(log)
    try:
        transport.open()
        yield Lighting(transport)
    finally:
        transport.close()
        log.close()


def connect(session: Optional[str] = None) -> Tuple[Journal, Transport, Lighting]:
    """Open the device and return its parts, for callers managing their own
    lifetimes. Prefer `open_lighting`."""
    journal = Journal(session=session)
    transport = Transport(journal).open()
    return journal, transport, Lighting(transport)
