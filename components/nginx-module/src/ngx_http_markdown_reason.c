/*
 * NGINX Markdown Filter Module - Reason Code Lookup
 *
 * This file maps existing eligibility enum values and error categories
 * to stable uppercase snake_case reason code strings.  These strings
 * are shared between decision log entries and Prometheus metrics labels
 * so that operators can correlate logs with metric counters without
 * translating between different vocabularies.
 *
 * Requirements: FR-01.1, FR-01.2, FR-01.3, FR-01.4, FR-01.5,
 *               FR-13.3, FR-13.4
 */

#include "ngx_http_markdown_filter_module.h"


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
                      "markdown reason: NGX_HTTP_MARKDOWN_ELIGIBLE "
                      "passed to mapper, callers must pick explicit "
                      "outcome reason (converted/failed)");
        break;

    default:
        ngx_log_error(NGX_LOG_WARN, (ngx_log_t *) log, 0,
                      "markdown reason: unknown eligibility "
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
                      "markdown reason: unknown error "
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
