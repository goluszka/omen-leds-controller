"""Where omenled keeps its data.

Resolved against the user's home directory rather than the package directory.
The package location is wrong for this: once installed, it is inside
site-packages, and a library that writes there will fail on a read-only install
and pollute the environment on a writable one.

Override with `OMENLED_DATA_DIR` — the reverse-engineering workbench uses that
to keep its journals with the repository, where they are project history rather
than user data.
"""

from __future__ import annotations

import os
import sys

ENV_VAR = "OMENLED_DATA_DIR"


def data_dir() -> str:
    """Base directory for journals and saved state."""
    override = os.environ.get(ENV_VAR)
    if override:
        return os.path.expanduser(override)
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/omen-led")
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "omen-led")


def sessions_dir() -> str:
    return os.path.join(data_dir(), "sessions")


def state_file() -> str:
    return os.path.join(data_dir(), "state.json")
