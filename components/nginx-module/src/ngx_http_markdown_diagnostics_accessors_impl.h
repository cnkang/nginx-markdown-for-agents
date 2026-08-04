/*
 * NGINX Markdown Filter Module - Diagnostics Accessor Implementations
 *
 * Provides accessor functions that bridge the diagnostics compilation
 * unit with the module-internal state (metrics pointer, dynconf watcher).
 *
 * This header MUST be included only from the main translation unit
 * (ngx_http_markdown_filter_module.c) after the module_state_impl.h
 * and dynconf_impl.h headers have been included.
 *
 * Requirement: REQ-0700-OPERABILITY-001
 */

#ifndef NGX_HTTP_MARKDOWN_DIAGNOSTICS_ACCESSORS_IMPL_H
#define NGX_HTTP_MARKDOWN_DIAGNOSTICS_ACCESSORS_IMPL_H

#include "ngx_http_markdown_diagnostics.h"


/*
 * Collect key metrics counters for the diagnostics endpoint.
 *
 * Reads the global ngx_http_markdown_metrics pointer (SHM zone)
 * and copies the relevant counters into the output struct.
 * If the metrics pointer is NULL (zone not initialized), all
 * fields are zeroed.
 */
void
ngx_http_markdown_diagnostics_collect_metrics(
    ngx_http_markdown_diag_metrics_t *out)
{
    if (out == NULL) {
        return;
    }

    ngx_memzero(out, sizeof(ngx_http_markdown_diag_metrics_t));

    if (ngx_http_markdown_metrics == NULL) {
        return;
    }

    out->conversions_total =
        ngx_http_markdown_metrics->conversions_succeeded;
    out->delivery_total =
        ngx_http_markdown_metrics->results.delivery_count;
    out->requests_total =
        ngx_http_markdown_metrics->requests_entered;
    out->failopen_total =
        ngx_http_markdown_metrics->results.failopen_count;
    out->overload_total =
        (ngx_atomic_uint_t) ngx_http_markdown_inflight_overload_total();
    out->backpressure_total =
        ngx_http_markdown_metrics->perf.backpressure_total;
    out->inflight = (ngx_atomic_uint_t) ngx_http_markdown_inflight_current();
    out->pending_output = 0;

#ifdef MARKDOWN_STREAMING_ENABLED
    out->streaming_requests_total =
        ngx_http_markdown_metrics->streaming.requests_total;
    out->streaming_succeeded_total =
        ngx_http_markdown_metrics->streaming.succeeded_total;
    out->streaming_failed_total =
        ngx_http_markdown_metrics->streaming.failed_total;
    out->streaming_fallback_total =
        ngx_http_markdown_metrics->streaming.fallback_total;
    out->streaming_candidate_total =
        ngx_http_markdown_metrics->streaming.selection.candidate_total;
    out->streaming_output_bytes_total =
        ngx_http_markdown_metrics->streaming.selection.output_bytes_total;
    out->engine_choice_streaming =
        ngx_http_markdown_metrics->streaming.engine_choice.streaming;
    out->engine_choice_full_buffer =
        ngx_http_markdown_metrics->streaming.engine_choice.full_buffer;
#endif
}


/*
 * Get the current dynconf watcher state for the diagnostics endpoint.
 *
 * Reads the global ngx_http_markdown_dynconf_watcher and copies
 * the relevant state fields into the output struct.  If the
 * watcher is not active, all fields are zeroed.
 */
