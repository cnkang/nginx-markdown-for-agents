from __future__ import annotations

import sys
from pathlib import Path

# Allow imports from tools/docs/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
