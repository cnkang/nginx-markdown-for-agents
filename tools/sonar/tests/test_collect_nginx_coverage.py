"""Contract tests for the module-enabled C coverage runtime."""

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
COVERAGE_SCRIPT = REPO_ROOT / "tools" / "sonar" / "collect_nginx_coverage.sh"
STREAMING_FAILURE_CACHE_SCRIPT = (
    REPO_ROOT / "tools" / "e2e" / "verify_streaming_failure_cache_e2e.sh"
)


def _advance_scan(
    script: str, index: int, quote: str, in_comment: bool
) -> tuple[int, str, bool]:
    """Consume one character for the block scanner.

    Returns the (next index, quote state, comment state) triple.  Inside an
    unclosed double quote a backslash escapes the following character; a
    ``#`` outside quotes starts a comment that runs to the end of the line.
    """
    char = script[index]
    if in_comment:
        return index + 1, quote, char != "\n"
    if quote:
        if char == "\\" and index + 1 < len(script):
            return index + 2, quote, False
        return index + 1, "" if char == quote else quote, False
    if char == "#":
        return index + 1, "", True
    if char in "\"'":
        return index + 1, char, False
    return index + 1, "", False


def _mask_step(
    script: str, index: int, quote: str, in_comment: bool
) -> tuple[int, str, bool, bool]:
    """One masking step; returns (next index, quote, comment, blank this char)."""
    char = script[index]
    if in_comment:
        if char == "\n":
            return index + 1, quote, False, False
        return index + 1, quote, True, True
    if quote:
        if char == "\\" and index + 1 < len(script):
            return index + 2, quote, False, False
        else:
            return (
                (index + 1, "", False, False)
                if char == quote
                else (index + 1, quote, False, False)
            )
    if char == "#":
        return index + 1, "", True, True
    if char in "\"'":
        return index + 1, char, False, False
    return index + 1, "", False, False


def _inside_quote(script: str, index: int) -> bool:
    """True when *index* sits inside a quoted string of *script*."""
    quote = ""
    cursor = 0
    while cursor < index:
        cursor, quote, _, _ = _mask_step(script, cursor, quote, False)
    return bool(quote)


def _mask_comments(script: str) -> str:
    """Blank comment text (quote-aware) so only active content is matched."""
    out = list(script)
    quote = ""
    in_comment = False
    index = 0
    while index < len(script):
        index, quote, in_comment, blank = _mask_step(
            script, index, quote, in_comment
        )
        if blank:
            out[index - 1] = " "
    return "".join(out)


def _scan_block_end(script: str, index: int) -> int:
    """Return the index just past the block starting at the opening brace.

    Braces inside quoted text or comments do not change the depth (matching
    the NGINX configuration parser closely enough for location scanning).
    """
    depth = 1
    quote = ""
    in_comment = False
    while index < len(script) and depth > 0:
        char = script[index]
        index, quote, in_comment = _advance_scan(script, index, quote, in_comment)
        if not quote and not in_comment:
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
    return index


def _conflicting_location_blocks(script: str) -> list[str]:
    """Return generated locations that violate the streaming/cache contract.

    Each block is delimited by brace depth rather than a fixed terminator, so a
    nested block or a differently indented closing brace cannot truncate the
    text before its directives are read.
    """
    blocks: list[str] = []
    # Accept every NGINX location modifier before the URI.  Without them an
    # exact (`= /x`) or prefix-modified (`^~ /x`, `~ /x`, `~* /x`) location is
    # silently skipped, and a conflicting block inside one would go unreported.
    masked = _mask_comments(script)
    for match in re.finditer(
        r"location\s+(?:=\s+|\^~\s+|~\*\s+|~\s+)?"
        r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s{]+)\s*\{",
        masked,
    ):
        if _inside_quote(masked, match.start()):
            # A `location`-shaped string inside a quoted value is text, not
            # a configuration block.
            continue
        index = _scan_block_end(masked, match.end())
        blocks.append(masked[match.start():index])
    return [
        block
        for block in blocks
        if "markdown_streaming force;" in block
        and "markdown_cache_validation full;" in block
    ]


def test_coverage_runtime_omits_removed_otel_directives() -> None:
    """Coverage NGINX config must not include removed OTel directives."""
    script = COVERAGE_SCRIPT.read_text(encoding="utf-8")

    assert "markdown_otel on;" not in script
    assert "markdown_otel_tracing on;" not in script


def test_coverage_runtime_avoids_rejected_streaming_cache_combination() -> None:
    """Every generated location must satisfy the streaming/cache contract."""
    script = COVERAGE_SCRIPT.read_text(encoding="utf-8")
    assert not _conflicting_location_blocks(script)


def test_streaming_failure_cache_runtime_avoids_rejected_combination() -> None:
    """The native failure/cache E2E config must pass config merge."""
    script = STREAMING_FAILURE_CACHE_SCRIPT.read_text(encoding="utf-8")

    assert not _conflicting_location_blocks(script)


def test_quoted_regex_location_with_brace_is_parsed() -> None:
    """A quoted regex location containing `{` must not truncate at the brace."""
    config = (
        "server {\n"
        "    location /ok {\n"
        "        markdown_streaming off;\n"
        "    }\n"
        '    location ~ "^/v\\d{2}$" {\n'
        "        markdown_streaming force;\n"
        "        markdown_cache_validation full;\n"
        "    }\n"
        "}\n"
    )
    blocks = _conflicting_location_blocks(config)
    assert len(blocks) == 1
    assert '"^/v\\d{2}$"' in blocks[0]


