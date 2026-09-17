"""Pytest tests for detect_scratch_files.py (Rule 70).

Adversarial fixtures reproduce the 0.9.2 pre-freeze incident: five
CodeRabbit-digest helper scripts and a PR body draft entered functional
commits (0e32598a, 8df10b9c) and stayed tracked.

Byte-level fixtures cover the NUL-safe scan contract: git path output is
read as bytes because a tracked name is not guaranteed to be valid UTF-8.
"""

import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect_scratch_files as module


def _completed(stdout: bytes, stderr: bytes = b"", returncode: int = 0):
    """Build a CompletedProcess shaped like the byte-mode subprocess result."""
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _patch_git(monkeypatch, completed):
    """Answer every resolved-git invocation with one canned result."""
    monkeypatch.setattr(
        module.subprocess, "run", lambda *args, **kwargs: completed
    )


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
    assert module.classify("tools/template_helpers.py") is None
    assert module.classify("tools/tempfile_helpers.py") is None
    assert module.classify("tools/parse_notes.txt") is None


def test_standalone_temp_names_are_still_violations():
    assert module.classify("tools/temp.py") is not None
    assert module.classify("tools/temp-output.json") is not None


def test_git_plumbing_failure_fails_closed(monkeypatch):
    """A git inventory failure must not be reported as a clean scan."""
    failed = subprocess.CompletedProcess(
        args=["git"], returncode=128, stdout=b"", stderr=b"repository unavailable"
    )
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: failed)

    assert module.tracked_files() is None
    assert module.staged_files() is None
    monkeypatch.setattr(sys, "argv", ["detect_scratch_files.py"])
    assert module.main() == 1


def test_non_utf8_tracked_name_does_not_raise(monkeypatch):
    """A non-UTF-8 tracked name must not raise UnicodeDecodeError."""
    raw_name = b"scratch-\xff\xfe.md"
    _patch_git(monkeypatch, _completed(raw_name + b"\0tools/ok.py\0"))

    tracked = module.tracked_files()

    assert tracked is not None, "a byte-clean inventory must still be returned"
    assert module.classify(tracked[0]) == "scratch-named file"
    assert "ok.py" in tracked[1]


def test_non_utf8_staged_name_does_not_raise(monkeypatch):
    """The staged inventory reads bytes too, so --staged survives such a name."""
    _patch_git(
        monkeypatch,
        _completed(b"pr_body-\xff\xfe.md\0"),
    )

    staged = module.staged_files()

    assert staged is not None
    assert module.classify(staged[0]) == "scratch-named file"


def test_byte_inventory_keeps_scan_scope_and_reports_findings(monkeypatch):
    """A byte inventory keeps every entry and still reports violations."""
    # A non-UTF-8 name that is not a violation must not turn into one.
    _patch_git(
        monkeypatch, _completed(b"build.sh\0tools/notes\xff_helper.py\0")
    )
    monkeypatch.setattr(sys, "argv", ["detect_scratch_files.py"])
    assert module.main() == 0

    _patch_git(monkeypatch, _completed(b"tools/notes\xff_helper.py\0parse_digest.py\0"))
    assert module.main() == 1, "the byte-decoded violation must still be found"


def test_byte_inventory_handles_non_utf8_stderr_on_failure(monkeypatch):
    """A failing git run with non-UTF-8 stderr reports instead of raising."""
    _patch_git(
        monkeypatch,
        _completed(b"", stderr=b"fatal: bad \xff\xfe object", returncode=128),
    )

    assert module.tracked_files() is None


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


def test_valid_entries_are_partitioned_from_malformed_ones():
    """Well-formed entries stay usable while every malformed one is reported."""
    accepted, errors = module.partition_allowlist(
        [
            "build.sh:ClusterFuzzLite requires build.sh at the repository root",
            "missing_justification.py:",
            "short_reason.py:tiny",
            "no_separator.py",
            ":justification without a path",
        ]
    )

    assert accepted == [
        "build.sh:ClusterFuzzLite requires build.sh at the repository root"
    ]
    assert len(errors) == 4
    assert all("malformed" in error for error in errors)


def test_malformed_allowlist_entry_fails_closed(monkeypatch):
    """A malformed entry must fail the run instead of silently disabling it."""
    _patch_git(monkeypatch, _completed(b"tools/only_tooling.py\0"))
    monkeypatch.setattr(sys, "argv", ["detect_scratch_files.py"])
    monkeypatch.setattr(
        module, "ALLOWLIST", ["mystery_file.py:tiny"]
    )

    assert module.main() == 1, (
        "a malformed allowlist entry must fail the check, not be skipped"
    )


def test_valid_entry_keeps_exemption_while_a_sibling_entry_is_malformed(
    monkeypatch,
):
    """A valid exemption still applies when another entry is malformed."""
    _patch_git(monkeypatch, _completed(b"build.sh\0tools/only_tooling.py\0"))
    monkeypatch.setattr(sys, "argv", ["detect_scratch_files.py"])
    monkeypatch.setattr(
        module,
        "ALLOWLIST",
        [
            "build.sh:ClusterFuzzLite requires build.sh at the repository root",
            "malformed_entry.py:",
        ],
    )

    assert module.is_allowlisted("build.sh") is True
    assert module.is_allowlisted("malformed_entry.py") is False
    assert module.main() == 1, "the malformed entry still fails the run"


def test_allowlist_partition_accepts_the_shipped_entries():
    """The shipped allowlist must stay well formed after the partition."""
    accepted, errors = module.partition_allowlist()

    assert errors == []
    assert module.is_allowlisted("build.sh", accepted) is True
