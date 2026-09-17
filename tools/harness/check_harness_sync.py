#!/usr/bin/env python3
"""Validate repo-owned harness truth surfaces and optional local adapters."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# The repository root has to be importable before any `tools.*` import below, so
# the script runs the same way whether it is started by a Makefile target or by
# hand.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

try:
    from tools.harness.constants import (
        FAIL,
        PASS,
        SKIP_NOT_PRESENT,
        WARN_NEEDS_AUTHOR_REVIEW,
    )
except ModuleNotFoundError:
    from constants import (  # type: ignore[no-redef]  # noqa: F401
        FAIL,
        PASS,
        SKIP_NOT_PRESENT,
        WARN_NEEDS_AUTHOR_REVIEW,
    )


from tools.lib.executable_validation import (  # noqa: E402
    resolve_approved_executable,
)
README_FILENAME = "README.md"
DOCS_HARNESS_README = f"docs/harness/{README_FILENAME}"
RISK_PACKS_README = f"risk-packs/{README_FILENAME}"
FUZZ_README_REL = f"fuzz/{README_FILENAME}"
COMPONENT_FUZZ_README_REL = f"components/rust-converter/fuzz/{README_FILENAME}"
GITHUB_WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CFLITE_PR_WORKFLOW = "cflite_pr.yml"
CFLITE_BATCH_WORKFLOW = "cflite_batch.yml"
CFLITE_CRON_WORKFLOW = "cflite_cron.yml"
CLUSTERFUZZ_DOCKERFILE = ".clusterfuzzlite/Dockerfile"
MANIFEST_PATH = REPO_ROOT / "docs" / "harness" / "routing-manifest.json"
E2E_HARNESS_DIR = REPO_ROOT / "tools" / "e2e-harness"
E2E_HARNESS_CARGO = E2E_HARNESS_DIR / "Cargo.toml"
README_PATH = REPO_ROOT / "docs" / "harness" / README_FILENAME
CORE_PATH = REPO_ROOT / "docs" / "harness" / "core.md"
SUMMARY_PATH = REPO_ROOT / "docs" / "harness" / "routing-manifest.md"
AGENTS_PATH = REPO_ROOT / "AGENTS.md"
RECENT_ANALYSIS_REPORT_GLOB = "docs/project/recent-git-harness-steering-analysis-*.md"
REMEDIATION_STATUSES = {
    "fixed",
    "intentionally deferred",
    "not applicable after review",
}
E2E_PYTHON_DIR = REPO_ROOT / "components" / "nginx-module" / "tests" / "e2e"
REMOVED_PYTHON_E2E_FILES = (
    "test_streaming_e2e.py",
    "test_streaming_failure_cache_e2e.py",
)
MIGRATED_SCENARIO_WRAPPERS = {
    "tools/e2e/verify_accept_negotiation_e2e.sh": "accept-negotiation",
    "tools/e2e/verify_metrics_endpoint_e2e.sh": "metrics-endpoint",
    "tools/e2e/verify_conditional_requests_e2e.sh": "conditional-requests",
    "tools/e2e/verify_auth_cache_e2e.sh": "auth-cache",
    "tools/e2e/verify_status_codes_e2e.sh": "status-codes",
}
DOCKER_RUNTIME_PATHS = (
    CLUSTERFUZZ_DOCKERFILE,
    "examples/docker/Dockerfile.official-nginx-source-build",
    "tools/build_release/Dockerfile.install-example",
)
NGINX_NON_ROOT_REQUIRED_SNIPPETS = (
    "USER nginx",
    "EXPOSE 8080",
    "pid /tmp/nginx.pid;",
    "client_body_temp_path /tmp/client_temp;",
)
CLUSTERFUZZ_NON_ROOT_REQUIRED_SNIPPETS = (
    "mkdir -p /rustc",
    "chown fuzzer:fuzzer /rustc",
    "touch /usr/lib/libFuzzingEngine.a",
    "chown fuzzer:fuzzer /usr/lib/libFuzzingEngine.a",
)
TRIVY_REQUIRED_LOCAL_EXCLUSIONS = (
    ".codeartsdoer",
    ".kiro",
    "build",
    "reports",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def _display_path(path: Path) -> str:
    """Return a repo-relative display string for *path*, falling back to the full path.

    Args:
        path: Absolute or relative path to format.

    Returns:
        Path string relative to REPO_ROOT if possible, otherwise the full path.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _load_manifest(path: Path | None = None) -> dict:
    """Load and parse the routing manifest JSON file.

    Args:
        path: Path to the manifest file. Defaults to MANIFEST_PATH.

    Returns:
        Parsed manifest as a dictionary.

    Raises:
        ValueError: If the file cannot be read or contains invalid JSON.
    """
    path = path or MANIFEST_PATH
    def reject_nonfinite(value: str) -> None:
        raise ValueError(f"non-finite JSON number is not allowed: {value}")

    try:
        document = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=reject_nonfinite
        )
    except (OSError, ValueError) as exc:
        raise ValueError(f"failed to load harness manifest from {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"failed to load harness manifest from {path}: root must be an object")
    return document


def _required_text(path: Path, needles: list[str]) -> list[str]:
    """Return the subset of *needles* not found in the text at *path*.

    Args:
        path: File to search.
        needles: Exact strings that must appear in the file.

    Returns:
        List of needles absent from the file, or an unreadability marker
        if the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [f"{_display_path(path)} unreadable"]
    return [needle for needle in needles if needle not in text]


def _required_patterns(path: Path, patterns: dict[str, str]) -> list[str]:
    """Return the labels whose regex patterns are not matched in the file at *path*.

    Args:
        path: File to search.
        patterns: Mapping of label to regex pattern; each pattern is
            searched with the MULTILINE flag.

    Returns:
        List of labels whose patterns were not found, or an unreadability
        marker if the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [f"{_display_path(path)} unreadable"]
    missing: list[str] = []
    missing.extend(
        label
        for label, pattern in patterns.items()
        if not re.search(pattern, text, flags=re.MULTILINE)
    )
    return missing


def _result(name: str, status: str, detail: str) -> CheckResult:
    """Build a CheckResult with the given fields.

    Args:
        name: Check identifier.
        status: One of PASS, FAIL, SKIP_NOT_PRESENT, or WARN_NEEDS_AUTHOR_REVIEW.
        detail: Human-readable explanation of the outcome.

    Returns:
        A frozen CheckResult dataclass instance.
    """
    return CheckResult(name=name, status=status, detail=detail)


def _is_git_ignored(path: Path) -> bool:
    """Return whether Git excludes *path* from the repository worktree.

    Tracked files are not reported as ignored by ``git check-ignore`` even
    when a matching ignore pattern exists, so repository-owned adapters remain
    eligible for validation.  If Git is unavailable or *path* is outside the
    repository, preserve the existing behavior and treat it as non-ignored.
    """
    try:
        relative_path = path.relative_to(REPO_ROOT)
        git = resolve_approved_executable("git")
        if git is None:
            return False
        result = subprocess.run(
            [
                git,
                "-C",
                str(REPO_ROOT),
                "check-ignore",
                "--quiet",
                "--",
                str(relative_path),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return False
    return result.returncode == 0


def _is_nonempty_string(value: object) -> bool:
    """Return whether a manifest value is a non-empty string."""
    return isinstance(value, str) and bool(value.strip())


def _is_nonempty_string_list(value: object) -> bool:
    """Return whether a manifest value is a list of non-empty strings."""
    return isinstance(value, list) and all(_is_nonempty_string(item) for item in value)


def _is_hashable_scalar(value: object) -> bool:
    """Return whether a JSON value is a hashable scalar.

    JSON objects and arrays are unhashable and cannot join a set comparison,
    so the status contract rejects them before `set()` raises `TypeError` out
    of the structured check.
    """
    return value is None or isinstance(value, (str, int, float, bool))


def _check_manifest_top_level(manifest: dict) -> CheckResult | None:
    """Validate the required top-level manifest keys."""
    required = {
        "version",
        "truth_surfaces",
        "status_semantics",
        "spec_resolver",
        "verification_families",
        "risk_packs",
        "task_entrypoints",
    }
    missing = sorted(required - set(manifest))
    if missing:
        return _result(
            "manifest-structure", FAIL, f"missing top-level keys: {', '.join(missing)}"
        )
    return None


def _check_manifest_truth_surfaces(manifest: dict) -> CheckResult | None:
    """Validate truth-surface names and path lists."""
    truth_surfaces = manifest["truth_surfaces"]
    if not isinstance(truth_surfaces, dict):
        return _result("manifest-structure", FAIL, "truth_surfaces must be an object")
    required = {"contract", "harness", "canonical_docs", "optional_adapters"}
    missing = sorted(required - set(truth_surfaces))
    if missing:
        return _result(
            "manifest-structure",
            FAIL,
            "missing truth surface keys: " + ", ".join(missing),
        )
    for key in sorted(required):
        if not _is_nonempty_string_list(truth_surfaces[key]):
            return _result(
                "manifest-structure",
                FAIL,
                f"truth_surfaces.{key} must be a list of non-empty strings",
            )
    return None


def _check_manifest_statuses(manifest: dict) -> CheckResult | None:
    """Validate the canonical four-state status contract."""
    expected = {PASS, FAIL, SKIP_NOT_PRESENT, WARN_NEEDS_AUTHOR_REVIEW}
    actual = manifest["status_semantics"]
    if not isinstance(actual, list) or len(actual) != len(expected):
        return _result(
            "manifest-status-semantics",
            FAIL,
            "status semantics do not match the canonical four-state contract",
        )
    if not all(_is_hashable_scalar(item) for item in actual):
        return _result(
            "manifest-status-semantics",
            FAIL,
            "status semantics must contain only scalar values, not objects or arrays",
        )
    if set(actual) != expected:
        return _result(
            "manifest-status-semantics",
            FAIL,
            "status semantics do not match the canonical four-state contract",
        )
    return None


def _check_manifest_spec_resolver(manifest: dict) -> CheckResult | None:
    """Validate spec-resolver keys and value types."""
    resolver = manifest["spec_resolver"]
    if not isinstance(resolver, dict):
        return _result("manifest-spec-resolver", FAIL, "spec_resolver must be an object")
    required = {"priority", "pointer_candidates", "multiple_spec_policy", "conflict_policy"}
    missing = sorted(required - set(resolver))
    if missing:
        return _result(
            "manifest-spec-resolver",
            FAIL,
            f"missing spec resolver keys: {', '.join(missing)}",
        )
    for key in ("priority", "pointer_candidates"):
        if not _is_nonempty_string_list(resolver[key]):
            return _result(
                "manifest-spec-resolver",
                FAIL,
                f"spec_resolver.{key} must be a list of non-empty strings",
            )
    for key in ("multiple_spec_policy", "conflict_policy"):
        if not _is_nonempty_string(resolver[key]):
            return _result(
                "manifest-spec-resolver",
                FAIL,
                f"spec_resolver.{key} must be a non-empty string",
            )
    return None


def _check_manifest_families(manifest: dict) -> CheckResult | None:
    """Validate verification-family object and optional path lists."""
    families = manifest["verification_families"]
    if not isinstance(families, dict):
        return _result(
            "manifest-structure", FAIL, "verification_families must be an object"
        )
    for name, family in families.items():
        if not isinstance(family, dict):
            return _result(
                "manifest-structure", FAIL, f"verification family {name!r} must be an object"
            )
        for field in ("commands", "workflow_paths"):
            if field in family and not _is_nonempty_string_list(family[field]):
                return _result(
                    "manifest-structure",
                    FAIL,
                    f"verification family {name!r} {field} must be a list of non-empty strings",
                )
    return None


def _check_manifest_risk_pack(index: int, pack: object) -> CheckResult | None:
    """Validate one risk-pack entry."""
    if not isinstance(pack, dict):
        return _result("manifest-structure", FAIL, f"risk_packs[{index}] must be an object")
    required = {"id", "doc", "verification_families"}
    missing = sorted(required - set(pack))
    if missing:
        return _result(
            "manifest-structure",
            FAIL,
            f"risk_packs[{index}] missing keys: {', '.join(missing)}",
        )
    for field in ("id", "doc"):
        if not _is_nonempty_string(pack[field]):
            return _result(
                "manifest-structure",
                FAIL,
                f"risk_packs[{index}].{field} must be a non-empty string",
            )
    if not _is_nonempty_string_list(pack["verification_families"]):
        return _result(
            "manifest-structure",
            FAIL,
            f"risk_packs[{index}].verification_families must be a list of non-empty strings",
        )
    return None


def _check_manifest_risk_packs(manifest: dict) -> CheckResult | None:
    """Validate all risk-pack entries."""
    packs = manifest["risk_packs"]
    if not isinstance(packs, list):
        return _result("manifest-structure", FAIL, "risk_packs must be a list")
    for index, pack in enumerate(packs):
        result = _check_manifest_risk_pack(index, pack)
        if result is not None:
            return result
    return None


def _check_manifest_entrypoints(manifest: dict) -> CheckResult | None:
    """Validate task-entrypoint identifiers and routes."""
    entrypoints = manifest["task_entrypoints"]
    if not isinstance(entrypoints, list):
        return _result("manifest-structure", FAIL, "task_entrypoints must be a list")
    for index, entrypoint in enumerate(entrypoints):
        if not isinstance(entrypoint, dict):
            return _result(
                "manifest-structure", FAIL, f"task_entrypoints[{index}] must be an object"
            )
        if not all(
            _is_nonempty_string(entrypoint.get(field))
            for field in ("id", "default_route")
        ):
            return _result(
                "manifest-structure",
                FAIL,
                f"task_entrypoints[{index}] needs non-empty id and default_route",
            )
    return None


def _check_manifest_structure(manifest: dict) -> CheckResult:
    """Validate required manifest structure and nested value types."""
    if not isinstance(manifest, dict):
        return _result("manifest-structure", FAIL, "manifest root must be an object")
    checks = (
        _check_manifest_top_level,
        _check_manifest_truth_surfaces,
        _check_manifest_statuses,
        _check_manifest_spec_resolver,
        _check_manifest_families,
        _check_manifest_risk_packs,
        _check_manifest_entrypoints,
    )
    for check in checks:
        result = check(manifest)
        if result is not None:
            return result
    return _result("manifest-structure", PASS, "manifest schema looks complete")


def _iter_manifest_string_values(value: object):
    """Yield non-empty strings from a manifest field list."""
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, str) and item.strip():
            yield item


def _iter_manifest_dict_field_values(manifest: dict, field: str):
    """Yield field values and recursively visit a manifest dictionary."""
    yield from _iter_manifest_string_values(manifest.get(field))
    for key, child in manifest.items():
        if key != field:
            yield from _iter_manifest_field_values(child, field)


def _iter_manifest_list_field_values(items: list, field: str):
    """Recursively visit manifest list members."""
    for child in items:
        yield from _iter_manifest_field_values(child, field)


def _iter_manifest_field_values(value: object, field: str):
    """Yield non-empty string values for *field* from a nested manifest."""
    if isinstance(value, dict):
        yield from _iter_manifest_dict_field_values(value, field)
    elif isinstance(value, list):
        yield from _iter_manifest_list_field_values(value, field)


def _iter_manifest_commands(value: object):
    """Yield command strings from nested manifest verification families."""
    yield from _iter_manifest_field_values(value, "commands")


def _iter_manifest_workflow_paths(value: object):
    """Yield explicit workflow paths from nested manifest families."""
    yield from _iter_manifest_field_values(value, "workflow_paths")


def _make_targets(makefile: Path) -> tuple[set[str], str | None]:
    """Return explicitly declared Make targets and an optional read error."""
    try:
        text = makefile.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return set(), f"{_display_path(makefile)} unreadable: {exc}"

    targets: set[str] = set()
    target_name_pattern = re.compile(r"[A-Za-z0-9_.%+-]+")
    for line in text.splitlines():
        # Recipe bodies and Make assignments are not target declarations.
        if line.startswith("\t") or ":=" in line.split("#", 1)[0]:
            continue
        lhs = line.split("#", 1)[0].partition(":")[0].strip()
        if not lhs:
            continue
        names = lhs.split()
        if all(target_name_pattern.fullmatch(name) for name in names):
            targets.update(names)
    return targets, None


def _is_manifest_path_token(token: str, allow_unprefixed: bool = False) -> bool:
    """Return whether a token is intended to name a repository path."""
    token = token.strip()
    if token.startswith("./"):
        token = token[2:]
    if not token or token.startswith("-") or token.startswith(("$", "${")):
        return False
    if Path(token).is_absolute():
        return True
    if allow_unprefixed:
        return True
    return token.startswith(
        (
            "tools/",
            "packaging/",
            "charts/",
            ".github/workflows/",
            "components/",
            "docs/",
            "tests/",
            "examples/",
            "schemas/",
            "skills/",
            "fuzz/",
        )
    )


def _manifest_path_candidate(
    token: str, *, allow_unprefixed: bool = False
) -> Path | None:
    """Return a canonical repository-owned command path, if applicable."""
    token = token.strip()
    original = token
    if token.startswith("./"):
        token = token[2:]
    if not _is_manifest_path_token(original, allow_unprefixed):
        return None
    if any(char in token for char in ";&|<>*?"):
        return None
    if not Path(token).is_absolute() and ".." in token.split("/"):
        return None

    candidate = Path(token) if Path(token).is_absolute() else REPO_ROOT / token
    try:
        resolved_root = REPO_ROOT.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=False)
        resolved_candidate.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved_candidate


def _manifest_path_is_unsafe(token: str, *, allow_unprefixed: bool = False) -> bool:
    """Return whether a repository-looking path failed canonical containment."""
    return _is_manifest_path_token(token, allow_unprefixed) and (
        _manifest_path_candidate(token, allow_unprefixed=allow_unprefixed) is None
    )


def _missing_make_targets(command: str, tokens: list[str], targets: set[str]) -> list[str]:
    """Return undeclared Make targets in one tokenized command."""
    make_index = tokens.index("make")
    after_make = tokens[make_index + 1 :]
    # Honor a -f/--file selection; otherwise validate against the
    # repository-root Makefile resolved by the caller.
    effective_targets, file_error = _resolve_makefile_targets(after_make, targets)
    if file_error:
        return [file_error]
    return _missing_make_target_arguments(command, after_make, effective_targets)


def _missing_make_target_arguments(
    command: str, candidates: list[str], targets: set[str]
) -> list[str]:
    """Return undeclared targets from the arguments after ``make``."""
    missing: list[str] = []
    skip_next = False
    for index, candidate in enumerate(candidates):
        if candidate in {"&&", "||", ";", "|", "&"}:
            break
        if skip_next:
            skip_next = False
            continue
        action = _classify_make_argument(candidate, index, candidates)
        if action == "value_flag":
            skip_next = True
        elif action == "target" and candidate not in targets:
            missing.append(
                f"{command!r}: Make target {candidate!r} is not declared"
            )
    return missing


def _classify_make_argument(
    candidate: str, index: int, candidates: list[str]
) -> str:
    """Classify a ``make`` argument after shell tokens are handled.

    Returns one of ``"value_flag"`` (consume the next token as its value),
    ``"option"`` (self-contained flag), ``"override"`` (VAR=value), or
    ``"target"`` (a candidate Make target name).
    """
    if candidate in {"-C", "-f", "--file", "-I"}:
        return "value_flag"
    if candidate in {"-j", "--jobs"}:
        # GNU make's job-count argument is optional: `-j` alone means
        # unbounded jobs, so the next token is not necessarily a value.
        # Only consume a following bare numeric job count so a target
        # that directly follows `-j` is still validated.
        if index + 1 < len(candidates) and candidates[index + 1].isdigit():
            return "value_flag"
        return "option"
    if candidate.startswith(("-C", "-j", "--jobs=")):
        return "option"
    if "=" in candidate:
        # VAR=value overrides are Make options, not target names.
        return "override"
    if candidate.startswith("-"):
        return "option"
    return "target"


def _resolve_makefile_targets(
    after_make: list[str], targets: set[str]
) -> tuple[set[str], str | None]:
    """Resolve the effective Make targets, honoring a -f/--file selection.

    Returns ``(targets, error)``: ``error`` is a non-empty string when the
    selected Makefile cannot be parsed, else ``None``.
    """
    for i, flag in enumerate(after_make):
        if flag in {"-f", "--file", "-C"} and i + 1 < len(after_make):
            return _resolve_makefile_option(flag, after_make[i + 1])
        if flag.startswith("-C") and len(flag) > 2:
            return _resolve_makefile_option("-C", flag[2:])
    return targets, None


def _resolve_makefile_option(
    flag: str, raw_path: str
) -> tuple[set[str], str | None]:
    """Resolve one makefile or make-directory option."""
    selected = _manifest_path_candidate(raw_path, allow_unprefixed=True)
    if selected is None:
        kind = "makefile path" if flag in {"-f", "--file"} else "make directory"
        return set(), f"{kind} {raw_path!r} is not repo-owned"
    if flag in {"-f", "--file"}:
        return _make_targets(selected)
    return _make_targets(selected / "Makefile")


def _missing_command_paths(command: str, tokens: list[str], start: int) -> list[str]:
    """Return missing repository-owned paths after a command token."""
    missing: list[str] = []
    for candidate in tokens[start:]:
        if candidate.startswith("-"):
            continue
        # A pytest node id ("tests/foo.py::test_bar") carries a "::"
        # suffix that is not a filesystem path; strip it before the
        # repository-path check.
        candidate = candidate.split("::", 1)[0]
        path = _manifest_path_candidate(candidate)
        if _manifest_path_is_unsafe(candidate):
            missing.append(
                f"{command!r}: repository path {candidate!r} is not repo-owned"
            )
        elif path is not None and not path.exists():
            missing.append(
                f"{command!r}: repository path {_display_path(path)!r} is missing"
            )
    return missing


def _missing_interpreter_path(
    command: str, tokens: list[str], index: int
) -> list[str]:
    """Return a missing path after a script interpreter token."""
    if index + 1 >= len(tokens):
        return []
    path = _manifest_path_candidate(tokens[index + 1])
    if _manifest_path_is_unsafe(tokens[index + 1]) or path is None or path.exists():
        if _manifest_path_is_unsafe(tokens[index + 1]):
            return [
                f"{command!r}: repository path {tokens[index + 1]!r} "
                "is not repo-owned"
            ]
        return []
    return [
        f"{command!r}: repository path {_display_path(path)!r} is missing"
    ]


def _missing_plain_command_path(command: str, token: str) -> list[str]:
    """Return a missing repository-owned path represented by one token."""
    path = _manifest_path_candidate(token)
    if _manifest_path_is_unsafe(token) or path is None or path.exists():
        if _manifest_path_is_unsafe(token):
            return [f"{command!r}: repository path {token!r} is not repo-owned"]
        return []
    return [
        f"{command!r}: repository path {_display_path(path)!r} is missing"
    ]


def _command_segments(tokens: list[str]) -> list[list[str]]:
    """Split shell-like command tokens at the supported control operators."""
    operators = {"&&", "||", ";", "|", "&"}
    segments: list[list[str]] = []
    segment: list[str] = []
    for token in tokens:
        if token in operators:
            if segment:
                segments.append(segment)
            segment = []
            continue
        segment.append(token)
    if segment:
        segments.append(segment)
    return segments


def _missing_manifest_segment(
    command: str, segment: list[str], targets: set[str]
) -> list[str]:
    """Return reachability findings for one shell command segment."""
    missing: list[str] = []
    for index, token in enumerate(segment):
        if token == "make":
            missing.extend(_missing_make_targets(command, segment, targets))
            break
        if token == "pytest":
            # A collecting run names paths that still have to exist; only the
            # claim that it executes them is what the option rules out.
            missing.extend(_missing_command_paths(command, segment, index + 1))
            break
        if token in {"python", "python3", "bash", "sh"}:
            missing.extend(_missing_interpreter_path(command, segment, index))
            continue
        missing.extend(_missing_plain_command_path(command, token))
    return missing


def _missing_one_manifest_command(
    command: str, targets: set[str]
) -> list[str]:
    """Return reachability findings for every segment of one command."""
    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        return [f"{command!r}: cannot parse command: {exc}"]

    missing: list[str] = []
    for segment in _command_segments(tokens):
        missing.extend(_missing_manifest_segment(command, segment, targets))
    return list(dict.fromkeys(missing))


def _check_manifest_command_reachability(manifest: dict) -> CheckResult:
    """Verify manifest Make targets, scripts, and workflow paths are reachable."""
    commands = list(_iter_manifest_commands(manifest))
    workflow_paths = list(_iter_manifest_workflow_paths(manifest))
    targets, makefile_error = _make_targets(REPO_ROOT / "Makefile")
    missing: list[str] = []
    if makefile_error and any("make" in command.split() for command in commands):
        missing.append(makefile_error)
    for command in commands:
        missing.extend(_missing_one_manifest_command(command, targets))
    for workflow_path in workflow_paths:
        path = _manifest_path_candidate(workflow_path)
        if path is None or not path.exists():
            missing.append(
                f"manifest workflow path {workflow_path!r} is missing or not repo-owned"
            )
    if missing:
        return _result(
            "manifest-command-reachability", FAIL, "; ".join(dict.fromkeys(missing))
        )
    return _result(
        "manifest-command-reachability",
        PASS,
        f"{len(commands)} manifest commands and {len(workflow_paths)} workflow paths "
        "resolve to declared or present repository surfaces",
    )


def _check_truth_surfaces(manifest: dict) -> CheckResult:
    """Verify that all repo-owned truth-surface files exist on disk.

    Args:
        manifest: Parsed routing manifest dictionary.

    Returns:
        CheckResult with PASS if every contract, harness, and
        canonical_docs file exists, or FAIL listing missing paths.
    """
    missing: list[str] = []
    for category in ("contract", "harness", "canonical_docs"):
        missing.extend(
            rel
            for rel in manifest["truth_surfaces"][category]
            if not (REPO_ROOT / rel).exists()
        )
    if missing:
        return _result(
            "truth-surfaces",
            FAIL,
            f"missing truth surface files: {', '.join(missing)}",
        )
    return _result("truth-surfaces", PASS, "repo-owned truth surfaces exist")


def _check_risk_pack_docs(manifest: dict) -> CheckResult:
    """Verify that every risk pack has its doc file and references known verification families.

    Args:
        manifest: Parsed routing manifest dictionary.

    Returns:
        CheckResult with PASS if all risk-pack docs exist and their
        verification families are defined, or FAIL with details.
    """
    missing_docs: list[str] = []
    missing_families: list[str] = []
    families = set(manifest["verification_families"])
    for pack in manifest["risk_packs"]:
        doc_path = REPO_ROOT / pack["doc"]
        if not doc_path.exists():
            missing_docs.append(pack["doc"])
        missing_families.extend(
            f"{pack['id']}->{family}"
            for family in pack["verification_families"]
            if family not in families
        )
    if missing_docs or missing_families:
        parts = []
        if missing_docs:
            parts.append(f"missing docs: {', '.join(missing_docs)}")
        if missing_families:
            parts.append(f"unknown verification families: {', '.join(missing_families)}")
        return _result("risk-pack-contract", FAIL, "; ".join(parts))
    return _result("risk-pack-contract", PASS, "risk packs and verification families line up")


def _check_harness_docs(manifest: dict) -> CheckResult:
    """Verify that harness documentation files contain required links, phrases, and patterns.

    Checks the harness entrypoint for navigation links, routing-manifest.md for
    risk-pack identifiers, and core.md for status labels and key phrases.

    Args:
        manifest: Parsed routing manifest dictionary.

    Returns:
        CheckResult with PASS if all required references are present,
        or FAIL listing the missing items.
    """
    missing: list[str] = []
    missing.extend(_required_patterns(
        README_PATH,
        {
            "core.md link": r"\[[^\]]+\]\(core\.md\)",
            "routing-manifest.json link": (
                r"\[[^\]]+\]\(routing-manifest\.json\)"
            ),
            "routing-manifest.md link": r"\[[^\]]+\]\(routing-manifest\.md\)",
            "risk-pack index link": (
                rf"\[[^\]]+\]\({re.escape(RISK_PACKS_README)}\)"
            ),
        },
    ))
    missing.extend(
        _required_patterns(
            SUMMARY_PATH,
            {
                pack_id: rf"\b{re.escape(pack_id)}\b"
                for pack_id in [pack["id"] for pack in manifest["risk_packs"]]
            },
        )
    )
    missing.extend(
        _required_patterns(
            CORE_PATH,
            {
                PASS: rf"`{re.escape(PASS)}`",
                FAIL: rf"`{re.escape(FAIL)}`",
                SKIP_NOT_PRESENT: rf"`{re.escape(SKIP_NOT_PRESENT)}`",
                WARN_NEEDS_AUTHOR_REVIEW: (
                    rf"`{re.escape(WARN_NEEDS_AUTHOR_REVIEW)}`"
                ),
                "outside voice": r"\boutside voice\b",
                "state carrier": r"\bstate carrier\b",
                "stop and explain the mismatch": (
                    r"\bstop and explain the mismatch\b"
                ),
                "tools/harness/resolve_spec.py": (
                    r"python3\s+tools/harness/resolve_spec\.py"
                ),
            },
        )
    )
    if missing:
        unique_missing = ", ".join(sorted(set(missing)))
        return _result(
            "harness-docs",
            FAIL,
            f"harness docs are missing required references or phrases: {unique_missing}",
        )
    return _result("harness-docs", PASS, "README, core, and summary expose the manifest contract")


RULE_CHECK_STAGES = {"save", "commit", "push", "ci"}


PRECOMMIT_CONFIG = ".pre-commit-config.yaml"
PUSH_PROFILE = "tools/ci/pre_push_profile.py"

WIRING_FILES = (
    "Makefile",
    PRECOMMIT_CONFIG,
    PUSH_PROFILE,
)


INTERPRETERS = ("python3", "python", "bash", "sh")


# Options that make a test runner list work instead of doing it.
NON_RUNNING_TEST_OPTIONS = {"--collect-only", "--co"}

# Options whose value is the following word, which is not a path to run.
VALUE_TAKING_TEST_OPTIONS = {
    "-k",
    "-m",
    "-n",
    "-o",
    "-p",
    "-W",
    "-c",
    "--basetemp",
    "--confcutdir",
    "--cov",
    "--cov-config",
    "--cov-report",
    "--deselect",
    "--doctest-glob",
    "--ignore",
    "--ignore-glob",
    "--import-mode",
    "--junitxml",
    "--log-cli-level",
    "--log-file",
    "--maxfail",
    "--override-ini",
    "--rootdir",
    "--tb",
}


BOOLEAN_TEST_OPTIONS = {
    "--collect-in-virtualenv",
    "--continue-on-collection-errors",
    "--doctest-modules",
    "--failed-first",
    "--full-trace",
    "--keep-duplicates",
    "--last-failed",
    "--lf",
    "--no-header",
    "--no-summary",
    "--pdb",
    "--pyargs",
    "--quiet",
    "--strict-config",
    "--strict-markers",
    "--trace",
    "--verbose",
    "--version",
}


INTERPRETER_VALUE_OPTIONS = {
    "python": {"-W", "-X", "-Q"},
    "python3": {"-W", "-X", "-Q"},
    "bash": {"-o", "-O", "--rcfile", "--init-file"},
    "sh": {"-o"},
}


INTERPRETER_BOOLEAN_SHORT = {
    "python": set("B E I O P R S s u v x q").difference({" "}),
    "python3": set("B E I O P R S s u v x q").difference({" "}),
    "bash": set("abdefhkmnptuvx"),
    "sh": set("abefhkmnptuvx"),
}


def _drop_leading_assignments(parts: list[str]) -> list[str]:
    """Drop environment assignments before an executable name."""
    index = 0
    while index < len(parts) and "=" in parts[index] and not parts[index].startswith("-"):
        index += 1
    return parts[index:]


def _interpreter_code_option(token: str) -> bool:
    """Return whether an interpreter option consumes source code."""
    return token in {"-m", "--module", "-c", "--command"} or token.startswith(
        ("-m", "--module=", "--command=")
    )


def _shell_short_option_action(
    interpreter: str, flags: str
) -> tuple[str, str | int | None]:
    """Classify a bundled shell option and its possible value."""
    for index, flag in enumerate(flags):
        if flag == "c":
            return "stop", None
        if flag in {"o", "O"}:
            skip = 2 if index == len(flags) - 1 else 1
            return "skip", skip
        if flag not in INTERPRETER_BOOLEAN_SHORT[interpreter]:
            return "stop", None
    return "skip", 1


def _python_short_option_action(
    interpreter: str, flags: str
) -> tuple[str, str | int | None]:
    """Classify a bundled Python option and its possible value."""
    if flags[0] in {"W", "X", "Q"}:
        # Python accepts both `-X utf8` and `-Xutf8` forms.
        return "skip", 2 if len(flags) == 1 else 1
    if all(flag in INTERPRETER_BOOLEAN_SHORT[interpreter] for flag in flags):
        return "skip", 1
    return "stop", None


def _interpreter_option_action(
    interpreter: str, rest: list[str]
) -> tuple[str, str | int | None]:
    """Classify one interpreter argument without guessing unknown options."""
    token = rest[0]
    if _interpreter_code_option(token):
        return "stop", None
    if token == "--":
        path = rest[1].rstrip("/") if len(rest) > 1 else None
        return "path", path
    if token.startswith("--"):
        if "=" in token:
            return "skip", 1
        if token in INTERPRETER_VALUE_OPTIONS.get(interpreter, set()):
            return "skip", 2
        # An unknown long option may consume a following word.  Refuse to
        # guess, because treating that word as a script would be false evidence.
        return "stop", None
    if token in INTERPRETER_VALUE_OPTIONS.get(interpreter, set()):
        return "skip", 2
    if not token.startswith("-"):
        return "path", token.rstrip("/")
    flags = token[1:]
    if not flags:
        return "path", token.rstrip("/")
    if interpreter in {"bash", "sh"}:
        return _shell_short_option_action(interpreter, flags)
    if interpreter in {"python", "python3"}:
        return _python_short_option_action(interpreter, flags)
    return "stop", None


def _scan_interpreter_args(interpreter: str, rest: list[str]) -> str | None:
    """Find the script path after safely consuming interpreter options."""
    while rest:
        action, value = _interpreter_option_action(interpreter, rest)
        if action == "path":
            return value if isinstance(value, str) else None
        if action == "stop":
            return None
        rest = rest[int(value):]
    return None


def _invocation_target(parts: list[str]) -> str | None:
    """Return the path an entry line runs, if the line runs a path at all."""
    parts = _drop_leading_assignments(parts)
    if not parts or parts[0] not in INTERPRETERS:
        return None
    return _scan_interpreter_args(parts[0], parts[1:])


def _is_test_runner(parts: list[str]) -> bool:
    """Return whether an argument vector invokes pytest or unittest discovery."""
    return (
        len(parts) >= 3
        and parts[0] in INTERPRETERS
        and parts[1] == "-m"
        and parts[2] in {"pytest", "unittest"}
    )


def _has_non_running_test_option(parts: list[str]) -> bool:
    """Return whether a runner invocation only collects or lists tests."""
    for token in parts:
        if token in NON_RUNNING_TEST_OPTIONS:
            return True
    return False


def _test_runner_option_action(
    token: str, parts: list[str], index: int
) -> tuple[str, str | int | None]:
    """Classify one pytest/unittest option or positional path."""
    if token == "--":
        path = parts[index + 1].rstrip("/") if index + 1 < len(parts) else None
        return "path", path
    if token in VALUE_TAKING_TEST_OPTIONS:
        return "skip", 2
    if token.startswith("--"):
        if token in BOOLEAN_TEST_OPTIONS:
            return "skip", 1
        if "=" in token:
            # An inline value (`--junitxml=out.xml`) is self-contained: the
            # option cannot consume the next token, so keep scanning.
            return "skip", 1
        # An unknown long option may consume a following value.  Do not expose
        # that value as a test directory.
        return "stop", None
    if token.startswith("-"):
        flags = token[1:]
        if flags and all(flag in "qvxsrlafA" for flag in flags):
            return "skip", 1
        return "stop", None
    return "path", token.rstrip("/")


def _scan_test_runner_args(parts: list[str]) -> str | None:
    """Find the first positional directory after runner options."""
    index = 3
    while index < len(parts):
        action, value = _test_runner_option_action(parts[index], parts, index)
        if action == "path":
            return value if isinstance(value, str) else None
        if action == "stop":
            return None
        index += int(value)
    return None


def _discovery_target(parts: list[str]) -> str | None:
    """Return the directory a test runner discovers, when one is named."""
    parts = _drop_leading_assignments(parts)
    if not _is_test_runner(parts) or _has_non_running_test_option(parts[3:]):
        return None
    return _scan_test_runner_args(parts)


def _is_invoked(path: str, wiring: str) -> bool:
    """True when an entry point runs a path, rather than only mentioning it.

    A file listed as an argument, named in a comment or kept in a variable is
    not a gate.  The mapping has to name something an entry point executes, or a
    directory whose run covers it.
    """
    stripped = path.removeprefix("./")
    parent = str(Path(stripped).parent)
    for line in wiring.splitlines():
        if _line_runs(line, stripped, parent):
            return True
    return False


def _line_runs(line: str, stripped: str, parent: str) -> bool:
    """True when one entry line runs the path, directly or by discovery."""
    text = line.strip()
    if not text or text.startswith("#"):
        return False
    if text.startswith(("entry:", "entry :")):
        value = text.split(":", 1)[1].strip()
        if value == stripped:
            return True
        # An entry that names an interpreter runs the path it passes on, so it
        # goes through the same analysis as any other line.
        text = value
    from tools.harness.stage_reachability import command_words

    parts = command_words(text)
    if parts and parts[0] == stripped:
        return True
    # A plain interpreter run reaches the file it names and nothing else: a
    # directory argument executes no file from that directory.
    if _invocation_target(parts) == stripped:
        return True
    discovered = _discovery_target(parts)
    if discovered is not None and _is_collected(stripped) and (
        discovered == stripped
        or discovered == parent
        or stripped.startswith(discovered + "/")
    ):
        return True
    return False


def _is_collected(path: str) -> bool:
    """True when pytest's default collection would run this file.

    A directory argument hands the runner a directory; only the test files under
    it are executed, so a detector living there is not reached by that run.
    """
    name = path.rsplit("/", 1)[-1]
    return name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py")
    )


