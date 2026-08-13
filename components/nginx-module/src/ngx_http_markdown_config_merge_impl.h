#ifndef NGX_HTTP_MARKDOWN_CONFIG_MERGE_IMPL_H
#define NGX_HTTP_MARKDOWN_CONFIG_MERGE_IMPL_H

/*
 * Shared configuration inheritance and default resolution.
 *
 * This header is included by the configuration core and by the focused
 * configuration-contract test. Keeping the value-resolution path here
 * prevents the test from maintaining a second merge implementation while
 * leaving validation and configuration side effects in the core.
 */

static void
ngx_http_markdown_merge_enabled(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    if (conf->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_UNSET) {
        if (prev->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_UNSET) {
            conf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
            conf->enabled = 0;
            conf->enabled_complex = NULL;
        } else {
            conf->enabled_source = prev->enabled_source;
            conf->enabled = prev->enabled;
            conf->enabled_complex = prev->enabled_complex;
        }
        return;
    }

    if (conf->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_STATIC) {
        conf->enabled_complex = NULL;
    }
}

static void
ngx_http_markdown_merge_str_if_unset(ngx_str_t *child,
    const ngx_str_t *parent)
{
    if (child->len == 0 && parent->len > 0) {
        *child = *parent;
    }
}

static void
ngx_http_markdown_apply_memory_budget_override(
    ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev,
    ngx_flag_t max_size_set)
{
    conf->decompress.max_size_explicit =
        max_size_set || prev->decompress.max_size_explicit;

    if (conf->advanced.memory_budget != NGX_CONF_UNSET_SIZE
        && !conf->decompress.max_size_explicit)
    {
        conf->max_size = conf->advanced.memory_budget;
    }
}

static void
ngx_http_markdown_merge_core_base_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_conf_merge_size_value(conf->max_size, prev->max_size,
                              10 * 1024 * 1024);
    ngx_conf_merge_msec_value(conf->timeout, prev->timeout, 5000);
    ngx_conf_merge_uint_value(conf->on_error, prev->on_error,
                              NGX_HTTP_MARKDOWN_ON_ERROR_PASS);
    ngx_conf_merge_uint_value(conf->error_status, prev->error_status,
                              NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT);
    ngx_conf_merge_uint_value(conf->flavor, prev->flavor, 0);
    ngx_conf_merge_value(conf->token_estimate, prev->token_estimate, 0);
    ngx_conf_merge_value(conf->front_matter, prev->front_matter, 0);
    ngx_conf_merge_uint_value(conf->accept_policy, prev->accept_policy,
                              NGX_HTTP_MARKDOWN_ACCEPT_STRICT);
    ngx_conf_merge_uint_value(conf->policy.auth_policy,
                              prev->policy.auth_policy, 0);
    ngx_conf_merge_value(conf->policy.generate_etag,
                         prev->policy.generate_etag, 0);
    ngx_conf_merge_uint_value(conf->policy.conditional_requests,
                              prev->policy.conditional_requests,
                              NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE);
    ngx_conf_merge_uint_value(conf->policy.log_verbosity,
                              prev->policy.log_verbosity,
                              NGX_HTTP_MARKDOWN_LOG_INFO);
    ngx_conf_merge_value(conf->decompress.auto_decompress,
                         prev->decompress.auto_decompress, 1);
    ngx_conf_merge_size_value(conf->decompress.max_size,
                              prev->decompress.max_size,
                              NGX_CONF_UNSET_SIZE);
    ngx_conf_merge_msec_value(conf->decompress.parse_timeout,
                              prev->decompress.parse_timeout, 30000);
    ngx_conf_merge_size_value(conf->decompress.parser_budget,
                              prev->decompress.parser_budget,
                              64 * 1024 * 1024);
}

static void
ngx_http_markdown_merge_core_ops_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_conf_merge_value(conf->ops.diagnostics_enabled,
                         prev->ops.diagnostics_enabled, 0);
    ngx_conf_merge_value(conf->ops.metrics_enabled,
                         prev->ops.metrics_enabled, 0);
}

static void
ngx_http_markdown_merge_core_ptr_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_conf_merge_size_value(conf->routing.large_body_threshold,
                              prev->routing.large_body_threshold,
                              NGX_HTTP_MARKDOWN_THRESHOLD_OFF);
    ngx_conf_merge_uint_value(conf->routing.max_inflight,
                              prev->routing.max_inflight,
                              NGX_HTTP_MARKDOWN_MAX_INFLIGHT_DEFAULT);
    ngx_conf_merge_ptr_value(conf->policy.auth_cookies,
                             prev->policy.auth_cookies, NULL);
    ngx_conf_merge_ptr_value(conf->routing.content_types,
                             prev->routing.content_types, NULL);
}

static void
ngx_http_markdown_merge_core_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_http_markdown_merge_core_base_values(conf, prev);
    ngx_http_markdown_merge_core_ops_values(conf, prev);
    ngx_http_markdown_merge_core_ptr_values(conf, prev);
}

static void
ngx_http_markdown_merge_advanced_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_conf_merge_value(conf->advanced.prune_noise,
                         prev->advanced.prune_noise, 1);
    ngx_conf_merge_ptr_value(conf->advanced.prune_selectors,
                             prev->advanced.prune_selectors, NULL);
    ngx_conf_merge_ptr_value(conf->advanced.prune_protection_selectors,
                             prev->advanced.prune_protection_selectors,
                             NULL);
    ngx_conf_merge_size_value(conf->advanced.memory_budget,
                              prev->advanced.memory_budget,
                              NGX_CONF_UNSET_SIZE);
    ngx_conf_merge_value(conf->advanced.dynconf_enabled,
                         prev->advanced.dynconf_enabled, 0);
    ngx_http_markdown_merge_str_if_unset(&conf->advanced.dynconf_path,
                                         &prev->advanced.dynconf_path);
    ngx_conf_merge_value(conf->advanced.dynconf_dry_run,
                         prev->advanced.dynconf_dry_run, 0);
}

