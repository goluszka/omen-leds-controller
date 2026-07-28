#!/usr/bin/env python3
"""Reverse-engineering workbench for the OMEN 27k lighting controller.

The protocol is decoded — see `docs/protocol.md`. What remains here are the
tools for the questions still open, plus general-purpose probes for anything
new.

Every subcommand journals what it did to `data/sessions/`, including anything
you type when it asks what you saw. That log is the point: it is what lets a
finding survive an interrupted session.

    info        device facts, transmits nothing
    baseline    what is readable before provoking anything
    query       send one packet, show the reply
    sweep       vary exactly one byte, prompting for observations
    release     hunt for the command that unlocks the OSD
    experiment  curated experiments for the open questions
    diff        compare two labelled replies in a session
    survey      which offsets move across a session's replies
    sessions    list what has been recorded

`info` and `baseline` never transmit. Everything else does, and anything that
transmits locks the monitor's OSD lighting menu until a USB replug or a monitor
reset.

Experiments whose questions were answered have been removed rather than kept as
history: several were written against byte offsets that later proved wrong, and
running one now would produce a confidently wrong result. What they established
is in `docs/protocol.md`; the sessions that produced it are in `data/sessions/`.
"""

from __future__ import annotations

import argparse
import os
import re
import readline  # noqa: F401  — gives input() line editing; an arrow key
# once landed in an answer as "^[[A" and lost an observation
import sys
from typing import Dict, List, Optional

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

# The workbench's journals belong with the repository: they are the evidence
# behind docs/protocol.md, not the user's data.
os.environ.setdefault("OMENLED_DATA_DIR", os.path.join(_REPO, "data"))

from omenled import protocol, safety, transport
from omenled.journal import Journal, hexs, read_session, unhex


# --------------------------------------------------------------------------
# reading what a human tells you
# --------------------------------------------------------------------------

# Phrases meaning "nothing changed". Kept in one place because comparing answer
# strings ad hoc produced three separate false-positive verdicts during the
# decode: matching "red" inside "flickered", "whole" inside "whole diamond
# except…", and reading the literal answer "the same" as movement because it
# differed from the reference *string*.
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

# Colour vocabulary. Anchored at word boundaries rather than matched as
# substrings: "flickered" contains "red", and a substring match reads an
# ambiguous answer as a confirmed colour, manufacturing evidence.
COLOUR_WORDS = {
    "red": r"\b(red\w*|czerwon\w*)",
    "green": r"\b(green\w*|zielon\w*)",
    "blue": r"\b(blue\w*|niebiesk\w*)",
    "white": r"\b(white\w*|bia[lł]\w*)",
    "off": r"\b(off|out|dark\w*|black\w*|zgas\w*|zgaś\w*|wy[lł][aą]cz\w*|ciemn\w*)\b",
    "nothing": r"\b(nothing|no change|no visible|nic|bez zmian)\b",
}

_VERTICAL = {
    "bottom": ("bottom", "lower", "dol", "dół", "doln"),
    "top": ("top", "upper", "gora", "góra", "górn", "gorn"),
}
_HORIZONTAL = {
    "left": ("left", "lew"),
    "right": ("right", "praw"),
}

ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}


def classify(answer: str) -> List[str]:
    """Which outcomes an answer reports. Empty means 'could not tell'.

    Returning nothing is a valid and useful result — the verdicts treat an
    unclassifiable answer as unclear rather than guessing at it.
    """
    low = answer.lower()
    return [name for name, pattern in COLOUR_WORDS.items() if re.search(pattern, low)]


