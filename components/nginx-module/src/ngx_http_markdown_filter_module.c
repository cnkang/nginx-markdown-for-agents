/*
 * NGINX Markdown Filter Module - Implementation
 *
 * This module provides HTML to Markdown conversion for AI agents via
 * HTTP content negotiation (Accept: text/markdown).
 *
 * Phase 1: Basic module structure and configuration
 * Full implementation will be completed in subsequent phases.
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

/*
 * Request-level buffered flag for this module while it is accumulating the
 * full response body before conversion.
 *
 * Low bits 0x01/0x02/0x04 are used by core modules (SSI/SUB/COPY). 0x08 is
 * available for request-level buffering (image filter uses 0x08 on
 * connection->buffered, not r->buffered).
 */
#define NGX_HTTP_MARKDOWN_BUFFERED  0x08
/* Bound eager reservation to avoid huge one-shot allocations on giant responses. */
#define NGX_HTTP_MARKDOWN_PRERESERVE_LIMIT (16 * 1024 * 1024)

/* Global converter instance for this worker process */
static struct MarkdownConverterHandle *ngx_http_markdown_converter = NULL;

/* Global metrics instance for this worker process */
static ngx_http_markdown_metrics_t ngx_http_markdown_metrics;

/* Forward declarations */
static ngx_int_t ngx_http_markdown_filter_init(ngx_conf_t *cf);
static ngx_int_t ngx_http_markdown_init_worker(ngx_cycle_t *cycle);
static void ngx_http_markdown_exit_worker(ngx_cycle_t *cycle);
static void *ngx_http_markdown_create_conf(ngx_conf_t *cf);
static char *ngx_http_markdown_merge_conf(ngx_conf_t *cf, void *parent, void *child);

/* Configuration directive handlers */
static char *ngx_http_markdown_on_error(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_flavor(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_auth_policy(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_auth_cookies(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_conditional_requests(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_log_verbosity(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_stream_types(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
static char *ngx_http_markdown_metrics_directive(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);

/* Filter function declarations */
static ngx_int_t ngx_http_markdown_header_filter(ngx_http_request_t *r);
static ngx_int_t ngx_http_markdown_body_filter(ngx_http_request_t *r, ngx_chain_t *in);

/* Metrics endpoint handler */
static ngx_int_t ngx_http_markdown_metrics_handler(ngx_http_request_t *r);

/* Configuration logging helpers */
static ngx_uint_t ngx_http_markdown_log_verbosity_to_ngx_level(ngx_uint_t verbosity);
static const ngx_str_t *ngx_http_markdown_on_error_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_flavor_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_auth_policy_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_conditional_requests_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_log_verbosity_name(ngx_uint_t value);
static const ngx_str_t *ngx_http_markdown_compression_name(
    ngx_http_markdown_compression_type_e compression_type);
static void ngx_http_markdown_log_merged_conf(ngx_conf_t *cf, ngx_http_markdown_conf_t *conf);
static ngx_int_t ngx_http_markdown_forward_headers(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx);
static ngx_int_t ngx_http_markdown_send_buffered_original_response(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx);
static ngx_int_t ngx_http_markdown_fail_open_with_buffered_prefix(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx, ngx_chain_t *remaining);
#if !(NGX_HTTP_HEADERS)
static ngx_table_elt_t *ngx_http_markdown_find_request_header(ngx_http_request_t *r,
    const ngx_str_t *name);
#endif

/* Next filter pointers for filter chain */
static ngx_http_output_header_filter_pt ngx_http_next_header_filter;
static ngx_http_output_body_filter_pt ngx_http_next_body_filter;

/*
 * Module directives
 *
 * These directives control the behavior of the Markdown filter.
 * Each directive includes validation and clear error messages.
 */
static ngx_command_t ngx_http_markdown_filter_commands[] = {
    /*
     * markdown_filter on|off
     * 
     * Enables or disables Markdown conversion for this context.
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
        NGX_HTTP_MAIN_CONF|NGX_HTTP_SRV_CONF|NGX_HTTP_LOC_CONF|NGX_CONF_FLAG,
        ngx_conf_set_flag_slot,
        NGX_HTTP_LOC_CONF_OFFSET,
        offsetof(ngx_http_markdown_conf_t, enabled),
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
     * Use allow/deny directives to customize access control.
     */
    {
        ngx_string("markdown_metrics"),
        NGX_HTTP_LOC_CONF|NGX_CONF_NOARGS,
        ngx_http_markdown_metrics_directive,
        NGX_HTTP_LOC_CONF_OFFSET,
        0,
        NULL
    },
    
    ngx_null_command
};

/*
 * Module context
 *
 * Defines callbacks for configuration creation and merging.
 */
static ngx_http_module_t ngx_http_markdown_filter_module_ctx = {
    NULL,                                   /* preconfiguration */
    ngx_http_markdown_filter_init,          /* postconfiguration */
    NULL,                                   /* create main configuration */
    NULL,                                   /* init main configuration */
    NULL,                                   /* create server configuration */
    NULL,                                   /* merge server configuration */
    ngx_http_markdown_create_conf,          /* create location configuration */
    ngx_http_markdown_merge_conf            /* merge location configuration */
};

/*
 * Module definition
 */
ngx_module_t ngx_http_markdown_filter_module = {
    NGX_MODULE_V1,
    &ngx_http_markdown_filter_module_ctx,   /* module context */
    ngx_http_markdown_filter_commands,      /* module directives */
    NGX_HTTP_MODULE,                        /* module type */
    NULL,                                   /* init master */
    NULL,                                   /* init module */
    ngx_http_markdown_init_worker,          /* init process */
    NULL,                                   /* init thread */
    NULL,                                   /* exit thread */
    ngx_http_markdown_exit_worker,          /* exit process */
    NULL,                                   /* exit master */
    NGX_MODULE_V1_PADDING
};

/*
 * Create location configuration
 *
 * Allocates and initializes configuration structure with default values.
 * All values are set to NGX_CONF_UNSET* to enable proper inheritance.
 */
static void *
ngx_http_markdown_create_conf(ngx_conf_t *cf)
{
    ngx_http_markdown_conf_t *conf;
    
    conf = ngx_pcalloc(cf->pool, sizeof(ngx_http_markdown_conf_t));
    if (conf == NULL) {
        return NULL;
    }
    
    /* Set unset values (NGX_CONF_UNSET*) for proper inheritance */
    conf->enabled = NGX_CONF_UNSET;
    conf->max_size = NGX_CONF_UNSET_SIZE;
    conf->timeout = NGX_CONF_UNSET_MSEC;
    conf->on_error = NGX_CONF_UNSET_UINT;
    conf->flavor = NGX_CONF_UNSET_UINT;
    conf->token_estimate = NGX_CONF_UNSET;
    conf->front_matter = NGX_CONF_UNSET;
    conf->on_wildcard = NGX_CONF_UNSET;
    conf->auth_policy = NGX_CONF_UNSET_UINT;
    conf->auth_cookies = NGX_CONF_UNSET_PTR;
    conf->generate_etag = NGX_CONF_UNSET;
    conf->conditional_requests = NGX_CONF_UNSET_UINT;
    conf->log_verbosity = NGX_CONF_UNSET_UINT;
    conf->buffer_chunked = NGX_CONF_UNSET;
    conf->stream_types = NGX_CONF_UNSET_PTR;
    conf->auto_decompress = NGX_CONF_UNSET;
    
    return conf;
}

/*
 * Merge location configuration
 *
 * Implements configuration inheritance: location > server > http.
 * Child configuration values override parent values if set.
 * Unset child values inherit from parent.
 *
 * Default values (applied when both parent and child are unset):
 * - enabled: 0 (off)
 * - max_size: 10MB (10 * 1024 * 1024 bytes)
 * - timeout: 5000ms (5 seconds)
 * - on_error: NGX_HTTP_MARKDOWN_ON_ERROR_PASS (fail-open)
 * - flavor: NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK
 * - token_estimate: 0 (off)
 * - front_matter: 0 (off)
 * - on_wildcard: 0 (off)
 * - auth_policy: NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW
 * - auth_cookies: NULL (no patterns)
 * - generate_etag: 1 (on)
 * - conditional_requests: NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT
 * - log_verbosity: NGX_HTTP_MARKDOWN_LOG_INFO
 * - buffer_chunked: 1 (on)
 * - stream_types: NULL (no exclusions)
 * - auto_decompress: 1 (on)
 */
static char *
ngx_http_markdown_merge_conf(ngx_conf_t *cf, void *parent, void *child)
{
    ngx_http_markdown_conf_t *prev = parent;
    ngx_http_markdown_conf_t *conf = child;
    
    /* Merge flag and scalar configuration values */
    ngx_conf_merge_value(conf->enabled, prev->enabled, 0);
    ngx_conf_merge_size_value(conf->max_size, prev->max_size, 10 * 1024 * 1024); /* 10MB */
    ngx_conf_merge_msec_value(conf->timeout, prev->timeout, 5000); /* 5 seconds */
    ngx_conf_merge_uint_value(conf->on_error, prev->on_error, 
                              NGX_HTTP_MARKDOWN_ON_ERROR_PASS);
    ngx_conf_merge_uint_value(conf->flavor, prev->flavor, 
                              NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK);
    ngx_conf_merge_value(conf->token_estimate, prev->token_estimate, 0);
    ngx_conf_merge_value(conf->front_matter, prev->front_matter, 0);
    ngx_conf_merge_value(conf->on_wildcard, prev->on_wildcard, 0);
    ngx_conf_merge_uint_value(conf->auth_policy, prev->auth_policy, 
                              NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW);
    ngx_conf_merge_value(conf->generate_etag, prev->generate_etag, 1);
    ngx_conf_merge_uint_value(conf->conditional_requests, prev->conditional_requests,
                              NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT);
    ngx_conf_merge_uint_value(conf->log_verbosity, prev->log_verbosity,
                              NGX_HTTP_MARKDOWN_LOG_INFO);
    ngx_conf_merge_value(conf->buffer_chunked, prev->buffer_chunked, 1);
    ngx_conf_merge_value(conf->auto_decompress, prev->auto_decompress, 1);
    
    /* Merge pointer configuration values (arrays) */
    ngx_conf_merge_ptr_value(conf->auth_cookies, prev->auth_cookies, NULL);
    ngx_conf_merge_ptr_value(conf->stream_types, prev->stream_types, NULL);

    /* Startup/reload-time configuration snapshot (FR-12.7) */
    ngx_http_markdown_log_merged_conf(cf, conf);
    
    return NGX_CONF_OK;
}

/*
 * Configuration directive handler: markdown_on_error
 *
 * Parses and validates the markdown_on_error directive value.
 * Valid values: "pass" (fail-open) or "reject" (fail-closed)
 *
 * @param cf    Configuration structure
 * @param cmd   Command structure
 * @param conf  Module configuration
 * @return      NGX_CONF_OK on success, NGX_CONF_ERROR on error
 */
static char *
ngx_http_markdown_on_error(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    
    /* Get directive arguments */
    value = cf->args->elts;
    
    /* Check if already set in this context */
    if (mcf->on_error != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }
    
    /* Parse and validate value */
    if (ngx_strcmp(value[1].data, "pass") == 0) {
        mcf->on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    } else if (ngx_strcmp(value[1].data, "reject") == 0) {
        mcf->on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                          "invalid value \"%V\" in \"%V\" directive, "
                          "it must be \"pass\" or \"reject\"",
                          &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }
    
    return NGX_CONF_OK;
}

/*
 * Configuration directive handler: markdown_flavor
 *
 * Parses and validates the markdown_flavor directive value.
 * Valid values: "commonmark" or "gfm" (GitHub Flavored Markdown)
 *
 * @param cf    Configuration structure
 * @param cmd   Command structure
 * @param conf  Module configuration
 * @return      NGX_CONF_OK on success, NGX_CONF_ERROR on error
 */
static char *
ngx_http_markdown_flavor(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    
    /* Get directive arguments */
    value = cf->args->elts;
    
    /* Check if already set in this context */
    if (mcf->flavor != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }
    
    /* Parse and validate value */
    if (ngx_strcmp(value[1].data, "commonmark") == 0) {
        mcf->flavor = NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK;
    } else if (ngx_strcmp(value[1].data, "gfm") == 0) {
        mcf->flavor = NGX_HTTP_MARKDOWN_FLAVOR_GFM;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                          "invalid value \"%V\" in \"%V\" directive, "
                          "it must be \"commonmark\" or \"gfm\"",
                          &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }
    
    return NGX_CONF_OK;
}

/*
 * Configuration directive handler: markdown_auth_policy
 *
 * Parses and validates the markdown_auth_policy directive value.
 * Valid values: "allow" or "deny"
 *
 * @param cf    Configuration structure
 * @param cmd   Command structure
 * @param conf  Module configuration
 * @return      NGX_CONF_OK on success, NGX_CONF_ERROR on error
 */
static char *
ngx_http_markdown_auth_policy(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    
    /* Get directive arguments */
    value = cf->args->elts;
    
    /* Check if already set in this context */
    if (mcf->auth_policy != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }
    
    /* Parse and validate value */
    if (ngx_strcmp(value[1].data, "allow") == 0) {
        mcf->auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW;
    } else if (ngx_strcmp(value[1].data, "deny") == 0) {
        mcf->auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                          "invalid value \"%V\" in \"%V\" directive, "
                          "it must be \"allow\" or \"deny\"",
                          &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }
    
    return NGX_CONF_OK;
}

/*
 * Configuration directive handler: markdown_auth_cookies
 *
 * Parses cookie name patterns for authentication detection.
 * Supports multiple patterns (exact match, prefix match with *).
 *
 * Examples:
 *   markdown_auth_cookies session* auth_token;
 *   markdown_auth_cookies PHPSESSID wordpress_logged_in_*;
 *
 * @param cf    Configuration structure
 * @param cmd   Command structure
 * @param conf  Module configuration
 * @return      NGX_CONF_OK on success, NGX_CONF_ERROR on error
 */
static char *
ngx_http_markdown_auth_cookies(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    ngx_str_t                *pattern;
    ngx_uint_t                i;
    
    /* Get directive arguments */
    value = cf->args->elts;
    
    /* Check if already set in this context */
    if (mcf->auth_cookies != NGX_CONF_UNSET_PTR) {
        return "is duplicate";
    }
    
    /* Create array to store patterns */
    mcf->auth_cookies = ngx_array_create(cf->pool, cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mcf->auth_cookies == NULL) {
        return NGX_CONF_ERROR;
    }
    
    /* Add each pattern to the array */
    for (i = 1; i < cf->args->nelts; i++) {
        /* Validate pattern is not empty */
        if (value[i].len == 0) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                              "empty cookie pattern in \"%V\" directive",
                              &cmd->name);
            return NGX_CONF_ERROR;
        }
        
        /* Allocate and copy pattern */
        pattern = ngx_array_push(mcf->auth_cookies);
        if (pattern == NULL) {
            return NGX_CONF_ERROR;
        }
        
        *pattern = value[i];
        
        ngx_conf_log_error(NGX_LOG_DEBUG, cf, 0,
                          "markdown_auth_cookies: added pattern \"%V\"",
                          pattern);
    }
    
    return NGX_CONF_OK;
}

/*
 * Configuration directive handler: markdown_conditional_requests
 *
 * Parses and validates the markdown_conditional_requests directive value.
 * Valid values: "full_support", "if_modified_since_only", or "disabled"
 *
 * @param cf    Configuration structure
 * @param cmd   Command structure
 * @param conf  Module configuration
 * @return      NGX_CONF_OK on success, NGX_CONF_ERROR on error
 */
static char *
ngx_http_markdown_conditional_requests(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    
    /* Get directive arguments */
    value = cf->args->elts;
    
    /* Check if already set in this context */
    if (mcf->conditional_requests != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }
    
    /* Parse and validate value */
    if (ngx_strcmp(value[1].data, "full_support") == 0) {
        mcf->conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    } else if (ngx_strcmp(value[1].data, "if_modified_since_only") == 0) {
        mcf->conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;
    } else if (ngx_strcmp(value[1].data, "disabled") == 0) {
        mcf->conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                          "invalid value \"%V\" in \"%V\" directive, "
                          "it must be \"full_support\", \"if_modified_since_only\", or \"disabled\"",
                          &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }
    
    return NGX_CONF_OK;
}

/*
 * Configuration directive handler: markdown_log_verbosity
 *
 * Valid values: "error", "warn", "info", "debug"
 */
static char *
ngx_http_markdown_log_verbosity(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    value = cf->args->elts;

    if (mcf->log_verbosity != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_strcmp(value[1].data, "error") == 0) {
        mcf->log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    } else if (ngx_strcmp(value[1].data, "warn") == 0) {
        mcf->log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
    } else if (ngx_strcmp(value[1].data, "info") == 0) {
        mcf->log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    } else if (ngx_strcmp(value[1].data, "debug") == 0) {
        mcf->log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                          "invalid value \"%V\" in \"%V\" directive, "
                          "it must be \"error\", \"warn\", \"info\", or \"debug\"",
                          &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }

    return NGX_CONF_OK;
}

static ngx_uint_t
ngx_http_markdown_log_verbosity_to_ngx_level(ngx_uint_t verbosity)
{
    switch (verbosity) {
        case NGX_HTTP_MARKDOWN_LOG_ERROR:
            return NGX_LOG_ERR;
        case NGX_HTTP_MARKDOWN_LOG_WARN:
            return NGX_LOG_WARN;
        case NGX_HTTP_MARKDOWN_LOG_DEBUG:
            return NGX_LOG_DEBUG;
        case NGX_HTTP_MARKDOWN_LOG_INFO:
        default:
            return NGX_LOG_INFO;
    }
}

static const ngx_str_t *
ngx_http_markdown_on_error_name(ngx_uint_t value)
{
    static ngx_str_t  pass = ngx_string("pass");
    static ngx_str_t  reject = ngx_string("reject");
    static ngx_str_t  unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_ON_ERROR_PASS:
            return &pass;
        case NGX_HTTP_MARKDOWN_ON_ERROR_REJECT:
            return &reject;
        default:
            return &unknown;
    }
}

static const ngx_str_t *
ngx_http_markdown_flavor_name(ngx_uint_t value)
{
    static ngx_str_t  commonmark = ngx_string("commonmark");
    static ngx_str_t  gfm = ngx_string("gfm");
    static ngx_str_t  unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK:
            return &commonmark;
        case NGX_HTTP_MARKDOWN_FLAVOR_GFM:
            return &gfm;
        default:
            return &unknown;
    }
}

static const ngx_str_t *
ngx_http_markdown_auth_policy_name(ngx_uint_t value)
{
    static ngx_str_t  allow = ngx_string("allow");
    static ngx_str_t  deny = ngx_string("deny");
    static ngx_str_t  unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW:
            return &allow;
        case NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY:
            return &deny;
        default:
            return &unknown;
    }
}

static const ngx_str_t *
ngx_http_markdown_conditional_requests_name(ngx_uint_t value)
{
    static ngx_str_t  full_support = ngx_string("full_support");
    static ngx_str_t  if_modified_since_only = ngx_string("if_modified_since_only");
    static ngx_str_t  disabled = ngx_string("disabled");
    static ngx_str_t  unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT:
            return &full_support;
        case NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE:
            return &if_modified_since_only;
        case NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED:
            return &disabled;
        default:
            return &unknown;
    }
}

static const ngx_str_t *
ngx_http_markdown_log_verbosity_name(ngx_uint_t value)
{
    static ngx_str_t  error = ngx_string("error");
    static ngx_str_t  warn = ngx_string("warn");
    static ngx_str_t  info = ngx_string("info");
    static ngx_str_t  debug = ngx_string("debug");
    static ngx_str_t  unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_LOG_ERROR:
            return &error;
        case NGX_HTTP_MARKDOWN_LOG_WARN:
            return &warn;
        case NGX_HTTP_MARKDOWN_LOG_INFO:
            return &info;
        case NGX_HTTP_MARKDOWN_LOG_DEBUG:
            return &debug;
        default:
            return &unknown;
    }
}

