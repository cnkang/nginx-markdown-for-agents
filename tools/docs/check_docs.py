#!/usr/bin/env python3
"""Repository documentation checks.

Runs lightweight checks for maintained Markdown docs (excluding docs/archive and
gitignored paths/files). The scan includes tracked Markdown files plus
untracked non-ignored Markdown files.
- local link validity
- heading hierarchy consistency (ignoring code fences)
- non-English Han characters (enforces English docs policy for canonical docs)
- duplicate doc sync (via tools/docs/check_duplicate_docs.py)
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from datetime import date
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.lib.executable_validation import (  # noqa: E402
    resolve_approved_executable,
)
from tools.lib.semver import is_release_version  # noqa: E402
ARCHIVE_SEGMENT = "docs/archive/"
README_FILENAME = "README.md"
CHANGELOG_FILENAME = "CHANGELOG.md"
CHINESE_README = "README_zh-CN.md"
BREAKING_CHANGES_GUIDE = "docs/guides/{version}-breaking-changes.md"
MIGRATION_GUIDE = "docs/guides/MIGRATION-{version}.md"
UPGRADE_AND_ROLLBACK_NOTE = "docs/releases/{version}-upgrade-and-rollback.md"
MAINTAINED_ROOT_DOCS = {"AGENTS.md", README_FILENAME, CHINESE_README}
LINK_RE = re.compile(r"(!?\[[^\]]+\]\(([^)]+)\))")
HAN_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
MARKDOWN_HEADING_LINE_RE = re.compile(
    r"^ {0,3}##(?!#)[^\n]*$", re.MULTILINE
)
SPEC_INDEX_RE = re.compile(
    r"\bspecs?\s*0*\d+(?:\s*[-–]\s*0*\d+)?\b",
    re.IGNORECASE,
)
KIRO_PATH_RE = re.compile(r"(?P<path>\.kiro/[A-Za-z0-9._/*-]+)")
ALLOWED_KIRO_PATHS = {".kiro/nginx-development-guide.md"}
BOOLEAN_TRUSTED_PROXY_RE = re.compile(
    r"\bmarkdown_trusted_proxies\s+(?:on|true|yes)\s*;",
    re.IGNORECASE,
)
_CHANGELOG_VERSION = (
    r"\d+\.\d+\.\d+(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
UNRELEASED_CHANGELOG_RE = re.compile(
    r"^ {0,3}##[ \t]+\[(?P<version>" + _CHANGELOG_VERSION + r")\]"
    r"[ \t]*-[ \t]*Unreleased(?:[ \t]+candidate)?[ \t]*$",
    re.MULTILINE,
)

DATED_CHANGELOG_RE = re.compile(
    r"^ {0,3}##[ \t]+\[(?P<version>" + _CHANGELOG_VERSION + r")\]"
    r"[ \t]*-[ \t]*(?P<date>\d{4}-\d{2}-\d{2})[ \t]*$",
    re.MULTILINE,
)
VERSION_HEADING_PREFIX_RE = re.compile(
    r"^ {0,3}##[ \t]+\[[vV]?(?a:\d)[^\]\r\n]*\]", re.MULTILINE
)


def is_maintained_markdown(rel_path: str) -> bool:
    """Return whether a markdown path belongs to maintained repo truth surfaces."""
    if rel_path in MAINTAINED_ROOT_DOCS:
        return True
    return rel_path.startswith("docs/") and not rel_path.startswith(ARCHIVE_SEGMENT)


def iter_markdown_files() -> list[Path]:
    """Return a sorted list of maintained Markdown files in the repository.

    Uses ``git ls-files`` when available, falling back to ``rglob`` otherwise.
    Only files under ``docs/`` (excluding the archive subtree) and top-level
    project docs (README, CHANGELOG, etc.) are included.
    """
    git = resolve_approved_executable("git")
    try:
        if git is None:
            raise FileNotFoundError("approved git executable is unavailable")
        proc = subprocess.run(
            [git, "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0:
            candidates = {
                (ROOT / rel.strip())
                for rel in proc.stdout.splitlines()
                if rel.strip()
            }
        else:
            candidates = set(ROOT.rglob("*.md"))
    except FileNotFoundError:
        candidates = set(ROOT.rglob("*.md"))

    return sorted(
        p
        for p in candidates
        if p.is_file()
        and is_maintained_markdown(p.relative_to(ROOT).as_posix())
    )


def get_git_tracked_paths() -> set[str]:
    """Return the set of file paths tracked by git in the repository.

    Falls back to an empty set if git is unavailable or the command fails.
    """
    git = resolve_approved_executable("git")
    if git is None:
        return set()
    try:
        proc = subprocess.run(
            [git, "ls-files"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return set()
        return {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    except FileNotFoundError:
        return set()


def _fence_marker(line: str) -> tuple[str, int, str] | None:
    """Return the fence marker a line carries, or None when it carries none.

    A fence is three or more backticks or tildes with at most three leading
    spaces.  A backtick fence may carry an info string, but not one containing a
    backtick, so such a line is ordinary text.  The third element is whatever
    follows the run, which a closing fence must leave empty.
    """
    indent = len(line) - len(line.lstrip(" "))
    body = line[indent:]
    char = body[:1]
    if char not in ("`", "~") or indent > 3:
        return None
    run = len(body) - len(body.lstrip(char))
    if run < 3:
        return None
    trailing = body[run:].strip()
    return None if char == "`" and "`" in trailing else (char, run, trailing)


def iter_lines_with_fences(text: str) -> list[tuple[int, str, bool]]:
    """Return every line, marking the fence markers themselves.

    `iter_unfenced_lines` deliberately hides fenced blocks, but a caller that
    assembles multi-line items has to know where a block starts so it can end that
    block there.
    """
    found: list[tuple[int, str, bool]] = []
    open_char: str | None = None
    open_len = 0
    for line_no, line in enumerate(text.splitlines(), 1):
        marker = _fence_marker(line)
        if marker is None:
            if open_char is None:
                found.append((line_no, line, False))
            continue
        open_char, open_len = _next_fence_state(marker, open_char, open_len)
        found.append((line_no, line, True))
    return found


def _next_fence_state(
    marker: tuple[str, int, str], open_char: str | None, open_len: int
) -> tuple[str | None, int]:
    """Return the fence state after a marker line.

    A marker outside a block opens one; a marker of the same character, at least
    as long, with nothing after the run, closes it.
    """
    char, run, trailing = marker
    if open_char is None:
        return char, run
    if char == open_char and not trailing and run >= open_len:
        return None, 0
    return open_char, open_len


def iter_unfenced_lines(text: str) -> list[tuple[int, str]]:
    """Extract lines that are outside fenced code blocks.

    Returns a list of ``(line_number, line_text)`` tuples for lines not inside
    a fenced code block.  Both fence styles count: backticks and tildes are
    equally valid in Markdown, and a block opened with one must be closed with
    the same marker, so the opener is remembered rather than toggled.
    """
    lines: list[tuple[int, str]] = []
    open_char: str | None = None
    open_len = 0

    for line_no, line in enumerate(text.splitlines(), 1):
        marker = _fence_marker(line)
        if marker is None:
            if open_char is None:
                lines.append((line_no, line))
            continue
        open_char, open_len = _next_fence_state(marker, open_char, open_len)

    return lines


def normalized_link_target(raw_target: str) -> str:
    """Normalize a Markdown link target for local-file existence checks.

    Strips angle brackets, discards external URLs (http/https/mailto) and
    fragment-only references, and removes trailing fragments/query strings.
    Returns the normalized relative path or an empty string for external
    or fragment-only targets.
    """
    target = raw_target.strip().strip("<>")
    if target.startswith("#"):
        return ""
    scheme = urlsplit(target).scheme.lower()
    if scheme in {"http", "https", "mailto"}:
        return ""
    return target.split("#", 1)[0].split("?", 1)[0]


def check_links(files: list[Path]) -> list[str]:
    """Check that local Markdown link targets exist on disk.

    Scans each file for ``[text](target)`` links outside code fences and
    reports any target whose normalized path does not resolve to an
    existing file relative to the link's source directory.
    """
    errors: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in iter_unfenced_lines(text):
            line_no_code = re.sub(r"`[^`]*`", "", line)
            for _, target in LINK_RE.findall(line_no_code):
                p = normalized_link_target(target)
                if not p:
                    continue
                if not (f.parent / p).resolve().exists():
                    errors.append(f"{f}:{line_no}: broken link target '{p}'")
    return errors


def check_heading_hierarchy(files: list[Path]) -> list[str]:
    """Check that heading levels do not skip (e.g., H1 directly to H3).

    Returns a list of error messages for files where a heading jumps more
    than one level from the previous heading.
    """
    errors: list[str] = []
    for f in files:
        prev = 0
        text = f.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in iter_unfenced_lines(text):
            s = line.strip()
            if not s.startswith("#"):
                continue
            level = len(s) - len(s.lstrip("#"))
            if prev and level > prev + 1:
                errors.append(
                    f"{f}:{line_no}: heading jumps from H{prev} to H{level}"
                )
            prev = level
    return errors


def check_english_policy(files: list[Path]) -> list[str]:
    """Check that non-README_zh-CN maintained docs contain no CJK characters.

    Returns a list of ``file:line:content`` entries for lines containing
    Han script characters in files other than ``README_zh-CN.md``.
    """
    errors: list[str] = []
    for f in files:
        if f.name == CHINESE_README:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if HAN_RE.search(line):
                errors.append(f"{f}:{line_no}:{line.strip()}")
    return errors


def check_duplicate_sync() -> list[str]:
    """Run the duplicate-document sync checker and return any errors.

    Delegates to ``check_duplicate_docs.py`` and surfaces its stderr/stdout
    as error entries if the subprocess exits non-zero.
    """
    script = ROOT / "tools" / "docs" / "check_duplicate_docs.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode == 0:
        return []
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return [f"duplicate-sync: {line}" for line in output.splitlines() if line.strip()]


def check_operator_config_examples(files: list[Path]) -> list[str]:
    """Reject examples that reintroduce the removed boolean proxy trust model."""
    errors: list[str] = []
    for doc in files:
        text = doc.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in enumerate(text.splitlines(), 1):
            if BOOLEAN_TRUSTED_PROXY_RE.search(line):
                errors.append(
                    f"{doc}:{line_no}: markdown_trusted_proxies requires at least "
                    "one trusted proxy CIDR (or off), not a boolean value"
                )
    return errors


def _find_unreleased_changelog_line(changelog: str) -> tuple[str | None, list[str]]:
    """Return (version, errors) for the first unreleased changelog heading.

    Every unreleased-like heading is inspected, not just the first match:
    a valid heading followed later by a malformed one must still fail
    closed, otherwise a typo'd line could silently survive the gate.  A
    well-formed line yields its version; a heading that merely starts like
    an unreleased line but carries an unexpected suffix produces an
    explanatory error.

    A second well-formed Unreleased heading for the same version is also an
    error: the pending-state checks examine one section, so a duplicate
    could carry a contradictory claim the gate never reads.
    """
    version: str | None = None
    errors: list[str] = []
    changelog = _without_fenced_blocks(changelog)
    for match in MARKDOWN_HEADING_LINE_RE.finditer(changelog):
        line = match.group(0)
        valid = UNRELEASED_CHANGELOG_RE.match(line)
        if valid is not None:
            found = valid.group("version")
            if not is_release_version(found):
                errors.append(
                    f"{CHANGELOG_FILENAME}: malformed unreleased heading "
                    f"{line.strip()!r}; invalid release identity {found!r}"
                )
                continue
            if version is None:
                version = found
            elif found == version:
                errors.append(
                    f"{CHANGELOG_FILENAME}: duplicate Unreleased heading for "
                    f"{found}; keep a single Unreleased section so the "
                    "pending-state checks cover every pending claim"
                )
            else:
                errors.append(
                    f"{CHANGELOG_FILENAME}: multiple Unreleased headings "
                    f"({version} and {found}); keep a single Unreleased "
                    "section"
                )
            continue
        if "unreleased" in line.lower():
            errors.append(
                f"{CHANGELOG_FILENAME}: malformed unreleased heading "
                f"{line.strip()!r}; expected "
                "'## [<version>] - Unreleased' or '## [<version>] - "
                "Unreleased candidate' without suffix"
            )
    return (None, errors) if errors else (version, [])


def check_release_status_consistency(
    changelog_path: Path,
    project_status_path: Path,
    release_notes_path: Path | None = None,
) -> list[str]:
    """Keep an unreleased release line from being presented as stable."""
    changelog = changelog_path.read_text(encoding="utf-8", errors="ignore")
    version, errors = _find_unreleased_changelog_line(changelog)
    if version is None:
        return errors

    project_status = project_status_path.read_text(
        encoding="utf-8",
        errors="ignore",
    )
    section_match = re.search(
        rf"^### Current Release Line {re.escape(version)}\s*$"
        rf"(?P<body>.*?)(?=^### |\Z)",
        project_status,
        re.MULTILINE | re.DOTALL,
    )
    if section_match is None:
        return [
            f"{project_status_path}: missing Current Release Line {version} section"
        ]

    status = section_match["body"]
    errors: list[str] = []
    if _has_stable_release_claim(status):
        errors.append(
            f"{project_status_path}: unreleased {version} cannot be marked stable"
        )
    if not re.search(
        r"\b(?:unreleased|development|release[- ]candidate)\b",
        status,
        re.IGNORECASE,
    ):
        errors.append(
            f"{project_status_path}: unreleased {version} must be identified as "
            "development or release-candidate status"
        )

    if release_notes_path is not None:
        release_notes = release_notes_path.read_text(
            encoding="utf-8",
            errors="ignore",
        )
        if re.search(r"\*\*Status\*\*:\s*Stable release", release_notes):
            errors.append(
                f"{release_notes_path}: unreleased {version} cannot have stable "
                "release notes"
            )
        if not re.search(
            r"\*\*Status\*\*:\s*(?:Pending release|Release candidate|Unreleased)",
            release_notes,
            re.IGNORECASE,
        ):
            errors.append(
                f"{release_notes_path}: unreleased {version} must be identified as "
                "pending or release-candidate status"
            )
    return errors


def _validate_kiro_reference(raw: str, tracked: set[str]) -> str | None:
    """Validate a single .kiro/ path reference against repository tracking.

    Returns an error message string if the reference violates policy
    (untracked target, directory/glob reference, etc.), or ``None`` if
    the reference is acceptable.
    """
    if raw in ALLOWED_KIRO_PATHS:
        if tracked and raw not in tracked:
            return f"referenced path '{raw}' is not tracked"
        return None

    if raw.endswith("/") or "*" in raw:
        return (
            f"avoid directory/glob reference '{raw}'; "
            "link tracked files instead"
        )

    if tracked and raw not in tracked:
        return (
            f"internal path '{raw}' is not tracked; "
            "remove or replace with tracked file reference"
        )
    return None


def check_internal_reference_policy(
    files: list[Path], tracked_paths: set[str] | None = None
) -> list[str]:
    """Reject internal/untracked reference patterns in maintained docs.

    Policy:
    - Avoid numbered internal shorthand like "spec 12" or "specs 12-18".
    - Allow relative references to tracked files under `.kiro/`.
    - Reject `.kiro/` directory references, globs, and untracked targets.
    """
    errors: list[str] = []
    tracked = tracked_paths if tracked_paths is not None else get_git_tracked_paths()

    for f in files:
        text = f.read_text(encoding="utf-8", errors="ignore")
        for line_no, line in iter_unfenced_lines(text):
            if SPEC_INDEX_RE.search(line):
                errors.append(
                    f"{f}:{line_no}: avoid internal numbered references like "
                    "'spec X'; describe release scope/capability directly"
                )

            for m in KIRO_PATH_RE.finditer(line):
                raw = m.group("path").rstrip(".,:;)]}\"'")
                reference_error = _validate_kiro_reference(raw, tracked)
                if reference_error is not None:
                    errors.append(f"{f}:{line_no}: {reference_error}")
    return errors


def _parse_document_update_version(
    version: str,
) -> tuple[tuple[int, ...], str]:
    """Return a sortable key for a document-update version cell.

    Every version maps to the same key shape (numeric parts, trailing
    suffix), so descending sort comparisons never mix int and str
    elements.  Numeric-only versions produce an empty suffix, which the
    table sorter ranks above any release-candidate suffix so a release
    orders above its rc builds while preserving full numeric ordering
    for four-component versions.
    """
    normalized = version.strip().strip("`*[]()\"'")
    normalized = normalized.removeprefix("v")
    numeric: list[int] = []
    remainder = normalized
    while remainder:
        match = re.match(r"(\d+)", remainder)
        if match is None:
            break
        numeric.append(int(match[1]))
        remainder = remainder[match.end():]
        if remainder.startswith("."):
            remainder = remainder[1:]
            continue
        break
    return (tuple(numeric), remainder)


_DOCUMENT_UPDATES_HEADING_RE = re.compile(
    r"^ {0,3}## Document Updates[ \t]*$", re.MULTILINE | re.IGNORECASE
)
# A heading of level one or two closes the section.  A deeper heading
# (`### ...`) stays inside it, so a table filed under a sub-heading is still
# part of the same ledger.
_DOCUMENT_UPDATES_SECTION_END_RE = re.compile(r"^ {0,3}#{1,2}(?:\s|$)", re.MULTILINE)


def _document_updates_section(content: str) -> str:
    """Return the text from the Document Updates heading to the next H2."""
    match = _DOCUMENT_UPDATES_HEADING_RE.search(content)
    if match is None:
        return ""
    remainder = content[match.end() :]
    end = _DOCUMENT_UPDATES_SECTION_END_RE.search(remainder)
    return remainder if end is None else remainder[: end.start()]


def _document_update_tables(content: str) -> list[list[str]]:
    """Return every Markdown table inside the Document Updates section.

    Reading only the first table let a second table in the same section carry
    any row order unnoticed, so each contiguous run of table rows is returned
    as its own table.  A table placed under this heading is ledger data by
    definition, which is what makes the ordering contract enforceable.
    """
    tables: list[list[str]] = []
    current: list[str] = []
    for line in _document_updates_section(content).splitlines():
        if line.strip().startswith("|"):
            current.append(line)
            continue
        if current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _document_update_table_lines(content: str) -> list[str]:
    """Extract every table row under a Document Updates heading.

    The union of the section's tables: the ignore-set consumers treat a line
    returned here as recorded history rather than prose, and every ledger
    table in the section is history.
    """
    return [
        line
        for table_lines in _document_update_tables(content)
        for line in table_lines
    ]


def _document_update_rows_are_sorted(table_lines: list[str]) -> bool:
    """Return whether table data rows descend by version and date."""
    if len(table_lines) < 3:
        return True

    data_lines = table_lines[2:]
    rows = []
    for line in data_lines:
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) >= 3:
            version_key = _parse_document_update_version(cells[1])
            rows.append((version_key, cells[2], line))

    retained_rows = [row[2] for row in rows]
    expected = [
        row[2]
        for row in sorted(
            rows,
            key=lambda row: (
                row[0][0],
                (
                    0
                    if row[0][1].lstrip("-").lower().startswith("rc")
                    else (1 if row[0][1] == "" else 2)
                ),
                row[0][1],
                row[1],
            ),
            reverse=True,
        )
    ]
    return expected == retained_rows


def check_document_updates_order(files: list[Path]) -> list[str]:
    """Verify every Document Updates table uses descending version/date order.

    The whole section is checked, not just its first table: a second table in
    the same section is part of the same ledger, so it is held to the same
    ordering contract.
    """
    errors: list[str] = []

    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for table_number, table_lines in enumerate(
            _document_update_tables(content), 1
        ):
            if not _document_update_rows_are_sorted(table_lines):
                errors.append(
                    f"{f}: '## Document Updates' table {table_number} rows must "
                    "be maintained in descending chronological order (highest "
                    "version and newest date on top)"
                )

    return errors


_SPELLED_FAMILY_COUNTS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
_SPELLED_FAMILY_COUNT_PATTERN = "|".join(
    sorted(_SPELLED_FAMILY_COUNTS, key=len, reverse=True)
)
FAMILY_COUNT_RE = re.compile(
    # The count must not be the trailing digit of a dotted version number
    # (for example the "2" in "0.9.2 family names" or the "4" in
    # "Prometheus text 0.0.4 family catalog"); such tokens are not
    # metric-family count claims.
    #
    # A plural ("sixteen metric families") or a hyphen-bound singular
    # ("ten-family v1 freeze", "frozen 10-family") states a count.  A
    # space-separated singular does not: prose like "belongs to exactly
    # one metric family" names one entry, not the contract's family total.
    rf"(?<![.\d])\b(?P<count>\d{{1,3}}|{_SPELLED_FAMILY_COUNT_PATTERN})"
    r"(?:[\s-]+(?:v1[\s-]+)?(?:metric[\s-]+)?families\b"
    r"|-(?:v1-)?(?:metric-)?family\b)",
    re.IGNORECASE,
)
HISTORICAL_FAMILY_CONTEXT_RE = re.compile(
    r"historical|legacy|renamed|removed|retired|0\.9\.1|previous|earlier|older",
    re.IGNORECASE,
)


def _claimed_family_count(line: str) -> int | None:
    """Extract a numeric or spelled-out metric-family count claim."""
    claimed = FAMILY_COUNT_RE.search(line)
    if claimed is None:
        return None
    token = claimed.group("count").casefold()
    return int(token) if token.isdecimal() else _SPELLED_FAMILY_COUNTS[token]


def _family_count_mismatch(
    path: Path, line_no: int, line: str, family_count: int
) -> str | None:
    """Return a failure message when a doc line claims a wrong count."""
    if not FAMILY_COUNT_RE.search(line):
        return None
    claimed_count = _claimed_family_count(line)
    if claimed_count is None:
        return None
    if claimed_count == family_count:
        return None
    # historical context is allowed to mention other counts
    if HISTORICAL_FAMILY_CONTEXT_RE.search(line):
        return None
    return (
        f"{path.relative_to(ROOT)}:{line_no}: claims {claimed_count} "
        f"metric families but the registry defines {family_count}"
    )


def check_metric_family_count(files: list[Path]) -> list[str]:
    """Metric-family counts claimed in docs must match the registry.

    The frozen 0.9.2 surface is the family list in
    schemas/metrics-v1.registry.json; docs must not restate a different count.
    Lines describing historical/pre-freeze surfaces are allowed to reference
    removed families, so only claims about the current contract are checked.
    """
    registry_path = ROOT / "schemas/metrics-v1.registry.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"cannot read metrics registry {registry_path}"]
    families = [
        entry
        for entry in registry.get("families", [])
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]
    family_count = len(families)
    failures: list[str] = []
    for path in files:
        if not path.is_relative_to(ROOT / "docs"):
            continue
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            failure = _family_count_mismatch(path, line_no, line, family_count)
            if failure is not None:
                failures.append(failure)
    return failures


_CHECKLIST_SHA_RE = re.compile(
    r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b", re.IGNORECASE
)
_CHECKLIST_CLAIM_RE = re.compile(
    r"current (?:head|candidate|branch head)s?\b|have not certified"
    r"|has not certified"
    r"|latest workflow|as of this (?:commit|writing)"
    r"|\*{0,2}status:\*{0,2}\s|candidate (?:passed|passes|satisfied)"
    r"|all required gates (?:passed|are green)"
    r"|remote workflows? (?:have|has) (?:passed|certified)"
    r"|workflow (?:set|suite) (?:is )?(?:passing|green|certified)",
    re.IGNORECASE,
)


def _list_item_indent(line: str) -> int | None:
    """Return the indentation of a task-list item, or None when it is not one."""
    return len(line) - len(line.lstrip()) if _is_task_list_line(line) else None


def _is_task_list_line(line: str) -> bool:
    """True for a Markdown task-list item, whatever marker it uses."""
    stripped = line.lstrip()
    if stripped[:1] not in ("-", "*", "+"):
        return False
    rest = stripped[1:].lstrip(" \t")
    gap = len(stripped) - 1 - len(rest)
    # A list marker may be followed by one to four spaces.
    if gap not in (1, 2, 3, 4):
        return False
    return rest.startswith("[") and rest[1:2] in (" ", "x", "X") and rest[2:3] == "]"


def check_release_checklist_is_static(files: list[Path]) -> list[str]:
    """A release checklist states requirements, never the state of a candidate.

    A checklist that names the head of the day goes stale with the next commit,
    and a stale checklist read as certification is worse than no checklist.  The
    scan covers the whole document, because status text drifts wherever it sits,
    but it ignores fenced code blocks and the Document Updates table, which
    legitimately records commit identifiers as history.
    """
    failures: list[str] = []
    for path in files:
        if not path.name.endswith("-release-checklist.md"):
            continue
        content = path.read_text(encoding="utf-8")
        history = _checklist_history(content)
        failures.extend(
            f"{path}: a requirement names a commit; bind status to the candidate-bound release evidence instead"
            for item in _checklist_items(content, history)
            if _CHECKLIST_SHA_RE.search(item)
        )
        failures.extend(
            f"{path}: states mutable candidate status; keep the checklist to requirements"
            for block in _logical_blocks(content, history)
            if _CHECKLIST_CLAIM_RE.search(block)
        )
    return failures


def _flush_task_item(items: list[str], current: list[str]) -> None:
    """Append the open item, if any, and reset the accumulator."""
    if current:
        items.append(" ".join(current))
        current.clear()


def _checklist_history(content: str) -> set[str]:
    """Return the lines belonging to the Document Updates history table."""
    return set(_document_update_table_lines(content))


THEMATIC_BREAK_RE = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})\s*$")


def _starts_block(line: str) -> bool:
    """True when a line opens a new Markdown block rather than continuing one."""
    stripped = line.lstrip()
    # `#not-a-heading` is ordinary text: an ATX heading needs the space.  A table
    # row is not treated as a boundary either, because a table is only a table
    # once its delimiter row appears, which a single line cannot show.
    if re.match(r"#{1,6}(?:\s|$)", stripped):
        return True
    if stripped.startswith(">"):
        return True
    if _fence_marker(line) is not None:
        return True
    if THEMATIC_BREAK_RE.match(stripped):
        return True
    # A list marker of any kind opens a new block.
    return bool(re.match(r"[-*+]\s", stripped)) or bool(re.match(r"\d+[.)]\s", stripped))


def _checklist_items(content: str, history: set[str]) -> list[str]:
    """Assemble every task item in a checklist, continuation lines included.

    A wrapped requirement carries part of its text on the following indented
    lines, and the pinned commit can fall on any of them.
    """
    items: list[str] = []
    current: list[str] = []
    current_indent = 0

    for _lineno, line, is_fence in iter_lines_with_fences(content):
        if is_fence:
            # A fenced block ends whatever was open; its contents are not prose.
            _flush_task_item(items, current)
            continue
        if not line.strip() or line in history:
            # A blank line ends the item: CommonMark starts a new paragraph, so
            # prose below the item is not part of it.
            _flush_task_item(items, current)
            continue
        indent = _list_item_indent(line)
        if indent is not None:
            if current and indent > current_indent:
                # A nested item is part of the requirement above it; only a
                # sibling or outer item ends the one that is open.
                current.append(line.strip())
                continue
            _flush_task_item(items, current)
            current_indent = indent
            current.append(line)
        elif current and not _starts_block(line):
            # A lazy, unindented continuation belongs to the open item, because
            # CommonMark reads it as part of the same paragraph.  A line that
            # opens another block ends the item instead.
            current.append(line.strip())
        else:
            _flush_task_item(items, current)

    _flush_task_item(items, current)
    return items


def _logical_blocks(content: str, history: set[str]) -> list[str]:
    """Group the prose into blocks so a wrapped sentence reads as one.

    A status claim wrapped over two lines has to be judged as the sentence it
    is, not as two fragments that each look harmless.
    """
    blocks: list[str] = []
    current: list[str] = []
    for _lineno, line, is_fence in iter_lines_with_fences(content):
        if is_fence:
            # A fenced block ends whatever was open; its contents are not prose.
            _flush_task_item(blocks, current)
            continue
        if not line.strip() or line in history:
            _flush_task_item(blocks, current)
            continue
        if _is_task_list_line(line):
            # A task item stands on its own.
            _flush_task_item(blocks, current)
        current.append(line.strip())
    _flush_task_item(blocks, current)
    return blocks


def _dated_heading(line: str) -> tuple[str, str] | None:
    """Return (version, date) for a dated release heading."""
    dated = DATED_CHANGELOG_RE.match(line)
    if dated is None:
        return None
    version = dated.group("version")
    return (version, dated.group("date")) if is_release_version(version) else None


def _validated_date(raw_date: str, line: str, errors: list[str]) -> bool:
    """Record an error for an impossible ISO date; return whether it is real."""
    try:
        date.fromisoformat(raw_date)
    except ValueError:
        errors.append(
            f"{CHANGELOG_FILENAME}: invalid release date {raw_date!r} in {line!r}"
        )
        return False
    return True


def _version_order_key(version: str) -> tuple:
    """Return a SemVer ordering key for a release heading version.

    A final release sorts above any of its prereleases.  Prerelease
    identifiers compare by SemVer rules: numeric identifiers below
    alphanumeric ones, and a shorter identifier list below a longer one with
    the same prefix.
    """
    core, _, prerelease = version.partition("-")
    major, minor, patch = (int(part) for part in core.split("."))
    if not prerelease:
        return (major, minor, patch, 1, ())
    identifiers = tuple(
        (0, int(identifier), "") if identifier.isdigit() else (1, 0, identifier)
        for identifier in prerelease.split(".")
    )
    return (major, minor, patch, 0, identifiers)


def _order_error(
    previous: tuple[tuple, str] | None,
    current: tuple[tuple, str],
    line: str,
) -> str | None:
    """Return a descending-order error for an out-of-order release heading."""
    if previous is None:
        return None
    if current[0] >= previous[0] or current[1] > previous[1]:
        return (
            f"{CHANGELOG_FILENAME}: release headings are not in descending "
            f"order at {line!r}"
        )
    return None


def _classify_changelog_heading(
    line: str,
) -> tuple[str, str | None, str | None] | None:
    """Classify a changelog heading line for the release-state scan."""
    dated = _dated_heading(line)
    if dated is not None:
        return ("dated", dated[0], dated[1])
    malformed_dated = DATED_CHANGELOG_RE.match(line)
    if malformed_dated is not None:
        return (
            "bad",
            malformed_dated.group("version"),
            malformed_dated.group("date"),
        )
    unreleased = UNRELEASED_CHANGELOG_RE.match(line)
    if unreleased is not None:
        unreleased_version = unreleased.group("version")
        if not is_release_version(unreleased_version):
            return ("bad", unreleased_version, None)
        return ("unreleased", None, None)
    if VERSION_HEADING_PREFIX_RE.match(line) is not None:
        return ("bad", None, None)
    return None


def _apply_dated_heading(
    version: str | None,
    previous: tuple[tuple, str] | None,
    line: str,
    heading_version: str,
    raw_date: str,
    errors: list[str],
    assign_version: bool,
) -> tuple[str | None, tuple[tuple, str] | None]:
    """Fold one dated heading into the running release-state scan."""
    if not _validated_date(raw_date, line, errors):
        return version, previous
    current = (_version_order_key(heading_version), raw_date)
    order_error = _order_error(previous, current, line)
    if order_error is not None:
        errors.append(order_error)
    if assign_version:
        version = heading_version
    return version, current


def _latest_dated_changelog_version(changelog: str) -> tuple[str | None, list[str]]:
    """Return (version, errors) for the newest released changelog entry.

    The first version heading decides: a dated heading means the release is
    final and its date must be a real ISO date, an unreleased heading means
    the release-state checks run in the pre-release direction instead, and
    any other version-style heading fails closed.  Dated headings must
    descend by version and date so the first entry is the newest one.
    """
    version: str | None = None
    errors: list[str] = []
    first_seen = False
    previous: tuple[tuple[int, ...], str] | None = None
    changelog = _without_fenced_blocks(changelog)
    for match in MARKDOWN_HEADING_LINE_RE.finditer(changelog):
        line = match.group(0)
        heading = _classify_changelog_heading(line)
        if heading is None:
            continue
        first = not first_seen
        first_seen = True
        kind, heading_version, raw_date = heading
        if kind == "bad":
            if heading_version is None:
                errors.append(
                    f"{CHANGELOG_FILENAME}: unrecognized release heading "
                    f"{line!r}"
                )
            else:
                errors.append(
                    f"{CHANGELOG_FILENAME}: invalid release identity "
                    f"{heading_version!r} in heading {line!r}"
                )
        elif kind == "dated":
            version, previous = _apply_dated_heading(
                version,
                previous,
                line,
                heading_version,
                raw_date,
                errors,
                assign_version=first,
            )
    if not first_seen:
        errors.append(f"{CHANGELOG_FILENAME}: missing release heading")
    return version, errors


RELEASE_SURFACE_FILES = (
    README_FILENAME,
    CHINESE_README,
    "docs/project/PROJECT_STATUS.md",
    "docs/project/VERSION_PLANNING.md",
    "docs/guides/INSTALLATION.md",
    "docs/guides/UPGRADE-TO-{version}.md",
    "docs/guides/VERSION_ROLLBACK-{version}.md",
    BREAKING_CHANGES_GUIDE,
    MIGRATION_GUIDE,
    "docs/development/{version}-implementation-plan.md",
    "docs/releases/{version}-release-notes.md",
    UPGRADE_AND_ROLLBACK_NOTE,
    "docs/releases/{version}-deployment-recommendation.md",
    "packaging/repo/apt/README.md",
    CHANGELOG_FILENAME,
    "docs/project/README.md",
)

# Boundary-bearing subset of the stale-claim phrases: everything except the
# two bare words, which only count when a real boundary or another (stronger)
# phrase is present.  See `_is_conditional_publication_block`.
_BOUNDARY_STALE_CLAIM_RE = re.compile(
    r"(?:release|development)[- ]candidate"
    r"|not (?:yet )?(?:published|released)"
    r"|尚未发布"
    r"|开发候选"
    r"|发布候选"
    r"|候选版本"
    r"|(?:publication|release) pending"
    r"|pending (?:publication|release)"
    r"|\b(?:is|are|remains?|stays?|still)\s+pending\b",
    re.IGNORECASE,
)

# Wording that describes an unpublished or candidate state.  A released
# version must not carry these claims next to its version string.
_STABLE_RELEASE_CLAIM_RE = re.compile(r"\bstable release\b", re.IGNORECASE)
_STABLE_RELEASE_VERSION_LINK_RE = re.compile(
    r"(?:\b(?:is|was|remains|stays|which|that|of|a|an|the|release|version|"
    r"candidate|current|currently|latest|number|stable|published|released)\b"
    r"|[\s,\x60])*",
    re.IGNORECASE,
)
_STABLE_RELEASE_LINK_COMMA_SUFFIX_RE = re.compile(
    r"[^,]*,\s*(?:(?:a|an)|(?:which|that)\s+"
    r"(?:is|was|remains|stays)\s+(?:(?:a|an)\s+)?)\s*",
    re.IGNORECASE,
)
_STABLE_RELEASE_COMPARISON_PREFIX_RE = re.compile(
    r"\b(?:unlike|compared\s+(?:with|to)|in\s+contrast\s+to)\s*$",
    re.IGNORECASE,
)
_NEGATED_STABLE_RELEASE_RE = re.compile(
    r"\bnot\s+(?:a\s+)?stable release\b", re.IGNORECASE
)


def _has_stable_release_claim(text: str) -> bool:
    """Return whether text claims a stable release without a negation."""
    return (
        _STABLE_RELEASE_CLAIM_RE.search(
            _NEGATED_STABLE_RELEASE_RE.sub("", text)
        )
        is not None
    )


_STALE_RELEASE_CLAIM_RE = re.compile(
    r"(?:release|development)[- ]candidate"
    r"|not (?:yet )?(?:published|released)"
    r"|\bunpublished\b"
    r"|\bunreleased\b"
    r"|尚未发布"
    r"|开发候选"
    r"|发布候选"
    r"|候选版本"
    r"|(?:publication|release) pending"
    r"|pending (?:publication|release)"
    r"|\b(?:is|are|remains?|stays?|still)\s+pending\b",
    re.IGNORECASE,
)
_UNPUBLISHED_RELEASE_CLAIM_RE = re.compile(
    r"(?:"
    r"\bnot\s+(?:yet\s+)?(?:published|released)\b"
    r"|\b(?:has|have|had)(?:\s+not|n['’]t)\s+"
    r"(?:yet\s+)?been\s+(?:published|released)\b"
    r"|\b(?:unpublished|unreleased)\b"
    r"|尚未发布"
    r"|(?<!no )pending\s+publication\b"
    r"|(?<!no )pending\s+release(?!\s+(?:feedback|blockers?)\b)"
    r"|(?<!no )(?:publication|release)"
    r"(?:\s+(?:(?:of|version)\s+)?`?[vV]?[0-9][A-Za-z0-9.+-]*`?"
    r"(?:(?:,\s*(?:and\s+)?|\s+(?:and|or)\s+)"
    r"`?[vV]?[0-9][A-Za-z0-9.+-]*`?)*)?"
    r"\s+(?:(?:is|are|remains?|stays?)\s+)?pending(?!\s+feedback\b)\b"
    r"|\bawaiting\s+publication\b"
    r"|\bawaiting\s+release(?!\s+feedback\b)"
    r")",
    re.IGNORECASE,
)
_RELEASE_CLAUSE_BREAK_RE = re.compile(
    r";|(?<![.-])\b(?:but|while|whereas|although|though)\b",
    re.IGNORECASE,
)
_VERSION_LIST_SEPARATOR_RE = re.compile(
    r"(?:\s*,\s*(?:and\s+)?|\s+(?:and|or)\s+)"
    r"(?:(?:candidate|release|version)\s+)?",
    re.IGNORECASE,
)
_CLAUSE_SUBJECT_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|has|have|had|remains?|stays?|supersedes|"
    r"replaces|precedes|follows|tagged|published|released|unreleased|"
    r"unpublished|available|pending)\b",
    re.IGNORECASE,
)
_COORDINATED_PREDICATE_RE = re.compile(
    r"\band\s+(?=(?:is|are|was|were|has|have|had|remains?|stays?|"
    r"still|not|unpublished|unreleased|awaiting|pending)\b)",
    re.IGNORECASE,
)
_STATUS_SUBJECT_TAIL_RE = re.compile(
    r"(?P<subject>(?<![\w’'-])"
    r"(?!(?:is|are|was|were|has|have|had|remains?|stays?)\b)"
    r"[A-Za-z][\w’'-]*(?:\s+[A-Za-z][\w’'-]*){0,4}?)"
    r"(?:\s+(?:is|are|was|were|has|have|had|remains?|stays?))?\s*$",
    re.IGNORECASE,
)
_CANDIDATE_STATUS_SUBJECT_RE = re.compile(
    r"(?:(?:the|this|that|current|published|released|new|latest)\s+)*"
    r"(?:it|release|candidate|version|pre[- ]?release|rc|beta|alpha|publication)"
    r"(?:\s+(?:release|candidate|version|publication))?"
    r"(?:['’]s\s+publication)?",
    re.IGNORECASE,
)
_POSITIVE_UNPUBLISHED_STATUS_RE = re.compile(
    r"(?:\bnot\s+(?:yet\s+)?(?:published|released)\b"
    r"|\b(?:has|have|had)(?:\s+not|n['’]t)\s+"
    r"(?:yet\s+)?been\s+(?:published|released)\b)",
    re.IGNORECASE,
)
_NEGATED_UNPUBLISHED_STATUS_PREFIX_RE = re.compile(
    r"\b(?:not(?:\s+yet)?|never|no\s+(?:longer|more)|"
    r"(?:is|are|was|were|has|have|had)n['’]t)\s+$",
    re.IGNORECASE,
)


def _push_heading(stack: list[tuple[int, bool]], level: int, mentions: bool) -> bool:
    """Open a heading of *level*; return the resulting version context."""
    stack[:] = [(lvl, seen) for (lvl, seen) in stack if lvl < level]
    stack.append((level, mentions))
    return any(seen for _lvl, seen in stack)


def _split_heading(line: str) -> tuple[int, str] | None:
    """Return (level, text) for an ATX heading line, or None."""
    if not line.startswith("#"):
        return None
    hashes = len(line) - len(line.lstrip("#"))
    if not 1 <= hashes <= 6:
        return None
    rest = line[hashes:]
    return (hashes, rest.strip()) if rest.startswith((" ", "\t")) else None


def _contextual_blocks(
    text: str,
    history: set[str],
    version_pattern: "re.Pattern[str]",
) -> list[tuple[str, bool]]:
    """Return (block, version_context) pairs from prose.

    A block's version context is true when any open ancestor heading (at a
    shallower level, or its own section heading) names the version under
    review, so a child heading cannot hide a stale claim from its parent section.
    """
    blocks: list[tuple[str, bool]] = []
    stack: list[tuple[int, bool]] = []
    current: list[str] = []
    context = False

    def flush() -> None:
        nonlocal current
        if current:
            blocks.append((" ".join(current), context))
            current = []

    for _lineno, line, is_fence in iter_lines_with_fences(text):
        if is_fence:
            flush()
            continue
        stripped = line.strip()
        heading = _split_heading(stripped)
        if heading is not None:
            flush()
            level, heading_text = heading
            mentions = version_pattern.search(heading_text) is not None
            context = _push_heading(stack, level, mentions)
            if mentions:
                # The heading text itself is a claim: a candidate-declaring
                # heading must not slip through unbeaten.
                blocks.append((heading_text, True))
            continue
        if not stripped or line in history:
            flush()
            continue
        current.append(stripped)
    flush()
    return blocks


def _without_fenced_blocks(text: str) -> str:
    """Blank fenced-block lines (markers and contents) keeping positions.

    Claims live in prose: example commands inside fences must not trip the
    stale-release scan, and their line positions must stay stable.
    """
    lines = text.splitlines()
    blanked = set(range(1, len(lines) + 1))
    for line_no, _line, is_fence in iter_lines_with_fences(text):
        if not is_fence:
            blanked.discard(line_no)
    for line_no in blanked:
        lines[line_no - 1] = ""
    return "\n".join(lines)


def _stable_claim_failures(rel: str, version: str, text: str) -> list[str]:
    """Flag pre-release wording in blocks that mention the released version."""
    failures: list[str] = []
    version_pattern = _release_version_pattern(version)
    text = _without_fenced_blocks(text)
    # Document Updates ledger rows record history, not current claims.
    history = set(_document_update_table_lines(text))
    # Headings delimit logical blocks: an adjacent heading pair must not
    # merge into one claim block.
    for block, context_version in _contextual_blocks(text, history, version_pattern):
        if not context_version and not version_pattern.search(block):
            continue
        stale = _STALE_RELEASE_CLAIM_RE.search(block)
        if stale is not None:
            failures.append(
                f"{rel}: released {version} described with pre-release "
                f"wording {stale.group(0)!r} in: {block[:80]!r}"
            )
    return failures


def _stable_release_version_link_keeps_subject(
    link: str,
    version_before_claim: bool,
    text_before_version: str,
) -> bool:
    """Return whether punctuation keeps a version link with its claim."""
    if "," not in link:
        return True
    if not version_before_claim:
        return False
    if _STABLE_RELEASE_COMPARISON_PREFIX_RE.search(text_before_version):
        return False
    return _STABLE_RELEASE_LINK_COMMA_SUFFIX_RE.fullmatch(link) is not None


def _stable_release_claim_links_other_version(
    claim: str,
    stable_claim: re.Match[str],
    versions: list[tuple[int, int, str]],
    candidate_version: re.Pattern[str],
) -> bool:
    """Return whether a stable-release claim links to another version."""
    for start, end, version in versions:
        if candidate_version.fullmatch(version) is not None:
            continue
        if end <= stable_claim.start():
            link = claim[end : stable_claim.start()]
            version_before_claim = True
        elif stable_claim.end() <= start:
            link = claim[stable_claim.end() : start]
            version_before_claim = False
        else:
            continue
        if not _stable_release_version_link_keeps_subject(
            link, version_before_claim, claim[:start]
        ):
            continue
        if _STABLE_RELEASE_CLAIM_RE.search(link) is not None:
            continue
        if _STABLE_RELEASE_VERSION_LINK_RE.fullmatch(link) is not None:
            return True
    return False


def _stable_claims_all_name_other_releases(
    claim: str, candidate_version: re.Pattern[str]
) -> bool:
    """Return whether every stable-release claim describes another version."""
    unnegated_claim = _NEGATED_STABLE_RELEASE_RE.sub(
        lambda match: " " * len(match.group(0)),
        claim,
    )
    versions = _version_tokens(claim)
    stable_claims = list(
        _STABLE_RELEASE_CLAIM_RE.finditer(unnegated_claim)
    )
    return bool(stable_claims) and all(
        _stable_release_claim_links_other_version(
            claim, stable_claim, versions, candidate_version
        )
        for stable_claim in stable_claims
    )


def _coordinated_status_subject_candidate(
    clause: str,
    status: re.Match[str],
    version_pattern: re.Pattern[str],
) -> bool | None:
    """Resolve a subject carried across a coordinated predicate."""
    before_status = clause[: status.start()]
    for connector in _COORDINATED_PREDICATE_RE.finditer(before_status):
        if _version_tokens(before_status[connector.end() :]):
            continue
        subject = _candidate_in_trailing_subject_group(
            before_status[: connector.start()], version_pattern
        )
        if subject is not None:
            return subject
    return None


def _overlapping_status_version_candidate(
    versions: list[tuple[int, int, str]],
    status: re.Match[str],
    version_pattern: re.Pattern[str],
) -> bool | None:
    """Resolve a version token that overlaps an unpublished status."""
    if overlapping := [
        version
        for version in versions
        if version[0] < status.end() and version[1] > status.start()
    ]:
        return any(
            version_pattern.fullmatch(version[2]) is not None
            for version in overlapping
        )
    else:
        return None


def _following_status_version_candidate(
    clause: str,
    status: re.Match[str],
    versions: list[tuple[int, int, str]],
    following_index: int,
    has_preceding: bool,
    version_pattern: re.Pattern[str],
) -> bool | None:
    """Resolve a version listed after an unpublished status phrase."""
    gap = (
        clause[status.end() : versions[following_index][0]]
        .replace("`", "")
        .strip()
        .lower()
    )
    direct_version = gap in {
        "", "version", "versions", "release", "releases",
        "candidate", "candidates", "of", "for",
    }
    version_list = gap in {
        "", ":", ",", "version", "versions", "versions are",
        "version is", "release", "releases", "releases are",
        "release is", "candidate", "candidates",
    }
    if direct_version or (not has_preceding and version_list):
        return _version_group_names_candidate(
            clause, versions, following_index, 1, version_pattern
        )
    return None


def _version_is_non_release_qualifier(clause: str, version_start: int) -> bool:
    """Return whether a version token belongs to an artifact qualifier."""
    prefix = clause[:version_start].rstrip()
    qualifier = re.search(
        r"\b(?:for|with|against|on|under|using)\s*$",
        prefix,
        re.IGNORECASE,
    )
    if qualifier is not None:
        subject = _STATUS_SUBJECT_TAIL_RE.search(prefix[: qualifier.start()])
        return subject is not None and _CANDIDATE_STATUS_SUBJECT_RE.fullmatch(
            subject["subject"].strip()
        ) is None
    comma = prefix.rfind(",")
    if comma < 0 or not _version_tokens(prefix[:comma]):
        return False
    return re.search(
        r"\b(?:for|with|against|on|under|using|targeting|running|requiring)\b",
        prefix[comma + 1 :],
        re.IGNORECASE,
    ) is not None


def _qualifier_follows_closed_release_subject(
    clause: str,
    release_version: tuple[int, int, str],
    qualifier_version_start: int,
) -> bool:
    """Return whether a qualifier follows a completed, separate subject."""
    prefix = clause[:qualifier_version_start].rstrip()
    qualifiers = list(
        re.finditer(
            r"\b(?:for|with|against|on|under|using|"
            r"targeting|running|requiring)\b",
            prefix,
            re.IGNORECASE,
        )
    )
    if not qualifiers:
        return False
    qualifier = qualifiers[-1]
    subject = _STATUS_SUBJECT_TAIL_RE.search(prefix[: qualifier.start()])
    if subject is None or _CANDIDATE_STATUS_SUBJECT_RE.fullmatch(
        subject["subject"].strip()
    ) is not None:
        return False
    before_subject = clause[release_version[1] : subject.start("subject")]
    if predicates := list(_CLAUSE_SUBJECT_VERB_RE.finditer(before_subject)):
        return re.fullmatch(
            r"\s*,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+",
            before_subject[predicates[-1].end() :],
            re.IGNORECASE,
        ) is not None
    else:
        return False


def _preceding_status_version_candidate(
    clause: str,
    versions: list[tuple[int, int, str]],
    preceding: list[int],
    version_pattern: re.Pattern[str],
) -> bool | None:
    """Resolve the release subject while skipping version qualifiers."""
    found_qualifier = False
    qualifier_version: tuple[int, int, str] | None = None
    for preceding_index in reversed(preceding):
        preceding_version = versions[preceding_index]
        if _version_is_non_release_qualifier(
            clause, preceding_version[0]
        ):
            found_qualifier = True
            if qualifier_version is None:
                qualifier_version = preceding_version
            continue
        if (
            qualifier_version is not None
            and _qualifier_follows_closed_release_subject(
                clause, preceding_version, qualifier_version[0]
            )
        ):
            return None
        return _version_group_names_candidate(
            clause, versions, preceding_index, -1, version_pattern
        )
    return False if found_qualifier else None


def _unpublished_status_names_candidate(
    clause: str,
    status: re.Match[str],
    version_pattern: re.Pattern[str],
) -> bool | None:
    """Return whether a status clause names the candidate as its subject."""
    coordinated = _coordinated_status_subject_candidate(
        clause, status, version_pattern
    )
    if coordinated is not None:
        return coordinated
    versions = _version_tokens(clause)
    overlapping = _overlapping_status_version_candidate(
        versions, status, version_pattern
    )
    if overlapping is not None:
        return overlapping
    following = [
        index
        for index, version in enumerate(versions)
        if version[0] >= status.end()
    ]
    preceding = [
        index
        for index, version in enumerate(versions)
        if version[1] <= status.start()
    ]
    if following:
        result = _following_status_version_candidate(
            clause,
            status,
            versions,
            following[0],
            bool(preceding),
            version_pattern,
        )
        if result is not None:
            return result
    if preceding:
        return _preceding_status_version_candidate(
            clause, versions, preceding, version_pattern
        )
    if following:
        return _version_group_names_candidate(
            clause, versions, following[0], 1, version_pattern
        )
    return None


def _unpublished_status_is_negated(
    text: str, status: re.Match[str]
) -> bool:
    """Return whether text explicitly negates a pending status phrase."""
    if _POSITIVE_UNPUBLISHED_STATUS_RE.fullmatch(status.group(0)):
        return False
    return (
        _NEGATED_UNPUBLISHED_STATUS_PREFIX_RE.search(text[: status.start()])
        is not None
    )


def _has_unpublished_release_status(text: str) -> bool:
    """Return whether text contains an affirmative unpublished status."""
    return any(
        not _unpublished_status_is_negated(text, status)
        for status in _UNPUBLISHED_RELEASE_CLAIM_RE.finditer(text)
    )


def _clause_names_other_version(
    clause: str, version_pattern: re.Pattern[str]
) -> bool:
    """Return whether a clause explicitly names a different version."""
    return any(
        version_pattern.fullmatch(token[2]) is None
        for token in _version_tokens(clause)
    )


def _unpublished_status_names_other_subject(
    clause: str, status: re.Match[str]
) -> bool:
    """Return whether a status clause names a non-release subject."""
    prefix = clause[: status.start()].strip()
    subject = _STATUS_SUBJECT_TAIL_RE.search(prefix)
    if subject is None:
        return False
    return _CANDIDATE_STATUS_SUBJECT_RE.fullmatch(
        subject["subject"].strip()
    ) is None


def _unpublished_status_applies_to_candidate(
    clause: str,
    status: re.Match[str],
    version_pattern: re.Pattern[str],
    candidate_context: bool | None,
    block_context: bool,
) -> bool:
    """Return whether an unpublished status refers to the candidate."""
    if _unpublished_status_is_negated(clause, status):
        return False
    names_candidate = _unpublished_status_names_candidate(
        clause, status, version_pattern
    )
    if names_candidate is not None:
        return names_candidate
    if _clause_names_other_version(clause, version_pattern):
        return False
    if _unpublished_status_names_other_subject(clause, status):
        return False
    return candidate_context is True or (
        block_context and candidate_context is None
    )


def _version_group_names_candidate(
    clause: str,
    versions: list[tuple[int, int, str]],
    start: int,
    direction: int,
    version_pattern: re.Pattern[str],
) -> bool:
    """Return whether a coordinated subject group contains the candidate."""
    if (
        version_pattern.fullmatch(versions[start][2]) is not None
        and _version_is_non_release_qualifier(clause, versions[start][0])
    ):
        return False
    group = [versions[start]]
    index = start
    while 0 <= index + direction < len(versions):
        next_index = index + direction
        left, right = sorted((versions[index], versions[next_index]))
        separator = clause[left[1] : right[0]].replace("`", "")
        if (
            _VERSION_LIST_SEPARATOR_RE.fullmatch(separator) is None
            or _version_is_non_release_qualifier(
                clause, versions[next_index][0]
            )
        ):
            break
        group.append(versions[next_index])
        index = next_index
    return any(
        version_pattern.fullmatch(version[2]) is not None for version in group
    )


def _candidate_modifier_precedes_subject_verb(
    clause: str,
    subject_verbs: list[re.Match[str]],
    index: int,
) -> bool:
    """Return whether published/released modifies the following subject."""
    if (
        subject_verbs[index].group(0).casefold()
        not in {"published", "released"}
        or index + 1 >= len(subject_verbs)
    ):
        return False
    subject = clause[: subject_verbs[index + 1].start()].strip(" \t,:;")
    return _CANDIDATE_STATUS_SUBJECT_RE.fullmatch(subject) is not None


def _unversioned_subject_names_candidate(clause: str) -> bool | None:
    """Resolve candidate context from an unversioned subject phrase."""
    subject_verbs = list(_CLAUSE_SUBJECT_VERB_RE.finditer(clause))
    if not subject_verbs:
        return None
    for index, subject_verb in enumerate(subject_verbs):
        subject = clause[: subject_verb.start()].strip(" \t,:;")
        subject = re.sub(
            r"^(?:(?:and|but|so|then)\s+)+",
            "",
            subject,
            flags=re.IGNORECASE,
        )
        if not subject or subject.casefold() in {
            "it", "this", "that", "these", "those"
        }:
            return None
        if _CANDIDATE_STATUS_SUBJECT_RE.fullmatch(subject) is not None:
            return True
        if _candidate_modifier_precedes_subject_verb(
            clause, subject_verbs, index
        ):
            continue
        return False
    return None


def _candidate_in_trailing_subject_group(
    clause: str, version_pattern: re.Pattern[str]
) -> bool | None:
    """Return whether a clause's last coordinated subject includes candidate."""
    versions = _version_tokens(clause)
    if not versions:
        return _unversioned_subject_names_candidate(clause)
    subject_indexes = [
        index
        for index, version in enumerate(versions)
        if _CLAUSE_SUBJECT_VERB_RE.search(
            clause[
                version[1] : (
                    versions[index + 1][0]
                    if index + 1 < len(versions)
                    else len(clause)
                )
            ]
        )
    ]
    subject_index = (
        subject_indexes[-1] if subject_indexes else len(versions) - 1
    )
    return _version_group_names_candidate(
        clause, versions, subject_index, -1, version_pattern
    )


