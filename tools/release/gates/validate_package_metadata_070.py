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

import json
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
RELEASE_MATRIX = PROJECT_ROOT / "tools" / "release-matrix.json"
INSTALLATION_DOC = PROJECT_ROOT / "docs" / "guides" / "INSTALLATION.md"
PACKAGE_INSTALLATION_DOC = PROJECT_ROOT / "docs" / "guides" / "PACKAGE_INSTALLATION.md"
PACKAGE_DISTRIBUTION_DOC = PROJECT_ROOT / "docs" / "guides" / "PACKAGE_DISTRIBUTION.md"
GPG_KEY_MANAGEMENT_DOC = PROJECT_ROOT / "docs" / "guides" / "GPG_KEY_MANAGEMENT.md"
SMOKE_TEST_BASIC_NAME = "smoke-test-basic.sh"
NFPM_POSTINSTALL_NAME = "postinstall.sh"
SMOKE_TEST_BASIC = PROJECT_ROOT / "packaging" / "scripts" / SMOKE_TEST_BASIC_NAME
GATE3_LOCAL_PACKAGE_SMOKE = PROJECT_ROOT / "tools" / "release" / "gates" / (
    "gate3_local_package_smoke.sh"
)
NFPM_POSTINSTALL = (
    PROJECT_ROOT / "packaging" / "nfpm" / "scripts" / NFPM_POSTINSTALL_NAME
)
RELEASE_DOCKERFILES = [
    PROJECT_ROOT / "tools" / "build_release" / "Dockerfile.glibc",
    PROJECT_ROOT / "tools" / "build_release" / "Dockerfile.musl",
    PROJECT_ROOT / "tools" / "build_release" / "Dockerfile.install-example",
]
CANONICAL_MODULE_SO = "ngx_http_markdown_filter_module.so"
LEGACY_MODULE_SO = "ngx_http_markdown_module.so"
CANONICAL_PACKAGE_NAME = "nginx-module-markdown-for-agents"
_NGINX_VERSION_ASSIGNMENT = "NGINX_VERSION="