def _first_match(text: str, vocabulary: Dict[str, tuple]) -> Optional[str]:
    """The vocabulary term appearing earliest in the answer.

    Earliest wins because an answer like "bottom left, with a slight part of
    the right bottom segment" names a primary position and then qualifies it;
    the qualifier must not outvote the subject.
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


def lit_report(opcode: int, value: int = 0xFF) -> bytes:
    """A full-brightness LED buffer under an arbitrary opcode.

    Probes must run against a *lit* buffer. Testing a modifier — colour,
    brightness, a mask — against an all-zero frame proves nothing, because a
    modifier with nothing to modify is indistinguishable from a dead byte. That
    error cost this project four passes over the same bytes.
    """
    header = bytes([opcode]) + bytes(protocol.HEADER_TAIL)
    return header + bytes([value] * protocol.LED_COUNT)


def _step(link, journal, label: str, packets: List[bytes], question: str,
          timeout: int) -> str:
    """Send zero or more packets as one unit, then ask what happened."""
    for i, payload in enumerate(packets):
        print(f"  TX: {hexs(payload[:12])}")
        show(link.exchange(payload, timeout_ms=timeout, label=f"{label}:{i}"))
    answer = ask(question)
    journal.observation(answer, label=label)
    return answer


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
    print(f"\n{protocol.LED_COUNT} LEDs, data from byte {protocol.DATA_FIRST}.")
    return 0


def cmd_baseline(args: argparse.Namespace) -> int:
    """What can be read without provoking the device?

    Deliberately transmits nothing. Established once that the device volunteers
    no input reports and refuses feature reads while idle — there is no passive
    state channel. Kept so the finding can be re-checked after a firmware change.
    """
    with Journal() as journal:
        print(f"session: {journal.path}")
        with transport.Transport(journal) as link:
            journal.note("baseline: read-only, no output reports sent")

            print("\nfeature report (6 bytes declared):")
            show(link.get_feature(0, label="baseline:feature"), "feature")

            print("\nunsolicited input reports (2s):")
            got = False
            for _ in range(20):
                payload = link.read(timeout_ms=100, label="baseline:idle")
                if payload:
                    show(payload, "in")
                    got = True
            if not got:
                print("  none — the device does not report state unprompted")
    return 0


# --------------------------------------------------------------------------
# general-purpose probes
# --------------------------------------------------------------------------


def cmd_query(args: argparse.Namespace) -> int:
    """Send one packet and show the reply."""
    payload = (protocol.build(unhex(args.raw)) if args.raw
               else lit_report(args.opcode))

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
                payload, timeout_ms=args.timeout, label=args.label,
                override_safety=args.i_know_what_im_doing,
            )
            show(reply)
            parsed = protocol.parse_reply(reply)
            if parsed:
                print(f"  status: {parsed}")
            if args.observe:
                journal.observation(ask("What did the LEDs do?"), label=args.label)
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Vary exactly one byte, holding everything else fixed.

    One variable at a time is the whole discipline. The base defaults to a lit
    buffer so a modifier has something to modify.
    """
    base = protocol.build(unhex(args.base)) if args.base else lit_report(args.opcode)
    values = parse_range(args.values)
    values = values if args.i_know_what_im_doing else safety.iter_safe(values)

    if not values:
        print("Every value in that range is on the refusal list.")
        return 2

    role = ("header" if args.byte < protocol.DATA_FIRST
            else f"LED {args.byte - protocol.DATA_FIRST}")
    print(f"base: {hexs(base[:16])}")
    print(f"varying byte {args.byte} over {len(values)} values  ({role})")

    with Journal() as journal:
        print(f"session: {journal.path}\n")
        with transport.Transport(journal) as link:
            journal.note(f"sweep byte {args.byte}; base {hexs(base)}")
            for value in values:
                label = f"sweep:b{args.byte}={value:02x}"
                payload = protocol.with_byte(base, args.byte, value)
                print(f"byte[{args.byte}] = {value:02x}")
                show(link.exchange(payload, timeout_ms=args.timeout, label=label,
                                   override_safety=args.i_know_what_im_doing,
                                   dry_run=args.dry_run))
                if args.observe and not args.dry_run:
                    journal.observation(ask("LEDs?"), label=label)
    return 0


# --------------------------------------------------------------------------
# open question: handing control back to the OSD
# --------------------------------------------------------------------------


