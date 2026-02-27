/*
 * NGINX Markdown Filter Module - Header File
 *
 * This header defines the NGINX module structures and functions for
 * the Markdown conversion filter.
 */

#ifndef NGX_HTTP_MARKDOWN_FILTER_MODULE_H
#define NGX_HTTP_MARKDOWN_FILTER_MODULE_H

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

/*
 * Configuration constants for on_error directive
 */
#define NGX_HTTP_MARKDOWN_ON_ERROR_PASS    0  /* fail-open: return original HTML */
#define NGX_HTTP_MARKDOWN_ON_ERROR_REJECT  1  /* fail-closed: return 502 error */

/*
 * Configuration constants for flavor directive
 */
#define NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK  0  /* CommonMark flavor */
#define NGX_HTTP_MARKDOWN_FLAVOR_GFM         1  /* GitHub Flavored Markdown */

/*
 * Configuration constants for auth_policy directive
 */
#define NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW  0  /* Allow conversion of authenticated requests */
#define NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY   1  /* Deny conversion of authenticated requests */

/*
 * Configuration constants for conditional_requests directive
 */
#define NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT         0  /* Full If-None-Match support */
#define NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE    1  /* If-Modified-Since only */
#define NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED             2  /* No conditional request support */

/*
 * Configuration constants for log verbosity directive
 *
 * This is a module-local verbosity filter for module-generated logs.
 * NGINX's global `error_log` level still applies as the outer filter.
 */
#define NGX_HTTP_MARKDOWN_LOG_ERROR  0  /* Only error/critical */
#define NGX_HTTP_MARKDOWN_LOG_WARN   1  /* Warnings and above */
#define NGX_HTTP_MARKDOWN_LOG_INFO   2  /* Informational and above (default) */
#define NGX_HTTP_MARKDOWN_LOG_DEBUG  3  /* Debug and above */

/*
 * Compression type enumeration
 *
 * Identifies the compression format of upstream response content.
 * Used for automatic decompression of compressed HTML before conversion.
 */
typedef enum {
    NGX_HTTP_MARKDOWN_COMPRESSION_NONE = 0,     /* No compression */
    NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,         /* gzip compression */
    NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,      /* deflate compression */
    NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,       /* brotli compression */
    NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN       /* Unknown/unsupported compression */
} ngx_http_markdown_compression_type_e;

/*
 * Module configuration structure
 *
 * This structure holds configuration directives for the Markdown filter.
 * It supports NGINX's configuration inheritance model (http, server, location).
 *
 * Configuration defaults (defined in ngx_http_markdown_create_loc_conf):
 * - enabled: NGX_CONF_UNSET (inherit from parent)
 * - max_size: 10MB (10 * 1024 * 1024 bytes)
 * - timeout: 5000ms (5 seconds)
 * - on_error: NGX_HTTP_MARKDOWN_ON_ERROR_PASS (fail-open)
 * - flavor: NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK
 * - token_estimate: NGX_CONF_UNSET (off by default)
 * - front_matter: NGX_CONF_UNSET (off by default)
 * - on_wildcard: NGX_CONF_UNSET (off by default)
 * - auth_policy: NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW
 * - auth_cookies: NULL (no patterns configured)
 * - generate_etag: 1 (on by default)
 * - conditional_requests: NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT
 * - log_verbosity: NGX_HTTP_MARKDOWN_LOG_INFO
 * - buffer_chunked: 1 (on by default)
 * - stream_types: NULL (no exclusions by default)
 * - auto_decompress: 1 (on by default)
 */
