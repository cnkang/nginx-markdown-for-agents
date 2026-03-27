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
    _normalise_curl_for_comparison,
    _parse_matrix_table,
)


# ---------------------------------------------------------------------------
# _extract_quick_start
# ---------------------------------------------------------------------------

class TestExtractQuickStart:
    def test_extracts_section(self):
        result = self._extracted_from_test_stops_at_next_h2_2(
            "## Quick Start\nContent\n## Next Section\nOther\n", "Content"
        )
        assert "Other" not in result

    def test_returns_empty_when_missing(self):
        assert _extract_quick_start("## Other\nStuff\n") == ""

    def test_captures_to_eof_if_last_section(self):
        result = self._extracted_from_test_stops_at_next_h2_2(
            "## Quick Start\nContent\nMore content\n", "More content"
        )

    def test_does_not_match_subsection(self):
        text = "### Quick Start\nNot this\n## Real\nYes\n"
        assert _extract_quick_start(text) == ""

    def test_stops_at_next_h2(self):
        result = self._extracted_from_test_stops_at_next_h2_2(
            "## Quick Start\nA\n### Sub\nB\n## Next\nC\n", "A"
        )
        assert "B" in result
        assert "C" not in result

    # TODO Rename this here and in `test_extracts_section`, `test_captures_to_eof_if_last_section` and `test_stops_at_next_h2`
    def _extracted_from_test_stops_at_next_h2_2(self, arg0, arg1):
        text = arg0
        result = _extract_quick_start(text)
        assert arg1 in result
        return result


# ---------------------------------------------------------------------------
# _extract_verification_curls
# ---------------------------------------------------------------------------

class TestExtractVerificationCurls:
    def test_extracts_markdown_curl(self):
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/\n'
        result = _extract_verification_curls(text)
        assert len(result) == 1
        assert "text/markdown" in result[0]

    def test_extracts_html_curl(self):
        text = 'curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/\n'
        result = _extract_verification_curls(text)
        assert len(result) == 1

    def test_ignores_download_curl(self):
        text = "curl -sSL https://raw.githubusercontent.com/.../install.sh | sudo bash\n"
        assert _extract_verification_curls(text) == []

    def test_ignores_metrics_curl(self):
        text = "curl http://localhost/markdown-metrics\n"
        assert _extract_verification_curls(text) == []

    def test_normalises_whitespace(self):
        text = '  curl   -sD -   -o /dev/null   -H "Accept: text/markdown"   http://localhost/  \n'
        result = _extract_verification_curls(text)
        assert len(result) == 1
        assert "  " not in result[0]  # no double spaces

    def test_multiple_curls(self):
        text = (
            'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/\n'
            'curl -sD - -o /dev/null -H "Accept: text/html" http://localhost/\n'
        )
        assert len(_extract_verification_curls(text)) == 2

    def test_ignores_non_curl_lines(self):
        text = "This mentions curl but is not a command\n"
        assert _extract_verification_curls(text) == []


# ---------------------------------------------------------------------------
# _normalise_curl_for_comparison
# ---------------------------------------------------------------------------

class TestNormaliseCurl:
    def test_identity(self):
        cmd = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/'
        assert _normalise_curl_for_comparison(cmd) == cmd


# ---------------------------------------------------------------------------
# _extract_nginx_code_blocks
# ---------------------------------------------------------------------------

class TestExtractNginxCodeBlocks:
    def test_extracts_nginx_block(self):
        text = "```nginx\nmarkdown_filter on;\n```\n"
        assert "markdown_filter on;" in _extract_nginx_code_blocks(text)

    def test_ignores_bash_block(self):
        text = "```bash\ncurl http://x\n```\n"
        assert _extract_nginx_code_blocks(text) == ""

    def test_multiple_blocks(self):
        text = "```nginx\na;\n```\nText\n```nginx\nb;\n```\n"
        result = _extract_nginx_code_blocks(text)
        assert "a;" in result
        assert "b;" in result

    def test_ignores_plain_code_block(self):
        text = "```\nplain code\n```\n"
        assert _extract_nginx_code_blocks(text) == ""

    def test_empty_input(self):
        assert _extract_nginx_code_blocks("") == ""


# ---------------------------------------------------------------------------
# _extract_curl_paths
# ---------------------------------------------------------------------------

class TestExtractCurlPaths:
    def test_extracts_root_path(self):
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/\n'
        assert "/" in _extract_curl_paths(text)

    def test_extracts_subpath(self):
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/docs/\n'
        assert "/docs/" in _extract_curl_paths(text)

    def test_defaults_to_root_when_no_path(self):
        # URL with no trailing path component
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost\n'
        assert "/" in _extract_curl_paths(text)


