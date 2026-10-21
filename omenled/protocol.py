"""Packet structure — pure functions, no I/O.

Two layers live here. The decoded write path (`plane_packet`, `frame_packets`,
`off_packet`, `parse_reply`) is CONFIRMED; it is what the library uses. The
probe helpers (`build_probe`, `diff`, `survey`, `annotate`) serve the
reverse-engineering workbench in `tools/probe.py`.

Keeping it free of device access is what lets the decode be unit-tested against
recorded journals with no monitor attached. See docs/protocol.md for the
evidence ledger; a field graduates to CONFIRMED only when a prediction made
*before* transmission produces the predicted result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

REPORT_SIZE = 64

# --------------------------------------------------------------------------
# Field labels for probe dumps
# --------------------------------------------------------------------------
# Attached to offsets by `annotate`, `ByteDiff` and `FieldSurvey`. Only the
# header is labelled: from byte 4 on, a request carries LED intensities (see
# DATA_FIRST). The prior attempt's guesses for 4..10 — R, G, B, brightness,
# count, effect, mode — are superseded by the CONFIRMED byte-4 data offset in
# docs/protocol.md: they were LEDs 0..6 pinned to constants, and labelling them
# so would mislead every dump.

FIELD_LABELS: Dict[int, str] = {
    0: "opcode: 05/06/07 colour plane, 0d blank; echoed in replies",
    1: "header 0x00 (hardware brightness untested)",
    2: "header 0x00 (hardware brightness untested)",
    3: "request tag 0xd0 / reply status: 0x80|tag ok, 0xf0 rejected",
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
        note = FIELD_LABELS.get(self.offset)
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
            note = FIELD_LABELS.get(offset, "")
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


def annotate(payload: bytes, width: int = 16) -> str:
    """Hex dump with the decoded header fields labelled."""
    lines = []
    for start in range(0, len(payload), width):
        chunk = payload[start : start + width]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        lines.append(f"{start:02d}: {hexpart}")
    lines.append("")
    for offset, meaning in sorted(FIELD_LABELS.items()):
        if offset < len(payload):
            lines.append(f"  [{offset:02d}] = {payload[offset]:02x}   {meaning}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# The decoded write path
# --------------------------------------------------------------------------
# Established 2026-07-29 by `probe.py experiment hunt`, layout corrected by
# `experiment headerleds`. The report is a 4-byte header followed by one
# intensity byte per LED, and the opcode selects which colour plane those
# intensities apply to. Writing the three planes in turn composes an arbitrary
# colour per LED.

PLANE_RED = 0x05
PLANE_GREEN = 0x06
PLANE_BLUE = 0x07
OP_OFF = 0x0D

PLANES = (PLANE_RED, PLANE_GREEN, PLANE_BLUE)

# The LED data starts at byte 4, established 2026-07-29 by setting each of
# bytes 1..7 alone to full brightness with everything else zeroed: bytes 1, 2
# and 3 lit nothing, bytes 4, 5, 6 and 7 each lit one LED, stepping along the
# strip. A write of 0xff across bytes 1..63 then lit the whole diamond with no
# gaps, which is the confirmation that 60 LEDs is the complete strip.
#
# The offset was wrong three times before this — assumed 9, then 8, then tested
# properly. Each earlier reading treated bytes as header because they had been
# pinned to constants inherited from an old probe packet, and a byte held at a
# constant is indistinguishable from a byte with no effect. Those constants
# caused the visible faults: bytes 4..6 at 0x00 left LEDs 0..2 permanently dark,
# and byte 7 at 0x0f left LED 3 stuck at a dim grey that never took a colour.
DATA_FIRST = 4
LED_COUNT = REPORT_SIZE - DATA_FIRST  # 60

# Header bytes 1..3. Byte 3's low nibble is echoed in the reply status, so it
# doubles as a request tag; 0xd0 is what the device has always been sent.
HEADER_TAIL = (0x00, 0x00, 0xD0)


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


# Reply byte 3 is a status code. Established 2026-07-29: every accepted write
# answered 0x80, and the only two rejected ones answered 0xf0 — both were blue
# plane writes sent directly after a red plane write, skipping green.
STATUS_OK = 0x80
STATUS_ERROR = 0xF0
STATUS_OFFSET = 3


@dataclass
class Reply:
    """A parsed device acknowledgement.

    The device exposes no readable state, so this status byte is the only
    feedback there is — it is the difference between a write that landed and
    one the device threw away.
    """

    opcode: int
    status: int

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def __str__(self) -> str:
        name = {STATUS_OK: "ok", STATUS_ERROR: "rejected"}.get(self.status, "unknown")
        return f"opcode {self.opcode:02x} -> {name} ({self.status:02x})"


def parse_reply(payload: Optional[bytes]) -> Optional[Reply]:
    """None for a timeout or a reply too short to carry a status."""
    if payload is None or len(payload) <= STATUS_OFFSET:
        return None
    return Reply(opcode=payload[0], status=payload[STATUS_OFFSET])


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


def scale(pixels: Sequence[Tuple[int, int, int]], percent: int
          ) -> List[Tuple[int, int, int]]:
    """Scale pixel intensities to a percentage of full brightness.

    Linear, because the intensity byte appears to be linear and nothing we can
    measure would justify a gamma curve. Rounding is half-up per channel, so a
    colour never drifts hue as it dims.
    """
    if not 0 <= percent <= 100:
        raise ValueError(f"brightness must be 0..100, got {percent}")
    return [tuple(round(c * percent / 100) for c in px) for px in pixels]


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
