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


def test_last_modified_time_strip_requires_full_mirror_cleanup():
    # Time strip + typed pointer NULL but NO list invalidation: a duplicate
    # Last-Modified list entry would survive.  Triple invariant demands all
    # three (or send_304 delegation).
    source = (
        "static void\n"
        "strip_validators(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    r->headers_out.last_modified = NULL;\n"
        "}\n"
    )
    hits = [f for f in findings_for(source) if f[3] == "P"]
    assert len(hits) == 1
    assert "full mirror cleanup" in hits[0][4]


def test_last_modified_time_triple_invariant_passes():
    source = (
        "static void\n"
        "strip_validators(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    r->headers_out.last_modified = NULL;\n"
        "    ngx_http_markdown_invalidate_headers(r,\n"
        "        ngx_http_markdown_hdr_last_modified,\n"
        "        sizeof(ngx_http_markdown_hdr_last_modified) - 1, 0, NULL);\n"
        "}\n"
    )
    assert findings_for(source) == []


def test_stop_after_first_invalidate_is_not_complete():
    # The historical HEAD defect shape: time mirror stripped, invalidate
    # present but with stop_after_first=1 — duplicate entries survive.
    # The old OR-semantics accepted this; the triple invariant must not.
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
    hits = [f for f in findings_for(source) if f[3] == "P"]
    assert len(hits) == 1
    assert "stop_after_first" in hits[0][4]


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


def test_top_level_comments_do_not_create_unparsed_candidates():
    rejected = []
    source = (
        "/* static void broken_fn(ngx_http_request_t *r, */\n"
        "static void clean_fn(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.status = NGX_HTTP_OK;\n"
        "}\n"
    )

    names = [
        name
        for name, _, _ in module.extract_functions_from_source(
            source, rejected=rejected
        )
    ]
    assert names == ["clean_fn"]
    assert rejected == []


def test_macro_invocations_do_not_create_unparsed_candidates():
    rejected = []
    source = (
        "#define FOR_EACH_DIRECTIVE(X) \\\n"
        "    X(NGX_HTTP_MARKDOWN_DIRECTIVE_FILTER) \\\n"
        "    X(NGX_HTTP_MARKDOWN_DIRECTIVE_LIMITS)\n"
        "static const ngx_command_t commands[] = {\n"
        "    ngx_string(\"markdown_filter\"),\n"
        "};\n"
    )

    names = list(
        module.extract_functions_from_source(source, rejected=rejected)
    )
    assert names == []
    assert rejected == []


def test_allowlist_requires_justification():
    entry = "fixture.c:build_response:P:fresh response build; core recomputes lowcase when NULL"
    assert module.is_allowlisted("fixture.c", "build_response", "P") is False
    module.ALLOWLIST.append(entry)
    try:
        assert module.is_allowlisted("fixture.c", "build_response", "P") is True
        assert module.is_allowlisted("fixture.c", "other_function", "P") is False
    finally:
        module.ALLOWLIST.pop()


# ── multiline function discovery (fail-green regression) ──────────

def test_multiline_signature_functions_are_scanned():
    # The previous single-line-only matcher silently skipped functions
    # whose signature spans lines — the exact shape of the production
    # helpers this detector polices.  A violation hidden in a multiline
    # function MUST be found.
    source = (
        "static ngx_int_t\n"
        "head_representation_headers(ngx_http_request_t *r,\n"
        "    ngx_http_markdown_ctx_t *ctx)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    return NGX_OK;\n"
        "}\n"
    )
    hits = [f for f in findings_for(source) if f[3] == "P"]
    assert len(hits) == 1, "multiline signature must be scanned"


def test_single_line_signature_brace_is_scanned():
    source = (
        "static ngx_int_t head_representation(ngx_http_request_t *r) {\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    return NGX_OK;\n"
        "}\n"
    )
    hits = [f for f in findings_for(source) if f[3] == "P"]
    assert len(hits) == 1, "same-line opening brace must be scanned"


def test_multiline_signature_clean_function_passes():
    source = (
        "static ngx_int_t\n"
        "clean_helper(ngx_http_request_t *r,\n"
        "    ngx_http_markdown_ctx_t *ctx)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    r->headers_out.last_modified = NULL;\n"
        "    ngx_http_markdown_invalidate_headers(r,\n"
        "        ngx_http_markdown_hdr_last_modified,\n"
        "        sizeof(ngx_http_markdown_hdr_last_modified) - 1, 0, NULL);\n"
        "    return NGX_OK;\n"
        "}\n"
    )
    assert findings_for(source) == []


def test_prototypes_and_calls_are_not_function_bodies():
    source = (
        "ngx_int_t forward_only(ngx_http_request_t *r);\n"
        "\n"
        "void caller(ngx_http_request_t *r)\n"
        "{\n"
        "    if (forward_only(r) == NGX_OK) {\n"
        "        return;\n"
        "    }\n"
        "}\n"
    )
    rejected = []
    names = [
        name
        for name, _, _ in module.extract_functions_from_source(
            source, rejected=rejected
        )
    ]
    assert "caller" in names
    assert "forward_only" not in names
    assert rejected == []


