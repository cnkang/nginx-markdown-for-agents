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
    ``$`` or backtick that would expand when nothing in the word is quoted.
    """
    char = raw[index]
    if char == "$" and index + 1 < len(raw) and raw[index + 1] in "'\"":
        # The $ of $'..' / $".." quoting is not an expansion character; the
        # quote that follows performs the quoting.
        return quote, 1, None, False
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

    Quote removal models the shell.  When any part of the word is quoted
    (quotes or backslashes), the shell performs quote removal only and never
    expands it, so the delimiter is static; a fully unquoted word with a
    live ``$`` or backtick depends on the environment at runtime.
    """
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
        r"(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*$", prefix
    )
    if match is not None:
        return match.group(1)
    keyword = re.search(r"function\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", prefix)
    return keyword.group(1) if keyword is not None else None


def _function_body_spans(script: str) -> list[tuple[str, int, int]]:
    """Spans ``(name, start, end)`` of every function body in the script.

    The walk matches the bodies-stripper so reachability and stripping agree
    on the structure.
    """
    spans: list[tuple[str, int, int]] = []
    index = 0
    depth = 0
    quote: str | None = None
    opener = ""
    name = ""
    body_start = 0
    while index < len(script):
        if depth == 0:
            found = _opens_function_body(script, index)
            if found is not None:
                name = _definition_name(script, index) or ""
                body_start = index + 1
                opener = found
                depth = 1
                quote = None
                index += 1
                continue
            quote, consumed = _scan_char(script, index, quote)
            index += consumed
            continue
        index, depth, quote, closer = _function_body_step(
            script, index, depth, quote, opener)
        if closer and depth == 0:
            spans.append((name, body_start, index))
    return spans


def _head_spans(
    script: str, bodies: list[tuple[str, int, int]]
) -> list[tuple[int, int]]:
    """Spans of the definition heads (the ``name ()`` before each body)."""
    heads: list[tuple[int, int]] = []
    for _, lo, _hi in bodies:
        prefix = script[: max(0, lo - 1)]
        match = re.search(
            r"(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*$", prefix
        )
        if match is None:
            match = re.search(r"function\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", prefix)
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
    branches: list[tuple[bool | None, bool]] = []
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
# `builtin` only runs shell builtins: a known literal builtin keeps its
# literal, and a command that is surely not a builtin makes it fail.
_BUILTIN_LITERAL_WORDS = {":": True, "true": True, "false": False}
_NON_BUILTIN_COMMANDS = frozenset({
    "bash", "sh", "dash", "python", "python3", "rustup",
    "env", "command", "exec", "nohup", "sudo",
})
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_BASH_C_RE = re.compile(r"-[A-Za-z]*c[A-Za-z]*\Z")


_SUDO_ARG_FLAGS = frozenset({"-u", "-g", "-p", "-C", "-T", "-r", "-t", "-h"})


def _skip_option_words(
    words: list[str], index: int, arg_flags: frozenset[str]
) -> int:
    """Skip a wrapper's own options (`sudo -n`, `sudo -u root`)."""
    while (
        index < len(words)
        and words[index].startswith("-")
        and words[index] != "--"
    ):
        if words[index] in arg_flags and index + 1 < len(words):
            index += 1
        index += 1
    return index


def _skip_env_assignments(words: list[str], index: int) -> int:
    """Skip `env VAR=VAL...` assignments."""
    while index < len(words) and _ENV_ASSIGN_RE.match(words[index]):
        index += 1
    return index


def _skip_env_options(words: list[str], index: int) -> int:
    """Skip `env` options (`-i`, `-u NAME`, `--unset=NAME`)."""
    while (
        index < len(words)
        and words[index].startswith("-")
        and words[index] != "--"
    ):
        if words[index] in ("-u", "--unset", "-C", "--chdir") and (
            index + 1 < len(words)
        ):
            index += 1
        index += 1
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


