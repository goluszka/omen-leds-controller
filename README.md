# omen-led

Software control of the HP OMEN 27k monitor's external lighting, from macOS.

No public decode of this protocol existed when this started. The protocol is now
decoded and the CLI works: **60 individually addressable LEDs, full RGB each.**

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

## Controlling the lighting

```sh
python3 -m omenled.cli fill '#00ff00'      # whole diamond green
python3 -m omenled.cli set 0 5 10 red      # specific LEDs
python3 -m omenled.cli gradient red blue   # fade across the strip
python3 -m omenled.cli brightness 40       # re-dim the current picture
python3 -m omenled.cli chase               # walk one LED, maps index to position
python3 -m omenled.cli off
```

60 LEDs, full RGB each. Brightness is applied in software; the last frame is
remembered at full intensity, so `brightness` re-dims the same picture rather
than compounding.

## Using the workbench

```sh
python3 tools/probe.py info        # device facts, transmits nothing
python3 tools/probe.py baseline    # what is readable before provoking anything
python3 tools/probe.py experiment  # list the curated experiments
python3 tools/probe.py sessions    # what has been recorded
```

`info` and `baseline` never transmit. Everything else can put the monitor into
software-controlled mode, which locks the OSD lighting menu until control is
handed back — or, until the release command is known, until the USB cable is
replugged or the monitor reset.

Add `--dry-run` to any command to see the exact bytes without sending them.

## Requirements

Python 3.9+ with `hid` (cython-hidapi), and Homebrew `hidapi`. Both are already
present on this machine.

```sh
python3 -m pytest tests/ -q      # 347 tests, no hardware needed
```
