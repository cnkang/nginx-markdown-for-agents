#!/usr/bin/env python3
"""Enforce the repository's exact Rust compiler and public MSRV contract."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATHS = (
    Path("components/rust-converter/Cargo.toml"),
    Path("components/rust-converter/fuzz/Cargo.toml"),
    Path("tools/e2e-harness/Cargo.toml"),
    Path("tools/corpus/test-corpus-conversion/Cargo.toml"),
)
BASELINE_ACTION_WORKFLOWS = (
    Path(".github/workflows/ci.yml"),
    Path(".github/workflows/codeql.yml"),
    Path(".github/workflows/macos-smoke.yml"),
    Path(".github/workflows/nightly-perf.yml"),
    Path(".github/workflows/real-nginx-ims.yml"),
    Path(".github/workflows/sonarcloud.yml"),
)
NIGHTLY_ACTION_WORKFLOWS = (Path(".github/workflows/nightly-fuzz.yml"),)
RELEASE_WORKFLOWS = (
    Path(".github/workflows/release-packages.yml"),
    Path(".github/workflows/release-deb.yml"),
    Path(".github/workflows/release-rpm.yml"),
)
RELEASE_DOCKERFILES = (
    Path("tools/build_release/Dockerfile.glibc"),
    Path("tools/build_release/Dockerfile.musl"),
)
CURRENT_BUILD_DOCS = (
    Path("README.md"),
    Path("README_zh-CN.md"),
    Path("CONTRIBUTING.md"),
    Path("docs/COMPATIBILITY.md"),
    Path("docs/FAQ.md"),
    Path("docs/guides/INSTALLATION.md"),
    Path("docs/guides/KUBERNETES_DEPLOYMENT.md"),
    Path("docs/guides/OPERATIONS.md"),
    Path("docs/project/PROJECT_STATUS.md"),
)
EXACT_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
ACTION_TOOLCHAIN_RE = re.compile(r"^\s*toolchain:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)
RELEASE_TOOLCHAIN_RE = re.compile(
    r"^\s*RUST_TOOLCHAIN:\s*['\"]?([^'\"\s#]+)", re.MULTILINE
)


def _read_toml(path: Path) -> dict:
    """Read a repository-owned TOML file."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _read_text(root: Path, relative_path: Path, errors: list[str]) -> str | None:
    """Read a required text file and record a clear error on failure."""
    path = root / relative_path
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"{relative_path}: cannot read required file: {exc}")
        return None


def _load_canonical_versions(root: Path, errors: list[str]) -> tuple[str, str] | None:
    """Return the exact toolchain and matching major.minor MSRV."""
    relative_path = Path("rust-toolchain.toml")
    try:
        data = _read_toml(root / relative_path)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"{relative_path}: cannot load canonical toolchain: {exc}")
        return None

    exact = str(data.get("toolchain", {}).get("channel", ""))
    if not EXACT_VERSION_RE.fullmatch(exact):
        errors.append(
            f"{relative_path}: toolchain.channel must be an exact MAJOR.MINOR.PATCH "
            f"version, found {exact!r}"
        )
        return None
    major, minor, _patch = exact.split(".")
    return exact, f"{major}.{minor}"


def _check_manifests(root: Path, expected_msrv: str, errors: list[str]) -> None:
    """Check every supported first-party Cargo manifest's public MSRV."""
    for relative_path in MANIFEST_PATHS:
        try:
            package = _read_toml(root / relative_path).get("package", {})
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"{relative_path}: cannot load manifest: {exc}")
            continue
        actual = str(package.get("rust-version", ""))
        if actual != expected_msrv:
            errors.append(
                f"{relative_path}: package.rust-version is {actual!r}; "
                f"expected {expected_msrv!r} from rust-toolchain.toml"
            )


def _check_workflow_group(
    root: Path,
    paths: tuple[Path, ...],
    pattern: re.Pattern[str],
    expected: str,
    label: str,
    errors: list[str],
) -> None:
    """Check every matching compiler declaration in a workflow group."""
    for relative_path in paths:
        content = _read_text(root, relative_path, errors)
        if content is None:
            continue
        versions = pattern.findall(content)
        if not versions:
            errors.append(f"{relative_path}: missing required {label} declaration")
            continue
        for version in versions:
            if version != expected:
                errors.append(
                    f"{relative_path}: {label} is {version!r}; expected {expected!r}"
                )


