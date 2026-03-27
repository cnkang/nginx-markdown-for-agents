#!/usr/bin/env python3
"""Packaging documentation structure and content validator.

Validates that the installation guide (docs/guides/INSTALLATION.md) and
supporting files meet the required structure and content expectations for
the packaging-first-run documentation spec.

Checks performed:
  1.  All 11 required sections present by heading
  2.  Shortest Success Path has <= 4 shell commands
  3.  All 9 troubleshooting SOPs with symptom/root-cause/resolution
  4.  Installation method sections contain tier labels
  5.  Operator Verification describes four module states
  6.  Compatibility matrix references tools/release-matrix.json
  7.  Release artifact naming convention and exact version match
  8.  Environment-specific notes cover four environments
  9.  Installation guide references install-verify.yml CI workflow
  10. Installation guide documents SKIP_ROOT_CHECK=1
  11. Fail-open explanation references markdown_on_error pass
  12. Compression SOP references proxy_set_header Accept-Encoding ""
  13. Content negotiation SOP explains eligibility requirements
  14. Minimal demo config exists
  15. Demo config has inline comments and no proxy_pass directive
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALL_GUIDE = ROOT / "docs" / "guides" / "INSTALLATION.md"
DEMO_CONFIG = ROOT / "examples" / "nginx-configs" / "00-minimal-demo.conf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _section_text(full_text: str, heading_pattern: str) -> str:
    """Return the text of a ## section whose heading matches *heading_pattern*."""
    pattern = rf"(^## {heading_pattern}.*?)(?=(?:^## )|\Z)"
    m = re.search(pattern, full_text, re.MULTILINE | re.DOTALL)
    return m[1] if m else ""


def _sop_section_text(full_text: str, sop_heading: str) -> str:
    """Return the text of a #### SOP section."""
    pattern = rf"(^#### {re.escape(sop_heading)}.*?)(?=(?:^#### )|(?:\n---\n)|\Z)"
    m = re.search(pattern, full_text, re.MULTILINE | re.DOTALL)
    return m[1] if m else ""


def _count_shell_commands(section: str) -> int:
    """Count non-comment, non-empty lines inside ```bash fenced blocks."""
    count = 0
    in_bash = False
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("```bash"):
            in_bash = True
            continue
        if stripped.startswith("```") and in_bash:
            in_bash = False
            continue
        if in_bash and stripped and not stripped.startswith("#"):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Individual checks — each returns a list of error strings
# ---------------------------------------------------------------------------

REQUIRED_SECTIONS = [
    r"1\.\s+Overview",
    r"2\.\s+Shortest Success Path",
    r"3\.\s+Install Path Tiers",
    r"4\.\s+Primary.*Install Script",
    r"5\.\s+Secondary.*Docker Source Build",
    r"6\.\s+Secondary.*Manual Source Build",
    r"7\.\s+Compatibility Matrix",
    r"8\.\s+Release Artifact Naming",
    r"9\.\s+Operator Verification",
    r"10\.\s+Troubleshooting",
    r"11\.\s+Environment-Specific Notes",
]


def check_required_sections(text: str) -> list[str]:
    """Check 1: All 11 required sections present."""
    errors: list[str] = []
    errors.extend(
        f"Missing required section matching '## {pattern}'"
        for pattern in REQUIRED_SECTIONS
        if not re.search(rf"^## {pattern}", text, re.MULTILINE)
    )
    return errors


def check_shortest_success_path(text: str) -> list[str]:
    """Check 2: Shortest Success Path has <= 4 shell commands."""
    section = _section_text(text, r"2\.")
    if not section:
        return ["Cannot locate '## 2. Shortest Success Path' section"]
    count = _count_shell_commands(section)
    if count > 4:
        return [f"Shortest Success Path has {count} shell commands (max 4)"]
    return []


SOP_HEADINGS = [
    "SOP 1: Module Not Loaded",
    "SOP 2: NGINX Version / ABI Mismatch",
    "SOP 3: Architecture Not Supported",
    "SOP 4: libc Incompatibility",
    "SOP 5: Network Download Failure",
    "SOP 6: Checksum Verification Failure",
    "SOP 7: Content Negotiation Not Triggering",
    "SOP 8: Upstream Response Not Eligible",
    "SOP 9: Compression / Decompression Issues",
]


