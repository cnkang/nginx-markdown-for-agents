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
import os
import re
from collections import deque
import subprocess
import sys
from dataclasses import dataclass

from pre_push_gates import load_gates
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


@dataclass
class Gate:
    """One gate in the push profile."""

    name: str
    command: list[str]
    needs_c_change: bool = False
    requires_nginx: bool = False
    reason: str = ""


@dataclass
class Outcome:
    """What happened to one gate."""

    gate: Gate
    status: str
    detail: str = ""


REF_RE = re.compile(r"[A-Za-z0-9._/@^~{}-]+")


def _validated_ref(ref: str) -> str:
    """Return the ref when it looks like one, otherwise refuse it.

    The base comes from the command line and travels into git, so it is checked
    against the characters a ref can contain before use: a value that does not
    look like a ref is a mistake, not something to hand to a subprocess.
    """
    match = REF_RE.fullmatch(ref or "")
    if match is None:
        raise ValueError(f"not a ref: {ref!r}")
    # The value handed on is the text the pattern accepted, not the raw
    # argument, so a rejected or altered string cannot travel further.
    return match.group(0)


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
    status, out = _git(["merge-base", "--end-of-options", _validated_ref(base), "HEAD"])
    return out.strip() if status == 0 and out.strip() else None


def _changed_files(base: str) -> list[str] | None:
    """Return the files changed since the merge base, or None when unknown.

    A failed diff is not an empty change set: treating it that way would skip
    every gate that depends on the change set and still report a pass.
    """
    status, out = _git(
        ["diff", "--name-only", "-z", "--end-of-options", f"{_validated_ref(base)}..HEAD"]
    )
    if status != 0:
        return None
    return [name for name in out.split("\0") if name]


C_BUILD_PREFIXES = ("components/nginx-module/",)
C_BUILD_FILES = ("Makefile",)
C_BUILD_SUFFIXES = (".sh", ".mk")


def is_c_build_change(changed: list[str]) -> bool:
    """True when the change can alter how the C module is built or tested.

    The module's own tree counts, as does the root Makefile, which carries the
    GCC container command and the C test entry.  Build scripts count too, but an
    unrelated tool change does not: running the container for every edit under
    tools would make the gate noise.
    """
    for path in changed:
        if path.startswith(C_BUILD_PREFIXES) or path in C_BUILD_FILES:
            return True
        if path.startswith("tools/ci/") and path.endswith(C_BUILD_SUFFIXES):
            return True
    return False


def _gates() -> list[Gate]:
    """Return the gates the profile selects from, as the shared declaration."""
    return [
        Gate(
            entry["name"],
            entry["command"],
            needs_c_change=entry["needs_c_change"],
            requires_nginx=entry["requires_nginx"],
        )
        for entry in load_gates(REPO_ROOT / "tools/ci/pre_push_gates.json")
    ]


def _run(gate: Gate) -> Outcome:
    """Run one gate, echo its output, and record its outcome.

    The output is streamed rather than captured, so a failure is visible while
    it happens and the summary can carry the command that failed instead of a
    final line that says "Error 1".
    """
    if gate.requires_nginx and not os.environ.get("NGINX_BIN"):
        # A required gate without its environment is not a pass and not a
        # not-selected check: the profile cannot claim to have completed.
        return Outcome(gate, "BLOCKED", "NGINX_BIN is not set")

    command = " ".join(gate.command)
    print(flush=True)
    print(f"── {gate.name}: {command}", flush=True)
    try:
        process = subprocess.Popen(
            gate.command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    except OSError as exc:
        # A gate whose command cannot start has failed, and saying so keeps the
        # summary complete instead of ending the run with a traceback.
        return Outcome(gate, "FAIL", f"cannot start: {exc}")
    tail: deque[str] = deque(maxlen=6)  # type: ignore[var-annotated]
    for line in process.stdout or []:
        print(line, end="", flush=True)
        if line.strip():
            tail.append(line.rstrip())
    code = process.wait()
    if code == 0:
        return Outcome(gate, "PASS")
    detail = tail[-1] if tail else ""
    return Outcome(gate, "FAIL", f"{command} -> {detail}" if detail else command)


def _select(gates: list[Gate], changed: list[str]) -> list[Outcome]:
    """Return an outcome for every gate, running the selected ones."""
    # The GCC gate also guards the C build configuration: the test Makefile and
    # the build scripts change compiler flags, so a source-suffix test is not
    # enough to decide that the gate can be skipped.
    c_changed = is_c_build_change(changed)
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
    blocked = [outcome for outcome in outcomes if outcome.status == "BLOCKED"]
    not_run = [outcome for outcome in outcomes if outcome.status == "NOT_RUN"]
    print()
    if failed:
        print(f"FAIL: {len(failed)} selected gate(s) failed; the profile did not pass.")
        return 1
    if blocked:
        print(
            f"INCOMPLETE: {len(blocked)} required gate(s) had no environment to "
            f"run in; the profile cannot claim to have passed."
        )
        return 2
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

    try:
        gates = _gates()
    except (OSError, ValueError) as exc:
        print(f"ERROR: invalid gate declaration: {exc}", file=sys.stderr)
        return 2
    if args.list:
        for gate in gates:
            marker = " (only when C changed)" if gate.needs_c_change else ""
            print(f"{gate.name}{marker}")
        return 0

    try:
        _validated_ref(args.base)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    base = _merge_base(args.base)
    if base is None:
        print(
            f"ERROR: cannot resolve the merge base with {args.base}, so the "
            f"change set is unknown",
            file=sys.stderr,
        )
        return 2

    changed = _changed_files(base)
    if changed is None:
        print(
            f"ERROR: cannot read the diff against {args.base}, so the change set "
            f"is unknown",
            file=sys.stderr,
        )
        return 2

    return _report(_select(gates, changed), base)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
