#!/usr/bin/env python3
"""Reverse-engineering workbench for the OMEN 27k lighting controller.

Every subcommand journals what it did to data/sessions/, including anything you
type when it asks what you saw. That log is the point: it is what lets a decode
survive an interrupted session.

Ordering matters. Run in this order:

    info        device facts, no writes at all
    baseline    what is readable before we provoke anything
    query       send one packet, show the reply annotated
    sweep       vary exactly one byte, prompting for observations
    diff        compare two labelled snapshots

`info` and `baseline` never transmit. Everything else does, and anything that
transmits can lock the monitor's OSD lighting controls.
"""

from __future__ import annotations

import argparse
import os
import re
import readline  # noqa: F401  — gives input() line editing; an arrow key
                 # once landed in an answer as "^[[A" and lost an observation
import sys
import time
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from omenled import protocol, safety, transport
from omenled.journal import Journal, hexs, read_session, unhex


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def parse_byte(text: str) -> int:
    value = int(text, 0)
    if not 0 <= value <= 0xFF:
        raise argparse.ArgumentTypeError(f"{text} does not fit in a byte")
    return value


def parse_range(text: str) -> List[int]:
    """`0x00:0x10` or `0,1,5` -> list of byte values."""
    if ":" in text:
        start, _, end = text.partition(":")
        return list(range(int(start, 0), int(end, 0) + 1))
    return [int(part, 0) for part in text.split(",")]


