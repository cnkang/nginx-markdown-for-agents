"""Regression tests for executable PATH trust-root resolution."""

from pathlib import Path

from tools.lib import executable_validation as module


def test_trusted_roots_keep_configured_and_resolved_spellings() -> None:
    roots = module._trusted_roots()

    for configured_root in module._APPROVED_EXECUTABLE_DIRS:
        assert configured_root in roots
        assert configured_root.resolve() in roots


def test_literal_bin_entry_is_trusted_when_bin_is_a_symlink() -> None:
    roots = module._trusted_roots()
    literal_bin = Path("/bin") / "git"

    assert module._is_under(literal_bin, roots)
