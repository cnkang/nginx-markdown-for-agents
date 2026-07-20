"""Unit tests for check_packaging_docs helper functions and validators.

Focuses on regex/parser boundary cases that could cause false positives
or false negatives during documentation refactors.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow imports from tools/docs/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_packaging_docs import (
    _check_directive_comments,
    _count_shell_commands,
    _section_text,
    _sop_section_text,
    _validate_single_sop,
    check_compatibility_matrix,
    check_content_negotiation_sop,
    check_no_hardcoded_release_tags,
    check_required_sections,
    check_shortest_success_path,
    check_tier_labels,
)


# ---------------------------------------------------------------------------
# _section_text
# ---------------------------------------------------------------------------

class TestSectionText:
    """Tests for _section_text extracting markdown sections between headings."""

    def test_extracts_section_between_headings(self):
        """Verify content between two h2 headings is extracted, excluding the next section."""
        result = self._assert_section_contains(
            "## 1. Overview\nHello\n## 2. Next\nWorld\n",
            r"1\.\s+Overview",
            "Hello",
        )
        assert "World" not in result

    def test_extracts_last_section_at_eof(self):
        """Verify the last section is extracted even when it ends at EOF."""
        self._assert_section_contains(
            "## 1. Foo\nA\n## 2. Bar\nB\n", r"2\.\s+Bar", "B"
        )

    def test_returns_empty_for_missing_section(self):
        """Verify empty string is returned when the heading pattern is not found."""
        text = "## 1. Foo\nContent\n"
        assert _section_text(text, r"99\.\s+Missing") == ""

    def test_handles_extra_whitespace_in_heading(self):
        """Verify headings with extra whitespace between number and title are matched."""
        self._assert_section_contains(
            "## 4.  Primary: Install Script\nContent\n## 5. Next\n",
            r"4\.\s+Primary.*Install Script",
            "Content",
        )

    def _assert_section_contains(self, text, pattern, expected):
        """Assert that the extracted section contains the expected substring."""
        result = _section_text(text, pattern)
        assert expected in result
        return result

    def test_does_not_match_h3_headings(self):
        """Verify h3 headings are not treated as section boundaries."""
        text = "### 1. Overview\nNot a section\n## 2. Real\nYes\n"
        assert _section_text(text, r"1\.\s+Overview") == ""


# ---------------------------------------------------------------------------
# _sop_section_text
# ---------------------------------------------------------------------------

class TestSopSectionText:
    """Tests for _sop_section_text extracting SOP subsections between h4 headings."""

    def test_extracts_sop_between_headings(self):
        """Verify content between two h4 SOP headings is extracted correctly."""
        result = self._assert_sop_contains(
            "#### SOP 1: Module Not Loaded\nContent1\n"
            "#### SOP 2: Version Mismatch\nContent2\n",
            "SOP 1: Module Not Loaded",
            "Content1",
        )
        assert "Content2" not in result

    def test_extracts_last_sop_at_eof(self):
        """Verify the last SOP section is extracted when it ends at EOF."""
        self._assert_sop_contains(
            "#### SOP 9: Compression / Decompression Issues\nLast SOP\n",
            "SOP 9: Compression / Decompression Issues",
            "Last SOP",
        )

    def test_stops_at_horizontal_rule(self):
        """Verify SOP extraction stops at a horizontal rule (---)."""
        result = self._assert_sop_contains(
            "#### SOP 1: Module Not Loaded\nContent\n\n---\n\nOther stuff\n",
            "SOP 1: Module Not Loaded",
            "Content",
        )
        assert "Other stuff" not in result

    def test_returns_empty_for_missing_sop(self):
        """Verify empty string is returned for a nonexistent SOP heading."""
        text = "#### SOP 1: Module Not Loaded\nContent\n"
        assert _sop_section_text(text, "SOP 99: Nonexistent") == ""

    def test_escapes_special_chars_in_heading(self):
        """Verify special characters like / in headings are handled correctly."""
        self._assert_sop_contains(
            "#### SOP 9: Compression / Decompression Issues\nBody\n",
            "SOP 9: Compression / Decompression Issues",
            "Body",
        )

    def _assert_sop_contains(self, text, heading, expected):
        """Assert that the extracted SOP section contains the expected substring."""
        result = _sop_section_text(text, heading)
        assert expected in result
        return result


# ---------------------------------------------------------------------------
# _count_shell_commands
# ---------------------------------------------------------------------------

class TestCountShellCommands:
    """Tests for _count_shell_commands counting commands in bash code blocks."""

    def test_counts_commands_in_bash_block(self):
        """Verify commands inside a bash code block are counted correctly."""
        section = "Text\n```bash\ncurl http://x\nnginx -t\n```\nMore text\n"
        assert _count_shell_commands(section) == 2

    def test_ignores_comments(self):
        """Verify comment lines starting with # are not counted as commands."""
        section = "```bash\n# comment\ncurl http://x\n```\n"
        assert _count_shell_commands(section) == 1

    def test_ignores_empty_lines(self):
        """Verify empty lines inside bash blocks are not counted as commands."""
        section = "```bash\ncurl http://x\n\nnginx -t\n```\n"
        assert _count_shell_commands(section) == 2

    def test_ignores_non_bash_blocks(self):
        """Verify code blocks with non-bash languages are ignored."""
        section = "```nginx\nmarkdown_filter on;\n```\n"
        assert _count_shell_commands(section) == 0

    def test_multiple_bash_blocks(self):
        """Verify commands across multiple bash blocks are aggregated."""
        section = "```bash\ncmd1\n```\nText\n```bash\ncmd2\ncmd3\n```\n"
        assert _count_shell_commands(section) == 3

    def test_no_code_blocks(self):
        """Verify zero is returned when no code blocks exist."""
        assert _count_shell_commands("Just text\n") == 0


