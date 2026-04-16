#!/usr/bin/env python3
"""
0.5.0 release gate document structure validation.

Validates governance artifacts defined in spec #12:
- Sub-spec document existence (Property 2)
- Requirements document required sections (Property 1)
- Boundary description existence and fields (Property 3)
- DoD assessment completeness (Property 5)
- Risk register format and high-severity mitigation (Property 7)
- Compatibility matrix valid states (Property 8)
- Checklist item verification references (Property 16)
- Test matrix dimension coverage (Property 6)

Two validation modes:
- framework (default): validates only spec #12's own governance documents
  (templates, matrix, checklist, naming doc). This mode passes when the
  framework itself is correctly delivered.
- strict: validates all sub-specs (#12-#18) for section completeness,
  boundary descriptions, DoD, and risk registers. Use this mode when all
  sub-specs are expected to be fully compliant.

Security: File paths are validated against an allow-list of expected
directories. No user-supplied paths are used for file operations without
validation (path traversal prevention).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Resolve project root relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent

# Default sub-spec directories under .kiro/specs/
DEFAULT_SPECS_DIR = PROJECT_ROOT / ".kiro" / "specs"

# 0.5.0 sub-spec directory names (12-18)
SUBSPECS_050 = [
    "12-overall-scope-release-gates-0-5-0",
    "13-rust-streaming-engine-core",
    "14-nginx-streaming-runtime-and-ffi",
    "15-streaming-failure-cache-semantics",
    "16-streaming-parity-diff-testing",
    "17-streaming-rollout-observability",
    "18-streaming-performance-evidence-and-release",
]

# Spec #12 only (for framework mode)
SPEC12_ONLY = ["12-overall-scope-release-gates-0-5-0"]

# Compatibility matrix valid states
VALID_STATES = frozenset(
    {"streaming-supported", "full-buffer-only", "pre-commit-fallback-only"}
)

# Canonical capability rows from Requirements 9.1
CANONICAL_CAPABILITIES = [
    "automatic decompression",
    "charset detection / transcoding",
    "security sanitization",
    "deterministic output",
    "markdown_timeout",
    "markdown_max_size",
    "markdown_token_estimate",
    "markdown_front_matter",
    "markdown_etag",
    "markdown_conditional_requests",
    "authenticated request policy / cache-control",
    "decision logs / reason codes / metrics",
    "table conversion",
    "prune_noise_regions",
    "markdown_on_wildcard",
]

# Required sections in a requirements document (Property 1)
# These match Chinese section headings in the spec documents under .kiro/specs/
REQUIRED_REQ_SECTIONS = [
    "目标",       # Goals
    "非目标",     # Non-Goals
    "兼容性",     # Compatibility
    "测试",       # Testing
    "文档",       # Documentation
    "rollout",    # Rollout
    "可观测",     # Observability
    "安全",       # Security
    "迁移",       # Migration
]

# DoD checkpoints (Property 5)
# These match Chinese checkpoint names in the spec design documents
DOD_CHECKPOINTS = [
    "功能正确",       # Functionally correct
    "可观测",         # Observable
    "可回滚",         # Rollbackable
    "可文档化",       # Documentable
    "可审计",         # Auditable
    "默认兼容不变",   # Default-compatible
]

# Boundary description required fields (Property 3)
# These match Chinese field names in the spec design documents
BOUNDARY_FIELDS = [
    "能力",                       # Capability
    "0.5.0 范围",                 # 0.5.0 Scope
    "0.6.x+ 范围",               # 0.6.x+ Scope
    "边界放置理由",               # Boundary Placement Rationale
    "推迟工作的依赖或前提条件",   # Prerequisites for Deferred Work
]

# Test matrix dimensions (Property 6)
# These match English headings in docs/project/test-matrix-0-5-0.md
TEST_MATRIX_DIMENSIONS = {
    "Platform": ["Ubuntu", "macOS"],
    "NGINX Version": ["1.24", "1.26", "1.27"],
    "Response Size Tier": ["Small", "Medium", "Large", "Extra-Large"],
    "Conversion Engine": ["full-buffer", "streaming"],
    "Conversion Path": ["convert", "skip", "fallback/fail-open"],
}

EMPTY_CAPABILITY_NAME = "<empty>"


class ValidationResult:
    """Collects pass/fail/skip results for structured output."""

    def __init__(self) -> None:
        """Initialize an empty result list.

        Each entry is stored as ``(status, check_name, detail)`` where status
        is one of ``PASS``, ``FAIL``, or ``SKIP``.
        """
        self.results: list[tuple[str, str, str]] = []

    def passed(self, check: str, detail: str = "") -> None:
        """Record a successful check result."""
        self.results.append(("PASS", check, detail))

    def failed(self, check: str, detail: str = "") -> None:
        """Record a failed check result."""
        self.results.append(("FAIL", check, detail))

    def skipped(self, check: str, detail: str = "") -> None:
        """Record a skipped check result."""
        self.results.append(("SKIP", check, detail))

    @property
    def has_failures(self) -> bool:
        """Return ``True`` when at least one recorded result is ``FAIL``."""
        return any(s == "FAIL" for s, _, _ in self.results)

    def print_report(self) -> None:
        """Print one human-readable line per recorded validation result."""
        for status, check, detail in self.results:
            suffix = f" — {detail}" if detail else ""
            print(f"  [{status}] {check}{suffix}")


def _read_file_safe(path: Path) -> str | None:
    """Read a file, returning None if it does not exist."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError):
        return None


