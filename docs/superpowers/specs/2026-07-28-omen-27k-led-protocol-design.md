# OMEN 27k External Lighting — Phase 1: Protocol Decode Harness

**Date:** 2026-07-28
**Status:** Design approved
**Scope:** Phase 1 only — decode the USB HID protocol. The user-facing CLI is Phase 2
and gets its own spec, written once we know what the device actually accepts.

## Goal

Produce a documented, evidence-backed description of the packet format the
`OMEN 27k External Light Device` accepts, sufficient to set lighting from macOS.

Success is measured by one thing: we can predict a packet, send it, and the LEDs
do what we predicted. Anything less is a hypothesis, not a result.

## Context

No public decode of this protocol exists. OpenRGB has an open, stalled request for
OMEN 27i monitor lighting with no captures and no implementation. A prior attempt
(Codex) produced ~70 probe scripts and confirmed the device is reachable, but
recorded no durable findings.

### Device facts (verified against live hardware)

| Property | Value |
|---|---|
| Product | `OMEN 27k External Light Device` |
| Manufacturer | `ENE` (the RGB MCU vendor, not HP) |
| VID / PID | `0x03F0` / `0x07B0` |
| HID interface | 1 |
| Usage page / usage | `0xFF01` / `0xA1` (vendor-defined) |

Report descriptor, read live from the `IOHIDInterface` node:

```
0601ff 09a1 a101  09ec 1500 26ff00 7508 9540 8102
                  0911 1500 26ff00 7508 9540 9102
                  0904                9506 b102  c0
```

Which decodes to:

- Input report: **64 bytes**, usage `0xEC`
- Output report: **64 bytes**, usage `0x11`
- Feature report: **6 bytes**, usage `0x04`
- **No report IDs anywhere.**

The absence of report IDs is load-bearing: there is exactly one output report and
it is unnumbered. Any experiment that varies a "report ID" is testing nothing.

### Corrections to the prior attempt's findings

These are recorded because acting on them again would waste effort:

1. **Report IDs `0xE0`, `0x0C`, `0xBB`, `0x3F`, `0xC0` are not this device.** They
   come from the descriptor `0600ff090ba101...`, which belongs to the *Apple
   Internal Keyboard / Trackpad* (VendorID 1452). Writes addressed to them
   returned `-1` because they were aimed at the wrong device.
2. **`WRITE: 65` / `WRITE: 64` are not status codes.** hidapi returns the byte
   count it was handed. It strips a leading `0x00` and transmits the remainder.
   So `write(b"\x00" + 64 bytes)` returns 65 and sends a correct 64-byte report;
   `write(b"\x00" + 63 bytes)` returns 64 and sends a malformed 63-byte one. The
   prior attempt's LED hits were short reports, so its byte offsets are not
   trustworthy.
3. **Its "red" and "blue" readback dumps are byte-identical.** The prior
   attempt's readback never observed state, so nothing derived from those dumps
   is evidence.

### Observations that remain credible

- Writes reach the device. Certain commands make the monitor display an OSD notice
  that lighting is "actively controlled by OMEN Gaming Hub or OMEN Light Studio" —
  i.e. we can enter external-control mode.
- Some commands produce visible LED changes at sub-segment granularity, which means
  the lighting is an addressable matrix, not four fixed RGB zones.
- The device replies with input reports, so the protocol is bidirectional.
- A recurring packet shape appears in both directions:

  ```
  TX  01 00 00 d0 RR GG BB 0f 06 00 03 00...
  RX  01 00 00 80 ec ac 00 00 ...
  RX  80 rr 00 d0 34 05 17 0f 06 00 03      (from a read-style probe)
  ```

  The shared `d0 ... 0f 06 00 03` tail across both directions suggests a common
  record layout. `ec ac 00` recurs regardless of the colour written, so it is more
  likely a fixed device signature than live colour data. Treated as unconfirmed.

## Constraints

- macOS only. No Windows machine or VM, so no USB capture of the official software.
- Local only. No remote resources, no downloading vendor binaries.
- Python 3.9 (`/usr/bin/python3`) with `hid` (cython-hidapi) already installed, and
  Homebrew `hidapi` at `/opt/homebrew/opt/hidapi/lib/libhidapi.dylib`.
- The user is available for hands-on steps at the monitor, but their time is the
  scarcest resource. Every question put to them must be short and specific.

## Method

Two oracles, used for different questions, because neither is sufficient alone.

**The OSD oracle** answers *where the fields are*. The monitor's OSD can set zones
and colours by hand. Set a known state, dump the device's readable state, label it,
diff against other labelled dumps. Field positions fall out of the diffs.

**Visual bisection** answers *what the fields map to physically*. No hex dump can
tell us which byte is "bottom-left" — only the LEDs can. Hold the packet constant,
vary exactly one byte, observe. One variable at a time, always.

### The lock problem

Reading state appears to require a write, and writes trigger the OSD lock, which
disables the very OSD control the first oracle depends on. The loop is therefore:

    OSD set → HID read → locked → clear lock → repeat

so *clearing the lock* is the rate limiter for the whole project, and is also the
undo button. Finding the release command is step 1, before any other experiment.
The official software must release the lock when it exits, so a release path exists.

