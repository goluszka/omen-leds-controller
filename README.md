# omen-led

Software control of the HP OMEN 27k monitor's external lighting, from macOS.

No public decode of this protocol exists, so the work is in two phases.
**Phase 1 (current): decode the protocol.** Phase 2: the CLI.

## Where things stand

Read [`docs/protocol.md`](docs/protocol.md) first — it is the evidence ledger,
and every claim in it is tagged CONFIRMED, HYPOTHESIS, or REFUTED. Design and
method are in [`docs/superpowers/specs/`](docs/superpowers/specs/).

If you are resuming after a break, `docs/protocol.md` plus the journals in
`data/sessions/` should be enough to continue without repeating any experiment.
That is the whole reason they exist.

## Layout

```
omenled/
  transport.py   HID I/O. The only place raw bytes reach the device, and the
                 only place report framing is handled.
  protocol.py    Pure packet build/parse. No I/O, so it is testable without
                 the monitor attached.
  safety.py      Refusal list, rate limit, release-on-exit.
  journal.py     Append-only JSONL session log.
tools/probe.py   Reverse-engineering workbench.
legacy/          Earlier exploratory scripts, kept for reference. See the
                 REFUTED section of docs/protocol.md before trusting any of it.
```

## Using the workbench

```sh
python3 tools/probe.py info        # device facts, transmits nothing
python3 tools/probe.py baseline    # what is readable before provoking anything
python3 tools/probe.py release     # hunt for the OSD handover command
python3 tools/probe.py query -r 255 --observe
python3 tools/probe.py sweep --byte 4 --values 0x00:0xff --observe
python3 tools/probe.py sessions    # what has been recorded
```

`info` and `baseline` never transmit. Everything else can put the monitor into
software-controlled mode, which locks the OSD lighting menu until control is
handed back — or, until the release command is known, until the USB cable is
replugged.

Add `--dry-run` to any transmitting command to see the packet without sending it.

## Requirements

Python 3.9+ with `hid` (cython-hidapi), and Homebrew `hidapi`. Both are already
present on this machine.

```sh
python3 -m pytest tests/ -q      # 72 tests, no hardware needed
```