def _parse_table_cells(line: str) -> list[str]:
    """Parse a Markdown table row into non-empty trimmed cells.

    Handles the trailing ``|`` that is standard in Markdown tables:
    ``| a | b | c |`` -> ``["a", "b", "c"]``

    Note: empty cells are removed.  Use ``_parse_table_cells_positional``
    when column position matters.
    """
    raw = line.split("|")
    cells = [c.strip() for c in raw]
    return [c for c in cells if c]


def _parse_table_cells_positional(line: str) -> list[str]:
    """Parse a Markdown table row preserving column positions.

    ``| a | | c |`` -> ``["a", "", "c"]``

    Leading and trailing empty strings from the outer pipes are stripped,
    but interior empty cells are preserved.
    """
    raw = [c.strip() for c in line.split("|")]
    # Strip the leading/trailing empty strings from outer pipes
    if raw and raw[0] == "":
        raw = raw[1:]
    if raw and raw[-1] == "":
        raw = raw[:-1]
    return raw


# ---------------------------------------------------------------------------
# Per-sub-spec checks (used in strict mode for all specs, framework for #12)
# ---------------------------------------------------------------------------

REQUIREMENTS_MD = "requirements.md"
DESIGN_MD = "design.md"
DESIGN_NOT_FOUND = "design.md not found"


def _resolve_spec_dir(specs_dir: Path, name: str) -> Path | None:
    """Locate a sub-spec directory, checking archive/ as a fallback.

    Completed specs may be moved to ``specs_dir / "archive" / name``.
    This helper checks the top-level location first, then the archive.

    Security: rejects absolute paths and ``..`` segments to prevent
    path traversal, and verifies the resolved path is contained within
    ``specs_dir``.

    Returns:
        The resolved directory ``Path``, or ``None`` if not found or
        if the name fails validation.
    """
    name_path = Path(name)
    if name_path.is_absolute() or ".." in name_path.parts:
        return None

    specs_resolved = specs_dir.resolve()

    candidate = (specs_dir / name).resolve()
    if candidate.is_dir() and candidate.is_relative_to(specs_resolved):
        return candidate

    archived = (specs_dir / "archive" / name).resolve()
    if archived.is_dir() and archived.is_relative_to(specs_resolved):
        return archived

    return None


def _resolve_spec_file(
    specs_dir: Path, name: str, filename: str
) -> Path | None:
    """Locate a file inside a sub-spec directory (with archive fallback).

    Combines ``_resolve_spec_dir`` with a filename join.  Returns the
    file ``Path`` if the directory is found (the file itself may or may
    not exist), or ``None`` if the directory cannot be resolved.
    """
    spec_dir = _resolve_spec_dir(specs_dir, name)
    if spec_dir is None:
        return None
    return spec_dir / filename


def check_subspecs_docs_exist(
    result: ValidationResult, specs_dir: Path, subspecs: list[str]
) -> None:
    """Property 2: every sub-spec must have requirements.md and design.md."""
    for name in subspecs:
        spec_dir = _resolve_spec_dir(specs_dir, name)
        if spec_dir is None:
            result.failed(f"docs-exist:{name}", "sub-spec directory not found")
            continue
        for doc in (REQUIREMENTS_MD, DESIGN_MD):
            path = spec_dir / doc
            if path.is_file():
                result.passed(f"docs-exist:{name}/{doc}")
            else:
                result.failed(f"docs-exist:{name}/{doc}", "file missing")


def _extract_headings(content: str) -> list[str]:
    """Extract all Markdown heading lines from *content*, lowercased."""
    return [
        line.strip().lower()
        for line in content.split("\n")
        if line.strip().startswith("#")
    ]


