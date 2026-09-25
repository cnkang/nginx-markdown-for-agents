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
        marker_prefix = "_" + marker_prefix
    for index, span in reversed(list(enumerate(direct))):
        head = heads.get(span)
        if head is None:
            continue
        marker = f"{marker_prefix}{index}"
        local_start = head[0] - start
        local_end = span[2] - start
        masked = masked[:local_start] + f"true {marker}" + masked[local_end:]
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


def _wrapper_command_step(words: list[str], index: int) -> int | None:
    """Advance past a leading command wrapper's own words; None when the
    wrapper must not be unwrapped (a `builtin`, a `command` lookup or
    invalid option spelling, or a sudo option outside the arity model).

    The wrapper's name is read after quote removal, as bash resolves it:
    a quoted ``'command'``/``"sudo"`` still runs that wrapper.
    """
    wrapper = _resolve_heredoc_word(words[index])[0]
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
            and _resolve_heredoc_word(words[index])[0] in _WRAPPER_COMMANDS
        ):
            step = _wrapper_command_step(words, index)
            if step is None:
                return index, True
            index = _skip_env_assignments(words, step)
        if index < len(words) and _resolve_heredoc_word(words[index])[0] == "env":
            index = _skip_env_prefix(words, index + 1)
    if index < len(words) and (
        _resolve_heredoc_word(words[index])[0] in _WRAPPER_COMMANDS
        or _resolve_heredoc_word(words[index])[0] == "env"
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
        and _resolve_heredoc_word(words[index])[0] == "retry"
        and words[index + 1].isdigit()
    ):
        index += 2
    index = _skip_bare_separators(words, index)
    index, refused = _unwrap_stacked_wrappers(words, index)
    if refused:
        return index, False
    if (
        index < len(words)
        and _resolve_heredoc_word(words[index])[0]
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
    r"(?:\"\$\{RUST_TOOLCHAIN\}\"|(?<![\w'])\$\{RUST_TOOLCHAIN\}(?![\w']))"
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
_RAW_INSTALL_RE = re.compile(r"^rustup\s+toolchain\s+install\b")
# Shell-equivalent spellings of installing the pinned release requirements:
# `python3 -m pip install -r requirements-release.txt`, `pip3 install
# --requirement=requirements-release.txt`, quoted paths, and a leading
# `sudo`/`retry N` wrapper (already peeled by the caller).
_PIP_REQUIREMENT_RE = re.compile(
    r"^(?:python3?\s+-m\s+pip|pip3?)"
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
) -> tuple[list[str], str | None, str]:
    """Scan one line for separators; returns (current, quote, separator)."""
    index = 0
    while index < len(line):
        if length := _separator_at(line, index, quote):
            _flush_segment(segments, current, separator)
            separator = line[index : index + length]
            current = []
            index += length
            continue
        new_quote, consumed = _scan_char(line, index, quote)
        current.append(line[index : index + consumed])
        quote = new_quote
        index += consumed
    return current, quote, separator


@functools.lru_cache(maxsize=64)
def _command_segments_with_separators_cached(
    script: str,
) -> tuple[tuple[str, str], ...]:
    """Immutable cached result for repeated scans of the same shell text."""
    segments: list[tuple[str, str]] = []
    current: list[str] = []
    quote: str | None = None
    separator = "\n"
    for line in script.splitlines():
        current, quote, separator = _scan_segment_line(
            line, current, quote, segments, separator)
        if quote is None:
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
        value = int(arg)
        if value == 0:
            return "zero"
        if value < 256:
            return "nonzero"
    return "unknown"


def _chain_prefix_words(segment: str) -> tuple[list[str], bool]:
    """Segment words with a leading ``!`` and ``time`` prefix removed.

    Returns the remaining words and whether a ``!`` negation came off: the
    negation inverts a failure the way a chain operand does, while ``time``
    is transparent (``time f`` runs ``f`` like a plain call).
    """
    words = segment.split()
    bang = False
    while words and words[0] == "!":
        bang = True
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
}


