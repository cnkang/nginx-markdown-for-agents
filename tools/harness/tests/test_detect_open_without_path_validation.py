"""Fixture tests for detect_open_without_path_validation.py.

Pins the AST classification of ``open()`` path arguments so that
regressions in the BinOp safety logic, literal-var tracking, and
strict-mode gating are caught.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "detect_open_without_path_validation",
        REPO_ROOT / "tools/harness/detect_open_without_path_validation.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["detect_open_without_path_validation"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def det():
    return _load_module()


def _check_source(det, source: str, *, strict: bool = False):
    """Parse source, return (errors, warnings)."""
    tree = ast.parse(textwrap.dedent(source))
    validated = det._collect_validated_vars(tree)
    hardcoded = det._collect_hardcoded_vars(tree)
    return det._find_open_calls(
        tree, validated, hardcoded, rel="fixture.py", strict=strict,
    )


def test_repo_root_div_literal_is_safe(det):
    # REPO_ROOT / "constant" — both sides safe
    src = """
    REPO_ROOT = Path(__file__).resolve().parents[2]
    with open(REPO_ROOT / "constants.json") as f:
        pass
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert warnings == []


def test_tmp_path_div_literal_is_safe(det):
    # tmp_path / "output.json" — fixture / literal
    src = """
    def test_x(tmp_path):
        with open(tmp_path / "output.json") as f:
            pass
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert warnings == []


def test_tmp_path_div_user_input_is_unsafe(det):
    # tmp_path / user_input — right side is unvalidated function param
    src = """
    def test_x(tmp_path, user_input):
        with open(tmp_path / user_input) as f:
            pass
    """
    errors, warnings = _check_source(det, src)
    # Advisory: warning, not error
    assert errors == []
    assert len(warnings) == 1
    assert "tmp_path / user_input" in warnings[0]


def test_tmp_path_div_user_input_strict_is_error(det):
    src = """
    def test_x(tmp_path, user_input):
        with open(tmp_path / user_input) as f:
            pass
    """
    errors, warnings = _check_source(det, src, strict=True)
    assert len(errors) == 1
    assert "tmp_path / user_input" in errors[0]
    assert warnings == []


def test_repo_root_div_args_path_is_unsafe(det):
    # REPO_ROOT / args.path — args.path is an Attribute, not a Name,
    # and "path" is not a DERIVED_ATTR, so right side is unsafe.
    src = """
    REPO_ROOT = Path(__file__).resolve().parents[2]
    def main(args):
        with open(REPO_ROOT / args.path) as f:
            pass
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert len(warnings) == 1
    assert "args.path" in warnings[0]


def test_literal_string_var_is_safe(det):
    # output_path = "output.json" (literal) -> tracked as hardcoded
    src = """
    def test_x(tmp_path):
        output_path = "output.json"
        with open(tmp_path / output_path) as f:
            pass
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert warnings == []


def test_validated_var_is_safe(det):
    src = """
    from lib.path_validation import validate_read_path
    def f(untrusted):
        p = validate_read_path(untrusted)
        with open(p) as fh:
            pass
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert warnings == []


def test_unvalidated_param_is_unsafe(det):
    src = """
    def f(path):
        with open(path) as fh:
            pass
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert len(warnings) == 1
    assert "open(path)" in warnings[0]


def test_strict_mode_promotes_to_error(det):
    src = """
    def f(path):
        with open(path) as fh:
            pass
    """
    errors, warnings = _check_source(det, src, strict=True)
    assert len(errors) == 1
    assert warnings == []


def test_method_open_unvalidated_receiver_strict_fail(det):
    src = """
    def f(path):
        with path.open(encoding="utf-8") as fh:
            pass
    """
    errors, warnings = _check_source(det, src, strict=True)
    assert len(errors) == 1
    assert "path" in errors[0]
    assert warnings == []


def test_method_open_validated_receiver_pass(det):
    src = """
    from lib.path_validation import validate_read_path
    def f(untrusted):
        p = validate_read_path(untrusted)
        with p.open(encoding="utf-8") as fh:
            pass
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert warnings == []


