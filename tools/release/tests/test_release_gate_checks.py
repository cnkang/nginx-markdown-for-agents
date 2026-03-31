"""Focused tests for release gate check edge cases."""

from pathlib import Path

from tools.release.release_gate_checks import (
    check_boundary_descriptions,
    check_checklist_verifiability,
    check_design_completeness,
    check_dod_evaluation_tables,
    check_requirements_completeness,
)


def _make_subspec_dir(tmp_path: Path, name: str = "5-overall-scope-release-gates") -> Path:
    """Create a minimal sub-spec directory that matches release-gate discovery rules."""
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_checklist_verifiability_ignores_fenced_code_and_counts_checked_items(tmp_path: Path):
    """Checklist entries inside fenced blocks are ignored; checked items are still validated."""
    release_dir = tmp_path / "release-gates"
    release_dir.mkdir()
    checklist = release_dir / "release-checklist.md"
    checklist.write_text(
        "- [x] make release-gates-check\n"
        "```md\n"
        "- [ ] example item in docs only\n"
        "```\n"
        "- [ ] docs/project/release-gates/README.md updated\n",
        encoding="utf-8",
    )

    passed, messages = check_checklist_verifiability(str(release_dir))
    assert passed
    assert "  INFO  Found 2 checklist items" in messages
    assert "  PASS  All checklist items have verifiable references" in messages


def test_checklist_verifiability_validates_checked_items(tmp_path: Path):
    """Checked checklist entries must still include verifiable evidence text."""
    release_dir = tmp_path / "release-gates"
    release_dir.mkdir()
    checklist = release_dir / "release-checklist.md"
    checklist.write_text("- [X] this should be reviewed manually\n", encoding="utf-8")

    passed, messages = check_checklist_verifiability(str(release_dir))
    assert not passed
    assert "  INFO  Found 1 checklist items" in messages
    assert "  FAIL  Non-verifiable item: this should be reviewed manually" in messages


def test_requirements_completeness_handles_read_errors(tmp_path: Path):
    """Unreadable requirement files should produce a failure message, not a crash."""
    subspec = _make_subspec_dir(tmp_path)
    (subspec / "requirements.md").write_bytes(b"\xff\xfe\xfd")

    passed, messages = check_requirements_completeness(str(tmp_path))
    assert not passed
    assert any("requirements.md read error:" in msg for msg in messages)


def test_boundary_descriptions_handles_read_errors(tmp_path: Path):
    """Unreadable design files should produce a failure message, not a crash."""
    subspec = _make_subspec_dir(tmp_path)
    (subspec / "design.md").write_bytes(b"\xff\xfe\xfd")

    passed, messages = check_boundary_descriptions(str(tmp_path))
    assert not passed
    assert any("design.md read error:" in msg for msg in messages)


def test_dod_evaluation_tables_warns_on_read_error_without_crashing(tmp_path: Path):
    """DoD scan should continue and emit a warning when a markdown file cannot be read."""
    subspec = _make_subspec_dir(tmp_path)
    (subspec / "tasks.md").write_bytes(b"\xff\xfe\xfd")

    passed, messages = check_dod_evaluation_tables(str(tmp_path))
    assert passed
    assert any("WARN  5-overall-scope-release-gates/tasks.md read error:" in msg for msg in messages)


# ---------------------------------------------------------------------------
# Tests for check_design_completeness (R3.4)
# ---------------------------------------------------------------------------


def test_design_completeness_passes_with_all_fields(tmp_path: Path):
    """Design doc with all 8 required field headings should pass."""
    subspec = _make_subspec_dir(tmp_path)
    (subspec / "design.md").write_text(
        "# Design\n\n"
        "## Backward Compatibility\nNo breaking changes.\n\n"
        "## Configuration Directives\nNew directive added.\n\n"
        "## Metrics and Observability\nNew counter exposed.\n\n"
        "## Testing Strategy\nUnit and integration tests.\n\n"
        "## Rollout Plan\nFeature-flagged rollout.\n\n"
        "## Rollback Procedure\nDisable via directive.\n\n"
        "## Non-Goals / Out of Scope\nNot targeting 1.0.\n\n"
        "## Scope Anchors — 0.4.0 vs Long-Term\nLimited to current API.\n",
        encoding="utf-8",
    )

    passed, messages = check_design_completeness(str(tmp_path))
    assert passed
    assert any("PASS" in msg and "design.md" in msg for msg in messages)


def test_design_completeness_fails_with_missing_fields(tmp_path: Path):
    """Design doc missing most required headings should fail and list them."""
    subspec = _make_subspec_dir(tmp_path)
    (subspec / "design.md").write_text(
        "# Design\n\n"
        "## Testing Strategy\nSome tests.\n\n"
        "## Rollout Plan\nGradual rollout.\n",
        encoding="utf-8",
    )

    passed, messages = check_design_completeness(str(tmp_path))
    assert not passed
    fail_msgs = [m for m in messages if "FAIL" in m]
    assert len(fail_msgs) == 1
    fail_msg = fail_msgs[0]
    # The missing fields should be listed in the failure message.
    assert "Backward Compatibility" in fail_msg
    assert "Configuration Directives" in fail_msg
    assert "Metrics or Logs" in fail_msg
    assert "Rollback" in fail_msg
    assert "What Is Not Done" in fail_msg
    assert "0.4.0 vs Long-Term" in fail_msg