def _makefile_text() -> str:
    """Return the root Makefile without evaluating its recipes."""
    return (REPO_ROOT / "Makefile").read_text(encoding="utf-8")


def _precommit_hook_entries() -> list[str]:
    """Return the `entry` values of the configured hooks.

    Parsed, not searched: a comment that mentions a command is not a hook, and a
    hook with no step is not an entry point.
    """
    import yaml

    try:
        config = yaml.safe_load(_stage_config_text()) or {}
    except yaml.YAMLError:
        return []
    if not isinstance(config, dict):
        return []
    return _enabled_hook_entries(config)


def _enabled_hook_entries(config: dict) -> list[str]:
    """Select only hooks enabled for the commit adapter."""
    entries: list[str] = []
    repos = config.get("repos", [])
    if not isinstance(repos, list):
        return entries
    for repo in repos:
        if not isinstance(repo, dict) or not isinstance(repo.get("hooks"), list):
            continue
        for hook in repo["hooks"]:
            entry = _commit_hook_entry(hook, config.get("default_stages", ["pre-commit"]))
            if entry is not None:
                entries.append(entry)
    return entries


def _commit_hook_entry(hook: object, default_stages: object) -> str | None:
    """Return a well-formed commit hook command, or no evidence."""
    if not isinstance(hook, dict) or not isinstance(hook.get("entry"), str):
        return None
    stages = hook.get("stages", default_stages)
    if isinstance(stages, list) and "pre-commit" in stages:
        return hook["entry"]
    return None


