#ifndef NGX_HTTP_MARKDOWN_CONFIG_DIRECTIVES_IMPL_H
#define NGX_HTTP_MARKDOWN_CONFIG_DIRECTIVES_IMPL_H

/*
 * Directive registry table.
 *
 * WARNING: This header is an implementation detail of the main translation unit
 * (ngx_http_markdown_filter_module.c). It must NOT be included from any other
 * .c file or used as a standalone compilation unit.
 *
 * This unit is intentionally data-heavy: it maps public directives to their
 * handlers, value setters, and inline usage notes.
 */

/*
 * Module directives
 *
 * These directives control the behavior of the Markdown filter.
 * Each directive includes validation and clear error messages.
 */

#ifdef MARKDOWN_STREAMING_ENABLED
/*
 * Enum table for markdown_streaming_on_error directive.
 *
 * Used by ngx_conf_set_enum_slot to validate and map string
 * values to integer constants.  Invalid values are automatically
 * rejected by the NGINX configuration parser.
 */
static ngx_conf_enum_t
    ngx_http_markdown_streaming_on_error_enum[] = {
    { ngx_string("pass"),
      NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS },
    { ngx_string("reject"),
      NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT },
    { ngx_null_string, 0 }
};
#endif /* MARKDOWN_STREAMING_ENABLED */

