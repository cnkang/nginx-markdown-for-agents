#!/usr/bin/env python3
"""Release gate validator for 0.9.2.

Validates 0.9.2-specific deliverables:
  - Version consistency (all version sources = 0.9.2)
  - Reason code registry completeness (26 codes)
  - Public surface inventory exists and is parseable

Adds 0.9.2-specific checks to the 0.9.1 Make gate chain; prior checks
remain delegated to that chain.

Exit codes:
  0 = all gates pass
  1 = at least one gate failed
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

EXPECTED_VERSION = "0.9.2"
EXPECTED_REASON_CODE_COUNT = 26
CHANGELOG_FILENAME = "CHANGELOG.md"


def find_repo_root() -> Path:
    """Return the repository root resolved from this validator's location."""
    return REPO_ROOT


def check_version_consistency(repo: Path) -> dict:
    """Verify all version sources agree on 0.9.2."""
    sources = {
        "Cargo.toml": repo / "components/rust-converter/Cargo.toml",
        CHANGELOG_FILENAME: repo / CHANGELOG_FILENAME,
    }
    mismatches = []

    cargo_toml = sources["Cargo.toml"]
    if not cargo_toml.exists():
        mismatches.append("Cargo.toml: file not found")
    else:
        content = cargo_toml.read_text()
        m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if m and m.group(1) != EXPECTED_VERSION:
            mismatches.append(f"Cargo.toml: {m.group(1)}")
        elif not m:
            mismatches.append("Cargo.toml: version not found")

    changelog = sources[CHANGELOG_FILENAME]
    if not changelog.exists():
        mismatches.append(f"{CHANGELOG_FILENAME}: file not found")
    else:
        content = changelog.read_text()
        if f"## [{EXPECTED_VERSION}]" not in content and f"## v{EXPECTED_VERSION}" not in content:
            mismatches.append(
                f"{CHANGELOG_FILENAME}: {EXPECTED_VERSION} header not found")

    if mismatches:
        return {"name": "version_consistency", "status": "fail",
                "message": "; ".join(mismatches)}
    return {"name": "version_consistency", "status": "pass",
            "details": {"expected": EXPECTED_VERSION}}


def check_reason_code_registry(repo: Path) -> dict:
    """Verify Rust reason code count is 26."""
    rc_file = repo / "components/rust-converter/src/decision/reason_code.rs"
    if not rc_file.exists():
        return {"name": "reason_code_registry", "status": "fail",
                "message": "reason_code.rs not found"}
    content = rc_file.read_text()
    m = re.search(r"pub const REASON_CODE_COUNT:\s*usize\s*=\s*(\d+)", content)
    if not m:
        return {"name": "reason_code_registry", "status": "fail",
                "message": "REASON_CODE_COUNT not found"}
    count = int(m.group(1))
    if count == EXPECTED_REASON_CODE_COUNT:
        return {"name": "reason_code_registry", "status": "pass",
                "details": {"count": count}}
    return {"name": "reason_code_registry", "status": "fail",
            "message": f"Expected {EXPECTED_REASON_CODE_COUNT}, got {count}"}


def check_public_surface_inventory(repo: Path) -> dict:
    """Verify public surface inventory exists and is parseable JSON."""
    inventory = repo / "docs/harness/public-surface-inventory.json"
    if not inventory.exists():
        return {"name": "public_surface_inventory", "status": "fail",
                "message": "public-surface-inventory.json not found"}
    try:
        data = json.loads(inventory.read_text())
    except json.JSONDecodeError as exc:
        return {"name": "public_surface_inventory", "status": "fail",
                "message": f"invalid JSON: {exc}"}
    if not isinstance(data, dict):
        return {"name": "public_surface_inventory", "status": "fail",
                "message": "expected top-level JSON object"}
    return {"name": "public_surface_inventory", "status": "pass",
            "details": {"keys": list(data.keys())[:10]}}


def main():
    """Run the 0.9.2-specific checks and exit non-zero on any failure."""
    repo = find_repo_root()
    checks = [
        check_version_consistency(repo),
        check_reason_code_registry(repo),
        check_public_surface_inventory(repo),
    ]

    failed = [c for c in checks if c["status"] == "fail"]
    for c in checks:
        status_tag = "PASS" if c["status"] == "pass" else "FAIL"
        msg = f"  [{status_tag}] {c['name']}"
        if "message" in c:
            msg += f": {c['message']}"
        print(msg)

    if failed:
        print(f"\n0.9.2 gates: {len(failed)} FAILED")
        sys.exit(1)
    print("\n0.9.2 gates: ALL PASS")


if __name__ == "__main__":
    main()
