# omen-led

Software control of the HP OMEN 27k monitor's external lighting, from macOS.

**60 individually addressable LEDs, full RGB each**, over USB HID. No public
decode of this protocol existed; it was reverse engineered from scratch, and
[`docs/protocol.md`](docs/protocol.md) records every finding with how confident
it is.

```python
from omenled import open_lighting, parse_colour

with open_lighting() as lights:
    lights.fill(parse_colour("#00ff00")).render()
```

## Install

```sh
pip install /path/to/omen-test
```

Needs Python 3.9+, and `hidapi` (installed as a dependency). On macOS the
monitor's USB upstream cable must be connected to the machine.

Or use it without installing — `python3 -m omenled.cli` and `import omenled`
both work from a checkout.

## CLI

```sh
omen-led fill '#00ff00'        # whole diamond green
omen-led set 0 5 10 red        # specific LEDs, others blanked
omen-led set 3 blue --keep     # leave the rest as they were
omen-led gradient red blue     # fade across the strip
omen-led brightness 40         # re-dim the current picture
omen-led police                # red/blue siren, then restore what was showing
omen-led police --speed 3      # faster strobe
omen-led chase                 # walk one LED, maps index to position
omen-led off
```

Colours accept `#rrggbb`, `#rgb`, or a name (`red`, `green`, `blue`, `white`,
`yellow`, `cyan`, `magenta`, `orange`, `purple`, `pink`, `black`).

`--brightness 0-100` works on any command and is remembered for the next one.
`--dry-run` prints the exact bytes without sending them, and needs no monitor
attached.

Both are accepted on either side of the subcommand — `omen-led --brightness 40
police` and `omen-led police --brightness 40` are the same thing.

## API

```python
from omenled import open_lighting, gradient, parse_colour, LED_COUNT

with open_lighting() as lights:
    lights.fill((0, 0, 0))                      # framebuffer only
    lights.set_led(0, (255, 0, 0))
    lights.set_all(gradient((255, 0, 0), (0, 0, 255), LED_COUNT))
    lights.render()                             # three plane writes, R→G→B
    lights.off()
```

`Lighting` holds a framebuffer and the setters return `self`, so calls chain.
Nothing reaches the monitor until `render()`.

- `fill(rgb)` / `set_led(i, rgb)` / `set_all(pixels)` / `gradient(a, b)`
- `render(dry_run=False, verify=True)` — `verify` checks the device's
  acknowledgement and raises `RenderRejected` rather than leaving a half-written
  frame on screen. Turn it off for animation, where the readback costs latency.
- `off()` — blanks every LED in one packet.

Errors worth catching: `DeviceNotFound`, `TransportError`, `RenderRejected`.

**Journalling is off by default.** Pass `open_lighting(journal=True)` to record
every packet as JSON Lines, or supply your own `Journal`. Data goes to
`~/Library/Application Support/omen-led`, overridable with `OMENLED_DATA_DIR`.

For raw packets, `plane_packet`, `frame_packets`, `off_packet` and `parse_reply`
are pure functions needing no device.

## Behaviour worth knowing

**The monitor has no readable state.** Nothing can be queried back, so the
framebuffer in this process is the only record of what is displayed. That is why
`brightness` needs a saved frame, and why a render always writes every LED.

**Sending anything locks the OSD lighting menu**, and the monitor shows a notice
that software is in control. No release command was found in nine candidates —
unplug the USB cable or reset the monitor from the OSD to get it back.

**Lighting persists after exit.** Nothing is blanked when the process ends; call
`off()` if that is what you want.

## Building on top of this

`AGENTS.md` is written for AI agents picking this up cold — the full protocol
inline, the constraints that bite, and the mistakes worth not repeating. It also
covers driving the lighting from a native macOS app, including the one framing
difference between hidapi and `IOHIDDeviceSetReport` that silently shifts every
LED by one.

Runnable animations in `examples/`, each with its pattern as a pure generator
so the shape can be tested without the monitor:

| | |
|---|---|
| `pulse.py` | breathing fade; ends dark |
| `alarm.py` | urgent flash in `#9E0000`, then **restores the previous picture** |

The siren graduated from an example to `omen-led police`, with its pattern in
`omenled/animations.py` — importable as `police_frames()` and `play()`.

`--speed N` divides every interval, keeping the rhythm rather than only
shortening the flash. There is a hard floor: a frame is three plane writes and
the device declares a 4 ms report interval, so roughly 12 ms per frame is the
best achievable. Past about `--speed 4` the write rate sets the pace, not the
pattern.

`alarm` and `police` restore the last picture set through the CLI when they
finish, including on Ctrl-C. "Previous" can only mean the last picture
*requested* — the monitor cannot be read back — which is what
`remembered_frame()` returns.

## Layout

```
omenled/
  protocol.py    Pure packet build/parse. No I/O, testable without hardware.
  transport.py   HID I/O. The only place raw bytes reach the device.
  device.py      Framebuffer and rendering.
  cli.py         Command line front end.
  animations.py  Reusable patterns as pure generators of frames.
  state.py       Remembers the last frame so brightness can re-dim it.
  journal.py     Append-only JSONL session log.
  safety.py      Refusal list for payloads resembling flash/bootloader commands.
tools/probe.py   Reverse-engineering workbench: the experiments that decoded
                 the protocol, kept so findings can be reproduced or extended.
examples/        Runnable reference code.
```

```sh
python3 -m pytest tests/ -q      # 421 tests, no hardware needed
```

## Still open

The write path is complete. What remains is refinement, and none of it blocks
use:

- Which LED index sits where physically (`omen-led chase` shows you).
- What hands control back to the OSD.
- Whether a hardware brightness control exists
  (`python3 tools/probe.py experiment hwbright`).

## Licence

MIT.
