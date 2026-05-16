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

static ngx_conf_enum_t
    ngx_http_markdown_llm_provider_values[] = {
    { ngx_string("default"),           0 },
    { ngx_string("openai-gpt"),        1 },
    { ngx_string("anthropic-claude"),  2 },
    { ngx_string("google-gemini"),     3 },
    { ngx_string("meta-llama"),        4 },
    { ngx_null_string, 0 }
};

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
     * markdown_flavor commonmark|gfm|mdx|org-mode
     *
     * Markdown flavor to generate:
     * - commonmark: CommonMark specification (default)
     * - gfm: GitHub Flavored Markdown (includes tables, strikethrough)
     * - mdx: MDX (Markdown + JSX, components preserved as-is)
     * - org-mode: Emacs Org-mode outline format
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
        offsetof(ngx_http_markdown_conf_t, policy.generate_etag),
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
     * markdown_content_types <type> [<type> ...]
     *
     * Content types eligible for Markdown conversion (positive allowlist).
     * Uses prefix + boundary-char matching: "text/html" matches
     * "text/html" and "text/html; charset=utf-8" but not "text/htmlx".
     *
     * Default: text/html (backward compatible)
     * Context: http, server, location
     *
     * Example:
     *   markdown_content_types text/html application/xhtml+xml;
     */
    {
        ngx_string("markdown_content_types"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_1MORE,
        ngx_http_markdown_content_types,
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

    /*
     * markdown_metrics_per_path on|off
     *
     * Enable per-URL-path metrics tracking.  When enabled, the top-N
     * most-hit URI paths are tracked individually alongside global
     * aggregates.  Per-path data is exposed in the metrics endpoint
     * under the "per_path" key.
     *
     * Default: off
     * Context: http, server, location
     *
     * Example:
     *   markdown_metrics_per_path on;
     */
    {
        ngx_string("markdown_metrics_per_path"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.metrics_per_path),
        NULL
    },

    /*
     * markdown_metrics_per_path_cardinality <number>
     *
     * Maximum number of distinct URI paths tracked individually in
     * the per-path RB-tree.  When this limit is reached, further
     * unique paths are counted in the overflow_count aggregate
     * and appear under the "__other__" pseudo-path in output.
     *
     * This is a global (http-level) setting because the per-path
     * limit is stored in shared memory and applies across all
     * server and location blocks.  Configuring it at server or
     * location level would be silently ignored since only the
     * http-level value is wired into the SHM metrics struct.
     *
     * Default: 100
     * Context: http
     *
     * Example:
     *   markdown_metrics_per_path_cardinality 200;
     */
    {
        ngx_string("markdown_metrics_per_path_cardinality"),
        NGX_HTTP_MAIN_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_num_slot,
        NGX_HTTP_MAIN_CONF_OFFSET,
        offsetof(ngx_http_markdown_main_conf_t, metrics_per_path_cardinality),
        NULL
    },

#ifdef MARKDOWN_STREAMING_ENABLED
    /*
     * markdown_streaming_engine off|on|auto|$variable
     *
     * Streaming engine selection mode.
     * Supports per-request variable-driven rollout.
     * Default: auto (per-request selection based on
     *          markdown_streaming_auto_threshold)
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
        offsetof(ngx_http_markdown_conf_t, streaming.budget),
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
        offsetof(ngx_http_markdown_conf_t, streaming.on_error),
        &ngx_http_markdown_streaming_on_error_enum
    },

    /*
     * markdown_streaming_shadow on|off
     *
     * Enable shadow mode: run both full-buffer and streaming
     * engines, return full-buffer result to client, compare
     * outputs and record differences in debug log and metrics.
     *
     * Default: off
     * Context: http, server, location
     *
     * Example:
     *   markdown_streaming_shadow on;
     */
    {
        ngx_string("markdown_streaming_shadow"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, streaming.shadow),
        NULL
    },

    /*
     * markdown_streaming_auto_threshold <size>
     *
     * Content-Length threshold for auto mode engine selection.
     * When markdown_streaming_engine is auto, responses with
     * Content-Length >= this value use streaming; smaller
     * responses use full-buffer. Chunked responses always
     * use streaming in auto mode.
     *
     * Default: 32k
     * Context: http, server, location
     *
     * Example:
     *   markdown_streaming_auto_threshold 64k;
     */
    {
        ngx_string("markdown_streaming_auto_threshold"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_size_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, streaming.auto_threshold),
        NULL
    },
#endif /* MARKDOWN_STREAMING_ENABLED */

    /*
     * markdown_prune_noise on|off
     *
     * Enable or disable noise region pruning at runtime.
     * When enabled, structural HTML regions matching prune
     * selectors are excluded from Markdown output.
     *
     * Default: on (v0.6.0)
     * Context: http, server, location
     *
     * Example:
     *   markdown_prune_noise off;
     */
    {
        ngx_string("markdown_prune_noise"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, advanced.prune_noise),
        NULL
    },

    /*
     * markdown_prune_selectors <string>
     *
     * Space-separated tag names for regions to prune.
     * Replaces built-in defaults when set.
     * Built-in defaults: nav footer aside
     *
     * Default: built-in defaults
     * Context: http, server, location
     *
     * Example:
     *   markdown_prune_selectors "nav footer aside sidebar";
     */
    {
        ngx_string("markdown_prune_selectors"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_str_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, advanced.prune_selectors),
        NULL
    },

    /*
     * markdown_prune_protection_selectors <string>
     *
     * Space-separated tag names for regions to protect
     * from pruning. Protection wins over prune: an element
     * matching both is kept.
     *
     * Default: empty (no protection)
     * Context: http, server, location
     *
     * Example:
     *   markdown_prune_protection_selectors "nav";
     */
    {
        ngx_string("markdown_prune_protection_selectors"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_str_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, advanced.prune_protection_selectors),
        NULL
    },

    /*
     * markdown_memory_budget <size>
     *
     * Unified memory budget for both streaming and full-buffer
     * conversion engines. When set, this value is used as the
     * memory limit for both paths unless overridden by the
     * path-specific directives (markdown_max_size for
     * full-buffer, markdown_streaming_budget for streaming).
     *
     * Priority: explicit path-specific > unified > default
     *
     * Default: unset (use path-specific defaults)
     * Context: http, server, location
     *
     * Example:
     *   markdown_memory_budget 8m;
     */
    {
        ngx_string("markdown_memory_budget"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_size_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, advanced.memory_budget),
        NULL
    },

    /*
     * markdown_llm_provider default|openai-gpt|anthropic-claude|google-gemini|meta-llama
     *
     * LLM provider for token estimation.  Each provider has a characteristic
     * chars-per-token ratio that improves estimate accuracy for that provider's
     * tokenizer family.
     *
     * Default: default (4.0 chars/token, English average)
     * Context: http, server, location
     *
     * Example:
     *   markdown_llm_provider openai-gpt;
     */
    {
        ngx_string("markdown_llm_provider"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_enum_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, advanced.llm_provider),
        &ngx_http_markdown_llm_provider_values
    },

    /*
     * markdown_chars_per_token <number>
     *
     * Explicit chars-per-token ratio for token estimation, stored as
     * fixed-point * 10 (e.g., 38 = 3.8 chars/token).  Overrides both
     * the default (40) and the provider-specific ratio.  Set to 0 to
     * use the provider's default.
     *
     * Range: 0-255 (0.0-25.5 chars/token).  Practical range: 20-60.
     *
     * Default: 0 (use provider default)
     * Context: http, server, location
     *
     * Example:
     *   markdown_chars_per_token 38;
     */
    {
        ngx_string("markdown_chars_per_token"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_num_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, advanced.chars_per_token_fixed),
        NULL
    },

    /*
     * markdown_otel on|off
     *
     * Enable OpenTelemetry span creation for conversion requests.
     * When enabled, each conversion creates a span with attributes
     * for flavor, engine, content_type, input/output bytes, and
     * reason code.
     *
     * Default: off
     * Context: http, server, location
     *
     * Example:
     *   markdown_otel on;
     */
    {
        ngx_string("markdown_otel"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.otel_enabled),
        NULL
    },

    /*
     * markdown_otel_endpoint <uri>
     *
     * Internal NGINX URI for OTel span export via subrequest.
     * The module issues an HTTP POST to this URI using
     * ngx_http_subrequest(), sending the OTLP JSON payload
     * as the request body.
     *
     * This URI must map to an internal location block in
     * nginx.conf that proxy_passes to the OTel collector:
     *
     *   location = /_otel_export {
     *       internal;
     *       proxy_pass http://collector:4318/v1/traces;
     *   }
     *
     * Default: (empty -- no endpoint configured)
     * Context: http, server, location
     *
     * Example:
     *   markdown_otel_endpoint /_otel_export;
     */
    {
        ngx_string("markdown_otel_endpoint"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_str_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.otel_endpoint),
        NULL
    },

    /*
     * markdown_otel_tracing on|off
     *
     * Enable OTel span creation for conversion request tracing.
     * When enabled, each conversion creates a span with trace
     * context propagation and conversion attributes.
     *
     * Default: off
     * Context: http, server, location
     */
    {
        ngx_string("markdown_otel_tracing"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.otel_tracing),
        NULL
    },

    /*
     * markdown_otel_metrics on|off
     *
     * Enable OTel metrics export via OTLP protocol.
     *
     * Default: off
     * Context: http, server, location
     */
    {
        ngx_string("markdown_otel_metrics"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.otel_metrics),
        NULL
    },

    /*
     * markdown_otel_service_name <name>
     *
     * Service name label for OTel resource attributes.
     *
     * Default: nginx-markdown
     * Context: http, server, location
     */
    {
        ngx_string("markdown_otel_service_name"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_str_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.otel_service_name),
        NULL
    },

    /*
     * markdown_otel_span_buffer_size <number>
     *
     * Buffer size for spans when the collector is unreachable.
     * Buffered spans are retried on the next export window.
     *
     * Default: 1024
     * Context: http, server, location
     */
    {
        ngx_string("markdown_otel_span_buffer_size"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_num_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.otel_span_buffer_size),
        NULL
    },

    /*
     * markdown_otel_export_timeout <time>
     *
     * Timeout for OTLP HTTP export requests.
     *
     * Default: 5s
     * Context: http, server, location
     */
    {
        ngx_string("markdown_otel_export_timeout"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_msec_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.otel_export_timeout),
        NULL
    },

    /*
     * markdown_dynamic_config on|off
     *
     * Enable runtime configuration hot-reload without NGINX restart.
     * Watches the file specified by markdown_dynamic_config_path for
     * changes and atomically swaps the active configuration.
     *
     * Default: off
     * Context: http, server, location
     *
     * Example:
     *   markdown_dynamic_config on;
     *   markdown_dynamic_config_path /etc/nginx/markdown_dynamic.conf;
     */
    {
        ngx_string("markdown_dynamic_config"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, advanced.dynconf_enabled),
        NULL
    },

    /*
     * markdown_dynamic_config_path <path>
     *
     * Path to the dynamic configuration file to watch for changes.
     * Only effective when markdown_dynamic_config is on.
     *
     * Default: (none)
     * Context: http, server, location
     *
     * Example:
     *   markdown_dynamic_config_path /etc/nginx/markdown_dynamic.conf;
     */
    {
        ngx_string("markdown_dynamic_config_path"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_set_dynconf_path,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, advanced.dynconf_path),
        NULL
    },

    ngx_null_command
};

#endif /* NGX_HTTP_MARKDOWN_CONFIG_DIRECTIVES_IMPL_H */
