"""Pytest fixtures for detect_test_assertion_coverage.py (Rules 14, 16).

Positive fixture: a ``#[test]`` function with no assertion — the run must
report ``Test '<name>' (line N) has no assertions``.

Negative fixtures: an ``assert_eq!`` test, a ``#[should_panic]`` test with no
assertions, a ``for_all`` property-style test, and a test whose only
verification is a recognized ``check_*`` helper call must all pass.

Fail-closed fixture: an unreadable (non-UTF-8) test file must surface as a
harness read error and fail the run instead of reporting a clean scan.

The detector's ``#[should_panic]`` exclusion reads the whole contiguous
attribute block of a test, so the attribute may precede or follow
``#[test]``; fixtures pin both orders.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DETECTOR = (
    Path(__file__).resolve().parent.parent / "detect_test_assertion_coverage.py"
)

ASSERTIONLESS_SOURCE = """\
#[test]
fn empty_check() {
    let _unused = 1;
}
"""

VERIFIED_SOURCE = """\
#[test]
fn computes_value() {
    let value = 3;
    assert_eq!(value, 3);
}

#[should_panic]
#[test]
fn rejects_bad_input() {
    let mut items: Vec<u32> = Vec::new();
    items.remove(0);
}

#[test]
fn roundtrip_for_all() {
    for_all(1u32..100u32, |value| {
        let _widened = u64::from(value);
    });
}
"""

HELPER_SOURCE = """\
#[test]
fn bounds_helper() {
    let x = 4;
    check_bounds(x);
}
"""

SHOULD_PANIC_AFTER_SOURCE = """\
#[test]
#[should_panic]
fn rejects_bad_input_after() {
    let mut items: Vec<u32> = Vec::new();
    items.remove(0);
}
"""


def _run(test_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(DETECTOR), str(test_dir)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(test_dir: Path, name: str, content: str) -> Path:
    path = test_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_assertionless_test_is_reported(tmp_path: Path) -> None:
    test_dir = tmp_path / "tests"
    _write(test_dir, "empty.rs", ASSERTIONLESS_SOURCE)

    result = _run(test_dir)

    assert result.returncode == 1
    assert "Found 1 test assertion coverage issue(s):" in result.stdout
    assert "Test 'empty_check'" in result.stdout
    assert "has no assertions" in result.stdout
    assert "OK:" not in result.stdout


def test_verified_tests_produce_clean_marker(tmp_path: Path) -> None:
    """assert_eq!, #[should_panic], and for_all tests all count as verified."""
    test_dir = tmp_path / "tests"
    _write(test_dir, "verified.rs", VERIFIED_SOURCE)

    result = _run(test_dir)

    assert result.returncode == 0
    assert "OK: All tests in" in result.stdout
    assert "have proper assertions" in result.stdout


def test_helper_name_call_is_accepted(tmp_path: Path) -> None:
    """``check_*`` helper calls count as verification."""
    test_dir = tmp_path / "tests"
    _write(test_dir, "helper.rs", HELPER_SOURCE)

    result = _run(test_dir)

    assert result.returncode == 0
    assert "OK: All tests in" in result.stdout


def test_unreadable_test_file_fails_closed(tmp_path: Path) -> None:
    """A non-UTF-8 test file must fail the run, not vanish from the scan."""
    test_dir = tmp_path / "tests"
    test_dir.mkdir(parents=True)
    (test_dir / "bad.rs").write_bytes(b"\xff\xfe")

    result = _run(test_dir)

    assert result.returncode == 1
    assert "Harness could not read Rust test file" in result.stdout
    assert "OK:" not in result.stdout


def test_should_panic_excluded_regardless_of_attribute_order(tmp_path: Path) -> None:
    """``#[should_panic]`` after ``#[test]`` still exempts the test.

    The whole contiguous attribute block counts, not a fixed window before
    ``#[test]``: Rust allows either order and the repository uses both.
    """
    test_dir = tmp_path / "tests"
    _write(test_dir, "after.rs", SHOULD_PANIC_AFTER_SOURCE)

    result = _run(test_dir)

    assert result.returncode == 0
    assert "OK: All tests in" in result.stdout
    assert "rejects_bad_input_after" not in result.stdout
