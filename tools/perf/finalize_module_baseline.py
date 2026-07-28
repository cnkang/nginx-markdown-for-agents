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
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT))

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_POLICY_TYPES = frozenset({"verbatim_run", "conservative_normalized"})
_HISTORICAL_BASELINE_COMMIT = "847f90139d287446882052ec78661746541aebff"


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
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_iso_utc(timestamp: str, *, field: str) -> str:
    """Validate a UTC ISO-8601 timestamp string and return it."""
    if not isinstance(timestamp, str) or not timestamp.strip():
        raise ValueError(f"{field} must be a non-empty ISO-8601 string")
    ts = timestamp.strip()
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(f"{field} is not valid ISO-8601: {ts!r} ({e})") from e
    return ts


def _validate_source_run(source_run: str) -> list[str]:
    """Validate the canonical provenance run URL.

    A canonical GitHub Actions baseline must record a run URL that
    contains a concrete run id and an attempt so the evidence can be
    located.  Historical exceptions are handled by the caller, not here.
    """
    errors: list[str] = []
    if not source_run or not source_run.strip():
        errors.append("--source-run must be non-empty")
        return errors
    run = source_run.strip()
    if "actions/runs/" not in run:
        errors.append(
            "--source-run must be a GitHub Actions run URL containing "
            "'actions/runs/<run-id>' (got " + repr(run) + ")"
        )
    if "/attempts/" not in run:
        errors.append(
            "--source-run must include '/attempts/<attempt>' so the exact "
            "workflow attempt can be located (got " + repr(run) + ")"
        )
    return errors


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize a canonical module baseline from a retained raw report.",
    )
    parser.add_argument(
        "--raw-input",
        required=True,
        help="Repository-relative path to the retained raw benchmark report.",
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
        required=True,
        help="GitHub Actions run URL with /attempts/<attempt> provenance.",
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
        return json.loads(adjustments)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"--adjustments must be valid JSON: {exc}"
        ) from exc


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON to ``path`` via a temp file plus os.replace().

    A write failure removes the temp file and never leaves a partial
    canonical baseline behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError, FileNotFoundError):
            tmp.unlink(missing_ok=True)
        raise


def _validate_raw_commit_match(
    raw_report: dict, source_git_commit: str,
) -> list[str]:
    """Verify raw.module_benchmark.git_commit matches the declared SHA prefix."""
    mb = raw_report.get("module_benchmark", {})
    if not isinstance(mb, dict):
        return ["raw report is missing a 'module_benchmark' object"]
    raw_commit = mb.get("git_commit")
    if not isinstance(raw_commit, str) or not raw_commit:
        return [
            "raw report is missing module_benchmark.git_commit; cannot verify "
            "that the raw report came from the declared source commit"
        ]
    if not raw_commit.lower().startswith(source_git_commit[:7].lower()):
        return [
            f"raw report module_benchmark.git_commit={raw_commit!r} does not "
            f"match the declared --source-git-commit prefix "
            f"{source_git_commit[:7]!r}; the finalized baseline must come "
            f"from the same commit as the raw report."
        ]
    return []


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
) -> tuple[Path, Path, str] | int:
    """Resolve and validate raw-input, output, and source_artifact paths.

    Returns ``(raw_input_path, output_path, source_artifact)`` on success
    or an integer exit code on failure.
    """
    try:
        raw_input_path = _resolve_repo_relative(
            args.raw_input, must_exist=True, purpose="--raw-input"
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

    source_artifact = args.source_artifact or args.raw_input
    if args.source_artifact and args.source_artifact != args.raw_input:
        print(
            f"ERROR: --source-artifact ({args.source_artifact!r}) must match "
            f"--raw-input ({args.raw_input!r}); the retained artifact must be "
            f"the actual raw report being finalized.",
            file=sys.stderr,
        )
        return 1

    return raw_input_path, output_path, source_artifact


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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    errors: list[str] = []
    if not _FULL_SHA_RE.match(args.source_git_commit):
        errors.append(
            f"--source-git-commit must be a full 40-character lowercase git "
            f"SHA (got {args.source_git_commit!r})"
        )
    errors.extend(_validate_source_run(args.source_run))

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    io_result = _resolve_io_paths(args)
    if isinstance(io_result, int):
        return io_result
    raw_input_path, output_path, source_artifact = io_result

    try:
        raw_report = json.loads(raw_input_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"ERROR: failed to read raw report: {exc}", file=sys.stderr)
        return 1

    if commit_errors := _validate_raw_commit_match(raw_report, args.source_git_commit):
        for err in commit_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    ts_result = _resolve_measurement_timestamp(args, raw_report)
    if isinstance(ts_result, int):
        return ts_result
    measurement_timestamp = ts_result

    if rc := _check_refinalize(args, raw_report):
        return rc

    raw_sha256 = _sha256_file(raw_input_path)
    if not _SHA256_RE.match(raw_sha256):
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
            source_run=args.source_run.strip(),
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


if __name__ == "__main__":
    raise SystemExit(main())