def _prerelease_claim_names_unpublished_version(
    claim: str,
    version_pattern: re.Pattern[str],
    block_context: bool,
    candidate_context: bool | None = None,
) -> tuple[bool, bool | None]:
    """Return whether stale wording identifies the candidate release."""
    for clause in _RELEASE_CLAUSE_BREAK_RE.split(claim):
        version_tokens = _version_tokens(clause)
        statuses = [
            status
            for status in _UNPUBLISHED_RELEASE_CLAIM_RE.finditer(clause)
            if not any(
                version[0] <= status.start() and status.end() <= version[1]
                for version in version_tokens
            )
        ]
        if any(
            _unpublished_status_applies_to_candidate(
                clause,
                status,
                version_pattern,
                candidate_context,
                block_context,
            )
            for status in statuses
        ):
            return True, candidate_context
        clause_context = _candidate_in_trailing_subject_group(
            clause, version_pattern
        )
        if clause_context is not None:
            candidate_context = clause_context
    return False, candidate_context


def _prerelease_claim_failure(
    rel: str,
    version: str,
    claim: str,
    version_pattern: re.Pattern[str],
    block_context: bool,
    candidate_context: bool | None = None,
) -> tuple[str | None, bool | None]:
    """Return a release-claim failure, if the claim describes the candidate."""
    inherited_context = candidate_context
    unpublished, candidate_context = _prerelease_claim_names_unpublished_version(
        claim, version_pattern, block_context, candidate_context
    )
    if unpublished:
        return (
            f"{rel}: prerelease {version} described as unpublished or pending "
            f"in: {claim[:80]!r}",
            candidate_context,
        )
    if not _has_stable_release_claim(claim):
        return None, candidate_context
    if _stable_claims_all_name_other_releases(claim, version_pattern):
        return None, candidate_context
    if version_pattern.search(claim) is not None or (
        block_context and inherited_context is not False
    ):
        return (
            f"{rel}: prerelease {version} described as a stable release "
            f"in: {claim[:80]!r}",
            candidate_context,
        )
    return None, candidate_context