### Decode sequence

| Step | Action | Exit condition |
|---|---|---|
| 0 | Baseline snapshot, zero writes | Know what is readable without triggering the lock |
| 1 | Find the release command | OSD lighting controls become usable again on demand |
| 2 | Find a state-reflecting read | Some byte provably tracks an OSD colour change |
| 3 | Field map from labelled diffs | Candidate offsets for zone, R, G, B, brightness |
| 4 | Verify by prediction | A predicted packet produces the predicted LED result |
| 5 | Zone map by visual bisection | Byte value → physical segment table |

Step 4 is the gate between hypothesis and fact. A field is only CONFIRMED when a
prediction made *before* sending succeeds.

## Architecture

Four modules with one responsibility each, plus a workbench.

```
omenled/
  journal.py     Append-only JSONL session log
  transport.py   HID I/O — the single chokepoint for bytes on the wire
  safety.py      Guard rails: opcode denylist, rate limit, release-on-exit
  protocol.py    Pure packet build/parse — the decoded knowledge
tools/
  probe.py       RE workbench CLI
docs/
  protocol.md    Living spec: CONFIRMED / HYPOTHESIS / REFUTED
  zones.md       Physical zone map
data/sessions/   *.jsonl
```

### `journal.py`

Append-only JSONL. One record per wire event or observation:

```json
{"ts": "...", "session": "...", "seq": 12, "kind": "tx|rx|note|observation",
 "label": "osd:red:zone1", "hex": "01 00 ...", "note": "..."}
```

This is the project's accumulating asset. The prior attempt's failure was not bad
probing, it was that nothing survived the session. If the work is interrupted, the
journal plus `protocol.md` must be enough to resume without repeating experiments.

### `transport.py`

The only place raw bytes touch the device. Responsibilities:

- Open by VID/PID, selecting the correct interface.
- `write(payload)` — asserts `len(payload) == 64`, prepends `0x00`, asserts hidapi
  returned 65. A short report is now unrepresentable.
- `read(timeout)` — returns the 64-byte input report or `None`.
- Journals every TX and RX automatically, so no experiment can forget to log.
- Context manager; guarantees release-on-exit.

Depends on: `journal`, `safety`. Knows nothing about packet meaning.

### `protocol.py`

Pure functions. No I/O, no device, no globals. Takes and returns bytes and small
dataclasses. Every fact we decode lands here as code, and every fact is covered by
a unit test against a fixture captured in a real journal.

Keeping it pure is what makes the decode testable without hardware, and is what
lets Phase 2's CLI reuse it unchanged.

### `safety.py`

This is an ENE microcontroller on a vendor HID pipe. Blind opcode sweeps can in
principle reach flash or ISP paths. Rules:

- Denylist of opcode/argument patterns associated with ISP and flash unlock
  sequences (`0xA5`/`0x5A`, `0xAA`/`0x55` magics, and similar).
- No unexplored opcode is swept without an explicit override flag.
- Minimum interval between writes.
- Release-on-exit registered via `atexit` as well as the context manager, so a
  crashed experiment does not strand the monitor in software-controlled mode.

### `tools/probe.py`

Subcommands: `info`, `handshake`, `release`, `snapshot <label>`, `diff <a> <b>`,
`sweep --byte N --range A:B`, `replay <seq>`. `sweep` prompts for an observation
after each step and writes it to the journal, so visual findings are captured at
the moment of observation rather than remembered later.

All destructive subcommands support `--dry-run`, printing the packet without
sending it.

## Error handling

- Device absent → named error naming the VID/PID and what to check, not a traceback.
- Write returning anything but 65 → raise. Never silently continue on a short report.
- Read timeout → returns `None`; callers distinguish "no reply" from "empty reply",
  because that distinction is itself protocol evidence.
- Any exit path, including crash → attempt release-of-control, and journal whether
  it succeeded.

## Testing

- `protocol.py`: unit tests, TDD, golden fixtures extracted from real journals.
  Runs without hardware.
- `transport.py`: tested against a fake HID object asserting exact byte sequences,
  in particular the 64→65 framing.
- `safety.py`: unit tests that denied opcodes raise.
- Hardware-dependent tests are marked and opt-in; they are never required to pass
  in a normal run, because the monitor is not always attached.

## Out of scope for Phase 1

Effects, animation, brightness curves, the CLI, the menu bar UI, reactive modes.
Phase 1 ends when `protocol.md` documents a CONFIRMED path to setting a colour on a
known zone, or documents with evidence why that could not be reached.

## Risks

- **The read channel may not reflect state.** If no byte tracks OSD changes, the
  OSD oracle collapses and everything falls to visual bisection, which is far
  slower. Mitigation: step 2 is an explicit go/no-go, run before any effort is
  sunk into diff tooling.
- **The lock may not be clearable in software.** Then every oracle cycle costs a
  USB replug. Mitigation: step 1 establishes the cost early; if it is high, the
  method shifts toward batching many observations per lock cycle.
- **Partial success is a real outcome.** Static per-zone colour may decode while
  effects stay opaque. That is still a usable tool and an acceptable Phase 1 exit.
