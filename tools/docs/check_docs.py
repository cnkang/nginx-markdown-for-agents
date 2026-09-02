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
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_SEGMENT = "docs/archive/"
MAINTAINED_ROOT_DOCS = {"AGENTS.md", "README.md", "README_zh-CN.md"}
LINK_RE = re.compile(r"(!?\[[^\]]+\]\(([^)]+)\))")
HAN_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
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
UNRELEASED_CHANGELOG_RE = re.compile(
    r"^ {0,3}##[ \t]+\[(?P<version>\d+\.\d+\.\d+)\][ \t]*-[ \t]*Unreleased(?:[ \t]+candidate)?[ \t]*$",
    re.MULTILINE,
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
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "*.md"],
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
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
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


def iter_unfenced_lines(text: str) -> list[tuple[int, str]]:
    """Extract lines that are outside fenced code blocks.

    Returns a list of ``(line_number, line_text)`` tuples for lines not
    inside `````...``` `` blocks, useful for checking Markdown structural
    rules without false positives from code samples.
    """
    lines: list[tuple[int, str]] = []
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append((line_no, line))
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
        if f.name == "README_zh-CN.md":
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
    """
    version: str | None = None
    errors: list[str] = []
    for match in re.finditer(
        r"^ {0,3}##[^\n]*$",
        changelog,
        re.MULTILINE,
    ):
        line = match.group(0)
        valid = UNRELEASED_CHANGELOG_RE.match(line)
        if valid is not None:
            if version is None:
                version = valid.group("version")
            continue
        if "unreleased" in line.lower():
            errors.append(
                "CHANGELOG.md: malformed unreleased heading "
                f"{line.strip()!r}; expected "
                "'## [<version>] - Unreleased' or '## [<version>] - "
                "Unreleased candidate' without suffix"
            )
    if errors:
        return None, errors
    return version, []


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

    status = section_match.group("body")
    errors: list[str] = []
    if re.search(r"\bstable release\b", status, re.IGNORECASE):
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
    if normalized.startswith("v"):
        normalized = normalized[1:]
    numeric: list[int] = []
    remainder = normalized
    while remainder:
        match = re.match(r"(\d+)", remainder)
        if match is None:
            break
        numeric.append(int(match.group(1)))
        remainder = remainder[match.end():]
        if remainder.startswith("."):
            remainder = remainder[1:]
            continue
        break
    return (tuple(numeric), remainder)


def _document_update_table_lines(content: str) -> list[str]:
    """Extract the first table under a Document Updates heading."""
    match = re.search(r"^## Document Updates\s*$", content, re.MULTILINE | re.IGNORECASE)
    if match is None:
        return []

    table_lines: list[str] = []
    for line in content[match.end() :].splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            table_lines.append(line)
        elif table_lines or stripped.startswith("#"):
            break
    return table_lines


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
    """Verify Document Updates tables use descending version/date order."""
    errors: list[str] = []

    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        if not _document_update_rows_are_sorted(
            _document_update_table_lines(content)
        ):
            errors.append(
                f"{f}: '## Document Updates' table rows must be maintained "
                "in descending chronological order (highest version and "
                "newest date on top)"
            )

    return errors


FAMILY_COUNT_RE = re.compile(
    r"twelve|eleven|exactly 11|exactly 12|11 famil|12 famil",
    re.IGNORECASE,
)
HISTORICAL_FAMILY_CONTEXT_RE = re.compile(
    r"historical|legacy|renamed|removed|retired|0\.9\.1|previous|earlier|older",
    re.IGNORECASE,
)


def _claimed_family_count(line: str) -> int | None:
    """Extract the metric-family count claimed by a doc line.

    Spelled-out numbers take precedence: a line may also contain
    version-like digits (e.g. "eleven v1 metric families") that
    a numeric extractor would misread as a small count.
    """
    if re.search(r"\beleven\b", line, re.IGNORECASE):
        return 11
    if re.search(r"\btwelve\b", line, re.IGNORECASE):
        return 12
    claimed = re.search(r"(\d+)(?>\s*(?:metric\s+)?)famil", line)
    if not claimed:
        return None
    return int(claimed.group(1))


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
    changelog_path = ROOT / "CHANGELOG.md"
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
    failures.extend(check_duplicate_sync())
    failures.extend(check_document_updates_order(files))
    failures.extend(check_metric_family_count(files))

    if failures:
        print("Documentation checks failed:")
        for line in failures:
            print(f"- {line}")
        return 1

    print("Documentation checks passed:")
    print(f"- Markdown files checked (excluding docs/archive and gitignored paths): {len(files)}")
    print("- Local links: OK")
    print("- Heading hierarchy: OK")
    print("- English docs policy (Han-character scan): OK")
    print("- Internal reference policy (tracked paths/no 'spec X'): OK")
    print("- Operator configuration examples: OK")
    print("- Unreleased/stable release status consistency: OK")
    print("- Duplicate canonical/mirror sync: OK")
    print("- Document Updates chronological order (descending): OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
