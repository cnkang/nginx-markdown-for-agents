"""Tests for the push profile's reporting contract."""

from __future__ import annotations

import json
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


def test_main_executes_shared_gate_and_propagates_failure(monkeypatch, capsys, tmp_path):
    """A real subprocess proves the runner consumes the JSON declaration."""
    import sys
    command = [sys.executable, "-c", "import sys; print('gate executed'); sys.exit(7)"]
    path = tmp_path / "tools/ci/pre_push_gates.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([{
        "name": "probe", "command": command,
        "needs_c_change": False, "requires_nginx": False,
    }]), encoding="utf-8")
    monkeypatch.setattr(profile, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(profile, "_merge_base", lambda base: "base")
    monkeypatch.setattr(profile, "_changed_files", lambda base: [])
    assert profile.main(["prog"]) == 1
    assert "gate executed" in capsys.readouterr().out


def test_invalid_declaration_reports_incomplete(monkeypatch, tmp_path):
    """Missing and malformed data never fall back to the repository defaults."""
    monkeypatch.setattr(profile, "REPO_ROOT", tmp_path)
    assert profile.main(["prog", "--list"]) == 2
    path = tmp_path / "tools/ci/pre_push_gates.json"
    path.parent.mkdir(parents=True)
    for data in ("[]", "not JSON", '[{"name":"one","name":"two"}]'):
        path.write_text(data, encoding="utf-8")
        assert profile.main(["prog", "--list"]) == 2


def test_consumers_share_json_and_do_not_cache(monkeypatch, tmp_path):
    """Both real consumers observe each edit to the same JSON file."""
    import shlex
    from tools.harness import check_harness_sync as sync

    path = tmp_path / "tools/ci/pre_push_gates.json"
    path.parent.mkdir(parents=True)
    monkeypatch.setattr(profile, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sync, "REPO_ROOT", tmp_path)
    for command in (["make", "checked"], ["make", "other"]):
        path.write_text(json.dumps([{
            "name": "probe", "command": command,
            "needs_c_change": False, "requires_nginx": False,
        }]), encoding="utf-8")
        assert profile._gates()[0].command == command
        assert sync._profile_gate_text() == shlex.join(command)


def test_duplicate_json_keys_and_python_are_refused(tmp_path):
    """Aliases and duplicate declarations cannot hide a different gate list."""
    import pytest
    from pre_push_gates import load_gates

    path = tmp_path / "gates.json"
    for data in ('[{"command":["true"],"command":["false"]}]',
                 'GATES = alias = []; alias.clear()', '[{"name":NaN}]'):
        path.write_text(data, encoding="utf-8")
        with pytest.raises(ValueError):
            load_gates(path)


def test_a_gate_that_cannot_start_fails_the_profile(monkeypatch, capsys, tmp_path) -> None:
    """An unlaunchable command is a failed gate, not a traceback."""

    monkeypatch.setattr(profile, "load_gates", lambda path: [{
        "name": "unlaunchable", "command": ["/nonexistent/gate-binary"],
        "needs_c_change": False, "requires_nginx": False,
    }])
    monkeypatch.setattr(profile, "_merge_base", lambda base: "base")
    monkeypatch.setattr(profile, "_changed_files", lambda base: [])

    # main() parses argv[1:], so the program name comes first.
    assert profile.main(["pre_push_profile", "--base", "base"]) == 1
    assert "FAIL" in capsys.readouterr().out