def test_design_completeness_handles_read_errors(tmp_path: Path):
    """Unreadable design files should produce a failure, not a crash."""
    subspec = _make_subspec_dir(tmp_path)
    (subspec / "design.md").write_bytes(b"\xff\xfe\xfd")

    passed, messages = check_design_completeness(str(tmp_path))
    assert not passed
    assert any("design.md read error:" in msg for msg in messages)


# ---------------------------------------------------------------------------
# Tests for structured DoD table parsing
# ---------------------------------------------------------------------------


def test_dod_evaluation_passes_with_valid_table(tmp_path: Path):
    """DoD table with all 6 checkpoints and concrete status should pass."""
    subspec = _make_subspec_dir(tmp_path)
    (subspec / "completion.md").write_text(
        "# Completion\n\n"
        "## DoD Evaluation\n\n"
        "| Checkpoint | Status | Evidence |\n"
        "|---|---|---|\n"
        "| functionally correct | ✅ | tests pass |\n"
        "| observable | ✅ | metrics exposed |\n"
        "| rollback-safe | ✅ | directive toggle |\n"
        "| documented | ✅ | guide updated |\n"
        "| auditable | ✅ | logs present |\n"
        "| compatible | ✅ | no breaking changes |\n",
        encoding="utf-8",
    )

    passed, messages = check_dod_evaluation_tables(str(tmp_path))
    assert passed
    assert any("PASS" in msg and "completion.md" in msg for msg in messages)


def test_dod_evaluation_fails_with_placeholder_status(tmp_path: Path):
    """DoD table rows with template placeholder '✅/❌' should be rejected."""
    subspec = _make_subspec_dir(tmp_path)
    (subspec / "completion.md").write_text(
        "# Completion\n\n"
        "## DoD Evaluation\n\n"
        "| Checkpoint | Status | Evidence |\n"
        "|---|---|---|\n"
        "| functionally correct | ✅/❌ | TBD |\n"
        "| observable | ✅/❌ | TBD |\n"
        "| rollback-safe | ✅/❌ | TBD |\n"
        "| documented | ✅/❌ | TBD |\n"
        "| auditable | ✅/❌ | TBD |\n"
        "| compatible | ✅/❌ | TBD |\n",
        encoding="utf-8",
    )

    passed, messages = check_dod_evaluation_tables(str(tmp_path))
    assert not passed
    assert any("placeholder status" in msg for msg in messages)


def test_dod_evaluation_fails_with_missing_checkpoints(tmp_path: Path):
    """DoD table with only 3 of 6 checkpoints should fail listing the missing ones."""
    subspec = _make_subspec_dir(tmp_path)
    (subspec / "completion.md").write_text(
        "# Completion\n\n"
        "## DoD Evaluation\n\n"
        "| Checkpoint | Status | Evidence |\n"
        "|---|---|---|\n"
        "| functionally correct | ✅ | tests pass |\n"
        "| observable | ✅ | metrics exposed |\n"
        "| documented | ✅ | guide updated |\n",
        encoding="utf-8",
    )

    passed, messages = check_dod_evaluation_tables(str(tmp_path))
    assert not passed
    fail_msgs = [m for m in messages if "missing checkpoints" in m]
    assert len(fail_msgs) == 1
    fail_msg = fail_msgs[0]
    assert "rollback-safe" in fail_msg
    assert "auditable" in fail_msg
    assert "compatible" in fail_msg


def test_dod_evaluation_skips_fenced_code_blocks(tmp_path: Path):
    """DoD heading inside a fenced code block should be ignored."""
    subspec = _make_subspec_dir(tmp_path)
    (subspec / "notes.md").write_text(
        "# Notes\n\n"
        "Here is an example of a DoD table in documentation:\n\n"
        "```markdown\n"
        "## DoD Evaluation\n\n"
        "| Checkpoint | Status | Evidence |\n"
        "|---|---|---|\n"
        "| functionally correct | ✅ | tests pass |\n"
        "| observable | ✅ | metrics exposed |\n"
        "| rollback-safe | ✅ | directive toggle |\n"
        "| documented | ✅ | guide updated |\n"
        "| auditable | ✅ | logs present |\n"
        "| compatible | ✅ | no breaking changes |\n"
        "```\n\n"
        "The above is just an example.\n",
        encoding="utf-8",
    )

    passed, messages = check_dod_evaluation_tables(str(tmp_path))
    # Should NOT detect the fenced DoD table — vacuous pass with no PASS messages.
    assert passed
    assert not any("PASS" in msg and "notes.md" in msg for msg in messages)
