from __future__ import annotations

import sys
from pathlib import Path

# Allow imports from tools/docs/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_docs import is_maintained_markdown


def test_root_truth_surfaces_are_included():
    assert is_maintained_markdown("AGENTS.md") is True
    assert is_maintained_markdown("README.md") is True
    assert is_maintained_markdown("README_zh-CN.md") is True


def test_docs_tree_is_included_but_archive_is_not():
    assert is_maintained_markdown("docs/harness/README.md") is True
    assert is_maintained_markdown("docs/archive/old-note.md") is False


def test_local_scratch_markdown_is_excluded():
    assert is_maintained_markdown("review-findings.md") is False
