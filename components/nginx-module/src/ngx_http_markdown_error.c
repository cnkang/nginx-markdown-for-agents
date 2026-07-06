/*
 * NGINX Markdown Filter Module - Error Classification
 *
 * This file implements error classification for conversion failures.
 * It maps Rust error codes to error categories for logging and metrics.
 *
 * Requirements: FR-09.5, FR-09.6, FR-09.7
 */

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>
#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

static ngx_str_t ngx_http_markdown_error_conversion_str = ngx_string("conversion");
static ngx_str_t ngx_http_markdown_error_resource_limit_str = ngx_string("resource_limit");
static ngx_str_t ngx_http_markdown_error_system_str = ngx_string("system");
static ngx_str_t ngx_http_markdown_error_unknown_str = ngx_string("unknown");

/*
 * Map Rust error code to error category via Rust FFI delegation
 *
 * This function classifies conversion failures into three categories:
 * - conversion: HTML parsing errors, invalid input, conversion logic failures
 * - resource_limit: Size limits exceeded, timeout exceeded
 * - system: Memory allocation failures, converter not initialized
 *
 * The raw error code → ErrorClass mapping is performed by Rust
 * (markdown_classify_error_code), which is the single source of truth.
 * This function maps the resulting ErrorClass discriminant to the C
 * error category enum.
 *
 * Parameters:
 *   error_code - Rust error code from MarkdownResult.error_code
 *
 * Returns:
 *   Error category enum value
 */
ngx_http_markdown_error_category_t
ngx_http_markdown_classify_error(uint32_t error_code)
{
    uint8_t class_discriminant;

    class_discriminant = markdown_classify_error_code(error_code);

    /*
     * Map Rust ErrorClass discriminants to C categories:
     *   ErrorClass::ConversionError(0)        → CONVERSION
     *   ErrorClass::Timeout(1)                → RESOURCE_LIMIT
     *   ErrorClass::MemoryBudgetExceeded(2)   → RESOURCE_LIMIT
     *   ErrorClass::FfiPanic(3)               → SYSTEM
     *   ErrorClass::DecompressionError(4)     → CONVERSION
     *   ErrorClass::Overload(5)               → RESOURCE_LIMIT
     *   ErrorClass::InvalidDynconf(6)         → SYSTEM
     *   ErrorClass::DegradedSnapshot(7)       → SYSTEM
     *   ErrorClass::HeaderPlanApplyError(8)   → SYSTEM
     *   ErrorClass::StreamingMidFlightError(9)→ SYSTEM
     */
    switch (class_discriminant) {

    case 0: /* ConversionError */
    case 4: /* DecompressionError */
        return NGX_HTTP_MARKDOWN_ERROR_CONVERSION;

    case 1: /* Timeout */
    case 2: /* MemoryBudgetExceeded */
    case 5: /* Overload */
        return NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT;

    default:
        return NGX_HTTP_MARKDOWN_ERROR_SYSTEM;
    }
}

/*
 * Get human-readable string for error category
 *
 * This function returns a string representation of the error category
 * for use in logging and metrics.
 *
 * Parameters:
 *   category - Error category enum value
 *
 * Returns:
 *   String representation of the category
 */
const ngx_str_t *
ngx_http_markdown_error_category_string(ngx_http_markdown_error_category_t category)
{
    switch (category) {
        case NGX_HTTP_MARKDOWN_ERROR_CONVERSION:
            return &ngx_http_markdown_error_conversion_str;
        case NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT:
            return &ngx_http_markdown_error_resource_limit_str;
        case NGX_HTTP_MARKDOWN_ERROR_SYSTEM:
            return &ngx_http_markdown_error_system_str;
        default:
            return &ngx_http_markdown_error_unknown_str;
    }
}
