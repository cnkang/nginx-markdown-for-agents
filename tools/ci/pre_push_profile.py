#!/usr/bin/env python3
"""Run the gates a push has to complete, and report what actually ran.

`make ci-local-check` runs the local gate set and then said "CI-equivalent gates
passed" whether or not the checks that need other tools had run.  This profile
selects gates from the change set, runs them, and reports each one as PASS, FAIL
or NOT_RUN.  The profile passes only when every *selected* gate ran and passed;
a gate that was not selected is reported as NOT_RUN and is never counted as a
pass.

Exit codes: 0 = every selected gate passed, 1 = a selected gate failed,
2 = the profile could not be completed (for example, the merge base is unknown).

Usage:
    python3 tools/ci/pre_push_profile.py [--base <ref>] [--list]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Gate:
    """One gate in the push profile."""

    name: str
    command: list[str]
    needs_c_change: bool = False
    reason: str = ""


@dataclass
class Outcome:
    """What happened to one gate."""

    gate: Gate
    status: str
    detail: str = ""


def _git(args: list[str]) -> tuple[int, str]:
    """Run git and return the exit status with its output."""
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout


def _merge_base(base: str) -> str | None:
    """Return the merge base with a base ref, or None when it is unknown."""
    status, out = _git(["merge-base", base, "HEAD"])
    return out.strip() if status == 0 and out.strip() else None


def _changed_files(base: str) -> list[str]:
    """Return the files changed since the merge base."""
    status, out = _git(["diff", "--name-only", f"{base}..HEAD"])
    return out.split() if status == 0 else []


def _gates() -> list[Gate]:
    """Return the gates the profile selects from."""
    return [
        Gate("local gate set (test-all)", ["make", "test-all"]),
        Gate(
            "real GCC C unit suite",
            ["make", "test-c-unit-gcc"],
            needs_c_change=True,
            reason="macOS gcc is an Apple clang alias, so GCC-only failures need "
            "the container",
        ),
    ]


def _run(gate: Gate) -> Outcome:
    """Run one gate and record its outcome."""
    result = subprocess.run(
        gate.command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return Outcome(gate, "PASS")
    tail = (result.stderr or result.stdout).strip().splitlines()
    return Outcome(gate, "FAIL", tail[-1] if tail else "")


def _select(gates: list[Gate], changed: list[str]) -> list[Outcome]:
    """Return an outcome for every gate, running the selected ones."""
    c_changed = any(
        path.endswith((".c", ".h")) and "nginx-module" in path for path in changed
    )
    outcomes: list[Outcome] = []
    for gate in gates:
        if gate.needs_c_change and not c_changed:
            outcomes.append(
                Outcome(gate, "NOT_RUN", "no C source changed in this push")
            )
            continue
        outcomes.append(_run(gate))
    return outcomes


def _report(outcomes: list[Outcome], base: str) -> int:
    """Print the profile result and return the exit status."""
    print()
    print("=== push profile ===")
    print(f"base: {base}")
    print()
    width = max(len(outcome.gate.name) for outcome in outcomes)
    for outcome in outcomes:
        line = f"  {outcome.gate.name:<{width}}  {outcome.status}"
        if outcome.detail:
            line += f"  ({outcome.detail})"
        print(line)
    failed = [outcome for outcome in outcomes if outcome.status == "FAIL"]
    not_run = [outcome for outcome in outcomes if outcome.status == "NOT_RUN"]
    print()
    if failed:
        print(f"FAIL: {len(failed)} selected gate(s) failed; the profile did not pass.")
        return 1
    if not_run:
        print(
            f"NOTE: {len(not_run)} gate(s) NOT_RUN.  The profile passed for the "
            f"selected gates; a NOT_RUN gate is not a pass."
        )
    print("PASS: every selected gate ran and passed.")
    return 0


def main(argv: list[str]) -> int:
    """Run the push profile."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="base ref to diff")
    parser.add_argument("--list", action="store_true", help="list the gates")
    args = parser.parse_args(argv[1:])

    gates = _gates()
    if args.list:
        for gate in gates:
            marker = " (only when C changed)" if gate.needs_c_change else ""
            print(f"{gate.name}{marker}")
        return 0

    base = _merge_base(args.base)
    if base is None:
        print(
            f"ERROR: cannot resolve the merge base with {args.base}, so the "
            f"change set is unknown",
            file=sys.stderr,
        )
        return 2

    return _report(_select(gates, _changed_files(base)), base)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
