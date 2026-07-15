/*
 * NGINX Markdown Filter Module - Dynconf Snapshot Introspection
 *
 * Implements the active_snapshot query mechanism that serializes
 * all dynconf-relevant configuration fields to JSON format.
 *
 * This function is called by the diagnostics handler (E01.3) to
 * expose the current configuration state.  It reads the location
 * config struct and produces key-value pairs for all configuration
 * items that affect module behavior.
 *
 * Requirement: REQ-0700-OPERABILITY-003
 * Risk Pack: dynamic-config-hot-reload
 * Rules: 34, 35
 */

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#include "ngx_http_markdown_dynconf_snapshot.h"
#include "ngx_http_markdown_filter_module.h"


/*
 * Helper: append a flag field as "on" or "off".
 *
 * Parameters:
 *   p    - Current write position
 *   last - End of buffer
 *   key  - JSON key name
 *   val  - Flag value (0=off, 1=on, NGX_CONF_UNSET=unset)
 *   trailing_comma - 1 to append comma, 0 for last field
 *
 * Returns:
 *   Updated write position
 */
static u_char *
ngx_http_markdown_snapshot_flag(u_char *p, u_char *last,
    const char *key, ngx_flag_t val, ngx_uint_t trailing_comma)
{
    const char  *str_val;

    if (val == NGX_CONF_UNSET || val == 0) {
        str_val = "off";
    } else {
        str_val = "on";
    }

    if (trailing_comma) {
        p = ngx_slprintf(p, last, "    \"%s\": \"%s\",\n", key, str_val);
    } else {
        p = ngx_slprintf(p, last, "    \"%s\": \"%s\"\n", key, str_val);
    }

    return p;
}


/*
 * Helper: append a size_t field as a numeric string.
 *
 * Parameters:
 *   p    - Current write position
 *   last - End of buffer
 *   key  - JSON key name
 *   val  - Size value in bytes
 *   trailing_comma - 1 to append comma, 0 for last field
 *
 * Returns:
 *   Updated write position
 */
static u_char *
ngx_http_markdown_snapshot_size(u_char *p, u_char *last,
    const char *key, size_t val, ngx_uint_t trailing_comma)
{
    if (trailing_comma) {
        p = ngx_slprintf(p, last, "    \"%s\": \"%uz\",\n", key, val);
    } else {
        p = ngx_slprintf(p, last, "    \"%s\": \"%uz\"\n", key, val);
    }

    return p;
}


/*
 * Helper: append a millisecond field as a numeric string.
 *
 * Parameters:
 *   p    - Current write position
 *   last - End of buffer
 *   key  - JSON key name
 *   val  - Millisecond value
 *   trailing_comma - 1 to append comma, 0 for last field
 *
 * Returns:
 *   Updated write position
 */
static u_char *
ngx_http_markdown_snapshot_msec(u_char *p, u_char *last,
    const char *key, ngx_msec_t val, ngx_uint_t trailing_comma)
{
    if (trailing_comma) {
        p = ngx_slprintf(p, last, "    \"%s\": \"%M\",\n", key, val);
    } else {
        p = ngx_slprintf(p, last, "    \"%s\": \"%M\"\n", key, val);
    }

    return p;
}


/*
 * Helper: append a string field as a quoted JSON value.
 *
 * Parameters:
 *   p    - Current write position
 *   last - End of buffer
 *   key  - JSON key name
 *   val  - String value
 *   trailing_comma - 1 to append comma, 0 for last field
 *
 * Returns:
 *   Updated write position
 */
static u_char *
ngx_http_markdown_snapshot_str(u_char *p, u_char *last,
    const char *key, const char *val, ngx_uint_t trailing_comma)
{
    if (trailing_comma) {
        p = ngx_slprintf(p, last, "    \"%s\": \"%s\",\n", key, val);
    } else {
        p = ngx_slprintf(p, last, "    \"%s\": \"%s\"\n", key, val);
    }

    return p;
}


/*
 * Map the effective error policy to its Config V2 string representation
 * (markdown_error_policy pass|fail_closed|status <code>).
 *
 * Derived from on_error (pass/reject) and error_status (the reject status
 * code).  fail_closed is the canonical name for reject with the default 502.
 */