def check_requirements_sections(
    result: ValidationResult, specs_dir: Path, subspecs: list[str]
) -> None:
    """Property 1: requirements docs must contain required sections as headings.

    Checks that each required section keyword appears in at least one
    Markdown heading line (lines starting with #), not just anywhere in
    the document body.

    Spec #12 is the governance spec that *defines* these section
    requirements for other sub-specs; it does not need to contain them
    itself.  It is skipped in this check.
    """
    for name in subspecs:
        if name == SPEC12_ONLY[0]:
            result.skipped(
                f"req-sections:{name}",
                "governance spec (defines section requirements, does not follow them)",
            )
            continue
        resolved = _resolve_spec_dir(specs_dir, name)
        if resolved is None:
            result.skipped(f"req-sections:{name}", f"{REQUIREMENTS_MD} not found")
            continue
        content = _read_file_safe(resolved / REQUIREMENTS_MD)
        if content is None:
            result.skipped(f"req-sections:{name}", f"{REQUIREMENTS_MD} not found")
            continue
        headings = _extract_headings(content)
        if missing := [
            s
            for s in REQUIRED_REQ_SECTIONS
            if all(s.lower() not in h for h in headings)
        ]:
            result.failed(
                f"req-sections:{name}",
                f"missing section headings: {', '.join(missing)}",
            )
        else:
            result.passed(f"req-sections:{name}")


def check_boundary_descriptions(
    result: ValidationResult, specs_dir: Path, subspecs: list[str]
) -> None:
    """Property 3: sub-specs with natural extensions must have boundary descriptions."""
    for name in subspecs:
        design_path = _resolve_spec_file(specs_dir, name, DESIGN_MD)
        if design_path is None:
            result.skipped(f"boundary:{name}", DESIGN_NOT_FOUND)
            continue
        content = _read_file_safe(design_path)
        if content is None:
            result.skipped(f"boundary:{name}", DESIGN_NOT_FOUND)
            continue
        if "Boundary Description" in content or "边界描述" in content:
            if missing_fields := [
                f for f in BOUNDARY_FIELDS if f not in content
            ]:
                result.failed(
                    f"boundary:{name}",
                    f"boundary description missing fields: {', '.join(missing_fields)}",
                )
            else:
                result.passed(f"boundary:{name}")
        else:
            result.skipped(
                f"boundary:{name}", "no boundary description section found"
            )


def check_dod_assessment(
    result: ValidationResult, specs_dir: Path, subspecs: list[str]
) -> None:
    """Property 5: completed sub-specs must have DoD assessment with all checkpoints."""
    for name in subspecs:
        design_path = _resolve_spec_file(specs_dir, name, DESIGN_MD)
        if design_path is None:
            result.skipped(f"dod:{name}", DESIGN_NOT_FOUND)
            continue
        content = _read_file_safe(design_path)
        if content is None:
            result.skipped(f"dod:{name}", DESIGN_NOT_FOUND)
            continue
        if "DoD Assessment" not in content and "DoD 评估" not in content:
            result.skipped(
                f"dod:{name}", "no DoD assessment found (not yet completed)"
            )
            continue
        if missing := [cp for cp in DOD_CHECKPOINTS if cp not in content]:
            result.failed(
                f"dod:{name}",
                f"DoD missing checkpoints: {', '.join(missing)}",
            )
        else:
            result.passed(f"dod:{name}")


def _is_table_separator(cells: list[str]) -> bool:
    """Return True if all cells are Markdown table separator dashes."""
    return all(c.replace("-", "").replace(":", "").strip() == "" for c in cells)


def _is_template_placeholder(cells: list[str]) -> bool:
    """Return True if any cell is a template placeholder like ``[Description]``."""
    return any("[" in c and "]" in c for c in cells)


def _high_severity_missing_mitigation(line: str, cells: list[str]) -> bool:
    """Return True if a high-severity row lacks a mitigation strategy."""
    if "高" not in line and "High" not in line:
        return False
    mitigation = cells[-1] if len(cells) >= 5 else ""
    return not mitigation or mitigation in ("-", "—")


def _is_risk_table_header(line: str) -> bool:
    """Return True if *line* is a risk table header row."""
    return ("风险" in line or "Risk" in line) and "|" in line and "#" in line


def _is_skippable_row(line: str, cells: list[str]) -> bool:
    """Return True if the table row should be skipped during risk scanning."""
    if _is_table_separator(cells):
        return True
    if non_empty := [c for c in cells if c]:
        return (
            True
            if _is_template_placeholder(non_empty)
            else "高" not in line and "High" not in line
        )
    else:
        return True