static const ngx_str_t *
ngx_http_markdown_compression_name(ngx_http_markdown_compression_type_e compression_type)
{
    static ngx_str_t  gzip = ngx_string("gzip");
    static ngx_str_t  deflate = ngx_string("deflate");
    static ngx_str_t  brotli = ngx_string("brotli");
    static ngx_str_t  unknown = ngx_string("unknown");
    static ngx_str_t  invalid = ngx_string("invalid");

    switch (compression_type) {
        case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
            return &gzip;
        case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
            return &deflate;
        case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
            return &brotli;
        case NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN:
            return &unknown;
        default:
            return &invalid;
    }
}

static void
ngx_http_markdown_log_merged_conf(ngx_conf_t *cf, ngx_http_markdown_conf_t *conf)
{
    ngx_uint_t log_level;
    ngx_uint_t auth_cookie_count = (conf->auth_cookies != NULL) ? conf->auth_cookies->nelts : 0;
    ngx_uint_t stream_type_count = (conf->stream_types != NULL) ? conf->stream_types->nelts : 0;

    if (cf == NULL) {
        return;
    }

    log_level = ngx_http_markdown_log_verbosity_to_ngx_level(conf->log_verbosity);

    ngx_conf_log_error(log_level, cf, 0,
                      "markdown filter config: enabled=%ui max_size=%uz timeout_ms=%M "
                      "on_error=%V flavor=%V token_estimate=%ui front_matter=%ui "
                      "on_wildcard=%ui auth_policy=%V auth_cookie_patterns=%ui "
                      "etag=%ui conditional_requests=%V log_verbosity=%V "
                      "buffer_chunked=%ui stream_types=%ui",
                      (ngx_uint_t) conf->enabled,
                      conf->max_size,
                      conf->timeout,
                      ngx_http_markdown_on_error_name(conf->on_error),
                      ngx_http_markdown_flavor_name(conf->flavor),
                      (ngx_uint_t) conf->token_estimate,
                      (ngx_uint_t) conf->front_matter,
                      (ngx_uint_t) conf->on_wildcard,
                      ngx_http_markdown_auth_policy_name(conf->auth_policy),
                      auth_cookie_count,
                      (ngx_uint_t) conf->generate_etag,
                      ngx_http_markdown_conditional_requests_name(conf->conditional_requests),
                      ngx_http_markdown_log_verbosity_name(conf->log_verbosity),
                      (ngx_uint_t) conf->buffer_chunked,
                      stream_type_count);
}

