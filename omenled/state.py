"""Remembers the last frame so brightness can be changed without re-specifying it.

The device has no readable state, so a fresh process cannot ask the monitor what
is currently displayed. Without this, `brightness 50` would have nothing to dim.

What is stored is the frame at *full* intensity plus the brightness separately,
never the scaled result — otherwise repeatedly dimming would compound and the
colours would decay toward black with no way back.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import List, Optional, Tuple

from . import paths, protocol

Pixel = Tuple[int, int, int]


def save(pixels: List[Pixel], brightness: int, path: Optional[str] = None) -> None:
    """Write the frame atomically.

    Written to a sibling file and renamed into place, so a concurrent `load` —
    an app shelling out to the CLI while another command runs — sees either
    the old frame or the new one, never a half-written file it would discard.

    The sibling is unique per call, not per process: two saves in one process
    sharing a name would truncate each other's file, and the second rename
    would find it already gone.
    """
    path = path or paths.state_file()
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pixels": [list(p) for p in pixels], "brightness": brightness}, fh)
        os.replace(tmp, path)
    except BaseException:
        # Includes KeyboardInterrupt: a Ctrl-C mid-save must not strand the
        # temp file. After a successful replace there is nothing to remove.
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def load(path: Optional[str] = None) -> Optional[Tuple[List[Pixel], int]]:
    """None if nothing has been rendered yet, or the file is unusable.

    A corrupt state file is not worth an error: it only costs the user the
    convenience of remembering, and the next render rewrites it.

    The path is resolved at call time, not bound as a default argument — a
    default would capture DEFAULT_PATH at import and make the location
    impossible to redirect, which had tests writing to the real state file and
    reading each other's leftovers.
    """
    path = path or paths.state_file()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        pixels = [tuple(int(c) for c in px) for px in data["pixels"]]
        brightness = int(data["brightness"])
        if not all(len(px) == 3 and all(0 <= c <= 0xFF for c in px) for px in pixels):
            return None
        if not 0 <= brightness <= 100:
            return None
        return pixels, brightness
    except (OSError, ValueError, KeyError, TypeError):
        return None


def remembered_frame(count: Optional[int] = None) -> Optional[List[Pixel]]:
    """The last picture the user asked for, ready to render, or None.

    Brightness is already applied, so the result can go straight to
    `Lighting.set_all`. Use this to restore the display after a transient
    animation.

    "Previous state" can only ever mean *the last picture requested*, not what
    is physically lit: the monitor cannot be read back, so nothing else is
    knowable. An animation that runs and exits therefore leaves this untouched
    rather than recording its own frames.
    """
    loaded = load()
    if loaded is None:
        return None
    pixels, brightness = loaded
    frame = protocol.scale(pixels, brightness)
    if count is None:
        return frame
    return (frame + [(0, 0, 0)] * count)[:count]
