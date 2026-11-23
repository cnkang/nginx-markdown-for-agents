"""Resolve immutable contract sources: a release tag or the working tree.

Every contract read goes through this module so a manifest records exactly one
revision.  Reading a mixture of revisions would let an inventory from one
release describe code from another, which is the failure mode the gate exists
to prevent.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional

from .model import ContractError

GIT_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class SourceRevision:
    """One resolved contract source."""

    kind: str  # "release-tag", "commit", or "worktree"
    label: str  # tag name, abbreviated SHA, or "worktree"
    commit: str  # full 40-character commit SHA
    tag_object: Optional[str] = None  # annotated tag object SHA, if any

    def as_dict(self) -> Dict[str, Optional[str]]:
        """Return the provenance record stored in a manifest."""
        record: Dict[str, Optional[str]] = {
            "kind": self.kind,
            "label": self.label,
            "commit": self.commit,
        }
        if self.tag_object is not None:
            record["tag_object"] = self.tag_object
        return record


def _git(repo_root: str, *args: str) -> str:
    """Run one git command and return stripped stdout, failing closed."""
    try:
        completed = subprocess.run(
            ["git", "-C", repo_root, *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ContractError("git {} failed: {}".format(" ".join(args), error))
    if completed.returncode != 0:
        raise ContractError(
            "git {} failed: {}".format(
                " ".join(args), (completed.stderr or "").strip()
            )
        )
    return completed.stdout.strip()


def repo_root_for(path: str) -> str:
    """Return the git top-level directory that contains ``path``."""
    probe = path if os.path.isdir(path) else os.path.dirname(os.path.abspath(path))
    try:
        completed = subprocess.run(
            ["git", "-C", probe, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ContractError("cannot locate a git repository: {}".format(error))
    if completed.returncode != 0:
        raise ContractError("cannot locate a git repository for {}".format(path))
    return completed.stdout.strip()


def resolve_tag(repo_root: str, tag: str) -> SourceRevision:
    """Resolve a release tag to its commit and record the tag object."""
    commit = _git(repo_root, "rev-parse", "--verify", "{}^{{commit}}".format(tag))
    object_type = _git(repo_root, "cat-file", "-t", tag)
    if object_type == "tag":
        tag_object = _git(repo_root, "rev-parse", "--verify", tag)
    elif object_type == "commit":
        tag_object = None
    else:
        raise ContractError(
            "tag {} resolves to unsupported object type {}".format(tag, object_type)
        )
    if len(commit) != 40:
        raise ContractError("tag {} did not resolve to a full commit SHA".format(tag))
    return SourceRevision(
        kind="release-tag", label=tag, commit=commit, tag_object=tag_object
    )


def resolve_commit(repo_root: str, revision: str) -> SourceRevision:
    """Resolve a commit-ish to a full commit SHA."""
    commit = _git(repo_root, "rev-parse", "--verify", "{}^{{commit}}".format(revision))
    if len(commit) != 40:
        raise ContractError(
            "revision {} did not resolve to a full commit SHA".format(revision)
        )
    return SourceRevision(kind="commit", label=revision, commit=commit)


def resolve_worktree(repo_root: str) -> SourceRevision:
    """Resolve the working tree's HEAD, rejecting a detached empty repository."""
    commit = _git(repo_root, "rev-parse", "--verify", "HEAD^{commit}")
    return SourceRevision(kind="worktree", label="worktree", commit=commit)


class SourceReader:
    """Read contract inputs from a recorded source revision."""

    def __init__(self, repo_root: str, revision: SourceRevision) -> None:
        self.repo_root = repo_root
        self.revision = revision
        self._cache: Dict[str, bytes] = {}

    def read_bytes(self, path: str) -> bytes:
        """Return the file bytes at the recorded revision, failing closed."""
        if path in self._cache:
            return self._cache[path]
        if self.revision.kind == "worktree":
            absolute = os.path.join(self.repo_root, path)
            if not os.path.isfile(absolute):
                raise ContractError(
                    "contract input {} is missing from the working tree".format(path)
                )
            with open(absolute, "rb") as handle:
                payload = handle.read()
        else:
            try:
                completed = subprocess.run(
                    [
                        "git",
                        "-C",
                        self.repo_root,
                        "show",
                        "{}:{}".format(self.revision.commit, path),
                    ],
                    capture_output=True,
                    timeout=GIT_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as error:
                raise ContractError(
                    "cannot read {} at {}: {}".format(path, self.revision.label, error)
                )
            if completed.returncode != 0:
                raise ContractError(
                    "contract input {} is missing at {}: {}".format(
                        path,
                        self.revision.label,
                        (completed.stderr or b"").decode("utf-8", "replace").strip(),
                    )
                )
            payload = completed.stdout
        self._cache[path] = payload
        return payload

    def read_text(self, path: str) -> str:
        """Return the file text at the recorded revision."""
        try:
            return self.read_bytes(path).decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContractError(
                "contract input {} is not valid UTF-8: {}".format(path, error)
            )


def verify_tag_commit(repo_root: str, tag: str, recorded_commit: str) -> None:
    """Fail when a tag no longer resolves to the recorded commit."""
    actual = resolve_tag(repo_root, tag)
    if actual.commit != recorded_commit:
        raise ContractError(
            "tag {} resolves to {}, but the anchor records {}".format(
                tag, actual.commit, recorded_commit
            )
        )
