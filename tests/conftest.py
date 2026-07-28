"""Shared test setup.

Isolating the state file is autouse rather than opt-in: a test that forgets it
writes into the project's real data directory and then reads whatever a previous
test left there, which makes brightness assertions fail in ways that look like
product bugs.
"""

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    from omenled import state

    monkeypatch.setattr(state, "DEFAULT_PATH", str(tmp_path / "state.json"))
