"""Tests for the check_docs harness check.

Validates that maintained markdown surfaces are correctly identified and
that the internal reference policy rejects forbidden shorthand and
directory/glob references while allowing tracked file references.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Allow imports from tools/docs/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_docs as docs_checker


def test_root_truth_surfaces_are_included():
    assert docs_checker.is_maintained_markdown("AGENTS.md") is True
    assert docs_checker.is_maintained_markdown("README.md") is True
    assert docs_checker.is_maintained_markdown("README_zh-CN.md") is True


def test_docs_tree_is_included_but_archive_is_not():
    assert docs_checker.is_maintained_markdown("docs/harness/README.md") is True
    assert docs_checker.is_maintained_markdown("docs/archive/old-note.md") is False


def test_local_scratch_markdown_is_excluded():
    assert docs_checker.is_maintained_markdown("review-findings.md") is False


def test_internal_reference_policy_rejects_spec_index_shorthand(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("Use spec 12 for rollout details.\n", encoding="utf-8")
    errors = docs_checker.check_internal_reference_policy([f], tracked_paths=set())
    assert any("avoid internal numbered references" in e for e in errors)


def test_internal_reference_policy_rejects_zero_padded_spec_index(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text(
        f"The behavior follows spec {7:03d}.\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_internal_reference_policy([f], tracked_paths=set())
    assert any("avoid internal numbered references" in e for e in errors)


def test_document_updates_must_descend_by_version(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text(
        "## Document Updates\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.9.0 | 2026-01-01 | Older |\n"
        "| 0.9.1 | 2026-02-01 | Newer |\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_document_updates_order([f])
    assert any("descending chronological order" in error for error in errors)


def test_document_updates_orders_release_above_rc(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text(
        "## Document Updates\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.9.2 | 2026-09-02 | Release |\n"
        "| 0.9.2rc5 | 2026-09-01 | Candidate |\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_document_updates_order([f])
    assert errors == []


def test_document_updates_rejects_rc_above_release(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text(
        "## Document Updates\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.9.2rc5 | 2026-09-01 | Candidate |\n"
        "| 0.9.2 | 2026-09-02 | Release |\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_document_updates_order([f])
    assert any("descending chronological order" in error for error in errors)


def test_document_updates_orders_release_above_dash_rc(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text(
        "## Document Updates\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.9.2 | 2026-09-02 | Release |\n"
        "| 0.9.2-rc5 | 2026-09-01 | Candidate |\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_document_updates_order([f])
    assert errors == []


def test_document_updates_rejects_dash_rc_above_release(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text(
        "## Document Updates\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.9.2-rc5 | 2026-09-01 | Candidate |\n"
        "| 0.9.2 | 2026-09-02 | Release |\n",
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


def test_document_updates_rejects_a_second_table_in_the_same_section(tmp_path):
    """A second ledger table in the section is checked, not bypassed."""
    f = tmp_path / "doc.md"
    f.write_text(
        "## Document Updates\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.9.2 | 2026-09-02 | Release |\n"
        "| 0.9.1 | 2026-07-14 | Older |\n"
        "\n"
        "A second table continues the ledger.\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.8.1 | 2026-01-01 | Oldest |\n"
        "| 0.9.0 | 2026-05-01 | Newer |\n",
        encoding="utf-8",
    )

    errors = docs_checker.check_document_updates_order([f])

    assert any("descending chronological order" in error for error in errors)


def test_document_updates_rejects_a_table_under_a_subheading(tmp_path):
    """A ledger table filed under a sub-heading is still inside the section."""
    f = tmp_path / "doc.md"
    f.write_text(
        "## Document Updates\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.9.2 | 2026-09-02 | Release |\n"
        "| 0.9.1 | 2026-07-14 | Older |\n"
        "\n"
        "### Archived Rows\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.8.0 | 2026-01-01 | Oldest |\n"
        "| 0.8.3 | 2026-06-26 | Newer |\n",
        encoding="utf-8",
    )

    errors = docs_checker.check_document_updates_order([f])

    assert any("descending chronological order" in error for error in errors)


def test_document_updates_accepts_every_ordered_table_in_the_section(tmp_path):
    """Both bypass shapes pass once their rows descend correctly."""
    f = tmp_path / "doc.md"
    f.write_text(
        "## Document Updates\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.9.2 | 2026-09-02 | Release |\n"
        "| 0.9.1 | 2026-07-14 | Older |\n"
        "\n"
        "### Archived Rows\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.8.3 | 2026-06-26 | Newer |\n"
        "| 0.8.0 | 2026-01-01 | Oldest |\n",
        encoding="utf-8",
    )

    assert docs_checker.check_document_updates_order([f]) == []


def test_document_updates_later_section_table_is_not_consumed_as_history():
    """A table after the next H2 stays out of the Document Updates scope."""
    content = (
        "## Document Updates\n\n"
        "| Version | Date | Notes |\n"
        "|---------|------|-------|\n"
        "| 0.9.2 | 2026-09-02 | Release |\n"
        "\n"
        "## Compatibility\n\n"
        "| Version | Support |\n"
        "|---|---|\n"
        "| 0.9.1 | supported |\n"
    )

    tables = docs_checker._document_update_tables(content)

    assert len(tables) == 1
    assert all("0.9.1 | supported" not in line for line in tables[0])


def test_internal_reference_policy_rejects_kiro_directory_reference(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("See `.kiro/specs/` for details.\n", encoding="utf-8")
    errors = docs_checker.check_internal_reference_policy([f], tracked_paths=set())
    assert any("avoid directory/glob reference" in e for e in errors)


def test_internal_reference_policy_allows_tracked_kiro_file_reference(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text(
        "Baseline rules are in `.kiro/nginx-development-guide.md`.\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_internal_reference_policy(
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


def test_unreleased_release_line_cannot_have_stable_release_notes(tmp_path):
    """Release notes must remain pending until the release is actually cut."""
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
        "**Status:** Release candidate.\n",
        encoding="utf-8",
    )
    release_notes = tmp_path / "0.9.1-release-notes.md"
    release_notes.write_text(
        "**Date**: 2026-07-28\n**Status**: Stable release\n",
        encoding="utf-8",
    )

    errors = check_status(changelog, project_status, release_notes)

    assert any("release notes" in error for error in errors)


def test_malformed_unreleased_heading_fails_closed(tmp_path):
    """An unreleased heading with an unexpected suffix must fail the gate."""
    check_status = getattr(
        docs_checker,
        "check_release_status_consistency",
        None,
    )
    assert callable(check_status)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## [0.9.2] - Unreleased candidate (rc3)\n",
        encoding="utf-8",
    )
    project_status = tmp_path / "PROJECT_STATUS.md"
    project_status.write_text(
        "### Current Release Line 0.9.2\n\n"
        "**Status:** Unreleased development and release-candidate line.\n",
        encoding="utf-8",
    )

    errors = check_status(changelog, project_status)

    assert any("malformed unreleased heading" in error for error in errors)


def test_malformed_unreleased_heading_after_valid_fails_closed(tmp_path):
    """A malformed heading later in the changelog must not be masked by an
    earlier valid unreleased heading."""
    check_status = getattr(
        docs_checker,
        "check_release_status_consistency",
        None,
    )
    assert callable(check_status)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## [0.9.2] - Unreleased candidate\n"
        "\n"
        "## [0.9.1] - Unreleased (draft)\n",
        encoding="utf-8",
    )
    project_status = tmp_path / "PROJECT_STATUS.md"
    project_status.write_text(
        "### Current Release Line 0.9.2\n\n"
        "**Status:** Unreleased development and release-candidate line.\n",
        encoding="utf-8",
    )

    errors = check_status(changelog, project_status)

    assert any("malformed unreleased heading" in error for error in errors)


def test_release_checklist_preamble_must_stay_static(tmp_path: Path) -> None:
    """The checklist guard must reject mutable status and accept requirements."""
    mutable = tmp_path / "0.9.2-release-checklist.md"
    mutable.write_text(
        "# 0.9.2 Release Checklist\n\n"
        "**Current status:** remote workflows have not certified the current head.\n\n"
        "## Pre-Release\n",
        encoding="utf-8",
    )
    assert docs_checker.check_release_checklist_is_static([mutable]), (
        "a preamble claiming current-head state must be rejected"
    )

    pinned = tmp_path / "0.9.3-release-checklist.md"
    pinned.write_text(
        "# 0.9.3 Release Checklist\n\n"
        "- [ ] Candidate f26e81897aedc48f79cf23943ee33496157ed0a9 passed CI\n\n"
        "## Pre-Release\n",
        encoding="utf-8",
    )
    assert docs_checker.check_release_checklist_is_static([pinned]), (
        "a requirement pinning a commit must be rejected"
    )

    history = tmp_path / "0.9.7-release-checklist.md"
    history.write_text(
        "# 0.9.7 Release Checklist\n\n"
        "Historical note: the rc6 tip 54eb5602 was the PR base.\n\n"
        "## Pre-Release\n",
        encoding="utf-8",
    )
    assert docs_checker.check_release_checklist_is_static([history]) == [], (
        "prose that records history may name a commit"
    )

    static = tmp_path / "0.9.4-release-checklist.md"
    static.write_text(
        "# 0.9.4 Release Checklist\n\n"
        "**This checklist states requirements, not status.**\n\n"
        "## Pre-Release\n",
        encoding="utf-8",
    )
    assert docs_checker.check_release_checklist_is_static([static]) == [], (
        "a requirements-only preamble must pass"
    )


def test_release_checklist_guard_rejects_explicit_status_fields(tmp_path: Path) -> None:
    """Explicit status fields and candidate-passed claims must also be rejected."""
    for body in (
        "# 0.9.5 Release Checklist\n\n**Status:** candidate passed all required gates.\n\n## Pre-Release\n",
        "# 0.9.6 Release Checklist\n\nStatus: all required gates passed\n\n## Pre-Release\n",
    ):
        path = tmp_path / f"{abs(hash(body))}-release-checklist.md"
        path.write_text(body, encoding="utf-8")
        assert docs_checker.check_release_checklist_is_static([path]), body

def test_iter_unfenced_lines_skips_tilde_fences():
    """Tilde fences are as valid as backtick fences and must be skipped too."""
    text = "before\n~~~\n- [ ] abc1234\nafter\n~~~\nreally after\n"
    kept = [line for _n, line in docs_checker.iter_unfenced_lines(text)]
    assert "- [ ] abc1234" not in kept
    assert "really after" in kept


def test_iter_unfenced_lines_needs_a_matching_closer():
    """A block opened with ``` is not closed by ~~~."""
    text = "~~~\nhidden\n```\nstill hidden\n~~~\nvisible\n"
    kept = [line for _n, line in docs_checker.iter_unfenced_lines(text)]
    assert "hidden" not in kept
    assert "still hidden" not in kept
    assert "visible" in kept


