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
static ngx_conf_enum_t
    ngx_http_markdown_streaming_engine_enum[] = {
    { ngx_string("off"),
      NGX_HTTP_MARKDOWN_STREAM_ENGINE_OFF },
    { ngx_string("auto"),
      NGX_HTTP_MARKDOWN_STREAM_ENGINE_AUTO },
    { ngx_string("on"),
      NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON },
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

/*
 * Enum table for markdown_accept directive (Config V2, 0.9.0).
 *
 * Replaces the removed markdown_on_wildcard on|off directive.
 * Invalid values are rejected by ngx_conf_set_enum_slot.
 */
static ngx_conf_enum_t
    ngx_http_markdown_accept_enum[] = {
    { ngx_string("strict"),    NGX_HTTP_MARKDOWN_ACCEPT_STRICT },
    { ngx_string("wildcard"),  NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD },
    { ngx_string("force"),     NGX_HTTP_MARKDOWN_ACCEPT_FORCE },
    { ngx_null_string, 0 }
};

static u_char ngx_http_markdown_hint_limits_memory[] =
    "use \"markdown_limits memory=<size>\" instead";
static u_char ngx_http_markdown_hint_limits_timeout[] =
    "use \"markdown_limits timeout=<time>\" instead";
static u_char ngx_http_markdown_hint_limits_streaming_buffer[] =
    "use \"markdown_limits streaming_buffer=<size>\" instead";
static u_char ngx_http_markdown_hint_error_policy[] =
    "use \"markdown_error_policy pass|fail_closed|status <code>\" instead";
static u_char ngx_http_markdown_hint_accept[] =
    "use \"markdown_accept strict|wildcard|force\" instead";
static u_char ngx_http_markdown_hint_cache_validation[] =
    "use \"markdown_cache_validation off|ims_only|full\" instead";
static u_char ngx_http_markdown_hint_trusted_proxies[] =
    "use \"markdown_trusted_proxies <CIDR>...\" instead";
static u_char ngx_http_markdown_hint_removed_no_replacement[] =
    "it has been removed with no direct replacement";


/*
 * Reject-only setter for legacy directives removed in 0.9.0 (Config V2).
 *
 * 0.9.0 is a breaking release with no alias compatibility.  Removed
 * directives keep a parser entry whose only behavior is to fail
 * "nginx -t" with an actionable migration hint, because NGINX's own
 * unknown-directive handling cannot point the operator at the
 * replacement.  The migration hint is carried in the ngx_command_t.post
 * field as a NUL-terminated C string.
 *
 * Parameters:
 *   cf   - configuration context
 *   cmd  - directive definition (cmd->name = legacy name,
 *          cmd->post = migration hint string)
 *   conf - unused
 *
 * Returns:
 *   Always NGX_CONF_ERROR.
 */
static char *
ngx_http_markdown_reject_removed_directive(ngx_conf_t *cf,
    ngx_command_t *cmd, void *conf) /* NOSONAR: cmd/conf must match ngx_command_t.set signature */
{
    (void) conf;

    ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
        "\"%V\" directive has been removed in 0.9.0; %s "
        "(see docs/guides/MIGRATION-0.9.md)",
        &cmd->name, (char *) cmd->post);

    return NGX_CONF_ERROR;
}


/*
 * Custom directive handler for markdown_diagnostics_allow.
 *
 * Parses a CIDR notation address and adds it to the location
 * configuration's diagnostics allow list.  Uses NGINX's built-in
 * ngx_ptocidr() for CIDR parsing.
 *
 * Parameters:
 *   cf  - configuration context
 *   cmd - directive definition
 *   conf - location configuration pointer
 *
 * Returns:
 *   NGX_CONF_OK on success, error string on failure
 */