NFPM_REQUIRED_SNIPPETS = [
    'name: "nginx-module-markdown-for-agents"',
    'version: "${PKG_VERSION}"',
    'arch: "${NFPM_ARCH}"',
    'nginx (>= ${NGINX_VERSION_FLOOR})',
    'nginx (<< ${NGINX_VERSION_CEIL})',
    "/usr/lib/nginx/modules/ngx_http_markdown_filter_module.so",
    "packager: deb",
    "/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so",
    "packager: rpm",
    "/usr/share/doc/nginx-markdown-for-agents/README.md",
    "/usr/share/doc/nginx-markdown-for-agents/INSTALL.md",
    "/usr/share/doc/nginx-markdown-for-agents/COMPATIBILITY.md",
    "/usr/share/licenses/nginx-markdown-for-agents/LICENSE",
]
NFPM_DEB_ONLY_MODULES_AVAILABLE_PATTERN = (
    r'src: "\./packaging/nfpm/modules-available/mod-markdown\.conf"\n'
    r'\s+dst: "/usr/share/nginx/modules-available/mod-markdown\.conf"\n'
    r"\s+type: config\|noreplace\n"
    r"\s+packager: deb"
)
RPM_REQUIRED_FIELDS = ["Name", "Version", "Requires"]
RPM_REQUIRED_SECTIONS = ["%post", "%changelog"]
MODULE_NAME_SURFACES = [
    NFPM_CONFIG,
    RPM_SPEC,
    PROJECT_ROOT / "packaging" / "rpm" / "nginx-markdown-module.spec",
    PROJECT_ROOT / "packaging" / "debian" / "postinst",
    PROJECT_ROOT / "packaging" / "snippets" / "mod-markdown-for-agents.conf",
    PROJECT_ROOT / "packaging" / "nfpm" / "modules-available" / "mod-markdown.conf",
    NFPM_POSTINSTALL,
    PROJECT_ROOT / "packaging" / "scripts" / "build-deb.sh",
    SMOKE_TEST_BASIC,
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
    './packaging/scripts/validate-version.sh "${{ inputs.version }}"',
    f'PKG_NAME="{CANONICAL_PACKAGE_NAME}"',
    "/usr/share/doc/nginx-markdown-for-agents",
    "/usr/share/licenses/nginx-markdown-for-agents",
    "docs/guides/INSTALL.md",
    "docs/COMPATIBILITY.md",
    'NGINX_VERSION_FLOOR="${NGINX_MAJOR}.${NGINX_MINOR}.0"',
    'NGINX_VERSION_CEIL="${NGINX_MAJOR}.$((NGINX_MINOR + 1)).0"',
    "tools/release/gates/check_install_layout.sh dist/*.deb",
    '"dist/${PKG_NAME}_${PKG_VERSION}_nginx-${NGINX_VERSION}_${PKG_ARCH}.deb"',
]
STANDALONE_RPM_WORKFLOW_SNIPPETS = [
    './packaging/scripts/validate-version.sh "${{ inputs.version }}"',
    f'PKG_NAME="{CANONICAL_PACKAGE_NAME}"',
    "docs/guides/INSTALL.md",
    "docs/COMPATIBILITY.md",
    'NGINX_VERSION_FLOOR="${NGINX_MAJOR}.${NGINX_MINOR}.0"',
    'NGINX_VERSION_CEIL="${NGINX_MAJOR}.$((NGINX_MINOR + 1)).0"',
    "tools/release/gates/check_install_layout.sh dist/*.rpm",
]
STANDALONE_RPM_SPEC_SNIPPETS = [
    f"Name:           {CANONICAL_PACKAGE_NAME}",
    "Requires:       nginx >= %{nginx_version_floor}",
    "Requires:       nginx < %{nginx_version_ceil}",
    "Source0:        %{name}-%{version}.tar.gz",
    f"%setup -q -n {CANONICAL_PACKAGE_NAME}-%{{version}}",
    "# No-op: release-rpm.yml packages a prebuilt dynamic module.",
    "install -m 0644 ngx_http_markdown_filter_module.so",
    "/usr/lib64/nginx/modules/ngx_http_markdown_filter_module.so",
]
FORBIDDEN_NAKED_EXACT_NGINX_DEPS = [
    "nginx (= ${NGINX_VERSION})",
    "Requires:       nginx = %{nginx_version}",
]
SMOKE_RPM_REPO_SNIPPETS = [
    "detect_rpm_repo_baseurl()",
    "nginx_repo_channel()",
    "amzn)",
    "packages/%samzn/",
    "almalinux|centos|rocky|rhel)",
    "packages/%scentos/",
]
GATE3_LOCAL_ARCH_SNIPPETS = [
    'pkg_pattern="*_${ARCH}.deb"',
    'pkg_pattern="*-1.${RPM_ARCH}.rpm"',
    'find "$dist_dir" -name "$pkg_pattern"',
]
NFPM_POSTINSTALL_SNIPPETS = [
    "configure|1|2)",
    "abort-upgrade|abort-remove|abort-deconfigure)",
    'info "postinstall called with unknown argument: $ACTION"',
    "/usr/share/doc/nginx-markdown-for-agents/README.md",
    "exit 0",
]
NFPM_POSTINSTALL_FORBIDDEN_SNIPPETS = [
    "/usr/share/doc/nginx-module-markdown-for-agents/README.md",
]
RELEASE_BUILD_GLIBC_SNIPPETS = {
    RELEASE_PACKAGES_WORKFLOW: ["container: almalinux:9"],
    PROJECT_ROOT / "tools" / "build_release" / "Dockerfile.glibc": [
        "ARG OS_BASE=almalinux:9",
        "dnf install -y",
        "rustup-init.sh -y --default-toolchain none",
        "COPY rust-toolchain.toml /src/rust-toolchain.toml",
        "rustup toolchain install",
    ],
    PROJECT_ROOT / "tools" / "build_release" / "Dockerfile.musl": [
        "rustup-init.sh -y --default-toolchain none",
        "COPY rust-toolchain.toml /src/rust-toolchain.toml",
        "rustup toolchain install",
    ],
}

RELEASE_RUST_BUILD_INVARIANTS = [
    "RUST_TARGET",
    'rustup target add "${RUST_TARGET}"',
    'cargo build --release --locked --target "${RUST_TARGET}" --features "${RUST_FEATURES}"',
    "target/${RUST_TARGET}/release/libnginx_markdown_converter.a",
    "markdown_streaming_new",
    "markdown_incremental_new",
    "markdown_decompress_bounded",
]

