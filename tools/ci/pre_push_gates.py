#!/usr/bin/env python3
"""The gates a push has to complete, as data both sides can read.

The profile runs these, and the wiring check verifies that the stage a rule
declares is actually reached by one of them.  Keeping one declaration means the
checker cannot drift from what the executor runs: a gate that is removed from
the list is removed from both.
"""

from __future__ import annotations

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


def gate_targets() -> set[str]:
    """Return the Make targets the gates run."""
    targets: set[str] = set()
    for gate in GATES:
        command = gate["command"]
        if isinstance(command, list) and command[:1] == ["make"] and len(command) > 1:
            targets.add(str(command[1]))
    return targets
