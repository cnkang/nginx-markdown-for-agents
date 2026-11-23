"""Isolated working trees for compatibility negative fixtures.

Negative fixtures must never touch the developer's checkout.  Each test gets a
detached git worktree pinned at the current commit, then the uncommitted
changes from the real working tree are overlaid so the fixture runs the code
under test rather than the last commit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPAT_MODULE = "tools.compatibility"


def _run(args: List[str], cwd: Path, env=None) -> subprocess.CompletedProcess:
    """Run a subprocess with captured output."""
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _changed_paths(repo: Path) -> List[str]:
    """Return worktree-relative paths that differ from HEAD."""
    completed = _run(["git", "status", "--porcelain=v1", "-z"], repo)
    if completed.returncode != 0:
        raise RuntimeError("git status failed: {}".format(completed.stderr))
    paths = []
    for record in completed.stdout.split("\0"):
        if len(record) < 4:
            continue
        status = record[:2]
        path = record[3:]
        if "R" in status or "C" in status:
            continue
        paths.append(path)
    return paths


def _overlay(repo: Path, worktree: Path) -> None:
    """Copy uncommitted working-tree content onto the detached worktree."""
    for relative in _changed_paths(repo):
        source = repo / relative
        target = worktree / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        elif source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        elif target.exists():
            target.unlink()


@pytest.fixture()
def isolated_worktree(tmp_path: Path):
    """Provide a detached worktree that mirrors the working tree, then clean it."""
    worktree = tmp_path / "repo"
    added = _run(
        ["git", "worktree", "add", "--detach", str(worktree), "HEAD"], REPO_ROOT
    )
    if added.returncode != 0:
        raise RuntimeError("cannot create the isolated worktree: {}".format(added.stderr))
    try:
        _overlay(REPO_ROOT, worktree)
        yield worktree
    finally:
        _run(["git", "worktree", "remove", "--force", str(worktree)], REPO_ROOT)
        _run(["git", "worktree", "prune"], REPO_ROOT)


def run_compat_check(worktree: Path, *extra: str) -> subprocess.CompletedProcess:
    """Run the compatibility gate inside an isolated worktree."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(worktree)
    env.pop("VIRTUAL_ENV", None)
    return _run(
        [
            "python3",
            "-m",
            COMPAT_MODULE,
            "check",
            "--index",
            str(worktree / "tools" / "compatibility" / "contracts" / "INDEX.json"),
            "--repo-root",
            str(worktree),
            *extra,
        ],
        worktree,
        env,
    )
