"""Guard rails for probing an undocumented microcontroller.

This is an ENE MCU behind a vendor-defined HID pipe. We do not know its opcode
map, which means a blind sweep can in principle reach in-system-programming or
flash paths and brick the lighting controller.

Nothing here can make that impossible — we cannot validate opcodes we have not
decoded. What it does is make the dangerous thing *deliberate*: patterns that
resemble known flash-unlock and bootloader-entry conventions are refused unless
the caller explicitly overrides, and every override is journalled.
"""

from __future__ import annotations

import atexit
import time
from typing import Callable, Iterable, List, Optional, Sequence, Set, Tuple


class UnsafePacket(Exception):
    """Raised when a payload matches a pattern we refuse to send by default."""


# Byte-0 values that, across a range of embedded controllers, commonly select a
# bootloader, ISP mode, or a flash operation rather than an application command.
# Heuristic, not authoritative.
DANGEROUS_OPCODES: Set[int] = {
    0x5A, 0xA5, 0x55, 0xAA,   # classic flash/ISP unlock magics
    0xF0, 0xFB, 0xFC, 0xFD, 0xFE, 0xFF,  # frequent "enter bootloader" / reset selectors
}

# Two-byte openings that appear as unlock handshakes. Order matters.
DANGEROUS_PREFIXES: Set[Tuple[int, int]] = {
    (0x5A, 0xA5), (0xA5, 0x5A),
    (0x55, 0xAA), (0xAA, 0x55),
}

# Deliberately *not* filtered: raw SPI-NOR opcodes (0x01 write-status,
# 0x02 program, 0x06 write-enable, 0x20/0xD8/0xC7 erase). Screening byte 0
# against them would refuse 0x01, which is this device's one known-working
# command — and there is no evidence the controller proxies raw flash frames
# at all. A rule that blocks known-good traffic on speculation trains you to
# pass the override reflexively, which costs more safety than it buys.


def explain(payload: Sequence[int]) -> Optional[str]:
    """Return why a payload is refused, or None if it passes.

    Kept separate from :func:`check` so callers can show the reason in a
    dry run without provoking an exception.
    """
    if len(payload) < 2:
        return "payload shorter than 2 bytes"

    b0, b1 = payload[0], payload[1]

    if (b0, b1) in DANGEROUS_PREFIXES:
        return f"opening bytes {b0:02x} {b1:02x} match a known flash-unlock magic"
    if b0 in DANGEROUS_OPCODES:
        return f"opcode {b0:02x} resembles a bootloader or ISP selector"
    return None


def check(payload: Sequence[int], override: bool = False) -> Optional[str]:
    """Refuse dangerous payloads unless explicitly overridden.

    Returns the reason when overridden, so the caller can journal it.
    """
    reason = explain(payload)
    if reason is None:
        return None
    if override:
        return reason
    raise UnsafePacket(
        f"refusing to send: {reason}. "
        "Pass override=True (probe.py: --i-know-what-im-doing) if this is intended."
    )


class RateLimiter:
    """Minimum spacing between writes.

    An undocumented MCU flooded with vendor reports is a plausible way to wedge
    it, and a wedged device costs a replug at best.
    """

    def __init__(self, min_interval: float = 0.05):
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


_cleanups: List[Callable[[], None]] = []
_registered = False


def on_exit(fn: Callable[[], None]) -> None:
    """Register a cleanup to run however the process ends.

    Used to hand control of the lighting back to the monitor's OSD. A crashed
    experiment must not strand the monitor in software-controlled mode.
    """
    global _registered
    _cleanups.append(fn)
    if not _registered:
        atexit.register(_run_cleanups)
        _registered = True


def _run_cleanups() -> None:
    while _cleanups:
        fn = _cleanups.pop()
        try:
            fn()
        except Exception:  # cleanup must never mask the original failure
            pass


def iter_safe(values: Iterable[int]) -> List[int]:
    """Filter an opcode sweep down to values that pass the byte-0 checks."""
    return [v for v in values if v not in DANGEROUS_OPCODES]