def _skip_shell_c(words: list[str], index: int) -> int:
    """Skip `<shell> [options] -c`, returning the payload word index."""
    probe = index + 1
    while (
        probe < len(words)
        and words[probe].startswith("-")
        and not _BASH_C_RE.match(words[probe])
    ):
        if words[probe] == "--":
            return index
        if words[probe] in ("-o", "-O") and probe + 1 < len(words):
            probe += 1
        probe += 1
    if probe < len(words) and _BASH_C_RE.match(words[probe]):
        return probe + 1
    return index


def _skip_bare_separators(words: list[str], index: int) -> int:
    """Skip bare ``--`` separators left by a wrapper."""
    while index < len(words) and words[index] == "--":
        index += 1
    return index


def _wrapper_command_step(words: list[str], index: int) -> tuple[int, bool] | None:
    """Advance past a leading command wrapper's own words; None when the
    wrapper must not be unwrapped (a `builtin`, or a `command` lookup)."""
    wrapper = words[index]
    if wrapper == "builtin":
        # `builtin` can only run shell builtins, never the external
        # commands the provisioning checks match.
        return None
    if wrapper == "command":
        rest, lookup = _command_operand(words[index + 1:])
        if lookup:
            return None
        return len(words) - len(rest), False
    probe = _skip_option_words(words, index + 1, _SUDO_ARG_FLAGS)
    if probe < len(words) and words[probe] == "--":
        probe += 1
    return probe, False


def _wrapper_prefix_length(words: list[str]) -> tuple[int, bool]:
    """How many leading wrapper words to drop (a deterministic token scan),
    plus whether the rest starts at a shell ``-c`` payload.

    Handles `retry N`, command wrappers with their own options, `env
    VAR=VAL...`, `<shell> -c '...'`, `eval` and bare `--` separators, in
    the order the shell accepts them.
    """
    index = _skip_env_assignments(words, 0)
    if (
        index + 1 < len(words)
        and words[index] == "retry"
        and words[index + 1].isdigit()
    ):
        index += 2
    index = _skip_bare_separators(words, index)
    if index < len(words) and words[index] in _WRAPPER_COMMANDS:
        step = _wrapper_command_step(words, index)
        if step is None:
            return index, False
        index = _skip_env_assignments(words, step[0])
    if index < len(words) and words[index] == "env":
        index = _skip_env_prefix(words, index + 1)
    if index < len(words) and words[index] in ("bash", "sh", "dash"):
        payload = _skip_shell_c(words, index)
        if payload != index:
            return payload, True
    if index < len(words) and words[index] == "eval":
        index += 1
    return index, False


def _retry_runs_its_target(script: str) -> bool:
    """Whether a locally defined ``retry`` actually runs its arguments.

    ``retry N cmd`` only provisions when the retry implementation invokes
    the command it wraps; a no-op or partial implementation never reaches
    the target, so wrapped provisioning must not count behind it.  The
    invocation may sit inside a loop or an unevaluated branch: only a
    provably dead position (a literal ``false`` branch or short-circuit)
    does not count.
    """
    effective, _superseded = _effective_body_spans(script)
    for name, lo, hi in effective:
        if name != "retry":
            continue
        for segment in _possibly_reached_segments(script[lo:hi]):
            words = _peel_execution_wrappers(segment.split())
            if words and _resolve_heredoc_word(words[0])[0] in ("$@", "${@}"):
                return True
        return False
    return True


def _possible_marker_carry(
    segment: str,
    separator: str,
    previous: bool | None,
    branches: list[tuple[bool | None, bool]],
) -> str | None:
    """The command a marker carries when its region is not provably dead
    and the marker itself is not short-circuited."""
    if _segment_keyword(segment) not in _BODY_MARKERS:
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
    branches: list[tuple[bool | None, bool]] = []
    pairs = _command_segments_with_separators(script)
    for index, (segment, separator) in enumerate(pairs):
        keyword = _segment_keyword(segment)
        condition = _pair_condition(pairs, index, keyword)
        if _branch_keyword_step(branches, segment, condition):
            _possible_branch_state(branches, keyword, condition)
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
            # The newline stays data: a quoted payload with line-separated
            # commands must keep them apart for the callers.
            current.append("\n")
    _flush_segment(segments, current, separator)
    return segments


def _command_segments(script: str) -> list[str]:
    """Split into command segments at unquoted shell separators."""
    return [segment for segment, _ in _command_segments_with_separators(script)]