def _step_runs_shell(step: dict) -> bool:
    """Whether a workflow step's ``run`` executes in a shell on every path.

    A step the workflow gates with ``if`` may never run, and a step whose
    ``shell`` is not bash/sh feeds ``run`` to another interpreter, so neither
    can satisfy a provisioning check.  GitHub's default is
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


def _workflow_jobs(workflow_content: str) -> dict | None:
    """Parse a workflow and return its job mapping; None means unverifiable."""
    try:
        workflow = yaml.safe_load(workflow_content)
    except yaml.YAMLError:
        return None
    jobs = workflow.get("jobs") if isinstance(workflow, dict) else None
    return jobs if isinstance(jobs, dict) else None


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


def _job_run_step_records(
    workflow_content: str, job_name: str
) -> list[dict] | None:
    """Return executable run steps with shell and scoped environment metadata."""
    jobs = _workflow_jobs(workflow_content)
    if jobs is None or job_name not in jobs:
        return None
    workflow = yaml.safe_load(workflow_content)
    workflow_env = workflow.get("env") if isinstance(workflow, dict) else None
    job = jobs[job_name]
    if not isinstance(job, dict):
        return None
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    records: list[dict] = []
    for step in steps:
        if not _is_shell_run_step(step):
            continue
        scopes = _step_environment_scopes(
            workflow_env, job.get("env"), step.get("env")
        )
        records.append({
            "run": step["run"],
            "shell": step.get("shell"),
            "env": _merge_environment_scopes(scopes),
            "env_scopes": scopes,
        })
    return records



def _job_run_scripts(workflow_content: str, job_name: str) -> list[str] | None:
    """Return one executable run script per step, or None when absent."""
    records = _job_run_step_records(workflow_content, job_name)
    return None if records is None else [record["run"] for record in records]


def _all_job_run_scripts(workflow_content: str) -> list[str] | None:
    """Return one run script per workflow step, or None when the
    workflow cannot be parsed.

    Every run step feeds this scan -- including conditional and non-shell
    steps: a raw toolchain install must fail the gate wherever it could ever
    appear.
    """
    jobs = _workflow_jobs(workflow_content)
    if jobs is None:
        return None
    scripts: list[str] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        scripts.extend(
            step["run"]
            for step in job.get("steps") or []
            if isinstance(step, dict) and isinstance(step.get("run"), str)
        )
    return scripts


def _quote_cleaned_command(text: str) -> str:
    """The command with its first word's quotes removed.

    Bash resolves the unquoted name, so ``'rustup' toolchain install`` runs
    the real ``rustup``.
    """
    first, _, rest = text.partition(" ")
    cleaned = _resolve_heredoc_word(first)[0]
    return cleaned + (f" {rest}" if rest else "")


def _mentions_raw_install(text: str) -> bool:
    """Whether an unparsed payload names the forbidden install sequence."""
    return bool(re.search(r"(?:^|\s)rustup\s+toolchain\s+install(?:\s|$)", text))


def _xargs_command_index(words: list[str]) -> int | None:
    """Return xargs' command operand after its supported static options."""
    flags = {
        "-0", "--null", "-p", "--interactive", "-r", "--no-run-if-empty",
        "-t", "--verbose", "-x", "--exit",
    }
    valued = {
        "-d", "--delimiter", "-E", "--eof", "-I",
        "-L", "--max-lines", "-n", "--max-args", "-P",
        "--max-procs", "-s", "--max-chars", "-a", "--arg-file",
    }
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--":
            return index + 1
        if not word.startswith("-") or word == "-":
            return index
        if word in {"-i", "--replace"}:
            # GNU xargs treats the replacement string on these aliases as
            # optional; without an attached value the next word is command.
            index += 1
        elif word in valued:
            index += 2
        elif word.startswith("--") and "=" in word:
            index += 1
        elif len(word) > 2 and word[:2] in {
            "-d", "-E", "-I", "-i", "-L", "-n", "-P", "-s", "-a",
        }:
            index += 1
        elif word in flags:
            index += 1
        else:
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
    if word.startswith("--") and "=" in word:
        return index + 1
    return None


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
    if "$(" in payload or "`" in payload:
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


def _raw_install_in_segments(
    segments: list[str],
    depth: int,
    variables: dict[str, str | None] | None = None,
) -> bool:
    """Scan sequential shell segments with bounded literal-variable state."""
    local_variables = dict(variables or {})
    path_dependent = any(_segment_has_shell_control_flow(segment) for segment in segments)
    if path_dependent:
        local_variables.clear()
    for segment in segments:
        if _raw_install_in_segment(segment, depth + 1, local_variables):
            return True
        if not path_dependent:
            _update_static_shell_variables(segment, local_variables)
    return False


def _raw_install_in_script(
    script: str, depth: int = 0, variables: dict[str, str | None] | None = None
) -> bool:
    """Scan a script's ordered commands with conservative variable flow."""
    if _eval_has_dynamic_substitution(script):
        return True
    return _raw_install_in_segments(_command_segments(script), depth, variables)


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


