/*
 * NGINX Markdown Filter Module - Reason Code Lookup (via Rust FFI)
 *
 * This file provides accessor functions that map C-side eligibility enums
 * and error categories to reason code strings.  All strings are sourced
 * from the Rust ReasonCode enum via FFI — there are no C-side string
 * literals for reason codes.
 *
 * The Rust enum in components/rust-converter/src/decision/reason_code.rs
 * is the SINGLE SOURCE OF TRUTH for all reason code strings.
 *
 * Requirements: FR-01.1, FR-01.2, FR-01.3, FR-01.4, FR-01.5,
 *               FR-13.3, FR-13.4
 */

#include "ngx_http_markdown_filter_module.h"


/*
 * Forward-declare the FFI wrapper so this translation unit does not
 * need to include the full markdown_converter.h header.
 */
ngx_int_t ngx_http_markdown_get_reason_code_str(uint32_t code,
    ngx_str_t *out_str);


/*
 * Reason code discriminant constants from the Rust enum.
 *
 * These MUST stay in sync with ReasonCode in reason_code.rs.
 * The canonical values are:
 *   0  = Converted           ("converted")
 *   1  = SkippedAccept       ("skipped_accept")
 *   2  = SkippedNoAccept     ("skipped_no_accept")
 *   3  = SkippedConditional  ("skipped_conditional")
 *  12  = SkippedAcceptReject ("skipped_accept_reject")
 *  13  = FfiPanic            ("ffi_panic")
 *  14  = NotEligible         ("not_eligible")
 *  15  = Disabled            ("disabled")
 *  16  = FailedOpen          ("failed_open")
 *  17  = FailedClosed        ("failed_closed")
 *  18  = ConversionError     ("conversion_error")
 *  19  = MemoryBudgetExceeded ("memory_budget_exceeded")
 */
#define REASON_CONVERTED                 0
#define REASON_SKIPPED_ACCEPT            1
#define REASON_SKIPPED_NO_ACCEPT         2
#define REASON_SKIPPED_CONDITIONAL       3
#define REASON_SKIPPED_ACCEPT_REJECT    12
#define REASON_FFI_PANIC                13
#define REASON_NOT_ELIGIBLE             14
#define REASON_DISABLED                 15
#define REASON_FAILED_OPEN              16
#define REASON_FAILED_CLOSED            17
#define REASON_CONVERSION_ERROR         18
#define REASON_MEMORY_BUDGET_EXCEEDED   19


/*
 * Static ngx_str_t storage for each accessor return value.
 *
 * Because the accessor functions return `const ngx_str_t *`, we need
 * stable storage.  These are populated lazily on first call; the
 * underlying string data points to static Rust memory (process-lifetime).
 */
static ngx_str_t  reason_str_converted;
static ngx_str_t  reason_str_skipped_accept;
static ngx_str_t  reason_str_skipped_no_accept;
static ngx_str_t  reason_str_skipped_conditional;
static ngx_str_t  reason_str_skipped_accept_reject;
static ngx_str_t  reason_str_not_eligible;
static ngx_str_t  reason_str_disabled;
static ngx_str_t  reason_str_failed_open;
static ngx_str_t  reason_str_failed_closed;
static ngx_str_t  reason_str_conversion_error;
static ngx_str_t  reason_str_memory_budget_exceeded;
static ngx_str_t  reason_str_ffi_panic;

static ngx_flag_t reason_strs_initialized = 0;


/*
 * Initialize all static reason string storage from Rust FFI.
 *
 * Called once on first use.  After this, all reason_str_* variables
 * contain valid ngx_str_t values pointing to Rust static memory.
 */
