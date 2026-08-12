#!/usr/bin/env python3
"""Generate release schema-drift artifacts from source of truth.

Generates the three release artifacts consumed by ``validate_schema_drift.py``
from checked-in canonical contracts and implementation cross-checks:

- ``metrics-registry.json`` — projected from
  ``schemas/metrics-v1.registry.json``.  The renderer-header comparison is
  performed by ``validate_schema_drift.py`` through
  ``validate_metrics_registry.py`` after generation.
- ``diagnostics-field-contract.json`` — derived from
  ``schemas/diagnostics.schema.json`` (``effective_config`` definition).
- ``dynconf-precedence-report.json`` — projected from
  ``schemas/dynconf-precedence-v1.json`` and cross-checked against
  ``schemas/dynconf.schema.json``, the Rust allowlist, and the dynconf
  precedence header (``components/nginx-module/src/ngx_http_markdown_dynconf_precedence.h``).

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

DEFAULT_VERSION = "0.9.2"

RENDERER_PATH = (
    SOURCE_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_metrics_v1_renderer.h"
)
DIAGNOSTICS_SCHEMA_PATH = SOURCE_ROOT / "schemas" / "diagnostics.schema.json"
DYNCONF_SCHEMA_PATH = SOURCE_ROOT / "schemas" / "dynconf.schema.json"
DYNCONF_RUST_SCHEMA_PATH = (
    SOURCE_ROOT
    / "components"
    / "rust-converter"
    / "src"
    / "dynconf"
    / "schema.rs"
)
PRECEDENCE_HEADER_PATH = (
    SOURCE_ROOT
    / "components"
    / "nginx-module"
    / "src"
    / "ngx_http_markdown_dynconf_precedence.h"
)
METRICS_CONTRACT_PATH = SOURCE_ROOT / "schemas" / "metrics-v1.registry.json"
DYNCONF_PRECEDENCE_CONTRACT_PATH = (
    SOURCE_ROOT / "schemas" / "dynconf-precedence-v1.json"
)

KNOWN_KEYS_PATTERN = re.compile(
    r"(?ms)^\s*(?:pub\s+)?const\s+KNOWN_KEYS\s*:\s*&\[&str\]\s*="
    r"\s*&\[\s*(.*?)\s*\];"
)
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


def _read_text(path: Path) -> str:
    """Read a repository text file with canonical path validation."""
    validated = validate_read_path(path, purpose="schema artifact generation")
    return validated.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict:
    """Read and parse a repository JSON file."""
    return json.loads(_read_text(path))


def _extract_rust_dynconf_keys(content: str) -> set[str]:
    """Extract the Rust parser's complete dynconf key allowlist."""
    match = KNOWN_KEYS_PATTERN.search(content)
    if match is None:
        raise ValueError("Rust dynconf schema has no KNOWN_KEYS allowlist")

    keys = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', match.group(1))
    if not keys:
        raise ValueError("Rust dynconf KNOWN_KEYS allowlist is empty")
    if len(keys) != len(set(keys)):
        raise ValueError("Rust dynconf KNOWN_KEYS allowlist has duplicate keys")
    return set(keys)


def _extract_precedence_matches(content: str) -> list[tuple[str, str]]:
    """Extract numbered precedence comments with deterministic parsing."""
    matches = []
    for line in content.splitlines():
        marker = line.lstrip(" \t")
        if not marker.startswith("*"):
            continue
        remainder = marker[1:].lstrip(" \t")
        tier_text, separator, description = remainder.partition(".")
        if (
            not separator
            or not tier_text
            or tier_text[0] not in "123456789"
            or not all(char in "0123456789" for char in tier_text)
        ):
            continue
        description = description.lstrip(" \t").rstrip()
        if description:
            matches.append((tier_text, description))
    return matches


