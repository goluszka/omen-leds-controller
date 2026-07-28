# OMEN 27k External Light Device — protocol notes

Evidence ledger. Every entry is tagged:

- **CONFIRMED** — either a prediction made *before* transmitting that held, or an
  invariance that survived many deliberately varied packets.
- **HYPOTHESIS** — consistent with what we have seen, not yet predictive.
- **REFUTED** — tested, found false. Kept so it is not retried.

Last updated: 2026-07-29, after the colour and zones sessions.

## CONFIRMED

### Device identity and framing

| | |
|---|---|
| Product | `OMEN 27k External Light Device` |
| Manufacturer | `ENE` |
| VID:PID | `03F0:07B0` |
| Serial | `(redacted)` |
| Interface | 1, usage page `0xFF01`, usage `0xA1` |

Report descriptor read live from the `IOHIDInterface` node
(`ioreg -c IOHIDInterface -r -l`):

```
0601ff 09a1 a101  09ec 1500 26ff00 7508 9540 8102
                  0911 1500 26ff00 7508 9540 9102
                  0904                9506 b102  c0
```

- Input report: 64 bytes, usage `0xEC`
- Output report: 64 bytes, usage `0x11`
- Feature report: 6 bytes, usage `0x04`
- **No report IDs.** One unnumbered output report, and that is all.

### hidapi framing

hidapi wants a leading report-ID byte and strips it when it is `0x00`. A correct
64-byte report is therefore written as 65 bytes, and `hid_write` returns 65.

```
write(b"\x00" + 64 bytes)  -> returns 65 -> device receives 64   correct
write(b"\x00" + 63 bytes)  -> returns 64 -> device receives 63   malformed
```

`omenled.transport.Transport.write` enforces this and raises on anything else.

### There is no passive state channel

`probe.py baseline`. With no output report sent at all: `get_feature_report`
fails (`read error`) while idle, and no unsolicited input reports arrive over a
2-second window.

**Consequence:** reading state requires writing first, so the OSD lock is
unavoidable during any read-based decode.

### The lock trigger

```
01 00 00 d0 ff 00 00 0f 06 00 03 00...
```

Predicted to put the monitor into software-controlled mode; it did. The OSD then
displays the "controlled by OMEN Gaming Hub or OMEN Light Studio" notice and the
OSD lighting menu stops responding.

### The reply is a fixed acknowledgement, not device state

Across nine packets varying byte 0, the marker at byte 3, the mode at byte 10,
the effect at byte 9, and the RGB bytes at 4–6, **every reply was identical
except for byte 0**:

```
<opcode echo> 00 00 80 ec ac 00 00 00 ...
```

The reply carries no colour, no zone, and no mode. It is an ACK with a device
signature, and nothing more.

**Consequence — this is the important one:** the read channel cannot serve as an
oracle. The design's step-2 go/no-go fails here, so the decode falls entirely to
visual bisection with a human observer. Plan effort accordingly.

### `0x0d` blanks the lighting

`probe.py experiment commit`, 2026-07-28, plus 13 further uses as the blanking
step of later experiments:

| Packet | Result |
|---|---|
| `0d 00 00 d0 ff 00 00 0f 06 00 03` | all off |
| `0d 00 00 d0 00 ff 00 0f 06 00 03` | all off |
| `01 …0000ff…` then `0d …000000…` | all off |

`0x0d` blanks regardless of what offsets 4–6 contain, so it is an **off**
command, not the commit half of a stage/commit pair. The staging hypothesis is
refuted: blue staged with `0x01` did not survive a black `0x0d`.

This one is safe from the contamination described below — an animation cannot
explain the lighting going *dark* on command, and it did so every time.

### `0x05` writes red to the bottom-left region, and does not restore OSD state

`05 00 00 d0 ff ff ff 0f 06 00 03` lights the bottom-left region red after a
`0x0d` blank, in every trial.

**The OSD had been set to static green before session `20260729-000931`, and
`0x05` produced red.** So `0x05` is not "lighting on" restoring the monitor's
own configuration — it writes a colour of its own. Our packets do reach the
lighting.

What selects that red, and that position, is unknown. Under `0x05` the only
bytes ever varied are byte 1 and offsets 4–6, all of which are inert. Bytes 2,
3, 7, 8, 9 and 10 have been held constant at `00 d0 … 0f 06 00 03` throughout,
and bytes 11–63 have never been anything but zero. The selector is among those.

### The packet tail is a pixel buffer, not a set of fields

`probe.py experiment bisect`, 2026-07-29. Filling byte ranges with `0xff` under
opcode `0x05`:

| Bytes set to `ff` | Lit area |
|---|---|
| 1–63 | whole diamond |
| 1–32 | whole diamond except the top-left segment |
| 1–16 | both bottom segments, top off |
| **1–8** | **identical to the reference** |
| 9–12 | bottom centre |
| 11 | bottom-left, bottom-centre dimmer, part of bottom-right |

