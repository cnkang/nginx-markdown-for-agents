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
    def test_extracts_section_between_headings(self):
        result = self._extracted_from_test_handles_extra_whitespace_in_heading_2(
            "## 1. Overview\nHello\n## 2. Next\nWorld\n",
            r"1\.\s+Overview",
            "Hello",
        )
        assert "World" not in result

    def test_extracts_last_section_at_eof(self):
        result = self._extracted_from_test_handles_extra_whitespace_in_heading_2(
            "## 1. Foo\nA\n## 2. Bar\nB\n", r"2\.\s+Bar", "B"
        )

    def test_returns_empty_for_missing_section(self):
        text = "## 1. Foo\nContent\n"
        assert _section_text(text, r"99\.\s+Missing") == ""

    def test_handles_extra_whitespace_in_heading(self):
        result = self._extracted_from_test_handles_extra_whitespace_in_heading_2(
            "## 4.  Primary: Install Script\nContent\n## 5. Next\n",
            r"4\.\s+Primary.*Install Script",
            "Content",
        )

    # TODO Rename this here and in `test_extracts_section_between_headings`, `test_extracts_last_section_at_eof` and `test_handles_extra_whitespace_in_heading`
    def _extracted_from_test_handles_extra_whitespace_in_heading_2(self, arg0, arg1, arg2):
        text = arg0
        result = _section_text(text, arg1)
        assert arg2 in result
        return result

    def test_does_not_match_h3_headings(self):
        text = "### 1. Overview\nNot a section\n## 2. Real\nYes\n"
        assert _section_text(text, r"1\.\s+Overview") == ""


# ---------------------------------------------------------------------------
# _sop_section_text
# ---------------------------------------------------------------------------

class TestSopSectionText:
    def test_extracts_sop_between_headings(self):
        result = self._extracted_from_test_escapes_special_chars_in_heading_2(
            "#### SOP 1: Module Not Loaded\nContent1\n"
            "#### SOP 2: Version Mismatch\nContent2\n",
            "SOP 1: Module Not Loaded",
            "Content1",
        )
        assert "Content2" not in result

    def test_extracts_last_sop_at_eof(self):
        result = self._extracted_from_test_escapes_special_chars_in_heading_2(
            "#### SOP 9: Compression / Decompression Issues\nLast SOP\n",
            "SOP 9: Compression / Decompression Issues",
            "Last SOP",
        )

    def test_stops_at_horizontal_rule(self):
        result = self._extracted_from_test_escapes_special_chars_in_heading_2(
            "#### SOP 1: Module Not Loaded\nContent\n\n---\n\nOther stuff\n",
            "SOP 1: Module Not Loaded",
            "Content",
        )
        assert "Other stuff" not in result

    def test_returns_empty_for_missing_sop(self):
        text = "#### SOP 1: Module Not Loaded\nContent\n"
        assert _sop_section_text(text, "SOP 99: Nonexistent") == ""

    def test_escapes_special_chars_in_heading(self):
        result = self._extracted_from_test_escapes_special_chars_in_heading_2(
            "#### SOP 9: Compression / Decompression Issues\nBody\n",
            "SOP 9: Compression / Decompression Issues",
            "Body",
        )

    # TODO Rename this here and in `test_extracts_sop_between_headings`, `test_extracts_last_sop_at_eof`, `test_stops_at_horizontal_rule` and `test_escapes_special_chars_in_heading`
    def _extracted_from_test_escapes_special_chars_in_heading_2(self, arg0, arg1, arg2):
        text = arg0
        result = _sop_section_text(text, arg1)
        assert arg2 in result
        return result


# ---------------------------------------------------------------------------
# _count_shell_commands
# ---------------------------------------------------------------------------

class TestCountShellCommands:
    def test_counts_commands_in_bash_block(self):
        section = "Text\n```bash\ncurl http://x\nnginx -t\n```\nMore text\n"
        assert _count_shell_commands(section) == 2

    def test_ignores_comments(self):
        section = "```bash\n# comment\ncurl http://x\n```\n"
        assert _count_shell_commands(section) == 1

    def test_ignores_empty_lines(self):
        section = "```bash\ncurl http://x\n\nnginx -t\n```\n"
        assert _count_shell_commands(section) == 2

    def test_ignores_non_bash_blocks(self):
        section = "```nginx\nmarkdown_filter on;\n```\n"
        assert _count_shell_commands(section) == 0

    def test_multiple_bash_blocks(self):
        section = "```bash\ncmd1\n```\nText\n```bash\ncmd2\ncmd3\n```\n"
        assert _count_shell_commands(section) == 3

    def test_no_code_blocks(self):
        assert _count_shell_commands("Just text\n") == 0


