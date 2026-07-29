#!/usr/bin/env python3
"""Finalize a canonical module baseline from a retained raw benchmark report.

The module benchmark harness writes a *raw* report containing
``module_benchmark`` and ``decompression_coverage`` but no provenance.
Baseline validators require a ``baseline_policy`` object with ``type``,
full 40-character ``source_git_commit``, ``source_run``,
``source_artifact``, ``source_artifact_sha256`` and
``measurement_timestamp``.  This finalizer bridges that gap by:

  * reading the raw report from ``--raw-input``;
  * computing the SHA-256 of the raw file and recording it in the policy;
  * taking ``measurement_timestamp`` from ``module_benchmark.timestamp``
    so the provenance reflects the real measurement time, not the
    finalizer's execution time;
  * verifying that ``raw.module_benchmark.git_commit`` matches the
    declared ``--source-git-commit`` prefix;
  * writing the finalized baseline to a *separate* ``--output`` path via
    a temporary file plus ``os.replace()`` so a write failure never
    leaves a half-written canonical baseline;
  * refusing to overwrite an already-normalized baseline as
    ``verbatim_run`` and refusing absolute / ``..`` / symlink-escaping
    paths.

For ``verbatim_run`` the finalized file is the raw report plus a
``baseline_policy`` block; no measured metric is modified.  For
``conservative_normalized`` the caller may pass ``--policy-type
conservative_normalized`` with ``--adjustments`` documentation; truth
evidence (path, fallback, output, memory, environment) is never
modified and the finalizer records the raw digest so the validator can
machine-verify the relationship between the finalized and raw files.

Usage:
    python3 tools/perf/finalize_module_baseline.py \\
        --raw-input perf/baselines/module-baseline-091-raw.json \\
        --output perf/baselines/module-baseline-091.json \\
        --source-git-commit <40-char SHA> \\
        --source-run <GitHub Actions run URL> \\
        [--policy-type verbatim_run|conservative_normalized] \\
        [--measurement-timestamp <UTC ISO-8601>]

``--source-artifact`` is derived from ``--raw-input`` so the caller
cannot declare a retained artifact that points at a different file.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The shared provenance and path-validation modules live under tools/lib.
# Add their package root explicitly so direct CI execution has the same import
# contract as the other repository-owned Python harnesses.
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.baseline_provenance import (  # noqa: E402 - direct script import
    validate_iso_utc,
    validate_raw_commit_match,
    validate_source_run,
)
from lib.path_validation import (  # noqa: E402 - direct script import
    validate_read_path,
    validate_write_path_within_root,
)
from lib.executable_validation import (  # noqa: E402 - direct script import
    resolve_approved_executable,
)

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_POLICY_TYPES = frozenset({"verbatim_run", "conservative_normalized"})


def _resolve_repo_relative(path: str, *, must_exist: bool, purpose: str) -> Path:
    """Resolve a repository-relative path, rejecting escapes.

    Absolute paths, ``..`` traversal, and symlinks resolving outside the
    repository checkout are rejected.  The returned path is absolute and
    contained within ``REPO_ROOT``.
    """
    raw = str(path)
    if raw == "":
        raise ValueError(f"{purpose} path must not be empty")
    if Path(raw).is_absolute():
        raise ValueError(f"{purpose} path must be relative, not absolute: {raw!r}")
    components = raw.replace("\\", "/").split("/")
    if ".." in components:
        raise ValueError(
            f"{purpose} path must not contain '..' traversal: {raw!r}"
        )

    resolved = Path(os.path.realpath(str(REPO_ROOT / raw)))
    root = REPO_ROOT.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as e:
        raise ValueError(
            f"{purpose} path {resolved} escapes repository root {root}"
        ) from e

    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"{purpose} path does not exist: {resolved}")
    return resolved


def _sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 digest of a file's bytes."""
    validated_path = validate_read_path(path, purpose="baseline input")
    digest = hashlib.sha256()
    with validated_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_iso_utc(timestamp: str, *, field: str) -> str:
    """Validate a UTC ISO-8601 timestamp string and return it."""
    return validate_iso_utc(timestamp, field=field)


def _validate_source_run(source_run: str) -> list[str]:
    """Validate the canonical provenance run URL.

    A canonical GitHub Actions baseline must record a run URL that
    contains a concrete run id and an attempt so the evidence can be
    located.  Historical exceptions are handled by the caller, not here.
    """
    return validate_source_run(source_run, repo_root=REPO_ROOT)


_SOURCE_RUN_ID_RE = re.compile(r"^[1-9]\d*$", re.ASCII)
_SOURCE_RUN_URL_RE = re.compile(
    r"^https://github\.com/"
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    r"/actions/runs/[1-9]\d*"
    r"/attempts/[1-9]\d*$",
    re.ASCII,
)


