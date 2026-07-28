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
from typing import List, Optional, Tuple

Pixel = Tuple[int, int, int]

from . import paths


def save(pixels: List[Pixel], brightness: int, path: Optional[str] = None) -> None:
    path = path or paths.state_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"pixels": [list(p) for p in pixels], "brightness": brightness}, fh)


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
        if not all(len(px) == 3 for px in pixels):
            return None
        return pixels, int(data["brightness"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
