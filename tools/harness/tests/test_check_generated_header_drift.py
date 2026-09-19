"""Tests for the regenerating generated-header drift check."""

import subprocess
from pathlib import Path

import pytest

from tools.harness import check_generated_header_drift as drift

GENERATED = (
    "/* begin */\n"
    "} MarkdownOptions;\n"
    "\n"
    "\n"
    "\n"
    "/** FFI surface */\n"
    "ffi_fn();\n"
)
NORMALIZED = "/* begin */\n} MarkdownOptions;\n\n/** FFI surface */\nffi_fn();\n"


def test_a_stale_committed_pair_is_reported() -> None:
    """Two identical-but-stale copies must not pass (the old false negative)."""
    stale = "/* begin */\n} MarkdownOptions;\n/* old */\nffi_fn();\n"

    errors = drift.collect_drift_errors(GENERATED, stale, stale, stale, stale)

    assert errors
    assert any("working tree copy" in error for error in errors)
    assert any(drift.RUST_HEADER_REL in error for error in errors)


def test_mismatched_copies_are_reported() -> None:
    """The two committed copies must agree with each other."""
    errors = drift.collect_drift_errors(
        GENERATED, NORMALIZED, NORMALIZED + "x", NORMALIZED, NORMALIZED
    )

    assert any("differ from each other" in error for error in errors)


def test_matching_copies_pass() -> None:
    """A fresh generation matching both copies and HEAD reports nothing."""
    errors = drift.collect_drift_errors(
        GENERATED, NORMALIZED, NORMALIZED, NORMALIZED, NORMALIZED
    )

    assert errors == []


def test_head_copy_drift_is_reported() -> None:
    """A committed copy that trails the generator is named explicitly."""
    errors = drift.collect_drift_errors(
        GENERATED, NORMALIZED, NORMALIZED, "stale-head", NORMALIZED
    )

    assert any(drift.RUST_HEADER_REL in error for error in errors)
    assert all(drift.NGINX_HEADER_REL not in error for error in errors)


def test_an_unrecognized_generated_header_fails_closed() -> None:
    """Generated text without the cbindgen boundary cannot be compared."""
    with pytest.raises(RuntimeError, match="boundary"):
        drift.collect_drift_errors("no boundary here", "a", "a", None, None)


def test_the_cbindgen_pin_is_enforced(monkeypatch) -> None:
    """Only the pinned cbindgen version may certify the comparison."""
    monkeypatch.setattr(drift.shutil, "which", lambda _name: "/fake/cbindgen")

    class Result:
        returncode = 0
        stdout = "cbindgen 0.30.0\n"

    monkeypatch.setattr(drift.subprocess, "run", lambda *a, **k: Result())
    assert drift._cbindgen_path() is None

    Result.stdout = "cbindgen 0.29.4\n"
    assert drift._cbindgen_path() == "/fake/cbindgen"


def test_the_generator_output_is_normalized(monkeypatch, tmp_path) -> None:
    """Generation output is returned raw and normalized by the comparison."""
    monkeypatch.setattr(drift, "TARGET_DIR", tmp_path / "target")

    def fake_run(args, **kwargs):
        output = Path(args[args.index("--output") + 1])
        output.write_text(GENERATED, encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(drift.subprocess, "run", fake_run)
    generated = drift.generate_header("/fake/cbindgen")

    assert generated == GENERATED


def test_a_failed_generator_fails_closed(monkeypatch, tmp_path) -> None:
    """A non-zero cbindgen run cannot certify anything."""
    monkeypatch.setattr(drift, "TARGET_DIR", tmp_path / "target")

    class Result:
        returncode = 2
        stdout = "boom"
        stderr = ""

    monkeypatch.setattr(drift.subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(RuntimeError, match="cbindgen failed"):
        drift.generate_header("/fake/cbindgen")


def test_generation_preserves_pre_existing_workspace_content(
    monkeypatch, tmp_path
) -> None:
    """A run only removes the directory it created.

    The old implementation deleted the fixed ``target/header-drift-check``
    directory even when it predated the run (or when generation failed), so
    untracked content under it could vanish.
    """
    rust_dir = tmp_path / "rust"
    target = rust_dir / "target"
    pre_existing = target / "header-drift-check"
    pre_existing.mkdir(parents=True)
    sentinel = pre_existing / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    def fake_run(args, **kwargs):
        output = Path(args[args.index("--output") + 1])
        output.write_text("/* header */\n", encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(drift, "RUST_DIR", rust_dir)
    monkeypatch.setattr(drift, "TARGET_DIR", target)
    monkeypatch.setattr(drift.subprocess, "run", fake_run)

    text = drift.generate_header("cbindgen")

    assert text == "/* header */\n"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert sorted(item.name for item in target.iterdir()) == [
        "header-drift-check"
    ]
