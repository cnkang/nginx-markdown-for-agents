#!/usr/bin/env python3
"""Tests for tools/render_release_matrix_docs.py marker system.

Verifies:
- Marker parsing (BEGIN/END detection, error handling)
- Content replacement (idempotency, outside-marker preservation)
- Section generation from matrix data
- Error conditions (mismatched markers, duplicates, unknown sections)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Add tools/ to path so we can import the module
TOOLS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS_DIR))

import render_release_matrix_docs as rmd


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

MINIMAL_MATRIX: dict = {
    "schema_version": "1.0.0",
    "updated_at": "2026-06-04T12:00:00Z",
    "support_tiers": {
        "supported": "CI passes, artifact produced",
        "experimental": "Available, not guaranteed",
    },
    "tier_mapping": {"full": "supported"},
    "matrix": [
        {
            "nginx": "1.26.3",
            "nginx_version": "1.26.3",
            "nginx_channel": "stable",
            "os": "linux",
            "os_type": "glibc",
            "libc": "glibc",
            "arch": "x86_64",
            "artifact_type": "dynamic-module",
            "test_level": "smoke-test",
            "support_tier": "full",
            "release_blocking": True,
            "owner_workflow": ".github/workflows/release-packages.yml",
        }
    ],
    "additional_artifacts": [
        {
            "nginx_channel": "stable",
            "artifact_type": "docker-image",
            "test_level": "functional-check",
            "support_tier": "supported",
            "release_blocking": True,
            "owner_workflow": ".github/workflows/official-nginx-docker.yml",
            "note": "Official nginx Docker images",
        }
    ],
}


def _get_entries():
    """Get normalized entries from test matrix."""
    return rmd.get_entries(MINIMAL_MATRIX)


# ---------------------------------------------------------------------------
# Marker parsing tests
# ---------------------------------------------------------------------------


def test_parse_markers_basic():
    """Parse a file with valid markers and extract content."""
    content = (
        "# Title\n"
        "\n"
        "Some intro text.\n"
        "\n"
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "old content here\n"
        "<!-- END:release-matrix:support-matrix -->\n"
        "\n"
        "Footer text.\n"
    )
    sections = rmd.parse_markers(content)
    assert "support-matrix" in sections
    start, end, inner = sections["support-matrix"]
    assert start == 4  # 0-indexed line of BEGIN
    assert end == 6  # 0-indexed line of END
    assert inner == "old content here\n"


def test_parse_markers_multiple_sections():
    """Parse multiple distinct sections."""
    content = (
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "content A\n"
        "<!-- END:release-matrix:support-matrix -->\n"
        "\n"
        "<!-- BEGIN:release-matrix:status-matrix -->\n"
        "content B\n"
        "<!-- END:release-matrix:status-matrix -->\n"
    )
    sections = rmd.parse_markers(content)
    assert len(sections) == 2
    assert "support-matrix" in sections
    assert "status-matrix" in sections


def test_parse_markers_empty_section():
    """Parse a section with no content between markers."""
    content = (
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "<!-- END:release-matrix:support-matrix -->\n"
    )
    sections = rmd.parse_markers(content)
    assert sections["support-matrix"][2] == ""


def test_parse_markers_missing_end():
    """Missing END marker raises MarkerError."""
    content = (
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "content\n"
    )
    try:
        rmd.parse_markers(content)
        assert False, "Should have raised MarkerError"
    except rmd.MarkerError as e:
        assert "Missing END marker" in str(e)


def test_parse_markers_mismatched_names():
    """END marker name doesn't match BEGIN."""
    content = (
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "content\n"
        "<!-- END:release-matrix:status-matrix -->\n"
    )
    try:
        rmd.parse_markers(content)
        assert False, "Should have raised MarkerError"
    except rmd.MarkerError as e:
        assert "does not match" in str(e)


def test_parse_markers_duplicate_section():
    """Duplicate section name raises MarkerError."""
    content = (
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "first\n"
        "<!-- END:release-matrix:support-matrix -->\n"
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "second\n"
        "<!-- END:release-matrix:support-matrix -->\n"
    )
    try:
        rmd.parse_markers(content)
        assert False, "Should have raised MarkerError"
    except rmd.MarkerError as e:
        assert "Duplicate section" in str(e)


def test_parse_markers_nested_begin():
    """Nested BEGIN marker raises MarkerError."""
    content = (
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "<!-- BEGIN:release-matrix:status-matrix -->\n"
        "content\n"
        "<!-- END:release-matrix:status-matrix -->\n"
        "<!-- END:release-matrix:support-matrix -->\n"
    )
    try:
        rmd.parse_markers(content)
        assert False, "Should have raised MarkerError"
    except rmd.MarkerError as e:
        assert "Nested BEGIN" in str(e)


def test_parse_markers_orphan_end():
    """END marker without BEGIN raises MarkerError."""
    content = (
        "some content\n"
        "<!-- END:release-matrix:support-matrix -->\n"
    )
    try:
        rmd.parse_markers(content)
        assert False, "Should have raised MarkerError"
    except rmd.MarkerError as e:
        assert "without matching BEGIN" in str(e)