def _workflow_run_text() -> str:
    """Return the shell commands the workflows actually run.

    Only `run` values are collected, so a comment that names a target is not an
    invocation.
    """
    import yaml

    commands: list[str] = []
    for path in _workflow_files():
        try:
            text = path.read_text(encoding="utf-8")
            document = yaml.safe_load(text)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ValueError(f"{_display_path(path)} cannot be read: {exc}") from exc
        try:
            commands.extend(_document_run_commands(document))
        except ValueError as exc:
            raise ValueError(f"{_display_path(path)} has invalid workflow shape: {exc}") from exc
    return "\n".join(commands)


def _defaults_directory(scope: object) -> object:
    """Read `defaults.run.working-directory` from a workflow or a job."""
    if not isinstance(scope, dict):
        return None
    defaults = scope.get("defaults")
    if not isinstance(defaults, dict):
        return None
    run = defaults.get("run")
    return run.get("working-directory") if isinstance(run, dict) else None


def _document_run_commands(document: object) -> list[str]:
    """Return the `run` values of one workflow document."""
    if not isinstance(document, dict):
        raise ValueError("workflow document must be a mapping")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        raise ValueError("workflow jobs must be a mapping")
    workflow_directory = _defaults_directory(document)
    commands: list[str] = []
    for name, job in jobs.items():
        commands.extend(_workflow_job_commands(name, job, workflow_directory))
    return commands


