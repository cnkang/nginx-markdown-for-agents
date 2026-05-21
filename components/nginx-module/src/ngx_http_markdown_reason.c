/*
 * NGINX Markdown Filter Module - Reason Code Lookup (Legacy)
 *
 * This file maps existing eligibility enum values and error categories
 * to stable uppercase snake_case reason code strings.  These strings
 * are shared between decision log entries and Prometheus metrics labels
 * so that operators can correlate logs with metric counters without
 * translating between different vocabularies.
 *
 * DEPRECATION NOTICE (v0.7.0):
 *   The reason code string literals defined in this file are LEGACY
 *   constants retained for backward compatibility.  The single source
 *   of truth for reason codes is now the Rust enum in
 *   components/rust-converter/src/decision/reason_code.rs.
 *
 *   New code should use the FFI accessors in ngx_http_markdown_reason_ffi.c:
 *     - ngx_http_markdown_get_reason_code_str(code, &str)
 *     - ngx_http_markdown_get_reason_code_metric_key(code, &str)
 *     - ngx_http_markdown_reason_code_total_count()
 *
 *   DO NOT add new reason code constants to this file.  All new reason
 *   codes must be added to the Rust enum and accessed via FFI.
 *
 * Requirements: FR-01.1, FR-01.2, FR-01.3, FR-01.4, FR-01.5,
 *               FR-13.3, FR-13.4
 */

#include "ngx_http_markdown_filter_module.h"


/*
 * Legacy skip reason codes — DEPRECATED (v0.7.0)
 *
 * These C-side string literals are retained for backward compatibility
 * with the eligibility-based decision path.  New code should use the
 * Rust FFI accessors instead:
 *   ngx_http_markdown_get_reason_code_str(code, &str)
 *
 * See: components/rust-converter/src/decision/reason_code.rs
 */

/* Skip reason codes */

static ngx_str_t ngx_http_markdown_reason_skip_config_str =
    ngx_string("SKIP_CONFIG");
static ngx_str_t ngx_http_markdown_reason_skip_method_str =
    ngx_string("SKIP_METHOD");
static ngx_str_t ngx_http_markdown_reason_skip_status_str =
    ngx_string("SKIP_STATUS");
static ngx_str_t ngx_http_markdown_reason_skip_content_type_str =
    ngx_string("SKIP_CONTENT_TYPE");
static ngx_str_t ngx_http_markdown_reason_skip_size_str =
    ngx_string("SKIP_SIZE");
static ngx_str_t ngx_http_markdown_reason_skip_streaming_str =
    ngx_string("SKIP_STREAMING");
static ngx_str_t ngx_http_markdown_reason_skip_auth_str =
    ngx_string("SKIP_AUTH");
static ngx_str_t ngx_http_markdown_reason_skip_range_str =
    ngx_string("SKIP_RANGE");
static ngx_str_t ngx_http_markdown_reason_skip_accept_str =
    ngx_string("SKIP_ACCEPT");
static ngx_str_t ngx_http_markdown_reason_skip_no_accept_str =
    ngx_string("SKIPPED_NO_ACCEPT");
static ngx_str_t ngx_http_markdown_reason_skip_accept_reject_str =
    ngx_string("SKIPPED_ACCEPT_REJECT");
static ngx_str_t ngx_http_markdown_reason_skip_conditional_str =
    ngx_string("SKIPPED_CONDITIONAL");

/* Content-type routing reason codes (v0.6.0 P1-3) */

static ngx_str_t ngx_http_markdown_reason_ct_route_default_str =
    ngx_string("CT_ROUTE_DEFAULT");
static ngx_str_t ngx_http_markdown_reason_ct_route_configured_str =
    ngx_string("CT_ROUTE_CONFIGURED");

/* Eligible outcome reason codes */

static ngx_str_t ngx_http_markdown_reason_converted_str =
    ngx_string("ELIGIBLE_CONVERTED");
static ngx_str_t ngx_http_markdown_reason_failed_open_str =
    ngx_string("ELIGIBLE_FAILED_OPEN");
static ngx_str_t ngx_http_markdown_reason_failed_closed_str =
    ngx_string("ELIGIBLE_FAILED_CLOSED");

/* Failure sub-classification reason codes */

static ngx_str_t ngx_http_markdown_reason_fail_conversion_str =
    ngx_string("FAIL_CONVERSION");
static ngx_str_t ngx_http_markdown_reason_fail_resource_limit_str =
    ngx_string("FAIL_RESOURCE_LIMIT");
static ngx_str_t ngx_http_markdown_reason_fail_system_str =
    ngx_string("FAIL_SYSTEM");