void
ngx_http_markdown_diagnostics_get_dynconf_state(
    ngx_http_markdown_diag_dynconf_t *out)
{
    ngx_uint_t  result;

    if (out == NULL) {
        return;
    }

    ngx_memzero(out, sizeof(ngx_http_markdown_diag_dynconf_t));

    if (!ngx_http_markdown_dynconf_watcher.active) {
        out->state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_DISABLED;
        return;
    }

    if (ngx_http_markdown_dynconf_watcher.last_mtime == 0
        && ngx_http_markdown_dynconf_watcher.source_digest[0] == '\0')
    {
        out->state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_NO_FILE;
        return;
    }

    result = ngx_http_markdown_dynconf_watcher.last_result;
    if (result == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE
        || result == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR
        || result == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL)
    {
        out->state = ngx_http_markdown_dynconf_watcher.lkg_valid
            ? NGX_HTTP_MARKDOWN_DIAG_DYNCONF_LKG_PRESERVED
            : NGX_HTTP_MARKDOWN_DIAG_DYNCONF_INVALID_NO_LKG;
    } else if (ngx_http_markdown_dynconf_watcher.generation != 0) {
        out->state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_ACTIVE;
    } else {
        out->state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_NO_FILE;
    }

    out->generation = ngx_http_markdown_dynconf_watcher.generation;
    ngx_memcpy(out->source_digest,
               ngx_http_markdown_dynconf_watcher.source_digest,
               sizeof(out->source_digest));
    ngx_memcpy(out->active_digest,
               ngx_http_markdown_dynconf_watcher.active_digest,
               sizeof(out->active_digest));
    ngx_memcpy(out->lkg_digest,
               ngx_http_markdown_dynconf_watcher.lkg_digest,
               sizeof(out->lkg_digest));
    out->last_success = ngx_http_markdown_dynconf_watcher.last_success;
    out->has_last_success = out->last_success != 0;
    if (ngx_http_markdown_dynconf_watcher.last_error_len > 0) {
        size_t  i;

        out->last_error_len = ngx_http_markdown_dynconf_watcher.last_error_len;
        if (out->last_error_len > sizeof(out->last_error) - 1) {
            out->last_error_len = sizeof(out->last_error) - 1;
        }
        ngx_memcpy(out->last_error,
                   ngx_http_markdown_dynconf_watcher.last_error,
                   out->last_error_len);
        for (i = 0; i < out->last_error_len; i++) {
            if (out->last_error[i] == '"'
                || out->last_error[i] == '\\'
                || out->last_error[i] < 0x20)
            {
                out->last_error[i] = ' ';
            }
        }
        out->last_error[out->last_error_len] = '\0';
    }
    out->active_mtime = ngx_http_markdown_dynconf_watcher.applied_mtime;
    out->config_version = ngx_http_markdown_dynconf_watcher.version;
    out->last_known_good_mtime = ngx_http_markdown_dynconf_watcher.lkg_mtime;
    out->lkg_valid = ngx_http_markdown_dynconf_watcher.lkg_valid ? 1 : 0;
}


#ifdef NGINX_MARKDOWN_CONVERTER_H

void
ngx_http_markdown_diagnostics_get_effective(
    const void *opaque_conf,
    ngx_http_markdown_diag_effective_t *out)
{
    const ngx_http_markdown_conf_t          *conf;
    const ngx_http_markdown_dynconf_snapshot_t *snapshot;
    ngx_http_markdown_effective_conf_t       effective;

    if (out == NULL) {
        return;
    }

    ngx_memzero(out, sizeof(ngx_http_markdown_diag_effective_t));
    conf = (const ngx_http_markdown_conf_t *) opaque_conf;
    if (conf == NULL) {
        return;
    }

    snapshot = (ngx_http_markdown_dynconf_watcher.active
                && ngx_http_markdown_dynconf_watcher.generation != 0
                && ngx_http_markdown_dynconf_watcher.active_snapshot.valid)
        ? &ngx_http_markdown_dynconf_watcher.active_snapshot : NULL;
    ngx_memzero(&effective, sizeof(effective));
    ngx_http_markdown_build_effective_conf(&effective, snapshot, conf);

    out->filter = effective.enabled;
    out->prune_noise = effective.prune_noise;
    out->log_verbosity = effective.log_verbosity;
    out->error_policy = effective.error_policy;
    out->error_status = effective.error_status;
    out->streaming_buffer = effective.streaming_buffer;
    out->filter_source = effective.filter_provenance;
    out->prune_noise_source = effective.prune_noise_provenance;
    out->log_verbosity_source = effective.log_verbosity_provenance;
    out->error_policy_source = effective.error_policy_provenance;
    out->streaming_buffer_source = effective.streaming_buffer_provenance;
}

#endif /* NGINX_MARKDOWN_CONVERTER_H */

#ifdef NGINX_MARKDOWN_CONVERTER_H

typedef struct {
    u_char  *pos;
    u_char  *last;
} ngx_http_markdown_manifest_builder_t;