static char *
ngx_http_markdown_diagnostics_allow(ngx_conf_t *cf,
    ngx_command_t *cmd, void *conf) /* NOSONAR: cmd must match ngx_command_t.set signature */
{
    ngx_http_markdown_conf_t  *mcf = conf;
    ngx_str_t                 *value;
    ngx_cidr_t                *cidr;
    ngx_int_t                  rc;

    (void) cmd;

    value = cf->args->elts;

    /* Lazy-initialize the allow array on first use. */
    if (mcf->ops.diagnostics_allow == NULL) {
        mcf->ops.diagnostics_allow = ngx_array_create(cf->pool, 4,
            sizeof(ngx_cidr_t));
        if (mcf->ops.diagnostics_allow == NULL) {
            return NGX_CONF_ERROR;
        }
    }

    cidr = ngx_array_push(mcf->ops.diagnostics_allow);
    if (cidr == NULL) {
        return NGX_CONF_ERROR;
    }

    ngx_memzero(cidr, sizeof(ngx_cidr_t));

    rc = ngx_ptocidr(&value[1], cidr);

    if (rc == NGX_ERROR) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid CIDR address \"%V\"", &value[1]);
        mcf->ops.diagnostics_allow->nelts--;
        return NGX_CONF_ERROR;
    }

    if (rc == NGX_DONE) {
        ngx_conf_log_error(NGX_LOG_WARN, cf, 0,
            "low bits of \"%V\" are meaningless", &value[1]);
    }

    return NGX_CONF_OK;
}


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
     * markdown_profile strict_cache|balanced|streaming_first   (0.9.0, spec 50)
     *
     * Selects a production-profile preset providing tuned Config V2
     * defaults for a common operational scenario.  The profile only
     * supplies defaults; explicit directives override profile values.
     *
     *   strict_cache    - CDN / caching proxy (full ETag, no streaming)
     *   balanced        - general-purpose (IMS-only, auto streaming)
     *   streaming_first - AI agent workloads (no cache, forced streaming)
     *
     * Default: none (built-in Config V2 defaults apply)
     * Context: http, server, location
     *
     * Example:
     *   markdown_profile balanced;
     */
    {
        ngx_string("markdown_profile"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF
            |NGX_CONF_TAKE1,
        ngx_http_markdown_set_profile,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_limits memory=<size> timeout=<time>
     *                 streaming_buffer=<size> max_inflight=<N>   (Config V2)
     *
     * Unified limits block. Consolidates the removed markdown_max_size,
     * markdown_timeout, and markdown_streaming_budget directives. Any subset
     * of keys may be given; unspecified keys inherit (per-key inheritance).
     * Context: http, server, location
     *
     * Example:
     *   markdown_limits memory=8m timeout=2s streaming_buffer=256k max_inflight=64;
     */
    {
        ngx_string("markdown_limits"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_1MORE,
        ngx_http_markdown_limits,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_max_size  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Migrated to markdown_limits memory=<size>.
     */
    {
        ngx_string("markdown_max_size"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_limits_memory
    },

    /*
     * markdown_timeout  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Migrated to markdown_limits timeout=<time>.
     */
    {
        ngx_string("markdown_timeout"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_limits_timeout
    },

    /*
     * markdown_streaming_budget  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Migrated to markdown_limits streaming_buffer=<size>.
     */
    {
        ngx_string("markdown_streaming_budget"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_limits_streaming_buffer
    },

    /*
     * markdown_error_policy pass|fail_closed|status <code>   (Config V2, 0.9.0)
     *
     * Unified pre-commit error policy. Consolidates the removed
     * markdown_on_error and markdown_streaming_on_error directives.
     *   pass        - return original content on pre-commit error (fail-open)
     *   fail_closed - return 502 on pre-commit error
     *   status <c>  - return status code c (429, 502, or 503)
     * Default: pass
     * Context: http, server, location
     *
     * Example:
     *   markdown_error_policy status 503;
     */
    {
        ngx_string("markdown_error_policy"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE12,
        ngx_http_markdown_error_policy,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_on_error  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Migrated to markdown_error_policy pass|fail_closed|status <code>.
     */
    {
        ngx_string("markdown_on_error"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_error_policy
    },

    /*
     * markdown_streaming_on_error  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Migrated to markdown_error_policy pass|fail_closed|status <code>.
     */
    {
        ngx_string("markdown_streaming_on_error"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_error_policy
    },

    /*
     * markdown_flavor commonmark|gfm|mdx|org-mode
     *
     * Markdown flavor to generate:
     * - commonmark: CommonMark specification (default)
     * - gfm: GitHub Flavored Markdown (includes tables, strikethrough)
     * - mdx: experimental MDX-oriented selector
     * - org-mode: experimental Org-mode-oriented selector
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
     * markdown_accept strict|wildcard|force   (Config V2, 0.9.0)
     *
     * Accept-header negotiation policy. Replaces the removed
     * markdown_on_wildcard on|off directive.
     *   strict   - convert only on an explicit text/markdown match (default)
     *   wildcard - also convert on wildcard Accept (equivalent to the old
     *              "markdown_on_wildcard on")
     *   force    - convert regardless of the Accept header (dangerous)
     * Context: http, server, location
     *
     * Example:
     *   markdown_accept wildcard;
     */
    {
        ngx_string("markdown_accept"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_enum_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, accept_policy),
        &ngx_http_markdown_accept_enum
    },

    /*
     * markdown_on_wildcard  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Migrated to markdown_accept strict|wildcard|force.
     */
    {
        ngx_string("markdown_on_wildcard"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_accept
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
     * markdown_cache_validation off|ims_only|full   (Config V2, 0.9.0)
     *
     * Cache-validation policy. Consolidates the removed markdown_etag and
     * markdown_conditional_requests directives.
     *   off      - no ETag, no conditional request handling
     *   ims_only - no ETag, If-Modified-Since only (default)
     *   full     - transformed ETag + If-None-Match + If-Modified-Since
     * Context: http, server, location
     *
     * Example:
     *   markdown_cache_validation full;
     */
    {
        ngx_string("markdown_cache_validation"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_cache_validation,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_streaming off|auto|force   (Config V2, 0.9.0)
     *
     * Streaming *enablement* policy.  Distinct from
     * markdown_streaming_engine (the implementation selector).
     *
     *   off   - never stream
     *   auto  - stream large responses, full-buffer small ones (default)
     *   force - always stream (subject to runtime hard blocks)
     *
     * Conflict (spec 49): markdown_cache_validation full + force => error;
     * full + auto => warning (runtime blocks streaming, falls back to
     * full-buffer).  Enforced in merge_conf.
     *
     * Example:
     *   markdown_streaming auto;
     */
    {
        ngx_string("markdown_streaming"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_streaming,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_etag  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Migrated to markdown_cache_validation off|ims_only|full.
     */
    {
        ngx_string("markdown_etag"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_cache_validation
    },

    /*
     * markdown_etag_policy  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Migrated to markdown_cache_validation off|ims_only|full.
     */
    {
        ngx_string("markdown_etag_policy"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_cache_validation
    },

    /*
     * markdown_conditional_requests  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Migrated to markdown_cache_validation off|ims_only|full.
     */
    {
        ngx_string("markdown_conditional_requests"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_cache_validation
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
     * markdown_trusted_proxies <CIDR>... | off   (Config V2, 0.9.0, spec 47)
     *
     * CIDR-based trusted-proxy list controlling whether forwarded headers
     * (Forwarded / X-Forwarded-Proto / X-Forwarded-Host) are honored when
     * deriving the base URL for relative-link resolution.  Replaces the
     * removed boolean markdown_trust_forwarded_headers trust model.
     *
     * Context: http only.  server/location context is rejected with a
     * migration hint to avoid per-location trust bypass.  CIDRs are
     * validated at config time (IPv4 + IPv6); "off" disables trust entirely.
     *
     * Example:
     *   markdown_trusted_proxies 10.0.0.0/8 2001:db8::/32;
     *   markdown_trusted_proxies off;
     */
    {
        ngx_string("markdown_trusted_proxies"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_1MORE,
        ngx_http_markdown_trusted_proxies,
        NGX_HTTP_MAIN_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_trust_forwarded_headers  (REMOVED in 0.9.0 - reject-only stub)
     *
     * The boolean trust model is replaced by CIDR-based
     * markdown_trusted_proxies (spec 47).  No alias, no fallback behavior.
     */
    {
        ngx_string("markdown_trust_forwarded_headers"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_trusted_proxies
    },

    /*
     * markdown_forwarded_headers  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Replaced by CIDR-based markdown_trusted_proxies (spec 47).
     */
    {
        ngx_string("markdown_forwarded_headers"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_trusted_proxies
    },

    /*
     * markdown_large_body_threshold  (REMOVED in 0.9.0 - reject-only stub)
     *
     * No direct Config V2 equivalent; the incremental-path threshold knob is
     * retired. See docs/guides/MIGRATION-0.9.md.
     */
    {
        ngx_string("markdown_large_body_threshold"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_removed_no_replacement
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
     * server and location blocks.  The directive is defined with
     * the NGX_HTTP_MAIN_CONF flag, so it is http-only: attempting
     * to place it in a server or location context will produce a
     * configuration parsing error at load time rather than being
     * silently ignored.
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
     * markdown_streaming_engine off|on|auto
     *
     * Streaming engine selection mode.
     * Default: auto (per-request selection based on
     *          markdown_stream_threshold)
     * Context: http, server, location
     *
     * Example:
     *   markdown_streaming_engine auto;
     */
    {
        ngx_string("markdown_streaming_engine"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_enum_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, stream.engine),
        &ngx_http_markdown_streaming_engine_enum
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
        offsetof(ngx_http_markdown_conf_t, stream.shadow),
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
     * Default: on
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
     * markdown_memory_budget  (REMOVED in 0.9.0 - reject-only stub)
     *
     * Migrated to markdown_limits memory=<size>.
     */
    {
        ngx_string("markdown_memory_budget"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_ANY,
        ngx_http_markdown_reject_removed_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        ngx_http_markdown_hint_limits_memory
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
     * markdown_parse_timeout <time>
     *
     * Maximum time to spend on HTML parsing phase (e.g., 30s, 5000ms).
     * The parse phase deadline is checked before and after parsing; the HTML
     * parser itself is not preemptively interrupted. If the deadline expires,
     * parsing is aborted and the request proceeds according to the on_error
     * policy. Combine with markdown_max_size, markdown_decompress_max_size, and
     * markdown_parser_budget for comprehensive resource control.
     *
     * Default: 30s
     * Context: http, server, location
     *
     * Example:
     *   markdown_parse_timeout 10s;
     */
    {
        ngx_string("markdown_parse_timeout"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_msec_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, decompress.parse_timeout),
        NULL
    },

    /*
     * markdown_parser_budget <size>
     *
     * Maximum memory the HTML parser may allocate (e.g., 64m, 128m).
     * If the parser exceeds this budget, parsing is terminated and the
     * request proceeds according to the on_error policy.
     *
     * Default: 64m (64 megabytes)
     * Context: http, server, location
     *
     * Example:
     *   markdown_parser_budget 32m;
     */
    {
        ngx_string("markdown_parser_budget"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_size_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, decompress.parser_budget),
        NULL
    },

    /*
     * markdown_decompress_max_size <size>
     *
     * Independent budget for decompressed output size.  When upstream
     * content is compressed (gzip/deflate/brotli), this directive caps
     * the maximum decompressed byte count, separate from markdown_max_size
     * which also limits the final Markdown output.
     *
     * Default: same as markdown_max_size (inherited after memory_budget
     * override resolution).
     * Context: http, server, location
     *
     * Example:
     *   markdown_decompress_max_size 20m;
     */
    {
        ngx_string("markdown_decompress_max_size"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_size_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, decompress.max_size),
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

    /*
     * markdown_dynconf_dry_run on|off
     *
     * Enable dry-run mode for dynamic configuration validation.
     * When enabled, configuration changes are validated but NOT
     * applied to the active snapshot.  This allows operators to
     * verify a new dynconf file without affecting live traffic.
     *
     * Default: off
     * Context: http, server, location
     *
     * Example:
     *   markdown_dynconf_dry_run on;
     */
    {
        ngx_string("markdown_dynconf_dry_run"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF
            |NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, advanced.dynconf_dry_run),
        NULL
    },

    /*
     * markdown_diagnostics on|off
     *
     * Enable or disable the runtime diagnostics endpoint
     * (/nginx-markdown/diagnostics).  When enabled, the endpoint
     * exposes config_snapshot, recent_decisions, and metrics_snapshot
     * for operational introspection.
     *
     * Access control: use markdown_diagnostics_allow to specify
     * CIDR addresses permitted to access the endpoint.  When no
     * allow rules are configured, only loopback (127.0.0.1/::1)
     * is permitted.
     *
     * Default: off
     * Context: http, server, location
     *
     * Example:
     *   location /nginx-markdown/diagnostics {
     *       markdown_diagnostics on;
     *       markdown_diagnostics_allow 10.0.0.0/8;
     *       allow ::1;
     *       deny all;
     *   }
     */
    {
        ngx_string("markdown_diagnostics"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF
            |NGX_CONF_FLAG,
        ngx_http_markdown_diagnostics_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, ops.diagnostics_enabled),
        NULL
    },

    /*
     * markdown_diagnostics_allow
     *
     * Multi-value directive accepting CIDR notation addresses that
     * are permitted to access the diagnostics endpoint.  When the
     * allow list is empty (default), only loopback addresses are
     * permitted.  When one or more CIDRs are configured, only
     * matching client addresses are allowed.
     *
     * Example:
     *   location /nginx-markdown/diagnostics {
     *       markdown_diagnostics on;
     *       markdown_diagnostics_allow 10.0.0.0/8;
     *       markdown_diagnostics_allow 172.16.0.0/12;
     *       markdown_diagnostics_allow 127.0.0.1;
     *       markdown_diagnostics_allow ::1;
     *   }
     */
    {
        ngx_string("markdown_diagnostics_allow"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF
            |NGX_CONF_TAKE1,
        ngx_http_markdown_diagnostics_allow,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_streaming_engine off|auto|on
     *
     * v0.8.0 core engine switch for true streaming availability.
     * - off: streaming disabled
     * - auto: automatic selection based on threshold (default)
     * - on: streaming always enabled for eligible responses
     *
     * Streaming-enabled builds register the same directive above through
     * ngx_conf_set_enum_slot. Non-streaming builds keep this parser so
     * config validation reports invalid values consistently.
     *
     * Default: auto
     * Context: http, server, location
     *
     * Example:
     *   markdown_streaming_engine on;
     */
#ifndef MARKDOWN_STREAMING_ENABLED
    {
        ngx_string("markdown_streaming_engine"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_stream_engine_handler,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },
#endif

    /*
     * markdown_stream_threshold <size>
     *
     * Minimum response size for streaming candidacy.
     * Responses with Content-Length below this value use
     * full-buffer conversion.  Zero is rejected.
     *
     * Default: 1m (1048576 bytes)
     * Context: http, server, location
     *
     * Example:
     *   markdown_stream_threshold 512k;
     */
    {
        ngx_string("markdown_stream_threshold"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_stream_threshold_handler,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },

    /*
     * markdown_stream_precommit_buffer <size>
     *
     * Size of the pre-commit replay buffer for streaming fallback.
     * Zero is allowed (disables pre-commit HTML fallback capability).
     *
     * Default: 256k (262144 bytes)
     * Context: http, server, location
     *
     * Example:
     *   markdown_stream_precommit_buffer 128k;
     *   markdown_stream_precommit_buffer 0;
     */
    {
        ngx_string("markdown_stream_precommit_buffer"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_conf_set_size_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, stream.precommit_buffer),
        NULL
    },

    /*
     * markdown_stream_flush_min <size>
     *
     * Minimum Markdown output batch size before flushing
     * downstream.  Must be greater than zero to avoid
     * pathological per-byte flushing.
     *
     * Default: 16k (16384 bytes)
     * Context: http, server, location
     *
     * Example:
     *   markdown_stream_flush_min 32k;
     */
    {
        ngx_string("markdown_stream_flush_min"),
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF
            |NGX_HTTP_LOC_CONF|NGX_CONF_TAKE1,
        ngx_http_markdown_stream_flush_min_handler,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
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
     * Default: none (only built-in hard exclusions apply)
     * Context: http, server, location
     *
     * Example:
     *   markdown_stream_excluded_types text/csv application/xml;
     */
    {
        ngx_string("markdown_stream_excluded_types"),
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