def _runs_elsewhere(directory: object) -> bool:
    """A working directory this cannot establish as the root is not evidence."""
    if directory is None:
        return False
    if not isinstance(directory, str):
        return True
    return directory.strip() not in {"", ".", "./"}


def _workflow_documents() -> list[tuple[str, dict]]:
    """Return every workflow as (display path, parsed document).

    A workflow that cannot be read or parsed is a hard error: an unreadable
    trigger surface must not silently drop the CI edges it carries.
    """
    import yaml

    documents: list[tuple[str, dict]] = []
    for path in _workflow_files():
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ValueError(f"{_display_path(path)} cannot be read: {exc}") from exc
        if not isinstance(document, dict):
            raise ValueError(
                f"{_display_path(path)}: workflow document must be a mapping"
            )
        documents.append((_display_path(path), document))
    return documents


def _job_commands_regardless_of_condition(
    name: object, job: object, workflow_directory: object
) -> list[str]:
    """Return a job's root-directory run commands, ignoring its own condition.

    This is the raw trigger surface: the caller decides whether the job's
    condition is static enough to be execution evidence on its own, or whether
    a paths-filter gate has to cover the changed files first.
    """
    if not isinstance(job, dict):
        raise ValueError(f"job {name!r} must be a mapping")
    if "steps" not in job:
        if isinstance(job.get("uses"), str) and job["uses"].strip():
            return []
        raise ValueError(f"job {name!r} has no steps or reusable workflow")
    directory = job.get("working-directory", _defaults_directory(job))
    if directory is None:
        directory = workflow_directory
    return _enabled_step_commands(job["steps"], directory)


FILTER_GATE_TOKEN = re.compile(
    r"needs\.changes\.outputs\.(\w+)[ \t]*==[ \t]*'true'", re.ASCII
)


def _filter_gate_names(condition: object) -> frozenset[str] | None:
    """Return the paths-filter names a job condition gates on, or None.

    Only a *pure* filter gate is recognized: an expression built from
    `needs.changes.outputs.<name> == 'true'` terms combined with `||`, `&&`,
    parentheses and whitespace.  A condition with any other clause
    (`github.ref == ...`, `always()`, a matrix term) is not reducible to a
    filter, so it provides no coverage evidence.
    """
    if not isinstance(condition, str):
        return None
    remainder = FILTER_GATE_TOKEN.sub("", condition)
    remainder = remainder.replace("||", "").replace("&&", "")
    if remainder.replace("(", "").replace(")", "").replace("\n", "").strip():
        return None
    names = frozenset(
        match.group(1) for match in FILTER_GATE_TOKEN.finditer(condition)
    )
    return names or None


def _paths_filter_patterns() -> dict[str, list[str]]:
    """Return the `paths-filter` pattern lists declared by the change detector."""
    for path in _workflow_files():
        document = _read_workflow_document(path)
        if document is None:
            continue
        for step in _document_steps(document):
            patterns = _step_filter_patterns(step)
            if patterns is not None:
                return patterns
    return {}


def _read_workflow_document(path: Path) -> dict | None:
    """Parse one workflow file, or no document when it is not a mapping.

    A file that cannot be read is a hard error: a filter declaration that
    silently disappeared would leave the trigger surface unchecked.
    """
    import yaml

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError(f"{_display_path(path)} cannot be read: {exc}") from exc
    return document if isinstance(document, dict) else None


def _step_filter_patterns(step: dict) -> dict[str, list[str]] | None:
    """Return one step's `filters` patterns, or no patterns for another step.

    The change detector carries its pattern lists as a YAML string inside the
    step, so the payload is parsed a second time.  Only list-valued entries
    become pattern lists, and only string items become patterns.
    """
    import yaml

    if step.get("id") != "filter":
        return None
    with_block = step.get("with")
    raw = with_block.get("filters") if isinstance(with_block, dict) else None
    if not isinstance(raw, str):
        return None
    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        return None
    return {
        name: [item for item in patterns if isinstance(item, str)]
        for name, patterns in parsed.items()
        if isinstance(patterns, list)
    }


def _document_steps(document: dict) -> list[dict]:
    """Yield every step mapping of a workflow document."""
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return []
    steps: list[dict] = []
    for job in jobs.values():
        if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
            continue
        steps.extend(step for step in job["steps"] if isinstance(step, dict))
    return steps


def _tracked_surface_files() -> list[str]:
    """Return repository-relative paths for the tracked-file coverage check.

    Tracked files are the surface the detectors scan; an untracked worktree
    artifact is not a change a paths-filter has to select.  When Git cannot
    answer, fall back to the worktree so the check still produces a verdict.
    """
    tracked = _git_tracked_files(resolve_approved_executable("git"))
    return tracked if tracked is not None else _worktree_surface_files()


def _git_tracked_files(git: str | None) -> list[str] | None:
    """Return the files Git tracks, or no answer when Git cannot provide one."""
    if git is None:
        return None
    try:
        result = subprocess.run(
            [git, "-C", str(REPO_ROOT), "ls-files", "-z"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return [
        entry.decode("utf-8", "surrogateescape")
        for entry in result.stdout.split(b"\0")
        if entry
    ]


def _worktree_surface_files() -> list[str]:
    """Return the worktree paths the tracked-file coverage check falls back to."""
    return sorted(
        str(path.relative_to(REPO_ROOT)).replace(os.sep, "/")
        for path in REPO_ROOT.rglob("*")
        if _is_worktree_surface_file(path)
    )


def _is_worktree_surface_file(path: Path) -> bool:
    """Return whether a worktree path belongs to the scanned surface.

    A directory entry is not a surface file, the repository metadata
    directory is not a change, and a hidden entry is not something a
    paths-filter selects.
    """
    if not path.is_file():
        return False
    if "/.git/" in str(path):
        return False
    return not any(part.startswith(".") and part != "." for part in path.parts)


def _filter_covers_pattern(patterns: list[str], declared: str) -> bool:
    """Return whether a paths-filter selects every change a rule covers.

    `declared` is a rule's file pattern.  It is covered when the filter carries
    an equal or broader pattern, or - for patterns no filter models exactly -
    when no repository file the pattern matches escapes the filter.  A pattern
    that matches no tracked file is covered vacuously: there is nothing a
    change could touch.  A few rules keep patterns ahead of the surface they
    will cover; until a file matches, that verdict stays covered by design.
    """
    for pattern in patterns:
        if pattern == declared or _path_pattern_matches(pattern, declared):
            return True
    matched = [
        path
        for path in _tracked_surface_files()
        if _path_pattern_matches(declared, path)
    ]
    return all(
        any(_path_pattern_matches(pattern, path) for pattern in patterns)
        for path in matched
    )


_PATH_LITERAL = "literal"
_PATH_QUESTION = "question"
_PATH_STAR = "star"
_PATH_GLOBSTAR = "globstar"
_PATH_GLOBSTAR_DIR = "globstar_dir"
_PATH_CLASS = "class"

_ASCII_DIGITS = frozenset("0123456789")
_ASCII_WORD = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
)
_ASCII_SPACE = frozenset(" \t\n\r\f\v")


def _path_pattern_tokens(pattern: str) -> list[tuple[str, str]]:
    """Split a paths-filter pattern into deterministic match tokens.

    `paths-filter` uses picomatch semantics: `*` never crosses a directory
    separator and `**` does.  Python's `fnmatch` treats `/` as an ordinary
    character, which would make `*.sh` match every shell script in the
    repository and quietly weaken the coverage verdict.  Matching advances
    over the path token by token instead of translating the pattern into a
    regular expression, so a wildcard-rich pattern stays data and cannot
    backtrack super-linearly.
    """
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            index = _append_star_token(tokens, pattern, index)
            continue
        if char == "?":
            tokens.append((_PATH_QUESTION, ""))
            index += 1
            continue
        if char == "[":
            close = pattern.find("]", index + 1)
            if close > index:
                tokens.append((_PATH_CLASS, pattern[index + 1 : close]))
                index = close + 1
                continue
        tokens.append((_PATH_LITERAL, char))
        index += 1
    return tokens


def _append_star_token(
    tokens: list[tuple[str, str]], pattern: str, index: int
) -> int:
    """Append the token for the `*` run at an index and return the next index.

    `**/` matches zero or more whole segments, which is why GitHub documents
    `**/README.md` as matching the repository root as well, `**` matches across
    separators, and a single `*` stops at one separator.
    """
    if pattern[index : index + 3] == "**/":
        tokens.append((_PATH_GLOBSTAR_DIR, ""))
        return index + 3
    if pattern[index : index + 2] == "**":
        tokens.append((_PATH_GLOBSTAR, ""))
        return index + 2
    tokens.append((_PATH_STAR, ""))
    return index + 1


def _path_pattern_matches(pattern: str, path: str) -> bool:
    """Return whether a path filter pattern selects a repository path."""
    reachable = {0}
    for token in _path_pattern_tokens(pattern):
        reachable = _advanced_positions(token, reachable, path)
        if not reachable:
            return False
    return len(path) in reachable


def _advanced_positions(
    token: tuple[str, str], reachable: set[int], path: str
) -> set[int]:
    """Return the path positions reachable after one pattern token."""
    kind, data = token
    if kind == _PATH_LITERAL:
        return _literal_positions(data, reachable, path)
    if kind == _PATH_QUESTION:
        return _question_positions(reachable, path)
    if kind == _PATH_STAR:
        return _star_positions(reachable, path)
    if kind == _PATH_GLOBSTAR:
        return _suffix_positions(reachable, path)
    if kind == _PATH_GLOBSTAR_DIR:
        return _segment_positions(reachable, path)
    return _class_positions(data, reachable, path)


def _literal_positions(
    literal: str, reachable: set[int], path: str
) -> set[int]:
    """Return the positions a literal character advances to."""
    return {
        index + 1
        for index in reachable
        if index < len(path) and path[index] == literal
    }


def _question_positions(reachable: set[int], path: str) -> set[int]:
    """Return the positions `?` advances to: one non-separator character."""
    return {
        index + 1
        for index in reachable
        if index < len(path) and path[index] != "/"
    }


def _class_positions(
    class_text: str, reachable: set[int], path: str
) -> set[int]:
    """Return the positions a bracketed class advances to."""
    return {
        index + 1
        for index in reachable
        if index < len(path) and _class_matches(class_text, path[index])
    }


def _suffix_positions(reachable: set[int], path: str) -> set[int]:
    """Return the positions `**` reaches: any prefix, separators included."""
    return set(range(min(reachable), len(path) + 1))


def _star_positions(reachable: set[int], path: str) -> set[int]:
    """Return the positions a single `*` reaches inside one path component."""
    found: set[int] = set()
    for index in reachable:
        found.add(index)
        while index < len(path) and path[index] != "/":
            index += 1
            found.add(index)
    return found


def _segment_positions(reachable: set[int], path: str) -> set[int]:
    """Return the positions `**/` reaches: whole segments or nothing."""
    found = set(reachable)
    for index in range(min(reachable), len(path)):
        if path[index] == "/":
            found.add(index + 1)
    return found


def _class_matches(class_text: str, char: str) -> bool:
    """Return whether a character satisfies a bracketed pattern class.

    The supported forms match what a picomatch-compatible path filter can
    carry: literal members, `a-z` ranges, the ASCII shorthand escapes, and a
    leading `^` that negates the class.
    """
    negated = class_text.startswith("^")
    body = class_text[1:] if negated else class_text
    matched = _class_body_matches(body, char)
    return not matched if negated else matched


def _class_body_matches(body: str, char: str) -> bool:
    """Return whether a character is a member of a class body."""
    index = 0
    while index < len(body):
        if body[index] == "\\" and index + 1 < len(body):
            if _escape_matches(body[index + 1], char):
                return True
            index += 2
            continue
        if index + 2 < len(body) and body[index + 1] == "-":
            if body[index] <= char <= body[index + 2]:
                return True
            index += 3
            continue
        if body[index] == char:
            return True
        index += 1
    return False


def _escape_matches(escape: str, char: str) -> bool:
    """Return whether an escaped class item matches a character."""
    if escape in ("d", "D"):
        member = char in _ASCII_DIGITS
        return member if escape == "d" else not member
    if escape in ("w", "W"):
        member = char in _ASCII_WORD
        return member if escape == "w" else not member
    if escape in ("s", "S"):
        member = char in _ASCII_SPACE
        return member if escape == "s" else not member
    return char == escape


def _condition_allows_execution(value: object) -> bool:
    """Return whether a workflow condition is statically always true.

    A dynamic condition (`needs.changes.outputs.x == 'true'`, `always()`,
    `matrix...`) cannot prove the work runs on the stage under evaluation, so
    it is not execution evidence.  Job and step level apply the same rule: a
    job whose condition is dynamic is a *conditional* trigger, and the CI
    trigger coverage check is what keeps its paths-filter honest.
    """
    if value is None or value is True:
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"true", "${{ true }}"}
    return False


