# Instructions for AI agents working on omen-led

Read this before touching anything. It contains the facts that cost the most to
establish and the mistakes most likely to be repeated.

## What this is

A decoded USB HID protocol for the HP OMEN 27k monitor's external lighting, plus
a Python package and CLI that drive it. **60 individually addressable LEDs, full
RGB each.** The protocol had no public documentation; it was reverse engineered
here.

Authoritative sources, in order:

1. `docs/protocol.md` — the evidence ledger. Every claim is tagged **CONFIRMED**,
   **HYPOTHESIS** or **REFUTED**. Do not contradict it without new evidence, and
   do not repeat anything in the REFUTED section.
2. `omenled/protocol.py` — the decode as executable code, pure functions only.
3. `data/sessions/*.jsonl` — raw evidence: every packet and every human
   observation from the decoding sessions.

## The protocol in full

You do not need to read the code to use this.

```
Output report: exactly 64 bytes, no report ID, HID usage page 0xFF01
Device:        VID 0x03F0, PID 0x07B0, interface 1

byte  0      opcode
             0x05 = red plane, 0x06 = green, 0x07 = blue, 0x0d = blank all
bytes 1..2   0x00 0x00
byte  3      0xd0   — low nibble is echoed back in the reply status
bytes 4..63  60 intensity bytes, one per LED, for the selected plane

Input report: 64 bytes
byte  0      echoes the commanding opcode
byte  3      status: 0x80 | (request byte3 & 0x0f) = accepted
                     0xf0 | …                      = REJECTED
```

**A frame is three writes, in red → green → blue order.** The device tracks
frame state across them and rejects an out-of-order plane with status `0xf0`,
leaving a partial frame on screen. `0x0d` resets that state.

## Constraints that will bite you

- **The device has no readable state.** Nothing can be queried. The only
  feedback is that status byte. Any process that wants to know what is displayed
  must remember it itself — that is what `omenled/state.py` exists for.
- **Sending anything locks the monitor's OSD lighting menu.** No release command
  was found in nine candidates. Recovery is a USB replug or a monitor reset. Warn
  users of anything you build.
- **Lighting persists after the process exits.** Deliberate. Do not add
  blank-on-exit.
- **hidapi framing:** hidapi wants a leading `0x00` report-ID byte and strips it,
  so a correct 64-byte report is written as **65 bytes** and `hid_write` returns
  65. `omenled/transport.py` enforces this; do not bypass it. This convention is
  hidapi's, **not** the device's — see the native macOS section below.

## Using it

As a library:

```python
from omenled import open_lighting, gradient, LED_COUNT

with open_lighting() as lights:
    lights.set_all(gradient((255, 0, 0), (0, 0, 255), LED_COUNT))
    lights.render()
```

As a subprocess: `omen-led fill '#00ff00'`, `omen-led off`, `--dry-run` on
anything to see the bytes without sending.

Animations keep their pattern in a pure generator yielding
`(pixels, hold_seconds)`, separate from playback. Copy that shape: it makes an
animation testable without hardware, which is otherwise impossible here since
nothing can be read back. `omenled/animations.py` holds the shipped ones;
`examples/pulse.py` and `examples/alarm.py` show writing your own.

`remembered_frame()` returns the last picture set through the CLI, brightness
already applied, for restoring the display after a transient animation. It can
only ever mean the last picture *requested*, not what is physically lit.

For animation, call `render(verify=False)` — the acknowledgement readback costs
~150 ms per plane and is not worth it per frame. There is also a 50 ms minimum
between writes in `safety.RateLimiter`; raise it via `Transport(min_interval=…)`
if you need a higher frame rate, and measure rather than assuming the device
keeps up.

## Building a native macOS app on top

Two viable routes.

**Shell out to the CLI.** Simplest, no HID code, and you inherit the safety
checks. Fine for a menu-bar app that sets colours occasionally. Not for
animation — a process launch per frame is too slow.

**Reimplement in Swift.** The protocol above is all you need; it is about twenty
lines against `IOHIDManager`. Match on `VendorID` `0x03F0` / `ProductID`
`0x07B0`, then:

```swift
IOHIDDeviceSetReport(device, kIOHIDReportTypeOutput, 0, bytes, 64)
```

**Pass exactly 64 bytes with report ID 0 — no leading `0x00`.** The extra byte
is a hidapi convention only. Adding it here shifts every LED by one and produces
a frame that looks almost right, which is the worst kind of bug.

A sandboxed app needs the `com.apple.security.device.usb` entitlement. The
vendor-defined usage page (`0xFF01`) is not a keyboard or pointer, so Input
Monitoring permission should not apply — verify on your target OS version rather
than assuming.

## Rules for changing the protocol layer

**Never conclude a byte is inert without varying it against a background where
its effect would be visible.** This is the single most expensive mistake in this
project's history. The data offset was read wrong three times — 9, then 8, then 4
— because bytes inherited constant values from an old probe packet and were never
varied. A byte pinned to a constant is indistinguishable from a byte with no
effect. The same error hid the colour planes and nearly hid brightness.

Corollary: testing a *modifier* (colour, brightness, mask) against an empty
framebuffer proves nothing. Light the buffer first. `tools/probe.py` has `_lit()`
for exactly this.

**Promote a claim to CONFIRMED only when a prediction made before transmitting
holds.** A model that explains observations already taken is not yet knowledge.

**Do not fuzz opcodes blindly.** This is an ENE microcontroller on a vendor HID
pipe; a blind sweep can in principle reach flash or bootloader paths.
`omenled/safety.py` refuses payloads resembling known unlock magics. Use
`tools/probe.py experiment …`, which is structured and journalled.

**Read the REFUTED section of `docs/protocol.md` before re-testing anything.**
An earlier attempt reached several provably wrong conclusions; they are
catalogued there so they are not rediscovered.

## Verifying your changes

```sh
python3 -m pytest tests/ -q          # 322 tests, no hardware needed
python3 -m omenled.cli --dry-run fill red    # shows exact bytes
```

Hardware behaviour cannot be unit tested — there is no readback. If you change
`protocol.py` or `device.py`, verify on the monitor and record what you saw in
`docs/protocol.md`. An untested protocol change is a guess.

When a change needs a human to look at the LEDs, add an experiment to
`tools/probe.py` rather than asking ad-hoc questions: it journals the answers,
and questions phrased as yes/no have twice collected no evidence here. Ask
"describe what you see", not "did X happen?".

## Still open

- Which LED index maps to which physical position (`omen-led chase` shows it).
- What hands lighting control back to the OSD.
- Whether a hardware brightness control exists
  (`python3 tools/probe.py experiment hwbright`). Brightness is currently
  software scaling, which quantises at low settings.
