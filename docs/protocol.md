# OMEN 27k External Light Device — protocol notes

Evidence ledger. Every entry is tagged:

- **CONFIRMED** — a prediction was made *before* transmitting, and it held.
- **HYPOTHESIS** — consistent with what we have seen, not yet predictive.
- **REFUTED** — tested, found false. Kept so it is not retried.

Last updated: 2026-07-28

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

`omenled.transport.Transport.write` enforces this and raises on anything else, so
a short report cannot be sent by accident. Verified by unit test.

### There is no passive state channel

Run: `probe.py baseline`, 2026-07-28. With no output report sent at all:

- `get_feature_report` fails (`read error`) while the device is idle.
- No unsolicited input reports arrive over a 2-second window.

**Consequence:** reading device state requires writing first. Since writes
provoke the OSD lock, the lock is unavoidable in any read-based decode, which is
why finding the release command is sequenced first.

## HYPOTHESIS

### Packet layout

From the one exchange the prior attempt recorded that produced both a reply and a
visible LED change:

```
TX  01 00 00 d0 ff 00 00 0f 06 00 03 00...
RX  01 00 00 80 ec ac 00 00 ...
```

and a read-style probe's reply:

```
RX  80 rr 00 d0 34 05 17 0f 06 00 03
```

The `d0 … 0f 06 00 03` tail appears in both directions, suggesting a single
record layout shared by set and report.

| Offset | Guess | Evidence |
|---|---|---|
| 0 | opcode | `01` on set; `80` leads read-style replies |
| 1 | index / zone | varied by prior read sweeps, echoed in replies |
| 2 | sub-index | almost always `00`; one reply carried `29` |
| 3 | record marker | `d0` outbound; one reply carried `80` here instead |
| 4–6 | R, G, B | position of `ff 00 00` in a packet that lit LEDs |
| 7 | brightness | constant `0f` (= 15) |
| 8 | count | constant `06` — possibly six zones |
| 9 | effect | constant `00` |
| 10 | mode / apply | constant `03` |

None of this is confirmed. No prediction has yet been made and verified.

### Opcodes that did something

Prior attempt, unlogged, offsets untrustworthy because the reports were 63 bytes:

- `05` — lit the bottom-left segment white
- `0d` — turned that segment off

Worth retesting with correct 64-byte framing before drawing conclusions.

### Addressing is finer than four zones

Reported observation: sub-segments within a zone took individual colours, and red
sub-segments appeared to travel toward the bottom-right segment. Suggests an
addressable matrix rather than fixed RGB zones. Not independently reproduced.

## REFUTED

### Report IDs `0xE0`, `0x0C`, `0xBB`, `0x3F`, `0xC0`

Not this device. They come from descriptor `0600ff090ba101…85e0…850c…85bb…`,
which belongs to the **Apple Internal Keyboard / Trackpad** (VendorID 1452,
visible in `legacy/hid_omen.txt`). Writes aimed at them returned `-1` because
they addressed the wrong device. This monitor has no report IDs at all, so no
experiment varying a report ID tests anything.

### `WRITE: 65` / `WRITE: 64` as status codes

They are byte counts echoed by hidapi, not device responses. See framing above.

### The prior attempt's colour dumps

`legacy/red.txt` and `legacy/blue.txt` are byte-identical, as are
`legacy/red_dump.txt` and `legacy/blue_dump.txt`. Those reads never observed
state, so nothing derived from them is evidence.

### `ec ac 00` is not the colour we wrote

It appears in replies regardless of the RGB values sent, so it is more likely a
fixed device signature or status constant. Weak — worth a deliberate test.

## Open questions, in the order they block progress

1. What command hands control back to the OSD? (`probe.py release`)
2. Does any reply byte track a colour set through the OSD? If not, the OSD
   oracle collapses and everything falls to visual bisection.
3. Which offset is the zone selector, and what is the physical zone map?
4. Is brightness a separate field or premultiplied into RGB?
5. Does a write take effect immediately, or is there a commit/apply command?
