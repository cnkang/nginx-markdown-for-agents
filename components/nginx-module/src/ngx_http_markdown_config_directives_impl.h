#ifndef NGX_HTTP_MARKDOWN_CONFIG_DIRECTIVES_IMPL_H
#define NGX_HTTP_MARKDOWN_CONFIG_DIRECTIVES_IMPL_H

#include "ngx_http_markdown_directive_names.h"

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



/*
 * Enum table for markdown_accept directive (Config V2, 0.9.0).
 *
 * Replaces the removed markdown_on_wildcard on|off directive.
 * Invalid values are rejected by ngx_conf_set_enum_slot.
 */
static ngx_conf_enum_t
    ngx_http_markdown_accept_enum[] = {
    { ngx_string("strict"),    NGX_HTTP_MARKDOWN_ACCEPT_STRICT },
    { ngx_string("force"),     NGX_HTTP_MARKDOWN_ACCEPT_FORCE },
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
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_FILTER),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_filter,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },



    /*
     * markdown_limits key=value ...
     *
     * Unified resource limits (0.9.2 frozen contract, 8 keys):
     *   conversion_timeout, parser_timeout, conversion_memory,
     *   parser_budget, streaming_buffer, decompressed_size,
     *   decompression_ratio, max_inflight
     *
     * Any subset of keys may be given; unspecified keys inherit
     * (per-key inheritance).
     * Public default: (per-key inheritance)
     * Context: http, server, location (max_inflight is http-only)
     *
     * Example:
     *   markdown_limits conversion_timeout=30s conversion_memory=64m;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_LIMITS),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_1MORE,
        ngx_http_markdown_limits,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },



    /*
     * markdown_error_policy pass|fail_closed|status <code>   (Config V2, 0.9.0)
     *
     * Unified pre-commit error policy.
     *   pass        - return original content on pre-commit error (fail-open)
     *   fail_closed - return 502 on pre-commit error
     *   status <c>  - return status code c (429 or 503)
     * Default: pass
     * Context: http, server, location
     *
     * Example:
     *   markdown_error_policy status 503;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_ERROR_POLICY),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE12,
        ngx_http_markdown_error_policy,
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
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_FLAVOR),
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
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_TOKEN_ESTIMATE),
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
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_FRONT_MATTER),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, front_matter),
        NULL
    },

    /*
     * markdown_accept strict|force   (Config V2, 0.9.0; wildcard removed 0.9.2)
     *
     * Accept-header negotiation policy.
     *   strict   - convert only on an explicit text/markdown match (default)
     *   force    - convert regardless of the Accept header (dangerous)
     * Public default: strict
     * Context: http, server, location
     *
     * The "wildcard" value was removed in 0.9.2 (LTS-R008/LTS-R010): it is no
     * longer part of ngx_http_markdown_accept_enum, and the
     * ngx_http_markdown_accept wrapper rejects it with an explicit migration
     * message so `nginx -t` fails rather than silently ignoring it.
     *
     * Example:
     *   markdown_accept strict;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_ACCEPT),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_accept,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, accept_policy),
        &ngx_http_markdown_accept_enum
    },



    /*
     * markdown_auth_policy allow|deny
     *
     * Policy for converting identifiable authenticated requests (LTS-R021):
     * - allow: Explicit opt-in; convert authenticated requests
     * - deny:  Do not convert authenticated requests (still serve the
     *          original HTML; never refuse)
     * Default: deny (unset resolves to no-convert for authenticated content)
     * Public default: deny
     * Context: http, server, location
     *
     * Example:
     *   markdown_auth_policy allow;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_AUTH_POLICY),
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
     * Public default: built-in session*, auth*, PHPSESSID, wordpress_logged_in_* (explicit replaces)
     * The built-in patterns are used in addition to Authorization
     * header detection.  An explicit directive replaces the built-ins.
     * Context: http, server, location
     *
     * Example:
     *   markdown_auth_cookies session* auth_token PHPSESSID;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_AUTH_COOKIES),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_1MORE,
        ngx_http_markdown_auth_cookies,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_cache_validation off|ims_only|full   (Config V2, 0.9.0)
     *
     * Cache-validation policy.
     *   off      - no ETag, no conditional request handling
     *   ims_only - no ETag; source If-Modified-Since applies to
     *              pass-through responses only; converted responses
     *              never produce a 304 (the module clears the source
     *              Last-Modified) (default)
     *   full     - transformed ETag + If-None-Match + If-Modified-Since
     * Public default: ims_only
     * Context: http, server, location
     *
     * Example:
     *   markdown_cache_validation full;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_CACHE_VALIDATION),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_cache_validation,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_streaming off|auto|force   (Config V2, 0.9.0)
     *
     * Sole streaming processing-path policy.
     *
     *   off   - bounded full-buffer conversion (default; unset == off)
     *   auto  - prefer streaming (no size/heuristic branching); explicit only
     *   force - always stream (subject to runtime hard blocks)
     * Public default: off
     *
     * Migration note (0.9.2): the unset default changed from auto to off.
     * Unset markdown_streaming now resolves to bounded full-buffer (design
     * §14(a), LTS-R011.2).  auto retains "prefer streaming" meaning only when
     * written explicitly; operators who relied on the old implicit streaming
     * default must now write "markdown_streaming auto" (or "force").
     *
     * Conflict (config conflict): markdown_cache_validation full + force => error;
     * full + auto => warning (runtime blocks streaming, falls back to
     * full-buffer).  Enforced in merge_conf.
     *
     * Example:
     *   markdown_streaming auto;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_STREAMING),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_streaming,
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
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_LOG_VERBOSITY),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_log_verbosity,
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
     * Public default: text/html
     * Default: text/html
     * Context: http, server, location
     *
     * Example:
     *   markdown_content_types text/html application/xhtml+xml;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_CONTENT_TYPES),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_1MORE,
        ngx_http_markdown_content_types,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_trusted_proxies <CIDR>... | off   (Config V2, 0.9.0)
     *
     * CIDR-based trusted-proxy list controlling whether forwarded headers
     * (Forwarded / X-Forwarded-Proto / X-Forwarded-Host) are honored when
     * deriving the base URL for relative-link resolution.  Replaces the
     * removed boolean markdown_trust_forwarded_headers trust model.
     *
     * Context: http only.  NGINX rejects server/location use to avoid
     * per-location trust bypass.  CIDRs are validated at config time
     * (IPv4 + IPv6); "off" disables trust entirely.
     *
     * Example:
     *   markdown_trusted_proxies 10.0.0.0/8 2001:db8::/32;
     *   markdown_trusted_proxies off;
     * Public default: off
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_TRUSTED_PROXIES),
        NGX_HTTP_MAIN_CONF|NGX_CONF_1MORE,
        ngx_http_markdown_trusted_proxies,
        NGX_HTTP_MAIN_CONF_OFFSET,
        0,
        NULL
    },



    /*
     * markdown_metrics_shm_size <size>
     *
     * Size of the shared-memory zone used to aggregate metrics across workers.
     * Public default: 8*pagesize
     * Default: 8 * ngx_pagesize
     * Context: http
     *
     * Example:
     *   markdown_metrics_shm_size 128k;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_METRICS_SHM_SIZE),
        NGX_HTTP_MAIN_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_size_slot,
        NGX_HTTP_MAIN_CONF_OFFSET,
        offsetof(ngx_http_markdown_main_conf_t, metrics_shm_size),
        NULL
    },



    /*
     * markdown_metrics
     *
     * Enables the Prometheus text 0.0.4 metrics endpoint at this location.
     * Accept negotiation does not select another metrics representation.
     * Access is restricted to localhost (127.0.0.1, ::1) by default for security.
     * Default: off
     * Context: location only
     *
     * Example:
     *   location /markdown-metrics {
     *       markdown_metrics;
     *   }
     *
     * Security: Only accessible from localhost by default.
     * NGINX allow/deny directives can further restrict access, but they do
     * not broaden access beyond localhost.
     *
     * Public default: off
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_METRICS),
        NGX_HTTP_LOC_CONF|NGX_CONF_NOARGS,
        ngx_http_markdown_metrics_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },







#ifdef MARKDOWN_STREAMING_ENABLED


#endif /* MARKDOWN_STREAMING_ENABLED */

    /*
     * markdown_prune_noise on|off
     *
     * Enable or disable noise region pruning at runtime.
     * When enabled, structural HTML regions matching prune
     * selectors are excluded from Markdown output.
     *
     * Default: on
     * Context: http, server, location
     *
     * Example:
     *   markdown_prune_noise off;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_PRUNE_NOISE),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, advanced.prune_noise),
        NULL
    },

    /*
     * markdown_prune_selectors <string>   (REMOVED in 0.9.2, LTS-R008/R009)
     *
     * Custom prune selectors were removed in 0.9.2.  The directive name stays
     * registered so a configuration still using it fails `nginx -t` with an
     * explicit migration message instead of being silently ignored.  Built-in
     * noise reduction remains controlled by markdown_prune_noise.
     * The advanced.prune_selectors config field/decode path was removed in
     * 0.9.2 (LTS-R009), so the offset is 0: ngx_http_markdown_removed_directive
     * ignores conf/offset and always fails nginx -t (LTS-R008).
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_PRUNE_SELECTORS),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },
    /* Context above preserves the pre-removal http/server/location surface;
     * only NGX_CONF_ANY (arg count) + the error handler remain so any usage
     * reaches ngx_http_markdown_removed_directive, which ignores conf/offset
     * and always fails nginx -t (LTS-R008). */

    /*
     * markdown_prune_protection_selectors <string>
     *                                     (REMOVED in 0.9.2, LTS-R008/R009)
     *
     * Custom protection selectors were removed in 0.9.2.  The directive name
     * stays registered so a configuration still using it fails `nginx -t`
     * with an explicit migration message instead of being silently ignored.
     * The advanced.prune_protection_selectors config field/decode path was
     * removed in 0.9.2 (LTS-R009), so the offset is 0:
     * ngx_http_markdown_removed_directive ignores conf/offset and always
     * fails nginx -t (LTS-R008).
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_PRUNE_PROTECTION_SELECTORS),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },











    /*
     * markdown_auto_decompress on|off
     *
     * Controls whether the module automatically decompresses upstream
     * compressed responses (gzip, deflate, brotli) before conversion.
     * When off, compressed responses pass through unconverted.
     *
     * Default: on
     * Context: http, server, location
     *
     * Example:
     *   markdown_auto_decompress off;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_AUTO_DECOMPRESS),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, decompress.auto_decompress),
        NULL
    },

    /*
     * markdown_dynamic_config on|off   (REMOVED in 0.9.2, LTS-R008)
     *
     * The dynamic-config hot-reload subsystem was removed in 0.9.2.  The
     * directive name stays registered so a configuration still using it fails
     * `nginx -t` with an explicit migration message instead of being silently
     * ignored.  Migrate to static config validated by `nginx -t` + reload.
     * The error handler ignores the configuration offset and always fails.
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_DYNAMIC_CONFIG),
        NGX_HTTP_MAIN_CONF|NGX_CONF_ANY,
        ngx_http_markdown_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_dynamic_config_path <path>   (REMOVED in 0.9.2, LTS-R008)
     *
     * Removed alongside markdown_dynamic_config.  The directive name stays
     * registered so a configuration still using it fails `nginx -t` with an
     * explicit migration message instead of being silently ignored.
     * The error handler ignores the configuration offset and always fails.
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_DYNAMIC_CONFIG_PATH),
        NGX_HTTP_MAIN_CONF|NGX_CONF_ANY,
        ngx_http_markdown_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_dynconf_dry_run on|off   (REMOVED in 0.9.2, LTS-R008)
     *
     * Removed alongside the dynamic-config subsystem.  The directive name
     * stays registered so a configuration still using it fails `nginx -t`
     * with an explicit migration message instead of being silently ignored.
     * The error handler ignores the configuration offset and always fails.
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_DYNCONF_DRY_RUN),
        NGX_HTTP_MAIN_CONF|NGX_CONF_ANY,
        ngx_http_markdown_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_diagnostics on|off
     *
     * Enable or disable the runtime diagnostics endpoint
     * (/nginx-markdown/diagnostics).  When enabled, the endpoint
     * exposes the Diagnostics Schema v3 fields: worker/build identity,
     * configuration, runtime counters, and recent decisions.
     *
     * Access control: the diagnostics content handler runs in the
     * NGINX content phase, which executes AFTER the access phase.
     * Use native NGINX allow/deny directives in the same location
     * block to restrict access.
     *
     * Default: off
     * Context: location
     *
     * Example:
     *   location /nginx-markdown/diagnostics {
     *       markdown_diagnostics on;
     *       allow 127.0.0.1;
     *       allow ::1;
     *       deny all;
     *   }
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_DIAGNOSTICS),
        NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_http_markdown_diagnostics_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.diagnostics_enabled),
        NULL
    },



    /*
     * markdown_stream_excluded_types <type> [<type> ...]
     *
     * Content types that should never enter streaming conversion.
     * User-configured types are additive to built-in hard
     * exclusions (text/event-stream, application/x-ndjson,
     * application/stream+json).
     *
     * Public default: none
     * Default: none (only built-in hard exclusions apply)
     * Context: http, server, location
     *
     * Example:
     *   markdown_stream_excluded_types text/csv application/xml;
     */
    {
        ngx_string(NGX_HTTP_MARKDOWN_DIRECTIVE_STREAM_EXCLUDED_TYPES),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_1MORE,
        ngx_http_markdown_stream_excluded_types_handler,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    ngx_null_command
};

#endif /* NGX_HTTP_MARKDOWN_CONFIG_DIRECTIVES_IMPL_H */
