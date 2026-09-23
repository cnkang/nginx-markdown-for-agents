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
        result.fail("pkg:release-workflow", "release-packages.yml not found")
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
            merged = merged[:-1] + " " + stripped.lstrip()
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
    if char == "'":
        return None, 1, None, False
    return "ansi-c", 1, char, False


def _walk_removal_escape(
    raw: str, index: int, quote: str | None
) -> tuple[str | None, int, str | None, bool]:
    """Remove a backslash when it quotes a shell-special character."""
    char = raw[index]
    if index + 1 < len(raw) and (
        quote is None or raw[index + 1] in ("$", "`", '"', chr(92))
    ):
        return quote, 2, raw[index + 1], False
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
    """Resolve a heredoc delimiter word; return (delimiter, dynamic).

    Quote removal models the shell.  When any part of the word is quoted
    (quotes or backslashes), the shell performs quote removal only and never
    expands it, so the delimiter is static; a fully unquoted word with a
    live ``$`` or backtick depends on the environment at runtime.
    """
    # ANSI-C escapes have more spellings than this quote-removal model
    # resolves.  Treat an escaped delimiter as dynamic/unverifiable instead
    # of guessing a terminator and exposing heredoc body text as commands.
    if raw.startswith("$'") and "\\" in raw:
        return raw[2:-1] if raw.endswith("'") else raw[2:], True
    resolved: list[str] = []
    dynamic = False
    quoted = False
    quote: str | None = None
    index = 0
    while index < len(raw):
        if raw[index] in ("'", '"', "\\"):
            quoted = True
        quote, consumed, keep, live = _walk_removal_char(raw, index, quote)
        if keep is not None:
            if live and quote != "'":
                dynamic = True
            resolved.append(keep)
        index += consumed
    return "".join(resolved), dynamic and not quoted


def _heredoc_marker_at(
    line: str, index: int
) -> tuple[str, bool, bool, int] | None:
    """Parse a heredoc marker at ``index``; return (word, tab, dynamic, end).

    ``None`` when the position does not open a heredoc (including the
    ``<<<`` herestring form).  Quoted delimiters are literal; a plain word
    resolves through quote removal, so escaped spaces stay part of it and
    escaped expansion characters do not make it dynamic.
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
    plain (any word without expansion characters).  The terminator must
    match the delimiter exactly -- ``<<-`` additionally strips leading tabs
    -- mirroring shell semantics, so a padded line never ends the body
    early.  Bodies opened with a dynamic delimiter (``<<$WORD``) cannot be
    delimited statically and are left intact; ``_dynamic_heredoc_markers``
    reports them so the provisioning checks can reject the script.
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
        line = line[:-1] + " " + lines[index].lstrip()
        index += 1
    return line, index


def _dynamic_heredoc_markers(script: str) -> list[str]:
    """Return the delimiters of heredocs whose word expands at runtime.

    The shell expands a plain delimiter word containing ``$`` or backticks,
    so the terminator depends on the environment; the provisioning checks
    treat such scripts as unverifiable instead of guessing.
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
        return match.group(1)
    keyword = re.search(r"function\s+([A-Za-z_](?a:\w)*)\s*$", prefix)
    return keyword.group(1) if keyword is not None else None


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
    return list(spans), state


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
    if quote is not None:
        return spans, "quote"
    return spans, ""


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
    if re.search(r"(?<![A-Za-z0-9_])(?:@|\?|\+|\*|!)\(", code):
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


def _masked_region(script: str, region: tuple[int, int] | None) -> str:
    """Region text with nested bodies and definition heads replaced by spaces."""
    if region is None:
        text = script
        bodies = _function_body_spans(script)
    else:
        text = script[region[0] : region[1]]
        bodies = [
            (name, lo - region[0], hi - region[0])
            for name, lo, hi in _function_body_spans(script)
            if lo > region[0] and hi < region[1]
        ]
    chars = list(text)
    for lo, hi in [
        (lo, hi) for _, lo, hi in bodies
    ] + _head_spans(text, bodies):
        for position in range(lo, min(hi, len(chars))):
            chars[position] = " "
    return "".join(chars)