def test_method_open_repo_root_div_literal_pass(det):
    src = """
    REPO_ROOT = Path(__file__).resolve().parents[2]
    with (REPO_ROOT / "x").open() as fh:
        pass
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert warnings == []


def test_method_open_tmp_path_div_user_input_still_fails(det):
    src = """
    def test_x(tmp_path, user_input):
        p = tmp_path / user_input
        with p.open(encoding="utf-8") as fh:
            pass
    """
    errors, warnings = _check_source(det, src, strict=True)
    assert len(errors) == 1
    assert "open(p)" in errors[0]
    assert warnings == []


def test_main_strict_flag_exits_nonzero(tmp_path, det, monkeypatch):
    # Drive main() with a directory containing an unsafe open() call.
    bad = tmp_path / "bad.py"
    bad.write_text(
        "def f(path):\n    with open(path) as fh:\n        pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["detect_open", "--path", str(tmp_path), "--strict"])
    rc = det.main()
    assert rc == 1


def test_main_advisory_returns_zero(tmp_path, det, monkeypatch):
    bad = tmp_path / "bad.py"
    bad.write_text(
        "def f(path):\n    with open(path) as fh:\n        pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["detect_open", "--path", str(tmp_path)])
    rc = det.main()
    assert rc == 0


def test_validate_write_path_within_root_is_safe(det):
    # validate_write_path_within_root() is a known validation helper
    src = """
    from lib.path_validation import validate_write_path_within_root
    def f(path):
        safe = validate_write_path_within_root(path, "/root")
        with open(safe) as fh:
            pass
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert warnings == []


def test_state_store_real_file_pass(det):
    """check_file() on the real state_store.py must show no open() violations.

    state_store.py uses ``validate_user_local_state_path()`` which is
    registered in FILE_SCOPED_VALIDATORS for this file only.  This is
    an end-to-end integration test — it verifies that the file-scoped
    validation mechanism works on the actual production code.
    """
    path = REPO_ROOT / "tools/harness/state_store.py"
    errors, warnings = det.check_file(path, strict=True)
    assert errors == []
    assert warnings == []


def test_state_validator_name_not_global(det, tmp_path):
    """A function named validate_user_local_state_path() in a non-state_store
    file must NOT be treated as a trusted validator.  FILE_SCOPED_VALIDATORS
    only exempts ``state_store.py``.
    """
    bad = tmp_path / "bad.py"
    bad.write_text(
        '''
def validate_user_local_state_path(path):
    return path

def f(path):
    safe = validate_user_local_state_path(path)
    with open(safe) as fh:
        pass
''',
        encoding="utf-8",
    )
    errors, warnings = det.check_file(bad, strict=True)
    assert len(errors) == 1


def test_write_text_validated_receiver_pass(det):
    """p.write_text() where p is validated — should be safe."""
    src = """
    from lib.path_validation import validate_write_path_within_root
    def f(path):
        safe = validate_write_path_within_root(path, "/root")
        safe.write_text("content")
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert warnings == []


def test_write_text_unvalidated_receiver_warns(det):
    """p.write_text() where p is unvalidated — advisory warning."""
    src = """
    def f(path):
        path.write_text("content")
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert len(warnings) == 1
    assert "write_text" in warnings[0]


def test_write_text_unvalidated_strict_still_warns(det):
    """Path IO methods are advisory-only even in strict mode."""
    src = """
    def f(path):
        path.write_text("content")
    """
    errors, warnings = _check_source(det, src, strict=True)
    assert errors == []
    assert len(warnings) == 1
    assert warnings[0].lstrip().startswith("WARNING")
    assert "write_text" in warnings[0]


def test_read_text_validated_receiver_pass(det):
    """p.read_text() where p is validated — should be safe."""
    src = """
    from lib.path_validation import validate_read_path
    def f(path):
        safe = validate_read_path(path)
        return safe.read_text()
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert warnings == []


def test_read_text_unvalidated_receiver_warns(det):
    """p.read_text() where p is unvalidated — advisory warning."""
    src = """
    def f(path):
        return path.read_text()
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert len(warnings) == 1
    assert "read_text" in warnings[0]


def test_write_bytes_unvalidated_receiver_warns(det):
    """p.write_bytes() where p is unvalidated — advisory warning."""
    src = """
    def f(path):
        path.write_bytes(b"data")
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert len(warnings) == 1
    assert "write_bytes" in warnings[0]


def test_read_bytes_unvalidated_receiver_warns(det):
    """p.read_bytes() where p is unvalidated — advisory warning."""
    src = """
    def f(path):
        return path.read_bytes()
    """
    errors, warnings = _check_source(det, src)
    assert errors == []
    assert len(warnings) == 1
    assert "read_bytes" in warnings[0]