def _row_missing_mitigation(cells: list[str], mitigation_idx: int) -> bool:
    """Return True if the row lacks a mitigation value at *mitigation_idx*."""
    mitigation = ""
    if 0 <= mitigation_idx < len(cells):
        mitigation = cells[mitigation_idx].strip()
    return not mitigation or mitigation in ("-", "—")


def _scan_risk_table(content: str) -> bool:
    """Return True if the risk table contains a high-severity row without mitigation.

    Uses positional cell parsing to reliably locate the mitigation column
    (index 4 in a 5-column risk table: #, description, likelihood, impact,
    mitigation).
    """
    in_risk_table = False
    mitigation_idx = -1
    for line in content.split("\n"):
        if _is_risk_table_header(line):
            in_risk_table = True
            header_cells = _parse_table_cells_positional(line)
            mitigation_idx = len(header_cells) - 1 if header_cells else -1
            continue
        if not in_risk_table:
            continue
        if "|" not in line:
            in_risk_table = False
            continue
        cells = _parse_table_cells_positional(line)
        if _is_skippable_row(line, cells):
            continue
        if _row_missing_mitigation(cells, mitigation_idx):
            return True
    return False


def check_risk_register(
    result: ValidationResult, specs_dir: Path, subspecs: list[str]
) -> None:
    """Property 7: sub-specs must have risk registers; high-severity needs mitigation."""
    for name in subspecs:
        design_path = _resolve_spec_file(specs_dir, name, DESIGN_MD)
        if design_path is None:
            result.skipped(f"risk:{name}", DESIGN_NOT_FOUND)
            continue
        content = _read_file_safe(design_path)
        if content is None:
            result.skipped(f"risk:{name}", DESIGN_NOT_FOUND)
            continue
        if "风险登记" not in content and "Risk Register" not in content:
            result.skipped(f"risk:{name}", "no risk register found")
            continue
        if _scan_risk_table(content):
            result.failed(
                f"risk:{name}",
                "high-severity risk without mitigation strategy",
            )
        else:
            result.passed(f"risk:{name}")


# ---------------------------------------------------------------------------
# Framework-level checks (governance documents produced by spec #12)
# ---------------------------------------------------------------------------


def find_heading_index(lines: list[str], heading: str) -> int:
    """Find the index of the first Markdown heading matching the given heading text.

    Args:
        lines: A list of lines from the markdown file
        heading: The heading text to search for

    Returns:
        The index of the first matching heading, or -1 if no match is found
    """
    heading_lower = heading.lower()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") and heading_lower in stripped.lower():
            return i
    return -1


def extract_table_under_heading(content: str, heading: str) -> list[list[str]]:
    """Extract parsed rows from the first Markdown table under *heading*.

    This function locates the first Markdown heading matching the given heading text,
    then extracts the table that follows it. It preserves empty cells and excludes
    table separator rows.

    Args:
        content: The content of the markdown file
        heading: The heading text to search for

    Returns:
        A list of rows, each containing a list of cells (empty cells preserved).
        Returns an empty list if the heading or table is not found.
    """
    lines = content.split("\n")
    start = find_heading_index(lines, heading)
    if start < 0:
        return []

    rows: list[list[str]] = []
    for line in lines[start + 1:]:
        stripped = line.strip()
        # Stop if we encounter a new heading and have already found rows
        if stripped.startswith("#") and rows:
            break
        # Skip lines without table cells
        if "|" not in stripped:
            if rows:
                break
            continue
        # Parse cells while preserving empty ones
        cells = _parse_table_cells_positional(stripped)
        # Skip empty rows and separator rows
        if not cells or _is_table_separator(cells):
            continue
        rows.append(cells)
    return rows


CHECK_COMPAT_CAPS = "compat-matrix:capabilities"
CHECK_COMPAT_STATES = "compat-matrix:states"
CHECK_GATE_TEMPLATE = "gate-failures:template"
CHECK_GATE_CHECKLIST = "gate-failures:checklist"
_VALID_STATES_LOWER = {s.lower() for s in VALID_STATES}


def _normalize_state_value(value: str) -> str:
    """Normalize a state cell from either matrix format to a canonical state.

    Supports:
    - old format: state columns marked with a checkmark
    - new format: single Classification column containing values such as
      "`streaming-supported`" or "`streaming-supported` (conditional)"
    """
    normalized = value.strip().replace("`", "").lower()
    if " " in normalized:
        normalized = normalized.split(" ", 1)[0]
    return normalized