The lit area grows with the *number* of bytes set, which is the signature of a
per-LED buffer rather than a parameter field. Bytes 1–8 are inert, so the report
is a **9-byte header followed by LED data from byte 9 to byte 63**.

This also explains the earlier dead results rather than contradicting them:
offsets 4–6 and byte 1 are inside the header and genuinely do nothing, and the
buffer behind them was empty in every one of those trials, so there was nothing
for a colour field to colour.

The lighting is physically a **diamond** with four segments — top-left,
top-right, bottom-left, bottom-right.

### The LED buffer is one byte per LED, not RGB triplets

`probe.py experiment stream`, 2026-07-29. A repeating `00 ff 00` pattern written
from byte 9, byte 10 and byte 11 in turn produced **red at every alignment**.
A triplet stream would have produced green at one of them.

A uniform `0x40` fill from byte 10 looked *brighter* than the `00 ff 00`
pattern. Sparse-bright loses to uniform-dim, which is what one byte per LED
looks like and not what grouped triplets look like.

So bytes 9–63 are **55 single-byte LED values**, and every one of them so far
has lit red.

### THE WRITE PATH — opcode selects the colour plane

`probe.py experiment hunt`, 2026-07-29. The identical lit buffer under four
neighbouring opcodes:

| Opcode | Result |
|---|---|
| `0x05` | red, bright |
| `0x06` | green, bright |
| `0x07` | blue, bright |
| `0x04` | nothing |
| `0x08` | nothing |
| `0x0d` | blank |

**The opcode selects which colour plane the intensity bytes apply to.** Writing
all three planes composes an arbitrary colour per LED. This is why every earlier
observation was red: we had only ever used `0x05`.

Full report layout:

```
byte  0      opcode: 05 = red plane, 06 = green, 07 = blue, 0d = blank all
byte  1      chunk index (candidate — see below)
bytes 2..8   00 d0 00 00 00 0f 06   held constant on every accepted write
bytes 9..63  55 intensity bytes, one per LED, for the selected plane
```

Implemented as `omenled.protocol.plane_packet` and `frame_packets`.

### Planes must be written red, green, blue — reply byte 3 is a status

`probe.py experiment confirm`, 2026-07-29.

| Sequence | Reply to the last write | Result |
|---|---|---|
| `0d, 05, 06, 07` | `80` | **white** — as predicted |
| `0d, 05, 07` (green skipped) | **`f0`** | garbled |
| `05, 07` (green skipped) | **`f0`** | garbled |
| `0d, 07` alone | `80` | blue |

Reply byte 3 is a status code: **`0x80` accepted, `0xf0` rejected.** The full
rejected reply is `<opcode> 00 00 f0 ec fa 01`, against `<opcode> 00 00 80 ec ac
00` when accepted.

The device tracks frame state across the three plane writes. `0x05` opens a
frame, `0x06` continues it, `0x07` closes it; a plane out of sequence is
rejected and the LEDs keep a partial frame. `0x0d` resets that state, which is
why `0d` followed by `07` alone is accepted.

This status byte is the only feedback the device gives — there is no readable
state — so `omenled.device.Lighting.render` checks it on every plane and raises
`RenderRejected` rather than leaving a half-written frame on screen.

### CONFIRMED by prediction: planes are independent and compose

Two trials, both predicted before transmitting:

- `0d` then all three planes with LED 0 at full → **one white LED**. Composition
  works.
- A full-brightness write to the **red plane only** → everything red *except*
  LED 0, which stayed **white**, holding the green and blue set by the previous
  trial. Planes are independent buffers that persist until overwritten.

### Byte 7 is not a segment mask; byte 1 is ignored

`probe.py experiment tail`, 2026-07-29. Every trial returned the identical
observation — "whole diamond except of middle led in left bottom segment":

| Trial | Result |
|---|---|
| `off` | **fully dark** — nothing is stranded outside our reach |
| full white, header as usual | one LED dark |
| byte 7 = `3f` | unchanged |
| byte 7 = `ff` | unchanged |
| chunk index = 1 | unchanged |

So byte 7 is not a segment write-mask, and byte 1 is ignored entirely — a second
independent finding that it is not a chunk index. And because `off` blanks
everything, no part of the panel is unaddressable in principle.

What remains is **exactly one LED**, mid bottom-left, that never lights.

### CONFIRMED: the LED data starts at byte 4 — 60 LEDs, whole diamond

`probe.py experiment headerleds`, 2026-07-29. Each of bytes 1..7 set alone to
full brightness with every other byte zeroed:

| Byte | Result |
|---|---|
| 1 | nothing lights |
| 2 | nothing lights |
| 3 | nothing lights (status echoes its low nibble — see below) |
| **4** | one LED, bottom-left segment, middle |
| **5** | one LED, slightly right of it |
| **6** | one LED, further right |
| **7** | one LED, further right again |

