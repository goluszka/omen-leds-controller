"""Command line control of the OMEN 27k external lighting.

    python3 -m omenled.cli fill '#ff0000'
    python3 -m omenled.cli set 0 green
    python3 -m omenled.cli gradient red blue
    python3 -m omenled.cli off

Sending anything puts the monitor into software-controlled mode, which locks
the OSD lighting menu. No release command is known yet; unplugging the USB
cable or resetting the monitor restores OSD control.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List, Optional

from . import protocol
from .device import Lighting, connect
from .transport import DeviceNotFound, TransportError


def colour(text: str):
    try:
        return protocol.parse_colour(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def cmd_off(lights: Lighting, args) -> int:
    lights.off(dry_run=args.dry_run)
    return 0


def cmd_fill(lights: Lighting, args) -> int:
    lights.fill(args.colour).render(dry_run=args.dry_run)
    return 0


def cmd_set(lights: Lighting, args) -> int:
    if not args.keep:
        lights.fill((0, 0, 0))
    for index in args.index:
        lights.set_led(index, args.colour)
    lights.render(dry_run=args.dry_run)
    return 0


def cmd_gradient(lights: Lighting, args) -> int:
    lights.gradient(args.start, args.end).render(dry_run=args.dry_run)
    return 0


def cmd_chase(lights: Lighting, args) -> int:
    """Walk one lit LED along the strip — the quickest way to see the mapping
    between LED index and physical position."""
    for index in range(lights.count):
        lights.fill((0, 0, 0)).set_led(index, args.colour).render(dry_run=args.dry_run)
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

    p = sub.add_parser("chase", help="walk one lit LED along the strip")
    p.add_argument("colour", type=colour, nargs="?", default=(0xFF, 0xFF, 0xFF))
    p.add_argument("--delay", type=float, default=0.4, help="seconds per LED")
    p.set_defaults(func=cmd_chase)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        journal, transport, lights = connect()
    except DeviceNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        return args.func(lights, args)
    except (TransportError, ValueError, IndexError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        transport.close()
        journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
