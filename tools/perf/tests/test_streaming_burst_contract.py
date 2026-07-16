"""Contract tests for the native continuous-compression smoke."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
NATIVE_STREAMING_E2E = (
    REPO_ROOT / "tools" / "e2e" / "verify_chunked_streaming_native_e2e.sh"
)


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