def _prerelease_claim_failures(rel: str, version: str, text: str) -> list[str]:
    """Flag stable or unpublished claims about a prerelease."""
    failures: list[str] = []
    version_pattern = _release_version_pattern(version)
    text = _without_fenced_blocks(text)
    history = set(_document_update_table_lines(text))
    for block, context_version in _contextual_blocks(text, history, version_pattern):
        block_context = (
            context_version or version_pattern.search(block) is not None
        )
        candidate_context: bool | None = None
        for claim in _split_sentences_protected(block):
            failure, candidate_context = _prerelease_claim_failure(
                rel,
                version,
                claim,
                version_pattern,
                block_context,
                candidate_context,
            )
            if failure is not None:
                failures.append(failure)
    return failures


def _first_dated_changelog_entry(changelog: str) -> tuple[str, str] | None:
    """Return (version, date) for the newest dated changelog entry."""
    match = DATED_CHANGELOG_RE.search(_without_fenced_blocks(changelog))
    if match is None:
        return None
    version = match.group("version")
    return (version, match.group("date")) if is_release_version(version) else None


def _prerelease_notes_failures(
    path: Path, version: str, release_date: str | None
) -> list[str]:
    """Require a dated candidate status for a published prerelease."""
    rel = f"docs/releases/{version}-release-notes.md"
    if not path.is_file():
        return [f"{rel}: missing release notes for released {version}"]
    text = _without_fenced_blocks(
        path.read_text(encoding="utf-8", errors="ignore")
    )
    failures: list[str] = []
    if re.search(rf"^# Release Notes: {re.escape(version)}\s*$", text, re.MULTILINE) is None:
        failures.append(f"{rel}: missing '# Release Notes: {version}' title")
    status = re.search(r"^\*\*Status\*\*:(?P<value>.*)$", text, re.MULTILINE)
    if status is None:
        failures.append(f"{rel}: missing release status for released {version}")
    else:
        value = " ".join(status["value"].split())
        if _has_unpublished_release_status(value):
            failures.append(
                f"{rel}: prerelease {version} described as unpublished "
                "or pending in release notes status"
            )
        elif _has_stable_release_claim(value):
            failures.append(f"{rel}: prerelease {version} cannot claim a stable status")
        elif re.match(
            r"^(?:Release candidate|Pre-release)(?:[.,]|$)", value, re.IGNORECASE
        ) is None:
            failures.append(
                f"{rel}: prerelease {version} carries status {status['value']!r}"
            )
    declared = re.search(r"^\*\*Date\*\*:(?P<value>.*)$", text, re.MULTILINE)
    if declared is None:
        failures.append(f"{rel}: missing release date for released {version}")
    elif release_date is not None and declared["value"].strip() != release_date:
        failures.append(
            f"{rel}: date {declared['value'].strip()!r} does not match "
            f"CHANGELOG date {release_date!r}"
        )
    return failures


