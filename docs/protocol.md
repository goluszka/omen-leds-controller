# OMEN 27k External Light Device — protocol notes

Evidence ledger. Every entry is tagged:

- **CONFIRMED** — either a prediction made *before* transmitting that held, or an
  invariance that survived many deliberately varied packets.
- **HYPOTHESIS** — consistent with what we have seen, not yet predictive.
- **REFUTED** — tested, found false. Kept so it is not retried.

Last updated: 2026-07-28, after session `20260728-234835`.

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

### `0x0d` either blanks the lighting or commits staged state

`0d 00 00 d0 00 00 00 0f 06 00 03` turned the LEDs out — the first reproducible
LED effect observed under correct 64-byte framing, and consistent with the prior
attempt's unlogged claim that `05` lights and `0d` extinguishes.

Two readings fit the evidence equally well:

1. `0x0d` is an **off** command and ignores the RGB bytes.
2. `0x0d` is a **commit**, and it applied the black that seven preceding `0x01`
   packets — all carrying RGB `00 00 00` — had been staging without visible
   effect.

The distinction decides the entire write model, so it is resolved first:
`probe.py experiment commit`.

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

None cleared the OSD lock. USB replug remains the only known reset. The release
mechanism is likely a distinct opcode, or tied to the USB session rather than to
any packet.

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

## Open questions, in the order they block progress

1. Is `0x0d` an off command or a commit? (`probe.py experiment commit`)
2. Do the RGB bytes at 4–6 control colour under any opcode?
3. Which opcodes light anything at all? (`probe.py experiment opcodes`)
4. Which byte selects the zone, and what is the physical zone map?
5. What hands control back to the OSD?
6. Is brightness a separate field or premultiplied into RGB?
