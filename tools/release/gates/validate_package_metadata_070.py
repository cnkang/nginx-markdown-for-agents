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

NFPM_REQUIRED_SNIPPETS = [
    'name: "nginx-module-markdown-for-agents"',
    'version: "${PKG_VERSION}"',
    'arch: "${NFPM_ARCH}"',
    'nginx (>= ${NGINX_VERSION})',
    "/usr/lib/nginx/modules/ngx_http_markdown_module.so",
    "/usr/share/doc/nginx-markdown-for-agents/README.md",
    "/usr/share/doc/nginx-markdown-for-agents/INSTALL.md",
    "/usr/share/doc/nginx-markdown-for-agents/COMPATIBILITY.md",
    "/usr/share/licenses/nginx-markdown-for-agents/LICENSE",
]
RPM_REQUIRED_FIELDS = ["Name", "Version", "Requires"]
RPM_REQUIRED_SECTIONS = ["%post", "%changelog"]


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
    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