static ngx_int_t
ngx_http_markdown_manifest_literal(
    ngx_http_markdown_manifest_builder_t *builder, const char *text)
{
    size_t  length;

    length = ngx_strlen(text);
    if ((size_t) (builder->last - builder->pos) < length) {
        return NGX_ERROR;
    }
    ngx_memcpy(builder->pos, text, length);
    builder->pos += length;
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_json_string(
    ngx_http_markdown_manifest_builder_t *builder,
    const u_char *data, size_t length)
{
    static const u_char  hex[] = "0123456789abcdef";
    size_t               i;
    u_char              ch;

    if (ngx_http_markdown_manifest_literal(builder, "\"") != NGX_OK) {
        return NGX_ERROR;
    }

    for (i = 0; i < length; i++) {
        ch = data[i];
        if (ch == '"' || ch == '\\') {
            if ((size_t) (builder->last - builder->pos) < 2) {
                return NGX_ERROR;
            }
            *builder->pos++ = '\\';
            *builder->pos++ = ch;
        } else if (ch == '\b' || ch == '\f' || ch == '\n'
                   || ch == '\r' || ch == '\t')
        {
            if ((size_t) (builder->last - builder->pos) < 2) {
                return NGX_ERROR;
            }
            *builder->pos++ = '\\';
            *builder->pos++ = ch == '\b' ? 'b'
                : ch == '\f' ? 'f'
                : ch == '\n' ? 'n'
                : ch == '\r' ? 'r' : 't';
        } else if (ch < 0x20) {
            if ((size_t) (builder->last - builder->pos) < 6) {
                return NGX_ERROR;
            }
            *builder->pos++ = '\\';
            *builder->pos++ = 'u';
            *builder->pos++ = '0';
            *builder->pos++ = '0';
            *builder->pos++ = hex[ch >> 4];
            *builder->pos++ = hex[ch & 0x0f];
        } else {
            if (builder->pos == builder->last) {
                return NGX_ERROR;
            }
            *builder->pos++ = ch;
        }
    }

    return ngx_http_markdown_manifest_literal(builder, "\"");
}


static ngx_int_t
ngx_http_markdown_manifest_json_u64(
    ngx_http_markdown_manifest_builder_t *builder, uint64_t value)
{
    u_char  *end;
    size_t   remaining;

    if (builder == NULL || builder->pos == NULL || builder->last == NULL
        || builder->pos >= builder->last)
    {
        return NGX_ERROR;
    }

    remaining = (size_t) (builder->last - builder->pos);
    end = ngx_snprintf(builder->pos, remaining, "%uL", value);
    if (end == builder->last) {
        return NGX_ERROR;
    }
    builder->pos = end;
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_json_bool(
    ngx_http_markdown_manifest_builder_t *builder, ngx_flag_t value)
{
    return ngx_http_markdown_manifest_literal(
        builder, value ? "true" : "false");
}


static ngx_int_t
ngx_http_markdown_manifest_field_string(
    ngx_http_markdown_manifest_builder_t *builder, const char *name,
    const u_char *value, size_t value_len, ngx_flag_t explicit,
    ngx_flag_t first)
{
    if (!first && ngx_http_markdown_manifest_literal(builder, ",") != NGX_OK) {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_json_string(builder,
                                               (const u_char *) name,
                                               ngx_strlen(name)) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
                                              ":{\"value\":") != NGX_OK
        || ngx_http_markdown_manifest_json_string(builder, value, value_len)
           != NGX_OK
        || ngx_http_markdown_manifest_literal(builder, ",\"explicit\":")
           != NGX_OK
        || ngx_http_markdown_manifest_json_bool(builder, explicit) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder, "}") != NGX_OK)
    {
        return NGX_ERROR;
    }
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_field_number(
    ngx_http_markdown_manifest_builder_t *builder, const char *name,
    uint64_t value, ngx_flag_t explicit, ngx_flag_t first)
{
    if (!first && ngx_http_markdown_manifest_literal(builder, ",") != NGX_OK) {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_json_string(builder,
                                               (const u_char *) name,
                                               ngx_strlen(name)) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
                                              ":{\"value\":") != NGX_OK
        || ngx_http_markdown_manifest_json_u64(builder, value) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder, ",\"explicit\":")
           != NGX_OK
        || ngx_http_markdown_manifest_json_bool(builder, explicit) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder, "}") != NGX_OK)
    {
        return NGX_ERROR;
    }
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_field_bool(
    ngx_http_markdown_manifest_builder_t *builder, const char *name,
    ngx_flag_t value, ngx_flag_t explicit, ngx_flag_t first)
{
    if (!first && ngx_http_markdown_manifest_literal(builder, ",") != NGX_OK) {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_json_string(builder,
                                               (const u_char *) name,
                                               ngx_strlen(name)) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
                                              ":{\"value\":") != NGX_OK
        || ngx_http_markdown_manifest_json_bool(builder, value) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder, ",\"explicit\":")
           != NGX_OK
        || ngx_http_markdown_manifest_json_bool(builder, explicit) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder, "}") != NGX_OK)
    {
        return NGX_ERROR;
    }
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_field_array(
    ngx_http_markdown_manifest_builder_t *builder, const char *name,
    const ngx_array_t *array, const u_char *fallback, size_t fallback_len,
    ngx_flag_t explicit, ngx_flag_t first)
{
    ngx_uint_t  i;
    ngx_str_t  *values;

    if (!first && ngx_http_markdown_manifest_literal(builder, ",") != NGX_OK) {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_json_string(builder,
                                               (const u_char *) name,
                                               ngx_strlen(name)) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
                                              ":{\"value\":[") != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (array == NULL || array->nelts == 0) {
        if (fallback != NULL
            && ngx_http_markdown_manifest_json_string(builder, fallback,
                                                      fallback_len) != NGX_OK)
        {
            return NGX_ERROR;
        }
    } else {
        values = array->elts;
        for (i = 0; i < array->nelts; i++) {
            if (i != 0 && ngx_http_markdown_manifest_literal(builder, ",")
                != NGX_OK)
            {
                return NGX_ERROR;
            }
            if (ngx_http_markdown_manifest_json_string(builder,
                                                       values[i].data,
                                                       values[i].len) != NGX_OK)
            {
                return NGX_ERROR;
            }
        }
    }
    if (ngx_http_markdown_manifest_literal(builder,
                                           "],\"explicit\":") != NGX_OK
        || ngx_http_markdown_manifest_json_bool(builder, explicit) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder, "}") != NGX_OK)
    {
        return NGX_ERROR;
    }
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_field_limits(
    ngx_http_markdown_manifest_builder_t *builder,
    const ngx_http_markdown_conf_t *conf, ngx_flag_t explicit, ngx_flag_t first)
{
    if (!first && ngx_http_markdown_manifest_literal(builder, ",") != NGX_OK) {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_json_string(builder,
                                               (const u_char *) "limits", 6)
        != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
            ":{\"value\":{\"conversion_memory\":") != NGX_OK
        || ngx_http_markdown_manifest_json_u64(builder,
            (uint64_t) conf->limits.conversion_memory) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
            ",\"conversion_timeout\":") != NGX_OK
        || ngx_http_markdown_manifest_json_u64(builder,
            (uint64_t) conf->limits.conversion_timeout) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
            ",\"decompressed_size\":") != NGX_OK
        || ngx_http_markdown_manifest_json_u64(builder,
            (uint64_t) conf->limits.decompressed_size) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
            ",\"decompression_ratio\":") != NGX_OK
        || ngx_http_markdown_manifest_json_u64(builder,
            (uint64_t) conf->limits.decompression_ratio) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
            ",\"max_inflight\":") != NGX_OK
        || ngx_http_markdown_manifest_json_u64(builder,
            (uint64_t) conf->limits.max_inflight) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
            ",\"parser_memory\":") != NGX_OK
        || ngx_http_markdown_manifest_json_u64(builder,
            (uint64_t) conf->limits.parser_memory) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
            ",\"parser_timeout\":") != NGX_OK
        || ngx_http_markdown_manifest_json_u64(builder,
            (uint64_t) conf->limits.parser_timeout) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
            ",\"streaming_buffer\":") != NGX_OK
        || ngx_http_markdown_manifest_json_u64(builder,
            (uint64_t) conf->limits.streaming_buffer) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder,
            "},\"explicit\":") != NGX_OK
        || ngx_http_markdown_manifest_json_bool(builder, explicit) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder, "}") != NGX_OK)
    {
        return NGX_ERROR;
    }
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_append_policy_fields(
    ngx_http_markdown_manifest_builder_t *builder,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t explicit)
{
    if (ngx_http_markdown_manifest_field_string(builder, "accept",
        conf->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD
            ? (const u_char *) "wildcard"
            : conf->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_FORCE
                ? (const u_char *) "force" : (const u_char *) "strict",
        conf->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD ? 8
            : conf->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_FORCE ? 5 : 6,
        explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_ACCEPT, 0) != NGX_OK
        || ngx_http_markdown_manifest_field_array(builder, "auth_cookies",
            conf->policy.auth_cookies, NULL, 0,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_AUTH_COOKIES, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder, "auth_policy",
            conf->policy.auth_policy == NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY
                ? (const u_char *) "deny" : (const u_char *) "allow",
            conf->policy.auth_policy == NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY
                ? 4 : 5,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_AUTH_POLICY, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder, "auto_decompress",
            conf->decompress.auto_decompress ? (const u_char *) "on"
                : (const u_char *) "off", 2,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DECOMPRESS, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder, "cache_validation",
            conf->policy.conditional_requests
                == NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT
                ? (const u_char *) "full"
                : conf->policy.conditional_requests
                    == NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE
                    ? (const u_char *) "ims_only" : (const u_char *) "off",
            conf->policy.conditional_requests
                == NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT ? 4
                : conf->policy.conditional_requests
                    == NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE
                    ? 8 : 3,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_CACHE, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_array(builder, "content_types",
            conf->routing.content_types, (const u_char *) "text/html", 9,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_CONTENT, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_bool(builder, "diagnostics",
            conf->ops.diagnostics_enabled,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DIAGNOSTICS, 0)
            != NGX_OK)
    {
        return NGX_ERROR;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_append_runtime_fields(
    ngx_http_markdown_manifest_builder_t *builder,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t explicit)
{
    const ngx_str_t  *complex_value;

    complex_value = (conf->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_COMPLEX
                     && conf->enabled_complex != NULL)
        ? &conf->enabled_complex->value : NULL;
    if (ngx_http_markdown_manifest_field_string(builder, "dynamic_config",
        conf->advanced.dynconf_enabled ? (const u_char *) "on"
            : (const u_char *) "off", 2,
        explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DYNCONF, 0) != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder,
            "dynamic_config_path", conf->advanced.dynconf_path.data != NULL
                ? conf->advanced.dynconf_path.data : (const u_char *) "",
            conf->advanced.dynconf_path.data != NULL
                ? conf->advanced.dynconf_path.len : 0,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DYNCONF, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder, "dynconf_dry_run",
            conf->advanced.dynconf_dry_run ? (const u_char *) "on"
                : (const u_char *) "off", 2,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DRY_RUN, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder, "error_policy",
            conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS
                ? (const u_char *) "pass"
                : conf->error_status == 429 ? (const u_char *) "status 429"
                : conf->error_status == 503 ? (const u_char *) "status 503"
                : (const u_char *) "fail_closed",
            conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS ? 4 : 10,
            explicit & NGX_HTTP_MARKDOWN_EXPLICIT_ERROR_POLICY, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder, "filter",
            complex_value != NULL ? complex_value->data
                : conf->enabled ? (const u_char *) "on"
                : (const u_char *) "off",
            complex_value != NULL ? complex_value->len : 2,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FILTER, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder, "flavor",
            conf->flavor == NGX_HTTP_MARKDOWN_FLAVOR_GFM
                ? (const u_char *) "gfm" : (const u_char *) "commonmark",
            conf->flavor == NGX_HTTP_MARKDOWN_FLAVOR_GFM ? 3 : 10,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FLAVOR, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_bool(builder, "front_matter",
            conf->front_matter,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FRONT_MATTER, 0)
            != NGX_OK)
    {
        return NGX_ERROR;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_append_limit_fields(
    ngx_http_markdown_manifest_builder_t *builder,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_main_conf_t *main_conf, ngx_uint_t explicit)
{
    if (ngx_http_markdown_manifest_field_limits(builder, conf,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_LIMITS, 0)
        != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder, "log_verbosity",
            conf->policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_ERROR
                ? (const u_char *) "error"
                : conf->policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN
                    ? (const u_char *) "warn"
                    : conf->policy.log_verbosity
                        == NGX_HTTP_MARKDOWN_LOG_DEBUG
                        ? (const u_char *) "debug" : (const u_char *) "info",
            conf->policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_ERROR ? 5
                : conf->policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN
                    ? 4 : conf->policy.log_verbosity
                        == NGX_HTTP_MARKDOWN_LOG_DEBUG ? 5 : 4,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_LOG, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_bool(builder, "metrics",
            conf->ops.metrics_enabled,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_METRICS, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_number(builder, "metrics_shm_size",
            (uint64_t) main_conf->metrics_shm_size, 0, 0) != NGX_OK)
    {
        return NGX_ERROR;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_append_prune_fields(
    ngx_http_markdown_manifest_builder_t *builder,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t explicit)
{
    if (ngx_http_markdown_manifest_field_bool(builder, "prune_noise",
            conf->advanced.prune_noise,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_PRUNE, 0)
        != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder,
            "prune_protection_selectors",
            conf->advanced.prune_protection_selectors != NULL
                ? conf->advanced.prune_protection_selectors->data
                : (const u_char *) "",
            conf->advanced.prune_protection_selectors != NULL
                ? conf->advanced.prune_protection_selectors->len : 0,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_PROTECTION, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder, "prune_selectors",
            conf->advanced.prune_selectors != NULL
                ? conf->advanced.prune_selectors->data
                : (const u_char *) "nav footer aside",
            conf->advanced.prune_selectors != NULL
                ? conf->advanced.prune_selectors->len : 16,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_SELECTORS, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_array(builder,
            "stream_excluded_types", conf->stream.excluded_types, NULL, 0,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_EXCLUDED, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_string(builder, "streaming",
            conf->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_OFF
                ? (const u_char *) "off"
                : conf->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_FORCE
                    ? (const u_char *) "force" : (const u_char *) "auto",
            conf->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_OFF ? 3
                : conf->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_FORCE
                    ? 5 : 4,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_STREAM, 0)
            != NGX_OK
        || ngx_http_markdown_manifest_field_bool(builder, "token_estimate",
            conf->token_estimate,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_TOKEN, 0)
            != NGX_OK)
    {
        return NGX_ERROR;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_append_trusted_proxies(
    ngx_http_markdown_manifest_builder_t *builder,
    const ngx_http_markdown_main_conf_t *main_conf)
{
    if (ngx_http_markdown_manifest_field_array(builder, "trusted_proxies",
        main_conf->trusted_proxies_manifest, NULL, 0,
        main_conf->trusted_proxies_configured, 0) != NGX_OK
        || ngx_http_markdown_manifest_literal(builder, "}") != NGX_OK)
    {
        return NGX_ERROR;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_digest(
    const ngx_http_request_t *request, ngx_pool_t *pool,
    u_char *out, size_t out_len)
{
    const ngx_http_markdown_conf_t       *conf;
    const ngx_http_markdown_main_conf_t  *main_conf;
    ngx_http_markdown_manifest_builder_t  builder;
    u_char                                *manifest;
    u_char                                *pos;
    u_char                                *start;
    u_char                                 digest[64];
    ngx_uint_t                             explicit;

    if (request == NULL || pool == NULL || out == NULL || out_len < 72) {
        return NGX_ERROR;
    }
    conf = ngx_http_get_module_loc_conf((ngx_http_request_t *) request,
                                        ngx_http_markdown_filter_module);
    main_conf = ngx_http_get_module_main_conf((ngx_http_request_t *) request,
                                              ngx_http_markdown_filter_module);
    if (conf == NULL || main_conf == NULL) {
        return NGX_ERROR;
    }

    manifest = ngx_palloc(pool, 65536);
    if (manifest == NULL) {
        return NGX_ERROR;
    }
    builder.pos = manifest;
    builder.last = manifest + 65536;
    if (ngx_http_markdown_manifest_literal(&builder,
        "{\"schema_version\":\"static_config_manifest_v1\"") != NGX_OK)
    {
        return NGX_ERROR;
    }
    explicit = conf->advanced.static_explicit_mask;
    if (ngx_http_markdown_manifest_append_policy_fields(
            &builder, conf, explicit) != NGX_OK
        || ngx_http_markdown_manifest_append_runtime_fields(
            &builder, conf, explicit) != NGX_OK
        || ngx_http_markdown_manifest_append_limit_fields(
            &builder, conf, main_conf, explicit) != NGX_OK
        || ngx_http_markdown_manifest_append_prune_fields(
            &builder, conf, explicit) != NGX_OK
        || ngx_http_markdown_manifest_append_trusted_proxies(
            &builder, main_conf) != NGX_OK)
    {
        return NGX_ERROR;
    }

    if (builder.pos < manifest || builder.pos > builder.last) {
        return NGX_ERROR;
    }
    pos = builder.pos;
    start = manifest;
    if (pos < start || pos > builder.last) {
        return NGX_ERROR;
    }
    if (markdown_sha256_hex(start, (size_t) (pos - start),
                            digest, sizeof(digest)) != 0)
    {
        return NGX_ERROR;
    }
    ngx_memcpy(out, "sha256:", 7);
    ngx_memcpy(out + 7, digest, 64);
    out[71] = '\0';
    return NGX_OK;
}


ngx_int_t
ngx_http_markdown_diagnostics_get_static_digest(
    const void *request, ngx_pool_t *pool, u_char *out, size_t out_len)
{
    return ngx_http_markdown_manifest_digest(request, pool, out, out_len);
}

#endif /* NGINX_MARKDOWN_CONVERTER_H */

#endif /* NGX_HTTP_MARKDOWN_DIAGNOSTICS_ACCESSORS_IMPL_H */
