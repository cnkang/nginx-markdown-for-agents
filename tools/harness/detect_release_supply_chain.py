#!/usr/bin/env python3
"""Validate immutable identities and verify-before-use release contracts.

This checker is intentionally repository-specific and low-noise. It protects
the release builders, documented source-build images, and Homebrew publisher
that can promote externally supplied bytes into distributed artifacts.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.path_validation import validate_read_path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
ALMALINUX_9 = (
    "almalinux:9@sha256:"
    "d2515c769e7b73f95c4fde38c0a505336ff38f14990c0b7253b77060a049a743"
)
ALPINE_320 = (
    "alpine:3.20@sha256:"
    "d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc"
)


@dataclass(frozen=True)
class Finding:
    """One violated repository release-integrity contract."""

    path: str
    message: str


def _require(path: str, text: str, needle: str, message: str) -> list[Finding]:
    """Return one finding when ``needle`` is absent."""
    return [] if needle in text else [Finding(path, message)]


def _require_order(
    path: str,
    text: str,
    first: str,
    second: str,
    third: str,
    message: str,
) -> list[Finding]:
    """Require three security boundary markers in strict source order."""
    first_pos = text.find(first)
    second_pos = text.find(second, first_pos + len(first)) if first_pos >= 0 else -1
    third_pos = text.find(third, second_pos + len(second)) if second_pos >= 0 else -1
    positions = (first_pos, second_pos, third_pos)
    if positions[0] >= 0 and positions[0] < positions[1] < positions[2]:
        return []
    return [Finding(path, message)]


def check_release_builder_digests(files: dict[str, str]) -> list[Finding]:
    """Require reviewed manifest digests on artifact-producing builders."""
    expected = {
        "tools/build_release/Dockerfile.glibc": f"ARG OS_BASE={ALMALINUX_9}",
        "tools/build_release/Dockerfile.musl": f"ARG OS_BASE={ALPINE_320}",
        ".github/workflows/release-packages.yml": f"container: {ALMALINUX_9}",
    }
    findings: list[Finding] = []
    for path, reference in expected.items():
        findings.extend(
            _require(
                path,
                files[path],
                reference,
                "release builder must use the reviewed multi-architecture "
                "manifest digest",
            )
        )
    return findings


def check_ingress_builder(text: str) -> list[Finding]:
    """Validate ingress source identity and NGINX archive ordering."""
    path = "examples/kubernetes/Dockerfile.ingress"
    required = {
        "ARG MODULE_SHA=": "MODULE_SHA must be a required build input",
        "grep -Eq '^[0-9a-f]{40}$'": "MODULE_SHA must be a full object ID",
        "actual_sha=\"$(git rev-parse 'FETCH_HEAD^{commit}')\"": (
            "fetched source must resolve to a commit"
        ),
        "test \"${actual_sha}\" = \"${MODULE_SHA}\"": (
            "fetched commit must equal the reviewed MODULE_SHA"
        ),
        "git checkout --detach \"${actual_sha}\"": (
            "only the verified commit may be checked out"
        ),
    }
    findings: list[Finding] = []
    for needle, message in required.items():
        findings.extend(_require(path, text, needle, message))
    findings.extend(
        _require_order(
            path,
            text,
            "https://nginx.org/download/nginx-${NGINX_VERSION}.tar.gz",
            "/opt/nginx-markdown/verify-checksum.sh",
            "tar -xzf /tmp/nginx.tar.gz",
            "NGINX source must be verified before extraction",
        )
    )
    return findings


def check_official_nginx_builder(text: str) -> list[Finding]:
    """Validate verify-before-extract ordering in the official-image example."""
    return _require_order(
        "examples/docker/Dockerfile.official-nginx-source-build",
        text,
        "https://nginx.org/download/nginx-${nginx_version}.tar.gz",
        "bash /opt/nginx-markdown/verify-checksum.sh",
        "tar -xzf /tmp/nginx.tar.gz",
        "official-image NGINX source must be verified before extraction",
    )


def check_homebrew_formula(text: str) -> list[Finding]:
    """Validate verified Rust and NGINX bootstraps in the Formula."""
    path = "packaging/homebrew/nginx-markdown-module.rb"
    findings: list[Finding] = []
    if "https://sh.rustup.rs" in text:
        findings.append(
            Finding(path, "Formula must not execute the network rustup script")
        )
    for needle, message in {
        "install-verified-rustup.sh": (
            "Formula must use the checksum-verifying rustup helper"
        ),
        '"--os", "darwin"': (
            "Formula must select reviewed Darwin rustup-init targets"
        ),
        "Hardware::CPU.arm?": (
            "Formula must select the matching macOS architecture"
        ),
    }.items():
        findings.extend(_require(path, text, needle, message))
    findings.extend(
        _require_order(
            path,
            text,
            "https://nginx.org/download/#{nginx_archive}",
            "verify-checksum.sh",
            'system "tar", "-xzf", nginx_archive',
            "Formula NGINX source must be verified before extraction",
        )
    )
    return findings


def check_homebrew_publisher(text: str) -> list[Finding]:
    """Require one tag-derived Formula identity before tap credentials."""
    path = ".github/workflows/homebrew-tap-publish.yml"
    required = {
        "github.event.repository.default_branch": (
            "manual publication must run only from the default branch"
        ),
        'git show "${TAG_COMMIT}:${FORMULA_SOURCE}"': (
            "Formula source must be read from the resolved tag commit"
        ),
        "render_homebrew_formula.py": (
            "tag Formula rendering must use the focused renderer"
        ),
        "verify_git_archive.py": (
            "downloaded tag archive must be compared with the resolved commit"
        ),
        'git archive --format=tar --prefix=reference/': (
            "publisher must materialize a reference archive from TAG_COMMIT"
        ),
        "brew audit --strict nginx-markdown-module": (
            "the exact rendered Formula must pass strict audit before publish"
        ),
    }
    findings: list[Finding] = []
    for needle, message in required.items():
        findings.extend(_require(path, text, needle, message))

    audit_pos = text.find("brew audit --strict nginx-markdown-module")
    token_pos = text.find("HOMEBREW_TAP_TOKEN")
    if audit_pos < 0 or token_pos < 0 or audit_pos >= token_pos:
        findings.append(
            Finding(
                path,
                "tap credentials must be introduced only after tag binding "
                "and strict Formula audit",
            )
        )
    return findings


def check_ingress_smoke(text: str) -> list[Finding]:
    """Require the default ingress build smoke to pass an immutable source ID."""
    path = "examples/kubernetes/tests/test-docker-build.sh"
    findings: list[Finding] = []
    for needle, message in {
        "git -C \"$BUILD_CONTEXT\" rev-parse --verify 'HEAD^{commit}'": (
            "default smoke must derive MODULE_SHA from the build context"
        ),
        '--build-arg "MODULE_SHA=${MODULE_SHA}"': (
            "ingress Docker build must receive the reviewed MODULE_SHA"
        ),
        "'^[0-9a-f]{40}$'": (
            "smoke must reject abbreviated or malformed module identities"
        ),
    }.items():
        findings.extend(_require(path, text, needle, message))
    return findings


def check_homebrew_dependencies(files: dict[str, str]) -> list[Finding]:
    """Require formula CI to follow every verification dependency."""
    path = ".github/workflows/homebrew-formula-gate.yml"
    text = files[path]
    dependencies = (
        "packaging/checksums.sha256",
        "packaging/scripts/install-verified-rustup.sh",
        "packaging/scripts/render_homebrew_formula.py",
        "packaging/scripts/verify-checksum.sh",
    )
    findings: list[Finding] = []
    for dependency in dependencies:
        findings.extend(
            _require(
                path,
                text,
                f'- "{dependency}"',
                f"Homebrew formula gate must watch {dependency}",
            )
        )
    return findings


def check_official_workflow_dependencies(text: str) -> list[Finding]:
    """Require the official-image gate to follow checksum control changes."""
    path = ".github/workflows/official-nginx-docker.yml"
    findings: list[Finding] = []
    findings.extend(
        Finding(path, f"pull and push filters must watch {dependency}")
        for dependency in (
            "packaging/checksums.sha256",
            "packaging/scripts/verify-checksum.sh",
        )
        if len(re.findall(re.escape(dependency), text)) < 2
    )
    return findings


def _read_files(root: Path, paths: tuple[str, ...]) -> tuple[dict[str, str], list[Finding]]:
    """Read fixed repository paths and fail closed on missing or invalid text."""
    files: dict[str, str] = {}
    findings: list[Finding] = []
    for relative in paths:
        try:
            resolved = validate_read_path(
                root / relative,
                purpose="release supply-chain input",
            )
            files[relative] = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            findings.append(Finding(relative, f"cannot read required file: {exc}"))
    return files, findings


def scan_repository(root: Path = REPO_ROOT) -> list[Finding]:
    """Scan the fixed release-integrity surfaces under ``root``."""
    paths = (
        "tools/build_release/Dockerfile.glibc",
        "tools/build_release/Dockerfile.musl",
        ".github/workflows/release-packages.yml",
        "examples/kubernetes/Dockerfile.ingress",
        "examples/docker/Dockerfile.official-nginx-source-build",
        "packaging/homebrew/nginx-markdown-module.rb",
        ".github/workflows/homebrew-tap-publish.yml",
        ".github/workflows/homebrew-formula-gate.yml",
        ".github/workflows/official-nginx-docker.yml",
        "examples/kubernetes/tests/test-docker-build.sh",
    )
    files, findings = _read_files(root, paths)
    if findings:
        return findings
    findings.extend(check_release_builder_digests(files))
    findings.extend(check_ingress_builder(files[paths[3]]))
    findings.extend(check_official_nginx_builder(files[paths[4]]))
    findings.extend(check_homebrew_formula(files[paths[5]]))
    findings.extend(check_homebrew_publisher(files[paths[6]]))
    findings.extend(check_homebrew_dependencies(files))
    findings.extend(check_official_workflow_dependencies(files[paths[8]]))
    findings.extend(check_ingress_smoke(files[paths[9]]))
    return findings


def main() -> int:
    """Run the repository scan and print deterministic diagnostics."""
    findings = scan_repository()
    for finding in findings:
        print(f"ERROR: {finding.path}: {finding.message}", file=sys.stderr)
    if findings:
        print(
            f"Release supply-chain check failed: {len(findings)} finding(s)",
            file=sys.stderr,
        )
        return 1
    print("Release supply-chain contracts passed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
