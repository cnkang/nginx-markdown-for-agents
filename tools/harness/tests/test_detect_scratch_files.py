"""Pytest tests for detect_scratch_files.py (Rule 70).

Adversarial fixtures reproduce the 0.9.2 pre-freeze incident: five
CodeRabbit-digest helper scripts and a PR body draft entered functional
commits (0e32598a, 8df10b9c) and stayed tracked.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect_scratch_files as module


def test_digest_helper_scripts_are_violations():
    for name in (
        "parse_digest_simple.py",
        "parse_final.py",
        "parse_findings.py",
        "process_digest.py",
        "process_digest2.py",
    ):
        assert module.classify(name) is not None, name


def test_pr_body_draft_is_violation():
    assert module.classify("pr_body.md") is not None
    assert module.classify("pr_body_final.md") is not None


def test_root_level_script_is_violation():
    assert module.classify("analyze_something.py") is not None
    assert module.classify("quick_fix.sh") is not None


def test_editor_and_system_junk_are_violations():
    for name in (
        "foo.c.bak",
        "notes.md.orig",
        "patch.diff.rej",
        "main.c~",
        ".DS_Store",
        "Thumbs.db",
        "swap.swp",
    ):
        assert module.classify(name) is not None, name


def test_legitimate_test_files_pass():
    assert module.classify(
        "components/nginx-module/tests/unit/parse_timeout_test.c") is None
    assert module.classify(
        "components/nginx-module/tests/unit/parse_interrupt_test.c") is None
    assert module.classify("tools/harness/tests/test_detect_regex_safety.py") \
        is None


def test_legitimate_tooling_passes():
    assert module.classify("tools/docs/check_kb_contract.py") is None
    assert module.classify("tools/perf/run_module_benchmark.sh") is None
    assert module.classify("Makefile") is None
    assert module.classify("docs/guides/INSTALLATION.md") is None


def test_clusterfuzz_entrypoint_allowlisted():
    assert module.is_allowlisted("build.sh") is True
    assert module.classify("build.sh") == "one-off script at repository root"
    findings = []
    reason = module.classify("build.sh")
    if reason and module.is_allowlisted("build.sh"):
        pass
    else:
        findings.append("build.sh")
    assert findings == []


def test_allowlist_requires_justification():
    assert module.is_allowlisted("mystery_file.py") is False
    module.ALLOWLIST.append("mystery_file.py:")
    try:
        assert module.is_allowlisted("mystery_file.py") is False
    finally:
        module.ALLOWLIST.pop()