/*
 * Configuration directive handler: markdown_stream_types
 *
 * Parses content type patterns to exclude from conversion.
 * These are typically streaming content types that should never be buffered.
 *
 * Examples:
 *   markdown_stream_types text/event-stream;
 *   markdown_stream_types text/event-stream application/x-ndjson;
 *
 * @param cf    Configuration structure
 * @param cmd   Command structure
 * @param conf  Module configuration
 * @return      NGX_CONF_OK on success, NGX_CONF_ERROR on error
 */
static char *
ngx_http_markdown_stream_types(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    ngx_str_t                *type;
    ngx_uint_t                i;
    
    /* Get directive arguments */
    value = cf->args->elts;
    
    /* Check if already set in this context */
    if (mcf->stream_types != NGX_CONF_UNSET_PTR) {
        return "is duplicate";
    }
    
    /* Create array to store content types */
    mcf->stream_types = ngx_array_create(cf->pool, cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mcf->stream_types == NULL) {
        return NGX_CONF_ERROR;
    }
    
    /* Add each content type to the array */
    for (i = 1; i < cf->args->nelts; i++) {
        /* Validate content type is not empty */
        if (value[i].len == 0) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                              "empty content type in \"%V\" directive",
                              &cmd->name);
            return NGX_CONF_ERROR;
        }
        
        /* Basic validation: must contain a slash */
        if (ngx_strchr(value[i].data, '/') == NULL) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                              "invalid content type \"%V\" in \"%V\" directive, "
                              "must be in format \"type/subtype\"",
                              &value[i], &cmd->name);
            return NGX_CONF_ERROR;
        }
        
        /* Allocate and copy content type */
        type = ngx_array_push(mcf->stream_types);
        if (type == NULL) {
            return NGX_CONF_ERROR;
        }
        
        *type = value[i];
        
        ngx_conf_log_error(NGX_LOG_DEBUG, cf, 0,
                          "markdown_stream_types: added type \"%V\"",
                          type);
    }
    
    return NGX_CONF_OK;
}

/*
 * Configuration directive handler: markdown_metrics
 *
 * Enables metrics exposure endpoint at this location.
 * The endpoint returns metrics in plain text or JSON format based on Accept header.
 * Access is restricted to localhost (127.0.0.1, ::1) by default for security.
 *
 * Example:
 *   location /markdown-metrics {
 *       markdown_metrics;
 *       # Optional: customize access control
 *       allow 10.0.0.0/8;
 *       deny all;
 *   }
 *
 * @param cf    Configuration structure
 * @param cmd   Command structure
 * @param conf  Module configuration (unused, directive takes no arguments)
 * @return      NGX_CONF_OK on success, NGX_CONF_ERROR on error
 *
 * Requirements: FR-13.5
 * Task: 22.4 Implement metrics exposure endpoint
 */
static char *
ngx_http_markdown_metrics_directive(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_core_loc_conf_t  *clcf;
    
    /*
     * Get the core location configuration for this location block.
     * We need to set the handler for this location to our metrics handler.
     */
    clcf = ngx_http_conf_get_module_loc_conf(cf, ngx_http_core_module);
    if (clcf == NULL) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                          "failed to get core location configuration for \"%V\" directive",
                          &cmd->name);
        return NGX_CONF_ERROR;
    }
    
    /*
     * Set the handler for this location to our metrics handler.
     * When a request matches this location, ngx_http_markdown_metrics_handler
     * will be called to generate the metrics response.
     */
    clcf->handler = ngx_http_markdown_metrics_handler;
    
    ngx_conf_log_error(NGX_LOG_INFO, cf, 0,
                      "markdown_metrics: endpoint enabled at this location");
    
    return NGX_CONF_OK;
}

/*
 * Module initialization
 *
 * Registers filter hooks in the NGINX filter chain.
 * This function is called during NGINX configuration phase to insert
 * our filters into the header and body filter chains.
 */
static ngx_int_t
ngx_http_markdown_filter_init(ngx_conf_t *cf)
{
    /*
     * Insert into header filter chain
     * 
     * The header filter is called when response headers are being sent.
     * We use it to check if conversion should be attempted and set up
     * the conversion context.
     */
    ngx_http_next_header_filter = ngx_http_top_header_filter;
    ngx_http_top_header_filter = ngx_http_markdown_header_filter;
    
    /*
     * Insert into body filter chain
     * 
     * The body filter is called for each chunk of the response body.
     * We use it to buffer the response and perform conversion when complete.
     */
    ngx_http_next_body_filter = ngx_http_top_body_filter;
    ngx_http_top_body_filter = ngx_http_markdown_body_filter;
    
    return NGX_OK;
}

/*
 * Worker process initialization
 *
 * Called once per worker process when NGINX starts or reloads.
 * Initializes the Rust converter instance for this worker.
 *
 * NGINX uses a multi-process worker model where each worker process
 * handles requests independently. Each worker needs its own converter
 * instance because:
 * 1. The converter is not thread-safe across workers
 * 2. Each worker operates in its own memory space
 * 3. No shared state is needed between workers
 *
 * @param cycle  The NGINX cycle structure
 * @return       NGX_OK on success, NGX_ERROR on failure
 */
static ngx_int_t
ngx_http_markdown_init_worker(ngx_cycle_t *cycle)
{
    /*
     * Initialize metrics structure.
     * 
     * All counters are initialized to zero. Since this is a global variable
     * in the worker process, it will be automatically zero-initialized by
     * the C runtime, but we explicitly zero it here for clarity.
     * 
     * Requirements: FR-09.8, FR-13.4
     */
    ngx_memzero(&ngx_http_markdown_metrics, sizeof(ngx_http_markdown_metrics_t));
    
    /*
     * Create a new converter instance for this worker process.
     * 
     * The converter is stored in a global variable that is local to
     * this worker process. Since NGINX uses a multi-process model
     * (not multi-threaded), this global is safe and won't be shared
     * across workers.
     */
    ngx_http_markdown_converter = markdown_converter_new();
    
    if (ngx_http_markdown_converter == NULL) {
        /*
         * Converter initialization failed.
         * 
         * This is a critical error because without a converter, the module
         * cannot function. We log a critical-level message and return NGX_ERROR,
         * which will cause NGINX to fail the worker initialization.
         * 
         * In production, this typically means:
         * - Memory allocation failed (system out of memory)
         * - Rust library initialization failed
         * 
         * The worker will not start, and NGINX will either:
         * - Retry with another worker (if configured)
         * - Fail to start entirely (if all workers fail)
         * 
         * Log level: NGX_LOG_CRIT (critical system failure)
         * Requirements: FR-09.5, FR-09.6, FR-09.7
         */
        ngx_log_error(NGX_LOG_CRIT, cycle->log, 0,
                      "markdown filter: failed to initialize converter in worker process, "
                      "category=system");
        return NGX_ERROR;
    }
    
    /*
     * Log successful initialization at info level.
     * 
     * This helps administrators verify that the module is properly
     * initialized in each worker process. The log message includes
     * the process ID to distinguish between workers.
     */
    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown filter: converter initialized in worker process (pid: %P)",
                  ngx_pid);
    
    /*
     * Log decompression support information.
     * 
     * This informs operators about which compression formats are supported
     * at startup. The brotli status depends on whether NGX_HTTP_BROTLI
     * was defined at compile time.
     * 
     * Requirements: 12.1, 12.2, 12.3, 12.4, 12.5
     */
#ifdef NGX_HTTP_BROTLI
    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown filter: decompression support: gzip=yes, deflate=yes, brotli=yes");
#else
    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown filter: decompression support: gzip=yes, deflate=yes, brotli=no");
#endif
    
    return NGX_OK;
}

/*
 * Worker process cleanup
 *
 * Called once per worker process when NGINX shuts down or reloads.
 * Cleans up the Rust converter instance for this worker.
 *
 * This function is called during graceful shutdown, after all active
 * requests have completed. It ensures proper resource cleanup to prevent
 * memory leaks.
 *
 * @param cycle  The NGINX cycle structure
 */
static void
ngx_http_markdown_exit_worker(ngx_cycle_t *cycle)
{
    /*
     * Check if converter was initialized.
     * 
     * The converter might be NULL if:
     * - Worker initialization failed
     * - This function is called multiple times (shouldn't happen)
     * - Module was never used in this worker
     */
    if (ngx_http_markdown_converter == NULL) {
        /*
         * No converter to clean up.
         * 
         * This is not an error - it just means the converter was never
         * initialized (perhaps due to initialization failure or module
         * not being used). We log at debug level for troubleshooting.
         */
        ngx_log_error(NGX_LOG_DEBUG, cycle->log, 0,
                      "markdown filter: no converter to clean up in worker process");
        return;
    }
    
    /*
     * Free the converter instance.
     * 
     * This calls the Rust FFI function to properly deallocate the
     * converter and all its internal resources. The function is safe
     * to call even if there are no active conversions (which there
     * shouldn't be at this point, since we're shutting down).
     */
    markdown_converter_free(ngx_http_markdown_converter);
    
    /*
     * Set the global pointer to NULL.
     * 
     * This prevents use-after-free bugs if this function is somehow
     * called multiple times, or if there are any lingering references
     * to the converter during shutdown.
     */
    ngx_http_markdown_converter = NULL;
    
    /*
     * Log successful cleanup at info level.
     * 
     * This helps administrators verify that resources are being properly
     * cleaned up during shutdown or reload. The log message includes
     * the process ID to distinguish between workers.
     */
    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
                  "markdown filter: converter cleaned up in worker process (pid: %P)",
                  ngx_pid);
}

/*
 * Header filter
 *
 * Called when response headers are being sent.
 * Checks if conversion should be attempted based on:
 * - Accept header (text/markdown)
 * - Configuration (enabled/disabled)
 * - Request method (GET/HEAD)
 * - Response status and content type
 * 
 * If conversion is eligible, sets up conversion context.
 * Otherwise, passes through to next filter.
 *
 * This is part of Task 14.4 setup - creates context for body buffering.
 */
