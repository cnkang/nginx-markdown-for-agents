"""Fixture tests for detect_forward_decl_order.py.

Pins single-line and multi-line prototype parsing, typedef-before and
typedef-after ordering, external-header (unknown) types, and strict-mode
gating.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "detect_forward_decl_order",
        REPO_ROOT / "tools/harness/detect_forward_decl_order.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["detect_forward_decl_order"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def det():
    return _load_module()


def _write_header(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_single_line_prototype_after_typedef_no_warning(det, tmp_path):
    src = """
    typedef struct foo_s foo_t;
    void do_thing(foo_t x);
    """
    h = _write_header(tmp_path, "ok.h", src)
    warnings = det.check_file(h)
    assert warnings == []


def test_single_line_prototype_before_typedef_is_violation(det, tmp_path):
    src = """
    void do_thing(foo_t x);
    typedef struct foo_s foo_t;
    """
    h = _write_header(tmp_path, "bad.h", src)
    warnings = det.check_file(h)
    assert len(warnings) == 1
    assert "do_thing" in warnings[0]
    assert "foo_t" in warnings[0]
    assert "Rule 24" in warnings[0]


def test_multiline_prototype_before_typedef_is_violation(det, tmp_path):
    # Multi-line prototype that references a typedef defined later.
    src = """
    ngx_int_t do_complex(
        foo_t *ctx,
        ngx_str_t *name,
        size_t len
    );
    typedef struct foo_s foo_t;
    """
    h = _write_header(tmp_path, "multi.h", src)
    warnings = det.check_file(h)
    assert len(warnings) == 1
    assert "do_complex" in warnings[0]
    assert "foo_t" in warnings[0]


def test_multiline_prototype_after_typedef_no_warning(det, tmp_path):
    src = """
    typedef struct foo_s foo_t;
    ngx_int_t do_complex(
        foo_t *ctx,
        ngx_str_t *name,
        size_t len
    );
    """
    h = _write_header(tmp_path, "multi_ok.h", src)
    warnings = det.check_file(h)
    assert warnings == []


def test_external_header_type_not_flagged(det, tmp_path):
    # A type not defined anywhere in this file is assumed to come from
    # another header and is not flagged (avoid false positives).
    src = """
    void do_thing(external_thing_t x);
    """
    h = _write_header(tmp_path, "ext.h", src)
    warnings = det.check_file(h)
    assert warnings == []


def test_closing_brace_typedef_recognized(det, tmp_path):
    src = """
    void do_thing(bar_t x);
    typedef struct bar_s {
        int field;
    } bar_t;
    """
    h = _write_header(tmp_path, "brace.h", src)
    warnings = det.check_file(h)
    assert len(warnings) == 1
    assert "bar_t" in warnings[0]


def test_strict_mode_exits_nonzero(det, tmp_path, monkeypatch):
    src = """
    void do_thing(foo_t x);
    typedef struct foo_s foo_t;
    """
    _write_header(tmp_path, "bad.h", src)
    monkeypatch.setattr(
        sys, "argv",
        ["detect_forward_decl_order", str(tmp_path), "--strict"],
    )
    rc = det.main()
    assert rc == 1


def test_strict_mode_passes_when_clean(det, tmp_path, monkeypatch):
    src = """
    typedef struct foo_s foo_t;
    void do_thing(foo_t x);
    """
    _write_header(tmp_path, "ok.h", src)
    monkeypatch.setattr(
        sys, "argv",
        ["detect_forward_decl_order", str(tmp_path), "--strict"],
    )
    rc = det.main()
    assert rc == 0


def test_advisory_mode_returns_zero_even_with_violation(det, tmp_path, monkeypatch):
    src = """
    void do_thing(foo_t x);
    typedef struct foo_s foo_t;
    """
    _write_header(tmp_path, "bad.h", src)
    monkeypatch.setattr(
        sys, "argv",
        ["detect_forward_decl_order", str(tmp_path)],
    )
    rc = det.main()
    assert rc == 0