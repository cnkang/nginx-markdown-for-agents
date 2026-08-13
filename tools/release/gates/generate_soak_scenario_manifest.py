#!/usr/bin/env python3
"""Generate a candidate-bound short-soak scenario manifest."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCOPE = REPO_ROOT / "release" / "scope" / "short-soak-scope.json"
DEFAULT_VERSION = "0.9.2"
MANIFEST_NAME = "short-soak-scenario-manifest.json"
SCENARIO_IDS = ("small", "medium", "large")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)

if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
from lib.path_validation import (  # noqa: E402
    validate_filename_strict,
    validate_read_path,
    validate_write_path_within_root,
)


def _load_json(path: Path) -> dict:
    validated_path = validate_read_path(path, purpose="short-soak scope")
    try:
        value = json.loads(validated_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read scope {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("short-soak scope must be a JSON object")
    return value


def _validate_scope(scope: dict) -> None:
    if scope.get("schema_version") != "release.scope.short-soak.v1":
        raise ValueError("unexpected short-soak scope schema_version")
    duration = scope.get("duration_minutes")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise ValueError(
            "short-soak scope duration_minutes must be a positive finite number"
        )
    concurrency = scope.get("concurrency")
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency <= 0:
        raise ValueError("short-soak scope concurrency must be positive")
    memory = scope.get("scenario_memory_bytes")
    if not isinstance(memory, dict) or set(memory) != set(SCENARIO_IDS):
        raise ValueError("short-soak scope must define all scenario memory limits")
    if not all(
        isinstance(memory[key], int)
        and not isinstance(memory[key], bool)
        and memory[key] > 0
        for key in SCENARIO_IDS
    ):
        raise ValueError("short-soak scenario memory limits must be positive integers")


def build_manifest(
    scope: dict,
    candidate_sha: str,
    scope_path: Path,
    created_at: str,
) -> dict:
    """Build the immutable scenario identity consumed by the soak gate."""
    if not SHA_RE.fullmatch(candidate_sha):
        raise ValueError("candidate SHA must be 40 lowercase hexadecimal characters")
    if (
        not isinstance(created_at, str)
        or not UTC_TIMESTAMP_RE.fullmatch(created_at)
    ):
        raise ValueError("created_at must be an ISO-8601 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(created_at[:-1])
    except ValueError as exc:
        raise ValueError("created_at must be a valid ISO-8601 UTC timestamp") from exc
    _validate_scope(scope)
    return {
        "schema_version": "release.short-soak-scenario-manifest.v1",
        "candidate_sha": candidate_sha,
        "created_at": created_at,
        "duration_minutes": scope["duration_minutes"],
        "concurrency": scope["concurrency"],
        "corpus": [
            {
                "id": scenario_id,
                "conversion_memory_bytes": scope["scenario_memory_bytes"][scenario_id],
            }
            for scenario_id in SCENARIO_IDS
        ],
        "scenario_refs": [scope_path.relative_to(REPO_ROOT).as_posix()],
    }


def _output_path(version: str) -> Path:
    safe_version = validate_filename_strict(version, purpose="release version")
    if not VERSION_RE.fullmatch(safe_version):
        raise ValueError(f"Invalid release version: {version!r}")
    expected_output = (
        REPO_ROOT / "artifacts" / "release" / safe_version / MANIFEST_NAME
    ).resolve()
    return expected_output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", default=str(DEFAULT_SCOPE))
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument(
        "--created-at",
        help="Timestamp to record in the manifest (defaults to the generation time)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        scope_path = (REPO_ROOT / args.scope).resolve()
        if not scope_path.is_relative_to(REPO_ROOT):
            raise ValueError("scope path escapes repository root")
        output = validate_write_path_within_root(
            _output_path(args.version),
            REPO_ROOT,
            purpose="short-soak manifest",
        )
        manifest = build_manifest(
            _load_json(scope_path),
            args.candidate_sha,
            scope_path,
            args.created_at or datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        # NOSONAR suppression for pythonsecurity:S2083: _output_path accepts
        # only the validated release version and exact canonical filename.
        output.write_text(  # NOSONAR
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {output.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