def test_checklist_guard_ignores_tilde_fenced_examples(tmp_path):
    """A fenced example may show a commit-bound requirement without failing."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text(
        "# Checklist\n\n~~~\n- [ ] certified at abc1234\n~~~\n\n- [ ] publish\n",
        encoding="utf-8",
    )
    assert docs_checker.check_release_checklist_is_static([path]) == []

def test_iter_unfenced_lines_respects_fence_run_rules():
    """A longer fence closes a shorter one; four leading spaces is not a fence."""
    kept = _assert_expected_line_is_unfenced(
        "```\ninner\n````\nstill inside\n```\nafter\n", "still inside"
    )
    assert "after" not in kept  # the 4 run then opened a new block
    kept2 = (
        _assert_expected_line_is_unfenced(
            "    ```\nnot a fence\n", "    ```"
        )
    )
    assert "not a fence" in kept2


def _assert_expected_line_is_unfenced(text: str, expected_line: str) -> list[str]:
    result = [line for _n, line in docs_checker.iter_unfenced_lines(text)]
    assert expected_line in result
    return result


def test_checklist_guard_accepts_plus_markers(tmp_path):
    """`+ [ ]` is a valid task-list marker and must be scanned like the others."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text("+ [ ] certified at abc1234\n", encoding="utf-8")
    failures = docs_checker.check_release_checklist_is_static([path])
    assert failures
    assert "names a commit" in failures[0]


def test_checklist_guard_scans_continuation_lines(tmp_path):
    """A commit pinned on a wrapped continuation line must still be rejected."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text(
        "- [ ] evidence passes the validator.  The snapshot anchors at its\n"
        "      historical measurement commit (`712c5300`, see the README).\n",
        encoding="utf-8",
    )
    failures = docs_checker.check_release_checklist_is_static([path])
    assert failures
    assert "names a commit" in failures[0]


def test_closing_fence_must_not_carry_trailing_text():
    """```python cannot close a block the way a bare fence does."""
    text = "```\ninside\n```python\nstill inside\n```\nafter\n"
    kept = [line for _n, line in docs_checker.iter_unfenced_lines(text)]
    assert "still inside" not in kept
    assert "after" in kept


def test_backtick_info_string_with_backtick_is_not_a_fence():
    """A backtick fence's info string cannot contain a backtick."""
    text = "```foo`bar\nstill text\n"
    kept = [line for _n, line in docs_checker.iter_unfenced_lines(text)]
    assert "still text" in kept