typedef struct {
    ngx_flag_t   enabled;              /* markdown_filter on|off */
    size_t       max_size;             /* markdown_max_size (default: 10MB) */
    ngx_msec_t   timeout;              /* markdown_timeout (default: 5000ms) */
    ngx_uint_t   on_error;             /* markdown_on_error pass|reject (default: pass) */
    ngx_uint_t   flavor;               /* markdown_flavor commonmark|gfm (default: commonmark) */
    ngx_flag_t   token_estimate;       /* markdown_token_estimate on|off (default: off) */
    ngx_flag_t   front_matter;         /* markdown_front_matter on|off (default: off) */
    ngx_flag_t   on_wildcard;          /* markdown_on_wildcard on|off (default: off) */
    ngx_uint_t   auth_policy;          /* markdown_auth_policy allow|deny (default: allow) */
    ngx_array_t *auth_cookies;         /* markdown_auth_cookies patterns (default: NULL) */
    ngx_flag_t   generate_etag;        /* markdown_etag on|off (default: on) */
    ngx_uint_t   conditional_requests; /* markdown_conditional_requests mode (default: full_support) */
    ngx_uint_t   log_verbosity;        /* markdown_log_verbosity error|warn|info|debug (default: info) */
    ngx_flag_t   buffer_chunked;       /* markdown_buffer_chunked on|off (default: on) */
    ngx_array_t *stream_types;         /* markdown_stream_types exclusion list (default: NULL) */
    ngx_flag_t   auto_decompress;      /* markdown_auto_decompress on|off (default: on) */
} ngx_http_markdown_conf_t;

/*
 * Response buffer structure
 *
 * This structure accumulates upstream response body chunks before conversion.
 * It enforces size limits during buffering to prevent resource exhaustion.
 */
typedef struct {
    u_char      *data;      /* Buffer data */
    size_t       size;      /* Current size (bytes used) */
    size_t       capacity;  /* Current allocated capacity (bytes) */
    size_t       max_size;  /* Maximum allowed size (bytes) */
    ngx_pool_t  *pool;      /* Request pool for cleanup registration/logging */
} ngx_http_markdown_buffer_t;

/*
 * Request context structure
 *
 * This structure maintains per-request state for the Markdown filter.
 */
typedef struct {
    ngx_http_request_t          *request;
    ngx_chain_t                 *in;           /* Input chain */
    ngx_chain_t                 *out;          /* Output chain */
    ngx_http_markdown_buffer_t   buffer;       /* Response buffer */
    ngx_flag_t                   buffer_initialized;
    ngx_flag_t                   eligible;     /* Eligible for conversion */
    ngx_flag_t                   headers_forwarded; /* Whether downstream headers were sent */
    ngx_flag_t                   conversion_attempted;
    ngx_flag_t                   conversion_succeeded;
    
    /* Decompression state */
    ngx_http_markdown_compression_type_e  compression_type;    /* Detected compression type */
    ngx_flag_t                            decompression_needed; /* Whether decompression is needed */
    ngx_flag_t                            decompression_done;   /* Whether decompression completed */
    size_t                                compressed_size;      /* Size before decompression */
    size_t                                decompressed_size;    /* Size after decompression */
} ngx_http_markdown_ctx_t;

/*
 * Metrics structure for observability
 *
 * This structure tracks conversion operations for monitoring and troubleshooting.
 * All counters use atomic operations for thread-safe updates across NGINX workers.
 *
 * Requirements: FR-09.8, FR-13.4
 *
 * Minimum Observability Fields (v1 Required):
 * - conversion_triggered: Total number of conversion attempts
 * - conversion_result: Counts by result type (success, failure, bypassed)
 * - failure_reason: Counts by failure category (conversion_error, resource_limit, system_error)
 * - conversion_time_ms: Sum of conversion times for averaging
 * - input_size_bytes: Sum of input sizes
 * - output_size_bytes: Sum of output sizes
 *
 * Usage Example:
 *
 *   On conversion attempt:
 *   ngx_atomic_fetch_add(&metrics->conversions_attempted, 1);
 *
 *   On success:
 *   ngx_atomic_fetch_add(&metrics->conversions_succeeded, 1);
 *   ngx_atomic_fetch_add(&metrics->input_bytes, html_len);
 *   ngx_atomic_fetch_add(&metrics->output_bytes, markdown_len);
 *   ngx_atomic_fetch_add(&metrics->conversion_time_sum_ms, elapsed_ms);
 *
 *   On failure:
 *   ngx_atomic_fetch_add(&metrics->conversions_failed, 1);
 *   switch (category) {
 *       case NGX_HTTP_MARKDOWN_ERROR_CONVERSION:
 *           ngx_atomic_fetch_add(&metrics->failures_conversion, 1);
 *           break;
 *       case NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT:
 *           ngx_atomic_fetch_add(&metrics->failures_resource_limit, 1);
 *           break;
 *       case NGX_HTTP_MARKDOWN_ERROR_SYSTEM:
 *           ngx_atomic_fetch_add(&metrics->failures_system, 1);
 *           break;
 *   }
 *
 *   On bypass (ineligible request):
 *   ngx_atomic_fetch_add(&metrics->conversions_bypassed, 1);
 *
 * Thread Safety:
 * - All fields use ngx_atomic_t for lock-free atomic operations
 * - Safe to update from multiple NGINX worker processes concurrently
 * - No mutex or spinlock required
 *
 * Memory Layout:
 * - Structure should be allocated in shared memory for cross-worker visibility
 * - In v1, per-worker metrics are acceptable (simpler implementation)
 * - Future versions may aggregate across workers
 */
