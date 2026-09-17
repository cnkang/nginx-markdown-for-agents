#!/usr/bin/env python3
"""Verify THIRD-PARTY-NOTICES covers all direct runtime and dev dependencies.

Checks:
1. Every Rust [dependencies] crate has an entry with its exact resolved version.
2. Every direct [dev-dependencies] crate of the converter crate has an entry
   with its exact resolved version, because the notices preamble lists
   development-only crates.
3. Required transitive runtime crates have entries with exact resolved versions.
4. Every known C runtime dependency (NGINX, zlib, brotli) has a matching entry.
5. Every first-party Rust workspace has a present and current Cargo.lock.

Dev-only dependencies are not linked into or distributed with the final
binary.  They still need an entry: the notices file lists them so the
development toolchain's licenses are auditable, and a dependency that
arrives without an entry would otherwise go unnoticed.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.lib.executable_validation import (  # noqa: E402
    resolve_approved_executable,
)

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

# Cross-workspace version-consistency allowlist.
#
# The baseline lock is components/rust-converter/Cargo.lock (the production
# archive).  Every other lock must resolve each package it shares with the
# baseline to a version the baseline also resolves; a sub-workspace that pins
# an OLDER shared version silently exercises different code than the shipped
# artifact (the 0.9.2 rc9 drift: encoding_rs 0.8.35 vs 0.8.41, flate2 1.1.9 vs
# 1.1.10, brotli 8.0.4 vs 9.0.0).
#
# Entries below are divergences that exist today and are documented, not
# blessed: the entry names the exact stale version, so a *new* divergence (or
# a further drift of the same package) fails the check instead of inheriting
# the exemption.  Remove an entry by rebuilding that lock against the baseline
# (`cargo update --manifest-path <lock's manifest>` or a targeted
# `cargo update -p <crate> --precise <baseline version>`).
CROSS_LOCK_ALLOWED_DIVERGENCES: dict[str, dict[str, set[str]]] = {
    "tools/corpus/test-corpus-conversion/Cargo.lock": {
        "encoding_rs": {"0.8.35"},
        "flate2": {"1.1.9"},
        "libc": {"0.2.186"},
        "miniz_oxide": {"0.8.9"},
        "regex": {"1.13.0"},
        "regex-automata": {"0.4.15"},
    },
    "tools/e2e-harness/Cargo.lock": {
        "alloc-no-stdlib": {"2.0.4"},
        "alloc-stdlib": {"0.2.4"},
        "brotli": {"8.0.4"},
        "brotli-decompressor": {"5.0.3"},
        "flate2": {"1.1.9"},
        "libc": {"0.2.186"},
        "miniz_oxide": {"0.8.9"},
        "regex": {"1.13.0"},
        "regex-automata": {"0.4.15"},
        "serde_json": {"1.0.150"},
        "simd-adler32": {"0.3.10"},
    },
}

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
    """Extract crate names from one manifest section of Cargo.toml.

    Stops at the next section header or end-of-file.  Skips comments and
    blank lines.  Handles both ``crate = "version"`` and ``crate = { ... }``
    forms.
    """
    return parse_rust_section_deps(cargo_toml, "[dependencies]")


def parse_rust_section_deps(cargo_toml: Path, section: str) -> list[str]:
    """Extract crate names from a named section of Cargo.toml.

    The section name is matched exactly, so ``[dev-dependencies]`` never reads
    the ``[dependencies]`` block and a target-specific table such as
    ``[target.'cfg(unix)'.dependencies]`` stays out of scope.
    """
    text = cargo_toml.read_text(encoding="utf-8")
    in_deps = False
    deps: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped == section:
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


def parse_rust_dev_deps(cargo_toml: Path) -> list[str]:
    """Extract crate names from the [dev-dependencies] section of Cargo.toml."""
    return parse_rust_section_deps(cargo_toml, "[dev-dependencies]")


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
    required_names: list[str],
    cargo_lock: Path,
    notices: str,
    label: str = "Rust dependency",
) -> list[str]:
    """Collect missing or stale exact-version NOTICE entries.

    The caller passes the full name list, so a dev-dependency call cannot
    inherit the transitive runtime scope.  The label distinguishes a runtime
    dependency from a dev dependency, so a finding names the manifest section
    that must gain the entry.
    """
    versions = resolved_versions(cargo_lock, required_names)
    return [
        f"{label}: {name} must list resolved version {version}"
        for name, version in versions.items()
        if not notice_has_exact_version(name, version, notices)
    ]


def collect_workspace_lock_issues() -> list[str]:
    """Collect missing or stale sub-workspace Cargo.lock errors.

    This check deliberately covers only SUB-workspace manifests: the main
    workspace lock is validated by the resolved-versions text parser
    (resolved_versions), which must also accept fixture/placeholder
    manifests in tests.  A cargo metadata run against the main manifest is
    not part of this gate.
    """
    issues: list[str] = []
    if not any(manifest.is_file() for manifest in SUB_WORKSPACE_CARGO_TOMLS):
        # Nothing to verify: do not require cargo when there are no
        # sub-workspace manifests on disk.
        return issues
    cargo = resolve_approved_executable("cargo")
    if cargo is None:
        return ["cargo is unavailable; cannot verify workspace Cargo.lock files"]
    for cargo_toml, cargo_lock in zip(
        SUB_WORKSPACE_CARGO_TOMLS, SUB_WORKSPACE_CARGO_LOCKS, strict=True
    ):
        if not cargo_toml.is_file():
            continue
        relative_manifest = cargo_toml.relative_to(ROOT)
        if not cargo_lock.is_file():
            issues.append(f"Cargo.lock missing for {relative_manifest}")
            continue
        try:
            completed = subprocess.run(
                [
                    cargo,
                    "metadata",
                    "--format-version",
                    "1",
                    "--locked",
                    "--manifest-path",
                    str(cargo_toml),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            issues.append(
                f"cargo is unavailable; cannot verify Cargo.lock for "
                f"{relative_manifest}"
            )
            continue
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            issues.append(
                f"Cargo.lock is stale for {relative_manifest}"
                + (f": {detail[-200:]}" if detail else "")
            )
    return issues


def lock_package_versions(cargo_lock: Path) -> dict[str, set[str]]:
    """Return every `name -> {versions}` pair resolved in one Cargo.lock."""
    data = tomllib.loads(cargo_lock.read_text(encoding="utf-8"))
    versions: dict[str, set[str]] = {}
    for package in data.get("package", []):
        name = package.get("name")
        version = package.get("version")
        if name and version:
            versions.setdefault(str(name), set()).add(str(version))
    return versions


def display_path(path: Path) -> str:
    """Render a path relative to the repository root when it lives inside it."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        # A fixture or out-of-tree lock path is reported as given.
        return str(path)