def _calls_in_region(
    script: str, defined: set[str], region: tuple[int, int] | None
) -> set[str]:
    """Calls made by the live code of a region.

    Nested function bodies and definition heads are masked, then the same
    reachability rules drop dead branches, so a call behind ``if false``
    or inside quoted text never activates its target.
    """
    masked = _masked_region(script, region)
    live = _live_command_segments(masked)
    return _called_function_names("\n".join(live), defined)


def _effective_body_spans(
    script: str,
) -> tuple[list[tuple[str, int, int]], list[tuple[str, int, int]]]:
    """Split body spans into the effective and the superseded ones.

    A redefined function runs its last definition, so earlier bodies of the
    same name can never execute on a later call: they are superseded.
    """
    spans = _function_body_spans(script)
    last: dict[str, int] = {}
    for position, (name, _lo, _hi) in enumerate(spans):
        last[name] = position
    effective: list[tuple[str, int, int]] = []
    superseded: list[tuple[str, int, int]] = []
    for position, span in enumerate(spans):
        target = effective if last[span[0]] == position else superseded
        target.append(span)
    return effective, superseded


def _live_function_names(script: str, defined: set[str]) -> set[str]:
    """Functions whose bodies actually run.

    Reachability is transitive: a call inside the body of a function nobody
    calls never executes, so it cannot make its target live, and only the
    last definition of a name can be live at all.  A fixpoint over the call
    graph keeps both directions honest.
    """
    if not defined:
        return set()
    effective, _superseded = _effective_body_spans(script)
    spans_by_name: dict[str, list[tuple[int, int]]] = {}
    for name, lo, hi in effective:
        spans_by_name.setdefault(name, []).append((lo, hi))
    live = _calls_in_region(script, defined, None)
    frontier = list(live)
    while frontier:
        name = frontier.pop()
        for region in spans_by_name.get(name, []):
            for found in _calls_in_region(script, defined, region):
                if found not in live:
                    live.add(found)
                    frontier.append(found)
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
        if _branch_keyword_step(branches, segment, condition):
            if _marker_return_status(
                segment, separator, previous, branches
            ) is not None:
                cut = found
                break
            previous = None
            continue
        if not _region_runs(branches):
            continue
        if _chain_skips(separator, previous):
            continue
        if keyword == "return":
            cut = found
            break
        previous = _segment_literal(segment)
    # The leading newline keeps the body's first command off the brace.
    return "\n" + body[:cut]


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
    live = _live_function_names(script, defined)
    chars = list(script)
    effective, superseded = _effective_body_spans(script)
    for name, lo, hi in effective:
        width = hi - lo
        if name in live:
            trimmed = _trim_body_after_terminator(script[lo:hi])[:width]
            replacement = list(trimmed) + [" "] * (width - len(trimmed))
        else:
            replacement = [" "] * width
        chars[lo:hi] = replacement
    for _name, lo, hi in superseded:
        chars[lo:hi] = [" "] * (hi - lo)
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
    "bash", "dash", "env", "eval", "sh",
})
# `builtin` only runs shell builtins: a known literal builtin keeps its
# literal, and a command that is surely not a builtin makes it fail.
_BUILTIN_LITERAL_WORDS = {":": True, "true": True, "false": False}
_NON_BUILTIN_COMMANDS = frozenset({
    "bash", "sh", "dash", "python", "python3", "rustup",
    "env", "command", "exec", "nohup", "sudo",
})
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_](?a:\w)*=")

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
        if option in _ENV_LONG_VALUE_OPTIONS and value:
            return index + 1
        return None
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
    if probe + span > len(words):
        return None
    return probe + span, command_mode


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
        if command_mode and probe + 1 < len(words):
            return probe + 1
        return -1
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
        if lookup:
            return None
        return len(words) - len(rest)
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
        and _resolve_heredoc_word(words[index])[0] in ("bash", "sh", "dash")
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
            carried = _possible_marker_carry(
                segment, separator, previous, branches
            )
            if carried:
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
    r"(?:-r|--requirement)(?:\s+|=)[\"']?requirements-release\.txt[\"']?\b"
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
    if quote in ("'", "ansi-c"):
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
    code = _masked_quotes(script[body_start:index])
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
        if char == "&":
            if (index > 0 and line[index - 1] in "<>") or line.startswith(
                "&>", index
            ):
                # In a redirection such as ``2>&1`` or ``&>file``, the
                # ampersand is part of the redirection token, not a command
                # separator.
                return 0
        return 2 if line.startswith(char * 2, index) else 1
    return 1 if char in "()`" else 0