def _compat_column_indices(header: list[str]) -> tuple[int, int | None]:
    """Return the capability column index and optional classification column index."""
    lowered = [cell.strip().lower() for cell in header]
    capability_idx = next(
        (idx for idx, value in enumerate(lowered) if value == "capability"), -1
    )
    classification_idx = next(
        (
            idx
            for idx, value in enumerate(lowered)
            if value == "classification"
        ),
        None,
    )
    return capability_idx, classification_idx


def check_compat_capabilities(
    result: ValidationResult, rows: list[list[str]]
) -> None:
    """Sub-check: all canonical capabilities must appear as table rows.

    This function parses the first column of the compatibility matrix table
    and verifies that every canonical capability appears at least once.
    It allows for sub-capability splits, such as "markdown_front_matter (common ...)".

    Args:
        result: ValidationResult object to record results
        rows: List of table rows, each containing a list of cells
    """
    if not rows:
        result.failed(CHECK_COMPAT_CAPS, "no table rows to check")
        return

    capability_idx, _ = _compat_column_indices(rows[0])
    if capability_idx == -1:
        result.failed(CHECK_COMPAT_CAPS, "capability column not found")
        return

    row_caps = []
    row_caps.extend(
        row[capability_idx].strip().lower()
        for row in rows[1:]
        if capability_idx < len(row)
    )
    missing = []
    for cap in CANONICAL_CAPABILITIES:
        cap_lower = cap.lower()
        # Allow sub-capability splits: "markdown_front_matter" matches
        # "markdown_front_matter (common head ...)" etc.
        if all(cap_lower not in rc for rc in row_caps):
            missing.append(cap)

    if missing:
        result.failed(
            CHECK_COMPAT_CAPS,
            f"missing capabilities in table rows: {', '.join(missing)}",
        )
    else:
        result.passed(CHECK_COMPAT_CAPS)


def check_compat_states(
    result: ValidationResult, header: list[str]
) -> tuple[list[int], int]:
    """Sub-check: header must have exactly the valid state columns.

    This function verifies that the compatibility matrix header contains
    exactly the three valid state columns and no unexpected columns.

    Args:
        result: ValidationResult object to record results
        header: List of header cells from the compatibility matrix

    Returns:
        List of column indices for the state columns, or empty list on failure
    """
    capability_idx, classification_idx = _compat_column_indices(header)
    if capability_idx == -1:
        result.failed(CHECK_COMPAT_STATES, "capability column not found")
        return [], -1

    if classification_idx is not None:
        result.passed(CHECK_COMPAT_STATES)
        return [classification_idx], capability_idx

    state_columns = [h for h in header if h.strip() and h.lower() in _VALID_STATES_LOWER]
    extra_columns = [
        h for h in header
        if h.strip()
        and h.lower() not in _VALID_STATES_LOWER
        and h.lower() not in ("#", "capability", "notes")
    ]
    if len(state_columns) != len(VALID_STATES):
        result.failed(
            CHECK_COMPAT_STATES,
            f"expected {len(VALID_STATES)} state columns, found {len(state_columns)}: {state_columns}",
        )
        return [], capability_idx
    if extra_columns:
        result.failed(
            CHECK_COMPAT_STATES,
            f"unexpected columns in matrix header: {extra_columns}",
        )
        return [], capability_idx
    result.passed(CHECK_COMPAT_STATES)
    return [
        i for i, h in enumerate(header) if h.strip() and h.lower() in _VALID_STATES_LOWER
    ], capability_idx


def _row_cap_name(row: list[str], capability_idx: int) -> str:
    """Return the capability name from a row, or '<empty>' as fallback."""
    if 0 <= capability_idx < len(row):
        return row[capability_idx].strip() or EMPTY_CAPABILITY_NAME
    if row:
        return row[0].strip() or EMPTY_CAPABILITY_NAME
    return EMPTY_CAPABILITY_NAME


def _validate_classification_row(row: list[str], idx: int, cap_name: str) -> str | None:
    """Return an error string if the classification cell is invalid, else None."""
    if idx >= len(row) or not row[idx].strip():
        return f"{cap_name} (missing classification)"
    state_value = _normalize_state_value(row[idx])
    if state_value not in _VALID_STATES_LOWER:
        return f"{cap_name} (invalid state: {row[idx].strip()})"
    return None


def _validate_multi_state_row(
    row: list[str], state_indices: list[int], cap_name: str
) -> str | None:
    """Return an error string if the row does not mark exactly one state, else None."""
    marked = sum(bool(idx < len(row) and row[idx].strip()) for idx in state_indices)
    return f"{cap_name} (marked {marked} states)" if marked != 1 else None