def _python_launcher_payload_is_raw(
    call: ast.Call,
    target: str,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Inspect a statically known process payload or reject an opaque one."""
    if not call.args:
        return True
    if target == "asyncio.create_subprocess_exec":
        argv = _literal_python_argv(call.args)
        if argv is None:
            return True
        return _raw_install_from_words(argv, depth + 1, variables)
    try:
        payload = ast.literal_eval(call.args[0])
    except (ValueError, TypeError):
        return True
    return _python_payload_is_raw(
        payload, _python_shell_mode(target, call), depth, variables
    )


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
            imported_names.update({
                name: f"{node.module}.{name}"
                for name in _PYTHON_STAR_IMPORTS.get(node.module, ())
            })
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
) -> bool:
    target = _python_call_name(call.func, module_aliases, imported_names)
    if target in {"exec", "eval"}:
        return _python_eval_call_is_raw(call, depth, variables)
    if target in _PYTHON_SHELL_LAUNCHERS | _PYTHON_ARGV_LAUNCHERS:
        return _python_launcher_payload_is_raw(call, target, depth, variables)
    return target is not None and re.fullmatch(r"os\.(?:exec|spawn).*", target) is not None


def _python_calls_include_raw_install(
    tree: ast.AST,
    module_aliases: dict[str, str],
    imported_names: dict[str, str],
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _python_call_is_raw(
            node, module_aliases, imported_names, depth, variables
        ):
            return True
    return False


def _python_inline_raw_install(
    payload: str,
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Find raw installs launched by literal Python ``-c`` source."""
    if depth > 12:
        return True
    try:
        tree = ast.parse(payload)
    except SyntaxError:
        return _mentions_raw_install(payload)

    module_aliases, imported_names = _python_import_bindings(tree)
    return _python_calls_include_raw_install(
        tree, module_aliases, imported_names, depth, variables
    )


def _python_short_flag_step(
    flag: str, short_options: str, words: list[str], index: int, offset: int
) -> tuple[str | None, str | int | None, int] | None:
    """Classify a Python flag that selects source or consumes a value."""
    if flag in {"h", "V"}:
        return "terminal", None, 1
    if flag == "c":
        attached_source = short_options[offset + 1:]
        if attached_source:
            return "inline", attached_source, 1
        source_index = index + 1 if index + 1 < len(words) else None
        return "inline", source_index, 1
    if flag == "m":
        return "script", None, 1
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
    if option.startswith("--check-hash-based-pycs="):
        return None, None, 1
    return None, None, 1


def _python_command_option(
    words: list[str], index: int
) -> tuple[str | None, str | int | None, int]:
    """Inspect one Python argv word and return its mode or advance width."""
    option = words[index]
    if option == "-":
        return "stdin", index, 0
    if not option.startswith("-"):
        return "script", None, 0
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


def _raw_install_from_python_command(
    words: list[str],
    depth: int,
    variables: dict[str, str | None] | None,
) -> bool:
    """Analyze Python ``-c`` source without treating inert strings as commands."""
    if not _PYTHON_COMMAND.fullmatch(Path(words[0]).name):
        return False
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


def _raw_install_from_shell_wrapper(
    words: list[str], depth: int, variables: dict[str, str | None] | None
) -> bool:
    """Follow a modeled shell/wrapper command's static payload."""
    wrappers = {"bash", "sh", "dash", "zsh", "nohup", "sudo", "retry"}
    shell = Path(words[0]).name
    if shell not in wrappers:
        return False
    serialized = shlex.join(words)
    stripped = _strip_provision_wrappers(serialized)
    if stripped == serialized:
        has_static_heredoc = any(
            _is_shell_heredoc_redirect(_resolve_heredoc_word(word)[0])
            for word in words[1:]
        )
        if shell in _SHELL_COMMANDS_THAT_READ_STDIN and not has_static_heredoc:
            return _shell_arguments_read_stdin_script(shell, words[1:])
        return False
    return _raw_install_in_script(stripped, depth + 1, variables)


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


def _raw_install_from_command(
    words: list[str], depth: int, variables: dict[str, str | None] | None
) -> bool:
    """Check a parsed command word against inert, direct, and wrapper forms."""
    if words[0] in _INERT_SHELL_COMMANDS:
        return False
    if Path(words[0]).name == "rustup":
        return len(words) >= 3 and words[1:3] == ["toolchain", "install"]
    if _raw_install_from_python_command(words, depth, variables):
        return True
    return _raw_install_from_dispatcher(words, depth, variables) or _raw_install_from_wrapper(
        words, depth, variables
    )


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
    if words[0] in ("if", "then", "elif", "else", "!"):
        return _raw_install_from_words(words[1:], depth + 1, variables)
    assignment_result = _raw_install_from_assignment_prefix(
        words, depth, variables
    )
    if assignment_result is not None:
        return assignment_result
    return _raw_install_from_command(words, depth, variables)


def _raw_install_in_segment(
    segment: str,
    depth: int = 0,
    variables: dict[str, str | None] | None = None,
) -> bool:
    """Whether the segment runs a raw install, directly or through dispatchers."""
    if depth > 12:
        return _mentions_raw_install(segment) or "$" in segment
    commands = _command_segments(segment)
    if len(commands) > 1:
        return _raw_install_in_segments(commands, depth + 1, variables)
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        first = _resolve_heredoc_word(segment.split(maxsplit=1)[0])[0] \
            if segment.split() else ""
        if first in _INERT_SHELL_COMMANDS:
            return False
        return _mentions_raw_install(segment)
    return _raw_install_from_words(words, depth, variables)


_SHELL_COMMANDS_THAT_READ_STDIN = frozenset({"bash", "sh", "dash", "zsh"})


def _is_shell_heredoc_redirect(argument: str) -> bool:
    """Whether one token is a heredoc redirection passed to a shell."""
    return argument.startswith("<<") or re.fullmatch(r"\d+<<.*", argument)


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
    if argument in _SHELL_FLAG_OPTIONS:
        return (None, position + 1)
    return None


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
    """Inspect executable run commands and shell-input heredocs recursively."""
    pending = [script]
    scanned: set[str] = set()
    while pending:
        current = pending.pop()
        if current in scanned:
            continue
        if len(scanned) >= 64:
            return True
        scanned.add(current)
        uncommented = _strip_shell_comments(current)
        stripped = _join_continuations(_strip_heredocs(uncommented))
        if _raw_install_in_script(stripped):
            return True
        python_bodies, opaque_python_stdin = _python_stdin_heredoc_bodies(
            uncommented
        )
        if opaque_python_stdin or any(
            _python_inline_raw_install(body, 0, None) for body in python_bodies
        ):
            return True
        pending.extend(_shell_stdin_heredoc_bodies(uncommented))
    return False


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
    runs = _all_job_run_scripts(workflow_content)
    if runs is None:
        return (
            "release workflow YAML or its jobs mapping cannot be parsed, "
            "so raw Rust toolchain installs cannot be ruled out"
        )
    for run in runs:
        if _raw_install_in_run_script(run):
            return (
                "release workflows must provision Rust toolchains "
                "through the verified installer; found a raw or unresolved "
                "toolchain-install command"
            )
    return None


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
        and _resolve_heredoc_word(words[index])[0] == "retry"
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
    "pip",
    "pip3",
    "python",
    "python3",
    "return",
    "rustup",
    "true",
})