def _flush_segment(
    segments: list[tuple[str, str]], current: list[str], separator: str
) -> None:
    """Append the finished segment with its separator, when non-empty."""
    segment = "".join(current).strip()
    if segment:
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
        length = _separator_at(line, index, quote)
        if length:
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
        if lookup:
            return None
        return rest
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
    words = _peel_execution_wrappers(segment.split())
    if not words:
        return None
    first = _resolve_heredoc_word(words[0])[0]
    if first == "--":
        return False
    if first == "builtin":
        rest = words[1:]
        if rest and rest[0] == "--":
            rest = rest[1:]
        if not rest:
            return None
        name = _resolve_heredoc_word(rest[0])[0]
        if name in _BUILTIN_LITERAL_WORDS:
            return _BUILTIN_LITERAL_WORDS[name]
        if name in _NON_BUILTIN_COMMANDS:
            return False
        return None
    if first in (":", "true"):
        return True
    if first == "false" and all(_is_redirection_word(w) for w in words[1:]):
        return False
    return None


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
    if keyword not in ("if", "elif"):
        return None
    return _condition_tristate(pairs, index)


def _combine_tristate(operator: str, left: bool | None, right: bool | None) -> bool | None:
    """Fold two literal parts under ``&&``/``||`` (None = unevaluated)."""
    if operator == "&&":
        if left is False or right is False:
            return False
        if left is None or right is None:
            return None
        return True
    if left is True or right is True:
        return True
    if left is None or right is None:
        return None
    return False


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
    if rest == ["false"]:
        return False
    return None


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
    words = segment.split()
    if not words or words[0] != "set":
        return None
    return _set_flags_state(words[1:])


def _flags_word_state(word: str) -> bool | None:
    """The errexit state one plain ``set`` word carries (None = no signal).

    A bare ``errexit`` enables it, an ``e`` in a ``+…`` cluster disables,
    an ``e`` in a ``-…`` cluster enables.
    """
    if word == "errexit":
        return True
    if word.startswith("+") and "e" in word[1:]:
        return False
    if word.startswith("-") and "e" in word[1:]:
        return True
    return None


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
    return separator in ("&&", "||") and (
        previous is None or _segment_unreachable(separator, previous)
    )


def _branch_chain_state(condition: bool | None) -> int:
    """The chain state after a branch whose condition evaluates so."""
    if condition is True:
        return _OPEN_CHAIN
    if condition is None:
        return _UNKNOWN_CHAIN
    return _CLEAR_CHAIN


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
    elif keyword in ("if", "elif"):
        branches[-1] = (condition is not False, chain)
    elif keyword == "else":
        previous_chain = chain if prior_chain is None else prior_chain
        branches[-1] = (previous_chain != _OPEN_CHAIN, _OPEN_CHAIN)


def _body_marker_command(segment: str) -> str:
    """The command a ``then``/``do``/``else`` marker carries on its own
    segment (``then return 1``); ``""`` when the marker stands alone."""
    words = segment.split()
    if len(words) < 2:
        return ""
    return " ".join(words[1:])


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
    if re.fullmatch(r"[0-9]+", arg):
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
    return separator in ("(", "`", "|", "&") or following in ("|", "&")


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
    if following in ("|", "&", ")"):
        return None, None
    if following in ("&&", "||"):
        return None, False
    return "fail", None


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
    if kind == "bare" and previous is False:
        return "fail", None
    return "ok", None


def _failing_segment_state(
    separator: str,
    following: str,
) -> tuple[str | None, bool | None]:
    """Verdict/status for a segment whose literal value is False."""
    if following in ("&&", "||"):
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
    words, bang = _chain_prefix_words(segment)
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
    if bang and value is not None:
        value = not value
    if value is False:
        return _failing_segment_state(separator, following)
    return None, value


