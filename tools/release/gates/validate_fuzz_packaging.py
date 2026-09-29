#!/usr/bin/env python3
"""
Fuzz and packaging infrastructure validator for the release gates.

Validates the fuzz and packaging infrastructure requirements:

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
12. Release-gate job provisions the pinned Rust toolchain (cargo, rustc,
    rustfmt) that its gate scripts resolve through Rustup shims
13. Release-gate job installs the pinned Python release dependencies
    (requirements-release.txt) before the docs-check chain imports them

Exit codes:
  0 - All checks passed
  1 - One or more checks failed

Security: All file reads use Path.resolve() within PROJECT_ROOT.
No user-supplied patterns are compiled at runtime.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
import functools
import re
import shlex
import shutil
import sys
from pathlib import Path

import yaml

try:
    from tools.lib.path_validation import validate_read_path
except ModuleNotFoundError:  # executed as a script from another directory
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from tools.lib.path_validation import validate_read_path  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
GITHUB_DIR = ".github"
WORKFLOWS_DIR = "workflows"
README_FILENAME = "README.md"
FUZZ_README_REL = f"fuzz/{README_FILENAME}"

FUZZ_TARGETS_GATE = "fuzz:targets-exist"
FUZZ_CORPUS_PRUNING_GATE = "fuzz:corpus-pruning"
PKG_NFPM_CONFIG_GATE = "pkg:nfpm-config"
PKG_ARTIFACT_NAMING_WORKFLOW_GATE = "pkg:artifact-naming-workflow"
PKG_RELEASE_GATE_TOOLCHAIN_GATE = "pkg:release-gate-toolchain"
PKG_RELEASE_GATE_PYTHON_DEPS_GATE = "pkg:release-gate-python-deps"
DOCS_COMPATIBILITY_GATE = "docs:compatibility"

RELEASE_PACKAGES_WORKFLOW_MISSING = "release-packages.yml not found"
VENV_MARKER_PREFIX = "venv:"

# The release-gate job runs this command; the gate scopes its toolchain
# expectation to the job that actually carries the command so a future job
# split must move the provisioning with it.
RELEASE_GATE_JOB_NAME = "release-gate"
RELEASE_GATE_RUSTFMT_CONSUMER = "tools/reason-codegen/generate.py --check"

# Both provisioning jobs read the same commands (bash, rustup, python3,
# retry), so the shadow guard spans both; a no-op `rustup()` in either job
# defeats its checks the same way.
FUZZ_QUALIFICATION_JOB_NAME = "fuzz-qualification"
PROVISIONING_JOB_NAMES = (RELEASE_GATE_JOB_NAME, FUZZ_QUALIFICATION_JOB_NAME)

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
RELEASE_REQUIREMENTS = PROJECT_ROOT / "requirements-release.txt"

# Documentation paths
INSTALL_DOCS = [
    PROJECT_ROOT / "docs" / "guides" / "INSTALLATION.md",
    PROJECT_ROOT / "docs" / "guides" / "PACKAGE_INSTALLATION.md",
]
COMPAT_DOCS = [
    # One canonical compatibility reference; old guide paths remain only as
    # navigation stubs and must not become independent validation surfaces.
    PROJECT_ROOT / "docs" / "guides" / "PACKAGE_COMPATIBILITY.md",
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
    """Read a project-contained file, returning empty string when unreadable.

    The path resolves through the shared `path_validation` helper (Rule
    33/54: canonicalize before containment), so a `..` traversal or a
    symlink that leaves the project root reads as empty exactly like a
    missing file: every check treats either as its FAIL path.
    """
    try:
        resolved = validate_read_path(
            path, must_exist=False, purpose="fuzz packaging gate"
        )
        resolved.relative_to(PROJECT_ROOT.resolve())
    except (ValueError, OSError):
        return ""
    try:
        return resolved.read_text(encoding="utf-8") if resolved.is_file() else ""
    except OSError:
        return ""


def check_fuzz_targets(result: ValidationResult) -> None:
    """Validate that fuzz targets are defined in fuzz/Cargo.toml."""
    content = read_safe(FUZZ_CARGO_TOML)
    if not content:
        result.fail(
            FUZZ_TARGETS_GATE,
            f"{FUZZ_CARGO_TOML.relative_to(PROJECT_ROOT).as_posix()} not found",
        )
        return

    # Check for [[bin]] sections which define fuzz targets
    if "[[bin]]" in content:
        # Count targets
        target_count = content.count("[[bin]]")
        result.pass_(
            FUZZ_TARGETS_GATE,
            f"{FUZZ_CARGO_TOML.relative_to(PROJECT_ROOT).as_posix()} defines "
            f"{target_count} fuzz target(s)",
        )
    else:
        result.fail(
            FUZZ_TARGETS_GATE,
            f"no [[bin]] targets in "
            f"{FUZZ_CARGO_TOML.relative_to(PROJECT_ROOT).as_posix()}",
        )


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
        result.fail("pkg:release-workflow", RELEASE_PACKAGES_WORKFLOW_MISSING)
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


def _scan_ansi_c_quote(line: str, index: int) -> tuple[str | None, int]:
    """Advance one character inside an ANSI-C-quoted string."""
    char = line[index]
    if char == chr(92) and index + 1 < len(line):
        return "ansi-c", 2
    return (None if char == "'" else "ansi-c"), 1


def _scan_single_quote(line: str, index: int) -> tuple[str | None, int]:
    """Advance one character inside a single-quoted string."""
    return (None if line[index] == "'" else "'"), 1


def _scan_double_quote(line: str, index: int) -> tuple[str | None, int]:
    """Advance one character inside a double-quoted string."""
    char = line[index]
    escaped = char == chr(92) and index + 1 < len(line)
    if escaped and line[index + 1] in ('"', chr(92), '`', '$'):
        return '"', 2
    return (None if char == '"' else '"'), 1


def _scan_unquoted_char(line: str, index: int) -> tuple[str | None, int]:
    """Advance one shell character when no quote is open."""
    char = line[index]
    if char == chr(92) and index + 1 < len(line):
        return None, 2
    if line.startswith("$'", index):
        return "ansi-c", 2
    return (char, 1) if char in ("'", '"') else (None, 1)


def _scan_char(line: str, index: int, quote: str | None) -> tuple[str | None, int]:
    """Advance one quote or escape unit; return (new quote, chars consumed).

    Quote state is shared by comments, heredocs, function spans and command
    segmentation so ANSI-C escaped apostrophes cannot desynchronize readers.
    """
    if quote == "ansi-c":
        return _scan_ansi_c_quote(line, index)
    if quote == "'":
        return _scan_single_quote(line, index)
    if quote == '"':
        return _scan_double_quote(line, index)
    return _scan_unquoted_char(line, index)


def _strip_comment_from_line(
    line: str, quote: str | None = None
) -> tuple[str, str | None]:
    """Drop an unquoted trailing comment; return the line and end quote state.

    ``quote`` carries the open-quote state from the previous line, so a
    quoted string that spans lines never lets an embedded ``#`` start a
    comment.  Escape sequences are honored, so an escaped ``#`` stays data
    and an escaped quote never toggles the quote state.
    """
    kept: list[str] = []
    index = 0
    while index < len(line):
        char = line[index]
        if (
            quote is None
            and char == "#"
            and (index == 0 or line[index - 1] in " \t;&|()")
        ):
            return "".join(kept), None
        new_quote, consumed = _scan_char(line, index, quote)
        kept.append(line[index : index + consumed])
        quote = new_quote
        index += consumed
    return "".join(kept), quote


def _strip_shell_comments(script: str) -> str:
    """Remove shell comments without letting heredoc data change quote state."""
    kept: list[str] = []
    lines = script.splitlines()
    pending: list[tuple[str, bool]] = []
    quote: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if pending:
            kept.append(line)
            delimiter, tab_stripped = pending[0]
            candidate = line.lstrip("\t") if tab_stripped else line
            if candidate == delimiter:
                pending.pop(0)
            index += 1
            continue
        command_lines, next_index, quote, markers = _strip_shell_comment_command(
            lines, index, quote
        )
        kept.extend(command_lines)
        index = next_index
        if any(dynamic for _, _, dynamic in markers):
            # The terminator is unknowable; leave the remainder opaque. The
            # separate dynamic-heredoc gate rejects this script fail-closed.
            kept.extend(lines[index:])
            break
        pending.extend((delimiter, tab_stripped)
                       for delimiter, tab_stripped, _ in markers)
    return "\n".join(kept)


def _strip_shell_comment_command(
    lines: list[str], start: int, quote: str | None
) -> tuple[list[str], int, str | None, list[tuple[str, bool, bool]]]:
    """Strip comments in one logical command and discover its heredocs."""
    initial_quote = quote
    command_lines: list[str] = []
    merged = ""
    index = start
    while index < len(lines):
        line = lines[index]
        line_quote = quote
        stripped, quote = _strip_comment_from_line(line, quote)
        command_lines.append(stripped)
        if len(command_lines) == 1:
            merged = stripped
        else:
            merged = f"{merged[:-1]} {stripped.lstrip()}"
        index += 1
        if not _line_continues(stripped, line_quote):
            break
    quote, markers = _scan_line_for_heredocs(merged, initial_quote)
    return command_lines, index, quote, markers


def _join_continuations(script: str) -> str:
    """Join backslash-continued lines so one command stays one logical line."""
    return re.sub(r"\\\n[ \t]*", " ", script)


_HEREDOC_MARKER_RE = re.compile(
    # The escaped alternative `\\.` matches any escaped character including
    # a backslash, so the two alternation branches overlap on `\\`; that is
    # harmless (the escaped branch wins) and kept for clarity.
    r"<<-?[ \t]*(?:(['\"])([^'\"]*)\1|((?:\\.|[^ \t;|&()<>])+))"
)


def _walk_ansi_c_removal(
    raw: str, index: int
) -> tuple[str | None, int, str | None, bool]:
    """One quote-removal step inside an ANSI-C string."""
    char = raw[index]
    if char == chr(92) and index + 1 < len(raw):
        return "ansi-c", 2, raw[index + 1], False
    return (None, 1, None, False) if char == "'" else ("ansi-c", 1, char, False)


def _walk_removal_escape(
    raw: str, index: int, quote: str | None
) -> tuple[str | None, int, str | None, bool]:
    """Remove a backslash when it quotes a shell-special character."""
    if index + 1 < len(raw) and (
        quote is None or raw[index + 1] in ("$", "`", '"', chr(92))
    ):
        return quote, 2, raw[index + 1], False
    char = raw[index]
    return quote, 1, char, False


def _walk_removal_quote(
    raw: str, index: int, quote: str | None
) -> tuple[str | None, int, str | None, bool]:
    """Open or close a regular single- or double-quoted fragment."""
    char = raw[index]
    if quote is None:
        return char, 1, None, False
    if quote == char:
        return None, 1, None, False
    return quote, 1, char, char in ("$", "`")


def _walk_removal_char(
    raw: str, index: int, quote: str | None
) -> tuple[str | None, int, str | None, bool]:
    """One quote-removal step; return (quote, consumed, keep, live)."""
    char = raw[index]
    if quote == "ansi-c":
        return _walk_ansi_c_removal(raw, index)
    if raw.startswith("$'", index):
        return "ansi-c", 2, None, False
    if char == "$" and index + 1 < len(raw) and raw[index + 1] == '"':
        # The dollar in $".." is not an expansion character; the quote
        # that follows performs the quoting.
        return quote, 1, None, False
    if char == chr(92):
        return _walk_removal_escape(raw, index, quote)
    if char in ("'", '"'):
        return _walk_removal_quote(raw, index, quote)
    return quote, 1, char, char in ("$", "`")


def _resolve_heredoc_word(raw: str) -> tuple[str, bool]:
    """Resolve a heredoc delimiter word; return (delimiter, unresolved).

    Bash performs quote removal, but not parameter or command substitution,
    on a here-document delimiter word. Unquoted ``$`` and backticks are
    therefore literal delimiter characters. ``unresolved`` is reserved for
    delimiter syntax this bounded quote-removal model cannot decode.
    """
    # ANSI-C escapes have more spellings than this quote-removal model
    # resolves. Treat an escaped delimiter as unresolved/unverifiable instead
    # of guessing a terminator and exposing heredoc body text as commands.
    if raw.startswith("$'") and "\\" in raw:
        return raw[2:-1] if raw.endswith("'") else raw[2:], True
    resolved: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(raw):
        quote, consumed, keep, _ = _walk_removal_char(raw, index, quote)
        if keep is not None:
            resolved.append(keep)
        index += consumed
    return "".join(resolved), False


def _heredoc_marker_at(
    line: str, index: int
) -> tuple[str, bool, bool, int] | None:
    """Parse a heredoc marker at ``index``; return (word, tab, dynamic, end).

    ``None`` when the position does not open a heredoc (including the
    ``<<<`` herestring form). Bash applies quote removal only, so unquoted
    expansion metacharacters also remain literal in the delimiter. Escaped
    spaces stay part of it.
    """
    if (
        not line.startswith("<<", index)
        or line.startswith("<<<", index)
        or (index > 0 and line[index - 1] == "<")
    ):
        return None
    match = _HEREDOC_MARKER_RE.match(line, index)
    if match is None:
        return None
    if match.group(2) is not None:
        word, dynamic = match.group(2), False
    else:
        word, dynamic = _resolve_heredoc_word(match.group(3))
    return word, match.group(0).startswith("<<-"), dynamic, match.end()


def _scan_line_for_heredocs(
    line: str, quote: str | None
) -> tuple[str | None, list[tuple[str, bool, bool]]]:
    """Scan one line for heredoc markers; return (quote, markers).

    Each marker is ``(delimiter, tab_stripped, dynamic)``.  Markers inside
    quotes are data and do not open a heredoc; a command line may open
    several heredocs, whose bodies follow in marker order.
    """
    markers: list[tuple[str, bool, bool]] = []
    index = 0
    while index < len(line):
        marker = _heredoc_marker_at(line, index) if quote is None else None
        if marker is not None:
            word, tab_stripped, dynamic, end = marker
            markers.append((word, tab_stripped, dynamic))
            index = end
            continue
        new_quote, consumed = _scan_char(line, index, quote)
        quote = new_quote
        index += consumed
    return quote, markers


def _strip_heredocs(script: str) -> str:
    """Drop heredoc bodies so their content never counts as a command.

    The marker line itself stays (it is executable); every line up to and
    including the terminator is dropped.  Command lines join their backslash
    continuations before marker detection, mirroring the shell: a continued
    marker line reads its delimiter from the merged command, while heredoc
    bodies stay literal and never join.  Delimiters may be quoted
    (``<<'WORD'``, ``<<"WORD"``), escaped (``<<\\WORD``, ``<<EOF\\ BAR``) or
    plain (any unquoted word). The terminator must
    match the delimiter exactly -- ``<<-`` additionally strips leading tabs
    -- mirroring shell semantics, so a padded line never ends the body
    early. Delimiter forms the bounded quote-removal model cannot decode are
    left intact; ``_dynamic_heredoc_markers`` reports them so provisioning
    checks can reject the script.
    """
    kept: list[str] = []
    pending: list[tuple[str, bool]] = []
    quote: str | None = None
    lines = script.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if pending:
            delimiter, tab_stripped = pending[0]
            candidate = line.lstrip("\t") if tab_stripped else line
            if candidate == delimiter:
                pending.pop(0)
            continue
        line, index = _join_command_line(lines, line, index, quote)
        kept.append(line)
        quote, markers = _scan_line_for_heredocs(line, quote)
        pending.extend(
            (marker[0], marker[1]) for marker in markers if not marker[2])
    return "\n".join(kept)


def _line_end_quote(line: str, quote: str | None) -> str | None:
    """The quote state at the end of one line, from the state it starts in."""
    index = 0
    while index < len(line):
        quote, consumed = _scan_char(line, index, quote)
        index += consumed
    return quote


def _line_continues(line: str, quote: str | None) -> bool:
    """Whether a command line ends with an unescaped backslash.

    The decision follows the quote state at the END of the line, not the
    state it starts in: a single-quoted string that closes mid-line puts
    the trailing backslash outside quotes (so it escapes the newline),
    while a string that stays open keeps a trailing backslash literal.
    Outside single quotes an odd run of trailing backslashes escapes the
    newline.
    """
    if _line_end_quote(line, quote) == "'":
        return False
    trailing = len(line) - len(line.rstrip("\\"))
    return trailing % 2 == 1


def _join_command_line(
    lines: list[str], line: str, index: int, quote: str | None
) -> tuple[str, int]:
    """Join a command line's backslash continuations; return (line, index).

    The merged line is what the shell parses, so a heredoc marker split
    across a continuation reads the same delimiter the shell would.  The
    line's carried quote state decides each join, and the same state is
    returned to the caller for the next line's scan.
    """
    while _line_continues(line, quote) and index < len(lines):
        line = f"{line[:-1]} {lines[index].lstrip()}"
        index += 1
    return line, index


def _dynamic_heredoc_markers(script: str) -> list[str]:
    """Return delimiter spellings that this parser cannot resolve safely.

    Bash does not expand ``$`` or backticks in delimiter words. The supported
    quote-removal model does not decode some ANSI-C escaped delimiter forms,
    so the provisioning check rejects those unsupported spellings rather
    than guessing their terminator.
    """
    markers: list[str] = []
    quote: str | None = None
    for line in script.splitlines():
        quote, line_markers = _scan_line_for_heredocs(line, quote)
        markers.extend(
            marker[0] for marker in line_markers if marker[2])
    return markers


_FUNCTION_DEF_TAIL_RE = re.compile(
    r"(?:^|[;&|()\s])(?:function\s+)?[A-Za-z_](?a:\w)*\s*\(\s*\)\s*$"
)
_FUNCTION_KEYWORD_TAIL_RE = re.compile(
    r"(?:^|[;&|()\s])function\s+[A-Za-z_](?a:\w)*\s*$"
)
_BODY_CLOSERS = {"{": "}", "(": ")"}


def _opens_function_body(script: str, index: int) -> str | None:
    """Return the opener when the char at ``index`` starts a function body.

    A definition body may be a brace group or a subshell; both run only
    when someone calls the function.
    """
    opener = script[index]
    if opener not in _BODY_CLOSERS:
        return None
    prefix = script[:index]
    if _FUNCTION_DEF_TAIL_RE.search(prefix) or _FUNCTION_KEYWORD_TAIL_RE.search(
        prefix
    ):
        return opener
    return None


def _function_brace_step(
    script: str, index: int, depth: int, quote: str | None, body_start: int
) -> tuple[int, int, str | None, str | None] | None:
    """Track a brace-group delimiter when it is a shell command token."""
    char = script[index]
    if quote is not None:
        return None
    if char == "{" and _body_brace_is_command_position(
        script, body_start, index, "{"
    ):
        return index + 1, depth + 1, quote, None
    if char == "}" and _body_brace_is_command_position(
        script, body_start, index, "}"
    ):
        depth -= 1
        return index + 1, depth, quote, "}" if depth == 0 else None
    return None


def _function_paren_step(
    script: str,
    index: int,
    depth: int,
    quote: str | None,
    closer: str,
) -> tuple[int, int, str | None, str | None] | None:
    """Track nested subshell delimiters inside a function body."""
    if quote is not None:
        return None
    char = script[index]
    if char == "(":
        return index + 1, depth + 1, quote, None
    if char == closer:
        depth -= 1
        return index + 1, depth, quote, closer if depth == 0 else None
    return None


def _function_body_step(
    script: str,
    index: int,
    depth: int,
    quote: str | None,
    opener: str,
    body_start: int,
) -> tuple[int, int, str | None, str | None]:
    """Advance one character inside a function body.

    Returns (new index, new depth, new quote, closer to keep); only the
    closing delimiter of the outermost body is kept, so the structure stays
    visible while the body commands remain dropped.
    """
    closer = _BODY_CLOSERS[opener]
    delimiter_step = (
        _function_brace_step(script, index, depth, quote, body_start)
        if opener == "{"
        else _function_paren_step(script, index, depth, quote, closer)
    )
    if delimiter_step is not None:
        return delimiter_step
    new_quote, consumed = _scan_char(script, index, quote)
    return index + consumed, depth, new_quote, None


_DEFINED_FUNCTION_RE = re.compile(
    r"(?:^|[;&|()\s])(?:function\s+)?([A-Za-z_](?a:\w)*)\s*\(\s*\)\s*[{( \t\n]"
)
_FUNCTION_KEYWORD_DEF_RE = re.compile(
    r"(?:^|[;&|()\s])function\s+([A-Za-z_](?a:\w)*)\s*[{( \t\n]"
)


def _blank_span(chars: list[str], start: int, end: int) -> None:
    """Blank the characters in ``[start, end)`` in place."""
    for position in range(max(0, start), min(end, len(chars))):
        chars[position] = " "


def _masked_quotes(script: str) -> str:
    """Script text with quoted spans blanked to spaces.

    A ``name() {`` shape inside a string is data the shell never defines:
    ``echo "define rustup() { like this"`` documents syntax, it does not
    shadow anything.  The scan is stateful like the rest of the analyzer,
    so escaped quotes and multi-line strings mean what they mean
    everywhere else; an ANSI-C string (``$'...'``) honors its own quote
    escape so the spans after it stay code.
    """
    chars = list(script)
    quote: str | None = None
    index = 0
    while index < len(script):
        new_quote, consumed = _scan_char(script, index, quote)
        if quote is not None or new_quote is not None:
            _blank_span(chars, index, index + consumed)
        quote = new_quote
        index += consumed
    return "".join(chars)


def _defined_function_names(script: str) -> set[str]:
    """Names of functions the script defines (both definition forms).

    Definitions are read from real code only: quoted spans are blanked
    first, so a definition shape inside a string can neither shadow a
    command nor hide a body from the checks.
    """
    code = _masked_quotes(script)
    names = set(_DEFINED_FUNCTION_RE.findall(code))
    names.update(_FUNCTION_KEYWORD_DEF_RE.findall(code))
    return names


def _defined_alias_names(script: str, depth: int = 0) -> set[str]:
    """Aliases that can expand after a real ``expand_aliases`` enablement."""
    if depth > 12:
        return set(_SHADOWED_NAMES)
    aliases: set[str] = set()
    expansion_enabled = False
    code = _strip_shell_comments(script)
    code, substitutions, opaque = _mask_command_substitutions(code)
    if opaque:
        return set(_SHADOWED_NAMES)
    for segment in _command_segments(code):
        step = _alias_name_segment_step(segment, expansion_enabled)
        if step is None:
            return set(_SHADOWED_NAMES)
        expansion_enabled, found = step
        aliases.update(found)
    for substitution in substitutions:
        aliases.update(_defined_alias_names(substitution, depth + 1))
    return aliases


def _alias_segment_words(segment: str) -> list[str] | None:
    """Parse alias-analysis words; harmless output with broken quoting is inert."""
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return [] if re.match(r"\s*(?:echo|printf)(?:\s|$)", segment) else None


def _alias_name_segment_step(
    segment: str, expansion_enabled: bool
) -> tuple[bool, set[str]] | None:
    """Update expansion state and aliases from one parsed shell segment."""
    words = _alias_segment_words(segment)
    if words is None:
        return None
    index = _skip_env_assignments(words, 0)
    if index >= len(words):
        return expansion_enabled, set()
    command = _shell_word_basename(words[index])
    options = words[index + 1:]
    if command == "shopt":
        return _shopt_alias_expansion_state(options, expansion_enabled), set()
    if command == "alias" and expansion_enabled:
        return expansion_enabled, _shadowed_alias_names(options)
    return expansion_enabled, set()


def _shopt_alias_expansion_state(
    options: list[str], expansion_enabled: bool
) -> bool:
    """Track explicit enable/disable commands for alias expansion."""
    if "expand_aliases" not in options:
        return expansion_enabled
    if "-s" in options or "--set" in options:
        return True
    return False if "-u" in options or "--unset" in options else expansion_enabled


def _alias_definitions_can_shadow(
    script: str, shell_name: str, depth: int = 0
) -> bool:
    """Reject aliases when this shell can expand them into later commands."""
    if depth > 12:
        return True
    expansion_enabled = shell_name in {"sh", "zsh"}
    aliases_defined = False
    code = _join_continuations(
        _strip_heredocs(_strip_shell_comments(script))
    )
    masked, substitutions, opaque = _mask_command_substitutions(code)
    if opaque:
        return True
    for segment in _command_segments(masked):
        step = _alias_shadow_segment_step(
            segment, expansion_enabled, aliases_defined
        )
        if step is None:
            return True
        expansion_enabled, aliases_defined, can_shadow = step
        if can_shadow:
            return True
    return any(
        _alias_definitions_can_shadow(body, shell_name, depth + 1)
        for body in substitutions
    )


def _alias_shadow_segment_step(
    segment: str, expansion_enabled: bool, aliases_defined: bool
) -> tuple[bool, bool, bool] | None:
    """Update alias state or report a command a future alias can shadow."""
    words = _alias_segment_words(segment)
    if words is None:
        return None
    index = _skip_env_assignments(words, 0)
    if index >= len(words):
        return expansion_enabled, aliases_defined, False
    command = _shell_word_basename(words[index])
    options = words[index + 1:]
    if command == "alias" and any(
        _shell_assignment_parts(_resolve_heredoc_word(word)[0])
        is not None
        for word in options
    ):
        aliases_defined = True
        return expansion_enabled, aliases_defined, expansion_enabled
    if command == "shopt":
        expansion_enabled = _shopt_alias_expansion_state(
            options, expansion_enabled
        )
        return expansion_enabled, aliases_defined, expansion_enabled and aliases_defined
    return expansion_enabled, aliases_defined, False


def _shadowed_alias_names(options: list[str]) -> set[str]:
    """Names defined by alias assignments that shadow checked commands."""
    aliases = set()
    for word in options:
        assignment = _resolve_heredoc_word(word)[0]
        if "=" not in assignment:
            continue
        name = assignment.split("=", 1)[0]
        if name in _SHADOWED_NAMES:
            aliases.add(name)
    return aliases


def _called_function_names(script: str, defined: set[str]) -> set[str]:
    """Names the script calls: the first word of command-position lines.

    The input is a joined list of command segments with definition heads
    masked out, so a call is exactly a segment whose first word (after quote
    removal) names a defined function; arguments, quoted text and escaped
    text never sit in first-word position.
    """
    called: set[str] = set()
    for line in script.splitlines():
        words = line.split()
        if not words:
            continue
        name = _resolve_heredoc_word(words[0])[0]
        if name in defined:
            called.add(name)
    return called


def _definition_name(script: str, index: int) -> str | None:
    """The function whose body opens at ``index`` (both definition forms)."""
    prefix = script[:index]
    match = re.search(
        r"(?:function\s+)?([A-Za-z_](?a:\w)*)\s*\(\s*\)\s*$", prefix
    )
    if match is not None:
        return match[1]
    keyword = re.search(r"function\s+([A-Za-z_](?a:\w)*)\s*$", prefix)
    return keyword[1] if keyword is not None else None


@functools.lru_cache(maxsize=64)
def _function_body_walk_cached(
    script: str,
) -> tuple[tuple[tuple[str, int, int], ...], str]:
    """Memoized core of ``_function_body_walk``: spans as an immutable tuple.

    The checks rescan the same script through several helpers (masking,
    reachability, truncation); caching the walk keeps those rescans from
    re-parsing the whole text each time.  ``lru_cache`` keys on the script
    text, so each distinct script parses once per process run.
    """
    spans, state = _function_body_walk_uncached(script)
    return tuple(spans), state


def _function_body_walk(
    script: str,
) -> tuple[list[tuple[str, int, int]], str]:
    """Spans ``(name, start, end)`` of every body, plus the open structure.

    The walk matches the bodies-stripper so reachability and stripping agree
    on the structure.  A definition only opens outside quoted text: a
    ``name() {`` shape inside a string is data, so the quote state the walk
    carries is checked before a body can open and is never reset, and a
    quoted decoy can no longer swallow the spans of the real definitions
    after it.  The second element is ``""`` when every body and quoted
    string closes; otherwise it names what stays open at the end
    (``"body"`` or ``"quote"``).  An unclosed script cannot be parsed by
    the shell, so callers fail closed instead of trusting its spans.
    """
    spans, state = _function_body_walk_cached(script)
    if state:
        return list(spans), state

    discovered = list(spans)
    seen = set(discovered)
    pending = list(discovered)
    while pending:
        _parent_name, body_start, body_end = pending.pop()
        nested_spans, nested_state = _function_body_walk_cached(
            script[body_start:body_end]
        )
        if nested_state:
            return discovered, nested_state
        for name, local_start, local_end in nested_spans:
            nested = (
                name,
                body_start + local_start,
                body_start + local_end,
            )
            if nested in seen:
                continue
            seen.add(nested)
            discovered.append(nested)
            pending.append(nested)

    return sorted(discovered, key=lambda span: span[1]), ""


def _function_body_walk_uncached(
    script: str,
) -> tuple[list[tuple[str, int, int]], str]:
    spans: list[tuple[str, int, int]] = []
    index = 0
    depth = 0
    quote: str | None = None
    opener = ""
    name = ""
    body_start = 0
    while index < len(script):
        if depth == 0:
            found = (
                _opens_function_body(script, index) if quote is None else None
            )
            if found is not None:
                name = _definition_name(script, index) or ""
                body_start = index + 1
                opener = found
                depth = 1
                index += 1
                continue
            quote, consumed = _scan_char(script, index, quote)
            index += consumed
            continue
        index, depth, quote, closer = _function_body_step(
            script, index, depth, quote, opener, body_start)
        if closer and depth == 0:
            spans.append((name, body_start, index))
    if depth != 0:
        return spans, "body"
    return (spans, "quote") if quote is not None else (spans, "")


def _function_body_spans(script: str) -> list[tuple[str, int, int]]:
    """Spans ``(name, start, end)`` of every function body in the script."""
    return _function_body_walk(script)[0]


def _unclosed_structure_issue(script: str) -> str | None:
    """The fail-closed issue for a script whose structure never closes.

    A phantom definition can only satisfy the provisioning checks by
    hiding the spans of the real ones, and the shell cannot even parse the
    script, so an unclosed body or quoted string is rejected outright.
    """
    code = _masked_quotes(_strip_shell_comments(script))
    if re.search(r"(?<!(?a:\w))[@?+*!]\(", code):
        return (
            "the release-gate job's run script uses extglob syntax that the "
            "static shell model cannot delimit safely; rewrite it without "
            "extglob so its toolchain provisioning can be verified"
        )
    unclosed = _function_body_walk(script)[1]
    if unclosed == "body":
        return (
            "the release-gate job's run script ends inside an open function "
            "body, so its toolchain provisioning cannot be verified "
            "statically; close every function body"
        )
    if unclosed == "quote":
        return (
            "the release-gate job's run script ends with an unterminated "
            "quoted string, so its toolchain provisioning cannot be "
            "verified statically; close every quoted string"
        )
    return None


def _head_spans(
    script: str, bodies: list[tuple[str, int, int]]
) -> list[tuple[int, int]]:
    """Spans of the definition heads (the ``name ()`` before each body)."""
    heads: list[tuple[int, int]] = []
    for _, lo, _hi in bodies:
        prefix = script[: max(0, lo - 1)]
        match = re.search(
            r"(?:function\s+)?([A-Za-z_](?a:\w)*)\s*\(\s*\)\s*$", prefix
        )
        if match is None:
            match = re.search(r"function\s+([A-Za-z_](?a:\w)*)\s*$", prefix)
        if match is not None:
            heads.append((match.start(1), max(0, lo - 1)))
    return heads


def _effective_body_spans(
    script: str,
) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """Split body spans into the effective and the superseded ones.

    A redefined function runs its last definition, so earlier bodies of the
    same name can never execute on a later call: they are superseded.
    """
    spans = _function_body_spans(script)
    last: dict[str, int] = {
        name: position for position, (name, _lo, _hi) in enumerate(spans)
    }
    effective: list[tuple[str, int, int]] = []
    superseded: list[tuple[str, int, int]] = []
    for position, span in enumerate(spans):
        target = effective if last[span[0]] == position else superseded
        target.append(span)
    return effective, superseded


def _direct_function_spans(
    spans: list[tuple[str, int, int]], region: tuple[int, int]
) -> list[tuple[str, int, int]]:
    """Function bodies directly nested in a source region, in source order."""
    start, end = region
    contained = [
        span for span in spans if start < span[1] and span[2] <= end
    ]
    return sorted(
        (
            span
            for span in contained
            if not any(
                other[1] < span[1] and span[2] < other[2]
                for other in contained
                if other != span
            )
        ),
        key=lambda span: span[1],
    )


def _live_commands_with_function_markers(
    script: str,
    region: tuple[int, int],
    spans: list[tuple[str, int, int]],
    heads: dict[tuple[str, int, int], tuple[int, int]],
) -> tuple[list[str], dict[str, tuple[str, int, int]]]:
    """Scan a whole region and mark definitions without splitting its branches."""
    start, end = region
    direct = _direct_function_spans(spans, region)
    masked = script[start:end]
    markers: dict[str, tuple[str, int, int]] = {}
    marker_prefix = "__release_gate_definition_"
    while marker_prefix in script:
        marker_prefix = f"_{marker_prefix}"
    for index, span in reversed(list(enumerate(direct))):
        head = heads.get(span)
        if head is None:
            continue
        marker = f"{marker_prefix}{index}"
        local_start = head[0] - start
        local_end = span[2] - start
        masked = f"{masked[:local_start]}true {marker}{masked[local_end:]}"
        markers[marker] = span
    return _live_command_segments(_strip_shell_comments(masked)), markers


def _apply_definition_markers(
    command: str,
    markers: dict[str, tuple[str, int, int]],
    defined: set[str],
    active: dict[str, tuple[str, int, int]],
) -> None:
    """Record definitions that become active within ``command``."""
    for marker, span in markers.items():
        if marker in command and span[0] in defined:
            active[span[0]] = span


def _calls_in_command(
    command: str,
    defined: set[str],
    active: dict[str, tuple[str, int, int]],
) -> list[tuple[str, int, int]]:
    """Return active-definition targets called from ``command``."""
    targets = (active.get(name) for name in _called_function_names(command, defined))
    return [target for target in targets if target is not None]


def _live_function_spans(
    script: str, defined: set[str]
) -> set[tuple[str, int, int]]:
    """Bodies reachable through definitions active at each call site."""
    if not defined:
        return set()
    spans = _function_body_spans(script)
    heads = dict(zip(spans, _head_spans(script, spans)))
    pending: list[
        tuple[tuple[str, int, int], tuple[tuple[str, tuple[str, int, int]], ...]]
    ] = []

    def scan_region(
        region: tuple[int, int], active: dict[str, tuple[str, int, int]]
    ) -> None:
        live_commands, markers = _live_commands_with_function_markers(
            script, region, spans, heads
        )
        for command in live_commands:
            _apply_definition_markers(command, markers, defined, active)
            for target in _calls_in_command(command, defined, active):
                pending.append((target, tuple(sorted(active.items()))))

    scan_region((0, len(script)), {})
    live: set[tuple[str, int, int]] = set()
    visited: set[
        tuple[tuple[str, int, int], tuple[tuple[str, tuple[str, int, int]], ...]]
    ] = set()
    while pending:
        body, environment = pending.pop()
        key = (body, environment)
        if key in visited:
            continue
        visited.add(key)
        live.add(body)
        scan_region((body[1], body[2]), dict(environment))
    return live


def _trim_body_after_terminator(body: str) -> str:
    """Drop a body's commands after a top-level ``return``: everything
    after cannot run.

    The kept text preserves the original separators, so line structure and
    quoting survive for the checks that follow.  A standalone failure under
    ``set -e`` ends the shell and is handled by the live-segment scan.
    """
    pairs = _command_segments_with_separators(body)
    cut = len(body)
    previous: bool | None = None
    branches: list[tuple[bool, int]] = []
    search_from = 0
    for index, (segment, separator) in enumerate(pairs):
        found = body.find(segment, search_from)
        if found < 0:
            break
        search_from = found + len(segment)
        keyword = _segment_keyword(segment)
        condition = _pair_condition(pairs, index, keyword)
        disposition = _body_segment_disposition(
            segment, separator, keyword, condition, previous, branches
        )
        if disposition == "return":
            cut = found
            break
        if disposition == "branch":
            previous = None
            continue
        if disposition == "live":
            previous = _segment_literal(segment)
    if cut == len(body):
        return body
    # Function spans include their closing brace or parenthesis. Preserve it
    # after dropping commands that follow a top-level return.
    closer = body[-1:] if body.endswith(("}", ")")) else ""
    return body[:cut] + closer


def _body_segment_disposition(
    segment: str,
    separator: str,
    keyword: str,
    condition: bool | None,
    previous: bool | None,
    branches: list[tuple[bool, int]],
) -> str:
    """Classify a body segment as branch, skipped, live, or terminating."""
    if _branch_keyword_step(branches, segment, condition):
        marker_returns = _marker_return_status(
            segment, separator, previous, branches
        ) is not None
        return "return" if marker_returns else "branch"
    if not _region_runs(branches) or _chain_skips(separator, previous):
        return "skip"
    return "return" if keyword == "return" else "live"


def _strip_function_bodies(script: str) -> str:
    """Drop the bodies of functions the script cannot reach, and a kept
    body's commands after its own ``return`` or ``set -e`` failure.

    The release gate must provision in reachable commands: a body counts
    when its function is called from top-level code, transitively through
    bodies that run.  Bodies nobody can reach never run, so their text is
    erased (brace groups and subshells without a definition stay: they
    execute in place).
    """
    defined = _defined_function_names(script)
    if not defined:
        return script
    live = _live_function_spans(script, defined)
    chars = list(script)
    for name, lo, hi in _function_body_spans(script):
        width = hi - lo
        if (name, lo, hi) in live:
            trimmed = _trim_body_after_terminator(script[lo:hi])[:width]
            replacement = list(trimmed) + [" "] * (width - len(trimmed))
        else:
            replacement = [" "] * width
        chars[lo:hi] = replacement
    return "".join(chars)


# Provisioning commands must sit in command position (optionally behind the
# repository's `retry N` wrapper); substring matches, echoes and heredoc
# bodies must never satisfy the gate.  The component/toolchain argument must
# stay inside the same command: an unbounded tail would cross command
# separators, letting `rustup toolchain install "${RUST_TOOLCHAIN}"; echo
# --component rustfmt` satisfy the check without installing rustfmt.
_WRAPPER_COMMANDS = frozenset({"command", "exec", "builtin", "nohup", "sudo"})
# Every command name `_wrapper_prefix_length` drops before it can match the
# real command (`retry` excluded: shadowing it is the modeled case, covered
# by `_retry_runs_its_target`).  The shadow guard must span this whole set —
# including names stripped outside `_WRAPPER_COMMANDS`, like `eval` — or a
# redefined no-op defeats the gate while the literal text still matches.
_PREFIX_STRIPPED_NAMES = _WRAPPER_COMMANDS | frozenset({
    "bash", "dash", "env", "eval", "sh", "zsh",
})
# `builtin` only runs shell builtins: a known literal builtin keeps its
# literal, and a command that is surely not a builtin makes it fail.
_BUILTIN_LITERAL_WORDS = {":": True, "true": True, "false": False}
_NON_BUILTIN_COMMANDS = frozenset({
    "bash", "sh", "dash", "zsh", "python", "python3", "rustup",
    "env", "command", "exec", "nohup", "sudo",
})
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_](?a:\w)*=")
_SHELL_VARIABLE_REFERENCE_RE = re.compile(
    r"\$\{([A-Za-z_](?a:\w)*)\}|\$([A-Za-z_](?a:\w)*)"
)
_INERT_SHELL_COMMANDS = frozenset({
    "echo", "printf", "test", "[", "true", "false", ":",
})
_SHELL_CONTROL_FLOW_WORDS = frozenset({
    "case", "do", "done", "elif", "else", "esac", "fi", "for", "if",
    "select", "then", "until", "while",
})

# Bash invocation options.  Value-taking options consume a file word;
# flag-only options the option parser steps over keep parsing.  Anything
# else -- an unrecognized option, a word whose spelling the parser cannot
# model -- stops the ``-c`` unwrap, because whether a payload behind it
# runs is unknowable and unverifiable text must never count as verified.
_SHELL_VALUE_OPTIONS = frozenset({"--rcfile", "--init-file"})
_SHELL_FLAG_OPTIONS = frozenset({
    "--debug", "--debugger", "--login", "--noediting", "--noprofile",
    "--norc", "--posix", "--pretty-print", "--restricted", "--verbose",
})
# Single letters a bash invocation accepts.  `c` (either sign, as the
# invocation parser reads it) takes the payload word; `-n`/`-D`/`+D`
# suppress execution (noexec / dump-strings) while `+n` does not; `o`/`O`
# consume the following shell-option name word; an unlisted letter makes
# bash reject the invocation outright (nothing runs).
_SHELL_OPTION_LETTERS = frozenset("abcefhiklmprstuvxBCEHPTnDoO")
_SHELL_SUPPRESS_LETTERS = frozenset({"n", "D"})


# sudo's options, split by arity.  Value-taking options consume their
# argument (`sudo -u root`, `sudo --user root`); the flags are stepped
# over.  `-h`/`--help` print help (a bare `-h` prints help, and `-h host`
# binds the host but refuses to run anything outside listing mode), and
# `-V`/`-v`/`-l`/`-e`/`-U` and `--version`/`--validate`/`--list`/`--edit`
# never reach a command either, so they all make the invocation un-runnable
# instead of transparently skippable.  Anything outside this model is
# treated the same way: without a trustworthy option model the wrapper
# must not be unwrapped, because the text behind it may never run.
_SUDO_VALUE_FLAGS = frozenset({
    "-u", "-g", "-p", "-C", "-T", "-r", "-t",
    "--user", "--group", "--prompt", "--close-from", "--chdir",
    "--role", "--type", "--command-timeout", "--other-user",
})
_SUDO_FLAG_FLAGS = frozenset({
    "-A", "-B", "-E", "-H", "-N", "-P", "-S", "-b", "-i", "-k", "-n", "-s",
    "--preserve-env", "--stdin", "--set-home", "--background", "--login",
    "--shell", "--non-interactive",
})
_MAX_COMMAND_WRAPPER_DEPTH = 4


def _skip_one_option_word(
    words: list[str], index: int, value_flags: frozenset[str],
    flag_flags: frozenset[str],
) -> int | None:
    """Return the next index for one modeled wrapper option."""
    word = words[index]
    if word in value_flags:
        return index + 2 if index + 1 < len(words) else None
    if word in flag_flags:
        return index + 1
    if word.startswith("--") and "=" in word:
        option, value = word.split("=", 1)
        if option in value_flags and value:
            return index + 1
    return None


def _skip_option_words(
    words: list[str], index: int, value_flags: frozenset[str],
    flag_flags: frozenset[str],
) -> int | None:
    """Skip a wrapper's own options (`sudo -n`, `sudo -u root`); None when
    an option is outside the arity model (the caller must not unwrap)."""
    while (
        index < len(words)
        and words[index].startswith("-")
        and words[index] != "--"
    ):
        next_index = _skip_one_option_word(
            words, index, value_flags, flag_flags
        )
        if next_index is None:
            return None
        index = next_index
    return index


def _skip_env_assignments(words: list[str], index: int) -> int:
    """Skip `env VAR=VAL...` assignments."""
    while index < len(words) and _ENV_ASSIGN_RE.match(words[index]):
        index += 1
    return index


_ENV_VALUE_OPTIONS = frozenset({"-u", "--unset", "-C", "--chdir"})
_ENV_FLAG_OPTIONS = frozenset({"-i", "--ignore-environment", "-0", "--null"})
_ENV_LONG_VALUE_OPTIONS = frozenset({"--unset", "--chdir"})


def _env_option_step(words: list[str], index: int) -> int | None:
    """Return the next index for one modeled ``env`` option."""
    word = words[index]
    if word in _ENV_VALUE_OPTIONS:
        return index + 2 if index + 1 < len(words) else None
    if word.startswith("--") and "=" in word:
        option, value = word.split("=", 1)
        return index + 1 if option in _ENV_LONG_VALUE_OPTIONS and value else None
    return index + 1 if word in _ENV_FLAG_OPTIONS else None


def _skip_env_options(words: list[str], index: int) -> int:
    """Skip only modeled ``env`` options; leave unknown options unpeeled.

    The command after an unknown option may not be the next word, so its
    apparent payload cannot count as a verified command.
    """
    while (
        index < len(words)
        and words[index].startswith("-")
        and words[index] != "--"
    ):
        next_index = _env_option_step(words, index)
        if next_index is None:
            return index
        index = next_index
    return index


def _skip_env_prefix(words: list[str], index: int) -> int:
    """Consume an ``env`` command's options, ``--`` separators and
    ``VAR=VAL`` assignments in any accepted order, returning the index of
    the command word that follows."""
    probe = index
    options_open = True
    while True:
        moved = probe
        if options_open:
            moved = _skip_env_options(words, moved)
        if moved < len(words) and words[moved] == "--":
            options_open = False
            moved += 1
        moved = _skip_env_assignments(words, moved)
        if moved == probe:
            return probe
        probe = moved


def _long_shell_option_step(word: str) -> tuple[int, bool] | None:
    """Classify a long (``--``) bash option; None when it cannot run text.

    ``--rcfile``/``--init-file`` consume the following file word; the
    modeled flag-only options consume none; ``--opt=value`` forms and
    unknown long options make bash reject the invocation outright, so
    nothing behind them could ever run either.
    """
    if "=" in word or word not in _SHELL_VALUE_OPTIONS | _SHELL_FLAG_OPTIONS:
        return None
    return (2 if word in _SHELL_VALUE_OPTIONS else 1), False


def _short_shell_option_span(letters: str, sign: str) -> int | None:
    """Consumed words for one short-option cluster; None = unverifiable.

    ``o``/``O`` take the following shell-option-name word and must be the
    cluster's last letter; ``-n``/``-D``/``+D`` suppress execution; any
    unlisted letter makes bash reject the invocation outright.
    """
    if not letters or any(
        letter not in _SHELL_OPTION_LETTERS for letter in letters
    ):
        return None
    if sign == "-" and _SHELL_SUPPRESS_LETTERS.intersection(letters):
        return None
    if sign == "+" and "D" in letters:
        return None
    value_tail = letters[-1] in "oO" and (
        letters.count("o") + letters.count("O") == 1
    )
    if ("o" in letters or "O" in letters) and not value_tail:
        return None
    return 2 if value_tail else 1


def _shell_option_step(words: list[str], probe: int) -> tuple[int, bool] | None:
    """Classify one bash invocation option word at ``probe``.

    Returns ``(next_probe, command_mode)``: where option scanning
    continues, and whether this word turned on the ``-c`` command mode.
    ``None`` means the line is unverifiable from this word on -- an
    unknown spelling, a suppressed invocation, or an argument the model
    cannot place -- and nothing behind it may count as a verified
    command.  The model follows the bash option parser (the release
    runners invoke bash): quote removal happens before a word is read as
    an option, a value-taking option consumes the following word, an
    unlisted letter makes bash reject the whole invocation, and ``-n``
    /``-D``/``+D`` suppress execution.
    """
    word = _resolve_heredoc_word(words[probe])[0]
    if word.startswith("--"):
        stepped = _long_shell_option_step(word)
    elif re.fullmatch(r"[+-][A-Za-z]*", word):
        span = _short_shell_option_span(word[1:], word[0])
        stepped = (span, "c" in word[1:]) if span is not None else None
    else:
        stepped = None
    if stepped is None:
        return None
    span, command_mode = stepped
    return None if probe + span > len(words) else (probe + span, command_mode)


def _shell_scan_outcome(
    words: list[str], probe: int, command_mode: bool
) -> int | None:
    """How the word at ``probe`` ends option scanning (None = keep going).

    A non-option word is the command string in command mode and otherwise
    names a script file; ``--`` switches to positional words, so its
    successor is the command string when command mode is already on.  A
    decided outcome is the payload index, or ``-1`` when nothing can be
    unwrapped from here.
    """
    cleaned = _resolve_heredoc_word(words[probe])[0]
    if cleaned == "--":
        return probe + 1 if command_mode and probe + 1 < len(words) else -1
    if cleaned.startswith(("-", "+")) and cleaned not in ("-", "+"):
        return None
    return probe if command_mode else -1


def _skip_shell_c(words: list[str], index: int) -> int:
    """Skip `<shell> [options] -c`, returning the payload word index.

    The payload is the first positional word after the options, exactly
    as bash resolves its command string.  A word the option model cannot
    classify stops the unwrap: text behind it is unverifiable and must
    never count as a verified command (fail closed).
    """
    probe = index + 1
    command_mode = False
    while probe < len(words):
        outcome = _shell_scan_outcome(words, probe, command_mode)
        if outcome is not None:
            return outcome if outcome >= 0 else index
        stepped = _shell_option_step(words, probe)
        if stepped is None:
            return index
        probe, activated = stepped
        command_mode = command_mode or activated
    return index


def _skip_bare_separators(words: list[str], index: int) -> int:
    """Skip bare ``--`` separators left by a wrapper."""
    while index < len(words) and words[index] == "--":
        index += 1
    return index


def _shell_word_basename(word: str) -> str:
    """Resolve quotes and path prefixes before matching an executable name."""
    return Path(_resolve_heredoc_word(word)[0]).name


def _wrapper_command_step(words: list[str], index: int) -> int | None:
    """Advance past a leading command wrapper's own words; None when the
    wrapper must not be unwrapped (a `builtin`, a `command` lookup or
    invalid option spelling, or a sudo option outside the arity model).

    The wrapper's name is read after quote removal, as bash resolves it:
    a quoted ``'command'``/``"sudo"`` still runs that wrapper.
    """
    wrapper = _shell_word_basename(words[index])
    if wrapper == "builtin":
        # `builtin` can only run shell builtins, never the external
        # commands the provisioning checks match.
        return None
    if wrapper == "command":
        rest, lookup = _command_operand(words[index + 1:])
        return None if lookup else len(words) - len(rest)
    probe = _skip_option_words(
        words, index + 1, _SUDO_VALUE_FLAGS, _SUDO_FLAG_FLAGS
    )
    if probe is None:
        return None
    if probe < len(words) and words[probe] == "--":
        probe += 1
    return probe


def _unwrap_stacked_wrappers(
    words: list[str], index: int
) -> tuple[int, bool]:
    """Unwrap stacked command wrappers; flag a refused step.

    One wrapper's own words can expose another wrapper in command position
    (``command sudo rustup ...``, ``nohup env FOO=1 cmd``); the loop is
    bounded, and a step that refuses to unwrap (``builtin``, a ``command``
    lookup, an unmodeled sudo option) stops the scan with the wrapper still
    in front, so its text never counts.
    """
    for _ in range(_MAX_COMMAND_WRAPPER_DEPTH):
        if (
            index < len(words)
            and _shell_word_basename(words[index]) in _WRAPPER_COMMANDS
        ):
            step = _wrapper_command_step(words, index)
            if step is None:
                return index, True
            index = _skip_env_assignments(words, step)
        if index < len(words) and _shell_word_basename(words[index]) == "env":
            index = _skip_env_prefix(words, index + 1)
    if index < len(words) and (
        _shell_word_basename(words[index]) in _WRAPPER_COMMANDS
        or _shell_word_basename(words[index]) == "env"
    ):
        return index, True
    return index, False


def _wrapper_prefix_length(words: list[str]) -> tuple[int, bool]:
    """How many leading wrapper words to drop (a deterministic token scan),
    plus whether the rest starts at a shell ``-c`` payload.

    Handles `retry N`, command wrappers with their own options, `env
    VAR=VAL...`, `<shell> -c '...'`, `eval` and bare `--` separators, in
    the order the shell accepts them.  Names are read after quote removal:
    the shell resolves ``'bash'``/``"retry"`` to the same commands, so a
    quoted spelling must unwrap exactly like the plain one.
    """
    index = _skip_env_assignments(words, 0)
    if (
        index + 1 < len(words)
        and _shell_word_basename(words[index]) == "retry"
        and words[index + 1].isdigit()
    ):
        index += 2
    index = _skip_bare_separators(words, index)
    index, refused = _unwrap_stacked_wrappers(words, index)
    if refused:
        return index, False
    if (
        index < len(words)
        and _shell_word_basename(words[index])
        in ("bash", "sh", "dash", "zsh")
    ):
        payload = _skip_shell_c(words, index)
        if payload != index:
            return payload, True
    if index < len(words) and _resolve_heredoc_word(words[index])[0] == "eval":
        index += 1
    return index, False


def _forwards_all_arguments(words: list[str]) -> bool:
    """Whether the words invoke their whole argument list (``"$@"`` forms).

    ``eval "$@"`` runs its arguments exactly like a plain ``"$@"`` does,
    so both count as a full forward; anything else does not.
    """
    if not words:
        return False
    first = _resolve_heredoc_word(words[0])[0]
    if first in ("$@", "${@}"):
        return True
    if first != "eval" or len(words) < 2:
        return False
    argument = _resolve_heredoc_word(words[1])[0]
    return argument.strip("'\"") in ("$@", "${@}", "$*", "${*}")


def _retry_runs_its_target(script: str) -> bool:
    """Whether a locally defined ``retry`` actually runs its arguments.

    ``retry N cmd`` only provisions when the retry implementation invokes
    the command it wraps; a no-op or partial implementation never reaches
    the target, so wrapped provisioning must not count behind it.  The
    invocation may sit inside a loop or an unevaluated branch: only a
    provably dead position (a literal ``false`` branch or short-circuit)
    does not count.  An honest implementation may forward through
    ``eval "$@"``, which runs its arguments like a plain ``"$@"`` does.
    """
    effective, _superseded = _effective_body_spans(script)
    for name, lo, hi in effective:
        if name != "retry":
            continue
        for segment in _possibly_reached_segments(script[lo:hi]):
            words = _peel_execution_wrappers(segment.split())
            if _forwards_all_arguments(words):
                return True
        return False
    return True


def _mask_verified_retry_forwarders(script: str) -> str:
    """Treat a verified retry wrapper's ``$@`` as forwarding, not a command.

    The raw-install scan still checks each retry call site; only the body
    token that forwards the statically supplied argv is neutralized.
    """
    if not _retry_runs_its_target(script):
        return script
    characters = list(script)
    forwarder = re.compile(
        r'"(?:\$@|\$\{\@\})"|(?<![\\\w$])\$(?:@|\{\@\})(?![\w])'
    )
    for name, start, end in _function_body_spans(script):
        if name != "retry":
            continue
        body = script[start:end]
        for match in forwarder.finditer(body):
            token_start = start + match.start()
            token_end = start + match.end()
            characters[token_start:token_end] = [":"] + [
                " "
            ] * (token_end - token_start - 1)
    return "".join(characters)


def _possible_marker_carry(
    segment: str,
    separator: str,
    previous: bool | None,
    branches: list[tuple[bool, int]],
) -> str | None:
    """The command a marker carries when its region is not provably dead
    and the marker itself is not short-circuited."""
    if _segment_keyword(segment) not in _CARRIED_BODY_MARKERS:
        return None
    if _segment_unreachable(separator, previous) or not _region_runs(branches):
        return None
    return _body_marker_command(segment) or None


def _possibly_reached_segments(script: str) -> list[str]:
    """Segments that are not provably dead.

    Literal-``false`` branches and short-circuits are dead; loops and
    unevaluated conditions stay possible, so an invocation a retry
    implementation wraps in a loop still counts.
    """
    live: list[str] = []
    previous: bool | None = None
    branches: list[tuple[bool, int]] = []
    pairs = _command_segments_with_separators(script)
    for index, (segment, separator) in enumerate(pairs):
        keyword = _segment_keyword(segment)
        condition = _pair_condition(pairs, index, keyword)
        prior_chain = branches[-1][1] if branches else None
        if _branch_keyword_step(branches, segment, condition):
            _possible_branch_state(
                branches, keyword, condition, prior_chain
            )
            if carried := _possible_marker_carry(
                segment, separator, previous, branches
            ):
                live.append(carried)
            previous = None
            continue
        if not _region_runs(branches):
            continue
        if _segment_unreachable(separator, previous):
            continue
        if (
            _segment_keyword(segment) == "return"
            and not _chain_skips(separator, previous)
        ):
            break
        live.append(segment)
        previous = _segment_literal(segment)
    return live


def _payload_end_index(text: str) -> int:
    """The index of a quoted payload's closing quote, escape aware inside
    double quotes; -1 when the payload never closes."""
    quote = text[0]
    index = 1
    while index < len(text):
        if quote == '"' and text[index] == "\\":
            index += 2
            continue
        if text[index] == quote:
            return index
        index += 1
    return -1


def _strip_provision_wrappers(segment: str) -> str:
    """Drop wrapper tokens a provision command may sit behind.

    Shell wrappers (`retry N`, `command`, `exec`, `builtin`, `nohup`,
    `sudo`, `env VAR=VAL...`, `bash -c '...'`) do not change which command
    runs, so the gate unwraps them before matching.  This is a
    deterministic scan instead of one composed pattern, which also keeps
    the pattern scanner free of nested-quantifier complaints.
    """
    words = re.split(r"[ \t]+", segment.strip()) if segment.strip() else []
    index, payload_at = _wrapper_prefix_length(words)
    if payload_at and index < len(words) and words[index][:1] in ("'", '"'):
        # A quoted payload keeps its text up to its closing quote (escape
        # aware inside double quotes); the words after it are positional
        # parameters ($0 and later), and an unquoted -c payload is one
        # word (the same rule).
        joined = " ".join(words[index:])
        close = _payload_end_index(joined)
        rest = joined[: close + 1] if close > 0 else joined
    elif payload_at and index < len(words):
        rest = words[index]
    else:
        rest = " ".join(words[index:])
    if len(rest) >= 2 and rest[0] in "'\"" and rest[-1] == rest[0]:
        quote_char = rest[0]
        rest = rest[1:-1]
        if quote_char == '"':
            # Inside double quotes bash turns \" into a literal ".
            rest = rest.replace('\\"', '"')
    return rest


# Release workflows must provision toolchains through the verified installer
# (with an explicit `bash` invocation) and add the rustfmt component
# separately; raw `rustup toolchain install` commands are rejected.
_TOOLCHAIN_VALUE_RE = (
    r"(?:\"\$\{(?:RUST_TOOLCHAIN|FUZZ_TOOLCHAIN)\}\"|"
    r"(?<![\w'])\$\{(?:RUST_TOOLCHAIN|FUZZ_TOOLCHAIN)\}(?![\w']))"
)
_VERIFIED_INSTALLER_RE = re.compile(
    r"^bash\s+[\"']?\./packaging/scripts/install-verified-rustup\.sh[\"']?"
    r"(?=\s|$)"
    + r"[^;|&]*--toolchain\s+" + _TOOLCHAIN_VALUE_RE + r"(?=$|[\s;|&)])"
)
_COMPONENT_ADD_RE = re.compile(
    r"^rustup\s+component\s+add\b[^;|&]*--toolchain\s+"
    + _TOOLCHAIN_VALUE_RE + r"(?=$|[\s;|&)])"
)
# Shell-equivalent spellings of installing the pinned release requirements:
# `python3 -m pip install -r requirements-release.txt`, `pip3 install
# --requirement=requirements-release.txt`, quoted paths, and a leading
# `sudo`/`retry N` wrapper (already peeled by the caller).
_PIP_REQUIREMENT_RE = re.compile(
    r"^(?:python(?:3(?:\.\d+)?)?\s+-m\s+pip|pip(?:3(?:\.\d+)?)?)"
    r"\s+install\s+"
    r"(?:-r|--requirement)(?:\s+|=)[\"']?requirements-release\.txt[\"']?"
    r"(?=\s|$)"
)
_DRIFT_CHECK_RE = re.compile(
    r"^(?:python3|python)\s+tools/reason-codegen/generate\.py\s+--check\b"
)


def _quote_removed_command(text: str) -> str:
    """Remove static shell word quotes before matching a command spelling."""
    words = text.split()
    return " ".join(_resolve_heredoc_word(word)[0] for word in words)


def _quoted_separator_at(line: str, index: int, quote: str) -> int:
    """Separator length inside a quoted string (0 = data)."""
    char = line[index]
    if quote in {"'", "ansi-c"}:
        return 0
    if char == "`":
        return 1
    if (
        char == "("
        and index > 0
        and line[index - 1] == "$"
        and (index < 2 or line[index - 2] != "\\")
    ):
        return 1
    return 0


def _brace_separator_at(line: str, index: int) -> int:
    """Separator length for a brace-group token (0 = glued word data)."""
    previous = line[index - 1] if index > 0 else ""
    following = line[index + 1] if index + 1 < len(line) else ""
    before_ok = previous in ("", " ", "\t", "\n", ";", "|", "&", "(", ")")
    after_ok = following in ("", " ", "\t", "\n", ";", ")")
    return 1 if before_ok and after_ok else 0


def _body_brace_is_command_position(
    script: str, body_start: int, index: int, brace: str
) -> bool:
    """Whether a brace in a function body is a shell group token.

    A right brace is a closer only as a command in its own right; treating a
    ``}`` argument or parameter expansion as a body closer exposes the rest
    of an uncalled function as top-level code.  Open brace groups likewise
    count only at command position, not as brace expansion or an argument.
    The release workflow's supported group boundaries are separated by a
    shell operator/newline, a compound-command keyword, or a subshell end.
    """
    if not _brace_separator_at(script, index):
        return False
    if brace == "{" and _opens_function_body(script, index) is not None:
        return True
    code = _masked_quotes(script[body_start:index])
    if brace == "}" and re.search(r"(?:^|[;\n])\s*$", code) is not None:
        return True
    current = re.split(r"[;&|\n]", code)[-1].strip()
    if not current:
        return True
    last_word = current.split()[-1]
    if brace == "{":
        return last_word in {"then", "do", "else", "!"}
    return last_word in {"fi", "done", "esac"} or (
        current.startswith("(") and current.endswith(")")
    )


def _separator_at(line: str, index: int, quote: str | None) -> int:
    """Return the command-separator length at ``index`` (0 = not one).

    ``;``, ``&&``, ``||``, ``|`` and a single ``&`` split only outside
    quotes.  ``(`` and ``)`` split outside quotes (subshells) and backticks
    split everywhere except single quotes (command substitution); inside
    double quotes only a ``$``-preceded ``(`` opens a substitution, so bare
    quoted parentheses stay data.  A whitespace-delimited ``{`` or ``}``
    (a brace group) splits too, while expansion braces glued to their word
    (``${VAR}``, ``{1..3}``) stay data.
    """
    char = line[index]
    if quote is not None:
        return _quoted_separator_at(line, index, quote)
    if char in "{}":
        return _brace_separator_at(line, index)
    if char == ";":
        return 1
    if char in "&|":
        if char == "&" and ((index > 0 and line[index - 1] in "<>") or line.startswith(
                        "&>", index
                    )):
            return 0
        return 2 if line.startswith(char * 2, index) else 1
    return 1 if char in "()`" else 0


def _array_assignment_group_starts(line: str, index: int) -> bool:
    """Whether an opening parenthesis begins a shell array assignment."""
    return (
        line[index] == "("
        and re.fullmatch(
            r"\s*[A-Za-z_]\w*(?:\[[^\]\s]+\])?\+?=",
            line[:index],
        ) is not None
    )


def _flush_segment(
    segments: list[tuple[str, str]], current: list[str], separator: str
) -> None:
    """Append the finished segment with its separator, when non-empty."""
    if segment := "".join(current).strip():
        segments.append((segment, separator))


def _scan_segment_line(
    line: str,
    current: list[str],
    quote: str | None,
    segments: list[tuple[str, str]],
    separator: str,
    array_depth: int,
) -> tuple[list[str], str | None, str, int]:
    """Scan one line for separators and shell array-assignment groups."""
    index = 0
    while index < len(line):
        if array_depth == 0 and quote is None and _array_assignment_group_starts(
            line, index
        ):
            current.append(line[index])
            array_depth = 1
            index += 1
            continue
        if array_depth and quote is None and line[index] in "()":
            current.append(line[index])
            array_depth += 1 if line[index] == "(" else -1
            index += 1
            continue
        if array_depth == 0 and (length := _separator_at(line, index, quote)):
            _flush_segment(segments, current, separator)
            separator = line[index : index + length]
            current = []
            index += length
            continue
        new_quote, consumed = _scan_char(line, index, quote)
        current.append(line[index : index + consumed])
        quote = new_quote
        index += consumed
    return current, quote, separator, array_depth


@functools.lru_cache(maxsize=64)
def _command_segments_with_separators_cached(
    script: str,
) -> tuple[tuple[str, str], ...]:
    """Immutable cached result for repeated scans of the same shell text."""
    segments: list[tuple[str, str]] = []
    current: list[str] = []
    quote: str | None = None
    separator = "\n"
    array_depth = 0
    for line in script.splitlines():
        current, quote, separator, array_depth = _scan_segment_line(
            line, current, quote, segments, separator, array_depth)
        if quote is None and array_depth == 0:
            had_segment = bool("".join(current).strip())
            _flush_segment(segments, current, separator)
            if had_segment:
                separator = "\n"
            current = []
        else:
            # The newline stays data: a quoted payload with line-separated
            # commands must keep them apart for the callers.
            current.append("\n")
    _flush_segment(segments, current, separator)
    return tuple(segments)


def _command_segments_with_separators(script: str) -> list[tuple[str, str]]:
    """Return a fresh list of segments from the immutable cached scan."""
    return list(_command_segments_with_separators_cached(script))


def _command_segments(script: str) -> list[str]:
    """Split into command segments at unquoted shell separators."""
    return [segment for segment, _ in _command_segments_with_separators(script)]


def _is_redirection_word(word: str) -> bool:
    """Whether the word is a bare redirection like ``>/dev/null``/``2>&1``."""
    return re.match(r"\d*[<>]", word) is not None


def _command_operand(rest: list[str]) -> tuple[list[str], bool]:
    """``command``'s operand after its own options, plus whether the
    invocation never runs it (a lookup or an invalid option spelling).

    Bash's ``command`` builtin accepts only clusters of ``p``/``v``/``V``:
    a cluster carrying ``v``/``V`` asks for a lookup instead of running the
    operand, an all-``p`` cluster keeps it runnable, and every other
    spelling (``-x``, ``--version``, a bare ``-``) makes bash reject or
    miss the invocation, so the operand must not count as executed.
    """
    probe = 0
    lookup = False
    while (
        probe < len(rest)
        and rest[probe].startswith("-")
        and rest[probe] != "--"
    ):
        body = rest[probe][1:]
        if not body or any(letter not in "pvV" for letter in body):
            # An invalid option spelling: bash refuses the invocation (or
            # treats the word as the name), so nothing behind it runs.
            return rest[probe:], True
        if "v" in body or "V" in body:
            lookup = True
        probe += 1
    if probe < len(rest) and rest[probe] == "--":
        probe += 1
    return rest[probe:], lookup


def _peel_one_wrapper(words: list[str]) -> list[str] | None:
    """Drop one leading execution wrapper (``command`` with its options
    and an optional ``--``, ``env`` with its whole prefix, or a
    ``VAR=VAL`` assignment); None when the leading word is not a wrapper
    (``builtin`` is not one: it only runs shell builtins)."""
    first = _resolve_heredoc_word(words[0])[0]
    if first == "command" and len(words) > 1:
        rest, lookup = _command_operand(words[1:])
        return None if lookup else rest
    if _ENV_ASSIGN_RE.match(words[0]) and len(words) > 1:
        return words[1:]
    if first == "env" and len(words) > 1:
        probe = _skip_env_prefix(words, 1)
        return words[probe:] if probe < len(words) else []
    return None


def _peel_execution_wrappers(words: list[str]) -> list[str]:
    """Drop leading wrappers that do not change which command runs:
    ``command``, ``env`` with its options and ``VAR=VAL`` assignments, and
    bare assignments.  ``builtin`` is deliberately not peeled: it can only
    run shell builtins, never the external commands the checks match."""
    while words:
        peeled = _peel_one_wrapper(words)
        if peeled is None:
            break
        words = peeled
    return words


def _segment_literal(segment: str) -> bool | None:
    """The boolean a segment trivially evaluates to, or None when unknown.

    The first word resolves through quote removal and execution wrappers,
    so ``"false"``, ``command false``, ``env false`` and ``FOO=1 false``
    all read as the ``false`` command; a leading ``false`` followed only
    by redirections is False, a leading ``true`` or ``:`` is True, and
    everything else may depend on runtime state.
    """
    words, negated = _chain_prefix_words(segment)
    words = _peel_execution_wrappers(words)
    if not words:
        return None
    first = _resolve_heredoc_word(words[0])[0]
    if first == "--":
        value: bool | None = False
    elif first == "builtin":
        value = _builtin_command_literal(words)
    elif first in (":", "true"):
        value = True
    elif first == "false" and all(
        _is_redirection_word(w) for w in words[1:]
    ):
        value = False
    else:
        value = None
    return not value if negated and value is not None else value


def _builtin_command_literal(words):
    """Literal tristate of an explicit ``builtin`` invocation."""
    rest = words[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        return None
    name = _resolve_heredoc_word(rest[0])[0]
    if name in _BUILTIN_LITERAL_WORDS:
        return _BUILTIN_LITERAL_WORDS[name]
    return False if name in _NON_BUILTIN_COMMANDS else None


_OPEN_KEYWORDS = {"while", "until", "for", "case", "select"}
_CLOSE_KEYWORDS = {"fi", "done", "esac"}
_BODY_MARKERS = {"then", "do"}
_CARRIED_BODY_MARKERS = _BODY_MARKERS | {"else"}


def _segment_unreachable(separator: str, previous: bool | None) -> bool:
    """Whether a literal short-circuit on ``separator`` skips this segment."""
    return (separator == "&&" and previous is False) or (
        separator == "||" and previous is True
    )


def _segment_keyword(segment: str) -> str:
    """The segment's first word after quote removal (``""`` when empty)."""
    words = segment.split()
    return _resolve_heredoc_word(words[0])[0] if words else ""


def _pair_condition(
    pairs: list[tuple[str, str]], index: int, keyword: str
) -> bool | None:
    """The literal condition of an ``if``/``elif`` header (None otherwise)."""
    return _condition_tristate(pairs, index) if keyword in {"if", "elif"} else None


def _combine_tristate(operator: str, left: bool | None, right: bool | None) -> bool | None:
    """Fold two literal parts under ``&&``/``||`` (None = unevaluated)."""
    if operator == "&&":
        return _short_circuit_fold(left, False, right, True)
    return _short_circuit_fold(left, True, right, False)


def _short_circuit_fold(left, dominating, right, both):
    """Fold two tristate operands under one short-circuit operator.

    ``dominating`` is the value that decides the fold as soon as either
    operand equals it; ``both`` is the result when neither operand is the
    dominating value and both are known.
    """
    if left is dominating or right is dominating:
        return dominating
    return None if left is None or right is None else both


def _condition_tristate(
    pairs: list[tuple[str, str]], index: int
) -> bool | None:
    """The literal value of an ``if``/``elif`` condition across segments.

    The segmentizer splits a condition at its ``&&``/``||`` operators, so
    the parts fold back together; the condition ends at the ``then`` marker
    or at a separator that is not a chain operator.  A ``|``/``|&``
    pipeline inside the condition evaluates its last command, not its left
    literal, so the value is unknown (None) rather than the left operand's;
    a condition longer than the fold cap is None too, never a stale value.
    """
    value = _condition_literal(pairs[index][0])
    probe = index + 1
    while probe < len(pairs):
        if probe > index + 64:
            return None
        segment, separator = pairs[probe]
        if separator in ("|", "|&"):
            return None
        if _segment_keyword(segment) in _BODY_MARKERS:
            break
        if separator not in ("&&", "||"):
            break
        value = _combine_tristate(separator, value, _segment_literal(segment))
        probe += 1
    return value


def _condition_literal(segment: str) -> bool | None:
    """The literal truth of an ``if``/``elif`` condition, or None."""
    words = segment.split()
    if not words or words[0] not in ("if", "elif"):
        return None
    rest = [_resolve_heredoc_word(word)[0] for word in words[1:]]
    rest = [word for word in rest if word and not _is_redirection_word(word)]
    if rest in (["true"], [":"]):
        return True
    return False if rest == ["false"] else None


# Branch chain states, tracked per enclosing construct: an earlier branch
# provably runs (OPEN_CHAIN: later parts are dead), an earlier branch might
# run (UNKNOWN_CHAIN: fail closed, later parts stay conditional), or no
# earlier branch ran (CLEAR_CHAIN: the current part decides).
_OPEN_CHAIN = 0
_UNKNOWN_CHAIN = 1
_CLEAR_CHAIN = 2


def _region_runs(branches: list[tuple[bool, int]]) -> bool:
    """Whether the branch regions so far all provably run."""
    return all(runs for runs, _chain in branches)


def _is_exec_replacement(words: list[str]) -> bool:
    """Whether ``exec cmd`` replaces the shell, ending it (the words are
    already peeled of non-replacing wrappers).

    Redirection-only forms (``exec 3<file``) keep the shell running.
    """
    if not words or _resolve_heredoc_word(words[0])[0] != "exec":
        return False
    return any(not _is_redirection_word(word) for word in words[1:])


def _set_errexit_state(segment: str) -> bool | None:
    """Whether the segment enables (True), disables (False) errexit or
    leaves it alone (None)."""
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return None
    return None if not words or words[0] != "set" else _set_flags_state(words[1:])


def _flags_word_state(word: str) -> bool | None:
    """The errexit state one plain ``set`` word carries (None = no signal).

    A bare ``errexit`` enables it, an ``e`` in a ``+…`` cluster disables,
    an ``e`` in a ``-…`` cluster enables.
    """
    if word == "errexit":
        return True
    if word.startswith("+") and "e" in word[1:]:
        return False
    return True if word.startswith("-") and "e" in word[1:] else None


def _set_flags_state(flags: list[str]) -> bool | None:
    """The errexit state a ``set`` argument list leaves behind."""
    state: bool | None = None
    option_mode: bool | None = None
    for word in flags:
        if word in ("-o", "+o"):
            option_mode = word == "-o"
            continue
        if option_mode is not None:
            if word == "errexit":
                state = option_mode
            option_mode = None
            continue
        found = _flags_word_state(word)
        if found is not None:
            state = found
    return state


def _chain_skips(separator: str, previous: bool | None) -> bool:
    """Whether an ``&&``/``||`` link makes the segment conditional.

    A left side the analyzer did not evaluate may or may not have
    succeeded, so the chained command cannot count as unconditional
    provisioning.
    """
    return separator in {"&&", "||"} and (
        previous is None or _segment_unreachable(separator, previous)
    )


def _branch_chain_state(condition: bool | None) -> int:
    """The chain state after a branch whose condition evaluates so."""
    if condition is True:
        return _OPEN_CHAIN
    return _UNKNOWN_CHAIN if condition is None else _CLEAR_CHAIN


def _branch_select(
    branches: list[tuple[bool, int]], keyword: str, condition: bool | None
) -> None:
    """Open the branch chain for an ``if`` or advance it for an ``elif``."""
    if keyword == "elif" and branches:
        _runs, chain = branches[-1]
        runs = chain == _CLEAR_CHAIN and condition is True
        branches[-1] = (
            runs,
            chain if chain != _CLEAR_CHAIN else _branch_chain_state(condition),
        )
    elif keyword == "if":
        branches.append((condition is True, _branch_chain_state(condition)))


def _branch_keyword_step(
    branches: list[tuple[bool, int]], segment: str, condition: bool | None
) -> bool:
    """Update the branch stack for a construct keyword; True when handled.

    ``if``/``elif``/``else``/``fi`` track which branch region provably
    runs: once a branch is selected, no later ``elif``/``else`` part can
    run, and an unevaluated condition makes the rest conditional.  Loops
    and ``case`` push an unevaluated condition, so their bodies stay
    conditional; ``then``/``do`` do not change the stack.
    """
    keyword = _segment_keyword(segment)
    if keyword in _CLOSE_KEYWORDS:
        if branches:
            branches.pop()
        return True
    if keyword in _BODY_MARKERS:
        return True
    if keyword in ("if", "elif"):
        _branch_select(branches, keyword, condition)
        return True
    if keyword == "else":
        if branches:
            _runs, chain = branches[-1]
            branches[-1] = (chain == _CLEAR_CHAIN, _OPEN_CHAIN)
        return True
    if keyword in _OPEN_KEYWORDS:
        branches.append((False, _UNKNOWN_CHAIN))
        return True
    return False


def _grown_function_kill_sets(
    bodies: list[tuple[str, str]],
    failing: frozenset[str],
    exiting: frozenset[str],
) -> tuple[frozenset[str], frozenset[str]]:
    """One monotone expansion of the function failure/exit fixpoints."""
    walked = [
        (name, *_body_verdict(body, failing, exiting))
        for name, body in bodies
    ]
    grown_failing = frozenset(
        name for name, fails, exits in walked if fails or exits
    )
    grown_exiting = frozenset(
        name for name, _fails, exits in walked if exits
    )
    return grown_failing, grown_exiting


def _function_kill_sets(script: str) -> tuple[frozenset[str], frozenset[str]]:
    """Functions whose invocation provably ends in failure or exit.

    ``failing``: invoking the function unprotected provably dies under
    errexit (a provable failure with no success return before it), so a
    standalone call under ``set -e`` ends the shell.  ``exiting``: invoking
    the function provably ends the shell wherever it runs (an
    ``exit``/``exec`` replacement on the provable path), which no
    protection list exempts.  Both sets are fixpoints over the call graph:
    a body that ends by calling another such function counts too.
    """
    script = _join_continuations(script)
    effective, _superseded = _effective_body_spans(script)
    bodies = [
        (name, script[lo:hi]) for name, lo, hi in effective if name
    ]
    failing: frozenset[str] = frozenset()
    exiting: frozenset[str] = frozenset()
    for _pass in range(len(bodies) + 1):
        grown_failing, grown_exiting = _grown_function_kill_sets(
            bodies, failing, exiting
        )
        if grown_failing == failing and grown_exiting == exiting:
            break
        failing, exiting = grown_failing, grown_exiting
    return failing, exiting


def _marker_return_status(
    segment: str,
    separator: str,
    previous: bool | None,
    branches: list[tuple[bool, int]],
) -> bool | None:
    """The failure status a marker's carried return provokes on the
    provable path; None when the marker carries no taken return."""
    if not _region_runs(branches) or _chain_skips(separator, previous):
        return None
    return _return_failure(_body_marker_command(segment))


def _possible_branch_state(
    branches: list[tuple[bool, int]],
    keyword: str,
    condition: bool | None,
    prior_chain: int | None = None,
) -> None:
    """Relax the branch stack to "not provably dead" semantics: loops and
    unevaluated conditions stay possible."""
    if not branches:
        return
    _runs, chain = branches[-1]
    if keyword in _OPEN_KEYWORDS:
        branches[-1] = (True, chain)
    elif keyword in {"if", "elif"}:
        branches[-1] = (condition is not False, chain)
    elif keyword == "else":
        previous_chain = chain if prior_chain is None else prior_chain
        branches[-1] = (previous_chain != _OPEN_CHAIN, _OPEN_CHAIN)


def _body_marker_command(segment: str) -> str:
    """The command a ``then``/``do``/``else`` marker carries on its own
    segment (``then return 1``); ``""`` when the marker stands alone."""
    words = segment.split()
    return "" if len(words) < 2 else " ".join(words[1:])


def _return_failure(segment: str) -> bool | None:
    """Whether a return is provably non-zero; unknown status is not failure."""
    if _segment_keyword(segment) != "return":
        return None
    return _return_kind(segment) == "nonzero"


def _return_kind(segment: str) -> str | None:
    """The kind of ``return`` a segment performs: a non-zero argument
    (``"nonzero"``), an explicit zero (``"zero"``), no argument
    (``"bare"``: the shell propagates the previous status), ``"unknown"``
    when its status depends on a non-literal argument, or None when the
    segment is not a return."""
    if _segment_keyword(segment) != "return":
        return None
    words = segment.split()
    if len(words) < 2:
        return "bare"
    arg = words[1].strip("'\"")
    if arg == "0":
        return "zero"
    if not arg:
        return "bare"
    if re.fullmatch(r"(?a:\d)+", arg):
        # The shell truncates a return status to its low 8 bits exactly
        # like an exit status, so `return 257` fails (1) and `return 256`
        # succeeds (0); classifying by the raw literal would call both
        # unknown and let commands after a failing return look reachable.
        value = int(arg) % 256
        return "zero" if value == 0 else "nonzero"
    return "unknown"


def _chain_prefix_words(segment: str) -> tuple[list[str], bool]:
    """Segment words with a leading ``!`` run and ``time`` prefix removed.

    Returns the remaining words and a negation flag: each ``!`` word
    toggles it, so the flag carries the parity bash 5.2 computes (an even
    count is a no-op, an odd count inverts the status), while ``time`` is
    transparent (``time f`` runs ``f`` like a plain call).
    """
    words = segment.split()
    bang = False
    while words and words[0] == "!":
        bang = not bang
        words = words[1:]
    while words and _resolve_heredoc_word(words[0])[0] == "time":
        words = words[1:]
        if words and words[0].startswith("-") and words[0] != "--":
            words = words[1:]
    return words, bang


def _literal_zero_exit(words: list[str]) -> bool:
    """Whether the words are the verb ``exit 0`` specifically."""
    return (
        bool(words)
        and _resolve_heredoc_word(words[0])[0] == "exit"
        and len(words) > 1
        and words[1].strip("'\"") == "0"
    )


def _contained_list(separator: str, following: str) -> bool:
    """Whether the segment runs inside a pipeline, background list,
    subshell or command substitution.

    A ``(``/backtick before the segment, or a ``|``/``&`` on either side,
    puts the end in a child: an ``exit`` is contained there, and only the
    status the container reports reaches the enclosing shell.
    """
    return separator in {"(", "`", "|", "&"} or following in {"|", "&"}


def _contained_failure(
    following: str,
) -> tuple[str | None, bool | None]:
    """How a contained failure (or non-zero exit) reaches the parent.

    A pipeline or background container swallows the status, an embedded
    argument substitution (``)`` right after, more command to come) is
    swallowed too (the outer command's own status decides), a chain
    operator makes the failure conditional, and a standalone container
    (``x=$(...)``, ``(...)``) dies under errexit through its own status.
    """
    if following in {"|", "&", ")"}:
        return None, None
    return (None, False) if following in {"&&", "||"} else ("fail", None)


def _exit_segment_state(
    words: list[str],
    separator: str,
    following: str,
    exiting: frozenset[str],
) -> tuple[str | None, bool | None] | None:
    """Verdict/status for a segment that names an exit-class command.

    ``exit``, an ``exec`` replacement and a call to a function in ``exiting``
    end the shell wherever they run; a contained list (pipeline, background,
    subshell, substitution) holds the end.  Returns None when the segment is
    not exit-class at all, so the caller keeps classifying.
    """
    first = _resolve_heredoc_word(words[0])[0] if words else ""
    if first == "exit" or _is_exec_replacement(words) or first in exiting:
        if _contained_list(separator, following):
            if _literal_zero_exit(words):
                return None, None
            return _contained_failure(following)
        return "exit", None
    return None


def _return_segment_state(
    segment: str, first: str, previous: bool | None
) -> tuple[str | None, bool | None] | None:
    """Verdict/status for a ``return`` segment, or None when not one.

    ``first`` is the segment's command word after the same chain-prefix
    and wrapper peeling the caller applied, so a wrapped or ``!``-prefixed
    ``return`` is classified exactly like a bare one.
    """
    if first != "return":
        return None
    kind = _return_kind(segment)
    if kind == "nonzero":
        return "fail", None
    return ("fail", None) if kind == "bare" and previous is False else ("ok", None)


def _failing_segment_state(
    separator: str,
    following: str,
) -> tuple[str | None, bool | None]:
    """Verdict/status for a segment whose literal value is False."""
    if following in {"&&", "||"}:
        return None, False
    if _contained_list(separator, following):
        return _contained_failure(following)
    return "fail", None


def _live_segment_state(
    segment: str,
    separator: str,
    following: str,
    previous: bool | None,
    failing: frozenset[str],
    exiting: frozenset[str],
) -> tuple[str | None, bool | None]:
    """The shell-end verdict and chain status of one live segment.

    Verdicts: ``"exit"`` ends the shell unconditionally wherever it runs,
    ``"fail"`` dies under errexit when unprotected, ``"ok"`` terminates a
    body normally, and None keeps running.  ``exit`` is only contained by
    pipelines, background lists, subshells and substitutions; a failure is
    also exempted as a non-final ``&&``/``||`` operand (including the whole
    run of a function called there).  The status feeds the chain tracker
    (None = unknown).
    """
    words, _bang = _chain_prefix_words(segment)
    words = _peel_execution_wrappers(words)
    first = _resolve_heredoc_word(words[0])[0] if words else ""
    exit_state = _exit_segment_state(words, separator, following, exiting)
    if exit_state is not None:
        return exit_state
    return_state = _return_segment_state(segment, first, previous)
    if return_state is not None:
        return return_state
    value = _segment_literal(segment)
    if value is None and first in failing:
        value = False
    if value is False:
        return _failing_segment_state(separator, following)
    return None, value


def _verdict_ends(verdict: str | None, errexit: bool) -> bool:
    """Whether a segment verdict ends the shell under the errexit state."""
    return True if verdict == "exit" else verdict == "fail" and errexit


def _condition_exit(segment: str, exiting: frozenset[str]) -> bool:
    """Whether an ``if``/``elif``/``while`` condition itself provably exits."""
    words, _bang = _chain_prefix_words(" ".join(segment.split()[1:]))
    words = _peel_execution_wrappers(words)
    if not words:
        return False
    first = _resolve_heredoc_word(words[0])[0]
    return first == "exit" or _is_exec_replacement(words) or first in exiting


def _branch_step_state(
    segment: str,
    keyword: str,
    separator: str,
    following: str,
    previous: bool | None,
    branches: list[tuple[bool, int]],
    failing: frozenset[str],
    exiting: frozenset[str],
) -> tuple[str | None, bool | None]:
    """The carried-command verdict and status of a construct keyword step.

    ``if true; then exit 1`` and ``then bail`` carry a command that ends
    the shell when the branch provably runs; an exiting condition
    (``if bail; then``) provokes the end wherever it is evaluated.  The
    status feeds the chain tracker exactly like a plain segment's does, so
    a carried ``then false`` still short-circuits the ``||`` after it.
    """
    if keyword in {"then", "do", "else"}:
        if not _region_runs(branches) or _chain_skips(separator, previous):
            return None, None
        if carried := _body_marker_command(segment):
            return _live_segment_state(
                carried, ";", following, previous, failing, exiting
            )
        else:
            return None, None
    if _condition_exit(segment, exiting) and keyword in {"if", "elif", "while", "until"}:
        return "exit", None
    return None, None


def _body_can_succeed(body: str) -> bool:
    """Whether a body can possibly return success.

    A reachable ``return 0`` or bare ``return`` (which propagates a status
    that can be zero) means the function can succeed, so it must never
    carry the always-failing label; the possible-path walk keeps loops and
    unevaluated branches alive for exactly this check.
    """
    return any(
        _return_kind(segment) in ("zero", "bare", "unknown")
        for segment in _possibly_reached_segments(body)
    )


def _verdict_disposition(
    verdict: str | None, can_succeed: bool
) -> tuple[bool, bool] | None:
    """Map a segment verdict to the (fails, exits) answer, or None.

    ``exit`` ends the shell from any position; ``fail`` ends it under
    errexit unless the body can possibly succeed.  None means the verdict
    carries no terminal disposition and the walk continues.
    """
    if verdict == "exit":
        return False, True
    return (not can_succeed, False) if verdict == "fail" else None


def _body_step_verdict(
    pairs: list[tuple[str, str]],
    index: int,
    previous: bool | None,
    branches: list[tuple[bool, int]],
    can_succeed: bool,
    failing: frozenset[str],
    exiting: frozenset[str],
) -> tuple[tuple[bool, bool] | None, bool | None, bool]:
    """One step of the body walk.

    Returns ``(disposition, previous, dead)``: a non-None disposition is
    the final ``(fails, exits)`` answer; otherwise ``dead`` tells the
    caller the step was skipped (dead region or short-circuit) and
    ``previous`` carries the state for the next step.
    """
    segment, separator = pairs[index]
    following = pairs[index + 1][1] if index + 1 < len(pairs) else ""
    keyword = _segment_keyword(segment)
    condition = _pair_condition(pairs, index, keyword)
    if _branch_keyword_step(branches, segment, condition):
        verdict, status = _branch_step_state(
            segment, keyword, separator, following, previous, branches,
            failing, exiting,
        )
        return _verdict_disposition(verdict, can_succeed), status, False
    if not _region_runs(branches) or _chain_skips(separator, previous):
        return None, previous, True
    verdict, status = _live_segment_state(
        segment, separator, following, previous, failing, exiting
    )
    disposition = _verdict_disposition(verdict, can_succeed)
    if disposition is None and verdict == "ok":
        disposition = (False, False)
    return disposition, status, False


def _body_verdict(
    body: str, failing: frozenset[str], exiting: frozenset[str]
) -> tuple[bool, bool]:
    """Whether invoking this body provably ends the shell, and how.

    ``fails``: an errexit-fatal event occurs on the provable path before
    the body returns, so a non-exempt call under ``set -e`` ends the
    shell; a possibly-reached success return disqualifies the label,
    because such a function can succeed.  ``exits``: the body provably
    reaches ``exit``/``exec`` on the provable path, which ends the shell
    from any non-contained position regardless of errexit.  Both sets feed
    the call-graph fixpoint in ``_function_kill_sets``.
    """
    can_succeed = _body_can_succeed(body)
    pairs = _command_segments_with_separators(body)
    previous: bool | None = None
    branches: list[tuple[bool, int]] = []
    for index in range(len(pairs)):
        disposition, previous_out, dead = _body_step_verdict(
            pairs, index, previous, branches, can_succeed, failing, exiting
        )
        if disposition is not None:
            return disposition
        if not dead:
            previous = previous_out
    return (not can_succeed, False) if previous is False else (False, False)


def _live_scan_step(
    pairs: list[tuple[str, str]],
    index: int,
    previous: bool | None,
    branches: list[tuple[bool, int]],
    exited: bool,
    errexit: bool,
    failing: frozenset[str],
    exiting: frozenset[str],
) -> tuple[bool, bool, bool | None, bool, bool]:
    """One step of the live-command scan.

    Returns ``(is_live, exited, previous, errexit, carry)``: ``is_live``
    marks a segment on the unconditional path (the caller appends it);
    ``carry`` is False when the step is provably dead or short-circuited,
    so the caller keeps its current chain state instead of the returned
    one.  ``exited`` and ``errexit`` are the updated shell-end state.
    """
    segment, separator = pairs[index]
    following = pairs[index + 1][1] if index + 1 < len(pairs) else ""
    condition = _pair_condition(pairs, index, _segment_keyword(segment))
    if _branch_keyword_step(branches, segment, condition):
        carried = (
            _body_marker_command(segment)
            if _segment_keyword(segment) in _CARRIED_BODY_MARKERS
            else ""
        )
        marker_is_live = (
            bool(carried)
            and not exited
            and _region_runs(branches)
            and not _segment_unreachable(separator, previous)
        )
        verdict, status = _branch_step_state(
            segment,
            _segment_keyword(segment),
            separator,
            following,
            previous,
            branches,
            failing,
            exiting,
        )
        if not exited and _verdict_ends(verdict, errexit):
            exited = True
        return marker_is_live, exited, status, errexit, True
    if exited or not _region_runs(branches) or _chain_skips(separator, previous):
        return False, exited, previous, errexit, False
    verdict, status = _live_segment_state(
        segment, separator, following, previous, failing, exiting
    )
    if _verdict_ends(verdict, errexit):
        return True, True, None, errexit, True
    state = _set_errexit_state(segment)
    if state is not None:
        errexit = state
    return True, exited, status, errexit, True


def _live_command_segments(
    script: str,
    failing: frozenset[str] = frozenset(),
    exiting: frozenset[str] = frozenset(),
    *,
    errexit: bool = True,
) -> list[str]:
    """Segments on the unconditional path of one shell's script.

    A command counts when the analyzer can prove it runs: an ``if`` with an
    unevaluated condition hides both branches, loops and ``case`` hide
    their bodies, an ``&&``/``||`` chain whose left side is not an
    evaluated literal is conditional itself, and ``exit``/``return`` end
    the run.  Literal conditions and short-circuits are modeled, so
    ``if true`` bodies and ``true &&`` chains still count; a standalone
    failing command under ``set -e`` ends the run too, including through
    calls to local functions that provably end in a failure or an exit.
    """
    live: list[str] = []
    previous: bool | None = None
    branches: list[tuple[bool, int]] = []
    exited = False
    # GitHub's default bash invocation adds ``-e -o pipefail``.  A custom
    # shell such as ``bash {0}`` receives no such implicit options.
    pairs = _command_segments_with_separators(script)
    for index in range(len(pairs)):
        # Each tuple carries the separator BEFORE its segment, so the
        # separator after this segment comes from the next tuple.
        is_live, exited_out, previous_out, errexit_out, carry = _live_scan_step(
            pairs, index, previous, branches, exited, errexit, failing, exiting
        )
        if is_live:
            segment = pairs[index][0]
            carried = (
                _body_marker_command(segment)
                if _segment_keyword(segment) in _CARRIED_BODY_MARKERS
                else ""
            )
            live.append(carried or segment)
        if carry:
            exited = exited_out
            previous = previous_out
            errexit = errexit_out
    return live


def _rustfmt_component_index(
    segments: list[str], after: int | None = None
) -> int | None:
    """Position of the segment that installs rustfmt for the pinned toolchain.

    Accepts either argument order inside a single ``rustup component add``
    command segment; the segment boundary keeps a later command's arguments
    from satisfying the requirement.  With ``after``, only the segments
    after that position qualify, so a reordered list that still contains a
    valid installer→component pair passes.
    """
    for position, segment in enumerate(segments):
        if after is not None and position <= after:
            continue
        if not _COMPONENT_ADD_RE.match(_strip_provision_wrappers(segment)):
            continue
        if re.search(r"\brustfmt\b", segment) and re.search(
            r"--toolchain\s+[\"']?\$\{RUST_TOOLCHAIN\}[\"']?", segment
        ):
            return position
    return None


def _literal_true_condition(value: str) -> bool:
    """Whether an ``if:`` expression runs on the normal path.

    GitHub expressions ``true``, ``success()`` and ``always()`` all leave
    the step running on the happy path (``success()`` is the default
    gate); every other expression may skip it."""
    text = value.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    return text in ("true", "success()", "always()")


def _syntax_only_shell_mode(words: list[str]) -> bool:
    """Whether a shell command requests syntax checking instead of execution."""
    for index, word in enumerate(words[1:], start=1):
        if word == "{0}":
            break
        if word in {"-n", "--noexec"}:
            return True
        if word.startswith("-") and not word.startswith("--") and "n" in word[1:]:
            return True
        if word in {"-o", "-O"} and index + 1 < len(words) and words[index + 1] == "noexec":
            return True
    return False


_EXECUTING_SHELL_TEMPLATE_PREFIXES = {
    "bash": frozenset({
        (),
        ("--noprofile", "--norc"),
        ("-e",),
        ("-u",),
        ("-x",),
        ("-eu",),
        ("-eux",),
        ("-euo", "pipefail"),
        ("-euxo", "pipefail"),
        ("-e", "-o", "pipefail"),
        ("-eo", "pipefail"),
        ("--noprofile", "--norc", "-e", "-o", "pipefail"),
        ("--noprofile", "--norc", "-eo", "pipefail"),
    }),
    "sh": frozenset({
        (),
        ("-e",),
        ("-u",),
        ("-x",),
        ("-eu",),
        ("-eux",),
    }),
    "zsh": frozenset({
        (),
        ("-e",),
        ("-u",),
        ("-x",),
        ("-eu",),
        ("-eux",),
        ("-o", "pipefail"),
    }),
}


def _step_runs_shell(step: dict) -> bool:
    """Whether a workflow step's ``run`` executes in a shell on every path.

    A step gated by ``if`` may never run. A step whose ``shell`` is not
    bash/sh/zsh feeds ``run`` to another interpreter, so neither can satisfy
    a provisioning check. GitHub's default is
    ``continue-on-error: false``; an explicit true or unparseable value means
    the step cannot prove successful provisioning.
    """
    if "continue-on-error" in step and step["continue-on-error"] is not False:
        return False
    if "if" in step:
        condition = step.get("if")
        if isinstance(condition, bool):
            if not condition:
                return False
        elif not (
            isinstance(condition, str) and _literal_true_condition(condition)
        ):
            return False
    shell = step.get("shell")
    if shell is None:
        return True
    if not isinstance(shell, str):
        # An unexpected shape cannot be known to feed bash; fail closed.
        return False
    try:
        words = shlex.split(shell, posix=True)
    except ValueError:
        return False
    return _shell_template_executes_script(words)


def _shell_template_executes_script(words: list[str]) -> bool:
    """Accept only known bash/sh templates that execute GitHub's script file."""
    if not words:
        return False
    name = words[0].rsplit("/", 1)[-1]
    allowed_prefixes = _EXECUTING_SHELL_TEMPLATE_PREFIXES.get(name)
    if allowed_prefixes is None:
        return False
    if len(words) == 1:
        return True
    script_indexes = [index for index, word in enumerate(words[1:], start=1)
                      if word == "{0}"]
    if len(script_indexes) != 1:
        return False
    script_index = script_indexes[0]
    prefix = tuple(words[1:script_index])
    return prefix in allowed_prefixes and not _syntax_only_shell_mode(
        words[:script_index + 1]
    )


def _load_workflow_object(workflow_content: str) -> dict | None:
    """Parse workflow YAML into a mapping, or fail closed on malformed input."""
    try:
        workflow = yaml.safe_load(workflow_content)
    except yaml.YAMLError:
        return None
    return workflow if isinstance(workflow, dict) else None


def _env_mapping(value: object) -> dict[str, object]:
    """Keep named environment values without dropping dynamic overrides."""
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _step_environment_scopes(
    workflow_env: object, job_env: object, step_env: object
) -> dict[str, dict[str, object]]:
    """Return the three GitHub Actions env scopes in precedence order."""
    return {
        "workflow": _env_mapping(workflow_env),
        "job": _env_mapping(job_env),
        "step": _env_mapping(step_env),
    }


def _merge_environment_scopes(
    scopes: dict[str, dict[str, object]]
) -> dict[str, object]:
    """Apply workflow → job → step precedence to a run step."""
    effective: dict[str, object] = {}
    for scope in scopes.values():
        effective |= scope
    return effective


def _is_shell_run_step(step: object) -> bool:
    """Whether a workflow step has a parseable run script and shell."""
    return (
        isinstance(step, dict)
        and isinstance(step.get("run"), str)
        and _step_runs_shell(step)
    )


def _workflow_job_steps(job: object) -> list[dict] | None:
    """Return structurally valid job steps, or fail closed on malformed YAML."""
    if not isinstance(job, dict):
        return None
    steps = job.get("steps", [])
    if not isinstance(steps, list):
        return None
    if any(not isinstance(step, dict) for step in steps):
        return None
    for step in steps:
        has_run = "run" in step
        has_uses = "uses" in step
        if has_run == has_uses:
            return None
        if has_run and not isinstance(step["run"], str):
            return None
        if has_uses and not isinstance(step["uses"], str):
            return None
    return steps


def _run_default_field(container: object, field: str) -> str | None:
    """Return one ``defaults.run.<field>`` value from a workflow or job."""
    if not isinstance(container, dict):
        return None
    defaults = container.get("defaults")
    run_defaults = defaults.get("run") if isinstance(defaults, dict) else None
    if not isinstance(run_defaults, dict):
        return None
    return run_defaults.get(field)


def _workflow_run_defaults(workflow: dict | None) -> str | list | None:
    """Return the workflow-level ``defaults.run.shell``, if declared."""
    return _run_default_field(workflow, "shell")


def _job_run_defaults(job: object) -> str | list | None:
    """Return the job-level ``defaults.run.shell``, if declared."""
    return _run_default_field(job, "shell")


def _effective_step_working_directory(
    step: dict,
    job_default: str | None,
    workflow_default: str | None,
) -> str | None:
    """Resolve a run step's working directory by GitHub's precedence.

    Step ``working-directory`` wins over job ``defaults.run.working-directory``,
    which wins over the workflow-level default.  A step that declares none
    inherits them, and an inherited value away from the repository root
    redirects the make invocation exactly as a step-level one does.
    """
    directory = step.get("working-directory")
    if directory is None:
        directory = job_default
    if directory is None:
        directory = workflow_default
    return directory


def _effective_step_shell(
    step: dict, job_default: str | list | None, workflow_default: str | list | None
) -> str | list | None:
    """Resolve a run step's shell by GitHub's precedence.

    Step ``shell`` wins over job ``defaults.run.shell``, which wins over the
    workflow-level default.  A step that declares none inherits them, so the
    effective shell must be recorded: an inherited Python shell would
    otherwise read as the bash fallback and a raw install inside it could be
    missed by the shell-only scan.
    """
    shell = step.get("shell")
    if shell is None:
        shell = job_default
    if shell is None:
        shell = workflow_default
    return shell


def _job_run_step_records(
    workflow_content: str, job_name: str
) -> list[dict] | None:
    """Return executable run steps with shell and scoped environment metadata."""
    workflow = _load_workflow_object(workflow_content)
    jobs = workflow.get("jobs") if workflow is not None else None
    if not isinstance(jobs, dict) or job_name not in jobs:
        return None
    workflow_env = workflow.get("env") if workflow is not None else None
    workflow_default = _workflow_run_defaults(workflow)
    workflow_directory = _run_default_field(workflow, "working-directory")
    job = jobs[job_name]
    if not isinstance(job, dict):
        return None
    job_default = _job_run_defaults(job)
    job_directory = _run_default_field(job, "working-directory")
    steps = _workflow_job_steps(job)
    if steps is None:
        return None
    records: list[dict] = []
    for step in steps:
        # The shell filter must see the EFFECTIVE shell: a step that omits
        # `shell` under a job or workflow default of `python {0}` does not
        # run in bash, and treating it as a shell step would let the
        # shell-only provisioning checks believe they covered it.
        shell = _effective_step_shell(step, job_default, workflow_default)
        effective_step = dict(step)
        if shell is not None:
            effective_step["shell"] = shell
        if not _is_shell_run_step(effective_step):
            continue
        scopes = _step_environment_scopes(
            workflow_env, job.get("env"), step.get("env")
        )
        records.append({
            "run": step["run"],
            "shell": shell,
            "working-directory": _effective_step_working_directory(
                step, job_directory, workflow_directory
            ),
            "env": _merge_environment_scopes(scopes),
            "env_scopes": scopes,
        })
    return records



def _job_run_scripts(workflow_content: str, job_name: str) -> list[str] | None:
    """Return one executable run script per step, or None when absent."""
    records = _job_run_step_records(workflow_content, job_name)
    return None if records is None else [record["run"] for record in records]


def _local_reusable_workflow_path(uses: object) -> Path | None:
    """Resolve one local reusable workflow within the repository root."""
    if not isinstance(uses, str) or not uses.startswith("./"):
        return None
    relative = Path(uses)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    root = PROJECT_ROOT.resolve()
    try:
        resolved = (root / relative).resolve(strict=True)
        resolved.relative_to(root)
        if (
            not resolved.is_file()
            or resolved.stat().st_size > 1_048_576
            or resolved.suffix not in {".yml", ".yaml"}
        ):
            return None
    except (OSError, ValueError):
        return None
    return resolved


def _ordinary_job_run_step_records(
    job: dict, workflow_default: str | list | None = None
) -> list[dict] | None:
    """Extract every shell run step from one ordinary workflow job."""
    steps = _workflow_job_steps(job)
    if steps is None:
        return None
    job_default = _job_run_defaults(job)
    return [
        {
            "run": step["run"],
            "shell": _effective_step_shell(step, job_default, workflow_default),
        }
        for step in steps
        if isinstance(step.get("run"), str)
    ]


def _reusable_workflow_run_step_records(
    job: dict, depth: int, active_workflows: set[Path]
) -> list[dict] | None:
    """Load a local reusable workflow while rejecting cycles and ambiguity."""
    reusable_path = _local_reusable_workflow_path(job.get("uses"))
    if reusable_path is None or reusable_path in active_workflows or "steps" in job:
        return None
    try:
        reusable_content = reusable_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    active_workflows.add(reusable_path)
    try:
        return _all_job_run_step_records(
            reusable_content, depth + 1, active_workflows
        )
    finally:
        active_workflows.remove(reusable_path)


def _all_job_run_step_records(
    workflow_content: str,
    depth: int = 0,
    active_workflows: set[Path] | None = None,
) -> list[dict] | None:
    """Return run text and shell metadata for every workflow step.

    Keep every run step, including conditional steps; follow local reusable
    workflows recursively. Unverifiable references and cycles fail closed.
    """
    if depth > 16:
        return None
    workflow = _load_workflow_object(workflow_content)
    jobs = workflow.get("jobs") if workflow is not None else None
    if not isinstance(jobs, dict):
        return None
    # The workflow-level default applies to jobs without their own default;
    # resolve it once so a step that omits `shell` records the shell it
    # actually runs under instead of falling back to bash.
    workflow_default = _workflow_run_defaults(workflow)
    active = active_workflows if active_workflows is not None else set()
    records: list[dict] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            return None
        job_records = (
            _reusable_workflow_run_step_records(job, depth, active)
            if "uses" in job
            else _ordinary_job_run_step_records(job, workflow_default)
        )
        if job_records is None:
            return None
        records.extend(job_records)
    return records


_PLACEHOLDER_SEGMENT_SPLIT_RE = re.compile(r"[;&|]+|\n")


def _placeholder_consumer_heads(
    words: list[str], command_index: int
) -> set[str]:
    """Return the command-position heads of every segment consuming ``{0}``.

    GitHub substitutes the script path for each ``{0}`` in the shell
    template, so a segment that names it as an argument is a consumer of
    the generated script.  Only a consumer's COMMAND POSITION proves which
    interpreter runs the block: a Python token sitting in argument position
    (``echo python3 {0}``) is data, not an interpreter, and a decoy in an
    earlier segment (``echo python3 {0}; bash {0}``) must not mask the
    shell launcher that actually executes the script.
    """
    heads: set[str] = set()
    for index in range(command_index + 1, len(words)):
        if words[index] == "{0}":
            # A standalone placeholder is consumed by the preceding word.
            heads.add(Path(words[index - 1]).name)
            continue
        if "{0}" in words[index]:
            heads |= _segment_heads_consuming_placeholder(words[index])
    return heads


def _segment_heads_consuming_placeholder(word: str) -> set[str]:
    """Return command-position heads of one word's ``{0}`` segments."""
    heads: set[str] = set()
    for segment in _PLACEHOLDER_SEGMENT_SPLIT_RE.split(word):
        if "{0}" not in segment:
            continue
        try:
            segment_words = shlex.split(segment.strip(), posix=True)
        except ValueError:
            segment_words = segment.strip().split()
        if segment_words:
            heads.add(Path(segment_words[0]).name)
    return heads


def _python_interpreter_before_placeholder(
    words: list[str], command_index: int
) -> bool:
    """Whether a Python interpreter consumes GitHub's script operand.

    Two template shapes reach the run block: the placeholder as its own word
    (`python3 {0}`), and the placeholder nested inside a quoted argument
    (`bash -c "python3 {0}"`, which shlex keeps as one word).  The check is
    command-position based so a Python token that is merely data or a decoy
    in an earlier segment cannot route a shell template to the Python
    analyzer, where a shell launcher would go unanalyzed.
    """
    return any(
        _PYTHON_COMMAND.fullmatch(head)
        for head in _placeholder_consumer_heads(words, command_index)
    )


_TEMPLATE_PLACEHOLDER_SENTINEL = "__workflow_step_script_placeholder__"


def _shell_template_executable_payloads(shell: object) -> list[str]:
    """Return the command-string payloads a custom shell template runs.

    The runner substitutes the generated script path into ``{0}``; commands
    the template spells out itself (``bash -c 'rustup ...' {0}``) execute
    in addition to the run block, and the run-block scan cannot see them.
    The command option is recognized in every short-option cluster bash
    accepts (``-c``, ``-ec``, ``-lc``), and the ``{0}`` placeholder is
    replaced with an inert sentinel token: the generated script is the
    step's run block, which the caller scans separately, so the
    placeholder must not make the rest of the payload unresolvable.
    """
    if not isinstance(shell, str):
        return []
    try:
        words = shlex.split(shell, posix=True)
    except ValueError:
        return []
    payloads: list[str] = [
        words[index + 1].replace("{0}", _TEMPLATE_PLACEHOLDER_SENTINEL)
        for index, word in enumerate(words[:-1])
        if _shell_option_selects_command(word)
    ]
    return payloads


def _shell_option_selects_command(word: str) -> bool:
    """Whether one shell token selects the command-string option (``-c``).

    Bash accepts ``-c`` standalone and inside short-option clusters
    (``-ec``, ``-lc``); long options never carry it.
    """
    return True if word == "--command" else _shell_option_has_flag(word, "c")


def _shell_template_conflicting_placeholder_consumers(shell: object) -> bool:
    """Whether more than one interpreter consumes the script placeholder.

    A template such as ``bash -c "bash {0}; python3 {0}"`` runs the same
    block under two interpreters.  Neither analysis alone covers it, so the
    caller fails closed by applying both.
    """
    if not isinstance(shell, str):
        return False
    try:
        words = shlex.split(shell, posix=True)
    except ValueError:
        return False
    if not words:
        return False
    command_index = 0
    command = Path(words[0]).name
    if command == "env":
        command_index = _skip_env_prefix(words, 1)
    elif command in {"uv", "poetry", "pipenv"} and len(words) > 1:
        if words[1] != "run":
            return False
        command_index = 2
    heads = _placeholder_consumer_heads(words, command_index)
    has_python = any(_PYTHON_COMMAND.fullmatch(head) for head in heads)
    has_shell = any(head in ("bash", "sh", "zsh", "dash") for head in heads)
    return has_python and has_shell


def _workflow_shell_uses_python(shell: object) -> bool:
    """Whether a custom workflow shell executes its run block as Python."""
    if not isinstance(shell, str):
        return False
    try:
        words = shlex.split(shell, posix=True)
    except ValueError:
        return False
    if not words:
        return False

    command_index = 0
    command = Path(words[0]).name
    if command == "env":
        command_index = _skip_env_prefix(words, 1)
    elif command in {"uv", "poetry", "pipenv"} and len(words) > 1:
        if words[1] != "run":
            return False
        command_index = 2
    if command_index < len(words) and _PYTHON_COMMAND.fullmatch(
        Path(words[command_index]).name
    ):
        return True
    # Custom runner templates can wrap the interpreter in another launcher;
    # recognizing a Python command before GitHub's script operand is
    # conservative and keeps shell-source from being mistaken for Python.
    return _python_interpreter_before_placeholder(words, command_index)


def _workflow_shell_name(shell: object) -> str:
    """Resolve the shell executable for a recognized workflow template."""
    if not isinstance(shell, str):
        return "bash"
    try:
        words = shlex.split(shell, posix=True)
    except ValueError:
        return "bash"
    if not words:
        return "bash"
    index = _skip_env_prefix(words, 1) if Path(words[0]).name == "env" else 0
    return Path(words[index]).name if index < len(words) else "bash"


def _mentions_raw_install(text: str) -> bool:
    """Whether an unparsed payload names the forbidden install sequence."""
    return bool(re.search(r"(?:^|\s)rustup\s+toolchain\s+install(?:\s|$)", text))


_XARGS_FLAGS = frozenset({
    "-0", "--null", "-p", "--interactive", "-r", "--no-run-if-empty",
    "-t", "--verbose", "-x", "--exit",
})
_XARGS_VALUE_OPTIONS = frozenset({
    "-d", "--delimiter", "-E", "--eof", "-I", "-L", "--max-lines",
    "-n", "--max-args", "-P", "--max-procs", "-s", "--max-chars",
    "-a", "--arg-file",
})
_XARGS_OPTIONAL_VALUE_OPTIONS = frozenset({"-i", "--replace"})
_XARGS_ATTACHED_VALUE_OPTIONS = frozenset(
    {"-d", "-E", "-I", "-i", "-L", "-n", "-P", "-s", "-a"}
)


def _xargs_option_advance(word: str, index: int) -> int | None:
    """Return the next argv index after one supported xargs option."""
    if word in _XARGS_OPTIONAL_VALUE_OPTIONS:
        return index + 1
    if word in _XARGS_VALUE_OPTIONS:
        return index + 2
    if word.startswith("--") and "=" in word:
        return index + 1
    if len(word) > 2 and word[:2] in _XARGS_ATTACHED_VALUE_OPTIONS:
        return index + 1
    return index + 1 if word in _XARGS_FLAGS else None


def _xargs_command_index(words: list[str]) -> int | None:
    """Return xargs' command operand after its supported static options."""
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--":
            return index + 1
        if not word.startswith("-") or word == "-":
            return index
        index = _xargs_option_advance(word, index)
        if index is None:
            return None
    return None


def _timeout_option_next_index(words: list[str], index: int) -> int | None:
    """Return the next index after one recognized timeout option."""
    value_options = {"-k", "--kill-after", "-s", "--signal"}
    flag_options = {"--foreground", "--preserve-status", "-v", "--verbose"}
    word = words[index]
    if word in flag_options:
        return index + 1
    if word in value_options:
        return index + 2 if index + 1 < len(words) else None
    return index + 1 if word.startswith("--") and "=" in word else None


def _timeout_command_index(words: list[str]) -> int | None:
    """Return timeout's command operand after its duration and options."""
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--":
            command_index = index + 2  # duration, then command
            return command_index if command_index < len(words) else None
        next_index = _timeout_option_next_index(words, index)
        if next_index is not None:
            index = next_index
            continue
        return None if word.startswith("-") and word != "-" else index + 1
    return index


_PROCESS_PREFIX_FLAGS = {
    "time": frozenset({
        "-a", "--append", "-p", "--portability", "-q", "--quiet",
        "-v", "--verbose",
    }),
    "nice": frozenset(),
    "setsid": frozenset({
        "-c", "--ctty", "-f", "--fork", "-w", "--wait",
    }),
    "stdbuf": frozenset(),
}
_PROCESS_PREFIX_VALUES = {
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "setsid": frozenset(),
    "stdbuf": frozenset({
        "-i", "--input", "-o", "--output", "-e", "--error",
    }),
}
_PROCESS_PREFIX_ATTACHED = {
    "time": ("-f", "-o"),
    "nice": ("-n",),
    "setsid": (),
    "stdbuf": ("-i", "-o", "-e"),
}


def _process_prefix_option_advance(word: str, prefix: str) -> int | None:
    """Number of argv words consumed by one recognized process option."""
    values = _PROCESS_PREFIX_VALUES[prefix]
    if word in values:
        return 2
    if word.startswith("--") and "=" in word:
        return 1 if word.partition("=")[0] in values else None
    if any(word.startswith(option) for option in _PROCESS_PREFIX_ATTACHED[prefix]):
        return 1
    return 1 if word in _PROCESS_PREFIX_FLAGS[prefix] else None


def _process_prefix_argument_step(
    words: list[str], index: int, prefix: str
) -> tuple[int, bool] | None:
    """Return the next argv index and whether it names the wrapped command."""
    word = words[index]
    if word == "--":
        return index + 1, True
    if word == "-" or not word.startswith("-"):
        return index, True
    if prefix == "nice" and re.fullmatch(r"-\d+", word):
        return index + 1, False
    advance = _process_prefix_option_advance(word, prefix)
    if advance is None or index + advance > len(words):
        return None
    return index + advance, False


def _process_prefix_command_index(words: list[str]) -> int | None:
    """Return a transparent process prefix's command after its options."""
    if not words:
        return None
    prefix = Path(words[0]).name
    if prefix not in _PROCESS_PREFIX_FLAGS:
        return None
    index = 1
    while index < len(words):
        step = _process_prefix_argument_step(words, index, prefix)
        if step is None:
            return None
        index, is_command = step
        if is_command:
            return index
    return None


def _find_exec_argv(words: list[str]) -> list[list[str]] | None:
    """Extract find's static -exec/-execdir commands, if well formed."""
    commands: list[list[str]] = []
    index = 0
    while index < len(words):
        if words[index] not in ("-exec", "-execdir"):
            index += 1
            continue
        start = index + 1
        end = start
        while end < len(words) and words[end] not in (";", "+"):
            end += 1
        if end in [len(words), start]:
            return None
        commands.append(words[start:end])
        index = end + 1
    return commands


def _shell_assignment_parts(word: str) -> tuple[str, str] | None:
    """Return a static shell assignment word as (name, value)."""
    if _ENV_ASSIGN_RE.match(word) is None:
        return None
    name, separator, value = word.partition("=")
    if not separator or re.fullmatch(r"[A-Za-z_](?a:\w)*", name) is None:
        return None
    return name, value


def _static_assignment_value(value: str) -> str | None:
    """Keep only literal assignment values; expansions remain unknown."""
    return None if "$" in value else value


def _shell_variable_reference_is_escaped(text: str, start: int) -> bool:
    """Whether a simple variable token is joined to an escape or dollar."""
    return start > 0 and text[start - 1] in {"\\", "$"}


def _expand_static_variable_pass(
    text: str, variables: dict[str, str | None]
) -> str | None:
    """Expand one layer of simple shell references or reject the layer."""
    references = list(_SHELL_VARIABLE_REFERENCE_RE.finditer(text))
    if not references:
        return None
    pieces: list[str] = []
    cursor = 0
    for reference in references:
        if _shell_variable_reference_is_escaped(text, reference.start()):
            return None
        name = reference.group(1) or reference.group(2)
        value = variables.get(name)
        if value is None:
            return None
        pieces.extend((text[cursor:reference.start()], value))
        cursor = reference.end()
    pieces.append(text[cursor:])
    return "".join(pieces)


def _expand_static_shell_variables(
    text: str, variables: dict[str, str | None] | None
) -> str | None:
    """Expand a bounded chain of simple variables with known literal values."""
    known = variables or {}
    for _ in range(8):
        if "$" not in text:
            return text
        expanded = _expand_static_variable_pass(text, known)
        if expanded is None:
            return None
        text = expanded
    return None if "$" in text else text


def _eval_payload_is_inert(
    payload: str, variables: dict[str, str | None] | None
) -> bool:
    """Whether every unresolved eval command is provably an inert builtin."""
    if "$" in payload or "`" in payload:
        return False
    for segment in _command_segments(payload):
        try:
            words = shlex.split(segment, posix=True)
        except ValueError:
            return False
        command_index = _skip_env_assignments(words, 0)
        if command_index >= len(words):
            continue
        command = _resolve_heredoc_word(words[command_index])[0]
        command = _expand_static_shell_variables(command, variables)
        if command not in _INERT_SHELL_COMMANDS:
            return False
    return True


def _eval_command_has_dynamic_substitution(words: list[str]) -> bool:
    """Whether this command runs eval with output from a shell substitution."""
    index = _skip_env_assignments(words, 0)
    controls = {"if", "then", "elif", "else", "while", "until", "do", "!"}
    while index < len(words) and _resolve_heredoc_word(words[index])[0] in controls:
        index += 1
        index = _skip_env_assignments(words, index)
    if index >= len(words):
        return False
    command = _resolve_heredoc_word(words[index])[0]
    arguments = words[index + 1:]
    if command == "command":
        arguments, lookup = _command_operand(arguments)
        if lookup or not arguments:
            return False
        command, *arguments = arguments
        command = _resolve_heredoc_word(command)[0]
    elif command in {"builtin", "exec"}:
        if not arguments:
            return False
        command, *arguments = arguments
        command = _resolve_heredoc_word(command)[0]
    if command != "eval":
        return False
    return any("$(" in word or "`" in word for word in arguments)


def _eval_has_dynamic_substitution(script: str) -> bool:
    """Fail closed on opaque eval source before shell segmentation can split it."""
    script = _strip_shell_comments(script)
    try:
        lexer = shlex.shlex(
            script, posix=True, punctuation_chars=";&|{}\n"
        )
        lexer.whitespace = " \t"
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return re.search(
            r"(?m)(?:^|[;&|]\s*)(?:(?:if|then|elif|else|while|until|do|!)\s+)*"
            r"(?:(?:command|builtin|exec)\s+)*eval(?=\s|$)",
            script,
        ) is not None

    current: list[str] = []
    separators = ";&|{}\n"
    for token in tokens:
        if token and all(character in separators for character in token):
            if _eval_command_has_dynamic_substitution(current):
                return True
            current = []
        else:
            current.append(token)
    return _eval_command_has_dynamic_substitution(current)


def _segment_has_shell_control_flow(segment: str) -> bool:
    """Whether a segment makes sequential variable values path-dependent."""
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return True
    command_index = _skip_env_assignments(words, 0)
    if command_index >= len(words):
        return False
    command = _resolve_heredoc_word(words[command_index])[0]
    return command in _SHELL_CONTROL_FLOW_WORDS or bool(
        re.match(
            r"^\s*(?:function\s+[A-Za-z_]\w*|[A-Za-z_]\w*\s*\(\s*\))\s*\{",
            segment,
        )
    )


def _update_static_shell_variables(
    segment: str, variables: dict[str, str | None]
) -> None:
    """Record unconditional literal assignment statements for later eval."""
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return
    if not words:
        return
    command = _resolve_heredoc_word(words[0])[0]
    if command == "unset":
        for name in words[1:]:
            if re.fullmatch(r"[A-Za-z_](?a:\w)*", name):
                variables[name] = None
        return
    assignment_words = (
        words[1:] if command in {"export", "local", "readonly"} else words
    )
    assignments = [_shell_assignment_parts(word) for word in assignment_words]
    if not assignments or any(assignment is None for assignment in assignments):
        return
    for assignment in assignments:
        if assignment is not None:
            name, value = assignment
            variables[name] = _static_assignment_value(value)


def _raw_install_in_loop_segments(
    segments: list[str],
    separators: list[str],
    index: int,
    depth: int,
    variables: dict[str, str | None],
) -> tuple[bool, int] | None:
    """Inspect a split loop body and return its detection and resume position."""
    if depth > 12:
        remainder = " ".join(segments[index:])
        found = _mentions_raw_install(remainder) or (
            _deep_segment_has_opaque_launcher(remainder, variables)
        )
        return found, len(segments)
    if index + 1 >= len(segments) or not _is_do_segment(segments[index + 1]):
        return None
    combined = f"{segments[index]} {segments[index + 1]}"
    if _raw_install_in_segment(combined, depth + 1, variables):
        return True, index
    do_segment = re.sub(r"^\s*do\b", "", segments[index + 1], count=1).strip()
    body_segments = [do_segment] if do_segment else []
    body_separators = ["\n"] if do_segment else []
    index += 2
    while index < len(segments) and not _is_done_segment(segments[index]):
        body_segments.append(segments[index])
        body_separators.append(separators[index])
        index += 1
    body_has_raw_install = _raw_install_in_segments(
        body_segments, depth + 1, variables, body_separators
    )
    if index < len(segments):
        index += 1
    return body_has_raw_install, index


def _raw_install_segment_step(
    segments: list[str],
    index: int,
    depth: int,
    variables: dict[str, str | None],
    path_dependent: bool,
    separators: list[str],
) -> tuple[bool, int]:
    """Analyze one shell segment and return whether it found a raw install."""
    segment = segments[index]
    if _rustup_segment_opens_dynamic_subcommand(segment, separators, index):
        return True, index
    if _is_loop_header(segment):
        loop_result = _raw_install_in_loop_segments(
            segments, separators, index, depth, variables
        )
        if loop_result is not None:
            return loop_result
    if _raw_install_in_segment(segment, depth + 1, variables):
        return True, index
    if not path_dependent:
        _update_static_shell_variables(segment, variables)
    return False, index + 1


def _shell_test_segment_step(
    segment: str,
    index: int,
    segments: list[str],
    separators: list[str],
) -> tuple[bool, int]:
    """Skip a complete bracket test only across its logical-chain splits."""
    opener = _shell_test_opener(segment)
    if opener is None:
        return False, index + 1
    closer_index = _shell_test_closer_index(
        segments, index, separators, opener
    )
    return (False, index + 1) if closer_index is None else (True, closer_index + 1)


def _shell_test_closer_index(
    segments: list[str],
    index: int,
    separators: list[str],
    closer: str,
) -> int | None:
    """Find a closing bracket word before a non-chain command boundary."""
    for probe in range(index, len(segments)):
        if probe > index and separators[probe] not in {"&&", "||"}:
            return None
        try:
            lexer = shlex.shlex(segments[probe], posix=False)
            lexer.whitespace_split = True
            lexer.commenters = ""
            if closer in lexer:
                return probe
        except ValueError:
            return None
    return None


def _raw_install_in_segments(
    segments: list[str],
    depth: int,
    variables: dict[str, str | None] | None = None,
    separators: list[str] | None = None,
) -> bool:
    """Scan sequential shell segments with bounded literal-variable state."""
    if separators is None:
        separators = ["\n"] * len(segments)
    elif len(separators) != len(segments):
        return True
    local_variables = dict(variables or {})
    path_dependent = any(_segment_has_shell_control_flow(segment) for segment in segments)
    if path_dependent:
        local_variables.clear()
    index = 0
    while index < len(segments):
        segment = segments[index].strip()
        skip_segment, next_index = _shell_test_segment_step(
            segment, index, segments, separators
        )
        if skip_segment:
            index = next_index
            continue
        found, index = _raw_install_segment_step(
            segments, index, depth, local_variables, path_dependent, separators
        )
        if found:
            return True
    return False


def _shell_test_opener(segment: str) -> str | None:
    """Return the closing token for an executable-free shell test fragment."""
    condition = re.sub(r"^(?:if|elif|while|until)\s+", "", segment)
    if condition.startswith("[["):
        return "]]"
    return "]" if condition.startswith(("[ ", "[\t")) else None


def _is_loop_header(segment: str) -> bool:
    """Whether the segment starts with a loop header (for/while/until)."""
    words = segment.strip().split()
    return len(words) > 0 and words[0] in ("for", "while", "until")


def _is_do_segment(segment: str) -> bool:
    """Whether the segment starts with 'do'."""
    words = segment.strip().split()
    return len(words) > 0 and words[0] == "do"


def _is_done_segment(segment: str) -> bool:
    """Whether the segment starts with 'done'."""
    words = segment.strip().split()
    return len(words) > 0 and words[0] == "done"


def _command_substitution_quoted_step(
    script: str,
    index: int,
    quote: str,
    quote_stack: list[str | None],
    depth: int,
) -> tuple[int, str | None, int, bool]:
    char = script[index]
    if quote == "'":
        return index + 1, None if char == "'" else quote, depth, False
    if char == "\\" and index + 1 < len(script):
        return index + 2, quote, depth, False
    if script.startswith("$(", index):
        quote_stack.append(quote)
        return index + 2, None, depth + 1, False
    return index + 1, None if char == '"' else quote, depth, False


def _command_substitution_unquoted_step(
    script: str,
    index: int,
    quote_stack: list[str | None],
    depth: int,
) -> tuple[int, str | None, int, bool]:
    char = script[index]
    if char == "\\" and index + 1 < len(script):
        return index + 2, None, depth, False
    if script.startswith("$(", index):
        quote_stack.append(None)
        return index + 2, None, depth + 1, False
    if char in ["'", '"']:
        return index + 1, char, depth, False
    if char == "(":
        quote_stack.append(None)
        return index + 1, None, depth + 1, False
    if char != ")":
        return index + 1, None, depth, False
    if depth == 1:
        return index + 1, None, depth, True
    return index + 1, quote_stack.pop(), depth - 1, False


def _command_substitution_scan_step(
    script: str,
    index: int,
    quote: str | None,
    quote_stack: list[str | None],
    depth: int,
) -> tuple[int, str | None, int, bool]:
    if quote is None:
        return _command_substitution_unquoted_step(
            script, index, quote_stack, depth
        )
    return _command_substitution_quoted_step(
        script, index, quote, quote_stack, depth
    )


def _command_substitution_end(script: str, start: int) -> int | None:
    """Return the matching close for ``$(``, respecting nested shell quotes."""
    if not script.startswith("$(", start):
        return None
    depth = 1
    quote: str | None = None
    quote_stack: list[str | None] = []
    index = start + 2
    while index < len(script):
        index, quote, depth, done = _command_substitution_scan_step(
            script, index, quote, quote_stack, depth
        )
        if done:
            return index
    return None


def _dollar_is_escaped(script: str, index: int) -> bool:
    """Whether an odd backslash run suppresses this shell dollar expansion."""
    backslashes = 0
    position = index - 1
    while position >= 0 and script[position] == "\\":
        backslashes += 1
        position -= 1
    return backslashes % 2 == 1


def _mask_command_substitutions(
    script: str,
) -> tuple[str, list[str], bool]:
    """Separate command-substitution bodies from their containing shell word."""
    masked: list[str] = []
    bodies: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(script):
        if (
            quote != "'"
            and script.startswith("$(", index)
            and not _dollar_is_escaped(script, index)
        ):
            end = _command_substitution_end(script, index)
            if end is None:
                return "".join(masked), bodies, True
            bodies.append(script[index + 2 : end - 1])
            masked.append("__shell_command_substitution__")
            index = end
            continue
        next_quote, consumed = _scan_char(script, index, quote)
        masked.append(script[index : index + consumed])
        quote = next_quote
        index += consumed
    return "".join(masked), bodies, False


def _shell_substitution_is_file_read(substitution: str) -> bool:
    """Recognize Bash's command-free ``$(<file)`` read optimization."""
    return re.fullmatch(
        r"\s*<\s*(?:'[^']*'|\"(?:\\.|[^\"\\])*\"|[^\s;|&<>]+)\s*",
        substitution,
    ) is not None


def _raw_install_in_script(
    script: str, depth: int = 0, variables: dict[str, str | None] | None = None
) -> bool:
    """Scan shell commands, nested substitutions and executable heredocs."""
    if _eval_has_dynamic_substitution(script):
        return True
    uncommented = _mask_verified_retry_forwarders(
        _strip_shell_comments(script)
    )
    if _dynamic_heredoc_markers(uncommented):
        return True
    python_bodies, opaque_python = _python_stdin_heredoc_bodies(uncommented)
    shell_bodies = _shell_stdin_heredoc_bodies(uncommented)
    heredoc_free = _join_continuations(_strip_heredocs(uncommented))
    masked, substitutions, opaque_substitution = _mask_command_substitutions(
        heredoc_free
    )
    if opaque_python or opaque_substitution:
        return True
    command_pairs = _command_segments_with_separators(masked)
    if _raw_install_in_segments(
        [segment for segment, _separator in command_pairs],
        depth,
        variables,
        [separator for _segment, separator in command_pairs],
    ):
        return True
    if any(
        _raw_install_in_script(body, depth + 1, variables)
        for body in substitutions
        if not _shell_substitution_is_file_read(body)
    ) or any(
        _raw_install_in_script(body, depth + 1, variables)
        for body in shell_bodies
    ):
        return True
    # An unquoted heredoc delimiter lets the shell expand the body, so a
    # `$(...)` inside a `cat <<EOF` block executes; the body is data for
    # command scanning but its substitutions are executable.
    if _raw_install_in_expanded_heredocs(uncommented, depth, variables):
        return True
    return any(
        _python_inline_raw_install(body, depth + 1, variables)
        for body in python_bodies
    )


def _raw_install_from_dispatcher(
    words: list[str], depth: int, variables: dict[str, str | None] | None = None
) -> bool:
    """Follow indirect command launchers with statically locatable operands."""
    if words[0] == "xargs":
        command_index = _xargs_command_index(words)
        if command_index is None:
            return _mentions_raw_install(" ".join(words))
        return _raw_install_from_words(words[command_index:], depth + 1, variables)
    if words[0] == "timeout":
        command_index = _timeout_command_index(words)
        if command_index is None or command_index >= len(words):
            return _mentions_raw_install(" ".join(words))
        return _raw_install_from_words(words[command_index:], depth + 1, variables)
    if words[0] == "find":
        commands = _find_exec_argv(words)
        if commands is None:
            return _mentions_raw_install(" ".join(words))
        return any(
            _raw_install_from_words(command, depth + 1, variables)
            for command in commands
        )
    if Path(words[0]).name in _PROCESS_PREFIX_FLAGS:
        command_index = _process_prefix_command_index(words)
        if command_index is None:
            return _mentions_raw_install(" ".join(words))
        return _raw_install_from_words(
            words[command_index:], depth + 1, variables
        )
    return False


def _raw_install_from_exec_wrapper(
    words: list[str], depth: int, variables: dict[str, str | None] | None
) -> bool:
    """Follow a direct shell ``exec`` command."""
    return _raw_install_from_words(words[1:], depth + 1, variables)


def _raw_install_from_eval_wrapper(
    words: list[str], depth: int, variables: dict[str, str | None] | None
) -> bool:
    """Reparse eval text after resolving statically known variable values."""
    payload = " ".join(words[1:])
    expanded = _expand_static_shell_variables(payload, variables)
    if expanded is not None:
        return _raw_install_in_script(expanded, depth + 1, variables)
    if _raw_install_in_segment(payload, depth + 1, variables):
        return True
    return not _eval_payload_is_inert(payload, variables)


def _raw_install_from_env_wrapper(
    words: list[str], depth: int, variables: dict[str, str | None] | None
) -> bool:
    """Follow an env wrapper while carrying its literal assignments."""
    command_index = _skip_env_prefix(words, 1)
    local_variables = dict(variables or {})
    for word in words[1:command_index]:
        assignment = _shell_assignment_parts(word)
        if assignment is not None:
            name, value = assignment
            local_variables[name] = _static_assignment_value(value)
    return _raw_install_from_words(
        words[command_index:], depth + 1, local_variables
    )


def _raw_install_from_command_wrapper(
    words: list[str], depth: int, variables: dict[str, str | None] | None
) -> bool:
    """Follow a modeled `command` invocation and its operands."""
    operands, lookup = _command_operand(words[1:])
    return False if lookup else _raw_install_from_words(
        operands, depth + 1, variables
    )


_PYTHON_COMMAND = re.compile(r"python(?:3(?:\.\d+)?)?\Z")
_PYTHON_SHELL_LAUNCHERS = frozenset({
    "os.system",
    "os.popen",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "asyncio.create_subprocess_shell",
})
_PYTHON_ARGV_LAUNCHERS = frozenset({
    "asyncio.create_subprocess_exec",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.run",
})
_PYTHON_STAR_IMPORTS = {
    "os": (
        "system", "popen", "execv", "execve", "execl", "execle", "execlp",
        "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
    ),
    "subprocess": (
        "Popen", "call", "check_call", "check_output", "run", "getoutput",
        "getstatusoutput",
    ),
    "asyncio": ("create_subprocess_exec", "create_subprocess_shell"),
}


def _python_call_name(
    function: ast.expr,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    """Resolve common imported Python launcher aliases to qualified names."""
    attributes: list[str] = []
    current = function
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    root = current.id
    if not attributes:
        return imported_names.get(root, root)
    prefix = imported_names.get(root, module_aliases.get(root, root))
    return ".".join((prefix, *reversed(attributes)))


def _python_static_sequence_value(
    node: ast.List | ast.Tuple,
    values: dict[str, object],
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> object | None:
    """Resolve a list or tuple only when all its elements are static."""
    parts = [
        _python_static_expression_value(
            item, values, module_aliases, imported_names
        )
        for item in node.elts
    ]
    return parts if isinstance(node, ast.List) else tuple(parts)


def _is_approved_executable_resolver(target: str | None) -> bool:
    """Recognize helpers that return a named allowlisted binary."""
    return target == "resolve_approved_executable" or (
        target is not None and target.endswith(".resolve_approved_executable")
    )


def _is_rustup_shim_resolver(target: str | None) -> bool:
    """Recognize the helper that returns only the cargo/rustc shims."""
    return target == "resolve_rustup_tool_shim" or (
        target is not None and target.endswith(".resolve_rustup_tool_shim")
    )


def _python_resolved_executable_value(
    node: ast.expr,
    values: dict[str, object],
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    """Resolve known toolchain helper calls to their executable name."""
    if not isinstance(node, ast.Call):
        return None
    target = _python_call_name(node.func, module_aliases, imported_names)
    if target == "_resolve_fuzz_cargo" and not node.args:
        return "cargo"
    if target == "_validated_nginx_binary" and not node.args:
        return "nginx"
    if target == "str" and len(node.args) == 1:
        value = _python_static_expression_value(
            node.args[0], values, module_aliases, imported_names
        )
        return str(value) if value is not None else None
    if not node.args:
        return None
    value = _python_static_expression_value(
        node.args[0], values, module_aliases, imported_names
    )
    if not isinstance(value, str):
        return None
    if _is_approved_executable_resolver(target):
        return value
    if _is_rustup_shim_resolver(target) and value in {"cargo", "rustc"}:
        return value
    return None


def _python_static_fstring_value(
    node: ast.JoinedStr,
    values: dict[str, object],
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    """Resolve f-strings whose formatted values are statically known."""
    parts: list[str] = []
    for value_node in node.values:
        if isinstance(value_node, ast.Constant) and isinstance(
            value_node.value, str
        ):
            parts.append(value_node.value)
        elif isinstance(value_node, ast.FormattedValue):
            value = _python_static_expression_value(
                value_node.value, values, module_aliases, imported_names
            )
            if value is None:
                return None
            parts.append(str(value))
        else:
            return None
    return "".join(parts)


def _python_path_root_value(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name) and node.id in {"REPO_ROOT", "PROJECT_ROOT"}:
        return "."
    return None


def _python_path_parent_value(
    node: ast.expr,
    values: dict[str, object],
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    parent = node.value if isinstance(node, ast.Subscript) else node
    if isinstance(parent, ast.Attribute) and parent.attr == "parents":
        return _python_path_expression_value(
            parent.value, values, module_aliases, imported_names
        )
    return None


def _python_path_call_value(
    node: ast.expr,
    values: dict[str, object],
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Attribute) and node.func.attr == "resolve":
        return _python_path_expression_value(
            node.func.value, values, module_aliases, imported_names
        )
    target = _python_call_name(node.func, module_aliases, imported_names)
    if target not in {"Path", "pathlib.Path"} or not node.args:
        return None
    if isinstance(node.args[0], ast.Name) and node.args[0].id == "__file__":
        return "."
    value = _python_static_expression_value(
        node.args[0], values, module_aliases, imported_names
    )
    return value if isinstance(value, str) else None


def _python_path_join_value(
    node: ast.expr,
    values: dict[str, object],
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
        return None
    left = _python_static_expression_value(
        node.left, values, module_aliases, imported_names
    )
    right = _python_static_expression_value(
        node.right, values, module_aliases, imported_names
    )
    if isinstance(left, str) and isinstance(right, str):
        return str(Path(left) / right)
    return None


def _python_path_expression_value(
    node: ast.expr,
    values: dict[str, object],
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    """Resolve simple pathlib expressions under the repository root."""
    return (
        _python_path_root_value(node)
        or _python_path_parent_value(
            node, values, module_aliases, imported_names
        )
        or _python_path_call_value(node, values, module_aliases, imported_names)
        or _python_path_join_value(node, values, module_aliases, imported_names)
    )


def _python_static_expression_value(
    node: ast.expr,
    values: dict[str, object],
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> object | None:
    """Resolve literal values and names assigned one unambiguous value."""
    path_value = _python_path_expression_value(
        node, values, module_aliases, imported_names
    )
    if path_value is not None:
        return path_value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, (ast.List, ast.Tuple)):
        return _python_static_sequence_value(
            node, values, module_aliases, imported_names
        )
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "executable"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    ):
        return "python3"
    if isinstance(node, ast.JoinedStr):
        return _python_static_fstring_value(
            node, values, module_aliases, imported_names
        )
    executable = _python_resolved_executable_value(
        node, values, module_aliases, imported_names
    )
    if executable is not None:
        return executable
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _python_assignment_parts(
    node: ast.AST,
) -> tuple[list[ast.expr], ast.expr | None]:
    """Return simple assignment targets and their right-hand expression."""
    if isinstance(node, ast.Assign):
        return node.targets, node.value
    if isinstance(node, ast.AnnAssign):
        return [node.target], node.value
    return [], None


def _record_python_static_assignment(
    name: str,
    value: object | None,
    values: dict[str, object],
    assigned: set[str],
) -> None:
    """Keep a name only while every assignment has the same literal value."""
    if name in assigned and values.get(name) != value:
        values.pop(name, None)
    elif value is not None:
        values[name] = value
    assigned.add(name)


def _python_scope_maps(
    tree: ast.AST,
) -> tuple[dict[int, int | None], dict[int, str]]:
    """Associate AST nodes with their enclosing function scope."""
    node_scopes: dict[int, int | None] = {}
    scope_names: dict[int, str] = {}

    def visit(node: ast.AST, scope: int | None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            node_scopes[id(node)] = scope
            child_scope = id(node)
            scope_names[child_scope] = node.name
            for expression in (
                *node.decorator_list,
                *node.args.defaults,
                *(value for value in node.args.kw_defaults if value is not None),
            ):
                visit(expression, scope)
            for statement in node.body:
                visit(statement, child_scope)
            return
        if isinstance(node, ast.Lambda):
            node_scopes[id(node)] = scope
            child_scope = id(node)
            scope_names[child_scope] = "<lambda>"
            visit(node.body, child_scope)
            return
        node_scopes[id(node)] = scope
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, None)
    return node_scopes, scope_names


def _python_static_assignment_values(
    tree: ast.AST,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
    node_scopes: dict[int, int | None],
    scope: int | None,
    initial: dict[str, object] | None = None,
) -> dict[str, object]:
    """Collect unambiguous assignments from one Python lexical scope."""
    values = dict(initial or {})
    assigned: set[str] = set()
    nodes = sorted(
        ast.walk(tree), key=lambda item: getattr(item, "lineno", 0)
    )
    for node in nodes:
        if node_scopes.get(id(node)) != scope:
            continue
        targets, expression = _python_assignment_parts(node)
        if expression is None:
            continue
        value = _python_static_expression_value(
            expression, values, module_aliases, imported_names
        )
        for target in targets:
            if isinstance(target, ast.Name):
                _record_python_static_assignment(
                    target.id, value, values, assigned
                )
    return values


def _python_release_gate_bash_launcher(
    call: ast.Call, argv: list[object], caller_name: str | None
) -> bool:
    """Recognize the fixed local-gate runner's allowlisted script dispatch."""
    if caller_name != "_run_local_gate" or len(argv) != 2 or argv[0] != "bash":
        return False
    payload = call.args[0] if call.args else None
    return (
        isinstance(payload, ast.List)
        and len(payload.elts) == 2
        and isinstance(payload.elts[1], ast.Call)
        and isinstance(payload.elts[1].func, ast.Name)
        and payload.elts[1].func.id == "str"
        and len(payload.elts[1].args) == 1
        and isinstance(payload.elts[1].args[0], ast.Name)
        and payload.elts[1].args[0].id == "script_path"
    )


def _python_interpreter_argv_is_raw(
    payload: list[object] | tuple[object, ...],
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Inspect a known Python source operand despite later dynamic args."""
    known_prefix: list[str] = []
    for part in payload:
        if not isinstance(part, str):
            break
        known_prefix.append(part)
    if not known_prefix:
        return True
    return _raw_install_from_python_command(
        known_prefix, depth + 1, variables
    )


def _python_rustup_argv_is_raw(
    payload: list[object] | tuple[object, ...],
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    if any(not isinstance(part, str) for part in payload):
        return True
    argv = [part for part in payload if isinstance(part, str)]
    return _raw_install_from_words(argv, depth + 1, variables)


def _python_shell_argv_is_raw(
    call: ast.Call,
    payload: list[object] | tuple[object, ...],
    depth: int,
    variables: dict[str, str | None] | None,
    caller_name: str | None,
) -> bool:
    if "-c" in payload:
        index = payload.index("-c")
        if index + 1 >= len(payload):
            return True
        source = payload[index + 1]
        if not isinstance(source, str):
            return True
        return _raw_install_in_script(source, depth + 1, variables)
    if _python_release_gate_bash_launcher(call, list(payload), caller_name):
        return False
    if len(payload) < 2 or not isinstance(payload[1], str):
        return True
    return _raw_install_from_shell_script_file(
        payload[1], depth + 1, variables
    )


def _python_subprocess_argv_is_raw(
    call: ast.Call,
    payload: object,
    depth: int,
    variables: dict[str, str | None] | None,
    caller_name: str | None,
) -> bool:
    """Check process argv without confusing data arguments with executables."""
    if not isinstance(payload, (list, tuple)) or not payload:
        return True
    executable = payload[0]
    if not isinstance(executable, str):
        return True
    command = Path(executable).name
    if command == "rustup":
        return _python_rustup_argv_is_raw(payload, depth, variables)
    if command in {"bash", "sh", "dash", "zsh", "ksh"}:
        return _python_shell_argv_is_raw(
            call, payload, depth, variables, caller_name
        )
    if command in {"python", "python2", "python3"} or command.startswith(
        "python3."
    ):
        return _python_interpreter_argv_is_raw(payload, depth, variables)
    if command in {
        "command", "exec", "env", "nice", "nohup", "retry", "setsid",
        "sudo", "stdbuf", "time", "timeout", "xargs",
    }:
        if not all(isinstance(part, str) for part in payload):
            return True
        return _raw_install_from_words(list(payload), depth + 1, variables)
    if not all(isinstance(part, str) for part in payload):
        return False
    return _raw_install_from_words(list(payload), depth + 1, variables)


def _python_launcher_payload_is_raw(
    call: ast.Call,
    target: str,
    depth: int,
    variables: dict[str, str | None] | None,
    static_values: dict[str, object],
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
    caller_name: str | None,
) -> bool:
    """Inspect a process payload with scope-aware executable values."""
    if not call.args:
        return True
    if target == "asyncio.create_subprocess_exec":
        payload = [
            _python_static_expression_value(
                argument, static_values, module_aliases, imported_names
            )
            for argument in call.args
        ]
        return _python_subprocess_argv_is_raw(
            call, payload, depth, variables, caller_name
        )
    payload = _python_static_expression_value(
        call.args[0], static_values, module_aliases, imported_names
    )
    if _python_shell_mode(target, call):
        return _python_payload_is_raw(payload, True, depth, variables)
    if payload is None:
        return True
    if isinstance(payload, (list, tuple)):
        return _python_subprocess_argv_is_raw(
            call, payload, depth, variables, caller_name
        )
    return _python_payload_is_raw(payload, False, depth, variables)


def _literal_python_argv(arguments: list[ast.expr]) -> list[str] | None:
    """Return a fully literal string argv, or None when any part is opaque."""
    try:
        argv = [ast.literal_eval(argument) for argument in arguments]
    except (ValueError, TypeError):
        return None
    if not argv or not all(isinstance(part, str) for part in argv):
        return None
    argv[0] = Path(argv[0]).name
    return argv


def _python_shell_mode(target: str, call: ast.Call) -> bool:
    """Whether a Python launcher interprets its payload as shell source."""
    return target in _PYTHON_SHELL_LAUNCHERS or any(
        _python_shell_keyword_is_true(keyword) for keyword in call.keywords
    )


def _python_shell_keyword_is_true(keyword: ast.keyword) -> bool:
    return (
        keyword.arg == "shell"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
    )


def _python_payload_is_raw(
    payload: object,
    shell_mode: bool,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Check a literal shell string or argv using its launch mode."""
    if isinstance(payload, str):
        if shell_mode:
            return _raw_install_in_script(payload, depth + 1, variables)
        # subprocess with shell=False treats a string as one executable name;
        # it does not split it into a command and arguments on POSIX.
        return False
    if not isinstance(payload, (list, tuple)):
        return True
    if not all(isinstance(part, str) for part in payload):
        return True
    argv = list(payload)
    if not argv:
        return True
    argv[0] = Path(argv[0]).name
    if shell_mode:
        return _raw_install_in_script(shlex.join(argv), depth + 1, variables)
    return _raw_install_from_words(argv, depth + 1, variables)


def _python_from_import_bindings(
    node: ast.ImportFrom, imported_names: dict[str, str]
) -> None:
    if not node.module:
        return
    for alias in node.names:
        if alias.name == "*":
            imported_names |= {
                name: f"{node.module}.{name}"
                for name in _PYTHON_STAR_IMPORTS.get(node.module, ())
            }
            continue
        bound_name = alias.asname or alias.name
        imported_names[bound_name] = f"{node.module}.{alias.name}"


def _python_import_bindings(
    tree: ast.AST,
) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve import aliases used to recognize process-launching calls."""
    module_aliases: dict[str, str] = {}
    imported_names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".", 1)[0]
                module_aliases[bound_name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            _python_from_import_bindings(node, imported_names)
    return module_aliases, imported_names


def _python_dynamic_call_target(
    function: ast.expr,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    """Resolve a call whose target is built from a dynamic call.

    ``__import__('os').system``, ``getattr(__import__('os'),'system')`` and
    ``importlib.import_module('os').system`` are attribute chains rooted in a
    call rather than a name, which ``_python_call_name`` cannot resolve.  The
    module name is a literal in every such form, so the qualified target is
    recovered here; an attribute chain rooted in anything else returns None
    so the caller can fail closed.
    """
    attributes: list[str] = []
    current: ast.expr = function
    while isinstance(current, ast.Attribute):
        attributes.append(current.attr)
        current = current.value
    if isinstance(current, ast.Call):
        module = _python_dynamic_import_module(
            current, module_aliases, imported_names
        )
        if module is None:
            return None
        if not attributes:
            # A bare call result used as the target: recover the attribute a
            # literal getattr names, e.g. getattr(mod, 'system')(...) ->
            # mod.system.
            named = _python_getattr_literal_name(
                current, module_aliases, imported_names
            )
            return f"{module}.{named}" if named is not None else module
        return ".".join((module, *reversed(attributes)))
    return None


def _python_getattr_literal_name(
    call: ast.Call,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    """Return the literal attribute name a top-level ``getattr`` selects."""
    target = _python_call_name(call.func, module_aliases, imported_names)
    if target != "getattr" or len(call.args) != 2:
        return None
    name = call.args[1]
    if isinstance(name, ast.Constant) and isinstance(name.value, str):
        return name.value
    return None


def _python_getattr_import_module(
    target: str | None,
    call: ast.Call,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    """Resolve ``getattr(<dynamic-import>, '<name>')`` to its module.

    Returns None when the call is not a literal two-argument getattr over a
    dynamic import, so the caller treats it as unresolvable.
    """
    if target != "getattr" or len(call.args) != 2:
        return None
    inner, name = call.args
    if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
        return None
    if not isinstance(inner, ast.Call):
        return None
    return _python_dynamic_import_module(inner, module_aliases, imported_names)


def _python_dynamic_import_module(
    call: ast.Call,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    """Resolve the module a dynamic-import call names, or None.

    ``importlib.import_module`` returns the named module, dotted names
    included.  ``__import__`` follows the import statement's semantics: a
    bare dotted name returns the TOP-LEVEL package (``__import__('os.path')``
    binds ``os``), while a non-empty ``fromlist`` returns the submodule
    named.  Modeling this matters: a resolver that answered
    ``os.path.system`` for ``__import__('os.path').system`` would miss that
    the callable really is ``os.system``.
    """
    target = _python_call_name(call.func, module_aliases, imported_names)
    if target not in {"__import__", _IMPORTLIB_MODULE_FUNCTION}:
        return _python_getattr_import_module(
            target, call, module_aliases, imported_names
        )
    if not call.args:
        return None
    argument = call.args[0]
    if not (isinstance(argument, ast.Constant) and isinstance(argument.value, str)):
        return None
    module_name = argument.value
    if module_name.startswith("."):
        # A relative import resolves against its package (a runtime concern);
        # the module it names cannot be determined from this payload, so the
        # caller fails closed instead of matching a launcher on a name that
        # could resolve anywhere.
        return None
    if target == "__import__" and _python_import_level_is_relative(call):
        # ``__import__`` with a nonzero ``level`` resolves the (undotted)
        # name against the calling package, so the real module is a runtime
        # concern again: a locally imported helper could hide a launcher.
        return None
    if target == _IMPORTLIB_MODULE_FUNCTION:
        return module_name
    if _python_import_fromlist_is_nonempty(call):
        return module_name
    return module_name.split(".", 1)[0]


def _python_import_positional_level(
    call: ast.Call,
) -> tuple[bool, ast.expr | None]:
    """Resolve ``__import__``'s positional ``level`` slot.

    Returns ``(undetermined, node)``: ``undetermined`` is True when a
    starred positional could shift or supply the slot without a known
    value, and ``node`` is the expression in the fifth argument position
    when the position is statically known.
    """
    positions: list[ast.expr] = []
    for node in call.args:
        if isinstance(node, ast.Starred):
            inner = node.value
            if isinstance(inner, (ast.List, ast.Tuple)):
                positions.extend(inner.elts)
                continue
            # An unexpandable ``*args`` can supply ``level`` from any
            # position; the slot cannot be determined.
            return True, None
        positions.append(node)
        if len(positions) > 5:
            break
    return (False, positions[4]) if len(positions) >= 5 else (False, None)


def _python_import_level_is_relative(call: ast.Call) -> bool:
    """Whether an ``__import__`` call passes a nonzero or unknown ``level``.

    The level parameter (positional index 4, the ``level`` keyword, a
    starred positional, or a ``**`` mapping) makes the module name
    relative to the calling package, so only a literal 0 keeps the
    absolute-import model this resolver applies.  A non-literal level -
    and any expansion that could supply one - is treated as relative for
    the same fail-closed reason.
    """
    undetermined, level_node = _python_import_positional_level(call)
    if undetermined:
        return True
    for keyword in call.keywords:
        if keyword.arg is None:
            # ``**mapping`` could supply ``level``; fail closed.
            return True
        if keyword.arg == "level":
            level_node = keyword.value
    if level_node is None:
        return False
    try:
        return ast.literal_eval(level_node) != 0
    except (ValueError, TypeError):
        return True


def _python_import_fromlist_is_nonempty(call: ast.Call) -> bool:
    """Whether an ``__import__`` call passes a non-empty literal fromlist.

    Positional index 3 and the ``fromlist`` keyword both name the parameter;
    only a literal non-empty list/tuple proves the submodule is returned.
    Anything else stays conservative for the top-level-package answer, which
    is what a bare call returns.
    """
    fromlist_node: ast.expr | None = None
    if len(call.args) >= 4:
        fromlist_node = call.args[3]
    for keyword in call.keywords:
        if keyword.arg == "fromlist":
            fromlist_node = keyword.value
    if fromlist_node is None:
        return False
    if isinstance(fromlist_node, (ast.List, ast.Tuple)):
        return len(fromlist_node.elts) > 0
    return False


def _python_call_name_or_dynamic(
    function: ast.expr,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    """Resolve a callable to its qualified name, following literal imports."""
    resolved = _python_call_name(function, module_aliases, imported_names)
    if resolved is not None:
        return resolved
    return _python_dynamic_call_target(function, module_aliases, imported_names)


def _python_eval_call_is_raw(
    call: ast.Call,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    if not call.args:
        return True
    try:
        source = ast.literal_eval(call.args[0])
    except (ValueError, TypeError):
        return True
    return not isinstance(source, str) or _python_inline_raw_install(
        source, depth + 1, variables
    )


def _python_call_is_raw(
    call: ast.Call,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
    depth: int,
    variables: dict[str, str | None] | None,
    static_values: dict[str, object],
    caller_name: str | None,
) -> bool:
    target = _python_call_name(call.func, module_aliases, imported_names)
    if target is None and _python_function_uses_dynamic_import(
        call.func, module_aliases, imported_names
    ):
        # The callable is rooted in a dynamic import (`__import__('os')`,
        # `importlib.import_module('os')`, or a literal `getattr` over one).
        # A literal module name resolves to its qualified target; when the
        # name is not statically known the call could be any launcher, so
        # fail closed rather than skip a potentially raw install.  Ordinary
        # call roots (`Path(x).read_text()`) are unaffected.
        target = _python_call_name_or_dynamic(
            call.func, module_aliases, imported_names
        )
        if target is None:
            return True
    if target in {"exec", "eval"}:
        return _python_eval_call_is_raw(call, depth, variables)
    if target in _PYTHON_SHELL_LAUNCHERS | _PYTHON_ARGV_LAUNCHERS:
        return _python_launcher_payload_is_raw(
            call,
            target,
            depth,
            variables,
            static_values,
            module_aliases,
            imported_names,
            caller_name,
        )
    return target is not None and re.fullmatch(r"os\.(?:exec|spawn).*", target) is not None


def _python_function_uses_dynamic_import(
    function: ast.expr,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> bool:
    """Whether a callable expression is rooted in an unresolved import call.

    Import-shaped roots (``__import__``, ``importlib.import_module``, and a
    literal ``getattr``) trigger the fail-closed path: those can produce any
    module, so an unknown module name hides a potential launcher.  A
    ``getattr`` whose inner expression is not a dynamic import also reaches
    this branch and fails closed; that is the safe direction (a spurious
    rejection, never a missed raw install).  Call roots that are neither an
    import nor a ``getattr`` keep their previous treatment.
    """
    current: ast.expr = function
    while isinstance(current, ast.Attribute):
        current = current.value
    if not isinstance(current, ast.Call):
        return False
    target = _python_call_name(current.func, module_aliases, imported_names)
    return target in {
        "__import__", _IMPORTLIB_MODULE_FUNCTION, "getattr"
    }


def _python_command_wrapper_spec(
    wrapper: str,
) -> tuple[int, set[str]] | None:
    """Return the payload index and executable allowlist for a wrapper."""
    wrappers = {
        "_run_toolchain_version_command": (0, {"cargo", "rustc"}),
        "_run_helm": (2, {"helm", "kubectl"}),
    }
    return wrappers.get(wrapper)


def _python_wrapper_call_executable(
    call: ast.Call,
    argument_index: int,
    scope: int | None,
    values_by_scope: dict[int | None, dict[str, object]],
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
) -> str | None:
    """Resolve the first executable from a wrapped command-list argument."""
    if len(call.args) <= argument_index:
        return None
    payload = call.args[argument_index]
    if not isinstance(payload, (ast.List, ast.Tuple)) or not payload.elts:
        return None
    first = payload.elts[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    values = values_by_scope.get(scope, values_by_scope[None])
    value = _python_static_expression_value(
        first, values, module_aliases, imported_names
    )
    if isinstance(value, str):
        return value
    return first.id if isinstance(first, ast.Name) else None


def _python_command_wrapper_callers_are_safe(
    tree: ast.AST,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
    node_scopes: dict[int, int | None],
    values_by_scope: dict[int | None, dict[str, object]],
    wrapper: str,
) -> bool:
    """Verify every caller passes a statically approved executable."""
    spec = _python_command_wrapper_spec(wrapper)
    if spec is None:
        return False
    argument_index, allowed = spec
    if call_sites := [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _python_call_name(node.func, module_aliases, imported_names)
        == wrapper
    ]:
        return all(
            (executable := _python_wrapper_call_executable(
                call,
                argument_index,
                node_scopes.get(id(call)),
                values_by_scope,
                module_aliases,
                imported_names,
            )) is not None
            and Path(executable).name in allowed
            for call in call_sites
        )
    else:
        return False


def _python_forwarded_argv_name(node: ast.expr) -> str | None:
    """Name a command vector forwarded unchanged into a runner wrapper."""
    if isinstance(node, ast.Name):
        return node.id
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "list"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
    ):
        return node.args[0].id
    return None


def _python_identity_spawn_call_is_safe(
    call: ast.Call,
    node_scopes: dict[int, int | None],
    scope_names: dict[int, str],
) -> bool:
    scope = node_scopes.get(id(call))
    return (
        scope is not None
        and scope_names.get(scope) == "_run_toolchain_version_command"
        and bool(call.args)
        and _python_forwarded_argv_name(call.args[0]) == "command"
    )


def _python_identity_spawn_callers_are_safe(
    tree: ast.AST,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
    node_scopes: dict[int, int | None],
    scope_names: dict[int, str],
) -> bool:
    """Require the Popen helper to forward only the checked version argv."""
    call_sites = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _python_call_name(node.func, module_aliases, imported_names)
        == "_start_toolchain_identity_process"
    ]
    return bool(call_sites) and all(
        _python_identity_spawn_call_is_safe(call, node_scopes, scope_names)
        for call in call_sites
    )


def _python_is_safe_forwarded_wrapper_call(
    call: ast.Call,
    function_name: str | None,
    target: str | None,
    safe_wrappers: dict[str, bool],
) -> bool:
    """Whether a process call forwards an approved command-vector argument."""
    if function_name not in safe_wrappers or not safe_wrappers[function_name]:
        return False
    expected_target = (
        "subprocess.Popen"
        if function_name == "_start_toolchain_identity_process"
        else "subprocess.run"
    )
    if target != expected_target or not call.args:
        return False
    parameter = {
        "_run_toolchain_version_command": "command",
        "_run_helm": "args",
        "_start_toolchain_identity_process": "command",
    }.get(function_name)
    return parameter is not None and (
        _python_forwarded_argv_name(call.args[0]) == parameter
    )


def _python_calls_include_raw_install(
    tree: ast.AST,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
    depth: int,
    variables: dict[str, str | None] | None,
    static_values: dict[int | None, dict[str, object]],
    node_scopes: dict[int, int | None],
    scope_names: dict[int, str],
) -> bool:
    """Inspect launcher calls using values local to their function scopes."""
    safe_toolchain_wrapper = _python_command_wrapper_callers_are_safe(
        tree, module_aliases, imported_names, node_scopes, static_values,
        "_run_toolchain_version_command",
    )
    safe_wrappers = {
        "_run_toolchain_version_command": safe_toolchain_wrapper,
        "_run_helm": _python_command_wrapper_callers_are_safe(
            tree, module_aliases, imported_names, node_scopes, static_values,
            "_run_helm",
        ),
        "_start_toolchain_identity_process": (
            safe_toolchain_wrapper
            and _python_identity_spawn_callers_are_safe(
                tree, module_aliases, imported_names, node_scopes, scope_names
            )
        ),
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        scope = node_scopes.get(id(node))
        function_name = scope_names.get(scope) if scope is not None else None
        target = _python_call_name(node.func, module_aliases, imported_names)
        if _python_is_safe_forwarded_wrapper_call(
            node, function_name, target, safe_wrappers
        ):
            continue
        values = static_values.get(scope, static_values[None])
        if _python_call_is_raw(
            node,
            module_aliases,
            imported_names,
            depth,
            variables,
            values,
            function_name,
        ):
            return True
    return False


def _python_relative_import_name(
    node: ast.ImportFrom, source_path: Path | None, root: Path
) -> str | None:
    """Resolve one relative import; None means its package is unknown."""
    if source_path is None:
        return None
    try:
        parts = source_path.relative_to(root).with_suffix("").parts
    except ValueError:
        return None
    package_parts = parts[:-1]
    remove = node.level - 1
    if node.level > len(package_parts):
        return None
    package = package_parts[:len(package_parts) - remove]
    module_parts = node.module.split(".") if node.module else []
    return ".".join((*package, *module_parts))


def _python_import_node_names(
    node: ast.AST, source_path: Path | None, root: Path
) -> set[str] | None:
    """Return statically named modules for one import node."""
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if not isinstance(node, ast.ImportFrom):
        return set()
    module_name = node.module or ""
    if node.level:
        module_name = _python_relative_import_name(node, source_path, root)
        if module_name is None:
            return None
    if not module_name:
        return set()
    imported = {
        f"{module_name}.{alias.name}"
        for alias in node.names
        if alias.name != "*"
    }
    imported.add(module_name)
    return imported


def _python_imported_module_names(
    tree: ast.AST, source_path: Path | None, root: Path
) -> set[str] | None:
    """Collect statically named modules, resolving package-relative imports."""
    modules: set[str] = set()
    for node in ast.walk(tree):
        imported = _python_import_node_names(node, source_path, root)
        if imported is None:
            return None
        modules.update(imported)
    return modules


def _python_local_module_sources(
    module_name: str, root: Path, execute_as_module: bool = False
) -> tuple[list[Path], bool]:
    """Resolve repository-local module files without importing their code."""
    parts = module_name.split(".")
    if not parts or not all(part.isidentifier() for part in parts):
        return [], True
    candidates = [
        root.joinpath(*parts[:index], "__init__.py")
        for index in range(1, len(parts))
    ]
    module_path = root.joinpath(*parts)
    candidates.extend((module_path.with_suffix(".py"), module_path / "__init__.py"))
    if execute_as_module:
        candidates.append(module_path / "__main__.py")

    sources: list[Path] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if not resolved.is_file() or resolved.stat().st_size > 1_048_576:
                return [], True
        except (OSError, ValueError):
            return [], True
        if resolved not in sources:
            sources.append(resolved)
    return sources, False


def _python_imports_include_raw_install(
    tree: ast.AST,
    source_path: Path | None,
    depth: int,
    variables: dict[str, str | None] | None,
    visited: set[Path] | None,
) -> bool:
    """Scan statically imported project modules under the repository root."""
    root = PROJECT_ROOT.resolve()
    if source_path is None:
        source_path = root / "__inline__.py"
    modules = _python_imported_module_names(tree, source_path, root)
    if modules is None:
        return True
    seen = visited if visited is not None else set()
    for module_name in sorted(modules):
        sources, invalid = _python_local_module_sources(module_name, root)
        if invalid:
            return True
        for source in sources:
            if _python_script_file_is_raw(
                str(source.relative_to(root)), depth, variables, seen
            ):
                return True
    return False


def _shell_template_runs_raw_install(shell: object) -> bool:
    """Whether a custom shell template's own commands install a raw toolchain."""
    return any(
        _raw_install_in_run_script(payload)
        for payload in _shell_template_executable_payloads(shell)
    )


def _python_inline_raw_install(
    payload: str,
    depth: int,
    variables: dict[str, str | None] | None,
    source_path: Path | None = None,
    visited: set[Path] | None = None,
) -> bool:
    """Find raw installs launched by literal Python source or scripts."""
    if depth > 12:
        return True
    try:
        tree = ast.parse(payload)
    except SyntaxError:
        return _mentions_raw_install(payload)

    module_aliases, imported_names = _python_import_bindings(tree)
    node_scopes, scope_names = _python_scope_maps(tree)
    global_values = _python_static_assignment_values(
        tree, module_aliases, imported_names, node_scopes, None
    )
    static_values: dict[int | None, dict[str, object]] = {None: global_values}
    for scope in set(node_scopes.values()) - {None}:
        static_values[scope] = _python_static_assignment_values(
            tree,
            module_aliases,
            imported_names,
            node_scopes,
            scope,
            global_values,
        )
    if _python_calls_include_raw_install(
        tree,
        module_aliases,
        imported_names,
        depth,
        variables,
        static_values,
        node_scopes,
        scope_names,
    ):
        return True
    return _python_imports_include_raw_install(
        tree, source_path, depth, variables, visited
    )


def _python_short_flag_step(
    flag: str, short_options: str, words: list[str], index: int, offset: int
) -> tuple[str | None, str | int | None, int] | None:
    """Classify a Python flag that selects source or consumes a value."""
    if flag in {"h", "V"}:
        return "terminal", None, 1
    if flag == "c":
        if attached_source := short_options[offset + 1 :]:
            return "inline", attached_source, 1
        source_index = index + 1 if index + 1 < len(words) else None
        return "inline", source_index, 1
    if flag == "m":
        source_index = index + 1 if index + 1 < len(words) else None
        return "module", source_index, 1
    if flag in {"W", "X"}:
        has_attached_value = offset + 1 < len(short_options)
        return None, None, 1 if has_attached_value else 2
    return None


def _python_short_option_step(
    option: str, words: list[str], index: int
) -> tuple[str | None, str | int | None, int]:
    """Inspect one grouped short-option word for source or a value option."""
    short_options = option[1:]
    for offset, flag in enumerate(short_options):
        step = _python_short_flag_step(flag, short_options, words, index, offset)
        if step is not None:
            return step
    return None, None, 1


def _python_long_option_step(
    option: str, words: list[str], index: int
) -> tuple[str | None, str | int | None, int]:
    """Inspect one long option and report its argument count or mode."""
    if option in {"--help", "--version"} or option.startswith("--help-"):
        return "terminal", None, 1
    if option == "--":
        mode = "script" if index + 1 < len(words) else "interactive"
        return mode, None, 0
    if option == "--check-hash-based-pycs":
        return None, None, 2
    return None, None, 1


def _python_command_option(
    words: list[str], index: int
) -> tuple[str | None, str | int | None, int]:
    """Inspect one Python argv word and return its mode or advance width."""
    option = words[index]
    if option == "-":
        return "stdin", index, 0
    if not option.startswith("-"):
        return "script", index, 0
    if option.startswith("--"):
        return _python_long_option_step(option, words, index)
    return _python_short_option_step(option, words, index)


def _python_command_source(
    words: list[str],
) -> tuple[str, str | int | None]:
    """Classify Python arguments and locate inline source or stdin input."""
    index = 1
    while index < len(words):
        if _is_shell_heredoc_redirect(words[index]):
            index += 1
            continue
        mode, source, consumed = _python_command_option(words, index)
        if mode is not None:
            return mode, source
        index += consumed
    return "interactive", None


def _python_stdin_source_is_raw(
    words: list[str], index: int | None
) -> bool:
    """Treat unmodeled stdin source as unsafe unless a static heredoc is used."""
    source_start = index + 1 if index is not None else 1
    has_heredoc = any(
        _is_shell_heredoc_redirect(word) for word in words[source_start:]
    )
    return not has_heredoc


def _python_script_path(words: list[str], source_index: str | int) -> str | None:
    """Return the selected Python script path from parsed argv."""
    if isinstance(source_index, str):
        return source_index
    return words[source_index] if source_index < len(words) else None


_IMPORTLIB_MODULE_FUNCTION = "importlib.import_module"

_RAW_TOOLCHAIN_INSTALL_MARKER_RE = re.compile(
    r"\brustup(?:-init)?\b|\btoolchain\s+install\b", re.IGNORECASE
)

# Redirection and heredoc syntax tokens that appear as words of a command
# segment but name no script operand (for example `2>&1`, `>file`, `<<PY`).
_SEGMENT_SYNTAX_TOKEN_RE = re.compile(
    r"(?:\d*>>?|<&?\d*|<<-?|&>>?)"
)


def _shell_script_mentions_toolchain_install(content: str) -> bool:
    """Select shell files whose contents can affect Rust toolchain install."""
    return _RAW_TOOLCHAIN_INSTALL_MARKER_RE.search(content) is not None


def _shell_script_invokes_interpreter(content: str) -> bool:
    """Whether a script can hand control to another script.

    Used for scripts that do not themselves mention the install markers: the
    conservative whole-file scan would false-positive on legitimate
    constructs it cannot resolve (a local array expansion such as
    ``"${build_cmd[@]}"`` fails closed), so such files are only followed
    through the scripts they invoke.  A file that neither mentions the
    markers nor invokes an interpreter cannot install a toolchain itself.
    """
    return any(
        head is not None
        for head in (
            _invocation_head(segment)
            for segment, _separator in _followable_command_segments(content)
        )
        if head is not None
    )


def _followable_command_segments(content: str):
    """Yield parseable command segments of a shell file's own body.

    Command substitutions are masked before segmentation, so fragments of a
    ``$(...)`` body appear as unparseable remainders; those are skipped here
    because the substitutions themselves are scanned separately elsewhere.
    """
    stripped = _join_continuations(_strip_heredocs(_strip_shell_comments(content)))
    for segment, separator in _command_segments_with_separators(stripped):
        if _parse_segment_words(segment) is None:
            # A masked substitution or an unresolved quote leaves a fragment
            # that cannot name a command; the substitution scan covers the
            # body it came from.
            continue
        yield segment, separator


def _parse_segment_words(segment: str) -> list[str] | None:
    """Parse one command segment, or None when it is not resolvable."""
    try:
        return shlex.split(segment.strip(), posix=True)
    except ValueError:
        return None


def _invocation_head(segment: str) -> str | None:
    """Return the interpreter basename a segment invokes, if any.

    Command wrappers are unwrapped first (``exec bash x.sh``, ``nohup
    bash x.sh``, ``command bash x.sh``), reusing the same bounded unwrapper
    the marker-present scan uses, so a wrapped shell invocation is followed
    instead of skipped.
    """
    words = _parse_segment_words(segment)
    if not words:
        return None
    index = _skip_env_assignments(words, 0)
    if (
        index + 1 < len(words)
        and _shell_word_basename(words[index]) == "retry"
        and words[index + 1].isdigit()
    ):
        index += 2
    index = _skip_bare_separators(words, index)
    index, refused = _unwrap_stacked_wrappers(words, index)
    if refused or index >= len(words):
        return None
    command = _resolve_heredoc_word(words[index])[0]
    if command in (".", "source"):
        # The dot-source spellings name no file of their own; Path(".").name
        # is empty, so the recognized head comes from the command word.
        return command
    head = Path(command).name
    if head in ("bash", "sh", "zsh", "dash"):
        return head
    return head if head == "python" or _PYTHON_COMMAND.fullmatch(head) else None


def _followed_script_is_raw(
    operand: str,
    head: str,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Scan one resolved operand of a shell or Python invocation.

    Only operands that resolve to an existing repository-local file are
    followed; an absolute, out-of-root, or not-yet-generated operand names
    something this scan cannot read, and (per this path's contract) an
    unresolvable operand is not evidence of a raw install.  The
    marker-present path keeps its own fail-closed treatment; this lighter
    path deliberately does not fail closed, because the file it examines
    never mentions the install markers.
    """
    root = PROJECT_ROOT.resolve()
    try:
        resolved = (root / operand).resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file():
            return False
    except (OSError, ValueError):
        return False
    if head in {"bash", "sh", "zsh", "dash", "source", "."}:
        return _raw_install_from_shell_script_file(
            operand, depth + 1, variables
        ) or _python_script_file_is_raw(operand, depth + 1, variables)
    return _python_script_file_is_raw(operand, depth + 1, variables)


def _followable_operand(operand: str) -> bool:
    """Whether an operand word can name a script this scan should follow."""
    if operand.startswith("-") or "$" in operand or "`" in operand:
        return False
    # Redirection and heredoc tokens are syntax, not script operands:
    # `python3 "$file" 2>&1 <<PY` must not scan `2>&1` or `<<PY`.
    return _SEGMENT_SYNTAX_TOKEN_RE.match(operand) is None


def _raw_install_from_inline_script(
    inline: str, depth: int, variables: dict[str, str | None] | None
) -> bool:
    """Scan a ``-c`` inline payload handed to a shell in a followed file.

    ``bash -c "echo build && make test"`` carries an executable string, not
    a filename; the marker-present path already routes such payloads to the
    script scan, and this path must do the same instead of treating the
    string as a path (which would fail closed on a harmless literal).
    """
    return _raw_install_in_script(inline, depth + 1, variables)


def _module_flag_operand_is_followable(words: list[str], index: int) -> bool:
    """Whether a ``-m`` operand names a script this scan should follow.

    ``python3 -m runpy <script>`` runs a script file, so its target is
    followed like a direct operand.  Any other module name is not a file
    and stops the scan instead.
    """
    return index + 1 < len(words) and words[index + 1] == "runpy"


def _single_operand_decision(
    words: list[str],
    index: int,
    operand: str,
    head: str,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool | None:
    """Decide one operand: True raw, False keep scanning, None stop.

    ``-c`` hands the next word to the inline-script scan; ``-m`` follows
    only a runpy target and otherwise stops; any other operand is followed
    when it can name a script.
    """
    if operand == "-c" and index + 1 < len(words):
        return _raw_install_from_inline_script(
            words[index + 1], depth, variables
        )
    if operand == "-m":
        return False if _module_flag_operand_is_followable(words, index) else None
    if not _followable_operand(operand):
        return False
    return _followed_script_is_raw(operand, head, depth, variables)


def _invocation_operands_are_raw(
    words: list[str],
    head: str,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Whether any operand of one followed invocation is a raw install."""
    for index, operand in enumerate(words[1:], start=1):
        decision = _single_operand_decision(
            words, index, operand, head, depth, variables
        )
        if decision is True:
            return True
        if decision is None:
            break
    return False


def _raw_install_from_invoked_scripts(
    content: str,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Follow the scripts a marker-less shell file invokes.

    The file's own body is deliberately not scanned as a whole (its
    unresolved constructs would fail closed on legitimate code), so only the
    literal script operands of its shell and Python invocations are resolved
    and scanned.  A chain such as ``outer.sh`` -> ``bash inner.sh`` is
    therefore caught, while an operand this scan cannot resolve (a dynamic
    expansion, an absolute path, or a not-yet-generated file) is not
    treated as evidence: this file does not mention the install markers, so
    an unresolvable operand is not a reason to fail the gate.  Inline ``-c``
    payloads are executable strings and are scanned as scripts.
    """
    for segment, _separator in _followable_command_segments(content):
        words = _parse_segment_words(segment)
        if not words or len(words) < 2:
            continue
        head = _invocation_head(segment)
        if head is None:
            continue
        if _invocation_operands_are_raw(words, head, depth, variables):
            return True
    return False


def _is_template_placeholder_operand(script_path: str, root: Path) -> bool:
    """Whether an operand is the workflow template's placeholder token.

    The generated script is the step's run block, which the caller scans
    separately, so the placeholder adds nothing here.  A real file or
    command of that exact name keeps the ordinary fail-closed treatment.
    """
    return (
        script_path == _TEMPLATE_PLACEHOLDER_SENTINEL
        and not (root / script_path).exists()
        and shutil.which(script_path) is None
    )


def _raw_install_from_shell_script_file(
    script_path: str,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Scan a bounded repository-local shell script; reject unknown paths."""
    if depth > 12 or script_path.startswith("-"):
        return True
    root = PROJECT_ROOT.resolve()
    if _is_template_placeholder_operand(script_path, root):
        return False
    if (
        not Path(script_path).is_absolute()
        and len(Path(script_path).parts) == 1
        and _mentions_raw_install(script_path)
        and not (root / script_path).exists()
        and shutil.which(script_path) is None
    ):
        return False
    try:
        resolved = (root / script_path).resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file() or resolved.stat().st_size > 1_048_576:
            return True
        content = resolved.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return True
    if not _shell_script_mentions_toolchain_install(content):
        # No marker text: this file cannot install a toolchain itself, but it
        # may invoke one that does.  Follow its interpreter invocations so a
        # multi-hop chain cannot hide an install; when it invokes nothing,
        # the file is genuinely inert.
        if not _shell_script_invokes_interpreter(content):
            return False
        return _raw_install_from_invoked_scripts(content, depth, variables)
    return _raw_install_in_script(content, depth + 1, variables)


def _python_script_file_is_raw(
    script_path: str | None,
    depth: int,
    variables: dict[str, str | None] | None,
    visited: set[Path] | None = None,
) -> bool:
    """Analyze one bounded repo-local script; unknown paths fail closed."""
    if script_path is None or script_path.startswith("-"):
        return True
    script_path = _repo_rooted_script_operand(script_path) or script_path
    try:
        root = PROJECT_ROOT.resolve()
        if _is_template_placeholder_operand(script_path, root):
            return False
        resolved = (root / script_path).resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file() or resolved.stat().st_size > 1_048_576:
            return True
        content = resolved.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return True
    seen = visited if visited is not None else set()
    if resolved in seen:
        return False
    if len(seen) >= 128:
        return True
    seen.add(resolved)
    return _python_inline_raw_install(
        content, depth + 1, variables, resolved, seen
    )


def _python_module_file_is_raw(
    module_name: str | None,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Inspect repository-local Python module and package entry points."""
    if module_name is None:
        return True
    root = PROJECT_ROOT.resolve()
    sources, invalid = _python_local_module_sources(
        module_name, root, execute_as_module=True
    )
    if invalid:
        return True
    visited: set[Path] = set()
    return any(
        _python_script_file_is_raw(
            str(source.relative_to(root)), depth, variables, visited
        )
        for source in sources
    )


def _python_command_here_string(
    words: list[str],
    depth: int,
    variables: dict[str, str | None] | None,
) -> tuple[list[str], bool | None]:
    """Analyze Python stdin source while removing the shell redirect tokens."""
    here_string = _shell_here_string_parts(words[1:])
    if here_string is None:
        return words, None
    index, source, consumed = here_string
    args = words[1:]
    cleaned = words[:1] + args[:index] + args[index + consumed:]
    mode, _ = _python_command_source(cleaned)
    if mode not in {"stdin", "interactive"}:
        return cleaned, None
    if "$" in source or "`" in source:
        return cleaned, True
    return cleaned, _python_inline_raw_install(source, depth + 1, variables)


def _raw_install_from_python_module(
    words: list[str],
    source_index: int | str | None,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Analyze a Python module launch, including ``runpy`` script targets."""
    module_name = (
        _python_script_path(words, source_index)
        if source_index is not None
        else None
    )
    if module_name != "runpy":
        return _python_module_file_is_raw(module_name, depth, variables)
    target_index = (
        source_index + 1
        if isinstance(source_index, int)
        else len(words)
    )
    if target_index >= len(words) or words[target_index].startswith("-"):
        return True
    return _python_module_file_is_raw(
        words[target_index], depth, variables
    )


def _raw_install_from_python_command(
    words: list[str],
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Analyze inline and file-based Python sources for raw toolchain installs."""
    if not _PYTHON_COMMAND.fullmatch(Path(words[0]).name):
        return False
    words, here_string_issue = _python_command_here_string(
        words, depth, variables
    )
    if here_string_issue is not None:
        return here_string_issue
    mode, source_index = _python_command_source(words)
    if mode == "inline":
        if isinstance(source_index, str):
            source = source_index
        elif isinstance(source_index, int) and source_index < len(words):
            source = words[source_index]
        else:
            return True
        return _python_inline_raw_install(source, depth + 1, variables)
    if mode in {"stdin", "interactive"}:
        return _python_stdin_source_is_raw(words, source_index)
    if mode == "script":
        if source_index is None:
            return False
        script_path = _python_script_path(words, source_index)
        return _python_script_file_is_raw(script_path, depth, variables)
    if mode == "module":
        return _raw_install_from_python_module(
            words, source_index, depth, variables
        )
    return False


def _python_arguments_read_stdin_script(arguments: list[str]) -> bool:
    """Whether Python arguments select executable source from standard input."""
    command_words = [
        "python3",
        *(argument for argument in arguments
          if not _is_shell_heredoc_redirect(argument)),
    ]
    mode, _source = _python_command_source(command_words)
    return mode in {"stdin", "interactive"}


def _python_command_reads_stdin_script(line: str) -> bool:
    """Whether one shell command invokes Python with stdin as its source."""
    for segment in _command_segments(line):
        try:
            words = shlex.split(segment, posix=True)
        except ValueError:
            continue
        if not words:
            continue
        index, shell_payload = _wrapper_prefix_length(words)
        if shell_payload or index >= len(words):
            continue
        command = Path(_resolve_heredoc_word(words[index])[0]).name
        if not _PYTHON_COMMAND.fullmatch(command):
            continue
        return _python_arguments_read_stdin_script(words[index + 1:])
    return False


def _python_stdin_heredoc_bodies(script: str) -> tuple[list[str], bool]:
    """Return Python-source heredocs and whether an input delimiter is opaque."""
    bodies: list[str] = []
    lines = script.splitlines()
    index = 0
    quote: str | None = None
    while index < len(lines):
        line = lines[index]
        index += 1
        line, index = _join_command_line(lines, line, index, quote)
        quote, markers = _scan_line_for_heredocs(line, quote)
        reads_stdin = _python_command_reads_stdin_script(line)
        for delimiter, tab_stripped, dynamic in markers:
            if dynamic:
                if reads_stdin:
                    return bodies, True
                continue
            body, index = _read_heredoc_body(
                lines, index, delimiter, tab_stripped
            )
            if reads_stdin:
                bodies.append(body)
    return bodies, False


def _shell_script_operand(shell: str, arguments: list[str]) -> str | None:
    """Return a file operand, excluding ``-c`` and stdin source modes."""
    position = 0
    while position < len(arguments):
        if arguments[position] == "--":
            return arguments[position + 1] if position + 1 < len(arguments) else None
        decision, next_position = _shell_argument_step(
            shell, arguments, position
        )
        if decision is True:
            return None
        if decision is False:
            argument = _resolve_heredoc_word(arguments[position])[0]
            return None if argument.startswith(("-", "+")) else argument
        position = next_position
    return None


def _dequote_shell_argv(words: list[str]) -> str:
    """Render parsed argv without shell quote syntax for allowlist matching."""
    rendered = shlex.join(words)
    return re.sub(r"'([^']*)'", lambda match: match.group(1), rendered)


def _is_verified_installer_command(command: str) -> bool:
    """Match the approved installer after shell tokenization."""
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        return False
    return bool(words) and _VERIFIED_INSTALLER_RE.match(
        _dequote_shell_argv(words)
    ) is not None


def _raw_install_from_shell_input(
    shell: str,
    arguments: list[str],
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool | None:
    """Scan a static shell stdin body or file operand when one is selected."""
    if shell not in _SHELL_COMMANDS_THAT_READ_STDIN:
        return None
    if here_string := _shell_here_string_parts(arguments):
        if _shell_arguments_read_stdin_script(shell, arguments):
            return _raw_install_in_script(here_string[1], depth + 1, variables)
    if script_path := _shell_script_operand(shell, arguments):
        return _raw_install_from_shell_script_file(
            script_path, depth + 1, variables
        )
    return None


def _raw_install_from_stripped_shell_wrapper(
    shell: str,
    arguments: list[str],
    serialized: str,
    stripped: str,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Handle opaque stdin modes and recursively scan stripped wrapper text."""
    if stripped != serialized:
        return (
            False
            if _is_verified_installer_command(stripped)
            else _raw_install_in_script(stripped, depth + 1, variables)
        )
    if shell in _SHELL_COMMANDS_THAT_READ_STDIN and not any(
        _is_shell_heredoc_redirect(_resolve_heredoc_word(word)[0])
        for word in arguments
    ):
        return _shell_arguments_read_stdin_script(shell, arguments)
    return False


def _raw_install_from_shell_wrapper(
    words: list[str], depth: int, variables: dict[str, str | None] | None
) -> bool:
    """Follow a modeled shell/wrapper command's static payload."""
    wrappers = {"bash", "sh", "dash", "zsh", "nohup", "sudo", "retry"}
    shell = Path(words[0]).name
    if shell not in wrappers:
        return False
    arguments = words[1:]
    serialized = shlex.join(words)
    stripped = _strip_provision_wrappers(serialized)
    if _is_verified_installer_command(serialized) or _is_verified_installer_command(
        stripped
    ):
        return False
    stdin_result = _raw_install_from_shell_input(
        shell, arguments, depth, variables
    )
    if stdin_result is not None:
        return stdin_result
    return _raw_install_from_stripped_shell_wrapper(
        shell, arguments, serialized, stripped, depth, variables
    )


def _raw_install_from_wrapper(
    words: list[str], depth: int, variables: dict[str, str | None] | None = None
) -> bool:
    """Dispatch recognized wrappers to their bounded analyzers."""
    if not words:
        return False
    wrapper_handlers = {
        "exec": _raw_install_from_exec_wrapper,
        "eval": _raw_install_from_eval_wrapper,
        "env": _raw_install_from_env_wrapper,
        "command": _raw_install_from_command_wrapper,
    }
    handler = wrapper_handlers.get(words[0])
    if handler is not None:
        return handler(words, depth, variables)
    if words[0] in (".", "source"):
        # A dot-sourced script's body runs in this shell, so its literal
        # operand is followed like any other invoked script.  A missing
        # operand fails closed: the body cannot be inspected.
        if len(words) < 2:
            return True
        return _raw_install_from_shell_script_file(words[1], depth, variables)
    return _raw_install_from_shell_wrapper(words, depth, variables)


def _raw_install_depth_limit_exceeded(
    words: list[str], variables: dict[str, str | None] | None
) -> bool:
    """Fail closed when wrapper recursion exceeds its static analysis bound."""
    joined = " ".join(words)
    expanded = _expand_static_shell_variables(joined, variables)
    return _mentions_raw_install(expanded or joined) or (
        expanded is None and "$" in joined
    )


def _raw_install_from_assignment_prefix(
    words: list[str], depth: int, variables: dict[str, str | None] | None
) -> bool | None:
    """Follow command-scoped assignments without leaking them to later commands."""
    command_index = _skip_env_assignments(words, 0)
    if not command_index:
        return None
    local_variables = dict(variables or {})
    for word in words[:command_index]:
        assignment = _shell_assignment_parts(word)
        if assignment is not None:
            name, value = assignment
            local_variables[name] = _static_assignment_value(value)
    return _raw_install_from_words(
        words[command_index:], depth + 1, local_variables
    )


_REPO_ROOTED_SCRIPT_RE = re.compile(
    r"\$(?:\{(?:PROJECT_ROOT|REPO_ROOT)\}|(?:PROJECT_ROOT|REPO_ROOT))"
    r"/(?P<path>[A-Za-z0-9_./-]+\.(?:py|sh))\Z"
)


def _repo_rooted_script_operand(word: str) -> str | None:
    """Resolve the literal repository-root prefix used by tracked scripts."""
    match = _REPO_ROOTED_SCRIPT_RE.fullmatch(word)
    return match.group("path") if match is not None else None


def _raw_install_expand_command_word(
    words: list[str], depth: int, variables: dict[str, str | None] | None
) -> tuple[bool | None, list[str]]:
    """Resolve a command-position variable before ordinary command dispatch."""
    if "$" not in words[0]:
        return None, words
    expanded = _expand_static_shell_variables(words[0], variables)
    if expanded is None:
        executable = Path(words[0]).name
        allowed_path_suffixes = {"rustup", "rustc", "cargo", "rustfmt"}
        if executable not in allowed_path_suffixes:
            return True, words
        return None, [executable, *words[1:]]
    try:
        expanded_words = shlex.split(expanded, posix=True)
    except ValueError:
        return _mentions_raw_install(expanded), words
    if not expanded_words:
        return False, words
    return _raw_install_from_words(
        expanded_words + words[1:], depth + 1, variables
    ), words


_RUSTUP_GLOBAL_FLAGS = frozenset(
    {"-v", "-V", "-q", "--verbose", "--quiet", "--version"}
)
_COMMAND_SUBSTITUTION_MASK = "__shell_command_substitution__"


def _rustup_subcommand_word_is_unresolved(word: str) -> bool:
    """Whether a rustup operand word still carries an unresolved expansion.

    Quote removal runs before this check, so a surviving ``$`` or backtick
    means bash evaluates the word at run time, and the masked placeholder
    marks a ``$(...)`` the scanner lifted out of the line.
    """
    return (
        "$" in word
        or "`" in word
        or word == _COMMAND_SUBSTITUTION_MASK
    )


def _raw_rustup_command_installs_toolchain(words: list[str]) -> bool:
    """Whether a rustup argv invokes its toolchain install subcommand.

    A subcommand region that is not the literal ``toolchain install`` pair
    and still carries an unresolved expansion fails closed: the expansion
    could produce that pair at run time, so it must not pass as the benign
    subcommand (``show``, ``component add``, ``toolchain list``) it merely
    resembles.
    """
    if Path(words[0]).name != "rustup":
        return False
    index = 1
    while index < len(words):
        word = words[index]
        if word.startswith("+") and len(word) > 1:
            # ``+toolchain`` selects a toolchain and shifts the subcommand
            # to the next word; skipping it keeps ``+stable toolchain
            # install`` recognized.
            index += 1
            continue
        if word.startswith("-") and word in _RUSTUP_GLOBAL_FLAGS:
            index += 1
            continue
        break
    subcommand = words[index : index + 2]
    if subcommand == ["toolchain", "install"]:
        return True
    return any(_rustup_subcommand_word_is_unresolved(word) for word in subcommand)


def _rustup_segment_opens_dynamic_subcommand(
    segment: str, separators: list[str], index: int
) -> bool:
    """Whether a backtick substitution fills a rustup subcommand slot.

    The segment scanner splits a command substitution out of its command
    line, so ``rustup `echo toolchain` install nightly`` reaches the argv
    check as the bare word ``rustup`` plus a separate substitution body.
    When a rustup command stops before its literal subcommand pair and a
    backtick substitution follows, the subcommand text is runtime text and
    the scan fails closed. A double-quoted substitution leaves the opening
    quote stranded on this segment, so one dangling quote is dropped
    before tokenizing.
    """
    if index + 1 >= len(separators) or separators[index + 1] != "`":
        return False
    text = segment.rstrip()
    if text and text[-1] in "\"'":
        text = text[:-1]
    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        return False
    if not words or Path(words[0]).name != "rustup":
        return False
    operand = words[1:]
    while operand:
        if operand[0].startswith("+") and len(operand[0]) > 1:
            operand = operand[1:]
            continue
        if operand[0].startswith("-") and operand[0] in _RUSTUP_GLOBAL_FLAGS:
            operand = operand[1:]
            continue
        break
    return operand in [[], ["toolchain"]]


def _raw_install_from_command(
    words: list[str], depth: int, variables: dict[str, str | None] | None
) -> bool:
    """Check a parsed command word against inert, direct, and wrapper forms."""
    if words[0] in _INERT_SHELL_COMMANDS:
        return False
    if script_path := _repo_rooted_script_operand(words[0]):
        if script_path.endswith(".py"):
            return _python_script_file_is_raw(script_path, depth + 1, variables)
        return _raw_install_from_shell_script_file(
            script_path, depth + 1, variables
        )
    decision, words = _raw_install_expand_command_word(words, depth, variables)
    if decision is not None:
        return decision
    if _raw_rustup_command_installs_toolchain(words):
        return True
    return (
        _raw_install_from_python_command(words, depth, variables)
        or _raw_install_from_dispatcher(words, depth, variables)
        or _raw_install_from_wrapper(words, depth, variables)
    )


def _raw_install_from_shell_loop(
    words: list[str],
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Inspect a for/while/until body, failing closed on a missing do."""
    try:
        do_index = words.index("do")
    except ValueError:
        return _mentions_raw_install(" ".join(words))
    return _raw_install_from_words(words[do_index + 1:], depth + 1, variables)


def _raw_install_from_case_control(
    words: list[str],
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Inspect each case body after checking its structural delimiters."""
    try:
        body_start = words.index("in") + 1
        esac_index = words.index("esac", body_start)
    except ValueError:
        return _mentions_raw_install(" ".join(words))
    case_bodies: list[list[str]] = []
    current: list[str] = []
    for word in words[body_start:esac_index]:
        if word == ";;":
            if current:
                case_bodies.append(current)
                current = []
        else:
            current.append(word)
    if current:
        case_bodies.append(current)
    return any(
        _raw_install_from_words(body, depth + 1, variables)
        for body in case_bodies
    )


def _raw_install_from_control_flow(
    words: list[str],
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool | None:
    """Inspect supported shell control-flow forms at command position."""
    if words[0] in {"if", "then", "elif", "else", "!"}:
        return _raw_install_from_words(words[1:], depth + 1, variables)
    if words[0] in {"for", "while", "until"}:
        return _raw_install_from_shell_loop(words, depth, variables)
    if words[0] == "case":
        return _raw_install_from_case_control(words, depth, variables)
    return None


def _raw_install_from_words(
    words: list[str],
    depth: int = 0,
    variables: dict[str, str | None] | None = None,
) -> bool:
    """Follow a bounded set of command-position dispatchers to their target."""
    if not words:
        return False
    if depth > 12:
        return _raw_install_depth_limit_exceeded(words, variables)
    control_flow_result = _raw_install_from_control_flow(
        words, depth, variables
    )
    if control_flow_result is not None:
        return control_flow_result
    assignment_result = _raw_install_from_assignment_prefix(
        words, depth, variables
    )
    if assignment_result is not None:
        return assignment_result
    return _raw_install_from_command(words, depth, variables)


def _shell_command_is_double_quoted_variable(segment: str) -> bool:
    """Whether the command word is one double-quoted variable expansion."""
    words = segment.lstrip().split(maxsplit=1)
    return bool(
        words
        and re.fullmatch(
            r'"\$(?:\{[A-Za-z_]\w*\}|[A-Za-z_]\w*)"',
            words[0],
        )
    )


def _shell_array_assignment_is_data(segment: str) -> bool:
    """Whether a complete shell array assignment is non-executable data."""
    return re.fullmatch(
        r"\s*[A-Za-z_]\w*(?:\[[^\]\s]+\])?\+?=\(.*\)\s*",
        segment,
        re.S,
    ) is not None


def _raw_install_in_segmented_commands(
    segment: str,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool | None:
    """Scan commands split from one segment, preserving their separators."""
    pairs = _command_segments_with_separators(segment)
    if len(pairs) <= 1:
        return None
    return _raw_install_in_segments(
        [command for command, _separator in pairs],
        depth + 1,
        variables,
        [separator for _command, separator in pairs],
    )


def _deep_command_word_is_opaque(
    command: str, variables: dict[str, str | None] | None
) -> bool:
    if "$" not in command:
        return False
    resolved = _expand_static_shell_variables(command, variables)
    return resolved is None and Path(command).name not in {
        "rustup", "rustc", "cargo", "rustfmt"
    }


def _deep_launcher_has_inline_code(executable: str, args: list[str]) -> bool:
    if executable in {"bash", "sh", "dash", "zsh"}:
        return "-c" in args or any(
            _shell_option_has_flag(arg, "c") for arg in args
        ) or _shell_here_string_parts(args) is not None
    return _PYTHON_COMMAND.fullmatch(executable) is not None and "-c" in args


def _deep_segment_has_opaque_launcher(
    segment: str, variables: dict[str, str | None] | None
) -> bool:
    """Fail closed only on unresolved command code at the recursion limit."""
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return True
    command_index = _skip_env_assignments(words, 0)
    if command_index >= len(words):
        return False
    command = words[command_index]
    executable = Path(command).name
    return (
        executable == "eval"
        or _deep_command_word_is_opaque(command, variables)
        or _deep_launcher_has_inline_code(
            executable, words[command_index + 1 :]
        )
    )


def _raw_install_in_segment(
    segment: str,
    depth: int = 0,
    variables: dict[str, str | None] | None = None,
) -> bool:
    """Whether the segment runs a raw install, directly or through dispatchers."""
    if _shell_array_assignment_is_data(segment):
        return False
    if depth > 12:
        return _mentions_raw_install(segment) or _deep_segment_has_opaque_launcher(
            segment, variables
        )
    segmented_result = _raw_install_in_segmented_commands(
        segment, depth, variables
    )
    if segmented_result is not None:
        return segmented_result
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        first = _resolve_heredoc_word(segment.split(maxsplit=1)[0])[0] \
            if segment.split() else ""
        if first in _INERT_SHELL_COMMANDS:
            return False
        return _mentions_raw_install(segment)
    if _shell_command_is_double_quoted_variable(segment):
        expanded_command = _expand_static_shell_variables(words[0], variables)
        if expanded_command is None:
            return True
        return _raw_install_from_words(
            [expanded_command, *words[1:]], depth, variables
        )
    return _raw_install_from_words(words, depth, variables)


_SHELL_COMMANDS_THAT_READ_STDIN = frozenset({"bash", "sh", "dash", "zsh"})


def _is_shell_here_string_redirect(argument: str) -> bool:
    """Whether a shell token supplies one inline string to standard input."""
    token = _resolve_heredoc_word(argument)[0]
    return re.match(r"^(?:0)?<<<", token) is not None


def _shell_here_string_parts(
    arguments: list[str],
) -> tuple[int, str, int] | None:
    """Locate a here-string and its statically visible input text."""
    for index, argument in enumerate(arguments):
        token = _resolve_heredoc_word(argument)[0]
        match = re.match(r"^(?:0)?<<<(.*)$", token)
        if match is None:
            continue
        if inline := match[1]:
            return index, inline, 1
        if index + 1 < len(arguments):
            return index, _resolve_heredoc_word(arguments[index + 1])[0], 2
        return index, "", 1
    return None


def _is_shell_heredoc_redirect(argument: str) -> bool:
    """Whether one token is a heredoc redirection passed to a shell."""
    token = _resolve_heredoc_word(argument)[0]
    if _is_shell_here_string_redirect(token):
        return False
    return token.startswith("<<") or re.fullmatch(r"\d+<<.*", token) is not None


def _shell_option_has_flag(argument: str, flag: str) -> bool:
    """Whether a short shell option cluster includes one flag letter."""
    return (
        argument.startswith("-")
        and not argument.startswith("--")
        and flag in argument[1:]
    )


def _shell_argument_stdin_mode(argument: str) -> bool | None:
    """Return whether one option selects shell code from stdin."""
    if argument == "-c" or _shell_option_has_flag(argument, "c"):
        return False
    if argument == "-s" or _shell_option_has_flag(argument, "s"):
        return True
    return None


def _shell_bash_option_step(
    shell: str, argument: str, arguments: list[str], position: int
) -> "tuple[bool | None, int] | None":
    """Advance past a known bash value/flag option, if ``argument`` is one."""
    if shell != "bash":
        return None
    if argument in _SHELL_VALUE_OPTIONS:
        if position + 1 >= len(arguments):
            return (False, position)
        return (None, position + 2)
    return (None, position + 1) if argument in _SHELL_FLAG_OPTIONS else None


def _shell_short_option_step(
    argument: str, arguments: list[str], position: int
) -> "tuple[bool | None, int] | None":
    """Advance past a clustered short-option token like ``-abc``."""
    if not re.fullmatch(r"[+-][A-Za-z]*", argument):
        return None
    span = _short_shell_option_span(argument[1:], argument[0])
    if span is None:
        return (True, position)
    if position + span > len(arguments):
        return (False, position)
    return (None, position + span)


def _shell_argument_step(
    shell: str, arguments: list[str], position: int
) -> "tuple[bool | None, int]":
    """Classify one argument: return (decision-or-None, next-position).

    A non-``None`` first element is the final answer for the whole scan; a
    ``None`` first element means "keep scanning from the returned position".
    """
    argument = _resolve_heredoc_word(arguments[position])[0]
    if _is_shell_heredoc_redirect(argument):
        return (None, position + 1)
    if _shell_argument_stops_execution(shell, argument, arguments, position):
        return (False, position)
    if argument == "--":
        rest_all_redirects = all(
            _is_shell_heredoc_redirect(rest) for rest in arguments[position + 1:]
        )
        return (rest_all_redirects, position)
    stdin_mode = _shell_argument_stdin_mode(argument)
    if stdin_mode is not None:
        return (stdin_mode, position)
    for step in (
        _shell_bash_option_step(shell, argument, arguments, position),
        _shell_short_option_step(argument, arguments, position),
    ):
        if step is not None:
            return step
    if argument.startswith(("-", "+")) and argument != "-":
        return (True, position)
    if not argument.startswith("-"):
        return (False, position)
    return (None, position + 1)


def _shell_argument_stops_execution(
    shell: str, argument: str, arguments: list[str], position: int
) -> bool:
    """Whether one shell argument exits before running a script."""
    if argument in {"--help", "--version"}:
        return True
    if shell in _SHELL_COMMANDS_THAT_READ_STDIN and (
        argument == "--noexec" or _shell_option_has_flag(argument, "n")
    ):
        return True
    if shell != "bash":
        return False
    if argument in {"-D", "+D"}:
        return True
    return (
        argument == "-o"
        and position + 1 < len(arguments)
        and _resolve_heredoc_word(arguments[position + 1])[0] == "noexec"
    )


def _shell_arguments_read_stdin_script(
    shell: str, arguments: list[str]
) -> bool:
    """Whether modeled shell options select commands from standard input."""
    for index, argument in enumerate(arguments):
        if not _is_shell_here_string_redirect(argument):
            continue
        position = 0
        prefix = arguments[:index]
        while position < len(prefix):
            decision, next_position = _shell_argument_step(
                shell, prefix, position
            )
            if decision is not None:
                return decision
            position = next_position
        return True
    position = 0
    while position < len(arguments):
        decision, position = _shell_argument_step(shell, arguments, position)
        if decision is not None:
            return decision
    return True


def _shell_command_reads_heredoc_as_script(line: str) -> bool:
    """Whether a shell command on a heredoc line executes its stdin as a script."""
    for command_segment in _command_segments(line):
        try:
            command_words = shlex.split(command_segment, posix=True)
        except ValueError:
            continue
        index, command_payload = _wrapper_prefix_length(command_words)
        if command_payload or index >= len(command_words):
            continue
        command = _resolve_heredoc_word(command_words[index])[0]
        if command not in _SHELL_COMMANDS_THAT_READ_STDIN:
            continue
        arguments = [
            _resolve_heredoc_word(word)[0]
            for word in command_words[index + 1:]
        ]
        if _shell_arguments_read_stdin_script(command, arguments):
            return True
    return False


def _read_heredoc_body(
    lines: list[str], index: int, delimiter: str, tab_stripped: bool
) -> tuple[str, int]:
    """Consume one heredoc body and return its text and following line index."""
    body: list[str] = []
    while index < len(lines):
        body_line = lines[index]
        index += 1
        candidate = body_line.lstrip("\t") if tab_stripped else body_line
        if candidate == delimiter:
            break
        body.append(body_line)
    return "\n".join(body), index


def _unquoted_heredoc_bodies(script: str) -> list[str]:
    """Return bodies of heredocs whose delimiter is unquoted.

    An unquoted delimiter lets the shell expand the body, so command
    substitutions inside it EXECUTE even though the body itself is data
    (``cat <<EOF`` with ``$(rustup ...)``).  A quoted delimiter
    (``<<'EOF'``/``<<"EOF"``) suppresses all expansion, so those bodies
    stay literal.  Bodies are returned for substitution scanning only;
    they must never be treated as command lines.
    """
    bodies: list[str] = []
    lines = script.splitlines()
    index = 0
    quote: str | None = None
    while index < len(lines):
        line = lines[index]
        index += 1
        line, index = _join_command_line(lines, line, index, quote)
        quote, markers = _scan_line_for_heredocs(line, quote)
        for delimiter, tab_stripped, dynamic in markers:
            if dynamic:
                return bodies
            body, index = _read_heredoc_body(
                lines, index, delimiter, tab_stripped
            )
            if not _heredoc_delimiter_was_quoted(line, delimiter):
                bodies.append(body)
    return bodies


def _raw_install_in_expanded_heredocs(
    script: str, depth: int, variables: dict[str, str | None] | None
) -> bool:
    """Whether a command substitution in an unquoted heredoc is a raw install.

    Only unquoted delimiters expand; a quoted delimiter keeps the body
    literal, so its text is never analyzed here.
    """
    for body in _unquoted_heredoc_bodies(script):
        _masked, substitutions, opaque = _mask_command_substitutions(body)
        if opaque:
            return True
        if any(
            _raw_install_in_script(substitution, depth + 1, variables)
            for substitution in substitutions
            if not _shell_substitution_is_file_read(substitution)
        ):
            return True
    return False


def _heredoc_delimiter_was_quoted(line: str, delimiter: str) -> bool:
    """Whether the marker that opens ``delimiter`` quotes it (no expansion).

    Bash suppresses parameter/command substitution in a heredoc body when
    the delimiter word is quoted in any part; the marker text still shows
    that quoting, so it is read back from the line for the matching
    delimiter.
    """
    index = 0
    while index < len(line):
        marker = _heredoc_marker_at(line, index)
        if marker is None:
            index += 1
            continue
        word, _tab, _dynamic, end = marker
        if word == delimiter:
            raw = line[index:end]
            head = raw.split("<<", 1)[1].lstrip("-").lstrip(" \t")
            # Quoting or escaping ANY part of the delimiter word
            # suppresses body expansion (E'OF', "E"OF, E\OF), so the
            # literal check covers every quoted/escaped character, not
            # only a leading quote.
            return any(
                char in head for char in ("'", '"', chr(92))
            )
        index = end
    return False


def _shell_stdin_heredoc_bodies(script: str) -> list[str]:
    """Return heredoc bodies used as input scripts by a shell command."""
    bodies: list[str] = []
    lines = script.splitlines()
    index = 0
    quote: str | None = None
    while index < len(lines):
        line = lines[index]
        index += 1
        line, index = _join_command_line(lines, line, index, quote)
        quote, markers = _scan_line_for_heredocs(line, quote)
        reads_stdin = _shell_command_reads_heredoc_as_script(line)
        for delimiter, tab_stripped, dynamic in markers:
            if dynamic:
                if index < len(lines):
                    bodies.append("\n".join(lines[index:]))
                return bodies
            body, index = _read_heredoc_body(
                lines, index, delimiter, tab_stripped
            )
            if reads_stdin:
                bodies.append(body)
    return bodies


def _raw_install_in_run_script(script: str) -> bool:
    """Inspect executable shell commands, source substitutions and heredocs."""
    return _raw_install_in_script(script)


def _raw_toolchain_install_issue(workflow_content: str) -> str | None:
    """Reject raw ``rustup toolchain install`` anywhere in the workflow.

    Release workflows must provision toolchains through the verified
    installer, which validates the downloaded rustup-init checksum before
    execution.  Every run line is scanned -- including conditional steps
    and function bodies -- because a raw install must fail the gate
    wherever it could ever appear. Simple literal variable assignments are
    followed through ``eval``. Unresolved eval payloads fail closed unless
    every command is a provably inert builtin; runtime-generated programs
    never count as provisioning.
    """
    steps = _all_job_run_step_records(workflow_content)
    if steps is None:
        return (
            "release workflow YAML or a referenced workflow cannot be "
            "inspected, so raw Rust toolchain installs cannot be ruled out"
        )
    return next(
        (
            "release workflows must provision Rust toolchains "
            "through the verified installer; found a raw or unresolved "
            "toolchain-install command"
            for step in steps
            if _step_runs_raw_toolchain_install(step)
        ),
        None,
    )


def _step_runs_raw_toolchain_install(step: dict) -> bool:
    """Whether one run step can execute a raw or unresolved toolchain install.

    The run block is analyzed under the interpreter the step's shell
    selects.  A custom shell template can also carry its own executable
    commands (``bash -c '...' {0}``), which the run-block scan cannot see,
    and a template whose consumers span both interpreters runs the block
    under each, so the OTHER analysis is applied too and either can fail
    the step closed.
    """
    run = step["run"]
    shell = step.get("shell")
    if _workflow_shell_uses_python(shell):
        if _python_inline_raw_install(run, 0, None):
            return True
    elif _raw_install_in_run_script(run):
        return True
    if _shell_template_runs_raw_install(shell):
        return True
    if not _shell_template_conflicting_placeholder_consumers(shell):
        return False
    if _workflow_shell_uses_python(shell):
        return _raw_install_in_run_script(run)
    return _python_inline_raw_install(run, 0, None)


def _is_retry_call(segment: str) -> bool:
    """Whether the segment invokes the local ``retry N`` wrapper.

    Leading ``VAR=VAL`` assignments do not change which command runs, so
    they are stepped over before the retry token is read; the token is
    resolved through quote removal, as the shell resolves it.
    """
    words = segment.split()
    index = _skip_env_assignments(words, 0)
    return (
        index + 1 < len(words)
        and _shell_word_basename(words[index]) == "retry"
        and words[index + 1].isdigit()
    )


def _provision_candidates(segments: list[str], retry_trusted: bool) -> list[str]:
    """Segments that can genuinely provision.

    A command behind an untrusted ``retry`` never runs, and a wrapper whose
    stripped payload still carries separators is several commands at once
    (its exit status cannot be predicted), so neither can satisfy the
    provisioning checks; the raw-install scan reads the plain segments.
    """
    candidates: list[str] = []
    for segment in segments:
        if not retry_trusted and _is_retry_call(segment):
            continue
        payload = _strip_provision_wrappers(segment)
        if "\n" in payload or re.search(
            r"[;|]|(?<![<>])&(?!>)", payload
        ):
            continue
        candidates.append(segment)
    return candidates


_SHADOWED_NAMES = _PREFIX_STRIPPED_NAMES | frozenset({
    ":",
    "exit",
    "false",
    "gmake",
    "make",
    "pip",
    "pip3",
    "python",
    "python3",
    "return",
    "rustup",
    "true",
})


def _shadowing_issue(
    script: str,
    job_name: str = RELEASE_GATE_JOB_NAME,
    shell_name: str = "bash",
) -> str | None:
    """Reject functions that shadow commands the provisioning checks read.

    A function named like a shell builtin or like one of the commands the
    checks match changes what those words do, so provisioning text can no
    longer be trusted to mean what it says.  The guard spans every
    provisioning job: a no-op wrapper in either job defeats its own checks
    the same way.
    """
    if _alias_definitions_can_shadow(script, shell_name):
        return (
            f"the {job_name} job enables alias expansion while defining aliases; "
            "remove aliases from provisioning scripts so command checks stay valid"
        )
    shadowed_commands = (
        _defined_function_names(script) | _defined_alias_names(script)
    ) & _SHADOWED_NAMES
    if shadowed_commands:
        return (
            f"the {job_name} job defines shell functions or aliases that shadow "
            "commands used by the provisioning checks ("
            + ", ".join(sorted(shadowed_commands))
            + "); rename them so the checks trust the commands they read"
        )
    else:
        return None


def _provisioning_unparseable_shell_segment_issue(
    segment: str,
) -> str | None:
    """Fail closed only when malformed syntax may load shell code."""
    if re.search(r"\b(?:source|eval)\s+", segment):
        return (
            "an unparseable provisioning command may source or "
            "evaluate shell code"
        )
    if re.search(r"(?:^|[;|&(){}\n])\s*\.\s+\S", segment):
        return "an unparseable provisioning command may source shell code"
    return None


def _provisioning_nested_shell_issue(
    words: list[str], index: int, depth: int
) -> str | None:
    """Inspect explicit ``shell -c`` payloads recursively."""
    return next(
        (
            _provisioning_shell_indirection_issue(
                words[option_index + 1], depth + 1
            )
            for option_index, option in enumerate(
                words[index + 1 :], index + 1
            )
            if option == "-c" and option_index + 1 < len(words)
        ),
        None,
    )


def _provisioning_shell_words_issue(
    words: list[str], depth: int
) -> str | None:
    """Reject one parsed shell command that redirects execution."""
    index = _skip_env_assignments(words, 0)
    while index < len(words) and words[index] in {
        "if", "then", "elif", "else", "do", "while", "until", "!", "time",
    }:
        index += 1
    if index >= len(words):
        return None
    command_word = words[index]
    command = Path(command_word).name
    if command_word == ".":
        command = "."
    if command in {"builtin", "command"} and index + 1 < len(words):
        index += 1
        command = Path(words[index]).name
    if command in {"source", ".", "eval"}:
        return (
            "provisioning scripts cannot source or evaluate external shell "
            "code; inline the checked commands instead"
        )
    if command in {"bash", "sh", "zsh"}:
        return _provisioning_nested_shell_issue(words, index, depth)
    return None


def _provisioning_shell_segment_issue(
    segment: str, depth: int
) -> str | None:
    """Parse and check one segment, retaining fail-closed malformed-source rules."""
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return _provisioning_unparseable_shell_segment_issue(segment)
    return _provisioning_shell_words_issue(words, depth)


def _provisioning_shell_indirection_issue(
    script: str, depth: int = 0
) -> str | None:
    """Reject shell inputs that can inject unscanned command definitions."""
    if depth > 8:
        return "nested provisioning shells exceed the static-analysis limit"
    code = _join_continuations(
        _strip_heredocs(_strip_shell_comments(script))
    )
    if re.search(r"(?<!\w)BASH_ENV(?!\w)", code):
        return "BASH_ENV is unsupported in provisioning jobs; remove it"
    for segment in _command_segments(code):
        if issue := _provisioning_shell_segment_issue(segment, depth):
            return issue
    return None


def _provisioning_shadow_issue(workflow_content: str) -> str | None:
    """Reject shadowing definitions anywhere in a provisioning job.

    Both release-gate and fuzz-qualification resolve the pinned toolchain
    through the same commands, so both jobs' run steps are scanned; a
    function defined in one job cannot be trusted to mean the builtin it
    shadows.  Steps that do not run a shell on every path are skipped,
    exactly as the provisioning checks skip them.
    """
    for job_name in PROVISIONING_JOB_NAMES:
        records = _job_run_step_records(workflow_content, job_name)
        if records is None:
            continue
        for record in records:
            if "BASH_ENV" in record["env"]:
                return (
                    f"the {job_name} job sets BASH_ENV, which can load "
                    "unchecked shell definitions before provisioning"
                )
            step = record["run"]
            if issue := _provisioning_shell_indirection_issue(step):
                return f"the {job_name} job {issue}"
            if issue := _shadowing_issue(
                _strip_heredocs(_strip_shell_comments(step)),
                job_name,
                _workflow_shell_name(record.get("shell")),
            ):
                return issue
    return None


def _shell_option_enables_errexit(words: list[str], index: int) -> bool:
    """Whether one explicit custom-shell option turns on errexit."""
    word = words[index]
    if word in ("-e", "--errexit"):
        return True
    if word.startswith("-") and not word.startswith("--") and "e" in word[1:]:
        return True
    return (
        word == "-o"
        and index + 1 < len(words)
        and words[index + 1] == "errexit"
    )


def _shell_option_word_consumes_operand(word: str) -> bool:
    """Whether one shell option word consumes the NEXT word as its operand.

    ``--rcfile FILE``/``--init-file FILE`` take a file operand, and
    ``-o``/``-O`` take a shell-option name; the word after any of them is
    that operand, not another option.  ``bash --rcfile -e {0}`` therefore
    runs the body without errexit (verified: the whole body executes), so
    the scan must step over the operand before reading option words.

    ``-o``/``-O`` are handled by the caller, which reads the operand as the
    ``-o errexit`` form before stepping over it.
    """
    if word == "--rcfile" or word == "--init-file":
        return True
    return word in _SHELL_VALUE_OPTIONS and word not in ("-o", "-O")


def _shell_initial_errexit(shell: object) -> bool:
    """Model whether a workflow shell starts with errexit enabled.

    Every word after the ``{0}`` operand is a positional parameter of the
    script, not a shell option: ``bash {0} -e`` passes the literal ``-e``
    as ``$1`` and runs the body without errexit (verified against bash),
    so the scan stops at the operand.  A word that is another option's
    operand is skipped for the same reason the positional parameters are:
    ``bash --rcfile -e {0}`` hands ``-e`` to ``--rcfile``.
    """
    if shell is None or not isinstance(shell, str):
        return True
    words = shell.split()
    if not words or "{0}" not in words:
        # GitHub's built-in `bash`/`sh` forms add errexit by default.
        return True
    operand = words.index("{0}")
    index = 1
    while index < operand:
        word = words[index]
        if _shell_option_enables_errexit(words, index):
            return True
        if _shell_option_word_consumes_operand(word):
            # The next word is this option's operand, not an option word
            # (``--rcfile -e`` hands ``-e`` to ``--rcfile``).
            index += 2
            continue
        index += 1
    return False


def _ends_with_background_operator(script: str) -> bool:
    """Whether the last non-space token is a bare shell ``&`` operator."""
    text = script.rstrip()
    if not text.endswith("&") or text.endswith("&&"):
        return False
    backslashes = 0
    for char in reversed(text[:-1]):
        if char != "\\":
            break
        backslashes += 1
    return backslashes % 2 == 0


def _release_gate_step_candidates(
    step: str, shell: object = None
) -> tuple[list[str], str | None]:
    """Analyze one independent workflow shell step for provisioning commands."""
    stripped = _strip_heredocs(_strip_shell_comments(step))
    executable_source = _join_continuations(stripped)
    if structure_issue := _unclosed_structure_issue(executable_source):
        return [], structure_issue
    if shadow_issue := _shadowing_issue(executable_source):
        return [], shadow_issue
    executable = _strip_function_bodies(executable_source)
    if _dynamic_heredoc_markers(executable):
        return [], (
            "the release-gate job uses an ANSI-C escaped heredoc delimiter "
            "that this validator cannot resolve statically; use a plain or "
            "quoted delimiter"
        )
    retry_trusted = _retry_runs_its_target(executable_source)
    failing, exiting = _function_kill_sets(executable_source)
    pairs = _command_segments_with_separators(executable)
    for index, (segment, _) in enumerate(pairs):
        following_separator = pairs[index + 1][1] if index + 1 < len(pairs) else ""
        if (
            following_separator == "&"
            and _VERIFIED_INSTALLER_RE.match(_strip_provision_wrappers(segment))
        ):
            return [], (
                "the verified Rust installer must finish in the foreground "
                "before rustup adds a component"
            )
    if pairs and _ends_with_background_operator(executable):
        last_segment = _strip_provision_wrappers(pairs[-1][0])
        if _VERIFIED_INSTALLER_RE.match(last_segment):
            return [], (
                "the verified Rust installer must finish in the foreground "
                "before a later workflow step"
            )
    candidates = _live_command_segments(
        executable, failing, exiting,
        errexit=_shell_initial_errexit(shell),
    )
    return _provision_candidates(candidates, retry_trusted), None


def _release_gate_provisioning_issue(segments: list[str]) -> str | None:
    """Check drift, installer and rustfmt ordering across analyzed steps."""
    drift_found = any(
        _DRIFT_CHECK_RE.match(
            _quote_removed_command(_strip_provision_wrappers(segment))
        )
        for segment in segments
    )
    if not drift_found:
        return (
            "the release-gate job no longer runs "
            f"{RELEASE_GATE_RUSTFMT_CONSUMER}; update this provisioning "
            "expectation with the job split"
        )
    installer_at = next(
        (
            position
            for position, segment in enumerate(segments)
            if _VERIFIED_INSTALLER_RE.match(_strip_provision_wrappers(segment))
        ),
        None,
    )
    if installer_at is None:
        return (
            "the release-gate job must provision the pinned Rust toolchain "
            "through the verified installer (bash ./packaging/scripts/"
            'install-verified-rustup.sh --toolchain "${RUST_TOOLCHAIN}")'
        )
    component_at = _rustfmt_component_index(segments, after=installer_at)
    if component_at is not None:
        return None
    if _rustfmt_component_index(segments) is None:
        return (
            "the release-gate job must add the rustfmt component for the "
            "pinned toolchain (rustup component add --toolchain "
            '"${RUST_TOOLCHAIN}" rustfmt) so cargo, rustc and rustfmt '
            "resolve for the gate scripts"
        )
    return (
        "the release-gate job must install the toolchain through the "
        "verified installer before adding the rustfmt component: rustup "
        "cannot add a component to a toolchain that is not installed"
    )


def _release_gate_toolchain_issue(
    run_scripts: str | Sequence[str | dict],
) -> str | None:
    """Return the toolchain provisioning issue, or None when satisfied.

    Only executable commands in command position count: comments, heredoc
    bodies, function bodies and quoted text cannot satisfy the gate. The
    pinned toolchain must use the verified installer and include rustfmt.
    """
    steps = [run_scripts] if isinstance(run_scripts, str) else list(run_scripts)
    segments: list[str] = []
    for record in steps:
        if isinstance(record, str):
            step, shell = record, None
        elif isinstance(record, dict) and isinstance(record.get("run"), str):
            step, shell = record["run"], record.get("shell")
        else:
            return "release-gate run step cannot be verified as a shell script"
        candidates, issue = _release_gate_step_candidates(step, shell)
        if issue:
            return issue
        segments.extend(candidates)
    return _release_gate_provisioning_issue(segments)


def check_release_gate_toolchain(result: ValidationResult) -> None:
    """Validate pinned Rust toolchain provisioning in the release-gate job."""
    content = read_safe(RELEASE_PACKAGES_WORKFLOW)
    if not content:
        result.fail(
            PKG_RELEASE_GATE_TOOLCHAIN_GATE,
            RELEASE_PACKAGES_WORKFLOW_MISSING,
        )
        return

    run_steps = _job_run_step_records(content, RELEASE_GATE_JOB_NAME)
    if run_steps is None:
        result.fail(
            PKG_RELEASE_GATE_TOOLCHAIN_GATE,
            f"{RELEASE_GATE_JOB_NAME} job not found in release-packages.yml",
        )
        return

    issue = (
        _raw_toolchain_install_issue(content)
        or _provisioning_shadow_issue(content)
        or _release_gate_toolchain_issue(run_steps)
    )
    if issue is None:
        result.pass_(
            PKG_RELEASE_GATE_TOOLCHAIN_GATE,
            "release gate provisions the pinned Rust toolchain through the "
            "verified installer (cargo/rustc/rustfmt)",
        )
    else:
        result.fail(PKG_RELEASE_GATE_TOOLCHAIN_GATE, issue)


def _workflow_naming_issue(wf_content: str) -> str | None:
    """Return a description of the naming gap, or None when both formats carry the version.

    The deb and rpm patterns are intentionally mutually exclusive: the deb
    filename form separates the product name with a hyphen (``nginx-<version>``)
    while the rpm form appends the version directly (``nginx<version>``).
    A shared ``nginx-?`` prefix would let the deb row satisfy the rpm check.
    """
    has_deb_naming = bool(
        re.search(r"nginx-\$\{?NGINX_VERSION", wf_content)
        or re.search(r"nginx-\$\{\{[^}]*nginx_version", wf_content)
    )
    has_rpm_naming = bool(
        re.search(r"nginx\$\{?NGINX_VERSION", wf_content)
        or re.search(r"nginx\$\{\{[^}]*nginx_version", wf_content)
    )
    if has_deb_naming and has_rpm_naming:
        return None
    missing = []
    if not has_deb_naming:
        missing.append(".deb naming without NGINX version")
    if not has_rpm_naming:
        missing.append(".rpm naming without NGINX version")
    return "; ".join(missing)


def _requirements_pin_issue(requirements: str) -> str | None:
    """Reject a missing requirements file or an unpinned dependency."""
    if not requirements:
        return (
            "requirements-release.txt not found; the release-gate job "
            "cannot be verified to install its pinned Python dependencies"
        )
    return next(
        (
            f"requirements-release.txt must pin {name} to a version (a bare name or a lone separator is not a pin)"
            for name, pattern in (
                ("jsonschema[format]", r"^jsonschema\[format\]=="),
                ("PyYAML", r"^PyYAML=="),
            )
            if not re.search(pattern + r"[^#\s]", requirements, re.M)
        ),
        None,
    )


def _step_script(step: str | dict) -> str | None:
    """Get a script from a plain string or parsed run-step record."""
    if isinstance(step, str):
        return step
    if isinstance(step, dict) and isinstance(step.get("run"), str):
        return step["run"]
    return None


def _step_live_commands(step: str | dict) -> list[str]:
    """Executable command segments in one run step with its shell semantics."""
    script = _step_script(step)
    if script is None:
        return []
    stripped = _strip_heredocs(_strip_shell_comments(script))
    executable_source = _join_continuations(stripped)
    executable = _strip_function_bodies(executable_source)
    failing, exiting = _function_kill_sets(executable_source)
    shell = step.get("shell") if isinstance(step, dict) else None
    return _live_command_segments(
        executable, failing, exiting,
        errexit=_shell_initial_errexit(shell),
    )


_CARRIED_BODY_MARKER_KEYWORDS = frozenset({"then", "do", "else"})


def _prerequisite_view_text(segment: str) -> str:
    """Segment text as the live-command view carries it.

    A ``then``/``do``/``else`` marker segment is presented to the
    prerequisite checks as the command it carries (``then pip install ...``
    appears as ``pip install ...``), so the masked and backgrounded sets
    must normalize the same way or a comparison against that view misses.
    """
    stripped = segment.strip()
    words = stripped.split(maxsplit=1)
    if words and words[0] in _CARRIED_BODY_MARKER_KEYWORDS and len(words) > 1:
        return words[1]
    return stripped


def _backgrounded_command_segments(script: str) -> set[str]:
    """Segment texts the shell runs in the background (``cmd &``).

    A backgrounded command's exit status is never observed by the step, so
    it cannot satisfy a prerequisite in that step.  The shell backgrounds
    the WHOLE list the ``&`` terminates: in ``a && b &`` the operator
    applies to the ``a && b`` list, so both segments run asynchronously.
    ``&&``/``||``/``|`` continue the current list, ``;`` and newline end it
    synchronously, and a trailing ``&`` is recovered from the script text
    (the pair stream drops the final separator).
    """
    pairs = _command_segments_with_separators(script)
    if not pairs:
        return set()
    followings = [
        pairs[index + 1][1]
        if index + 1 < len(pairs)
        else ("&" if _ends_with_background_operator(script) else "")
        for index in range(len(pairs))
    ]
    backgrounded: set[str] = set()
    group: list[str] = []
    for (segment, _separator), following in zip(pairs, followings):
        group.append(segment.strip())
        if following in ("&&", "||", "|"):
            # The list continues; the terminator decides its fate.
            continue
        if following == "&":
            backgrounded.update(_prerequisite_view_text(s) for s in group)
        group = []
    return backgrounded


def _shell_rhs_discards_failure(segment: str) -> bool:
    """Whether a ``||`` right-hand segment turns failure into success.

    Only a provably unsuccessful right-hand side keeps the left-hand
    failure visible: ``false``, and ``exit``/``return`` with no argument
    (the failure status is reused) or a literal nonzero status AFTER the
    shell's 8-bit truncation (``exit 256`` exits successfully, so it
    masks).  Every other form - ``true``, ``:``, ``exit 0``, ``echo``,
    ``printf``, an empty or unrecognized segment - masks the failure, so
    the chain succeeds even when the command fails and its exit status
    proves nothing.
    """
    words = _shell_words(segment)
    command_index = _skip_env_assignments(words, 0)
    command = words[command_index:]
    if not command:
        return True
    if command[0] == "false":
        return False
    if command[0] in {"exit", "return"}:
        if len(command) == 1:
            # Without an argument the status is reused, so a failed left
            # side keeps failing the chain.
            return False
        if len(command) == 2:
            try:
                status = int(command[1])
            except ValueError:
                # Non-literal statuses stay unrecognized and fail closed.
                return True
            # The shell truncates an exit status to its low 8 bits, so
            # `exit 256` exits successfully (256 % 256 == 0) and the
            # chain succeeds: that masks the failure.
            return status % 256 == 0
        # More than one argument (a syntax error at runtime) fails closed.
        return True
    return True


def _failure_masked_command_segments(
    script: str, errexit: bool = True
) -> set[str]:
    """Segment texts whose failure a following ``||`` chain swallows.

    A prerequisite written as ``cmd || true`` can certify a step while its
    failure is silently ignored, so such a segment must not count as
    satisfying it.  Each member of a connected ``&&``/``||`` list is
    examined separately: its failure flows forward through the connectors
    and is swallowed exactly when the list's final status can become
    success anyway.  In ``false || true && pip install ...`` the prefix
    failure is absorbed but the install is reached regardless and reports
    its own status, so only the prefix is masked; in ``cmd || false ||
    true`` the chain reaches ``true``, so ``cmd`` is masked too.  An
    unknown right-hand side is treated as succeeding (fail closed).

    ``errexit`` models the step's shell: with errexit (GitHub's default
    bash adds ``-e``) a propagating list fails the shell immediately, so
    a later command cannot replace its status.  Without errexit the shell
    continues past the failed list and the step's final status comes from
    the last command, so a followed list still swallows the failure; the
    conservative reading masks it whenever any later segment exists.
    """
    pairs = _command_segments_with_separators(script)
    masked: set[str] = set()
    list_start = 0
    for index in range(len(pairs)):
        if _connected_list_continues(pairs, index):
            continue
        for position in range(list_start, index + 1):
            if _member_failure_is_swallowed(pairs, position, index, errexit):
                masked.add(_prerequisite_view_text(pairs[position][0]))
        list_start = index + 1
    return masked


def _connected_list_continues(pairs: list[tuple[str, str]], index: int) -> bool:
    """Whether the segment at ``index`` continues a connected ``&&``/``||`` list."""
    if index + 1 >= len(pairs):
        return False
    return pairs[index + 1][1] in ("&&", "||")


def _member_failure_is_swallowed(
    pairs: list[tuple[str, str]], position: int, end: int, errexit: bool
) -> bool:
    """Whether one member's failure can be absorbed before the list ends.

    The simulation assumes the member fails, then follows the connectors:
    an ``||`` runs its right-hand side when the status is failure and an
    ``&&`` runs it when the status is success.  A right-hand side that
    discards failure (``true``, ``exit 0``, an unrecognized command) turns
    the running status into success, so any later member sees a success
    path; a right-hand side that provably fails (``false``, ``exit 1``)
    continues the failure.  Success in the final status means the member's
    failure never reaches the step's exit code and the member must not
    count.  Without errexit, a following segment replaces the list's
    status wholesale, so the failure is swallowed there as well.
    """
    status_failed = True
    for next_position in range(position + 1, end + 1):
        connector = pairs[next_position][1]
        runs = not status_failed if connector == "&&" else status_failed
        if runs:
            status_failed = not _shell_rhs_discards_failure(
                pairs[next_position][0]
            )
    return not errexit and end + 1 < len(pairs) if status_failed else True


def _masked_command_segments_for_step(step: str | dict) -> set[str]:
    """Failure-masked segments of one run step's executable script."""
    script = _step_script(step)
    if script is None:
        return set()
    executable = _strip_function_bodies(
        _join_continuations(_strip_heredocs(_strip_shell_comments(script)))
    )
    shell = step.get("shell") if isinstance(step, dict) else None
    return _failure_masked_command_segments(
        executable, errexit=_shell_initial_errexit(shell)
    )


def _foreground_live_commands(step: str | dict) -> list[str]:
    """Live command segments that run in the FOREGROUND to completion.

    The prerequisite checks consume this view so a backgrounded ``pip
    install`` or ``make docs-check`` cannot count as satisfied: the shell
    never waits for ``cmd &``, so its exit status proves nothing.
    """
    script = _step_script(step)
    if script is None:
        return []
    stripped = _strip_heredocs(_strip_shell_comments(script))
    executable_source = _join_continuations(stripped)
    executable = _strip_function_bodies(executable_source)
    backgrounded = _backgrounded_command_segments(executable)
    return [
        segment
        for segment in _step_live_commands(step)
        if segment.strip() not in backgrounded
    ]


def _normalize_shell_command_words(words: list[str]) -> list[str]:
    """Normalize a path-qualified executable without altering its arguments."""
    return [_shell_word_basename(words[0]), *words[1:]] if words else words


def _shell_command_after_prefix(words: list[str]) -> list[str] | None:
    """Strip known execution wrappers and normalize path-based command names."""
    if not words:
        return []
    command = _shell_word_basename(words[0])
    if command == "timeout":
        index = _timeout_command_index(words)
        return None if index is None else _normalize_shell_command_words(words[index:])
    if command in _PROCESS_PREFIX_FLAGS:
        index = _process_prefix_command_index(words)
        return None if index is None else _normalize_shell_command_words(words[index:])
    wrappers = {
        "bash", "sh", "dash", "zsh", "sudo", "env", "command",
        "retry", "nohup", "nice", "stdbuf", "time",
    }
    if command not in wrappers:
        return [_shell_word_basename(words[0]), *words[1:]]
    serialized = shlex.join(words)
    stripped = _strip_provision_wrappers(serialized)
    command_source = stripped if stripped != serialized else serialized
    try:
        words = shlex.split(command_source, posix=True)
    except ValueError:
        return None
    return _normalize_shell_command_words(words)


def _shell_words(segment: str) -> list[str]:
    """Tokenize one command while retaining its environment assignments."""
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return []
    command_index = _skip_env_assignments(words, 0)
    if command_index == len(words):
        return words
    prefix = words[:command_index]
    command = _shell_command_after_prefix(words[command_index:])
    return [] if command is None else [*prefix, *command]


# Options that always consume the following word as their operand.
_MAKE_REQUIRED_VALUE_OPTIONS = frozenset({
    "-C", "--directory", "-f", "--file", "--makefile", "-I",
    "--include-dir", "-o", "--old-file", "-W", "--what-if",
    "--assume-new", "-E", "--eval",
})
# -j/--jobs take an optional operand that GNU Make consumes only when the
# next word is all digits (positive_int); -l/--load-average/--max-load
# consume it only when it starts with a digit or a dot (floating).  A word
# like ``docs-check`` stays a goal, so ``make -j docs-check`` runs the
# repository chain and must certify.
_MAKE_OPTIONAL_INT_OPTIONS = frozenset({"-j", "--jobs"})
_MAKE_OPTIONAL_FLOAT_OPTIONS = frozenset(
    {"-l", "--load-average", "--max-load"}
)
# -O/--output-sync take an optional operand that GNU Make accepts only in
# the same word (``-Oline``, ``--output-sync=line``); a separate next word
# is never consumed.  ``--debug``/``--shuffle``/``--random`` take optional
# operands with the same attached-only rule.
_MAKE_ATTACHED_VALUE_OPTIONS = frozenset(
    {"-O", "--output-sync", "--debug", "--shuffle", "--random"}
)
# Long options that neither select another makefile nor stop execution.
# They are modeled so an exact name certifies; an argument-taking one
# consumes its operand like make does.
_MAKE_HARMLESS_LONG_OPTIONS = frozenset({
    "--always-make", "--environment-overrides",
    "--keep-going", "--no-keep-going", "--stop", "--no-builtin-rules",
    "--no-builtin-variables", "--silent", "--quiet", "--no-silent",
    "--print-directory", "--no-print-directory", "--warn-undefined-variables",
    "--trace", "--check-symlink-times",
})
_MAKE_HARMLESS_LONG_VALUE_OPTIONS = frozenset({
    "--jobserver-auth", "--jobserver-fds", "--sync-mutex",
    "--jobserver-style", "--temp-stdin",
})
# Every long option this model knows by exact name.  GNU Make's getopt_long
# also accepts unique abbreviations (``--dry`` == ``--dry-run``), and the
# abbreviation must resolve against the RUNNING make's catalog, which
# varies by version; an abbreviated or unknown word therefore cannot be
# certified and is rejected.

# A -C/--directory operand that still names the repository root.  Any other
# operand selects another directory's Makefile, so the resolved docs-check
# target is no longer provably the repository chain.
_MAKE_ROOT_DIRECTORY_VALUES = frozenset({".", "./"})
# Options that supply their own makefile or definitions.  An invocation
# carrying one of these cannot prove that the REPOSITORY's docs-check ran:
# the target it resolves may come from the supplied makefile or from the
# ``--eval`` text (``make -f /dev/null --eval='docs-check: ;' docs-check``
# succeeds while the repository chain never executes).
_MAKE_UNCERTIFIABLE_OPTIONS = frozenset(
    {"-f", "--file", "--makefile", "-E", "--eval",
     "-o", "--old-file", "-W", "--what-if", "--assume-new",
     "-p", "--print-data-base"}
)
_MAKE_UNCERTIFIABLE_LONG_PREFIXES = (
    "--file=", "--makefile=", "--eval=",
    "--old-file=", "--what-if=", "--assume-new=",
)
# Short letters of the uncertifiable options: f/E supply a makefile or an
# evaled statement; o/W mark the following file old or what-if, which skips
# the named target's recipe when it is the checked one.
_MAKE_UNCERTIFIABLE_SHORT_LETTERS = frozenset({"f", "E", "o", "W", "p"})


def _make_option_uncertifiable(word: str) -> bool:
    """Whether one option word defeats a docs-check certification.

    A supplied makefile or ``--eval`` text replaces what is read; an
    old-file/what-if operand can name the checked target and skip its
    recipe (``make -o docs-check docs-check`` prints "Nothing to be done").
    Long forms match exactly or with an ``=`` operand, and a short cluster
    is scanned as make parses it: an argument-taking letter before the
    decisive one means it is that option's argument, not an option.
    """
    if word in _MAKE_UNCERTIFIABLE_OPTIONS:
        return True
    if word.startswith(_MAKE_UNCERTIFIABLE_LONG_PREFIXES):
        return True
    if not word.startswith("-") or word.startswith("--"):
        return False
    for letter in word[1:]:
        if letter in _MAKE_UNCERTIFIABLE_SHORT_LETTERS:
            return True
        if letter in _MAKE_ARGUMENT_TAKING_SHORT:
            return False
    return False


def _make_option_masks_failures(word: str) -> bool:
    """Whether one option word makes make ignore a failing recipe.

    ``-i``/``--ignore-errors`` makes the recipe's failure invisible: the
    step succeeds although the docs check failed (verified: ``make -i f``
    exits 0 with the failure "ignored").  A cluster carries it too: the
    short spelling ``-silent`` is read by make as ``-s -i -l ent``, so the
    scan looks for ``i`` up to the first argument-taking letter.
    """
    if word in ("-i", "--ignore-errors"):
        return True
    if not word.startswith("-") or word.startswith("--"):
        return False
    for letter in word[1:]:
        if letter == "i":
            return True
        if letter in _MAKE_ARGUMENT_TAKING_SHORT:
            return False
    return False


def _make_directory_operand_off_root(operand: str | None) -> bool:
    """Whether a -C/--directory operand changes away from the repo root.

    GNU Make changes directory before reading makefiles, so only an
    operand naming the repository root (``.``/``./``) resolves the
    repository docs-check; any other directory selects a different
    Makefile.  An unreadable operand fails closed.
    """
    return operand not in _MAKE_ROOT_DIRECTORY_VALUES
_MAKE_NONEXECUTING_OPTIONS = frozenset({
    "-n", "--dry-run", "--just-print", "--recon",
    "-q", "--question", "-t", "--touch",
    "-v", "--version", "-h", "--help",
})
_MAKE_NONEXECUTING_LONG_OPTIONS = frozenset(
    option for option in _MAKE_NONEXECUTING_OPTIONS if option.startswith("--")
)
_MAKE_NONEXECUTING_SHORT_FLAGS = frozenset("nqtvh")

# Short options that consume the rest of their cluster as an argument
# (make's switch table: C f I j l o O W E).  The cluster scan stops here:
# the letters after one of these are its argument, not options.  ``-Wn``
# therefore asks make to treat file ``n`` as new and the recipe still
# runs, while ``-fn`` reads makefile ``n`` and fails before any recipe -
# neither is a silent non-execution, so neither disqualifies the step.
_MAKE_ARGUMENT_TAKING_SHORT = frozenset("CfIjloOWE")


def _make_short_cluster_prevents_execution(
    word: str, non_executing: frozenset[str]
) -> bool:
    """Whether one short-option cluster selects a non-executing mode.

    Every letter is scanned until a letter in ``non_executing`` matches
    or an argument-taking letter consumes the remainder of the word.
    The command-line path and the MAKEFLAGS path both scan clusters with
    this helper so the two agree.
    """
    for letter in word[1:]:
        if letter in non_executing:
            return True
        if letter in _MAKE_ARGUMENT_TAKING_SHORT:
            break
    return False


def _make_option_prevents_execution(word: str) -> bool:
    """Recognize options that stop make before docs-check can run."""
    option = word.split("=", 1)[0]
    if word in _MAKE_NONEXECUTING_OPTIONS or option in _MAKE_NONEXECUTING_LONG_OPTIONS:
        return True
    return (
        word.startswith("-")
        and not word.startswith("--")
        and _make_short_cluster_prevents_execution(
            word, _MAKE_NONEXECUTING_SHORT_FLAGS
        )
    )


def _make_operand_consumption(letter: str, operand: str | None) -> bool:
    """Whether GNU Make consumes the next word as this option's operand.

    ``j`` (jobs) consumes a separated operand only when it is all digits,
    ``l`` (load-average) only when it starts with a digit or a dot, and
    ``O`` (output-sync) never consumes a separated word.  Every other
    argument-taking letter consumes the next word unconditionally.
    """
    if letter == "O":
        return False
    if letter == "j":
        return bool(operand) and operand.isascii() and operand.isdigit()
    if letter == "l":
        first = operand[:1] if operand is not None else ""
        return first.isascii() and (first.isdigit() or first == ".")
    return operand is not None


def _make_directory_step(rest: str, operand: str | None, index: int) -> int | None:
    """Advance past a ``-C`` with its attached or separated operand."""
    if rest:
        return None if _make_directory_operand_off_root(rest) else index + 1
    if operand is None or _make_directory_operand_off_root(operand):
        return None
    return index + 2


def _make_cluster_letter_step(
    letter: str, rest: str, operand: str | None, index: int
) -> int | None | bool:
    """Resolve one argument-taking letter of a short cluster.

    Returns the next index, None for a rejection, or False when the letter
    is not argument-taking (the caller keeps scanning).
    """
    if letter not in _MAKE_ARGUMENT_TAKING_SHORT:
        return False
    if letter == "C":
        return _make_directory_step(rest, operand, index)
    if rest:
        # An attached operand: the letter consumed the remainder.
        return index + 1
    if _make_operand_consumption(letter, operand):
        return index + 2
    # A required-operand letter with a missing operand cannot be certified;
    # an optional-operand letter (j/l/O) simply takes the next word.
    return index + 1 if letter in ("j", "l", "O") else None


def _make_short_option_step(
    word: str, operand: str | None, index: int
) -> int | None:
    """Advance past one short-option cluster, or reject it."""
    for offset, letter in enumerate(word[1:]):
        if letter in _MAKE_NONEXECUTING_SHORT_FLAGS:
            return None
        step = _make_cluster_letter_step(
            letter, word[2 + offset:], operand, index
        )
        if step is not False:
            return step
    return index + 1


def _make_long_option_unknown(word: str) -> bool:
    """Whether a ``--`` word names an option this model does not know.

    GNU Make's getopt_long accepts unique abbreviations resolved against
    the running version's catalog, so ``--dry`` means ``--dry-run``,
    ``--eva`` means ``--eval``, and ``--fil`` means ``--file``.  An
    unknown or abbreviated name therefore cannot be proven harmless and
    fails closed.
    """
    return "--" + word[2:].split("=", 1)[0] not in _MAKE_KNOWN_LONG_OPTIONS


_MAKE_FAILURE_MASKING_LONG_OPTIONS = frozenset({"--ignore-errors"})
_MAKE_KNOWN_LONG_OPTIONS = (
    _MAKE_HARMLESS_LONG_OPTIONS
    | _MAKE_HARMLESS_LONG_VALUE_OPTIONS
    | _MAKE_FAILURE_MASKING_LONG_OPTIONS
    | _MAKE_ATTACHED_VALUE_OPTIONS
    | _MAKE_OPTIONAL_INT_OPTIONS
    | _MAKE_OPTIONAL_FLOAT_OPTIONS
    | _MAKE_REQUIRED_VALUE_OPTIONS
    | _MAKE_UNCERTIFIABLE_OPTIONS
    | frozenset({"--directory"})
    | _MAKE_NONEXECUTING_LONG_OPTIONS
)


def _make_long_option_step(
    word: str, operand: str | None, index: int
) -> int | None:
    """Advance past one ``--``-prefixed option word, or reject it.

    The dispatch order mirrors make's own parse: an unknown or abbreviated
    name fails closed; a harmless flag advances one word; a harmless value
    option consumes its operand (attached or next word); ``--directory``
    checks its operand; the optional-operand forms consume a next word
    only when the typed operand matches; attached-only forms never do; and
    a required operand must be present.
    """
    name, _, attached = word.partition("=")
    if name not in _MAKE_KNOWN_LONG_OPTIONS:
        # Unknown or abbreviated (``--dry``, ``--eva``): make resolves the
        # latter against the running version's catalog, so the word cannot
        # be certified.  Fail closed.
        return None
    if name in _MAKE_HARMLESS_LONG_OPTIONS:
        return index + 1
    if name == "--directory":
        return _make_directory_step(attached, operand, index)
    if attached:
        # The operand is inside this word: whatever the option kind, the
        # next word is not its operand.
        return index + 1
    if name in _MAKE_HARMLESS_LONG_VALUE_OPTIONS | _MAKE_REQUIRED_VALUE_OPTIONS:
        return index + 2 if operand is not None else None
    if name in _MAKE_ATTACHED_VALUE_OPTIONS:
        return index + 1
    letter = "j" if name in _MAKE_OPTIONAL_INT_OPTIONS else "l"
    return index + 2 if _make_operand_consumption(letter, operand) else index + 1


def _make_option_step(words: list[str], index: int) -> int | None:
    """Advance past one make option word, or reject the invocation.

    Returns the next index, or None when the word cannot be certified: a
    non-executing option, a supplied makefile/--eval, a directory redirect
    away from the repository root, or a missing required operand.  The
    operand rules mirror GNU Make: ``-j``/``--jobs`` consume the next word
    only when it is all digits, ``-l``/``--load-average``/``--max-load``
    only when it starts with a digit or a dot, ``-O``/``--output-sync``
    never consume a next word, and every other argument-taking option
    consumes its operand from the remainder of the word or the next word.
    """
    word = words[index]
    operand = words[index + 1] if index + 1 < len(words) else None
    if _make_option_prevents_execution(word):
        return None
    if _make_option_uncertifiable(word):
        # A supplied makefile, --eval text, or old-file/what-if operand
        # means the resolved target may not be (or may not be remade as)
        # the repository's docs-check chain.
        return None
    if _make_option_masks_failures(word):
        # A masked failure would let the step succeed although the check
        # failed, so the invocation cannot certify it.
        return None
    if word.startswith("--"):
        return _make_long_option_step(word, operand, index)
    if not word.startswith("-"):
        return None
    return _make_short_option_step(word, operand, index)


def _make_target_word_is_assignment(word: str) -> bool:
    """Whether a non-option word is a make variable assignment.

    GNU Make classifies any non-option argument containing ``=`` as a
    variable definition (``handle_non_switch_argument``), and assignments
    applied for the whole run: ``make SHELL=/usr/bin/true docs-check``
    executes every recipe through ``true`` and succeeds without doing the
    work (verified on 3.81 and 4.4.1), so the invocation cannot certify
    anything about the checked target.  The spelling covers any variable
    name make accepts (dots included, such as ``.SHELLFLAGS``).
    """
    return "=" in word


def _make_targets_after_terminator(
    targets: list[str], words: list[str], index: int
) -> list[str] | None:
    """Resolve the words after a ``--`` terminator.

    Every word after ``--`` is a non-option argument, and an assignment
    there still shapes the run (verified), so one disqualifies the
    invocation.
    """
    rest = words[index + 1:]
    if any(_make_target_word_is_assignment(word) for word in rest):
        return None
    return targets + rest


def _make_targets_after_options(words: list[str], index: int) -> list[str] | None:
    """Collect make target words, or reject a command that cannot execute them."""
    targets: list[str] = []
    while index < len(words):
        word = words[index]
        if word == "--":
            return _make_targets_after_terminator(targets, words, index)
        if word.startswith("-"):
            next_index = _make_option_step(words, index)
            if next_index is None:
                return None
            index = next_index
            continue
        if _make_target_word_is_assignment(word):
            # An assignment shapes the whole run's environment; the caller
            # cannot assume the checked target runs its recipes.
            return None
        targets.append(word)
        index += 1
    return targets


def _make_docs_wrapper_next_index(
    words: list[str], index: int, command: str
) -> int | None:
    """Return the next command index for one recognized execution wrapper."""
    if command == "env":
        return _skip_env_prefix(words, index + 1)
    if command == "timeout":
        advance = _timeout_command_index(words[index:])
    elif command in _PROCESS_PREFIX_FLAGS:
        advance = _process_prefix_command_index(words[index:])
    elif command in _WRAPPER_COMMANDS:
        next_index = _wrapper_command_step(words, index)
        return next_index if next_index is not None and next_index > index else None
    else:
        return None
    return index + advance if advance is not None and advance > 0 else None


def _make_docs_check_index(words: list[str]) -> int | None:
    """Find make or gmake after transparent execution wrappers."""
    index = _skip_env_assignments(words, 0)
    for _ in range(_MAX_COMMAND_WRAPPER_DEPTH):
        if index >= len(words):
            return None
        if _shell_word_basename(words[index]) in {"make", "gmake"}:
            return index
        next_index = _make_docs_wrapper_next_index(
            words, index, _shell_word_basename(words[index])
        )
        if next_index is None:
            return None
        index = next_index
    return None


# Letters that stop recipe execution via MAKEFLAGS: just-print (n),
# question (q), touch (t), version (v).  ``h``/``--help`` are excluded
# deliberately: make 3.81 and 4.3 ignore them in the environment and the
# recipe runs, so disqualifying the step would be wrong on those versions.
_MAKE_NONEXECUTING_SHORT = frozenset("nqtv")

# Long spellings of the same non-executing modes (help excluded for the
# same version-dependent reason).
_MAKE_NONEXECUTING_LONG = frozenset(
    ("dry-run", "just-print", "recon", "question", "touch", "version")
)


def _make_option_word_prevents_execution(word: str) -> bool:
    """Whether one make option word selects a non-executing mode.

    A word starting with ``--`` is a long option (the name up to ``=`` is
    matched against the non-executing spellings).  Otherwise the word is a
    short-option cluster, scanned by the same helper the command-line path
    uses so the two cannot drift apart.
    """
    if word.startswith("--"):
        return word[2:].split("=", 1)[0] in _MAKE_NONEXECUTING_LONG
    return _make_short_cluster_prevents_execution(
        word, _MAKE_NONEXECUTING_SHORT
    )


def _make_flags_value_prevents_execution(value: str) -> bool:
    """Whether one MAKEFLAGS/GNUMAKEFLAGS value stops recipe execution.

    GNU Make prepends a dash to the first word unless it already starts
    with a dash or contains ``=``, so both ``n`` and ``-n`` select
    just-print.  Later words are parsed as written: a word that is not an
    option is a goal or variable definition and selects no mode.
    """
    for index, word in enumerate(value.split()):
        if index == 0 and not word.startswith("-") and "=" not in word:
            word = "-" + word
        if not word.startswith("-"):
            continue
        if _make_option_word_prevents_execution(word):
            return True
        if word.startswith("--") and _make_long_option_unknown(word):
            # An abbreviation can mean any non-executing mode (--dry is
            # --dry-run) or a makefile supplier (--eva is --eval); the
            # word cannot be proven harmless, so the value disqualifies.
            return True
    return False


def _make_flags_value_uncertifiable(value: str) -> bool:
    """Whether one MAKEFLAGS/GNUMAKEFLAGS value defeats certification.

    ``--eval`` text and ``-f``/``--makefile`` selections read from the
    environment take effect before the repository Makefile is read
    (``MAKEFLAGS='--eval=SHELL=/bin/true'`` makes every recipe a no-op),
    old-file/what-if operands can skip the checked target, and a variable
    assignment in the value changes the run's environment
    (``MAKEFLAGS='SHELL=/usr/bin/true'`` runs every recipe through
    ``true`` and succeeds without doing the work - verified).  The first
    word gets the implied dash, exactly as make applies it before parsing;
    an assignment keeps no dash, so it is rejected as written.
    """
    for index, word in enumerate(value.split()):
        if index == 0 and not word.startswith("-") and "=" not in word:
            word = "-" + word
        if not word.startswith("-"):
            if _make_target_word_is_assignment(word):
                # A variable assignment in the flags changes the run's
                # environment for every recipe; the checked target cannot
                # be certified.
                return True
            continue
        if _make_option_uncertifiable(word):
            return True
    return False


def _make_flags_value_masks_failures(value: str) -> bool:
    """Whether one MAKEFLAGS/GNUMAKEFLAGS value hides a failing recipe.

    The environment forms of ``-i``/``--ignore-errors`` (including the
    dash-less ``i`` and clusters such as ``silent``, which make reads as
    ``-s -i -l ent``) let a failing docs check exit 0, so a step carrying
    one cannot prove the check succeeded.  The first word gets the implied
    dash, exactly as make applies it before parsing.
    """
    for index, word in enumerate(value.split()):
        if index == 0 and not word.startswith("-") and "=" not in word:
            word = "-" + word
        if not word.startswith("-"):
            continue
        if _make_option_masks_failures(word):
            return True
    return False


# Every environment name the make gate reads: an export of any of these
# changes what a later invocation in the same shell does.
_MAKE_ENV_NAMES = frozenset(
    {"MAKE", "MAKEFILES", "MAKEFLAGS", "GNUMAKEFLAGS"}
)


def _make_environment_overrides_the_run(env: object) -> bool:
    """Whether make's environment re-points its toolchain or its inputs.

    ``MAKE`` replaces the program every ``$(MAKE)`` recursion runs and
    ``MAKEFILES`` preloads a file whose definitions precede the repository
    Makefile, so either leaves the checked target's recipes unverifiable
    (verified on GNU Make 4.3 and 4.4.1: ``MAKE=/usr/bin/true make
    docs-check`` and a preloaded file assigning ``SHELL`` both exit 0
    without running the check).
    """
    if not isinstance(env, dict):
        return False
    for name in ("MAKE", "MAKEFILES"):
        value = env.get(name)
        if isinstance(value, str) and value:
            return True
    return False


def _make_environment_defeats_certification(env: object) -> bool:
    """Whether make's environment defeats a docs-check certification.

    GNU Make reads ``MAKEFLAGS`` (and ``GNUMAKEFLAGS``) from the
    environment, so ``-n``/``-q``/``-t`` set there stop recipe execution
    for every invocation in the step.  The same is true of the dash-less
    spellings (``n``, ``kn``) and the long forms (``--dry-run``).  A
    value that supplies its own makefile or ``--eval`` text also defeats
    the certification: the repository Makefile is never read (or its
    recipes are replaced), so ``docs-check`` cannot have run.  A
    ``MAKE``/``MAKEFILES`` override defeats it outright.  A workflow,
    job, or step scope can carry any of these, and a command-local
    assignment is inspected by the caller.
    """
    if _make_environment_overrides_the_run(env):
        return True
    if not isinstance(env, dict):
        return False
    for name in ("MAKEFLAGS", "GNUMAKEFLAGS"):
        value = env.get(name)
        if not isinstance(value, str):
            continue
        if _make_flags_value_prevents_execution(value):
            return True
        if _make_flags_value_uncertifiable(value):
            return True
        if _make_flags_value_masks_failures(value):
            return True
    return False


def _make_command_local_values_defeat(
    words: list[str], command_index: int
) -> bool:
    """Whether the invocation's own prefix overrides defeat certification.

    A command-scoped ``MAKE``/``MAKEFILES`` assignment re-points the run
    exactly as its environment form does, and a ``MAKEFLAGS``/
    ``GNUMAKEFLAGS`` prefix carrying a non-executing mode,
    makefile-supplying option, failure mask, or variable assignment
    disqualifies the invocation.
    """
    command_local = _pip_prefix_env_values(words, command_index)
    for name in ("MAKE", "MAKEFILES"):
        if command_local.get(name):
            return True
    for name in ("MAKEFLAGS", "GNUMAKEFLAGS"):
        value = command_local.get(name)
        if value is None:
            continue
        if (
            _make_flags_value_prevents_execution(value)
            or _make_flags_value_uncertifiable(value)
            or _make_flags_value_masks_failures(value)
        ):
            return True
    return False


def _runs_make_docs_check(
    words: list[str],
    env: object = None,
    *,
    cwd_at_root: bool = True,
    inherited: dict[str, str] | None = None,
) -> bool:
    """Whether make's command and target positions invoke docs-check.

    The check also requires the invocation to actually execute in the
    repository: a non-executing mode in the effective environment
    (``MAKEFLAGS``), in a command-local assignment, or in the option list
    itself disqualifies it, and so does a step whose directory left the
    repository root (the make invocation then reads another Makefile).
    """
    if not cwd_at_root:
        return False
    if _make_environment_defeats_certification(env):
        return False
    if _make_environment_defeats_certification(inherited):
        return False
    command_index = _make_docs_check_index(words)
    if command_index is None:
        return False
    if _make_command_local_values_defeat(words, command_index):
        return False
    targets = _make_targets_after_options(words, command_index + 1)
    return targets is not None and "docs-check" in targets


def _virtualenv_root(path: str) -> str | None:
    """Recognize common activated-venv paths without treating system bin as one."""
    normalized = path.replace("\\", "/").rstrip("/")
    suffix = "/bin/activate"
    if normalized.endswith(suffix):
        return normalized[:-len(suffix)]
    parts = normalized.split("/")
    return next(
        (
            "/".join(parts[: index + 1])
            for index, part in enumerate(parts)
            if re.fullmatch(r"\.?venv\d*|virtualenvs?", part, re.IGNORECASE)
        ),
        None,
    )


def _virtualenv_markers_from_path(value: str) -> set[str]:
    """Marker set for PATH entries that name a virtual environment."""
    markers = set()
    for entry in value.split(":"):
        root = _virtualenv_root(entry)
        if root is not None:
            markers.add(VENV_MARKER_PREFIX + root)
    return markers


def _virtualenv_markers_from_word(word: str) -> set[str]:
    """Marker set for an inline VIRTUAL_ENV or PATH assignment."""
    key, separator, value = word.partition("=")
    if not separator:
        return set()
    if key == "VIRTUAL_ENV" and value:
        return {VENV_MARKER_PREFIX + value.rstrip("/")}
    return _virtualenv_markers_from_path(value) if key == "PATH" else set()


def _virtualenv_command_markers(segment: str) -> set[str]:
    """Virtualenv state established by one live shell command."""
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return set()
    markers = set()
    command_index = _skip_env_assignments(words, 0)
    for word in words[:command_index]:
        markers.update(_virtualenv_markers_from_word(word))
    if command_index == len(words):
        return markers
    command = words[command_index]
    if command_index + 1 < len(words) and command in (".", "source"):
        root = _virtualenv_root(words[command_index + 1])
        if root is not None:
            markers.add(VENV_MARKER_PREFIX + root)
    elif command == "export":
        for word in words[command_index + 1:]:
            markers.update(_virtualenv_markers_from_word(word))
    elif command == "env":
        env_index = _skip_env_assignments(words, command_index + 1)
        for word in words[command_index + 1:env_index]:
            markers.update(_virtualenv_markers_from_word(word))
    return markers


def _virtualenv_environment_markers(environment: object) -> set[str]:
    """Virtualenv state inherited from a workflow/job/step env mapping."""
    if not isinstance(environment, dict):
        return set()
    markers = set()
    virtual_env = environment.get("VIRTUAL_ENV")
    if isinstance(virtual_env, str) and virtual_env:
        markers.add(VENV_MARKER_PREFIX + virtual_env.rstrip("/"))
    path_value = environment.get("PATH")
    if isinstance(path_value, str):
        markers.update(_virtualenv_markers_from_path(path_value))
    return markers


def _is_virtualenv_deactivation(segment: str) -> bool:
    """Whether a live command deactivates the current shell's virtualenv."""
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return False
    command_index = _skip_env_assignments(words, 0)
    return command_index < len(words) and words[command_index] == "deactivate"


def _virtualenv_command_scope(
    segment: str,
) -> tuple[set[str], set[str]]:
    """Split command markers into persistent state and one-command scope."""
    command_markers = _virtualenv_command_markers(segment)
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return set(), set()
    command_index = _skip_env_assignments(words, 0)
    command = words[command_index] if command_index < len(words) else None
    if command is None or command in {"export", ".", "source"}:
        return command_markers, set()
    return set(), command_markers


def _virtualenv_markers(step: str | dict, through: int | None) -> set[str]:
    """Python-environment state established by a run-step prefix and its env.

    The command list must be the same foreground view ``_pip_first_steps``
    indexes: with the unfiltered list, a backgrounded segment shifts every
    subsequent index and the marker prefix would be read from the wrong
    command.
    """
    commands = _foreground_live_commands(step)
    visible = commands if through is None else commands[:through + 1]
    environment = step.get("env") if isinstance(step, dict) else None
    markers = _virtualenv_environment_markers(environment)
    command_scoped: set[str] = set()
    for segment in visible:
        if _is_virtualenv_deactivation(segment):
            markers = {
                marker
                for marker in markers
                if not marker.startswith(VENV_MARKER_PREFIX)
            }
            command_scoped.clear()
            continue
        persistent, command_scoped = _virtualenv_command_scope(segment)
        markers.update(persistent)
    if through is not None and through < len(commands) and visible:
        return markers | command_scoped
    return markers


def _pip_install_is_dry_run(words: list[str], command_index: int) -> bool:
    """Whether one pip invocation explicitly requests a dry run."""
    return any(
        word == "--dry-run" or word.startswith("--dry-run=")
        for word in words[command_index:]
    )


_PIP_DRY_RUN_FALSE_VALUES = frozenset(
    {"", "0", "false", "no", "off", "n", "f"}
)


def _pip_dry_run_value_active(value: object) -> bool:
    """Whether one ``PIP_DRY_RUN`` value enables pip's dry run.

    pip accepts the environment variable as the boolean form of
    ``--dry-run``; every value except a proven-off spelling enables it,
    and an unrecognized value fails closed (treated as enabled).
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in _PIP_DRY_RUN_FALSE_VALUES
    return True


def _pip_prefix_env_values(
    words: list[str], command_index: int
) -> dict[str, str]:
    """Extract the ``VAR=VALUE`` assignments that prefix one command."""
    values: dict[str, str] = {}
    for word in words[:command_index]:
        if not _ENV_ASSIGN_RE.match(word):
            continue
        name, _, value = word.partition("=")
        values[name] = value
    return values


def _pip_dry_run_active(
    words: list[str], command_index: int, env: object
) -> bool:
    """Whether a pip invocation runs as a dry run (flag, prefix, step env)."""
    if _pip_install_is_dry_run(words, command_index):
        return True
    prefix_values = _pip_prefix_env_values(words, command_index)
    if "PIP_DRY_RUN" in prefix_values:
        return _pip_dry_run_value_active(prefix_values["PIP_DRY_RUN"])
    if isinstance(env, dict) and "PIP_DRY_RUN" in env:
        return _pip_dry_run_value_active(env.get("PIP_DRY_RUN"))
    return False


def _step_retry_is_trusted(step: str | dict) -> bool:
    """Whether a local retry function in one run step invokes its target."""
    script = _step_script(step)
    if script is None:
        return True
    executable_source = _join_continuations(
        _strip_heredocs(_strip_shell_comments(script))
    )
    return _retry_runs_its_target(executable_source)


def _pip_step_commands(
    segment: str,
    env: object = None,
    cwd_at_root: bool = True,
    inherited: dict[str, str] | None = None,
) -> tuple[bool, bool]:
    """Whether one executable segment installs pinned deps or runs docs-check.

    ``env`` is the step's effective environment mapping (workflow → job →
    step precedence), so ``PIP_DRY_RUN`` set at any scope disqualifies the
    install the same way the ``--dry-run`` flag does.  ``cwd_at_root`` is
    the step's directory state at this segment: a make invocation in a
    foreign directory cannot certify the repository check.
    """
    words = _shell_words(segment)
    command_index = _skip_env_assignments(words, 0)
    command = " ".join(words[command_index:])
    install = bool(_PIP_REQUIREMENT_RE.search(command)) and not (
        _pip_dry_run_active(words, command_index, env)
    )
    return install, _runs_make_docs_check(
        words, env, cwd_at_root=cwd_at_root, inherited=inherited
    )


def _pip_prerequisite_position(
    segment: str,
    retry_trusted: bool,
    masked: set[str],
) -> bool:
    """Whether one live segment may satisfy a prerequisite.

    A segment whose failure a following ``||`` swallows (``cmd || true``)
    does not count: the chain succeeds even when the command fails, so its
    exit status proves nothing about the prerequisite.  A retry call whose
    wrapper cannot be trusted is skipped for the same reason.
    """
    if not retry_trusted and _is_retry_call(segment):
        return False
    return segment.strip() not in masked


def _make_relevant_export_values(segment: str) -> dict[str, str] | None:
    """The make-relevant variables a segment exports, when it exports any.

    ``export MAKEFLAGS=-n`` (and ``export MAKEFLAGS; MAKEFLAGS=-n``-style
    pairs are out of scope) changes every later invocation in the same
    shell: GNU Make inherits the value and only prints recipes (verified).
    The returned mapping holds the exported ``NAME=VALUE`` pairs whose
    names the make gate inspects; None means the segment is not an export
    the scan can attribute.
    """
    words = _parse_segment_words(segment)
    if not words:
        return None
    index = _skip_env_assignments(words, 0)
    if index >= len(words) or words[index] != "export":
        return None
    exported: dict[str, str] = {}
    for word in words[index + 1:]:
        if "=" not in word:
            # `export NAME` re-exports an existing value this scan does
            # not track; treat the segment as unattributable.
            return None
        name, _, value = word.partition("=")
        if name in _MAKE_ENV_NAMES:
            exported[name] = value
    return exported or None


def _segment_abandons_repo_root(segment: str) -> bool:
    """Whether one command segment changes away from the repository root.

    ``cd <operand>`` with an operand other than ``.``/``./`` moves the
    shell, so a later ``make`` in the same step reads a different
    Makefile (``cd "$RUNNER_TEMP/noop" && make -C . docs-check`` runs the
    planted chain - verified).  A bare ``cd`` goes home and an operand the
    scan cannot resolve may name any directory, so both count as leaving.
    """
    words = _parse_segment_words(segment)
    if not words:
        return False
    index = _skip_env_assignments(words, 0)
    if index >= len(words) or _shell_word_basename(words[index]) != "cd":
        return False
    if index + 1 >= len(words):
        return True
    return words[index + 1] not in (".", "./")


def _step_working_directory_keeps_root(step: str | dict) -> bool:
    """Whether a step's declared working directory stays at the repo root."""
    if not isinstance(step, dict):
        return True
    return step.get("working-directory") in (None, "", ".", "./")


def _pip_step_scan(step: str | dict) -> list[tuple[int, bool, bool]]:
    """Classify each live, unmasked segment of one step.

    Returns ``(segment_index, installs, checks_docs)`` for every command in
    the step's foreground view that may satisfy a prerequisite; masked and
    untrusted-retry segments are filtered out here so both consumers share
    one liveness model.  A command that leaves the repository root (a
    ``cd`` away, or a foreign ``working-directory``) disqualifies a later
    docs-check segment: the make invocation then reads another directory's
    Makefile.
    """
    retry_trusted = _step_retry_is_trusted(step)
    masked = _masked_command_segments_for_step(step)
    step_env = step.get("env") if isinstance(step, dict) else None
    cwd_at_root = _step_working_directory_keeps_root(step)
    # The shell's own `export` assignments accumulate as its segments run,
    # so a later make inherits them.
    exported: dict[str, str] = {}
    scanned: list[tuple[int, bool, bool]] = []
    for command_index, segment in enumerate(_foreground_live_commands(step)):
        if _segment_abandons_repo_root(segment):
            cwd_at_root = False
        segment_exports = _make_relevant_export_values(segment)
        if segment_exports is not None:
            exported.update(segment_exports)
        if not _pip_prerequisite_position(segment, retry_trusted, masked):
            continue
        installs, checks_docs = _pip_step_commands(
            segment, step_env, cwd_at_root, exported
        )
        scanned.append((command_index, installs, checks_docs))
    return scanned


def _pip_first_steps(
    steps: Sequence[str | dict],
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Positions of the first live requirements install and docs-check command."""
    install_at: tuple[int, int] | None = None
    docs_check_at: tuple[int, int] | None = None
    for step_index, step in enumerate(steps):
        for command_index, installs, checks_docs in _pip_step_scan(step):
            position = (step_index, command_index)
            if installs and install_at is None:
                install_at = position
            if checks_docs and docs_check_at is None:
                docs_check_at = position
    return install_at, docs_check_at


def _masked_prerequisite_kinds(steps: Sequence[str | dict]) -> set[str]:
    """Which prerequisites appear with a failure-swallowing operator.

    Used for the diagnostic text: when a prerequisite position comes back
    None, a masked form explains why (the requirement is present but its
    failure cannot reach the step), so the message points at the masking
    instead of asking for a missing command.
    """
    kinds: set[str] = set()
    for step in steps:
        masked = _masked_command_segments_for_step(step)
        if not masked:
            continue
        step_env = step.get("env") if isinstance(step, dict) else None
        for segment in _step_live_commands(step):
            if segment.strip() not in masked:
                continue
            installs, checks_docs = _pip_step_commands(segment, step_env)
            kinds.update(_masked_prerequisite_kind_for(installs, checks_docs))
    return kinds


def _masked_prerequisite_kind_for(
    installs: bool, checks_docs: bool
) -> set[str]:
    """The prerequisite kind names one masked segment represents."""
    kinds: set[str] = set()
    if installs:
        kinds.add("install")
    if checks_docs:
        kinds.add("docs-check")
    return kinds


def _python_deps_issue(run_scripts: str | Sequence[str | dict]) -> str | None:
    """Check pinned dependencies are installed in the environment docs-check uses."""
    pin_issue = _requirements_pin_issue(read_safe(RELEASE_REQUIREMENTS))
    if pin_issue is not None:
        return pin_issue
    steps = [run_scripts] if isinstance(run_scripts, str) else list(run_scripts)
    for step in steps:
        script = _step_script(step)
        if script is None:
            continue
        executable = _join_continuations(
            _strip_heredocs(_strip_shell_comments(script))
        )
        if shadow_issue := _shadowing_issue(executable):
            return shadow_issue
    install_at, docs_check_at = _pip_first_steps(steps)
    if install_at is None:
        if "install" in _masked_prerequisite_kinds(steps):
            return (
                "the release-gate job must not discard a failed requirements "
                "install: a failure-masking operator (for example ``|| true``) "
                "makes the step succeed even when the install fails, so the "
                "pinned dependencies are never proven present"
            )
        return (
            "the release-gate job must install the pinned Python release "
            "dependencies (from requirements-release.txt) so the docs-check "
            "chain can import jsonschema and PyYAML"
        )
    if docs_check_at is None:
        if "docs-check" in _masked_prerequisite_kinds(steps):
            return (
                "the release-gate job must not discard a failed docs-check: "
                "a failure-masking operator (for example ``|| true``) makes "
                "the step succeed even when the docs-check chain fails"
            )
        return (
            "the release-gate job must run its docs-check chain once the "
            "pinned Python dependencies are installed"
        )
    if install_at > docs_check_at:
        return (
            "the release-gate job must install requirements-release.txt "
            "before the docs-check step that imports it"
        )
    install_environment = _virtualenv_markers(
        steps[install_at[0]], install_at[1]
    )
    docs_environment = _virtualenv_markers(
        steps[docs_check_at[0]], docs_check_at[1]
    )
    if install_environment != docs_environment:
        return (
            "the release-gate job must install requirements into the "
            "Python environment used by docs-check; run-step shells do "
            "not share virtualenv activation"
        )
    return None


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

    issue = _workflow_naming_issue(wf_content)
    if issue is None:
        result.pass_(
            PKG_ARTIFACT_NAMING_WORKFLOW_GATE,
            "workflow constructs .deb/.rpm filenames with NGINX version",
        )
    else:
        result.fail(
            PKG_ARTIFACT_NAMING_WORKFLOW_GATE,
            "workflow must include NGINX version in BOTH package formats: "
            + issue,
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


def check_release_gate_python_deps(result: ValidationResult) -> None:
    """Validate the release-gate job installs its pinned Python deps.

    The docs-check chain imports jsonschema and PyYAML; a fresh runner
    only has what requirements-release.txt installs, so the install step
    must stay in the job and precede the check that needs it.
    """
    content = read_safe(RELEASE_PACKAGES_WORKFLOW)
    if not content:
        result.fail(
            PKG_RELEASE_GATE_PYTHON_DEPS_GATE,
            RELEASE_PACKAGES_WORKFLOW_MISSING,
        )
        return
    run_steps = _job_run_step_records(content, RELEASE_GATE_JOB_NAME)
    if run_steps is None:
        result.fail(
            PKG_RELEASE_GATE_PYTHON_DEPS_GATE,
            f"{RELEASE_GATE_JOB_NAME} job not found in release-packages.yml",
        )
        return
    issue = _python_deps_issue(run_steps)
    if issue is None:
        result.pass_(
            PKG_RELEASE_GATE_PYTHON_DEPS_GATE,
            "release gate installs the pinned Python release dependencies "
            "before the docs-check chain",
        )
    else:
        result.fail(PKG_RELEASE_GATE_PYTHON_DEPS_GATE, issue)


def print_report(result: ValidationResult) -> None:
    """Print a formatted validation report."""
    print("Fuzz & Packaging Infrastructure Validation Report")
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
    check_release_gate_toolchain(result)
    check_release_gate_python_deps(result)
    check_artifact_naming(result)
    check_install_docs(result)

    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