# ---------------------------------------------------------------------------
# _validate_single_sop
# ---------------------------------------------------------------------------

class TestValidateSingleSop:
    """Tests for _validate_single_sop checking SOP structure compliance."""

    def test_valid_sop_with_category(self):
        """Verify a well-formed SOP with all required fields returns no errors."""
        sop = (
            "**Category:** `config`\n"
            "**Symptom:** Something broke\n"
            "**Root Cause:** Bad config\n"
            "**Resolution Steps:** Fix it\n"
        )
        assert _validate_single_sop("SOP 1: Module Not Loaded", sop) == []

    def test_missing_symptom(self):
        """Verify an error is raised when the Symptom field is missing."""
        self._assert_sop_error(
            "**Root Cause:** X\n**Resolution Steps:** Y\n**Category:** `config`\n",
            "Symptom",
        )

    def test_missing_root_cause(self):
        """Verify an error is raised when the Root Cause field is missing."""
        self._assert_sop_error(
            "**Symptom:** X\n**Resolution Steps:** Y\n**Category:** `config`\n",
            "Root Cause",
        )

    def test_missing_resolution(self):
        """Verify an error is raised when the Resolution Steps field is missing."""
        self._assert_sop_error(
            "**Symptom:** X\n**Root Cause:** Y\n**Category:** `config`\n",
            "Resolution",
        )

    def test_wrong_category(self):
        """Verify an error is raised when the Category value is not 'config'."""
        self._assert_sop_error(
            "**Category:** `network`\n"
            "**Symptom:** X\n**Root Cause:** Y\n**Resolution Steps:** Z\n",
            "expected 'config'",
        )

    def test_missing_category_field(self):
        """Verify an error is raised when the Category field is absent."""
        self._assert_sop_error(
            "**Symptom:** X\n**Root Cause:** Y\n**Resolution Steps:** Z\n",
            "missing Category",
        )

    def _assert_sop_error(self, sop, expected_fragment):
        """Assert that validation errors contain the expected fragment."""
        errors = _validate_single_sop("SOP 1: Module Not Loaded", sop)
        assert any(expected_fragment in e for e in errors)

    def test_no_category_check_for_sop7(self):
        """Verify SOP 7 skips Category validation since it uses a different structure."""
        sop = "**Symptom:** X\n**Root Cause:** Y\n**Resolution Steps:** Z\n"
        errors = _validate_single_sop("SOP 7: Content Negotiation Not Triggering", sop)
        assert all("Category" not in e for e in errors)