# ---------------------------------------------------------------------------
# _validate_single_sop
# ---------------------------------------------------------------------------

class TestValidateSingleSop:
    def test_valid_sop_with_category(self):
        sop = (
            "**Category:** `config`\n"
            "**Symptom:** Something broke\n"
            "**Root Cause:** Bad config\n"
            "**Resolution Steps:** Fix it\n"
        )
        assert _validate_single_sop("SOP 1: Module Not Loaded", sop) == []

    def test_missing_symptom(self):
        self._extracted_from_test_missing_category_field_2(
            "**Root Cause:** X\n**Resolution Steps:** Y\n**Category:** `config`\n",
            "Symptom",
        )

    def test_missing_root_cause(self):
        self._extracted_from_test_missing_category_field_2(
            "**Symptom:** X\n**Resolution Steps:** Y\n**Category:** `config`\n",
            "Root Cause",
        )

    def test_missing_resolution(self):
        self._extracted_from_test_missing_category_field_2(
            "**Symptom:** X\n**Root Cause:** Y\n**Category:** `config`\n",
            "Resolution",
        )

    def test_wrong_category(self):
        self._extracted_from_test_missing_category_field_2(
            "**Category:** `network`\n"
            "**Symptom:** X\n**Root Cause:** Y\n**Resolution Steps:** Z\n",
            "expected 'config'",
        )

    def test_missing_category_field(self):
        self._extracted_from_test_missing_category_field_2(
            "**Symptom:** X\n**Root Cause:** Y\n**Resolution Steps:** Z\n",
            "missing Category",
        )

    # TODO Rename this here and in `test_missing_symptom`, `test_missing_root_cause`, `test_missing_resolution`, `test_wrong_category` and `test_missing_category_field`
    def _extracted_from_test_missing_category_field_2(self, arg0, arg1):
        sop = arg0
        errors = _validate_single_sop("SOP 1: Module Not Loaded", sop)
        assert any(arg1 in e for e in errors)

    def test_no_category_check_for_sop7(self):
        sop = "**Symptom:** X\n**Root Cause:** Y\n**Resolution Steps:** Z\n"
        errors = _validate_single_sop("SOP 7: Content Negotiation Not Triggering", sop)
        assert all("Category" not in e for e in errors)


# ---------------------------------------------------------------------------
# check_tier_labels
# ---------------------------------------------------------------------------

class TestCheckTierLabels:
    def test_correct_tier_passes(self):
        text = (
            "## 4. Primary: Install Script\n**Tier: Primary**\n"
            "## 5. Secondary: Docker Source Build\n**Tier: Secondary**\n"
            "## 6. Secondary: Manual Source Build\n**Tier: Secondary**\n"
            "## 7. Next\n"
        )
        assert check_tier_labels(text) == []

    def test_wrong_tier_fails(self):
        self._extracted_from_test_missing_tier_label_fails_2(
            "## 4. Primary: Install Script\n**Tier: Convenience**\n"
            "## 5. Secondary: Docker Source Build\n**Tier: Secondary**\n"
            "## 6. Secondary: Manual Source Build\n**Tier: Secondary**\n"
            "## 7. Next\n",
            "expected 'Primary'",
        )

    def test_missing_tier_label_fails(self):
        self._extracted_from_test_missing_tier_label_fails_2(
            "## 4. Primary: Install Script\nNo tier here\n"
            "## 5. Secondary: Docker Source Build\n**Tier: Secondary**\n"
            "## 6. Secondary: Manual Source Build\n**Tier: Secondary**\n"
            "## 7. Next\n",
            "missing tier label",
        )

    # TODO Rename this here and in `test_wrong_tier_fails` and `test_missing_tier_label_fails`
    def _extracted_from_test_missing_tier_label_fails_2(self, arg0, arg1):
        text = arg0
        errors = check_tier_labels(text)
        assert any(arg1 in e for e in errors)


# ---------------------------------------------------------------------------
# check_content_negotiation_sop (SOP 7 eligibility keywords)
# ---------------------------------------------------------------------------

