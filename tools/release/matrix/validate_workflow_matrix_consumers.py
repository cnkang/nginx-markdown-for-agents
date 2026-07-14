#!/usr/bin/env python3
"""Validate that GitHub Actions workflow files are consistent with release-matrix.json.

Ensures that:
  1. Canonical release workflows (release-packages, release-binaries, install-verify)
     dynamically read their matrix from tools/release-matrix.json (no hardcoded versions).
  2. Any hardcoded NGINX versions found in workflow files exist in release-matrix.json.
  3. Legacy/non-canonical workflows that hardcode versions are flagged as warnings
     (not errors) when the version still exists in the matrix.

Subset filters (e.g., only stable, only glibc) are allowed and documented in
release-matrix.json's owner_workflow fields.

Exit code 0 = consistent, exit code 1 = inconsistencies found.

Usage:
    python3 tools/release/matrix/validate_workflow_matrix_consumers.py

Part of release matrix source of truth (Requirement 5).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lib.path_validation import validate_read_path

# Paths relative to the repository root
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent.parent
MATRIX_PATH = REPO_ROOT / "tools" / "release-matrix.json"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"

# Canonical workflows that MUST read from release-matrix.json dynamically.
# These should not hardcode NGINX version lists.
CANONICAL_DYNAMIC_WORKFLOWS = {
    "release-packages.yml",
    "release-binaries.yml",
    "install-verify.yml",
}

# Workflows explicitly marked as legacy/non-canonical.
# Hardcoded versions here produce warnings, not errors.
LEGACY_WORKFLOWS = {
    "release-deb.yml",
    "release-rpm.yml",
}

# Candidate semantic versions are classified as NGINX versions only when the
# same workflow line explicitly associates them with NGINX. This avoids numeric
# range guesses that eventually misclassify Rust, Python, or tool releases.
NGINX_VERSION_RE = re.compile(r"(?<![0-9.])\d+\.\d+\.\d+(?![0-9.])")
NGINX_CONTEXT_RE = re.compile(r"\bnginx\b|\bnginx[_-]", re.IGNORECASE)
NGINX_BLOCK_KEY_RE = re.compile(
    r"^(?P<indent>\s*)nginx(?:[_-]versions?)?\s*:", re.IGNORECASE
)

# Patterns that indicate dynamic matrix resolution from release-matrix.json
DYNAMIC_RESOLUTION_PATTERNS = [
    "tools/release-matrix.json",
    "release-matrix.json",
]

# Patterns to exclude from version detection (comments, descriptions, examples)
EXCLUDE_CONTEXT_PATTERNS = [
    r"^\s*#",           # YAML comments
    r"description:",    # Input descriptions
    r"e\.g\.",          # Example mentions
    r"Example:",        # Example labels
    r"# Example:",      # Comment examples
    r"Floor\s*=",       # Code comment about version math
]


def load_matrix_versions(path: Path) -> set[str]:
    """Load all NGINX versions from release-matrix.json.

    Returns the set of all nginx version strings across both the main matrix
    and additional_artifacts entries.
    """
    validated = validate_read_path(path, purpose="workflow matrix validation")
    with open(validated, "r", encoding="utf-8") as f:
        data = json.load(f)

    versions: set[str] = set()

    # Support both canonical 'entries' (release matrix canonical format) and legacy 'matrix' arrays
    entries = data.get("entries", []) or data.get("matrix", [])
    for entry in entries:
        if v := entry.get("nginx"):
            versions.add(v)
        if v := entry.get("nginx_version"):
            versions.add(v)

    return versions


def _is_excluded_line(line: str) -> bool:
    """Check if a line should be excluded from version detection."""
    return any(re.search(pat, line) for pat in EXCLUDE_CONTEXT_PATTERNS)


def _uses_dynamic_resolution(content: str) -> bool:
    """Check if workflow content reads matrix from release-matrix.json."""
    return any(pattern in content for pattern in DYNAMIC_RESOLUTION_PATTERNS)


def _nginx_context_for_line(
    line: str, nginx_block_indent: int | None
) -> tuple[bool, int | None]:
    """Return whether *line* is NGINX-scoped and the updated YAML block indent."""
    stripped = line.strip()
    indent = len(line) - len(line.lstrip())
    block_match = NGINX_BLOCK_KEY_RE.match(line)
    if block_match is not None:
        nginx_block_indent = len(block_match.group("indent"))
    elif (
        stripped
        and not stripped.startswith("#")
        and nginx_block_indent is not None
        and indent <= nginx_block_indent
    ):
        nginx_block_indent = None

    explicit_context = NGINX_CONTEXT_RE.search(line) is not None
    nested_context = nginx_block_indent is not None and indent > nginx_block_indent
    return explicit_context or nested_context, nginx_block_indent


def extract_hardcoded_versions(content: str) -> list[tuple[int, str, str]]:
    """Extract hardcoded NGINX version references from workflow content.

    Returns a list of (line_number, version_string, line_content) tuples
    for versions that appear to be hardcoded NGINX version references
    (not in excluded contexts like comments/descriptions/examples).
    """
    found: list[tuple[int, str, str]] = []
    nginx_block_indent: int | None = None

    for lineno, line in enumerate(content.splitlines(), start=1):
        in_nginx_context, nginx_block_indent = _nginx_context_for_line(
            line, nginx_block_indent
        )
        if _is_excluded_line(line) or not in_nginx_context:
            continue

        found.extend(
            (lineno, match.group(0), line.strip())
            for match in NGINX_VERSION_RE.finditer(line)
        )

    return found


def validate_canonical_workflows() -> tuple[list[str], list[str]]:
    """Validate canonical workflows use dynamic resolution.

    Returns (errors, warnings) lists.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for wf_name in sorted(CANONICAL_DYNAMIC_WORKFLOWS):
        wf_path = WORKFLOWS_DIR / wf_name
        if not wf_path.exists():
            warnings.append(f"Canonical workflow not found: {wf_name}")
            continue

        content = wf_path.read_text(encoding="utf-8")

        if not _uses_dynamic_resolution(content):
            errors.append(
                f"Canonical workflow {wf_name} does not reference "
                f"release-matrix.json for dynamic matrix resolution"
            )
            continue

        # Even though dynamic, canonical workflows must not hardcode any
        # NGINX version — all versions must come from the matrix.
        hardcoded = extract_hardcoded_versions(content)
        errors.extend(
            f"{wf_name}:{lineno}: canonical workflow must not hardcode "
            f"version '{version}' (line: {line_ctx!r})"
            for lineno, version, line_ctx in hardcoded
        )
    return errors, warnings


