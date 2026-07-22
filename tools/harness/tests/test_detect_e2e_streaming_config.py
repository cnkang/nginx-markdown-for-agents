"""Pytest tests for detect_e2e_streaming_config.py — comment masking,
nested-location isolation, fail-closed scan errors, and CLI contract.

Rule 60 (e2e-runner): Validates that the block-aware detector correctly
handles comment braces, nested locations, read failures, and parse errors.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS_DIR))

from harness.detect_e2e_streaming_config import (  # noqa: E402
    Finding,
    ScanError,
    _extract_direct_depth_block,
    _extract_nginx_from_rust,
    _extract_nginx_from_shell,
    _extract_location_blocks,
    _mask_nginx_comments,
    _LocationBlock,
    scan_file,
)

DETECTOR = Path(__file__).resolve().parent.parent / "detect_e2e_streaming_config.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


def _scan(files: dict[str, str], tmp_path: Path) -> tuple[list[Finding], list[ScanError]]:
    root = _make_repo(tmp_path, files)
    findings: list[Finding] = []
    errors: list[ScanError] = []
    for rel in files:
        p = root / rel
        f, e = scan_file(p, root)
        findings.extend(f)
        errors.extend(e)
    return findings, errors


# ---------------------------------------------------------------------------
# P1-7: nginx comment masking before brace parsing
# ---------------------------------------------------------------------------

class TestCommentMasking:
    """Comments containing ``{`` or ``}`` must not affect brace depth."""

    def test_comment_with_open_brace(self) -> None:
        blocks = self._extract_blocks(
            "location /a {\n    # example: location /ignored {\n    markdown_cache_validation full;\n}\n"
        )
        assert len(blocks) == 1
        assert blocks[0].path == "/a"

    def test_comment_with_close_brace(self) -> None:
        blocks = self._extract_blocks(
            "location /a {\n"
            "    # close: }\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "location /b {\n"
            "    markdown_streaming off;\n"
            "}\n"
        )
        paths = [b.path for b in blocks]
        assert "/a" in paths
        assert "/b" in paths

    def _extract_blocks(self, text: str) -> list[_LocationBlock]:
        """Extract location blocks from text with comment masking."""
        masked = _mask_nginx_comments(text)
        result, errors = _extract_location_blocks(text, masked)
        assert not errors
        return result

    def test_quote_protects_hash(self) -> None:
        """``#`` inside a quoted string is not a comment start."""
        text = 'location /a {\n    set x "#notcomment";\n    markdown_cache_validation full;\n}\n'
        masked = _mask_nginx_comments(text)
        # The quoted text should survive masking.
        assert "#notcomment" in masked

    def test_adjacent_locations_no_cross_contamination(self, tmp_path: Path) -> None:
        """/a missing streaming; /b has streaming.  /a flagged, /b not."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                "    server {\n"
                "        location /a {\n"
                "            # example: location /ignored {\n"
                "            markdown_cache_validation full;\n"
                "        }\n"
                "        location /b {\n"
                "            markdown_streaming off;\n"
                "            markdown_cache_validation off;\n"
                "        }\n"
                "    }\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        flagged = [f for f in findings if f.loc_path == "/a"]
        assert len(flagged) == 1
        assert "markdown_streaming" in flagged[0].message
        assert all(f.loc_path != "/b" for f in findings)


# ---------------------------------------------------------------------------
# P1-8: parent location isolation from nested location directives
# ---------------------------------------------------------------------------

class TestNestedLocationIsolation:
    """Parent location must not inherit child's directive."""

    def test_parent_not_satisfied_by_child(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                "    server {\n"
                "        location /parent {\n"
                "            markdown_cache_validation full;\n"
                "            location /parent/child {\n"
                "                markdown_streaming off;\n"
                "            }\n"
                "        }\n"
                "    }\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        parent = [f for f in findings if f.loc_path == "/parent"]
        assert len(parent) == 1
        assert "markdown_streaming" in parent[0].message
        # Child has its own explicit streaming → no finding for child.
        assert all(f.loc_path != "/parent/child" for f in findings)

    def test_direct_depth_extraction(self) -> None:
        text = (
            "markdown_cache_validation full;\n"
            "location /child {\n"
            "    markdown_streaming off;\n"
            "}\n"
        )
        block = _LocationBlock(path="/x", content_start=0, content_end=len(text), line_number=1)
        direct, errors = _extract_direct_depth_block(text, block)
        assert not errors
        assert "markdown_cache_validation full" in direct
        # The nested location directive must be blanked out.
        assert "markdown_streaming" not in direct


# ---------------------------------------------------------------------------
# P1-9: fail-closed scan errors
# ---------------------------------------------------------------------------

class TestFailClosed:
    """Read failures and unmatched braces surface as ScanErrors."""

    def test_unmatched_brace(self, tmp_path: Path) -> None:
        # location /broken opens but has no closing brace at all — the body
        # runs to EOF without a matching '}'.  The detector must surface this.
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                "    server {\n"
                "        location /broken {\n"
                "            markdown_cache_validation full;\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert any("unmatched" in e.message.lower() for e in errors)

    def test_read_failure_surfaces_error(self, tmp_path: Path) -> None:
        """mocked read_text raising OSError → ScanError, not silent skip."""
        p = tmp_path / "tools" / "e2e" / "test.sh"
        p.parent.mkdir(parents=True)
        p.write_text("markdown_cache_validation full;\n")

        def _raise(*args, **kwargs):
            raise OSError("mocked read failure")

        with patch.object(Path, "read_text", _raise):
            findings, errors = scan_file(p, tmp_path)
        assert len(errors) >= 1
        assert any("cannot read" in e.message for e in errors)
        assert not findings

    def test_strict_exits_nonzero_on_scan_error(self, tmp_path: Path) -> None:
        """--strict returns 1 on scan errors (unmatched brace)."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                "    server {\n"
                "        location /broken {\n"
                "            markdown_cache_validation full;\n"
                "EOF\n"
            ),
        }
        root = _make_repo(tmp_path, files)
        result = subprocess.run(
            [sys.executable, str(DETECTOR), str(root), "--strict"],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert "SCAN_ERROR" in result.stderr

    def test_non_strict_does_not_print_ok_with_errors(self, tmp_path: Path) -> None:
        """Non-strict mode must not print 'OK' when scan errors exist."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                "    server {\n"
                "        location /broken {\n"
                "            markdown_cache_validation full;\n"
                "EOF\n"
            ),
        }
        root = _make_repo(tmp_path, files)
        result = subprocess.run(
            [sys.executable, str(DETECTOR), str(root)],
            capture_output=True, text=True,
        )
        assert "SCAN_ERROR" in result.stderr
        assert "OK: no contradictory" not in result.stderr

    def test_rust_raw_string(self, tmp_path: Path) -> None:
        """Rust raw-string config is extracted and checked."""
        files = {
            "tools/e2e-harness/src/test.rs": (
                'fn build() {\n'
                '    let c = r#"\n'
                'http {\n'
                '    server {\n'
                '        location /raw/ {\n'
                '            markdown_cache_validation full;\n'
                '        }\n'
                '    }\n'
                '}\n'
                '"#;\n'
                '}\n'
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any(f.loc_path == "/raw/" for f in findings)

    def test_shell_heredoc_intentional_comment_exemption(self, tmp_path: Path) -> None:
        """Intentional auto + full with documented comment → no finding."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                "    server {\n"
                "        # intentional: validates runtime-block mechanism\n"
                "        location /t05/ {\n"
                "            markdown_cache_validation full;\n"
                "            markdown_streaming auto;\n"
                "        }\n"
                "    }\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert not findings

    def test_intentional_comment_missing_reason(self, tmp_path: Path) -> None:
        """auto + full without an intentional comment → finding."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                "    server {\n"
                "        # just a comment\n"
                "        location /bad/ {\n"
                "            markdown_cache_validation full;\n"
                "            markdown_streaming auto;\n"
                "        }\n"
                "    }\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any(f.loc_path == "/bad/" for f in findings)


# ---------------------------------------------------------------------------
# Rust raw string variants
# ---------------------------------------------------------------------------

class TestRustRawStringVariants:
    """Test Rust raw string parsing with various hash counts."""

    def test_zero_hash_raw_string(self, tmp_path: Path) -> None:
        """r\"...\" (zero hashes) should be parsed."""
        files = {
            "tools/e2e-harness/src/test.rs": (
                'fn build() {\n'
                '    let c = r"\n'
                'location /zero/ {\n'
                '    markdown_cache_validation full;\n'
                '}\n'
                '";\n'
                '}\n'
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any(f.loc_path == "/zero/" for f in findings)

    def test_two_hash_raw_string(self, tmp_path: Path) -> None:
        """r##\"...\"## (two hashes) should be parsed."""
        files = {
            "tools/e2e-harness/src/test.rs": (
                'fn build() {\n'
                '    let c = r##"\n'
                'location /two/ {\n'
                '    markdown_cache_validation full;\n'
                '}\n'
                '"##;\n'
                '}\n'
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any(f.loc_path == "/two/" for f in findings)

    def test_three_hash_raw_string(self, tmp_path: Path) -> None:
        """r###\"...\"### (three hashes) should be parsed."""
        files = {
            "tools/e2e-harness/src/test.rs": (
                'fn build() {\n'
                '    let c = r###"\n'
                'location /three/ {\n'
                '    markdown_cache_validation full;\n'
                '}\n'
                '"###;\n'
                '}\n'
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any(f.loc_path == "/three/" for f in findings)

    def test_correct_config_no_finding(self, tmp_path: Path) -> None:
        """Raw string with correct config produces no finding."""
        files = {
            "tools/e2e-harness/src/test.rs": (
                'fn build() {\n'
                '    let c = r#"\n'
                'location /ok/ {\n'
                '    markdown_cache_validation full;\n'
                '    markdown_streaming off;\n'
                '}\n'
                '"#;\n'
                '}\n'
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert not findings

    def test_unterminated_raw_string(self, tmp_path: Path) -> None:
        """Unterminated raw string produces a scan error."""
        files = {
            "tools/e2e-harness/src/test.rs": (
                'fn build() {\n'
                '    let c = r##"\n'
                'location /broken/ {\n'
                '    markdown_cache_validation full;\n'
                '}\n'
                '"#;\n'  # Wrong hash count — not properly closed
                '}\n'
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert any("unterminated" in e.message.lower() for e in errors)

    def test_rust_comment_ignored(self, tmp_path: Path) -> None:
        """Raw strings inside Rust comments should not be scanned."""
        files = {
            "tools/e2e-harness/src/test.rs": (
                '// let c = r#"\n'
                '// location /commented/ {\n'
                '//     markdown_cache_validation full;\n'
                '// }\n'
                '// "#;\n'
                'fn main() {}\n'
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert not findings


# ---------------------------------------------------------------------------
# NGINX location modifier variants
# ---------------------------------------------------------------------------

class TestLocationModifiers:
    """Test that location modifiers (=, ^~, ~, ~*) are correctly parsed."""

    def test_exact_modifier(self, tmp_path: Path) -> None:
        """location = /exact { ... } should be detected."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "location = /exact {\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any(f.loc_path == "/exact" for f in findings)

    def test_prefix_modifier(self, tmp_path: Path) -> None:
        """location ^~ /prefix/ { ... } should be detected."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "location ^~ /prefix/ {\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any(f.loc_path == "/prefix/" for f in findings)

    def test_regex_modifier(self, tmp_path: Path) -> None:
        r"""location ~ \.html$ { ... } should be detected."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                r"location ~ \.html$ {" "\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert len(findings) == 1

    def test_case_insensitive_regex_modifier(self, tmp_path: Path) -> None:
        r"""location ~* \.(png|jpg)$ { ... } should be detected."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                r"location ~* \.(png|jpg)$ {" "\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert len(findings) == 1

    def test_named_location(self, tmp_path: Path) -> None:
        """location @named { ... } should be detected."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "location @fallback {\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any(f.loc_path == "@fallback" for f in findings)


# ---------------------------------------------------------------------------
# Quoted braces and comment isolation
# ---------------------------------------------------------------------------

class TestQuotedBraceAndCommentIsolation:
    """Test that quoted braces don't affect depth and nested comments don't exempt parent."""

    def test_quoted_braces_not_counted(self, tmp_path: Path) -> None:
        """Braces inside quoted strings should not affect block depth."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "location /test {\n"
                '    set $value "{";\n'
                '    add_header X-Debug "{}";\n'
                "    markdown_cache_validation full;\n"
                "    markdown_streaming off;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        # Should NOT be flagged (has explicit streaming off)
        assert not findings

    def test_nested_comment_does_not_exempt_parent(self, tmp_path: Path) -> None:
        """Intentional comment in nested location must not exempt parent."""
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "location /parent {\n"
                "    markdown_cache_validation full;\n"
                "    markdown_streaming auto;\n"
                "    location /parent/child {\n"
                "        # intentional: validates runtime-block mechanism for child\n"
                "        markdown_streaming off;\n"
                "    }\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        # Parent should be flagged (auto + full, no own intentional comment)
        parent = [f for f in findings if f.loc_path == "/parent"]
        assert len(parent) == 1
        # Child should not be flagged
        assert all(f.loc_path != "/parent/child" for f in findings)


# ---------------------------------------------------------------------------
# Quoted regex location paths (deterministic header scanner)
# ---------------------------------------------------------------------------

class TestQuotedLocationRegex:
    """Deterministic location header scanner handles quoted regex paths."""

    def test_quoted_regex_with_spaces(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                'location ~ "^/foo bar/" {\n'
                "    markdown_cache_validation full;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any("foo bar" in f.loc_path for f in findings)

    def test_regex_quantifier_braces(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                r'location ~ "^/item/[0-9]{2}$" {' "\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any("[0-9]{2}" in f.loc_path for f in findings)

    def test_escaped_quote_in_regex(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                r'location ~ "^/path\"end$" {' "\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any("path" in f.loc_path and "end" in f.loc_path for f in findings)

    def test_missing_location_argument(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "location {\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert any("missing argument" in e.message for e in errors)

    def test_missing_block_opener(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "location /path\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert any("missing opening brace" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Strict UTF-8 reading
# ---------------------------------------------------------------------------

class TestStrictUTF8:
    """Invalid UTF-8 must produce ScanError, not silent skip."""

    def test_invalid_utf8_produces_scan_error(self, tmp_path: Path) -> None:
        p = tmp_path / "tools" / "e2e" / "bad.sh"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'\xff\xfe')
        findings, errors = scan_file(p, tmp_path)
        assert not findings
        assert any("UTF-8" in e.message for e in errors)

    def test_valid_utf8_no_error(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "location /ok/ {\n"
                "    markdown_cache_validation full;\n"
                "    markdown_streaming off;\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        encoding_errors = [e for e in errors if "UTF-8" in e.message]
        assert not encoding_errors


# ---------------------------------------------------------------------------
# Heredoc delimiter variants (hyphens, dots)
# ---------------------------------------------------------------------------

class TestHeredocDelimiter:
    """Heredoc delimiters with hyphens and dots must be recognised."""

    def test_hyphen_delimiter(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'NGINX-CONF' > /tmp/nginx.conf\n"
                "location /hyphen/ {\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "NGINX-CONF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any(f.loc_path == "/hyphen/" for f in findings)

    def test_dot_delimiter(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'NGINX.CONF' > /tmp/nginx.conf\n"
                "location /dot/ {\n"
                "    markdown_cache_validation full;\n"
                "}\n"
                "NGINX.CONF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not errors
        assert any(f.loc_path == "/dot/" for f in findings)


# ---------------------------------------------------------------------------
# Rust comment skipping (ordinary strings in comments)
# ---------------------------------------------------------------------------

class TestRustCommentSkipping:
    """Ordinary strings inside Rust comments must not be scanned."""

    def test_rust_line_comment_not_scanned(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e-harness/src/test.rs": (
                '// let config = "location /fake { markdown_cache_validation full; }";\n'
                'fn main() {}\n'
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not findings
        assert not errors

    def test_rust_block_comment_not_scanned(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e-harness/src/test.rs": (
                '/* "location /fake { markdown_cache_validation full; }" */\n'
                'fn main() {}\n'
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert not findings
        assert not errors

    def test_unterminated_rust_string(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e-harness/src/test.rs": (
                'fn main() {\n'
                '    let s = "location /bad { markdown_cache_validation full;\n'
                '}\n'
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert any("unterminated" in e.message.lower() for e in errors)


# ---------------------------------------------------------------------------
# Whole-section structure validation
# ---------------------------------------------------------------------------

class TestStructureValidation:
    """_validate_config_structure detects unbalanced braces."""

    def test_unmatched_closing_brace(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                "    server {\n"
                "        location /test {\n"
                "            markdown_cache_validation full;\n"
                "        }\n"
                "    }\n"
                "}\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert any("unmatched closing brace" in e.message for e in errors)

    def test_outer_unbalanced_braces(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                "    server {\n"
                "        location /test {\n"
                "            markdown_cache_validation full;\n"
                "        }\n"
                "    }\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        assert any("unmatched opening brace" in e.message for e in errors)


# ---------------------------------------------------------------------------
# Quote structure / heredoc / Rust literal adversarial (Section XIII)
# ---------------------------------------------------------------------------

class TestUnterminatedQuote:
    """Unterminated nginx quoted strings produce a single root ScanError."""

    def test_unterminated_double_quote(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                '    set $value "unterminated;\n'
                "    location /test {\n"
                "        markdown_cache_validation full;\n"
                "    }\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        quote_errs = [e for e in errors if "unterminated quoted string" in e.message]
        assert len(quote_errs) == 1

    def test_unterminated_single_quote(self, tmp_path: Path) -> None:
        files = {
            "tools/e2e/test.sh": (
                "#!/usr/bin/env bash\n"
                "cat <<'EOF' > /tmp/nginx.conf\n"
                "http {\n"
                "    location /test {\n"
                "        markdown_cache_validation full;\n"
                "        set $value 'unterminated;\n"
                "    }\n"
                "}\n"
                "EOF\n"
            ),
        }
        findings, errors = _scan(files, tmp_path)
        quote_errs = [e for e in errors if "unterminated quoted string" in e.message]
        assert len(quote_errs) == 1

    def test_escaped_quote_closes_correctly(self, tmp_path: Path) -> None:
        self._extracted_from_test_quote_containing_braces_2(
            "#!/usr/bin/env bash\n"
            "cat <<'EOF' > /tmp/nginx.conf\n"
            "http {\n"
            '    set $value "escaped quote: \\"";\n'
            "    location /test/ {\n"
            "        markdown_cache_validation full;\n"
            "    }\n"
            "}\n"
            "EOF\n",
            tmp_path,
            "unterminated",
        )

    def test_quote_containing_braces(self, tmp_path: Path) -> None:
        """Braces inside a quoted string must not affect depth."""
        self._extracted_from_test_quote_containing_braces_2(
            "#!/usr/bin/env bash\n"
            "cat <<'EOF' > /tmp/nginx.conf\n"
            "http {\n"
            '    set $value "{";\n'
            "    location /test/ {\n"
            "        markdown_cache_validation full;\n"
            "    }\n"
            "}\n"
            "EOF\n",
            tmp_path,
            "unmatched",
        )

    # TODO Rename this here and in `test_escaped_quote_closes_correctly` and `test_quote_containing_braces`
    def _extracted_from_test_quote_containing_braces_2(self, arg0, tmp_path, arg2):
        files = {"tools/e2e/test.sh": arg0}
        findings, errors = _scan(files, tmp_path)
        assert not [e for e in errors if arg2 in e.message.lower()]
        assert any(f.loc_path == "/test/" for f in findings)


class TestHeredocOpenerScanner:
    """Deterministic heredoc opener skips comments and quoted strings."""

    def _extract(self, content: str) -> tuple[list, list]:
        return _extract_nginx_from_shell(content, "t.sh")

    def test_commented_fake_opener_ignored(self) -> None:
        """# cat <<EOF must not open a heredoc."""
        content = (
            "# cat <<'EOF'\n"
            "location /fake/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "EOF\n"
        )
        configs, errors = self._extract(content)
        assert not errors  # no unterminated heredoc

    def test_here_string_is_not_a_heredoc(self) -> None:
        """Bash ``<<<`` input must not be parsed as a heredoc opener."""
        content = (
            "IFS=',' read -r -a tokens <<< \"${header_value}\"\n"
            "cat <<EOF\n"
            "location /real/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "EOF\n"
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/real/" in config for config, _ in configs)

    def test_single_quoted_fake_opener_ignored(self) -> None:
        """echo '<<EOF' must not open a heredoc."""
        content = (
            "echo '<<EOF'\n"
            "cat <<EOF\n"
            "location /real/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "EOF\n"
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("location" in c[0] and "/real/" in c[0] for c in configs)

    def test_double_quoted_fake_opener_ignored(self) -> None:
        """echo \"<<EOF\" must not open a heredoc."""
        content = (
            'echo "<<EOF"\n'
            "cat <<EOF\n"
            "location /real/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "EOF\n"
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/real/" in c[0] for c in configs)

    def test_quoted_delimiter(self) -> None:
        """cat <<'NGINX-CONF' with matching close."""
        content = (
            "cat <<'NGINX-CONF'\n"
            "location /q/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "NGINX-CONF\n"
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/q/" in c[0] for c in configs)

    def test_hyphen_strip_tabs_close(self) -> None:
        """cat <<-EOF closes after leading tabs only."""
        content = (
            "cat <<-EOF\n"
            "location /t/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "\tEOF\n"
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/t/" in c[0] for c in configs)

    def test_hyphen_space_close_rejected(self) -> None:
        """cat <<-EOF with a space-indented close is rejected (tabs only)."""
        content = (
            "cat <<-EOF\n"
            "location /t/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "    EOF\n"
        )
        configs, errors = self._extract(content)
        assert any("unterminated" in e.message.lower() for e in errors)

    def test_no_strip_space_close_rejected(self) -> None:
        """cat <<EOF requires the close at column 0 (no leading space)."""
        content = (
            "cat <<EOF\n"
            "location /t/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            " EOF\n"
        )
        configs, errors = self._extract(content)
        assert any("unterminated" in e.message.lower() for e in errors)

    def test_body_contains_fake_opener_text(self) -> None:
        """Heredoc body containing <<FAKE text does not open a new heredoc."""
        content = (
            "cat <<EOF\n"
            "body has <<FAKE here\n"
            "location /t/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "EOF\n"
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/t/" in c[0] for c in configs)

    def test_multiple_heredocs(self) -> None:
        """Two sequential heredocs are both extracted."""
        content = (
            "cat <<EOF1\n"
            "location /a/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "EOF1\n"
            "cat <<EOF2\n"
            "location /b/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
            "EOF2\n"
        )
        configs, errors = self._extract(content)
        assert not errors
        bodies = "".join(c[0] for c in configs)
        assert "/a/" in bodies
        assert "/b/" in bodies

    def test_unterminated_heredoc_reports_error(self) -> None:
        content = (
            "cat <<EOF\n"
            "location /t/ {\n"
            "    markdown_cache_validation full;\n"
            "}\n"
        )
        configs, errors = self._extract(content)
        assert any("unterminated" in e.message.lower() for e in errors)


class TestRustLiteralScanner:
    """Rust raw/ordinary string and char literal handling."""

    def _extract(self, content: str) -> tuple[list, list]:
        return _extract_nginx_from_rust(content, "t.rs")

    def test_raw_string_with_ordinary_quotes(self) -> None:
        """Quotes inside a raw string are not re-scanned by the ordinary scanner."""
        content = (
            'let config = r#"\n'
            "location /test/ {\n"
            '    set $value "{";\n'
            "    markdown_cache_validation full;\n"
            "}\n"
            '"#;\n'
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/test/" in c[0] for c in configs)

    def test_raw_string_followed_by_ordinary(self) -> None:
        """A raw string followed by an ordinary config string extracts the ordinary one."""
        content = (
            'let a = r#"raw"#;\n'
            'let b = "location /ord/ {\\n'
            "    markdown_cache_validation full;\\n"
            '}"\n'
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/ord/" in c[0] for c in configs)

    def test_ordinary_before_raw(self) -> None:
        """An ordinary config string before a raw string extracts the ordinary one."""
        content = (
            'let b = "location /ord/ {\\n'
            "    markdown_cache_validation full;\\n"
            '}";\n'
            'let a = r#"raw"#;\n'
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/ord/" in c[0] for c in configs)

    def test_char_literal_quote_not_string(self) -> None:
        """A char literal '\"' must not be mistaken for an ordinary string."""
        content = (
            "let quote = '\"';\n"
            'let b = "location /ord/ {\\n'
            "    markdown_cache_validation full;\\n"
            '}";\n'
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/ord/" in c[0] for c in configs)

    def test_byte_char_literal_quote_not_string(self) -> None:
        """A byte-char literal b'\"' must not be mistaken for an ordinary string."""
        content = (
            "let bq = b'\"';\n"
            'let b = "location /ord/ {\\n'
            "    markdown_cache_validation full;\\n"
            '}";\n'
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/ord/" in c[0] for c in configs)

    def test_lifetime_before_ordinary_string(self) -> None:
        """A Rust lifetime must not hide a later ordinary config string."""
        content = (
            "fn config() -> &'static str {\n"
            '    let cfg = "location /lifetime/ {\\n'
            "        markdown_cache_validation full;\\n"
            '    }";\n'
            "    cfg\n"
            "}\n"
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/lifetime/" in config for config, _ in configs)

    def test_nested_block_comment_with_raw(self) -> None:
        """A raw string inside a block comment is not scanned."""
        content = (
            '/* r"location /fake/ { markdown_cache_validation full; }" */\n'
            'let b = "location /real/ {\\n'
            "    markdown_cache_validation full;\\n"
            '}";\n'
        )
        configs, errors = self._extract(content)
        assert not errors
        assert any("/real/" in c[0] for c in configs)
        assert all("/fake/" not in c[0] for c in configs)

    def test_unterminated_raw_string_reports_error(self) -> None:
        content = 'let a = r##"unterminated\n'
        configs, errors = self._extract(content)
        assert any("unterminated" in e.message.lower() for e in errors)

    def test_unterminated_ordinary_string_reports_error(self) -> None:
        content = 'let a = "location /bad/ { markdown_cache_validation full;\n}\n'
        configs, errors = self._extract(content)
        assert any("unterminated" in e.message.lower() for e in errors)
