"""Package-level guard for the soak gate tests.

The per-test leak assertion runs in definition order, so it cannot see a
directory a later test leaves behind. This hook runs once the whole package has
finished, which closes that gap: a test that exercises the real runtime tree
without cleaning up fails the session instead of quietly accumulating
directories under build/soak-runtime.
"""

from __future__ import annotations

import pathlib

import pytest

_SOAK_RUNTIME_RELATIVE = ("build", "soak-runtime")

# Names present before this session ran. A directory left behind by an
# interrupted earlier run is not this session's leak, and failing on it would make
# the suite depend on whatever happened to be on disk.
#
# Captured at import, not in pytest_sessionstart: under xdist the workers import
# and collect before their own sessionstart runs, so a baseline taken there would
# arrive after the collection-time registration and be empty.
# Taken at import so it is in place before any test reads it, including under
# xdist where collection runs ahead of the worker's sessionstart hook. Assigned
# below, once _leaked_runtime_dirs exists.
_PRE_EXISTING: frozenset[str] | None = None


def _leaked_runtime_dirs() -> list[str]:
    """Return any leftover markdown-soak-* directories in the real build tree."""
    # Located from this file rather than imported from the gate: the gate binds
    # SOAK_RUNTIME_ROOT at import time, and the whole point is to look at the
    # tree the tests actually write to.
    tests_dir = pathlib.Path(__file__).resolve().parent
    repo_root = tests_dir.parents[3]
    runtime_root = repo_root.joinpath(*_SOAK_RUNTIME_RELATIVE)
    if not runtime_root.is_dir():
        return []
    return sorted(p.name for p in runtime_root.glob("markdown-soak-*"))


_PRE_EXISTING = frozenset(_leaked_runtime_dirs())


def pytest_sessionstart(session: pytest.Session) -> None:
    """Record what was already there so only new directories count as leaks."""
    global _PRE_EXISTING
    if _PRE_EXISTING is None:
        _PRE_EXISTING = frozenset(_leaked_runtime_dirs())


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the session when a test leaked a runtime directory."""
    # Only directories this session created. A leftover from an interrupted run
    # is reported for visibility but is not attributed to the tests.
    created = [name for name in _leaked_runtime_dirs() if name not in (_PRE_EXISTING or ())]
    if not created:
        return
    # Raising here would surface as an internal error rather than a test failure,
    # so the leftover is reported through the terminal reporter and the session
    # exit status is set explicitly.
    message = (
        "a test created a soak runtime directory and did not remove it: "
        + ", ".join(created)
    )
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(f"ERROR: {message}", red=True, bold=True)
    else:
        print(f"ERROR: {message}")
    # A session that already failed keeps its own status; do not overwrite it.
    if session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED