"""Directories the release workflow stages into the checkout must be ignored.

The release workflow downloads published artifacts into the worktree (for
example the benchmark runtime for the release gate).  The release gate then
requires a clean checkout before it records candidate-bound evidence, so a
staging directory that git does not ignore makes the gate fail after the
downloads have happened.  Every ``actions/download-artifact`` step path that
addresses the repository is asserted to be git-ignored here.

The guard reads the workflow document once per session (the parsed document
and its top-level env block are cached), resolves the workflow's own static
``env.`` references, and fails closed on every path it cannot prove: an
unresolvable reference, a literal segment mixed with expressions, a parent
segment walking back into the checkout, or a repository-root target.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path, PurePosixPath

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-packages.yml"


ENV_REFERENCE = re.compile(r"\$\{\{\s*env\.([A-Za-z_](?a:\w)*)\s*\}\}")

# The GitHub Actions expression opener: ``$`` immediately followed by ``{{``.
# It is assembled from its two characters rather than written as one literal
# so this module never carries that sequence as a standalone token -- only
# full expressions (fixtures and the documented roots below) contain it
# verbatim -- and every opener comparison reads it through this constant.
_GH_EXPR_OPEN = "$" + "{{"

# Each iteration of the resolution loop below substitutes one ``env.NAME``
# reference; a path whose reference chain outlasts this bound fails closed
# instead of being trusted.  The workflow's own paths substitute a single
# static value, so only a crafted chain can reach the bound.
_MAX_ENV_SUBSTITUTIONS = 8


@lru_cache(maxsize=1)
def _workflow_document() -> dict:
    """The parsed workflow, cached: the document is fixed while the suite runs.

    Callers must not mutate the returned mapping.
    """
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document if isinstance(document, dict) else {}


@lru_cache(maxsize=1)
def _workflow_env() -> dict[str, str]:
    """Static top-level env values of the workflow.

    Parsed once per session: every download path is resolved against the
    document.  Callers must not mutate the returned mapping.
    """
    env = _workflow_document().get("env")
    if not isinstance(env, dict):
        return {}
    return {
        name: value for name, value in env.items() if isinstance(value, str)
    }


def _step_effective_env(step: object) -> dict[str, object]:
    """Merge workflow, job, and step env values in GitHub precedence order."""
    if not isinstance(step, dict):
        return {name: value for name, value in _workflow_env().items()}
    effective: dict[str, object] = {
        name: value for name, value in _workflow_env().items()
    }
    scoped = step.get("_effective_env")
    if isinstance(scoped, dict):
        return {key: value for key, value in scoped.items()
                if isinstance(key, str)}
    step_env = step.get("env")
    if isinstance(step_env, dict):
        effective.update({key: value for key, value in step_env.items()
                          if isinstance(key, str)})
    return effective


def _resolve_static_env(
    text: str, env: Mapping[str, object] | None = None
) -> str | None:
    """Substitute the workflow's own ``${{ env.NAME }}`` references.

    Only names defined as static string values in the workflow's top-level
    env block are resolved; any other reference (a missing name, a
    non-string value, a value that is itself an expression) returns None
    so the caller fails closed instead of trusting an unverifiable path.
    ``env`` defaults to the workflow's own block; a caller may inject a
    synthetic mapping to drive the fail-closed branches.
    """
    if _GH_EXPR_OPEN not in text:
        # Nothing to substitute and no document to read: the scan loop only
        # replaces ``env.`` references, which need the opener.
        return text
    if env is None:
        env = _workflow_env()
    resolved = text
    for _ in range(_MAX_ENV_SUBSTITUTIONS):
        match = ENV_REFERENCE.search(resolved)
        if match is None:
            return resolved
        value = env.get(match.group(1))
        if not isinstance(value, str) or _GH_EXPR_OPEN in value:
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


def _step_path(step: object) -> object:
    """The step's ``with.path`` value; None when the shape is unreadable.

    A step whose ``with`` value is not a mapping names no path the guard can
    read.  The predicates treat that like a missing path -- root staging --
    so a malformed step fails closed instead of raising ``AttributeError``.
    """
    if not isinstance(step, dict):
        return None
    with_values = step.get("with")
    if not isinstance(with_values, dict):
        return None
    return with_values.get("path")


def _repository_path(step: object) -> str | None:
    """The static repository-relative download path of the step, if any.

    Only paths that are fully static after resolving the workflow's own
    env references and stripping an optional ``${{ github.workspace }}``
    prefix can be checked against git's ignore rules; anything unresolved
    is left to the fail-closed predicate below.
    """
    path = _step_path(step)
    if not isinstance(path, str):
        return None
    resolved = _resolve_static_env(path, _step_effective_env(step))
    if resolved is None:
        return None
    path = resolved
    if path.startswith(WORKSPACE_EXPRESSION):
        path = path[len(WORKSPACE_EXPRESSION):].lstrip("/")
    if path.startswith("/") or _GH_EXPR_OPEN in path or _has_parent_segment(path):
        return None
    normalized = PurePosixPath(path).as_posix()
    return None if normalized == "." else normalized


def _stages_into_repository_root(step: object) -> bool:
    """True when a download step targets the checkout root itself.

    ``actions/download-artifact`` without ``path`` drops files into the
    workspace root, and an explicit github.workspace expression names it;
    either way the files cannot be attributed to a git-ignored staging
    directory, so such steps are rejected outright.
    """
    if not isinstance(step, dict):
        return False
    path = _step_path(step)
    if path is None:
        # No path key (root staging) or an unreadable with: block: either way
        # the step cannot be attributed to a git-ignored staging directory.
        return True
    if not isinstance(path, str):
        return False
    path = _resolve_static_env(path, _step_effective_env(step))
    if path is None or path.startswith("/"):
        return False
    remainder = path
    if remainder.startswith(WORKSPACE_EXPRESSION):
        remainder = remainder[len(WORKSPACE_EXPRESSION):].lstrip("/")
    if _GH_EXPR_OPEN in remainder or _has_parent_segment(remainder):
        return False
    return PurePosixPath(remainder).as_posix() == "."


def _unresolvable_repository_path(step: object) -> str | None:
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
    path = _step_path(step)
    if not isinstance(path, str):
        return None
    resolved = _resolve_static_env(path, _step_effective_env(step))
    if resolved is None:
        return path
    path = resolved
    if _has_parent_segment(path):
        # A parent segment can walk back from an external root (for example
        # ${{ runner.temp }}/../<repo>/<repo>/...) into the checkout.
        return path
    if path.startswith(WORKSPACE_EXPRESSION):
        remainder = path[len(WORKSPACE_EXPRESSION):].lstrip("/")
        return path if _GH_EXPR_OPEN in remainder else None
    if path.startswith("/"):
        return path
    if path.startswith(_GH_EXPR_OPEN):
        # Only roots with documented external semantics are provably outside
        # the checkout; any other expression (matrix, env, inputs, ...) can
        # resolve to a workspace-relative path.  The remainder of an external
        # root must stay fully static.
        root = _external_expression_root(path)
        if root is None:
            return path
        remainder = path[len(root):].lstrip("/")
        return path if _GH_EXPR_OPEN in remainder else None
    if _GH_EXPR_OPEN in path:
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


def _env_scope(value: object) -> dict[str, object]:
    """Keep every named env override, including non-static values to fail closed."""
    if not isinstance(value, Mapping):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _download_steps(document: object | None = None) -> list[dict]:
    """Every artifact download step with its effective scoped environment."""
    if document is None:
        document = _workflow_document()
    if not isinstance(document, dict):
        return []
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return []
    workflow_env = _env_scope(document.get("env"))
    steps: list[dict] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        job_steps = job.get("steps")
        if not isinstance(job_steps, list):
            continue
        job_env = _env_scope(job.get("env"))
        for step in job_steps:
            if not isinstance(step, dict) or not _is_download_artifact(step):
                continue
            effective_env = dict(workflow_env)
            effective_env.update(job_env)
            effective_env.update(_env_scope(step.get("env")))
            scoped_step = dict(step)
            scoped_step["_effective_env"] = effective_env
            steps.append(scoped_step)
    return steps


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


def test_parent_segment_walks_back_into_the_checkout() -> None:
    """A ``..`` segment is unresolvable even behind an external expression root.

    ``${{ runner.temp }}/../<repo>`` starts at a documented-external root but
    walks back up into the checkout, so it must fail closed rather than be
    skipped as an outside-the-checkout path.
    """
    parent_walk = "${{ runner.temp }}/../" + "nginx-markdown-for-agents"
    assert _has_parent_segment(parent_walk)
    assert _unresolvable_repository_path({"with": {"path": parent_walk}}) == parent_walk
    assert _repository_path({"with": {"path": parent_walk}}) is None
    # Positive controls: the identical shape without the parent segment, and
    # a plain workspace-relative directory, both stay accepted.
    assert not _has_parent_segment("${{ runner.temp }}/staging")
    assert _unresolvable_repository_path(
        {"with": {"path": "${{ runner.temp }}/staging"}}
    ) is None
    assert _repository_path({"with": {"path": "runtime/staged"}}) == "runtime/staged"


def test_resolve_static_env_fails_closed_on_unprovable_values() -> None:
    """Every unresolvable ``env.`` reference returns None, never a guess.

    The synthetic mappings cover the branches the workflow's own env block
    cannot exercise: an unknown name, a non-string value, and a value that is
    itself an expression.
    """
    def resolve(text: str, env: Mapping[str, object]) -> str | None:
        return _resolve_static_env(text, env)

    assert resolve("${{ env.KNOWN }}/x", {"KNOWN": "value"}) == "value/x"
    assert resolve("${{ env.MISSING }}/x", {"KNOWN": "value"}) is None
    for non_string in (None, 7, ["value"], {"nested": "value"}, True):
        assert resolve("${{ env.NAME }}", {"NAME": non_string}) is None, (
            f"non-string env value {non_string!r} must fail closed"
        )
    assert resolve("${{ env.NAME }}", {"NAME": "${{ secrets.TOKEN }}"}) is None
    assert resolve("${{ env.NAME }}", {"NAME": "$" + "{{ matrix.name }}"}) is None
    # Positive controls: a value that merely contains a non-expression dollar
    # sign, and a reference-free path, still resolve.
    assert resolve("${{ env.NAME }}", {"NAME": "$5/staged"}) == "$5/staged"
    assert resolve("staged/", {"NAME": "unused"}) == "staged/"


def test_resolve_static_env_bounds_the_reference_chain() -> None:
    """A reference chain at or past the bound is unresolvable.

    Each loop iteration substitutes one reference, so a chain resolves only
    while its length is strictly below ``_MAX_ENV_SUBSTITUTIONS``: the loop
    that substitutes the last reference has no iteration left to observe the
    reference-free result.
    """
    def chain(count: int) -> str:
        return "".join("${{ env.A }}" for _ in range(count))

    env = {"A": "value"}
    bound = _MAX_ENV_SUBSTITUTIONS
    assert bound >= 2, "the bound must leave room for a resolving chain"
    assert _resolve_static_env(chain(bound - 1), env) == "value" * (bound - 1)
    assert _resolve_static_env(chain(bound), env) is None
    assert _resolve_static_env(chain(bound + 1), env) is None


def test_malformed_step_shapes_never_crash_the_guard() -> None:
    """A malformed workflow cannot crash the guard with AttributeError.

    Unreadable step and ``with`` shapes fail closed (root staging), while a
    well-formed step with a staging path stays accepted.
    """
    malformed = (
        {"with": None},
        {"with": "dist/"},
        {"with": ["dist/"]},
        {"with": 7},
    )
    for step in malformed:
        assert _stages_into_repository_root(step), (
            f"unreadable with: block {step!r} must fail closed"
        )
        assert _repository_path(step) is None
        assert _unresolvable_repository_path(step) is None
    # Positive controls: non-mapping steps are skipped rather than rejected,
    # and the documented staging directories still resolve.
    assert not _stages_into_repository_root(None)
    assert _stages_into_repository_root({"uses": "actions/download-artifact@v8"})
    assert _repository_path({"with": {"path": "release-assets/"}}) == "release-assets"


def test_download_steps_skips_malformed_containers() -> None:
    """A job that is not a mapping, a non-list ``steps``, and a non-mapping
    step are each skipped instead of raising from the scan.

    The synthetic documents go through the production scan, so the guard's
    iteration itself is what is exercised -- not a copy of its logic.
    """
    document = {
        "jobs": {
            "scalar-job": "not-a-mapping",
            "steps-not-a-list": {"steps": "run: echo hi"},
            "steps-not-iterable": {"steps": 7},
            "steps-null": {"steps": None},
            "steps-are-scalars": {"steps": ["echo hi", 7, None]},
            "mixed": {
                "steps": [
                    "echo hi",
                    {
                        "uses": "actions/download-artifact@v8",
                        "with": {"path": "staged/"},
                    },
                ]
            },
        }
    }
    steps = _download_steps(document)
    assert [step.get("with", {}).get("path") for step in steps] == ["staged/"]
    for malformed in (
        {"jobs": None},
        {"jobs": ["not-a-mapping"]},
        {},
        "not-a-document",
        {"jobs": {"ok": {"steps": []}}},
    ):
        assert _download_steps(malformed) == [], (
            f"malformed document {malformed!r} must yield no steps"
        )
    # Positive control: the real workflow still yields its own steps.
    assert _download_steps()


def test_workflow_env_is_cached_and_static() -> None:
    """The env mapping is the cached document's static string entries.

    Repeated calls must not re-read the workflow, and every kept value must be
    a plain string: a value that is itself an expression is exactly what the
    resolver fails closed on.
    """
    _workflow_env.cache_clear()
    _workflow_document.cache_clear()
    first = _workflow_env()
    assert first == _workflow_env(), "repeated calls must return the same mapping"
    assert _workflow_env.cache_info().hits >= 1
    assert first
    for name, value in first.items():
        assert isinstance(name, str)
        assert isinstance(value, str)
        assert _GH_EXPR_OPEN not in value, (
            f"workflow env {name!r} is itself an expression; the resolver "
            "fails closed on it"
        )


def test_download_path_uses_workflow_job_then_step_environment() -> None:
    """Narrower GitHub Actions env scopes shadow wider path variables."""
    document = {
        "env": {"STAGE": "release-assets"},
        "jobs": {
            "release": {
                "env": {"STAGE": "job-staging"},
                "steps": [
                    {
                        "uses": "actions/download-artifact@v8",
                        "with": {"path": "${{ env.STAGE }}"},
                    },
                    {
                        "uses": "actions/download-artifact@v8",
                        "env": {"STAGE": "."},
                        "with": {"path": "${{ env.STAGE }}"},
                    },
                    {
                        "uses": "actions/download-artifact@v8",
                        "env": {"STAGE": "step-staging"},
                        "with": {"path": "${{ env.STAGE }}/downloaded/"},
                    },
                ],
            }
        },
    }
    steps = _download_steps(document)
    assert len(steps) == 3
    assert _repository_path(steps[0]) == "job-staging"
    assert _stages_into_repository_root(steps[1])
    assert _repository_path(steps[1]) is None
    assert _repository_path(steps[2]) == "step-staging/downloaded"
    for step in steps:
        assert _unresolvable_repository_path(step) is None


def test_shadowed_unresolvable_step_environment_fails_closed() -> None:
    """A dynamic step override cannot fall back to a safe workflow value."""
    document = {
        "env": {"STAGE": "release-assets"},
        "jobs": {
            "release": {
                "steps": [
                    {
                        "uses": "actions/download-artifact@v8",
                        "env": {"STAGE": "${{ matrix.stage }}"},
                        "with": {"path": "${{ env.STAGE }}/downloaded"},
                    }
                ]
            }
        },
    }
    (step,) = _download_steps(document)
    assert _unresolvable_repository_path(step) == "${{ env.STAGE }}/downloaded"
    assert _repository_path(step) is None
