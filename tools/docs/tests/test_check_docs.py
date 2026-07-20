"""Tests for the check_docs harness check.

Validates that maintained markdown surfaces are correctly identified and
that the internal reference policy rejects forbidden shorthand and
directory/glob references while allowing tracked file references.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow imports from tools/docs/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_docs as docs_checker
from check_docs import is_maintained_markdown
from check_docs import check_internal_reference_policy


def test_root_truth_surfaces_are_included():
    assert is_maintained_markdown("AGENTS.md") is True
    assert is_maintained_markdown("README.md") is True
    assert is_maintained_markdown("README_zh-CN.md") is True


def test_docs_tree_is_included_but_archive_is_not():
    assert is_maintained_markdown("docs/harness/README.md") is True
    assert is_maintained_markdown("docs/archive/old-note.md") is False


def test_local_scratch_markdown_is_excluded():
    assert is_maintained_markdown("review-findings.md") is False


def test_internal_reference_policy_rejects_spec_index_shorthand(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("Use spec 12 for rollout details.\n", encoding="utf-8")
    errors = check_internal_reference_policy([f], tracked_paths=set())
    assert any("avoid internal numbered references" in e for e in errors)


def test_internal_reference_policy_rejects_zero_padded_spec_index(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text(
        f"The behavior follows spec {7:03d}.\n",
        encoding="utf-8",
    )
    errors = check_internal_reference_policy([f], tracked_paths=set())
    assert any("avoid internal numbered references" in e for e in errors)


def test_document_updates_must_descend_by_version(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text(
        "## Document Updates\n\n"
        "| Version | Date | Summary |\n"
        "|---|---|---|\n"
        "| 0.9.0 | 2026-01-01 | Older |\n"
        "| 0.9.1 | 2026-02-01 | Newer |\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_document_updates_order([f])
    assert any("descending chronological order" in error for error in errors)


def test_document_updates_does_not_consume_a_later_section_table():
    content = (
        "## Document Updates\n\n"
        "No update ledger is present.\n\n"
        "## Compatibility\n\n"
        "| Version | Support |\n"
        "|---|---|\n"
        "| 0.9.1 | supported |\n"
    )

    assert docs_checker._document_update_table_lines(content) == []


def test_internal_reference_policy_rejects_kiro_directory_reference(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("See `.kiro/specs/` for details.\n", encoding="utf-8")
    errors = check_internal_reference_policy([f], tracked_paths=set())
    assert any("avoid directory/glob reference" in e for e in errors)


def test_internal_reference_policy_allows_tracked_kiro_file_reference(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text(
        "Baseline rules are in `.kiro/nginx-development-guide.md`.\n",
        encoding="utf-8",
    )
    errors = check_internal_reference_policy(
        [f], tracked_paths={".kiro/nginx-development-guide.md"}
    )
    assert errors == []


def test_operator_examples_reject_boolean_trusted_proxy_value(tmp_path):
    """Trusted proxies must name CIDRs rather than use the removed boolean model."""
    check_examples = getattr(
        docs_checker,
        "check_operator_config_examples",
        None,
    )
    assert callable(check_examples)
    doc = tmp_path / "OPERATIONS.md"
    doc.write_text(
        "Add `markdown_trusted_proxies on;` behind your reverse proxy.\n",
        encoding="utf-8",
    )

    errors = check_examples([doc])

    assert any("trusted proxy CIDR" in error for error in errors)


def test_unreleased_release_line_cannot_be_marked_stable(tmp_path):
    """Project status must not call an unreleased changelog line stable."""
    check_status = getattr(
        docs_checker,
        "check_release_status_consistency",
        None,
    )
    assert callable(check_status)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [0.9.1] - Unreleased\n", encoding="utf-8")
    project_status = tmp_path / "PROJECT_STATUS.md"
    project_status.write_text(
        "### Current Release Line 0.9.1\n\n"
        "**Status:** Current stable release.\n",
        encoding="utf-8",
    )

    errors = check_status(changelog, project_status)

    assert any("cannot be marked stable" in error for error in errors)


def test_unreleased_release_line_accepts_development_rc_status(tmp_path):
    """An unreleased development/RC status is consistent with the changelog."""
    check_status = getattr(
        docs_checker,
        "check_release_status_consistency",
        None,
    )
    assert callable(check_status)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [0.9.1] - Unreleased\n", encoding="utf-8")
    project_status = tmp_path / "PROJECT_STATUS.md"
    project_status.write_text(
        "### Current Release Line 0.9.1\n\n"
        "**Status:** Unreleased development and release-candidate line.\n",
        encoding="utf-8",
    )

    assert check_status(changelog, project_status) == []
