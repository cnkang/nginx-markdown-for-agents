"""Shared constants for packaging documentation validators.

Centralises heading labels and patterns so that minor documentation
refactors only require updating one file.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Installation guide section heading patterns (regex, for ## level)
# ---------------------------------------------------------------------------

INSTALL_SECTION_PATTERNS: list[str] = [
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

# ---------------------------------------------------------------------------
# Troubleshooting SOP headings (exact text after ####)
# ---------------------------------------------------------------------------

SOP_HEADINGS: list[str] = [
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

# SOPs 1-6 must declare a Category matching the install script's error
# categories (Requirement 6.3).
SOP_EXPECTED_CATEGORIES: dict[str, str] = {
    "SOP 1: Module Not Loaded": "config",
    "SOP 2: NGINX Version / ABI Mismatch": "version_mismatch",
    "SOP 3: Architecture Not Supported": "arch_unsupported",
    "SOP 4: libc Incompatibility": "config",
    "SOP 5: Network Download Failure": "network",
    "SOP 6: Checksum Verification Failure": "checksum",
}

# ---------------------------------------------------------------------------
# Install path tier sections (pattern, expected tier label)
# ---------------------------------------------------------------------------

TIER_SECTIONS: list[tuple[str, str]] = [
    (r"4\.\s+Primary", "Primary"),
    (r"5\.\s+Secondary.*Docker", "Secondary"),
    (r"6\.\s+Secondary.*Manual", "Secondary"),
]

# ---------------------------------------------------------------------------
# Module states for Operator Verification
# ---------------------------------------------------------------------------

MODULE_STATES: list[str] = [
    "installation successful",
    "module loaded",
    "conversion pipeline hit",
    "policy passed but fail-open",
]

# ---------------------------------------------------------------------------
# Environment labels for Environment-Specific Notes
# ---------------------------------------------------------------------------

ENVIRONMENTS: list[tuple[str, str]] = [
    ("bare-metal", "glibc"),
    ("alpine", "musl"),
    ("docker", "container"),
    ("macos", "macos"),
]

# ---------------------------------------------------------------------------
# Demo config directives requiring inline comments
# ---------------------------------------------------------------------------

DEMO_DIRECTIVES_REQUIRING_COMMENTS: list[str] = [
    "markdown_filter",
    "markdown_max_size",
    "markdown_timeout",
    "markdown_on_error",
]

# ---------------------------------------------------------------------------
# README / Installation guide heading labels
# ---------------------------------------------------------------------------

README_QUICK_START_HEADING = "## Quick Start"
INSTALL_SHORTEST_PATH_PATTERN = r"^## 2\.\s+Shortest Success Path"
