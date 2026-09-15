"""Tests for the push profile's reporting contract."""

from __future__ import annotations

from pathlib import Path

# Run with PYTHONPATH=tools/ci, the way the aggregate entry runs the other
# tools/ci tests, so the module is imported normally.
import pre_push_profile as profile

REPO_ROOT = Path(__file__).resolve().parents[2]
assert REPO_ROOT.name


def test_a_gate_that_did_not_run_is_not_a_pass() -> None:
    """NOT_RUN is reported as such and never counted towards a pass."""
    gate = profile.Gate("real GCC C unit suite", ["true"], needs_c_change=True)

    outcomes = profile._select([gate], changed=["docs/readme.md"])

    assert outcomes[0].status == "NOT_RUN"


def test_a_failing_selected_gate_fails_the_profile() -> None:
    """A selected gate that fails makes the profile fail."""
    gate = profile.Gate("local gate set (test-all)", ["false"])

    outcomes = profile._select([gate], changed=[])

    assert outcomes[0].status == "FAIL"
    assert profile._report(outcomes, "test-base") == 1


def test_an_unresolvable_base_cannot_pass() -> None:
    """An unknown change set means the profile cannot be completed."""
    assert profile.main(["prog", "--base", "refs/heads/does-not-exist"]) == 2


def test_the_profile_lists_its_gates() -> None:
    """The gate list is available without running anything."""
    assert profile.main(["prog", "--list"]) == 0


def test_c_changes_select_the_gcc_gate() -> None:
    """A C change in the module selects the real-GCC gate."""
    gate = profile.Gate("real GCC C unit suite", ["true"], needs_c_change=True)

    outcomes = profile._select(
        [gate], changed=["components/nginx-module/src/ngx_http_markdown_foo.c"]
    )

    assert outcomes[0].status == "PASS"


def test_shared_declaration_is_strict() -> None:
    """Invalid data cannot become a different command or selection condition."""
    import pytest
    from pre_push_gates import validate_gates

    valid = {"name": "gate", "command": ["true"], "needs_c_change": False, "requires_nginx": False}
    assert validate_gates([valid]) == [valid]
    for invalid in ([], [{**valid, "command": "make"}],
                    [{**valid, "needs_c_change": "false"}], [valid, valid],
                    [{**valid, "command": []}], [{**valid, "requires_nginx": None}]):
        with pytest.raises(ValueError):
            validate_gates(invalid)


def test_main_executes_shared_gate_and_propagates_failure(monkeypatch, capsys):
    """A real subprocess proves the runner consumes the declaration."""
    import sys
    command = [sys.executable, "-c", "import sys; print('gate executed'); sys.exit(7)"]
    monkeypatch.setattr(profile, "GATES", [{
        "name": "probe", "command": command,
        "needs_c_change": False, "requires_nginx": False,
    }])
    monkeypatch.setattr(profile, "_merge_base", lambda base: "base")
    monkeypatch.setattr(profile, "_changed_files", lambda base: [])
    assert profile.main(["prog"]) == 1
    assert "gate executed" in capsys.readouterr().out


def test_invalid_declaration_reports_incomplete(monkeypatch):
    monkeypatch.setattr(profile, "GATES", [])
    assert profile.main(["prog"]) == 2
