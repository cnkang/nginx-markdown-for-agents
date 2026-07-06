"""Regression tests for the v0.9.0 release-gate validator."""

from tools.release.gates import validate_release_gates_090 as validator


def test_no_stale_symbols_gate_passes_without_diagnostics(monkeypatch, tmp_path):
    """A clean stale-symbol scan should map to a passing gate result."""
    monkeypatch.setattr(
        validator,
        "run_stale_symbol_check",
        lambda repo: (0, "No stale 0.8 symbols found.", ""),
    )

    result = validator.check_no_stale_symbols(tmp_path)

    assert result == {
        "name": "no_stale_symbols",
        "status": "pass",
        "message": "",
    }


def test_no_stale_symbols_gate_reports_tail_of_stdout_and_stderr(
    monkeypatch, tmp_path
):
    """Failure diagnostics should include recent stdout and stderr lines."""
    stdout = "\n".join([f"finding-{i}" for i in range(1, 8)])
    stderr = "read-error"
    monkeypatch.setattr(
        validator,
        "run_stale_symbol_check",
        lambda repo: (1, stdout, stderr),
    )

    result = validator.check_no_stale_symbols(tmp_path)

    assert result["name"] == "no_stale_symbols"
    assert result["status"] == "fail"
    assert result["message"] == "\n".join(
        ["finding-4", "finding-5", "finding-6", "finding-7", "read-error"]
    )
