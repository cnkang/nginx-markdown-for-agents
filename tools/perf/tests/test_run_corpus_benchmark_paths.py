"""Path-validation regression tests for run_corpus_benchmark fixture discovery."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_corpus_benchmark as rcb  # noqa: E402
from run_corpus_benchmark import discover_fixtures, write_examples  # noqa: E402


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


def test_write_examples_uses_non_metadata_filenames(tmp_path, monkeypatch):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    meta_path = corpus_dir / "fixture.meta.json"
    html_path = corpus_dir / "fixture.html"
    meta_path.write_text(
        json.dumps({"fixture-id": "../../evil", "failure-corpus": False}),
        encoding="utf-8",
    )
    html_path.write_text("<html><body>ok</body></html>", encoding="utf-8")

    examples = [{"fixture-id": "../../evil", "page-type": "bad/type"}]
    fixtures_meta = [{
        "fixture-id": "../../evil",
        "failure-corpus": False,
        "_meta_path": str(meta_path),
    }]
    out_dir = tmp_path / "examples"

    monkeypatch.setattr(
        rcb, "run_converter", lambda _bin, _html: ("# converted\n", 0, 0.1),
    )
    write_examples(examples, fixtures_meta, "/bin/echo", out_dir)

    generated = sorted(p.name for p in out_dir.iterdir() if p.is_file())
    assert generated == ["example-001.html", "example-001.md"]