def _is_redirection_word(word: str) -> bool:
    """Whether the word is a bare redirection like ``>/dev/null``/``2>&1``."""
    return re.match(r"\d*[<>]", word) is not None


def _command_operand(rest: list[str]) -> tuple[list[str], bool]:
    """``command``'s operand after its own options, plus whether the
    options ask for a lookup (``-v``/``-V``), which never runs it."""
    probe = 0
    lookup = False
    while (
        probe < len(rest)
        and rest[probe].startswith("-")
        and rest[probe] != "--"
    ):
        if "v" in rest[probe][1:] or "V" in rest[probe][1:]:
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
    ``command``/``builtin``, ``env`` with its options and ``VAR=VAL``
    assignments, and bare assignments."""
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
    or at a separator that is not a chain operator.
    """
    value = _condition_literal(pairs[index][0])
    probe = index + 1
    while probe < len(pairs) and probe <= index + 64:
        segment, separator = pairs[probe]
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
        if word == "errexit":
            state = True
        elif word.startswith("+") and "e" in word[1:]:
            state = False
        elif word.startswith("-") and "e" in word[1:]:
            state = True
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


def _always_failing_functions(script: str) -> set[str]:
    """Functions whose last definition always returns a failure status."""
    effective, _superseded = _effective_body_spans(script)
    return {
        name
        for name, lo, hi in effective
        if _body_fails_unconditionally(script[lo:hi])
    }


def _marker_return_status(
    segment: str,
    separator: str,
    previous: bool | None,
    branches: list[tuple[bool | None, bool]],
) -> bool | None:
    """The failure status a marker's carried return provokes on the
    provable path; None when the marker carries no taken return."""
    if not _region_runs(branches) or _chain_skips(separator, previous):
        return None
    return _return_failure(_body_marker_command(segment))