def _verdict_ends(verdict: str | None, errexit: bool) -> bool:
    """Whether a segment verdict ends the shell under the errexit state."""
    if verdict == "exit":
        return True
    return verdict == "fail" and errexit


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
    if keyword in ("then", "do", "else"):
        if not _region_runs(branches) or _chain_skips(separator, previous):
            return None, None
        carried = _body_marker_command(segment)
        if not carried:
            return None, None
        return _live_segment_state(
            carried, ";", following, previous, failing, exiting
        )
    if keyword in ("if", "elif", "while", "until"):
        if _condition_exit(segment, exiting):
            return "exit", None
    return None, None


def _body_can_succeed(body: str) -> bool:
    """Whether a body can possibly return success.

    A reachable ``return 0`` or bare ``return`` (which propagates a status
    that can be zero) means the function can succeed, so it must never
    carry the always-failing label; the possible-path walk keeps loops and
    unevaluated branches alive for exactly this check.
    """
    for segment in _possibly_reached_segments(body):
        if _return_kind(segment) in ("zero", "bare", "unknown"):
            return True
    return False


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
    if verdict == "fail":
        return (not can_succeed), False
    return None


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
    if previous is False:
        return (not can_succeed), False
    return False, False


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
        marker_is_live = bool(carried) and _region_runs(branches) and not (
            _segment_unreachable(separator, previous)
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
            live.append(carried if carried else segment)
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
        if word in {"-o", "-O"} and index + 1 < len(words):
            if words[index + 1] == "noexec":
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
        effective.update(scope)
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
    if records is None:
        return None
    return [record["run"] for record in records]


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
        for step in job.get("steps") or []:
            if isinstance(step, dict) and isinstance(step.get("run"), str):
                scripts.append(step["run"])
    return scripts


def _quote_cleaned_command(text: str) -> str:
    """The command with its first word's quotes removed.

    Bash resolves the unquoted name, so ``'rustup' toolchain install`` runs
    the real ``rustup``.
    """
    first, _, rest = text.partition(" ")
    cleaned = _resolve_heredoc_word(first)[0]
    return cleaned + ((" " + rest) if rest else "")


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
        "-d", "--delimiter", "-E", "--eof", "-I", "--replace",
        "-i", "-L", "--max-lines", "-n", "--max-args", "-P",
        "--max-procs", "-s", "--max-chars", "-a", "--arg-file",
    }
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--":
            return index + 1
        if not word.startswith("-") or word == "-":
            return index
        if word in valued:
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


def _timeout_command_index(words: list[str]) -> int | None:
    """Return timeout's command operand after its duration and options."""
    value_options = {"-k", "--kill-after", "-s", "--signal"}
    flag_options = {"--foreground", "--preserve-status", "-v", "--verbose"}
    index = 1
    while index < len(words):
        word = words[index]
        if word == "--":
            index += 1
            break
        if word in flag_options:
            index += 1
            continue
        if word in value_options:
            index += 2
            continue
        if word.startswith("--") and "=" in word:
            index += 1
            continue
        if word.startswith("-") and word != "-":
            return None
        # timeout requires one duration before its command.
        return index + 1
    return index


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
        if end == len(words) or end == start:
            return None
        commands.append(words[start:end])
        index = end + 1
    return commands


def _raw_install_from_dispatcher(words: list[str], depth: int) -> bool:
    """Follow indirect command launchers with statically locatable operands."""
    if words[0] == "xargs":
        command_index = _xargs_command_index(words)
        if command_index is None:
            return _mentions_raw_install(" ".join(words))
        return _raw_install_from_words(words[command_index:], depth + 1)
    if words[0] == "timeout":
        command_index = _timeout_command_index(words)
        if command_index is None or command_index >= len(words):
            return _mentions_raw_install(" ".join(words))
        return _raw_install_from_words(words[command_index:], depth + 1)
    if words[0] == "find":
        commands = _find_exec_argv(words)
        if commands is None:
            return _mentions_raw_install(" ".join(words))
        return any(_raw_install_from_words(command, depth + 1)
                   for command in commands)
    return False