def check_prerelease_release_surfaces(
    root: Path,
    version: str,
    release_date: str | None,
    surface_rel_paths: tuple[str, ...] = RELEASE_SURFACE_FILES,
) -> list[str]:
    """Check a published prerelease without demanding a stable status."""
    failures: list[str] = []
    notes_rel = f"docs/releases/{version}-release-notes.md"
    for template in surface_rel_paths:
        rel = template.format(version=version)
        if rel == notes_rel:
            continue
        path = root / rel
        if not path.is_file():
            failures.append(f"{rel}: missing release surface for {version}")
            continue
        failures.extend(
            _prerelease_claim_failures(
                rel, version, path.read_text(encoding="utf-8", errors="ignore")
            )
        )
    failures.extend(
        _prerelease_notes_failures(root / notes_rel, version, release_date)
    )
    return failures


def _stable_surface_check(
    changelog: str,
    root: Path,
) -> tuple[bool, list[str]]:
    """Run the stable-surface scan; return whether it ran and its failures.

    The scan targets the newest released version and stays inactive while a
    newer unreleased entry sits on top of the changelog.
    """
    stable_version, parse_errors = _latest_dated_changelog_version(changelog)
    if parse_errors:
        return False, parse_errors
    if stable_version is None:
        return False, []
    if "-" in stable_version:
        entry = _first_dated_changelog_entry(changelog)
        release_date = entry[1] if entry is not None else None
        return True, check_prerelease_release_surfaces(
            root, stable_version, release_date
        )
    return True, check_stable_release_surfaces(root, stable_version)


