#ifndef NGX_HTTP_MARKDOWN_DECISION_LOG_IMPL_H
#define NGX_HTTP_MARKDOWN_DECISION_LOG_IMPL_H

/*
 * Decision log emission helper.
 *
 * WARNING: This header is an implementation detail of the main translation
 * unit (ngx_http_markdown_filter_module.c).  It must NOT be included from
 * any other .c file or used as a standalone compilation unit.
 *
 * Emits a structured "markdown:" log entry once per request,
 * gated by the configured markdown_log_verbosity level.
 *
 * Requirements: FR-03.1, FR-03.2, FR-03.3, FR-03.4, FR-03.5, FR-03.6
 */

#ifndef ngx_str_set
#define ngx_str_set(str, text)                                                    \
    do {                                                                          \
        (str)->len = sizeof(text) - 1;                                            \
        (str)->data = (u_char *) text;                                            \
    } while (0)
#endif

/* C99 declaration visibility for standalone static analysis of this impl header. */
ngx_int_t ngx_http_complex_value(ngx_http_request_t *r,
    ngx_http_complex_value_t *val, ngx_str_t *value);
#ifndef ngx_log_error
void ngx_log_error(ngx_uint_t level, ngx_log_t *log, ngx_err_t err,
    const char *fmt, ...);
#endif

/* Forward declarations — defined below */
static void ngx_http_markdown_log_decision(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code);
static void ngx_http_markdown_log_decision_with_category(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code,
    const ngx_str_t *error_category);

/*
 * Resolved request metadata for decision log emission.
 * Groups method name and content-type to keep log_decision_debug
 * parameter count within the 7-parameter limit (c:S107).
 */
typedef struct {
    ngx_str_t *method_name;
    ngx_str_t *content_type;
} ngx_http_markdown_decision_meta_t;

typedef struct {
    const u_char  *data;
    size_t         len;
} ngx_http_markdown_literal_t;

#define ngx_http_markdown_literal(text)                                          \
    { (const u_char *) text, sizeof(text) - 1 }

#define ngx_http_markdown_reason_has_prefix_literal(reason, text)                \
    ngx_http_markdown_reason_has_prefix(                                         \
        reason, (const u_char *) (text), sizeof(text) - 1)

static void ngx_http_markdown_log_decision_debug(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code,
    const ngx_str_t *error_category, ngx_uint_t log_level,
    ngx_http_markdown_decision_meta_t *meta);

static ngx_int_t
ngx_http_markdown_reason_has_prefix(const ngx_str_t *reason_code,
    const u_char *prefix, size_t prefix_len)
{
    return (reason_code->len >= prefix_len
            && ngx_strncmp(reason_code->data, prefix, prefix_len) == 0);
}

static ngx_int_t
ngx_http_markdown_reason_is_exact(const ngx_str_t *reason_code,
    const ngx_http_markdown_literal_t *codes, ngx_uint_t code_count)
{
    for (ngx_uint_t i = 0; i < code_count; i++) {
        if (reason_code->len == codes[i].len
            && ngx_strncmp(reason_code->data, codes[i].data,
                           codes[i].len) == 0)
        {
            return 1;
        }
    }

    return 0;
}


/*
 * Determine whether a reason code represents a failure outcome.
 *
 * Failure outcomes are reason codes matching any of:
 *   - "failed_open" or "failed_closed" (schema v1 fail-open/closed)
 *   - "conversion_error", "memory_budget_exceeded", "ffi_panic",
 *     "decompression_error", "decompression_budget_exceeded",
 *     "decompression_format_error", "decompression_truncated_input",
 *     "decompression_io_error", "timeout", "budget_exceeded",
 *     "replay_error", "overload", "invalid_dynconf",
 *     "degraded_snapshot", "header_plan_apply_error",
 *     "streaming_mid_flight_error" (schema v1 error codes)
 *   - Legacy: "ELIGIBLE_FAILED" prefix, "FAIL_" prefix
 *   - "STREAMING_FAIL_" substring (streaming post-commit failures)
 *   - "STREAMING_PRECOMMIT_" prefix (streaming pre-commit failures)
 *   - "STREAMING_BUDGET_" prefix (streaming budget exceeded)
 *   - "STREAMING_FALLBACK_" prefix (streaming degraded to fallback path)
 *
 * All other codes (skipped_*, converted, disabled, not_eligible,
 * ENGINE_*, STREAMING_CONVERT, STREAMING_SHADOW, STREAMING_SKIP_*)
 * are non-failure outcomes.
 *
 * Parameters:
 *   reason_code - pointer to the reason code ngx_str_t
 *
 * Returns:
 *   1 if the reason code is a failure outcome
 *   0 otherwise
 */
