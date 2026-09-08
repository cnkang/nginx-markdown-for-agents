#!/usr/bin/env python3
"""Generate release schema-drift artifacts from source of truth.

Generates the release artifacts consumed by ``validate_schema_drift.py``
from checked-in canonical contracts and implementation cross-checks:

- ``metrics-registry.json`` — projected from
  ``schemas/metrics-v1.registry.json``.  The renderer-header comparison is
  performed by ``validate_schema_drift.py`` through
  ``validate_metrics_registry.py`` after generation.
- ``diagnostics-field-contract.json`` — derived from
  ``schemas/diagnostics.schema.json`` (``effective_config`` definition).

All artifacts are written under ``artifacts/release/<version>/`` (git-ignored
generated evidence).  The generator is idempotent; ``validate_schema_drift.py``
then verifies the artifacts against the same sources and the renderer.

Usage:
  python3 tools/release/gates/generate_schema_artifacts.py [--version 0.9.2]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = SOURCE_ROOT
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
from lib.path_validation import (  # noqa: E402
    validate_read_path,
    validate_write_path_within_root,
)

CARGO_TOML_PATH = SOURCE_ROOT / "components" / "rust-converter" / "Cargo.toml"


def _default_version() -> str:
    """Derive the default release version from the rust-converter package."""
    try:
        with CARGO_TOML_PATH.open("rb") as fh:
            cargo = tomllib.load(fh)
        version = cargo.get("package", {}).get("version", "")
        if isinstance(version, str) and version:
            return version
    except (OSError, tomllib.TOMLDecodeError):
        # Keep the release fallback when package metadata is unavailable or
        # malformed.
        return "0.9.2"
    return "0.9.2"


DEFAULT_VERSION = _default_version()

RENDERER_PATH = (
    SOURCE_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_metrics_v1_renderer.h"
)
DIAGNOSTICS_SCHEMA_PATH = SOURCE_ROOT / "schemas" / "diagnostics.schema.json"
METRICS_CONTRACT_PATH = SOURCE_ROOT / "schemas" / "metrics-v1.registry.json"

VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def _read_text(path: Path) -> str:
    """Read a repository text file with canonical path validation."""
    validated = validate_read_path(path, purpose="schema artifact generation")
    return validated.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict:
    """Read and parse a repository JSON file."""
    return json.loads(_read_text(path))


def generate_metrics_registry() -> dict:
    """Project the canonical metrics contract into a release artifact."""
    contract = _read_json(METRICS_CONTRACT_PATH)
    result = dict(contract)
    result["generator"] = Path(__file__).name
    result["contract_source"] = str(
        METRICS_CONTRACT_PATH.relative_to(SOURCE_ROOT)
    )
    result["implementation_sources"] = [
        str(RENDERER_PATH.relative_to(SOURCE_ROOT))
    ]
    return result


def generate_diagnostics_field_contract() -> dict:
    """Generate diagnostics-field-contract.json from diagnostics.schema.json."""
    schema = _read_json(DIAGNOSTICS_SCHEMA_PATH)
    effective_def = schema.get("$defs", {}).get("effective_config", {})
    properties = effective_def.get("properties", {})
    if not properties:
        raise ValueError(
            "diagnostics.schema.json has no $defs.effective_config.properties"
        )

    effective_fields: dict[str, dict] = {}
    for name, prop in sorted(properties.items()):
        # Fail fast when a property lacks a type instead of emitting a
        # field with a null type into the generated artifact.
        prop_type = prop.get("type")
        if prop_type is None:
            raise ValueError(
                f"diagnostics.schema.json $defs.effective_config property "
                f"{name!r} has no type"
            )
        field: dict = {"type": prop_type}
        if prop.get("enum") is not None:
            field["enum"] = prop["enum"]
        if prop.get("minimum") is not None or prop.get("maximum") is not None:
            bounds: dict = {}
            if prop.get("minimum") is not None:
                bounds["minimum"] = prop["minimum"]
            if prop.get("maximum") is not None:
                bounds["maximum"] = prop["maximum"]
            field["bounds"] = bounds
        effective_fields[name] = field

    return {
        "schema_version": 1,
        "generator": Path(__file__).name,
        "source": str(DIAGNOSTICS_SCHEMA_PATH.relative_to(SOURCE_ROOT)),
        "effective_fields": effective_fields,
        "constraints": {"field_count": len(effective_fields)},
    }


def main(argv: list[str] | None = None) -> int:
    """Generate all schema-drift artifacts and report results."""
    parser = argparse.ArgumentParser(
        description="Generate release schema-drift artifacts from sources."
    )
    parser.add_argument(
        "--version",
        default=DEFAULT_VERSION,
        help="Release version directory (default: %(default)s)",
    )
    parser.add_argument("--check", action="store_true",
                        help="Compare artifacts without modifying files")
    args = parser.parse_args(argv)

    if VERSION_PATTERN.fullmatch(args.version) is None:
        parser.error("--version must use MAJOR.MINOR.PATCH")

    artifact_root = REPO_ROOT / "artifacts" / "release"
    artifact_dir = validate_write_path_within_root(
        artifact_root / args.version,
        artifact_root,
        purpose="schema artifact release version",
    )
    if not args.check:
        artifact_dir.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "metrics-registry.json": generate_metrics_registry(),
        "diagnostics-field-contract.json": generate_diagnostics_field_contract(),
    }

    stale = False
    for name, data in artifacts.items():
        path = artifact_dir / name
        expected = json.dumps(data, indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.is_file() or _read_text(path) != expected:
                print(f"Stale or missing: {path.relative_to(REPO_ROOT)}",
                      file=sys.stderr)
                stale = True
            continue
        path.write_text(expected, encoding="utf-8")
        print(f"Wrote {path.relative_to(REPO_ROOT)}")

    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
