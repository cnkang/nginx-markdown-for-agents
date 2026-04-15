from __future__ import annotations

from pathlib import Path

from tools.harness import state_store


def test_state_dir_uses_override(tmp_path, monkeypatch):
    expected = tmp_path / "harness-state-test"
    monkeypatch.setenv("HARNESS_STATE_DIR", str(expected))
    assert state_store.state_dir() == expected


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


def test_summary_reports_no_events_when_state_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    assert state_store.summarize() == "No harness state recorded yet."


def test_load_events_skips_malformed_jsonl_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    path = state_store.state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                '{"event_type":"loop","key":"runtime","source":"t","note":"ok","ts":"2026-04-13T00:00:00+00:00"}',
                "{bad-json",
            ]
        ),
        encoding="utf-8",
    )

    events = state_store.load_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "loop"


def test_summary_skips_entries_missing_event_type(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    path = state_store.state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                '{"event_type":"loop","key":"runtime","source":"t","note":"ok","ts":"2026-04-13T00:00:00+00:00"}',
                '{"key":"runtime","source":"t","note":"missing","ts":"2026-04-13T00:00:01+00:00"}',
            ]
        ),
        encoding="utf-8",
    )

    assert state_store.summarize() == "1 events, loop=1"


def test_load_events_skips_non_dict_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    path = state_store.state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                '{"event_type":"loop","key":"runtime","source":"t","note":"ok","ts":"2026-04-13T00:00:00+00:00"}',
                '"raw-string-entry"',
                "1",
                "[]",
            ]
        ),
        encoding="utf-8",
    )

    events = state_store.load_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "loop"
