"""Resolve immutable contract sources: a release tag or the working tree.

Every contract read goes through this module so a manifest records exactly one
revision.  Reading a mixture of revisions would let an inventory from one
release describe code from another, which is the failure mode the gate exists
to prevent.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Dict, Optional

# Bootstrap the repository root before the tools.* import so the module also
# resolves when another script loads it from outside the package tree.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tools.compatibility.model import ContractError  # noqa: E402
from tools.lib.path_validation import validate_read_path  # noqa: E402

GIT_TIMEOUT_SECONDS = 120

# Git references come from the anchor index, which is file content.  An
# allowlist keeps a crafted value from becoming a git option or a second
# argument, and it excludes every revision expression that could execute a
# hook or reach outside the repository.
_REF_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-"
)
_COMMIT_CHARS = set("0123456789abcdef")


def validate_ref(ref: str, purpose: str = "git reference") -> str:
    """Return ``ref`` when it is a safe single git revision or tag name."""
    if (
        not ref
        or ref.startswith("-")
        or ".." in ref
        or set(ref) - _REF_CHARS
    ):
        raise ContractError("unsafe {}: {!r}".format(purpose, ref))
    return ref


def validate_commit(commit: str, purpose: str = "commit") -> str:
    """Return ``commit`` when it is a full lowercase hexadecimal SHA."""
    if len(commit) != 40 or set(commit) - _COMMIT_CHARS:
        raise ContractError(
            "{} must be 40 lowercase hexadecimal characters: {!r}".format(
                purpose, commit
            )
        )
    return commit


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


# Only these option words may reach git.  Every other argument must be a plain
# reference, commit, or path token, so a crafted value cannot add an option.
_ALLOWED_GIT_OPTIONS = frozenset(
    {"-C", "-t", "--verify", "--show-toplevel", "rev-parse", "cat-file", "show"}
)
_SAFE_GIT_TOKEN = re.compile(r"[A-Za-z0-9._/@{}:=+~^-]{1,4096}")
# A directory path may carry a space or a comma; a reference may not.
# A checkout path is passed to `git -C`, which consumes it as a directory, so
# the guard rejects control characters and traversal components rather than
# restricting the characters a real path may contain.
_SAFE_GIT_PATH = re.compile(r"[^\x00-\x1f\x7f]{1,4096}")


# Location variables would make `git -C` operate on a different repository
# than the one the caller selected.
_GIT_LOCATION_VARS = (
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


def _git_environment() -> dict:
    """Return the process environment without inherited repository locations."""
    return {
        key: value
        for key, value in os.environ.items()
        if key not in _GIT_LOCATION_VARS
    }


def _git(repo_root: str, *args: str) -> str:
    """Run one git command and return stripped stdout, failing closed.

    The working directory and every argument pass an allowlist before the
    command starts.  Only known option words are accepted, and every other
    token must be a plain reference, commit, or path, so a crafted value cannot
    add an option or a second command.
    """
    if _SAFE_GIT_PATH.fullmatch(repo_root) is None or ".." in repo_root.split("/"):
        raise ContractError("unsafe git working directory: {!r}".format(repo_root))
    if not os.path.isdir(repo_root):
        raise ContractError(
            "git working directory is not a directory: {!r}".format(repo_root)
        )
    for argument in args:
        if argument in _ALLOWED_GIT_OPTIONS:
            continue
        if _SAFE_GIT_TOKEN.fullmatch(argument) is None or argument.startswith("-"):
            raise ContractError("unsafe git argument: {!r}".format(argument))
    try:
        # Every element passed the allowlist above: the working directory has
        # no traversal component, each option word is a known constant, and each
        # operand is limited to reference, commit, and path characters and may
        # not start with a dash.  No value can become an option.
        completed = subprocess.run(  # NOSONAR
            ["git", "-C", repo_root, *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            env=_git_environment(),
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
    """Resolve a release tag through ``refs/tags`` and record both objects.

    Resolving the bare name could pick a branch of the same name, so the
    reference is qualified first and the resolved object is peeled afterwards.
    """
    validate_ref(tag, "tag")
    reference = "refs/tags/{}".format(tag)
    object_id = _git(repo_root, "rev-parse", "--verify", reference)
    validate_commit(object_id, "tag object")
    object_type = _git(repo_root, "cat-file", "-t", object_id)
    if object_type == "tag":
        tag_object = object_id
        commit = _git(
            repo_root, "rev-parse", "--verify", "{}^{{commit}}".format(object_id)
        )
    elif object_type == "commit":
        tag_object = None
        commit = object_id
    else:
        raise ContractError(
            "tag {} resolves to unsupported object type {}".format(tag, object_type)
        )
    validate_commit(commit, "tag commit")
    return SourceRevision(
        kind="release-tag", label=tag, commit=commit, tag_object=tag_object
    )


def resolve_commit(repo_root: str, revision: str) -> SourceRevision:
    """Resolve a commit-ish to a full commit SHA."""
    validate_ref(revision, "revision")
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
            resolved = validate_read_path(absolute, purpose="contract input")
            repository_root = os.path.realpath(self.repo_root)
            if (
                os.path.commonpath([repository_root, os.path.realpath(resolved)])
                != repository_root
            ):
                raise ContractError(
                    "contract input {} escapes the repository".format(path)
                )
            try:
                with open(resolved, "rb") as handle:
                    payload = handle.read()
            except OSError as error:
                raise ContractError(
                    "cannot read {} from the working tree: {}".format(path, error)
                )
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
                    env=_git_environment(),
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