def validate_legacy_workflows(
    matrix_versions: set[str],
) -> tuple[list[str], list[str]]:
    """Validate legacy workflows reference only known matrix versions.

    Legacy workflows may hardcode versions (they are retained for
    compatibility), but those versions must still exist in the matrix.
    Unknown versions are errors; known versions produce informational warnings.
    """
    errors: list[str] = []
    warnings: list[str] = []

    for wf_name in sorted(LEGACY_WORKFLOWS):
        wf_path = WORKFLOWS_DIR / wf_name
        if not wf_path.exists():
            continue

        content = wf_path.read_text(encoding="utf-8")
        hardcoded = extract_hardcoded_versions(content)

        for lineno, version, line_ctx in hardcoded:
            if version not in matrix_versions:
                errors.append(
                    f"{wf_name}:{lineno}: legacy workflow references "
                    f"version '{version}' not in release-matrix.json "
                    f"(line: {line_ctx!r})"
                )
            else:
                warnings.append(
                    f"{wf_name}:{lineno}: legacy workflow hardcodes "
                    f"version '{version}' (subset of matrix — OK)"
                )

    return errors, warnings


def validate_other_workflows(
    matrix_versions: set[str],
) -> tuple[list[str], list[str]]:
    """Check remaining workflows for hardcoded versions not in the matrix.

    Workflows not classified as canonical or legacy are scanned for
    hardcoded version references. Versions not in the matrix are errors.
    """
    errors: list[str] = []
    warnings: list[str] = []

    known_workflows = CANONICAL_DYNAMIC_WORKFLOWS | LEGACY_WORKFLOWS

    if not WORKFLOWS_DIR.exists():
        return errors, warnings

    for wf_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        if wf_path.name in known_workflows:
            continue

        content = wf_path.read_text(encoding="utf-8")
        hardcoded = extract_hardcoded_versions(content)

        errors.extend(
            f"{wf_path.name}:{lineno}: references version '{version}' not in release-matrix.json (line: {line_ctx!r})"
            for lineno, version, line_ctx in hardcoded
            if version not in matrix_versions
        )
    return errors, warnings


