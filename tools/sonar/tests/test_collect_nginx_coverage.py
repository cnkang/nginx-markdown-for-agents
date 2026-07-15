"""Contract tests for the module-enabled C coverage runtime."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
COVERAGE_SCRIPT = REPO_ROOT / "tools" / "sonar" / "collect_nginx_coverage.sh"
STREAMING_FAILURE_CACHE_SCRIPT = (
    REPO_ROOT / "tools" / "e2e" / "verify_streaming_failure_cache_e2e.sh"
)


def _conflicting_location_blocks(script: str) -> list[str]:
    """Return generated locations that violate the streaming/cache contract."""
    location_blocks = [
        segment.split("\n        }", 1)[0]
        for segment in script.split("location ")[1:]
    ]
    return [
        block
        for block in location_blocks
        if "markdown_streaming force;" in block
        and "markdown_cache_validation full;" in block
    ]


def test_coverage_runtime_uses_canonical_otel_switch() -> None:
    """Coverage NGINX config must not enable a reject-only directive."""
    script = COVERAGE_SCRIPT.read_text(encoding="utf-8")

    assert "markdown_otel on;" in script
    assert "markdown_otel_tracing on;" not in script


def test_coverage_runtime_avoids_rejected_streaming_cache_combination() -> None:
    """Every generated location must satisfy the streaming/cache contract."""
    script = COVERAGE_SCRIPT.read_text(encoding="utf-8")
    assert not _conflicting_location_blocks(script)


def test_streaming_failure_cache_runtime_avoids_rejected_combination() -> None:
    """The native failure/cache E2E config must pass config merge."""
    script = STREAMING_FAILURE_CACHE_SCRIPT.read_text(encoding="utf-8")

    assert not _conflicting_location_blocks(script)
