#!/usr/bin/env python3
"""Run the streaming parity harness and write candidate-bound evidence.

The output is deliberately written to a caller-selected path, normally a
runner temporary directory.  It is a generated release input, never a
checked-in status note or a substitute for executing the parity test.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from tools.release.gates.validate_streaming_evidence import (  # noqa: E402
    EXPECTED_CORPUS_ROOT,
    EXPECTED_REGISTRY_PATH,
    SCHEMA_VERSION,
    VERIFIED_BY,
    VERIFICATION_COMMAND,
    _count_registry,
    _sha256_file,
    _sha256_tree,
)
from lib.path_validation import (  # noqa: E402
    validate_read_path,
    validate_write_path_within_root,
)

MARKER = "STREAMING_PARITY_EVIDENCE_V1 "
PARITY_COMMAND = [
    "cargo",
    "test",
    "--locked",
    "--manifest-path",
    "components/rust-converter/Cargo.toml",
    "--features",
    "streaming",
    "--test",
    "streaming_parity",
    "corpus_driven_differential_harness",
    "--",
    "--nocapture",
]
PARITY_MARKER_FIELDS = (
    "total_comparisons",
    "identical_count",
    "known_difference_count",
    "unknown_difference_count",
    "error_parity_mismatch_count",
)


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_marker_counts(payload: dict[str, object]) -> None:
    for field in PARITY_MARKER_FIELDS:
        if not _is_nonnegative_int(payload.get(field)):
            raise ValueError(
                f"parity marker field {field} must be a non-negative integer"
            )
    if (
        payload["identical_count"]
        + payload["known_difference_count"]
        + payload["unknown_difference_count"]
        != payload["total_comparisons"]
    ):
        raise ValueError("parity marker comparison counts are not a partition")


def _validate_marker_ids(payload: dict[str, object]) -> None:
    ids = payload.get("known_difference_ids")
    if not isinstance(ids, list) or any(
        not isinstance(entry_id, str) or not entry_id.strip() for entry_id in ids
    ):
        raise ValueError("parity marker known_difference_ids must be non-empty strings")
    if len(set(ids)) != len(ids):
        raise ValueError("parity marker known_difference_ids must be unique")
    observations = payload.get("known_difference_observation_ids")
    if not isinstance(observations, list) or any(
        not isinstance(entry_id, str) or not entry_id.strip()
        for entry_id in observations
    ):
        raise ValueError(
            "parity marker known_difference_observation_ids must be "
            "non-empty strings"
        )
    if payload["known_difference_count"] != len(observations):
        raise ValueError(
            "parity marker known-difference count does not match observations"
        )
    if set(ids) != set(observations):
        raise ValueError(
            "parity marker unique ids do not cover all observations"
        )


def _parse_marker(stdout: str) -> dict[str, object]:
    """Extract exactly one machine-readable result from the test output."""
    payloads = [line[len(MARKER):] for line in stdout.splitlines()
                if line.startswith(MARKER)]
    if len(payloads) != 1:
        raise ValueError(
            "streaming parity harness emitted an unexpected number of evidence "
            f"markers ({len(payloads)})"
        )
    try:
        payload = json.loads(payloads[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"streaming parity evidence marker is invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("streaming parity evidence marker must be a JSON object")
    _validate_marker_counts(payload)
    _validate_marker_ids(payload)
    return payload


def _run_parity() -> dict[str, object]:
    try:
        result = subprocess.run(
            PARITY_COMMAND,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"unable to run streaming parity harness: {exc}") from exc
    if result.returncode != 0:
        diagnostics = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(
            "streaming parity harness failed (exit "
            f"{result.returncode}):\n{diagnostics[-8000:]}"
        )
    return _parse_marker(result.stdout)


def _git_output(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _rustc_version() -> str:
    result = subprocess.run(
        ["rustc", "--version"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("unable to resolve rustc --version")
    return result.stdout.strip()


def _registry_observations(
    observation_ids: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    registry_path = REPO_ROOT / EXPECTED_REGISTRY_PATH
    validated_registry_path = validate_read_path(
        registry_path, purpose="known-differences registry"
    )
    registry = tomllib.loads(
        validated_registry_path.read_text(encoding="utf-8")
    )
    entries = registry.get("difference")
    if not isinstance(entries, list):
        raise ValueError("known-differences registry has no difference entries")
    by_id: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ValueError("known-differences registry contains an invalid id")
        entry_id = entry["id"]
        if entry_id in by_id:
            raise ValueError(f"known-differences registry repeats id {entry_id!r}")
        by_id[entry_id] = entry

    drift = Counter()
    severity = Counter()
    for entry_id in observation_ids:
        entry = by_id.get(entry_id)
        if entry is None:
            raise ValueError(f"parity harness emitted unknown registry id {entry_id!r}")
        if entry.get("acceptable") is not True:
            raise ValueError(f"parity harness emitted non-acceptable id {entry_id!r}")
        drift_type = entry.get("drift_type")
        level = entry.get("severity")
        if not isinstance(drift_type, str) or not isinstance(level, str):
            raise ValueError(f"registry entry {entry_id!r} lacks drift metadata")
        drift[drift_type] += 1
        severity[level] += 1
    return dict(drift), dict(severity)


def _validate_output_path(raw_output: str) -> Path:
    """Resolve an output path and reject writes inside the repository."""
    raw_path = Path(raw_output).expanduser()
    if raw_path.is_symlink():
        raise ValueError("streaming evidence output must not be a symlink")
    output = validate_read_path(
        raw_path, must_exist=False, purpose="streaming evidence output"
    )
    try:
        output.relative_to(REPO_ROOT)
    except ValueError:
        return output
    raise ValueError("streaming evidence output must be outside the repository")


def _write_summary(output: Path, summary: dict[str, object]) -> None:
    output = output.expanduser().resolve()
    if output == REPO_ROOT or output.is_dir():
        raise ValueError("streaming evidence output must be a file outside the repository")
    try:
        output.relative_to(REPO_ROOT)
    except ValueError:
        pass
    else:
        raise ValueError("streaming evidence output must be outside the repository")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary = validate_write_path_within_root(
        temporary,
        output.parent,
        purpose="streaming evidence temporary output",
    )
    try:
        temporary.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_summary(parity: dict[str, object], *, allow_dirty: bool) -> dict[str, object]:
    """Build the generated summary after the parity command succeeds."""
    status = _git_output("status", "--porcelain", "--untracked-files=all")
    if status and not allow_dirty:
        raise RuntimeError(
            "git worktree is dirty; candidate-bound evidence requires a clean "
            "checkout (use --allow-dirty only for local diagnostics)"
        )
    registry_path = REPO_ROOT / EXPECTED_REGISTRY_PATH
    corpus_path = REPO_ROOT / EXPECTED_CORPUS_ROOT
    validated_registry_path = validate_read_path(
        registry_path, purpose="known-differences registry"
    )
    registry_drift, registry_severity, registry_total = _count_registry(
        tomllib.loads(validated_registry_path.read_text(encoding="utf-8"))
    )
    observed_drift, observed_severity = _registry_observations(
        parity["known_difference_observation_ids"]
    )
    commit = _git_output("rev-parse", "HEAD")
    return {
        "schema_version": SCHEMA_VERSION,
        "verified_by": VERIFIED_BY,
        "verified_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "total_comparisons": parity["total_comparisons"],
        "identical_count": parity["identical_count"],
        "known_difference_count": parity["known_difference_count"],
        "known_difference_by_drift_type": observed_drift,
        "known_difference_by_severity": observed_severity,
        "known_difference_registry_by_drift_type": dict(registry_drift),
        "known_difference_registry_by_severity": dict(registry_severity),
        "known_difference_ids": parity["known_difference_ids"],
        "known_difference_observation_ids": parity[
            "known_difference_observation_ids"
        ],
        "unknown_difference_count": parity["unknown_difference_count"],
        "error_parity_mismatch_count": parity["error_parity_mismatch_count"],
        "pass": True,
        "known_differences_registry": EXPECTED_REGISTRY_PATH,
        "known_differences_registry_total_entries": registry_total,
        "corpus_root": EXPECTED_CORPUS_ROOT,
        "verification_command": VERIFICATION_COMMAND,
        "verification_result": "PASS",
        "candidate_sha": commit,
        "source_git_commit": commit,
        "source_tree_clean": not bool(status),
        "corpus_sha256": _sha256_tree(corpus_path),
        "registry_sha256": _sha256_file(registry_path),
        "rustc_version": _rustc_version(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="generated summary path outside the repository")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow a dirty worktree for local diagnostics; the result cannot pass --git-head",
    )
    args = parser.parse_args(argv)
    try:
        output = _validate_output_path(args.output)
        parity = _run_parity()
        summary = build_summary(parity, allow_dirty=args.allow_dirty)
        _write_summary(output, summary)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"PASS: generated streaming evidence at {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
