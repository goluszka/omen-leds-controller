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


EXPERIMENTS = {
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