def _extract_precedence_hierarchy(
    content: str, expected_hierarchy: list[dict]
) -> list[dict]:
    """Extract and validate the precedence hierarchy against its contract."""
    matches = _extract_precedence_matches(content)
    expected_tiers = [entry["tier"] for entry in expected_hierarchy]
    actual_tiers = [int(tier) for tier, _ in matches]
    if actual_tiers != expected_tiers:
        raise ValueError(
            "dynconf precedence header has unexpected tier numbering: "
            f"{actual_tiers!r}"
        )

    hierarchy = []
    for expected, (tier_text, description) in zip(
        expected_hierarchy, matches
    ):
        if description != expected["description"]:
            raise ValueError(
                f"dynconf precedence header tier {tier_text} does not match "
                f"expected source {expected['source']!r}"
            )
        if int(tier_text) != expected["tier"]:
            raise ValueError(
                f"dynconf precedence header tier {tier_text} number "
                "drifted from the canonical contract"
            )

        parsed = dict(expected)
        parsed["description"] = description
        hierarchy.append(parsed)

    return hierarchy


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
        field: dict = {"type": prop.get("type")}
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


def generate_dynconf_precedence_report() -> dict:
    """Project the canonical precedence contract after implementation checks."""
    contract = _read_json(DYNCONF_PRECEDENCE_CONTRACT_PATH)
    schema = _read_json(DYNCONF_SCHEMA_PATH)
    schema_properties = set(schema.get("properties", {}))
    schema_keys = {
        key for key in schema_properties if key != "schema_version"
    }
    if not schema_keys:
        raise ValueError("dynconf.schema.json has no properties")

    contract_fields = set(
        contract.get("field_specific_provenance_rules", {})
        .get("fields", {})
    )
    if schema_keys != contract_fields:
        schema_only = sorted(schema_keys - contract_fields)
        contract_only = sorted(contract_fields - schema_keys)
        raise ValueError(
            "dynconf schema keys drifted from canonical precedence contract: "
            f"schema_only={schema_only!r}, contract_only={contract_only!r}"
        )

    rust_keys = _extract_rust_dynconf_keys(_read_text(DYNCONF_RUST_SCHEMA_PATH))
    if schema_properties != rust_keys:
        schema_only = sorted(schema_properties - rust_keys)
        rust_only = sorted(rust_keys - schema_properties)
        raise ValueError(
            "dynconf schema keys drifted from Rust KNOWN_KEYS allowlist: "
            f"schema_only={schema_only!r}, rust_only={rust_only!r}"
        )

    _extract_precedence_hierarchy(
        _read_text(PRECEDENCE_HEADER_PATH),
        contract["five_tier_precedence_hierarchy"],
    )

    result = dict(contract)
    result["generator"] = Path(__file__).name
    result["contract_source"] = str(
        DYNCONF_PRECEDENCE_CONTRACT_PATH.relative_to(SOURCE_ROOT)
    )
    result["implementation_sources"] = [
        str(DYNCONF_SCHEMA_PATH.relative_to(SOURCE_ROOT)),
        str(DYNCONF_RUST_SCHEMA_PATH.relative_to(SOURCE_ROOT)),
        str(PRECEDENCE_HEADER_PATH.relative_to(SOURCE_ROOT)),
    ]
    return result


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
    args = parser.parse_args(argv)

    if VERSION_PATTERN.fullmatch(args.version) is None:
        parser.error("--version must use MAJOR.MINOR.PATCH")

    artifact_root = REPO_ROOT / "artifacts" / "release"
    artifact_dir = validate_write_path_within_root(
        artifact_root / args.version,
        artifact_root,
        purpose="schema artifact release version",
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)

    artifacts = {
        "metrics-registry.json": generate_metrics_registry(),
        "diagnostics-field-contract.json": generate_diagnostics_field_contract(),
        "dynconf-precedence-report.json": generate_dynconf_precedence_report(),
    }

    for name, data in artifacts.items():
        path = artifact_dir / name
        path.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