def test_quoted_brace_locations_without_conflict_stay_clean() -> None:
    """Separate locations around a quoted brace must not merge into one."""
    config = (
        "server {\n"
        '    location ~ "^/v\\d{2}$" {\n'
        "        markdown_streaming force;\n"
        "    }\n"
        "    location /ok {\n"
        "        markdown_cache_validation full;\n"
        "    }\n"
        "}\n"
    )
    assert not _conflicting_location_blocks(config)


def test_tilde_star_modifier_locations_are_parsed() -> None:
    """A ~* (case-insensitive) location must be recognized."""
    config = (
        "server {\n"
        '    location ~* "^/v\\d{2}$" {\n'
        "        markdown_streaming force;\n"
        "        markdown_cache_validation full;\n"
        "    }\n"
        "}\n"
    )
    blocks = _conflicting_location_blocks(config)
    assert len(blocks) == 1


def test_escaped_quote_delimiters_in_location_arguments_are_detected() -> None:
    """Quoted location arguments with escaped delimiters must not hide a
    conflicting block from the scan."""
    script = (
        'location ~ "a\\"b" {\n'
        "    markdown_streaming force;\n"
        "    markdown_cache_validation full;\n"
        "}\n"
        "location ~ 'c\\'d' {\n"
        "    markdown_streaming force;\n"
        "    markdown_cache_validation full;\n"
        "}\n"
    )

    blocks = _conflicting_location_blocks(script)

    assert len(blocks) == 2

def test_braces_inside_comments_do_not_close_the_block() -> None:
    """A `# }` comment must not terminate the scan early: the real closing
    brace still bounds the block, so the directives stay visible."""
    script = (
        "location /x { # } looks closed here\n"
        "    markdown_streaming force;\n"
        "    markdown_cache_validation full;\n"
        "}\n"
    )

    blocks = _conflicting_location_blocks(script)

    assert len(blocks) == 1

def test_commented_location_block_is_not_scanned() -> None:
    """A commented location block must not be matched or flagged."""
    script = (
        "# location /x {\n"
        "#     markdown_streaming force;\n"
        "#     markdown_cache_validation full;\n"
        "# }\n"
    )

    blocks = _conflicting_location_blocks(script)

    assert blocks == []


def test_escaped_single_quote_inside_location_argument_is_skipped() -> None:
    """A backslash escapes the following character inside single quotes too,
    so an escaped quote cannot terminate the scanner's quote state."""
    escaped = f"{chr(92)}'"
    script = (
        f"location ~ 'a{escaped}" + "b{' {\n"
        "    markdown_streaming force;\n"
        "    markdown_cache_validation full;\n"
        "}\n"
    )
    blocks = _conflicting_location_blocks(script)
    assert len(blocks) == 1

def test_location_shaped_text_inside_quotes_is_not_scanned() -> None:
    """A `location /fake {` string inside a quoted value is text, not a
    configuration block; only the active block is reported."""
    script = (
        "map $http_user_agent $note {\n"
'    default "location /fake {";\n'
        "}\n"
        "location /real {\n"
        "    markdown_streaming force;\n"
        "    markdown_cache_validation full;\n"
        "}\n"
    )
    blocks = _conflicting_location_blocks(script)
    assert len(blocks) == 1
    assert "markdown_streaming force;" in blocks[0]


def _heredoc_body(script: str, marker_fragment: str) -> tuple[str, str]:
    """Extract one heredoc body; return (body, delimiter word).

    The marker line is the first line that names *marker_fragment* and
    opens a ``<<``-heredoc.  The body is every following line up to the
    first line equal to the delimiter, so the returned text is what the
    shell would expand (or not) during generation.
    """
    lines = script.splitlines()
    start = None
    delimiter = None
    for index, line in enumerate(lines):
        if marker_fragment not in line:
            continue
        match = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", line)
        if match is None:
            continue
        start = index + 1
        delimiter = match[2]
        break
    assert start is not None, marker_fragment
    end = next(
        (
            index
            for index in range(start, len(lines))
            if lines[index] == delimiter
        ),
        None,
    )
    assert end is not None, f"unterminated heredoc {delimiter}"
    return "\n".join(lines[start:end]), delimiter


def test_coverage_config_heredoc_body_has_no_executable_substitutions() -> None:
    """The generated nginx.conf heredoc must stay data-only.

    ``collect_nginx_coverage.sh`` writes its coverage config through an
    unquoted heredoc, so the shell expands the body during generation:
    a backtick or an unbalanced ``$(`` in the body would run a command (or
    corrupt the config) while the file is written.  The regression pins the
    shape-independent property, so a future edit cannot reintroduce one.
    """
    script = COVERAGE_SCRIPT.read_text(encoding="utf-8")
    body, _delimiter = _heredoc_body(script, "conf/nginx.conf")

    # The extractor must find the real body, never an empty slice.
    assert "server {" in body
    assert "cat >" not in body

    assert "`" not in body

    # No command substitution at all, balanced or not: the shell expands
    # the unquoted heredoc while writing the config, so even a balanced
    # substitution would execute.
    assert "$(" not in body


def test_coverage_config_heredoc_body_rejects_injected_backtick() -> None:
    """Control: the same property checker flags an injected substitution.

    Without this control the body test could pass vacuously if the
    extraction silently returned text that was never scanned.
    """
    script = COVERAGE_SCRIPT.read_text(encoding="utf-8")
    body, delimiter = _heredoc_body(script, "conf/nginx.conf")
    injected = body.replace("worker_processes", "worker_processes `true`", 1)
    assert "`" in injected

    mutated_script = script.replace(body, injected, 1)
    mutated_body, _delimiter = _heredoc_body(mutated_script, "conf/nginx.conf")
    assert "`" in mutated_body
    assert delimiter == "EOF"
    # The property checker itself rejects the injected body, so the
    # assertion in the preceding test cannot pass vacuously.
    with pytest.raises(AssertionError):
        assert "`" not in mutated_body
