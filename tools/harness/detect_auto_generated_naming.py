#!/usr/bin/env python3
"""Detect auto-generated numeric-suffix naming (Rule: meaningful-naming).

Function and method names must use descriptive identifiers, not
auto-generated numeric disambiguators. This detector blocks two smells:

1. IDE "Extract Method" artifacts — any function or method whose name
   contains the substring ``_extracted_from_`` (e.g.
   ``_extracted_from_test_foo_3``). These are unambiguous IDE leftovers
   produced by PyCharm / similar tooling and must never be committed.

2. Generic numeric-index suffixes on otherwise-descriptive names, e.g.
   ``helper_1``, ``do_thing_2``, ``render_3``. A small trailing integer
   used purely as a disambiguator (the classic ``XXX_1`` / ``XXX_2``
   smell) carries no semantic meaning and should be replaced with a
   descriptive name.

The following *semantic* numeric suffixes are intentionally exempt because
they encode real meaning, not an auto-generated index:

  - exit-code semantics:           ``..._exit_0``, ``..._exit_2``
  - spec / version tags:           ``validate_module_benchmark_091``
  - ADR references:                ``check_adr_0007``
  - semver embedded in a name:     ``test_schema_version_is_1_0_0``

Nginx-native code (stock nginx sources and build artifacts under
``target/``) is out of scope per project convention; such names are
exempt, so C/H files are not scanned.

Detection is conservative: the ``_extracted_from_`` pattern is always an
error. The generic ``_N`` index suffix is a warning by default and is
promoted to an error under ``--strict``.

Usage:
  python3 tools/harness/detect_auto_generated_naming.py [directory]
  python3 tools/harness/detect_auto_generated_naming.py tools/ --strict

Exit codes:
  0 — no findings (or only warnings)
  1 — one or more errors found (always includes _extracted_from_)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path


REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Skip patterns — build artifacts and vendored / third-party trees
# ---------------------------------------------------------------------------

_SKIP_DIRS = (
    "/target/",
    "/.venv/",
    "/venv/",
    "site-packages",
    "node_modules",
    "/.git/",
)

# Files/dirs that legitimately embed _extracted_from_ / XXX_N sample names
# as test fixtures (e.g. this detector's own unit-test file). These are not
# real code smells, so they are excluded from scanning.
_SKIP_PATH_FRAGMENTS = (
    "tools/harness/tests/test_detect_auto_generated_naming.py",
)

_SCANNED_SUFFIXES = (".py", ".rs", ".sh", ".bash")

# ---------------------------------------------------------------------------
# Name-extraction patterns
# ---------------------------------------------------------------------------

PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")

RUST_FN_RE = re.compile(
    r"^\s*(?:pub\s*|pub\s*\([^)]*\)\s*|const\s*|unsafe\s*|async\s*)*"
    r"fn\s+([A-Za-z_]\w*)\s*\(",
)

# Shell: `function name {` / `function name()` and `name() {`
SHELL_FUNC_RE = re.compile(
    r"(?:function\s+([A-Za-z_]\w*)\s*(?:\(\))?\s*\{?)"
    r"|(?:^\s*([A-Za-z_]\w*)\s*\(\s*\)\s*\{)",
)


# ---------------------------------------------------------------------------
# Classification patterns
# ---------------------------------------------------------------------------

# Unambiguous IDE extraction artifact — always an error.
EXTRACTED_FROM_RE = re.compile(r"_extracted_from_")

# Generic numeric-index suffix: 1 or 2 digit trailing integer disambiguator.
GENERIC_INDEX_SUFFIX_RE = re.compile(r"_(\d{1,2})$")

# Exempt: semver embedded in a name, e.g. ``_1_0_0``.
SEMVER_SUFFIX_RE = re.compile(r"_\d+_\d+_\d+$")

# Exempt: exit-code semantics, e.g. ``_exit_0``, ``_exits_2``,
# ``_error_code_8`` — the trailing integer is the actual code under test.
EXIT_CODE_RE = re.compile(r"_(?:exit|exits|error_code)_\d+$")

# Exempt: zero-padded version/spec/ADR codes (3+ digits), e.g. ``_091``, ``_0007``.
VERSION_CODE_RE = re.compile(r"_\d{3,}$")

# Exempt: referenced standards / charsets, e.g. ``iso_8859_1``.
STANDARD_RE = re.compile(r"_(?:iso|rfc|utf|ascii|unicode)_\d+(?:_\d+)*$", re.IGNORECASE)

# Exempt: canonical demo numbering in example files, e.g. ``example_1``.
EXAMPLE_RE = re.compile(r"^example_\d+$")

# Exempt: explicit semantic tokens that explain the trailing digits.
SEMANTIC_TOKEN_RE = re.compile(
    r"(version|spec|adr|release|schema|changelog|v\d)", re.IGNORECASE,
)


def is_exempt_numeric_suffix(name: str) -> bool:
    """Return True when a trailing ``_N`` is a semantic (allowed) suffix."""
    if SEMVER_SUFFIX_RE.search(name):
        return True
    if EXIT_CODE_RE.search(name):
        return True
    if VERSION_CODE_RE.search(name):
        return True
    if STANDARD_RE.search(name):
        return True
    return True if EXAMPLE_RE.match(name) else bool(SEMANTIC_TOKEN_RE.search(name))


def classify_name(name: str) -> str | None:
    """Classify a single function/method name.

    Returns:
        ``"extracted"`` for IDE extraction artifacts (always an error),
        ``"index"`` for a generic numeric-index suffix (warning / strict error),
        or ``None`` when the name is clean / exempt.
    """
    if EXTRACTED_FROM_RE.search(name):
        return "extracted"
    if GENERIC_INDEX_SUFFIX_RE.search(name):
        return None if is_exempt_numeric_suffix(name) else "index"
    return None


def _iter_names(content: str, suffix: str):
    """Yield (name, line_number) for every function/method def in *content*."""
    for lineno, line in enumerate(content.splitlines(), start=1):
        if suffix == ".py":
            if m := PY_DEF_RE.match(line):
                yield m.group(1), lineno
        elif suffix == ".rs":
            if m := RUST_FN_RE.match(line):
                yield m.group(1), lineno
        elif suffix in (".sh", ".bash"):
            if m := SHELL_FUNC_RE.search(line):
                yield (m.group(1) or m.group(2)), lineno


def _scan_file(path: Path, strict: bool) -> tuple[list[str], list[str]]:
    """Scan one file; return (errors, warnings)."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return [], []

    errors: list[str] = []
    warnings: list[str] = []
    rel = _display_path(path)

    for name, lineno in _iter_names(content, path.suffix):
        verdict = classify_name(name)
        if verdict is None:
            continue
        if verdict == "extracted":
            errors.append(
                f"  ERROR   {rel}:{lineno} — function '{name}' is an IDE "
                f"auto-extraction artifact (_extracted_from_); give it a "
                f"descriptive name"
            )
        elif verdict == "index":
            message = (
                f"  {('ERROR' if strict else 'WARNING')} {rel}:{lineno} — "
                f"function '{name}' uses a generic numeric-index suffix "
                f"(XXX_N); rename with a descriptive identifier"
            )
            if strict:
                errors.append(message)
            else:
                warnings.append(message)

    return errors, warnings


