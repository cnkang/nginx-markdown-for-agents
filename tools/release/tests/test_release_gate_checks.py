"""Focused tests for release gate check edge cases."""

from pathlib import Path

from tools.release.release_gate_checks import (
    check_boundary_descriptions,
    check_checklist_verifiability,
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
