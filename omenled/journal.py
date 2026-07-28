"""Append-only session log.

Every byte on the wire and every human observation goes here, as JSON Lines.
The point is that a session survives itself: if work stops halfway through a
decode, the journal plus docs/protocol.md are enough to resume without
repeating experiments.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterator, List, Optional

DEFAULT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sessions"
)


def hexs(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def unhex(text: str) -> bytes:
    return bytes(int(tok, 16) for tok in text.split())


class Journal:
    """Writes one JSON object per line, flushed immediately.

    Flushing on every record is deliberate. Experiments here end in unplugged
    cables and killed processes, and a buffered record is a lost one.
    """

    def __init__(self, session: Optional[str] = None, directory: str = DEFAULT_DIR):
        self.session = session or time.strftime("%Y%m%d-%H%M%S")
        self.directory = directory
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(directory, f"{self.session}.jsonl")
        self._seq = 0
        self._fh = open(self.path, "a", encoding="utf-8")

    def record(self, kind: str, **fields: Any) -> Dict[str, Any]:
        self._seq += 1
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session": self.session,
            "seq": self._seq,
            "kind": kind,
        }
        entry.update({k: v for k, v in fields.items() if v is not None})
        self._fh.write(json.dumps(entry) + "\n")
        self._fh.flush()
        return entry

    def tx(self, payload: bytes, label: Optional[str] = None) -> Dict[str, Any]:
        return self.record("tx", label=label, hex=hexs(payload))

    def rx(self, payload: Optional[bytes], label: Optional[str] = None) -> Dict[str, Any]:
        """A timeout is recorded, not dropped: silence is protocol evidence too."""
        if payload is None:
            return self.record("rx", label=label, timeout=True)
        return self.record("rx", label=label, hex=hexs(payload))

    def note(self, text: str, label: Optional[str] = None) -> Dict[str, Any]:
        return self.record("note", label=label, note=text)

    def observation(self, text: str, label: Optional[str] = None) -> Dict[str, Any]:
        """What the human saw. The only source of physical ground truth."""
        return self.record("observation", label=label, note=text)

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def read_session(path: str) -> List[Dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def iter_sessions(directory: str = DEFAULT_DIR) -> Iterator[str]:
    if not os.path.isdir(directory):
        return
    for name in sorted(os.listdir(directory)):
        if name.endswith(".jsonl"):
            yield os.path.join(directory, name)
