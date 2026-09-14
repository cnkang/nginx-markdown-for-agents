#!/usr/bin/env python3
"""The gates a push has to complete, as data both sides can read.

The profile runs these, and the wiring check verifies that the stage a rule
declares is actually reached by one of them.  Keeping one declaration means the
checker cannot drift from what the executor runs: a gate that is removed from
the list is removed from both.
"""

from __future__ import annotations

from pathlib import Path

# A gate names the command to run, whether it is selected from the change set,
# and whether it needs an environment that may be absent.
GATES: tuple[dict[str, object], ...] = (
    {
        "name": "local gate set (test-all)",
        "command": ["make", "test-all"],
        "needs_c_change": False,
        "requires_nginx": False,
    },
    {
        "name": "real GCC C unit suite",
        "command": ["make", "test-c-unit-gcc"],
        "needs_c_change": True,
        "requires_nginx": False,
    },
    {
        "name": "security static analysis",
        "command": ["make", "security-static"],
        "needs_c_change": False,
        "requires_nginx": False,
    },
    {
        "name": "module end-to-end checks",
        "command": ["make", "test-all-e2e"],
        "needs_c_change": False,
        "requires_nginx": True,
    },
    {
        "name": "coverage gate",
        "command": ["make", "test-all-coverage"],
        "needs_c_change": False,
        "requires_nginx": True,
    },
)


def validate_gates(value: object) -> list[dict]:
    """Reject malformed declarations instead of coercing commands or flags."""
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("GATES must be a non-empty sequence")
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


def load_gates(path: Path) -> list[dict]:
    """Read literal GATES data without executing the declaration file."""
    import ast

    from tools.lib.path_validation import validate_read_path

    validated = validate_read_path(str(path))
    source = Path(validated)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "GATES":
                return validate_gates(ast.literal_eval(node.value))
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "GATES"
            for target in node.targets
        ):
            return validate_gates(ast.literal_eval(node.value))
    raise ValueError("missing GATES declaration")
