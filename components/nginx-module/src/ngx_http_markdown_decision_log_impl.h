#ifndef NGX_HTTP_MARKDOWN_DECISION_LOG_IMPL_H
#define NGX_HTTP_MARKDOWN_DECISION_LOG_IMPL_H

/*
 * Decision log emission helper.
 *
 * WARNING: This header is an implementation detail of the main translation
 * unit (ngx_http_markdown_filter_module.c).  It must NOT be included from
 * any other .c file or used as a standalone compilation unit.
 *
 * Emits a structured "markdown decision:" log entry once per request,
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
    const ngx_http_markdown_conf_t *conf, const ngx_str_t *reason_code);
static void ngx_http_markdown_log_decision_with_category(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    const ngx_str_t *reason_code,
    const ngx_str_t *error_category);
static void ngx_http_markdown_log_decision_debug(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    const ngx_str_t *reason_code, const ngx_str_t *error_category,
    ngx_uint_t log_level, ngx_str_t *method_name,
    ngx_str_t *content_type);


/*
 * Determine whether a reason code represents a failure outcome.
 *
 * Failure outcomes are reason codes matching any of:
 *   - "ELIGIBLE_FAILED" prefix (full-buffer conversion failures)
 *   - "FAIL_" prefix (legacy failure codes)
 *   - "STREAMING_FAIL_" substring (streaming post-commit failures)
 *   - "STREAMING_PRECOMMIT_" prefix (streaming pre-commit failures)
 *   - "STREAMING_BUDGET_" prefix (streaming budget exceeded)
 *   - "STREAMING_FALLBACK_" prefix (streaming degraded to fallback path)
 *
 * All other codes (SKIP_*, ELIGIBLE_CONVERTED, ENGINE_*,
 * STREAMING_CONVERT, STREAMING_SHADOW, STREAMING_SKIP_*)
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
    if (reason_code == NULL || reason_code->len == 0) {
        return 0;
    }

    /* Check for "ELIGIBLE_FAILED" prefix (15 chars) */
    if (reason_code->len >= 15
        && ngx_strncmp(reason_code->data,
                       (const u_char *) "ELIGIBLE_FAILED",
                       15) == 0)
    {
        return 1;
    }

    /* Check for "FAIL_" prefix (5 chars) */
    if (reason_code->len >= 5
        && ngx_strncmp(reason_code->data,
                       (const u_char *) "FAIL_", 5) == 0)
    {
        return 1;
    }

    /*
     * Streaming failure codes use the "STREAMING_" prefix
     * but not all STREAMING_* codes are failures.
     *
     * Failures:
     *   STREAMING_FAIL_POSTCOMMIT
     *   STREAMING_PRECOMMIT_FAILOPEN
     *   STREAMING_PRECOMMIT_REJECT
     *   STREAMING_BUDGET_EXCEEDED
     *
     * Non-failures (informational):
     *   STREAMING_CONVERT
     *   STREAMING_SHADOW
     *   STREAMING_SKIP_UNSUPPORTED
     */
    if (reason_code->len >= 10
        && ngx_strncmp(reason_code->data,
                       (const u_char *) "STREAMING_",
                       10) == 0)
    {
        /* "STREAMING_FAIL_" (15 chars) */
        if (reason_code->len >= 15
            && ngx_strncmp(reason_code->data,
                           (const u_char *) "STREAMING_FAIL_",
                           15) == 0)
        {
            return 1;
        }

        /* "STREAMING_PRECOMMIT_" (20 chars) */
        if (reason_code->len >= 20
            && ngx_strncmp(reason_code->data,
                           (const u_char *) "STREAMING_PRECOMMIT_",
                           20) == 0)
        {
            return 1;
        }

        /* "STREAMING_BUDGET_" (17 chars) */
        if (reason_code->len >= 17
            && ngx_strncmp(reason_code->data,
                           (const u_char *) "STREAMING_BUDGET_",
                           17) == 0)
        {
            return 1;
        }

        /* "STREAMING_FALLBACK_" (19 chars) */
        if (reason_code->len >= 19
            && ngx_strncmp(reason_code->data,
                           (const u_char *) "STREAMING_FALLBACK_",
                           19) == 0)
        {
            return 1;
        }
    }

    return 0;
}


