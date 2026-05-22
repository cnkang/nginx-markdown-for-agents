#!/usr/bin/env python3
"""
Fuzz and packaging infrastructure validator for v0.7.0 release gates.

Validates the 12-item checklist from spec 33 requirements (Requirement 2):

1. Fuzz targets exist (fuzz/Cargo.toml lists targets)
2. ClusterFuzzLite PR workflow exists (.github/workflows/cflite_pr.yml)
3. Nightly batch fuzz workflow exists (.github/workflows/cflite_batch.yml)
4. Corpus pruning mechanism exists (.github/workflows/cflite_cron.yml)
5. Fuzz guide document is complete (fuzz/README.md has required sections)
6. Release package workflow exists (.github/workflows/release-packages.yml)
7. .deb/.rpm artifact naming includes NGINX target version (nFPM config +
   workflow both reference NGINX_VERSION in filename construction)
8. SHA256SUMS generation logic exists in release workflow
9. Install/compatibility documentation exists
10. Package smoke test job exists in release workflow
11. Harness rules FUZZ-001 through FUZZ-007 defined in fuzz/README.md

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

# Workflow paths
CFLITE_PR_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "cflite_pr.yml"
CFLITE_BATCH_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "cflite_batch.yml"
CFLITE_CRON_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "cflite_cron.yml"
RELEASE_PACKAGES_WORKFLOW = (
    PROJECT_ROOT / ".github" / "workflows" / "release-packages.yml"
)

# Fuzz paths
FUZZ_README = PROJECT_ROOT / "fuzz" / "README.md"
FUZZ_CARGO_TOML = PROJECT_ROOT / "components" / "rust-converter" / "fuzz" / "Cargo.toml"

# Packaging paths
NFPM_CONFIG = PROJECT_ROOT / "packaging" / "nfpm" / "nfpm.yaml"

# Documentation paths
INSTALL_DOCS = [
    PROJECT_ROOT / "docs" / "guides" / "INSTALLATION.md",
    PROJECT_ROOT / "docs" / "guides" / "PACKAGE_INSTALLATION.md",
]
COMPAT_DOCS = [
    PROJECT_ROOT / "docs" / "guides" / "NGINX_COMPATIBILITY.md",
    PROJECT_ROOT / "docs" / "features" / "NGINX_COMPATIBILITY.md",
    PROJECT_ROOT / "docs" / "guides" / "COMPATIBILITY.md",
]

# Required fuzz guide sections (keywords that must appear)
FUZZ_GUIDE_REQUIRED_KEYWORDS = [
    "FUZZ-001",
    "FUZZ-002",
    "FUZZ-003",
    "FUZZ-004",
    "FUZZ-005",
    "FUZZ-006",
    "FUZZ-007",
]


class ValidationResult:
    """Accumulates PASS/FAIL results for reporting."""

    def __init__(self) -> None:
        self.results: list[tuple[str, str, str]] = []

    def pass_(self, check_id: str, message: str) -> None:
        self.results.append(("PASS", check_id, message))

    def fail(self, check_id: str, message: str) -> None:
        self.results.append(("FAIL", check_id, message))

    def skip(self, check_id: str, message: str) -> None:
        self.results.append(("SKIP", check_id, message))

    @property
    def has_failures(self) -> bool:
        return any(s == "FAIL" for s, _, _ in self.results)


def read_safe(path: Path) -> str:
    """Read file content safely, returning empty string if missing."""
    resolved = path.resolve()
    if not str(resolved).startswith(str(PROJECT_ROOT)):
        return ""
    return resolved.read_text(encoding="utf-8") if resolved.is_file() else ""


def check_fuzz_targets(result: ValidationResult) -> None:
    """Validate that fuzz targets are defined in fuzz/Cargo.toml."""
    content = read_safe(FUZZ_CARGO_TOML)
    if not content:
        result.fail("fuzz:targets-exist", "fuzz/Cargo.toml not found")
        return

    # Check for [[bin]] sections which define fuzz targets
    if "[[bin]]" in content:
        # Count targets
        target_count = content.count("[[bin]]")
        result.pass_(
            "fuzz:targets-exist",
            f"fuzz/Cargo.toml defines {target_count} fuzz target(s)",
        )
    else:
        result.fail("fuzz:targets-exist", "no [[bin]] targets in fuzz/Cargo.toml")


def check_cflite_workflows(result: ValidationResult) -> None:
    """Validate ClusterFuzzLite workflow files exist."""
    # PR workflow (Req 2.3)
    if CFLITE_PR_WORKFLOW.is_file():
        result.pass_("fuzz:cflite-pr-workflow", "cflite_pr.yml exists")
    else:
        result.fail("fuzz:cflite-pr-workflow", "cflite_pr.yml not found")

    # Batch workflow (Req 2.4)
    if CFLITE_BATCH_WORKFLOW.is_file():
        result.pass_("fuzz:cflite-batch-workflow", "cflite_batch.yml exists")
    else:
        result.fail("fuzz:cflite-batch-workflow", "cflite_batch.yml not found")

    # Corpus pruning / cron workflow (Req 2.5)
    if CFLITE_CRON_WORKFLOW.is_file():
        content = read_safe(CFLITE_CRON_WORKFLOW)
        if "prune" in content.lower():
            result.pass_(
                "fuzz:corpus-pruning",
                "cflite_cron.yml exists with pruning mode",
            )
        else:
            result.fail(
                "fuzz:corpus-pruning",
                "cflite_cron.yml exists but missing prune mode",
            )
    else:
        result.fail("fuzz:corpus-pruning", "cflite_cron.yml not found")


def check_fuzz_guide(result: ValidationResult) -> None:
    """Validate fuzz guide document completeness (Req 2.6)."""
    content = read_safe(FUZZ_README)
    if not content:
        result.fail("fuzz:guide-exists", "fuzz/README.md not found")
        return
    result.pass_("fuzz:guide-exists", "fuzz/README.md exists")

    # Check for required harness rule references
    missing_rules = []
    for keyword in FUZZ_GUIDE_REQUIRED_KEYWORDS:
        if keyword not in content:
            missing_rules.append(keyword)

    if missing_rules:
        result.fail(
            "fuzz:guide-rules",
            f"fuzz/README.md missing rules: {', '.join(missing_rules)}",
        )
    else:
        result.pass_(
            "fuzz:guide-rules",
            "fuzz/README.md contains all FUZZ-001..007 rules",
        )


def check_release_workflow(result: ValidationResult) -> None:
    """Validate release package workflow (Req 2.7, 2.9, 2.11)."""
    content = read_safe(RELEASE_PACKAGES_WORKFLOW)
    if not content:
        result.fail("pkg:release-workflow", "release-packages.yml not found")
        return
    result.pass_("pkg:release-workflow", "release-packages.yml exists")

    # SHA256SUMS generation (Req 2.9)
    if re.search(r"SHA256SUMS|generate-checksums|sha256sum", content):
        result.pass_("pkg:sha256sums", "SHA256SUMS generation logic found")
    else:
        result.fail("pkg:sha256sums", "no SHA256SUMS generation logic")

    # Package smoke test job (Req 2.11)
    if re.search(r"smoke[-_]test", content):
        result.pass_("pkg:smoke-test-job", "smoke test job found in workflow")
    else:
        result.fail("pkg:smoke-test-job", "no smoke test job in workflow")


def check_artifact_naming(result: ValidationResult) -> None:
    """Validate artifact naming includes NGINX target version (Req 2.8).

    Checks both the nFPM config template and the release workflow to ensure
    NGINX_VERSION is incorporated into the artifact filename.
    """
    # Check nFPM config references NGINX_VERSION
    nfpm_content = read_safe(NFPM_CONFIG)
    if not nfpm_content:
        result.fail("pkg:nfpm-config", "packaging/nfpm/nfpm.yaml not found")
    else:
        if "NGINX_VERSION" in nfpm_content or "nginx_version" in nfpm_content:
            result.pass_(
                "pkg:nfpm-config",
                "nFPM config references NGINX_VERSION",
            )
        else:
            result.fail(
                "pkg:nfpm-config",
                "nFPM config does not reference NGINX_VERSION",
            )

    # Check workflow constructs filenames with NGINX version
    wf_content = read_safe(RELEASE_PACKAGES_WORKFLOW)
    if not wf_content:
        result.skip(
            "pkg:artifact-naming-workflow",
            "release-packages.yml not found (checked separately)",
        )
        return

    # Look for filename patterns that include nginx version
    has_deb_naming = bool(
        re.search(r"nginx-.*\$\{?NGINX_VERSION", wf_content)
        or re.search(r"nginx-\$\{\{.*nginx_version", wf_content)
    )
    has_rpm_naming = bool(
        re.search(r"nginx\$\{?NGINX_VERSION", wf_content)
        or re.search(r"nginx\$\{\{.*nginx_version", wf_content)
    )

    if has_deb_naming and has_rpm_naming:
        result.pass_(
            "pkg:artifact-naming-workflow",
            "workflow constructs .deb/.rpm filenames with NGINX version",
        )
    elif has_deb_naming or has_rpm_naming:
        result.pass_(
            "pkg:artifact-naming-workflow",
            "workflow includes NGINX version in artifact naming",
        )
    else:
        result.fail(
            "pkg:artifact-naming-workflow",
            "workflow does not include NGINX version in artifact filenames",
        )


def check_install_docs(result: ValidationResult) -> None:
    """Validate install/compatibility documentation exists (Req 2.10)."""
    install_found = any(p.is_file() for p in INSTALL_DOCS)
    if install_found:
        result.pass_("docs:install", "installation documentation exists")
    else:
        result.fail(
            "docs:install",
            "no installation documentation found at expected paths",
        )

    compat_found = any(p.is_file() for p in COMPAT_DOCS)
    if compat_found:
        result.pass_("docs:compatibility", "compatibility documentation exists")
    else:
        # Check if compatibility info is in the install doc
        for p in INSTALL_DOCS:
            content = read_safe(p)
            if content and re.search(
                r"compat|nginx.*version|--with-compat", content, re.IGNORECASE
            ):
                result.pass_(
                    "docs:compatibility",
                    "compatibility info found in installation docs",
                )
                return
        result.fail(
            "docs:compatibility",
            "no compatibility documentation found",
        )


def print_report(result: ValidationResult) -> None:
    """Print a formatted validation report."""
    print("v0.7.0 Fuzz & Packaging Infrastructure Validation Report")
    print("=" * 60)
    for status, check_id, message in result.results:
        print(f"  {status:4s}  {check_id:35s}  {message}")
    print()
    p = sum(s == "PASS" for s, _, _ in result.results)
    f = sum(s == "FAIL" for s, _, _ in result.results)
    k = sum(s == "SKIP" for s, _, _ in result.results)
    print(f"Summary: {p} passed, {f} failed, {k} skipped")


def main() -> int:
    """CLI entry point for fuzz and packaging infrastructure validation."""
    result = ValidationResult()

    check_fuzz_targets(result)
    check_cflite_workflows(result)
    check_fuzz_guide(result)
    check_release_workflow(result)
    check_artifact_naming(result)
    check_install_docs(result)

    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
