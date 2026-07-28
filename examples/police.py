#!/usr/bin/env python3
"""Alternating red and blue strobe, imitating police lights.

    python3 examples/police.py
    python3 examples/police.py --cycles 20 --flashes 2

One half of the strip strobes red, then the other half strobes blue, in bursts.
Uneven on/off timing is what makes it read as a siren rather than a blinking
light — a real strobe is a short bright pop against a longer gap.

Restores the last picture you asked for when it finishes, including on Ctrl-C.
If nothing has been set, the lighting ends off.

**The halves are split by LED index, not by physical position.** The index →
position mapping is not yet decoded — run `omen-led chase` to see it — so which
side lights first depends on where the strip physically starts. If the colours
land somewhere odd, that mapping is why, and `--swap` flips them.
"""

import argparse
import os
import sys
import time
from typing import Iterator, List, Sequence, Tuple

# Only needed when running from a checkout; unnecessary once omen-led is
# installed. Harmless either way.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omenled import LED_COUNT, open_lighting, remembered_frame
from omenled.transport import DeviceNotFound

RED = (255, 0, 0)
BLUE = (0, 0, 255)

FLASH_ON = 0.055
FLASH_OFF = 0.045
SIDE_GAP = 0.12

Pixel = Tuple[int, int, int]
Frame = Tuple[List[Pixel], float]


def half_lit(colour: Pixel, first_half: bool, count: int = LED_COUNT) -> List[Pixel]:
    """A frame with one half of the strip lit and the other dark."""
    half = count // 2
    span = range(0, half) if first_half else range(half, count)
    pixels = [(0, 0, 0)] * count
    for index in span:
        pixels[index] = colour
    return pixels


def police_frames(count: int = LED_COUNT, cycles: int = 8, flashes: int = 3,
                  swap: bool = False) -> Iterator[Frame]:
    """Yield (pixels, hold_seconds) for the whole animation."""
    dark = [(0, 0, 0)] * count
    sides = [(RED, not swap), (BLUE, swap)]
    for _ in range(cycles):
        for colour, first_half in sides:
            for _ in range(flashes):
                yield half_lit(colour, first_half, count), FLASH_ON
                yield list(dark), FLASH_OFF
            yield list(dark), SIDE_GAP


def play(lights, frames: Sequence[Frame]) -> None:
    for pixels, hold in frames:
        lights.set_all(pixels).render(verify=False)
        time.sleep(hold)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=8)
    parser.add_argument("--flashes", type=int, default=3,
                        help="flashes per side before switching colour")
    parser.add_argument("--swap", action="store_true",
                        help="put blue on the half that currently strobes red")
    args = parser.parse_args()

    previous = remembered_frame(LED_COUNT)

    try:
        with open_lighting(min_interval=0.002) as lights:
            try:
                play(lights, police_frames(cycles=args.cycles,
                                           flashes=args.flashes, swap=args.swap))
            finally:
                if previous is None:
                    lights.off()
                else:
                    lights.set_all(previous).render(verify=False)
    except DeviceNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

    if previous is None:
        print("nothing was previously set, so the lighting is now off")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
