#!/usr/bin/env python3
"""Generate the release-contract matrix from the policy matrix.

``tools/release-matrix.json`` is the only manually maintained policy source.
This tool projects its platform and artifact rows into the ABI-bound contract
consumed by the release gate at ``docs/releases/release-matrix.json``.

The projection records the canonical-content digest of its source.  ``--check``
is fail-closed when the checked-in projection is missing, stale, or differs
from the deterministic projection of the policy source.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from lib.path_validation import (  # noqa: E402
    validate_read_path,
    validate_write_path_within_root,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_MATRIX_PATH = REPO_ROOT / "tools" / "release-matrix.json"
SOURCE_SCHEMA_PATH = REPO_ROOT / "tools" / "release-matrix.schema.json"
PROJECTION_PATH = REPO_ROOT / "docs" / "releases" / "release-matrix.json"
SOURCE_MATRIX_RELATIVE_PATH = "tools/release-matrix.json"

SOURCE_ENTRY_REQUIRED = (
    "nginx_version",
    "os",
    "libc",
    "arch",
    "artifact_type",
    "feature_manifest_digest",
    "abi_version",
)

ARCH_NAMES = {
    "amd64": "x86_64",
    "arm64": "aarch64",
}

TARGET_SUFFIXES = {
    "glibc": "unknown-linux-gnu",
    "musl": "unknown-linux-musl",
    "darwin": "apple-darwin",
}

ARTIFACT_ORDER = {
    "deb-package": 0,
    "docker-image": 1,
    "dynamic-module": 2,
    "rpm-package": 3,
    "homebrew-formula": 4,
    "source": 5,
}


def _load_json(path: Path, label: str) -> dict[str, Any]:
    """Load one trusted repository JSON object."""
    validated = validate_read_path(path, purpose=label)
    try:
        value = json.loads(validated.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def canonical_digest(value: dict[str, Any]) -> str:
    """Return the digest convention used by generated release evidence."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _validate_source_schema(source: dict[str, Any]) -> None:
    """Validate the policy source before projecting any rows."""
    try:
        import jsonschema
    except ImportError as exc:
        raise ValueError(
            "jsonschema is required to validate the policy matrix"
        ) from exc

    schema = _load_json(SOURCE_SCHEMA_PATH, "policy matrix schema")
    try:
        jsonschema.validate(instance=source, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ValueError(f"policy matrix schema validation failed: {exc.message}") from exc


def _project_target(entry: dict[str, Any]) -> str:
    """Convert policy ``arch``/``libc`` fields to a Rust target identity."""
    arch = entry["arch"]
    libc = entry["libc"]
    artifact_type = entry["artifact_type"]

    if arch == "any":
        if artifact_type != "source" or libc != "n/a":
            raise ValueError(
                "only the platform-independent source row may use arch=any"
            )
        return "any"

    arch_name = ARCH_NAMES.get(arch)
    suffix = TARGET_SUFFIXES.get(libc)
    if arch_name is None or suffix is None:
        raise ValueError(
            f"cannot project matrix row to a target: arch={arch!r}, libc={libc!r}"
        )
    return f"{arch_name}-{suffix}"


def _project_os(entry: dict[str, Any]) -> str:
    """Project the source-only policy row to the contract's Linux scope."""
    if entry["artifact_type"] == "source":
        if (entry["os"], entry["libc"], entry["arch"]) != ("any", "n/a", "any"):
            raise ValueError("source row must use os=any, libc=n/a, arch=any")
        return "linux"
    return entry["os"]


def _project_libc(entry: dict[str, Any]) -> str:
    """Use the contract's platform-independent value for source artifacts."""
    if entry["artifact_type"] == "source":
        return "any"
    return entry["libc"]


def project_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Project one policy row into the release-contract entry shape."""
    missing = [key for key in SOURCE_ENTRY_REQUIRED if key not in entry]
    if missing:
        raise ValueError(f"policy matrix row is missing required keys: {missing}")

    projected = {
        "nginx_version": entry["nginx_version"],
        "os": _project_os(entry),
        "libc": _project_libc(entry),
        "target": _project_target(entry),
        "artifact_type": entry["artifact_type"],
        "feature_manifest_digest": entry["feature_manifest_digest"],
        "abi_version": entry["abi_version"],
    }
    for key in ("image_ref", "image_digest"):
        if key in entry:
            projected[key] = entry[key]
    return projected


def build_projection(source: dict[str, Any]) -> dict[str, Any]:
    """
    Build a deterministic release-contract projection from a policy matrix.
    
    Parameters:
    	source (dict[str, Any]): Policy matrix containing a non-empty ``entries`` array.
    
    Returns:
    	dict[str, Any]: Projection with schema metadata, source digest, and sorted release-contract entries.
    
    Raises:
    	ValueError: If the source has no non-empty ``entries`` array or projects to duplicate release-contract rows.
    """
    entries = source.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("policy matrix must contain a non-empty entries array")

    projected_entries = [project_entry(entry) for entry in entries]
    projected_entries.sort(
        key=lambda entry: (
            tuple(int(part) for part in entry["nginx_version"].split(".")),
            entry["target"],
            ARTIFACT_ORDER.get(entry["artifact_type"], 99),
            entry["os"],
            entry["libc"],
        )
    )
    identities = {
        (
            entry["nginx_version"],
            entry["os"],
            entry["libc"],
            entry["target"],
            entry["artifact_type"],
        )
        for entry in projected_entries
    }
    if len(identities) != len(projected_entries):
        raise ValueError("policy matrix projects to duplicate release-contract rows")

    return {
        "schema_version": 1,
        "generated_from": {
            "path": SOURCE_MATRIX_RELATIVE_PATH,
            "sha256": canonical_digest(source),
        },
        "entries": projected_entries,
    }


def load_source() -> dict[str, Any]:
    """Load and validate the canonical policy matrix."""
    source = _load_json(SOURCE_MATRIX_PATH, "policy release matrix")
    _validate_source_schema(source)
    return source


def _write_projection(projection: dict[str, Any]) -> None:
    """Atomically write the generated projection inside the repository."""
    output = validate_write_path_within_root(
        PROJECTION_PATH, REPO_ROOT, purpose="release matrix projection"
    )
    output_directory = validate_write_path_within_root(
        output.parent, REPO_ROOT, purpose="release matrix projection directory"
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_directory,
            prefix=".release-matrix-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(json.dumps(projection, indent=2) + "\n")
        os.replace(temporary_path, output)
    except (OSError, ValueError):
        if temporary_path is not None:
            with contextlib.suppress(OSError, ValueError):
                temporary_path.unlink(missing_ok=True)
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or check the release-contract matrix projection."
    )
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument(
        "--check",
        action="store_true",
        help="fail if the checked-in projection is stale or missing",
    )
    actions.add_argument(
        "--write",
        action="store_true",
        help="write the deterministic projection to docs/releases/",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the projection generator or freshness check."""
    args = _build_parser().parse_args(argv)
    try:
        expected = build_projection(load_source())
        if args.write:
            _write_projection(expected)
            print(
                "Wrote release-contract projection: "
                f"{PROJECTION_PATH} ({expected['generated_from']['sha256']})"
            )
            return 0

        actual = _load_json(PROJECTION_PATH, "release-contract projection")
        if actual != expected:
            raise ValueError(
                "release-contract projection is stale; run "
                "generate_release_contract_matrix.py --write"
            )
        print(
            "PASS: release-contract projection is current "
            f"({expected['generated_from']['sha256']})"
        )
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
