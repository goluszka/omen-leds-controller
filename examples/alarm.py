#!/usr/bin/env python3
"""An alarm flash that puts the lighting back the way it was.

    python3 examples/alarm.py
    python3 examples/alarm.py --pulses 6 --colour '#ff2200'

Unlike `pulse.py`, which ends dark, this restores the last picture you asked
for when it finishes — the behaviour you want for a notification.

"The way it was" means *the last picture requested through the CLI*, not what is
physically lit. The monitor cannot be read back, so nothing else is knowable. If
nothing has been set, the lighting ends off.

The pattern is a pure generator, so it can be tested and previewed without the
monitor attached.
"""

import argparse
import os
import sys
import time
from typing import Iterator, List, Sequence, Tuple

# Only needed when running from a checkout; unnecessary once omen-led is
# installed. Harmless either way.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omenled import (
    LED_COUNT,
    open_lighting,
    parse_colour,
    remembered_frame,
    scale,
)
from omenled.transport import DeviceNotFound

ALARM_COLOUR = "#9E0000"

Pixel = Tuple[int, int, int]
Frame = Tuple[List[Pixel], float]


def envelope(position: float) -> float:
    """Intensity 0..1 across one pulse, given a position 0..1.

    Sharp attack and a decaying tail, rather than the sine used by `pulse.py`.
    A symmetric breath reads as calm; an alarm needs the light to snap on and
    fall away.
    """
    attack = 0.12
    if position < attack:
        return position / attack
    return (1 - (position - attack) / (1 - attack)) ** 2


def alarm_frames(colour: Pixel, count: int = LED_COUNT, pulses: int = 4,
                 steps: int = 18, pulse_seconds: float = 0.55) -> Iterator[Frame]:
    """Yield (pixels, hold_seconds) for the whole animation."""
    hold = pulse_seconds / steps
    base = [colour] * count

    # The envelope peaks at the end of the attack, which sampled steps
    # straddle rather than land on — so the flash would top out below full
    # brightness, dimmer for coarser `steps`. Normalising against the brightest
    # sample makes every pulse reach full whatever the step count.
    levels = [envelope(step / steps) for step in range(steps)]
    peak = max(levels) or 1.0

    for _ in range(pulses):
        for level in levels:
            yield scale(base, round(level / peak * 100)), hold


def play(lights, frames: Sequence[Frame]) -> None:
    for pixels, hold in frames:
        lights.set_all(pixels).render(verify=False)
        time.sleep(hold)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--colour", default=ALARM_COLOUR)
    parser.add_argument("--pulses", type=int, default=4)
    args = parser.parse_args()

    colour = parse_colour(args.colour)
    previous = remembered_frame(LED_COUNT)

    try:
        with open_lighting(min_interval=0.002) as lights:
            try:
                play(lights, alarm_frames(colour, pulses=args.pulses))
            finally:
                # Restore on the way out however we leave, including Ctrl-C:
                # an alarm that abandons the lighting mid-flash is worse than
                # one that never ran.
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