typedef struct {
    /* Conversion attempt tracking */
    ngx_atomic_t  conversions_attempted;    /* Total conversion attempts (conversion_triggered) */
    ngx_atomic_t  conversions_succeeded;    /* Successful conversions (conversion_result=success) */
    ngx_atomic_t  conversions_failed;       /* Failed conversions (conversion_result=failure) */
    ngx_atomic_t  conversions_bypassed;     /* Bypassed/ineligible requests (conversion_result=bypassed) */

    /* Failure classification (failure_reason breakdown) */
    ngx_atomic_t  failures_conversion;      /* Conversion errors (HTML parse, encoding, invalid input) */
    ngx_atomic_t  failures_resource_limit;  /* Resource limit errors (timeout, memory limit) */
    ngx_atomic_t  failures_system;          /* System errors (internal errors, unexpected conditions) */

    /* Performance metrics (optional but recommended) */
    ngx_atomic_t  conversion_time_sum_ms;   /* Sum of conversion times in milliseconds (for averaging) */
    ngx_atomic_t  input_bytes;              /* Sum of input HTML sizes in bytes */
    ngx_atomic_t  output_bytes;             /* Sum of output Markdown sizes in bytes */
    
    /* Decompression metrics */
    ngx_atomic_t  decompressions_attempted;  /* Total decompression attempts */
    ngx_atomic_t  decompressions_succeeded;  /* Successful decompressions */
    ngx_atomic_t  decompressions_failed;     /* Failed decompressions */
    ngx_atomic_t  decompressions_gzip;       /* Gzip decompressions */
    ngx_atomic_t  decompressions_deflate;    /* Deflate decompressions */
    ngx_atomic_t  decompressions_brotli;     /* Brotli decompressions */
} ngx_http_markdown_metrics_t;

/* Module declaration */
extern ngx_module_t ngx_http_markdown_filter_module;

/* Forward declarations for FFI types used in public module interfaces */
struct MarkdownConverterHandle;
struct MarkdownResult;

/*
 * Accept header parser functions
 *
 * These functions implement RFC 9110 content negotiation with tie-break rules.
 */

/* Parse Accept header into structured entries */
ngx_int_t ngx_http_markdown_parse_accept(ngx_http_request_t *r, ngx_str_t *accept,
    ngx_array_t *entries);

/* Sort Accept entries by precedence (q-value, specificity, order) */
void ngx_http_markdown_sort_accept_entries(ngx_array_t *entries);

/* Determine if request should be converted based on Accept header */
ngx_int_t ngx_http_markdown_should_convert(ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf);

/*
 * Response buffer functions
 *
 * These functions manage buffering of upstream response bodies.
 */

/* Initialize response buffer with size limit */
ngx_int_t ngx_http_markdown_buffer_init(ngx_http_markdown_buffer_t *buf,
    size_t max_size, ngx_pool_t *pool);

/* Pre-reserve buffer capacity (bounded by max_size) when size hints are available */
ngx_int_t ngx_http_markdown_buffer_reserve(ngx_http_markdown_buffer_t *buf,
    size_t capacity_hint);

/* Append data to buffer with size limit checking */
ngx_int_t ngx_http_markdown_buffer_append(ngx_http_markdown_buffer_t *buf,
    u_char *data, size_t len);

/*
 * Error classification
 *
 * These enums and functions classify conversion failures into categories
 * for logging and metrics (FR-09.5, FR-09.6, FR-09.7).
 */

