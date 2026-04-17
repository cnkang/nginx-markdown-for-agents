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

struct MarkdownOptions;

/*
 * Processing path constants for threshold router
 */
#define NGX_HTTP_MARKDOWN_PATH_FULLBUFFER   0  /* Full-buffer path */
#define NGX_HTTP_MARKDOWN_PATH_INCREMENTAL  1  /* Incremental path */
#define NGX_HTTP_MARKDOWN_PATH_STREAMING    2  /* Streaming path */

#ifdef MARKDOWN_STREAMING_ENABLED
/*
 * Streaming engine mode constants
 */
#define NGX_HTTP_MARKDOWN_STREAMING_ENGINE_OFF   0
#define NGX_HTTP_MARKDOWN_STREAMING_ENGINE_ON    1
#define NGX_HTTP_MARKDOWN_STREAMING_ENGINE_AUTO  2

/*
 * Streaming commit state constants
 */
#define NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE   0
#define NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST  1

/*
 * Default streaming budget: 2 MiB
 */
#define NGX_HTTP_MARKDOWN_STREAMING_BUDGET_DEFAULT \
    (2 * 1024 * 1024)

/*
 * Streaming on_error policy constants
 *
 * Controls Pre_Commit_Phase failure behavior for the
 * markdown_streaming_on_error directive.
 */
#define NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS    0
#define NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT  1
#endif /* MARKDOWN_STREAMING_ENABLED */

/*
 * Threshold off sentinel — used in merge and path selection logic.
 */
#define NGX_HTTP_MARKDOWN_THRESHOLD_OFF     0

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
 * Configuration constants for metrics_format directive
 */
#define NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO        0  /* JSON or plain-text (default) */
#define NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS   1  /* Prometheus text exposition */

/*
 * Configuration source for markdown_filter directive
 */
#define NGX_HTTP_MARKDOWN_ENABLED_UNSET    0  /* Not configured in this scope */
#define NGX_HTTP_MARKDOWN_ENABLED_STATIC   1  /* markdown_filter on|off */
#define NGX_HTTP_MARKDOWN_ENABLED_COMPLEX  2  /* markdown_filter <variable/expr> */

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
 * - enabled_source: NGX_HTTP_MARKDOWN_ENABLED_UNSET (inherit from parent)
 * - enabled_complex: NULL
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
    ngx_flag_t   enabled;              /* markdown_filter static resolved value */
    ngx_uint_t   enabled_source;       /* markdown_filter source (static|complex|unset) */
    ngx_http_complex_value_t *enabled_complex; /* markdown_filter variable/complex expression */
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
    size_t       large_body_threshold; /* markdown_large_body_threshold (NGX_HTTP_MARKDOWN_THRESHOLD_OFF = off) */

    /*
     * Operational settings.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_conf_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_flag_t   trust_forwarded_headers; /* markdown_trust_forwarded_headers on|off (default: off) */
        ngx_uint_t   metrics_format;       /* markdown_metrics_format auto|prometheus (default: auto) */
    } ops;

#ifdef MARKDOWN_STREAMING_ENABLED
    ngx_http_complex_value_t  *streaming_engine;  /* markdown_streaming_engine (complex value) */
    size_t                     streaming_budget;   /* markdown_streaming_budget (default: 2m) */
    ngx_uint_t                 streaming_on_error; /* markdown_streaming_on_error pass|reject */
    ngx_flag_t                 streaming_shadow;   /* markdown_streaming_shadow on|off */
#endif
} ngx_http_markdown_conf_t;

/*
 * Main configuration structure
 *
 * Holds process-wide shared state that is initialized once during
 * configuration parsing and then reused by all worker processes.
 */
typedef struct {
    ngx_shm_zone_t *metrics_shm_zone;  /* Shared-memory zone for cross-worker metrics */
    size_t          metrics_shm_size;  /* Configured metrics SHM size (default: 8 pages) */
} ngx_http_markdown_main_conf_t;

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
 * Error classification
 *
 * These enums and functions classify conversion failures into categories
 * for logging and metrics (FR-09.5, FR-09.6, FR-09.7).
 *
 * Defined before ngx_http_markdown_ctx_t because the context struct
 * contains a last_error_category field of this type.
 */

/* Error category enum */
typedef enum {
    NGX_HTTP_MARKDOWN_ERROR_CONVERSION,      /* HTML parsing errors, invalid input, conversion logic failures */
    NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,  /* Size limits exceeded, timeout exceeded */
    NGX_HTTP_MARKDOWN_ERROR_SYSTEM           /* Memory allocation failures, converter not initialized */
} ngx_http_markdown_error_category_t;