def _resolve_github_repository(repo_root: Path) -> str | None:
    """Return the checkout's GitHub repository slug when discoverable."""
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if repository and re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        return repository.lower()

    git_executable = resolve_approved_executable("git")
    if git_executable is None:
        return None

    try:
        result = subprocess.run(
            [git_executable, "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(repo_root),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    remote = result.stdout.strip()
    match = re.search(
        r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?$",
        remote,
        re.IGNORECASE,
    )
    return match.group(1).lower() if match else None


def _normalize_source_run(value: str, *, repo_root: Path) -> str:
    """Normalize a source-run value into canonical provenance URL format.

    Bare numeric run IDs (from deprecated ``--source-run-id``) are converted
    to full GitHub Actions URLs using the discovered repository and
    ``attempt=1``.  Full URLs pass through unchanged.  Values that are
    neither a bare ID nor a full URL are returned as-is so that
    ``_validate_source_run`` can report the format error.
    """
    if _SOURCE_RUN_URL_RE.fullmatch(value):
        return value
    if not _SOURCE_RUN_ID_RE.fullmatch(value):
        return value
    repository = _resolve_github_repository(repo_root)
    if repository is None:
        print(
            "WARNING: Cannot normalize --source-run-id into a canonical URL: "
            "GitHub repository slug is not discoverable. "
            "Use --source-run with the full URL instead.",
            file=sys.stderr,
        )
        return value
    print(
        "NOTE: Normalized --source-run-id to canonical URL with attempt=1; "
        "retried runs must use --source-run explicitly.",
        file=sys.stderr,
    )
    return f"https://github.com/{repository}/actions/runs/{value}/attempts/1"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize a canonical module baseline from a retained raw report.",
    )
    parser.add_argument(
        "--raw-input",
        required=False,
        default=None,
        help="Repository-relative path to the retained raw benchmark report.",
    )
    parser.add_argument(
        "--input",
        required=False,
        default=None,
        help=(
            "Deprecated; use --raw-input. "
            "Repository-relative path to the retained raw benchmark report."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Repository-relative path to write the finalized baseline.",
    )
    parser.add_argument(
        "--source-git-commit",
        required=True,
        help="Full 40-character lowercase git SHA of the commit producing the run.",
    )
    parser.add_argument(
        "--source-run",
        required=False,
        default=None,
        help="GitHub Actions run URL with /attempts/<attempt> provenance.",
    )
    parser.add_argument(
        "--source-run-id",
        required=False,
        default=None,
        help="Deprecated; use --source-run with a full URL. Workflow run id.",
    )
    parser.add_argument(
        "--source-artifact",
        default=None,
        help=(
            "Repository-relative path to the retained raw artifact. "
            "Defaults to --raw-input; when provided it must equal --raw-input."
        ),
    )
    parser.add_argument(
        "--policy-type",
        choices=sorted(_SUPPORTED_POLICY_TYPES),
        default="verbatim_run",
        help="Baseline policy type (default: verbatim_run).",
    )
    parser.add_argument(
        "--measurement-timestamp",
        default=None,
        help=(
            "UTC ISO-8601 measurement timestamp; defaults to "
            "module_benchmark.timestamp from the raw report. When "
            "provided it must match the raw report timestamp."
        ),
    )
    parser.add_argument(
        "--adjustments",
        default=None,
        help="For conservative_normalized: JSON document of adjustment details.",
    )
    parser.add_argument(
        "--adjustment-reason",
        default=None,
        help="For conservative_normalized: human-readable reason for adjustments.",
    )
    parser.add_argument(
        "--adjustment-date",
        default=None,
        help="For conservative_normalized: ISO-8601 date of the adjustment.",
    )
    return parser.parse_args(argv)


def _build_policy(
    *,
    policy_type: str,
    source_git_commit: str,
    source_run: str,
    source_artifact: str,
    raw_sha256: str,
    measurement_timestamp: str,
    adjustment_fields: dict[str, str | None],
) -> dict:
    """Build the baseline_policy block for the finalized baseline.

    ``adjustment_fields`` carries the conservative_normalized-only fields
    (``adjustments``, ``adjustment_reason``, ``adjustment_date``) so the
    parameter count stays below the complexity threshold.
    """
    policy: dict = {
        "type": policy_type,
        "source_git_commit": source_git_commit,
        "source_run": source_run,
        "source_artifact": source_artifact,
        "source_artifact_sha256": raw_sha256,
        "measurement_timestamp": measurement_timestamp,
    }
    if policy_type == "verbatim_run":
        policy["normalization"] = "none"
    elif policy_type == "conservative_normalized":
        policy["normalization"] = "conservative"
        policy["adjustment_reason"] = adjustment_fields.get("adjustment_reason") or ""
        policy["adjustment_date"] = adjustment_fields.get("adjustment_date") or ""
        policy["adjustments"] = _parse_adjustments(
            adjustment_fields.get("adjustments")
        )
        policy["adjustment_rule"] = (
            "RPS may only be rounded downward or lowered; "
            "latency/TTFB/TTLB may only be rounded upward or raised."
        )
    return policy


def _parse_adjustments(adjustments: str | None) -> dict:
    """Parse the --adjustments JSON document, returning {} when absent."""
    if not adjustments:
        return {}
    try:
        parsed = json.loads(adjustments)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"--adjustments must be valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ValueError("--adjustments must be a JSON object")
    return parsed


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON to ``path`` via a temp file plus os.replace().

    A write failure removes the temp file and never leaves a partial
    canonical baseline behind.
    """
    validated_path = validate_write_path_within_root(
        path, REPO_ROOT, purpose="finalized baseline"
    )
    validated_path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=validated_path.parent,
            prefix=f".{validated_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            tmp = Path(stream.name)
            tmp = validate_write_path_within_root(
                tmp,
                REPO_ROOT,
                purpose="temporary finalized baseline",
            )
            stream.write(json.dumps(payload, indent=2, ensure_ascii=False))
            stream.write("\n")
        os.replace(tmp, validated_path)
    except (OSError, ValueError):
        if tmp is not None:
            with contextlib.suppress(OSError, FileNotFoundError):
                tmp.unlink(missing_ok=True)
        raise


def _validate_raw_commit_match(
    raw_report: dict, source_git_commit: str,
) -> list[str]:
    """Verify raw.module_benchmark.git_commit matches the declared SHA prefix."""
    return validate_raw_commit_match(raw_report, source_git_commit)


def _extract_raw_timestamp(raw_report: dict) -> str:
    """Return module_benchmark.timestamp from the raw report."""
    mb = raw_report.get("module_benchmark", {})
    if not isinstance(mb, dict):
        raise ValueError(
            "raw report is missing a 'module_benchmark' object; cannot read "
            "the measurement timestamp"
        )
    ts = mb.get("timestamp")
    if not isinstance(ts, str) or not ts.strip():
        raise ValueError(
            "raw report module_benchmark.timestamp is missing or not a "
            "non-empty string; the measurement timestamp must come from the "
            "real benchmark run, not the finalizer's execution time"
        )
    return _validate_iso_utc(ts, field="module_benchmark.timestamp")


def _resolve_io_paths(
    args: argparse.Namespace,
) -> tuple[Path, Path, str, str] | int:
    """Resolve and validate raw-input, output, and source_artifact paths.

    Returns ``(raw_input_path, output_path, source_run, source_artifact)`` on success
    or an integer exit code on failure.
    """
    raw_input = args.raw_input or args.input
    source_run = args.source_run or args.source_run_id

    if not raw_input:
        print(
            "ERROR: --raw-input (or deprecated --input) is required",
            file=sys.stderr,
        )
        return 1
    if not source_run:
        print(
            "ERROR: --source-run (or deprecated --source-run-id) is required",
            file=sys.stderr,
        )
        return 1
    if args.input:
        print(
            "WARNING: --input is deprecated; use --raw-input instead",
            file=sys.stderr,
        )
    if args.source_run_id:
        print(
            "WARNING: --source-run-id is deprecated; use --source-run "
            "with a full GitHub Actions run URL instead",
            file=sys.stderr,
        )

    source_run = _normalize_source_run(source_run, repo_root=REPO_ROOT)

    try:
        raw_input_path = _resolve_repo_relative(
            raw_input, must_exist=True, purpose="--raw-input"
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        output_path = _resolve_repo_relative(
            args.output, must_exist=False, purpose="--output"
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if raw_input_path.resolve() == output_path.resolve():
        print(
            "ERROR: --raw-input and --output must not be the same path; "
            "the finalized baseline must be a separate file from the raw report.",
            file=sys.stderr,
        )
        return 1

    source_artifact = args.source_artifact or raw_input
    if args.source_artifact and args.source_artifact != raw_input:
        print(
            f"ERROR: --source-artifact ({args.source_artifact!r}) must match "
            f"--raw-input ({raw_input!r}); the retained artifact must be "
            f"the actual raw report being finalized.",
            file=sys.stderr,
        )
        return 1

    return raw_input_path, output_path, source_run, source_artifact


def _resolve_measurement_timestamp(
    args: argparse.Namespace, raw_report: dict,
) -> str | int:
    """Determine the measurement timestamp from the raw report or CLI.

    Returns the timestamp string on success or an exit code on failure.
    """
    try:
        raw_timestamp = _extract_raw_timestamp(raw_report)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not args.measurement_timestamp:
        return raw_timestamp

    try:
        declared_ts = _validate_iso_utc(
            args.measurement_timestamp, field="--measurement-timestamp"
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if declared_ts != raw_timestamp:
        print(
            f"ERROR: --measurement-timestamp ({declared_ts!r}) does not "
            f"match the raw report module_benchmark.timestamp "
            f"({raw_timestamp!r}); the finalized provenance must reflect "
            f"the real measurement time.",
            file=sys.stderr,
        )
        return 1
    return declared_ts


def _check_refinalize(args: argparse.Namespace, raw_report: dict) -> int | None:
    """Refuse to re-finalize an already-normalized baseline as verbatim_run.

    Returns an exit code on failure or ``None`` to continue.
    """
    if not isinstance(raw_report.get("baseline_policy"), dict):
        return None
    existing_type = raw_report["baseline_policy"].get("type")
    if args.policy_type == "verbatim_run" and existing_type:
        print(
            f"ERROR: --raw-input already contains a baseline_policy of "
            f"type {existing_type!r}; verbatim_run finalization must "
            f"start from a raw report without a policy block.  Use the "
            f"original raw report as --raw-input.",
            file=sys.stderr,
        )
        return 1
    return None


def _print_errors(errors: list[str]) -> int:
    """Print validation errors and return the command failure status."""
    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return 1


def _load_raw_report(raw_input_path: Path) -> dict | None:
    """Read and decode the retained raw report after path validation."""
    try:
        validated_path = validate_read_path(
            raw_input_path, purpose="raw baseline report"
        )
        raw_report = json.loads(
            validated_path.read_text(encoding="utf-8")
        )
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: failed to read raw report: {exc}", file=sys.stderr)
        return None
    if not isinstance(raw_report, dict):
        print("ERROR: raw report must be a JSON object", file=sys.stderr)
        return None
    return raw_report


def _finalize_report(
    args: argparse.Namespace,
    io_result: tuple[Path, Path, str, str],
    measurement_timestamp: str,
    raw_report: dict,
) -> int:
    """Build the policy-bound report and atomically write the output."""
    raw_input_path, output_path, source_run, source_artifact = io_result
    raw_sha256 = _sha256_file(raw_input_path)
    if not _SHA256_RE.fullmatch(raw_sha256):
        print(
            f"ERROR: computed raw SHA-256 is not a 64-char hex digest "
            f"(got {raw_sha256!r})",
            file=sys.stderr,
        )
        return 1

    finalized = dict(raw_report)
    try:
        policy = _build_policy(
            policy_type=args.policy_type,
            source_git_commit=args.source_git_commit,
            source_run=source_run.strip(),
            source_artifact=source_artifact,
            raw_sha256=raw_sha256,
            measurement_timestamp=measurement_timestamp,
            adjustment_fields={
                "adjustments": args.adjustments,
                "adjustment_reason": args.adjustment_reason,
                "adjustment_date": args.adjustment_date,
            },
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finalized["baseline_policy"] = policy

    try:
        _atomic_write_json(output_path, finalized)
    except OSError as exc:
        print(f"ERROR: failed to write finalized baseline: {exc}", file=sys.stderr)
        return 1

    print(f"Finalized baseline written to {output_path}")
    print(f"  policy type: {args.policy_type}")
    print(f"  source_git_commit: {args.source_git_commit}")
    print(f"  source_artifact: {source_artifact}")
    print(f"  source_artifact_sha256: {raw_sha256}")
    print(f"  measurement_timestamp: {measurement_timestamp}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if not _FULL_SHA_RE.fullmatch(args.source_git_commit):
        return _print_errors([
            f"--source-git-commit must be a full 40-character lowercase git "
            f"SHA (got {args.source_git_commit!r})",
        ])

    io_result = _resolve_io_paths(args)
    if isinstance(io_result, int):
        return io_result
    raw_input_path, _output_path, source_run, _source_artifact = io_result

    source_errors = _validate_source_run(source_run)
    if source_errors:
        return _print_errors(source_errors)

    raw_report = _load_raw_report(raw_input_path)
    if raw_report is None:
        return 1

    commit_errors = _validate_raw_commit_match(raw_report, args.source_git_commit)
    if commit_errors:
        return _print_errors(commit_errors)

    ts_result = _resolve_measurement_timestamp(args, raw_report)
    if isinstance(ts_result, int):
        return ts_result

    if rc := _check_refinalize(args, raw_report):
        return rc

    return _finalize_report(
        args,
        io_result,
        ts_result,
        raw_report,
    )


if __name__ == "__main__":
    raise SystemExit(main())
