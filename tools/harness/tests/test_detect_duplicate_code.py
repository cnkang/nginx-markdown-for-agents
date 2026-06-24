"""Fixture tests for detect_duplicate_code.py.

Pins the classification and verdict logic so that regressions in
adjacent-merge-residual detection, non-adjacent duplicate classification,
all_reviews inclusion in the verdict, strict-mode gating, and noise
reduction (narrowed MEMORY_KEYWORDS) are caught.
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
        "detect_duplicate_code",
        REPO_ROOT / "tools/harness/detect_duplicate_code.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["detect_duplicate_code"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def det():
    return _load_module()


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def test_adjacent_merge_residual_is_detected(det, tmp_path):
    # 3 identical lines immediately repeated → adjacent duplicate.
    # The adjacent detector requires 3+ consecutive identical lines
    # repeated back-to-back, so we use a 3-line block.
    src = """
    void f(void) {
        ngx_int_t rc;
        rc = do_thing(a, b, c);
        rc = do_thing(a, b, c);
        rc = do_thing(a, b, c);
        rc = do_thing(a, b, c);
        rc = do_thing(a, b, c);
        rc = do_thing(a, b, c);
    }
    """
    _write(tmp_path, "adj.c", src)
    warnings, infos = det.check_file(tmp_path / "adj.c")
    joined = "\n".join(warnings + infos)
    assert "adjacent duplicate block" in joined


def test_memory_duplicate_classified_direct_fix(det, tmp_path):
    # A non-adjacent duplicate containing ngx_palloc → memory/direct-fix.
    # Block exceeds SMALL_BLOCK_THRESHOLD so it is not downgraded.
    block = """\
    u_char *buf = ngx_palloc(r->pool, len);
    if (buf == NULL) {
        return NGX_ERROR;
    }
    ngx_memcpy(buf, src, len);
    out = buf;
    extra_line_to_exceed_threshold = 1;
    """
    src = f"""
    void f(void) {{
    {block}
        /* different middle code */
        x = 1;
        y = 2;
        z = 3;
        w = 4;
    {block}
        out[0] = 0;
    }}
    """
    _write(tmp_path, "mem.c", src)
    warnings, infos = det.check_file(tmp_path / "mem.c")
    joined = "\n".join(warnings + infos)
    assert "[memory]" in joined
    assert "direct-fix" in joined


def test_signature_duplicate_is_ignored(det, tmp_path):
    # Two function signatures with same params → signature/ignore
    sig = """\
    ngx_http_request_t *r,
    ngx_chain_t *in,
    ngx_int_t rc;
    """
    src = f"""
    static ngx_int_t foo({sig})
    static ngx_int_t bar({sig})
    """
    # The 5-line non-adjacent detector needs 5 matching lines; pad the sig.
    block = """\
    ngx_http_request_t *r,
    ngx_chain_t *in,
    ngx_int_t rc,
    void *ctx,
    ngx_buf_t *b;
    """
    src2 = f"""
    static void f({block})
    static void g({block})
    """
    _write(tmp_path, "sig.c", src2)
    warnings, infos = det.check_file(tmp_path / "sig.c")
    joined = "\n".join(warnings + infos)
    # Signature duplicates should be classified as ignore-by-rule and
    # NOT appear as direct-fix warnings.
    assert "[signature]" in joined
    assert "ignore-by-rule" in joined


def test_structural_duplicate_is_review(det, tmp_path):
    # Generic duplicate without risk keywords → structural/needs-human-review
    block = """\
    a = compute(x);
    b = compute(y);
    c = combine(a, b);
    d = finalize(c);
    e = publish(d);
    """
    src = f"""
    void f(void) {{
    {block}
        unrelated1();
        unrelated2();
        unrelated3();
        unrelated4();
        unrelated5();
    {block}
        cleanup();
    }}
    """
    _write(tmp_path, "struct.c", src)
    warnings, infos = det.check_file(tmp_path / "struct.c")
    joined = "\n".join(warnings + infos)
    assert "[structural]" in joined
    assert "needs-human-review" in joined


def test_log_only_duplicate_is_ignored(det, tmp_path):
    # 5-line log block repeated non-adjacently → log-only/ignore.
    # Avoid state-machine keywords (state, event, switch, case) so the
    # classifier falls through to the log-only bucket.
    block = """\
    ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
        "failed: %d", rc);
    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, log, 0,
        "ctx value %d", value);
    category = 1;
    return NGX_ERROR;
    """
    src = f"""
    void f(void) {{
    {block}
        middle1();
        middle2();
        middle3();
        middle4();
        middle5();
    {block}
        done();
    }}
    """
    _write(tmp_path, "log.c", src)
    warnings, infos = det.check_file(tmp_path / "log.c")
    joined = "\n".join(warnings + infos)
    assert "[log-only]" in joined
    assert "ignore-by-rule" in joined


def test_narrow_memory_keywords_reduce_noise(det, tmp_path):
    # A block that mentions "buffer" / "alloc" as a bare word but has
    # no NGINX allocator call should NOT be classified as memory.
    block = """\
    total += buffer_offset;
    alloc_count += 1;
    result = total + alloc_count;
    final = result * 2;
    done = final + 1;
    """
    src = f"""
    void f(void) {{
    {block}
        m1();
        m2();
        m3();
        m4();
        m5();
    {block}
        end();
    }}
    """
    _write(tmp_path, "noise.c", src)
    warnings, infos = det.check_file(tmp_path / "noise.c")
    joined = "\n".join(warnings + infos)
    # Should NOT be classified as memory now that bare "buffer"/"alloc"
    # keywords are removed.
    assert "[memory]" not in joined


def test_strict_mode_exits_nonzero_on_direct_fix(det, tmp_path, monkeypatch):
    # Block must exceed SMALL_BLOCK_THRESHOLD to get direct-fix action.
    block = """\
    u_char *buf = ngx_palloc(r->pool, len);
    if (buf == NULL) {
        return NGX_ERROR;
    }
    ngx_memcpy(buf, src, len);
    out = buf;
    extra_line_to_exceed_threshold = 1;
    """
    src = f"""
    void f(void) {{
    {block}
        a();
        b();
        c();
        d();
        e();
    {block}
        z();
    }}
    """
    _write(tmp_path, "mem.c", src)
    monkeypatch.setattr(
        sys, "argv",
        ["detect_duplicate_code", str(tmp_path), "--strict"],
    )
    rc = det.main()
    assert rc == 1


def test_strict_mode_passes_on_review_only(det, tmp_path, monkeypatch):
    # structural duplicates are needs-human-review, not direct-fix, so
    # strict mode should still exit 0.
    block = """\
    a = compute(x);
    b = compute(y);
    c = combine(a, b);
    d = finalize(c);
    e = publish(d);
    """
    src = f"""
    void f(void) {{
    {block}
        m1();
        m2();
        m3();
        m4();
        m5();
    {block}
        cleanup();
    }}
    """
    _write(tmp_path, "struct.c", src)
    monkeypatch.setattr(
        sys, "argv",
        ["detect_duplicate_code", str(tmp_path), "--strict"],
    )
    rc = det.main()
    assert rc == 0


def test_verdict_includes_reviews(det, tmp_path, monkeypatch):
    # A file with only structural REVIEW findings should NOT print
    # "PASS: no duplicate code blocks found" — it must say "PASS with
    # reviews".
    block = """\
    a = compute(x);
    b = compute(y);
    c = combine(a, b);
    d = finalize(c);
    e = publish(d);
    """
    src = f"""
    void f(void) {{
    {block}
        m1();
        m2();
        m3();
        m4();
        m5();
    {block}
        cleanup();
    }}
    """
    _write(tmp_path, "struct.c", src)
    import io
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    monkeypatch.setattr(
        sys, "argv",
        ["detect_duplicate_code", str(tmp_path)],
    )
    rc = det.main()
    assert rc == 0
    out = buf.getvalue()
    assert "PASS with reviews" in out
    assert "no duplicate code blocks found" not in out