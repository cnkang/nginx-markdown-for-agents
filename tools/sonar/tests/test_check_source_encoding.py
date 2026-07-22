#!/usr/bin/env python3
"""Tests for tools/sonar/check_source_encoding.py.

These tests exercise the encoding checker against the real repository
fixtures and validate exception-manifest semantics."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER = REPO_ROOT / "tools" / "sonar" / "check_source_encoding.py"
MANIFEST = REPO_ROOT / "tools" / "sonar" / "encoding_exceptions.json"
PROPERTIES = REPO_ROOT / ".sonarcloud.properties"


def load_checker_module():
    """Load the checker directly so tmp_path cases do not touch repository data."""
    spec = importlib.util.spec_from_file_location("encoding_checker_test", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def test_missing_repository_manifest_reports_invalid_utf8(self) -> None:
        """Without the manifest, the intentional ISO-8859-1 fixture is unlisted."""
        result = run_checker("--manifest", "tools/sonar/missing-manifest.json")
        assert result.returncode == 1, result.stderr
        assert "latin1.html" in result.stderr

    def test_manifest_outside_repository_is_rejected(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        result = run_checker("--manifest", str(manifest))
        assert result.returncode == 2
        assert "repository-relative" in result.stderr

    def test_unknown_option_exits_nonzero(self) -> None:
        result = run_checker("--bogus-flag")
        assert result.returncode != 0


class TestCheckerAdversarialInputs:
    """Validate path, binary, and generated-root contracts in isolation."""

    def test_manifest_rejects_escape_and_empty_contract_fields(self, tmp_path: Path) -> None:
        checker = load_checker_module()
        checker.REPO_ROOT = tmp_path
        (tmp_path / "fixture.html").write_bytes(b"\xe9")
        for payload in (
            {"../fixture.html": {"encoding": "latin-1", "reason": "test"}},
            {"fixture.html": {"encoding": "", "reason": "test"}},
            {"fixture.html": {"encoding": "latin-1", "reason": 1}},
        ):
            manifest = tmp_path / "manifest.json"
            manifest.write_text(json.dumps(payload), encoding="utf-8")
            with pytest.raises(RuntimeError):
                checker._load_manifest(Path("manifest.json"))

    def test_resolve_repository_path_rejects_absolute_and_parent_paths(
        self, tmp_path: Path
    ) -> None:
        checker = load_checker_module()
        checker.REPO_ROOT = tmp_path

        for path in (tmp_path / "manifest.json", Path("nested/../manifest.json")):
            with pytest.raises(RuntimeError, match="repository-relative"):
                checker._resolve_repository_path(path, "Test path")

    def test_manifest_symlink_outside_repository_is_rejected(self, tmp_path: Path) -> None:
        checker = load_checker_module()
        checker.REPO_ROOT = tmp_path
        outside = tmp_path.parent / "outside-manifest.json"
        outside.write_text("{}", encoding="utf-8")
        manifest = tmp_path / "manifest.json"
        manifest.symlink_to(outside)
        with pytest.raises(RuntimeError, match="within the repository"):
            checker._load_manifest(Path("manifest.json"))

    def test_extensionless_file_root_and_nul_are_checked(self, tmp_path: Path) -> None:
        checker = load_checker_module()
        checker.REPO_ROOT = tmp_path
        config = tmp_path / "components" / "nginx-module" / "config"
        config.parent.mkdir(parents=True)
        config.write_bytes(b"ok\0bad")
        checker.SONAR_ROOTS = [Path("components/nginx-module/config")]
        rc, count = checker._audit_generated({})
        assert rc == 1
        assert count == 1


class TestSonarConfiguration:
    """The intentional fixture remains a test-only exclusion, never a source exclusion."""

    def test_latin1_is_precisely_test_excluded(self) -> None:
        props = PROPERTIES.read_text(encoding="utf-8")
        assert "sonar.test.exclusions=tests/corpus/encoding/latin1.html" in props
        source_line = next(line for line in props.splitlines() if line.startswith("sonar.exclusions="))
        assert "latin1.html" not in source_line
        assert "tests/corpus/**" not in source_line


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
