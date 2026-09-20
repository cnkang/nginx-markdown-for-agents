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
    text = "```\ninner\n````\nstill inside\n```\nafter\n"
    kept = [line for _n, line in docs_checker.iter_unfenced_lines(text)]
    assert "still inside" in kept  # the 4-backtick run closed the 3 run
    assert "after" not in kept  # the 4 run then opened a new block
    indented = "    ```\nnot a fence\n"
    kept2 = [line for _n, line in docs_checker.iter_unfenced_lines(indented)]
    assert "    ```" in kept2
    assert "not a fence" in kept2


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


def test_stable_release_surface_check_flags_unreleased_wording(tmp_path):
    for phrase in ("v9.9.9 remains unreleased", "v9.9.9 stays unpublished"):
        (tmp_path / "README.md").write_text(phrase + ".\n", encoding="utf-8")
        errors = docs_checker.check_stable_release_surfaces(
            tmp_path, "9.9.9", ("README.md",)
        )
        assert any("pre-release wording" in error for error in errors), phrase
    errors = docs_checker.check_stable_release_surfaces(
        tmp_path, "9.9.9", ("README.md",)
    )
    assert any("pre-release wording" in error for error in errors), errors


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


def test_stable_surface_check_flags_pending_publication_wording(tmp_path):
    for phrase in ("v9.9.9 release pending", "pending publication of v9.9.9"):
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


def test_release_surface_manifest_covers_release_docs():
    for template in (
        "docs/releases/{version}-upgrade-and-rollback.md",
        "docs/releases/{version}-deployment-recommendation.md",
        "docs/releases/{version}-implementation-plan.md".replace(
            "docs/releases", "docs/development"
        ),
    ):
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


def test_stable_claim_failures_flag_pending_claims(tmp_path):
    _write_stable_notes(tmp_path, "9.9.9")
    for phrase in ("9.9.9 is pending", "v9.9.9 release is still pending"):
        (tmp_path / "README.md").write_text(phrase + ".\n", encoding="utf-8")
        errors = docs_checker.check_stable_release_surfaces(
            tmp_path, "9.9.9", ("README.md",)
        )
        assert any("pre-release wording" in error for error in errors), phrase


def test_stable_claim_failures_flag_candidate_headings(tmp_path):
    _write_stable_notes(tmp_path, "9.9.9")
    for heading in ("## 9.9.9 Development Candidate", "## 9.9.9 Release Candidate"):
        (tmp_path / "README.md").write_text(heading + "\n", encoding="utf-8")
        errors = docs_checker.check_stable_release_surfaces(
            tmp_path, "9.9.9", ("README.md",)
        )
        assert any("pre-release wording" in error for error in errors), heading
