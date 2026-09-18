"""Contract tests for the module-enabled C coverage runtime."""

import re
from pathlib import Path


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
        if char == "\\" and quote == '"' and index + 1 < len(script):
            return index + 2, quote, False
        return index + 1, "" if char == quote else quote, False
    if char == "#":
        return index + 1, "", True
    if char in "\"'":
        return index + 1, char, False
    return index + 1, "", False


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
    for match in re.finditer(
        r"location\s+(?:=\s+|\^~\s+|~\*\s+|~\s+)?"
        r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s{]+)\s*\{",
        script,
    ):
        index = _scan_block_end(script, match.end())
        blocks.append(script[match.start():index])
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
