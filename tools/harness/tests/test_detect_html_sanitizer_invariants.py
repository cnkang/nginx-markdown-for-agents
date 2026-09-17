"""Pytest fixtures for detect_html_sanitizer_invariants.py (Rules 6, 27).

Positive fixture: a Rust file that references ``href`` with no
dangerous-scheme guard and no nesting depth limit — both invariants must be
reported.

Negative fixture: the same ``href`` reference plus an ``is_safe_url`` helper
and a ``MAX_DEPTH`` constant — the clean marker must appear.

Fail-closed fixtures: a non-UTF-8 ``.rs`` file and an existing but empty
directory must abort the scan instead of reporting the tree as verified.  A
missing directory is refused by the read-path guard before the scan begins
(the detector's own "Source directory not found" branch is reachable only
through its default path).

Fixtures live under ``tmp_path`` — never inside the repository — because the
detector concatenates every ``*.rs`` file below the directory it is given.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DETECTOR = (
    Path(__file__).resolve().parent.parent / "detect_html_sanitizer_invariants.py"
)

VIOLATING_SOURCE = """\
pub fn render_link(href: &str) -> String {
    format!("<a href=\\"{}\\">link</a>", href)
}
"""

CLEAN_SOURCE = """\
pub fn is_safe_url(link: &str) -> bool {
    !link.starts_with("javascript:")
}

const MAX_DEPTH: usize = 32;

pub fn render_link(href: &str) -> String {
    format!("<a href=\\"{}\\">link</a>", href)
}
"""


def _run(src_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DETECTOR), str(src_dir)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(src_dir: Path, name: str, content: str) -> Path:
    path = src_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_href_without_safety_or_depth_guards_is_reported(tmp_path: Path) -> None:
    """A bare ``href`` reference must trip both invariant checks."""
    src = tmp_path / "src"
    _write(src, "render.rs", VIOLATING_SOURCE)

    result = _run(src)

    assert result.returncode == 1
    assert "Found 2 HTML sanitizer invariant issue(s):" in result.stdout
    assert "no URL safety validation for dangerous schemes" in result.stdout
    assert "no nesting depth protection detected" in result.stdout


def test_safety_helper_and_depth_limit_produce_clean_marker(tmp_path: Path) -> None:
    """``is_safe_url`` + ``MAX_DEPTH`` must clear both invariants."""
    src = tmp_path / "src"
    _write(src, "render.rs", CLEAN_SOURCE)

    result = _run(src)

    assert result.returncode == 0
    assert "OK: HTML sanitizer invariants verified in" in result.stdout
    assert "issue(s)" not in result.stdout


def test_non_utf8_source_fails_closed(tmp_path: Path) -> None:
    """An unreadable ``.rs`` file must abort, not be skipped silently."""
    src = tmp_path / "src"
    src.mkdir(parents=True)
    (src / "bad.rs").write_bytes(b"\xff\xfe")

    result = _run(src)

    assert result.returncode == 1
    assert "ERROR: Unable to read Rust source file(s)" in result.stderr
    assert "OK: HTML sanitizer invariants verified" not in result.stdout


def test_empty_directory_fails_closed(tmp_path: Path) -> None:
    """An existing directory with no readable Rust must fail closed."""
    src = tmp_path / "src"
    src.mkdir(parents=True)

    result = _run(src)

    assert result.returncode == 1
    assert "ERROR: No readable Rust files found in" in result.stderr
    assert "OK:" not in result.stdout


def test_missing_directory_fails_closed(tmp_path: Path) -> None:
    """A missing directory must be refused without a clean-scan message."""
    missing = tmp_path / "absent"
    assert not missing.exists()

    result = _run(missing)

    assert result.returncode == 1
    assert "OK:" not in result.stdout
    assert (
        "does not exist" in result.stderr
        or "Source directory not found" in result.stderr
    )