def test_task_marker_with_extra_spaces_is_recognised():
    """CommonMark allows one to four spaces after a list marker."""
    assert docs_checker._is_task_list_line("-  [ ] item")
    assert docs_checker._is_task_list_line("- [ ] item")
    assert not docs_checker._is_task_list_line("-     [ ] item")


def test_checklist_guard_sees_a_pinned_commit_after_extra_spaces(tmp_path):
    """A wider marker must not become a way to hide a pinned commit."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text("-  [ ] certified at abc1234\n", encoding="utf-8")
    failures = docs_checker.check_release_checklist_is_static([path])
    assert failures
    assert "names a commit" in failures[0]


def test_claim_split_across_lines_is_rejected(tmp_path):
    """A wrapped status claim must be judged as one sentence."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text(
        "- [ ] The suite is recorded. The current branch\n"
        "      head has the full remote workflow set\n"
        "      passing.\n",
        encoding="utf-8",
    )
    failures = docs_checker.check_release_checklist_is_static([path])
    assert failures
    assert "mutable candidate status" in failures[0]


def test_wrapped_prose_claim_is_rejected(tmp_path):
    """A status claim wrapped without indentation is still one sentence."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text(
        "Publish the release. The current branch head\n"
        "has the full remote workflow set passing.\n",
        encoding="utf-8",
    )
    failures = docs_checker.check_release_checklist_is_static([path])
    assert failures
    assert "mutable candidate status" in failures[0]


def test_lazy_continuation_commit_is_rejected(tmp_path):
    """An unindented continuation belongs to the item and is scanned."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text(
        "- [ ] evidence passes the validator.\ncommit abc1234\n",
        encoding="utf-8",
    )
    failures = docs_checker.check_release_checklist_is_static([path])
    assert failures
    assert "names a commit" in failures[0]


def test_heading_ends_the_task_item(tmp_path):
    """A heading opens a new block, so its text is not part of the item."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text(
        "- [ ] publish the release\n\n## Verification Record\n\ncommit abc1234\n",
        encoding="utf-8",
    )
    assert docs_checker.check_release_checklist_is_static([path]) == []


def test_blank_line_ends_the_task_item(tmp_path):
    """Prose a blank line below an item is not part of that item."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text(
        "- [ ] publish the release\n\nThe baseline anchors at abc1234.\n",
        encoding="utf-8",
    )
    assert docs_checker.check_release_checklist_is_static([path]) == []


def test_fence_block_ends_a_task_item(tmp_path):
    """A fenced example after an item is not read as its continuation."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text(
        "- [ ] publish\n```text\ncommit abc1234\n```\n\nplain prose\n",
        encoding="utf-8",
    )
    assert docs_checker.check_release_checklist_is_static([path]) == []


def test_checklist_keeps_a_nested_item_with_its_requirement(tmp_path):
    """A nested item belongs to the requirement above it, not to the next one."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text(
        "- [ ] publish the bundle\n  - [ ] nested detail\n- [ ] next step\n",
        encoding="utf-8",
    )
    assert docs_checker.check_release_checklist_is_static([path]) == []
    assert docs_checker._checklist_items(path.read_text(), set()) == [
        "- [ ] publish the bundle - [ ] nested detail",
        "- [ ] next step",
    ]


