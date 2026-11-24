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
# An inherited repository location would point fixture commands at a
# different checkout than the one under test.
GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_CEILING_DIRECTORIES",
)


def sanitized_environment(extra=None) -> dict:
    """Return the environment without inherited git repository locations."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in GIT_LOCATION_VARS
    }
    env.pop("VIRTUAL_ENV", None)
    if extra:
        env.update(extra)
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def _run(args: List[str], cwd: Path, env=None) -> subprocess.CompletedProcess:
    """Run a subprocess with captured output and a sanitized environment."""
    return subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=sanitized_environment(env),
        check=False,
    )


def _changed_paths(repo: Path) -> List[str]:
    """Return worktree-relative paths that differ from HEAD."""
    completed = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "-z"],
        repo,
    )
    if completed.returncode != 0:
        raise RuntimeError("git status failed: {}".format(completed.stderr))
    fields = completed.stdout.split("\0")
    paths: List[str] = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 4:
            continue
        status = record[:2]
        paths.append(record[3:])
        if "R" in status or "C" in status:
            # A rename or copy record carries the origin path in the next
            # NUL-separated field, which the overlay has to remove as well.
            origin = fields[index] if index < len(fields) else ""
            index += 1
            if origin:
                paths.append(origin)
    return paths


def _remove_overlay_target(target: Path) -> None:
    """Remove one conflicting path inside the isolated fixture worktree."""
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()


def _overlay_entry(source: Path, target: Path) -> None:
    """Copy one changed file, directory, or symlink into the fixture tree."""
    if source.is_symlink():
        target.parent.mkdir(parents=True, exist_ok=True)
        _remove_overlay_target(target)
        os.symlink(os.readlink(source), target)
        return
    if source.is_dir():
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            _remove_overlay_target(target)
        # A nested symlink stays a link instead of copying its target.
        shutil.copytree(source, target, symlinks=True, dirs_exist_ok=True)
        return
    if source.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        # copy2 follows a destination symlink and would overwrite its target,
        # so the link itself is removed before the regular file is copied.
        if target.is_symlink() or target.is_dir():
            _remove_overlay_target(target)
        shutil.copy2(source, target)
        return
    _remove_overlay_target(target)


def _overlay(repo: Path, worktree: Path) -> None:
    """Copy uncommitted working-tree content onto the detached worktree."""
    for relative in _changed_paths(repo):
        _overlay_entry(repo / relative, worktree / relative)


@pytest.fixture
def isolated_worktree(tmp_path: Path):
    """Provide a detached worktree that mirrors the working tree, then clean it."""
    # A unique basename keeps two parallel workers from colliding on the
    # administrative entry git derives from the directory name.
    worktree = tmp_path / "repo-{}".format(os.getpid())
    added = _run(
        ["git", "worktree", "add", "--detach", str(worktree), "HEAD"], REPO_ROOT
    )
    if added.returncode != 0:
        raise RuntimeError("cannot create the isolated worktree: {}".format(added.stderr))
    try:
        _overlay(REPO_ROOT, worktree)
        yield worktree
    finally:
        # Only remove this worktree.  A repository-wide prune would race a
        # sibling worker that is still using its own checkout.
        _run(["git", "worktree", "remove", "--force", str(worktree)], REPO_ROOT)


def run_compat_check(worktree: Path, *extra: str) -> subprocess.CompletedProcess:
    """Run the compatibility gate inside an isolated worktree."""
    env = {"PYTHONPATH": str(worktree)}
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