def test_scan_counters_report_seen_and_scanned(capsys):
    source = (
        "static void a(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.status = NGX_HTTP_OK;\n"
        "}\n"
    )
    list(module.extract_functions_from_source(source))
    # The counters are printed by main(); here we verify the extraction
    # yields exactly what was fed so seen == scanned by construction.
    assert len(list(module.extract_functions_from_source(source))) == 1


# ── invalidate-call matching (Rule 69 P precision) ─────────────────

def test_invalidate_requires_actual_last_modified_call():
    # A function that strips the time mirror and merely MENTIONS
    # hdr_last_modified (here: a comment-free declaration of the name in
    # an unrelated call) must not satisfy the triple invariant — only a
    # real invalidate_headers(..., hdr_last_modified, ...) call does.
    source = (
        "static ngx_int_t\n"
        "mention_only(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    r->headers_out.last_modified = NULL;\n"
        "    log_name(ngx_http_markdown_hdr_last_modified);\n"
        "    return NGX_OK;\n"
        "}\n"
    )
    hits = [f for f in findings_for(source) if f[3] == "P"]
    assert len(hits) >= 1, (
        "a mention without a real invalidate_headers call must flag"
    )


def test_real_invalidate_call_satisfies_invariant():
    source = (
        "static void\n"
        "clear_lm(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    r->headers_out.last_modified = NULL;\n"
        "    ngx_http_markdown_invalidate_headers(r,\n"
        "        ngx_http_markdown_hdr_last_modified,\n"
        "        sizeof(ngx_http_markdown_hdr_last_modified) - 1,\n"
        "        0, NULL);\n"
        "}\n"
    )
    hits = [f for f in findings_for(source) if f[3] == "P"]
    assert hits == [], "the canonical cleanup call must satisfy Rule 69 P"


def test_stop_after_first_still_flags():
    source = (
        "static void\n"
        "clear_lm_partial(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    r->headers_out.last_modified = NULL;\n"
        "    ngx_http_markdown_invalidate_headers(r,\n"
        "        ngx_http_markdown_hdr_last_modified,\n"
        "        sizeof(ngx_http_markdown_hdr_last_modified) - 1,\n"
        "        1, NULL);\n"
        "}\n"
    )
    hits = [f for f in findings_for(source) if f[3] == "P"]
    assert len(hits) == 1, "stop_after_first=1 leaves duplicates alive"


def test_unrelated_stop_after_first_does_not_poison_clean_invalidation():
    source = (
        "static void\n"
        "clear_lm_with_unrelated_option(ngx_http_request_t *r)\n"
        "{\n"
        "    r->headers_out.last_modified_time = (time_t) -1;\n"
        "    r->headers_out.last_modified = NULL;\n"
        "    unrelated_helper(stop_after_first=1);\n"
        "    ngx_http_markdown_invalidate_headers(r,\n"
        "        ngx_http_markdown_hdr_last_modified,\n"
        "        sizeof(ngx_http_markdown_hdr_last_modified) - 1,\n"
        "        0, NULL);\n"
        "}\n"
    )
    assert findings_for(source) == []


# ── strict / fail-on-unparsed (unparsed-candidate accounting) ──────

def test_unparsed_candidates_are_recorded():
    # A definition-shaped line whose scan window closes with neither a
    # brace nor a semicolon is unparseable; it must land in `rejected`
    # instead of vanishing.
    rejected = []
    source = (
        "static void ok_fn(ngx_http_request_t *r)\n"
        "{\n"
        "}\n"
        "static void broken_fn(ngx_http_request_t *r,\n"
    )
    list(module.extract_functions_from_source(source, rejected=rejected))
    assert [name for name, _line in rejected] == ["broken_fn"]


def test_strict_mode_fails_on_unparsed(tmp_path):
    target = tmp_path / "fixture.c"
    target.write_text(
        "static void broken_fn(ngx_http_request_t *r,\n",
        encoding="utf-8",
    )
    import contextlib
    import io
    import sys as _sys

    err = io.StringIO()
    old_argv = _sys.argv
    _sys.argv = [
        "detect_representation_metadata_clearing.py", str(tmp_path), "--strict"
    ]
    try:
        with contextlib.redirect_stderr(err):
            rc = module.main()
    finally:
        _sys.argv = old_argv
    assert rc == 2, "--strict must exit 2 when candidates cannot be parsed"
    assert "UNPARSED" in err.getvalue()
    assert "functions_seen=1" in err.getvalue()
    assert "functions_scanned=0" in err.getvalue()
    assert "functions_skipped=1" in err.getvalue()
