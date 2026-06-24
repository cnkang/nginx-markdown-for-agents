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