static const char *
ngx_http_markdown_error_policy_str(ngx_uint_t on_error, ngx_uint_t error_status)
{
    if (on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS) {
        return "pass";
    }

    switch (error_status) {
    case 429:
        return "status_429";
    case 503:
        return "status_503";
    case 502:
    default:
        return "fail_closed";
    }
}


/*
 * Map flavor enum to string representation.
 */
static const char *
ngx_http_markdown_flavor_str(ngx_uint_t val)
{
    switch (val) {
    case NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK:
        return "commonmark";
    case NGX_HTTP_MARKDOWN_FLAVOR_GFM:
        return "gfm";
    default:
        return "unknown";
    }
}


/*
 * Map markdown_accept policy enum to string representation (Config V2, 0.9.0).
 */
static const char *
ngx_http_markdown_accept_policy_str(ngx_uint_t val)
{
    switch (val) {
    case NGX_HTTP_MARKDOWN_ACCEPT_STRICT:
        return "strict";
    case NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD:
        return "wildcard";
    case NGX_HTTP_MARKDOWN_ACCEPT_FORCE:
        return "force";
    default:
        return "unknown";
    }
}


/*
 * Map the effective cache-validation state to its Config V2 string
 * representation (markdown_cache_validation off|ims_only|full).
 *
 * Derived from policy.conditional_requests, which markdown_cache_validation
 * keeps consistent with policy.generate_etag.
 */
static const char *
ngx_http_markdown_cache_validation_str(ngx_uint_t conditional_requests)
{
    switch (conditional_requests) {
    case NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED:
        return "off";
    case NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE:
        return "ims_only";
    case NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT:
        return "full";
    default:
        return "unknown";
    }
}


/*
 * Map metrics_format enum to string representation.
 */
static const char *
ngx_http_markdown_metrics_format_str(ngx_uint_t val)
{
    switch (val) {
    case NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO:
        return "auto";
    case NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS:
        return "prometheus";
    default:
        return "unknown";
    }
}


/*
 * Map log_verbosity enum to string representation.
 */
static const char *
ngx_http_markdown_log_verbosity_str(ngx_uint_t val)
{
    switch (val) {
    case NGX_HTTP_MARKDOWN_LOG_ERROR:
        return "error";
    case NGX_HTTP_MARKDOWN_LOG_WARN:
        return "warn";
    case NGX_HTTP_MARKDOWN_LOG_INFO:
        return "info";
    case NGX_HTTP_MARKDOWN_LOG_DEBUG:
        return "debug";
    default:
        return "unknown";
    }
}


#ifdef MARKDOWN_STREAMING_ENABLED
/*
 * Map the streaming policy enum to its public string representation.
 *
 * The sole selector is stored as a ngx_uint_t enum (off=0, auto=1,
 * force=2).  This helper maps the enum value to a human-readable string.
 */
static const char *
ngx_http_markdown_streaming_policy_str(ngx_uint_t policy)
{
    switch (policy) {
    case NGX_HTTP_MARKDOWN_STREAMING_OFF:
        return "off";
    case NGX_HTTP_MARKDOWN_STREAMING_AUTO:
        return "auto";
    case NGX_HTTP_MARKDOWN_STREAMING_FORCE:
        return "force";
    default:
        return "unknown";
    }
}
#endif /* MARKDOWN_STREAMING_ENABLED */


/*
 * Serialize the current location configuration to JSON format.
 *
 * Reads all dynconf-relevant fields from the location config and
 * produces a JSON object fragment containing key-value pairs.
 *
 * The output contains active directive-shaped keys only.  Unified Config V2
 * limits are already represented by diagnostics.effective_config and are not
 * duplicated under removed directive names here:
 *   "config_snapshot": {
 *       "markdown_filter": "on",
 *       "markdown_streaming": "auto",
 *       "markdown_error_policy": "pass",
 *       ...
 *   }
 *
 * Parameters:
 *   pool    - Memory pool for buffer allocation
 *   conf    - Current location configuration to serialize
 *   out_buf - [out] Pointer to the start of the JSON output
 *   out_len - [out] Length of the JSON output in bytes
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on allocation failure or NULL conf
 */
