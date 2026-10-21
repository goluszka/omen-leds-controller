# omen-led: control HP OMEN 27k monitor RGB lighting from macOS

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)
![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)

**omen-led** is a command-line tool and Python library for the RGB LED lighting
on the back of the **HP OMEN 27k** gaming monitor. It lets a Mac set any colour
on each of the **60 LEDs** of the light diamond, fade gradients, dim, and play
animations, over the monitor's USB cable.

HP's own lighting apps, OMEN Gaming Hub and OMEN Light Studio, are Windows-only.
Without them, the only control a Mac has is the monitor's on-screen menu. HP
never published the USB protocol, so it was
[reverse engineered for this project](docs/protocol.md).

```sh
omen-led fill '#00ff00'          # the whole diamond green
omen-led gradient red blue       # fade from red to blue
omen-led brightness 40           # dim whatever is showing
omen-led police                  # red/blue strobe, then put things back
```

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Install](#install)
- [Command line](#command-line)
- [Python library](#python-library)
- [Read this before you start](#read-this-before-you-start)
- [Animations](#animations)
- [Building an app on top](#building-an-app-on-top)
- [How it works](#how-it-works)
- [FAQ](#faq)
- [Contributing](#contributing)

## Features

- **Per-LED colour.** All 60 LEDs are individually addressable, with full 24-bit
  RGB on each one.
- **Simple CLI.** `fill`, `set`, `gradient`, `brightness`, `off`, plus a
  `police` strobe and a `chase` that walks one LED around the diamond.
- **Python API.** Chainable framebuffer: build a picture, then send it with
  `render()`.
- **Remembers your picture.** `brightness` re-dims the last thing you set
  without you repeating it, and animations restore it when they finish.
- **Dry run.** Add `--dry-run` to any command to print the exact USB bytes
  without needing a monitor attached.
- **No background service, no daemon.** The lighting stays as you left it after
  the command exits.

## Requirements

- An **HP OMEN 27k** monitor (USB ID `03F0:07B0`, "OMEN 27k External Light
  Device").
- The monitor's **USB upstream cable connected to the Mac**. HDMI or
  DisplayPort on its own is not enough: the lighting is controlled over USB.
- **Python 3.9 or newer.** [`hidapi`](https://pypi.org/project/hidapi/) is
  installed automatically.
- **macOS.** That is where it is developed and tested. hidapi also runs on
  Linux and Windows, so those may work, but nobody has tried them yet.

## Install

```sh
pip install git+https://github.com/goluszka/omen-leds-controller
```

Or from a clone: `pip install .`. You can also skip installing.
`python3 -m omenled.cli …` and `import omenled` both work from inside the
checkout.

Check it works, sending nothing to the monitor:

```sh
omen-led --dry-run fill red                       # no monitor needed; shows the bytes
python3 -c "import omenled; print(omenled.describe())"   # [] means not connected
```

## Command line

```sh
omen-led fill '#00ff00'          # every LED one colour
omen-led set 0 5 10 red          # light LEDs 0, 5 and 10; blank the rest
omen-led set 3 blue --keep       # change LED 3, leave the others as they were
omen-led gradient red blue       # fade across the strip
omen-led brightness 40           # re-dim the current picture to 40 %
omen-led police                  # red/blue siren, then restore the picture
omen-led police --speed 3        # the same, three times faster
omen-led chase                   # walk one LED along the strip (index → position)
omen-led off                     # everything dark
```

| Option | Works with | What it does |
|---|---|---|
| `--brightness 0-100` | any command | scale intensity; remembered for next time |
| `--dry-run` | any command | print the bytes instead of sending them |
| `--keep` | `set` | keep the other LEDs instead of blanking them |
| `--speed N` | `police` | run N times faster (useful up to about 4) |
| `--cycles N`, `--flashes N`, `--swap` | `police` | length and pattern of the strobe |
| `--delay SECONDS` | `chase` | how long each LED stays lit |

**Colours** can be `#rrggbb`, `#rgb`, or a name: `red`, `green`, `blue`,
`white`, `yellow`, `cyan`, `magenta`, `orange`, `purple`, `pink`, `black`/`off`.

Global options go on either side of the command:
`omen-led --brightness 40 police` and `omen-led police --brightness 40` do the
same thing.

## Python library

```python
from omenled import open_lighting, gradient, parse_colour, LED_COUNT

with open_lighting() as lights:
    lights.fill(parse_colour("#00ff00")).render()        # whole diamond green
    lights.set_led(0, (255, 0, 0)).render()              # LED 0 red
    lights.set_all(gradient((255, 0, 0), (0, 0, 255), LED_COUNT)).render()
    lights.off()                                         # everything dark
```

`Lighting` holds a framebuffer of 60 `(r, g, b)` tuples. The setters only change
that buffer and return `self`, so they chain. Nothing reaches the monitor until
you call `render()`.

| Method | Effect |
|---|---|
| `fill(rgb)` | every LED one colour |
| `set_led(i, rgb)` | one LED, `i` from 0 to 59 |
| `set_all(pixels)` | a list of colours; a short list leaves the rest dark |
| `gradient(a, b)` | linear fade from colour `a` to colour `b` |
| `render(dry_run=False, verify=True)` | send the frame |
| `off()` | blank every LED in one packet |

`render(verify=True)` checks that the monitor accepted each write and raises
`RenderRejected` if it did not. Pass `verify=False` in animation loops: the
check waits up to 150 ms on each of a frame's three writes.

Errors you may want to catch: `DeviceNotFound` (monitor not connected),
`TransportError` (device busy or a write failed), `RenderRejected`.

Helpers that need no device: `parse_colour`, `gradient`, `scale` (brightness),
`remembered_frame` (the last picture set through the CLI), and the raw packet
builders `plane_packet`, `frame_packets`, `off_packet`, `parse_reply`.

**Logging is off by default.** `open_lighting(journal=True)` records every
packet as JSON Lines under `~/Library/Application Support/omen-led`. Set
`OMENLED_DATA_DIR` to put it, and the saved picture, somewhere else.

## Read this before you start

> [!WARNING]
> **Sending any command locks the monitor's lighting menu.** Once software takes
> control, the monitor shows a notice and its on-screen lighting settings stop
> responding. No command has been found that hands control back. To get the
> menu back, **unplug and replug the USB cable**, or reset the monitor from its
> on-screen menu.

- **The lighting stays on after the command exits.** That is deliberate. Run
  `omen-led off` or call `off()` if you want it dark.
- **The monitor cannot report what it is showing.** omen-led remembers the last
  picture you set itself. That memory is what `brightness` dims and what
  animations restore, so it knows what you *asked for*, not what is lit. If
  something else changed the lighting in between, it will not know.
- **Brightness is done in software**, by scaling the colour values. At very low
  settings colours lose precision.

## Animations

`omen-led police` and the scripts in [`examples/`](examples/) show how to
animate the lighting:

| Script | What it does |
|---|---|
| `omen-led police` | red/blue siren strobe, then restores your picture (also on Ctrl-C) |
| [`examples/pulse.py`](examples/pulse.py) | slow breathing fade; ends dark |
| [`examples/alarm.py`](examples/alarm.py) | sharp red alarm flash, then restores your picture |

Each pattern is a plain generator that yields `(pixels, seconds_to_hold)`.
Because it never touches hardware, you can test it without a monitor. To write
your own, copy that shape and pass the frames to `omenled.play()`.

On speed: by default the library waits 50 ms between USB writes, as a safety
margin. A frame is three writes, so open with
`open_lighting(min_interval=0.002)` for animation, as the examples do. Even
then the device declares a 4 ms report interval, so about 12 ms per frame
(roughly 80 fps) is the ceiling. Above `--speed 4` the `police` strobe is
limited by that, not by the pattern.

## Building an app on top

- **Menu-bar app or script:** call the `omen-led` CLI. You get the safety
  checks and the remembered picture for free. A new process per command is too
  slow for animation, though.
- **Native Swift app:** the protocol is about twenty lines against
  `IOHIDManager`. [`AGENTS.md`](AGENTS.md) has the full recipe, including the
  one framing detail that differs from hidapi and shifts every LED by one if you
  get it wrong.

## How it works

The lighting controller (an ENE microcontroller inside the monitor) shows up as
a vendor-defined USB HID device. Each frame is three 64-byte reports, one for
each colour channel:

```
byte 0       which colour: 0x05 red, 0x06 green, 0x07 blue (0x0d = all off)
bytes 1..3   0x00 0x00 0xd0
bytes 4..63  one brightness byte per LED, 60 in total
```

The three must be sent red, then green, then blue, or the monitor rejects the
frame. The full decode is in [`docs/protocol.md`](docs/protocol.md). It covers
the evidence behind each claim, the dead ends, and what is still unknown.

```
omenled/
  protocol.py    build and parse packets (pure functions, no hardware)
  transport.py   the only code that talks to the USB device
  device.py      the Lighting framebuffer and rendering
  cli.py         the omen-led command
  animations.py  reusable animation patterns
  state.py       remembers the last picture
  journal.py     optional packet log
  safety.py      refuses packets that look like firmware/bootloader commands
tools/probe.py   the reverse-engineering workbench used to decode the protocol
examples/        runnable animation examples
```

Run the tests (no monitor needed):

```sh
python3 -m pytest tests/ -q
```

## FAQ

**Does it work with other OMEN monitors (27i, 27c, 25…)?**
Only the OMEN 27k has been tested. A model whose lighting shows up with the
same USB ID (`03F0:07B0`) may well use the same protocol.
`python3 -c "import omenled; print(omenled.describe())"` lists matching
devices without sending them anything.

**Does it need OMEN Gaming Hub, Windows, or a VM?**
No. It talks to the monitor directly over USB from macOS.

**My monitor's lighting menu stopped working.**
That is expected once software has sent a command. See
[Read this before you start](#read-this-before-you-start): replug the USB cable
or reset the monitor.

**`error: no HID device 03f0:07b0`**
The monitor's USB upstream cable is not connected to this computer. Video
cables (HDMI, DisplayPort) do not carry the lighting.

**`could not open it … Another process may be holding it`**
Another program, or an earlier run of omen-led that has not finished, still
has the device open. Wait a moment and retry, or replug the cable.

**Which LED number is where?**
Not fully mapped yet. `omen-led chase` lights them one at a time and prints
each index, so you can see for yourself.

## Contributing

Issues and pull requests are welcome, especially on the open questions:

- **LED map:** which index sits where on the diamond (`omen-led chase`).
- **Releasing control:** the command that hands lighting back to the monitor's
  own menu.
- **Hardware brightness:** whether the controller has a brightness setting of
  its own (`python3 tools/probe.py experiment hwbright`).

Before changing anything in the protocol layer, read [`AGENTS.md`](AGENTS.md)
and the REFUTED section of [`docs/protocol.md`](docs/protocol.md). They list
the mistakes that have already cost hours.

## Licence

[MIT](LICENSE).