Bytes 4..7 each light one LED, stepping along the strip. Then `0xff` across
**bytes 1..63** lit the diamond **completely white with no gaps** — the
confirmation that 60 LEDs is the entire strip.

Final layout:

```
byte  0      opcode: 05 red plane, 06 green, 07 blue, 0d blank all
bytes 1..2   00 00        header
byte  3      d0           header; low nibble echoed in the reply status
bytes 4..63  60 intensity bytes, one per LED, for the selected plane
```

`DATA_FIRST = 4`, `LED_COUNT = 60`.

### Why this took four attempts, and the lesson

The data offset was read as 9, then 8, then 4. Every wrong reading came from the
same mistake: **a byte held at a constant is indistinguishable from a byte with
no effect.** Bytes 4..7 were inherited from an old probe packet as
`00 00 00 0f` and never varied, so every experiment concluded they were inert
header — while they were in fact LEDs 0..3, pinned dark and dim.

Those constants were the visible faults reported from the panel: LEDs 0..2 held
at `0x00` were the permanently dark spot, and LED 3 held at `0x0f` was the dim
patch that never took a colour.

The fix that finally worked was not a cleverer sweep but a different question —
"set this byte alone and tell me if *anything* lights" — plus a description of
what the wrong pixels physically looked like. Ruling a byte inert requires
varying it against a background where its effect would be visible; nothing else
counts as evidence.

### Reply byte 3 carries a request tag

Sending byte 3 = `0xd1` instead of `0xd0` changed the reply status from `0x80`
to `0x81`. The low nibble of the request's byte 3 is echoed in the reply's
status byte:

```
status = 0x80 | (request_byte3 & 0x0f)     accepted
         0xf0 | …                          rejected
```

Not needed for control, but it means a request can be tagged and its
acknowledgement matched to it — useful if writes are ever pipelined.

### Reply byte 0 echoes an opcode### Reply byte 0 echoes an opcode

| TX byte 0 | RX byte 0 |
|---|---|
| `00` | `01` |
| `00` | `01` |
| `01` | `01` |
| `02` | `02` |
| `0d` | `0d` |

Byte 0 of the reply tracks byte 0 of the command, giving a cheap check that a
command was received at all.

## HYPOTHESIS

### Opcode `0x00` is declined, and the echo reports the last *accepted* opcode

Two consecutive commands with byte 0 = `00` both drew a reply of `01`, which was
the opcode before them. That fits an interpretation where `0x00` is a no-op the
device refuses and the echo holds the last command it accepted.

If true, the echo is a validity oracle: send opcode N, and if the reply does not
echo N, the device rejected it. That would let opcode discovery run without a
human watching the LEDs, which given the finding above would be worth a great
deal.

**Test:** send `02`, then `00`, then read. If the reply echoes `02`, confirmed.

### Observation contamination — ruled out

Session of 2026-07-29 ran `experiment colour` and `experiment zones`. Combined
result, 13 trials:

| Varied | Values | Result |
|---|---|---|
| offsets 4–6 | `ff0000`, `00ff00`, `0000ff`, `ffffff`, `000000` | red, bottom-left, every time |
| byte 1 | `00`–`07` | red, bottom-left, every time |

The lighting was **red in all 13 trials, including with colour bytes all zero**,
and never went dark. Only the size and position of the lit region drifted, by
small amounts, in no relation to what was sent.

The suspected explanation was a running animation repainting over our writes,
which would have made every answer a snapshot of the effect rather than a
response to a packet.

**`probe.py experiment animation`, session `20260729-000931`, refutes it.** With
no packets sent, the lighting did not move over 15 seconds; after `0x05` it did
not change over a further 10 seconds; `0x0d` blanked it and it stayed blank.
The lighting is static and observations are attributable.

So the results stand, and they say something sharper than contamination would
have: **offsets 4–6 and byte 1 genuinely have no effect under `0x05`.** The
small drifts in described size and position across trials were descriptive
noise, not movement.

**Process note:** the animation question was raised before that batch and the
batch went ahead without settling it. It cost 13 observations that could not be
interpreted until the control ran. Validate the observation channel *before*
spending observations through it.

### Offsets 4–6 are not a plain RGB triple

```
05 00 00 d0 ff ff ff 0f 06 00 03  ->  bottom-left segment lit RED
```

White in, red out. Whatever offsets 4–6 encode, it is not R, G, B in that order.
Candidate readings still open: a different channel ordering, inverted channels,
or an index into a palette rather than a colour at all. `probe.py experiment
colour` eliminates them by trial.

Note the prior attempt's claim that `05` with `ff 00 00` produced *white* comes
from `legacy/omen_find_led_offset.py`, which sent 63-byte reports and also
overwrote one byte per iteration. It is not evidence and is not used here.