def _display_path(path: Path) -> str:
    """Return a repo-relative display string for *path*."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _resolve_scan_root() -> Path:
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        return Path(validate_read_path(sys.argv[1]))
    return REPO_ROOT


def _parse_strict() -> bool:
    return "--strict" in sys.argv[1:]


def _should_skip(path: Path) -> bool:
    """Return True when *path* should be excluded from scanning."""
    str_path = str(path)
    if any(skip in str_path for skip in _SKIP_DIRS):
        return True
    return any(frag in str_path for frag in _SKIP_PATH_FRAGMENTS)


def _collect_findings(
    root: Path, strict: bool
) -> tuple[list[str], list[str]]:
    """Walk *root* and return aggregated (errors, warnings)."""
    all_errors: list[str] = []
    all_warnings: list[str] = []

    for suffix in _SCANNED_SUFFIXES:
        for path in root.rglob(f"*{suffix}"):
            if _should_skip(path):
                continue
            errors, warnings = _scan_file(path, strict)
            all_errors.extend(errors)
            all_warnings.extend(warnings)

    return all_errors, all_warnings


def _report_findings(
    root: Path, all_errors: list[str], all_warnings: list[str]
) -> int:
    """Print findings and return the appropriate exit code."""
    if all_errors:
        print(f"Found {len(all_errors)} auto-generated naming error(s):")
        for err in all_errors:
            print(err)
        if all_warnings:
            print(f"\n{len(all_warnings)} additional warning(s):")
            for warn in all_warnings:
                print(warn)
        return 1

    if all_warnings:
        print(f"Found {len(all_warnings)} auto-generated naming warning(s):")
        for warn in all_warnings:
            print(warn)
        return 0

    print(f"OK: no auto-generated numeric-suffix naming in {root}")
    return 0


def main() -> int:
    strict = _parse_strict()
    root = _resolve_scan_root()

    if not root.exists():
        print(f"Scan root not found: {root}")
        return 0

    all_errors, all_warnings = _collect_findings(root, strict)
    return _report_findings(root, all_errors, all_warnings)


if __name__ == "__main__":
    sys.exit(main())