def _job_condition_allows_execution(job: dict) -> bool:
    """Return whether a job's own condition is statically always true."""
    return _condition_allows_execution(job.get("if"))


def _workflow_job_commands(
    name: object, job: object, workflow_directory: object
) -> list[str]:
    """Validate one workflow job and return its root-directory commands.

    A job gated by a dynamic condition is not execution evidence on its own:
    the same semantics as the step level apply, because a `${{ ... }}`
    condition can be false for the change under evaluation.  Such a job's
    reachability is instead established by the paths-filter coverage check in
    `_check_ci_trigger_coverage`.
    """
    if not isinstance(job, dict):
        raise ValueError(f"job {name!r} must be a mapping")
    if not _job_condition_allows_execution(job):
        return []
    if "steps" not in job:
        if isinstance(job.get("uses"), str) and job["uses"].strip():
            return []
        raise ValueError(f"job {name!r} has no steps or reusable workflow")
    directory = job.get("working-directory", _defaults_directory(job))
    if directory is None:
        directory = workflow_directory
    return _enabled_step_commands(job["steps"], directory)


def _enabled_step_command(
    index: int, step: object, job_directory: object
) -> list[str] | None:
    """Validate one step and return its literal root-directory commands."""
    if not isinstance(step, dict):
        raise ValueError(f"step {index} must be a mapping")
    if step.get("if") is False:
        return None
    if "run" in step and not isinstance(step["run"], str):
        raise ValueError(f"step {index} run must be a string")
    run = step.get("run")
    if not isinstance(run, str):
        return None
    if not _condition_allows_execution(step.get("if")):
        # A dynamic expression (including always(), tag checks and manual
        # inputs) cannot prove that the command runs on the current stage.
        return None
    if _runs_elsewhere(job_directory) or _runs_elsewhere(step.get("working-directory")):
        return None
    from tools.harness.stage_reachability import literal_script_lines

    return literal_script_lines(run)


def _enabled_step_commands(steps: object, job_directory: object = None) -> list[str]:
    """Extract enabled run steps; unsupported structures provide no evidence."""
    if not isinstance(steps, list):
        raise ValueError("workflow steps must be a list")
    commands: list[str] = []
    for index, step in enumerate(steps, 1):
        step_commands = _enabled_step_command(index, step, job_directory)
        if step_commands is not None:
            commands.extend(step_commands)
    return commands


def _profile_gate_text() -> str:
    """Read validated shared data without executing the declaration module."""
    from tools.ci.pre_push_gates import load_gates

    gates = load_gates(REPO_ROOT / "tools/ci/pre_push_gates.json")
    return "\n".join(shlex.join(gate["command"]) for gate in gates)


def _stage_config_text() -> str:
    """Return the pre-commit configuration."""
    return (REPO_ROOT / PRECOMMIT_CONFIG).read_text(encoding="utf-8")


def _workflow_files() -> list[Path]:
    """Return the workflow files."""
    return sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))


def _stage_wiring(stage: str, files: list[str] | None = None) -> str:
    """Resolve actual stage entries, never inserting expected targets as edges.

    Save denotes the optional editor adapter for the same quick hooks. This
    proves configured commands, not installation of an editor or Git hook.
    CI edges may be conditional; trigger coverage is checked separately, and a
    paths-filter-gated job only contributes its commands when the filter would
    actually start it for the files the rule covers.  That is what makes a
    conditional job an honest edge instead of a blanket one.
    """
    from tools.harness.stage_reachability import reachable_commands

    gates: list[str] = []
    if stage in {"save", "commit"}:
        entries = _precommit_hook_entries()
    elif stage == "ci":
        entries = _ci_entry_points(files)
    elif stage == "push":
        entries = ["make pre-push-check"]
        gates = _profile_gate_text().splitlines()
    else:
        return ""
    return reachable_commands(_makefile_text(), entries, PUSH_PROFILE, gates,
                              root=REPO_ROOT)


def _ci_entry_points(files: list[str] | None) -> list[str]:
    """Return the commands a CI run would execute for a change touching *files*.

    Unconditional jobs always contribute; a job gated by a paths-filter
    contributes only when that filter selects everything the rule covers.  An
    unknown file set therefore certifies only the unconditional jobs, which is
    the fail-closed direction.
    """
    try:
        filters = _paths_filter_patterns()
        lanes = _ci_entry_point_lanes()
    except (OSError, ValueError, SyntaxError):
        # An unreadable workflow surface cannot certify a conditional edge.
        return _workflow_run_text().splitlines()
    selected: list[str] = []
    for _display, gate_names, commands in lanes:
        if "" in gate_names:
            selected.extend(commands)
            continue
        if not files:
            continue
        patterns = [
            pattern
            for gate in sorted(gate_names)
            for pattern in filters.get(gate, [])
        ]
        if all(_filter_covers_pattern(patterns, pattern) for pattern in files):
            selected.extend(commands)
    return selected


def _wiring_text() -> str:
    """Return the text of the files that invoke checks and tests."""
    return "\n".join(
        (REPO_ROOT / name).read_text(encoding="utf-8")
        for name in WIRING_FILES
        if (REPO_ROOT / name).exists()
    )


def _stage_problems(stages: object, rule: str) -> list[str]:
    """Return the problems with an entry's stage list."""
    if (
        not isinstance(stages, list)
        or not stages
        or not all(isinstance(stage, str) and stage for stage in stages)
    ):
        return [f"rule {rule}: stage must be a non-empty list of names"]
    if not set(stages) <= RULE_CHECK_STAGES:
        return [f"rule {rule}: unknown stage {stages!r}"]
    return []


def _mapping_shape_problems(entry: dict, rule: str) -> list[str]:
    """Return the problems with an entry's field types and emptiness."""
    problems = _stage_problems(entry["stage"], rule)
    if not isinstance(entry["rule"], str) or not entry["rule"].strip():
        problems.append(f"rule {rule}: rule must be a non-empty string")
    files = entry["files"]
    if (
        not isinstance(files, list)
        or not files
        or not all(isinstance(item, str) and item for item in files)
    ):
        problems.append(f"rule {rule}: files must be a non-empty list of patterns")
    if not isinstance(entry["blocking"], bool):
        problems.append(f"rule {rule}: blocking must be a boolean")
    for key in ("check", "summary", "not_covered"):
        problem = _mapping_text_problem(entry, key, rule)
        if problem is not None:
            problems.append(problem)
    test_problem = _mapping_test_problem(entry, rule)
    if test_problem is not None:
        problems.append(test_problem)
    return problems


def _mapping_text_problem(entry: dict, key: str, rule: str) -> str | None:
    """Return a problem for a required non-empty mapping text field."""
    value = entry[key]
    if isinstance(value, str) and value.strip():
        return None
    return f"rule {rule}: {key} must be a non-empty string"


def _mapping_test_problem(entry: dict, rule: str) -> str | None:
    """Return a problem when the optional mapping test path has a bad shape."""
    test = entry["test"]
    if test is None:
        return None
    if isinstance(test, str) and test.strip():
        return None
    return f"rule {rule}: test must be a path or null"


def _rule_check_entry_problems(entry: dict, agents: str, wiring: str) -> list[str]:
    """Return the problems with one mapping entry, empty when it is sound.

    A path that merely exists is not wiring: the entry has to name a check that
    an entry point actually invokes, and a test that something runs.  That is the
    drift this mapping exists to catch.
    """
    required = {
        "rule", "summary", "check", "files", "stage", "blocking", "test", "not_covered",
    }
    rule = str(entry.get("rule", "?"))
    unknown = sorted(set(entry) - required, key=str)
    if unknown:
        return [f"rule {rule}: unknown field(s): {', '.join(unknown)}"]
    missing = sorted(required - set(entry))
    if missing:
        return [f"rule {rule}: missing {', '.join(missing)}"]

    problems = _mapping_shape_problems(entry, rule)
    if f"| {rule} |" not in agents:
        problems.append(f"rule {rule}: not in the AGENTS.md rule table")

    check = entry["check"]
    if isinstance(check, str) and check.strip():
        if not (REPO_ROOT / check).is_file():
            problems.append(f"rule {rule}: check {check} is not a repository file")
        elif not _is_invoked(check, wiring):
            problems.append(
                f"rule {rule}: nothing invokes {check}; the mapping would claim a "
                f"gate that never runs"
            )
    test = entry["test"]
    if isinstance(test, str) and test.strip() and not (REPO_ROOT / test).is_file():
        problems.append(f"rule {rule}: test {test} does not exist")
    elif isinstance(test, str) and test.strip() and not _is_invoked(test, wiring):
        problems.append(f"rule {rule}: nothing runs {test}")

    problems.extend(_stage_wiring_problems(entry["stage"], rule, check, entry["files"]))
    return problems


def _stage_wiring_problems(
    stages: object, rule: str, check: object, files: object = None
) -> list[str]:
    """Return the stages whose entry points do not reach the check.

    The declared stages have to reach the check, not just the tree: a check
    named under a target nobody calls would never gate anything.  A CI mapping
    is evaluated against the files the rule covers, so a paths-filter-gated job
    is an edge only when its filter selects those files.
    """
    if not isinstance(check, str) or not check.strip() or not isinstance(stages, list):
        return []
    covered = _covered_rule_files(files)
    problems: list[str] = []
    for stage in stages:
        problem = _stage_entry_problem(stage, rule, check, covered)
        if problem is not None:
            problems.append(problem)
    return problems


def _covered_rule_files(files: object) -> list[str]:
    """Return the file patterns a rule declares, ignoring any other item."""
    return [item for item in files if isinstance(item, str)] if isinstance(files, list) else []


def _stage_entry_problem(
    stage: object, rule: str, check: str, covered: list[str]
) -> str | None:
    """Return why one declared stage does not reach the check, or no problem.

    A stage that is not a declarable stage contributes nothing.  A stage whose
    entry points cannot be resolved is a problem in its own right: a stage that
    cannot be read is not evidence that a gate runs.
    """
    if not isinstance(stage, str) or stage not in RULE_CHECK_STAGES:
        return None
    try:
        wiring = _stage_wiring(stage, covered)
    except (OSError, ValueError, SyntaxError) as exc:
        return f"rule {rule}: cannot verify {stage}: {exc}"
    if _is_invoked(check, wiring):
        return None
    return f"rule {rule}: no {stage} entry point runs {check}"


def _job_is_advisory(job: dict) -> bool:
    """Return whether a job's failures are non-blocking by design.

    ``continue-on-error: true`` (or any value that does not statically
    evaluate to false) means a failing command does not fail the workflow,
    so the job's commands cannot certify that a gate blocks anything.
    """
    value = job.get("continue-on-error")
    return value is not None and value is not False


def _ci_entry_point_lanes() -> list[tuple[str, frozenset[str], list[str]]]:
    """Return (job name, filter names the job gates on, its commands).

    A job whose condition is a pure paths-filter gate contributes its commands
    to the lanes selected by those filter names.  A job with no condition, or a
    condition that is statically true, is an unconditional lane (`""`), which
    matches every filter name.  A condition this cannot reduce to a filter is
    skipped: it is neither unconditional evidence nor attributable coverage,
    and the check's job is the reachable lanes (the CI mappings prove
    configured calls, which the routing docs already say can be conditional).
    Advisory jobs (``continue-on-error``) contribute no lane at all: their
    commands run but never block, so they cannot certify a gate.
    """
    lanes: list[tuple[str, frozenset[str], list[str]]] = []
    for display, document in _workflow_documents():
        jobs = _workflow_jobs(display, document)
        workflow_directory = _defaults_directory(document)
        for name, job in jobs.items():
            lane = _ci_entry_point_lane(display, name, job, workflow_directory)
            if lane is not None:
                lanes.append(lane)
    return lanes


def _workflow_jobs(display: str, document: dict) -> dict:
    """Return a workflow's job mappings, failing closed on any other shape."""
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        raise ValueError(f"{display}: workflow jobs must be a mapping")
    return jobs


def _ci_entry_point_lane(
    display: str, name: object, job: object, workflow_directory: object
) -> tuple[str, frozenset[str], list[str]] | None:
    """Return one job's lane, or no lane when it cannot certify a gate.

    An advisory job contributes nothing, and a job whose condition reduces to
    neither static truth nor a pure filter gate contributes nothing either.
    """
    if not isinstance(job, dict):
        raise ValueError(f"{display}: job {name!r} must be a mapping")
    if _job_is_advisory(job):
        return None
    condition = job.get("if")
    commands = _job_commands_regardless_of_condition(name, job, workflow_directory)
    if _condition_allows_execution(condition):
        return (f"{display}:{name}", frozenset({""}), commands)
    filter_names = _filter_gate_names(condition)
    if filter_names is None:
        return None
    return (f"{display}:{name}", filter_names, commands)