static ngx_int_t
ngx_http_markdown_header_filter(ngx_http_request_t *r)
{
    ngx_http_markdown_ctx_t         *ctx;
    ngx_http_markdown_conf_t        *conf;
    ngx_http_markdown_eligibility_t  eligibility;
    ngx_int_t                        should_convert;

    /* Get module configuration */
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    if (conf == NULL || conf->enabled == 0) {
        /* Module disabled, pass through */
        return ngx_http_next_header_filter(r);
    }

    /* Check if client wants Markdown (Accept header) */
    should_convert = ngx_http_markdown_should_convert(r, conf);
    if (!should_convert) {
        /* Client doesn't want Markdown, pass through */
        return ngx_http_next_header_filter(r);
    }

    /* Check if response is eligible for conversion */
    eligibility = ngx_http_markdown_check_eligibility(r, conf);
    if (eligibility != NGX_HTTP_MARKDOWN_ELIGIBLE) {
        /* Not eligible, pass through */
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: response not eligible: %V",
                      ngx_http_markdown_eligibility_string(eligibility));
        return ngx_http_next_header_filter(r);
    }

    /* Create request context for buffering */
    ctx = ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_ctx_t));
    if (ctx == NULL) {
        /*
         * Context allocation failed - critical system error.
         * This means we're out of memory.
         * 
         * Log level: NGX_LOG_CRIT (critical system failure)
         * Requirements: FR-09.5, FR-09.6, FR-09.7
         */
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: failed to allocate context, category=system");
        return ngx_http_next_header_filter(r);
    }

    /* Initialize context */
    ctx->request = r;
    ctx->eligible = 1;
    ctx->buffer_initialized = 0;
    ctx->headers_forwarded = 0;
    ctx->conversion_attempted = 0;
    ctx->conversion_succeeded = 0;
    
    /* Initialize decompression state (Task 2.4 - Fast path initialization)
     * 
     * For uncompressed content, decompression_needed remains 0, ensuring
     * zero overhead in the body filter (no decompression functions called).
     * 
     * Requirements: 4.2, 10.3
     */
    ctx->compression_type = NGX_HTTP_MARKDOWN_COMPRESSION_NONE;
    ctx->decompression_needed = 0;
    ctx->decompression_done = 0;
    ctx->compressed_size = 0;
    ctx->decompressed_size = 0;

    /* Set context for this request */
    ngx_http_set_ctx(r, ctx, ngx_http_markdown_filter_module);

    /*
     * Detect compression type if auto_decompress is enabled (Task 2.1, 4.2)
     * 
     * Fast path: If compression_type == NONE, decompression_needed stays 0
     * Slow path: If compression detected, decompression_needed is set to 1
     * Special case: If compression_type == UNKNOWN, trigger fail-open immediately
     * 
     * Requirements: 1.1, 1.6, 4.2, 8.1, 10.3, 11.1, 11.5
     */
    if (conf->auto_decompress) {
        ctx->compression_type = ngx_http_markdown_detect_compression(r);
        
        if (ctx->compression_type == NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN) {
            /*
             * Unsupported compression format detected (Task 4.2)
             * 
             * This is an expected degradation scenario, not a failure.
             * We gracefully degrade by returning the original content.
             * 
             * Note: The warning log has already been emitted by
             * ngx_http_markdown_detect_compression() with the format name.
             * 
             * Requirements: 1.6, 11.5
             */
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown filter: unsupported compression format detected, "
                         "returning original content (fail-open)");
            
            /* Set eligible = 0 to trigger fail-open (return original content) */
            ctx->eligible = 0;
            
            /* Don't set decompression_needed - we're not attempting decompression */
            /* Don't increment failure counter - this is expected degradation */
            
        } else if (ctx->compression_type != NGX_HTTP_MARKDOWN_COMPRESSION_NONE) {
            /* Supported compression format - set flag for decompression */
            ctx->decompression_needed = 1;
            
            ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                          "markdown filter: decompression detected compression type: %d",
                          ctx->compression_type);
        }
    }

    /*
     * Request in-memory buffers from upstream filters/modules.
     *
     * Static file responses may otherwise arrive as file-backed buffers
     * (sendfile path), where `buf->pos..last` is empty and the payload is
     * only described by file offsets. This filter buffers and converts the
     * response body in userspace, so it requires in-memory data.
     */
    r->filter_need_in_memory = 1;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: response eligible for conversion, "
                  "context initialized");

    /*
     * Defer downstream header emission until the body filter completes
     * conversion (or decides to fail-open). This allows the module to set
     * accurate Content-Type / Content-Length / ETag headers based on the
     * converted Markdown output.
     */
    return NGX_OK;
}

/*
 * Construct base URL for resolving relative URLs
 *
 * This function constructs the base URL using the following priority order:
 * 1. X-Forwarded-Proto + X-Forwarded-Host (reverse proxy scenario)
 * 2. r->schema + r->headers_in.server (direct connection)
 * 3. server_name from configuration (fallback)
 *
 * The base_url is used by the Rust conversion engine to resolve relative URLs
 * in HTML to absolute URLs in the Markdown output.
 *
 * Format: scheme://host/uri
 * Example: https://example.com/docs/page.html
 *
 * @param r     The request structure
 * @param pool  Memory pool for allocation
 * @param base_url  Output parameter for constructed base URL
 * @return      NGX_OK on success, NGX_ERROR on failure
 *
 * Requirements: Design - URL Resolution, NGINX Integration
 * Task: 14.8 Implement base_url construction with X-Forwarded headers priority
 */
static ngx_int_t
ngx_http_markdown_construct_base_url(ngx_http_request_t *r, ngx_pool_t *pool,
    ngx_str_t *base_url)
{
    ngx_str_t                    scheme;
    ngx_str_t                    host;
    ngx_str_t                   *x_forwarded_proto;
    ngx_str_t                   *x_forwarded_host;
    ngx_http_core_srv_conf_t    *cscf;
    u_char                      *p;
    size_t                       len;

    /* Initialize output */
    base_url->data = NULL;
    base_url->len = 0;

    /*
     * Priority 1: X-Forwarded-Proto + X-Forwarded-Host (reverse proxy scenario)
     * 
     * When behind a reverse proxy (e.g., nginx proxy_pass, load balancer),
     * the X-Forwarded-* headers contain the original client request information.
     * This is the most specific and should be preferred.
     */
    x_forwarded_proto = NULL;
    x_forwarded_host = NULL;

    /* Check for X-Forwarded-Proto header */
    if (r->headers_in.headers.part.nelts > 0) {
        ngx_list_part_t  *part;
        ngx_table_elt_t  *header;
        ngx_uint_t        i;

        part = &r->headers_in.headers.part;
        header = part->elts;

        for (i = 0; /* void */; i++) {
            if (i >= part->nelts) {
                if (part->next == NULL) {
                    break;
                }
                part = part->next;
                header = part->elts;
                i = 0;
            }

            /* Case-insensitive comparison for X-Forwarded-Proto */
            if (header[i].key.len == sizeof("X-Forwarded-Proto") - 1
                && ngx_strncasecmp(header[i].key.data,
                                  (u_char *) "X-Forwarded-Proto",
                                  sizeof("X-Forwarded-Proto") - 1) == 0)
            {
                x_forwarded_proto = &header[i].value;
            }

            /* Case-insensitive comparison for X-Forwarded-Host */
            if (header[i].key.len == sizeof("X-Forwarded-Host") - 1
                && ngx_strncasecmp(header[i].key.data,
                                  (u_char *) "X-Forwarded-Host",
                                  sizeof("X-Forwarded-Host") - 1) == 0)
            {
                x_forwarded_host = &header[i].value;
            }
        }
    }

    /* If both X-Forwarded headers are present, use them */
    if (x_forwarded_proto != NULL && x_forwarded_host != NULL
        && x_forwarded_proto->len > 0 && x_forwarded_host->len > 0)
    {
        /* Validate X-Forwarded-Proto (must be http or https) */
        if ((x_forwarded_proto->len == 4
             && ngx_strncasecmp(x_forwarded_proto->data, (u_char *) "http", 4) == 0)
            || (x_forwarded_proto->len == 5
                && ngx_strncasecmp(x_forwarded_proto->data, (u_char *) "https", 5) == 0))
        {
            scheme = *x_forwarded_proto;
            host = *x_forwarded_host;
            goto construct_url;
        }
        /* Invalid X-Forwarded-Proto, fall through to next priority */
    }

    /*
     * Priority 2: r->schema + r->headers_in.server (direct connection)
     * 
     * When not behind a reverse proxy, use the request's schema and Host header.
     * This is the standard case for direct connections.
     */
    if (r->schema.len > 0 && r->headers_in.server.len > 0) {
        scheme = r->schema;
        host = r->headers_in.server;
        goto construct_url;
    }

    /*
     * Priority 3: server_name from configuration (fallback)
     * 
     * If no Host header is present, use the server_name from configuration.
     * This is the least specific but ensures we always have a base URL.
     */
    cscf = ngx_http_get_module_srv_conf(r, ngx_http_core_module);
    if (cscf != NULL && cscf->server_name.len > 0) {
        /* Use http as default scheme if not available */
        if (r->schema.len > 0) {
            scheme = r->schema;
        } else {
            ngx_str_set(&scheme, "http");
        }
        host = cscf->server_name;
        goto construct_url;
    }

    /* No valid source for base URL, return error */
    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                 "markdown filter: unable to construct base_url, "
                 "no valid scheme/host available");
    return NGX_ERROR;

construct_url:
    /*
     * Construct base_url as: scheme://host/uri
     * 
     * Format: <scheme>://<host><uri>
     * Example: https://example.com/docs/page.html
     */
    len = scheme.len;
    if (len > ((size_t) -1) - (sizeof("://") - 1)) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: base_url length overflow (scheme)");
        return NGX_ERROR;
    }
    len += sizeof("://") - 1;
    if (len > ((size_t) -1) - host.len) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: base_url length overflow (host)");
        return NGX_ERROR;
    }
    len += host.len;
    if (len > ((size_t) -1) - r->uri.len) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: base_url length overflow (uri)");
        return NGX_ERROR;
    }
    len += r->uri.len;
    
    p = ngx_pnalloc(pool, len);
    if (p == NULL) {
        /*
         * Memory allocation failed for base_url - critical system error.
         * 
         * Log level: NGX_LOG_CRIT (critical system failure)
         * Requirements: FR-09.5, FR-09.6, FR-09.7
         */
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: failed to allocate memory for base_url, category=system");
        return NGX_ERROR;
    }

    base_url->data = p;
    
    /* Copy scheme */
    p = ngx_cpymem(p, scheme.data, scheme.len);
    
    /* Add :// */
    *p++ = ':';
    *p++ = '/';
    *p++ = '/';
    
    /* Copy host */
    p = ngx_cpymem(p, host.data, host.len);
    
    /* Copy URI */
    p = ngx_cpymem(p, r->uri.data, r->uri.len);
    
    base_url->len = p - base_url->data;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: constructed base_url: \"%V\"", base_url);

    return NGX_OK;
}

/*
 * Body filter
 *
 * Called for each chunk of the response body.
 * Buffers the response and performs conversion when complete.
 * 
 * This implements Task 14.7: Body filter hook
 * - Accumulates response chunks in buffer
 * - Detects when all chunks are buffered (last_buf flag)
 * - Calls Rust conversion engine via FFI
 * - Updates response headers on success
 * - Sends converted Markdown response
 * - Handles errors with configured strategy
 *
 * Requirements: FR-02.4, FR-04.1, FR-09.1, FR-09.2, FR-10.1, FR-10.3
 * 
 * @param r   The request structure
 * @param in  The input chain containing response body chunks
 * @return    NGX_OK on success, NGX_ERROR on error
 */