# ---------------------------------------------------------------------------
# _extract_curl_hosts
# ---------------------------------------------------------------------------

class TestExtractCurlHosts:
    def test_extracts_localhost(self):
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://localhost/\n'
        assert "localhost" in _extract_curl_hosts(text)

    def test_extracts_host_with_port(self):
        text = 'curl -sD - -o /dev/null -H "Accept: text/markdown" http://127.0.0.1:8080/\n'
        assert "127.0.0.1:8080" in _extract_curl_hosts(text)

    def test_empty_for_non_verification_curl(self):
        text = "curl -sSL https://example.com/install.sh\n"
        assert _extract_curl_hosts(text) == set()


# ---------------------------------------------------------------------------
# _extract_nginx_location_paths
# ---------------------------------------------------------------------------

class TestExtractNginxLocationPaths:
    def test_extracts_location(self):
        text = '```nginx\nlocation /docs/ {\n    proxy_pass http://backend;\n}\n```\n'
        assert "/docs/" in _extract_nginx_location_paths(text)

    def test_extracts_root_location(self):
        text = '```nginx\nlocation / {\n    root /var/www;\n}\n```\n'
        assert "/" in _extract_nginx_location_paths(text)

    def test_no_locations(self):
        text = '```nginx\nmarkdown_filter on;\n```\n'
        assert _extract_nginx_location_paths(text) == set()


# ---------------------------------------------------------------------------
# _parse_matrix_table
# ---------------------------------------------------------------------------

class TestParseMatrixTable:
    def test_parses_valid_table(self):
        rows = self._extracted_from_test_skips_short_rows_2(
            "| NGINX Version | OS Type | Architecture | Support Tier |\n"
            "|---|---|---|---|\n"
            "| 1.24.0 | glibc | x86_64 | Full |\n"
            "| 1.26.3 | musl | aarch64 | Full |\n",
            2,
        )
        assert rows[0] == ("1.24.0", "glibc", "x86_64", "Full")
        assert rows[1] == ("1.26.3", "musl", "aarch64", "Full")

    def test_skips_header_and_separator(self):
        rows = self._extracted_from_test_skips_short_rows_2(
            "| NGINX Version | OS Type | Architecture | Support Tier |\n"
            "|---|---|---|---|\n"
            "| 1.24.0 | glibc | x86_64 | Full |\n",
            1,
        )

    def test_skips_non_pipe_lines(self):
        rows = self._extracted_from_test_skips_short_rows_2(
            "Some text\n| 1.24.0 | glibc | x86_64 | Full |\nMore text\n", 1
        )

    def test_skips_short_rows(self):
        rows = self._extracted_from_test_skips_short_rows_2(
            "| only | two |\n| 1.24.0 | glibc | x86_64 | Full |\n", 1
        )

    # TODO Rename this here and in `test_parses_valid_table`, `test_skips_header_and_separator`, `test_skips_non_pipe_lines` and `test_skips_short_rows`
    def _extracted_from_test_skips_short_rows_2(self, arg0, arg1):
        table = arg0
        result = _parse_matrix_table(table)
        assert len(result) == arg1
        return result

    def test_empty_input(self):
        assert _parse_matrix_table("") == []

    def test_whitespace_in_cells(self):
        table = "|  1.24.0  |  glibc  |  x86_64  |  Full  |\n"
        rows = _parse_matrix_table(table)
        assert rows[0] == ("1.24.0", "glibc", "x86_64", "Full")


# ---------------------------------------------------------------------------
# ARTIFACT_RE
# ---------------------------------------------------------------------------

class TestArtifactRegex:
    @pytest.mark.parametrize("name", [
        "ngx_http_markdown_filter_module-1.24.0-glibc-x86_64.tar.gz",
        "ngx_http_markdown_filter_module-1.26.3-musl-aarch64.tar.gz",
        "ngx_http_markdown_filter_module-1.29.6-glibc-aarch64.tar.gz",
    ])
    def test_valid_names_match(self, name):
        assert ARTIFACT_RE.fullmatch(name)

    @pytest.mark.parametrize("name", [
        "ngx_http_markdown_filter_module-1.24.0-glibc-arm.tar.gz",
        "ngx_http_markdown_filter_module-1.24.0-uclibc-x86_64.tar.gz",
        "ngx_http_markdown_filter_module-1.24-glibc-x86_64.tar.gz",
        "other-module-1.24.0-glibc-x86_64.tar.gz",
        "ngx_http_markdown_filter_module-1.24.0-glibc-x86_64.tar.gz.sha256",
    ])
    def test_invalid_names_rejected(self, name):
        assert not ARTIFACT_RE.fullmatch(name)
