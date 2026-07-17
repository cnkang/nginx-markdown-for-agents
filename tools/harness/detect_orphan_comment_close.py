#!/usr/bin/env python3
"""detect_orphan_comment_close.py — Detect orphan */ without matching /*

Rule (c-safety, compile-safety): A missing comment opening /* during edits
leaves a bare */ that causes a C syntax error and blocks compilation.
This detector catches the pattern at write time before it reaches the
compiler, preventing broken builds from being committed.

Detection strategy:
  For each C source file (*.c, *.h), scan character by character while
  tracking block comment state.  A */ that appears outside a block comment
  is an orphan and is flagged.

  String literals ("..." and '...') are skipped to avoid false positives
  from */ sequences inside string constants.  Line comments (//) are also
  handled, though NGINX style avoids them.

Usage:
  python3 tools/harness/detect_orphan_comment_close.py [directory]
    directory defaults to components/nginx-module/src

Exit codes:
  0 — no orphan comment closers found
  1 — one or more orphan comment closers found
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add tools directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.path_validation import validate_read_path

C_EXTENSIONS = {".c", ".h"}


def _handle_in_string(content: str, i: int, length: int, string_char: str) -> tuple[int, bool, str | None]:
    """Process characters inside a string literal. Returns (new_i, in_string, string_char)."""
    char = content[i]
    if char == "\\" and i + 1 < length:
        return i + 2, True, string_char
    if char == string_char:
        return i + 1, False, None
    return i + 1, True, string_char


def _handle_in_comment(content: str, i: int, length: int) -> tuple[int, bool]:
    """Process characters inside a block comment. Returns (new_i, in_block_comment)."""
    if content[i] == "*" and i + 1 < length and content[i + 1] == "/":
        return i + 2, False
    return i + 1, True


def _handle_normal(content: str, i: int, length: int) -> tuple[int, bool, bool, str | None, int]:
    """Process characters outside comments and strings.

    Returns (new_i, in_block_comment, in_string, string_char, line_delta).
    line_delta is 0 unless an orphan */ was found (in which case it's 0 too,
    but the caller should check the special return value -1 for orphan detection).
    """
    char = content[i]

    # Line comment
    if char == "/" and i + 1 < length and content[i + 1] == "/":
        new_i = i
        while new_i < length and content[new_i] != "\n":
            new_i += 1
        return new_i, False, False, None, 0

    # Block comment open
    if char == "/" and i + 1 < length and content[i + 1] == "*":
        return i + 2, True, False, None, 0

    # String literal start
    if char == '"' or char == "'":
        return i + 1, False, True, char, 0

    # Orphan */ found
    if char == "*" and i + 1 < length and content[i + 1] == "/":
        return i + 2, False, False, None, -1

    return i + 1, False, False, None, 0


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return a list of (line_number, line_text) for orphan */ found."""
    findings: list[tuple[int, str]] = []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return findings

    in_block_comment = False
    in_string = False
    string_char: str | None = None
    line_num = 1
    i = 0
    length = len(content)

    while i < length:
        char = content[i]

        if char == "\n":
            line_num += 1

        if in_string and string_char is not None:
            i, in_string, string_char = _handle_in_string(content, i, length, string_char)
            continue

        if in_block_comment:
            i, in_block_comment = _handle_in_comment(content, i, length)
            continue

        i, in_block_comment, in_string, string_char, orphan = _handle_normal(
            content, i, length
        )
        if orphan == -1:
            lines = content.split("\n")
            line_text = lines[line_num - 1] if 0 < line_num <= len(lines) else ""
            findings.append((line_num, line_text))

    return findings


def main() -> None:
    if len(sys.argv) > 1:
        src_dir = Path(validate_read_path(sys.argv[1]))
    else:
        src_dir = Path(__file__).resolve().parents[2] / "components" / "nginx-module" / "src"

    if not src_dir.exists():
        print(f"ERROR: directory not found: {src_dir}", file=sys.stderr)
        sys.exit(1)

    all_findings: list[str] = []

    for path in sorted(src_dir.rglob("*")):
        if path.suffix not in C_EXTENSIONS or not path.is_file():
            continue
        findings = _scan_file(path)
        for line_num, line_text in findings:
            all_findings.append(
                f"{path}: {line_num}: orphan */ without matching /*\n  {line_text.strip()}"
            )

    if all_findings:
        print(
            f"FAIL: found {len(all_findings)} orphan comment closer(s)",
            file=sys.stderr,
        )
        for finding in all_findings:
            print(f"  ERROR: {finding}", file=sys.stderr)
        sys.exit(1)

    print("OK: no orphan comment closers found")
    sys.exit(0)


if __name__ == "__main__":
    main()