static ngx_int_t
ngx_http_markdown_is_failure_outcome(const ngx_str_t *reason_code)
{
    static const ngx_http_markdown_literal_t failure_codes[] = {
        ngx_http_markdown_literal("decompression_error"),
        ngx_http_markdown_literal("decompression_budget_exceeded"),
        ngx_http_markdown_literal("decompression_format_error"),
        ngx_http_markdown_literal("decompression_truncated_input"),
        ngx_http_markdown_literal("decompression_io_error"),
        ngx_http_markdown_literal("conversion_error"),
        ngx_http_markdown_literal("memory_budget_exceeded"),
        ngx_http_markdown_literal("ffi_panic"),
        ngx_http_markdown_literal("timeout"),
        ngx_http_markdown_literal("budget_exceeded"),
        ngx_http_markdown_literal("replay_error"),
        /* Indices 20-24: reserved for future reason codes (not yet production-used) */
        ngx_http_markdown_literal("overload"),
        ngx_http_markdown_literal("invalid_dynconf"),
        ngx_http_markdown_literal("degraded_snapshot"),
        ngx_http_markdown_literal("header_plan_apply_error"),
        ngx_http_markdown_literal("streaming_mid_flight_error")
    };

    if (reason_code == NULL || reason_code->len == 0) {
        return 0;
    }

    if (ngx_http_markdown_reason_has_prefix_literal(
            reason_code, "failed_")
        || ngx_http_markdown_reason_has_prefix_literal(
            reason_code, "ELIGIBLE_FAILED")
        || ngx_http_markdown_reason_has_prefix_literal(
            reason_code, "FAIL_")
        || ngx_http_markdown_reason_has_prefix_literal(
            reason_code, "STREAMING_FAIL_")
        || ngx_http_markdown_reason_has_prefix_literal(
            reason_code, "STREAMING_PRECOMMIT_")
        || ngx_http_markdown_reason_has_prefix_literal(
            reason_code, "STREAMING_BUDGET_")
        || ngx_http_markdown_reason_has_prefix_literal(
            reason_code, "STREAMING_FALLBACK_"))
    {
        return 1;
    }

    return ngx_http_markdown_reason_is_exact(
        reason_code, failure_codes,
        sizeof(failure_codes) / sizeof(failure_codes[0]));
}


/*
 * Emit the debug-level extended decision log entry (FR-03.3).
 *
 * Adds filter_value, accept, and status fields to the base format.
 *
 * Parameters:
 *   r              - NGINX request structure
 *   conf           - module location configuration
 *   eff            - request-local effective configuration view; may be NULL
 *   reason_code    - the reason code string for this decision
 *   error_category - optional FAIL_* sub-classification (NULL if none)
 *   log_level      - NGINX log level (NGX_LOG_WARN or NGX_LOG_INFO)
 *   method_name    - resolved request method name
 *   content_type   - resolved upstream content-type
 */
static void
ngx_http_markdown_log_decision_debug(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code,
    const ngx_str_t *error_category, ngx_uint_t log_level,
    ngx_http_markdown_decision_meta_t *meta)
{
    ngx_str_t                     accept_value;
    ngx_str_t                     filter_value;
    ngx_str_t                     empty = ngx_string("-");
    const ngx_http_markdown_ctx_t *ctx;

    /*
     * Resolve filter_value from the cached header-phase decision
     * stored in ctx->filter_enabled.  This avoids re-evaluating
     * ngx_http_complex_value() which could drift from the
     * header-phase result for dynamic variables.
     *
     * Fall back to evaluating conf->enabled_complex only when no
     * cached decision exists (ctx is NULL).  In that case, read
     * enabled_source and enabled through the effective view to
     * maintain request-level consistency.
     */
    ctx = ngx_http_get_module_ctx(r,
        ngx_http_markdown_filter_module);

    if (ctx != NULL) {
        if (ctx->filter_enabled) {
            ngx_str_set(&filter_value, "on");
        } else {
            ngx_str_set(&filter_value, "off");
        }
    } else if (ngx_http_markdown_effective_enabled_source(eff, conf)
        == NGX_HTTP_MARKDOWN_ENABLED_COMPLEX
        && conf->enabled_complex != NULL)
    {
        if (ngx_http_complex_value(r,
                conf->enabled_complex,
                &filter_value) != NGX_OK)
        {
            ngx_str_set(&filter_value,
                        "$variable(error)");
        }
    } else if (ngx_http_markdown_effective_enabled(eff, conf)) {
        ngx_str_set(&filter_value, "on");
    } else {
        ngx_str_set(&filter_value, "off");
    }

    /* Resolve Accept header value */
    accept_value = empty;

#if (NGX_HTTP_HEADERS)
    if (r->headers_in.accept != NULL
        && r->headers_in.accept->value.len > 0)
    {
        accept_value = r->headers_in.accept->value;
    }
#else
    {
        ngx_str_t        accept_name = ngx_string("Accept");
        ngx_table_elt_t *accept_hdr;

        accept_hdr =
            ngx_http_markdown_find_request_header(
                r, &accept_name);
        if (accept_hdr != NULL
            && accept_hdr->value.len > 0)
        {
            accept_value = accept_hdr->value;
        }
    }
#endif

    if (error_category != NULL
        && error_category->len > 0)
    {
        ngx_log_error(log_level, r->connection->log, 0,
            "markdown: reason=%V "
            "category=%V "
            "method=%V uri=%V content_type=%V "
            "filter_value=%V accept=%V status=%ui",
            reason_code, error_category,
            meta->method_name, &r->uri,
            meta->content_type, &filter_value,
            &accept_value,
            r->headers_out.status);
    } else {
        ngx_log_error(log_level, r->connection->log, 0,
            "markdown: reason=%V "
            "method=%V uri=%V content_type=%V "
            "filter_value=%V accept=%V status=%ui",
            reason_code, meta->method_name, &r->uri,
            meta->content_type, &filter_value,
            &accept_value,
            r->headers_out.status);
    }
}