/*
 * Map eligibility enum to reason code string.
 *
 * Returns a pointer to a static ngx_str_t containing the uppercase
 * snake_case reason code for the given eligibility result.
 *
 * For NGX_HTTP_MARKDOWN_ELIGIBLE the caller should use one of the
 * outcome-specific helpers (converted, failed_open, failed_closed)
 * instead; this function returns FAIL_SYSTEM as a fallback.
 *
 * Unknown enum values return FAIL_SYSTEM and log a warning.
 *
 * Parameters:
 *   eligibility - eligibility enum value
 *   log         - NGINX log for warning on unknown values
 *
 * Returns:
 *   Pointer to static ngx_str_t with the reason code string
 */
const ngx_str_t *
ngx_http_markdown_reason_from_eligibility(
    ngx_http_markdown_eligibility_t eligibility, const ngx_log_t *log)
{
    switch (eligibility) {

    case NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG:
        return &ngx_http_markdown_reason_skip_config_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD:
        return &ngx_http_markdown_reason_skip_method_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS:
        return &ngx_http_markdown_reason_skip_status_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE:
        return &ngx_http_markdown_reason_skip_content_type_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE:
        return &ngx_http_markdown_reason_skip_size_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING:
        return &ngx_http_markdown_reason_skip_streaming_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH:
        return &ngx_http_markdown_reason_skip_auth_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE:
        return &ngx_http_markdown_reason_skip_range_str;

    case NGX_HTTP_MARKDOWN_ELIGIBLE:
        ngx_log_error(NGX_LOG_ERR, (ngx_log_t *) log, 0,
                      "markdown: NGX_HTTP_MARKDOWN_ELIGIBLE "
                      "passed to mapper, callers must pick explicit "
                      "outcome reason (converted/failed)");
        break;

    default:
        ngx_log_error(NGX_LOG_WARN, (ngx_log_t *) log, 0,
                      "markdown: unknown eligibility "
                      "value %d, returning FAIL_SYSTEM",
                      (int) eligibility);
        break;
    }

    return &ngx_http_markdown_reason_fail_system_str;
}


/*
 * Map error category enum to failure reason code string.
 *
 * Returns a pointer to a static ngx_str_t containing the uppercase
 * snake_case failure sub-classification reason code.
 *
 * Unknown enum values return FAIL_SYSTEM and log a warning.
 *
 * Parameters:
 *   category - error category enum value
 *   log      - NGINX log for warning on unknown values
 *
 * Returns:
 *   Pointer to static ngx_str_t with the reason code string
 */
const ngx_str_t *
ngx_http_markdown_reason_from_error_category(
    ngx_http_markdown_error_category_t category, const ngx_log_t *log)
{
    switch (category) {

    case NGX_HTTP_MARKDOWN_ERROR_CONVERSION:
        return &ngx_http_markdown_reason_fail_conversion_str;

    case NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT:
        return &ngx_http_markdown_reason_fail_resource_limit_str;

    case NGX_HTTP_MARKDOWN_ERROR_SYSTEM:
        return &ngx_http_markdown_reason_fail_system_str;

    default:
        ngx_log_error(NGX_LOG_WARN, (ngx_log_t *) log, 0,
                      "markdown: unknown error "
                      "category %d, returning FAIL_SYSTEM",
                      (int) category);
        return &ngx_http_markdown_reason_fail_system_str;
    }
}


/*
 * Return the ELIGIBLE_CONVERTED reason code.
 *
 * Returns:
 *   Pointer to static ngx_str_t "ELIGIBLE_CONVERTED"
 */
const ngx_str_t *
ngx_http_markdown_reason_converted(void)
{
    return &ngx_http_markdown_reason_converted_str;
}


/*
 * Return the ELIGIBLE_FAILED_OPEN reason code.
 *
 * Returns:
 *   Pointer to static ngx_str_t "ELIGIBLE_FAILED_OPEN"
 */
const ngx_str_t *
ngx_http_markdown_reason_failed_open(void)
{
    return &ngx_http_markdown_reason_failed_open_str;
}


/*
 * Return the ELIGIBLE_FAILED_CLOSED reason code.
 *
 * Returns:
 *   Pointer to static ngx_str_t "ELIGIBLE_FAILED_CLOSED"
 */
const ngx_str_t *
ngx_http_markdown_reason_failed_closed(void)
{
    return &ngx_http_markdown_reason_failed_closed_str;
}


/*
 * Return the SKIP_ACCEPT reason code.
 *
 * Accept-based skip is not part of the eligibility enum because
 * Accept negotiation happens after the eligibility check.  This
 * helper provides the reason code for that case.
 *
 * Returns:
 *   Pointer to static ngx_str_t "SKIP_ACCEPT"
 */