def _ci_lane_wiring(commands: list[str]) -> str:
    """Resolve a CI lane's commands through the Makefile, with a small cache.

    A workflow step says `make harness-security-checks`; the check it reaches
    is two Make targets deeper.  The lane has to go through the same resolver
    the stage wiring uses, or a gate would look unreachable and its filter
    coverage would go unchecked.
    """
    from tools.harness.stage_reachability import reachable_commands

    return _CI_LANE_WIRING_CACHE.setdefault(
        tuple(commands),
        reachable_commands(_makefile_text(), list(commands), PUSH_PROFILE, [],
                           root=REPO_ROOT),
    )


_CI_LANE_WIRING_CACHE: dict[tuple[str, ...], str] = {}


def _dynamic_gate_names(check: str, lanes: list) -> list[str]:
    """Return the filter names of the dynamic lanes that reach a check."""
    names: set[str] = set()
    for _display, filter_names, commands in lanes:
        if "" in filter_names:
            continue
        if _is_invoked(check, _ci_lane_wiring(commands)):
            names.update(filter_names)
    return sorted(names)


def _check_ci_trigger_coverage(entries: list) -> CheckResult:
    """Verify that a dynamic CI mapping is backed by its path filter.

    A mapping that declares `ci` claims the check runs on that stage.  A CI job
    gated by a dynamic `if:` (a paths-filter gate) is not execution evidence on
    its own, so the filter has to select the rule's own files: without that, a
    change to the rule's surface would never start the job and the mapping
    would claim a gate that never runs for the very change it covers.
    """
    try:
        filters = _paths_filter_patterns()
        lanes = _ci_entry_point_lanes()
    except (OSError, ValueError, SyntaxError) as exc:
        return _result("ci-trigger-coverage", FAIL, f"cannot verify ci coverage: {exc}")

    problems: list[str] = []
    checked = 0
    for entry in entries:
        entry_problems = _ci_trigger_entry_problems(entry, filters, lanes)
        if entry_problems is None:
            continue
        checked += 1
        problems.extend(entry_problems)

    if problems:
        return _result("ci-trigger-coverage", FAIL, "; ".join(problems[:4]))
    return _result(
        "ci-trigger-coverage",
        PASS,
        f"{checked} conditional CI mapping(s) covered by their paths filters",
    )


def _ci_trigger_entry_problems(entry: object, filters: dict, lanes: list) -> list[str] | None:
    """Return the uncovered filter gaps of one mapping, or no finding.

    No finding covers three cases: an entry that is not a conditional CI
    mapping, an entry that declares no covered files, and an entry whose check
    runs through an unconditional lane, which needs no filter to start it.
    The returned list is empty when the conditional lanes are all covered.
    """
    if not isinstance(entry, dict) or "ci" not in (entry.get("stage") or []):
        return None
    check = entry.get("check")
    if not isinstance(check, str) or not check.strip():
        return None
    files = [item for item in entry.get("files") or [] if isinstance(item, str)]
    if not files:
        return None
    gate_names = _dynamic_gate_names(check, lanes)
    if not gate_names:
        # The check runs through an unconditional lane; nothing to gate.
        return None
    return [
        problem
        for gate in gate_names
        for problem in _uncovered_filter_problems(entry, check, files, filters, gate)
    ]


def _uncovered_filter_problems(
    entry: dict, check: str, files: list[str], filters: dict, gate: str
) -> list[str]:
    """Return one finding per filter that leaves part of a rule's surface out."""
    patterns = filters.get(gate) or []
    uncovered = [
        pattern for pattern in files if not _filter_covers_pattern(patterns, pattern)
    ]
    if not uncovered:
        return []
    return [
        f"rule {entry.get('rule', '?')}: the {gate} paths-filter does not "
        f"select {', '.join(sorted(uncovered))}, so a change to "
        f"{check} would never start the job that runs it"
    ]


def _check_rule_checks(manifest: dict) -> CheckResult:
    """Verify that every rule-to-check mapping names something that exists.

    An entry that points at a missing script, a stage nobody runs, or a rule
    number that AGENTS.md no longer lists claims a rule is wired when it is not.
    """
    entries = manifest.get("rule_checks")
    if not isinstance(entries, list) or not entries:
        return _result("rule-checks", FAIL, "rule_checks missing from the manifest")

    agents = AGENTS_PATH.read_text(encoding="utf-8") if AGENTS_PATH.exists() else ""
    wiring = _wiring_text()
    problems: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append("an entry is not an object")
            continue
        problems.extend(_rule_check_entry_problems(entry, agents, wiring))

    if problems:
        return _result("rule-checks", FAIL, "; ".join(problems[:4]))
    return _result("rule-checks", PASS, f"{len(entries)} rule(s) mapped to an existing check")


def _check_agents_rule_coverage(manifest: dict) -> CheckResult:
    """Fail when an AGENTS.md detector rule is absent from the mapping.

    `_check_rule_checks` validates the mapping entry by entry, so it can only
    judge rules that are present: a blocking detector rule that nobody mapped
    is invisible to it.  This is the reverse direction, and it is what makes
    the mapping's silence a finding instead of a blind spot.

    It runs as its own result because it needs the *shipped* manifest: the
    single-entry fixtures the stage-wiring tests build are deliberately
    partial and must keep their focused PASS/FAIL verdicts.
    """
    entries = manifest.get("rule_checks")
    if not isinstance(entries, list) or not entries:
        return _result("agents-rule-coverage", FAIL, "rule_checks missing from the manifest")
    agents = AGENTS_PATH.read_text(encoding="utf-8") if AGENTS_PATH.exists() else ""
    problems = _agents_rule_coverage_problems(entries, agents)
    if problems:
        return _result("agents-rule-coverage", FAIL, "; ".join(problems[:4]))
    return _result(
        "agents-rule-coverage",
        PASS,
        "every AGENTS.md rule row naming a harness detector is mapped",
    )


AGENTS_RULE_ROW = re.compile(r"^\|\s*(\d+)\s*\|", re.MULTILINE)
AGENTS_TOOL_PATH = re.compile(r"tools/harness/\w[\w./-]*", re.ASCII)


def _agents_detector_rules(agents: str) -> dict[str, set[str]]:
    """Map rule number -> the harness tool paths its AGENTS.md row names.

    The source of truth is the rule table itself: only a row that literally
    contains a `tools/harness/...` path is a detector binding, so a rule that
    merely *describes* a check without naming it is not reported.  That keeps
    the reverse coverage free of rows whose binding is a Makefile target or a
    pre-commit hook id alone, and prose outside the table cannot add a binding.
    """
    bindings: dict[str, set[str]] = {}
    rule: str | None = None
    row: str | None = None
    for line in agents.splitlines():
        rule, row = _agents_row_step(bindings, line, rule, row)
    _flush_agents_row(bindings, rule, row)
    return bindings


def _agents_row_step(
    bindings: dict[str, set[str]], line: str, rule: str | None, row: str | None
) -> tuple[str | None, str | None]:
    """Return the `(rule, row)` state after one AGENTS.md line.

    A line that opens a rule row starts a new binding, a further `|` line
    extends the current row (a table row wrapped over several lines keeps its
    binding, while prose that follows the table closes the row), and any other
    line closes it.
    """
    match = AGENTS_RULE_ROW.match(line)
    if match:
        _flush_agents_row(bindings, rule, row)
        return match.group(1), line
    if rule is not None and line.lstrip().startswith("|"):
        return rule, f"{row} {line}" if row is not None else line
    _flush_agents_row(bindings, rule, row)
    return None, None


def _flush_agents_row(
    bindings: dict[str, set[str]], rule: str | None, row: str | None
) -> None:
    """Record the tool paths one closed row names under its rule number."""
    if rule is None or row is None:
        return
    paths = {
        match.group(0).rstrip(".,;:`")
        for match in AGENTS_TOOL_PATH.finditer(row)
    }
    if paths:
        bindings.setdefault(rule, set()).update(paths)


def _agents_rule_coverage_problems(entries: list, agents: str) -> list[str]:
    """Return rules whose AGENTS.md detector binding is absent from the mapping.

    `_rule_check_entry_problems` validates the mapping in one direction: every
    entry names a real, invoked check.  Nothing there can notice a *missing*
    entry, so a blocking detector rule can stay unmapped forever.  This closes
    that structural blind spot by enumerating the AGENTS.md rule rows that name
    a harness detector and requiring each of them in `rule_checks`.
    """
    mapped_rules, mapped_checks = _mapped_rule_bindings(entries)
    bindings = _agents_detector_rules(agents)
    problems: list[str] = []
    for rule, paths in sorted(bindings.items(), key=lambda kv: int(kv[0])):
        problem = _unmapped_rule_problem(rule, paths, mapped_rules, mapped_checks)
        if problem is not None:
            problems.append(problem)
    return problems


def _mapped_rule_bindings(entries: list) -> tuple[set[str], set[object]]:
    """Return the rule numbers and check paths the mapping already declares.

    An entry that is not a mapping, or whose rule or check is not a string,
    contributes nothing: the forward validator is what reports that shape.
    """
    mapped_rules: set[str] = {
        str(entry.get("rule"))
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("rule"), str)
    }
    mapped_checks: set[object] = {
        entry.get("check")
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("check"), str)
    }
    return mapped_rules, mapped_checks


def _unmapped_rule_problem(
    rule: str, paths: set[str], mapped_rules: set[str], mapped_checks: set[object]
) -> str | None:
    """Return why one AGENTS.md-bound rule is unmapped, or no problem.

    A rule the mapping already lists, and a rule whose every bound detector
    path appears as some entry's check, are both covered: the first by rule
    number, the second because the mapping does reach that detector.
    """
    if rule in mapped_rules:
        return None
    unmapped = sorted(path for path in paths if path not in mapped_checks)
    if not unmapped:
        return None
    return (
        f"rule {rule}: AGENTS.md names {', '.join(unmapped)} but the mapping "
        f"has no entry for it; a blocking detector rule that is not mapped "
        f"can go unwired without the mapping noticing"
    )


def _check_agents_map() -> CheckResult:
    """Verify that AGENTS.md references the harness entrypoints and Codex-first semantics.

    Returns:
        CheckResult with PASS if all required references are present,
        or FAIL listing the missing items.
    """
    missing = _required_patterns(
        AGENTS_PATH,
        {
            DOCS_HARNESS_README: rf"`{re.escape(DOCS_HARNESS_README)}`",
            "docs/harness/core.md": r"`docs/harness/core\.md`",
            "docs/harness/routing-manifest.json": (
                r"`docs/harness/routing-manifest\.json`"
            ),
            "Codex-first": r"\bCodex-first\b",
        },
    )
    if missing:
        return _result(
            "agents-map",
            FAIL,
            f"AGENTS.md is missing harness map references: {', '.join(missing)}",
        )
    return _result("agents-map", PASS, "AGENTS.md points at the harness entrypoints")


def _check_e2e_harness_contract() -> CheckResult:
    """Validate the Rust E2E harness presence and binary contract."""
    missing: list[str] = []
    if not E2E_HARNESS_DIR.exists():
        missing.append(_display_path(E2E_HARNESS_DIR))
    if not E2E_HARNESS_CARGO.exists():
        missing.append(_display_path(E2E_HARNESS_CARGO))
    if missing:
        return _result(
            "e2e-harness-contract",
            FAIL,
            f"missing required harness paths: {', '.join(missing)}",
        )

    cargo_text = E2E_HARNESS_CARGO.read_text(encoding="utf-8")
    has_bin_decl = bool(
        re.search(
            r"\[\[bin\]\]\s*name\s*=\s*\"e2e-harness\"",
            cargo_text,
            flags=re.MULTILINE | re.DOTALL,
        )
    )
    if not has_bin_decl:
        return _result(
            "e2e-harness-contract",
            FAIL,
            "Cargo.toml missing [[bin]] name = \"e2e-harness\" declaration",
        )

    main_rs = E2E_HARNESS_DIR / "src" / "main.rs"
    if not main_rs.exists():
        return _result(
            "e2e-harness-contract",
            FAIL,
            f"missing harness entrypoint: {_display_path(main_rs)}",
        )

    return _result(
        "e2e-harness-contract",
        PASS,
        "Rust e2e-harness directory and binary contract are present",
    )


def _wrapper_invokes_expected_scenario(text: str, scenario_name: str) -> bool:
    """Return True when a migrated shell wrapper calls the expected scenario."""
    literal_call_pattern = rf"\bscenario\s+{re.escape(scenario_name)}\b"
    scenario_decl_pattern = rf"SCENARIO_NAME=\"{re.escape(scenario_name)}\""
    has_literal_call = re.search(literal_call_pattern, text) is not None
    has_variable_call = (
        "args=(scenario \"${SCENARIO_NAME}\")" in text
        and re.search(scenario_decl_pattern, text) is not None
    )
    return has_literal_call or has_variable_call


def _wrapper_contains_stale_runtime_logic(text: str) -> bool:
    """Return True when wrapper text still contains migrated assertion/runtime logic."""
    forbidden_tokens = (
        "markdown_build_with_nginx",
        "markdown_prepare_runtime_reuse",
        "curl -sS",
        "curl -fsS",
        "curl -X",
    )
    return any(token in text for token in forbidden_tokens)