def collect_cross_lock_version_issues(
    baseline_lock: Path,
    sub_locks: list[Path],
    allowed: dict[str, dict[str, set[str]]] | None = None,
) -> list[str]:
    """Fail when a sub-workspace lock resolves a shared package differently.

    A sub-workspace lock (fuzz, e2e harness, corpus tooling) that pins its own
    copy of a shared crate silently exercises different code than the shipped
    production archive: the rc9 drift had fuzz decompression running
    encoding_rs 0.8.35 / flate2 1.1.9 while the release artifact linked
    0.8.41 / 1.1.10, and the fuzz helper compressing with brotli 8.0.4 while
    the converter decoded with 9.0.0.

    Only packages present in BOTH locks are compared: a sub-workspace's own
    dependencies (libfuzzer-sys, clap, tokio, ...) are legitimately unique to
    it.  Any version the sub-lock resolves that the baseline never resolves is
    a divergence unless the exact version is listed in `allowed` for that
    lock, so a documented-yet-untouched entry cannot absorb a further drift.
    """
    allowed = allowed or {}
    try:
        baseline = lock_package_versions(baseline_lock)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"cannot read baseline Cargo.lock {baseline_lock.name}: {exc}"]

    issues: list[str] = []
    for sub_lock in sub_locks:
        if not sub_lock.is_file():
            continue
        relative = display_path(sub_lock)
        exemption = allowed.get(relative, {})
        try:
            sub_versions = lock_package_versions(sub_lock)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            issues.append(f"cannot read Cargo.lock {relative}: {exc}")
            continue
        for name, versions in sorted(sub_versions.items()):
            baseline_versions = baseline.get(name)
            if not baseline_versions:
                continue  # unique to this workspace: not a shared package
            stale = versions - baseline_versions
            stale -= exemption.get(name, set())
            if stale:
                issues.append(
                    f"cross-workspace version drift: {relative} resolves "
                    f"{name} {', '.join(sorted(stale))} but the baseline lock "
                    f"({display_path(baseline_lock)}) resolves "
                    f"{', '.join(sorted(baseline_versions))}; rebuild the lock "
                    "against the baseline or record the divergence in "
                    "CROSS_LOCK_ALLOWED_DIVERGENCES"
                )
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
    # --- Rust direct dev dependencies ---
    # The notices preamble states that development-only crates are listed, so
    # the checker holds the file to that claim instead of trusting it.
    rust_dev_deps = [
        name for name in parse_rust_dev_deps(CARGO_TOML) if name not in rust_deps
    ]
    runtime_names = rust_deps + list(NOTICE_REQUIRED_TRANSITIVE_DEPS)
    try:
        problems.extend(
            collect_notice_version_issues(
                runtime_names, CARGO_LOCK, notices, label="Rust dependency"
            )
        )
        problems.extend(
            collect_notice_version_issues(
                rust_dev_deps,
                CARGO_LOCK,
                notices,
                label="Rust dev dependency",
            )
        )
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

    # --- Cross-workspace shared-package version consistency ---
    # The baseline is the production converter lock; every other lock must
    # agree with it on shared packages (see CROSS_LOCK_ALLOWED_DIVERGENCES
    # for the documented current exceptions).  A Dependabot-style commit that
    # touches only the baseline lock now fails here instead of silently
    # leaving the fuzz/e2e locks behind.
    problems.extend(
        collect_cross_lock_version_issues(
            CARGO_LOCK,
            [lock for lock in SUB_WORKSPACE_CARGO_LOCKS if lock != CARGO_LOCK],
            CROSS_LOCK_ALLOWED_DIVERGENCES,
        )
    )

    # --- Report ---
    if problems:
        return report_missing_and_fail(problems)
    dep_count = (
        len(rust_deps)
        + len(rust_dev_deps)
        + len(NOTICE_REQUIRED_TRANSITIVE_DEPS)
        + len(C_RUNTIME_DEPS)
    )
    print(f"THIRD-PARTY-NOTICES coverage check passed ({dep_count} dependencies verified).")
    return 0


def report_missing_and_fail(issues: list[str]) -> int:
    """Print dependency or lockfile issues and return a non-zero exit code."""
    print("THIRD-PARTY-NOTICES coverage check failed.")
    print("The following dependency or lockfile validation issues were found:")
    for item in issues:
        print(f"  - {item}")
    print()
    print("Please update the THIRD-PARTY-NOTICES file in the repository root")
    print("to include license information for each missing dependency.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