def _shadowing_issue(
    script: str, job_name: str = RELEASE_GATE_JOB_NAME
) -> str | None:
    """Reject functions that shadow commands the provisioning checks read.

    A function named like a shell builtin or like one of the commands the
    checks match changes what those words do, so provisioning text can no
    longer be trusted to mean what it says.  The guard spans every
    provisioning job: a no-op wrapper in either job defeats its own checks
    the same way.
    """
    if shadowed := _defined_function_names(script) & _SHADOWED_NAMES:
        return (
            f"the {job_name} job defines shell functions that shadow commands "
            "used by the provisioning checks ("
            + ", ".join(sorted(shadowed))
            + "); rename them so the checks trust the commands they read"
        )
    else:
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
        run_scripts = _job_run_scripts(workflow_content, job_name)
        if run_scripts is None:
            continue
        for step in run_scripts:
            if issue := _shadowing_issue(
                _strip_heredocs(_strip_shell_comments(step)), job_name
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


def _shell_initial_errexit(shell: object) -> bool:
    """Model whether a workflow shell starts with errexit enabled."""
    if shell is None or not isinstance(shell, str):
        return True
    words = shell.split()
    if not words or "{0}" not in words:
        # GitHub's built-in `bash`/`sh` forms add errexit by default.
        return True
    return any(
        _shell_option_enables_errexit(words, index)
        for index in range(1, len(words))
    )


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


def _shell_words(segment: str) -> list[str]:
    """Tokenize a parsed command and peel only real execution wrappers."""
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return []
    wrappers = {"bash", "sh", "dash", "zsh", "sudo", "env", "command", "retry"}
    if words and words[0] in wrappers:
        text = _strip_provision_wrappers(segment)
        if text != segment:
            try:
                return shlex.split(text, posix=True)
            except ValueError:
                return []
    return words


_MAKE_VALUE_OPTIONS = frozenset({
    "-C", "--directory", "-f", "--file", "--makefile", "-I",
    "--include-dir", "-j", "--jobs", "-O", "--output-sync", "-o",
    "--old-file", "-W", "--what-if", "--assume-new", "--eval",
})
_MAKE_NONEXECUTING_OPTIONS = frozenset({
    "-n", "--dry-run", "--just-print", "--recon",
    "-q", "--question", "-t", "--touch",
})
_MAKE_NONEXECUTING_LONG_OPTIONS = frozenset(
    option for option in _MAKE_NONEXECUTING_OPTIONS if option.startswith("--")
)
_MAKE_NONEXECUTING_SHORT_FLAGS = frozenset("nqt")


def _make_option_prevents_execution(word: str) -> bool:
    """Recognize dry-run/question/touch options, including short clusters."""
    option = word.split("=", 1)[0]
    if word in _MAKE_NONEXECUTING_OPTIONS or option in _MAKE_NONEXECUTING_LONG_OPTIONS:
        return True
    return (
        word.startswith("-")
        and not word.startswith("--")
        and any(flag in word[1:] for flag in _MAKE_NONEXECUTING_SHORT_FLAGS)
    )


def _make_targets_after_options(words: list[str], index: int) -> list[str] | None:
    """Collect make target words, or reject a command that cannot execute them."""
    targets: list[str] = []
    while index < len(words):
        word = words[index]
        if word == "--":
            return targets + words[index + 1:]
        if _make_option_prevents_execution(word):
            return None
        if word in _MAKE_VALUE_OPTIONS:
            if index + 1 >= len(words):
                return None
            index += 2
        elif word.startswith("--") and "=" in word:
            index += 1
        elif word.startswith("-"):
            index += 1
        else:
            targets.append(word)
            index += 1
    return targets


def _runs_make_docs_check(words: list[str]) -> bool:
    """Whether make's command and target positions invoke docs-check."""
    command_index = _skip_env_assignments(words, 0)
    if command_index >= len(words) or words[command_index] != "make":
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
    """Python-environment state established by a run-step prefix and its env."""
    commands = _step_live_commands(step)
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


def _step_retry_is_trusted(step: str | dict) -> bool:
    """Whether a local retry function in one run step invokes its target."""
    script = _step_script(step)
    if script is None:
        return True
    executable_source = _join_continuations(
        _strip_heredocs(_strip_shell_comments(script))
    )
    return _retry_runs_its_target(executable_source)


def _pip_step_commands(segment: str) -> tuple[bool, bool]:
    """Whether one executable segment installs pinned deps or runs docs-check."""
    words = _shell_words(segment)
    command_index = _skip_env_assignments(words, 0)
    command = " ".join(words[command_index:])
    install = bool(_PIP_REQUIREMENT_RE.search(command)) and not (
        _pip_install_is_dry_run(words, command_index)
    )
    return install, _runs_make_docs_check(words)


def _pip_first_steps(
    steps: Sequence[str | dict],
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Positions of the first live requirements install and docs-check command."""
    install_at: tuple[int, int] | None = None
    docs_check_at: tuple[int, int] | None = None
    for step_index, step in enumerate(steps):
        retry_trusted = _step_retry_is_trusted(step)
        for command_index, segment in enumerate(_step_live_commands(step)):
            if not retry_trusted and _is_retry_call(segment):
                continue
            position = (step_index, command_index)
            installs, checks_docs = _pip_step_commands(segment)
            if installs and install_at is None:
                install_at = position
            if checks_docs and docs_check_at is None:
                docs_check_at = position
    return install_at, docs_check_at


def _python_deps_issue(run_scripts: str | Sequence[str | dict]) -> str | None:
    """Check pinned dependencies are installed in the environment docs-check uses."""
    pin_issue = _requirements_pin_issue(read_safe(RELEASE_REQUIREMENTS))
    if pin_issue is not None:
        return pin_issue
    steps = [run_scripts] if isinstance(run_scripts, str) else list(run_scripts)
    install_at, docs_check_at = _pip_first_steps(steps)
    if install_at is None:
        return (
            "the release-gate job must install the pinned Python release "
            "dependencies (from requirements-release.txt) so the docs-check "
            "chain can import jsonschema and PyYAML"
        )
    if docs_check_at is None:
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