def test_status_claim_requires_the_colon(tmp_path):
    """Prose like "status rewrite" is not a release claim."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text("status rewrite planned\n", encoding="utf-8")
    assert docs_checker.check_release_checklist_is_static([path]) == []


def test_a_second_level_heading_ends_a_task_item(tmp_path):
    """Every ATX level opens a block, not only the first."""
    path = tmp_path / "0.9.2-release-checklist.md"
    path.write_text("- [ ] publish\n## Section\ncommit abc1234\n", encoding="utf-8")
    assert docs_checker.check_release_checklist_is_static([path]) == []
    assert docs_checker._checklist_items(path.read_text(), set()) == ["- [ ] publish"]


def test_an_invalid_fence_does_not_open_a_block() -> None:
    """A run of backticks only opens a block when it is a real fence."""
    assert docs_checker._starts_block("```bad`info") is False
    assert docs_checker._starts_block("```python") is True


_SURFACE_FIXTURES = (
    "README.md",
    "README_zh-CN.md",
    "docs/project/PROJECT_STATUS.md",
    "docs/project/VERSION_PLANNING.md",
    "docs/project/README.md",
    "docs/guides/INSTALLATION.md",
    "docs/guides/UPGRADE-TO-{version}.md",
    "docs/guides/VERSION_ROLLBACK-{version}.md",
    "docs/guides/{version}-breaking-changes.md",
    "docs/guides/MIGRATION-{version}.md",
    "docs/development/{version}-implementation-plan.md",
    "docs/releases/{version}-upgrade-and-rollback.md",
    "docs/releases/{version}-deployment-recommendation.md",
    "packaging/repo/apt/README.md",
    "CHANGELOG.md",
)


def _write_all_surfaces(root, version):
    """Write a minimal compliant release-surface set for the default manifest."""
    _write_stable_notes(root, version)
    for template in _SURFACE_FIXTURES:
        path = root / template.format(version=version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"v{version} shipped on 2027-01-01.\n", encoding="utf-8")


def _write_stable_notes(root, version):
    notes = root / "docs" / "releases"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / f"{version}-release-notes.md").write_text(
        "**Date**: 2026-01-01\n**Status**: Stable release\n", encoding="utf-8"
    )


def test_latest_dated_changelog_version_detects_released_line():
    assert docs_checker._latest_dated_changelog_version(
        "## [1.2.3] - 2026-01-01\n"
    ) == ("1.2.3", [])
    assert docs_checker._latest_dated_changelog_version(
        "## [1.2.3] - Unreleased candidate\n"
    ) == (None, [])


def test_latest_dated_changelog_version_rejects_impossible_dates():
    version, errors = docs_checker._latest_dated_changelog_version(
        "## [1.2.3] - 2026-99-99\n"
    )
    assert version is None
    assert any("invalid release date" in error for error in errors)


def test_latest_dated_changelog_version_rejects_unknown_headings():
    version, errors = docs_checker._latest_dated_changelog_version(
        "## [1.2.3] - next milestone\n"
    )
    assert version is None
    assert any("unrecognized release heading" in error for error in errors)


def test_stable_release_surface_check_flags_stale_candidate_text(tmp_path):
    (tmp_path / "README.md").write_text(
        "v9.9.9 is a development candidate and is not yet published.\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_stable_release_surfaces(
        tmp_path, "9.9.9", ("README.md",)
    )
    assert any("pre-release wording" in error for error in errors), errors


def test_stable_release_surface_check_accepts_released_text(tmp_path):
    (tmp_path / "README.md").write_text(
        "v9.9.9 shipped on 2027-01-01 as the current release.\n",
        encoding="utf-8",
    )
    _write_stable_notes(tmp_path, "9.9.9")
    assert (
        docs_checker.check_stable_release_surfaces(tmp_path, "9.9.9", ("README.md",))
        == []
    )


def test_stable_release_surface_check_ignores_history_table_rows(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "update.md").write_text(
        "## Document Updates\n\n"
        "| Version | Date | Author | Changes |\n"
        "|---------|------|--------|---------|\n"
        "| 9.9.9 | 2027-01-01 | Kang | Release-candidate matrix entries are not downloads |\n",
        encoding="utf-8",
    )
    _write_stable_notes(tmp_path, "9.9.9")
    assert (
        docs_checker.check_stable_release_surfaces(
            tmp_path, "9.9.9", ("docs/update.md",)
        )
        == []
    )


@pytest.mark.parametrize(
    "phrase", ("v9.9.9 remains unreleased", "v9.9.9 stays unpublished")
)
def test_stable_release_surface_check_flags_unreleased_wording(tmp_path, phrase):
    (tmp_path / "README.md").write_text(phrase + ".\n", encoding="utf-8")
    errors = docs_checker.check_stable_release_surfaces(
        tmp_path, "9.9.9", ("README.md",)
    )
    assert any("pre-release wording" in error for error in errors), phrase


def test_stable_surface_check_skips_while_unreleased(tmp_path):
    ran, failures = docs_checker._stable_surface_check(
        "## [1.2.4] - Unreleased candidate\n", tmp_path
    )
    assert ran is False
    assert failures == []


def test_stable_surface_check_runs_for_dated_release(tmp_path):
    _write_all_surfaces(tmp_path, "1.2.3")
    ran, failures = docs_checker._stable_surface_check(
        "## [1.2.3] - 2026-01-01\n", tmp_path
    )
    assert ran is True
    assert failures == []


def test_stable_surface_check_flags_missing_notes(tmp_path):
    ran, failures = docs_checker._stable_surface_check(
        "## [1.2.3] - 2026-01-01\n", tmp_path
    )
    assert ran is True
    assert any("missing release notes" in error for error in failures)


def test_stable_surface_check_flags_missing_surfaces(tmp_path):
    _write_stable_notes(tmp_path, "1.2.3")
    ran, failures = docs_checker._stable_surface_check(
        "## [1.2.3] - 2026-01-01\n", tmp_path
    )
    assert ran is True
    assert any("missing release surface" in error for error in failures)


@pytest.mark.parametrize(
    "phrase", ("v9.9.9 release pending", "pending publication of v9.9.9")
)
def test_stable_surface_check_flags_pending_publication_wording(tmp_path, phrase):
    (tmp_path / "README.md").write_text(phrase + ".\n", encoding="utf-8")
    errors = docs_checker.check_stable_release_surfaces(
        tmp_path, "9.9.9", ("README.md",)
    )
    assert any("pre-release wording" in error for error in errors), phrase


def test_release_notes_status_flags_development_candidate(tmp_path):
    notes = tmp_path / "docs" / "releases"
    notes.mkdir(parents=True)
    (notes / "9.9.9-release-notes.md").write_text(
        "# Release Notes: 9.9.9 Development Candidate\n**Status**: Development candidate\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_stable_release_surfaces(tmp_path, "9.9.9", ())
    assert any("carries status" in error for error in errors), errors


def test_release_notes_status_requires_stable_release(tmp_path):
    notes = tmp_path / "docs" / "releases"
    notes.mkdir(parents=True)
    notes_file = notes / "9.9.9-release-notes.md"
    notes_file.write_text("**Status**: Stable Release\n", encoding="utf-8")
    assert docs_checker.check_stable_release_surfaces(tmp_path, "9.9.9", ()) == []
    notes_file.write_text("# Notes without a status line\n", encoding="utf-8")
    errors = docs_checker.check_stable_release_surfaces(tmp_path, "9.9.9", ())
    assert any("missing release status" in error for error in errors), errors


def test_latest_dated_changelog_version_validates_all_dated_headings():
    version, errors = docs_checker._latest_dated_changelog_version(
        "## [1.2.3] - 2026-01-01\n## [1.2.2] - 2026-99-99\n"
    )
    assert version == "1.2.3"
    assert any("invalid release date" in error for error in errors)


def test_stable_surface_check_scans_changelog_prose(tmp_path):
    _write_stable_notes(tmp_path, "9.9.9")
    (tmp_path / "CHANGELOG.md").write_text(
        "## [9.9.9] - 2027-01-01\n\n9.9.9 is not yet published.\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_stable_release_surfaces(
        tmp_path, "9.9.9", ("CHANGELOG.md",)
    )
    assert any("pre-release wording" in error for error in errors), errors


def test_stable_surface_check_ignores_lower_unreleased_heading(tmp_path):
    _write_all_surfaces(tmp_path, "1.2.3")
    ran, failures = docs_checker._stable_surface_check(
        "## [1.2.3] - 2026-01-01\n## [1.2.2] - Unreleased\n", tmp_path
    )
    assert ran is True
    assert failures == []


def test_stable_surface_check_scans_changelog_by_default(tmp_path):
    _write_all_surfaces(tmp_path, "9.9.9")
    (tmp_path / "CHANGELOG.md").write_text(
        "## [9.9.9] - 2027-01-01\n\n9.9.9 is not yet published.\n",
        encoding="utf-8",
    )
    ran, failures = docs_checker._stable_surface_check(
        "## [9.9.9] - 2027-01-01\n", tmp_path
    )
    assert ran is True
    assert any("pre-release wording" in failure for failure in failures), failures


@pytest.mark.parametrize(
    "template",
    (
        "docs/releases/{version}-upgrade-and-rollback.md",
        "docs/releases/{version}-deployment-recommendation.md",
        "docs/development/{version}-implementation-plan.md",
    ),
)
def test_release_surface_manifest_covers_release_docs(template):
    assert template in docs_checker.RELEASE_SURFACE_FILES


def test_latest_dated_changelog_version_requires_a_heading():
    version, errors = docs_checker._latest_dated_changelog_version("no headings\n")
    assert version is None
    assert any("missing release heading" in error for error in errors)


def test_stable_surface_check_fails_closed_without_heading(tmp_path):
    ran, failures = docs_checker._stable_surface_check("", tmp_path)
    assert ran is False
    assert any("missing release heading" in failure for failure in failures)


def test_latest_dated_changelog_version_requires_descending_order():
    version, errors = docs_checker._latest_dated_changelog_version(
        "## [0.9.1] - 2026-07-01\n## [0.9.2] - 2026-09-01\n"
    )
    assert version == "0.9.1"
    assert any("descending order" in error for error in errors)


def test_stable_surface_check_carries_version_across_blocks(tmp_path):
    notes = tmp_path / "docs" / "releases"
    notes.mkdir(parents=True)
    (notes / "9.9.9-release-notes.md").write_text(
        "# Release Notes: 9.9.9\n**Status**: Stable release\n\n"
        "The release is not yet published.\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_stable_release_surfaces(
        tmp_path, "9.9.9", ()
    )
    assert any("pre-release wording" in error for error in errors), errors


def test_latest_dated_changelog_version_ignores_lower_dated_after_unreleased():
    version, errors = docs_checker._latest_dated_changelog_version(
        "## [1.2.4] - Unreleased candidate\n## [1.2.3] - 2026-01-01\n"
    )
    assert version is None


def test_release_surface_manifest_includes_project_index():
    assert "docs/project/README.md" in docs_checker.RELEASE_SURFACE_FILES


def test_stable_claim_failures_ignore_fenced_examples(tmp_path):
    _write_stable_notes(tmp_path, "9.9.9")
    (tmp_path / "README.md").write_text(
        "v9.9.9 shipped.\n\n```bash\n# 9.9.9 is not yet published\n```\n",
        encoding="utf-8",
    )
    assert (
        docs_checker.check_stable_release_surfaces(tmp_path, "9.9.9", ("README.md",))
        == []
    )


def test_stable_claim_failures_flag_not_released_wording(tmp_path):
    _write_stable_notes(tmp_path, "9.9.9")
    (tmp_path / "README.md").write_text("v9.9.9 is not released yet.\n", encoding="utf-8")
    errors = docs_checker.check_stable_release_surfaces(
        tmp_path, "9.9.9", ("README.md",)
    )
    assert any("pre-release wording" in error for error in errors), errors


def test_stable_claim_failures_inherit_parent_heading_context(tmp_path):
    _write_stable_notes(tmp_path, "9.9.9")
    (tmp_path / "README.md").write_text(
        "## 9.9.9 release\n\n### Details\n\nThe build is not yet published.\n",
        encoding="utf-8",
    )
    errors = docs_checker.check_stable_release_surfaces(
        tmp_path, "9.9.9", ("README.md",)
    )
    assert any("pre-release wording" in error for error in errors), errors


def test_release_notes_status_ignores_fenced_examples(tmp_path):
    notes = tmp_path / "docs" / "releases"
    notes.mkdir(parents=True)
    (notes / "9.9.9-release-notes.md").write_text(
        "**Status**: Stable release\n\n```text\n**Status**: Development candidate\n```\n",
        encoding="utf-8",
    )
    assert docs_checker.check_stable_release_surfaces(tmp_path, "9.9.9", ()) == []


def test_latest_dated_changelog_version_ignores_fenced_headings():
    version, errors = docs_checker._latest_dated_changelog_version(
        "```md\n## [9.9.9] - Unreleased\n```\n## [0.9.2] - 2026-09-19\n"
    )
    assert version == "0.9.2"
    assert errors == []


@pytest.mark.parametrize(
    "phrase", ("9.9.9 is pending", "v9.9.9 release is still pending")
)
def test_stable_claim_failures_flag_pending_claims(tmp_path, phrase):
    _write_stable_notes(tmp_path, "9.9.9")
    (tmp_path / "README.md").write_text(phrase + ".\n", encoding="utf-8")
    errors = docs_checker.check_stable_release_surfaces(
        tmp_path, "9.9.9", ("README.md",)
    )
    assert any("pre-release wording" in error for error in errors), phrase


@pytest.mark.parametrize(
    "heading", ("## 9.9.9 Development Candidate", "## 9.9.9 Release Candidate")
)
def test_stable_claim_failures_flag_candidate_headings(tmp_path, heading):
    _write_stable_notes(tmp_path, "9.9.9")
    (tmp_path / "README.md").write_text(heading + "\n", encoding="utf-8")
    errors = docs_checker.check_stable_release_surfaces(
        tmp_path, "9.9.9", ("README.md",)
    )
    assert any("pre-release wording" in error for error in errors), heading


# --------------------------------------------------------------------------
# Release-state contract: pre-publication and post-publication surfaces.
# --------------------------------------------------------------------------

_PENDING_SURFACES = {
    "README.md": "> Current line: v9.9.9 is a release candidate and the project has\n"
    "> not published it yet. The latest public stable release is v9.8.8.\n",
    "README_zh-CN.md": "> 当前版本线：v9.9.9 是发布候选版本，尚未发布。\n",
    "docs/project/PROJECT_STATUS.md": "### Current Release Line 9.9.9\n\n"
    "**Status:** Release candidate. 9.8.8 is the latest released version\n"
    "(2026-01-01). 9.9.9 is the final pre-1.0 breaking release and the project\n"
    "publishes it after the merge.\n",
    "docs/project/README.md": "The v9.9.9 release is pending publication; v9.8.8\n"
    "remains the latest published stable release.\n",
    "docs/project/VERSION_PLANNING.md": "v9.9.9 is the current development line\n"
    "and is a release candidate. The project publishes it after the merge.\n",
    "docs/development/9.9.9-implementation-plan.md": "## Release Status\n\n"
    "9.9.9 is a release candidate pending publication.\n"
    "The latest published stable tag is v9.8.8. The planned tag is v9.9.9.\n",
    "docs/guides/INSTALLATION.md": "The project has not published 9.9.9 yet.\n"
    "Set RELEASE_TAG to the latest published tag until the v9.9.9 assets\n"
    "become available.\n",
    "docs/guides/UPGRADE-TO-9.9.9.md": "> Publication status: 9.9.9 is a release\n"
    "> candidate and the project has not published it yet.\n",
    "docs/guides/VERSION_ROLLBACK-9.9.9.md": "This guide covers rolling back\n"
    "the 9.9.9 release candidate, which is not yet published.\n",
    "docs/guides/9.9.9-breaking-changes.md":
    "Breaking-change reference for the pending 9.9.9 release candidate.\n",
    "docs/releases/9.9.9-release-notes.md": "# Release Notes: 9.9.9\n\n"
    "**Date**: Pending publication\n\n"
    "**Status**: Pending release. This document describes the release candidate\n"
    "for the v9.9.9 line and does not assert that a tag or checksum exists.\n",
    "docs/releases/9.9.9-deployment-recommendation.md":
    "Record the v9.9.9 tag and commit SHA as the release identity once published.\n",
    "packaging/repo/apt/README.md": "The example below uses the v9.8.8 release\n"
    "assets, the latest published release. The v9.9.9 assets become available\n"
    "after publication.\n",
}

# The same surfaces once the release is real: no pending wording survives.
_PUBLISHED_SURFACES = {
    "README.md": "> Current line: v9.9.9 shipped on 2026-02-02 as the final\n"
    "> breaking release before v1.0.\n",
    "README_zh-CN.md": "> 当前版本线：v9.9.9 已正式发布（2026-02-02）。\n",
    "docs/project/PROJECT_STATUS.md": "### Current Release Line 9.9.9\n\n"
    "**Status:** Stable release. 9.9.9 is the latest released version,\n"
    "published 2026-02-02.\n",
    "docs/project/VERSION_PLANNING.md": "v9.9.9 is the current released line\n"
    "(published 2026-02-02).\n",
    "docs/project/README.md": "The v9.9.9 line is the latest published stable\n"
    "release.\n",
    "docs/development/9.9.9-implementation-plan.md": "## Release Status\n\n"
    "The latest published stable tag is v9.9.9 (2026-02-02).\n",
    "docs/guides/INSTALLATION.md": "Set RELEASE_TAG to the published v9.9.9 tag\n"
    "and run the authenticated sequence.\n",
    "docs/guides/UPGRADE-TO-9.9.9.md": "> Publication status: 9.9.9 shipped\n"
    "> (2026-02-02). The release carries the tag and signed artifacts.\n",
    "docs/guides/VERSION_ROLLBACK-9.9.9.md":
    "This guide covers rolling back the released 9.9.9 version.\n",
    "docs/guides/9.9.9-breaking-changes.md":
    "Breaking-change reference for the released 9.9.9 version.\n",
    "docs/releases/9.9.9-release-notes.md": "# Release Notes: 9.9.9\n\n"
    "**Date**: 2026-02-02\n\n**Status**: Stable release\n",
    "docs/releases/9.9.9-deployment-recommendation.md":
    "Record the published v9.9.9 tag and commit SHA as the release identity.\n",
    "packaging/repo/apt/README.md": "The example below uses the v9.9.9 release\n"
    "assets and the published checksum manifest.\n",
}

_CHANGELOG_PENDING = (
    "## [9.9.9] - Unreleased\n\nPending work.\n\n"
    "## [9.8.8] - 2026-01-01\n\nReleased work.\n"
)


def _write_pending_state(root, *, changelog=_CHANGELOG_PENDING, **overrides):
    """Write the canonical pre-publication surface set plus the changelog."""
    (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    notes = root / "docs" / "releases"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / "9.8.8-release-notes.md").write_text(
        "# Release Notes: 9.8.8\n\n**Date**: 2026-01-01\n**Status**: Stable release\n",
        encoding="utf-8",
    )
    _write_surfaces(root, dict(_PENDING_SURFACES) | overrides)


def _write_surfaces(root, surfaces):
    """Write each ``rel -> body`` surface under ``root``, creating parents."""
    for rel, body in surfaces.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def test_release_state_contract_accepts_canonical_pre_publication_state(tmp_path):
    """The real pre-publication shape passes: v9.8.8 stable, v9.9.9 pending."""
    _write_pending_state(tmp_path)

    assert docs_checker.check_release_state_contract(tmp_path) == []


def test_release_state_contract_checks_current_unreleased_changelog_prose(
    tmp_path,
):
    """A published claim under the current Unreleased heading must fail."""
    changelog = (
        "## [9.9.9] - Unreleased\n\n"
        "The v9.9.9 release has been published and its assets are available.\n\n"
        "## [9.8.8] - 2026-01-01\n\nReleased work.\n"
    )
    _write_pending_state(tmp_path, changelog=changelog)

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("CHANGELOG.md" in failure and "published" in failure for failure in failures)


def test_release_state_contract_uses_unreleased_heading_context(tmp_path):
    """Claims under the pending heading apply to that release version."""
    changelog = (
        "## [9.9.9] - Unreleased\n\n"
        "The release has been published.\n\n"
        "## [9.8.8] - 2026-01-01\n\nReleased work.\n"
    )
    _write_pending_state(tmp_path, changelog=changelog)

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("CHANGELOG.md" in failure and "published" in failure for failure in failures)


def test_release_state_contract_ignores_published_claims_in_changelog_history(
    tmp_path,
):
    """A historical release description does not violate pending-state prose."""
    changelog = (
        "## [9.9.9] - Unreleased\n\nPending work.\n\n"
        "## [9.8.8] - 2026-01-01\n\n"
        "The v9.8.8 release was published with its assets.\n"
    )
    _write_pending_state(tmp_path, changelog=changelog)

    assert docs_checker.check_release_state_contract(tmp_path) == []


def _v092_is_pending() -> bool:
    changelog = (docs_checker.ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return any(
        line.strip() == "## [0.9.2] - Unreleased"
        for line in changelog.splitlines()
    )


@pytest.mark.skipif(
    not _v092_is_pending(),
    reason="pending-release wording applies only before v0.9.2 publication",
)
def test_implementation_plan_distinguishes_prepared_notes_from_publication():
    """WI-11 must separate prepared notes from unpublished release assets."""
    plan_text = (
        docs_checker.ROOT / "docs/development/0.9.2-implementation-plan.md"
    ).read_text(encoding="utf-8")
    wi11 = plan_text.partition("### WI-11:")[2].partition("### WI-12:")[0]
    normalized_wi11 = " ".join(wi11.split())
    assert "first prepared on 2026-09-19" in wi11
    assert "revised through 2026-09-23" in wi11
    assert "Publication of the tag and assets remains pending." in normalized_wi11

    status_table = plan_text.partition("## 5. Task Status Tracking")[2]
    wi11_row = next(
        (line for line in status_table.splitlines() if line.startswith("| 11 |")),
        "",
    )
    assert "first prepared on 2026-09-19" in wi11_row
    assert "revised through 2026-09-23" in wi11_row
    assert "Publication of the tag and assets remains pending." in wi11_row


def test_release_document_history_does_not_claim_a_planned_publish_date():
    """Document-update dates must not be presented as release metadata."""
    paths = (
        "docs/guides/UPGRADE-TO-0.9.2.md",
        "docs/development/0.9.2-implementation-plan.md",
        "docs/releases/0.9.2-release-notes.md",
        "docs/project/VERSION_PLANNING.md",
        "docs/project/PROJECT_STATUS.md",
    )
    unsupported_claims = (
        "Planned publication date recorded in the release metadata",
        "Planned publication date adjusted in the release metadata",
        "Planned publication date corrected in the release metadata",
    )

    for relative_path in paths:
        text = (docs_checker.ROOT / relative_path).read_text(encoding="utf-8")
        assert not any(claim in text for claim in unsupported_claims), relative_path


@pytest.mark.skipif(
    not _v092_is_pending(),
    reason="pending-release wording applies only before v0.9.2 publication",
)
def test_version_planning_scopes_the_candidate_release_description():
    """The release plan must keep its timing and compatibility scope clear."""
    text = (docs_checker.ROOT / "docs/project/VERSION_PLANNING.md").read_text(
        encoding="utf-8"
    )
    current_state = " ".join(
        text.partition("## Current Release State")[2]
        .partition("## ")[0]
        .split()
    )

    assert (
        "project plans to publish it after the merge and candidate-bound gates "
        "pass"
    ) in current_state
    assert "consolidates the v0.9.1 baseline and resets compatibility" in current_state


@pytest.mark.skipif(
    not _v092_is_pending(),
    reason="pending-release wording applies only before v0.9.2 publication",
)
def test_implementation_plan_scopes_historical_pending_labels():
    """Historical work-item notes must not imply publication is historical."""
    plan_text = (
        docs_checker.ROOT / "docs/development/0.9.2-implementation-plan.md"
    ).read_text(encoding="utf-8")
    intro = " ".join(
        plan_text.partition("## 1. Baseline Information")[2]
        .partition("### Historical 0.9.1 Baseline")[0]
        .split()
    )
    assert (
        "Completed work-item statuses and dated `Document Updates` entries are "
        "historical snapshots."
    ) in intro
    assert "WI-8 publication section below show the current state" in intro
    assert (
        "v0.9.2 remains pending publication, including its tag, assets, and "
        "checksums."
    ) in intro
    assert "plan-era `pending` wording below is historical" not in intro


@pytest.mark.skipif(
    not _v092_is_pending(),
    reason="pending-release wording applies only before v0.9.2 publication",
)
def test_rollback_guide_history_does_not_claim_v092_was_released():
    """The revision log must reflect the unpublished release candidate."""
    rollback = (
        docs_checker.ROOT / "docs/guides/VERSION_ROLLBACK-0.9.2.md"
    ).read_text(encoding="utf-8")
    history_row = next(
        (
            line
            for line in rollback.splitlines()
            if "| 0.9.2 | 2026-09-19 | Kang |" in line
        ),
        "",
    )
    assert "v0.9.2 release candidate" in history_row
    assert "released v0.9.2" not in history_row.lower()


@pytest.mark.skipif(
    not _v092_is_pending(),
    reason="pending-release wording applies only before v0.9.2 publication",
)
def test_upgrade_guide_does_not_substitute_an_older_release_tag():
    """A pending target release must not silently downgrade the download."""
    guide = (
        docs_checker.ROOT / "docs/guides/UPGRADE-TO-0.9.2.md"
    ).read_text(encoding="utf-8")
    assert "wait for its assets; do not substitute v0.9.1" in guide.lower()
    assert "replace v0.9.2 with the latest published tag" not in guide.lower()


def test_release_state_contract_accepts_future_publication_clause(tmp_path):
    """Availability after the named version is published remains conditional."""
    _write_pending_state(
        tmp_path,
        **{
            "packaging/repo/apt/README.md":
            "The v9.9.9 assets will be available after v9.9.9 is published.\n",
        },
    )

    assert docs_checker.check_release_state_contract(tmp_path) == []


def test_release_state_contract_rejects_pending_version_as_latest_tag(tmp_path):
    _write_pending_state(
        tmp_path,
        **{
            "docs/development/9.9.9-implementation-plan.md":
            "## Release Status\n\n"
            "9.9.9 is a release candidate pending publication.\n\n"
            "- Latest tag: v9.9.9\n",
        },
    )

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("latest tag" in failure for failure in failures), failures


def test_release_state_contract_rejects_pending_version_as_latest_stable_release(
    tmp_path,
):
    _write_pending_state(
        tmp_path,
        **{
            "README.md": "v9.9.9 is pending publication. "
            "The v9.9.9 is the latest stable release.\n",
        },
    )

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("latest tag" in failure for failure in failures), failures


def test_release_state_contract_scans_versioned_breaking_changes_guide(tmp_path):
    _write_pending_state(tmp_path)
    guide = tmp_path / "docs/guides/9.9.9-breaking-changes.md"
    guide.parent.mkdir(parents=True, exist_ok=True)
    guide.write_text(
        "The v9.9.9 release has been published.\n",
        encoding="utf-8",
    )

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("9.9.9-breaking-changes.md" in failure for failure in failures)


def test_release_state_contract_rejects_a_published_claim_while_pending(tmp_path):
    """A current-state surface cannot call the pending version published."""
    _write_pending_state(
        tmp_path,
        **{
            "README.md": "> Current line: v9.9.9 is a release candidate but shipped on\n"
            "> 2026-01-01 despite publication being pending. Use the `v9.9.9` tag\n"
            "> for installation.\n",
        },
    )

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("pending 9.9.9" in failure for failure in failures), failures


def test_release_state_contract_rejects_a_stable_status_while_pending(tmp_path):
    """The project status section cannot declare the pending line stable."""
    _write_pending_state(
        tmp_path,
        **{
            "docs/project/PROJECT_STATUS.md": "### Current Release Line 9.9.9\n\n"
            "**Status:** Stable release. 9.9.9 is the latest released version, "
            "published 2026-01-01.\n",
        },
    )

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("stable release" in failure or "pending 9.9.9" in failure
               for failure in failures), failures


def test_release_state_contract_requires_a_publication_boundary(tmp_path):
    """Prose that names the pending version must state the publication gate."""
    _write_pending_state(
        tmp_path,
        **{"docs/guides/INSTALLATION.md": "Install guide for 9.9.9 operators.\n"},
    )

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("publication boundary" in failure for failure in failures), failures


def test_release_state_contract_ignores_history_and_fenced_examples(tmp_path):
    """Ledger rows and fenced commands may name the pending version freely."""
    _write_pending_state(
        tmp_path,
        **{
            "README.md": "> Current line: v9.9.9 is a release candidate and the\n"
            "> project has not published it yet.\n"
            "\n## Document Updates\n\n"
            "| Version | Date | Change |\n"
            "|---------|------|--------|\n"
            "| 9.9.9 | 2026-01-01 | Release finalization prepared for the published 9.9.9 |\n"
            "\n```bash\n"
            "VERSION=v9.9.9  # 9.9.9 shipped on 2026-01-01\n"
            "```\n",
        },
    )

    assert docs_checker.check_release_state_contract(tmp_path) == []


def test_release_state_contract_accepts_post_publication_state(tmp_path):
    """A dated top changelog entry flips the same contract to the released side."""
    _write_pending_state(
        tmp_path,
        changelog="## [9.9.9] - 2026-02-02\n\nReleased work.\n",
    )
    _write_surfaces(tmp_path, _PUBLISHED_SURFACES)

    assert docs_checker.check_release_state_contract(tmp_path) == []


def test_release_state_contract_requires_the_published_baseline_to_stay_canonical(
    tmp_path,
):
    """The released baseline keeps its stable notes and changelog date."""
    _write_pending_state(tmp_path)
    (tmp_path / "docs" / "releases" / "9.8.8-release-notes.md").write_text(
        "# Release Notes: 9.8.8\n\n**Date**: 2026-01-01\n**Status**: Pending release\n",
        encoding="utf-8",
    )

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("9.8.8" in failure for failure in failures), failures


def test_release_state_contract_surfaces_the_malformed_unreleased_heading(tmp_path):
    """A malformed unreleased heading fails the contract instead of hiding."""
    _write_pending_state(
        tmp_path,
        changelog="## [9.9.9] - Unreleased candidate (rc3)\n",
    )

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("malformed unreleased heading" in failure for failure in failures)


def test_release_state_contract_has_no_failures_without_a_changelog(tmp_path):
    """A missing changelog leaves the decision to the checks that own it."""
    assert docs_checker.check_release_state_contract(tmp_path) == []


def test_pending_claim_window_scopes_a_verb_to_its_own_version():
    """A completion verb credits the version nearest to it in the sentence."""
    window = "9.9.9 is the current development line and 9.8.8 was published."
    assert not docs_checker._claim_belongs_to_pending_version(window, "9.9.9")
    window = "9.9.9 was published as the final breaking release."
    assert docs_checker._claim_belongs_to_pending_version(window, "9.9.9")


def test_unreleased_context_alone_does_not_flag_ordinary_availability():
    """Heading context without a version needs a release subject to be a claim."""
    version_pattern = re.compile(r"\bv?9\.9\.9\b")
    ordinary, _ = docs_checker._pending_sentence_failures(
        "CHANGELOG.md",
        "The new directive is available.",
        "9.9.9",
        version_pattern,
        True,
    )
    assert ordinary == []
    claim, _ = docs_checker._pending_sentence_failures(
        "CHANGELOG.md",
        "The release has been published.",
        "9.9.9",
        version_pattern,
        True,
    )
    assert claim


@pytest.mark.parametrize(
    "claim",
    [
        "The v9.9.9 assets are available after publication.",
        "The v9.9.9 assets become available once published.",
        "The v9.9.9 release is not currently published.",
        "No v9.9.9 assets have been published yet.",
        "The v9.9.9 release 将已发布.",
    ],
)
def test_release_state_contract_accepts_conditional_and_negated_claims(
    tmp_path, claim
):
    """Publication conditions and current negation are not completed claims."""
    _write_pending_state(
        tmp_path,
        **{"packaging/repo/apt/README.md": claim + "\n"},
    )

    assert docs_checker.check_release_state_contract(tmp_path) == []


@pytest.mark.parametrize(
    "claim",
    [
        "The v9.9.9 assets are available only after integration testing.",
        "The v9.9.9 release is pending, e.g. the release build has been published.",
        "The v9.9.9 release 已正式发布.",
    ],
)
def test_release_state_contract_rejects_affirmative_claims_without_publication(
    tmp_path, claim
):
    """Testing qualifiers and examples do not hide affirmative release claims."""
    _write_pending_state(
        tmp_path,
        **{"packaging/repo/apt/README.md": claim + "\n"},
    )

    failures = docs_checker.check_release_state_contract(tmp_path)

    assert any("without a publication boundary" in failure for failure in failures)


def test_pending_release_version_token_rejects_prerelease_and_longer_versions():
    """A stable pending version does not match prerelease or longer versions."""
    assert not docs_checker._claim_belongs_to_pending_version(
        "v9.9.9-rc5 was published.", "9.9.9"
    )
    assert not docs_checker._claim_belongs_to_pending_version(
        "v9.9.9.1 was published.", "9.9.9"
    )
    assert docs_checker._claim_belongs_to_pending_version(
        "v9.9.9 was published.", "9.9.9"
    )


def test_pending_sentence_reports_the_claim_tied_to_the_pending_version():
    """The diagnostic names the pending release's claim, not another release."""
    failures, _ = docs_checker._pending_sentence_failures(
        "guide.md",
        "v9.8.8 was published, while v9.9.9 assets are available.",
        "9.9.9",
        re.compile(r"\bv?9\.9\.9\b"),
    )

    assert len(failures) == 1
    assert "'available'" in failures[0]