def _release_notes_status_failures(path: Path, version: str) -> list[str]:
    """Require a stable or candidate status that matches the release line."""
    rel = f"docs/releases/{version}-release-notes.md"
    if not path.is_file():
        return [f"{rel}: missing release notes for released {version}"]
    notes_text = _without_fenced_blocks(
        path.read_text(encoding="utf-8", errors="ignore")
    )
    status = re.search(r"^\*\*Status\*\*:(?P<value>.*)$", notes_text, re.MULTILINE)
    if status is None:
        return [f"{rel}: missing release status for released {version}"]
    value = " ".join(status["value"].split()).casefold()
    if "-" in version:
        if _has_unpublished_release_status(value):
            return [
                f"{rel}: prerelease {version} cannot be marked unpublished"
            ]
        if _has_stable_release_claim(value):
            return [f"{rel}: prerelease {version} cannot carry a stable status"]
        if re.match(r"^(?:release candidate|pre-release)(?:[.,]|$)", value) is None:
            return [f"{rel}: released {version} carries status {status['value']!r}"]
        return []
    if value != "stable release":
        return [f"{rel}: released {version} carries status {status['value']!r}"]
    return []


def check_stable_release_surfaces(
    root: Path,
    version: str,
    surface_rel_paths: tuple[str, ...] = RELEASE_SURFACE_FILES,
) -> list[str]:
    """Flag pre-release wording next to a released version string.

    Any logical block that mentions the released version and also carries
    candidate or unpublished wording fails, so a stale surface cannot
    survive a stable release.
    """
    failures: list[str] = []
    notes_rel = f"docs/releases/{version}-release-notes.md"
    for template in surface_rel_paths:
        rel = template.format(version=version)
        if rel == notes_rel:
            continue
        path = root / rel
        if not path.is_file():
            failures.append(f"{rel}: missing release surface for {version}")
            continue
        failures.extend(
            _stable_claim_failures(
                rel, version, path.read_text(encoding="utf-8", errors="ignore")
            )
        )
    notes = root / notes_rel
    if notes.is_file():
        # The release notes are scanned even when a caller passes a custom
        # surface list: their prose must never hide a stale claim.
        failures.extend(
            _stable_claim_failures(
                notes_rel, version, notes.read_text(encoding="utf-8", errors="ignore")
            )
        )
    failures.extend(_release_notes_status_failures(notes, version))
    return failures