def _collect_migrated_wrapper_findings() -> tuple[list[str], list[str]]:
    """Collect missing wrappers and stale wrapper logic violations."""
    missing_wrappers: list[str] = []
    stale_shell_logic: list[str] = []
    for rel_path, scenario_name in MIGRATED_SCENARIO_WRAPPERS.items():
        script_path = REPO_ROOT / rel_path
        if not script_path.exists():
            missing_wrappers.append(rel_path)
            continue
        text = script_path.read_text(encoding="utf-8")
        if not _wrapper_invokes_expected_scenario(text, scenario_name):
            stale_shell_logic.append(f"{rel_path}:missing scenario wrapper call")
        if _wrapper_contains_stale_runtime_logic(text):
            stale_shell_logic.append(f"{rel_path}:contains scenario assertion/runtime logic")
    return missing_wrappers, stale_shell_logic


def _collect_removed_python_e2e_paths() -> list[str]:
    """Return removed Python E2E file paths that still exist on disk."""
    return [
        str(E2E_PYTHON_DIR / filename)
        for filename in REMOVED_PYTHON_E2E_FILES
        if (E2E_PYTHON_DIR / filename).exists()
    ]


def _collect_stale_execution_surface_refs() -> list[str]:
    """Return execution-surface references that still mention removed E2E files."""
    refs: list[str] = []
    execution_surfaces = (
        REPO_ROOT / "Makefile",
        REPO_ROOT / "tools" / "e2e" / "run_e2e_suite.sh",
        GITHUB_WORKFLOWS_DIR / "ci.yml",
        REPO_ROOT / "docs" / "testing" / "E2E_TESTS.md",
    )
    for surface in execution_surfaces:
        if not surface.is_file():
            continue
        text = surface.read_text(encoding="utf-8")
        refs.extend(
            f"{_display_path(surface)}::{filename}"
            for filename in REMOVED_PYTHON_E2E_FILES
            if filename in text
        )
    return refs


def _check_e2e_migration_policy() -> CheckResult:
    """Validate Rust-first E2E migration policy for Python and shell paths."""
    missing_wrappers, stale_shell_logic = _collect_migrated_wrapper_findings()
    removed_python_present = _collect_removed_python_e2e_paths()
    stale_execution_refs = _collect_stale_execution_surface_refs()

    if missing_wrappers or stale_shell_logic or removed_python_present or stale_execution_refs:
        return _format_e2e_migration_failure(
            missing_wrappers,
            stale_shell_logic,
            removed_python_present,
            stale_execution_refs,
        )
    return _result(
        "e2e-migration-policy",
        PASS,
        "migrated shell paths are thin wrappers and removed Python E2E files are absent",
    )


def _format_e2e_migration_failure(
    missing_wrappers,
    stale_shell_logic,
    removed_python_present,
    stale_execution_refs,
) -> CheckResult:
    details: list[str] = []
    if missing_wrappers:
        details.append(f"missing migrated wrappers: {', '.join(missing_wrappers)}")
    if stale_shell_logic:
        details.append(f"non-wrapper migrated shell logic: {', '.join(stale_shell_logic)}")
    if removed_python_present:
        display = ", ".join(_display_path(Path(p)) for p in removed_python_present)
        details.append(f"removed Python E2E files still present: {display}")
    if stale_execution_refs:
        details.append(f"stale execution-surface refs: {', '.join(stale_execution_refs)}")
    return _result("e2e-migration-policy", FAIL, "; ".join(details))


def check_batch_prune_pairing() -> CheckResult:
    """Verify batch → prune workflow pairing (FUZZ-005).

    If the batch fuzzing workflow exists, the corresponding corpus pruning
    workflow must also exist.  If batch is absent, this check passes
    unconditionally (no requirement for prune without batch).

    Returns:
        CheckResult with PASS if pairing is satisfied or batch is absent,
        or FAIL if batch exists without a corresponding prune workflow.
    """
    batch_path = GITHUB_WORKFLOWS_DIR / CFLITE_BATCH_WORKFLOW
    prune_path = GITHUB_WORKFLOWS_DIR / CFLITE_CRON_WORKFLOW

    if not batch_path.exists():
        return _result(
            "batch-prune-pairing",
            PASS,
            "batch workflow absent; pairing check not applicable",
        )

    if not prune_path.exists():
        return _result(
            "batch-prune-pairing",
            FAIL,
            "FUZZ-005: batch workflow exists but prune workflow "
            f"({_display_path(prune_path)}) is missing",
        )

    return _result(
        "batch-prune-pairing",
        PASS,
        "batch and prune workflows both present (FUZZ-005 satisfied)",
    )


def check_cfl_workflows() -> CheckResult:
    """Verify ClusterFuzzLite CI workflow files exist and are correctly configured.

    Checks:
    - ClusterFuzzLite PR, batch, and cron workflows all exist
    - PR workflow uses address sanitizer
    - PR workflow has path filters under pull_request trigger
    - Batch workflow has corpus storage-repo configuration

    Returns:
        CheckResult with PASS if all checks pass, or FAIL listing issues.
    """
    required_workflows = [
        str((GITHUB_WORKFLOWS_DIR / CFLITE_PR_WORKFLOW).relative_to(REPO_ROOT)),
        str((GITHUB_WORKFLOWS_DIR / CFLITE_BATCH_WORKFLOW).relative_to(REPO_ROOT)),
        str((GITHUB_WORKFLOWS_DIR / CFLITE_CRON_WORKFLOW).relative_to(REPO_ROOT)),
    ]

    missing: list[str] = []
    missing.extend(
        rel for rel in required_workflows if not (REPO_ROOT / rel).exists()
    )
    if missing:
        return _result(
            "cfl-workflows",
            FAIL,
            f"missing ClusterFuzzLite workflow files: {', '.join(missing)}",
        )

    issues: list[str] = []

    # Verify PR workflow uses address sanitizer
    pr_workflow = GITHUB_WORKFLOWS_DIR / CFLITE_PR_WORKFLOW
    pr_missing = _required_patterns(
        pr_workflow,
        {"sanitizer: address": r"sanitizer:\s*address"},
    )
    if pr_missing:
        issues.append(f"{CFLITE_PR_WORKFLOW} missing address sanitizer configuration")

    # Verify PR workflow has path filters under pull_request trigger
    pr_path_missing = _required_patterns(
        pr_workflow,
        {"pull_request paths filter": r"pull_request:\s*\n\s+paths:"},
    )
    if pr_path_missing:
        issues.append(f"{CFLITE_PR_WORKFLOW} missing path filter for pull_request trigger")

    # Verify batch workflow has corpus storage-repo configuration
    batch_workflow = GITHUB_WORKFLOWS_DIR / CFLITE_BATCH_WORKFLOW
    batch_missing = _required_patterns(
        batch_workflow,
        {"storage-repo": r"storage-repo:"},
    )
    if batch_missing:
        issues.append(f"{CFLITE_BATCH_WORKFLOW} missing storage-repo configuration")

    # The build action creates absent workspace directories as root before it
    # starts the project image. Pre-creation keeps build-out owned by the
    # GitHub runner UID shared by the final non-root fuzzer user.
    for workflow_name in (
        CFLITE_PR_WORKFLOW,
        CFLITE_BATCH_WORKFLOW,
        CFLITE_CRON_WORKFLOW,
    ):
        workflow = GITHUB_WORKFLOWS_DIR / workflow_name
        output_missing = _required_patterns(
            workflow,
            {
                "non-root build-out preparation": (
                    r"run:\s*mkdir -p build-out[\s\S]*"
                    r"google/clusterfuzzlite/actions/build_fuzzers@"
                )
            },
        )
        if output_missing:
            issues.append(
                f"{workflow_name} must pre-create build-out "
                "before build_fuzzers"
            )

    if issues:
        return _result("cfl-workflows", FAIL, "; ".join(issues))

    return _result(
        "cfl-workflows",
        PASS,
        "ClusterFuzzLite workflows present and correctly configured",
    )


def _check_optional_kiro(manifest: dict, full: bool) -> CheckResult:
    """Check optional .kiro/steering adapters for drift against harness truth surfaces.

    If no non-ignored adapters are present, returns SKIP_NOT_PRESENT.  Git-
    ignored adapters are user-local state outside repository validation;
    tracked adapters remain eligible even when an ignore pattern matches.
    If eligible adapters lack required links, returns
    WARN_NEEDS_AUTHOR_REVIEW in quick mode or FAIL in full mode.

    Args:
        manifest: Parsed routing manifest dictionary.
        full: If True, treat missing links as a blocking failure.

    Returns:
        CheckResult indicating whether optional adapters are in sync,
        absent, or need author review.
    """
    adapters = manifest.get("truth_surfaces", {}).get("optional_adapters", [])
    present = [
        REPO_ROOT / rel
        for rel in adapters
        if (REPO_ROOT / rel).exists()
        and not _is_git_ignored(REPO_ROOT / rel)
    ]
    if not present:
        return _result(
            "kiro-adapters",
            SKIP_NOT_PRESENT,
            ".kiro/steering is absent or git-ignored, skipping optional "
            "adapter checks",
        )

    missing_links: list[str] = []
    for path in present:
        missing_links.extend(
            f"{path.relative_to(REPO_ROOT)}::{needle}"
            for needle in _required_text(
                path,
                [
                    DOCS_HARNESS_README,
                    "docs/harness/core.md",
                ],
            )
        )
    if missing_links:
        status = FAIL if full else WARN_NEEDS_AUTHOR_REVIEW
        return _result(
            "kiro-adapters",
            status,
            f"local adapter docs need refresh: {', '.join(missing_links)}",
        )
    return _result("kiro-adapters", PASS, "optional local Kiro adapters point at harness truth")


def _check_recent_analysis_reports() -> CheckResult:
    """Validate recent-change analysis reports when they are present.

    These reports are optional project evidence, but once one exists it must
    carry enough structure to make findings traceable through remediation and
    verification.  This keeps post-hoc harness updates from becoming prose-only
    recommendations with no closeout state.

    Returns:
        CheckResult indicating skip, pass, or missing report evidence.
    """
    reports = sorted(REPO_ROOT.glob(RECENT_ANALYSIS_REPORT_GLOB))
    if not reports:
        return _result(
            "recent-analysis-report",
            SKIP_NOT_PRESENT,
            "no recent Git harness/steering analysis reports found",
        )

    missing: list[str] = []
    for report in reports:
        missing.extend(_missing_recent_report_evidence(report))

    if missing:
        return _result(
            "recent-analysis-report",
            FAIL,
            "analysis reports are missing traceable closeout evidence: "
            + ", ".join(missing),
        )
    return _result(
        "recent-analysis-report",
        PASS,
        "recent analysis reports include findings, remediation, and verification",
    )


def _missing_recent_report_evidence(report: Path) -> list[str]:
    """Return closeout evidence gaps for one recent-analysis report."""
    try:
        text = report.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{_display_path(report)}::unreadable ({exc})"]

    missing = _missing_recent_report_sections(report, text)
    finding_ids = sorted(set(re.findall(r"\|\s*(P[0-3]-\d{3})\s*\|", text)))
    if not finding_ids:
        missing.append(f"{_display_path(report)}::finding ids")
        return missing

    remediation_start = text.find("## Remediation Results")
    remediation_text = text[remediation_start:] if remediation_start >= 0 else ""
    for finding_id in finding_ids:
        missing.extend(_missing_recent_finding_closeout(report, remediation_text, finding_id))
    return missing


def _missing_recent_report_sections(report: Path, text: str) -> list[str]:
    """Return required section headings absent from a report."""
    required = (
        "## Phase 1 Analysis",
        "## Findings",
        "## Remediation Results",
        "## Verification",
    )
    return [f"{_display_path(report)}::{section}" for section in required if section not in text]


def _missing_recent_finding_closeout(
    report: Path,
    remediation_text: str,
    finding_id: str,
) -> list[str]:
    """Return remediation row/final-status gaps for one finding."""
    row_match = re.search(
        rf"\|\s*{re.escape(finding_id)}\s*\|([^\n]+)\|",
        remediation_text,
        re.IGNORECASE,
    )
    if not row_match:
        return [f"{_display_path(report)}::{finding_id} remediation row"]

    row = row_match[0].lower()
    if all(status not in row for status in REMEDIATION_STATUSES):
        return [f"{_display_path(report)}::{finding_id} final status"]
    return []


