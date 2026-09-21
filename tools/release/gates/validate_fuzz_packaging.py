#!/usr/bin/env python3
"""
Fuzz and packaging infrastructure validator for the release gates.

Validates the 12-item fuzz and packaging infrastructure requirements:

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

Exit codes:
  0 - All checks passed
  1 - One or more checks failed

Security: All file reads use Path.resolve() within PROJECT_ROOT.
No user-supplied patterns are compiled at runtime.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

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
DOCS_COMPATIBILITY_GATE = "docs:compatibility"

# The release-gate job runs this command; the gate scopes its toolchain
# expectation to the job that actually carries the command so a future job
# split must move the provisioning with it.
RELEASE_GATE_JOB_NAME = "release-gate"
RELEASE_GATE_RUSTFMT_CONSUMER = "tools/reason-codegen/generate.py --check"

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
    """Read file content safely, returning empty string if missing."""
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return ""
    return resolved.read_text(encoding="utf-8") if resolved.is_file() else ""


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


def _scan_char(line: str, index: int, quote: str | None) -> tuple[str | None, int]:
    """Advance one quote or escape unit; return (new quote, chars consumed).

    Outside quotes a backslash escapes the next character, so an escaped
    quote is a literal quote rather than a quote opener.  Inside double
    quotes a backslash escapes ``"``, ``\\``, ``$`` and backticks; inside
    single quotes backslashes are literal.  A quote that opens on one line
    stays open across lines.
    """
    char = line[index]
    if quote == "'":
        return (None if char == "'" else "'"), 1
    if quote == '"':
        if char == "\\" and index + 1 < len(line) and line[index + 1] in '"\\`$':
            return '"', 2
        return (None if char == '"' else '"'), 1
    if char == "\\" and index + 1 < len(line):
        return None, 2
    if char in ("'", '"'):
        return char, 1
    return None, 1


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
        if quote is None and char == "#" and (index == 0 or line[index - 1] in " \t"):
            return "".join(kept), None
        new_quote, consumed = _scan_char(line, index, quote)
        kept.append(line[index : index + consumed])
        quote = new_quote
        index += consumed
    return "".join(kept), quote


def _strip_shell_comments(script: str) -> str:
    """Remove shell comments from run scripts, respecting quotes and escapes.

    A leading or whitespace-preceded ``#`` starts a comment; ``#`` inside a
    quoted string is literal.  Quote state carries across lines, so the
    content of a string that spans lines stays string data for the command
    checks; the text after the closing quote on that line is executable again
    and stays in the output.
    """
    kept: list[str] = []
    quote: str | None = None
    for line in script.splitlines():
        stripped, quote = _strip_comment_from_line(line, quote)
        kept.append(stripped)
    return "\n".join(kept)


def _join_continuations(script: str) -> str:
    """Join backslash-continued lines so one command stays one logical line."""
    return re.sub(r"\\\n[ \t]*", " ", script)


_HEREDOC_MARKER_RE = re.compile(
    r"<<-?[ \t]*(?:(['\"])([^'\"]*)\1|((?:\\.|[^ \t;|&()<>])+))"
)


def _walk_removal_char(
    raw: str, index: int, quote: str | None
) -> tuple[str | None, int, str | None, bool]:
    """One quote-removal step; returns (quote, consumed, keep, live).

    Backslashes escape the shell's special characters (the escaped character
    stays literal, never expansion), quotes toggle string state, and
    everything else passes through as data.  ``live`` marks an unescaped
    ``$`` or backtick: outside single quotes the shell expands it.
    """
    char = raw[index]
    if char == "\\" and index + 1 < len(raw) and (
        quote is None or raw[index + 1] in "$`\"\\"
    ):
        return quote, 2, raw[index + 1], False
    if char in ("'", '"'):
        if quote is None:
            return char, 1, None, False
        if quote == char:
            return None, 1, None, False
    return quote, 1, char, char in ("$", "`")


def _resolve_heredoc_word(raw: str) -> tuple[str, bool]:
    """Resolve a heredoc delimiter word; return (delimiter, dynamic).

    Quote removal models the shell: single quotes keep their content
    literal, double quotes still expand ``$`` and backticks, and a
    backslash escapes the next character everywhere outside single quotes.
    A live expansion character makes the word dynamic, because the shell
    then resolves the terminator at runtime.
    """
    resolved: list[str] = []
    dynamic = False
    quote: str | None = None
    index = 0
    while index < len(raw):
        quote, consumed, keep, live = _walk_removal_char(raw, index, quote)
        if keep is not None:
            if live and quote != "'":
                dynamic = True
            resolved.append(keep)
        index += consumed
    return "".join(resolved), dynamic


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
        word = match.group(2)
        dynamic = match.group(1) == '"' and ("$" in word or "`" in word)
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
        line, quote, index = _join_command_line(lines, line, index, quote)
        kept.append(line)
        quote, markers = _scan_line_for_heredocs(line, quote)
        pending.extend(
            (marker[0], marker[1]) for marker in markers if not marker[2])
    return "\n".join(kept)


def _line_continues(line: str, quote: str | None) -> bool:
    """Whether a command line ends with an unescaped backslash.

    Single-quoted strings keep backslashes literal, so a line inside one
    never continues; elsewhere an odd run of trailing backslashes escapes
    the newline.
    """
    if quote == "'":
        return False
    trailing = len(line) - len(line.rstrip("\\"))
    return trailing % 2 == 1


def _join_command_line(
    lines: list[str], line: str, index: int, quote: str | None
) -> tuple[str, str | None, int]:
    """Join a command line's backslash continuations; return (line, quote, index).

    The merged line is what the shell parses, so a heredoc marker split
    across a continuation reads the same delimiter the shell would.
    """
    while _line_continues(line, quote) and index < len(lines):
        line = line[:-1] + " " + lines[index].lstrip()
        index += 1
    return line, quote, index


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
    r"(?:^|[;&|()\n\s])(?:function\s+)?[A-Za-z_][A-Za-z0-9_]*\s*\(\s*\)\s*$"
)
_FUNCTION_KEYWORD_TAIL_RE = re.compile(
    r"(?:^|[;&|()\n\s])function\s+[A-Za-z_][A-Za-z0-9_]*\s*$"
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


def _function_body_step(
    script: str, index: int, depth: int, quote: str | None, opener: str
) -> tuple[int, int, str | None, str | None]:
    """Advance one character inside a function body.

    Returns (new index, new depth, new quote, closer to keep); only the
    closing delimiter of the outermost body is kept, so the structure stays
    visible while the body commands remain dropped.
    """
    closer = _BODY_CLOSERS[opener]
    char = script[index]
    if quote is None and char == opener:
        return index + 1, depth + 1, quote, None
    if quote is None and char == closer:
        depth -= 1
        return index + 1, depth, quote, closer if depth == 0 else None
    new_quote, consumed = _scan_char(script, index, quote)
    return index + consumed, depth, new_quote, None


_DEFINED_FUNCTION_RE = re.compile(
    r"(?:^|[;&|()\n\s])(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*[{( \t\n]"
)
_FUNCTION_KEYWORD_DEF_RE = re.compile(
    r"(?:^|[;&|()\n\s])function\s+([A-Za-z_][A-Za-z0-9_]*)\s*[{( \t\n]"
)


def _defined_function_names(script: str) -> set[str]:
    """Names of functions the script defines (both definition forms)."""
    names = set(_DEFINED_FUNCTION_RE.findall(script))
    names.update(_FUNCTION_KEYWORD_DEF_RE.findall(script))
    return names


def _called_function_names(script: str, defined: set[str]) -> set[str]:
    """Names the script calls: in command position, not the definition.

    A definition writes the name before ``()`` (or after ``function``), so
    those occurrences are skipped; every other command-position use runs the
    body.
    """
    called: set[str] = set()
    for match in re.finditer(
        r"(?:^|[;&|()\n\s])([A-Za-z_][A-Za-z0-9_]*)", script
    ):
        name = match.group(1)
        if name not in defined:
            continue
        if script[match.end() :].startswith("("):
            continue
        if re.search(r"function\s+$", script[: match.start(1)]):
            continue
        called.add(name)
    return called


def _strip_function_bodies(script: str) -> str:
    """Drop uncalled shell function bodies; calling one executes its body.

    The release gate must provision in reachable commands: a definition body
    counts when the script calls the function, because the call executes the
    body.  Bodies of functions nobody calls never run, so they are dropped
    (brace groups and subshells without a definition stay: they execute in
    place).
    """
    defined = _defined_function_names(script)
    called = _called_function_names(script, defined)
    kept: list[str] = []
    index = 0
    depth = 0
    quote: str | None = None
    opener = ""
    while index < len(script):
        if depth == 0:
            found = _opens_function_body(script, index)
            if found is not None:
                if _function_body_is_live(script, index, called):
                    new_quote, consumed = _scan_char(script, index, quote)
                    kept.append(script[index : index + consumed])
                    quote = new_quote
                    index += consumed
                    continue
                depth = 1
                opener = found
                kept.append(found)
                index += 1
                continue
            new_quote, consumed = _scan_char(script, index, quote)
            kept.append(script[index : index + consumed])
            quote = new_quote
            index += consumed
            continue
        index, depth, quote, closer = _function_body_step(
            script, index, depth, quote, opener)
        if closer:
            kept.append(closer)
    return "".join(kept)


def _function_body_is_live(
    script: str, index: int, called: set[str]
) -> bool:
    """Whether the body opening at ``index`` belongs to a called function."""
    if not called:
        return False
    prefix = script[:index]
    match = re.search(
        r"(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*$", prefix
    )
    if match is not None:
        return match.group(1) in called
    keyword = re.search(r"function\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", prefix)
    return keyword is not None and keyword.group(1) in called


# Provisioning commands must sit in command position (optionally behind the
# repository's `retry N` wrapper); substring matches, echoes and heredoc
# bodies must never satisfy the gate.  The component/toolchain argument must
# stay inside the same command: an unbounded tail would cross command
# separators, letting `rustup toolchain install "${RUST_TOOLCHAIN}"; echo
# --component rustfmt` satisfy the check without installing rustfmt.
# Release workflows must provision toolchains through the verified installer
# (with an explicit `bash` invocation) and add the rustfmt component
# separately; raw `rustup toolchain install` commands are rejected.
_PROVISION_PREFIX = r"^(?:retry\s+[0-9]+\s+)?"
_VERIFIED_INSTALLER_RE = re.compile(
    _PROVISION_PREFIX
    + r"bash\s+\./packaging/scripts/install-verified-rustup\.sh\b"
    + r"[^;|&]*--toolchain\s+[\"']?\$\{RUST_TOOLCHAIN\}[\"']?"
)
_COMPONENT_ADD_RE = re.compile(
    _PROVISION_PREFIX + r"rustup\s+component\s+add\b[^;|&]*"
)
_RAW_INSTALL_RE = re.compile(
    _PROVISION_PREFIX + r"rustup\s+toolchain\s+install\b"
)
_DRIFT_CHECK_RE = re.compile(
    r"^(?:python3|python)\s+tools/reason-codegen/generate\.py\s+--check\b"
)


def _quoted_separator_at(line: str, index: int, quote: str) -> int:
    """Separator length inside a quoted string (0 = data)."""
    char = line[index]
    if quote == "'":
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
    before_ok = previous in ("", " ", "\t", ";", "|", "&", "(", ")")
    after_ok = following in ("", " ", "\t", ";", ")")
    return 1 if before_ok and after_ok else 0


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


def _command_segments_with_separators(script: str) -> list[tuple[str, str]]:
    """Split into (segment, separator-before) pairs at shell separators.

    Every separator from ``_separator_at`` starts a new command position, so
    a command chained behind one is still a command.  Separators inside
    quotes stay data, so quoted fragments can neither satisfy a provisioning
    requirement nor trip the rejection side.  A string that spans lines keeps
    its content as data: its segment continues across the newline instead of
    restarting inside the quote.  A statement boundary reports ``\\n`` as its
    separator, so callers can tell chains apart from fresh lines.
    """
    segments: list[tuple[str, str]] = []
    current: list[str] = []
    quote: str | None = None
    separator = "\n"
    for line in script.splitlines():
        current, quote, separator = _scan_segment_line(
            line, current, quote, segments, separator)
        if quote is None:
            _flush_segment(segments, current, separator)
            separator = "\n"
            current = []
        else:
            current.append(" ")
    _flush_segment(segments, current, separator)
    return segments


def _command_segments(script: str) -> list[str]:
    """Split into command segments at unquoted shell separators."""
    return [segment for segment, _ in _command_segments_with_separators(script)]


def _segment_literal(segment: str) -> bool | None:
    """The boolean a segment trivially evaluates to, or None when unknown.

    A leading-and-sole ``false`` is False; a leading ``true`` or ``:`` is
    True; everything else may depend on runtime state.
    """
    words = segment.split()
    if not words:
        return None
    if words[0] in (":", "true"):
        return True
    if words[0] == "false" and len(words) == 1:
        return False
    return None


def _segment_unreachable(separator: str, previous: bool | None) -> bool:
    """Whether a literal short-circuit on ``separator`` skips this segment."""
    return (separator == "&&" and previous is False) or (
        separator == "||" and previous is True
    )


def _live_command_segments(script: str) -> list[str]:
    """Segments some execution path reaches; dead branches are dropped.

    Literal short-circuits model the shell: behind ``false &&`` or
    ``true ||`` nothing runs, and the chain stays dead for further
    ``&&`` links while ``||`` revives it.  ``if false`` branches stay dead
    until their ``fi``.  Commands that cannot run must not satisfy a
    provisioning requirement.
    """
    live: list[str] = []
    previous: bool | None = None
    dead_if = 0
    for segment, separator in _command_segments_with_separators(script):
        words = segment.split()
        if dead_if:
            if words[0] == "fi":
                dead_if = 0
                previous = None
            continue
        if words[0] == "fi":
            previous = None
            continue
        if _segment_unreachable(separator, previous):
            continue
        live.append(segment)
        if words[0] == "if" and words[1:2] == ["false"]:
            dead_if = 1
            previous = None
            continue
        previous = _segment_literal(segment)
    return live


def _rustfmt_component_index(segments: list[str]) -> int | None:
    """Position of the segment that installs rustfmt for the pinned toolchain.

    Accepts either argument order inside a single ``rustup component add``
    command segment; the segment boundary keeps a later command's arguments
    from satisfying the requirement.
    """
    for position, segment in enumerate(segments):
        if not _COMPONENT_ADD_RE.match(segment):
            continue
        if re.search(r"\brustfmt\b", segment) and re.search(
            r"--toolchain\s+[\"']?\$\{RUST_TOOLCHAIN\}[\"']?", segment
        ):
            return position
    return None


def _provides_rustfmt_component(run_scripts: str) -> bool:
    """Whether a command installs rustfmt for the pinned toolchain."""
    return _rustfmt_component_index(_command_segments(run_scripts)) is not None


def _step_runs_shell(step: dict) -> bool:
    """Whether a workflow step's ``run`` executes in a shell on every path.

    A step the workflow gates with ``if`` may never run, and a step whose
    ``shell`` is not bash/sh feeds ``run`` to another interpreter, so neither
    can satisfy a provisioning check.
    """
    if "if" in step:
        return False
    shell = step.get("shell")
    if isinstance(shell, str) and shell.split():
        return shell.split()[0] in ("bash", "sh")
    return True


def _job_run_scripts(workflow_content: str, job_name: str) -> str | None:
    """Return the concatenated run scripts of one job, or None when absent.

    The workflow is parsed as YAML so that only executable ``run`` steps feed
    the checks: shell comments in the raw file never satisfy them, and steps
    that are conditional or run another interpreter are skipped.
    """
    try:
        workflow = yaml.safe_load(workflow_content)
    except yaml.YAMLError:
        return None
    jobs = (workflow or {}).get("jobs") if isinstance(workflow, dict) else None
    if not isinstance(jobs, dict) or job_name not in jobs:
        return None
    job = jobs[job_name]
    if not isinstance(job, dict):
        return None
    scripts: list[str] = []
    for step in job.get("steps") or []:
        if (
            isinstance(step, dict)
            and isinstance(step.get("run"), str)
            and _step_runs_shell(step)
        ):
            scripts.append(step["run"])
    return "\n".join(scripts)


def _all_job_run_scripts(workflow_content: str) -> str | None:
    """Return the concatenated run scripts of every job, or None when the
    workflow cannot be parsed."""
    try:
        workflow = yaml.safe_load(workflow_content)
    except yaml.YAMLError:
        return None
    jobs = (workflow or {}).get("jobs") if isinstance(workflow, dict) else None
    if not isinstance(jobs, dict):
        return None
    scripts: list[str] = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if (
                isinstance(step, dict)
                and isinstance(step.get("run"), str)
                and _step_runs_shell(step)
            ):
                scripts.append(step["run"])
    return "\n".join(scripts)


def _raw_toolchain_install_issue(workflow_content: str) -> str | None:
    """Reject raw ``rustup toolchain install`` anywhere in the workflow.

    Release workflows must provision toolchains through the verified
    installer, which validates the downloaded rustup-init checksum before
    execution.
    """
    all_scripts = _all_job_run_scripts(workflow_content)
    if all_scripts is None:
        return None
    # Strip comments and static heredoc bodies first: markers there are data,
    # so they must not trip the dynamic-delimiter rejection.
    stripped = _strip_heredocs(_strip_shell_comments(all_scripts))
    dynamic = _dynamic_heredoc_markers(stripped)
    if dynamic:
        return (
            "release workflows must not open heredocs with runtime-expanded "
            "delimiters (`<<$VAR`): the toolchain provisioning cannot be "
            "verified statically"
        )
    executable = _join_continuations(stripped)
    for segment in _live_command_segments(executable):
        if _RAW_INSTALL_RE.match(segment):
            return (
                "release workflows must provision Rust toolchains through "
                "the verified installer; found a raw `rustup toolchain "
                "install` command"
            )
    return None


def _release_gate_toolchain_issue(run_scripts: str) -> str | None:
    """Return the toolchain provisioning issue, or None when satisfied.

    Only executable commands in command position count: shell comments are
    stripped (with quote state carried across lines), heredoc bodies are
    dropped, and backslash continuations are joined, so a commented-out,
    echoed, embedded or quoted-text install or drift check cannot satisfy
    the gate.  The release gate must provision the pinned ${RUST_TOOLCHAIN}
    through the verified installer with an explicit ``bash`` invocation and
    add the rustfmt component for that toolchain: the gate scripts resolve
    rustfmt through Rustup shims, while the installer's minimal profile
    does not include it.
    """
    # Strip comments and static heredoc bodies first: markers there are data,
    # so they must not trip the dynamic-delimiter rejection.
    stripped = _strip_heredocs(_strip_shell_comments(run_scripts))
    dynamic = _dynamic_heredoc_markers(stripped)
    if dynamic:
        return (
            "the release-gate job opens a heredoc with a runtime-expanded "
            "delimiter, so its toolchain provisioning cannot be verified "
            "statically; use a plain delimiter"
        )
    executable = _join_continuations(_strip_function_bodies(stripped))
    segments = _live_command_segments(executable)
    if not any(_DRIFT_CHECK_RE.match(segment) for segment in segments):
        return (
            "the release-gate job no longer runs "
            f"{RELEASE_GATE_RUSTFMT_CONSUMER}; update this provisioning "
            "expectation with the job split"
        )
    installer_at = next(
        (
            position
            for position, segment in enumerate(segments)
            if _VERIFIED_INSTALLER_RE.match(segment)
        ),
        None,
    )
    if installer_at is None:
        return (
            "the release-gate job must provision the pinned Rust toolchain "
            "through the verified installer (bash ./packaging/scripts/"
            'install-verified-rustup.sh --toolchain "${RUST_TOOLCHAIN}")'
        )
    component_at = _rustfmt_component_index(segments)
    if component_at is None:
        return (
            "the release-gate job must add the rustfmt component for the "
            "pinned toolchain (rustup component add --toolchain "
            '"${RUST_TOOLCHAIN}" rustfmt) so cargo, rustc and rustfmt '
            "resolve for the gate scripts"
        )
    if component_at < installer_at:
        return (
            "the release-gate job must install the toolchain through the "
            "verified installer before adding the rustfmt component: rustup "
            "cannot add a component to a toolchain that is not installed"
        )
    return None


def check_release_gate_toolchain(result: ValidationResult) -> None:
    """Validate pinned Rust toolchain provisioning in the release-gate job."""
    content = read_safe(RELEASE_PACKAGES_WORKFLOW)
    if not content:
        result.fail(
            PKG_RELEASE_GATE_TOOLCHAIN_GATE,
            "release-packages.yml not found",
        )
        return

    run_scripts = _job_run_scripts(content, RELEASE_GATE_JOB_NAME)
    if run_scripts is None:
        result.fail(
            PKG_RELEASE_GATE_TOOLCHAIN_GATE,
            f"{RELEASE_GATE_JOB_NAME} job not found in release-packages.yml",
        )
        return

    issue = _raw_toolchain_install_issue(content) or _release_gate_toolchain_issue(
        run_scripts
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
        or re.search(r"nginx-\$\{\{.*nginx_version", wf_content)
    )
    has_rpm_naming = bool(
        re.search(r"nginx\$\{?NGINX_VERSION", wf_content)
        or re.search(r"nginx\$\{\{.*nginx_version", wf_content)
    )
    if has_deb_naming and has_rpm_naming:
        return None
    missing = []
    if not has_deb_naming:
        missing.append(".deb naming without NGINX version")
    if not has_rpm_naming:
        missing.append(".rpm naming without NGINX version")
    return "; ".join(missing)


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
    check_artifact_naming(result)
    check_install_docs(result)

    print_report(result)
    return 1 if result.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
