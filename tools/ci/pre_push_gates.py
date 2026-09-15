#!/usr/bin/env python3
"""The gates a push has to complete, as data both sides can read.

The profile runs these, and the wiring check verifies that the stage a rule
declares is actually reached by one of them.  Keeping one declaration means the
checker cannot drift from what the executor runs: a gate that is removed from
the list is removed from both.
"""

from __future__ import annotations

import ast
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
    from tools.lib.path_validation import validate_read_path

    validated = validate_read_path(str(path))
    source = Path(validated)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    declarations: list[ast.Assign | ast.AnnAssign] = []
    for index, node in enumerate(tree.body):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "GATES":
                declarations.append(node)
                continue
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "GATES"
            for target in node.targets
        ):
            declarations.append(node)
            continue
        if _mentions_gates(node):
            # An import runs everything that follows, so a later statement that
            # touches the name can change what the executor sees.
            raise ValueError("the declaration must be the only use of GATES")
    if not declarations:
        raise ValueError("missing GATES declaration")
    if len(declarations) > 1:
        # The checker reads one assignment while Python applies the last.
        raise ValueError("GATES must be declared exactly once")
    return validate_gates(ast.literal_eval(declarations[0].value))


# Statements that run when the module is imported.  A definition only runs
# when it is called, so its body is not a later modification.
_IMPORT_TIME = (
    ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr, ast.Delete, ast.For,
    ast.While, ast.If, ast.With, ast.Try,
)


def _mentions_gates(node: ast.stmt) -> bool:
    """True when importing the module could touch the name the declaration binds."""
    if not isinstance(node, _IMPORT_TIME):
        return False
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == "GATES":
            return True
    return False