static ngx_int_t
ngx_http_markdown_body_filter(ngx_http_request_t *r, ngx_chain_t *in)
{
    ngx_http_markdown_ctx_t   *ctx;
    ngx_http_markdown_conf_t  *conf;
    ngx_chain_t               *cl;
    ngx_chain_t               *out;
    ngx_buf_t                 *b;
    ngx_int_t                  rc;
    size_t                     chunk_size;
    ngx_flag_t                 last_buf;
    struct MarkdownOptions     options;
    struct MarkdownResult      result;
    ngx_time_t                *tp;
    ngx_msec_t                 start_time, end_time, elapsed_ms;
    size_t                     attempted_size;
    size_t                     reserve_hint;
    off_t                      content_length_n;

    /* Get module configuration */
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    if (conf == NULL || conf->enabled == 0) {
        /* Module disabled, pass through */
        return ngx_http_next_body_filter(r, in);
    }

    /* Get or create request context */
    ctx = ngx_http_get_module_ctx(r, ngx_http_markdown_filter_module);
    if (ctx == NULL) {
        /* No context means header filter didn't set up conversion */
        /* Pass through unchanged */
        return ngx_http_next_body_filter(r, in);
    }

    /* If not eligible for conversion, pass through */
    if (!ctx->eligible) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        /* Track bypassed request (ineligible for conversion) */
        ngx_atomic_fetch_add(&ngx_http_markdown_metrics.conversions_bypassed, 1);
        if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
            return NGX_ERROR;
        }
        return ngx_http_next_body_filter(r, in);
    }

    /* If conversion already attempted, pass through */
    if (ctx->conversion_attempted) {
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        return ngx_http_next_body_filter(r, in);
    }

    /* Initialize buffer on first body filter call */
    if (!ctx->buffer_initialized) {
        rc = ngx_http_markdown_buffer_init(&ctx->buffer, conf->max_size, r->pool);
        if (rc != NGX_OK) {
            /*
             * Buffer initialization failed - critical system error.
             * This typically means memory allocation failed.
             * 
             * Log level: NGX_LOG_CRIT (critical system failure)
             * Requirements: FR-09.5, FR-09.6, FR-09.7
             */
            ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                         "markdown filter: failed to initialize buffer, category=system");
            
            /* Apply failure strategy */
            if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
                /* Fail-closed: return error */
                return NGX_ERROR;
                } else {
                    /* Fail-open: pass through original response (FR-04.10, FR-09.1) */
                    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                                  "markdown filter: fail-open strategy - returning original HTML");
                    ctx->eligible = 0;
                    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
                    if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                        return NGX_ERROR;
                    }
                return ngx_http_next_body_filter(r, in);
            }
        }
        ctx->buffer_initialized = 1;

        /*
         * If upstream advertised Content-Length and it is within the module's
         * size limit, reserve once up front to reduce repeated growth/copy
         * cycles for larger responses.
         */
        content_length_n = r->headers_out.content_length_n;
        if (content_length_n > 0 && content_length_n <= (off_t) conf->max_size) {
            reserve_hint = (size_t) content_length_n;
            if (reserve_hint > NGX_HTTP_MARKDOWN_PRERESERVE_LIMIT) {
                reserve_hint = NGX_HTTP_MARKDOWN_PRERESERVE_LIMIT;
            }

            if (ngx_http_markdown_buffer_reserve(&ctx->buffer,
                    reserve_hint) != NGX_OK)
            {
                ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                             "markdown filter: failed to pre-reserve %uz bytes buffer capacity",
                             reserve_hint);
            }
        }
    }

    /* Accumulate response chunks in buffer and check for last buffer */
    last_buf = 0;
    for (cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL) {
            continue;
        }

        /* Calculate chunk size */
        chunk_size = cl->buf->last - cl->buf->pos;
        if (chunk_size > 0) {
            /* Append chunk to buffer with size limit enforcement */
            rc = ngx_http_markdown_buffer_append(&ctx->buffer, cl->buf->pos, chunk_size);
            if (rc != NGX_OK) {
                attempted_size = ctx->buffer.max_size;
                if (ctx->buffer.size <= ctx->buffer.max_size
                    && chunk_size <= (ctx->buffer.max_size - ctx->buffer.size))
                {
                    attempted_size = ctx->buffer.size + chunk_size;
                }

                /* Size limit exceeded (FR-10.1) */
                ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                             "markdown filter: response size exceeds limit, "
                             "size=%uz bytes, max=%uz bytes, category=resource_limit",
                             attempted_size, conf->max_size);
                
                /* Apply failure strategy (FR-10.3, FR-04.10, FR-09.1) */
                if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
                    /* Fail-closed: return error */
                    return NGX_ERROR;
                } else {
                    /* Fail-open: pass through original response */
                    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                                  "markdown filter: fail-open strategy - returning original HTML");
                    ctx->eligible = 0;
                    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
                    if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                        return NGX_ERROR;
                    }
                    return ngx_http_markdown_fail_open_with_buffered_prefix(r, ctx, cl);
                }
            }

            /*
             * Mark the source buffer as consumed after copying its bytes into
             * the module-owned accumulation buffer. This allows upstream copy
             * filter temporary buffers to be recycled for larger responses.
             */
            cl->buf->pos = cl->buf->last;
        }

        /*
         * Detect end of response body.
         *
         * For main requests, only `last_buf` marks the end of the full
         * response. `last_in_chain` may simply mean "last buffer in this
         * invocation's chain segment" and can appear before more body filter
         * calls (common with larger responses).
         *
         * For subrequests, `last_in_chain` is the terminal marker.
         */
        if (cl->buf->last_buf || (r != r->main && cl->buf->last_in_chain)) {
            last_buf = 1;
            break;
        }
    }

    /* If not last buffer, continue buffering */
    if (!last_buf) {
        /* More chunks expected, don't pass to next filter yet */
        r->buffered |= NGX_HTTP_MARKDOWN_BUFFERED;
        return NGX_OK;
    }

    /* Full response body received; this filter is no longer buffering input. */
    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;

    /*
     * Decompression handling (Task 2.2, 2.4)
     * 
     * Fast path for uncompressed content (Task 2.4, Requirements 4.2, 10.3):
     * When compression_type == NONE, decompression_needed is 0 (set in header filter),
     * so this entire block is skipped with zero overhead. No decompression functions
     * are called, and we proceed directly to conversion below.
     * 
     * Slow path for compressed content (Task 2.2):
     * If decompression is needed (detected in header filter), decompress the
     * buffered data before conversion. This must happen after buffering is
     * complete but before conversion starts.
     * 
     * Requirements: 4.2, 8.2, 8.3, 8.4, 10.3
     */
    if (ctx->decompression_needed && !ctx->decompression_done) {
        ngx_chain_t  *compressed_chain;
        ngx_chain_t  *decompressed_chain;
        ngx_buf_t    *compressed_buf;
        ngx_int_t     decompress_rc;
        const u_char *decompressed_data;
        u_char       *target_data;
        
        ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: starting decompression, type=%d, size=%uz bytes",
                      ctx->compression_type, ctx->buffer.size);
        
        /* Record compressed size before decompression */
        ctx->compressed_size = ctx->buffer.size;
        
        /* Create a chain from the buffered data */
        compressed_buf = ngx_create_temp_buf(r->pool, ctx->buffer.size);
        if (compressed_buf == NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: failed to create compressed buffer, "
                         "category=system");
            
            /* Apply failure strategy */
            if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
                return NGX_ERROR;
            } else {
                /* Fail-open: pass through original response */
                ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                              "markdown filter: fail-open strategy - returning original content");
                ctx->eligible = 0;
                r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
                if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                    return NGX_ERROR;
                }
                return ngx_http_markdown_send_buffered_original_response(r, ctx);
            }
        }
        
        if (ctx->buffer.size > 0) {
            if (ctx->buffer.data == NULL) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown filter: buffered payload pointer is NULL with non-zero size, "
                             "size=%uz, category=system",
                             ctx->buffer.size);

                if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
                    return NGX_ERROR;
                } else {
                    ctx->eligible = 0;
                    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
                    if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                        return NGX_ERROR;
                    }
                    return ngx_http_markdown_send_buffered_original_response(r, ctx);
                }
            }

            ngx_memcpy(compressed_buf->pos, ctx->buffer.data, ctx->buffer.size);
        }
        compressed_buf->last = compressed_buf->pos + ctx->buffer.size;
        compressed_buf->last_buf = 1;
        
        compressed_chain = ngx_alloc_chain_link(r->pool);
        if (compressed_chain == NULL) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: failed to allocate chain link, "
                         "category=system");
            
            /* Apply failure strategy */
            if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
                return NGX_ERROR;
            } else {
                ctx->eligible = 0;
                r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
                if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                    return NGX_ERROR;
                }
                return ngx_http_markdown_send_buffered_original_response(r, ctx);
            }
        }
        
        compressed_chain->buf = compressed_buf;
        compressed_chain->next = NULL;
        
        /* Call the unified decompression function */
        decompress_rc = ngx_http_markdown_decompress(r, ctx->compression_type,
                                                      compressed_chain,
                                                      &decompressed_chain);
        
        if (decompress_rc == NGX_DECLINED) {
            /*
             * Decompression not supported (Task 4.2, Task 4.3, Requirements 1.6, 3.3, 11.5)
             * 
             * This happens when:
             * 1. Compression format is unknown/unsupported (Task 4.2)
             * 2. Brotli compression detected but brotli module not available (Task 4.3)
             * 
             * For these cases, we always fail-open (return original content) regardless
             * of the on_error configuration, because this is not a decompression failure
             * but rather a graceful degradation scenario.
             * 
             * The warning log has already been logged by the decompression function.
             */
            ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                          "markdown filter: decompression not supported, "
                          "returning original content (fail-open)");
            
            /* Don't increment failure counter - this is expected degradation, not a failure */
            
            /* Always fail-open for unsupported formats (Requirement 11.5) */
            ctx->eligible = 0;
            r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
            if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                return NGX_ERROR;
            }
            return ngx_http_markdown_send_buffered_original_response(r, ctx);
        }
        
        if (decompress_rc != NGX_OK) {
            /* 
             * Decompression failed (Task 4.1, Requirements 6.1, 6.2, 6.3, 6.4)
             * 
             * This is a real decompression error (corrupted data, size limit exceeded, etc.)
             * The detailed error with specific error reason and category has already
             * been logged by the decompression function (ngx_http_markdown_decompress_gzip
             * or ngx_http_markdown_decompress_brotli). Here we log a summary with the
             * compression type name for easier troubleshooting.
             */
            const ngx_str_t *compression_name;

            compression_name = ngx_http_markdown_compression_name(
                ctx->compression_type);
            
            /* Log summary error (detailed error already logged by decompression function) */
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: decompression failed, compression=%V, "
                         "error=\"decompression error\", category=conversion",
                         compression_name);
            
            /* Update metrics (Requirement 6.4) */
            ngx_atomic_fetch_add(&ngx_http_markdown_metrics.decompressions_failed, 1);
            
            /* Apply failure strategy (Requirements 6.2, 6.3) */
            if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
                /* Fail-closed: return HTTP 502 error (Requirement 6.2) */
                return NGX_ERROR;
            } else {
                /* Fail-open: pass through original (compressed) content (Requirement 6.3) */
                ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                              "markdown filter: fail-open strategy - returning original content");
                ctx->eligible = 0;
                r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
                if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                    return NGX_ERROR;
                }
                return ngx_http_markdown_send_buffered_original_response(r, ctx);
            }
        }
        
        /* Decompression succeeded - replace buffered data with decompressed data */
        if (decompressed_chain == NULL || decompressed_chain->buf == NULL) {
            /* 
             * Decompression returned NULL chain (Task 4.1)
             * This is a system error - the decompression function should never
             * return NGX_OK with a NULL chain.
             */
            const ngx_str_t *compression_name;

            compression_name = ngx_http_markdown_compression_name(
                ctx->compression_type);
            
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: decompression returned NULL chain, "
                         "compression=%V, category=system",
                         compression_name);
            
            ngx_atomic_fetch_add(&ngx_http_markdown_metrics.decompressions_failed, 1);
            
            if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
                return NGX_ERROR;
            } else {
                ctx->eligible = 0;
                r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
                if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                    return NGX_ERROR;
                }
                return ngx_http_markdown_send_buffered_original_response(r, ctx);
            }
        }
        
        /* Calculate decompressed size */
        ctx->decompressed_size = decompressed_chain->buf->last - decompressed_chain->buf->pos;
        decompressed_data = decompressed_chain->buf->pos;
        
        /*
         * Compute copy target once so decompressed payload copy uses a single
         * code path regardless of whether reallocation is needed.
         */
        target_data = ctx->buffer.data;

        /* Ensure buffer has enough capacity */
        if (ctx->decompressed_size > ctx->buffer.capacity) {
            /* 
             * Need to reallocate buffer (Task 4.1)
             * This can happen if the decompressed size is larger than the
             * initial buffer capacity.
             */
            u_char *new_data = ngx_alloc(ctx->decompressed_size, r->connection->log);
            if (new_data == NULL) {
                const ngx_str_t *compression_name;

                compression_name = ngx_http_markdown_compression_name(
                    ctx->compression_type);
                
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown filter: failed to allocate decompressed buffer, "
                             "compression=%V, size=%uz, category=system",
                             compression_name, ctx->decompressed_size);
                
                ngx_atomic_fetch_add(&ngx_http_markdown_metrics.decompressions_failed, 1);
                
                if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
                    return NGX_ERROR;
                } else {
                    ctx->eligible = 0;
                    r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
                    if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                        return NGX_ERROR;
                    }
                    return ngx_http_markdown_send_buffered_original_response(r, ctx);
                }
            }

            target_data = new_data;
        }

        /*
         * Copy payload once using a unified condition. Skip when source/dest
         * are identical to avoid undefined behavior with overlapping memcpy.
         */
        if (ctx->decompressed_size > 0 && target_data != decompressed_data) {
            ngx_memcpy(target_data, decompressed_data, ctx->decompressed_size);
        }

        if (target_data != ctx->buffer.data) {
            /*
             * Copy has completed; now it is safe to release previous storage.
             * This ordering protects against future aliasing regressions.
             */
            if (ctx->buffer.data != NULL) {
                ngx_free(ctx->buffer.data);
            }

            ctx->buffer.data = target_data;
            ctx->buffer.capacity = ctx->decompressed_size;
        }

        ctx->buffer.size = ctx->decompressed_size;
        
        /* Mark decompression as done */
        ctx->decompression_done = 1;
        
        /* Update metrics */
        ngx_atomic_fetch_add(&ngx_http_markdown_metrics.decompressions_succeeded, 1);
        
        /* Update compression-type-specific metrics */
        switch (ctx->compression_type) {
            case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
                ngx_atomic_fetch_add(&ngx_http_markdown_metrics.decompressions_gzip, 1);
                break;
            case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
                ngx_atomic_fetch_add(&ngx_http_markdown_metrics.decompressions_deflate, 1);
                break;
            case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
                ngx_atomic_fetch_add(&ngx_http_markdown_metrics.decompressions_brotli, 1);
                break;
            default:
                break;
        }
        
        /* Log success */
        ngx_log_error(NGX_LOG_INFO, r->connection->log, 0,
                     "markdown filter: decompression succeeded, "
                     "compression=%d, compressed=%uz bytes, decompressed=%uz bytes, ratio=%.1fx",
                     ctx->compression_type, ctx->compressed_size, ctx->decompressed_size,
                     (float)ctx->decompressed_size / ctx->compressed_size);
        
        /* Remove Content-Encoding header after successful decompression (Task 2.3, Requirements 2.5, 10.4) */
        ngx_http_markdown_remove_content_encoding(r);
        
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: removed Content-Encoding header after decompression");
    }

    /* All chunks buffered - perform conversion */
    ctx->conversion_attempted = 1;
    
    /* Track conversion attempt */
    ngx_atomic_fetch_add(&ngx_http_markdown_metrics.conversions_attempted, 1);
    
    /* Initialize timing variables (will be set during conversion) */
    elapsed_ms = 0;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: buffered complete response, size: %uz bytes",
                  ctx->buffer.size);

    /*
     * Check for If-None-Match conditional request (Task 18.1)
     * 
     * This must be done AFTER buffering is complete but BEFORE conversion
     * (or as part of conversion if full_support mode is enabled).
     * 
     * Configuration modes:
     * - full_support: Perform conversion to generate ETag, compare, return 304 if match
     * - if_modified_since_only: Skip If-None-Match processing (performance optimization)
     * - disabled: No conditional request support for Markdown variants
     * 
     * Performance note: full_support mode requires conversion to generate ETag,
     * which has a performance cost. Administrators can use if_modified_since_only
     * mode to avoid this cost.
     * 
     * Best-practice note:
     * - This module handles Markdown-variant If-None-Match / ETag semantics.
     * - If-Modified-Since semantics are intentionally delegated to NGINX core
     *   using upstream Last-Modified handling, to avoid duplicating RFC 9110
     *   conditional logic inside the body filter path.
     *
     * Requirements: FR-06.1, FR-06.2, FR-06.3, FR-06.6
     */
    struct MarkdownResult *conditional_result = NULL;
    rc = ngx_http_markdown_handle_if_none_match(
        r, conf, ctx, ngx_http_markdown_converter, &conditional_result);
    
    if (rc == NGX_HTTP_NOT_MODIFIED) {
        /* ETag matches - send 304 Not Modified response */
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: If-None-Match matched, sending 304 Not Modified");
        
        /* Send 304 response with ETag and Vary headers */
        rc = ngx_http_markdown_send_304(r, conditional_result);
        
        /* Free conversion result */
        if (conditional_result != NULL) {
            markdown_result_free(conditional_result);
        }
        
        /* Return NGX_OK to indicate request is complete */
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        return NGX_OK;
    } else if (rc == NGX_ERROR) {
        /* Error during conditional request processing */
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: error during If-None-Match processing");
        
        /* Free conversion result if allocated */
        if (conditional_result != NULL) {
            markdown_result_free(conditional_result);
        }
        
        /* Apply failure strategy */
        if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
            /* Fail-closed: return error */
            return NGX_ERROR;
        } else {
            /* Fail-open: pass through original response */
            ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                          "markdown filter: fail-open strategy - returning original HTML");
            ctx->eligible = 0;
            r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
            if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                return NGX_ERROR;
            }
            return ngx_http_markdown_send_buffered_original_response(r, ctx);
        }
    } else if (rc == NGX_DECLINED && conditional_result != NULL) {
        /* If-None-Match was processed but didn't match - use existing conversion result */
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: If-None-Match did not match, using existing conversion");
        
        /* Use the conversion result from conditional request handling */
        ngx_memcpy(&result, conditional_result, sizeof(struct MarkdownResult));
        
        /* Free the conditional_result structure (but not the data inside, which we copied) */
        ngx_pfree(r->pool, conditional_result);
        
        /* Note: Timing is not available for conditional request path conversions.
         * This is a known limitation - the conditional handler performs conversion
         * internally without timing tracking. We set elapsed_ms to 0 to indicate
         * timing is not available for this request.
         */
        elapsed_ms = 0;
        
        /* Skip the conversion below since we already have the result */
        goto conversion_complete;
    }
    /* else: rc == NGX_DECLINED and conditional_result == NULL - no If-None-Match, proceed with conversion */

    /* Check if converter is initialized */
    if (ngx_http_markdown_converter == NULL) {
        /*
         * Converter not initialized - critical system error.
         * This should never happen in normal operation since converter
         * is initialized in worker init. If it does happen, it indicates
         * a serious system problem.
         * 
         * Log level: NGX_LOG_CRIT (critical system failure)
         * Requirements: FR-09.5, FR-09.6, FR-09.7
         */
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: converter not initialized, category=system");
        
        /* Apply failure strategy (FR-04.10, FR-09.1) */
        if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
            /* Fail-closed: return error */
            return NGX_ERROR;
        } else {
            /* Fail-open: pass through original response */
            ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                          "markdown filter: fail-open strategy - returning original HTML");
            ctx->eligible = 0;
            r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
            if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                return NGX_ERROR;
            }
            return ngx_http_markdown_send_buffered_original_response(r, ctx);
        }
    }

    /* Prepare conversion options */
    ngx_memzero(&options, sizeof(struct MarkdownOptions));
    options.flavor = conf->flavor;
    options.timeout_ms = conf->timeout;
    options.generate_etag = conf->generate_etag;
    options.estimate_tokens = conf->token_estimate;
    options.front_matter = conf->front_matter;
    options.content_type = NULL;
    options.content_type_len = 0;
    options.base_url = NULL;
    options.base_url_len = 0;

    /* Extract Content-Type header for charset detection */
    if (r->headers_out.content_type.len > 0) {
        options.content_type = r->headers_out.content_type.data;
        options.content_type_len = r->headers_out.content_type.len;
    }

    /* Construct base_url for URL resolution (Task 14.8) */
    ngx_str_t base_url;
    if (ngx_http_markdown_construct_base_url(r, r->pool, &base_url) == NGX_OK) {
        options.base_url = base_url.data;
        options.base_url_len = base_url.len;
    } else {
        /* Failed to construct base_url, log warning but continue without it */
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: continuing conversion without base_url");
    }

    /* Record start time for conversion timing */
    tp = ngx_timeofday();
    start_time = (ngx_msec_t)(tp->sec * 1000 + tp->msec);

    /* Call Rust conversion engine */
    ngx_memzero(&result, sizeof(struct MarkdownResult));
    markdown_convert(ngx_http_markdown_converter,
                    ctx->buffer.data,
                    ctx->buffer.size,
                    &options,
                    &result);
    
    /* Record end time and calculate elapsed time */
    tp = ngx_timeofday();
    end_time = (ngx_msec_t)(tp->sec * 1000 + tp->msec);
    elapsed_ms = end_time - start_time;

    /* Check conversion result */
    if (result.error_code != ERROR_SUCCESS) {
        /* Conversion failed - classify error (FR-09.6, FR-09.7) */
        ngx_http_markdown_error_category_t error_category;
        const ngx_str_t                *category_str;
        
        error_category = ngx_http_markdown_classify_error(result.error_code);
        category_str = ngx_http_markdown_error_category_string(error_category);
        
        /* Update failure metrics */
        ngx_atomic_fetch_add(&ngx_http_markdown_metrics.conversions_failed, 1);
        
        /* Update failure category metrics */
        switch (error_category) {
            case NGX_HTTP_MARKDOWN_ERROR_CONVERSION:
                ngx_atomic_fetch_add(&ngx_http_markdown_metrics.failures_conversion, 1);
                break;
            case NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT:
                ngx_atomic_fetch_add(&ngx_http_markdown_metrics.failures_resource_limit, 1);
                break;
            case NGX_HTTP_MARKDOWN_ERROR_SYSTEM:
                ngx_atomic_fetch_add(&ngx_http_markdown_metrics.failures_system, 1);
                break;
        }
        
        /* Log error with classification (FR-09.5, FR-09.6) */
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown filter: conversion failed, "
                     "error_code=%ud, category=%V, message=\"%*s\", elapsed_ms=%M",
                     result.error_code,
                     category_str,
                     (result.error_message != NULL) ? (ngx_int_t) result.error_len : 0,
                     (result.error_message != NULL) ? result.error_message : (u_char *) "",
                     elapsed_ms);
        
        /* Free result memory */
        markdown_result_free(&result);
        
        /* Apply failure strategy (FR-09.1, FR-09.2, FR-04.10) */
        if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
            /* Fail-closed: return error */
            return NGX_ERROR;
        } else {
            /* Fail-open: pass through original response (FR-04.10, FR-09.1) */
            ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                          "markdown filter: fail-open strategy - returning original HTML");
            ctx->eligible = 0;
            r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
            if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
                return NGX_ERROR;
            }
            return ngx_http_markdown_send_buffered_original_response(r, ctx);
        }
    }

    /* Conversion succeeded */
    ctx->conversion_succeeded = 1;

    /*
     * Validate FFI pointer/length invariants before consuming result buffers.
     * This prevents null-dereference/overread if upstream FFI contracts are broken.
     */
    if ((result.markdown == NULL && result.markdown_len > 0)
        || (result.error_message == NULL && result.error_len > 0)
        || (result.etag == NULL && result.etag_len > 0))
    {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: invalid FFI result invariants: "
                     "markdown=%p markdown_len=%uz etag=%p etag_len=%uz "
                     "error_message=%p error_len=%uz",
                     result.markdown, result.markdown_len,
                     result.etag, result.etag_len,
                     result.error_message, result.error_len);
        markdown_result_free(&result);

        if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT) {
            return NGX_ERROR;
        }

        ctx->eligible = 0;
        r->buffered &= ~NGX_HTTP_MARKDOWN_BUFFERED;
        if (ngx_http_markdown_forward_headers(r, ctx) != NGX_OK) {
            return NGX_ERROR;
        }
        return ngx_http_markdown_send_buffered_original_response(r, ctx);
    }
    
    /* Update success metrics */
    ngx_atomic_fetch_add(&ngx_http_markdown_metrics.conversions_succeeded, 1);
    ngx_atomic_fetch_add(&ngx_http_markdown_metrics.input_bytes, ctx->buffer.size);
    ngx_atomic_fetch_add(&ngx_http_markdown_metrics.output_bytes, result.markdown_len);
    ngx_atomic_fetch_add(&ngx_http_markdown_metrics.conversion_time_sum_ms, elapsed_ms);

