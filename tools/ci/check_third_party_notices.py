#!/usr/bin/env python3
"""Verify THIRD-PARTY-NOTICES covers all direct runtime dependencies.

Checks:
1. Every Rust [dependencies] crate in Cargo.toml has a matching entry.
2. Every known C runtime dependency (NGINX, zlib, brotli) has a matching entry.
3. The THIRD-PARTY-NOTICES file exists and is non-empty.

Dev-only dependencies ([dev-dependencies], test frameworks, CI tools) are
intentionally excluded because they are not linked into or distributed with
the final binary.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NOTICES_PATH = ROOT / "THIRD-PARTY-NOTICES"
_CARGO_TOML_NAME = "Cargo.toml"
CARGO_TOML = ROOT / "components" / "rust-converter" / _CARGO_TOML_NAME

# Additional Cargo.toml files for sub-workspaces that have their own
# Cargo.lock.  These are checked for stale lock files and their direct
# dependencies are not required to appear in THIRD-PARTY-NOTICES (they
# are dev/test/fuzz only), but their Cargo.lock must be in sync with
# their Cargo.toml.
SUB_WORKSPACE_CARGO_TOMLS: list[Path] = [
    ROOT / "components" / "rust-converter" / "fuzz" / _CARGO_TOML_NAME,
    ROOT / "tools" / "corpus" / "test-corpus-conversion" / _CARGO_TOML_NAME,
]

# Corresponding Cargo.lock files for sub-workspaces.
SUB_WORKSPACE_CARGO_LOCKS: list[Path] = [
    ROOT / "components" / "rust-converter" / "fuzz" / "Cargo.lock",
    ROOT / "tools" / "corpus" / "test-corpus-conversion" / "Cargo.lock",
]

# Known C-side runtime dependencies that must appear in the notices file.
# Each tuple is (display_name, list_of_search_patterns).
C_RUNTIME_DEPS: list[tuple[str, list[str]]] = [
    ("NGINX", ["nginx"]),
    ("zlib", ["zlib"]),
    ("Brotli", ["brotli"]),
]

# Pre-compiled regexes for Cargo.toml parsing (avoids recompilation per call).
_SECTION_RE = re.compile(r"^\[.*\]")
_KV_RE = re.compile(r"^([A-Za-z0-9_-]+)\s*=")


def parse_rust_direct_deps(cargo_toml: Path) -> list[str]:
    """Extract crate names from the [dependencies] section of Cargo.toml.

    Stops at the next section header or end-of-file.  Skips comments and
    blank lines.  Handles both ``crate = "version"`` and ``crate = { ... }``
    forms.
    """
    text = cargo_toml.read_text(encoding="utf-8")
    in_deps = False
    deps: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "[dependencies]":
            in_deps = True
            continue
        if _SECTION_RE.match(stripped):
            if in_deps:
                break
            continue
        if not in_deps:
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if m := _KV_RE.match(stripped):
            deps.append(m[1])
    return deps


def load_notices(path: Path) -> str:
    """Read the THIRD-PARTY-NOTICES file, returning its full text."""
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def check_dep_in_notices(patterns: list[str], notices: str) -> bool:
    """Return True if at least one pattern appears in the notices text (case-insensitive)."""
    lower = notices.lower()
    return any(p.lower() in lower for p in patterns)


def main() -> int:
    """Run THIRD-PARTY-NOTICES coverage check and report results."""
    # --- Existence check ---
    if not NOTICES_PATH.is_file():
        print(f"THIRD-PARTY-NOTICES file not found at: {NOTICES_PATH}")
        return 1

    notices = load_notices(NOTICES_PATH)
    if not notices.strip():
        print("THIRD-PARTY-NOTICES file is empty.")
        return 1

    missing: list[str] = []

    # --- Rust direct dependencies ---
    rust_deps = parse_rust_direct_deps(CARGO_TOML)
    for dep in rust_deps:
        # Search for the crate name (underscores and hyphens are interchangeable
        # in Cargo but the notices file may use either form).
        patterns = [dep, dep.replace("-", "_"), dep.replace("_", "-")]
        if not check_dep_in_notices(patterns, notices):
            missing.append(f"Rust dependency: {dep}")

    # --- C runtime dependencies ---
    missing.extend(
        f"C dependency: {display_name}"
        for display_name, patterns in C_RUNTIME_DEPS
        if not check_dep_in_notices(patterns, notices)
    )

    # --- Sub-workspace Cargo.lock existence check ---
    # These sub-workspaces have their own Cargo.lock that can go stale
    # when Cargo.toml changes are made without running cargo update.
    # We only check existence here — a full lock-file freshness check
    # requires running cargo and is left to CI.
    stale_locks: list[str] = []
    for cargo_toml, cargo_lock in zip(
        SUB_WORKSPACE_CARGO_TOMLS, SUB_WORKSPACE_CARGO_LOCKS,
        strict=True,
    ):
        if cargo_toml.is_file() and not cargo_lock.is_file():
            stale_locks.append(
                f"Cargo.lock missing for {cargo_toml.relative_to(ROOT)}"
            )

    if stale_locks:
        print("Sub-workspace Cargo.lock issues detected:")
        for item in stale_locks:
            print(f"  - {item}")
        print("Run `cargo generate-lockfile` in each sub-workspace directory.")
        # Report as warning, not failure — lock file may not exist yet
        # in early development.  CI will catch staleness via --locked.

    # --- Report ---
    if missing:
        return report_missing_and_fail(missing)
    dep_count = len(rust_deps) + len(C_RUNTIME_DEPS)
    print(f"THIRD-PARTY-NOTICES coverage check passed ({dep_count} dependencies verified).")
    if stale_locks:
        print(f"WARNING: {len(stale_locks)} sub-workspace Cargo.lock issue(s) detected.")
    return 0


def report_missing_and_fail(missing: list[str]) -> int:
    """Print missing dependency entries and return a non-zero exit code."""
    print("THIRD-PARTY-NOTICES coverage check failed.")
    print("The following runtime dependencies are not mentioned:")
    for item in missing:
        print(f"  - {item}")
    print()
    print("Please update the THIRD-PARTY-NOTICES file in the repository root")
    print("to include license information for each missing dependency.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