/*
 * Response eligibility validation
 *
 * Defined before ngx_http_markdown_ctx_t because function prototypes
 * referencing this type appear before the context struct definition.
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
    ngx_flag_t                   filter_enabled; /* Cached markdown_filter decision from header phase */
    ngx_flag_t                   buffer_initialized;
    ngx_flag_t                   eligible;     /* Eligible for conversion */
    ngx_flag_t                   headers_forwarded; /* Whether downstream headers were sent */
    time_t                       source_last_modified_time; /* Preserved upstream Last-Modified */
    ngx_flag_t                   has_last_modified_time;     /* Whether Last-Modified was present */
    ngx_flag_t                   conversion_attempted;
    ngx_flag_t                   conversion_succeeded;
    ngx_flag_t                   bypass_counted; /* Whether conversions_bypassed was incremented */
    
    /* Threshold router path selection (NGX_HTTP_MARKDOWN_PATH_FULLBUFFER or NGX_HTTP_MARKDOWN_PATH_INCREMENTAL) */
    ngx_uint_t                   processing_path;

    /*
     * Decompression state.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_ctx_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_http_markdown_compression_type_e  type;      /* Detected compression type */
        ngx_flag_t                            needed;    /* Whether decompression is needed */
        ngx_flag_t                            done;      /* Whether decompression completed */
        size_t                                compressed_size;   /* Size before decompression */
        size_t                                decompressed_size; /* Size after decompression */
    } decompression;

    /* Last error category from conversion failure (for decision log) */
    ngx_http_markdown_error_category_t    last_error_category;
    ngx_flag_t                            has_error_category;

#ifdef MARKDOWN_STREAMING_ENABLED
    /*
     * Streaming state sub-struct.
     *
     * Grouped to comply with SonarCloud c:S1820
     * 20-field limit.
     */
    struct {
        /* Streaming converter handle (Rust opaque pointer) */
        struct StreamingConverterHandle  *handle;

        /* Commit state: PRE or POST */
        ngx_uint_t                        commit_state;

        /* Pending output chain for backpressure */
        ngx_chain_t                      *pending_output;

        /* Incremental decompressor state */
        void                             *decompressor;

        /* Per-request statistics */
        ngx_uint_t                        chunks_processed;
        ngx_uint_t                        flushes_sent;
        size_t                            total_input_bytes;
        size_t                            total_output_bytes;

        /* TTFB tracking (from first feed to first non-empty output) */
        ngx_msec_t                        feed_start_ms;
        ngx_flag_t                        ttfb_recorded;

        /* Pending output chain has non-empty data (for TTFB resume path) */
        ngx_flag_t                        pending_has_data;

        /* Pre-Commit prebuffer for fallback */
        ngx_http_markdown_buffer_t        prebuffer;
        size_t                            prebuffer_limit;
        ngx_flag_t                        prebuffer_initialized;

        /* Reused fail-open restoration slots (avoid per-call allocations) */
        ngx_buf_t                       **failopen_consumed_bufs;
        u_char                          **failopen_consumed_pos;
        ngx_uint_t                        failopen_consumed_capacity;
        ngx_uint_t                        failopen_consumed_count;

        /* Deferred terminal last_buf (backpressure during finalize) */
        ngx_flag_t                        finalize_pending_lastbuf;

        /* Metrics deferred for terminal last_buf (backpressure on
         * terminal send — set when send_output(last_buf=1) returns
         * NGX_AGAIN, cleared when resume drain succeeds or fails). */
        ngx_flag_t                        pending_terminal_metrics;

        /* Post-commit failure metrics have been recorded for this request. */
        ngx_flag_t                        failure_recorded;

        /* Continue finalize() after tail-output backpressure drains */
        ngx_flag_t                        finalize_after_pending;
    } streaming;
#endif
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
 * - No per-request mutex or spinlock required for counter updates
 *
 * Memory Layout:
 * - Structure is allocated in shared memory for cross-worker visibility
 * - All workers update the same counters, so the metrics endpoint reports
 *   aggregate values instead of worker-local snapshots
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
    ngx_atomic_t  conversion_latency_le_10ms;    /* Completed conversions <= 10ms */
    ngx_atomic_t  conversion_latency_le_100ms;   /* Completed conversions <= 100ms */
    ngx_atomic_t  conversion_latency_le_1000ms;  /* Completed conversions <= 1000ms */
    ngx_atomic_t  conversion_latency_gt_1000ms;  /* Completed conversions > 1000ms */

    /*
     * Decompression metrics.
     *
     * Grouped into an anonymous sub-struct so that the parent
     * ngx_http_markdown_metrics_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     * The JSON/text output format is unaffected — keys are still
     * emitted as flat "decompressions_*" names.
     */
    struct {
        ngx_atomic_t  attempted;   /* Total decompression attempts */
        ngx_atomic_t  succeeded;   /* Successful decompressions */
        ngx_atomic_t  failed;      /* Failed decompressions */
        ngx_atomic_t  gzip;        /* Gzip decompressions */
        ngx_atomic_t  deflate;     /* Deflate decompressions */
        ngx_atomic_t  brotli;      /* Brotli decompressions */
    } decompressions;

    /*
     * Path hit metrics (threshold router).
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_metrics_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_atomic_t  fullbuffer;      /* Requests routed to full-buffer path */
        ngx_atomic_t  incremental;     /* Requests routed to incremental path */
