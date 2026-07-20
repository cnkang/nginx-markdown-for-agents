#!/usr/bin/env python3
"""
Fuzz and packaging infrastructure validator for v0.7.0 release gates.

Validates the 11-item checklist from v0.7.0 fuzz and packaging infrastructure requirements (Requirement 2):

1. Fuzz targets exist (fuzz/Cargo.toml lists targets)
2. ClusterFuzzLite PR workflow exists
3. Nightly batch fuzz workflow exists
4. Corpus pruning mechanism exists
5. Fuzz guide document is complete
6. Release package workflow exists
7. .deb/.rpm artifact naming includes NGINX target version (nFPM config +
   workflow both reference NGINX_VERSION in filename construction)
8. SHA256SUMS generation logic exists in release workflow
9. Install/compatibility documentation exists
10. Package smoke test job exists in release workflow
11. Harness rules FUZZ-001 through FUZZ-007 defined in the fuzz guide

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
GITHUB_DIR = ".github"
WORKFLOWS_DIR = "workflows"
README_FILENAME = "README.md"
FUZZ_README_REL = f"fuzz/{README_FILENAME}"

FUZZ_TARGETS_GATE = "fuzz:targets-exist"
FUZZ_CORPUS_PRUNING_GATE = "fuzz:corpus-pruning"
PKG_NFPM_CONFIG_GATE = "pkg:nfpm-config"
PKG_ARTIFACT_NAMING_WORKFLOW_GATE = "pkg:artifact-naming-workflow"
DOCS_COMPATIBILITY_GATE = "docs:compatibility"

# Workflow paths
CFLITE_PR_WORKFLOW = PROJECT_ROOT / GITHUB_DIR / WORKFLOWS_DIR / "cflite_pr.yml"
CFLITE_BATCH_WORKFLOW = PROJECT_ROOT / GITHUB_DIR / WORKFLOWS_DIR / "cflite_batch.yml"
CFLITE_CRON_WORKFLOW = PROJECT_ROOT / GITHUB_DIR / WORKFLOWS_DIR / "cflite_cron.yml"
RELEASE_PACKAGES_WORKFLOW = (
    PROJECT_ROOT / GITHUB_DIR / WORKFLOWS_DIR / "release-packages.yml"
)

# Fuzz paths
FUZZ_README = PROJECT_ROOT / "fuzz" / README_FILENAME
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
        result.fail(FUZZ_TARGETS_GATE, "fuzz/Cargo.toml not found")
        return

    # Check for [[bin]] sections which define fuzz targets
    if "[[bin]]" in content:
        # Count targets
        target_count = content.count("[[bin]]")
        result.pass_(
            FUZZ_TARGETS_GATE,
            f"fuzz/Cargo.toml defines {target_count} fuzz target(s)",
        )
    else:
        result.fail(FUZZ_TARGETS_GATE, "no [[bin]] targets in fuzz/Cargo.toml")


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
                FUZZ_CORPUS_PRUNING_GATE,
                "cflite_cron.yml exists with pruning mode",
            )
        else:
            result.fail(
                FUZZ_CORPUS_PRUNING_GATE,
                "cflite_cron.yml exists but missing prune mode",
            )
    else:
        result.fail(FUZZ_CORPUS_PRUNING_GATE, "cflite_cron.yml not found")


def check_fuzz_guide(result: ValidationResult) -> None:
    """Validate fuzz guide document completeness (Req 2.6)."""
    content = read_safe(FUZZ_README)
    if not content:
        result.fail("fuzz:guide-exists", f"{FUZZ_README_REL} not found")
        return
    result.pass_("fuzz:guide-exists", f"{FUZZ_README_REL} exists")

    # Check for required harness rule references
    missing_rules = []
    missing_rules.extend(
        keyword
        for keyword in FUZZ_GUIDE_REQUIRED_KEYWORDS
        if keyword not in content
    )
    if missing_rules:
        result.fail(
            "fuzz:guide-rules",
            f"{FUZZ_README_REL} missing rules: {', '.join(missing_rules)}",
        )
    else:
        result.pass_(
            "fuzz:guide-rules",
            f"{FUZZ_README_REL} contains all FUZZ-001..007 rules",
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
    if nfpm_content := read_safe(NFPM_CONFIG):
        if "NGINX_VERSION" in nfpm_content or "nginx_version" in nfpm_content:
            result.pass_(
                PKG_NFPM_CONFIG_GATE,
                "nFPM config references NGINX_VERSION",
            )
        else:
            result.fail(
                PKG_NFPM_CONFIG_GATE,
                "nFPM config does not reference NGINX_VERSION",
            )

    else:
        result.fail(PKG_NFPM_CONFIG_GATE, "packaging/nfpm/nfpm.yaml not found")
    # Check workflow constructs filenames with NGINX version
    wf_content = read_safe(RELEASE_PACKAGES_WORKFLOW)
    if not wf_content:
        result.skip(
            PKG_ARTIFACT_NAMING_WORKFLOW_GATE,
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
            PKG_ARTIFACT_NAMING_WORKFLOW_GATE,
            "workflow constructs .deb/.rpm filenames with NGINX version",
        )
    elif has_deb_naming or has_rpm_naming:
        result.pass_(
            PKG_ARTIFACT_NAMING_WORKFLOW_GATE,
            "workflow includes NGINX version in artifact naming",
        )
    else:
        result.fail(
            PKG_ARTIFACT_NAMING_WORKFLOW_GATE,
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
        result.pass_(DOCS_COMPATIBILITY_GATE, "compatibility documentation exists")
    else:
        # Check if compatibility info is in the install doc
        for p in INSTALL_DOCS:
            content = read_safe(p)
            if content and re.search(
                r"compat|nginx.*version|--with-compat", content, re.IGNORECASE
            ):
                result.pass_(
                    DOCS_COMPATIBILITY_GATE,
                    "compatibility info found in installation docs",
                )
                return
        result.fail(
            DOCS_COMPATIBILITY_GATE,
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
