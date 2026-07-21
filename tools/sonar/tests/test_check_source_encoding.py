#!/usr/bin/env python3
"""Tests for tools/sonar/check_source_encoding.py.

These tests exercise the encoding checker against the real repository
fixtures and validate exception-manifest semantics."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER = REPO_ROOT / "tools" / "sonar" / "check_source_encoding.py"
MANIFEST = REPO_ROOT / "tools" / "sonar" / "encoding_exceptions.json"


def run_checker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


# ---------------------------------------------------------------------------
# Exception manifest validation
# ---------------------------------------------------------------------------


class TestExceptionManifest:
    """The encoding_exceptions.json must be valid and consistent."""

    @staticmethod
    def _load() -> dict[str, dict[str, object]]:
        assert MANIFEST.exists(), "encoding_exceptions.json is missing"
        return json.loads(MANIFEST.read_bytes())

    def test_latin1_exception_present(self) -> None:
        manifest = self._load()
        assert "tests/corpus/encoding/latin1.html" in manifest

    def test_latin1_declared_iso_8859_1(self) -> None:
        manifest = self._load()
        info = manifest["tests/corpus/encoding/latin1.html"]
        assert str(info["encoding"]).upper() == "ISO-8859-1"

    def test_latin1_has_reason(self) -> None:
        manifest = self._load()
        info = manifest["tests/corpus/encoding/latin1.html"]
        assert isinstance(info.get("reason"), str) and len(info["reason"]) > 0

    def test_latin1_is_not_valid_utf8(self) -> None:
        path = REPO_ROOT / "tests" / "corpus" / "encoding" / "latin1.html"
        data = path.read_bytes()
        with pytest.raises(UnicodeDecodeError):
            data.decode("utf-8", errors="strict")

    def test_latin1_is_valid_declared_encoding(self) -> None:
        path = REPO_ROOT / "tests" / "corpus" / "encoding" / "latin1.html"
        data = path.read_bytes()
        manifest = self._load()
        declared = str(manifest["tests/corpus/encoding/latin1.html"]["encoding"])
        data.decode(declared, errors="strict")

    def test_keys_are_repo_relative_and_exist(self) -> None:
        manifest = self._load()
        for rel in manifest:
            assert not os.path.isabs(rel), f"Exception key must be relative: {rel}"
            target = REPO_ROOT / rel
            assert target.exists(), f"Exception references missing path: {rel}"


# ---------------------------------------------------------------------------
# Checker CLI behaviour
# ---------------------------------------------------------------------------


class TestCheckerCLI:
    """The checker must exit 0 when exceptions are valid."""

    def test_include_exceptions_passes(self) -> None:
        result = run_checker("--include-exceptions")
        assert result.returncode == 0, result.stderr

    def test_include_generated_passes(self) -> None:
        result = run_checker("--include-exceptions", "--include-generated")
        assert result.returncode == 0, result.stderr

    def test_output_mentions_tracked_count(self) -> None:
        result = run_checker()
        assert result.returncode == 0
        assert "tracked text files" in result.stdout

    def test_manifest_missing_reports_invalid_utf8(self) -> None:
        """Without the manifest, the intentional ISO-8859-1 fixture is unlisted."""
        result = run_checker("--manifest", "/nonexistent/manifest.json")
        assert result.returncode == 1, result.stderr
        assert "latin1.html" in result.stderr

    def test_unknown_option_exits_nonzero(self) -> None:
        result = run_checker("--bogus-flag")
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# Fixture byte contracts
# ---------------------------------------------------------------------------


class TestCharsetFixtures:
    """Verify declared encoding matches actual bytes."""

    def test_latin1_fixture_has_single_byte_accented_chars(self) -> None:
        path = REPO_ROOT / "tests" / "corpus" / "encoding" / "latin1.html"
        data = path.read_bytes()
        assert b"\xe9" in data, "latin1.html missing 0xE9 (é in ISO-8859-1)"
        assert b"\xc3\xa9" not in data, "latin1.html contains UTF-8 é"
        with pytest.raises(UnicodeDecodeError):
            data.decode("utf-8", errors="strict")

    def test_mixed_charset_fixture_is_valid_utf8(self) -> None:
        path = REPO_ROOT / "tests" / "corpus" / "streaming-perf" / "mixed-charset.html"
        if not path.exists():
            pytest.skip("mixed-charset.html not present")
        data = path.read_bytes()
        data.decode("utf-8", errors="strict")

    def test_charset_mismatch_fixture_is_valid_utf8(self) -> None:
        path = REPO_ROOT / "tests" / "corpus" / "streaming" / "charset-mismatch.html"
        if not path.exists():
            pytest.skip("charset-mismatch.html not present")
        data = path.read_bytes()
        data.decode("utf-8", errors="strict")
        meta = path.with_suffix(".meta.json")
        if meta.exists():
            meta_data = json.loads(meta.read_bytes())
            assert meta_data.get("source-description") == "Content-Type and meta charset mismatch handling"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
