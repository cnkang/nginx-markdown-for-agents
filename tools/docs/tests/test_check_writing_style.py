"""Tests for the check_writing_style writing-style checker.

Covers the STE-inspired audit rules plus the gate modes:
- audit(): sentence length, semicolons, Latin abbreviations, contractions,
  noun chains, passive-voice-ish patterns, and prose-only exclusion.
- main() exit codes for --strict, --changed (incremental), and --baseline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


def test_descriptive_sentence_uses_descriptive_limit():
    text = "The module " + " ".join(["word"] * 21) + ".\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert not any("long instruction" in w for w in warnings)


def test_imperative_sentence_uses_instruction_limit():
    text = "Run " + " ".join(["word"] * 20) + ".\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert any("long instruction" in w for w in warnings)


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


def test_capitalized_contraction_detected():
    text = "Don't skip this validation.\n"
    warnings = cws.audit(text, Path("x.md"), None)
    assert any("contraction 'Don't'" in w for w in warnings)


def test_limit_caps_warnings():
    text = "The file is edited by the agent; another is checked; a third is read.\n"
    warnings = cws.audit(text, Path("x.md"), limit=1)
    assert len(warnings) == 1


def test_is_maintained_scope():
    assert cws._is_maintained("AGENTS.md") is True
    assert cws._is_maintained("CONTRIBUTING.md") is True
    assert cws._is_maintained("SECURITY.md") is True
    assert cws._is_maintained("CHANGELOG.md") is True
    assert cws._is_maintained("docs/features/security.md") is True
    assert cws._is_maintained("docs/archive/old-note.md") is False


def test_rule_checklist_item_long_length_exempt():
    # Rule-checklist item ("- **Title**: ...") length is structural.
    text = "- **Fat-pointer safety**: When transferring ownership of a Rust slice or Vec to C via Box::into_raw, the pointer must stay valid until the matching free helper runs in every failure path, and the caller must not retain the pointer after free.\n"
    warnings = cws.audit(text, Path("docs/harness/rules/x.md"), None)
    assert not any("long sentence" in w for w in warnings)


def test_rule_doc_plain_list_item_exempt():
    # Governance "- " list items in AGENTS.md / harness rule docs are atomic.
    text = "- Full-buffer and streaming gzip/deflate/Brotli preserve codec/member lifecycle, streaming state survives arbitrary chunks and backpressure resumes, and gzip member resets keep response-wide budgets.\n"
    warnings = cws.audit(text, Path("AGENTS.md"), None)
    assert not any("long sentence" in w for w in warnings)


def test_normal_doc_long_list_item_still_flagged():
    # The same long list item in a non-rule doc is still a violation.
    text = "- Full-buffer and streaming gzip/deflate/Brotli preserve codec/member lifecycle, streaming state survives arbitrary chunks and backpressure resumes, and gzip member resets keep response-wide budgets while the module tracks peak memory across the whole conversion path.\n"
    warnings = cws.audit(text, Path("docs/guides/x.md"), None)
    assert any("long sentence" in w for w in warnings)


def test_quoted_source_citation_exempt():
    # Explicitly labelled source citations stay verbatim and are not audited.
    text = '- **Source citation**: "0=pass, 1=fail_closed; 255=not set" and "Zero-copy was removed; always pool-copy".\n'
    warnings = cws.audit(text, Path("docs/project/x.md"), None)
    assert warnings == []


def test_unlabelled_quoted_prose_is_audited():
    text = 'The module says "The file is edited by the agent; e.g. it isn\'t safe."\n'
    warnings = cws.audit(text, Path("docs/project/x.md"), None)
    assert any("passive-ish" in w for w in warnings)
    assert any("Latin abbreviation" in w for w in warnings)
    assert any("contraction" in w for w in warnings)
    assert any("semicolon" in w for w in warnings)


def test_rust_doc_comment_fence_is_excluded():
    text = "/// ```\n/// The file is edited by the agent; e.g. don't.\n/// ```\n"
    warnings = cws.audit(text, Path("CONTRIBUTING.md"), None)
    assert warnings == []


def test_main_requires_explicit_base_for_changed(monkeypatch):
    monkeypatch.setattr(cws.sys, "argv", ["check_writing_style.py", "--changed"])
    with pytest.raises(SystemExit) as exc_info:
        cws.main()
    assert exc_info.value.code == 2


@pytest.mark.parametrize("base_args", [["--base", "HEAD"], ["--base=HEAD"]])
def test_main_accepts_both_base_forms(monkeypatch, base_args):
    seen = []
    monkeypatch.setattr(
        cws.sys,
        "argv",
        ["check_writing_style.py", "--changed", *base_args],
    )
    monkeypatch.setattr(cws, "_resolve_base", lambda ref: ref)
    monkeypatch.setattr(
        cws,
        "_changed_md_files",
        lambda base: seen.append(base) or [],
    )
    assert cws.main() == 0
    assert seen == ["HEAD"]


def test_main_rejects_unknown_option(monkeypatch):
    monkeypatch.setattr(
        cws.sys,
        "argv",
        ["check_writing_style.py", "--strcit"],
    )
    with pytest.raises(SystemExit) as exc_info:
        cws.main()
    assert exc_info.value.code == 2


def test_changed_mode_computes_base_warnings_once(monkeypatch):
    calls = []
    file_path = Path(__file__).resolve()
    monkeypatch.setattr(cws.sys, "argv", ["check_writing_style.py", "--changed", "--base", "HEAD"])
    monkeypatch.setattr(cws, "_resolve_base", lambda ref: ref)
    monkeypatch.setattr(cws, "_changed_md_files", lambda base: [file_path])
    monkeypatch.setattr(
        cws,
        "_base_warning_counts",
        lambda files, base: calls.append((files, base)) or {file_path: cws.Counter()},
    )
    monkeypatch.setattr(cws, "audit", lambda text, path, limit: [])
    assert cws.main() == 0
    assert len(calls) == 1


def test_makefile_passes_explicit_style_base():
    makefile = (cws.ROOT / "Makefile").read_text(encoding="utf-8")
    assert 'check_writing_style.py --changed --base "$(STYLE_BASE)"' in makefile


def test_reference_line_noun_chain_exempt():
    # Reference lines carry formal document titles.
    text = "- ADR-0019: 0.9.0 Production Readiness Release Gate Framework\n"
    warnings = cws.audit(text, Path("docs/architecture/x.md"), None)
    assert warnings == []


def test_allowlisted_formal_title_exempt():
    text = "- Xcode Command Line Tools (macOS) and the Rust toolchain are required.\n"
    warnings = cws.audit(text, Path("docs/guides/x.md"), None)
    assert not any("noun chain" in w for w in warnings)


def test_cross_line_allowlisted_noun_chain_exempt():
    # Cross-line merge of an allowlisted title with neighboring prose.
    prose = "Send an explicit Prometheus header. See\nPrometheus Metrics Guide for the catalog."
    warnings = cws.audit(prose, Path("docs/guides/x.md"), None)
    assert not any("noun chain" in w for w in warnings)