static void
ngx_http_markdown_reason_init_strs(void)
{
    if (reason_strs_initialized) {
        return;
    }

    ngx_http_markdown_get_reason_code_str(REASON_CONVERTED,
        &reason_str_converted);
    ngx_http_markdown_get_reason_code_str(REASON_SKIPPED_ACCEPT,
        &reason_str_skipped_accept);
    ngx_http_markdown_get_reason_code_str(REASON_SKIPPED_NO_ACCEPT,
        &reason_str_skipped_no_accept);
    ngx_http_markdown_get_reason_code_str(REASON_SKIPPED_CONDITIONAL,
        &reason_str_skipped_conditional);
    ngx_http_markdown_get_reason_code_str(REASON_SKIPPED_ACCEPT_REJECT,
        &reason_str_skipped_accept_reject);
    ngx_http_markdown_get_reason_code_str(REASON_NOT_ELIGIBLE,
        &reason_str_not_eligible);
    ngx_http_markdown_get_reason_code_str(REASON_DISABLED,
        &reason_str_disabled);
    ngx_http_markdown_get_reason_code_str(REASON_FAILED_OPEN,
        &reason_str_failed_open);
    ngx_http_markdown_get_reason_code_str(REASON_FAILED_CLOSED,
        &reason_str_failed_closed);
    ngx_http_markdown_get_reason_code_str(REASON_CONVERSION_ERROR,
        &reason_str_conversion_error);
    ngx_http_markdown_get_reason_code_str(REASON_MEMORY_BUDGET_EXCEEDED,
        &reason_str_memory_budget_exceeded);
    ngx_http_markdown_get_reason_code_str(REASON_FFI_PANIC,
        &reason_str_ffi_panic);

    reason_strs_initialized = 1;
}


/*
 * Map eligibility enum to reason code string (via Rust FFI).
 *
 * All INELIGIBLE_* variants now map to "not_eligible" (discriminant 14)
 * except INELIGIBLE_CONFIG which maps to "disabled" (discriminant 15).
 *
 * For NGX_HTTP_MARKDOWN_ELIGIBLE the caller should use one of the
 * outcome-specific helpers (converted, failed_open, failed_closed)
 * instead; this function returns "ffi_panic" as a fallback.
 *
 * Parameters:
 *   eligibility - eligibility enum value
 *   log         - NGINX log for warning on unknown values (may be NULL)
 *
 * Returns:
 *   Pointer to static ngx_str_t with the reason code string
 */
const ngx_str_t *
ngx_http_markdown_reason_from_eligibility(
    ngx_http_markdown_eligibility_t eligibility, ngx_log_t *log)
{
    ngx_http_markdown_reason_init_strs();

    switch (eligibility) {

    case NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG:
        return &reason_str_disabled;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD:
    case NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS:
    case NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE:
    case NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE:
    case NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING:
    case NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH:
    case NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE:
        return &reason_str_not_eligible;

    case NGX_HTTP_MARKDOWN_ELIGIBLE:
        if (log != NULL) {
            ngx_log_error(NGX_LOG_ERR, log, 0,
                          "markdown: NGX_HTTP_MARKDOWN_ELIGIBLE "
                          "passed to mapper, callers must pick "
                          "explicit outcome reason "
                          "(converted/failed)");
        }
        break;

    default:
        if (log != NULL) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: unknown eligibility "
                          "value %d, returning ffi_panic",
                          (int) eligibility);
        }
        break;
    }

    return &reason_str_ffi_panic;
}


/*
 * Map error category enum to failure reason code string (via Rust FFI).
 *
 * Parameters:
 *   category - error category enum value
 *   log      - NGINX log for warning on unknown values (may be NULL)
 *
 * Returns:
 *   Pointer to static ngx_str_t with the reason code string
 */
const ngx_str_t *
ngx_http_markdown_reason_from_error_category(
    ngx_http_markdown_error_category_t category, ngx_log_t *log)
{
    ngx_http_markdown_reason_init_strs();

    switch (category) {

    case NGX_HTTP_MARKDOWN_ERROR_CONVERSION:
        return &reason_str_conversion_error;

    case NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT:
        return &reason_str_memory_budget_exceeded;

    case NGX_HTTP_MARKDOWN_ERROR_SYSTEM:
        return &reason_str_ffi_panic;

    default:
        if (log != NULL) {
            ngx_log_error(NGX_LOG_WARN, log, 0,
                          "markdown: unknown error "
                          "category %d, returning ffi_panic",
                          (int) category);
        }
        return &reason_str_ffi_panic;
    }
}


/*
 * Return the "converted" reason code.
 *
 * Returns:
 *   Pointer to static ngx_str_t "converted"
 */
const ngx_str_t *
ngx_http_markdown_reason_converted(void)
{
    ngx_http_markdown_reason_init_strs();
    return &reason_str_converted;
}


/*
 * Return the "failed_open" reason code.
 *
 * Returns:
 *   Pointer to static ngx_str_t "failed_open"
 */
