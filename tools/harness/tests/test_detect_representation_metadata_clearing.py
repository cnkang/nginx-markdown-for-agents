"""Pytest tests for detect_representation_metadata_clearing.py (Rule 69).

Adversarial fixtures reproduce the defect shapes from the historical fix
chain (aac2f341, ccc320c7, 8b3633c1, de6cec38):
- Trailer declaration invalidated without clearing the trailers list
- last_modified_time stripped without nulling the typed pointer
- content_type_lowcase cleared without content_type_hash=0
and the compliant forms (invalidate escape, send_304 delegation).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import detect_representation_metadata_clearing as module


def findings_for(source):
    results = []
    for name, start_line, body in module.extract_functions_from_source(source):
        module.check_body(Path("fixture.c"), name, start_line, body, results)
    return results


def test_trailer_declaration_without_list_clear_is_violation():
    source = (
        "static void\n"
        "commit_metadata(ngx_http_request_t *r)\n"
        "{\n"
        "    static u_char  hdr_trailer[] = \"Trailer\";\n"
        "    ngx_http_markdown_invalidate_headers(r,\n"
        "        hdr_trailer, sizeof(hdr_trailer) - 1, 0, NULL);\n"
        "}\n"
    )
    hits = [f for f in findings_for(source) if f[3] == "T"]
    assert len(hits) == 1
    assert "clear_trailers" in hits[0][4]


def test_trailer_declaration_with_list_clear_passes():
    source = (
        "static void\n"
        "commit_metadata(ngx_http_request_t *r)\n"
        "{\n"
        "    static u_char  hdr_trailer[] = \"Trailer\";\n"
        "    ngx_http_markdown_invalidate_headers(r,\n"
        "        hdr_trailer, sizeof(hdr_trailer) - 1, 0, NULL);\n"
        "    ngx_http_markdown_clear_trailers(r);\n"
        "}\n"
    )
    assert findings_for(source) == []


def test_last_modified_time_strip_without_pointer_is_violation():
    source = (
        "static void\n"
        "strip_validators(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "}\n"
    )
    hits = [f for f in findings_for(source) if f[3] == "P"]
    assert len(hits) == 1
    assert "last_modified" in hits[0][4]


def test_last_modified_time_strip_with_pointer_null_passes():
    source = (
        "static void\n"
        "strip_validators(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    r->headers_out.last_modified = NULL;\n"
        "}\n"
    )
    assert findings_for(source) == []


def test_last_modified_time_strip_with_invalidate_escape_passes():
    source = (
        "static ngx_int_t\n"
        "head_representation(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    ngx_http_markdown_invalidate_headers(r,\n"
        "        ngx_http_markdown_hdr_last_modified,\n"
        "        sizeof(ngx_http_markdown_hdr_last_modified) - 1, 1, NULL);\n"
        "    return NGX_OK;\n"
        "}\n"
    )
    assert findings_for(source) == []


def test_last_modified_time_strip_with_send_304_delegation_passes():
    source = (
        "static ngx_int_t\n"
        "conditional_shortcut(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    return ngx_http_markdown_send_304(r, NULL);\n"
        "}\n"
    )
    assert findings_for(source) == []


def test_content_type_lowcase_without_hash_is_violation():
    source = (
        "static void\n"
        "build_response(ngx_http_request_t *r)\n"
        "{\n"
        "    ngx_str_set(&r->headers_out.content_type, \"text/plain\");\n"
        "    r->headers_out.content_type_lowcase = NULL;\n"
        "}\n"
    )
    hits = [f for f in findings_for(source) if f[3] == "P"]
    assert len(hits) == 1
    assert "content_type_hash" in hits[0][4]


def test_content_type_pair_cleared_together_passes():
    source = (
        "static void\n"
        "build_response(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.content_type_lowcase = NULL;\n"
        "    r->headers_out.content_type_hash = 0;\n"
        "}\n"
    )
    assert findings_for(source) == []


def test_forward_path_non_literal_writes_are_not_flagged():
    source = (
        "static ngx_int_t\n"
        "forward_headers(ngx_http_request_t *r,\n"
        "    ngx_http_markdown_ctx_t *ctx)\n"
        "{\n"
        "    if (ctx->lifecycle.last_modified.has_last_modified_time) {\n"
        "        r->headers_out.last_modified_time =\n"
        "            ctx->lifecycle.last_modified.source_last_modified_time;\n"
        "    }\n"
        "    return NGX_OK;\n"
        "}\n"
    )
    assert findings_for(source) == []


def test_comments_do_not_trigger_findings():
    source = (
        "static void\n"
        "documented_only(ngx_http_request_t *r)\n"
        "{\n"
        "    /* hdr_trailer and last_modified are handled elsewhere */\n"
        "    r->headers_out.status = NGX_HTTP_OK;\n"
        "}\n"
    )
    assert findings_for(source) == []


def test_allowlist_requires_justification():
    entry = "fixture.c:build_response:P:fresh response build; core recomputes lowcase when NULL"
    assert module.is_allowlisted("fixture.c", "build_response", "P") is False
    module.ALLOWLIST.append(entry)
    try:
        assert module.is_allowlisted("fixture.c", "build_response", "P") is True
        assert module.is_allowlisted("fixture.c", "other_function", "P") is False
    finally:
        module.ALLOWLIST.pop()
