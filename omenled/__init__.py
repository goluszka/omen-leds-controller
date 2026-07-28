"""Software control of the HP OMEN 27k monitor's external lighting, on macOS.

60 individually addressable LEDs, full RGB each, over USB HID.

    from omenled import open_lighting, parse_colour

    with open_lighting() as lights:
        lights.fill(parse_colour("#00ff00")).render()
        lights.set_led(3, (255, 0, 0)).render()
        lights.off()

The protocol had no public documentation; `docs/protocol.md` records how it was
decoded and how confident each claim is.
"""

from .device import Lighting, RenderRejected, connect, open_lighting
from .protocol import (
    DATA_FIRST,
    LED_COUNT,
    OP_OFF,
    PLANE_BLUE,
    PLANE_GREEN,
    PLANE_RED,
    PLANES,
    frame_packets,
    gradient,
    off_packet,
    parse_colour,
    parse_reply,
    plane_packet,
    scale,
)
from .state import remembered_frame
from .transport import DeviceNotFound, Transport, TransportError, describe

__version__ = "0.1.0"

__all__ = [
    # high level
    "open_lighting",
    "Lighting",
    "connect",
    # colour helpers
    "parse_colour",
    "gradient",
    "scale",
    "remembered_frame",
    # device facts
    "LED_COUNT",
    "DATA_FIRST",
    "PLANES",
    "PLANE_RED",
    "PLANE_GREEN",
    "PLANE_BLUE",
    "OP_OFF",
    "describe",
    # low level, for building packets yourself
    "plane_packet",
    "frame_packets",
    "off_packet",
    "parse_reply",
    "Transport",
    # errors
    "DeviceNotFound",
    "TransportError",
    "RenderRejected",
    "__version__",
]