def validate_owner_workflow_refs(matrix_path: Path) -> list[str]:
    """Verify that owner_workflow references in the matrix point to existing files."""
    errors: list[str] = []

    validated = validate_read_path(matrix_path, purpose="owner workflow check")
    with open(validated, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Support both canonical 'entries' (release matrix canonical format) and legacy 'matrix' + 'additional_artifacts'
    all_entries = data.get("entries", []) or (
        data.get("matrix", []) + data.get("additional_artifacts", [])
    )

    for i, entry in enumerate(all_entries):
        wf = entry.get("owner_workflow", "")
        if not wf:
            continue

        wf_path = REPO_ROOT / wf
        if not wf_path.exists():
            errors.append(
                f"Matrix entry {i}: owner_workflow '{wf}' does not exist"
            )

    return errors


def main() -> int:
    """Run workflow matrix consumer validation.

    Returns 0 on success, 1 if errors found.
    """
    if not MATRIX_PATH.exists():
        print(f"ERROR: Matrix file not found: {MATRIX_PATH}", file=sys.stderr)
        return 1

    if not WORKFLOWS_DIR.exists():
        print(
            f"ERROR: Workflows directory not found: {WORKFLOWS_DIR}",
            file=sys.stderr,
        )
        return 1

    matrix_versions = load_matrix_versions(MATRIX_PATH)
    if not matrix_versions:
        print(
            "ERROR: No NGINX versions found in release-matrix.json",
            file=sys.stderr,
        )
        return 1

    all_errors: list[str] = []
    all_warnings: list[str] = []

    # 1. Canonical workflows must use dynamic resolution
    errors, warnings = validate_canonical_workflows()
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    # 2. Legacy workflows must reference known versions
    errors, warnings = validate_legacy_workflows(matrix_versions)
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    # 3. Other workflows should not hardcode unknown versions
    errors, warnings = validate_other_workflows(matrix_versions)
    all_errors.extend(errors)
    all_warnings.extend(warnings)

    # 4. owner_workflow references in matrix point to real files
    owner_errors = validate_owner_workflow_refs(MATRIX_PATH)
    all_errors.extend(owner_errors)

    # Report results
    if all_warnings:
        print("Warnings:", file=sys.stderr)
        for i, warning in enumerate(all_warnings, 1):
            print(f"  {i}. {warning}", file=sys.stderr)
        print(file=sys.stderr)

    if all_errors:
        print(
            "Workflow matrix consumer check FAILED — found inconsistencies "
            "between workflows and release-matrix.json:",
            file=sys.stderr,
        )
        for i, error in enumerate(all_errors, 1):
            print(f"  {i}. {error}", file=sys.stderr)
        return 1

    # Success summary
    print("Workflow matrix consumer check PASSED.")
    print(f"  Matrix NGINX versions: {sorted(matrix_versions)}")
    print(f"  Canonical dynamic workflows: {len(CANONICAL_DYNAMIC_WORKFLOWS)}")
    print(f"  Legacy workflows checked: {len(LEGACY_WORKFLOWS)}")
    print(
        f"  Other workflows scanned: "
        f"{sum(1 for _ in WORKFLOWS_DIR.glob('*.yml')) - len(CANONICAL_DYNAMIC_WORKFLOWS) - len(LEGACY_WORKFLOWS)}"
    )
    if all_warnings:
        print(f"  Warnings: {len(all_warnings)} (non-blocking)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
