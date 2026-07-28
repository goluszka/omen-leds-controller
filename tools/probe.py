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
import sys
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