_CHECK_CHECKSUMS_EXISTS = "checksums:exists"

PACKAGE_DOC_REQUIRED_SNIPPETS = {
    INSTALLATION_DOC: [
        "## 4.2 Linux Package Artifacts",
        "**Tier: Secondary** (v0.7.0+)",
        "APT/YUM repository publishing is planned",
        CANONICAL_PACKAGE_NAME,
        CANONICAL_MODULE_SO,
    ],
    PACKAGE_INSTALLATION_DOC: [
        "GitHub Releases are the current distribution channel",
        "Public APT/YUM repositories are planned but not available yet",
        CANONICAL_PACKAGE_NAME,
        CANONICAL_MODULE_SO,
        "SHA256SUMS",
    ],
    PACKAGE_DISTRIBUTION_DOC: [
        "GitHub Releases",
        "Active for v0.7.0+ release artifacts",
        "APT/YUM repository publishing is intentionally tracked as a future",
    ],
    GPG_KEY_MANAGEMENT_DOC: [
        "Public APT/YUM repositories are not available yet",
        "future self-hosted repository publication",
        "PACKAGE_INSTALLATION.md",
    ],
}

PACKAGE_DOC_FORBIDDEN_SNIPPETS = [
    "sudo apt-get install nginx-module-markdown",
    "sudo yum install nginx-module-markdown",
    "sudo apt-get install nginx-markdown-module",
    "sudo yum install nginx-markdown-module",
    "APT_REPO_URL",
    "YUM_REPO_URL",
]


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


def _is_within_project(path: Path) -> bool:
    """Return True if resolved path is within PROJECT_ROOT."""
    try:
        path.resolve().relative_to(PROJECT_ROOT.resolve())
        return True
    except ValueError:
        return False


def read_safe(path: Path) -> str:
    """Read file content safely, returning empty string if missing."""
    resolved = path.resolve()
    if not _is_within_project(path):
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
    if re.search(NFPM_DEB_ONLY_MODULES_AVAILABLE_PATTERN, content):
        result.pass_(
            "nfpm:modules-available:deb-only",
            "modules-available snippet is packaged only for DEB",
        )
    else:
        result.fail(
            "nfpm:modules-available:deb-only",
            "modules-available snippet must be limited to packager: deb",
        )


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


def validate_nginx_dependency_constraints(result: ValidationResult) -> None:
    """Reject exact dependencies that use only the upstream source version."""
    for path in (NFPM_CONFIG, RPM_SPEC):
        rel = path.relative_to(PROJECT_ROOT)
        content = read_safe(path)
        if not content:
            result.fail(f"nginx-dep:exists:{rel}", f"{rel} not found")
            continue
        for snippet in FORBIDDEN_NAKED_EXACT_NGINX_DEPS:
            sid = f"nginx-dep:not-exact:{rel}:{snippet[:12]}"
            if snippet in content:
                result.fail(
                    sid,
                    f"{rel} uses naked exact dependency {snippet}",
                )
            else:
                result.pass_(
                    sid,
                    f"{rel} does not use naked exact dependency {snippet}",
                )


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


def _iter_quote_aware(text: str):
    """Yield (index, char) for non-quote characters, tracking escape state.

    Quote characters themselves are consumed but never yielded, so callers
    see only the literal content between matching quotes.
    """
    quote: str | None = None
    escaped = False

    for index, char in enumerate(text):
        if escaped:
            yield index, char
            escaped = False
            continue

        if quote:
            if quote == '"' and char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = None
                continue
            yield index, char
            continue

        if char in {"'", '"'}:
            quote = char
            continue

        yield index, char


def _strip_unquoted_comment(line: str) -> str:
    """Strip inline comments while preserving # inside quoted strings."""
    quote: str | None = None
    escaped = False

    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue

        if quote:
            if quote == '"' and char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue

        if char in {"'", '"'}:
            quote = char
            continue

        if char == "#":
            return line[:index]

    return line


