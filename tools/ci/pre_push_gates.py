#!/usr/bin/env python3
"""The gates a push has to complete, as data both sides can read.

The profile runs these, and the wiring check verifies that the stage a rule
declares is actually reached by one of them.  Keeping one declaration means the
checker cannot drift from what the executor runs: a gate that is removed from
the list is removed from both.
"""

from __future__ import annotations

import json
from pathlib import Path

def validate_gates(value: object) -> list[dict]:
    """Reject malformed declarations instead of coercing commands or flags."""
    if not isinstance(value, list) or not value:
        raise ValueError("gate declaration must be a non-empty list")
    names: set[str] = set()
    result: list[dict] = []
    for entry in value:
        result.append(_validated_gate(entry, names))
    return result


def _validated_gate(entry: object, names: set[str]) -> dict:
    """Return one validated gate, recording its name for the uniqueness check."""
    if not isinstance(entry, dict):
        raise ValueError("each gate must be an object")
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip() or name in names:
        raise ValueError("gate names must be non-empty and unique")
    command = entry.get("command")
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part.strip() for part in command
    ):
        raise ValueError(f"{name}: command must be a non-empty string list")
    for flag in ("needs_c_change", "requires_nginx"):
        if not isinstance(entry.get(flag), bool):
            raise ValueError(f"{name}: {flag} must be a boolean")
    names.add(name)
    return dict(entry, command=list(command))


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject duplicate JSON keys before a parser can silently overwrite them."""
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate gate declaration key: {key}")
        result[key] = value
    return result


def load_gates(path: Path) -> list[dict]:
    """Load and validate the sole JSON declaration, without caching or fallback."""
    from tools.lib.path_validation import validate_read_path

    validated = validate_read_path(str(path))
    source = Path(validated)
    data = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    return validate_gates(data)