def check_compat_row_validity(
    result: ValidationResult,
    data_rows: list[list[str]],
    state_indices: list[int],
    capability_idx: int,
) -> None:
    """Sub-check: each data row must mark exactly one state.

    This function verifies that each row in the compatibility matrix
    marks exactly one of the three valid states, ensuring consistent
    classification of capabilities.

    Args:
        result: ValidationResult object to record results
        data_rows: List of data rows from the compatibility matrix
        state_indices: List of column indices for the state columns
    """
    invalid_rows = []
    uses_classification_column = len(state_indices) == 1

    for row in data_rows:
        cap_name = _row_cap_name(row, capability_idx)
        if uses_classification_column:
            error = _validate_classification_row(row, state_indices[0], cap_name)
        else:
            error = _validate_multi_state_row(row, state_indices, cap_name)
        if error:
            invalid_rows.append(error)

    if invalid_rows:
        result.failed(
            "compat-matrix:row-validity",
            f"rows with != 1 state marked: {'; '.join(invalid_rows)}",
        )
    else:
        result.passed("compat-matrix:row-validity")


def check_compatibility_matrix(result: ValidationResult) -> None:
    """Property 8: compatibility matrix must use only valid states.

    This function validates the compatibility matrix document by:
    1. Checking that the classification table exists
    2. Verifying all canonical capabilities are present
    3. Ensuring the header has exactly the three valid state columns
    4. Confirming each row marks exactly one state

    Args:
        result: ValidationResult object to record results
    """
    matrix_path = PROJECT_ROOT / "docs" / "project" / "compatibility-matrix-0-5-0.md"
    content = _read_file_safe(matrix_path)
    if content is None:
        result.failed("compat-matrix", "compatibility-matrix-0-5-0.md not found")
        return

    # Extract the classification matrix table
    rows = extract_table_under_heading(content, "Capability Classification Matrix")
    if not rows:
        result.failed("compat-matrix:table", "classification table not found")
        return

    # Check that all canonical capabilities are present
    check_compat_capabilities(result, rows)

    # Check that the header has the correct state columns and validate rows
    state_indices, capability_idx = check_compat_states(result, rows[0])
    if state_indices:
        check_compat_row_validity(result, rows[1:], state_indices, capability_idx)


_CHECKLIST_RE_PREFIX = "- ["


def _is_checklist_item(line: str) -> bool:
    """Check if a line is a Markdown checklist item in any state.

    This function identifies Markdown checklist items regardless of their state,
    including unchecked, checked, in-progress, etc.

    Args:
        line: The line to check

    Returns:
        True if the line is a Markdown checklist item, False otherwise
    """
    stripped = line.strip()
    # Match - [ ], - [x], - [X], - [-], - [~]
    if not stripped.startswith(_CHECKLIST_RE_PREFIX):
        return False
    # Expect closing ] within the first 6 characters: "- [?] " or "- [?]"
    close = stripped.find("]", 3)
    return close != -1 and close <= 5


def check_checklist_verification(result: ValidationResult) -> None:
    """Property 16: each checklist item must reference a verification method.

    This function validates that every checklist item in the release checklist
    document includes a verification reference, regardless of its state (unchecked,
    checked, in-progress). This ensures verification references are not lost
    as items are ticked off.

    Args:
        result: ValidationResult object to record results
    """
    checklist_path = (
        PROJECT_ROOT / "docs" / "project" / "release-checklist-0-5-0.md"
    )
    content = _read_file_safe(checklist_path)
    if content is None:
        result.failed("checklist", "release-checklist-0-5-0.md not found")
        return

    # Identify checklist items without verification references
    items_without_verification = []
    items_without_verification.extend(
        line.strip()[:80]
        for line in content.split("\n")
        if _is_checklist_item(line) and "verify" not in line.lower()
    )
    if items_without_verification:
        result.failed(
            "checklist:verification",
            f"{len(items_without_verification)} items without verification reference",
        )
    else:
        result.passed("checklist:verification")


