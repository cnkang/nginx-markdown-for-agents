"""Regression tests for const-correctness detector token boundaries."""

from tools.harness import detect_const_correctness as detector


def test_retired_runtime_types_are_not_silently_exempted() -> None:
    """Removed subsystems no longer widen the detector's type surface."""
    assert "dynconf_snapshot_t" not in detector.NGINX_STRUCT_TYPES.pattern
    assert "otel_span_t" not in detector.NGINX_STRUCT_TYPES.pattern
    assert "ngx_http_markdown_dynconf_impl.h" not in detector.EXEMPT_FILES
    assert "ngx_http_markdown_otel.c" not in detector.EXEMPT_FILES
    assert "dynconf_snapshot_from_conf" not in detector.INTENTIONAL_MUTATOR_RE.pattern
    assert "dynconf_apply_snapshot" not in detector.INTENTIONAL_MUTATOR_RE.pattern
    assert "conf_t" in detector.NGINX_STRUCT_TYPES.pattern
    assert "metrics_t" in detector.NGINX_STRUCT_TYPES.pattern


def test_detector_checks_active_types_and_ignores_removed_types() -> None:
    """The detector's documented type inventory controls its warnings."""
    _, active_warnings = detector._check_line_for_const_violations(
        "static void inspect(ngx_http_markdown_ctx_t *ctx)", 1, "fixture.c", False
    )
    _, retired_warnings = detector._check_line_for_const_violations(
        "static void inspect(ngx_http_markdown_otel_span_t *ctx)",
        1,
        "fixture.c",
        False,
    )
    assert len(active_warnings) == 1, active_warnings
    assert retired_warnings == []


def test_loop_cursor_declarations_do_not_look_like_function_parameters() -> None:
    """A for-loop pointer declaration is not a function parameter."""
    errors, warnings = detector._check_line_for_const_violations(
        "for (ngx_http_markdown_ctx_t *ctx = start; ctx != NULL; ctx = ctx->next)",
        1,
        "fixture.c",
        False,
    )
    assert errors == []
    assert warnings == []


def test_final_substrings_in_parameter_names_do_not_hide_const_warnings() -> None:
    """A `final` parameter name is not proof that the function mutates state."""
    line = (
        "ngx_int_t ngx_http_markdown_inspect("
        "ngx_http_markdown_conf_t *conf, int final_result)"
    )

    _, warnings = detector._check_line_for_const_violations(
        line, 1, "fixture.c", False
    )

    assert len(warnings) == 1, warnings


def test_mutator_tokens_do_not_match_inside_identifiers() -> None:
    for identifier in (
        "offset", "appendix", "freeze", "final_result", "is_final",
        "final2", "nonfinal",
    ):
        assert detector.INTENTIONAL_MUTATOR_RE.search(identifier) is None


def test_actual_mutator_tokens_still_match() -> None:
    for identifier in (
        "set_value",
        "append_node",
        "mark_header_reject",
    ):
        assert detector.INTENTIONAL_MUTATOR_RE.search(identifier) is not None
    assert detector._is_intentional_mutator_function("sha256_final")
    assert detector._is_intentional_mutator_function(
        "ngx_http_markdown_sha256_final"
    )