/*
 * Apply only inheritance, defaults, and legacy-field projection. The caller
 * remains responsible for cross-key validation, logging, and registration.
 * The return value preserves whether max_size was explicit at this level.
 */
static ngx_flag_t
ngx_http_markdown_merge_inherited_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_flag_t  max_size_set;
    ngx_flag_t  conversion_timeout_explicit;
    ngx_flag_t  parser_timeout_explicit;
    ngx_flag_t  conversion_memory_explicit;
    ngx_flag_t  parser_memory_explicit;
    ngx_flag_t  streaming_buffer_explicit;
#ifdef MARKDOWN_STREAMING_ENABLED
    ngx_flag_t  stream_budget_set;
#endif

    max_size_set = (conf->max_size != NGX_CONF_UNSET_SIZE);
    conversion_timeout_explicit =
        (conf->limits.conversion_timeout != NGX_CONF_UNSET_MSEC);
    parser_timeout_explicit =
        (conf->limits.parser_timeout != NGX_CONF_UNSET_MSEC);
    conversion_memory_explicit =
        (conf->limits.conversion_memory != NGX_CONF_UNSET_SIZE);
    parser_memory_explicit =
        (conf->limits.parser_memory != NGX_CONF_UNSET_SIZE);
    streaming_buffer_explicit =
        (conf->limits.streaming_buffer != NGX_CONF_UNSET_SIZE);
#ifdef MARKDOWN_STREAMING_ENABLED
    stream_budget_set =
        (conf->limits.streaming_buffer != NGX_CONF_UNSET_SIZE);
#endif

    ngx_http_markdown_merge_enabled(conf, prev);
    ngx_http_markdown_merge_core_values(conf, prev);
    ngx_http_markdown_merge_stream_values(conf, prev);

#ifdef MARKDOWN_STREAMING_ENABLED
    if (stream_budget_set) {
        conf->stream.budget_explicit = 1;
    }
#endif

    ngx_http_markdown_merge_advanced_values(conf, prev);

    conf->decompress.max_size_explicit =
        max_size_set || prev->decompress.max_size_explicit;

    ngx_conf_merge_msec_value(conf->limits.conversion_timeout,
                              prev->limits.conversion_timeout,
                              NGX_HTTP_MARKDOWN_LIMITS_CONVERSION_TIMEOUT_DEFAULT);
    ngx_conf_merge_msec_value(conf->limits.parser_timeout,
                              prev->limits.parser_timeout,
                              NGX_HTTP_MARKDOWN_LIMITS_PARSER_TIMEOUT_DEFAULT);
    ngx_conf_merge_size_value(conf->limits.conversion_memory,
                              prev->limits.conversion_memory,
                              NGX_HTTP_MARKDOWN_LIMITS_CONVERSION_MEMORY_DEFAULT);
    ngx_conf_merge_size_value(conf->limits.parser_memory,
                              prev->limits.parser_memory,
                              NGX_HTTP_MARKDOWN_LIMITS_PARSER_MEMORY_DEFAULT);
    ngx_conf_merge_size_value(conf->limits.streaming_buffer,
                              prev->limits.streaming_buffer,
                              NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT);
    ngx_conf_merge_size_value(conf->limits.decompressed_size,
                              prev->limits.decompressed_size,
                              NGX_HTTP_MARKDOWN_LIMITS_DECOMPRESSED_SIZE_DEFAULT);
    ngx_conf_merge_uint_value(conf->limits.decompression_ratio,
                              prev->limits.decompression_ratio,
                              NGX_HTTP_MARKDOWN_LIMITS_DECOMPRESSION_RATIO_DEFAULT);
    ngx_conf_merge_uint_value(conf->limits.max_inflight,
                              prev->limits.max_inflight,
                              NGX_HTTP_MARKDOWN_LIMITS_MAX_INFLIGHT_DEFAULT);

    /*
     * Propagate per-key explicitness: a key is explicit when set at this
     * level or inherited from an explicit parent.  Captured before the
     * ngx_conf_merge_* calls above overwrote the UNSET sentinels.
     */
    conf->limits.conversion_timeout_explicit =
        conversion_timeout_explicit
        || prev->limits.conversion_timeout_explicit;
    conf->limits.parser_timeout_explicit =
        parser_timeout_explicit || prev->limits.parser_timeout_explicit;
    conf->limits.conversion_memory_explicit =
        conversion_memory_explicit
        || prev->limits.conversion_memory_explicit;
    conf->limits.parser_memory_explicit =
        parser_memory_explicit || prev->limits.parser_memory_explicit;
    conf->limits.streaming_buffer_explicit =
        streaming_buffer_explicit
        || prev->limits.streaming_buffer_explicit;

    conf->timeout = conf->limits.conversion_timeout;
    conf->decompress.parse_timeout = conf->limits.parser_timeout;
    if (!conf->decompress.max_size_explicit) {
        conf->max_size = conf->limits.conversion_memory;
    }
    conf->decompress.parser_budget = conf->limits.parser_memory;
    conf->stream.budget = conf->limits.streaming_buffer;
    conf->decompress.max_size = conf->limits.decompressed_size;
    conf->routing.max_inflight = conf->limits.max_inflight;

    return max_size_set;
}

#endif /* NGX_HTTP_MARKDOWN_CONFIG_MERGE_IMPL_H */