def _yes(answer: str) -> bool:
    return answer.lower().startswith(("y", "t"))  # y/yes, t/tak


LOCK_TRIGGER = lit_report(protocol.PLANE_RED)


def release_candidates() -> List[tuple]:
    """Opcodes to try, excluding the four whose meaning is already known."""
    known = {protocol.PLANE_RED, protocol.PLANE_GREEN,
             protocol.PLANE_BLUE, protocol.OP_OFF}
    return [(f"opcode {op:02x}", lit_report(op, value=0x00))
            for op in safety.iter_safe(range(0x00, 0x20)) if op not in known]


def cmd_release(args: argparse.Namespace) -> int:
    """Hunt for the command that hands lighting control back to the OSD.

    Nine candidates have been tried and rejected — see docs/protocol.md. This
    is kept so new ideas can be tested the same way: the lock is established
    deliberately first, and any apparent hit must clear a freshly re-established
    lock a second time before it is recorded. A single lucky observation is not
    evidence.
    """
    candidates = release_candidates()

    with Journal() as journal:
        print(f"session: {journal.path}\n")
        with transport.Transport(journal) as link:

            def establish_lock() -> bool:
                if _yes(ask("Is the OSD lighting menu locked right now? (y/n)")):
                    return True
                print("  locking it deliberately so there is something to release...")
                link.exchange(LOCK_TRIGGER, timeout_ms=args.timeout, label="lock:trigger")
                locked = _yes(ask("Locked now? (y/n)"))
                journal.observation(
                    f"lock trigger {'worked' if locked else 'did NOT lock the OSD'}",
                    label="lock:trigger")
                return locked

            if not establish_lock():
                print("\n  Cannot test release commands without a lock to release.")
                return 1

            print(f"\nTrying {len(candidates)} candidates. After each, check whether")
            print("the OSD notice cleared and the lighting menu responds again.\n")

            for name, payload in candidates:
                label = f"release:{name}"
                print(f"{name}: {hexs(payload[:8])}")
                show(link.exchange(payload, timeout_ms=args.timeout, label=label))
                answer = ask("OSD unlocked? (y / n / describe)")
                journal.observation(answer, label=label)
                if not _yes(answer):
                    continue

                print(f"\n  {name} looks like a hit. Confirming it repeats.")
                link.exchange(LOCK_TRIGGER, timeout_ms=args.timeout,
                              label="lock:reconfirm")
                if not _yes(ask("Locked again? (y/n)")):
                    print("  Could not re-lock, so the result is unproven. Continuing.")
                    continue
                link.exchange(payload, timeout_ms=args.timeout, label=f"{label}:confirm")
                if _yes(ask("Unlocked again? (y/n)")):
                    print(f"\nCONFIRMED release command: {name}")
                    journal.note(f"RELEASE COMMAND CONFIRMED: {hexs(payload)}")
                    return 0
                print("  Did not repeat — coincidence, not a release. Continuing.")

    print("\nNo candidate worked. USB replug or a monitor reset stays the reset.")
    return 1


# --------------------------------------------------------------------------
# curated experiments for the open questions
# --------------------------------------------------------------------------

KNOWN_OPCODES = {0x05: "red plane", 0x06: "green plane",
                 0x07: "blue plane", 0x0D: "blank all"}


def exp_opcodes(link, journal, timeout: int) -> Dict[str, str]:
    """Which byte-0 values do anything, beyond the four already known?

    Run against a lit buffer, so an opcode that alters colour, brightness or
    extent shows rather than hiding behind an empty frame.
    """
    blank = protocol.off_packet()
    answers: Dict[str, str] = {}

    opcodes = safety.iter_safe(range(0x00, 0x21))
    print(f"Testing {len(opcodes)} opcodes with a full-brightness buffer.")
    print("Say what you see, or press Enter for no change.\n")
    for opcode in opcodes:
        note = f"  ({KNOWN_OPCODES[opcode]}, known)" if opcode in KNOWN_OPCODES else ""
        print(f"opcode {opcode:02x}{note}")
        answers[f"{opcode:02x}"] = _step(
            link, journal, f"opcodes:{opcode:02x}",
            [blank, lit_report(opcode)], "LEDs?", timeout)
    return answers