def _check_workflow_inventory(root: Path, errors: list[str]) -> None:
    """Reject newly added Rust-installing workflows outside the frozen policy."""
    workflow_dir = root / ".github" / "workflows"
    known = set(BASELINE_ACTION_WORKFLOWS + NIGHTLY_ACTION_WORKFLOWS)
    known.update(RELEASE_WORKFLOWS)
    for path in sorted(workflow_dir.glob("*.y*ml")):
        content = path.read_text(encoding="utf-8")
        installs_rust = "dtolnay/rust-toolchain" in content or "RUST_TOOLCHAIN:" in content
        relative_path = path.relative_to(root)
        if installs_rust and relative_path not in known:
            errors.append(
                f"{relative_path}: Rust-installing workflow is not classified by "
                "check_rust_baseline.py"
            )


def _check_release_dockerfiles(root: Path, errors: list[str]) -> None:
    """Ensure release images consume the canonical repository toolchain file."""
    required = ("COPY rust-toolchain.toml", "rustup toolchain install")
    for relative_path in RELEASE_DOCKERFILES:
        content = _read_text(root, relative_path, errors)
        if content is None:
            continue
        for snippet in required:
            if snippet not in content:
                errors.append(
                    f"{relative_path}: release compiler must consume the canonical "
                    f"rust-toolchain.toml (missing {snippet!r})"
                )


def _check_packaging(root: Path, expected_msrv: str, errors: list[str]) -> None:
    """Check the source-build package compiler floor."""
    relative_path = Path("packaging/debian/control")
    content = _read_text(root, relative_path, errors)
    if content is None:
        return
    match = re.search(r"\brustc\s*\(>=\s*([0-9]+\.[0-9]+)\s*\)", content)
    if match is None:
        errors.append(f"{relative_path}: missing rustc (>= MAJOR.MINOR) build dependency")
    elif match.group(1) != expected_msrv:
        errors.append(
            f"{relative_path}: rustc floor is {match.group(1)!r}; "
            f"expected {expected_msrv!r}"
        )


def _check_current_docs(
    root: Path, exact: str, expected_msrv: str, errors: list[str]
) -> None:
    """Check active build documentation without inspecting historical records."""
    for relative_path in CURRENT_BUILD_DOCS:
        content = _read_text(root, relative_path, errors)
        if content is None:
            continue
        if exact not in content and f"MSRV {expected_msrv}" not in content:
            errors.append(
                f"{relative_path}: current build documentation must mention Rust "
                f"{exact} or MSRV {expected_msrv}"
            )


def collect_errors(root: Path = REPO_ROOT) -> tuple[str | None, str | None, list[str]]:
    """Collect Rust baseline contract violations for *root*."""
    errors: list[str] = []
    versions = _load_canonical_versions(root, errors)
    if versions is None:
        return None, None, errors
    exact, msrv = versions

    _check_manifests(root, msrv, errors)
    _check_workflow_group(
        root,
        BASELINE_ACTION_WORKFLOWS,
        ACTION_TOOLCHAIN_RE,
        exact,
        "toolchain",
        errors,
    )
    _check_workflow_group(
        root,
        NIGHTLY_ACTION_WORKFLOWS,
        ACTION_TOOLCHAIN_RE,
        "nightly",
        "nightly toolchain",
        errors,
    )
    _check_workflow_group(
        root,
        RELEASE_WORKFLOWS,
        RELEASE_TOOLCHAIN_RE,
        exact,
        "RUST_TOOLCHAIN",
        errors,
    )
    _check_workflow_inventory(root, errors)
    _check_release_dockerfiles(root, errors)
    _check_packaging(root, msrv, errors)
    _check_current_docs(root, exact, msrv, errors)
    return exact, msrv, errors


def main() -> int:
    """Validate the checked-in Rust baseline contract."""
    exact, msrv, errors = collect_errors()
    if errors:
        print("Rust baseline consistency check FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"Rust baseline consistency check PASSED: toolchain={exact}, MSRV={msrv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