def check_test_matrix(result: ValidationResult) -> None:
    """Property 6: test matrix must define all five dimensions and their values.

    Parses the Dimension Definitions table and validates that each
    expected dimension row exists and contains all required values.
    """
    matrix_path = PROJECT_ROOT / "docs" / "project" / "test-matrix-0-5-0.md"
    content = _read_file_safe(matrix_path)
    if content is None:
        result.failed("test-matrix", "test-matrix-0-5-0.md not found")
        return

    rows = extract_table_under_heading(content, "Dimension Definitions")
    if not rows:
        result.failed("test-matrix:table", "Dimension Definitions table not found")
        return

    dim_values: dict[str, str] = {
        row[0].strip(): row[1].strip() if len(row) > 1 else ""
        for row in rows[1:]
        if len(row) >= 2 and row[0].strip()
    }
    for dimension, expected_values in TEST_MATRIX_DIMENSIONS.items():
        if dimension not in dim_values:
            result.failed(
                f"test-matrix:{dimension}",
                f"dimension '{dimension}' not found in Dimension Definitions table",
            )
            continue
        cell_text = dim_values[dimension]
        if missing_vals := [v for v in expected_values if v not in cell_text]:
            result.failed(
                f"test-matrix:{dimension}",
                f"missing values in definition row: {', '.join(missing_vals)}",
            )
        else:
            result.passed(f"test-matrix:{dimension}")


def check_non_goals(result: ValidationResult) -> None:
    """Verify non-goals list exists in release gates document.
    
    This function validates that the release gates document includes
    references to all key non-goals for the 0.5.0 release.
    
    Args:
        result: ValidationResult object to record results
    """
    gates_path = PROJECT_ROOT / "docs" / "project" / "release-gates-0-5-0.md"
    content = _read_file_safe(gates_path)
    if content is None:
        result.failed("non-goals", "release-gates-0-5-0.md not found")
        return

    # Key non-goal keywords that should be present (case-insensitive, whole word)
    non_goal_keywords = ["JSON", "OpenTelemetry", "GUI", "Helm", "tokenizer"]
    if missing := [
        kw
        for kw in non_goal_keywords
        if re.search(
            rf"(?<![0-9A-Za-z_]){re.escape(kw)}(?![0-9A-Za-z_])",
            content,
            flags=re.IGNORECASE,
        )
        is None
    ]:
        result.failed("non-goals", f"missing non-goal keywords: {', '.join(missing)}")
    else:
        result.passed("non-goals")


def check_naming_conventions_doc(result: ValidationResult) -> None:
    """Verify naming conventions document defines all three categories.
    
    This function validates that the naming conventions document includes
    definitions for all required naming categories.
    
    Args:
        result: ValidationResult object to record results
    """
    naming_path = (
        PROJECT_ROOT / "docs" / "project" / "naming-conventions-0-5-0.md"
    )
    content = _read_file_safe(naming_path)
    if content is None:
        result.failed("naming-doc", "naming-conventions-0-5-0.md not found")
        return

    # Required naming categories that should be defined
    categories = ["NGINX", "Prometheus", "Reason Code"]
    if missing := [c for c in categories if c not in content]:
        result.failed(
            "naming-doc", f"missing naming categories: {', '.join(missing)}"
        )
    else:
        result.passed("naming-doc")


def check_architecture_status(result: ValidationResult) -> None:
    """Verify release gates document contains architecture status statement."""
    gates_path = PROJECT_ROOT / "docs" / "project" / "release-gates-0-5-0.md"
    content = _read_file_safe(gates_path)
    if content is None:
        result.failed("arch-status", "release-gates-0-5-0.md not found")
        return

    required_phrases = [
        "buffer-all-then-finalize",
        "feed_chunk",
        "finalize",
        "64 MiB",
        "API/ABI",
        "request-scoped",
        "XSS",
    ]
    if missing := [p for p in required_phrases if p not in content]:
        result.failed(
            "arch-status",
            f"missing architecture status phrases: {', '.join(missing)}",
        )
    else:
        result.passed("arch-status")


# Required fields in a gate-failure exception record (Property 15)
_EXCEPTION_FIELDS = ["Gate Item", "Exception Rationale", "Risk Assessment", "Mitigation Strategy"]


