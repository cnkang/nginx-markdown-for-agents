"""Directories the release workflow stages into the checkout must be ignored.

The release workflow downloads published artifacts into the worktree (for
example the benchmark runtime for the release gate).  The release gate then
requires a clean checkout before it records candidate-bound evidence, so a
staging directory that git does not ignore makes the gate fail after the
downloads have happened.  Every ``actions/download-artifact`` step path that
addresses the repository is asserted to be git-ignored here.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"


ENV_REFERENCE = re.compile(r"\$\{\{\s*env\.([A-Za-z_](?a:\w)*)\s*\}\}")


def _workflow_env() -> dict[str, str]:
    """Static top-level env values of the workflow."""
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    env = document.get("env") if isinstance(document, dict) else None
    if not isinstance(env, dict):
        return {}
    return {
        name: value for name, value in env.items() if isinstance(value, str)
    }


def _resolve_static_env(text: str) -> str | None:
    """Substitute the workflow's own ``${{ env.NAME }}`` references.

    Only names defined as static string values in the workflow's top-level
    env block are resolved; any other reference (a missing name, a
    non-string value, a value that is itself an expression) returns None
    so the caller fails closed instead of trusting an unverifiable path.
    """
    env = _workflow_env()
    resolved = text
    for _ in range(8):
        match = ENV_REFERENCE.search(resolved)
        if match is None:
            return resolved
        value = env.get(match.group(1))
        if not isinstance(value, str) or "${" + "{" in value:
            return None
        resolved = resolved[: match.start()] + value + resolved[match.end():]
    return None


def _is_download_artifact(step: object) -> bool:
    if not isinstance(step, dict):
        return False
    uses = step.get("uses", "")
    return isinstance(uses, str) and uses.startswith("actions/download-artifact@")


WORKSPACE_EXPRESSION = "${{ github.workspace }}"
# Expression roots that are documented to resolve outside the checkout.
EXTERNAL_EXPRESSION_ROOTS = ("${{ runner.temp }}",)


def _repository_path(step: dict) -> str | None:
    """The static repository-relative download path of the step, if any.

    Only paths that are fully static after resolving the workflow's own
    env references and stripping an optional ``${{ github.workspace }}``
    prefix can be checked against git's ignore rules; anything unresolved
    is left to the fail-closed predicate below.
    """
    path = step.get("with", {}).get("path")
    if not isinstance(path, str):
        return None
    resolved = _resolve_static_env(path)
    if resolved is None:
        return None
    path = resolved
    if path.startswith(WORKSPACE_EXPRESSION):
        path = path[len(WORKSPACE_EXPRESSION):].lstrip("/")
    if path.startswith("/") or "${" + "{" in path or ".." in path.split("/"):
        return None
    return path.rstrip("/") or None


def _stages_into_repository_root(step: dict) -> bool:
    """True when a download step targets the checkout root itself.

    ``actions/download-artifact`` without ``path`` drops files into the
    workspace root, and an explicit github.workspace expression names it;
    either way the files cannot be attributed to a git-ignored staging
    directory, so such steps are rejected outright.
    """
    if not isinstance(step, dict):
        return False
    path = step.get("with", {}).get("path")
    if path is None:
        return True
    if not isinstance(path, str) or path.startswith("/"):
        return False
    remainder = path
    if remainder.startswith(WORKSPACE_EXPRESSION):
        remainder = remainder[len(WORKSPACE_EXPRESSION):].lstrip("/")
    if "${{" in remainder:
        return False
    return remainder.rstrip("/") == ""


def _unresolvable_repository_path(step: dict) -> str | None:
    """A download path that cannot be proven safe, so the guard fails closed.

    - The workflow's own static env references are resolved first; an env
      reference that cannot be resolved (unknown name, non-string value,
      value containing another expression) fails closed.
    - A path rooted in ``${{ github.workspace }}`` resolves inside the
      checkout; if its remainder still contains expressions it cannot be
      proven git-ignored.
    - A literal absolute path cannot be proven to sit outside the checkout.
    - A literal segment followed by expressions (``staging/${{ x }}``)
      cannot be proven git-ignored.
    A path rooted in any other expression (for example ``${{ runner.temp }}``)
    addresses a location outside the checkout and is skipped.
    """
    if not isinstance(step, dict):
        return None
    path = step.get("with", {}).get("path")
    if not isinstance(path, str):
        return None
    resolved = _resolve_static_env(path)
    if resolved is None:
        return path
    path = resolved
    if _has_parent_segment(path):
        # A parent segment can walk back from an external root (for example
        # ${{ runner.temp }}/../<repo>/<repo>/...) into the checkout.
        return path
    if path.startswith(WORKSPACE_EXPRESSION):
        remainder = path[len(WORKSPACE_EXPRESSION):].lstrip("/")
        return path if "$" + "{{" in remainder else None
    if path.startswith("/"):
        return path
    if path.startswith("$" + "{{"):
        # Only roots with documented external semantics are provably outside
        # the checkout; any other expression (matrix, env, inputs, ...) can
        # resolve to a workspace-relative path.  The remainder of an external
        # root must stay fully static.
        root = _external_expression_root(path)
        if root is None:
            return path
        remainder = path[len(root):].lstrip("/")
        return path if "$" + "{{" in remainder else None
    if "$" + "{{" in path:
        return path
    return None


def _has_parent_segment(path: str) -> bool:
    """True when the path contains a ``..`` segment."""
    return ".." in path.split("/")


def _external_expression_root(path: str) -> str | None:
    """The matching documented-external expression root, if any."""
    for root in EXTERNAL_EXPRESSION_ROOTS:
        if path == root or path.startswith(root + "/"):
            return root
    return None


def _download_steps() -> list[dict]:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return [
        step
        for job in document.get("jobs", {}).values()
        if isinstance(job, dict)
        for step in job.get("steps", [])
        if _is_download_artifact(step)
    ]


def test_download_artifact_paths_are_git_ignored() -> None:
    steps = _download_steps()
    assert steps, "expected download-artifact steps"
    for step in steps:
        assert not _stages_into_repository_root(step), (
            "a download-artifact step stages into the checkout root; give "
            "it a git-ignored staging directory"
        )
        unresolvable = _unresolvable_repository_path(step)
        assert unresolvable is None, (
            f"download-artifact path {unresolvable!r} mixes literal segments "
            "with expressions and cannot be proven git-ignored; use a static "
            "staging directory"
        )
    paths = sorted({p for step in steps if (p := _repository_path(step))})
    for path in paths:
        result = subprocess.run(
            ["git", "check-ignore", "-q", f"{path}/.probe"],
            cwd=REPO_ROOT,
            check=False,
        )
        assert result.returncode == 0, (
            f"the release workflow stages downloads into '{path}/', which git "
            "does not ignore; the release gate requires a clean checkout"
        )


def test_repository_path_resolution() -> None:
    assert _repository_path({"with": {"path": "dist/"}}) == "dist"
    assert (
        _repository_path({"with": {"path": "${{ github.workspace }}/staged/"}})
        == "staged"
    )
    assert _repository_path({"with": {"path": "${{ runner.temp }}/x"}}) is None
    assert _repository_path({"with": {"path": "/abs/path"}}) is None
    assert _repository_path({"with": {"path": "${{ github.workspace }}"}}) is None
    assert _repository_path({}) is None


def test_root_staging_detection() -> None:
    assert _stages_into_repository_root({})
    assert _stages_into_repository_root({"with": {}})
    assert _stages_into_repository_root(
        {"with": {"path": "${{ github.workspace }}"}}
    )
    assert _stages_into_repository_root(
        {"with": {"path": "${{ github.workspace }}/"}}
    )
    assert not _stages_into_repository_root(
        {"with": {"path": "${{ github.workspace }}/staged"}}
    )
    assert not _stages_into_repository_root({"with": {"path": "dist/"}})
    assert not _stages_into_repository_root(
        {"with": {"path": "${{ runner.temp }}/x"}}
    )
    assert not _stages_into_repository_root({"with": {"path": "/abs"}})


def test_unresolvable_repository_path_detection() -> None:
    def flagged(path: str) -> bool:
        return _unresolvable_repository_path({"with": {"path": path}}) is not None

    assert flagged("staging/${{ matrix.name }}")
    assert flagged("${{ github.workspace }}/staging/${{ matrix.name }}")
    assert flagged("${{ github.workspace }}/${{ matrix.name }}")
    assert flagged("/absolute/staging")
    assert flagged("${{ matrix.name }}/staging")
    assert flagged(
        "${{ runner.temp }}/../${{ github.event.repository.name }}/"
        "${{ github.event.repository.name }}/release-assets"
    )
    assert flagged("${{ runner.temp }}/staging/../../escape")
    assert flagged("${{ runner.temp }}/${{ matrix.name }}")
    assert flagged("${{ runner.temp }}suffix")
    assert not flagged("${{ runner.temp }}/official-nginx-docker")
    assert not flagged("${{ github.workspace }}/staged/")
    assert not flagged("dist/")
    assert not flagged("")