def _contains_make_build_command(content: str) -> bool:
    """Return True when content contains an active ``make build`` command line.

    Detects common variants:
      - ``make build``
      - ``make -j4 build``
      - ``cd src && make build``
      - ``gmake build``
      - ``%make_build build`` (RPM macro style)

    Does not match when make appears as an argument to another command
    (e.g. ``echo make build``).
    """
    for raw_line in content.splitlines():
        line = _strip_unquoted_comment(raw_line).strip()
        if not line:
            continue
        if _line_has_make_build(line):
            return True

    return False


def _line_has_make_build(line: str) -> bool:
    """Check if a single line contains a make build command."""
    shell_operators = {"&&", "||", ";", "|"}

    segments: list[list[str]] = [[]]
    for token in line.split():
        if token in shell_operators:
            segments.append([])
        else:
            segments[-1].append(token)

    return any(
        _segment_is_make_build(segment)
        for segment in segments
        if segment
    )


def _segment_is_make_build(segment: list[str]) -> bool:
    """Return True if a command segment is a make invocation with 'build' target."""
    cmd_token = segment[0]
    return "build" in segment[1:] if _is_make_token(cmd_token) else False


def _is_make_token(token: str) -> bool:
    """Return True if token looks like a make invocation.

    Accepts exact commands (make, gmake) and RPM-style macros (%make, %make_build, etc.).
    Does not match unrelated commands that happen to contain 'make' (cmake, automake).
    """
    return bool(
        re.match(r"^(?:make|gmake|%make\S*)$", token, re.IGNORECASE)
    )


def _unquote(value: str) -> str:
    """Trim whitespace, an optional trailing comma, and matching quotes."""
    value = value.strip().rstrip(",").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _is_nginx_version(value: str) -> bool:
    """Return True for strict number.number.number versions."""
    parts = value.split(".")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def _split_inline_list(value: str) -> list[str]:
    """Split a one-line YAML-style list without regex."""
    items: list[str] = []
    current: list[str] = []

    for _index, char in _iter_quote_aware(value):
        if char == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)

    if current:
        items.append("".join(current).strip())

    return items


def _extract_nginx_assignment_value(line: str) -> str | None:
    """Extract shell/Dockerfile-style NGINX_VERSION assignment value."""
    prefix = _NGINX_VERSION_ASSIGNMENT

    if line.startswith(prefix):
        return line[len(prefix):].strip()

    if line.startswith("ARG "):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].startswith(prefix):
            return parts[1][len(prefix):].strip()

    return None


def _extract_yaml_array_versions(value: str) -> set[str]:
    """Extract versions from a YAML-style inline array value."""
    versions: set[str] = set()
    closing_bracket = value.find("]")
    if closing_bracket == -1:
        return versions
    for item in _split_inline_list(value[1:closing_bracket]):
        version = _unquote(item)
        if _is_nginx_version(version):
            versions.add(version)
    return versions


def extract_nginx_versions(content: str) -> set[str]:
    """Extract NGINX source versions from active release configuration."""
    versions: set[str] = set()

    if "tools/release-matrix.json" in content and RELEASE_MATRIX.exists():
        try:
            data = json.loads(read_safe(RELEASE_MATRIX))
        except json.JSONDecodeError:
            data = {}
        for entry in data.get("matrix", []):
            if entry.get("support_tier") == "full" and entry.get("os_type") == "glibc":
                version = entry.get("nginx")
                if isinstance(version, str) and _is_nginx_version(version):
                    versions.add(version)

    for raw_line in content.splitlines():
        line = _strip_unquoted_comment(raw_line).strip()
        if not line:
            continue

        if line.startswith("nginx_version:"):
            value = line[len("nginx_version:"):].strip()
            if value.startswith("["):
                versions.update(_extract_yaml_array_versions(value))
            continue

        assignment_value = _extract_nginx_assignment_value(line)
        if assignment_value is not None:
            version = _unquote(assignment_value)
            if _is_nginx_version(version):
                versions.add(version)

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
    if _contains_make_build_command(rpm_spec):
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


def validate_smoke_test_repo_selection(result: ValidationResult) -> None:
    """Validate RPM smoke tests select nginx.org repos by distro family."""
    content = read_safe(SMOKE_TEST_BASIC)
    if not content:
        result.fail("smoke-repo:exists", f"{SMOKE_TEST_BASIC_NAME} not found")
        return
    _check_snippets(
        content, SMOKE_RPM_REPO_SNIPPETS, "smoke-repo",
        SMOKE_TEST_BASIC_NAME, result,
    )