/*
 * Emit a structured decision log entry for the current request.
 *
 * Format (info level):
 *   markdown decision: reason=<CODE> method=<METHOD>
 *       uri=<URI> content_type=<TYPE>
 *
 * Format with error category (failure outcomes):
 *   markdown decision: reason=<CODE> category=<FAIL_*>
 *       method=<METHOD> uri=<URI> content_type=<TYPE>
 *
 * Extended format (debug verbosity adds):
 *   ... filter_value=<VALUE> accept=<ACCEPT> status=<STATUS>
 *
 * Verbosity gating:
 *   info / debug  — emit for all outcomes
 *   warn / error  — emit only for failure outcomes
 *
 * NGINX log level:
 *   NGX_LOG_INFO  for non-failure outcomes (disabled, not_eligible,
 *                 skipped_accept, skipped_no_accept, converted,
 *                 streaming outcomes, etc.)
 *   NGX_LOG_WARN  for failure outcomes (failed_open, failed_closed,
 *                 error reason codes, streaming errors, etc.)
 *
 * Parameters:
 *   r              - NGINX request structure
 *   conf           - module location configuration
 *   eff            - effective configuration view for per-request log_verbosity
 *   reason_code    - the reason code string for this decision
 *   error_category - optional FAIL_* sub-classification (NULL if none)
 */
static void
ngx_http_markdown_log_decision_with_category(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code,
    const ngx_str_t *error_category)
{
    ngx_uint_t       log_level;
    ngx_int_t        is_failure;
    ngx_uint_t       effective_verbosity;
    ngx_str_t        method_name;
    ngx_str_t        content_type;
    ngx_str_t        empty = ngx_string("-");

    if (r == NULL || conf == NULL || reason_code == NULL) {
        return;
    }

    is_failure = ngx_http_markdown_is_failure_outcome(reason_code);

    effective_verbosity = ngx_http_markdown_effective_log_verbosity(
        eff, conf);

    if (effective_verbosity > NGX_HTTP_MARKDOWN_LOG_DEBUG) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown: invalid log_verbosity=%ui, "
                      "falling back to INFO",
                      effective_verbosity);
        effective_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(results.decision_count);

    if (effective_verbosity <= NGX_HTTP_MARKDOWN_LOG_WARN
        && !is_failure)
    {
        return;
    }

    /* Select NGINX log level based on outcome type (FR-03.5) */
    log_level = is_failure ? NGX_LOG_WARN : NGX_LOG_INFO;

    /* Resolve request method name */
    if (r->method_name.len > 0) {
        method_name = r->method_name;
    } else {
        method_name = empty;
    }

    /* Resolve upstream content-type (FR-03.2) */
    if (r->headers_out.content_type.len > 0) {
        content_type = r->headers_out.content_type;
    } else {
        content_type = empty;
    }

    /* Debug extended format (FR-03.3) — delegated to helper */
    if (effective_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG) {
        ngx_http_markdown_decision_meta_t  meta;

        meta.method_name = &method_name;
        meta.content_type = &content_type;
        ngx_http_markdown_log_decision_debug(r, conf, eff,
            reason_code, error_category, log_level,
            &meta);
        return;
    }

    /* Base format (FR-03.2, FR-03.6) */
    if (error_category != NULL && error_category->len > 0) {
        ngx_log_error(log_level, r->connection->log, 0,
            "markdown: reason=%V "
            "category=%V "
            "method=%V uri=%V content_type=%V",
            reason_code, error_category,
            &method_name, &r->uri,
            &content_type);
    } else {
        ngx_log_error(log_level, r->connection->log, 0,
            "markdown: reason=%V "
            "method=%V uri=%V content_type=%V",
            reason_code, &method_name, &r->uri,
            &content_type);
    }
}


/*
 * Convenience wrapper: emit decision log without error category.
 */
static void
ngx_http_markdown_log_decision(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_str_t *reason_code)
{
    ngx_http_markdown_log_decision_with_category(
        r, conf, eff, reason_code, NULL);
}

#endif /* NGX_HTTP_MARKDOWN_DECISION_LOG_IMPL_H */