# ---------------------------------------------------------------------------
# check_tier_labels
# ---------------------------------------------------------------------------

class TestCheckTierLabels:
    """Tests for check_tier_labels verifying tier label correctness in sections."""

    def test_correct_tier_passes(self):
        """Verify no errors when Primary/Secondary tiers match their headings."""
        text = (
            "## 4. Primary: Install Script\n**Tier: Primary**\n"
            "## 5. Secondary: Docker Source Build\n**Tier: Secondary**\n"
            "## 6. Secondary: Manual Source Build\n**Tier: Secondary**\n"
            "## 7. Next\n"
        )
        assert check_tier_labels(text) == []

    def test_wrong_tier_fails(self):
        """Verify an error when a section's tier label does not match its heading."""
        self._assert_tier_error(
            "## 4. Primary: Install Script\n**Tier: Convenience**\n"
            "## 5. Secondary: Docker Source Build\n**Tier: Secondary**\n"
            "## 6. Secondary: Manual Source Build\n**Tier: Secondary**\n"
            "## 7. Next\n",
            "expected 'Primary'",
        )

    def test_missing_tier_label_fails(self):
        """Verify an error when a section is missing its tier label entirely."""
        self._assert_tier_error(
            "## 4. Primary: Install Script\nNo tier here\n"
            "## 5. Secondary: Docker Source Build\n**Tier: Secondary**\n"
            "## 6. Secondary: Manual Source Build\n**Tier: Secondary**\n"
            "## 7. Next\n",
            "missing tier label",
        )

    def _assert_tier_error(self, text, expected_fragment):
        """Assert that tier validation errors contain the expected fragment."""
        errors = check_tier_labels(text)
        assert any(expected_fragment in e for e in errors)


# ---------------------------------------------------------------------------
# check_content_negotiation_sop (SOP 7 eligibility keywords)
# ---------------------------------------------------------------------------

class TestCheckContentNegotiationSop:
    """Tests for check_content_negotiation_sop validating SOP 7 keyword presence."""

    def test_all_keywords_present(self):
        """Verify no errors when all required SOP 7 keywords are present."""
        text = (
            "#### SOP 7: Content Negotiation Not Triggering\n"
            "Status 200, Content-Type text/html, Accept text/markdown, "
            "markdown_limits memory=16m\n"
        )
        assert check_content_negotiation_sop(text) == []

    def test_missing_keyword_fails(self):
        """Verify an error when a required SOP 7 keyword is missing."""
        text = (
            "#### SOP 7: Content Negotiation Not Triggering\n"
            "Status 200, Content-Type text/html, Accept text/markdown\n"
        )
        errors = check_content_negotiation_sop(text)
        assert any("markdown_limits" in e for e in errors)

    def test_missing_sop_section(self):
        """Verify an error when the SOP 7 section cannot be located."""
        errors = check_content_negotiation_sop("No SOP here\n")
        assert any("Cannot locate" in e for e in errors)


# ---------------------------------------------------------------------------
# check_no_hardcoded_release_tags
# ---------------------------------------------------------------------------

class TestCheckNoHardcodedReleaseTags:
    """Tests for check_no_hardcoded_release_tags detecting hardcoded version tags."""

    def test_placeholder_passes(self):
        """Verify no errors when release URLs use a placeholder instead of a version."""
        text = "wget https://github.com/.../releases/download/<release_tag>/file.tar.gz\n"
        assert check_no_hardcoded_release_tags(text) == []

    def test_hardcoded_tag_fails(self):
        """Verify an error when a release URL contains a hardcoded version tag."""
        text = "wget https://github.com/.../releases/download/v0.3.0/file.tar.gz\n"
        errors = check_no_hardcoded_release_tags(text)
        assert len(errors) == 1
        assert "v0.3.0" in errors[0]

    def test_non_download_url_passes(self):
        """Verify no errors for GitHub release page URLs without download paths."""
        text = "See https://github.com/org/repo/releases for details\n"
        assert check_no_hardcoded_release_tags(text) == []

    def test_multiple_lines(self):
        """Verify all hardcoded tags across multiple lines are detected."""
        text = (
            "wget .../releases/download/v1.0.0/a.tar.gz\n"
            "wget .../releases/download/v2.0.0/b.tar.gz\n"
        )
        assert len(check_no_hardcoded_release_tags(text)) == 2


