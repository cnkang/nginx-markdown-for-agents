#!/usr/bin/env python3
"""Minimal user-local state carrier for harness execution memory."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FILE = "events.jsonl"


@dataclass(frozen=True)
class StateEvent:
    event_type: str
    key: str
    source: str
    note: str
    ts: str


def state_dir(repo_root: Path | None = None) -> Path:
    repo_root = repo_root or REPO_ROOT
    override = os.environ.get("HARNESS_STATE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".gstack" / "projects" / repo_root.name / "harness-state"


def state_file(repo_root: Path | None = None) -> Path:
    return state_dir(repo_root) / DEFAULT_FILE


def _as_event(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    event_type = payload.get("event_type")
    if not isinstance(event_type, str) or not event_type:
        return None
    return payload


def append_event(
    event_type: str,
    key: str,
    source: str,
    note: str,
    repo_root: Path | None = None,
) -> Path:
    path = state_file(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "event_type": event_type,
        "key": key,
        "source": source,
        "note": note,
        "ts": datetime.now(UTC).isoformat(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    return path


def load_events(repo_root: Path | None = None) -> list[dict[str, Any]]:
    path = state_file(repo_root)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    for line in lines:
        if line.strip():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = _as_event(payload)
            if event is not None:
                events.append(event)
    return events


def summarize(repo_root: Path | None = None) -> str:
    events = load_events(repo_root)
    if not events:
        return "No harness state recorded yet."
    counts = Counter(
        event_type
        for event in events
        if (
            isinstance(event, dict)
            and isinstance(event_type := event.get("event_type"), str)
            and event_type
        )
    )
    event_count = sum(counts.values())
    if not counts:
        return "No harness state recorded yet."
    parts = [f"{key}={counts[key]}" for key in sorted(counts)]
    return f"{event_count} events, " + ", ".join(parts)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("path")
    sub.add_parser("summary")

    append = sub.add_parser("append")
    append.add_argument("--event-type", required=True)
    append.add_argument("--key", required=True)
    append.add_argument("--source", required=True)
    append.add_argument("--note", required=True)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "path":
        print(state_file())
        return 0
    if args.command == "summary":
        print(summarize())
        return 0
    if args.command == "append":
        path = append_event(
            event_type=args.event_type,
            key=args.key,
            source=args.source,
            note=args.note,
        )
        print(path)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