# Surfaces that carry the current release state a reader acts on.  A pending
# release is judged on these documents, so one document cannot declare a
# publication the others do not support.
PENDING_STATE_SURFACES = (
    README_FILENAME,
    CHINESE_README,
    "docs/project/PROJECT_STATUS.md",
    "docs/project/README.md",
    "docs/project/VERSION_PLANNING.md",
    "docs/development/{version}-implementation-plan.md",
    "docs/guides/INSTALLATION.md",
    "docs/guides/UPGRADE-TO-{version}.md",
    "docs/guides/VERSION_ROLLBACK-{version}.md",
    BREAKING_CHANGES_GUIDE,
    MIGRATION_GUIDE,
    "docs/releases/{version}-release-notes.md",
    UPGRADE_AND_ROLLBACK_NOTE,
    "docs/releases/{version}-deployment-recommendation.md",
    "packaging/repo/apt/README.md",
    CHANGELOG_FILENAME,
)

# Reference-only and procedure surfaces are scanned for affirmative release
# claims but do not need to repeat the publication boundary.
PENDING_BOUNDARY_OPTIONAL_SURFACES = frozenset({
    BREAKING_CHANGES_GUIDE,
    MIGRATION_GUIDE,
    UPGRADE_AND_ROLLBACK_NOTE,
    CHANGELOG_FILENAME,
})

# Wording that ties a mention of the pending version to a later publication.
# A conditional instruction is honest; an unconditional one reads as a
# download that works today.
_PREPUBLICATION_BOUNDARY_RE = re.compile(
    r"after (?:the )?(?:publication|release)"
    r"|after (?:(?:the )?(?:v?\d+\.\d+\.\d+"
    r"(?: (?:release|tag))?|version|tag)|it|"
    r"this (?:version|release)|that (?:version|release))"
    r"\s+(?:is|has been|will be|gets)\s+(?:published|released)"
    r"|after (?:the )?merge\b"
    r"|following (?:the )?publication"
    r"|once (?:the project )?(?:publishes|has published)"
    r"|once published"
    r"|post-publication"
    r"|will become available"
    r"|release[- ]time template"
    r"|(?:release|development)[- ]candidate"
    r"|before (?:the )?(?:publication|release)"
    r"|until (?:the )?(?:v?\d+\.\d+\.\d+\s+)?(?:assets|release|tag)\b"
    r"|not (?:yet |currently |presently |now )?(?:been )?(?:published|released)"
    r"|\bno\b.{0,100}\b(?:published|released)\b"
    r"|将(?:会)?(?:已)?(?:正式)?发布|即将(?:正式)?发布"
    r"|尚未发布|等待发布|待发布|未发布|发布后|发布之前",
    re.IGNORECASE,
)

# Completion verbs.  A sentence that names the pending version with one of these
# and no local negation or future-publication qualifier states a completed release.
_PREPUBLICATION_COMPLETION_CLAIM_RE = re.compile(
    r"\b(?:shipped|published|released|available)\b|已发布|已正式发布",
    re.IGNORECASE,
)
# Completion verbs that affirm a release even when the sentence does not name
# a version token.  ``available`` is deliberately excluded here: in a
# versionless sentence it over-matches ordinary prose such as "the release
# artifacts are available from the mirror", so it only counts when the
# sentence names the pending version (the versioned branch in
# `_claim_names_pending_version`).
_VERSIONLESS_CLAIM_VERBS = frozenset(
    {"shipped", "published", "released", "available"}
)
# An availability claim asserted OF THE RELEASE ITSELF ("the release is
# available", "the new version is available") affirms a shipped artifact,
# so ``available`` joins the versionless verbs.  Availability asserted of
# only generic artifacts ("artifacts are available from the mirror") stays
# excluded: the sentence names an artifact surface, not the release.
_GENERIC_AVAILABILITY_OBJECT_RE = re.compile(
    r"\b(?:artifacts?|assets?|packages?|binaries?|checksums?|"
    r"file|files|downloads?|mirror|repository|feed)\b[^.]{0,40}$",
    re.IGNORECASE,
)
_CI_ARTIFACT_AVAILABILITY_RE = re.compile(
    r"\bavailable\s+(?:in|from)\s+(?:the\s+)?(?:ci|workflow|build pipeline)\b",
    re.IGNORECASE,
)
# The broad `no ... has been` alternative is only safe after its input prefix
# has been cut at clause and unrelated-version boundaries. Keep that scoping in
# `_completion_claim_is_nonaffirmative`; searching a whole document can let an
# earlier, unrelated negation hide a later affirmative release claim.
_NEGATED_COMPLETION_CLAIM_RE = re.compile(
    r"\b(?:not|never|no)\s+(?:(?:yet|still|currently|presently|now|longer|been|be|become|officially|formally)\s+)*$"
    r"|\b(?:isn't|aren't|wasn't|weren't|hasn't|haven't)\s+"
    r"(?:(?:yet|still|currently|presently|now|been)\s+)*$"
    r"|\bno\b.{0,100}\b(?:is|are|was|were|has been|have been)\s*$",
    re.IGNORECASE,
)
_COMPLETION_CLAUSE_BREAK_RE = re.compile(
    r"[,;:!?]|\b(?:and|but|however|although|though|whereas|while|instead)\b",
    re.IGNORECASE,
)
# How far a completion verb may sit from the pending version and still read as
# a claim about it.  A verb that belongs to another release sits further away
# or carries a sentence break between the two.
def _release_version_pattern(version: str) -> re.Pattern[str]:
    """Match a stable release token with ASCII-only identifier boundaries."""
    return re.compile(
        rf"(?<![A-Za-z0-9_.-])v?{re.escape(version)}"
        r"(?![A-Za-z0-9_-]|\.\d)"
    )


_VERSION_LEFT_BOUNDARY_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
)
_VERSION_IDENTIFIER_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
)


def _consume_version_digits(text: str, index: int) -> int:
    """Return the first index after one ASCII digit run."""
    while index < len(text) and "0" <= text[index] <= "9":
        index += 1
    return index


def _consume_version_identifier(text: str, index: int) -> int:
    """Return the first index after one ASCII prerelease identifier."""
    while index < len(text) and text[index] in _VERSION_IDENTIFIER_CHARS:
        index += 1
    return index


def _version_core_end(text: str, start: int) -> tuple[int | None, int]:
    """Return the end of a three-component numeric version core."""
    index = start + (text[start] == "v")
    component_end = _consume_version_digits(text, index)
    if component_end == index:
        return None, max(start + 1, component_end)
    index = component_end
    for _ in range(2):
        if index >= len(text) or text[index] != ".":
            return None, max(start + 1, index)
        component_start = index + 1
        component_end = _consume_version_digits(text, component_start)
        if component_end == component_start:
            return None, max(start + 1, component_end)
        index = component_end
    return index, index


def _version_suffix_end(
    text: str, start: int, index: int
) -> tuple[int | None, int]:
    """Return the end of an optional dot-separated prerelease suffix."""
    if index >= len(text) or text[index] != "-":
        return index, index
    part_start = index + 1
    part_end = _consume_version_identifier(text, part_start)
    if part_end == part_start:
        return None, max(start + 1, part_end)
    index = part_end
    while index < len(text) and text[index] == ".":
        part_start = index + 1
        part_end = _consume_version_identifier(text, part_start)
        if part_end == part_start:
            break
        index = part_end
    return index, index


def _version_end_is_valid(text: str, index: int) -> bool:
    """Apply the release token's right boundary without regex backtracking."""
    if index >= len(text):
        return True
    return (
        False
        if text[index] in _VERSION_IDENTIFIER_CHARS
        else text[index] != "."
        or index + 1 >= len(text)
        or not "0" <= text[index + 1] <= "9"
    )


def _version_token_at(
    text: str, start: int
) -> tuple[tuple[int, int, str] | None, int]:
    """Parse one stable-version token without regex backtracking."""
    if start and text[start - 1] in _VERSION_LEFT_BOUNDARY_CHARS:
        return None, start + 1
    core_end, resume = _version_core_end(text, start)
    if core_end is None:
        return None, resume
    token_end, resume = _version_suffix_end(text, start, core_end)
    if token_end is None or not _version_end_is_valid(text, token_end):
        return None, max(start + 1, resume)
    return (start, token_end, text[start:token_end]), token_end


def _version_tokens(text: str) -> list[tuple[int, int, str]]:
    """Return version tokens using a bounded, deterministic character scan."""
    tokens: list[tuple[int, int, str]] = []
    index = 0
    while index < len(text):
        if text[index] != "v" and not ("0" <= text[index] <= "9"):
            index += 1
            continue
        token, next_index = _version_token_at(text, index)
        if token is not None:
            tokens.append(token)
        index = max(index + 1, next_index)
    return tokens

# Abbreviations that must not trigger sentence splits.
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.", "al.", "et al.")

# Placeholder for protected abbreviations during sentence splitting.
_ABBR_PLACEHOLDER = "\uE000"  # Private use area, won't appear in docs


def _protect_version_periods(text: str) -> str:
    """Replace periods inside complete version tokens with placeholders."""
    protected = list(text)
    for start, end, _version in _version_tokens(text):
        for index in range(start, end):
            if protected[index] == ".":
                protected[index] = _ABBR_PLACEHOLDER
    return "".join(protected)

def _protect_abbreviations(text: str) -> str:
    """Replace known abbreviations with placeholders to prevent false splits."""
    for abbr in sorted(_ABBREVIATIONS, key=len, reverse=True):
        pattern = re.compile(rf"(?<!\w){re.escape(abbr)}", re.IGNORECASE)
        text = pattern.sub(
            lambda match: match.group(0).replace(".", _ABBR_PLACEHOLDER),
            text,
        )
    return text