#ifdef MARKDOWN_STREAMING_ENABLED
        ngx_atomic_t  streaming;       /* Requests routed to streaming path */
#endif
    } path_hits;

    /*
     * Total requests that entered the decision chain.
     *
     * Incremented in the header filter after scope enablement
     * check passes.  This is the denominator for conversion
     * rate calculations.
     */
    ngx_atomic_t  requests_entered;

#ifdef MARKDOWN_STREAMING_ENABLED
    /*
     * Streaming metrics sub-struct.
     *
     * Grouped to comply with SonarCloud c:S1820
     * 20-field limit.
     */
    struct {
        ngx_atomic_t  requests_total;          /* Entered streaming path */
        ngx_atomic_t  fallback_total;          /* Pre-Commit fallbacks */
        ngx_atomic_t  succeeded_total;         /* Streaming successes */
        ngx_atomic_t  failed_total;            /* Streaming failures */
        ngx_atomic_t  postcommit_error_total;  /* Post-Commit errors */
        ngx_atomic_t  precommit_failopen_total;  /* Pre-Commit fail-open */
        ngx_atomic_t  precommit_reject_total;    /* Pre-Commit fail-closed */
        ngx_atomic_t  budget_exceeded_total;     /* Memory budget exceeded */
        ngx_atomic_t  shadow_total;              /* Shadow mode runs */
        ngx_atomic_t  shadow_diff_total;         /* Shadow output diffs */
        ngx_atomic_t  last_ttfb_ms;              /* Last streaming TTFB (milliseconds) */
        ngx_atomic_t  last_peak_memory_bytes;    /* Last streaming peak estimate (bytes; not RSS) */
    } streaming;
#endif

    /*
     * Skip counters by reason code.
     *
     * Each field corresponds to a specific eligibility check
     * failure.  Grouped into a sub-struct so that the parent
     * ngx_http_markdown_metrics_t stays within the 20-field
     * limit enforced by static analysis (SonarCloud rule
     * c:S1820).
     */
    struct {
        ngx_atomic_t  config;        /* SKIP_CONFIG */
        ngx_atomic_t  method;        /* SKIP_METHOD */
        ngx_atomic_t  status;        /* SKIP_STATUS */
        ngx_atomic_t  content_type;  /* SKIP_CONTENT_TYPE */
        ngx_atomic_t  size;          /* SKIP_SIZE */
        ngx_atomic_t  streaming;     /* SKIP_STREAMING */
        ngx_atomic_t  auth;          /* SKIP_AUTH */
        ngx_atomic_t  range;         /* SKIP_RANGE */
        ngx_atomic_t  accept;        /* SKIP_ACCEPT */
    } skips;

    /*
     * Fail-open counter: conversion failed but original HTML
     * served due to markdown_on_error pass.
     */
    ngx_atomic_t  failopen_count;

    /*
     * Estimated cumulative token savings across all successful
     * conversions.  Only non-zero when markdown_token_estimate
     * is enabled.  Value is an approximation.
     */
    ngx_atomic_t  estimated_token_savings;
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
    const ngx_http_markdown_conf_t *conf);

/* Resolve markdown_filter on/off state for the current request */
ngx_flag_t ngx_http_markdown_is_enabled(ngx_http_request_t *r,
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
    const u_char *data, size_t len);

/*
 * Error classification functions
 *
 * (Enum ngx_http_markdown_error_category_t is defined above,
 * before ngx_http_markdown_ctx_t.)
 */

/* Map Rust error code to error category */
ngx_http_markdown_error_category_t ngx_http_markdown_classify_error(uint32_t error_code);

/* Get human-readable string for error category */
const ngx_str_t *ngx_http_markdown_error_category_string(
    ngx_http_markdown_error_category_t category);

/*
 * Response eligibility validation functions
 *
 * (Enum ngx_http_markdown_eligibility_t is defined above,
 * before ngx_http_markdown_ctx_t.)
 */