def check_gate_failure_handling(result: ValidationResult) -> None:
    """Property 15: gate failure handling structure must exist.

    Validates that:
    1. The Go/No-Go template contains an Exceptions section
    2. The exception table header includes all required fields
    3. The release checklist documents the exception handling process
    """
    # Check Go/No-Go template has exception table with required fields
    gonogo_path = PROJECT_ROOT / "docs" / "project" / "go-nogo-template-0-5-0.md"
    content = _read_file_safe(gonogo_path)
    if content is None:
        result.failed(CHECK_GATE_TEMPLATE, "go-nogo-template-0-5-0.md not found")
        return

    if "Exceptions" not in content:
        result.failed(CHECK_GATE_TEMPLATE, "no Exceptions section in Go/No-Go template")
        return

    if missing_fields := [f for f in _EXCEPTION_FIELDS if f not in content]:
        result.failed(
            CHECK_GATE_TEMPLATE,
            f"exception table missing fields: {', '.join(missing_fields)}",
        )
    else:
        result.passed(CHECK_GATE_TEMPLATE)

    # Check release checklist documents exception handling process
    checklist_path = PROJECT_ROOT / "docs" / "project" / "release-checklist-0-5-0.md"
    cl_content = _read_file_safe(checklist_path)
    if cl_content is None:
        result.failed(CHECK_GATE_CHECKLIST, "release-checklist-0-5-0.md not found")
        return

    required_process = ["Exception", "escalat", "risk assessment"]
    if missing_process := [
        p for p in required_process if p.lower() not in cl_content.lower()
    ]:
        result.failed(
            CHECK_GATE_CHECKLIST,
            f"exception handling process missing keywords: {', '.join(missing_process)}",
        )
    else:
        result.passed(CHECK_GATE_CHECKLIST)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Run validation checks and report results.

    Modes:
    - framework (default): only validates spec #12's own governance documents.
      This is what ``make release-gates-check`` runs. It passes when the
      framework itself is correctly delivered, independent of whether
      sub-specs #13-#18 are fully compliant yet.
    - strict: validates all sub-specs (#12-#18) for section completeness,
      boundary descriptions, DoD, and risk registers. Use this when all
      sub-specs are expected to be fully compliant (e.g., pre-release).
    """
    parser = argparse.ArgumentParser(
        description="Validate 0.5.0 release gate documents"
    )
    parser.add_argument(
        "--specs-dir",
        type=Path,
        default=DEFAULT_SPECS_DIR,
        help="Path to the specs directory (default: .kiro/specs/)",
    )
    parser.add_argument(
        "--mode",
        choices=["framework", "strict"],
        default="framework",
        help=(
            "framework: validate only spec #12 governance documents (default). "
            "strict: validate all sub-specs #12-#18."
        ),
    )
    parser.add_argument(
        "--check",
        choices=[
            "docs-exist",
            "req-sections",
            "boundary",
            "dod",
            "risk",
            "compat-matrix",
            "checklist",
            "test-matrix",
            "non-goals",
            "naming-doc",
            "arch-status",
            "gate-failures",
            "all",
        ],
        default="all",
        help="Which check to run (default: all)",
    )
    args = parser.parse_args()

    specs_dir = args.specs_dir.resolve()
    result = ValidationResult()

    # Determine which sub-specs to check for per-spec validations
    target_subspecs = SUBSPECS_050 if args.mode == "strict" else SPEC12_ONLY

    # Per-sub-spec checks (scoped by mode)
    per_spec_checks: dict[str, object] = {
        "docs-exist": lambda r: check_subspecs_docs_exist(r, specs_dir, target_subspecs),
        "req-sections": lambda r: check_requirements_sections(r, specs_dir, target_subspecs),
        "boundary": lambda r: check_boundary_descriptions(r, specs_dir, target_subspecs),
        "dod": lambda r: check_dod_assessment(r, specs_dir, target_subspecs),
        "risk": lambda r: check_risk_register(r, specs_dir, target_subspecs),
    }

    # Framework-level checks (always run, independent of mode)
    framework_checks: dict[str, object] = {
        "compat-matrix": lambda r: check_compatibility_matrix(r),
        "checklist": lambda r: check_checklist_verification(r),
        "test-matrix": lambda r: check_test_matrix(r),
        "non-goals": lambda r: check_non_goals(r),
        "naming-doc": lambda r: check_naming_conventions_doc(r),
        "arch-status": lambda r: check_architecture_status(r),
        "gate-failures": lambda r: check_gate_failure_handling(r),
    }

    all_checks = per_spec_checks | framework_checks

    if args.check == "all":
        for check_fn in all_checks.values():
            check_fn(result)
    elif args.check in all_checks:
        all_checks[args.check](result)

    mode_label = "framework" if args.mode == "framework" else "strict (all sub-specs)"
    print(f"0.5.0 Release Gate Validation Report [mode: {mode_label}]")
    print("=" * 60)
    result.print_report()
    print()

    pass_count = sum(s == "PASS" for s, _, _ in result.results)
    fail_count = sum(s == "FAIL" for s, _, _ in result.results)
    skip_count = sum(s == "SKIP" for s, _, _ in result.results)
    print(f"Summary: {pass_count} passed, {fail_count} failed, {skip_count} skipped")

    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