def check_clusterfuzzlite_build_config() -> CheckResult:
    """Verify .clusterfuzzlite/ directory completeness and content correctness.

    Checks that the three required ClusterFuzzLite build configuration files
    exist (project.yaml, Dockerfile, build.sh), that project.yaml declares
    language as rust with address sanitizer, and that build.sh has executable
    permission.

    Returns:
        CheckResult with PASS if all checks pass, or FAIL with details of
        the first failing condition.
    """
    required_files = [
        ".clusterfuzzlite/project.yaml",
        CLUSTERFUZZ_DOCKERFILE,
        ".clusterfuzzlite/build.sh",
    ]
    if missing := [
        rel for rel in required_files if not (REPO_ROOT / rel).exists()
    ]:
        return _result(
            "clusterfuzzlite-build-config",
            FAIL,
            f"missing ClusterFuzzLite build files: {', '.join(missing)}",
        )

    # Validate project.yaml content
    project_yaml_path = REPO_ROOT / ".clusterfuzzlite" / "project.yaml"
    try:
        project_yaml_text = project_yaml_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _result(
            "clusterfuzzlite-build-config",
            FAIL,
            f".clusterfuzzlite/project.yaml unreadable: {exc}",
        )

    content_issues: list[str] = []
    if "language: rust" not in project_yaml_text:
        content_issues.append("project.yaml missing 'language: rust'")
    if "address" not in project_yaml_text:
        content_issues.append("project.yaml missing address sanitizer")
    if content_issues:
        return _result(
            "clusterfuzzlite-build-config",
            FAIL,
            "; ".join(content_issues),
        )

    # Validate build.sh has executable permission
    build_sh_path = REPO_ROOT / ".clusterfuzzlite" / "build.sh"
    if not os.access(build_sh_path, os.X_OK):
        return _result(
            "clusterfuzzlite-build-config",
            FAIL,
            ".clusterfuzzlite/build.sh is not executable",
        )

    return _result(
        "clusterfuzzlite-build-config",
        PASS,
        "ClusterFuzzLite build config is complete and valid",
    )


def _dockerfile_final_user(content: str) -> str | None:
    """Return the final Dockerfile stage's USER principal, excluding any group.

    The scan stops at the FROM instruction that opens the final stage, so a
    USER instruction belonging to an earlier stage is never attributed to the
    image that is actually built.
    """
    for raw_line in reversed(content.splitlines()):
        parts = raw_line.strip().split(None, 1)
        if not parts:
            continue
        keyword = parts[0].upper()
        if keyword == "FROM":
            return None
        if len(parts) == 2 and keyword == "USER":
            return parts[1].split()[0].split(":", 1)[0].lower()
    return None


def _final_docker_stage_args(content: str) -> set[str]:
    """Return ARG names declared after the final FROM instruction."""
    final_stage_args: set[str] = set()
    for raw_line in content.splitlines():
        parts = raw_line.strip().split(None, 1)
        if parts and parts[0].upper() == "FROM":
            final_stage_args.clear()
        elif len(parts) == 2 and parts[0].upper() == "ARG":
            final_stage_args.add(parts[1].split("=", 1)[0])
    return final_stage_args


def _docker_runtime_content_issues(
    relative_path: str, content: str
) -> list[str]:
    """Return non-root and NGINX runtime contract issues for one Dockerfile."""
    issues: list[str] = []
    final_user = _dockerfile_final_user(content)
    if final_user is None:
        issues.append(f"{relative_path} missing final USER")
    elif final_user in {"0", "root"}:
        issues.append(f"{relative_path} final USER must be non-root")

    if relative_path == CLUSTERFUZZ_DOCKERFILE:
        issues.extend(_clusterfuzz_non_root_issues(relative_path, content))
        return issues
    issues.extend(
        f"{relative_path} missing '{snippet}'"
        for snippet in NGINX_NON_ROOT_REQUIRED_SNIPPETS
        if snippet not in content
    )
    if relative_path.endswith("Dockerfile.install-example"):
        stage_args = _final_docker_stage_args(content)
        if not {"MODULE_REF", "INSTALL_SHA256"}.issubset(stage_args):
            issues.append(
                f"{relative_path} missing stage ARG declarations"
            )
    return issues


def _clusterfuzz_non_root_issues(
    relative_path: str, content: str
) -> list[str]:
    """Return missing non-root write contracts for ClusterFuzzLite."""
    missing = [
        snippet
        for snippet in CLUSTERFUZZ_NON_ROOT_REQUIRED_SNIPPETS
        if snippet not in content
    ]
    issues: list[str] = []
    if any("/rustc" in snippet for snippet in missing):
        issues.append(f"{relative_path} missing writable /rustc contract")
    if any("/usr/lib/libFuzzingEngine.a" in snippet for snippet in missing):
        issues.append(
            f"{relative_path} missing writable "
            "/usr/lib/libFuzzingEngine.a contract"
        )
    return issues


def check_docker_runtime_security() -> CheckResult:
    """Verify tracked runnable Dockerfiles use a non-root final user.

    The NGINX runtime images must also use an unprivileged port and writable
    PID/body-temp paths so the non-root declaration is operational rather than
    a static-scanner-only change.
    """
    issues: list[str] = []
    for relative_path in DOCKER_RUNTIME_PATHS:
        path = REPO_ROOT / relative_path
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            issues.append(f"{relative_path} unreadable: {exc}")
            continue
        issues.extend(_docker_runtime_content_issues(relative_path, content))

    if issues:
        return _result("docker-runtime-security", FAIL, "; ".join(issues))
    return _result(
        "docker-runtime-security",
        PASS,
        "tracked runnable Dockerfiles use operational non-root runtimes",
    )


def check_trivy_local_scan_scope() -> CheckResult:
    """Verify local Trivy scans exclude ignored adapters and build output."""
    makefile = REPO_ROOT / "Makefile"
    try:
        content = makefile.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _result(
            "trivy-local-scope", FAIL, f"Makefile unreadable: {exc}"
        )

    # A Trivy invocation may list one directory per --skip-dirs option
    # (current Makefile form) or several whitespace-separated directories
    # under a single option.  Normalize line continuations first, then
    # collect the value set of every option so both forms are recognized.
    # Collection stops at the next option boundary (a token beginning
    # with --), so arguments of a LATER option (e.g. --cache-dir values
    # on the same line) are never mistaken for skipped directories.
    flattened = content.replace("\\\n", " ")
    marked = flattened.replace("--skip-dirs", "\x00")
    skip_dirs_values: set[str] = set()
    for entry in marked.split("\x00")[1:]:
        values = entry.split("\n", 1)[0].strip()
        for value in values.split():
            if value.startswith("--"):
                break
            skip_dirs_values.add(value.strip("\"'"))

    missing = [
        directory
        for directory in TRIVY_REQUIRED_LOCAL_EXCLUSIONS
        if directory not in skip_dirs_values
    ]
    if missing:
        return _result(
            "trivy-local-scope",
            FAIL,
            "local Trivy scan includes ignored paths: " + ", ".join(missing),
        )
    return _result(
        "trivy-local-scope",
        PASS,
        "local Trivy scan excludes ignored adapters and generated reports",
    )


def check_fuzz_target_determinism() -> CheckResult:
    """Verify fuzz targets do not use non-deterministic APIs (FUZZ-003).

    Scans all fuzz target source files for forbidden API patterns that would
    violate the deterministic execution requirement: network I/O, filesystem
    writes, system time for logic branches, external random sources, environment
    variable reads, and process/thread creation.

    Returns:
        CheckResult with PASS if no forbidden patterns are found, or FAIL
        listing the violations.
    """
    fuzz_targets_dir = (
        REPO_ROOT / "components" / "rust-converter" / "fuzz" / "fuzz_targets"
    )
    if not fuzz_targets_dir.exists():
        return _result(
            "fuzz-target-determinism",
            FAIL,
            "FUZZ-003: fuzz targets directory not found",
        )

    # Forbidden API patterns that indicate non-deterministic behavior.
    # Each tuple is (pattern_regex, description).
    forbidden_patterns: list[tuple[str, str]] = [
        (r"\bstd::net\b", "network I/O (std::net)"),
        (r"\bTcpStream\b", "network I/O (TcpStream)"),
        (r"\bUdpSocket\b", "network I/O (UdpSocket)"),
        (r"\bstd::fs::(?:write|create_dir|remove)", "filesystem write"),
        (r"\bFile::create\b", "filesystem write (File::create)"),
        (r"\bSystemTime::now\b", "system time"),
        (r"\bInstant::now\b", "system time (Instant::now)"),
        (r"\brand::thread_rng\b", "external random (thread_rng)"),
        (r"\bOsRng\b", "external random (OsRng)"),
        (r"\bstd::env::var\b", "environment variable read"),
        (r"\bstd::process::Command\b", "process creation"),
        (r"\bstd::thread::spawn\b", "thread creation"),
    ]

    violations: list[str] = []
    for target_file in sorted(fuzz_targets_dir.glob("*.rs")):
        try:
            text = target_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            violations.append(f"{target_file.name}:unreadable")
            continue

        violations.extend(
            f"{target_file.name}:{desc}"
            for pattern, desc in forbidden_patterns
            if re.search(pattern, text)
        )
    if violations:
        return _result(
            "fuzz-target-determinism",
            FAIL,
            f"FUZZ-003 violations: {'; '.join(violations)}",
        )

    return _result(
        "fuzz-target-determinism",
        PASS,
        "fuzz targets are free of non-deterministic API usage (FUZZ-003 satisfied)",
    )


def check_fuzz_gitignore() -> CheckResult:
    """Verify that .gitignore excludes fuzz artifact and corpus paths.

    Checks that the root .gitignore contains entries for fuzz artifacts
    and corpus directories to prevent generated fuzzing outputs from
    being accidentally committed.

    Returns:
        CheckResult with PASS if exclusion entries are found, or
        WARN_NEEDS_AUTHOR_REVIEW if expected patterns are missing.
    """
    gitignore_path = REPO_ROOT / ".gitignore"
    if not gitignore_path.exists():
        return _result(
            "fuzz-gitignore",
            WARN_NEEDS_AUTHOR_REVIEW,
            ".gitignore not found; cannot verify fuzz exclusion entries",
        )

    try:
        text = gitignore_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return _result(
            "fuzz-gitignore",
            WARN_NEEDS_AUTHOR_REVIEW,
            ".gitignore unreadable",
        )

    # Check for fuzz artifact/corpus exclusion patterns
    fuzz_patterns = [
        r"fuzz/artifacts",
        r"fuzz/corpus",
    ]
    if any(pat in text for pat in fuzz_patterns):
        return _result(
            "fuzz-gitignore",
            PASS,
            "fuzz artifact/corpus paths excluded in .gitignore",
        )
    else:
        return _result(
            "fuzz-gitignore",
            WARN_NEEDS_AUTHOR_REVIEW,
            ".gitignore missing fuzz artifact/corpus exclusion entries",
        )


def check_fuzz_guide() -> CheckResult:
    """Verify that a fuzz README guide exists.

    Checks for the fuzz guide at both the top-level and component-level paths.
    At least one must exist.

    Returns:
        CheckResult with PASS if at least one fuzz guide exists, or
        FAIL if neither location has a README.
    """
    candidates = [
        REPO_ROOT / FUZZ_README_REL,
        REPO_ROOT / COMPONENT_FUZZ_README_REL,
    ]
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return _result(
            "fuzz-guide",
            FAIL,
            f"{FUZZ_README_REL} not found (checked both fuzz guide locations)",
        )

    locations = ", ".join(_display_path(p) for p in existing)
    return _result(
        "fuzz-guide",
        PASS,
        f"fuzz guide present: {locations}",
    )


def collect_results(full: bool = False) -> list[CheckResult]:
    """Run all harness-sync checks and return their results.

    Loads the manifest first; if that fails or the manifest structure is
    invalid, returns early with a single failure result.  Otherwise
    returns results for manifest structure, truth surfaces, risk-pack
    docs, harness docs, AGENTS.md map, and optional Kiro adapters.

    Args:
        full: If True, treat optional adapter drift as a blocking failure.

    Returns:
        List of CheckResult instances, one per check.
    """
    try:
        manifest = _load_manifest()
    except ValueError as exc:
        return [_result("manifest-load", FAIL, str(exc))]

    manifest_structure = _check_manifest_structure(manifest)
    if manifest_structure.status == FAIL:
        return [manifest_structure]

    return [
        manifest_structure,
        _check_manifest_command_reachability(manifest),
        _check_truth_surfaces(manifest),
        _check_risk_pack_docs(manifest),
        _check_harness_docs(manifest),
        _check_agents_map(),
        _check_rule_checks(manifest),
        _check_agents_rule_coverage(manifest),
        _check_ci_trigger_coverage(manifest.get("rule_checks") or []),
        _check_e2e_harness_contract(),
        _check_e2e_migration_policy(),
        _check_recent_analysis_reports(),
        check_clusterfuzzlite_build_config(),
        check_docker_runtime_security(),
        check_trivy_local_scan_scope(),
        check_cfl_workflows(),
        check_batch_prune_pairing(),
        check_fuzz_target_determinism(),
        check_fuzz_gitignore(),
        check_fuzz_guide(),
        _check_optional_kiro(manifest, full=full),
    ]


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments for the harness-sync checker.

    Args:
        argv: Argument strings to parse (typically sys.argv[1:]).

    Returns:
        Parsed namespace with the ``full`` boolean flag.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full",
        action="store_true",
        help="treat optional local adapter drift as blocking",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the harness-sync CLI.

    Runs all checks, prints each result, and returns an exit code of 1
    if any result is blocking (FAIL, or WARN in full mode), otherwise 0.

    Args:
        argv: Command-line arguments. Defaults to sys.argv[1:].

    Returns:
        Exit code: 0 for success, 1 for failure.
    """
    selected_argv = sys.argv[1:] if argv is None else argv
    args = parse_args(selected_argv)
    results = collect_results(full=args.full)
    failing_statuses = {FAIL}
    if args.full:
        failing_statuses.add(WARN_NEEDS_AUTHOR_REVIEW)

    for result in results:
        print(f"{result.status:<24} {result.name:<20} {result.detail}")

    return 1 if any(result.status in failing_statuses for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