# ---------------------------------------------------------------------------
# Content replacement tests
# ---------------------------------------------------------------------------


def test_replace_section_content():
    """Replace content between markers."""
    content = (
        "# Title\n"
        "\n"
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "old content\n"
        "<!-- END:release-matrix:support-matrix -->\n"
        "\n"
        "Footer\n"
    )
    result = rmd.replace_section_content(content, "support-matrix", "new content\n")
    assert "new content\n" in result
    assert "old content" not in result
    # Verify outside content preserved
    assert "# Title\n" in result
    assert "Footer\n" in result


def test_replace_preserves_markers():
    """Markers themselves remain intact after replacement."""
    content = (
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "old\n"
        "<!-- END:release-matrix:support-matrix -->\n"
    )
    result = rmd.replace_section_content(content, "support-matrix", "new\n")
    assert "<!-- BEGIN:release-matrix:support-matrix -->" in result
    assert "<!-- END:release-matrix:support-matrix -->" in result
    assert "new\n" in result


def test_replace_idempotent():
    """Replacing with same content produces identical output."""
    content = (
        "Header\n"
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "the content\n"
        "<!-- END:release-matrix:support-matrix -->\n"
        "Footer\n"
    )
    result1 = rmd.replace_section_content(content, "support-matrix", "the content\n")
    result2 = rmd.replace_section_content(result1, "support-matrix", "the content\n")
    assert result1 == result2


def test_replace_nonexistent_section():
    """Replacing in file without the section raises MarkerError."""
    content = "No markers here.\n"
    try:
        rmd.replace_section_content(content, "support-matrix", "content\n")
        assert False, "Should have raised MarkerError"
    except rmd.MarkerError as e:
        assert "not found" in str(e)


# ---------------------------------------------------------------------------
# Section generation tests
# ---------------------------------------------------------------------------


def test_generate_support_matrix():
    """Support matrix generator produces valid table."""
    entries = _get_entries()
    result = rmd._generate_support_matrix(entries, MINIMAL_MATRIX)
    assert "| NGINX | Channel | OS | libc | Arch | Artifact | Tier | Blocking |" in result
    assert "supported" in result
    assert "dynamic-module" in result
    # Additional artifacts table
    assert "**Additional Artifacts:**" in result
    assert "docker-image" in result


def test_generate_compatibility_matrix():
    """Compatibility matrix includes full details."""
    entries = _get_entries()
    result = rmd._generate_compatibility_matrix(entries, MINIMAL_MATRIX)
    assert "## Platform Compatibility Matrix" in result
    assert "Test Level" in result
    assert "Workflow" in result
    assert "### Tier Definitions" in result
    assert "**supported**" in result


def test_generate_installation_matrix():
    """Installation matrix groups by artifact type."""
    entries = _get_entries()
    result = rmd._generate_installation_matrix(entries, MINIMAL_MATRIX)
    assert "## Available Packages by Platform" in result
    assert "### dynamic-module" in result
    assert "### Additional Distribution Channels" in result


def test_generate_status_matrix():
    """Status matrix shows tier counts and blocking entries."""
    entries = _get_entries()
    result = rmd._generate_status_matrix(entries, MINIMAL_MATRIX)
    assert "## Release Matrix Summary" in result
    assert "### Tier Distribution" in result
    assert "### Release-Blocking Entries" in result
    assert "supported" in result


def test_generate_distribution_matrix():
    """Distribution matrix shows package types and workflows."""
    entries = _get_entries()
    result = rmd._generate_distribution_matrix(entries, MINIMAL_MATRIX)
    assert "## Distribution Channels" in result
    assert "### Package Types" in result
    assert "### Build Workflows" in result


def test_tier_mapping_resolved():
    """tier_mapping resolves 'full' to 'supported'."""
    result = rmd.resolve_tier(MINIMAL_MATRIX, "full")
    assert result == "supported"


def test_tier_mapping_passthrough():
    """Unknown tiers pass through unchanged."""
    result = rmd.resolve_tier(MINIMAL_MATRIX, "experimental")
    assert result == "experimental"


# ---------------------------------------------------------------------------
# Section registry tests
# ---------------------------------------------------------------------------


def test_section_registry_coverage():
    """All registry sections have generators."""
    for sections in rmd.SECTION_REGISTRY.values():
        for section_name in sections:
            assert section_name in rmd.SECTION_GENERATORS, (
                f"No generator for registered section '{section_name}'"
            )


def test_known_sections_complete():
    """KNOWN_SECTIONS includes all registry entries."""
    all_from_registry = set()
    for sections in rmd.SECTION_REGISTRY.values():
        all_from_registry.update(sections)
    assert all_from_registry.issubset(rmd.KNOWN_SECTIONS)