static ngx_command_t ngx_http_markdown_filter_commands[] = {
    /*
     * markdown_filter on|off|$variable
     *
     * Enables or disables Markdown conversion for this context.
     * Also supports per-request toggle via nginx variables/complex values.
     * Default: off
     * Context: http, server, location
     *
     * Example:
     *   location /api {
     *       markdown_filter on;
     *   }
     */
    {
        ngx_string("markdown_filter"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_filter,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_max_size <size>
     *
     * Maximum response size to attempt conversion (e.g., 10m, 5k).
     * Responses larger than this will not be converted.
     * Default: 10m (10 megabytes)
     * Context: http, server, location
     *
     * Example:
     *   markdown_max_size 5m;
     */
    {
        ngx_string("markdown_max_size"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_size_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, max_size),
        NULL
    },

    /*
     * markdown_timeout <time>
     *
     * Maximum time to spend on conversion (e.g., 5s, 1000ms).
     * Conversions exceeding this timeout will be aborted.
     * Default: 5s (5 seconds)
     * Context: http, server, location
     *
     * Example:
     *   markdown_timeout 3s;
     */
    {
        ngx_string("markdown_timeout"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_msec_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, timeout),
        NULL
    },

    /*
     * markdown_on_error pass|reject
     *
     * Failure strategy when conversion fails:
     * - pass: Return original HTML (fail-open, default)
     * - reject: Return 502 Bad Gateway (fail-closed)
     * Default: pass
     * Context: http, server, location
     *
     * Example:
     *   markdown_on_error reject;
     */
    {
        ngx_string("markdown_on_error"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_on_error,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_flavor commonmark|gfm
     *
     * Markdown flavor to generate:
     * - commonmark: CommonMark specification (default)
     * - gfm: GitHub Flavored Markdown (includes tables, strikethrough)
     * Default: commonmark
     * Context: http, server, location
     *
     * Example:
     *   markdown_flavor gfm;
     */
    {
        ngx_string("markdown_flavor"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_flavor,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_token_estimate on|off
     *
     * Include X-Markdown-Tokens header with estimated token count.
     * Useful for AI agents to manage context windows.
     * Default: off
     * Context: http, server, location
     *
     * Example:
     *   markdown_token_estimate on;
     */
    {
        ngx_string("markdown_token_estimate"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, token_estimate),
        NULL
    },

    /*
     * markdown_front_matter on|off
     *
     * Include YAML front matter with metadata (title, description, etc.).
     * Default: off
     * Context: http, server, location
     *
     * Example:
     *   markdown_front_matter on;
     */
    {
        ngx_string("markdown_front_matter"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, front_matter),
        NULL
    },

    /*
     * markdown_on_wildcard on|off
     *
     * Convert when Accept header contains wildcards (star/slash-star or text slash star).
     * Default: off (only convert on explicit text/markdown)
     * Context: http, server, location
     *
     * Example:
     *   markdown_on_wildcard on;
     */
    {
        ngx_string("markdown_on_wildcard"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, on_wildcard),
        NULL
    },

    /*
     * markdown_auth_policy allow|deny
     *
     * Policy for converting authenticated requests:
     * - allow: Convert authenticated requests (default)
     * - deny: Skip conversion for authenticated requests
     * Default: allow
     * Context: http, server, location
     *
     * Example:
     *   markdown_auth_policy deny;
     */
    {
        ngx_string("markdown_auth_policy"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_auth_policy,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_auth_cookies <pattern> [<pattern> ...]
     *
     * Cookie name patterns to identify authenticated requests.
     * Supports exact match, prefix match (pattern*), and wildcards.
     * Default: none (only Authorization header detection)
     * Context: http, server, location
     *
     * Example:
     *   markdown_auth_cookies session* auth_token PHPSESSID;
     */
    {
        ngx_string("markdown_auth_cookies"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_1MORE,
        ngx_http_markdown_auth_cookies,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_etag on|off
     *
     * Generate ETag header for Markdown variants.
     * ETags are computed from the Markdown output for proper caching.
     * Default: on
     * Context: http, server, location
     *
     * Example:
     *   markdown_etag off;
     */
    {
        ngx_string("markdown_etag"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, generate_etag),
        NULL
    },

    /*
     * markdown_conditional_requests full_support|if_modified_since_only|disabled
     *
     * Conditional request support mode:
     * - full_support: Support If-None-Match and If-Modified-Since (default)
     * - if_modified_since_only: Only support If-Modified-Since (performance)
     * - disabled: No conditional request support for Markdown variants
     * Default: full_support
     * Context: http, server, location
     *
     * Example:
     *   markdown_conditional_requests if_modified_since_only;
     */
    {
        ngx_string("markdown_conditional_requests"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_conditional_requests,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_log_verbosity error|warn|info|debug
     *
     * Module-local verbosity filter for module-generated logs.
     * NGINX's global error_log level still applies.
     * Default: info
     * Context: http, server, location
     *
     * Example:
     *   markdown_log_verbosity warn;
     */
    {
        ngx_string("markdown_log_verbosity"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_log_verbosity,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_buffer_chunked on|off
     *
     * Buffer and convert chunked Transfer-Encoding responses.
     * When off, chunked responses are passed through without conversion.
     * Default: on
     * Context: http, server, location
     *
     * Example:
     *   markdown_buffer_chunked off;
     */
    {
        ngx_string("markdown_buffer_chunked"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, buffer_chunked),
        NULL
    },

    /*
     * markdown_stream_types <type> [<type> ...]
     *
     * Content types to exclude from conversion (streaming responses).
     * These content types will never be converted, even if eligible.
     * Default: none (no exclusions)
     * Context: http, server, location
     *
     * Example:
     *   markdown_stream_types text/event-stream application/x-ndjson;
     */
    {
        ngx_string("markdown_stream_types"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_1MORE,
        ngx_http_markdown_stream_types,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_trust_forwarded_headers on|off
     *
     * Controls whether X-Forwarded-Proto and X-Forwarded-Host headers
     * are used for base URL construction in Markdown output.
     *
     * Security: When off (default), only the NGINX request schema and
     * server header are used, preventing client-supplied header injection
     * that could poison relative URLs in the Markdown output.
     *
     * Enable this only when NGINX sits behind a trusted reverse proxy
     * that sets and overwrites these headers. The proxy must strip
     * X-Forwarded-* headers from untrusted clients.
     *
     * Default: off
     * Context: http, server, location
     *
     * Example:
     *   # Only enable behind a trusted reverse proxy
     *   markdown_trust_forwarded_headers on;
     */
    {
        ngx_string("markdown_trust_forwarded_headers"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.trust_forwarded_headers),
        NULL
    },

    /*
     * markdown_large_body_threshold off|<size>
     *
     * Threshold for routing responses to the incremental
     * processing path. Responses with Content-Length at or
     * above this value use the incremental path.
     * Default: off (all responses use full-buffer path)
     * Context: http, server, location
     *
     * Example:
     *   markdown_large_body_threshold 512k;
     */
    {
        ngx_string("markdown_large_body_threshold"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_large_body_threshold,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_metrics_shm_size <size>
     *
     * Size of the shared-memory zone used to aggregate metrics across workers.
     * Default: 8 * ngx_pagesize
     * Context: http
     *
     * Example:
     *   markdown_metrics_shm_size 128k;
     */
    {
        ngx_string("markdown_metrics_shm_size"),
        NGX_HTTP_MAIN_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_size_slot,
        NGX_HTTP_MAIN_CONF_OFFSET,
        offsetof(ngx_http_markdown_main_conf_t, metrics_shm_size),
        NULL
    },

    /*
     * markdown_metrics_format auto|prometheus
     *
     * Controls the output format of the markdown_metrics endpoint.
     * - auto: JSON or plain-text based on Accept header (default)
     * - prometheus: Prometheus text exposition format for non-JSON
     * Default: auto
     * Context: http, server, location
     *
     * Example:
     *   markdown_metrics_format prometheus;
     */
    {
        ngx_string("markdown_metrics_format"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_metrics_format,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_metrics
     *
     * Enables metrics exposure endpoint at this location.
     * Returns metrics in plain text or JSON format based on Accept header.
     * Access is restricted to localhost (127.0.0.1, ::1) by default for security.
     * Default: off
     * Context: location only
     *
     * Example:
     *   location /markdown-metrics {
     *       markdown_metrics;
     *   }
     *
     * Response formats:
     * - Plain text (default): Accept: text/plain or no Accept header
     * - JSON: Accept: application/json
     *
     * Security: Only accessible from localhost by default.
     * NGINX allow/deny directives can further restrict access, but they do
     * not broaden access beyond localhost.
     */
    {
        ngx_string("markdown_metrics"),
        NGX_HTTP_LOC_CONF|NGX_CONF_NOARGS,
        ngx_http_markdown_metrics_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

#ifdef MARKDOWN_STREAMING_ENABLED
    /*
     * markdown_streaming_engine off|on|auto|$variable
     *
     * Streaming engine selection mode.
     * Supports per-request variable-driven rollout.
     * Default: off (all requests use full-buffer path)
     * Context: http, server, location
     *
     * Example:
     *   markdown_streaming_engine auto;
     *   markdown_streaming_engine $streaming_flag;
     */
    {
        ngx_string("markdown_streaming_engine"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_streaming_engine,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_streaming_budget <size>
     *
     * Memory budget for streaming conversion (passed to Rust).
     * Default: 2m (2 megabytes)
     * Context: http, server, location
     *
     * Example:
     *   markdown_streaming_budget 4m;
     */
    {
        ngx_string("markdown_streaming_budget"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_size_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t,
                 streaming_budget),
        NULL
    },

    /*
     * markdown_streaming_on_error pass|reject
     *
     * Failure strategy for streaming Pre_Commit_Phase errors:
     * - pass: Fail-open, return original HTML (default)
     * - reject: Fail-closed, return error
     *
     * This directive is independent of markdown_on_error which
     * controls the full-buffer path.  Post_Commit_Phase errors
     * are always fail-closed regardless of this setting.
     *
     * Default: pass
     * Context: http, server, location
     *
     * Example:
     *   markdown_streaming_on_error reject;
     */
    {
        ngx_string("markdown_streaming_on_error"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_enum_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t,
                 streaming_on_error),
        &ngx_http_markdown_streaming_on_error_enum
    },
#endif /* MARKDOWN_STREAMING_ENABLED */

    ngx_null_command
};

#endif /* NGX_HTTP_MARKDOWN_CONFIG_DIRECTIVES_IMPL_H */