const ngx_str_t *
ngx_http_markdown_reason_skip_accept(void)
{
    return &ngx_http_markdown_reason_skip_accept_str;
}


/*
 * Return the SKIPPED_NO_ACCEPT reason code.
 *
 * Used when no Accept header is present in the request.
 *
 * Returns:
 *   Pointer to static ngx_str_t "SKIPPED_NO_ACCEPT"
 */
const ngx_str_t *
ngx_http_markdown_reason_skip_no_accept(void)
{
    return &ngx_http_markdown_reason_skip_no_accept_str;
}


/*
 * Return the SKIPPED_ACCEPT_REJECT reason code.
 *
 * Used when the client explicitly rejects text/markdown (q=0).
 *
 * Returns:
 *   Pointer to static ngx_str_t "SKIPPED_ACCEPT_REJECT"
 */
const ngx_str_t *
ngx_http_markdown_reason_skip_accept_reject(void)
{
    return &ngx_http_markdown_reason_skip_accept_reject_str;
}


/*
 * Return the SKIPPED_CONDITIONAL reason code.
 *
 * Used when a conditional request matches (304 Not Modified).
 * The conversion was eligible but skipped because the client
 * already has the current version.
 *
 * Returns:
 *   Pointer to static ngx_str_t "SKIPPED_CONDITIONAL"
 */
const ngx_str_t *
ngx_http_markdown_reason_skip_conditional(void)
{
    return &ngx_http_markdown_reason_skip_conditional_str;
}


#ifdef MARKDOWN_STREAMING_ENABLED

/* Streaming reason codes */

static ngx_str_t ngx_http_markdown_reason_engine_streaming_str =
    ngx_string("ENGINE_STREAMING");
static ngx_str_t ngx_http_markdown_reason_streaming_convert_str =
    ngx_string("STREAMING_CONVERT");
static ngx_str_t ngx_http_markdown_reason_streaming_fallback_str =
    ngx_string("STREAMING_FALLBACK_PREBUFFER");
static ngx_str_t ngx_http_markdown_reason_streaming_fail_postcommit_str =
    ngx_string("STREAMING_FAIL_POSTCOMMIT");
static ngx_str_t ngx_http_markdown_reason_streaming_skip_str =
    ngx_string("STREAMING_SKIP_UNSUPPORTED");
static ngx_str_t ngx_http_markdown_reason_streaming_budget_str =
    ngx_string("STREAMING_BUDGET_EXCEEDED");
static ngx_str_t ngx_http_markdown_reason_streaming_precommit_failopen_str =
    ngx_string("STREAMING_PRECOMMIT_FAILOPEN");
static ngx_str_t ngx_http_markdown_reason_streaming_precommit_reject_str =
    ngx_string("STREAMING_PRECOMMIT_REJECT");
static ngx_str_t ngx_http_markdown_reason_streaming_shadow_str =
    ngx_string("STREAMING_SHADOW");

/* Auto-mode engine selection reason codes */
static ngx_str_t ngx_http_markdown_reason_eligible_streaming_auto_str =
    ngx_string("ELIGIBLE_STREAMING_AUTO");
static ngx_str_t ngx_http_markdown_reason_eligible_fullbuffer_auto_str =
    ngx_string("ELIGIBLE_FULLBUFFER_AUTO");


/*
 * Return the ENGINE_STREAMING reason code.
 *
 * Logged when the engine selector chooses the streaming path.
 *
 * Returns:
 *   Pointer to static ngx_str_t "ENGINE_STREAMING"
 */
const ngx_str_t *
ngx_http_markdown_reason_engine_streaming(void)
{
    return &ngx_http_markdown_reason_engine_streaming_str;
}


/*
 * Return the STREAMING_CONVERT reason code.
 *
 * Logged when streaming conversion completes successfully.
 *
 * Returns:
 *   Pointer to static ngx_str_t "STREAMING_CONVERT"
 */
const ngx_str_t *
ngx_http_markdown_reason_streaming_convert(void)
{
    return &ngx_http_markdown_reason_streaming_convert_str;
}


/*
 * Return the STREAMING_FALLBACK_PREBUFFER reason code.
 *
 * Logged when streaming falls back to full-buffer in
 * the Pre_Commit_Phase due to Rust engine returning
 * ERROR_STREAMING_FALLBACK.
 *
 * Returns:
 *   Pointer to static ngx_str_t "STREAMING_FALLBACK_PREBUFFER"
 */
const ngx_str_t *
ngx_http_markdown_reason_streaming_fallback(void)
{
    return &ngx_http_markdown_reason_streaming_fallback_str;
}


