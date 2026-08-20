"""Contract tests for native streaming smoke budget routing."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
NATIVE_STREAMING_E2E = (
    REPO_ROOT / "tools" / "e2e" / "verify_chunked_streaming_native_e2e.sh"
)


def _location_block(source: str, location: str) -> str:
    marker = f"location {location} {{"
    assert marker in source, (
        f"location block {location!r} not found in NATIVE_STREAMING_E2E"
    )
    start = source.index(marker)
    terminator = "\n        }"
    assert terminator in source[start:], (
        f"location block {location!r} has no closing terminator "
        f"{terminator.strip()!r} in NATIVE_STREAMING_E2E"
    )
    end = source.index(terminator, start)
    return source[start:end]


def test_native_e2e_reserves_full_budget_for_conversion_routes():
    source = NATIVE_STREAMING_E2E.read_text(encoding="utf-8")

    for location in ("/streaming/", "/streaming-zero-copy/"):
        block = _location_block(source, location)
        assert "streaming_buffer=${MARKDOWN_MAX_SIZE}" in block
        assert "decompression_ratio=1000;" in block
        assert "streaming_buffer=256k" not in block

    bounded_block = _location_block(source, "/streaming-256k/")
    assert "streaming_buffer=256k" in bounded_block
    assert "decompression_ratio=1000;" in bounded_block


def test_native_e2e_covers_256k_continuous_compression_bursts():
    source = NATIVE_STREAMING_E2E.read_text(encoding="utf-8")

    assert "streaming_buffer=256k" in source
    assert "CONTINUOUS_BURST_TARGET" in source
    assert "continuous-burst-gzip" in source
    assert "continuous-burst-deflate" in source
    assert "Z_SYNC_FLUSH" not in source
    assert "precommit_failopen_total" in source
    assert "budget_exceeded_total" in source
    assert "decompression_streaming_total" in source
    assert "cmp -s" in source
