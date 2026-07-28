#!/usr/bin/env python3
"""Validate retained module benchmark probe artifacts.

The module benchmark writes one response probe triplet for every canonical
scenario.  This validator makes the triplets an explicit evidence gate rather
than relying on upload-artifact's aggregate "at least one file" behavior.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

from lib.path_validation import validate_read_path  # noqa: E402


SCENARIOS = (
    "plain-small",
    "chunked-medium",
    "gzip-large",
    "large-body",
    "streaming-first",
    "gzip-streaming-first",
    "deflate-streaming-first",
    "brotli-streaming-first",
)
RESPONSE_FIELDS = (
    "verdict",
    "curl_exit_code",
    "body_sha256",
    "body_bytes",
    "header_artifact",
    "body_artifact",
    "heading_present",
    "tail_token_present",
    "tail_token_count",
)


def _resolve_root(path: str | Path) -> Path:
    """Resolve a validation root through the shared read-path helper."""
    return validate_read_path(path, purpose="validation root")


def _resolve_repo_relative(
    raw_path: str | Path,
    root: Path,
    *,
    purpose: str,
) -> Path:
    """Resolve a relative artifact path and enforce root containment."""
    raw = str(raw_path)
    if not raw:
        raise ValueError(f"{purpose} must not be empty")
    if Path(raw).is_absolute():
        raise ValueError(f"{purpose} must be repository-relative: {raw!r}")
    if ".." in raw.replace("\\", "/").split("/"):
        raise ValueError(f"{purpose} must not contain '..': {raw!r}")

    resolved_root = root.resolve()
    resolved = (resolved_root / raw).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(
            f"{purpose} escapes validation root: {resolved}"
        ) from exc
    return validate_read_path(resolved, purpose=purpose)


def _read_json(path: Path, *, label: str) -> tuple[Any | None, str | None]:
    """Read a validated JSON file and return a useful parse error."""
    try:
        validated_path = validate_read_path(path, purpose=label)
        with validated_path.open("r", encoding="utf-8") as handle:
            return json.load(handle), None
    except json.JSONDecodeError as exc:
        return None, f"{label}: invalid JSON: {exc.msg}"
    except OSError as exc:
        return None, f"{label}: cannot read file: {exc}"


def _artifact_file(
    probe_dir: Path,
    scenario: str,
    suffix: str,
    *,
    root: Path,
) -> tuple[Path | None, str | None]:
    """Resolve one fixed-name probe file without following an escape."""
    filename = f"{scenario}.{suffix}"
    try:
        path = _resolve_repo_relative(
            probe_dir.relative_to(root) / filename,
            root,
            purpose=f"{scenario} {suffix} artifact",
        )
    except (FileNotFoundError, ValueError) as exc:
        return None, f"{scenario}: {filename}: {exc}"
    if not path.is_file():
        return None, f"{scenario}: {filename}: not a regular file"
    if path.stat().st_size == 0:
        return None, f"{scenario}: {filename}: file is empty"
    return path, None


def _validate_probe(
    scenario: str,
    probe_dir: Path,
    *,
    root: Path,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate one scenario's headers, body, and JSON evidence."""
    errors: list[str] = []
    artifacts: dict[str, Path] = {}
    for suffix in ("headers", "body", "json"):
        path, error = _artifact_file(probe_dir, scenario, suffix, root=root)
        if error:
            errors.append(error)
        elif path is not None:
            artifacts[suffix] = path
    if errors or len(artifacts) != 3:
        return None, errors

    payload, error = _read_json(
        artifacts["json"], label=f"{scenario}: {scenario}.json"
    )
    if error:
        return None, [error]
    if not isinstance(payload, dict):
        return None, [f"{scenario}: {scenario}.json: JSON value is not an object"]

    if payload.get("verdict") != "pass":
        errors.append(
            f"{scenario}: {scenario}.json: verdict must be 'pass', "
            f"got {payload.get('verdict')!r}"
        )
    if payload.get("curl_exit_code") != 0:
        errors.append(
            f"{scenario}: {scenario}.json: curl_exit_code must be 0, "
            f"got {payload.get('curl_exit_code')!r}"
        )
    if payload.get("header_artifact") != f"{scenario}.headers":
        errors.append(
            f"{scenario}: {scenario}.json: header_artifact must be "
            f"'{scenario}.headers'"
        )
    if payload.get("body_artifact") != f"{scenario}.body":
        errors.append(
            f"{scenario}: {scenario}.json: body_artifact must be "
            f"'{scenario}.body'"
        )

    validated_body = validate_read_path(
        artifacts["body"], purpose=f"{scenario} body artifact"
    )
    body = validated_body.read_bytes()
    digest = hashlib.sha256(body).hexdigest()
    if payload.get("body_sha256") != digest:
        errors.append(
            f"{scenario}: {scenario}.body: SHA-256 {digest} does not match "
            f"JSON body_sha256 {payload.get('body_sha256')!r}"
        )
    if payload.get("body_bytes") != len(body):
        errors.append(
            f"{scenario}: {scenario}.body: byte count {len(body)} does not "
            f"match JSON body_bytes {payload.get('body_bytes')!r}"
        )
    return payload, errors