def _restore_abbreviations(text: str) -> str:
    """Restore abbreviations from placeholders."""
    return text.replace(_ABBR_PLACEHOLDER, ".")


# Sentence splitting must handle missing space after period (common in prose).
# Split on punctuation plus whitespace, or punctuation plus an uppercase letter.
# Complete version tokens and known abbreviations are protected before splitting.
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])(?:\s+|(?=[A-Z]))",
)
_RELEASE_SUBJECT_RE = re.compile(
    r"\b(?:release|version|tag|build|package|artifact|assets?)\b",
    re.IGNORECASE,
)


def _first_dated_changelog_version(changelog: str) -> str | None:
    """Return the newest released version, or None while none is dated.

    The first dated heading is the released one: an unreleased entry above it
    belongs to the pending line, and that line has no publication date yet.
    Any malformed dated identity makes the lookup fail closed.
    """
    text = _without_fenced_blocks(changelog)
    version: str | None = None
    for match in MARKDOWN_HEADING_LINE_RE.finditer(text):
        dated = DATED_CHANGELOG_RE.match(match.group(0))
        if dated is None:
            continue
        heading_version = dated.group("version")
        if not is_release_version(heading_version):
            return None
        if version is None:
            version = heading_version
    return version


def _is_conditional_publication_block(block: str) -> bool:
    """Return whether a block marks the pending version as not yet published.

    The explicit pre-publication boundary pattern decides first.  The
    stale-claim phrases also describe a not-yet-published state, but the two
    bare single words (``unreleased`` / ``unpublished``) are excluded here:
    a passing mention of the word itself must not satisfy the boundary
    requirement, or ordinary prose could suppress the "needs a publication
    boundary" diagnostic.  Those two words still serve the
    released-direction stale-claim check.
    """
    if _PREPUBLICATION_BOUNDARY_RE.search(block) is not None:
        return True
    return _BOUNDARY_STALE_CLAIM_RE.search(block) is not None


def _split_sentences_protected(text: str) -> list[str]:
    """Split sentences while protecting version tokens and abbreviations."""
    protected = _protect_abbreviations(_protect_version_periods(text))
    return [_restore_abbreviations(s) for s in _SENTENCE_SPLIT_RE.split(protected)]


def _publication_claim_window(block: str, version_pattern: "re.Pattern[str]") -> str:
    """Keep the caller's already-isolated sentence if it names the version.

    `_pending_text_failures` performs abbreviation-aware sentence splitting
    before this helper. Splitting again here risks separating the version from
    its publication verb or the clause that qualifies that verb.
    """
    return block if version_pattern.search(block) is not None else ""


def _completion_claim_prefix_is_negated(
    prefix: str, pending: "re.Pattern[str] | None"
) -> bool:
    negation_start = max(
        (match.end() for match in _COMPLETION_CLAUSE_BREAK_RE.finditer(prefix)),
        default=0,
    )
    for _start, end, version_text in _version_tokens(prefix):
        if pending is None or pending.fullmatch(version_text) is None:
            negation_start = max(negation_start, end)
    return _NEGATED_COMPLETION_CLAIM_RE.search(prefix[negation_start:]) is not None


def _english_future_claim_is_nonaffirmative(
    claim_word: str,
    sentence: str,
    claim: re.Match[str],
    prefix: str,
    context: str,
) -> bool:
    if claim_word in {"published", "released", "shipped"}:
        future = re.search(
            r"\b(?:once|will\s+be|after\b[^!?]*\b"
            r"(?:is|has been|will be|gets))\s*$",
            prefix,
            re.IGNORECASE,
        )
        if future is not None and _PREPUBLICATION_BOUNDARY_RE.search(context):
            return True
    if claim_word != "available":
        return False
    future = re.search(
        r"\b(?:will\s+)?become(?:s)?\s*$|\bwill\s+be\s*$",
        prefix,
        re.IGNORECASE,
    )
    if future is not None and _PREPUBLICATION_BOUNDARY_RE.search(context):
        return True
    return _claim_followed_by_publication_boundary(sentence, claim)


def _claim_followed_by_publication_boundary(
    sentence: str, claim: re.Match[str]
) -> bool:
    after_claim = sentence[claim.end() :]
    return re.search(
        r"^\s*(?:only\s+)?(?:after\s+(?:the\s+)?(?:publication|release)\b"
        r"|after\s+(?:(?:the\s+)?(?:v?\d+\.\d+\.\d+|version|tag|release)\s+)?"
        r"(?:is|has been|will be|gets)\s+(?:published|released)\b"
        r"|once\s+(?:it\s+is\s+)?published\b"
        r"|following\s+(?:the\s+)?publication\b)",
        after_claim,
        re.IGNORECASE,
    ) is not None


def _completion_claim_is_nonaffirmative(
    sentence: str,
    claim: re.Match[str],
    pending: "re.Pattern[str] | None" = None,
) -> bool:
    """Ignore a negated claim or future availability tied to publication."""
    prefix = re.sub(r"\s+", " ", sentence[:claim.start()].replace(">", " "))
    context = re.sub(r"\s+", " ", sentence.replace(">", " ").replace("`", ""))
    ci_availability = _CI_ARTIFACT_AVAILABILITY_RE.search(sentence)
    if (
        claim.group(0).lower() == "available"
        and ci_availability is not None
        and ci_availability.start() == claim.start()
    ):
        return True
    if _completion_claim_prefix_is_negated(prefix, pending):
        return True
    if claim.group(0) in {"已发布", "已正式发布"}:
        return re.search(r"(?:将会|将|即将)\s*$", prefix) is not None
    return _english_future_claim_is_nonaffirmative(
        claim.group(0).lower(), sentence, claim, prefix, context
    )


def _nearest_version_to_claim(
    window: str, claim: re.Match[str]
) -> tuple[int, int, str] | None:
    """Return the version token closest to the claim match."""
    if versions := _version_tokens(window):
        return min(versions, key=lambda token: abs(token[0] - claim.start()))
    else:
        return None


def _published_until_tag_exception(
    window: str, claim: re.Match[str], pending_span: tuple[int, int, str]
) -> bool:
    """Return whether a "published ... until ... tag" phrasing is exempt."""
    pending_start = pending_span[0]
    if claim.start() >= pending_start or claim.group(0).lower() != "published":
        return False
    between = window[claim.end():pending_start]
    return re.search(r"\buntil\b", between, re.IGNORECASE) is not None and re.search(
        r"\btag\b", between, re.IGNORECASE
    ) is not None


def _claim_names_pending_version(
    window: str,
    claim: re.Match[str],
    pending: "re.Pattern[str]",
) -> bool:
    """Return whether a single completion claim affirms the pending version."""
    nearest = _nearest_version_to_claim(window, claim)
    if nearest is None:
        # Without a version token, heading context alone is not enough.
        # Two conditions must both hold for the sentence to count as a
        # claim about the pending release:
        # 1. a release subject is named (a bare "available" without such a
        #    subject is ordinary wording, not a release claim), and
        # 2. the completion verb is explicit (published/released/shipped).
        #    A bare "available" in the versionless branch over-matches
        #    ordinary changelog prose ("artifacts are available from the
        #    mirror"), so it counts only when the sentence names the
        #    pending version - the versioned branch below.
        if _RELEASE_SUBJECT_RE.search(window) is None:
            return False
        if claim.group(0).lower() not in _VERSIONLESS_CLAIM_VERBS:
            return False
        if _generic_artifact_availability(window, claim):
            # "artifacts are available from the mirror" is ordinary prose,
            # not a claim that the release shipped.
            return False
        return not _completion_claim_is_nonaffirmative(window, claim, pending)
    if pending.fullmatch(nearest[2]) is None:
        return False
    if _published_until_tag_exception(window, claim, nearest):
        return False
    return not _completion_claim_is_nonaffirmative(window, claim, pending)


def _generic_artifact_availability(
    window: str, claim: "re.Match[str]"
) -> bool:
    """Whether an ``available`` claim describes generic artifacts only.

    The subject check above admits the sentence whenever any release-shaped
    word appears, so "the release artifacts are available from the mirror"
    would otherwise read as a shipped-release affirmation.  When the words
    directly before ``available`` name an artifact surface rather than the
    release itself, the claim stays ordinary prose.
    """
    if claim.group(0).lower() != "available":
        return False
    before = window[max(0, claim.start() - 80):claim.start()]
    if clause_breaks := list(_COMPLETION_CLAUSE_BREAK_RE.finditer(before)):
        before = before[clause_breaks[-1].end():]
    return _GENERIC_AVAILABILITY_OBJECT_RE.search(before) is not None


def _pending_completion_claim(
    window: str, pending_version: str
) -> re.Match[str] | None:
    """Return the first affirmative completion claim tied to the pending version."""
    pending = _release_version_pattern(pending_version)
    return next(
        (
            claim
            for claim in _PREPUBLICATION_COMPLETION_CLAIM_RE.finditer(window)
            if _claim_names_pending_version(window, claim, pending)
        ),
        None,
    )


def _claim_belongs_to_pending_version(
    window: str, pending_version: str
) -> bool:
    """Return whether an affirmative completion verb names the pending version.

    The nearest version token decides, so a published baseline is not mistaken
    for a claim about the pending line in a sentence that names both.
    """
    return _pending_completion_claim(window, pending_version) is not None


def _latest_tag_prefix_is_negated(prefix: str) -> bool:
    return re.search(
        r"\b(?:not|never|no longer|isn't|aren't|wasn't|weren't)\b"
        r"[^.!?;]{0,40}$",
        prefix,
        re.IGNORECASE,
    ) is not None


def _latest_tag_suffix_is_negated(suffix: str) -> bool:
    return re.match(
        r"\s*(?:(?:is|are|was|were|has been|have been)\s+)?"
        r"(?:not|never|no longer|isn't|aren't|wasn't|weren't)\b",
        suffix,
        re.IGNORECASE,
    ) is not None


def _latest_tag_claim_is_negated(
    sentence: str,
    latest_match: re.Match[str],
    lower_bound: int = 0,
    upper_bound: int | None = None,
) -> bool:
    """Reject latest-tag language explicitly scoped by a local negation."""
    upper_bound = len(sentence) if upper_bound is None else upper_bound
    prefix = sentence[max(lower_bound, latest_match.start() - 80):latest_match.start()]
    suffix = sentence[latest_match.end():min(upper_bound, latest_match.end() + 80)]
    if prefix_breaks := [
        match.end() for match in _COMPLETION_CLAUSE_BREAK_RE.finditer(prefix)
    ]:
        prefix = prefix[max(prefix_breaks):]
    if suffix_break := _COMPLETION_CLAUSE_BREAK_RE.search(suffix):
        suffix = suffix[:suffix_break.start()]
    return _latest_tag_prefix_is_negated(prefix) or _latest_tag_suffix_is_negated(
        suffix
    )


def _latest_tag_patterns(
    pending_version: str,
) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    latest_tag = re.compile(
        r"\b(?:latest|most recent)\s+"
        r"(?:(?:public|published|stable)\s+)*(?:tag|release)\b"
        r"(?!\s+notes?\b)",
        re.IGNORECASE,
    )
    version = _release_version_pattern(pending_version)
    forward = re.compile(
        rf"{latest_tag.pattern}\s*(?:is\s+)?[:=]?\s*{version.pattern}",
        re.IGNORECASE,
    )
    reverse = re.compile(
        version.pattern + r"\s+(?:is\s+)?(?:the\s+)?" + latest_tag.pattern,
        re.IGNORECASE,
    )
    return version, latest_tag, re.compile(
        rf"(?:{forward.pattern})|(?:{reverse.pattern})", re.IGNORECASE
    )


def _latest_tag_match_overlaps_direct_claim(
    latest_match: re.Match[str], direct_claims: list[re.Match[str]]
) -> bool:
    return any(
        direct.start() < latest_match.end()
        and latest_match.start() < direct.end()
        for direct in direct_claims
    )


def _latest_tag_match_affirms_pending(
    plain: str,
    latest_match: re.Match[str],
    version: re.Pattern[str],
    direct_claims: list[re.Match[str]],
    version_context: bool,
    lower_bound: int,
    upper_bound: int,
) -> bool:
    if _latest_tag_claim_is_negated(
        plain, latest_match, lower_bound, upper_bound
    ):
        return False
    if _latest_tag_match_overlaps_direct_claim(latest_match, direct_claims):
        return True
    if not version_context:
        return False
    nearest = _nearest_version_to_claim(plain, latest_match)
    return nearest is None or version.fullmatch(nearest[2]) is not None