def _raw_install_from_wrapper(words: list[str], depth: int) -> bool:
    """Follow shell and command wrappers while preserving quoted word boundaries."""
    if words[0] == "exec":
        return _raw_install_from_words(words[1:], depth + 1)
    if words[0] == "eval":
        # Bash eval joins its expanded arguments with spaces before parsing
        # them; shlex.join would preserve argv boundaries Bash discards.
        return _raw_install_in_segment(" ".join(words[1:]), depth + 1)
    if words[0] == "env":
        command_index = _skip_env_prefix(words, 1)
        return _raw_install_from_words(words[command_index:], depth + 1)
    if words[0] == "command":
        operands, lookup = _command_operand(words[1:])
        return False if lookup else _raw_install_from_words(operands, depth + 1)
    wrappers = {"bash", "sh", "dash", "zsh", "sudo", "retry"}
    if words[0] in wrappers:
        serialized = shlex.join(words)
        stripped = _strip_provision_wrappers(serialized)
        if stripped != serialized:
            return any(
                _raw_install_in_segment(inner, depth + 1)
                for inner in _command_segments(stripped)
            )
    return False


def _raw_install_from_words(words: list[str], depth: int = 0) -> bool:
    """Follow a bounded set of command-position dispatchers to their target."""
    if not words:
        return False
    if depth > 12:
        return _mentions_raw_install(" ".join(words))
    if words[0] in ("if", "then", "elif", "else", "!"):
        return _raw_install_from_words(words[1:], depth + 1)
    command_index = _skip_env_assignments(words, 0)
    if command_index:
        return _raw_install_from_words(words[command_index:], depth + 1)
    if words[0] in {"echo", "printf", "test", "[", "true", "false", ":"}:
        return False
    if words[0] == "rustup":
        return len(words) >= 3 and words[1:3] == ["toolchain", "install"]
    return _raw_install_from_dispatcher(words, depth) or _raw_install_from_wrapper(
        words, depth
    )


def _raw_install_in_segment(segment: str, depth: int = 0) -> bool:
    """Whether the segment runs a raw install, directly or through dispatchers."""
    if depth > 12:
        return _mentions_raw_install(segment)
    commands = _command_segments(segment)
    if len(commands) > 1:
        return any(
            _raw_install_in_segment(command, depth + 1) for command in commands
        )
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        first = _resolve_heredoc_word(segment.split(maxsplit=1)[0])[0] \
            if segment.split() else ""
        if first in {"echo", "printf", "test", "[", "true", "false", ":"}:
            return False
        return _mentions_raw_install(segment)
    return _raw_install_from_words(words, depth)