# ---------------------------------------------------------------------------
# _check_directive_comments
# ---------------------------------------------------------------------------

class TestCheckDirectiveComments:
    """Tests for _check_directive_comments verifying inline comment presence on directives."""

    def test_directive_with_inline_comment(self):
        """Verify no errors when a directive has an inline comment."""
        lines = ["    markdown_filter on;  # Enable conversion"]
        assert _check_directive_comments(lines, "markdown_filter") == []

    def test_directive_without_comment_fails(self):
        """Verify an error when a directive lacks an inline comment."""
        self._assert_directive_error(
            "    markdown_filter on;", "no inline comment"
        )

    def test_missing_directive_fails(self):
        """Verify an error when the expected directive is not found at all."""
        self._assert_directive_error(
            "    other_directive on;", "missing directive"
        )

    def _assert_directive_error(self, line, expected_fragment):
        """Assert that directive check errors contain the expected fragment."""
        errors = _check_directive_comments([line], "markdown_filter")
        assert any(expected_fragment in e for e in errors)

    def test_comment_line_is_skipped(self):
        """Verify comment-only lines containing the directive name are skipped."""
        lines = ["    # markdown_filter is great", "    markdown_filter on;  # yes"]
        assert _check_directive_comments(lines, "markdown_filter") == []

    def test_at_least_one_documented_suffices(self):
        """Verify no errors when at least one occurrence of the directive has a comment."""
        lines = [
            "    markdown_filter on;",
            "    markdown_filter off;  # disable here",
        ]
        assert _check_directive_comments(lines, "markdown_filter") == []

    def test_empty_lines_skipped(self):
        """Verify empty lines are skipped without causing false errors."""
        lines = ["", "    markdown_filter on;  # ok", ""]
        assert _check_directive_comments(lines, "markdown_filter") == []


# ---------------------------------------------------------------------------
# check_compatibility_matrix
# ---------------------------------------------------------------------------

class TestCheckCompatibilityMatrix:
    """Tests for check_compatibility_matrix validating the matrix section structure."""

    def test_valid_matrix_section(self):
        """Verify no errors when the matrix section has the canonical reference and a table."""
        text = (
            "## 7. Compatibility Matrix\n"
            "Canonical source: release-matrix.json\n"
            "| NGINX Version | OS Type | Arch |\n"
            "|---|---|---|\n"
            "| 1.24.0 | glibc | x86_64 |\n"
            "## 8. Next\n"
        )
        assert check_compatibility_matrix(text) == []

    def test_missing_matrix_reference(self):
        """Verify an error when the release-matrix.json canonical source reference is missing."""
        self._assert_matrix_error(
            "## 7. Compatibility Matrix\n"
            "| NGINX Version | OS Type | Arch |\n"
            "## 8. Next\n",
            "release-matrix.json",
        )

    def test_missing_table(self):
        """Verify an error when the platform compatibility table is missing."""
        self._assert_matrix_error(
            "## 7. Compatibility Matrix\nrelease-matrix.json\nNo table\n## 8. Next\n",
            "missing the platform table",
        )

    def _assert_matrix_error(self, text, expected_fragment):
        """Assert that compatibility matrix errors contain the expected fragment."""
        errors = check_compatibility_matrix(text)
        assert any(expected_fragment in e for e in errors)

    def test_missing_section(self):
        """Verify an error when the Compatibility Matrix section heading is absent."""
        errors = check_compatibility_matrix("## 1. Overview\nStuff\n")
        assert any("Cannot locate" in e for e in errors)