conversion_complete:
    ngx_log_debug3(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: conversion succeeded, "
                  "input: %uz bytes, output: %uz bytes, elapsed: %M ms",
                  ctx->buffer.size, result.markdown_len, elapsed_ms);

    /* Update response headers (FR-04.1, FR-04.2, FR-04.3, FR-04.5, FR-15.2) */
    rc = ngx_http_markdown_update_headers(r, &result, conf);
    if (rc != NGX_OK) {
        /*
         * Header update failed - system error.
         * This typically means memory allocation failed.
         * 
         * Log level: NGX_LOG_CRIT (critical system failure)
         * Requirements: FR-09.5, FR-09.6, FR-09.7
         */
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: failed to update response headers, category=system");
        markdown_result_free(&result);
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_forward_headers(r, ctx);
    if (rc != NGX_OK) {
        markdown_result_free(&result);
        return rc;
    }

    /* Create output buffer */
    b = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        /*
         * Buffer allocation failed - critical system error.
         * 
         * Log level: NGX_LOG_CRIT (critical system failure)
         * Requirements: FR-09.5, FR-09.6, FR-09.7
         */
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: failed to allocate output buffer, category=system");
        markdown_result_free(&result);
        return NGX_ERROR;
    }

    /*
     * Handle HEAD requests per FR-04.9:
     * - Conversion is performed to calculate accurate Content-Length
     * - All headers are set correctly (Content-Type, Vary, ETag, etc.)
     * - Response body MUST be omitted per HTTP semantics
     */
    if (r->method == NGX_HTTP_HEAD) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: HEAD request - omitting response body");
        
        /* Create empty buffer for HEAD response (FR-04.9) */
        b->pos = NULL;
        b->last = NULL;
        b->memory = 0;
        b->last_buf = (r == r->main) ? 1 : 0;
        b->last_in_chain = 1;
        
        /* Free Rust-allocated memory */
        markdown_result_free(&result);
    } else {
        /* Regular GET request - include Markdown content in response body */
        if (result.markdown_len > 0) {
            /* Allocate memory for Markdown output in NGINX pool */
            b->pos = ngx_pnalloc(r->pool, result.markdown_len);
            if (b->pos == NULL) {
                /*
                 * Memory allocation failed for output - critical system error.
                 * 
                 * Log level: NGX_LOG_CRIT (critical system failure)
                 * Requirements: FR-09.5, FR-09.6, FR-09.7
                 */
                ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                             "markdown filter: failed to allocate output memory, category=system");
                markdown_result_free(&result);
                return NGX_ERROR;
            }

            /* Copy Markdown content to NGINX-managed memory */
            ngx_memcpy(b->pos, result.markdown, result.markdown_len);
            b->last = b->pos + result.markdown_len;
            b->memory = 1;
        } else {
            /* Empty markdown output is valid; send an empty body safely. */
            b->pos = NULL;
            b->last = NULL;
            b->memory = 0;
        }

        b->last_buf = (r == r->main) ? 1 : 0;
        b->last_in_chain = 1;

        /* Free Rust-allocated memory */
        markdown_result_free(&result);
    }

    /* Create output chain */
    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        /*
         * Chain link allocation failed - critical system error.
         * 
         * Log level: NGX_LOG_CRIT (critical system failure)
         * Requirements: FR-09.5, FR-09.6, FR-09.7
         */
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown filter: failed to allocate output chain, category=system");
        return NGX_ERROR;
    }
    out->buf = b;
    out->next = NULL;

    /* Send converted Markdown response */
    return ngx_http_next_body_filter(r, out);
}