def ask(prompt: str) -> str:
    """Capture a human observation. Blank means 'nothing happened'."""
    try:
        answer = input(f"  {prompt} ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return "(interrupted)"
    return answer or "no visible change"


def show(payload: Optional[bytes], title: str = "RX") -> None:
    if payload is None:
        print(f"  {title}: (timeout — no reply)")
        return
    if not any(payload):
        print(f"  {title}: all zero")
        return
    print(f"  {title}: {hexs(payload[:16])}")


# --------------------------------------------------------------------------
# subcommands that never transmit
# --------------------------------------------------------------------------


def cmd_info(args: argparse.Namespace) -> int:
    devices = transport.describe()
    if not devices:
        print(f"No device {transport.VID:04x}:{transport.PID:04x} found.")
        print("Check the monitor's USB upstream cable is connected to this Mac.")
        return 1

    for dev in devices:
        print("OMEN lighting controller")
        for key, value in dev.items():
            print(f"  {key:18} {value}")

    print("\nReport descriptor (from IOHIDInterface):")
    print("  Input   64 bytes   usage 0xEC")
    print("  Output  64 bytes   usage 0x11")
    print("  Feature  6 bytes   usage 0x04")
    print("  No report IDs — there is exactly one unnumbered output report.")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    """Step 0: what can be read without provoking the device?

    Deliberately transmits nothing. If the device volunteers input reports or
    answers a feature read while idle, that is a state channel we can use
    without ever triggering the OSD lock.
    """
    with Journal() as journal:
        print(f"session: {journal.path}")
        with transport.Transport(journal) as link:
            journal.note("baseline: read-only, no output reports sent")

            print("\nfeature report (6 bytes declared):")
            feature = link.get_feature(0, label="baseline:feature")
            show(feature, "feature")

            print("\nunsolicited input reports (2s):")
            got = False
            for _ in range(20):
                payload = link.read(timeout_ms=100, label="baseline:idle")
                if payload:
                    show(payload, "in")
                    got = True
            if not got:
                print("  none — the device does not report state unprompted")

            print(
                "\nConclusion: a state read almost certainly requires a write,"
                "\nwhich means the OSD lock is unavoidable and finding the"
                "\nrelease command is the next step."
            )
    return 0


# --------------------------------------------------------------------------
# subcommands that transmit
# --------------------------------------------------------------------------


def cmd_query(args: argparse.Namespace) -> int:
    """Send one packet and show the reply, annotated against the hypothesis."""
    payload = (
        protocol.build(unhex(args.raw))
        if args.raw
        else protocol.build_probe(
            r=args.r, g=args.g, b=args.b,
            opcode=args.opcode, index=args.index, marker=args.marker,
        )
    )

    print(f"TX: {hexs(payload[:16])}")
    reason = safety.explain(payload)
    if reason:
        print(f"  ! {reason}")
        if not args.i_know_what_im_doing:
            print("  refused. Re-run with --i-know-what-im-doing to send anyway.")
            return 2
    if args.dry_run:
        print("  dry run — nothing sent")
        return 0

    with Journal() as journal:
        print(f"session: {journal.path}")
        with transport.Transport(journal) as link:
            reply = link.exchange(
                payload,
                timeout_ms=args.timeout,
                label=args.label,
                override_safety=args.i_know_what_im_doing,
            )
            show(reply)
            if reply:
                print()
                print(protocol.annotate(reply[:16]))
            if args.observe:
                journal.observation(ask("What did the LEDs do?"), label=args.label)
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Vary exactly one byte, holding everything else fixed.

    One variable at a time is the whole discipline here. The previous attempt
    at this protocol varied several bytes per step, which is why its results
    could not be attributed to any particular field.
    """
    base = protocol.build(unhex(args.base)) if args.base else protocol.build_probe()
    values = parse_range(args.values)
    values = values if args.i_know_what_im_doing else safety.iter_safe(values)

    if not values:
        print("Every value in that range is on the refusal list.")
        return 2

    guess = protocol.HYPOTHESIS.get(args.byte, "unknown field")
    print(f"base: {hexs(base[:16])}")
    print(f"varying byte {args.byte} over {len(values)} values  ({guess})")
    if args.observe:
        print("After each step, describe what you see and press Enter.\n")

    with Journal() as journal:
        print(f"session: {journal.path}\n")
        with transport.Transport(journal) as link:
            journal.note(
                f"sweep byte {args.byte} over {hexs(bytes(values))}; "
                f"base {hexs(base)}"
            )
            for value in values:
                label = f"sweep:b{args.byte}={value:02x}"
                payload = protocol.with_byte(base, args.byte, value)
                print(f"byte[{args.byte}] = {value:02x}")
                reply = link.exchange(
                    payload, timeout_ms=args.timeout, label=label,
                    override_safety=args.i_know_what_im_doing,
                    dry_run=args.dry_run,
                )
                show(reply)
                if args.observe and not args.dry_run:
                    journal.observation(ask("LEDs?"), label=label)
    return 0


def _yes(answer: str) -> bool:
    return answer.lower().startswith(("y", "t"))  # y/yes, t/tak


# The packet the previous attempt confirmed both provokes the OSD lock and
# draws a reply. Used here to put the monitor into the locked state on purpose,
# so a release candidate has something to release.
LOCK_TRIGGER = protocol.build_probe(r=0xFF)


def cmd_release(args: argparse.Namespace) -> int:
    """Hunt for the command that hands lighting control back to the OSD.

    Structured so a hit is proven rather than assumed: the lock is established
    deliberately first, and any candidate that appears to work must clear a
    freshly re-established lock a second time before it is recorded as found.
    A single lucky observation is not evidence.
    """
    base = protocol.build_probe()
    candidates: List[tuple] = [
        ("all zero", protocol.build([0x00])),
        ("opcode 00, keep tail", protocol.with_byte(base, 0, 0x00)),
        ("marker 00", protocol.with_byte(base, 3, 0x00)),
        ("mode 00", protocol.with_byte(base, 10, 0x00)),
        ("mode 01", protocol.with_byte(base, 10, 0x01)),
        ("mode 02", protocol.with_byte(base, 10, 0x02)),
        ("effect ff", protocol.with_byte(base, 9, 0xFF)),
        ("opcode 02", protocol.with_byte(base, 0, 0x02)),
        ("opcode 0d", protocol.with_byte(base, 0, 0x0D)),
    ]

    with Journal() as journal:
        print(f"session: {journal.path}\n")
        with transport.Transport(journal) as link:

            def establish_lock() -> bool:
                """Make sure the monitor is in software-controlled mode."""
                if _yes(ask("Is the OSD lighting menu locked right now? (y/n)")):
                    return True
                print("  locking it deliberately so there is something to release...")
                link.exchange(LOCK_TRIGGER, timeout_ms=args.timeout, label="lock:trigger")
                locked = _yes(ask("Locked now? (y/n)"))
                journal.observation(
                    f"lock trigger {'worked' if locked else 'did NOT lock the OSD'}",
                    label="lock:trigger",
                )
                if not locked:
                    print("\n  That packet does not lock the OSD after all — worth knowing,")
                    print("  but it means we cannot test release commands yet.")
                return locked

            if not establish_lock():
                return 1

            print("\nNow trying handover candidates. After each, check the monitor:")
            print("has the 'controlled by OMEN Gaming Hub' notice cleared, and does")
            print("the OSD lighting menu respond again?\n")

            for name, payload in candidates:
                label = f"release:{name}"
                print(f"{name}: {hexs(payload[:12])}")
                show(link.exchange(payload, timeout_ms=args.timeout, label=label))
                answer = ask("OSD unlocked? (y / n / describe)")
                journal.observation(answer, label=label)
                if not _yes(answer):
                    continue

                print(f"\n  {name} looks like a hit. Confirming it repeats.")
                journal.note(f"candidate release: {name}", label=label)
                link.exchange(LOCK_TRIGGER, timeout_ms=args.timeout, label="lock:reconfirm")
                if not _yes(ask("Locked again? (y/n)")):
                    print("  Could not re-lock, so the result is unproven. Continuing.")
                    journal.observation("re-lock failed; hit unproven", label=label)
                    continue
                link.exchange(payload, timeout_ms=args.timeout, label=f"{label}:confirm")
                if _yes(ask("Unlocked again? (y/n)")):
                    print(f"\nCONFIRMED release command: {name}")
                    print(f"  {hexs(payload[:12])}")
                    journal.note(f"RELEASE COMMAND CONFIRMED: {name} = {hexs(payload)}")
                    return 0
                print("  Did not repeat — coincidence, not a release. Continuing.")
                journal.observation("did not repeat; rejected", label=label)

    print("\nNo candidate worked. USB replug stays the reset for now.")
    print("Recorded in the journal so the next pass starts from here.")
    return 1


# --------------------------------------------------------------------------
# curated experiments
# --------------------------------------------------------------------------

# Patterns the answers get classified by. Polish included because that is what
# gets typed under time pressure.
#
# These are anchored at word boundaries rather than matched as substrings, and
# that is not fussiness: "flickered" contains "red", so a substring match reads
# an ambiguous answer as a confirmed colour and manufactures evidence. A decode
# that misreads its own observations is worse than one with no observations.
COLOUR_WORDS = {
    "red": r"\b(red\w*|czerwon\w*)",
    "green": r"\b(green\w*|zielon\w*)",
    "blue": r"\b(blue\w*|niebiesk\w*)",
    "white": r"\b(white\w*|bia[lł]\w*)",
    "off": r"\b(off|out|dark\w*|black\w*|zgas\w*|zgaś\w*|wy[lł][aą]cz\w*|ciemn\w*)\b",
    "nothing": r"\b(nothing|no change|no visible|nic|bez zmian)\b",
}


# Phrases meaning "nothing changed". Kept in one place because comparing answer
# strings ad hoc has produced three separate false-positive verdicts: matching
# "red" inside "flickered", "whole" inside "whole diamond except…", and treating
# the literal answer "the same" as movement because it differed from the
# reference *string*.
SAME_RE = re.compile(
    r"\b(the same|same|identical|unchanged|no change|no visible change|"
    r"nothing|none|bez zmian|tak samo|to samo|bez r[oó][zż]nicy)\b"
)

CHANGE_RE = re.compile(
    # Comparatives only. A bare "dark" appears in answers meaning "still dark",
    # so only "darker" is taken as evidence of a change.
    r"\b(moved|shifted|different|differs|changed|elsewhere|"
    r"brighter|dimmer|darker|brightened|dimmed|"
    r"przesun\w*|zmien\w*|inn\w*|ja[sś]niej\w*|ciemniej\w*)\b"
)


ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}


def _numbers(text: str) -> set:
    """Positions named numerically — "LED 3", "the second one"."""
    found = {int(tok) for tok in re.findall(r"\b\d+\b", text)}
    for word, value in ORDINALS.items():
        if re.search(rf"\b{word}\b", text):
            found.add(value)
    return found


def reports_change(answer: str, reference: str) -> bool:
    """Did this answer describe something different from the reference?

    Deliberately conservative. It needs a recognisably different position, a
    different numbered position, or an explicit word of change; anything
    ambiguous counts as no change. A verdict that invents a finding is worse
    than one that misses it, because the finding gets built upon.
    """
    low = answer.strip().lower()
    if not low or SAME_RE.search(low):
        return False
    if CHANGE_RE.search(low):
        return True

    here, there = position(reference), position(answer)
    if here and there and here != there:
        return True

    mine, theirs = _numbers(reference.lower()), _numbers(low)
    if mine and theirs and mine != theirs:
        return True

    return False


def classify(answer: str) -> List[str]:
    """Which outcomes an answer reports. Empty means 'could not tell'.

    Returning nothing is a valid and useful result — the verdicts treat an
    unclassifiable answer as unclear rather than guessing at it.
    """
    low = answer.lower()
    return [name for name, pattern in COLOUR_WORDS.items() if re.search(pattern, low)]


def _step(link, journal, label: str, packets: List[bytes], question: str,
          timeout: int) -> str:
    """Send one or more packets as a single unit, then ask what happened.

    Packets in one step are sent without an intervening prompt, which is how a
    stage-then-commit pair gets tested as one action.
    """
    for i, payload in enumerate(packets):
        print(f"  TX: {hexs(payload[:12])}")
        show(link.exchange(payload, timeout_ms=timeout, label=f"{label}:{i}"))
    answer = ask(question)
    journal.observation(answer, label=label)
    return answer


def exp_commit(link, journal, timeout: int) -> Dict[str, str]:
    """Is 0x0d an 'off' command, or the commit half of a stage/commit pair?

    The distinction decides the entire write model, so it is resolved before
    any effort goes into mapping zones or colours.
    """
    red = protocol.build_probe(r=0xFF, opcode=0x0D)
    green = protocol.build_probe(g=0xFF, opcode=0x0D)
    stage_blue = protocol.build_probe(b=0xFF, opcode=0x01)
    commit_black = protocol.build_probe(opcode=0x0D)
    white_05 = protocol.build_probe(r=0xFF, g=0xFF, b=0xFF, opcode=0x05)

    answers: Dict[str, str] = {}

    print("\n1/4  opcode 0d carrying red")
    answers["0d+red"] = _step(link, journal, "exp:commit:0d-red", [red],
                              "LEDs? (red / off / nothing / describe)", timeout)

    print("\n2/4  opcode 0d carrying green")
    answers["0d+green"] = _step(link, journal, "exp:commit:0d-green", [green],
                                "LEDs? (green / off / nothing / describe)", timeout)

    print("\n3/4  stage blue with 01, then commit black with 0d")
    answers["stage+commit"] = _step(
        link, journal, "exp:commit:stage-blue", [stage_blue, commit_black],
        "LEDs? (blue / off / nothing / describe)", timeout)

    print("\n4/4  opcode 05 carrying white")
    answers["05+white"] = _step(link, journal, "exp:commit:05-white", [white_05],
                                "LEDs? (white / off / nothing / describe)", timeout)
    return answers


def verdict_commit(answers: Dict[str, str]) -> str:
    red = classify(answers.get("0d+red", ""))
    green = classify(answers.get("0d+green", ""))
    staged = classify(answers.get("stage+commit", ""))

    carries_colour = "red" in red or "green" in green
    stages = "blue" in staged

    if carries_colour and not stages:
        return (
            "0x0d carries colour in its own packet. That is a direct write:\n"
            "  opcode 0d + RGB at offsets 4..6 sets the lighting.\n"
            "Next: find the zone selector, then map zones to physical segments."
        )
    if stages:
        return (
            "Stage-then-commit confirmed: 0x01 loads colour, 0x0d applies it.\n"
            "That is the whole write model. Next: find which byte selects the\n"
            "zone, then map zone values to physical segments."
        )
    if "off" in red and "off" in green:
        return (
            "0x0d ignores RGB and simply blanks the lighting — an off command.\n"
            "The colour bytes therefore live behind a different opcode. Next:\n"
            "census byte 0 to find which opcodes light anything at all."
        )
    return (
        "Mixed or unclear result. The raw answers are in the journal; the\n"
        "honest read is that no model is established yet. An opcode census\n"
        "(probe.py experiment opcodes) is the next cheapest step."
    )


def exp_opcodes(link, journal, timeout: int) -> Dict[str, str]:
    """Which byte-0 values do anything visible?

    Bright red throughout, so any response is unmistakable, and the reply's
    opcode echo tells us separately whether the command was even accepted.
    """
    answers: Dict[str, str] = {}
    opcodes = safety.iter_safe(range(0x00, 0x11))
    print(f"Testing {len(opcodes)} opcodes with red. Say what you see, or press")
    print("Enter for no change.\n")
    for opcode in opcodes:
        print(f"opcode {opcode:02x}")
        payload = protocol.build_probe(r=0xFF, opcode=opcode)
        answers[f"{opcode:02x}"] = _step(
            link, journal, f"exp:opcodes:{opcode:02x}", [payload], "LEDs?", timeout)
    return answers


def verdict_opcodes(answers: Dict[str, str]) -> str:
    live = [op for op, ans in answers.items() if "nothing" not in classify(ans) and ans]
    live = [op for op in live if classify(answers[op])]
    if not live:
        return "No opcode in 0x00-0x10 changed the lighting."
    lines = ["Opcodes that changed the lighting:"]
    for op in live:
        lines.append(f"  {op}  {answers[op]}")
    return "\n".join(lines)


# Established 2026-07-28: 0x05 lights a segment, 0x0d blanks everything.
OP_SET = 0x05
OP_BLANK = 0x0D


def name_rgb(r: int, g: int, b: int) -> str:
    """The colour name a human would give an (r, g, b) triple.

    Only the unmistakable cases are named. Anything else returns "other" and is
    excluded from model comparison rather than forced into a bucket.
    """
    high = [c >= 0x80 for c in (r, g, b)]
    if not any(high):
        return "off"
    if all(high):
        return "white"
    if high == [True, False, False]:
        return "red"
    if high == [False, True, False]:
        return "green"
    if high == [False, False, True]:
        return "blue"
    return "other"


# Candidate readings of offsets 4..6. Plain RGB is already contradicted by
# ff ff ff producing red, but it stays in the list so the contradiction is
# visible in the output rather than assumed.
COLOUR_MODELS = {
    "plain RGB (4,5,6)": lambda a, b, c: (a, b, c),
    "BGR (6,5,4)": lambda a, b, c: (c, b, a),
    "GRB (5,4,6)": lambda a, b, c: (b, a, c),
    "R, inverted G, inverted B": lambda a, b, c: (a, 0xFF - b, 0xFF - c),
    "fully inverted": lambda a, b, c: (0xFF - a, 0xFF - b, 0xFF - c),
}

COLOUR_TRIALS = [
    (0xFF, 0x00, 0x00),
    (0x00, 0xFF, 0x00),
    (0x00, 0x00, 0xFF),
    (0xFF, 0xFF, 0xFF),
    (0x00, 0x00, 0x00),
]


def exp_colour(link, journal, timeout: int) -> Dict[str, str]:
    """What do offsets 4..6 actually encode?

    Each trial blanks first, so exactly one segment is lit and the observation
    cannot be contaminated by whatever the previous trial left behind.
    """
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}
    print(f"{len(COLOUR_TRIALS)} trials under opcode {OP_SET:02x}. Name the colour")
    print("you see on the lit segment, or say 'off'.\n")
    for r, g, b in COLOUR_TRIALS:
        key = f"{r:02x}{g:02x}{b:02x}"
        print(f"bytes 4..6 = {r:02x} {g:02x} {b:02x}")
        payload = protocol.build_probe(r=r, g=g, b=b, opcode=OP_SET)
        answers[key] = _step(link, journal, f"exp:colour:{key}", [blank, payload],
                             "colour? (red/green/blue/white/off/describe)", timeout)
    return answers


def verdict_colour(answers: Dict[str, str]) -> str:
    observed = {}
    for key, answer in answers.items():
        names = [n for n in classify(answer) if n != "nothing"]
        if names:
            observed[key] = names

    if not observed:
        return "No trial produced a nameable colour. Offsets 4..6 may not be colour at all."

    lines = ["observed:"]
    for key, names in observed.items():
        lines.append(f"  {key} -> {'/'.join(names)}")

    lines.append("")
    survivors = []
    for label, model in COLOUR_MODELS.items():
        contradictions = []
        for key, names in observed.items():
            raw = (int(key[0:2], 16), int(key[2:4], 16), int(key[4:6], 16))
            predicted = name_rgb(*model(*raw))
            if predicted == "other":
                continue
            if predicted not in names:
                contradictions.append(f"{key} predicted {predicted}, saw {'/'.join(names)}")
        if contradictions:
            lines.append(f"  ruled out: {label}  ({contradictions[0]})")
        else:
            survivors.append(label)

    lines.append("")
    if len(survivors) == 1:
        lines.append(f"Consistent with exactly one model: {survivors[0]}")
    elif survivors:
        lines.append("Still consistent with: " + ", ".join(survivors))
        lines.append("More trials needed to separate them.")
    else:
        lines.append("No candidate model fits. Offsets 4..6 are not a colour triple")
        lines.append("in any obvious ordering — the colour data likely lives elsewhere")
        lines.append("in the packet, or these bytes are an index into a palette.")
    return "\n".join(lines)


def exp_zones(link, journal, timeout: int) -> Dict[str, str]:
    """Which byte selects the segment, and where is each segment physically?

    Uses the one byte pattern known to produce a visible result, so the trial
    does not depend on having decoded the colour encoding first.
    """
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}
    print("Blanking, then lighting one segment at a time by varying byte 1.")
    print("Say where the lit segment is (e.g. 'bottom left', 'top right'),")
    print("or press Enter if nothing lit.\n")
    for index in range(0x00, 0x08):
        print(f"byte 1 = {index:02x}")
        payload = protocol.build_probe(r=0xFF, g=0xFF, b=0xFF, opcode=OP_SET, index=index)
        answers[f"{index:02x}"] = _step(
            link, journal, f"exp:zones:{index:02x}", [blank, payload],
            "which segment lit?", timeout)
    return answers


# Position vocabulary. Comparing raw answer strings does not work: "left
# bottom" and "left bottom, slighlty shifted" are the same place described
# twice, and treating them as two positions once produced a confident verdict
# that byte 1 was the segment selector when it had changed nothing at all.
_VERTICAL = {
    "bottom": ("bottom", "lower", "dol", "dół", "doln"),
    "top": ("top", "upper", "gora", "góra", "górn", "gorn"),
}
_HORIZONTAL = {
    "left": ("left", "lew"),
    "right": ("right", "praw"),
}


def _first_match(text: str, vocabulary: Dict[str, tuple]) -> Optional[str]:
    """The vocabulary term appearing earliest in the answer.

    Earliest wins because an answer like "bottom left, with a slight part of
    the right bottom segment" is naming a primary position and then qualifying
    it; the qualifier must not outvote the subject.
    """
    best, best_at = None, len(text) + 1
    for name, words in vocabulary.items():
        for word in words:
            at = text.find(word)
            if at != -1 and at < best_at:
                best, best_at = name, at
    return best


def position(answer: str) -> Optional[tuple]:
    """Normalise a free-text answer to a (vertical, horizontal) position."""
    low = answer.lower()
    vertical = _first_match(low, _VERTICAL)
    horizontal = _first_match(low, _HORIZONTAL)
    if vertical is None and horizontal is None:
        return None
    return (vertical, horizontal)


def verdict_zones(answers: Dict[str, str]) -> str:
    lit = {k: v for k, v in answers.items() if v and "nothing" not in classify(v)}
    if not lit:
        return ("Byte 1 lit nothing at any value 00-07. It is not the segment\n"
                "selector — try byte 2, or the bytes past offset 10.")

    positions = {k: position(v) for k, v in lit.items()}
    distinct = {p for p in positions.values() if p is not None}

    lines = [f"byte 1 lit something at {len(lit)} of 8 values:"]
    for key, answer in lit.items():
        where = positions[key]
        shown = "-".join(p for p in where if p) if where else "unrecognised"
        lines.append(f"  {key} -> {shown:14} ({answer})")
    lines.append("")

    if len(distinct) <= 1:
        lines.append("Every value lit the same position, so byte 1 does not select")
        lines.append("the segment. Wording differences between answers are not")
        lines.append("evidence of movement.")
        lines.append("")
        lines.append("Before trying another offset: if the lighting is animated, no")
        lines.append("byte will appear to do anything, because the effect repaints")
        lines.append("over whatever we write. Run `experiment animation` first.")
    else:
        lines.append(f"Byte 1 lit {len(distinct)} genuinely different positions, so it")
        lines.append("is a candidate segment selector.")
        lines.append("Confirm by predicting a position before sending, then extend")
        lines.append("the range past 07 to count the segments.")
    return "\n".join(lines)


def _pause(seconds: int) -> None:
    print(f"  ...watching for {seconds}s, don't touch anything...")
    time.sleep(seconds)


def exp_animation(link, journal, timeout: int) -> Dict[str, str]:
    """Is the lighting animated, and does 0x05 restore the monitor's own effect?

    A control experiment, not a decode. If an effect is running, it repaints
    over anything we write, every byte looks inert, and each observation is a
    snapshot of the animation rather than a response to our packet. That failure
    mode is invisible from inside the data — a constant colour with drifting
    geometry looks exactly like a protocol we have not cracked yet — so the
    observation channel gets validated before more effort is spent on it.
    """
    answers: Dict[str, str] = {}

    print("\n1/5  no packets sent — just watch")
    _pause(15)
    answers["idle_moves"] = _step(
        link, journal, "exp:anim:idle", [],
        "Did the lighting move, pulse or change by itself? (yes/no)", timeout)

    print("\n2/5  sending 0d")
    answers["after_off"] = _step(
        link, journal, "exp:anim:off", [protocol.build_probe(opcode=OP_BLANK)],
        "Lighting off now? (yes/no)", timeout)

    print("\n3/5  waiting, sending nothing")
    _pause(10)
    answers["off_persists"] = _step(
        link, journal, "exp:anim:off-persists", [],
        "Still off, or did it come back on by itself? (still off / came back)", timeout)

    print("\n4/5  sending 05")
    answers["after_on"] = _step(
        link, journal, "exp:anim:on",
        [protocol.build_probe(r=0xFF, g=0xFF, b=0xFF, opcode=OP_SET)],
        "Describe exactly what is lit — colour and position", timeout)

    print("\n5/5  waiting, sending nothing")
    _pause(10)
    answers["on_drifts"] = _step(
        link, journal, "exp:anim:on-drifts", [],
        "Has it changed since your last answer? (yes/no)", timeout)
    return answers


def _affirmative(answer: str) -> bool:
    return bool(re.search(r"\b(yes|yeah|yep|tak|came back|moved|moves)\b", answer.lower()))


def verdict_animation(answers: Dict[str, str]) -> str:
    idle = _affirmative(answers.get("idle_moves", ""))
    drifts = _affirmative(answers.get("on_drifts", ""))
    returned = _affirmative(answers.get("off_persists", ""))

    lines = []
    if idle or drifts:
        lines += [
            "An animation IS running.",
            "",
            "Every observation taken while it runs is a snapshot of the effect,",
            "not a response to our packet. That invalidates the colour and zone",
            "results: the constant red with drifting geometry was the effect",
            "moving between answers, not a field we failed to decode.",
            "",
            "Required before any further decoding: set the lighting to a STATIC",
            "colour in the OSD, and pick one that is not red — green or blue —",
            "so anything we successfully write is instantly distinguishable from",
            "the monitor's own state.",
        ]
    else:
        lines += [
            "No animation detected. The lighting is static, so observations can",
            "be attributed to the packets that preceded them.",
            "",
            "That makes the earlier results real findings: offsets 4..6 genuinely",
            "do not carry colour, and byte 1 genuinely does not select a segment.",
            "The addressing lives somewhere else in the 64 bytes — most likely",
            "past offset 10, which we have never varied.",
        ]

    if returned:
        lines += ["", "0x0d's blanking is temporary — the lighting restored itself,",
                  "so the monitor reasserts its own state on a timer."]
    return "\n".join(lines)


FILL = 0xFF


def _fill(base: bytes, lo: int, hi: int, value: int = FILL) -> bytes:
    """Copy of `base` with the inclusive range [lo, hi] set to `value`."""
    out = bytearray(base)
    for i in range(lo, hi + 1):
        out[i] = value
    return bytes(out)


def _differs(link, journal, blank: bytes, payload: bytes, label: str,
             timeout: int) -> bool:
    for pkt in (blank, payload):
        link.exchange(pkt, timeout_ms=timeout, label=label)
    answer = ask("same as the reference, or different? (same/different)")
    journal.observation(answer, label=label)
    return bool(re.search(r"\b(diff\w*|inn\w*|zmien\w*)\b", answer.lower()))


def exp_bisect(link, journal, timeout: int) -> Dict[str, str]:
    """Which byte of the packet does opcode 0x05 actually read?

    We have been varying 11 bytes of a 64-byte report and seeing no response.
    Bytes 11..63 have never been touched. Rather than sweep 53 bytes one at a
    time, this fills whole ranges and narrows by halves, so locating a single
    responsive byte costs about six observations instead of fifty.

    The first trial is the valuable one: it sets every byte but the opcode, so
    if the result is unchanged then the device ignores the entire packet body
    and 0x05 is nothing more than "on" — which we learn for one observation.
    """
    base = protocol.build_probe(opcode=OP_SET)
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}

    print("\nEstablishing the reference. Blanking, then sending the base packet.")
    for pkt in (blank, base):
        link.exchange(pkt, timeout_ms=timeout, label="bisect:reference")
    reference = ask("Describe exactly what is lit — this is the reference")
    journal.observation(reference, label="bisect:reference")
    answers["reference"] = reference

    print("\nTrial 1: setting every byte except the opcode to ff.")
    print("If this looks identical to the reference, the device is ignoring")
    print("the whole packet body.\n")
    if not _differs(link, journal, blank, _fill(base, 1, 63), "bisect:all", timeout):
        answers["responsive"] = "none"
        return answers

    lo, hi = 1, 63
    while lo < hi:
        mid = (lo + hi) // 2
        print(f"\nNarrowing: bytes {lo}..{mid} set to ff (of {lo}..{hi})")
        if _differs(link, journal, blank, _fill(base, lo, mid),
                    f"bisect:{lo}-{mid}", timeout):
            hi = mid
        else:
            lo = mid + 1
    answers["responsive"] = str(lo)
    return answers


def verdict_bisect(answers: Dict[str, str]) -> str:
    responsive = answers.get("responsive")
    reference = answers.get("reference", "(not recorded)")

    if responsive == "none":
        return (
            f"reference: {reference}\n\n"
            "Setting all 63 payload bytes to ff changed nothing.\n\n"
            "The device ignores the entire packet body under opcode 0x05, so the\n"
            "output is fixed to the opcode itself. Note this is NOT the same as\n"
            "0x05 meaning 'lighting on': the OSD was set to static green and 0x05\n"
            "produced red, so it writes its own colour rather than restoring the\n"
            "monitor's state.\n\n"
            "That makes 0x05 a hardcoded write, and colour and zone selection must\n"
            "live behind a different opcode.\n\n"
            "Next: `experiment opcodes` to find which other byte-0 values do\n"
            "anything."
        )
    if responsive is None:
        return "Bisection did not complete. The journal holds the answers so far."
    offset = int(responsive)
    guess = protocol.HYPOTHESIS.get(offset, "not in the current hypothesis")
    return (
        f"reference: {reference}\n\n"
        f"Byte {offset} changes the output under opcode 0x05.  ({guess})\n\n"
        "This is the first byte shown to matter. Confirm it is real before\n"
        "building on it: predict what a specific value will do, then send it.\n"
        f"  probe.py sweep --byte {offset} --values 0x00:0x0f --observe\n\n"
        "Note the bisection finds ONE responsive byte. If several matter, the\n"
        "others are still hidden — re-run after pinning this one down."
    )


def _repeat(base: bytes, start: int, pattern: tuple) -> bytes:
    """Fill from `start` to the end of the report with a repeating pattern."""
    out = bytearray(base)
    for i in range(start, protocol.REPORT_SIZE):
        out[i] = pattern[(i - start) % len(pattern)]
    return bytes(out)


# Candidate offsets at which per-LED data could begin. Bisection put the first
# responsive byte at 11 and showed bytes 1..8 inert, so the boundary is at 9,
# 10 or 11; a triplet stream starting at 10 would give exactly 18 LEDs in the
# remaining 54 bytes, which is the tidiest of the three.
DATA_STARTS = (9, 10, 11)

GREEN = (0x00, 0xFF, 0x00)
PHASE_B = (("red", (0xFF, 0x00, 0x00)),
           ("blue", (0x00, 0x00, 0xFF)),
           ("white", (0xFF, 0xFF, 0xFF)))


def exp_stream(link, journal, timeout: int) -> Dict[str, str]:
    """Is the tail of the packet a stream of RGB triplets?

    Bisection showed the lit area grows with the number of bytes set, which is
    the signature of a pixel buffer rather than a field. If those bytes are RGB
    triplets, a repeating green pattern must produce green — and everything we
    have seen so far has been red, so green is unmistakable.

    Phase A tries the three plausible alignments; phase B only runs if one of
    them yields green, because without colour control the alignment does not
    matter yet.
    """
    base = protocol.build_probe(opcode=OP_SET)
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}

    print("\nPhase A: repeating 00 ff 00 from three candidate start offsets.")
    print("If the tail is RGB triplets, one of these must come out GREEN.\n")
    for start in DATA_STARTS:
        print(f"data starting at byte {start}")
        answers[f"green@{start}"] = _step(
            link, journal, f"stream:green@{start}",
            [blank, _repeat(base, start, GREEN)],
            "colour? (green / red / other — describe)", timeout)

    winner = next(
        (s for s in DATA_STARTS if "green" in classify(answers[f"green@{s}"])), None)

    if winner is None:
        print("\nNo alignment produced green. Testing whether the bytes are")
        print("intensity-only, with colour fixed elsewhere.\n")
        answers["dim"] = _step(
            link, journal, "stream:dim", [blank, _repeat(base, 10, (0x40,))],
            "same colour as before but dimmer, or something else?", timeout)
        return answers

    answers["start"] = str(winner)
    print(f"\nGreen at start {winner}. Phase B: confirming the other channels.\n")
    for name, pattern in PHASE_B:
        print(f"repeating {' '.join(f'{v:02x}' for v in pattern)} — expecting {name}")
        answers[name] = _step(
            link, journal, f"stream:{name}",
            [blank, _repeat(base, winner, pattern)],
            f"colour? (expecting {name} — describe what you see)", timeout)
    return answers


def verdict_stream(answers: Dict[str, str]) -> str:
    start = answers.get("start")
    if start is None:
        dim = answers.get("dim", "")
        lines = ["No alignment produced green, so the tail is not RGB triplets.", ""]
        for s in DATA_STARTS:
            lines.append(f"  start {s}: {answers.get(f'green@{s}', '(no answer)')}")
        lines.append("")
        if dim:
            lines.append(f"  intensity test: {dim}")
        lines += [
            "",
            "The bytes still control which LEDs light — bisection established",
            "that. So they are most likely per-LED intensity with the colour",
            "fixed by something else, or a packed format wider than one byte per",
            "channel. Colour control is not solved and may need a different",
            "opcode entirely.",
        ]
        return "\n".join(lines)

    hits = [name for name, _ in PHASE_B if name in classify(answers.get(name, ""))]
    leds = (protocol.REPORT_SIZE - int(start)) // 3

    lines = [
        f"The tail is a stream of RGB triplets starting at byte {start}.",
        f"That leaves {protocol.REPORT_SIZE - int(start)} bytes = {leds} LEDs "
        f"per report.",
        "",
        f"  green confirmed at start {start}",
    ]
    for name, _ in PHASE_B:
        mark = "confirmed" if name in hits else f"NOT seen — got: {answers.get(name, '')}"
        lines.append(f"  {name}: {mark}")
    lines.append("")

    if len(hits) == len(PHASE_B):
        lines += [
            "All channels behave. This is the write path:",
            f"  05 <8 header bytes> then {leds} RGB triplets from byte {start}.",
            "",
            "Next: confirm by prediction — light one LED a specific colour and",
            "check it lands where predicted, then map LED index to position.",
        ]
    else:
        lines += [
            "Green works but not every channel did, so the ordering may not be",
            "R,G,B. Vary one channel at a time from here rather than assuming.",
        ]
    return "\n".join(lines)


DATA_FIRST = protocol.DATA_FIRST   # one source of truth
PLANE_CANDIDATES = (0x06, 0x07, 0x04, 0x08)


def _lit(opcode: int = OP_SET, value: int = 0xFF) -> bytes:
    """A packet that lights the whole diamond, so modifiers have something to
    modify. Testing a colour field against an empty buffer is what made bytes
    1..8 look inert twice over."""
    return _fill(protocol.build_probe(opcode=opcode), DATA_FIRST, 63, value)


def exp_hunt(link, journal, timeout: int) -> Dict[str, str]:
    """Where does colour come from?

    Every observation so far has been red, and we have only ever used opcode
    0x05. One byte per LED across 55 bytes would mean 55 red-only LEDs, which no
    RGB product has — so the likelier reading is that the opcode selects a
    colour plane and 0x05 is simply the red one.

    Phase A tries the neighbouring opcodes, which is the cheap high-prior test.
    Phase B only runs if that fails, and retests the header bytes with the
    buffer lit, since the earlier "bytes 1..8 are inert" finding was measured
    against an empty buffer and cannot distinguish a dead byte from a colour
    field with nothing to colour.
    """
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}

    print("\nReference: whole diamond lit under opcode 05.")
    for pkt in (blank, _lit()):
        link.exchange(pkt, timeout_ms=timeout, label="hunt:reference")
    answers["reference"] = ask("Describe it — colour and brightness")
    journal.observation(answers["reference"], label="hunt:reference")

    print("\nPhase A: the same buffer under neighbouring opcodes.")
    print("If the opcode selects a colour plane, one of these is not red.\n")
    for opcode in PLANE_CANDIDATES:
        print(f"opcode {opcode:02x}")
        answers[f"op{opcode:02x}"] = _step(
            link, journal, f"hunt:op{opcode:02x}", [blank, _lit(opcode)],
            "colour? (describe — or 'nothing' if unlit)", timeout)

    if any(set(classify(v)) - {"red", "nothing", "off"}
           for k, v in answers.items() if k.startswith("op")):
        return answers

    print("\nPhase B: header bytes, this time with the buffer lit.\n")
    base = _lit()
    cleared = _fill(base, 4, 6, 0x00)
    header_trials = [
        ("4-6 = 00 ff 00", protocol.with_byte(cleared, 5, 0xFF)),
        ("4-6 = 00 00 ff", protocol.with_byte(cleared, 6, 0xFF)),
        ("4-6 = ff ff ff", _fill(base, 4, 6, 0xFF)),
        ("byte 3: d0 -> 00", protocol.with_byte(base, 3, 0x00)),
        ("byte 7: 0f -> 00", protocol.with_byte(base, 7, 0x00)),
        ("byte 8: 06 -> 01", protocol.with_byte(base, 8, 0x01)),
    ]

    for name, payload in header_trials:
        print(name)
        answers[f"hdr:{name}"] = _step(
            link, journal, f"hunt:hdr:{name}", [blank, payload],
            "colour? (describe)", timeout)
    return answers


def verdict_hunt(answers: Dict[str, str]) -> str:
    reference = answers.get("reference", "(not recorded)")
    lines = [f"reference (opcode 05, buffer lit): {reference}", ""]

    def interesting(value: str) -> bool:
        return bool(set(classify(value)) - {"red", "nothing", "off"})

    planes = {k: v for k, v in answers.items() if k.startswith("op")}
    headers = {k: v for k, v in answers.items() if k.startswith("hdr:")}

    lines.append("opcodes tried with the buffer lit:")
    for key, value in planes.items():
        lines.append(f"  {key[2:]}  {value}")

    plane_hits = [k[2:] for k, v in planes.items() if interesting(v)]
    if plane_hits:
        lines += [
            "",
            f"Opcode(s) {', '.join(plane_hits)} produced a colour other than red.",
            "The opcode selects the colour plane: write the red plane with 0x05,",
            "then the others, to compose an arbitrary colour per LED.",
            "",
            "Next: confirm by prediction — write a specific LED a specific colour",
            "across the planes and check it lands where predicted.",
        ]
        return "\n".join(lines)

    if not headers:
        lines += ["", "No neighbouring opcode changed the colour."]
        return "\n".join(lines)

    lines += ["", "header bytes tried with the buffer lit:"]
    for key, value in headers.items():
        lines.append(f"  {key[4:]:18} {value}")

    header_hits = [k[4:] for k, v in headers.items() if interesting(v)]
    if header_hits:
        lines += [
            "",
            f"Colour changed with: {', '.join(header_hits)}.",
            "That is the colour control, and it was invisible earlier only",
            "because the buffer was empty. Sweep it next.",
        ]
    else:
        lines += [
            "",
            "Neither the neighbouring opcodes nor the header bytes changed the",
            "colour, with the buffer lit in every trial. So colour is not",
            "selected anywhere in this packet.",
            "",
            "Remaining possibilities, cheapest first: a separate mode-setting",
            "packet sent before the pixel data; a wider opcode range than the",
            "four tried here; or the 6-byte feature report, which we have never",
            "successfully written.",
            "",
            "Worth stating plainly: single-colour control of 55 addressable LEDs",
            "is already a working tool. If colour resists a couple more rounds,",
            "shipping red-only and revisiting is the better trade.",
        ]
    return "\n".join(lines)


def exp_confirm(link, journal, timeout: int) -> Dict[str, str]:
    """Predict, then send, then check — the gate between model and fact.

    Each trial states what should happen before the packet goes out. A model
    that only ever explains observations already taken is not yet knowledge;
    this is what promotes the plane decoding from HYPOTHESIS to CONFIRMED.

    Two things beyond the model are settled here: whether plane writes
    accumulate or replace each other, and whether byte 1 addresses the LEDs
    beyond the first 55 that the whole-buffer write left dark.
    """
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}

    def trial(key: str, prediction: str, packets: List[bytes], question: str) -> None:
        print(f"\n{key}")
        print(f"  predicting: {prediction}")
        answers[key] = _step(link, journal, f"confirm:{key}", packets, question, timeout)

    one_red = protocol.plane_packet(protocol.PLANE_RED, [0xFF])
    one_blue = protocol.plane_packet(protocol.PLANE_BLUE, [0xFF])
    one_green = protocol.plane_packet(protocol.PLANE_GREEN, [0xFF])

    trial("single LED", "exactly ONE LED lit, red, at one end of the strip",
          [blank, one_red], "how many LEDs lit, what colour, and where?")

    trial("plane persistence",
          "adding the blue plane WITHOUT blanking: magenta if planes stack, "
          "blue if each write replaces the frame",
          [one_blue], "colour of that LED now? (magenta / blue / other)")

    trial("white LED", "one LED, white",
          [blank, one_red, one_green, one_blue], "colour of the lit LED?")

    half = protocol.LED_COUNT // 2
    trial("two-tone",
          f"first {half} LEDs red, remaining {protocol.LED_COUNT - half} blue",
          [blank,
           protocol.plane_packet(protocol.PLANE_RED, [0xFF] * half),
           protocol.plane_packet(protocol.PLANE_BLUE,
                                 [0x00] * half + [0xFF] * (protocol.LED_COUNT - half))],
          "describe what you see")

    trial("chunk 1",
          "if byte 1 is a chunk index, the LEDs left dark by a full write now light",
          [blank,
           protocol.plane_packet(protocol.PLANE_RED, [0xFF] * protocol.LED_COUNT),
           protocol.plane_packet(protocol.PLANE_RED, [0xFF] * protocol.LED_COUNT, chunk=1)],
          "is the whole diamond lit now, including the previously dark LEDs?")
    return answers


def verdict_confirm(answers: Dict[str, str]) -> str:
    lines = ["predictions and outcomes:", ""]
    for key, answer in answers.items():
        lines.append(f"  {key:18} {answer}")
    lines.append("")

    composed = "white" in classify(answers.get("white LED", ""))

    if composed:
        lines += [
            "Three plane writes in red-green-blue order produced white, so the",
            "planes are independent buffers and colour composes. The write path",
            "is CONFIRMED.",
            "",
            "The 'plane persistence' trial is NOT evidence against this: it sends",
            "blue directly after red, skipping green, and the device answers that",
            "with status 0xf0 — a rejection. Planes must be written in order.",
        ]
    else:
        lines += [
            "Three plane writes did not produce white, so composition is not",
            "established. Check the journal for status 0xf0 replies before",
            "concluding anything: a rejected write looks exactly like a wrong",
            "model from the LEDs alone.",
        ]

    lines.append("")
    lines.append("Reply byte 3 is a status: 0x80 accepted, 0xf0 rejected. That is")
    lines.append("the only feedback this device gives, and omenled.device now")
    lines.append("checks it on every render.")

    if composed:
        lines += ["", "The CLI is ready:", "  python3 -m omenled.cli fill '#00ff00'"]
    return "\n".join(lines)


def _frame(colour=(0xFF, 0xFF, 0xFF), chunk: int = 0,
           byte7: Optional[int] = None, byte8: Optional[int] = None) -> List[bytes]:
    """A full-brightness frame with optional header overrides."""
    packets = protocol.frame_packets([colour] * protocol.LED_COUNT, chunk=chunk)
    out = []
    for packet in packets:
        if byte7 is not None:
            packet = protocol.with_byte(packet, 7, byte7)
        if byte8 is not None:
            packet = protocol.with_byte(packet, 8, byte8)
        out.append(packet)
    return out


def exp_tail(link, journal, timeout: int) -> Dict[str, str]:
    """Why does part of the bottom-left segment never respond?

    Byte 7 has always been 0x0f and byte 8 always 0x06. If byte 8 is a segment
    count and byte 7 a bitmask of segments to write, then 0x0f selects only four
    of six and two segments have never been addressed — which is what an area
    that ignores both writes and blanking looks like.

    The `off` trial runs first because it is the cleanest discriminator: an area
    still lit after a blank is outside our control entirely, and no amount of
    pixel data will reach it.
    """
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}

    def trial(key: str, prediction: str, packets: List[bytes], question: str) -> None:
        print(f"\n{key}\n  expecting: {prediction}")
        answers[key] = _step(link, journal, f"tail:{key}", packets, question, timeout)

    trial("off", "the whole diamond dark", [blank],
          "is ANY part still lit? (no / describe what stays lit)")

    trial("baseline white", "all but the problem area white",
          [blank] + _frame(), "which parts are lit, which are not?")

    trial("byte7 = 3f", "if byte 7 is a segment mask, the missing area now lights",
          [blank] + _frame(byte7=0x3F), "is the whole diamond white now?")

    trial("byte7 = ff", "same idea, all bits set",
          [blank] + _frame(byte7=0xFF), "is the whole diamond white now?")

    trial("byte8 = 3f", "if byte 8 is a count rather than byte 7 a mask",
          [blank] + _frame(byte8=0x3F), "any change to the missing area?")

    trial("chunk 1", "if byte 1 selects a further block of LEDs",
          [blank] + _frame(chunk=1), "what is lit?")
    return answers


def verdict_tail(answers: Dict[str, str]) -> str:
    lines = ["trials:", ""]
    for key, answer in answers.items():
        lines.append(f"  {key:16} {answer}")
    lines.append("")

    def whole(key: str) -> bool:
        """True only for unqualified completeness.

        "Whole diamond except of middle led" is not whole. Matching the word
        "whole" alone once reported three separate trials as fixes when every
        one of them had reproduced the fault verbatim.
        """
        value = answers.get(key, "").lower()
        if re.search(r"\b(except|but|without|missing|apart from|"
                     r"opr[oó]cz|poza|bez)\b", value):
            return False
        return bool(re.search(r"\b(whole|entire|all|yes|tak|ca[lł]\w*)\b", value))

    survives_off = not re.search(
        r"\b(no|none|nie|dark|off|nothing)\b", answers.get("off", "").lower())

    winners = [k for k in ("byte7 = 3f", "byte7 = ff", "byte8 = 3f", "chunk 1")
               if whole(k)]

    if winners:
        lines += [
            f"Fixed by: {', '.join(winners)}.",
            "",
            "That header byte selects which segments a write reaches. Set it in",
            "omenled/protocol.py HEADER_TAIL and the whole diamond becomes",
            "addressable.",
        ]
    elif survives_off:
        lines += [
            "Part of the lighting survives a blank and ignores every header",
            "variant tried. That area is not addressed by this report at all.",
            "",
            "It is most likely a separate strip behind its own opcode or report",
            "shape. Worth one more session; it does not affect control of the",
            "LEDs we can already reach.",
        ]
    else:
        lines += [
            "Blanking does clear everything, so the area is reachable — but none",
            "of the header variants tried made a full write cover it.",
            "",
            "Next cheapest: sweep byte 7 across all 256 values with the buffer",
            "lit, since a mask should show a clear on/off boundary.",
        ]
    return "\n".join(lines)


def exp_led0(link, journal, timeout: int) -> Dict[str, str]:
    """Does the LED data start at byte 8 rather than byte 9?

    Byte 8 has been 0x06 on every write ever made. If it is LED 0 rather than a
    header byte, it has been sitting at 6/255 — about 2% — which next to
    full-brightness neighbours reads as unlit. That would make the strip 56
    LEDs, starting mid-bottom-left, which is exactly where the dark LED is.

    Trial 3 is the discriminating one: it predicts that setting byte 8 alone
    lights precisely the LED that has never lit, and nothing else.
    """
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}

    def planes_with(byte8: int, data: int) -> List[bytes]:
        out = []
        for plane in protocol.PLANES:
            packet = protocol.plane_packet(plane, [data] * protocol.LED_COUNT)
            out.append(protocol.with_byte(packet, 8, byte8))
        return out

    def trial(key: str, prediction: str, packets: List[bytes], question: str) -> None:
        print(f"\n{key}\n  predicting: {prediction}")
        answers[key] = _step(link, journal, f"led0:{key}", packets, question, timeout)

    trial("byte8 = ff, all lit",
          "the dark LED finally lights — the diamond is completely white",
          [blank] + planes_with(0xFF, 0xFF),
          "is the diamond COMPLETELY white, no gaps? (yes / describe the gap)")

    trial("byte8 = 00, all lit",
          "the same single LED dark again, confirming byte 8 drives it",
          [blank] + planes_with(0x00, 0xFF),
          "is that one LED dark again? (yes / no / describe)")

    trial("byte8 alone",
          "ONLY the previously-dark LED lights, everything else off",
          [blank] + planes_with(0xFF, 0x00),
          "how many LEDs are lit and where?")
    return answers


def verdict_led0(answers: Dict[str, str]) -> str:
    def affirms(key: str) -> bool:
        value = answers.get(key, "").lower()
        if re.search(r"\b(except|but|without|missing|gap|opr[oó]cz|poza|bez)\b", value):
            return False
        return bool(re.search(r"\b(yes|tak|complete\w*|whole|entire|all)\b", value))

    lines = ["trials:", ""]
    for key, answer in answers.items():
        lines.append(f"  {key:20} {answer}")
    lines.append("")

    filled = affirms("byte8 = ff, all lit")
    returned = affirms("byte8 = 00, all lit")
    alone = answers.get("byte8 alone", "").lower()
    single = bool(re.search(r"\b(one|1|single|jed\w*)\b", alone))

    if filled and returned and single:
        lines += [
            "CONFIRMED: the LED data starts at byte 8, not byte 9.",
            "",
            "The strip is 56 LEDs. Byte 8 is LED 0 and has been pinned at 0x06 —",
            "about 2% brightness — on every write we ever made, which is why it",
            "read as a dead LED.",
            "",
            "Fix: DATA_FIRST = 8 in omenled/protocol.py, and drop byte 8 from",
            "HEADER_TAIL. LED_COUNT becomes 56 and the whole diamond is",
            "addressable.",
        ]
    elif filled:
        lines += [
            "Byte 8 lights the missing LED, so it is pixel data rather than a",
            "header byte — but the confirming trials did not both agree, so",
            "check the answers above before changing DATA_FIRST.",
        ]
    else:
        lines += [
            "Byte 8 does not light the missing LED, so the data does not start",
            "there. With byte 7, byte 1 and the chunk index all excluded, the",
            "likeliest remaining explanation is a dead LED in the panel itself —",
            "which no protocol change will fix.",
            "",
            "A quick way to tell: the OSD's own lighting. If that LED stays dark",
            "under the monitor's built-in effects too, it is hardware.",
        ]
    return "\n".join(lines)


def exp_offset(link, journal, timeout: int) -> Dict[str, str]:
    """Can we reach the one LED past the end of the report?

    With 56 LEDs per report and the strip starting mid bottom-left, the dark LED
    sits immediately beside LED 0 — which is where the *last* LED of a strip
    lands when it wraps. Reaching it needs a second report, and bytes 2 and 3
    are the only header bytes never varied against a lit buffer.

    A single lit LED is the probe rather than a full frame: an offset shows up
    as the lit spot *moving*, which is far easier to see than one gap closing.
    """
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}

    def one_led(byte2: int = 0x00, byte3: int = 0xD0) -> List[bytes]:
        out = []
        for plane in protocol.PLANES:
            packet = protocol.plane_packet(plane, [0xFF])
            packet = protocol.with_byte(packet, 2, byte2)
            out.append(protocol.with_byte(packet, 3, byte3))
        return out

    def trial(key: str, prediction: str, packets: List[bytes]) -> None:
        print(f"\n{key}\n  expecting: {prediction}")
        answers[key] = _step(link, journal, f"offset:{key}", packets,
                             "where exactly is the lit LED?", timeout)

    trial("reference", "one white LED at the start of the strip",
          [blank] + one_led())
    trial("byte2 = 01", "if byte 2 is an offset, the lit LED moves by one",
          [blank] + one_led(byte2=0x01))
    trial("byte2 = 08", "a larger offset moves it further",
          [blank] + one_led(byte2=0x08))
    trial("byte3 = 00", "byte 3 changed from d0",
          [blank] + one_led(byte3=0x00))
    trial("byte3 = d1", "byte 3 incremented",
          [blank] + one_led(byte3=0xD1))
    return answers


def verdict_offset(answers: Dict[str, str]) -> str:
    lines = ["trials:", ""]
    for key, answer in answers.items():
        lines.append(f"  {key:12} {answer}")
    lines.append("")

    reference = answers.get("reference", "")
    moved = [
        key for key, answer in answers.items()
        if key != "reference" and reports_change(answer, reference)
    ]

    if moved:
        lines += [
            f"The lit LED moved with: {', '.join(moved)}.",
            "",
            "That byte offsets the write into the strip, which is how the LEDs",
            "past byte 63 are reached. Sweep it to find the total LED count,",
            "then teach omenled.device to send as many reports as the strip",
            "needs.",
        ]
    else:
        lines += [
            "Nothing moved the lit LED. Bytes 2 and 3 do not offset the write,",
            "and with byte 1 and the chunk index already excluded, this report",
            "shape reaches 56 LEDs and no more.",
            "",
            "The remaining LED is likely addressed by a report we have not found",
            "— or the strip really is 56 and that dark spot is a hardware fault.",
            "The OSD's own effects settle which: if it stays dark under HP's",
            "firmware too, nothing we send will fix it.",
            "",
            "Either way, 56 of 57 LEDs under full RGB control is the tool you",
            "asked for, and it works today.",
        ]
    return "\n".join(lines)


def exp_headerleds(link, journal, timeout: int) -> Dict[str, str]:
    """Are bytes 1..7 header, or are they LEDs we have been pinning?

    In every frame we send, byte 3 is 0xd0 in all three planes — (208,208,208),
    white — and bytes 4..6 are 0x00 in all three, off. The reported faults are a
    permanently white spot and a permanently dark one, adjacent, at the start of
    the strip. If bytes 1..7 are LED data rather than a header, that is not a
    coincidence but a complete explanation.

    Against it: byte 3's low nibble appears in the reply status, which is
    header-like. So each byte is tested directly — set alone to full brightness
    with every other byte zero. A byte that lights something is a pixel.
    """
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}

    def only(offset: int, value: int = 0xFF) -> List[bytes]:
        return [
            protocol.with_byte(bytes([plane]) + bytes(63), offset, value)
            for plane in protocol.PLANES
        ]

    print("Each trial lights ONE byte and zeroes every other byte in the report.")
    print("If that byte is a pixel, exactly one LED lights.\n")

    for offset in range(1, 8):
        print(f"byte {offset} = ff, everything else 0")
        answers[f"byte{offset}"] = _step(
            link, journal, f"hdrled:byte{offset}", [blank] + only(offset),
            "does anything light? (no / where?)", timeout)

    print("\nfinally: every byte 1..63 at full brightness")
    all_on = [bytes([plane]) + bytes([0xFF] * 63) for plane in protocol.PLANES]
    answers["all 1..63"] = _step(
        link, journal, "hdrled:all", [blank] + all_on,
        "is the diamond completely white with no gaps? (yes / describe)", timeout)
    return answers


def verdict_headerleds(answers: Dict[str, str]) -> str:
    lines = ["trials:", ""]
    for key, answer in answers.items():
        lines.append(f"  {key:12} {answer}")
    lines.append("")

    def lit(key: str) -> bool:
        value = answers.get(key, "").strip().lower()
        if not value or SAME_RE.search(value) or re.search(r"\b(no|nie|nothing)\b", value):
            return False
        return True

    pixels = [n for n in range(1, 8) if lit(f"byte{n}")]

    if pixels:
        first = min(pixels)
        lines += [
            f"Bytes {', '.join(str(p) for p in pixels)} light LEDs — they are "
            "pixel data, not header.",
            "",
            f"The data therefore starts at byte {first}, giving "
            f"{protocol.REPORT_SIZE - first} LEDs.",
            "",
            "Those LEDs were invisible because the old header pinned them to",
            "constants inherited from an old probe packet — zeros read as dead",
            "pixels, and a small non-zero value as a dim colour that never",
            "changed. A byte held at a constant cannot be told apart from a byte",
            "with no effect, which is why this took several passes to see.",
            "",
            f"Fix: DATA_FIRST = {first} in omenled/protocol.py, and shorten",
            "HEADER_TAIL to match.",
        ]
    else:
        lines += [
            "None of bytes 1..7 lights anything on its own, so they are header",
            "after all and the data does start at byte 8.",
            "",
            "The white and dark spots then belong to LEDs this report cannot",
            "reach, and every addressing mechanism tried — bytes 1, 2, 3, 7 and",
            "the chunk index — has been excluded. What is left is an opcode or",
            "report shape we have not found.",
            "",
            "`experiment opcodes` has never been run and would census byte 0",
            "across 0x00-0x10. That is the last cheap avenue.",
        ]

    final = answers.get("all 1..63", "")
    if final and not re.search(r"\b(except|but|gap|missing|opr[oó]cz)\b", final.lower()):
        lines += ["", f"Full-brightness write: {final}"]
    return "\n".join(lines)


def exp_hwbright(link, journal, timeout: int) -> Dict[str, str]:
    """Is there a hardware brightness control in the header?

    Bytes 1 and 2 light nothing on their own, but that was measured with every
    pixel at zero — and a global brightness multiplier applied to an all-black
    frame does nothing visible either. The same empty-buffer trap hid the pixel
    bytes for four passes, so they are re-tested here against a lit frame.

    The frame sits at half intensity so a change in either direction shows.
    """
    blank = protocol.build_probe(opcode=OP_BLANK)
    answers: Dict[str, str] = {}

    def frame(offset: Optional[int] = None, value: int = 0x00) -> List[bytes]:
        out = []
        for plane in protocol.PLANES:
            packet = protocol.plane_packet(plane, [0x80] * protocol.LED_COUNT)
            if offset is not None:
                packet = protocol.with_byte(packet, offset, value)
            out.append(packet)
        return out

    def trial(key: str, packets: List[bytes]) -> None:
        print(f"\n{key}")
        answers[key] = _step(link, journal, f"hwbright:{key}", packets,
                             "brighter, dimmer, or the same?", timeout)

    print("Every trial writes the same half-brightness white frame and changes")
    print("one header byte. Say brighter, dimmer, or the same.\n")

    trial("reference", [blank] + frame())
    for offset in (1, 2):
        for value in (0xFF, 0x01):
            trial(f"byte{offset} = {value:02x}", [blank] + frame(offset, value))
    trial("byte3 = 00", [blank] + frame(3, 0x00))
    trial("byte3 = f0", [blank] + frame(3, 0xF0))
    return answers


def verdict_hwbright(answers: Dict[str, str]) -> str:
    lines = ["trials:", ""]
    for key, answer in answers.items():
        lines.append(f"  {key:14} {answer}")
    lines.append("")

    reference = answers.get("reference", "")
    changed = [k for k, v in answers.items()
               if k != "reference" and reports_change(v, reference)]

    if changed:
        lines += [
            f"Brightness changed with: {', '.join(changed)}.",
            "",
            "That byte is a hardware brightness control. Sweep it to find the",
            "range, then expose it in omenled instead of scaling the pixel",
            "values — hardware dimming keeps the full 8 bits of colour, where",
            "software scaling loses precision at low levels.",
        ]
    else:
        lines += [
            "No header byte changed the brightness, tested against a lit frame",
            "this time rather than an empty one.",
            "",
            "There is no hardware brightness control in this report. Software",
            "scaling is the answer, which omenled already does:",
            "  python3 -m omenled.cli brightness 40",
            "",
            "The only cost is precision at very low settings, where 8-bit",
            "channels quantise — 10% of a mid grey is a handful of levels.",
        ]
    return "\n".join(lines)


EXPERIMENTS = {
    "hwbright": (exp_hwbright, verdict_hwbright,
                 "is there a hardware brightness byte in the header?"),
    "headerleds": (exp_headerleds, verdict_headerleds,
                   "are bytes 1..7 header, or LEDs we have been pinning?"),
    "offset": (exp_offset, verdict_offset,
               "can we reach the LED past the end of the report?"),
    "led0": (exp_led0, verdict_led0,
             "does the LED data start at byte 8, making the strip 56 LEDs?"),
    "tail": (exp_tail, verdict_tail,
             "why does part of the bottom-left segment never respond?"),
    "confirm": (exp_confirm, verdict_confirm,
                "predict-then-verify the decoded plane write path"),
    "hunt": (exp_hunt, verdict_hunt,
             "where does colour come from — plane opcodes or header bytes?"),
    "stream": (exp_stream, verdict_stream,
               "is the packet tail a stream of RGB triplets?"),
    "bisect": (exp_bisect, verdict_bisect,
               "which byte of the 64 does opcode 05 actually read?"),
    "animation": (exp_animation, verdict_animation,
                  "is an effect running that repaints over everything we write?"),
    "commit": (exp_commit, verdict_commit,
               "is 0x0d an off command or the commit half of stage/commit?"),
    "opcodes": (exp_opcodes, verdict_opcodes,
                "which byte-0 values change the lighting at all?"),
    "colour": (exp_colour, verdict_colour,
               "what do offsets 4..6 encode, given ff ff ff produced red?"),
    "zones": (exp_zones, verdict_zones,
              "does byte 1 select which segment lights?"),
}


def cmd_experiment(args: argparse.Namespace) -> int:
    if args.name not in EXPERIMENTS:
        print("Available experiments:")
        for name, (_, _, blurb) in EXPERIMENTS.items():
            print(f"  {name:10} {blurb}")
        return 2

    run, verdict, blurb = EXPERIMENTS[args.name]
    print(f"experiment: {args.name} — {blurb}")
    print("Watch the monitor. Ctrl-C is safe; answers are saved as you give them.")

    with Journal() as journal:
        print(f"session: {journal.path}")
        with transport.Transport(journal) as link:
            journal.note(f"experiment: {args.name}")
            answers = run(link, journal, args.timeout)

    text = verdict(answers)
    print("\n" + "=" * 60)
    print(text)
    print("=" * 60)
    journal_note = f"verdict({args.name}): {text}"
    with Journal(session=journal.session) as reopened:
        reopened.note(journal_note)
    return 0


# --------------------------------------------------------------------------
# analysis over journals
# --------------------------------------------------------------------------


def _collect(session_path: str, kind: str = "rx") -> Dict[str, bytes]:
    """label -> payload, for entries of one kind."""
    out: Dict[str, bytes] = {}
    for entry in read_session(session_path):
        if entry.get("kind") == kind and entry.get("hex") and entry.get("label"):
            out[entry["label"]] = unhex(entry["hex"])
    return out


def cmd_diff(args: argparse.Namespace) -> int:
    samples = _collect(args.session)
    if args.left not in samples or args.right not in samples:
        print("Labels present in that session:")
        for label in sorted(samples):
            print(f"  {label}")
        return 2

    differences = protocol.diff(samples[args.left], samples[args.right])
    if not differences:
        print(f"{args.left} and {args.right} are byte-identical.")
        print("The reply does not track that change — this is not a state channel.")
        return 0
    print(f"{args.left} -> {args.right}")
    for d in differences:
        print(f"  {d}")
    return 0


def cmd_survey(args: argparse.Namespace) -> int:
    samples = _collect(args.session)
    if not samples:
        print("No labelled replies in that session.")
        return 2
    print(f"{len(samples)} labelled replies")
    print(protocol.survey(samples.values()).report())
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    from omenled.journal import iter_sessions

    found = False
    for path in iter_sessions():
        entries = read_session(path)
        labels = {e.get("label") for e in entries if e.get("label")}
        observations = sum(1 for e in entries if e.get("kind") == "observation")
        print(f"{os.path.basename(path):24} {len(entries):4} entries  "
              f"{len(labels):3} labels  {observations:3} observations")
        found = True
    if not found:
        print("No sessions recorded yet.")
    return 0


# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p, transmits: bool = True):
        if transmits:
            p.add_argument("--timeout", type=int, default=500, help="read timeout, ms")
            p.add_argument("--dry-run", action="store_true",
                           help="show the packet without sending it")
            p.add_argument("--i-know-what-im-doing", action="store_true",
                           help="bypass the refusal list (see omenled/safety.py)")
        return p

    p = sub.add_parser("info", help="device facts; transmits nothing")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("baseline", help="what is readable before provoking anything")
    p.set_defaults(func=cmd_baseline)

    p = add_common(sub.add_parser("query", help="send one packet, annotate the reply"))
    p.add_argument("--raw", help='full packet as hex, e.g. "01 00 00 d0 ff 00 00"')
    p.add_argument("-r", type=parse_byte, default=0)
    p.add_argument("-g", type=parse_byte, default=0)
    p.add_argument("-b", type=parse_byte, default=0)
    p.add_argument("--opcode", type=parse_byte, default=protocol.BASELINE[0])
    p.add_argument("--index", type=parse_byte, default=protocol.BASELINE[1])
    p.add_argument("--marker", type=parse_byte, default=protocol.BASELINE[3])
    p.add_argument("--label", default="query")
    p.add_argument("--observe", action="store_true", help="prompt for what the LEDs did")
    p.set_defaults(func=cmd_query)

    p = add_common(sub.add_parser("sweep", help="vary exactly one byte"))
    p.add_argument("--byte", type=int, required=True, help="offset to vary")
    p.add_argument("--values", required=True, help='"0x00:0x0f" or "0,1,5"')
    p.add_argument("--base", help="base packet as hex; defaults to the hypothesis packet")
    p.add_argument("--observe", action="store_true", help="prompt after each step")
    p.set_defaults(func=cmd_sweep)

    p = add_common(sub.add_parser("release", help="hunt for the OSD handover command"))
    p.set_defaults(func=cmd_release)

    p = add_common(sub.add_parser("experiment", help="run a curated experiment"))
    p.add_argument("name", nargs="?", default="", help="commit | opcodes")
    p.set_defaults(func=cmd_experiment)

    p = sub.add_parser("diff", help="compare two labelled replies in a session")
    p.add_argument("session")
    p.add_argument("left")
    p.add_argument("right")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("survey", help="which offsets move across a session's replies")
    p.add_argument("session")
    p.set_defaults(func=cmd_survey)

    p = sub.add_parser("sessions", help="list recorded sessions")
    p.set_defaults(func=cmd_sessions)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except transport.DeviceNotFound as exc:
        print(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
