#!/usr/bin/env python3
"""Verify THIRD-PARTY-NOTICES covers all direct runtime dependencies.

Checks:
1. Every Rust [dependencies] crate has an entry with its exact resolved version.
2. Required transitive runtime crates have entries with exact resolved versions.
3. Every known C runtime dependency (NGINX, zlib, brotli) has a matching entry.
4. Every first-party Rust workspace has a present and current Cargo.lock.

Dev-only dependencies ([dev-dependencies], test frameworks, CI tools) are
intentionally excluded because they are not linked into or distributed with
the final binary.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NOTICES_PATH = ROOT / "THIRD-PARTY-NOTICES"
_CARGO_TOML_NAME = "Cargo.toml"
_CARGO_LOCK_NAME = "Cargo.lock"
CARGO_TOML = ROOT / "components" / "rust-converter" / _CARGO_TOML_NAME
CARGO_LOCK = ROOT / "components" / "rust-converter" / _CARGO_LOCK_NAME

# Additional Cargo.toml files for sub-workspaces that have their own
# Cargo.lock.  These are checked for stale lock files and their direct
# dependencies are not required to appear in THIRD-PARTY-NOTICES (they
# are dev/test/fuzz only), but their Cargo.lock must be in sync with
# their Cargo.toml.
SUB_WORKSPACE_CARGO_TOMLS: list[Path] = [
    ROOT / "components" / "rust-converter" / "fuzz" / _CARGO_TOML_NAME,
    ROOT / "tools" / "corpus" / "test-corpus-conversion" / _CARGO_TOML_NAME,
    ROOT / "tools" / "e2e-harness" / _CARGO_TOML_NAME,
]

# Corresponding Cargo.lock files for sub-workspaces.
SUB_WORKSPACE_CARGO_LOCKS: list[Path] = [
    ROOT / "components" / "rust-converter" / "fuzz" / _CARGO_LOCK_NAME,
    ROOT / "tools" / "corpus" / "test-corpus-conversion" / _CARGO_LOCK_NAME,
    ROOT / "tools" / "e2e-harness" / _CARGO_LOCK_NAME,
]

# Runtime crates that are transitive but intentionally documented because their
# implementation is shipped as part of the converter's parser stack.
NOTICE_REQUIRED_TRANSITIVE_DEPS = ("markup5ever",)

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


def resolved_versions(cargo_lock: Path, dependency_names: list[str]) -> dict[str, str]:
    """Return one unambiguous resolved Cargo.lock version per dependency."""
    data = tomllib.loads(cargo_lock.read_text(encoding="utf-8"))
    packages = data.get("package", [])
    versions: dict[str, str] = {}

    for name in dependency_names:
        matches = {
            str(package.get("version", ""))
            for package in packages
            if package.get("name") == name and package.get("version")
        }
        if len(matches) != 1:
            rendered = ", ".join(sorted(matches)) if matches else "none"
            raise ValueError(
                f"expected one resolved version for {name}, found: {rendered}"
            )
        versions[name] = matches.pop()
    return versions


def notice_has_exact_version(name: str, version: str, notices: str) -> bool:
    """Return whether a numbered NOTICE entry has the exact resolved version."""
    variants = {name, name.replace("-", "_"), name.replace("_", "-")}
    return any(
        re.search(
            rf"^\s*\d+\.\s+{re.escape(variant)}\s+{re.escape(version)}(?=\s|\(|$)",
            notices,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        is not None
        for variant in variants
    )


def collect_notice_version_issues(
    rust_deps: list[str], cargo_lock: Path, notices: str
) -> list[str]:
    """Collect missing or stale exact-version NOTICE entries."""
    required_names = rust_deps + list(NOTICE_REQUIRED_TRANSITIVE_DEPS)
    versions = resolved_versions(cargo_lock, required_names)
    return [
        f"Rust dependency: {name} must list resolved version {version}"
        for name, version in versions.items()
        if not notice_has_exact_version(name, version, notices)
    ]


def collect_workspace_lock_issues() -> list[str]:
    """Collect missing or stale sub-workspace Cargo.lock errors."""
    issues: list[str] = []
    for cargo_toml, cargo_lock in zip(
        SUB_WORKSPACE_CARGO_TOMLS, SUB_WORKSPACE_CARGO_LOCKS, strict=True
    ):
        if not cargo_toml.is_file():
            continue
        relative_manifest = cargo_toml.relative_to(ROOT)
        if not cargo_lock.is_file():
            issues.append(f"Cargo.lock missing for {relative_manifest}")
            continue
        completed = subprocess.run(
            [
                "cargo",
                "metadata",
                "--format-version",
                "1",
                "--locked",
                "--no-deps",
                "--manifest-path",
                str(cargo_toml),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            issues.append(f"Cargo.lock is stale for {relative_manifest}")
    return issues


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

    problems: list[str] = []

    # --- Rust direct dependencies ---
    rust_deps = parse_rust_direct_deps(CARGO_TOML)
    try:
        problems.extend(collect_notice_version_issues(rust_deps, CARGO_LOCK, notices))
    except (OSError, ValueError) as exc:
        problems.append(f"Cargo.lock resolution error: {exc}")

    # --- C runtime dependencies ---
    problems.extend(
        f"C dependency: {display_name}"
        for display_name, patterns in C_RUNTIME_DEPS
        if not check_dep_in_notices(patterns, notices)
    )

    # --- Sub-workspace Cargo.lock freshness check ---
    problems.extend(collect_workspace_lock_issues())

    # --- Report ---
    if problems:
        return report_missing_and_fail(problems)
    dep_count = (
        len(rust_deps) + len(NOTICE_REQUIRED_TRANSITIVE_DEPS) + len(C_RUNTIME_DEPS)
    )
    print(f"THIRD-PARTY-NOTICES coverage check passed ({dep_count} dependencies verified).")
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