/*
 * Metrics endpoint handler
 *
 * This handler responds to requests for the metrics endpoint.
 * It returns metrics in plain text or JSON format based on the Accept header.
 * Access is restricted to localhost (127.0.0.1, ::1) by default for security.
 *
 * Response formats:
 * - Plain text (default): Accept: text/plain or no Accept header
 * - JSON: Accept: application/json
 *
 * Security:
 * - Only accessible from localhost by default
 * - Use NGINX allow/deny directives to customize access control
 *
 * Example configuration:
 *   location /markdown-metrics {
 *       markdown_metrics;
 *       allow 127.0.0.1;
 *       allow ::1;
 *       deny all;
 *   }
 *
 * @param r  The request structure
 * @return   NGX_OK on success, NGX_HTTP_* error code on failure
 *
 * Requirements: FR-13.5
 * Task: 22.4 Implement metrics exposure endpoint
 */
static ngx_int_t
ngx_http_markdown_metrics_handler(ngx_http_request_t *r)
{
    ngx_int_t     rc;
    ngx_buf_t    *b;
    ngx_chain_t   out;
    u_char       *p;
    size_t        len;
    ngx_flag_t    json_format;
    
    /* Only allow GET and HEAD methods */
    if (!(r->method & (NGX_HTTP_GET|NGX_HTTP_HEAD))) {
        return NGX_HTTP_NOT_ALLOWED;
    }
    
    /*
     * Security: Restrict access to localhost by default
     * 
     * Check if the request is from localhost (127.0.0.1 or ::1).
     * This provides a secure default, but administrators can override
     * this using NGINX's allow/deny directives in the location block.
     * 
     * Note: This is a basic check. For production use, administrators
     * should configure explicit allow/deny rules in the location block.
     */
    if (r->connection->sockaddr->sa_family == AF_INET) {
        struct sockaddr_in *sin = (struct sockaddr_in *) r->connection->sockaddr;
        /* Check for 127.0.0.1 (localhost) */
        if (ntohl(sin->sin_addr.s_addr) != INADDR_LOOPBACK) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown_metrics: access denied from non-localhost IPv4 address");
            return NGX_HTTP_FORBIDDEN;
        }
    }
#if (NGX_HAVE_INET6)
    else if (r->connection->sockaddr->sa_family == AF_INET6) {
        struct sockaddr_in6 *sin6 = (struct sockaddr_in6 *) r->connection->sockaddr;
        /* Check for ::1 (localhost) */
        if (!IN6_IS_ADDR_LOOPBACK(&sin6->sin6_addr)) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                         "markdown_metrics: access denied from non-localhost IPv6 address");
            return NGX_HTTP_FORBIDDEN;
        }
    }