ngx_int_t
ngx_http_markdown_dynconf_snapshot_to_json(ngx_pool_t *pool,
    const ngx_http_markdown_conf_t *conf,
    u_char **out_buf, size_t *out_len)
{
    u_char  *buf;
    u_char  *p;
    u_char  *last;

    if (pool == NULL || conf == NULL || out_buf == NULL || out_len == NULL) {
        return NGX_ERROR;
    }

    buf = ngx_palloc(pool, NGX_HTTP_MARKDOWN_SNAPSHOT_BUF_SIZE);
    if (buf == NULL) {
        return NGX_ERROR;
    }

    p = buf;
    last = buf + NGX_HTTP_MARKDOWN_SNAPSHOT_BUF_SIZE;

    /* markdown_filter (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_filter", conf->enabled, 1);

    /* markdown_error_policy (pass|fail_closed|status_<code>) */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_error_policy",
        ngx_http_markdown_error_policy_str(conf->on_error,
                                           conf->error_status), 1);

    /* markdown_flavor */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_flavor",
        ngx_http_markdown_flavor_str(conf->flavor), 1);

    /* markdown_token_estimate (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_token_estimate", conf->token_estimate, 1);

    /* markdown_front_matter (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_front_matter", conf->front_matter, 1);

    /* markdown_accept (strict|wildcard|force) */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_accept",
        ngx_http_markdown_accept_policy_str(conf->accept_policy), 1);

    /* markdown_buffer_chunked (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_buffer_chunked", conf->buffer_chunked, 1);

    /* markdown_auto_decompress (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_auto_decompress", conf->decompress.auto_decompress, 1);

    /* markdown_parse_timeout */
    p = ngx_http_markdown_snapshot_msec(p, last,
        "markdown_parse_timeout", conf->decompress.parse_timeout, 1);

    /* markdown_parser_budget */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_parser_budget", conf->decompress.parser_budget, 1);

    /* markdown_prune_noise (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_prune_noise", conf->advanced.prune_noise, 1);

    /* markdown_dynamic_config (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_dynamic_config", conf->advanced.dynconf_enabled, 1);

    /* markdown_dynconf_dry_run (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_dynconf_dry_run", conf->advanced.dynconf_dry_run, 1);

    /* markdown_log_verbosity */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_log_verbosity",
        ngx_http_markdown_log_verbosity_str(conf->policy.log_verbosity), 1);

    /* markdown_cache_validation (off|ims_only|full) */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_cache_validation",
        ngx_http_markdown_cache_validation_str(conf->policy.conditional_requests),
        1);

    /* markdown_metrics_format */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_metrics_format",
        ngx_http_markdown_metrics_format_str(conf->ops.metrics_format),
#ifdef MARKDOWN_STREAMING_ENABLED
        1);
#else
        0);
#endif

#ifdef MARKDOWN_STREAMING_ENABLED
    /* markdown_streaming */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_streaming",
        ngx_http_markdown_streaming_policy_str(conf->stream.policy), 1);

    /* markdown_streaming_shadow (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_streaming_shadow", conf->stream.shadow, 1);

    /* markdown_streaming_zero_copy (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_streaming_zero_copy", conf->stream.zero_copy, 1);

    /* markdown_stream_threshold (v0.8.0 directive name) */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_stream_threshold",
        conf->stream.threshold, 1);

    /* markdown_stream_precommit_buffer */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_stream_precommit_buffer",
        conf->stream.precommit_buffer, 1);

    /* markdown_stream_flush_min */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_stream_flush_min",
        conf->stream.flush_min, 0);
#endif /* MARKDOWN_STREAMING_ENABLED */

    /*
     * Detect truncation: if p reached last, the buffer was too small
     * and the JSON output is incomplete.  Return NGX_ERROR so the
     * caller does not serve a partial snapshot.
     */
    if (p >= last) {
        return NGX_ERROR;
    }

    *out_buf = buf;
    *out_len = p - buf;

    return NGX_OK;
}