def test_pending_release_contract_fails_when_a_configured_surface_is_missing(
    tmp_path,
):
    """Required release-state documents cannot silently disappear."""
    failures = docs_checker.check_pending_release_state(
        tmp_path, "9.9.9", surface_rel_paths=("docs/required.md",)
    )

    assert any("docs/required.md" in failure for failure in failures)
    assert any("missing" in failure.lower() for failure in failures)


def test_current_unreleased_changelog_section_excludes_dated_history():
    """Pending-state parsing stops before older dated release entries."""
    changelog = (
        "## [9.9.9] - Unreleased\nPending work.\n"
        "## [9.8.8] - 2026-01-01\nThe old release was published.\n"
    )

    section = docs_checker._current_unreleased_changelog_section(
        changelog, "9.9.9"
    )

    assert "Pending work." in section
    assert "9.8.8" not in section
    assert "published" not in section


def test_main_deduplicates_repeated_checker_diagnostics(tmp_path, monkeypatch, capsys):
    """The top-level check prints a repeated diagnostic only once."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [9.9.9] - Unreleased\n", encoding="utf-8")
    monkeypatch.setattr(docs_checker, "ROOT", tmp_path)
    monkeypatch.setattr(docs_checker, "iter_markdown_files", lambda: [])
    monkeypatch.setattr(
        docs_checker, "_find_unreleased_changelog_line", lambda _: ("9.9.9", [])
    )
    def duplicate(*args, **kwargs):
        return ["repeated diagnostic"] * 2

    for name in (
        "check_links",
        "check_heading_hierarchy",
        "check_english_policy",
        "check_internal_reference_policy",
        "check_operator_config_examples",
        "check_release_status_consistency",
        "check_release_state_contract",
        "check_duplicate_sync",
        "check_document_updates_order",
        "check_metric_family_count",
        "check_release_checklist_is_static",
    ):
        monkeypatch.setattr(docs_checker, name, duplicate)
    monkeypatch.setattr(docs_checker, "_stable_surface_check", lambda *_: (False, []))

    assert docs_checker.main() == 1
    assert capsys.readouterr().out.count("- repeated diagnostic") == 1