#endif
    else {
        /* Unknown address family - deny by default */
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown_metrics: access denied from unknown address family");
        return NGX_HTTP_FORBIDDEN;
    }
    
    /*
     * Determine output format based on Accept header
     * 
     * - Accept: application/json -> JSON format
     * - Accept: text/plain or no Accept header -> Plain text format (default)
     */
    json_format = 0;
    {
        ngx_table_elt_t *accept_header = NULL;

#if (NGX_HTTP_HEADERS)
        accept_header = r->headers_in.accept;
#else
        static ngx_str_t  accept_name = ngx_string("Accept");

        accept_header = ngx_http_markdown_find_request_header(
            r, &accept_name);
#endif

        if (accept_header != NULL
            && ngx_strstr(accept_header->value.data, "application/json") != NULL)
        {
            json_format = 1;
        }
    }
    
    /*
     * Discard request body if present
     * 
     * The metrics endpoint doesn't need the request body, so we discard it.
     * This is a standard pattern for GET endpoints in NGINX.
     */
    rc = ngx_http_discard_request_body(r);
    if (rc != NGX_OK) {
        return rc;
    }
    
    /*
     * Allocate buffer for response
     * 
     * We allocate a buffer large enough to hold the metrics output.
     * The buffer size is estimated based on the format:
     * - Plain text: ~500 bytes
     * - JSON: ~800 bytes
     * 
     * We allocate 2048 bytes to be safe and allow for future expansion.
     */
    b = ngx_create_temp_buf(r->pool, 2048);
    if (b == NULL) {
        ngx_log_error(NGX_LOG_CRIT, r->connection->log, 0,
                     "markdown_metrics: failed to allocate response buffer");
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }
    
    /*
     * Format metrics output
     * 
     * Read metrics from the global metrics structure and format them
     * according to the requested format (plain text or JSON).
     */
    p = b->pos;
    
    if (json_format) {
        /*
         * JSON format
         * 
         * Format metrics as JSON object with all fields.
         * This format is machine-readable and suitable for monitoring tools.
         * 
         * Example output:
         * {
         *   "conversions_attempted": 100,
         *   "conversions_succeeded": 95,
         *   "conversions_failed": 3,
         *   "conversions_bypassed": 2,
         *   "failures_conversion": 2,
         *   "failures_resource_limit": 1,
         *   "failures_system": 0,
         *   "conversion_time_sum_ms": 1250,
         *   "input_bytes": 524288,
         *   "output_bytes": 131072,
         *   "decompressions_attempted": 50,
         *   "decompressions_succeeded": 48,
         *   "decompressions_failed": 2,
         *   "decompressions_gzip": 40,
         *   "decompressions_deflate": 8,
         *   "decompressions_brotli": 0
         * }
         */
        p = ngx_slprintf(p, b->end,
            "{\n"
            "  \"conversions_attempted\": %uA,\n"
            "  \"conversions_succeeded\": %uA,\n"
            "  \"conversions_failed\": %uA,\n"
            "  \"conversions_bypassed\": %uA,\n"
            "  \"failures_conversion\": %uA,\n"
            "  \"failures_resource_limit\": %uA,\n"
            "  \"failures_system\": %uA,\n"
            "  \"conversion_time_sum_ms\": %uA,\n"
            "  \"input_bytes\": %uA,\n"
            "  \"output_bytes\": %uA,\n"
            "  \"decompressions_attempted\": %uA,\n"
            "  \"decompressions_succeeded\": %uA,\n"
            "  \"decompressions_failed\": %uA,\n"
            "  \"decompressions_gzip\": %uA,\n"
            "  \"decompressions_deflate\": %uA,\n"
            "  \"decompressions_brotli\": %uA\n"
            "}",
            ngx_http_markdown_metrics.conversions_attempted,
            ngx_http_markdown_metrics.conversions_succeeded,
            ngx_http_markdown_metrics.conversions_failed,
            ngx_http_markdown_metrics.conversions_bypassed,
            ngx_http_markdown_metrics.failures_conversion,
            ngx_http_markdown_metrics.failures_resource_limit,
            ngx_http_markdown_metrics.failures_system,
            ngx_http_markdown_metrics.conversion_time_sum_ms,
            ngx_http_markdown_metrics.input_bytes,
            ngx_http_markdown_metrics.output_bytes,
            ngx_http_markdown_metrics.decompressions_attempted,
            ngx_http_markdown_metrics.decompressions_succeeded,
            ngx_http_markdown_metrics.decompressions_failed,
            ngx_http_markdown_metrics.decompressions_gzip,
            ngx_http_markdown_metrics.decompressions_deflate,
            ngx_http_markdown_metrics.decompressions_brotli);
    } else {
        /*
         * Plain text format
         * 
         * Format metrics as human-readable plain text with labels.
         * This format is easy to read and suitable for manual inspection.
         * 
         * Example output:
         * Markdown Filter Metrics
         * =======================
         * Conversions Attempted: 100
         * Conversions Succeeded: 95
         * Conversions Failed: 3
         * Conversions Bypassed: 2
         * 
         * Failure Breakdown:
         * - Conversion Errors: 2
         * - Resource Limit Exceeded: 1
         * - System Errors: 0
         * 
         * Performance:
         * - Total Conversion Time: 1250 ms
         * - Total Input Bytes: 524288
         * - Total Output Bytes: 131072
         * 
         * Decompression Statistics:
         * - Decompressions Attempted: 50
         * - Decompressions Succeeded: 48
         * - Decompressions Failed: 2
         * - Gzip Decompressions: 40
         * - Deflate Decompressions: 8
         * - Brotli Decompressions: 0
         */
        p = ngx_slprintf(p, b->end,
            "Markdown Filter Metrics\n"
            "=======================\n"
            "Conversions Attempted: %uA\n"
            "Conversions Succeeded: %uA\n"
            "Conversions Failed: %uA\n"
            "Conversions Bypassed: %uA\n"
            "\n"
            "Failure Breakdown:\n"
            "- Conversion Errors: %uA\n"
            "- Resource Limit Exceeded: %uA\n"
            "- System Errors: %uA\n"
            "\n"
            "Performance:\n"
            "- Total Conversion Time: %uA ms\n"
            "- Total Input Bytes: %uA\n"
            "- Total Output Bytes: %uA\n"
            "\n"
            "Decompression Statistics:\n"
            "- Decompressions Attempted: %uA\n"
            "- Decompressions Succeeded: %uA\n"
            "- Decompressions Failed: %uA\n"
            "- Gzip Decompressions: %uA\n"
            "- Deflate Decompressions: %uA\n"
            "- Brotli Decompressions: %uA\n",
            ngx_http_markdown_metrics.conversions_attempted,
            ngx_http_markdown_metrics.conversions_succeeded,
            ngx_http_markdown_metrics.conversions_failed,
            ngx_http_markdown_metrics.conversions_bypassed,
            ngx_http_markdown_metrics.failures_conversion,
            ngx_http_markdown_metrics.failures_resource_limit,
            ngx_http_markdown_metrics.failures_system,
            ngx_http_markdown_metrics.conversion_time_sum_ms,
            ngx_http_markdown_metrics.input_bytes,
            ngx_http_markdown_metrics.output_bytes,
            ngx_http_markdown_metrics.decompressions_attempted,
            ngx_http_markdown_metrics.decompressions_succeeded,
            ngx_http_markdown_metrics.decompressions_failed,
            ngx_http_markdown_metrics.decompressions_gzip,
            ngx_http_markdown_metrics.decompressions_deflate,
            ngx_http_markdown_metrics.decompressions_brotli);
    }
    
    /* Calculate actual content length */
    len = p - b->pos;
    b->last = p;
    
    /*
     * Set response headers
     * 
     * - Status: 200 OK
     * - Content-Type: application/json or text/plain
     * - Content-Length: actual length of the response body
     */
    r->headers_out.status = NGX_HTTP_OK;
    r->headers_out.content_length_n = len;
    
    if (json_format) {
        ngx_str_set(&r->headers_out.content_type, "application/json");
    } else {
        ngx_str_set(&r->headers_out.content_type, "text/plain");
    }
    r->headers_out.content_type_len = r->headers_out.content_type.len;
    
    /*
     * Mark buffer as last in chain
     * 
     * This tells NGINX that this is the final buffer in the response.
     */
    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;
    
    /*
     * Send response headers
     */
    rc = ngx_http_send_header(r);
    if (rc == NGX_ERROR || rc > NGX_OK || r->header_only) {
        return rc;
    }
    
    /*
     * Send response body
     * 
     * For HEAD requests, we don't send the body (r->header_only is set).
     * For GET requests, we send the formatted metrics.
     */
    out.buf = b;
    out.next = NULL;
    
    return ngx_http_output_filter(r, &out);
}

/*
 * Find a request header by name in the generic headers list.
 *
 * This fallback is used for builds where some convenience pointers in
 * ngx_http_headers_in_t (for example `accept`) are not compiled in.
 */
#if !(NGX_HTTP_HEADERS)
static ngx_table_elt_t *
ngx_http_markdown_find_request_header(ngx_http_request_t *r, const ngx_str_t *name)
{
    ngx_list_part_t *part;
    ngx_table_elt_t *headers;
    ngx_uint_t       i;

    if (r == NULL || name == NULL || name->len == 0) {
        return NULL;
    }

    part = &r->headers_in.headers.part;
    headers = part->elts;

    for ( ;; ) {
        for (i = 0; i < part->nelts; i++) {
            if (headers[i].key.len == name->len
                && ngx_strncasecmp(headers[i].key.data, name->data, name->len) == 0)
            {
                return &headers[i];
            }
        }

        if (part->next == NULL) {
            break;
        }

        part = part->next;
        headers = part->elts;
    }

    return NULL;
}
#endif

static ngx_int_t
ngx_http_markdown_forward_headers(ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    ngx_int_t rc;

    if (ctx == NULL || ctx->headers_forwarded) {
        return NGX_OK;
    }

    rc = ngx_http_next_header_filter(r);
    if (rc == NGX_ERROR || rc > NGX_OK) {
        return rc;
    }

    ctx->headers_forwarded = 1;
    return rc;
}

/*
 * Send the fully buffered original HTML response (fail-open path).
 *
 * This is used when conversion fails after the module has already buffered and
 * consumed the entire upstream body. At that point, forwarding the current
 * input chain would lose data; we must emit the buffered original body.
 */
static ngx_int_t
ngx_http_markdown_send_buffered_original_response(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx)
{
    ngx_buf_t    *b;
    ngx_chain_t  *out;

    if (ctx == NULL) {
        return NGX_ERROR;
    }

    b = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        return NGX_ERROR;
    }

    if (r->method == NGX_HTTP_HEAD || ctx->buffer.size == 0) {
        b->pos = NULL;
        b->last = NULL;
        b->memory = 0;
    } else {
        b->pos = ctx->buffer.data;
        b->last = ctx->buffer.data + ctx->buffer.size;
        b->memory = 1;
    }

    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        return NGX_ERROR;
    }
    out->buf = b;
    out->next = NULL;

    return ngx_http_next_body_filter(r, out);
}

/*
 * Fail-open while buffering is in progress by replaying the already-buffered
 * prefix and then forwarding the unconsumed upstream chain.
 *
 * This preserves correctness when a size limit is exceeded after some chunks
 * were already copied into the module buffer and marked consumed.
 */
static ngx_int_t
ngx_http_markdown_fail_open_with_buffered_prefix(ngx_http_request_t *r,
    ngx_http_markdown_ctx_t *ctx, ngx_chain_t *remaining)
{
    ngx_buf_t    *b;
    ngx_chain_t  *out;

    if (ctx == NULL || ctx->buffer.size == 0) {
        return ngx_http_next_body_filter(r, remaining);
    }

    b = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        return NGX_ERROR;
    }

    b->pos = ctx->buffer.data;
    b->last = ctx->buffer.data + ctx->buffer.size;
    b->memory = 1;
    b->last_buf = 0;
    b->last_in_chain = 0;

    out = ngx_alloc_chain_link(r->pool);
    if (out == NULL) {
        return NGX_ERROR;
    }
    out->buf = b;
    out->next = remaining;

    return ngx_http_next_body_filter(r, out);
}
