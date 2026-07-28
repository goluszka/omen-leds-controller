"""Packet structure — pure functions, no I/O.

Everything here is either a decoded fact or an explicitly labelled hypothesis.
Keeping it free of device access is what lets the decode be unit-tested against
recorded journals with no monitor attached, and lets the eventual CLI reuse it
without change.

Nothing in this module is CONFIRMED yet. See docs/protocol.md for the evidence
ledger; a field graduates to CONFIRMED only when a prediction made *before*
transmission produces the predicted result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPORT_SIZE = 64

# --------------------------------------------------------------------------
# Working hypothesis
# --------------------------------------------------------------------------
# Derived from the one packet pair the previous attempt recorded that produced
# both a response and a visible LED change:
#
#   TX  01 00 00 d0 ff 00 00 0f 06 00 03 00...
#   RX  01 00 00 80 ec ac 00 00 ...
#
# and from the repeating tail seen in read-style probes:
#
#   RX  80 rr 00 d0 34 05 17 0f 06 00 03
#
# The shared `d0 .. 0f 06 00 03` tail in both directions suggests one record
# layout used for both set and report. Offsets below are guesses to be tested,
# not findings.

HYPOTHESIS: Dict[int, str] = {
    0: "opcode",
    1: "index/zone",
    2: "sub-index?",
    3: "record marker (0xd0 on set, 0x80 seen in one reply)",
    4: "R?",
    5: "G?",
    6: "B?",
    7: "brightness? (0x0f = 15)",
    8: "count? (0x06 = 6 zones?)",
    9: "effect?",
    10: "apply/mode? (0x03)",
}

# The bytes the prior attempt held constant. Retained so experiments can vary
# exactly one of them at a time rather than several at once.
BASELINE = (0x01, 0x00, 0x00, 0xD0, 0x00, 0x00, 0x00, 0x0F, 0x06, 0x00, 0x03)


def build(values: Sequence[int]) -> bytes:
    """Zero-pad leading bytes into a full report."""
    if len(values) > REPORT_SIZE:
        raise ValueError(f"{len(values)} bytes exceeds the {REPORT_SIZE}-byte report")
    if any(not 0 <= v <= 0xFF for v in values):
        raise ValueError("every value must fit in a byte")
    return bytes(values) + bytes(REPORT_SIZE - len(values))


def build_probe(
    r: int = 0,
    g: int = 0,
    b: int = 0,
    opcode: int = BASELINE[0],
    index: int = BASELINE[1],
    sub: int = BASELINE[2],
    marker: int = BASELINE[3],
    brightness: int = BASELINE[7],
    count: int = BASELINE[8],
    effect: int = BASELINE[9],
    mode: int = BASELINE[10],
) -> bytes:
    """Build a packet in the hypothesised layout.

    Named parameters exist so an experiment reads as "vary brightness, hold
    everything else" rather than as a hex literal whose changed byte has to be
    spotted by eye.
    """
    return build([opcode, index, sub, marker, r, g, b, brightness, count, effect, mode])


def with_byte(payload: bytes, offset: int, value: int) -> bytes:
    """Return a copy with a single byte changed — the unit of a clean sweep."""
    if not 0 <= offset < REPORT_SIZE:
        raise ValueError(f"offset {offset} outside the {REPORT_SIZE}-byte report")
    if not 0 <= value <= 0xFF:
        raise ValueError("value must fit in a byte")
    out = bytearray(payload)
    out[offset] = value
    return bytes(out)


# --------------------------------------------------------------------------
# Differential analysis — how labelled samples become a field map
# --------------------------------------------------------------------------


@dataclass
class ByteDiff:
    offset: int
    left: int
    right: int

    def __str__(self) -> str:
        note = HYPOTHESIS.get(self.offset)
        suffix = f"  ({note})" if note else ""
        return f"[{self.offset:02d}] {self.left:02x} -> {self.right:02x}{suffix}"


def diff(left: bytes, right: bytes) -> List[ByteDiff]:
    """Byte-level difference between two reports of equal length."""
    if len(left) != len(right):
        raise ValueError(f"length mismatch: {len(left)} vs {len(right)}")
    return [
        ByteDiff(i, a, b) for i, (a, b) in enumerate(zip(left, right)) if a != b
    ]


@dataclass
class FieldSurvey:
    """Which offsets move across a set of labelled samples, and which never do.

    Offsets that vary are candidate fields. Offsets that never vary are either
    constants of the protocol or fields our samples failed to exercise — the
    distinction matters, so both lists are kept.
    """

    varying: Dict[int, List[int]] = field(default_factory=dict)
    constant: Dict[int, int] = field(default_factory=dict)

    def candidates(self) -> List[Tuple[int, List[int]]]:
        return sorted(self.varying.items())

    def report(self) -> str:
        lines = ["varying offsets (candidate fields):"]
        if not self.varying:
            lines.append("  none — samples did not exercise any field")
        for offset, values in self.candidates():
            note = HYPOTHESIS.get(offset, "")
            seen = " ".join(f"{v:02x}" for v in values)
            lines.append(f"  [{offset:02d}] {seen}" + (f"   ({note})" if note else ""))
        lines.append(f"constant across all {len(self.constant)} remaining offsets")
        return "\n".join(lines)


def survey(samples: Iterable[bytes]) -> FieldSurvey:
    """Summarise which byte positions move across samples."""
    samples = list(samples)
    if not samples:
        raise ValueError("need at least one sample")
    length = len(samples[0])
    if any(len(s) != length for s in samples):
        raise ValueError("all samples must be the same length")

    result = FieldSurvey()
    for offset in range(length):
        values = [s[offset] for s in samples]
        distinct = sorted(set(values))
        if len(distinct) > 1:
            result.varying[offset] = distinct
        else:
            result.constant[offset] = distinct[0]
    return result


def tracks(samples: Sequence[Tuple[int, bytes]]) -> List[int]:
    """Offsets whose byte equals the expected value in every labelled sample.

    The core test of the OSD oracle: set a colour to a known component value,
    dump the device's reply, and ask which byte position carries that exact
    value every time. An offset that survives several distinct values is very
    unlikely to be coincidence.
    """
    if not samples:
        return []
    length = len(samples[0][1])
    return [
        offset
        for offset in range(length)
        if all(payload[offset] == expected for expected, payload in samples)
    ]


def annotate(payload: bytes, width: int = 16) -> str:
    """Hex dump with the current hypothesis attached to known offsets."""
    lines = []
    for start in range(0, len(payload), width):
        chunk = payload[start : start + width]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        lines.append(f"{start:02d}: {hexpart}")
    lines.append("")
    for offset, meaning in sorted(HYPOTHESIS.items()):
        if offset < len(payload):
            lines.append(f"  [{offset:02d}] = {payload[offset]:02x}   {meaning}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The decoded write path
# --------------------------------------------------------------------------
# Established 2026-07-29 by `probe.py experiment hunt`. The report is a 9-byte
# header followed by one intensity byte per LED, and the opcode selects which
# colour plane those intensities apply to. Writing the three planes in turn
# composes an arbitrary colour per LED.

PLANE_RED = 0x05
PLANE_GREEN = 0x06
PLANE_BLUE = 0x07
OP_OFF = 0x0D

PLANES = (PLANE_RED, PLANE_GREEN, PLANE_BLUE)

DATA_FIRST = 9
LED_COUNT = REPORT_SIZE - DATA_FIRST  # 55 per report

# Header bytes 1..8, held at these values throughout every successful write.
# Byte 1 is a candidate chunk index for LEDs beyond the first 55; the rest have
# no established meaning, and are preserved rather than zeroed because they are
# what the device has always been sent.
HEADER_TAIL = (0x00, 0x00, 0xD0, 0x00, 0x00, 0x00, 0x0F, 0x06)


def plane_packet(plane: int, intensities: Sequence[int], chunk: int = 0) -> bytes:
    """One report: `plane` opcode, then an intensity per LED.

    Shorter sequences are zero-padded, which turns off the LEDs they do not
    reach — the device holds no state we can merge into.
    """
    if plane not in PLANES:
        raise ValueError(f"plane must be one of {[hex(p) for p in PLANES]}, got {plane:#x}")
    if len(intensities) > LED_COUNT:
        raise ValueError(f"{len(intensities)} intensities exceeds {LED_COUNT} per report")
    if any(not 0 <= v <= 0xFF for v in intensities):
        raise ValueError("every intensity must fit in a byte")

    header = bytes([plane, chunk]) + bytes(HEADER_TAIL[1:])
    data = bytes(intensities) + bytes(LED_COUNT - len(intensities))
    return header + data


def off_packet() -> bytes:
    """Blank every LED."""
    return build([OP_OFF])


def frame_packets(pixels: Sequence[Tuple[int, int, int]], chunk: int = 0) -> List[bytes]:
    """The three plane reports that render one frame of RGB pixels."""
    if len(pixels) > LED_COUNT:
        raise ValueError(f"{len(pixels)} pixels exceeds {LED_COUNT} per report")
    return [
        plane_packet(plane, [px[channel] for px in pixels], chunk=chunk)
        for channel, plane in enumerate(PLANES)
    ]


def parse_colour(text: str) -> Tuple[int, int, int]:
    """`#rrggbb`, `rrggbb`, `#rgb`, or a handful of names."""
    named = {
        "red": (0xFF, 0x00, 0x00), "green": (0x00, 0xFF, 0x00),
        "blue": (0x00, 0x00, 0xFF), "white": (0xFF, 0xFF, 0xFF),
        "black": (0, 0, 0), "off": (0, 0, 0),
        "yellow": (0xFF, 0xFF, 0x00), "cyan": (0x00, 0xFF, 0xFF),
        "magenta": (0xFF, 0x00, 0xFF), "orange": (0xFF, 0x60, 0x00),
        "purple": (0x80, 0x00, 0xFF), "pink": (0xFF, 0x40, 0x80),
    }
    key = text.strip().lower()
    if key in named:
        return named[key]

    def unreadable() -> ValueError:
        return ValueError(
            f"cannot read {text!r} as a colour. Use #rrggbb, #rgb, "
            f"or one of: {', '.join(sorted(named))}"
        )

    digits = key[1:] if key.startswith("#") else key
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    if len(digits) != 6:
        raise unreadable()
    try:
        value = int(digits, 16)
    except ValueError:
        # A six-letter word like "banana" reaches here. Naming it "not hex"
        # would be true and useless; the caller needs the accepted forms.
        raise unreadable()
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def gradient(start: Tuple[int, int, int], end: Tuple[int, int, int],
             count: int) -> List[Tuple[int, int, int]]:
    """Linear interpolation, used by the CLI's gradient command."""
    if count <= 0:
        return []
    if count == 1:
        return [start]
    return [
        tuple(
            round(a + (b - a) * i / (count - 1))
            for a, b in zip(start, end)
        )
        for i in range(count)
    ]


def nonzero_offsets(payload: bytes) -> List[int]:
    return [i for i, b in enumerate(payload) if b]


def is_empty(payload: Optional[bytes]) -> bool:
    return payload is None or not any(payload)