const ngx_str_t *
ngx_http_markdown_reason_failed_open(void)
{
    ngx_http_markdown_reason_init_strs();
    return &reason_str_failed_open;
}


/*
 * Return the "failed_closed" reason code.
 *
 * Returns:
 *   Pointer to static ngx_str_t "failed_closed"
 */
const ngx_str_t *
ngx_http_markdown_reason_failed_closed(void)
{
    ngx_http_markdown_reason_init_strs();
    return &reason_str_failed_closed;
}


/*
 * Return the "skipped_accept" reason code.
 *
 * Accept-based skip is not part of the eligibility enum because
 * Accept negotiation happens after the eligibility check.
 *
 * Returns:
 *   Pointer to static ngx_str_t "skipped_accept"
 */
const ngx_str_t *
ngx_http_markdown_reason_skip_accept(void)
{
    ngx_http_markdown_reason_init_strs();
    return &reason_str_skipped_accept;
}


/*
 * Return the "skipped_no_accept" reason code.
 *
 * Used when no Accept header is present in the request.
 *
 * Returns:
 *   Pointer to static ngx_str_t "skipped_no_accept"
 */
const ngx_str_t *
ngx_http_markdown_reason_skip_no_accept(void)
{
    ngx_http_markdown_reason_init_strs();
    return &reason_str_skipped_no_accept;
}


/*
 * Return the "skipped_accept_reject" reason code.
 *
 * Used when the client explicitly rejects text/markdown (q=0).
 *
 * Returns:
 *   Pointer to static ngx_str_t "skipped_accept_reject"
 */
const ngx_str_t *
ngx_http_markdown_reason_skip_accept_reject(void)
{
    ngx_http_markdown_reason_init_strs();
    return &reason_str_skipped_accept_reject;
}


/*
 * Return the "skipped_conditional" reason code.
 *
 * Used when a conditional request matches (304 Not Modified).
 *
 * Returns:
 *   Pointer to static ngx_str_t "skipped_conditional"
 */
const ngx_str_t *
ngx_http_markdown_reason_skip_conditional(void)
{
    ngx_http_markdown_reason_init_strs();
    return &reason_str_skipped_conditional;
}


#ifdef MARKDOWN_STREAMING_ENABLED

/*
 * Streaming reason codes.
 *
 * These are C-only reason codes for the streaming path.  They do NOT
 * have a corresponding Rust ReasonCode variant yet (streaming codes
 * are local to the C streaming engine).  They retain their UPPERCASE
 * format as legacy constants until a future spec adds them to Rust.
 */

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
static ngx_str_t ngx_http_markdown_reason_streaming_skip_compressed_str =
    ngx_string("STREAMING_SKIP_COMPRESSED");
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


const ngx_str_t *
ngx_http_markdown_reason_engine_streaming(void)
{
    return &ngx_http_markdown_reason_engine_streaming_str;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_convert(void)
{
    return &ngx_http_markdown_reason_streaming_convert_str;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_fallback(void)
{
    return &ngx_http_markdown_reason_streaming_fallback_str;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_fail_postcommit(void)
{
    return &ngx_http_markdown_reason_streaming_fail_postcommit_str;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_skip_unsupported(void)
{
    return &ngx_http_markdown_reason_streaming_skip_str;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_skip_compressed(void)
{
    return &ngx_http_markdown_reason_streaming_skip_compressed_str;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_budget_exceeded(void)
{
    return &ngx_http_markdown_reason_streaming_budget_str;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_precommit_failopen(void)
{
    return &ngx_http_markdown_reason_streaming_precommit_failopen_str;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_precommit_reject(void)
{
    return &ngx_http_markdown_reason_streaming_precommit_reject_str;
}

const ngx_str_t *
ngx_http_markdown_reason_streaming_shadow(void)
{
    return &ngx_http_markdown_reason_streaming_shadow_str;
}

const ngx_str_t *
ngx_http_markdown_reason_eligible_streaming_auto(void)
{
    return &ngx_http_markdown_reason_eligible_streaming_auto_str;
}

const ngx_str_t *
ngx_http_markdown_reason_eligible_fullbuffer_auto(void)
{
    return &ngx_http_markdown_reason_eligible_fullbuffer_auto_str;
}

#endif /* MARKDOWN_STREAMING_ENABLED */