def _possible_branch_state(
    branches: list[tuple[bool | None, bool]],
    keyword: str,
    condition: bool | None,
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
        branches[-1] = (chain != _OPEN_CHAIN, chain)


def _body_marker_command(segment: str) -> str:
    """The command a ``then``/``do``/``else`` marker carries on its own
    segment (``then return 1``); ``""`` when the marker stands alone."""
    words = segment.split()
    if len(words) < 2:
        return ""
    return " ".join(words[1:])


def _return_failure(segment: str) -> bool | None:
    """Whether a ``return`` segment provokes a failure: True for a non-zero
    argument, False for success, None when the segment is not a return."""
    if _segment_keyword(segment) != "return":
        return None
    words = segment.split()
    arg = words[1].strip("'\"") if len(words) > 1 else ""
    return bool(arg and arg != "0")


def _return_success_evidence(pairs: list[tuple[str, str]]) -> bool:
    """Whether any segment or marker carries a success return.

    Success evidence counts wherever it appears: a function that can
    return success is never labelled as failing."""
    if any(_return_failure(segment) is False for segment, _sep in pairs):
        return True
    return any(
        _return_failure(_body_marker_command(segment)) is False
        for segment, _sep in pairs
        if _segment_keyword(segment) in ("then", "do", "else")
    )


def _marker_failure_evidence(
    segment: str,
    separator: str,
    previous: bool | None,
    branches: list[tuple[bool | None, bool]],
) -> bool:
    """Whether a marker's carried return provokes a failure on the
    provable path."""
    return (
        _marker_return_status(segment, separator, previous, branches) is True
    )


def _body_fails_unconditionally(body: str) -> bool:
    """Whether a body's provable path ends in a failure return.

    Failure evidence must sit on the provable path; any success return
    disqualifies the label, so a function that can succeed is never
    treated as failing.
    """
    pairs = _command_segments_with_separators(body)
    if _return_success_evidence(pairs):
        return False
    failure_return = False
    previous: bool | None = None
    branches: list[tuple[bool | None, bool]] = []
    for index, (segment, separator) in enumerate(pairs):
        keyword = _segment_keyword(segment)
        condition = _pair_condition(pairs, index, keyword)
        if _branch_keyword_step(branches, segment, condition):
            if keyword in ("then", "do", "else"):
                failure_return = failure_return or _marker_failure_evidence(
                    segment, separator, previous, branches
                )
            previous = None
            continue
        if not _region_runs(branches) or _chain_skips(separator, previous):
            continue
        if _return_failure(segment) is True:
            failure_return = True
        previous = _segment_literal(segment)
    return failure_return


def _segment_ends_shell(
    segment: str,
    following: str,
    errexit: bool,
    failing: frozenset[str] = frozenset(),
) -> bool:
    """Whether the segment ends its shell.

    ``exit`` always ends it, ``exec cmd`` replaces the process, and a
    standalone failing command ends it under ``set -e`` (errexit ignores
    failures inside an ``&&``/``||``/pipeline list).  A call to a local
    function that always returns a failure status counts as failing too.
    """
    words = _peel_execution_wrappers(segment.split())
    keyword = _resolve_heredoc_word(words[0])[0] if words else ""
    if keyword == "exit" or _is_exec_replacement(words):
        return True
    value = _segment_literal(segment)
    if value is None and failing and words:
        if _resolve_heredoc_word(words[0])[0] in failing:
            value = False
    return (
        errexit and value is False and following not in ("&&", "||", "|")
    )


def _live_command_segments(
    script: str, failing: frozenset[str] = frozenset()
) -> list[str]:
    """Segments on the unconditional path of one shell's script.

    A command counts when the analyzer can prove it runs: an ``if`` with an
    unevaluated condition hides both branches, loops and ``case`` hide
    their bodies, an ``&&``/``||`` chain whose left side is not an
    evaluated literal is conditional itself, and ``exit``/``return`` end
    the run.  Literal conditions and short-circuits are modeled, so
    ``if true`` bodies and ``true &&`` chains still count; a standalone
    failing command under ``set -e`` ends the run too.
    """
    live: list[str] = []
    previous: bool | None = None
    branches: list[tuple[bool | None, bool]] = []
    exited = False
    errexit = False
    pairs = _command_segments_with_separators(script)
    for index, (segment, separator) in enumerate(pairs):
        # Each tuple carries the separator BEFORE its segment, so the
        # separator after this segment comes from the next tuple.
        following = pairs[index + 1][1] if index + 1 < len(pairs) else ""
        condition = _pair_condition(
            pairs, index, _segment_keyword(segment)
        )
        if _branch_keyword_step(branches, segment, condition):
            previous = None
            continue
        if exited or not _region_runs(branches):
            continue
        if _chain_skips(separator, previous):
            continue
        live.append(segment)
        if _segment_ends_shell(segment, following, errexit, failing):
            exited = True
            previous = None
            continue
        state = _set_errexit_state(segment)
        if state is not None:
            errexit = state
        previous = _segment_literal(segment)
    return live


def _rustfmt_component_index(segments: list[str]) -> int | None:
    """Position of the segment that installs rustfmt for the pinned toolchain.

    Accepts either argument order inside a single ``rustup component add``
    command segment; the segment boundary keeps a later command's arguments
    from satisfying the requirement.
    """
    for position, segment in enumerate(segments):
        if not _COMPONENT_ADD_RE.match(_strip_provision_wrappers(segment)):
            continue
        if re.search(r"\brustfmt\b", segment) and re.search(
            r"--toolchain\s+[\"']?\$\{RUST_TOOLCHAIN\}[\"']?", segment
        ):
            return position
    return None


def _provides_rustfmt_component(run_scripts: str) -> bool:
    """Whether a command installs rustfmt for the pinned toolchain."""
    return _rustfmt_component_index(_command_segments(run_scripts)) is not None


def _literal_true_condition(value: str) -> bool:
    """Whether an ``if:`` expression is literally, unconditionally true."""
    text = value.strip()
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    return text == "true"


def _step_runs_shell(step: dict) -> bool:
    """Whether a workflow step's ``run`` executes in a shell on every path.

    A step the workflow gates with ``if`` may never run, and a step whose
    ``shell`` is not bash/sh feeds ``run`` to another interpreter, so neither
    can satisfy a provisioning check.
    """
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
    if isinstance(shell, str) and shell.split():
        return shell.split()[0] in ("bash", "sh")
    return True


def _job_run_scripts(workflow_content: str, job_name: str) -> list[str] | None:
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
    return scripts


def _all_job_run_scripts(workflow_content: str) -> list[str] | None:
    """Return the concatenated run scripts of every job, or None when the
    workflow cannot be parsed.

    Every run step feeds this scan -- including conditional and non-shell
    steps: a raw toolchain install must fail the gate wherever it could ever
    appear.
    """
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


def _raw_install_in_segment(segment: str) -> bool:
    """Whether the segment runs a raw install, directly or via a shell -c."""
    stripped = _strip_provision_wrappers(segment)
    if _RAW_INSTALL_RE.match(_quote_cleaned_command(stripped)):
        return True
    if stripped != segment:
        return any(
            _RAW_INSTALL_RE.match(
                _quote_cleaned_command(_strip_provision_wrappers(inner))
            )
            for inner in _command_segments(stripped)
        )
    return False


def _raw_toolchain_install_issue(workflow_content: str) -> str | None:
    """Reject raw ``rustup toolchain install`` anywhere in the workflow.

    Release workflows must provision toolchains through the verified
    installer, which validates the downloaded rustup-init checksum before
    execution.  Every run line is scanned -- including conditional steps
    and function bodies -- because a raw install must fail the gate
    wherever it could ever appear.
    """
    runs = _all_job_run_scripts(workflow_content)
    if runs is None:
        return None
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
    they are stepped over before the retry token is read.
    """
    words = segment.split()
    index = _skip_env_assignments(words, 0)
    return (
        index + 1 < len(words)
        and words[index] == "retry"
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
        if re.search(r"[;&|\n]", _strip_provision_wrappers(segment)):
            continue
        candidates.append(segment)
    return candidates


_SHADOWED_NAMES = frozenset({
    ":",
    "bash",
    "builtin",
    "command",
    "dash",
    "env",
    "exec",
    "exit",
    "false",
    "python",
    "python3",
    "return",
    "rustup",
    "sh",
    "true",
})


def _shadowing_issue(script: str) -> str | None:
    """Reject functions that shadow commands the provisioning checks read.

    A function named like a shell builtin or like one of the commands the
    checks match changes what those words do, so provisioning text can no
    longer be trusted to mean what it says.
    """
    shadowed = _defined_function_names(script) & _SHADOWED_NAMES
    if not shadowed:
        return None
    return (
        "the release-gate job defines shell functions that shadow commands "
        "used by the provisioning checks ("
        + ", ".join(sorted(shadowed))
        + "); rename them so the checks trust the commands they read"
    )


def _release_gate_toolchain_issue(
    run_scripts: "str | list[str]",
) -> str | None:
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
    steps = [run_scripts] if isinstance(run_scripts, str) else list(run_scripts)
    segments: list[str] = []
    for step in steps:
        # Strip comments and static heredoc bodies first: markers there are
        # data, so they must not trip the dynamic-delimiter rejection.  A
        # step runs in its own shell, so function reachability resets per
        # step: a definition cannot cross into the next step's shell.
        stripped = _strip_heredocs(_strip_shell_comments(step))
        shadow_issue = _shadowing_issue(stripped)
        if shadow_issue:
            return shadow_issue
        executable = _join_continuations(_strip_function_bodies(stripped))
        # Unreachable function bodies are already gone, so a dynamic
        # delimiter in dead code cannot reject a script whose real
        # provisioning is static.
        if _dynamic_heredoc_markers(executable):
            return (
                "the release-gate job opens a heredoc with a runtime-expanded "
                "delimiter, so its toolchain provisioning cannot be verified "
                "statically; use a plain delimiter"
            )
        retry_trusted = _retry_runs_its_target(stripped)
        failing = frozenset(_always_failing_functions(stripped))
        segments.extend(
            _provision_candidates(
                _live_command_segments(executable, failing), retry_trusted
            )
        )
    if not any(
        _DRIFT_CHECK_RE.match(_strip_provision_wrappers(segment))
        for segment in segments
    ):
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
