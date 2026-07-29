"""Reusable animation patterns.

Patterns are generators yielding `(pixels, hold_seconds)` and touch no hardware,
so their shape can be asserted in tests. That matters more here than usual: the
device cannot be read back, so an animation checked only by eye is checked by
nobody.

Playback is separate from pattern, which is what lets the same generator drive
the CLI, a dry run, or a test.
"""

from __future__ import annotations

import time
from typing import Iterator, List, Sequence, Tuple

from .protocol import LED_COUNT

Pixel = Tuple[int, int, int]
Frame = Tuple[List[Pixel], float]

RED = (255, 0, 0)
BLUE = (0, 0, 255)

# Uneven on/off timing is what makes a strobe read as a siren rather than a
# blinking light: a short bright pop against a longer gap.
FLASH_ON = 0.055
FLASH_OFF = 0.045
BURST_GAP = 0.12


def police_frames(count: int = LED_COUNT, cycles: int = 8, flashes: int = 3,
                  swap: bool = False) -> Iterator[Frame]:
    """Alternating red and blue bursts across the whole diamond.

    Every LED takes the colour, rather than each colour claiming half the
    strip. Splitting the strip looked plausible on paper but reads as two
    separate lights on the panel; a real light bar alternates the whole fixture
    between colours, and so does this.

    `swap` starts on blue.
    """
    dark = [(0, 0, 0)] * count
    order = (BLUE, RED) if swap else (RED, BLUE)
    for _ in range(cycles):
        for colour in order:
            lit = [colour] * count
            for _ in range(flashes):
                yield list(lit), FLASH_ON
                yield list(dark), FLASH_OFF
            yield list(dark), BURST_GAP


def play(lights, frames: Sequence[Frame], verify: bool = False) -> None:
    """Render each frame and hold it.

    `verify` defaults off: reading the acknowledgement back costs about 150 ms
    per plane, and a frame is three planes, which would dominate the timing the
    pattern is trying to express.
    """
    for pixels, hold in frames:
        lights.set_all(pixels).render(verify=verify)
        time.sleep(hold)
