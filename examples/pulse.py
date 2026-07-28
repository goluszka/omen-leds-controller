#!/usr/bin/env python3
"""A breathing animation — the reference for driving omen-led from your own code.

    python3 examples/pulse.py '#00b4ff'
    python3 examples/pulse.py red --cycles 5

Shows the three things an animation needs to get right:

  * build the framebuffer, then render once per frame
  * render(verify=False), because the acknowledgement readback costs ~150 ms a
    plane and a frame is three planes
  * lower the inter-write interval, since the default 50 ms is a probing safety
    margin rather than a device limit

The lighting is left off at the end. That is this script being polite about a
demo; the library deliberately leaves whatever you rendered on screen.
"""

import argparse
import math
import os
import sys
import time

# Only needed when running from a checkout; unnecessary once omen-led is
# installed. Harmless either way.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omenled import LED_COUNT, open_lighting, parse_colour, scale
from omenled.transport import DeviceNotFound


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("colour", nargs="?", default="#00b4ff")
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()

    base = [parse_colour(args.colour)] * LED_COUNT
    frames = args.cycles * args.fps * 2

    try:
        # min_interval is lowered from the default probing margin; measure
        # before pushing it further, since the device's real limit is unknown.
        # min_interval is lowered from the default probing margin; measure
        # before pushing it further, since the device's real limit is unknown.
        with open_lighting(min_interval=0.002) as lights:
            for frame in range(frames):
                phase = frame / (args.fps * 2) * 2 * math.pi
                level = int((math.sin(phase) + 1) / 2 * 100)
                lights.set_all(scale(base, level)).render(verify=False)
                time.sleep(1 / args.fps)
            lights.off()
    except DeviceNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
