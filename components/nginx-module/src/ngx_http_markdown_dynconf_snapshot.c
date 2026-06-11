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
 * Map on_error enum to string representation.
 */
static const char *
ngx_http_markdown_on_error_str(ngx_uint_t val)
{
    switch (val) {
    case NGX_HTTP_MARKDOWN_ON_ERROR_PASS:
        return "pass";
    case NGX_HTTP_MARKDOWN_ON_ERROR_REJECT:
        return "reject";
    default:
        return "unknown";
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
    case NGX_HTTP_MARKDOWN_FLAVOR_MDX:
        return "mdx";
    case NGX_HTTP_MARKDOWN_FLAVOR_ORG_MODE:
        return "org-mode";
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
 * Map streaming engine mode to string representation.
 *
 * The streaming engine is stored as a complex value; when NULL
 * it defaults to "auto" mode.  This helper maps the resolved
 * mode constant to a human-readable string.
 */
static const char *
ngx_http_markdown_streaming_engine_str(
    const ngx_http_markdown_streaming_cfg_t *streaming)
{
    if (streaming->engine == NULL) {
        return "auto";
    }

    /*
     * When the engine is a complex value, we cannot resolve it
     * without a request context.  Report as "configured" to
     * indicate a non-default value is set.
     */
    return "configured";
}


/*
 * Map streaming on_error enum to string representation.
 */
static const char *
ngx_http_markdown_streaming_on_error_str(ngx_uint_t val)
{
    switch (val) {
    case NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS:
        return "pass";
    case NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT:
        return "reject";
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
 * The output format matches the design.md §13.2 specification:
 *   "config_snapshot": {
 *       "markdown_filter": "on",
 *       "markdown_streaming_engine": "auto",
 *       "decompression_budget": "10485760",
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

    /* markdown_max_size */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_max_size", conf->max_size, 1);

    /* markdown_timeout */
    p = ngx_http_markdown_snapshot_msec(p, last,
        "markdown_timeout", conf->timeout, 1);

    /* markdown_on_error */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_on_error",
        ngx_http_markdown_on_error_str(conf->on_error), 1);

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

    /* markdown_on_wildcard (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_on_wildcard", conf->on_wildcard, 1);

    /* markdown_buffer_chunked (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_buffer_chunked", conf->buffer_chunked, 1);

    /* markdown_auto_decompress (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_auto_decompress", conf->decompress.auto_decompress, 1);

    /* markdown_decompression_budget (decompress.max_size) */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_decompression_budget", conf->decompress.max_size, 1);

    /* markdown_parse_timeout */
    p = ngx_http_markdown_snapshot_msec(p, last,
        "markdown_parse_timeout", conf->decompress.parse_timeout, 1);

    /* markdown_parser_budget */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_parser_budget", conf->decompress.parser_budget, 1);

    /* markdown_prune_noise (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_prune_noise", conf->advanced.prune_noise, 1);

    /* markdown_memory_budget */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_memory_budget", conf->advanced.memory_budget, 1);

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

    /* markdown_generate_etag (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_generate_etag", conf->policy.generate_etag, 1);

    /* markdown_metrics_format */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_metrics_format",
        ngx_http_markdown_metrics_format_str(conf->ops.metrics_format), 1);

    /* markdown_trust_forwarded_headers (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_trust_forwarded_headers",
        conf->ops.trust_forwarded_headers, 1);

    /* markdown_large_body_threshold */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_large_body_threshold", conf->large_body_threshold,
#ifdef MARKDOWN_STREAMING_ENABLED
        1);
#else
        0);
#endif

#ifdef MARKDOWN_STREAMING_ENABLED
    /* markdown_streaming_engine */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_streaming_engine",
        ngx_http_markdown_streaming_engine_str(&conf->streaming), 1);

    /* markdown_streaming_budget */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_streaming_budget", conf->stream.budget, 1);

    /* markdown_streaming_on_error */
    p = ngx_http_markdown_snapshot_str(p, last,
        "markdown_streaming_on_error",
        ngx_http_markdown_streaming_on_error_str(
            conf->stream.on_error), 1);

    /* markdown_streaming_shadow (on/off) */
    p = ngx_http_markdown_snapshot_flag(p, last,
        "markdown_streaming_shadow", conf->stream.shadow, 1);

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
        conf->stream.flush_min, 1);

    /* markdown_streaming_auto_threshold (deprecated alias for markdown_stream_threshold) */
    p = ngx_http_markdown_snapshot_size(p, last,
        "markdown_streaming_auto_threshold",
        conf->stream.threshold, 0);
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