/*
 * Return the STREAMING_FAIL_POSTCOMMIT reason code.
 *
 * Logged when a post-commit error occurs during streaming.
 *
 * Returns:
 *   Pointer to static ngx_str_t "STREAMING_FAIL_POSTCOMMIT"
 */
const ngx_str_t *
ngx_http_markdown_reason_streaming_fail_postcommit(void)
{
    return &ngx_http_markdown_reason_streaming_fail_postcommit_str;
}


/*
 * Return the STREAMING_SKIP_UNSUPPORTED reason code.
 *
 * Logged when the engine selector rejects streaming due to
 * unsupported capability or policy, before Rust session starts.
 *
 * Returns:
 *   Pointer to static ngx_str_t "STREAMING_SKIP_UNSUPPORTED"
 */
const ngx_str_t *
ngx_http_markdown_reason_streaming_skip_unsupported(void)
{
    return &ngx_http_markdown_reason_streaming_skip_str;
}


/*
 * Return the STREAMING_BUDGET_EXCEEDED reason code.
 *
 * Auxiliary classification code logged when memory budget
 * is exceeded.  The terminal state is determined by the
 * markdown_streaming_on_error policy.
 *
 * Returns:
 *   Pointer to static ngx_str_t "STREAMING_BUDGET_EXCEEDED"
 */
const ngx_str_t *
ngx_http_markdown_reason_streaming_budget_exceeded(void)
{
    return &ngx_http_markdown_reason_streaming_budget_str;
}


/*
 * Return the STREAMING_PRECOMMIT_FAILOPEN reason code.
 *
 * Logged when a pre-commit error triggers fail-open behavior.
 *
 * Returns:
 *   Pointer to static ngx_str_t "STREAMING_PRECOMMIT_FAILOPEN"
 */
const ngx_str_t *
ngx_http_markdown_reason_streaming_precommit_failopen(void)
{
    return &ngx_http_markdown_reason_streaming_precommit_failopen_str;
}


/*
 * Return the STREAMING_PRECOMMIT_REJECT reason code.
 *
 * Logged when a pre-commit error triggers fail-closed behavior.
 *
 * Returns:
 *   Pointer to static ngx_str_t "STREAMING_PRECOMMIT_REJECT"
 */
const ngx_str_t *
ngx_http_markdown_reason_streaming_precommit_reject(void)
{
    return &ngx_http_markdown_reason_streaming_precommit_reject_str;
}


/*
 * Return the STREAMING_SHADOW reason code.
 *
 * Logged when shadow mode runs a comparison.
 *
 * Returns:
 *   Pointer to static ngx_str_t "STREAMING_SHADOW"
 */
const ngx_str_t *
ngx_http_markdown_reason_streaming_shadow(void)
{
    return &ngx_http_markdown_reason_streaming_shadow_str;
}


/*
 * Return the ELIGIBLE_STREAMING_AUTO reason code.
 *
 * Logged when auto mode selects the streaming engine
 * (Content-Length >= auto_threshold or chunked transfer).
 *
 * Returns:
 *   Pointer to static ngx_str_t "ELIGIBLE_STREAMING_AUTO"
 */
const ngx_str_t *
ngx_http_markdown_reason_eligible_streaming_auto(void)
{
    return &ngx_http_markdown_reason_eligible_streaming_auto_str;
}


/*
 * Return the ELIGIBLE_FULLBUFFER_AUTO reason code.
 *
 * Logged when auto mode selects the full-buffer engine
 * (Content-Length < auto_threshold).
 *
 * Returns:
 *   Pointer to static ngx_str_t "ELIGIBLE_FULLBUFFER_AUTO"
 */
const ngx_str_t *
ngx_http_markdown_reason_eligible_fullbuffer_auto(void)
{
    return &ngx_http_markdown_reason_eligible_fullbuffer_auto_str;
}

#endif /* MARKDOWN_STREAMING_ENABLED */


/*
 * Return the CT_ROUTE_DEFAULT reason code.
 *
 * Logged when the content-type allowlist is the default
 * (text/html only) and the request matches.
 *
 * Returns:
 *   Pointer to static ngx_str_t "CT_ROUTE_DEFAULT"
 */
const ngx_str_t *
ngx_http_markdown_reason_ct_route_default(void)
{
    return &ngx_http_markdown_reason_ct_route_default_str;
}


/*
 * Return the CT_ROUTE_CONFIGURED reason code.
 *
 * Logged when the content-type allowlist was configured
 * via markdown_content_types and the request matches.
 *
 * Returns:
 *   Pointer to static ngx_str_t "CT_ROUTE_CONFIGURED"
 */
const ngx_str_t *
ngx_http_markdown_reason_ct_route_configured(void)
{
    return &ngx_http_markdown_reason_ct_route_configured_str;
}