def _baseline_scenarios(
    baseline: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Return finalized scenarios keyed by name and structural errors."""
    module = baseline.get("module_benchmark")
    scenarios = module.get("scenarios") if isinstance(module, dict) else None
    if not isinstance(scenarios, list):
        return {}, ["baseline: module_benchmark.scenarios must be an array"]

    mapping: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for entry in scenarios:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            errors.append("baseline: scenario entry must be an object with a name")
            continue
        name = entry["name"]
        if name in mapping:
            errors.append(f"baseline: {name}: duplicate scenario")
        else:
            mapping[name] = entry
    for name in SCENARIOS:
        if name not in mapping:
            errors.append(f"baseline: {name}: scenario is missing")
    return mapping, errors


def _cross_check_baseline(
    probes: dict[str, dict[str, Any]],
    baseline: dict[str, Any],
) -> list[str]:
    """Compare retained probe correctness fields with finalized evidence."""
    scenarios, errors = _baseline_scenarios(baseline)
    for name in SCENARIOS:
        probe = probes.get(name)
        scenario = scenarios.get(name)
        if probe is None or scenario is None:
            continue
        response = scenario.get("response_correctness")
        if not isinstance(response, dict):
            errors.append(
                f"{name}: baseline response_correctness must be an object"
            )
            continue
        for field in RESPONSE_FIELDS:
            if field not in probe:
                errors.append(f"{name}: probe JSON missing {field}")
            elif field not in response:
                errors.append(f"{name}: baseline response_correctness missing {field}")
            elif response[field] != probe[field]:
                errors.append(
                    f"{name}: response_correctness.{field} differs from probe JSON"
                )
    return errors


def validate_probe_artifacts(
    probe_dir: str | Path,
    *,
    baseline: str | Path | None = None,
    repo_root: str | Path = REPO_ROOT,
) -> list[str]:
    """Return validation errors for a retained probe directory.

    ``probe_dir`` and ``baseline`` are relative to ``repo_root``.  Absolute
    paths, ``..`` components, missing files, and symlink escapes are rejected.
    """
    root = _resolve_root(repo_root)
    try:
        resolved_probe_dir = _resolve_repo_relative(
            probe_dir, root, purpose="probe directory"
        )
    except (FileNotFoundError, ValueError) as exc:
        return [f"probe directory: {exc}"]
    if not resolved_probe_dir.is_dir():
        return [f"probe directory: {resolved_probe_dir} is not a directory"]

    probes: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for scenario in SCENARIOS:
        payload, scenario_errors = _validate_probe(
            scenario, resolved_probe_dir, root=root
        )
        errors.extend(scenario_errors)
        if payload is not None:
            probes[scenario] = payload

    if baseline is not None:
        try:
            baseline_path = _resolve_repo_relative(
                baseline, root, purpose="baseline"
            )
        except (FileNotFoundError, ValueError) as exc:
            errors.append(f"baseline: {exc}")
        else:
            payload, error = _read_json(baseline_path, label="baseline")
            if error:
                errors.append(error)
            elif not isinstance(payload, dict):
                errors.append("baseline: JSON value is not an object")
            else:
                errors.extend(_cross_check_baseline(probes, payload))
    return errors


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate all retained module benchmark probe artifacts."
    )
    parser.add_argument(
        "--probe-dir",
        required=True,
        help="Repository-relative directory containing the probe triplets.",
    )
    parser.add_argument(
        "--baseline",
        help="Repository-relative finalized baseline for response cross-checking.",
    )
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="Validation root; probe and baseline paths remain relative to it.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the validator CLI and return a process exit code."""
    args = _parse_args(argv)
    try:
        errors = validate_probe_artifacts(
            args.probe_dir,
            baseline=args.baseline,
            repo_root=args.repo_root,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"validated {len(SCENARIOS)} module probe scenarios")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