def _raw_toolchain_install_issue(workflow_content: str) -> str | None:
    """Reject raw ``rustup toolchain install`` anywhere in the workflow.

    Release workflows must provision toolchains through the verified
    installer, which validates the downloaded rustup-init checksum before
    execution.  Every run line is scanned -- including conditional steps
    and function bodies -- because a raw install must fail the gate
    wherever it could ever appear.  The scan models static shell command
    lines; a payload assembled at runtime (a ``python3 -c`` program, an
    expanded variable) is outside its scope and such a form never counts
    as provisioning either.
    """
    runs = _all_job_run_scripts(workflow_content)
    if runs is None:
        return (
            "release workflow YAML or its jobs mapping cannot be parsed, "
            "so raw Rust toolchain installs cannot be ruled out"
        )
    for run in runs:
        stripped = _join_continuations(
            _strip_heredocs(_strip_shell_comments(run)))
        for segment in _command_segments(stripped):
            if _raw_install_in_segment(segment):
                return (
                    "release workflows must provision Rust toolchains "
                    "through the verified installer; found a raw `rustup "
                    "toolchain install` command"
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
    shadowed = _defined_function_names(script) & _SHADOWED_NAMES
    if not shadowed:
        return None
    return (
        f"the {job_name} job defines shell functions that shadow commands "
        "used by the provisioning checks ("
        + ", ".join(sorted(shadowed))
        + "); rename them so the checks trust the commands they read"
    )


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
            issue = _shadowing_issue(
                _strip_heredocs(_strip_shell_comments(step)), job_name
            )
            if issue:
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
    structure_issue = _unclosed_structure_issue(executable_source)
    if structure_issue:
        return [], structure_issue
    shadow_issue = _shadowing_issue(executable_source)
    if shadow_issue:
        return [], shadow_issue
    executable = _strip_function_bodies(executable_source)
    if _dynamic_heredoc_markers(executable):
        return [], (
            "the release-gate job opens a heredoc with a runtime-expanded "
            "delimiter, so its toolchain provisioning cannot be verified "
            "statically; use a plain delimiter"
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
            "release-packages.yml not found",
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
    for name, pattern in (
        ("jsonschema[format]", r"^jsonschema\[format\]=="),
        ("PyYAML", r"^PyYAML=="),
    ):
        if not re.search(pattern + r"[^#\s]", requirements, re.M):
            return (
                f"requirements-release.txt must pin {name} to a version "
                "(a bare name or a lone separator is not a pin)"
            )
    return None


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
    for index, part in enumerate(parts):
        if re.fullmatch(r"\.?venv\d*|virtualenvs?", part, re.IGNORECASE):
            return "/".join(parts[:index + 1])
    return None


def _virtualenv_markers_from_path(value: str) -> set[str]:
    """Marker set for PATH entries that name a virtual environment."""
    markers = set()
    for entry in value.split(":"):
        root = _virtualenv_root(entry + "/activate")
        if root is not None:
            markers.add("venv:" + root)
    return markers


def _virtualenv_markers_from_word(word: str) -> set[str]:
    """Marker set for an inline VIRTUAL_ENV or PATH assignment."""
    key, separator, value = word.partition("=")
    if not separator:
        return set()
    if key == "VIRTUAL_ENV" and value:
        return {"venv:" + value.rstrip("/")}
    if key == "PATH":
        return _virtualenv_markers_from_path(value)
    return set()


def _virtualenv_command_markers(segment: str) -> set[str]:
    """Virtualenv state established by one live shell command."""
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        return set()
    markers = set()
    if len(words) > 1 and words[0] in (".", "source"):
        root = _virtualenv_root(words[1])
        if root is not None:
            markers.add("venv:" + root)
    for word in words:
        markers.update(_virtualenv_markers_from_word(word))
    return markers


def _virtualenv_environment_markers(environment: object) -> set[str]:
    """Virtualenv state inherited from a workflow/job/step env mapping."""
    if not isinstance(environment, dict):
        return set()
    markers = set()
    virtual_env = environment.get("VIRTUAL_ENV")
    if isinstance(virtual_env, str) and virtual_env:
        markers.add("venv:" + virtual_env.rstrip("/"))
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


def _virtualenv_markers(step: str | dict, through: int | None) -> set[str]:
    """Python-environment state established by a run-step prefix and its env."""
    commands = _step_live_commands(step)
    visible = commands if through is None else commands[:through + 1]
    environment = step.get("env") if isinstance(step, dict) else None
    markers = _virtualenv_environment_markers(environment)
    for segment in visible:
        if _is_virtualenv_deactivation(segment):
            markers = {marker for marker in markers if not marker.startswith("venv:")}
        else:
            markers.update(_virtualenv_command_markers(segment))
    return markers


def _pip_first_steps(
    steps: Sequence[str | dict],
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Positions of the first live requirements install and docs-check command."""
    install_at: tuple[int, int] | None = None
    docs_check_at: tuple[int, int] | None = None
    for step_index, step in enumerate(steps):
        for command_index, segment in enumerate(_step_live_commands(step)):
            words = _shell_words(segment)
            position = (step_index, command_index)
            command_index_after_env = _skip_env_assignments(words, 0)
            command = " ".join(words[command_index_after_env:])
            if _PIP_REQUIREMENT_RE.search(command) and install_at is None:
                install_at = position
            if _runs_make_docs_check(words) and docs_check_at is None:
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
            "release-packages.yml not found",
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