def verdict_opcodes(answers: Dict[str, str]) -> str:
    known = {f"{op:02x}" for op in KNOWN_OPCODES}
    live = {op: ans for op, ans in answers.items()
            if ans and classify(ans) and "nothing" not in classify(ans)}
    new = {op: ans for op, ans in live.items() if op not in known}

    lines = ["opcodes that changed the lighting:"]
    for op, ans in live.items():
        lines.append(f"  {op}{'  (known)' if op in known else ''}  {ans}")
    if not live:
        lines.append("  none")
    lines.append("")
    if new:
        lines.append(f"NEW: {', '.join(sorted(new))} — not previously documented.")
        lines.append("Record them in docs/protocol.md, then work out what each does.")
    else:
        lines.append("Nothing beyond the four already documented.")
    return "\n".join(lines)


def exp_hwbright(link, journal, timeout: int) -> Dict[str, str]:
    """Is there a hardware brightness control in the header?

    Bytes 1 and 2 light nothing on their own, but that was measured with every
    pixel at zero — and a global multiplier applied to an all-black frame does
    nothing visible either. The frame here sits at half intensity so a change in
    either direction shows.
    """
    blank = protocol.off_packet()
    answers: Dict[str, str] = {}

    def frame(offset: Optional[int] = None, value: int = 0x00) -> List[bytes]:
        out = []
        for plane in protocol.PLANES:
            packet = lit_report(plane, value=0x80)
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
            "  omen-led brightness 40",
            "",
            "The only cost is precision at very low settings, where 8-bit",
            "channels quantise — 10% of a mid grey is a handful of levels.",
        ]
    return "\n".join(lines)


EXPERIMENTS = {
    "hwbright": (exp_hwbright, verdict_hwbright,
                 "is there a hardware brightness byte in the header?"),
    "opcodes": (exp_opcodes, verdict_opcodes,
                "do any opcodes beyond the known four do anything?"),
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
        journal.note(f"verdict({args.name}): {text}")

    print("\n" + "=" * 60)
    print(text)
    print("=" * 60)
    return 0


# --------------------------------------------------------------------------
# analysis over journals
# --------------------------------------------------------------------------


def _collect(session_path: str, kind: str = "rx") -> Dict[str, bytes]:
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

    def transmitting(p):
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

    p = transmitting(sub.add_parser("query", help="send one packet, show the reply"))
    p.add_argument("--raw", help='full packet as hex, e.g. "05 00 00 d0 ff ff"')
    p.add_argument("--opcode", type=parse_byte, default=protocol.PLANE_RED)
    p.add_argument("--label", default="query")
    p.add_argument("--observe", action="store_true", help="prompt for what the LEDs did")
    p.set_defaults(func=cmd_query)

    p = transmitting(sub.add_parser("sweep", help="vary exactly one byte"))
    p.add_argument("--byte", type=int, required=True, help="offset to vary")
    p.add_argument("--values", required=True, help='"0x00:0x0f" or "0,1,5"')
    p.add_argument("--base", help="base packet as hex; defaults to a lit buffer")
    p.add_argument("--opcode", type=parse_byte, default=protocol.PLANE_RED)
    p.add_argument("--observe", action="store_true", help="prompt after each step")
    p.set_defaults(func=cmd_sweep)

    p = transmitting(sub.add_parser("release", help="hunt for the OSD handover command"))
    p.set_defaults(func=cmd_release)

    p = transmitting(sub.add_parser("experiment", help="run a curated experiment"))
    p.add_argument("name", nargs="?", default="", help=" | ".join(EXPERIMENTS))
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