def check_troubleshooting_sops(text: str) -> list[str]:
    """Check 3: All 9 SOPs present with symptom/root-cause/resolution."""
    errors: list[str] = []
    for heading in SOP_HEADINGS:
        sop = _sop_section_text(text, heading)
        if not sop:
            errors.append(f"Missing troubleshooting '{heading}'")
            continue
        sop_lower = sop.lower()
        if "**symptom" not in sop_lower and "symptom:" not in sop_lower:
            errors.append(f"'{heading}' missing Symptom subsection")
        if "**root cause" not in sop_lower and "root cause:" not in sop_lower:
            errors.append(f"'{heading}' missing Root Cause subsection")
        if "**resolution" not in sop_lower and "resolution" not in sop_lower:
            errors.append(f"'{heading}' missing Resolution subsection")
    return errors


TIER_SECTIONS = [
    (r"4\.\s+Primary", "Primary"),
    (r"5\.\s+Secondary.*Docker", "Secondary"),
    (r"6\.\s+Secondary.*Manual", "Secondary"),
]


def check_tier_labels(text: str) -> list[str]:
    """Check 4: Each installation method section contains the correct tier label."""
    errors: list[str] = []
    for pattern, expected_tier in TIER_SECTIONS:
        section = _section_text(text, pattern)
        if not section:
            errors.append(f"Cannot locate section matching '## {pattern}'")
            continue
        tier_re = re.compile(r"\*\*Tier:\s*(Primary|Secondary|Convenience)\*\*", re.IGNORECASE)
        m = tier_re.search(section)
        if not m:
            errors.append(
                f"Section '## {pattern}' missing tier label "
                f"(expected '**Tier: {expected_tier}**')"
            )
        elif m[1].lower() != expected_tier.lower():
            errors.append(
                f"Section '## {pattern}' has tier label '{m[1]}' but expected '{expected_tier}'"
            )
    return errors


MODULE_STATES = [
    "installation successful",
    "module loaded",
    "conversion pipeline hit",
    "policy passed but fail-open",
]


def check_operator_verification(text: str) -> list[str]:
    """Check 5: Operator Verification describes all four module states."""
    section = _section_text(text, r"9\.\s+Operator Verification")
    if not section:
        return ["Cannot locate '## 9. Operator Verification' section"]
    section_lower = section.lower()
    errors: list[str] = [
        f"Operator Verification missing module state: '{state}'"
        for state in MODULE_STATES
        if state.lower() not in section_lower
    ]
    return errors


def check_compatibility_matrix(text: str) -> list[str]:
    """Check 6: Compatibility matrix exists and references release-matrix.json."""
    section = _section_text(text, r"7\.\s+Compatibility Matrix")
    if not section:
        return ["Cannot locate '## 7. Compatibility Matrix' section"]
    errors: list[str] = []
    if "release-matrix.json" not in section:
        errors.append(
            "Compatibility Matrix section does not reference "
            "'tools/release-matrix.json'"
        )
    # Check that a markdown table exists (header row with pipes)
    if not re.search(r"\|.*NGINX.*\|.*OS.*\|", section, re.IGNORECASE):
        errors.append("Compatibility Matrix section missing the platform table")
    return errors


def check_release_artifact_naming(text: str) -> list[str]:
    """Check 7: Release artifact naming convention and exact version match."""
    section = _section_text(text, r"8\.\s+Release Artifact Naming")
    if not section:
        return ["Cannot locate '## 8. Release Artifact Naming' section"]
    errors: list[str] = []
    # Naming convention pattern
    if "ngx_http_markdown_filter_module-" not in section:
        errors.append(
            "Release Artifact Naming section missing naming convention pattern"
        )
    # Exact version match requirement
    section_lower = section.lower()
    if "exact version match" not in section_lower:
        errors.append(
            "Release Artifact Naming section missing exact version match requirement"
        )
    return errors


ENVIRONMENTS = [
    ("bare-metal", "glibc"),
    ("alpine", "musl"),
    ("docker", "container"),
    ("macos", "macos"),
]


def check_environment_notes(text: str) -> list[str]:
    """Check 8: Environment-specific notes cover all four environments."""
    section = _section_text(text, r"11\.\s+Environment-Specific Notes")
    if not section:
        return ["Cannot locate '## 11. Environment-Specific Notes' section"]
    section_lower = section.lower()
    errors: list[str] = [
        f"Environment-Specific Notes missing coverage for '{label}'"
        for label, keyword in ENVIRONMENTS
        if label not in section_lower and keyword not in section_lower
    ]
    return errors


def check_ci_workflow_reference(text: str) -> list[str]:
    """Check 9: Installation guide references install-verify.yml."""
    if "install-verify.yml" not in text:
        return ["Installation guide does not reference 'install-verify.yml' CI workflow"]
    return []


def check_skip_root_check(text: str) -> list[str]:
    """Check 10: Installation guide documents SKIP_ROOT_CHECK=1."""
    if "SKIP_ROOT_CHECK=1" not in text:
        return ["Installation guide does not document 'SKIP_ROOT_CHECK=1'"]
    return []


