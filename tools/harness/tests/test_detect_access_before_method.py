"""Pytest tests for detect_access_before_method.py.

Validates the access-control-before-method ordering audit:
- handler with access check before 405 rejection passes
- handler with 405 rejection before access control is a violation
- handler with no 405 path is exempt
- non-handler functions are skipped
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect_access_before_method as module


def _audit_text(content: str, tmp_path: Path):
    path = tmp_path / "_fixture_access_before_method.c"
    path.write_text(content, encoding="utf-8")
    try:
        return module.audit_file(path)
    finally:
        path.unlink(missing_ok=True)


CLEAN_HANDLER = """\
ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    ngx_int_t rc;

    rc = ngx_http_markdown_diagnostics_check_access(r);
    if (rc != NGX_OK) {
        r->headers_out.status = (ngx_uint_t) rc;
        return rc;
    }

    if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD))) {
        r->headers_out.status = NGX_HTTP_NOT_ALLOWED;
        return ngx_http_markdown_diagnostics_method_not_allowed(r);
    }
    return NGX_OK;
}
"""

BAD_ORDER_HANDLER = """\
ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    ngx_int_t rc;

    if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD))) {
        r->headers_out.status = NGX_HTTP_NOT_ALLOWED;
        return ngx_http_markdown_diagnostics_method_not_allowed(r);
    }

    rc = ngx_http_markdown_diagnostics_check_access(r);
    if (rc != NGX_OK) {
        return rc;
    }
    return NGX_OK;
}
"""

CONDITIONAL_ACCESS_HANDLER = """\
ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    if (r->headers_in.authorization != NULL) {
        ngx_http_markdown_diagnostics_check_access(r);
    }
    if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD))) {
        return NGX_HTTP_NOT_ALLOWED;
    }
    return NGX_OK;
}
"""

NESTED_BRACELESS_ACCESS_HANDLER = """\
ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    if (enabled)
        if (ngx_http_markdown_diagnostics_check_access(r) != NGX_OK)
            return NGX_ERROR;
    if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD))) {
        return NGX_HTTP_NOT_ALLOWED;
    }
    return NGX_OK;
}
"""

SHORT_CIRCUITED_ACCESS_HANDLER = """\
ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    if (enabled
        && ngx_http_markdown_diagnostics_check_access(r) != NGX_OK) {
        return NGX_ERROR;
    }
    if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD))) {
        return NGX_HTTP_NOT_ALLOWED;
    }
    return NGX_OK;
}
"""

NESTED_PARENTHESIZED_ACCESS_HANDLER = """\
ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    if ((enabled))
        if (ngx_http_markdown_diagnostics_check_access(r) != NGX_OK)
            return NGX_ERROR;
    if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD))) {
        return NGX_HTTP_NOT_ALLOWED;
    }
    return NGX_OK;
}
"""

BRACELESS_ELSE_ACCESS_HANDLER = """\
ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    if (enabled)
        return NGX_OK;
    else
        ngx_http_markdown_diagnostics_check_access(r);
    if (!(r->method & NGX_HTTP_GET)) {
        return NGX_HTTP_NOT_ALLOWED;
    }
    return NGX_OK;
}
"""

BRACELESS_DO_ACCESS_HANDLER = """\
ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    do
        ngx_http_markdown_diagnostics_check_access(r);
    while (enabled);
    if (!(r->method & NGX_HTTP_GET)) {
        return NGX_HTTP_NOT_ALLOWED;
    }
    return NGX_OK;
}
"""

NO_REJECT_HANDLER = """\
ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD))) {
        return NGX_HTTP_NOT_IMPLEMENTED;
    }
    return NGX_OK;
}
"""

NON_HANDLER = """\
static ngx_int_t
ngx_http_markdown_diagnostics_parse(ngx_str_t *s, ngx_int_t *value)
{
    if (s->len == 0) {
        return NGX_ERROR;
    }
    return NGX_OK;
}
"""


INLINE_SIGNATURE_HANDLER = """\
static ngx_int_t ngx_http_markdown_inline_handler(ngx_http_request_t *r)
{
    ngx_int_t rc;

    rc = ngx_http_markdown_diagnostics_check_access(r);
    if (rc != NGX_OK) {
        return rc;
    }

    if (!(r->method & NGX_HTTP_GET)) {
        return NGX_HTTP_NOT_ALLOWED;
    }
    return NGX_OK;
}
"""


MULTILINE_SIGNATURE_HANDLER = """\
static ngx_int_t
ngx_http_markdown_multiline_handler(
    ngx_http_request_t *r,
    ngx_http_complex_value_t *value
)
{
    ngx_int_t rc;

    rc = ngx_http_markdown_diagnostics_check_access(r);
    if (rc != NGX_OK) {
        return rc;
    }
    if (!(r->method & NGX_HTTP_GET)) {
        return NGX_HTTP_NOT_ALLOWED;
    }
    return value == NULL ? NGX_ERROR : NGX_OK;
}
"""


def test_clean_handler_passes(tmp_path) -> None:
    violations, reviews = _audit_text(CLEAN_HANDLER, tmp_path)
    assert violations == []
    assert reviews == []


def test_inline_signature_is_scanned(tmp_path) -> None:
    violations, reviews = _audit_text(INLINE_SIGNATURE_HANDLER, tmp_path)
    assert violations == []
    assert reviews == []


def test_multiline_signature_is_scanned(tmp_path) -> None:
    violations, reviews = _audit_text(MULTILINE_SIGNATURE_HANDLER, tmp_path)
    assert violations == []
    assert reviews == []


def test_metrics_access_helper_passes(tmp_path) -> None:
    content = CLEAN_HANDLER.replace(
        "ngx_http_markdown_diagnostics_check_access",
        "ngx_http_markdown_metrics_check_access",
    )
    violations, reviews = _audit_text(content, tmp_path)
    assert violations == []
    assert reviews == []


def test_bad_order_is_violation(tmp_path) -> None:
    violations, _ = _audit_text(BAD_ORDER_HANDLER, tmp_path)
    assert any("BEFORE access" in v for v in violations)


def test_conditional_access_before_rejection_is_violation(tmp_path) -> None:
    violations, _ = _audit_text(CONDITIONAL_ACCESS_HANDLER, tmp_path)
    assert any("unconditionally" in v for v in violations)


def test_nested_braceless_access_before_rejection_is_violation(
    tmp_path,
) -> None:
    violations, _ = _audit_text(NESTED_BRACELESS_ACCESS_HANDLER, tmp_path)
    assert any("unconditionally" in v for v in violations)


def test_short_circuited_access_before_rejection_is_violation(tmp_path) -> None:
    violations, _ = _audit_text(SHORT_CIRCUITED_ACCESS_HANDLER, tmp_path)
    assert any("unconditionally" in v for v in violations)


def test_nested_parenthesized_access_before_rejection_is_violation(
    tmp_path,
) -> None:
    violations, _ = _audit_text(NESTED_PARENTHESIZED_ACCESS_HANDLER, tmp_path)
    assert any("unconditionally" in v for v in violations)


def test_braceless_else_access_before_rejection_is_violation(tmp_path) -> None:
    violations, _ = _audit_text(BRACELESS_ELSE_ACCESS_HANDLER, tmp_path)
    assert any("unconditionally" in v for v in violations)


def test_braceless_do_access_before_rejection_is_violation(tmp_path) -> None:
    violations, _ = _audit_text(BRACELESS_DO_ACCESS_HANDLER, tmp_path)
    assert any("unconditionally" in v for v in violations)


def test_no_reject_handler_exempt(tmp_path) -> None:
    violations, reviews = _audit_text(NO_REJECT_HANDLER, tmp_path)
    assert violations == []
    assert reviews == []


def test_non_handler_skipped(tmp_path) -> None:
    violations, reviews = _audit_text(NON_HANDLER, tmp_path)
    assert violations == []
    assert reviews == []