class TestCheckContentNegotiationSop:
    def test_all_keywords_present(self):
        text = (
            "#### SOP 7: Content Negotiation Not Triggering\n"
            "Status 200, Content-Type text/html, Accept text/markdown, "
            "markdown_max_size limit\n"
        )
        assert check_content_negotiation_sop(text) == []

    def test_missing_keyword_fails(self):
        text = (
            "#### SOP 7: Content Negotiation Not Triggering\n"
            "Status 200, Content-Type text/html, Accept text/markdown\n"
        )
        errors = check_content_negotiation_sop(text)
        assert any("markdown_max_size" in e for e in errors)

    def test_missing_sop_section(self):
        errors = check_content_negotiation_sop("No SOP here\n")
        assert any("Cannot locate" in e for e in errors)


# ---------------------------------------------------------------------------
# check_no_hardcoded_release_tags
# ---------------------------------------------------------------------------

class TestCheckNoHardcodedReleaseTags:
    def test_placeholder_passes(self):
        text = "wget https://github.com/.../releases/download/<release_tag>/file.tar.gz\n"
        assert check_no_hardcoded_release_tags(text) == []

    def test_hardcoded_tag_fails(self):
        text = "wget https://github.com/.../releases/download/v0.3.0/file.tar.gz\n"
        errors = check_no_hardcoded_release_tags(text)
        assert len(errors) == 1
        assert "v0.3.0" in errors[0]

    def test_non_download_url_passes(self):
        text = "See https://github.com/org/repo/releases for details\n"
        assert check_no_hardcoded_release_tags(text) == []

    def test_multiple_lines(self):
        text = (
            "wget .../releases/download/v1.0.0/a.tar.gz\n"
            "wget .../releases/download/v2.0.0/b.tar.gz\n"
        )
        assert len(check_no_hardcoded_release_tags(text)) == 2


# ---------------------------------------------------------------------------
# _check_directive_comments
# ---------------------------------------------------------------------------

class TestCheckDirectiveComments:
    def test_directive_with_inline_comment(self):
        lines = ["    markdown_filter on;  # Enable conversion"]
        assert _check_directive_comments(lines, "markdown_filter") == []

    def test_directive_without_comment_fails(self):
        self._extracted_from_test_missing_directive_fails_2(
            "    markdown_filter on;", "no inline comment"
        )

    def test_missing_directive_fails(self):
        self._extracted_from_test_missing_directive_fails_2(
            "    other_directive on;", "missing directive"
        )

    # TODO Rename this here and in `test_directive_without_comment_fails` and `test_missing_directive_fails`
    def _extracted_from_test_missing_directive_fails_2(self, arg0, arg1):
        lines = [arg0]
        errors = _check_directive_comments(lines, "markdown_filter")
        assert any(arg1 in e for e in errors)

    def test_comment_line_is_skipped(self):
        lines = ["    # markdown_filter is great", "    markdown_filter on;  # yes"]
        assert _check_directive_comments(lines, "markdown_filter") == []

    def test_at_least_one_documented_suffices(self):
        lines = [
            "    markdown_filter on;",
            "    markdown_filter off;  # disable here",
        ]
        assert _check_directive_comments(lines, "markdown_filter") == []

    def test_empty_lines_skipped(self):
        lines = ["", "    markdown_filter on;  # ok", ""]
        assert _check_directive_comments(lines, "markdown_filter") == []


# ---------------------------------------------------------------------------
# check_compatibility_matrix
# ---------------------------------------------------------------------------

class TestCheckCompatibilityMatrix:
    def test_valid_matrix_section(self):
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
        self._extracted_from_test_missing_table_2(
            "## 7. Compatibility Matrix\n"
            "| NGINX Version | OS Type | Arch |\n"
            "## 8. Next\n",
            "release-matrix.json",
        )

    def test_missing_table(self):
        self._extracted_from_test_missing_table_2(
            "## 7. Compatibility Matrix\nrelease-matrix.json\nNo table\n## 8. Next\n",
            "missing the platform table",
        )

    # TODO Rename this here and in `test_missing_matrix_reference` and `test_missing_table`
    def _extracted_from_test_missing_table_2(self, arg0, arg1):
        text = arg0
        errors = check_compatibility_matrix(text)
        assert any(arg1 in e for e in errors)

    def test_missing_section(self):
        errors = check_compatibility_matrix("## 1. Overview\nStuff\n")
        assert any("Cannot locate" in e for e in errors)
