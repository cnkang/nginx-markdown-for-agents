from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))

from harness import state_store


def test_state_dir_uses_override(monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", "/tmp/harness-state-test")
    assert state_store.state_dir() == Path("/tmp/harness-state-test")


def test_append_and_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    path = state_store.append_event(
        event_type="loop",
        key="runtime-streaming",
        source="local-test",
        note="first retry",
    )
    assert path.exists()
    summary = state_store.summarize()
    assert "1 events" in summary
    assert "loop=1" in summary
