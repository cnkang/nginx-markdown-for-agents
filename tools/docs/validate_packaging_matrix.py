#!/usr/bin/env python3
"""Validate packaging/matrix.yaml consistency with tools/release-matrix.json.

When packaging/matrix.yaml exists, this script validates:
  1. All NGINX versions listed in the packaging matrix are present in the
     authoritative release-matrix.json.
  2. No version in the packaging matrix claims a higher support tier than
     what release-matrix.json defines.

If packaging/matrix.yaml does not exist, the script exits 0 (skip gracefully).

Exit codes:
    0  Validation passed or packaging/matrix.yaml absent (skipped)
    1  Validation failure (inconsistency detected)

Part of spec 40: Release Matrix Source of Truth.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("WARNING: PyYAML not installed; skipping packaging matrix validation")
    sys.exit(0)

ROOT = Path(__file__).resolve().parents[2]
PACKAGING_MATRIX = ROOT / "packaging" / "matrix.yaml"
RELEASE_MATRIX = ROOT / "tools" / "release-matrix.json"

# Tier ordering from lowest to highest privilege.
# "source_only" is a backward-compat alias for "best-effort";
# "full" is a backward-compat alias for "supported".
TIER_ORDER: dict[str, int] = {
    "unsupported": 0,
    "best-effort": 1,
    "source_only": 1,  # alias: same level as best-effort
    "experimental": 2,
    "full": 3,          # alias: same level as supported
    "supported": 3,
}


def load_release_matrix(path: Path) -> dict:
    """Load the authoritative release-matrix.json."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_packaging_matrix(path: Path) -> dict:
    """Load packaging/matrix.yaml."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_release_matrix_versions(data: dict) -> set[str]:
    """Extract all NGINX versions from release-matrix.json."""
    versions: set[str] = set()

    # Check 'entries' array first (canonical format), then 'matrix' (legacy)
    entries = data.get("entries") or data.get("matrix", [])
    for entry in entries:
        if version := entry.get("nginx_version") or entry.get("nginx", ""):
            versions.add(version)

    return versions


def get_release_matrix_max_tier(data: dict, version: str) -> int:
    """Get the maximum tier level for a given version in release-matrix.json."""
    entries = data.get("entries") or data.get("matrix", [])
    max_tier = -1

    tier_mapping = data.get("tier_mapping", {})

    for entry in entries:
        entry_version = entry.get("nginx_version") or entry.get("nginx", "")
        if entry_version != version:
            continue
        raw_tier = entry.get("support_tier", "")
        # Resolve through tier_mapping
        resolved = tier_mapping.get(raw_tier, raw_tier)
        tier_level = TIER_ORDER.get(resolved, TIER_ORDER.get(raw_tier, -1))
        if tier_level > max_tier:
            max_tier = tier_level

    return max_tier


def validate() -> list[str]:
    """Run all validation checks. Returns list of error messages."""
    errors: list[str] = []

    if not RELEASE_MATRIX.exists():
        errors.append(
            f"Authoritative source {RELEASE_MATRIX.relative_to(ROOT)} not found"
        )
        return errors

    release_data = load_release_matrix(RELEASE_MATRIX)
    packaging_data = load_packaging_matrix(PACKAGING_MATRIX)

    if packaging_data is None:
        errors.append("packaging/matrix.yaml is empty or invalid YAML")
        return errors

    # --- Check 1: NGINX versions subset ---
    release_versions = get_release_matrix_versions(release_data)
    packaging_versions = set(packaging_data.get("nginx_versions", []))

    if not packaging_versions:
        # No nginx_versions key or empty list — nothing to validate
        return errors

    if extra_versions := packaging_versions - release_versions:
        errors.extend(
            f"packaging/matrix.yaml lists NGINX version '{v}' which is not present in tools/release-matrix.json (known versions: {sorted(release_versions)})"
            for v in sorted(extra_versions)
        )
    # --- Check 2: No tier escalation ---
    # packaging/matrix.yaml doesn't directly declare tiers per-version,
    # but if a tier_overrides or support_tiers section exists, validate it
    tier_overrides = packaging_data.get("tier_overrides", {})
    for version, claimed_tier in tier_overrides.items():
        if version not in release_versions:
            # Already caught in check 1
            continue
        claimed_level = TIER_ORDER.get(claimed_tier, -1)
        if claimed_level < 0:
            errors.append(
                f"packaging/matrix.yaml tier_overrides: unknown tier "
                f"'{claimed_tier}' for version '{version}'"
            )
            continue
        release_max = get_release_matrix_max_tier(release_data, version)
        if claimed_level > release_max:
            release_tier_name = next(
                (k for k, v in TIER_ORDER.items() if v == release_max),
                "unknown",
            )
            errors.append(
                f"packaging/matrix.yaml claims tier '{claimed_tier}' "
                f"(level {claimed_level}) for version '{version}', but "
                f"release-matrix.json maximum is '{release_tier_name}' "
                f"(level {release_max})"
            )

    return errors


def main() -> int:
    """Entry point."""
    if not PACKAGING_MATRIX.exists():
        print(
            "packaging/matrix.yaml not found — skipping validation (OK)"
        )
        return 0

    if errors := validate():
        print("packaging/matrix.yaml consistency check FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("packaging/matrix.yaml consistency check passed:")
    print(
        '  - All NGINX versions in packaging/matrix.yaml are present in tools/release-matrix.json'
    )
    print("  - No tier escalation detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