### Packet layout

From the packet shape that produced both a reply and a visible LED change:

| Offset | Guess | Evidence |
|---|---|---|
| 0 | opcode | `01`, `02`, `0d` all echo; `0d` extinguished the LEDs |
| 1 | index / zone | untested |
| 2 | sub-index | almost always `00` |
| 3 | marker | `d0` outbound; replies carry `80` here |
| 4–6 | R, G, B | position of `ff 00 00` in a packet that lit LEDs |
| 7 | brightness | held at `0f` (= 15) throughout |
| 8 | count | held at `06` — possibly six zones |
| 9 | effect | varying it to `ff` changed nothing visible |
| 10 | mode / apply | varying it over `00`,`01`,`02` changed nothing visible |

Offsets 9 and 10 producing no visible change is weak evidence they are inert in
this context, not that they are meaningless.

### Addressing is finer than four zones

Reported observation from the prior attempt: sub-segments within a zone took
individual colours, and red sub-segments appeared to travel toward the
bottom-right segment. Not independently reproduced, and recorded under
untrustworthy framing.

## REFUTED

### `ec ac 00` is not colour data

It appears at offsets 4–6 of **every** reply regardless of the RGB values sent,
including replies to all-zero packets. A fixed signature or status constant.

### Nine release candidates do not hand control back

Session `20260728-234835`, all with the OSD confirmed locked beforehand:

| Candidate | Packet |
|---|---|
| all zero | `00 00 00 00 …` |
| opcode 00, keep tail | `00 00 00 d0 00 00 00 0f 06 00 03` |
| marker 00 | `01 00 00 00 00 00 00 0f 06 00 03` |
| mode 00 | `01 00 00 d0 00 00 00 0f 06 00 00` |
| mode 01 | `01 00 00 d0 00 00 00 0f 06 00 01` |
| mode 02 | `01 00 00 d0 00 00 00 0f 06 00 02` |
| effect ff | `01 00 00 d0 00 00 00 0f 06 ff 03` |
| opcode 02 | `02 00 00 d0 00 00 00 0f 06 00 03` |
| opcode 0d | `0d 00 00 d0 00 00 00 0f 06 00 03` |

None cleared the OSD lock. The release mechanism is likely a distinct opcode, or
tied to the USB session rather than to any packet.

Two resets are known to work, both out-of-band: USB replug, and a monitor reset
from the OSD (reported 2026-07-28, which also restored the lighting to its
default on-state). Neither is a protocol finding — they are the fallback while
the release command remains unknown.

### Report IDs `0xE0`, `0x0C`, `0xBB`, `0x3F`, `0xC0`

Not this device. They come from descriptor `0600ff090ba101…85e0…850c…85bb…`,
which belongs to the **Apple Internal Keyboard / Trackpad** (VendorID 1452,
visible in `legacy/hid_omen.txt`). Writes aimed at them returned `-1` because
they addressed the wrong device. This monitor has no report IDs at all.

### `WRITE: 65` / `WRITE: 64` as status codes

Byte counts echoed by hidapi, not device responses. See framing above.

### The prior attempt's colour dumps

`legacy/red.txt` and `legacy/blue.txt` are byte-identical, as are
`legacy/red_dump.txt` and `legacy/blue_dump.txt`. Those reads never observed
state, so nothing derived from them is evidence.

### `0x01` does nothing visible

Every `0x01` packet sent so far — varying marker, mode, effect and RGB — changed
the lighting in no observable way, while `0x05` and `0x0d` both did. `0x01` is
accepted (it echoes) but its effect, if any, is not visual.

## Brightness

Implemented in software: `omenled.protocol.scale` multiplies the intensities and
`omenled.cli brightness <0-100>` re-renders the remembered frame. The frame is
stored unscaled so repeated dimming does not compound.

**Hardware brightness is untested, not absent.** Bytes 1 and 2 light nothing on
their own, but that was measured with every pixel at zero — and a global
multiplier applied to an all-black frame is equally invisible. This is the same
empty-buffer trap that hid the pixel bytes for four passes, so those bytes are
not ruled out. `probe.py experiment hwbright` retests them against a lit frame.

If a hardware control exists it is worth preferring: it keeps the full 8 bits
per channel, where software scaling quantises at low settings.

## Open questions

The write path is complete: 60 LEDs, full RGB each, whole diamond addressable.
What remains is refinement.

1. Which LED index maps to which physical position? (`omenled.cli chase`)
   Needed before named zones like `--zone bottom-left` are possible.
2. What hands control back to the OSD? Still unknown after nine candidates.
   USB replug or a monitor reset are the only known clears.
3. Is there a hardware brightness byte? (`experiment hwbright`)
4. Do any other opcodes do anything? `experiment opcodes` has never been run.
