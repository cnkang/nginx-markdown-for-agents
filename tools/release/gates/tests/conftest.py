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


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Fail the session when a test leaked a runtime directory."""
    del session, exitstatus
    leftover = _leaked_runtime_dirs()
    assert not leftover, {
        "leftover": leftover,
        "msg": "a test created a soak runtime directory and did not remove it",
    }