def check_fail_open_reference(text: str) -> list[str]:
    """Check 11: Fail-open explanation references markdown_on_error pass."""
    if "markdown_on_error pass" not in text:
        return [
            "Installation guide does not reference 'markdown_on_error pass' "
            "in fail-open explanation"
        ]
    return []


def check_compression_sop(text: str) -> list[str]:
    """Check 12: Compression SOP references proxy_set_header Accept-Encoding."""
    if sop := _sop_section_text(
        text, "SOP 9: Compression / Decompression Issues"
    ):
        return (
            [
                "Compression SOP does not reference "
                "'proxy_set_header Accept-Encoding \"\"'"
            ]
            if 'proxy_set_header Accept-Encoding ""' not in sop
            else []
        )
    else:
        return ["Cannot locate SOP 9 (Compression / Decompression Issues)"]


def check_content_negotiation_sop(text: str) -> list[str]:
    """Check 13: Content negotiation SOP explains eligibility requirements."""
    sop = _sop_section_text(text, "SOP 7: Content Negotiation Not Triggering")
    if not sop:
        return ["Cannot locate SOP 7 (Content Negotiation Not Triggering)"]
    sop_lower = sop.lower()
    if "eligib" not in sop_lower:
        return [
            "Content Negotiation SOP does not explain eligibility requirements"
        ]
    return []


def check_demo_config_exists() -> list[str]:
    """Check 14: Minimal demo config exists."""
    if not DEMO_CONFIG.exists():
        return [
            f"Minimal demo config not found at "
            f"'{DEMO_CONFIG.relative_to(ROOT)}'"
        ]
    return []


DEMO_DIRECTIVES_REQUIRING_COMMENTS = [
    "markdown_filter",
    "markdown_max_size",
    "markdown_timeout",
    "markdown_on_error",
]


def _check_directive_comments(lines: list[str], directive: str) -> list[str]:
    """Verify *directive* appears in *lines* with an inline or preceding comment."""
    errors: list[str] = []
    found = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if not re.search(rf"\b{re.escape(directive)}\b", stripped):
            continue
        found = True
        has_inline = "#" in stripped.split(directive, 1)[-1]
        has_preceding = idx > 0 and lines[idx - 1].strip().startswith("#")
        if not has_inline and not has_preceding:
            errors.append(
                f"Demo config directive '{directive}' has no inline or "
                f"preceding comment"
            )
    if not found:
        errors.append(f"Demo config missing directive '{directive}'")
    return errors


def check_demo_config_content() -> list[str]:
    """Check 15: Demo config has inline comments for key directives and no proxy_pass."""
    if not DEMO_CONFIG.exists():
        return ["Cannot check demo config content — file does not exist"]
    content = _read_text(DEMO_CONFIG)
    errors: list[str] = []
    if not re.search(r"^\s*#", content, re.MULTILINE):
        errors.append("Demo config has no inline comments")
    if "proxy_pass" in content:
        errors.append("Demo config contains a 'proxy_pass' directive (should not)")
    lines = content.splitlines()
    for directive in DEMO_DIRECTIVES_REQUIRING_COMMENTS:
        errors.extend(_check_directive_comments(lines, directive))
    return errors


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not INSTALL_GUIDE.exists():
        print(f"ERROR: Installation guide not found at {INSTALL_GUIDE}")
        return 1

    text = _read_text(INSTALL_GUIDE)
    errors: list[str] = []

    checks = [
        ("Required sections (11)", check_required_sections(text)),
        ("Shortest Success Path command count", check_shortest_success_path(text)),
        ("Troubleshooting SOPs (9)", check_troubleshooting_sops(text)),
        ("Tier labels on install methods", check_tier_labels(text)),
        ("Operator Verification module states", check_operator_verification(text)),
        ("Compatibility matrix", check_compatibility_matrix(text)),
        ("Release artifact naming", check_release_artifact_naming(text)),
        ("Environment-specific notes", check_environment_notes(text)),
        ("CI workflow reference", check_ci_workflow_reference(text)),
        ("SKIP_ROOT_CHECK=1 documented", check_skip_root_check(text)),
        ("Fail-open reference", check_fail_open_reference(text)),
        ("Compression SOP reference", check_compression_sop(text)),
        ("Content negotiation SOP", check_content_negotiation_sop(text)),
        ("Demo config exists", check_demo_config_exists()),
        ("Demo config content", check_demo_config_content()),
    ]

    for _label, errs in checks:
        errors.extend(errs)

    if errors:
        print("Packaging documentation checks FAILED:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("Packaging documentation checks passed:")
    for label, _ in checks:
        print(f"  - {label}: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
