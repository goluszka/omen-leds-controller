"""Command line control of the OMEN 27k external lighting.

    python3 -m omenled.cli fill '#ff0000'
    python3 -m omenled.cli set 0 green
    python3 -m omenled.cli gradient red blue
    python3 -m omenled.cli brightness 40
    python3 -m omenled.cli police
    python3 -m omenled.cli off

Brightness is applied in software by scaling the intensities. The last frame is
remembered at full intensity, so `brightness` re-dims the same picture instead
of compounding.

Sending anything puts the monitor into software-controlled mode, which locks
the OSD lighting menu. No release command is known yet; unplugging the USB
cable or resetting the monitor restores OSD control.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional

from . import animations, protocol, state
from .device import Lighting, RenderRejected, open_lighting
from .transport import DeviceNotFound, TransportError


def percent(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number 0-100")
    if not 0 <= value <= 100:
        raise argparse.ArgumentTypeError(f"brightness must be 0-100, got {value}")
    return value


def colour(text: str):
    try:
        return protocol.parse_colour(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def _show(lights: Lighting, args) -> int:
    """Apply brightness to the framebuffer, render, and remember the original.

    The frame is saved before scaling so `brightness` later re-dims the picture
    the user asked for rather than compounding on an already-dimmed one.
    """
    base = list(lights.pixels)
    percent = args.brightness if args.brightness is not None else _remembered_brightness()
    lights.set_all(protocol.scale(base, percent)).render(dry_run=args.dry_run)
    if not args.dry_run:
        state.save(base, percent)
    return 0


def _remembered_brightness() -> int:
    remembered = state.load()
    return remembered[1] if remembered else 100


def cmd_off(lights: Lighting, args) -> int:
    lights.off(dry_run=args.dry_run)
    if not args.dry_run:
        state.save(lights.pixels, _remembered_brightness())
    return 0


def cmd_fill(lights: Lighting, args) -> int:
    lights.fill(args.colour)
    return _show(lights, args)


def cmd_set(lights: Lighting, args) -> int:
    if args.keep:
        remembered = state.load()
        if remembered:
            lights.set_all(remembered[0][:lights.count])
    for index in args.index:
        lights.set_led(index, args.colour)
    return _show(lights, args)


def cmd_gradient(lights: Lighting, args) -> int:
    lights.gradient(args.start, args.end)
    return _show(lights, args)


def cmd_brightness(lights: Lighting, args) -> int:
    """Re-render the remembered frame at a new brightness."""
    remembered = state.load()
    if remembered is None:
        print("nothing rendered yet — run fill, set or gradient first",
              file=sys.stderr)
        return 1
    base = remembered[0][:lights.count]
    lights.set_all(protocol.scale(base, args.percent)).render(dry_run=args.dry_run)
    if not args.dry_run:
        state.save(base, args.percent)
    return 0


def cmd_police(lights: Lighting, args) -> int:
    """Alternating red and blue bursts, restoring the display afterwards.

    Transient, so it deliberately does not overwrite the saved frame: the
    picture you set is what `brightness` should still re-dim once the siren
    stops.
    """
    previous = state.remembered_frame(lights.count)
    frames = list(animations.police_frames(
        lights.count, cycles=args.cycles, flashes=args.flashes, swap=args.swap))

    if args.brightness is not None:
        frames = [(protocol.scale(pixels, args.brightness), hold)
                  for pixels, hold in frames]

    if args.dry_run:
        seconds = sum(hold for _, hold in frames)
        print(f"{len(frames)} frames, {seconds:.1f}s, "
              f"{args.cycles} cycles of {args.flashes} flashes per colour")
        print(f"first frame: {' '.join(f'{c:02x}' for c in frames[0][0][0])} "
              f"across all {lights.count} LEDs")
        return 0

    try:
        animations.play(lights, frames)
    finally:
        # Restore however we leave, including Ctrl-C: a siren that abandons the
        # lighting mid-flash is worse than one that never ran.
        if previous is None:
            lights.off()
        else:
            lights.set_all(previous).render(verify=False)

    if previous is None:
        print("nothing was previously set, so the lighting is now off")
    return 0


def cmd_chase(lights: Lighting, args) -> int:
    """Walk one lit LED along the strip — the quickest way to see the mapping
    between LED index and physical position."""
    for index in range(lights.count):
        lights.fill((0, 0, 0)).set_led(index, args.colour).render(
            dry_run=args.dry_run, verify=False)
        print(f"LED {index}", flush=True)
        if not args.dry_run:
            time.sleep(args.delay)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omenled", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be sent without sending it")
    parser.add_argument("--brightness", type=percent, default=None, metavar="0-100",
                        help="scale intensities; defaults to the last value used")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("off", help="blank every LED")
    p.set_defaults(func=cmd_off)

    p = sub.add_parser("fill", help="set every LED to one colour")
    p.add_argument("colour", type=colour, help="#rrggbb, #rgb, or a name")
    p.set_defaults(func=cmd_fill)

    p = sub.add_parser("set", help="light specific LEDs")
    p.add_argument("index", type=int, nargs="+", help=f"0..{protocol.LED_COUNT - 1}")
    p.add_argument("colour", type=colour)
    p.add_argument("--keep", action="store_true",
                   help="leave other LEDs as they were rather than blanking them")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("gradient", help="fade from one colour to another")
    p.add_argument("start", type=colour)
    p.add_argument("end", type=colour)
    p.set_defaults(func=cmd_gradient)

    p = sub.add_parser("brightness", help="re-dim the current picture")
    p.add_argument("percent", type=percent, metavar="0-100")
    p.set_defaults(func=cmd_brightness)

    p = sub.add_parser("police", help="alternating red/blue strobe, then restore")
    p.add_argument("--cycles", type=int, default=8)
    p.add_argument("--flashes", type=int, default=3,
                   help="flashes per colour before switching")
    p.add_argument("--swap", action="store_true", help="start on blue")
    p.set_defaults(func=cmd_police)

    p = sub.add_parser("chase", help="walk one lit LED along the strip")
    p.add_argument("colour", type=colour, nargs="?", default=(0xFF, 0xFF, 0xFF))
    p.add_argument("--delay", type=float, default=0.4, help="seconds per LED")
    p.set_defaults(func=cmd_chase)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with open_lighting() as lights:
            return args.func(lights, args)
    except DeviceNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (TransportError, RenderRejected, ValueError, IndexError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