/*
 * Emit the debug-level extended decision log entry (FR-03.3).
 *
 * Adds filter_value, accept, and status fields to the base format.
 *
 * Parameters:
 *   r              - NGINX request structure
 *   conf           - module location configuration
 *   reason_code    - the reason code string for this decision
 *   error_category - optional FAIL_* sub-classification (NULL if none)
 *   log_level      - NGINX log level (NGX_LOG_WARN or NGX_LOG_INFO)
 *   method_name    - resolved request method name
 *   content_type   - resolved upstream content-type
 */
static void
ngx_http_markdown_log_decision_debug(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf, const ngx_str_t *reason_code,
    const ngx_str_t *error_category, ngx_uint_t log_level,
    ngx_str_t *method_name, ngx_str_t *content_type)
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
     * cached decision exists (ctx is NULL).
     */
    ctx = ngx_http_get_module_ctx(r,
        ngx_http_markdown_filter_module);

    if (ctx != NULL) {
        if (ctx->filter_enabled) {
            ngx_str_set(&filter_value, "on");
        } else {
            ngx_str_set(&filter_value, "off");
        }
    } else if (conf->enabled_source
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
    } else if (conf->enabled) {
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
            "markdown decision: reason=%V "
            "category=%V "
            "method=%V uri=%V content_type=%V "
            "filter_value=%V accept=%V status=%ui",
            reason_code, error_category,
            method_name, &r->uri,
            content_type, &filter_value,
            &accept_value,
            r->headers_out.status);
    } else {
        ngx_log_error(log_level, r->connection->log, 0,
            "markdown decision: reason=%V "
            "method=%V uri=%V content_type=%V "
            "filter_value=%V accept=%V status=%ui",
            reason_code, method_name, &r->uri,
            content_type, &filter_value,
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
 *   NGX_LOG_INFO  for non-failure outcomes (SKIP_*, ELIGIBLE_CONVERTED,
 *                 STREAMING_CONVERT, STREAMING_SHADOW, etc.)
 *   NGX_LOG_WARN  for failure outcomes (ELIGIBLE_FAILED_*, FAIL_*,
 *                 STREAMING_FAIL_*, STREAMING_PRECOMMIT_*,
 *                 STREAMING_BUDGET_*)
 *
 * Parameters:
 *   r              - NGINX request structure
 *   conf           - module location configuration
 *   reason_code    - the reason code string for this decision
 *   error_category - optional FAIL_* sub-classification (NULL if none)
 */
static void
ngx_http_markdown_log_decision_with_category(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf, const ngx_str_t *reason_code,
    const ngx_str_t *error_category)
{
    ngx_uint_t       log_level;
    ngx_int_t        is_failure;
    ngx_str_t        method_name;
    ngx_str_t        content_type;
    ngx_str_t        empty = ngx_string("-");

    if (r == NULL || conf == NULL || reason_code == NULL) {
        return;
    }

    is_failure = ngx_http_markdown_is_failure_outcome(reason_code);

    /*
     * Verbosity gating (FR-03.4):
     *   info / debug  — all outcomes
     *   warn / error  — failure outcomes only
     */
    if (conf->log_verbosity <= NGX_HTTP_MARKDOWN_LOG_WARN
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
    if (conf->log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG) {
        ngx_http_markdown_log_decision_debug(r, conf,
            reason_code, error_category, log_level,
            &method_name, &content_type);
        return;
    }

    /* Base format (FR-03.2, FR-03.6) */
    if (error_category != NULL && error_category->len > 0) {
        ngx_log_error(log_level, r->connection->log, 0,
            "markdown decision: reason=%V "
            "category=%V "
            "method=%V uri=%V content_type=%V",
            reason_code, error_category,
            &method_name, &r->uri,
            &content_type);
    } else {
        ngx_log_error(log_level, r->connection->log, 0,
            "markdown decision: reason=%V "
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
    const ngx_http_markdown_conf_t *conf, const ngx_str_t *reason_code)
{
    ngx_http_markdown_log_decision_with_category(
        r, conf, reason_code, NULL);
}

#endif /* NGX_HTTP_MARKDOWN_DECISION_LOG_IMPL_H */
