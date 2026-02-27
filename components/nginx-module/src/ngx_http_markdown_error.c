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
 * Map Rust error code to error category
 *
 * This function classifies conversion failures into three categories:
 * - conversion: HTML parsing errors, invalid input, conversion logic failures
 * - resource_limit: Size limits exceeded, timeout exceeded
 * - system: Memory allocation failures, converter not initialized
 *
 * Error code mapping (from markdown_converter.h):
 * - ERROR_SUCCESS (0): Success - no error occurred
 * - ERROR_PARSE (1): HTML parsing failed (malformed HTML, invalid structure)
 * - ERROR_ENCODING (2): Character encoding error (invalid UTF-8, unsupported charset)
 * - ERROR_TIMEOUT (3): Conversion timeout exceeded
 * - ERROR_MEMORY_LIMIT (4): Memory limit exceeded during conversion
 * - ERROR_INVALID_INPUT (5): Invalid input data (NULL pointers, invalid parameters)
 * - ERROR_INTERNAL (99): Internal error (unexpected condition, panic caught)
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
    switch (error_code) {
        /* Conversion errors: HTML parsing, encoding, invalid input */
        case ERROR_PARSE:
        case ERROR_ENCODING:
        case ERROR_INVALID_INPUT:
            return NGX_HTTP_MARKDOWN_ERROR_CONVERSION;

        /* Resource limit errors: timeout, memory limit */
        case ERROR_TIMEOUT:
        case ERROR_MEMORY_LIMIT:
            return NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT;

        /* System errors: internal errors, unexpected conditions */
        case ERROR_INTERNAL:
            return NGX_HTTP_MARKDOWN_ERROR_SYSTEM;

        /* Default to system error for unknown error codes */
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
