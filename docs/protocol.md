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

### Reply byte 0 echoes an opcode

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

## Open questions, in the order they block progress

1. Is an animated effect repainting over everything we write?
   (`probe.py experiment animation`) — blocks all visual decoding.
2. With a static, non-red OSD colour set: does any byte change the lighting?
3. Which byte selects the segment, and how many segments exist?
4. What do offsets 4–6 encode, if anything?
5. Which other opcodes do anything? (`probe.py experiment opcodes`)
6. What hands control back to the OSD?

Bytes past offset 10 have never been varied. Given the 64-byte report and the
finding that offsets 1 and 4–6 appear inert, the addressing may well live
there.
