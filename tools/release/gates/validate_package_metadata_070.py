#!/usr/bin/env python3
"""
Package metadata validator for v0.7.0 release gates.

Validates that the package metadata files used by the v0.7.0 workflow exist
and contain required fields/paths:

1. packaging/nfpm/nfpm.yaml - Required nFPM metadata and install layout paths
2. packaging/rpm/SPECS/nginx-module-markdown.spec - Required sections:
   Name, Version, Requires, %post, %changelog

Exit codes:
  0 - All checks passed
  1 - One or more checks failed

Security: All file reads use Path.resolve() within PROJECT_ROOT.
No user-supplied patterns are compiled at runtime.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

NFPM_CONFIG = PROJECT_ROOT / "packaging" / "nfpm" / "nfpm.yaml"
RPM_SPEC = (
    PROJECT_ROOT
    / "packaging"
    / "rpm"
    / "SPECS"
    / "nginx-module-markdown.spec"
)
GITHUB_WORKFLOWS_DIR = PROJECT_ROOT / ".github" / "workflows"
RELEASE_PACKAGES_WORKFLOW = GITHUB_WORKFLOWS_DIR / "release-packages.yml"
RELEASE_DEB_WORKFLOW = GITHUB_WORKFLOWS_DIR / "release-deb.yml"
RELEASE_RPM_WORKFLOW = GITHUB_WORKFLOWS_DIR / "release-rpm.yml"
SIGN_AND_PUBLISH_WORKFLOW = GITHUB_WORKFLOWS_DIR / "sign-and-publish.yml"
CHECKSUMS_FILE = PROJECT_ROOT / "packaging" / "checksums.sha256"
RELEASE_DOCKERFILES = [
    PROJECT_ROOT / "tools" / "build_release" / "Dockerfile.glibc",
    PROJECT_ROOT / "tools" / "build_release" / "Dockerfile.musl",
    PROJECT_ROOT / "tools" / "build_release" / "Dockerfile.install-example",
]
CANONICAL_MODULE_SO = "ngx_http_markdown_filter_module.so"
LEGACY_MODULE_SO = "ngx_http_markdown_module.so"
CANONICAL_PACKAGE_NAME = "nginx-module-markdown-for-agents"

NFPM_REQUIRED_SNIPPETS = [
    'name: "nginx-module-markdown-for-agents"',
    'version: "${PKG_VERSION}"',
    'arch: "${NFPM_ARCH}"',
    'nginx (>= ${NGINX_VERSION})',
    "/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so",
    "/usr/share/doc/nginx-markdown-for-agents/README.md",
    "/usr/share/doc/nginx-markdown-for-agents/INSTALL.md",
    "/usr/share/doc/nginx-markdown-for-agents/COMPATIBILITY.md",
    "/usr/share/licenses/nginx-markdown-for-agents/LICENSE",
]
RPM_REQUIRED_FIELDS = ["Name", "Version", "Requires"]
RPM_REQUIRED_SECTIONS = ["%post", "%changelog"]
MODULE_NAME_SURFACES = [
    NFPM_CONFIG,
    RPM_SPEC,
    PROJECT_ROOT / "packaging" / "rpm" / "nginx-markdown-module.spec",
    PROJECT_ROOT / "packaging" / "debian" / "postinst",
    PROJECT_ROOT / "packaging" / "snippets" / "mod-markdown-for-agents.conf",
    PROJECT_ROOT / "packaging" / "nfpm" / "modules-available" / "mod-markdown.conf",
    PROJECT_ROOT / "packaging" / "nfpm" / "scripts" / "postinstall.sh",
    PROJECT_ROOT / "packaging" / "scripts" / "build-deb.sh",
    PROJECT_ROOT / "packaging" / "scripts" / "smoke-test-basic.sh",
    PROJECT_ROOT / "packaging" / "scripts" / "smoke-test-diagnostics.sh",
    PROJECT_ROOT / "tools" / "release" / "gates" / "check_install_layout.sh",
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "README_zh-CN.md",
    PROJECT_ROOT / "docs" / "COMPATIBILITY.md",
    PROJECT_ROOT / "docs" / "guides" / "INSTALL.md",
    RELEASE_PACKAGES_WORKFLOW,
    RELEASE_DEB_WORKFLOW,
    RELEASE_RPM_WORKFLOW,
]
RELEASE_VERSION_SURFACES = [
    RELEASE_PACKAGES_WORKFLOW,
    RELEASE_DEB_WORKFLOW,
    RELEASE_RPM_WORKFLOW,
    *RELEASE_DOCKERFILES,
]
RELEASE_ARTIFACT_SNIPPETS = {
    RELEASE_PACKAGES_WORKFLOW: [
        "name: pkg-deb-${{ matrix.nginx_version }}-${{ matrix.arch }}",
        "name: pkg-rpm-${{ matrix.nginx_version }}-${{ matrix.arch }}",
        "name: pkg-${{ matrix.format }}-${{ matrix.nginx_version }}-${{ matrix.arch }}",
    ],
    RELEASE_DEB_WORKFLOW: [
        "name: pkg-deb-${{ matrix.os }}-${{ matrix.arch }}-${{ matrix.nginx_channel }}",
    ],
    RELEASE_RPM_WORKFLOW: [
        "name: pkg-rpm-${{ matrix.os }}-${{ matrix.arch }}-${{ matrix.nginx_channel }}",
    ],
    SIGN_AND_PUBLISH_WORKFLOW: ["pattern: 'pkg-*'"],
}
ARCH_RUNNER_SNIPPET = (
    "runs-on: ${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || "
    "'ubuntu-24.04' }}"
)
STANDALONE_CONTAINER_BASH_SHELL = "defaults:\n      run:\n        shell: bash"
STANDALONE_DEB_SNIPPETS = [
    f'PKG_NAME="{CANONICAL_PACKAGE_NAME}"',
    "/usr/share/doc/nginx-markdown-for-agents",
    "/usr/share/licenses/nginx-markdown-for-agents",
    "docs/guides/INSTALL.md",
    "docs/COMPATIBILITY.md",
    "tools/release/gates/check_install_layout.sh dist/*.deb",
    '"dist/${PKG_NAME}_${PKG_VERSION}_nginx-${NGINX_VERSION}_${PKG_ARCH}.deb"',
]
STANDALONE_RPM_WORKFLOW_SNIPPETS = [
    f'PKG_NAME="{CANONICAL_PACKAGE_NAME}"',
    "docs/guides/INSTALL.md",
    "docs/COMPATIBILITY.md",
    "tools/release/gates/check_install_layout.sh dist/*.rpm",
]
STANDALONE_RPM_SPEC_SNIPPETS = [
    f"Name:           {CANONICAL_PACKAGE_NAME}",
    "Source0:        %{name}-%{version}.tar.gz",
    f"%setup -q -n {CANONICAL_PACKAGE_NAME}-%{{version}}",
    "# No-op: release-rpm.yml packages a prebuilt dynamic module.",
    "install -m 0644 ngx_http_markdown_filter_module.so",
]

_CHECK_CHECKSUMS_EXISTS = "checksums:exists"


class ValidationResult:
    """Accumulates PASS/FAIL results for reporting."""

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def pass_(self, check_id: str, message: str) -> None:
        self.results.append(("PASS", check_id, message))

    def fail(self, check_id: str, message: str) -> None:
        self.results.append(("FAIL", check_id, message))

    @property
    def has_failures(self) -> bool:
        return any(s == "FAIL" for s, _, _ in self.results)


def read_safe(path: Path) -> str:
    """Read file content safely, returning empty string if missing."""
    resolved = path.resolve()
    if not str(resolved).startswith(str(PROJECT_ROOT)):
        return ""
    return resolved.read_text(encoding="utf-8") if resolved.is_file() else ""


def _check_snippets(
    content: str, snippets: list[str], prefix: str, label: str,
    result: ValidationResult,
) -> None:
    """Check that content contains all required snippets."""
    for snippet in snippets:
        sid = f"{prefix}:{snippet[:24]}"
        if snippet in content:
            result.pass_(sid, f"{label} contains {snippet}")
        else:
            result.fail(sid, f"{label} missing {snippet}")


def _check_container_bash_shell(
    content: str, prefix: str, result: ValidationResult
) -> None:
    """Check that a container job with bashisms sets defaults.run.shell: bash."""
    has_bashism = "[[" in content or "{BUILD,RPMS,SOURCES,SPECS,SRPMS}" in content
    if "container:" not in content or not has_bashism:
        return
    if STANDALONE_CONTAINER_BASH_SHELL in content:
        result.pass_(
            f"{prefix}:container-shell",
            "container job uses bash for run steps",
        )
    else:
        result.fail(
            f"{prefix}:container-shell",
            "container job with bashisms must set defaults.run.shell: bash",
        )


def validate_nfpm_config(result: ValidationResult) -> None:
    """Validate the nFPM config used by release-packages.yml."""
    check_id = "nfpm:exists"
    content = read_safe(NFPM_CONFIG)
    if not content:
        result.fail(check_id, "packaging/nfpm/nfpm.yaml not found")
        return
    result.pass_(check_id, "packaging/nfpm/nfpm.yaml exists")

    for snippet in NFPM_REQUIRED_SNIPPETS:
        sid = f"nfpm:contains:{snippet[:24]}"
        if snippet in content:
            result.pass_(sid, f"nFPM config contains {snippet}")
        else:
            result.fail(sid, f"nFPM config missing {snippet}")


def validate_rpm_spec(result: ValidationResult) -> None:
    """Validate the RPM spec file exists and has required sections."""
    check_id = "rpm:exists"
    content = read_safe(RPM_SPEC)
    if not content:
        result.fail(check_id, "packaging/rpm/SPECS/nginx-module-markdown.spec not found")
        return
    result.pass_(check_id, "RPM spec file exists")

    for field in RPM_REQUIRED_FIELDS:
        fid = f"rpm:field:{field}"
        pattern = rf"^{re.escape(field)}:"
        if re.search(pattern, content, re.MULTILINE):
            result.pass_(fid, f"{field} field present")
        else:
            result.fail(fid, f"{field} field missing from RPM spec")

    for section in RPM_REQUIRED_SECTIONS:
        sid = f"rpm:section:{section}"
        if section in content:
            result.pass_(sid, f"{section} section present")
        else:
            result.fail(sid, f"{section} section missing from RPM spec")


def validate_module_filename_consistency(result: ValidationResult) -> None:
    """Ensure active package surfaces use the canonical NGINX module filename."""
    for path in MODULE_NAME_SURFACES:
        rel = path.relative_to(PROJECT_ROOT)
        content = read_safe(path)
        if not content:
            result.fail(f"module-name:exists:{rel}", f"{rel} not found")
            continue
        if LEGACY_MODULE_SO in content:
            result.fail(
                f"module-name:legacy:{rel}",
                f"{rel} still references {LEGACY_MODULE_SO}",
            )
        elif CANONICAL_MODULE_SO in content:
            result.pass_(
                f"module-name:canonical:{rel}",
                f"{rel} uses {CANONICAL_MODULE_SO}",
            )
        else:
            result.fail(
                f"module-name:missing:{rel}",
                f"{rel} does not reference {CANONICAL_MODULE_SO}",
            )


def checksum_identifiers() -> set[str]:
    """Return checked-in checksum identifiers from packaging/checksums.sha256."""
    if not CHECKSUMS_FILE.exists():
        return set()
    content = read_safe(CHECKSUMS_FILE)
    identifiers: set[str] = set()
    for line in content.splitlines():
        if match := re.match(r"^[0-9a-f]{64}\s+(\S+)$", line.strip()):
            identifiers.add(match[1])
    return identifiers


def extract_nginx_versions(content: str) -> set[str]:
    """Extract NGINX source versions from active release configuration."""
    versions: set[str] = set()
    # Handle matrix-style arrays: nginx_version: ["1.25.5", "1.26.1"]
    for match in re.findall(r'nginx_version:\s{0,20}\[([^\]]+)\]', content):
        versions.update(re.findall(r'(\d+\.\d+\.\d+)', match))
    # Handle shell/Dockerfile-style declarations
    patterns = [
        r'NGINX_VERSION="(\d+\.\d+\.\d+)"',
        r"ARG\s+NGINX_VERSION=(\d+\.\d+\.\d+)",
    ]
    for pattern in patterns:
        versions.update(re.findall(pattern, content))
    return versions


def validate_release_versions_have_checksums(result: ValidationResult) -> None:
    """Verify active release source versions are present in checksum metadata."""
    if not CHECKSUMS_FILE.exists():
        result.fail(
            _CHECK_CHECKSUMS_EXISTS,
            "packaging/checksums.sha256 not found",
        )
        return
    identifiers = checksum_identifiers()
    if not identifiers:
        result.fail(_CHECK_CHECKSUMS_EXISTS, "packaging/checksums.sha256 has no entries")
        return
    result.pass_(_CHECK_CHECKSUMS_EXISTS, "packaging/checksums.sha256 has checksum entries")

    for path in RELEASE_VERSION_SURFACES:
        rel = path.relative_to(PROJECT_ROOT)
        content = read_safe(path)
        if not content:
            result.fail(f"nginx-version:exists:{rel}", f"{rel} not found")
            continue
        versions = extract_nginx_versions(content)
        if not versions:
            result.fail(f"nginx-version:present:{rel}", f"{rel} has no NGINX version")
            continue
        for version in sorted(versions):
            identifier = f"nginx-{version}"
            sid = f"nginx-version:checksum:{rel}:{version}"
            if identifier in identifiers:
                result.pass_(sid, f"{identifier} has a checked-in checksum")
            else:
                result.fail(sid, f"{identifier} missing from packaging/checksums.sha256")


def validate_release_artifact_flow(result: ValidationResult) -> None:
    """Ensure package artifact producers and consumers share one name contract."""
    for path, snippets in RELEASE_ARTIFACT_SNIPPETS.items():
        rel = path.relative_to(PROJECT_ROOT)
        content = read_safe(path)
        if not content:
            result.fail(f"artifact-flow:exists:{rel}", f"{rel} not found")
            continue
        for snippet in snippets:
            sid = f"artifact-flow:{rel}:{snippet[:18]}"
            if snippet in content:
                result.pass_(sid, f"{rel} contains {snippet}")
            else:
                result.fail(sid, f"{rel} missing {snippet}")

    release_packages = read_safe(RELEASE_PACKAGES_WORKFLOW)
    if ARCH_RUNNER_SNIPPET in release_packages:
        result.pass_(
            "smoke-runner:arch",
            "release-packages smoke tests select runner from matrix.arch",
        )
    else:
        result.fail(
            "smoke-runner:arch",
            "release-packages smoke tests must use arch-matched runners",
        )


def _validate_standalone_deb(result: ValidationResult) -> None:
    """Validate standalone DEB workflow matches canonical package layout."""
    deb_workflow = read_safe(RELEASE_DEB_WORKFLOW)
    if not deb_workflow:
        result.fail("standalone-deb:exists", "release-deb.yml not found")
        return
    _check_container_bash_shell(deb_workflow, "standalone-deb", result)
    _check_snippets(
        deb_workflow, STANDALONE_DEB_SNIPPETS, "standalone-deb",
        "release-deb.yml", result,
    )


def _validate_standalone_rpm_workflow(result: ValidationResult) -> None:
    """Validate standalone RPM workflow matches canonical package layout."""
    rpm_workflow = read_safe(RELEASE_RPM_WORKFLOW)
    if not rpm_workflow:
        result.fail("standalone-rpm:exists", "release-rpm.yml not found")
        return
    _check_container_bash_shell(rpm_workflow, "standalone-rpm", result)
    _check_snippets(
        rpm_workflow, STANDALONE_RPM_WORKFLOW_SNIPPETS, "standalone-rpm-workflow",
        "release-rpm.yml", result,
    )


def _validate_standalone_rpm_spec(result: ValidationResult) -> None:
    """Validate standalone RPM spec matches canonical package layout."""
    rpm_spec = read_safe(RPM_SPEC)
    if not rpm_spec:
        result.fail("standalone-rpm-spec:exists", "RPM spec not found")
        return
    _check_snippets(
        rpm_spec, STANDALONE_RPM_SPEC_SNIPPETS, "standalone-rpm-spec",
        "RPM spec", result,
    )
    if re.search(r"^\s*make\s+build\s*$", rpm_spec, re.MULTILINE):
        result.fail(
            "standalone-rpm-spec:no-make-build",
            "prebuilt standalone RPM spec must not run make build",
        )
    else:
        result.pass_(
            "standalone-rpm-spec:no-make-build",
            "prebuilt standalone RPM spec does not run make build",
        )


def validate_standalone_workflow_packaging(result: ValidationResult) -> None:
    """Validate standalone DEB/RPM workflows match canonical package layout."""
    _validate_standalone_deb(result)
    _validate_standalone_rpm_workflow(result)
    _validate_standalone_rpm_spec(result)


def print_report(result: ValidationResult) -> None:
    """Print a formatted validation report."""
    print("v0.7.0 Package Metadata Validation Report")
    print("=" * 60)
    for status, check_id, message in result.results:
        print(f"  {status:4s}  {check_id:35s}  {message}")
    print()
    p = sum(s == "PASS" for s, _, _ in result.results)
    f = sum(s == "FAIL" for s, _, _ in result.results)
    print(f"Summary: {p} passed, {f} failed")


def main() -> int:
    """CLI entry point for package metadata validation."""
    result = ValidationResult()
    validate_nfpm_config(result)
    validate_rpm_spec(result)
    validate_module_filename_consistency(result)
    validate_release_versions_have_checksums(result)
    validate_release_artifact_flow(result)
    validate_standalone_workflow_packaging(result)
    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