/* Check if response is eligible for conversion */
ngx_http_markdown_eligibility_t ngx_http_markdown_check_eligibility(
    const ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    ngx_flag_t filter_enabled);

/* Get human-readable string for eligibility result */
const ngx_str_t *ngx_http_markdown_eligibility_string(
    ngx_http_markdown_eligibility_t eligibility);

/*
 * Reason code lookup functions
 *
 * These functions map existing eligibility enum values and error categories
 * to stable uppercase snake_case reason code strings.  The returned strings
 * are shared between decision log entries and Prometheus metrics labels so
 * that operators can correlate logs with metric counters without translating
 * between different vocabularies.
 */

/* Map eligibility enum to reason code string */
const ngx_str_t *ngx_http_markdown_reason_from_eligibility(
    ngx_http_markdown_eligibility_t eligibility, const ngx_log_t *log);

/* Map error category enum to failure reason code string */
const ngx_str_t *ngx_http_markdown_reason_from_error_category(
    ngx_http_markdown_error_category_t category, const ngx_log_t *log);

/* Return the ELIGIBLE_CONVERTED reason code */
const ngx_str_t *ngx_http_markdown_reason_converted(void);

/* Return the ELIGIBLE_FAILED_OPEN reason code */
const ngx_str_t *ngx_http_markdown_reason_failed_open(void);

/* Return the ELIGIBLE_FAILED_CLOSED reason code */
const ngx_str_t *ngx_http_markdown_reason_failed_closed(void);

/* Return the SKIP_ACCEPT reason code (not in eligibility enum) */
const ngx_str_t *ngx_http_markdown_reason_skip_accept(void);

#ifdef MARKDOWN_STREAMING_ENABLED
/* Streaming reason code accessors */
const ngx_str_t *ngx_http_markdown_reason_engine_streaming(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_convert(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_fallback(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_fail_postcommit(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_skip_unsupported(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_budget_exceeded(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_precommit_failopen(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_precommit_reject(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_shadow(void);
#endif /* MARKDOWN_STREAMING_ENABLED */

/*
 * Header management functions
 *
 * These functions handle HTTP header updates for Markdown responses.
 */

/* Update response headers after successful conversion */
ngx_int_t ngx_http_markdown_update_headers(ngx_http_request_t *r,
    const struct MarkdownResult *result, const ngx_http_markdown_conf_t *conf);

/* Remove Content-Encoding header (called after decompression) */
void ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r);

/* Shared header helpers used by both full-buffer and streaming paths */
#define NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL  "text/markdown; charset=utf-8"
extern u_char ngx_http_markdown_content_type[];
#define NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN \
    (sizeof(NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL) - 1)
ngx_int_t ngx_http_markdown_add_vary_accept(ngx_http_request_t *r);
ngx_int_t ngx_http_markdown_set_etag(ngx_http_request_t *r,
    const u_char *etag, size_t etag_len);

/*
 * Authentication detection and cache control functions
 *
 * These functions detect authenticated requests and modify cache headers
 * to ensure secure caching behavior.
 */

ngx_int_t ngx_http_markdown_is_authenticated(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf);
ngx_int_t ngx_http_markdown_modify_cache_control_for_auth(
    ngx_http_request_t *r);

/*
 * Shared conversion-option helpers
 *
 * These helpers populate Rust FFI options consistently for both the normal
 * conversion path and conditional-request revalidation.
 */
ngx_int_t ngx_http_markdown_construct_base_url(ngx_http_request_t *r,
    ngx_pool_t *pool, ngx_str_t *base_url);
ngx_int_t ngx_http_markdown_prepare_conversion_options(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf, struct MarkdownOptions *options);

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
    const ngx_http_markdown_conf_t *conf, const ngx_http_markdown_ctx_t *ctx,
    struct MarkdownConverterHandle *converter,
    struct MarkdownResult **result);

/* Send 304 Not Modified response */
ngx_int_t ngx_http_markdown_send_304(ngx_http_request_t *r,
    const struct MarkdownResult *result);

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
                                   const ngx_chain_t *in,
                                   ngx_chain_t **out);

/* Decompress brotli compressed data using brotli library */
ngx_int_t
ngx_http_markdown_decompress_brotli(ngx_http_request_t *r,
                                    const ngx_chain_t *in,
                                    ngx_chain_t **out);

/* Unified decompression entry function */
ngx_int_t
ngx_http_markdown_decompress(ngx_http_request_t *r,
                              ngx_http_markdown_compression_type_e type,
                              const ngx_chain_t *in,
                              ngx_chain_t **out);

#endif /* NGX_HTTP_MARKDOWN_FILTER_MODULE_H */