def validate_gate3_local_arch_selection(result: ValidationResult) -> None:
    """Validate local Gate 3 smoke selects architecture-matched packages."""
    content = read_safe(GATE3_LOCAL_PACKAGE_SMOKE)
    if not content:
        result.fail("gate3-arch:exists", "gate3_local_package_smoke.sh not found")
        return
    _check_snippets(
        content, GATE3_LOCAL_ARCH_SNIPPETS, "gate3-arch",
        "gate3_local_package_smoke.sh", result,
    )


def validate_nfpm_postinstall_lifecycle(result: ValidationResult) -> None:
    """Validate nFPM postinstall accepts DEB and RPM lifecycle arguments."""
    content = read_safe(NFPM_POSTINSTALL)
    if not content:
        result.fail("nfpm-postinstall:exists", f"{NFPM_POSTINSTALL_NAME} not found")
        return
    _check_snippets(
        content, NFPM_POSTINSTALL_SNIPPETS, "nfpm-postinstall",
        NFPM_POSTINSTALL_NAME, result,
    )
    for snippet in NFPM_POSTINSTALL_FORBIDDEN_SNIPPETS:
        sid = f"nfpm-postinstall-forbidden:{snippet[:24]}"
        if snippet in content:
            result.fail(sid, f"{NFPM_POSTINSTALL_NAME} must not contain {snippet}")
        else:
            result.pass_(sid, f"{NFPM_POSTINSTALL_NAME} omits {snippet}")


def validate_release_build_glibc_baseline(result: ValidationResult) -> None:
    """Validate release builds use the supported RPM glibc baseline."""
    for path, snippets in RELEASE_BUILD_GLIBC_SNIPPETS.items():
        content = read_safe(path)
        rel = path.relative_to(PROJECT_ROOT)
        if not content:
            result.fail(f"glibc-baseline:exists:{rel}", f"{rel} not found")
            continue
        _check_snippets(
            content, snippets, "glibc-baseline",
            str(rel), result,
        )


def validate_release_rust_build_invariants(result: ValidationResult) -> None:
    """Validate release workflow contains Rust target/feature/symbol invariants.

    Prevents regressions if the release workflow is refactored: the workflow
    must set RUST_TARGET, cross-compile with the correct triple, and verify
    that streaming/incremental/decompression FFI symbols are exported.
    """
    content = read_safe(RELEASE_PACKAGES_WORKFLOW)
    if not content:
        result.fail(
            "rust-build-invariant:exists",
            "release-packages.yml not found",
        )
        return
    for snippet in RELEASE_RUST_BUILD_INVARIANTS:
        sid = f"rust-build-invariant:{snippet[:28]}"
        if snippet in content:
            result.pass_(sid, f"release-packages.yml contains {snippet}")
        else:
            result.fail(sid, f"release-packages.yml missing {snippet}")


def validate_package_installation_docs(result: ValidationResult) -> None:
    """Validate public install docs match the current package channel state."""
    for path, snippets in PACKAGE_DOC_REQUIRED_SNIPPETS.items():
        rel = path.relative_to(PROJECT_ROOT)
        content = read_safe(path)
        if not content:
            result.fail(f"package-docs:exists:{rel}", f"{rel} not found")
            continue

        _check_snippets(content, snippets, f"package-docs:{rel}", str(rel),
                        result)

        for idx, snippet in enumerate(PACKAGE_DOC_FORBIDDEN_SNIPPETS):
            sid = f"package-docs:no-repo-install:{rel}:{idx}"
            if snippet in content:
                result.fail(
                    sid,
                    f"{rel} advertises unpublished repository install: {snippet}",
                )
            else:
                result.pass_(
                    sid,
                    f"{rel} omits unpublished repository install {snippet}",
                )


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
    validate_nginx_dependency_constraints(result)
    validate_module_filename_consistency(result)
    validate_release_versions_have_checksums(result)
    validate_release_artifact_flow(result)
    validate_standalone_workflow_packaging(result)
    validate_smoke_test_repo_selection(result)
    validate_gate3_local_arch_selection(result)
    validate_nfpm_postinstall_lifecycle(result)
    validate_release_build_glibc_baseline(result)
    validate_release_rust_build_invariants(result)
    validate_package_installation_docs(result)
    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