/* Error category enum */
typedef enum {
    NGX_HTTP_MARKDOWN_ERROR_CONVERSION,      /* HTML parsing errors, invalid input, conversion logic failures */
    NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,  /* Size limits exceeded, timeout exceeded */
    NGX_HTTP_MARKDOWN_ERROR_SYSTEM           /* Memory allocation failures, converter not initialized */
} ngx_http_markdown_error_category_t;

/* Map Rust error code to error category */
ngx_http_markdown_error_category_t ngx_http_markdown_classify_error(uint32_t error_code);

/* Get human-readable string for error category */
const ngx_str_t *ngx_http_markdown_error_category_string(
    ngx_http_markdown_error_category_t category);

/*
 * Response eligibility validation
 *
 * These functions determine if a response should be converted to Markdown.
 */

/* Eligibility result enum */
typedef enum {
    NGX_HTTP_MARKDOWN_ELIGIBLE,                /* Response is eligible for conversion */
    NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD,       /* Not GET/HEAD */
    NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,       /* Not 200 or 206 Partial Content */
    NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE, /* Not text/html */
    NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE,         /* Exceeds max_size */
    NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING,    /* Unbounded streaming (SSE, etc.) */
    NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH,         /* Auth policy denies */
    NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE,        /* Range request (partial content) */
    NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG        /* Disabled by config */
} ngx_http_markdown_eligibility_t;

/* Check if response is eligible for conversion */
ngx_http_markdown_eligibility_t ngx_http_markdown_check_eligibility(
    ngx_http_request_t *r, ngx_http_markdown_conf_t *conf);

/* Get human-readable string for eligibility result */
const ngx_str_t *ngx_http_markdown_eligibility_string(
    ngx_http_markdown_eligibility_t eligibility);

/*
 * Header management functions
 *
 * These functions handle HTTP header updates for Markdown responses.
 */

/* Update response headers after successful conversion */
ngx_int_t ngx_http_markdown_update_headers(ngx_http_request_t *r,
    struct MarkdownResult *result, ngx_http_markdown_conf_t *conf);

/* Remove Content-Encoding header (called after decompression) */
void ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r);

/*
 * Authentication detection and cache control functions
 *
 * These functions detect authenticated requests and modify cache headers
 * to ensure secure caching behavior.
 */

/* Check if request is authenticated (Authorization header or auth cookies) */
ngx_int_t ngx_http_markdown_is_authenticated(ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf);

/* Modify Cache-Control header for authenticated content */
ngx_int_t ngx_http_markdown_modify_cache_control_for_auth(ngx_http_request_t *r);

/*
 * Conditional request handling functions
 *
 * These functions implement If-None-Match and If-Modified-Since support
 * for Markdown variants with configurable behavior.
 */

/*
 * Handle If-None-Match conditional request with configurable behavior.
 *
 * The Rust FFI converter handle is required when conversion is performed to
 * generate a Markdown-variant ETag for comparison.
 */
ngx_int_t ngx_http_markdown_handle_if_none_match(ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf, ngx_http_markdown_ctx_t *ctx,
    struct MarkdownConverterHandle *converter,
    struct MarkdownResult **result);

/* Send 304 Not Modified response */
ngx_int_t ngx_http_markdown_send_304(ngx_http_request_t *r,
    struct MarkdownResult *result);

/*
 * Decompression functions
 *
 * These functions implement automatic decompression of upstream compressed content.
 */

/* Detect compression type from Content-Encoding header */
ngx_http_markdown_compression_type_e
ngx_http_markdown_detect_compression(ngx_http_request_t *r);

/* Decompress gzip/deflate compressed data using zlib */
ngx_int_t
ngx_http_markdown_decompress_gzip(ngx_http_request_t *r,
                                   ngx_http_markdown_compression_type_e type,
                                   ngx_chain_t *in,
                                   ngx_chain_t **out);

/* Decompress brotli compressed data using brotli library */
ngx_int_t
ngx_http_markdown_decompress_brotli(ngx_http_request_t *r,
                                     ngx_chain_t *in,
                                     ngx_chain_t **out);

/* Unified decompression entry function */
ngx_int_t
ngx_http_markdown_decompress(ngx_http_request_t *r,
                              ngx_http_markdown_compression_type_e type,
                              ngx_chain_t *in,
                              ngx_chain_t **out);

#endif /* NGX_HTTP_MARKDOWN_FILTER_MODULE_H */