def _latest_tag_sentence_claims_pending_version(
    sentence: str,
    version: re.Pattern[str],
    latest_tag: re.Pattern[str],
    direct_claim_pattern: re.Pattern[str],
    version_context: bool,
) -> bool:
    plain = sentence.replace("**", "").replace("`", "")
    latest_matches = list(latest_tag.finditer(plain))
    direct_claims = list(direct_claim_pattern.finditer(plain))
    for index, latest_match in enumerate(latest_matches):
        lower_bound = latest_matches[index - 1].end() if index else 0
        upper_bound = (
            latest_matches[index + 1].start()
            if index + 1 < len(latest_matches)
            else len(plain)
        )
        if _latest_tag_match_affirms_pending(
            plain, latest_match, version, direct_claims,
            version_context, lower_bound, upper_bound,
        ):
            return True
    return False


def _claims_pending_latest_tag(
    block: str, pending_version: str, version_context: bool = False
) -> bool:
    """Return whether the pending version is called the latest tag or release."""
    version, latest_tag, direct_claim = _latest_tag_patterns(pending_version)
    return any(
        _latest_tag_sentence_claims_pending_version(
            sentence, version, latest_tag, direct_claim, version_context
        )
        for sentence in _split_sentences_protected(block)
    )


def _pending_sentence_failures(
    rel: str,
    sentence: str,
    pending_version: str,
    version_pattern: "re.Pattern[str]",
    version_context: bool = False,
) -> tuple[list[str], bool]:
    """Validate one current-state sentence about the pending release."""
    sentence_names_version = version_pattern.search(sentence) is not None
    if not sentence_names_version and not version_context:
        return [], False
    boundary = _is_conditional_publication_block(sentence)
    window = (
        _publication_claim_window(sentence, version_pattern)
        if sentence_names_version
        else sentence
    )
    claim = _pending_completion_claim(window, pending_version)
    if claim is not None:
        return [
            f"{rel}: pending {pending_version} is described as "
            f"{claim.group(0)!r} without a publication boundary in: "
            f"{sentence[:80]!r}"
        ], boundary
    return [], boundary


def _pending_document_failures(
    root: Path,
    rel: str,
    pending_version: str,
    require_boundary: bool = True,
) -> list[str]:
    """Validate all current-state blocks for one pending-release surface."""
    path = root / rel
    if not path.is_file():
        return [f"{rel}: required release-state surface is missing"]
    text = path.read_text(encoding="utf-8", errors="ignore")
    return _pending_text_failures(
        rel, text, pending_version, require_boundary=require_boundary
    )


def _pending_changelog_prologue_failures(
    root: Path, pending_version: str
) -> list[str]:
    """Check only the changelog introduction, not dated history sections."""
    path = root / CHANGELOG_FILENAME
    if not path.is_file():
        return []
    text = _without_fenced_blocks(
        path.read_text(encoding="utf-8", errors="ignore")
    )
    first_heading = VERSION_HEADING_PREFIX_RE.search(text)
    prologue = text[:first_heading.start()] if first_heading is not None else text
    return _pending_text_failures(
        CHANGELOG_FILENAME,
        prologue,
        pending_version,
        require_boundary=False,
    )


def _pending_block_failures(
    rel: str,
    block: str,
    pending_version: str,
    version_pattern: "re.Pattern[str]",
    version_context: bool,
) -> tuple[list[str], bool]:
    """Validate one current-state block and retain its publication boundary."""
    names_version = version_pattern.search(block) is not None
    if not version_context and not names_version:
        return [], False
    boundary = _is_conditional_publication_block(block)
    failures = []
    if _claims_pending_latest_tag(block, pending_version, version_context):
        failures.append(
            f"{rel}: pending {pending_version} is identified as the latest tag "
            "or stable release"
        )
    for sentence in _split_sentences_protected(block):
        sentence_failures, sentence_boundary = _pending_sentence_failures(
            rel, sentence, pending_version, version_pattern, version_context
        )
        failures.extend(sentence_failures)
        boundary = boundary or sentence_boundary
    return failures, boundary


def _pending_text_failures(
    rel: str,
    text: str,
    pending_version: str,
    require_boundary: bool = True,
    include_heading_context: bool = False,
) -> list[str]:
    """Validate pending-release prose in a named current-state surface.

    Heading context is enabled only for the changelog's already-scoped current
    Unreleased section; ordinary documents can contain historical version
    headings whose prose is not a claim about today's pending release.
    """
    version_pattern = _release_version_pattern(pending_version)
    text = _without_fenced_blocks(text)
    history = set(_document_update_table_lines(text))
    blocks = (
        _contextual_blocks(text, history, version_pattern)
        if include_heading_context
        else [(block, False) for block in _logical_blocks(text, history)]
    )
    failures: list[str] = []
    boundary_seen = False
    for block, version_context in blocks:
        block_failures, boundary = _pending_block_failures(
            rel, block, pending_version, version_pattern, version_context
        )
        failures.extend(block_failures)
        boundary_seen = boundary_seen or boundary
    if require_boundary and not boundary_seen:
        failures.append(
            f"{rel}: pending {pending_version} needs one explicit "
            "publication boundary in current-state prose"
        )
    return failures


def _current_unreleased_changelog_section(
    changelog: str, pending_version: str
) -> str:
    """Return only the matching Unreleased section, excluding history/fences."""
    text = _without_fenced_blocks(changelog)
    for match in UNRELEASED_CHANGELOG_RE.finditer(text):
        if match.group("version") != pending_version:
            continue
        next_heading = VERSION_HEADING_PREFIX_RE.search(text, match.end())
        end = next_heading.start() if next_heading is not None else len(text)
        return text[match.start():end]
    return ""


def check_pending_release_state(
    root: Path,
    pending_version: str,
    surface_rel_paths: tuple[str, ...] = PENDING_STATE_SURFACES,
) -> list[str]:
    """Reject current-state claims that a pending release is published."""
    failures: list[str] = []
    for template in surface_rel_paths:
        if template == CHANGELOG_FILENAME:
            failures.extend(
                _pending_changelog_prologue_failures(root, pending_version)
            )
            continue
        rel = template.format(version=pending_version)
        failures.extend(
            _pending_document_failures(
                root,
                rel,
                pending_version,
                require_boundary=(
                    template not in PENDING_BOUNDARY_OPTIONAL_SURFACES
                ),
            )
        )
    return failures


def _published_baseline_failures(root: Path, published_version: str) -> list[str]:
    """Require the published baseline release to stay canonical.

    A pending line sits on top of a real publication, so the checks confirm
    that the published release notes carry the correct release status and
    the changelog date that the release declared.
    """
    notes = root / "docs" / "releases" / f"{published_version}-release-notes.md"
    changelog_path = root / CHANGELOG_FILENAME
    if not changelog_path.is_file():
        return _release_notes_status_failures(notes, published_version)
    changelog = _without_fenced_blocks(
        changelog_path.read_text(encoding="utf-8", errors="ignore")
    )
    heading = re.search(
        rf"^ {{0,3}}##[ \t]+\[{re.escape(published_version)}\][ \t]*-[ \t]*"
        rf"(?P<date>\d{{4}}-\d{{2}}-\d{{2}})[ \t]*$",
        changelog,
        re.MULTILINE,
    )
    if heading is None:
        failures = _release_notes_status_failures(notes, published_version)
        failures.append(
            f"{CHANGELOG_FILENAME}: published {published_version} has no dated heading"
        )
        return failures
    if "-" in published_version:
        return _prerelease_notes_failures(
            notes, published_version, heading["date"]
        )
    failures = _release_notes_status_failures(notes, published_version)
    if notes.is_file():
        declared = re.search(
            r"^\*\*Date\*\*:(?P<value>.*)$",
            _without_fenced_blocks(notes.read_text(encoding="utf-8", errors="ignore")),
            re.MULTILINE,
        )
        if (
            declared is not None
            and declared["value"].strip() != heading["date"]
        ):
            failures.append(
                f"docs/releases/{published_version}-release-notes.md: date {declared['value'].strip()!r} does not match CHANGELOG date {heading['date']!r}"
            )
    return failures


def check_release_state_contract(
    root: Path,
    surface_rel_paths: tuple[str, ...] = PENDING_STATE_SURFACES,
) -> list[str]:
    """Run the release-state contract in the direction the changelog declares.

    An unreleased top entry means the pending version must not read as
    published while the latest dated release stays canonical.  A dated top
    entry means the released version must not read as pending, so the same
    contract accepts the post-publication state.
    """
    changelog_path = root / CHANGELOG_FILENAME
    if not changelog_path.is_file():
        return []
    changelog = changelog_path.read_text(encoding="utf-8", errors="ignore")
    pending_version, errors = _find_unreleased_changelog_line(changelog)
    if errors:
        return errors
    if pending_version is not None:
        failures = check_pending_release_state(root, pending_version, surface_rel_paths)
        if current_section := _current_unreleased_changelog_section(
            changelog, pending_version
        ):
            failures.extend(
                _pending_text_failures(
                    CHANGELOG_FILENAME, current_section, pending_version,
                    require_boundary=False,
                    include_heading_context=True,
                )
            )
        published_version = _first_dated_changelog_version(changelog)
        if published_version is not None:
            failures.extend(_published_baseline_failures(root, published_version))
        return failures
    published_version = _first_dated_changelog_version(changelog)
    if published_version is None:
        return []
    failures: list[str] = []
    claim_check = (
        _prerelease_claim_failures
        if "-" in published_version
        else _stable_claim_failures
    )
    for template in surface_rel_paths:
        rel = template.format(version=published_version)
        path = root / rel
        if not path.is_file():
            continue
        failures.extend(
            claim_check(
                rel,
                published_version,
                path.read_text(encoding="utf-8", errors="ignore"),
            )
        )
    return failures


def main() -> int:
    """Entry point: run all doc consistency checks and print a report.

    Returns 0 if all checks pass, 1 if any errors are found.
    """
    files = iter_markdown_files()
    failures: list[str] = []

    failures.extend(check_links(files))
    failures.extend(check_heading_hierarchy(files))
    failures.extend(check_english_policy(files))
    failures.extend(check_internal_reference_policy(files))
    failures.extend(check_operator_config_examples(files))
    changelog_path = ROOT / CHANGELOG_FILENAME
    changelog = changelog_path.read_text(encoding="utf-8", errors="ignore")
    # Use the unreleased-changelog regex so validation targets the version
    # under development, not the latest released version.
    version_match, _ = _find_unreleased_changelog_line(changelog)
    release_notes_path = None
    if version_match:
        candidate_release_notes = (
            ROOT / "docs" / "releases"
            / f"{version_match}-release-notes.md"
        )
        if candidate_release_notes.is_file():
            release_notes_path = candidate_release_notes
    failures.extend(
        check_release_status_consistency(
            changelog_path,
            ROOT / "docs" / "project" / "PROJECT_STATUS.md",
            release_notes_path,
        )
    )
    stable_ran, stable_failures = _stable_surface_check(changelog, ROOT)
    failures.extend(stable_failures)
    failures.extend(check_release_state_contract(ROOT))
    failures.extend(check_duplicate_sync())
    failures.extend(check_document_updates_order(files))
    failures.extend(check_metric_family_count(files))
    failures.extend(check_release_checklist_is_static(files))
    if failures := list(dict.fromkeys(failures)):
        print("Documentation checks failed:")
        for line in failures:
            print(f"- {line}")
        return 1

    print("Documentation checks passed:")
    print(f"- Markdown files checked (excluding docs/archive and gitignored paths): {len(files)}")
    _print_status_lines(
        "- Local links: OK",
        "- Heading hierarchy: OK",
        "- English docs policy (Han-character scan): OK",
        "- Internal reference policy (tracked paths/no 'spec X'): OK",
    )
    print("- Operator configuration examples: OK")
    print("- Unreleased/stable release status consistency: OK")
    print(
        "- Stable release surface consistency: "
        + ("OK" if stable_ran else "SKIPPED (no dated release in CHANGELOG)")
    )
    _print_status_lines(
        "- Pending/released release-state contract: OK",
        "- Duplicate canonical/mirror sync: OK",
        "- Document Updates chronological order (descending): OK",
        "- Release checklist states requirements only: OK",
    )
    return 0


def _print_status_lines(*lines: str) -> None:
    """Print each status line on its own row."""
    for line in lines:
        print(line)


if __name__ == "__main__":
    sys.exit(main())