def test_section_registry_matches_task_spec():
    """Verify the section registry matches task 2.5 spec requirements."""
    assert rmd.SECTION_REGISTRY["README.md"] == ["support-matrix"]
    assert rmd.SECTION_REGISTRY["docs/COMPATIBILITY.md"] == ["compatibility-matrix"]
    assert rmd.SECTION_REGISTRY["docs/guides/INSTALLATION.md"] == ["installation-matrix"]
    assert rmd.SECTION_REGISTRY["docs/project/PROJECT_STATUS.md"] == ["status-matrix"]
    assert rmd.SECTION_REGISTRY["README_zh-CN.md"] == ["support-matrix"]
    assert rmd.SECTION_REGISTRY["docs/guides/PACKAGE_DISTRIBUTION.md"] == ["distribution-matrix"]


# ---------------------------------------------------------------------------
# Integration: write + check idempotency
# ---------------------------------------------------------------------------


def test_write_then_check_idempotent():
    """Writing content then checking produces no errors (idempotency)."""
    entries = _get_entries()
    content = (
        "# Test Doc\n"
        "\n"
        "Some intro.\n"
        "\n"
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "<!-- END:release-matrix:support-matrix -->\n"
        "\n"
        "Footer stays.\n"
    )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp_path = Path(f.name)

    try:
        _extracted_from_test_write_then_check_idempotent_23(entries, tmp_path)
    finally:
        tmp_path.unlink()


# TODO Rename this here and in `test_write_then_check_idempotent`
def _extracted_from_test_write_then_check_idempotent_23(entries, tmp_path):
    # Generate expected content
    generated = rmd.generate_section("support-matrix", entries, MINIMAL_MATRIX)

    # Write the generated content into the temp file
    file_content = tmp_path.read_text(encoding="utf-8")
    updated = rmd.replace_section_content(file_content, "support-matrix", generated)
    tmp_path.write_text(updated, encoding="utf-8")

    # Now verify: replace again should produce same result
    file_content2 = tmp_path.read_text(encoding="utf-8")
    updated2 = rmd.replace_section_content(file_content2, "support-matrix", generated)
    assert updated == updated2, "Second write produced different content (not idempotent)"

    # Verify outside-marker content preserved
    assert "# Test Doc\n" in updated
    assert "Some intro.\n" in updated
    assert "Footer stays.\n" in updated


def test_write_preserves_surrounding_content():
    """Content before and after markers is untouched."""
    before = "Line 1\nLine 2\n"
    after = "Line A\nLine B\n"
    content = (
        f"{before}"
        "<!-- BEGIN:release-matrix:support-matrix -->\n"
        "old stuff\n"
        "<!-- END:release-matrix:support-matrix -->\n"
        f"{after}"
    )
    result = rmd.replace_section_content(content, "support-matrix", "new stuff\n")
    assert result.startswith(before)
    assert result.endswith(after)


def test_full_cycle_all_sections():
    """Every registered section generates content, can be written, and is idempotent."""
    entries = _get_entries()

    for section_name in rmd.KNOWN_SECTIONS:
        # Each section can generate content
        generated = rmd.generate_section(section_name, entries, MINIMAL_MATRIX)
        assert generated, f"Section '{section_name}' generated empty content"

        # Content can be written and re-read identically
        doc = (
            f"<!-- BEGIN:release-matrix:{section_name} -->\n"
            f"<!-- END:release-matrix:{section_name} -->\n"
        )
        result1 = rmd.replace_section_content(doc, section_name, generated)
        result2 = rmd.replace_section_content(result1, section_name, generated)
        assert result1 == result2, (
            f"Section '{section_name}' not idempotent"
        )


# ---------------------------------------------------------------------------
# Release notes generation
# ---------------------------------------------------------------------------


def test_release_notes_output():
    """Release notes contain key summary info."""
    entries = _get_entries()
    result = rmd.generate_release_notes(MINIMAL_MATRIX, entries)
    assert "## Platform Support" in result
    assert "1.26.3" in result
    assert "supported" in result.lower()


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run_tests():
    """Run all tests and report results."""
    tests = [
        test_parse_markers_basic,
        test_parse_markers_multiple_sections,
        test_parse_markers_empty_section,
        test_parse_markers_missing_end,
        test_parse_markers_mismatched_names,
        test_parse_markers_duplicate_section,
        test_parse_markers_nested_begin,
        test_parse_markers_orphan_end,
        test_replace_section_content,
        test_replace_preserves_markers,
        test_replace_idempotent,
        test_replace_nonexistent_section,
        test_generate_support_matrix,
        test_generate_compatibility_matrix,
        test_generate_installation_matrix,
        test_generate_status_matrix,
        test_generate_distribution_matrix,
        test_tier_mapping_resolved,
        test_tier_mapping_passthrough,
        test_section_registry_coverage,
        test_known_sections_complete,
        test_section_registry_matches_task_spec,
        test_write_then_check_idempotent,
        test_write_preserves_surrounding_content,
        test_full_cycle_all_sections,
        test_release_notes_output,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
            print(f"  PASS: {test_fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"  FAIL: {test_fn.__name__}: {e}")

    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
