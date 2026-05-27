"""Unit tests for check_packaging_consistency helper functions.

Focuses on regex/parser boundary cases for section extraction,
curl parsing, matrix table parsing, and artifact name validation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow imports from tools/docs/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_packaging_consistency import (
    ARTIFACT_RE,
    _extract_curl_hosts,
    _extract_curl_paths,
    _extract_nginx_code_blocks,
    _extract_nginx_location_paths,
    _extract_quick_start,
    _extract_verification_curls,
    _parse_matrix_table,
)


# ---------------------------------------------------------------------------
# _extract_quick_start
# ---------------------------------------------------------------------------

class TestExtractQuickStart:
    """Tests for _extract_quick_start section extraction."""

    def test_extracts_section(self):
        """Verify content between '## Quick Start' and the next H2 is returned."""
        result = self._assert_quick_start_contains(
            "## Quick Start\nContent\n## Next Section\nOther\n", "Content"
        )
        assert "Other" not in result

    def test_returns_empty_when_missing(self):
        """Verify empty string is returned when no Quick Start section exists."""
        assert _extract_quick_start("## Other\nStuff\n") == ""

    def test_captures_to_eof_if_last_section(self):
        """Verify extraction continues to end of file when no next H2 exists."""
        self._assert_quick_start_contains(
            "## Quick Start\nContent\nMore content\n", "More content"
        )

    def test_does_not_match_subsection(self):
        """Verify H3 '### Quick Start' is not treated as an H2 section."""
        text = "### Quick Start\nNot this\n## Real\nYes\n"
        assert _extract_quick_start(text) == ""

    def test_stops_at_next_h2(self):
        """Verify extraction includes subsections but stops at the next H2."""
        result = self._assert_quick_start_contains(
            "## Quick Start\nA\n### Sub\nB\n## Next\nC\n", "A"
        )
        assert "B" in result
        assert "C" not in result

    def _assert_quick_start_contains(self, text, expected):
        """Run _extract_quick_start and assert expected substring is present."""
        result = _extract_quick_start(text)
        assert expected in result
        return result


# ---------------------------------------------------------------------------
# _extract_verification_curls
# ---------------------------------------------------------------------------

class TestExtractVerificationCurls:
    """Tests for _extract_verification_curls curl command parsing."""

    def test_extracts_markdown_curl(self):
        """Verify a curl command with Accept: text/markdown is extracted."""
        result = self._assert_single_curl(
            'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/\n'
        )
        assert "text/markdown" in result[0]

    def test_extracts_html_curl(self):
        """Verify a curl command with Accept: text/html is extracted."""
        self._assert_single_curl(
            'curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/\n'
        )

    def test_ignores_download_curl(self):
        """Verify curl commands piping to shell (download scripts) are ignored."""
        text = "curl -sSL https://raw.githubusercontent.com/.../install.sh | sudo bash\n"
        assert _extract_verification_curls(text) == []

    def test_ignores_metrics_curl(self):
        """Verify curl commands targeting metrics endpoints are ignored."""
        text = "curl http://localhost/markdown-metrics\n"
        assert _extract_verification_curls(text) == []

    def test_normalises_whitespace(self):
        """Verify extra whitespace in curl commands is collapsed."""
        result = self._assert_single_curl(
            '  curl   -sD -   -o /dev/null   -H "Accept: text/markdown"   http://localhost/  \n'
        )
        assert "  " not in result[0]  # no double spaces

    def _assert_single_curl(self, text):
        """Run _extract_verification_curls and assert exactly one result."""
        result = _extract_verification_curls(text)
        assert len(result) == 1
        return result

    def test_multiple_curls(self):
        """Verify multiple verification curl commands are all extracted."""
        text = (
            'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/\n'
            'curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/\n'
        )
        assert len(_extract_verification_curls(text)) == 2

    def test_ignores_non_curl_lines(self):
        """Verify lines mentioning 'curl' but not as a command are ignored."""
        text = "This mentions curl but is not a command\n"
        assert _extract_verification_curls(text) == []


# ---------------------------------------------------------------------------
# _extract_nginx_code_blocks
# ---------------------------------------------------------------------------

class TestExtractNginxCodeBlocks:
    """Tests for _extract_nginx_code_blocks fenced block extraction."""

    def test_extracts_nginx_block(self):
        """Verify content inside ```nginx fenced blocks is extracted."""
        text = "```nginx\nmarkdown_filter on;\n```\n"
        assert "markdown_filter on;" in _extract_nginx_code_blocks(text)

    def test_ignores_bash_block(self):
        """Verify ```bash blocks are not included in the result."""
        text = "```bash\ncurl http://x\n```\n"
        assert _extract_nginx_code_blocks(text) == ""

    def test_multiple_blocks(self):
        """Verify content from multiple ```nginx blocks is concatenated."""
        text = "```nginx\na;\n```\nText\n```nginx\nb;\n```\n"
        result = _extract_nginx_code_blocks(text)
        assert "a;" in result
        assert "b;" in result

    def test_ignores_plain_code_block(self):
        """Verify unlabeled ``` blocks (no language tag) are ignored."""
        text = "```\nplain code\n```\n"
        assert _extract_nginx_code_blocks(text) == ""

    def test_empty_input(self):
        """Verify empty input returns an empty string."""
        assert _extract_nginx_code_blocks("") == ""


# ---------------------------------------------------------------------------
# _extract_curl_paths
# ---------------------------------------------------------------------------

class TestExtractCurlPaths:
    """Tests for _extract_curl_paths URL path extraction."""

    def test_extracts_root_path(self):
        """Verify the root path '/' is extracted from a localhost URL."""
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/\n'
        assert "/" in _extract_curl_paths(text)

    def test_extracts_subpath(self):
        """Verify a subpath like '/docs/' is extracted correctly."""
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/docs/\n'
        assert "/docs/" in _extract_curl_paths(text)

    def test_defaults_to_root_when_no_path(self):
        """Verify root '/' is returned when the URL has no trailing path."""
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost\n'
        assert "/" in _extract_curl_paths(text)

    def test_strips_query_string(self):
        """Verify query strings are stripped from extracted paths."""
        self._assert_base_path_extracted(
            'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/foo?bar=baz\n',
            "/foo?bar=baz",
        )

    def test_strips_fragment(self):
        """Verify URL fragments are stripped from extracted paths."""
        self._assert_base_path_extracted(
            'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/foo#section\n',
            "/foo#section",
        )

    def _assert_base_path_extracted(self, text, raw_path):
        """Assert '/foo' is in results but the raw path with query/fragment is not."""
        paths = _extract_curl_paths(text)
        assert "/foo" in paths
        assert raw_path not in paths


# ---------------------------------------------------------------------------
# _extract_curl_hosts
# ---------------------------------------------------------------------------

class TestExtractCurlHosts:
    """Tests for _extract_curl_hosts host extraction."""

    def test_extracts_localhost(self):
        """Verify 'localhost' is extracted as a host from a verification curl."""
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/\n'
        assert "localhost" in _extract_curl_hosts(text)

    def test_extracts_host_with_port(self):
        """Verify host:port like '127.0.0.1:8080' is extracted correctly."""
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://127.0.0.1:8080/\n'
        assert "127.0.0.1:8080" in _extract_curl_hosts(text)

    def test_empty_for_non_verification_curl(self):
        """Verify non-verification curl commands return an empty set."""
        text = "curl -sSL https://example.com/install.sh\n"
        assert _extract_curl_hosts(text) == set()


# ---------------------------------------------------------------------------
# _extract_nginx_location_paths
# ---------------------------------------------------------------------------

class TestExtractNginxLocationPaths:
    """Tests for _extract_nginx_location_paths location directive extraction."""

    def test_extracts_location(self):
        """Verify a location directive path '/docs/' is extracted from nginx blocks."""
        text = '```nginx\nlocation /docs/ {\n    proxy_pass http://backend;\n}\n```\n'
        assert "/docs/" in _extract_nginx_location_paths(text)

    def test_extracts_root_location(self):
        """Verify the root location '/' is extracted correctly."""
        text = '```nginx\nlocation / {\n    root /var/www;\n}\n```\n'
        assert "/" in _extract_nginx_location_paths(text)

    def test_no_locations(self):
        """Verify empty set is returned when no location directives exist."""
        text = '```nginx\nmarkdown_filter on;\n```\n'
        assert _extract_nginx_location_paths(text) == set()


# ---------------------------------------------------------------------------
# _parse_matrix_table
# ---------------------------------------------------------------------------

class TestParseMatrixTable:
    """Tests for _parse_matrix_table markdown table parsing."""

    def test_parses_valid_table(self):
        """Verify a valid markdown table is parsed into correct tuples."""
        rows = self._assert_table_row_count(
            "| NGINX Version | OS Type | Architecture | Support Tier |\n"
            "|---|---|---|---|\n"
            "| 1.24.0 | glibc | x86_64 | Full |\n"
            "| 1.26.3 | musl | aarch64 | Full |\n",
            2,
        )
        assert rows[0] == ("1.24.0", "glibc", "x86_64", "Full")
        assert rows[1] == ("1.26.3", "musl", "aarch64", "Full")

    def test_skips_header_and_separator(self):
        """Verify header row and separator row are excluded from results."""
        self._assert_table_row_count(
            "| NGINX Version | OS Type | Architecture | Support Tier |\n"
            "|---|---|---|---|\n"
            "| 1.24.0 | glibc | x86_64 | Full |\n",
            1,
        )

    def test_skips_non_pipe_lines(self):
        """Verify lines not starting with '|' are ignored."""
        self._assert_table_row_count(
            "Some text\n| 1.24.0 | glibc | x86_64 | Full |\nMore text\n", 1
        )

    def test_skips_short_rows(self):
        """Verify rows with fewer than 4 columns are excluded."""
        self._assert_table_row_count(
            "| only | two |\n| 1.24.0 | glibc | x86_64 | Full |\n", 1
        )

    def _assert_table_row_count(self, table, expected_count):
        """Parse table and assert the expected number of data rows."""
        result = _parse_matrix_table(table)
        assert len(result) == expected_count
        return result

    def test_empty_input(self):
        """Verify empty input returns an empty list."""
        assert _parse_matrix_table("") == []

    def test_whitespace_in_cells(self):
        """Verify leading/trailing whitespace in cells is stripped."""
        table = "|  1.24.0  |  glibc  |  x86_64  |  Full  |\n"
        rows = _parse_matrix_table(table)
        assert rows[0] == ("1.24.0", "glibc", "x86_64", "Full")


# ---------------------------------------------------------------------------
# ARTIFACT_RE
# ---------------------------------------------------------------------------

class TestArtifactRegex:
    """Tests for the ARTIFACT_RE compiled regex pattern."""

    @pytest.mark.parametrize("name", [
        "ngx_http_markdown_filter_module-1.24.0-glibc-x86_64.tar.gz",
        "ngx_http_markdown_filter_module-1.26.3-musl-aarch64.tar.gz",
        "ngx_http_markdown_filter_module-1.29.6-glibc-aarch64.tar.gz",
    ])
    def test_valid_names_match(self, name):
        """Verify valid artifact filenames match the regex fully."""
        assert ARTIFACT_RE.fullmatch(name)

    @pytest.mark.parametrize("name", [
        "ngx_http_markdown_filter_module-1.24.0-glibc-arm.tar.gz",
        "ngx_http_markdown_filter_module-1.24.0-uclibc-x86_64.tar.gz",
        "ngx_http_markdown_filter_module-1.24-glibc-x86_64.tar.gz",
        "other-module-1.24.0-glibc-x86_64.tar.gz",
        "ngx_http_markdown_filter_module-1.24.0-glibc-x86_64.tar.gz.sha256",
    ])
    def test_invalid_names_rejected(self, name):
        """Verify invalid artifact filenames do not match the regex."""
        assert not ARTIFACT_RE.fullmatch(name)


# ---------------------------------------------------------------------------
# Integration tests for higher-level check functions
# ---------------------------------------------------------------------------

import check_packaging_consistency as cpc
from check_packaging_consistency import check_artifact_names, check_curl_pattern


class TestCheckArtifactNames:
    """Integration tests for check_artifact_names with synthetic docs."""

    def test_valid_artifacts_pass(self, tmp_path, monkeypatch):
        """Verify valid artifact names produce no errors."""
        doc = tmp_path / "INSTALL.md"
        doc.write_text(
            "Download: ngx_http_markdown_filter_module-1.26.3-glibc-x86_64.tar.gz\n"
        )
        monkeypatch.setattr(cpc, "INSTALL_GUIDE", doc)
        assert check_artifact_names() == []

    def test_invalid_artifact_flagged(self, tmp_path, monkeypatch):
        """Verify invalid artifact names produce an error mentioning the issue."""
        doc = tmp_path / "INSTALL.md"
        doc.write_text(
            "Download: ngx_http_markdown_filter_module-1.26.3-uclibc-arm.tar.gz\n"
        )
        monkeypatch.setattr(cpc, "INSTALL_GUIDE", doc)
        errors = check_artifact_names()
        assert len(errors) == 1
        assert "uclibc" in errors[0]

    def test_no_artifacts_passes(self, tmp_path, monkeypatch):
        """Verify docs with no artifact references produce no errors."""
        doc = tmp_path / "INSTALL.md"
        doc.write_text("No artifact references here.\n")
        monkeypatch.setattr(cpc, "INSTALL_GUIDE", doc)
        assert check_artifact_names() == []


class TestCheckCurlPattern:
    """Integration tests for check_curl_pattern with synthetic docs."""

    def test_correct_pattern_passes(self, tmp_path, monkeypatch):
        """Verify a correctly formatted verification curl produces no errors."""
        doc = tmp_path / "INSTALL.md"
        doc.write_text(
            'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/\n'
        )
        monkeypatch.setattr(cpc, "INSTALL_GUIDE", doc)
        assert check_curl_pattern() == []

    def test_missing_pattern_flagged(self, tmp_path, monkeypatch):
        """Verify a curl missing required flags produces an error."""
        doc = tmp_path / "INSTALL.md"
        doc.write_text(
            'curl -H "Accept: text/markdown" http://localhost/\n'
        )
        monkeypatch.setattr(cpc, "INSTALL_GUIDE", doc)
        errors = check_curl_pattern()
        assert len(errors) == 1
        assert "-sD - -o /dev/null" in errors[0]

    def test_non_verification_curls_ignored(self, tmp_path, monkeypatch):
        """Verify non-verification curl commands are not flagged."""
        doc = tmp_path / "INSTALL.md"
        doc.write_text(
            "curl -sSL https://example.com/install.sh | sudo bash\n"
        )
        monkeypatch.setattr(cpc, "INSTALL_GUIDE", doc)
        assert check_curl_pattern() == []
