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


def _validate_state_path(path: Path) -> Path:
    """Reject traversal components and resolve *path* to an absolute form.

    This is a lightweight validation for user-local state directories:
    it ensures ``..`` cannot escape the intended root, and returns a
    resolved ``Path`` that is safe to open.

    Args:
        path: Raw path to validate.

    Returns:
        Resolved absolute Path.

    Raises:
        ValueError: If the path contains ``..`` traversal components.
    """
    raw = str(path)
    if ".." in Path(raw).parts:
        raise ValueError(
            f"Refusing state path with '..' traversal component: {raw!r}"
        )
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class StateEvent:
    """A single harness state event with type, key, source, note, and timestamp."""

    event_type: str
    key: str
    source: str
    note: str
    ts: str


def state_dir(repo_root: Path | None = None) -> Path:
    """Return the directory used to store harness state files.

    Uses HARNESS_STATE_DIR environment variable if set, otherwise
    defaults to ~/.gstack/projects/<repo-name>/harness-state/.

    The override path is validated to reject ``..`` traversal components
    and resolved to an absolute form before use.

    Args:
        repo_root: Repository root path. Defaults to REPO_ROOT.

    Returns:
        Resolved state directory path.
    """
    repo_root = repo_root or REPO_ROOT
    override = os.environ.get("HARNESS_STATE_DIR")
    if override:
        return _validate_state_path(Path(override))
    return Path.home() / ".gstack" / "projects" / repo_root.name / "harness-state"


def state_file(repo_root: Path | None = None) -> Path:
    """Return the path to the JSONL events file.

    Args:
        repo_root: Repository root path. Defaults to REPO_ROOT.

    Returns:
        Path to the events.jsonl file inside the state directory.
    """
    return state_dir(repo_root) / DEFAULT_FILE


def _as_event(payload: Any) -> dict[str, Any] | None:
    """Validate that a parsed JSON payload is a well-formed state event.

    Args:
        payload: Parsed JSON object to validate.

    Returns:
        The payload dict if it has a non-empty string event_type,
        None otherwise.
    """
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
    """Append a new event to the JSONL state file.

    Creates the state directory and file if they do not exist.

    Args:
        event_type: Category of the event (e.g. "spec-resolve", "check-run").
        key: Identifier for the event subject.
        source: Origin of the event (e.g. script name, CI job).
        note: Human-readable description of the event.
        repo_root: Repository root path. Defaults to REPO_ROOT.

    Returns:
        Path to the state file that was appended to.
    """
    path = _validate_state_path(state_file(repo_root))
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
    """Load all valid state events from the JSONL file.

    Skips malformed lines and lines that fail JSON parsing.

    Args:
        repo_root: Repository root path. Defaults to REPO_ROOT.

    Returns:
        List of validated event dictionaries, in file order.
    """
    path = _validate_state_path(state_file(repo_root))
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
    """Produce a one-line summary of recorded harness state events.

    Args:
        repo_root: Repository root path. Defaults to REPO_ROOT.

    Returns:
        Human-readable summary string with event counts by type.
    """
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
    """Parse command-line arguments for the state-store CLI.

    Args:
        argv: Argument strings to parse. Defaults to sys.argv[1:].

    Returns:
        Parsed namespace with the ``command`` sub-command.
    """
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
    """Entry point for the state-store CLI.

    Supports three sub-commands: path (print state file path), summary
    (print event count summary), and append (add a new event).

    Args:
        argv: Command-line arguments. Defaults to sys.argv[1:].

    Returns:
        Exit code: 0 for success, 1 for unknown command.
    """
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
