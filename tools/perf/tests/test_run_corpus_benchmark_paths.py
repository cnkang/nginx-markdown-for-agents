"""Path-validation regression tests for run_corpus_benchmark fixture discovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_corpus_benchmark import discover_fixtures  # noqa: E402


def test_discover_fixtures_persists_validated_html_path(tmp_path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()

    html_path = corpus_dir / "sample.html"
    html_path.write_text("<html><body>ok</body></html>", encoding="utf-8")

    meta_path = corpus_dir / "sample.meta.json"
    meta_path.write_text(json.dumps({"fixture-id": "sample"}), encoding="utf-8")

    fixtures = discover_fixtures(corpus_dir)

    assert len(fixtures) == 1
    assert fixtures[0]["_html_path"] == str(html_path.resolve())
