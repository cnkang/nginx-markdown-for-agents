"""Contract tests for the module-enabled C coverage runtime."""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
COVERAGE_SCRIPT = REPO_ROOT / "tools" / "sonar" / "collect_nginx_coverage.sh"
STREAMING_FAILURE_CACHE_SCRIPT = (
    REPO_ROOT / "tools" / "e2e" / "verify_streaming_failure_cache_e2e.sh"
)


def _conflicting_location_blocks(script: str) -> list[str]:
    """Return generated locations that violate the streaming/cache contract.

    Each block is delimited by brace depth rather than a fixed terminator, so a
    nested block or a differently indented closing brace cannot truncate the
    text before its directives are read.
    """
    blocks: list[str] = []
    for match in re.finditer(r"location\s+[^\s{]+\s*\{", script):
        depth = 1
        index = match.end()
        while index < len(script) and depth > 0:
            if script[index] == "{":
                depth += 1
            elif script[index] == "}":
                depth -= 1
            index += 1
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
