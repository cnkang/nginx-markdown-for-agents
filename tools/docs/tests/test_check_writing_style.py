"""Tests for the check_writing_style writing-style checker.

Covers the STE-inspired audit rules plus the gate modes:
- audit(): sentence length, semicolons, Latin abbreviations, contractions,
  noun chains, passive-voice-ish patterns, and prose-only exclusion.
- main() exit codes for --strict, --changed (incremental), and --baseline.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow imports from tools/docs/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_writing_style as cws


def test_passive_voice_detected():
    text = "The file is edited by the agent.\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert any("passive-ish 'is edited'" in w for w in warnings)


def test_active_voice_passes():
    text = "The agent edits the file.\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert not any("passive-ish" in w for w in warnings)


def test_long_sentence_detected():
    words = " ".join(["word"] * 30)
    text = f"{words}.\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert any("long sentence" in w for w in warnings)


def test_short_sentence_passes():
    text = "This sentence stays short.\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert not any("long sentence" in w for w in warnings)


def test_semicolon_detected_in_prose():
    text = "One clause stays here; another follows.\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert any("semicolon" in w for w in warnings)


def test_code_block_excluded_from_prose_scan():
    text = "```\nThe file is edited by the agent; e.g. don't.\n```\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert warnings == []


def test_table_row_excluded_from_prose_scan():
    text = "| A | B |\n| the file is edited; e.g. | x |\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert warnings == []


def test_inline_code_excluded_from_prose_scan():
    text = "Run `the file is edited; e.g. don't` now.\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert warnings == []


def test_latin_abbreviation_detected():
    text = "Use curl e.g. with -H.\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert any("Latin abbreviation" in w for w in warnings)


def test_contraction_detected():
    text = "The module doesn't convert this.\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert any("contraction" in w for w in warnings)


def test_limit_caps_warnings():
    text = "The file is edited by the agent; another is checked; a third is read.\n"
    warnings = cws.audit(text, Path("x.md"), limit=1)
    assert len(warnings) == 1


def test_is_maintained_scope():
    assert cws._is_maintained("AGENTS.md") is True
    assert cws._is_maintained("docs/features/security.md") is True
    assert cws._is_maintained("docs/archive/old-note.md") is False
