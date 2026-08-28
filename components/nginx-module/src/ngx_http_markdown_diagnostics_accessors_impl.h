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
 * Requirement: structured decision path logging
 */

#ifndef NGX_HTTP_MARKDOWN_DIAGNOSTICS_ACCESSORS_IMPL_H
#define NGX_HTTP_MARKDOWN_DIAGNOSTICS_ACCESSORS_IMPL_H

#include "ngx_http_markdown_diagnostics.h"

#define NGX_HTTP_MARKDOWN_DIAGNOSTICS_MANIFEST_BUF_SIZE  65536

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
    out->diagnostics_recording_state =
        ngx_http_markdown_diagnostics_recording_state();
    out->pending_output =
        ngx_http_markdown_pending_output_current();

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
    out->copied_output_total =
        ngx_http_markdown_metrics->perf.copied_output_total;

#ifdef MARKDOWN_STREAMING_ENABLED
    out->streaming_requests_total =
        ngx_http_markdown_metrics->streaming.requests_total;
    out->precommit_failopen_total =
        ngx_http_markdown_metrics->streaming.precommit_failopen_total;
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

    if (ngx_http_markdown_dynconf_watcher.file_state.last_mtime == 0
        && ngx_http_markdown_dynconf_watcher.digest_state.source_digest[0] == '\0')
    {
        out->state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_NO_FILE;
        return;
    }

    result = ngx_http_markdown_dynconf_watcher.diagnostic_state.last_result;
    if (result == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_INVALID_FILE
        || result == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_IO_ERROR
        || result == NGX_HTTP_MARKDOWN_DYNCONF_RELOAD_DRY_RUN_FAIL)
    {
        out->state = ngx_http_markdown_dynconf_watcher.digest_state.lkg_valid
            ? NGX_HTTP_MARKDOWN_DIAG_DYNCONF_LKG_PRESERVED
            : NGX_HTTP_MARKDOWN_DIAG_DYNCONF_INVALID_NO_LKG;
    } else if (ngx_http_markdown_dynconf_watcher.digest_state.generation != 0) {
        out->state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_ACTIVE;
    } else {
        out->state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_NO_FILE;
    }

    out->generation = ngx_http_markdown_dynconf_watcher.digest_state.generation;
    ngx_memcpy(out->source_digest,
               ngx_http_markdown_dynconf_watcher.digest_state.source_digest,
               sizeof(out->source_digest));
    ngx_memcpy(out->active_digest,
               ngx_http_markdown_dynconf_watcher.digest_state.active_digest,
               sizeof(out->active_digest));
    ngx_memcpy(out->lkg_digest,
               ngx_http_markdown_dynconf_watcher.digest_state.lkg_digest,
               sizeof(out->lkg_digest));
    out->last_success = ngx_http_markdown_dynconf_watcher.diagnostic_state.last_success;
    out->has_last_success = out->last_success != 0;
    if (ngx_http_markdown_dynconf_watcher.diagnostic_state.last_error_len > 0) {

        out->last_error_len = ngx_http_markdown_dynconf_watcher.diagnostic_state.last_error_len;
        if (out->last_error_len > sizeof(out->last_error) - 1) {
            out->last_error_len = sizeof(out->last_error) - 1;
        }
        ngx_memcpy(out->last_error,
                   ngx_http_markdown_dynconf_watcher.diagnostic_state.last_error,
                   out->last_error_len);
        for (size_t i = 0; i < out->last_error_len; i++) {
            if (out->last_error[i] == '"'
                || out->last_error[i] == '\\'
                || out->last_error[i] < 0x20)
            {
                out->last_error[i] = ' ';
            }
        }
        out->last_error[out->last_error_len] = '\0';
    }
    out->active_mtime = ngx_http_markdown_dynconf_watcher.file_state.applied_mtime;
    out->config_version = ngx_http_markdown_dynconf_watcher.diagnostic_state.version;
    out->last_known_good_mtime = ngx_http_markdown_dynconf_watcher.digest_state.lkg_mtime;
    out->lkg_valid = ngx_http_markdown_dynconf_watcher.digest_state.lkg_valid ? 1 : 0;
    out->masked_fields =
        ngx_http_markdown_dynconf_watcher.diagnostic_state.last_masked_fields;
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
                && ngx_http_markdown_dynconf_watcher.digest_state.generation != 0
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
ngx_http_markdown_manifest_append_json_char(
    ngx_http_markdown_manifest_builder_t *builder, u_char ch)
{
    static const u_char  hex[] = "0123456789abcdef";
    u_char               escaped;
    size_t               required;

    if (ch == '"' || ch == '\\') {
        required = 2;
    } else if (ch == '\b' || ch == '\f' || ch == '\n'
               || ch == '\r' || ch == '\t')
    {
        required = 2;
    } else if (ch < 0x20) {
        required = 6;
    } else {
        required = 1;
    }

    if ((size_t) (builder->last - builder->pos) < required) {
        return NGX_ERROR;
    }

    if (ch == '"' || ch == '\\') {
        *builder->pos++ = '\\';
        *builder->pos++ = ch;
    } else if (ch == '\b' || ch == '\f' || ch == '\n'
               || ch == '\r' || ch == '\t')
    {
        switch (ch) {
        case '\b':
            escaped = 'b';
            break;
        case '\f':
            escaped = 'f';
            break;
        case '\n':
            escaped = 'n';
            break;
        case '\r':
            escaped = 'r';
            break;
        default:
            escaped = 't';
            break;
        }
        *builder->pos++ = '\\';
        *builder->pos++ = escaped;
    } else if (ch < 0x20) {
        *builder->pos++ = '\\';
        *builder->pos++ = 'u';
        *builder->pos++ = '0';
        *builder->pos++ = '0';
        *builder->pos++ = hex[ch >> 4];
        *builder->pos++ = hex[ch & 0x0f];
    } else {
        *builder->pos++ = ch;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_manifest_json_string(
    ngx_http_markdown_manifest_builder_t *builder,
    const u_char *data, size_t length)
{
    if (ngx_http_markdown_manifest_literal(builder, "\"") != NGX_OK) {
        return NGX_ERROR;
    }

    for (size_t i = 0; i < length; i++) {
        if (ngx_http_markdown_manifest_append_json_char(
                builder, data[i]) != NGX_OK)
        {
            return NGX_ERROR;
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
    const ngx_str_t  *values;

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
        for (ngx_uint_t i = 0; i < array->nelts; i++) {
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


/*
 * Append effective policy values and explicitness flags to the manifest.
 *
 * The explicit mask preserves whether each value was configured directly,
 * while the values are rendered from the effective configuration.
 *
 * Returns:
 *     NGX_OK on success, or NGX_ERROR if a field cannot be appended.
 */
static ngx_int_t
ngx_http_markdown_manifest_append_policy_fields(
    ngx_http_markdown_manifest_builder_t *builder,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t explicit)
{
    const u_char  *accept_value;
    const u_char  *auth_value;
    const u_char  *auto_value;
    const u_char  *cache_value;
    size_t         accept_len;
    size_t         auth_len;
    size_t         auto_len;
    size_t         cache_len;

    /*
     * Normalize policy enums and flags before rendering the manifest so
     * equivalent effective configurations produce stable string values.
     */
    if (conf->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD) {
        accept_value = (const u_char *) "wildcard";
        accept_len = sizeof("wildcard") - 1;
    } else if (conf->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_FORCE) {
        accept_value = (const u_char *) "force";
        accept_len = sizeof("force") - 1;
    } else {
        accept_value = (const u_char *) "strict";
        accept_len = sizeof("strict") - 1;
    }
    if (conf->policy.auth_policy == NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY) {
        auth_value = (const u_char *) "deny";
        auth_len = sizeof("deny") - 1;
    } else {
        auth_value = (const u_char *) "allow";
        auth_len = sizeof("allow") - 1;
    }
    if (conf->decompress.auto_decompress) {
        auto_value = (const u_char *) "on";
        auto_len = sizeof("on") - 1;
    } else {
        auto_value = (const u_char *) "off";
        auto_len = sizeof("off") - 1;
    }
    if (conf->policy.conditional_requests
        == NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT)
    {
        cache_value = (const u_char *) "full";
        cache_len = sizeof("full") - 1;
    } else if (conf->policy.conditional_requests
               == NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE)
    {
        cache_value = (const u_char *) "ims_only";
        cache_len = sizeof("ims_only") - 1;
    } else {
        cache_value = (const u_char *) "off";
        cache_len = sizeof("off") - 1;
    }

    /*
     * Keep the field order canonical because the serialized manifest is
     * hashed and compared by the diagnostics contract.
     */
    if (ngx_http_markdown_manifest_field_string(
            builder, "accept", accept_value, accept_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_ACCEPT, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_array(
            builder, "auth_cookies", conf->policy.auth_cookies, NULL, 0,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_AUTH_COOKIES, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "auth_policy", auth_value, auth_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_AUTH_POLICY, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "auto_decompress", auto_value, auto_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DECOMPRESS, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "cache_validation", cache_value, cache_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_CACHE, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_array(
            builder, "content_types", conf->routing.content_types,
            (const u_char *) "text/html", sizeof("text/html") - 1,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_CONTENT, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_bool(
            builder, "diagnostics", conf->ops.diagnostics_enabled,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DIAGNOSTICS, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    return NGX_OK;
}


static void
ngx_http_markdown_manifest_error_value(
    const ngx_http_markdown_conf_t *conf,
    const u_char **value, size_t *value_len)
{
    if (conf->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS) {
        *value = (const u_char *) "pass";
        *value_len = sizeof("pass") - 1;
    } else if (conf->error_status == 429) {
        *value = (const u_char *) "status 429";
        *value_len = sizeof("status 429") - 1;
    } else if (conf->error_status == 503) {
        *value = (const u_char *) "status 503";
        *value_len = sizeof("status 503") - 1;
    } else {
        *value = (const u_char *) "fail_closed";
        *value_len = sizeof("fail_closed") - 1;
    }
}


static void
ngx_http_markdown_manifest_dynamic_value(
    const ngx_http_markdown_conf_t *conf,
    const u_char **value, size_t *value_len)
{
    if (conf->advanced.dynconf_enabled == 1) {
        *value = (const u_char *) "on";
        *value_len = sizeof("on") - 1;
    } else {
        *value = (const u_char *) "off";
        *value_len = sizeof("off") - 1;
    }
}


static void
ngx_http_markdown_manifest_filter_value(
    const ngx_http_markdown_conf_t *conf,
    const ngx_str_t *complex_value,
    const u_char **value, size_t *value_len)
{
    if (complex_value != NULL) {
        *value = complex_value->data;
        *value_len = complex_value->len;
    } else if (ngx_http_markdown_effective_enabled(NULL, conf)) {
        *value = (const u_char *) "on";
        *value_len = sizeof("on") - 1;
    } else {
        *value = (const u_char *) "off";
        *value_len = sizeof("off") - 1;
    }
}


static void
ngx_http_markdown_manifest_flavor_value(
    const ngx_http_markdown_conf_t *conf,
    const u_char **value, size_t *value_len)
{
    if (conf->flavor == NGX_HTTP_MARKDOWN_FLAVOR_GFM) {
        *value = (const u_char *) "gfm";
        *value_len = sizeof("gfm") - 1;
    } else {
        *value = (const u_char *) "commonmark";
        *value_len = sizeof("commonmark") - 1;
    }
}


static ngx_int_t
ngx_http_markdown_manifest_append_runtime_fields(
    ngx_http_markdown_manifest_builder_t *builder,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t explicit)
{
    const ngx_str_t  *complex_value;
    const u_char     *dynamic_value;
    const u_char     *error_value;
    const u_char     *filter_value;
    const u_char     *flavor_value;
    size_t            dynamic_len;
    size_t            error_len;
    size_t            filter_len;
    size_t            flavor_len;

    complex_value = NULL;
    if (conf->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_COMPLEX
        && conf->enabled_complex != NULL)
    {
        complex_value = &conf->enabled_complex->value;
    }

    ngx_http_markdown_manifest_error_value(conf, &error_value, &error_len);
    ngx_http_markdown_manifest_dynamic_value(
        conf, &dynamic_value, &dynamic_len);
    ngx_http_markdown_manifest_filter_value(
        conf, complex_value, &filter_value, &filter_len);
    ngx_http_markdown_manifest_flavor_value(conf, &flavor_value, &flavor_len);

    if (ngx_http_markdown_manifest_field_string(
            builder, "dynamic_config", dynamic_value, dynamic_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DYNCONF, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "dynamic_config_path",
            conf->advanced.dynconf_path.data != NULL
                ? conf->advanced.dynconf_path.data : (const u_char *) "",
            conf->advanced.dynconf_path.data != NULL
                ? conf->advanced.dynconf_path.len : 0,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DYNCONF, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "dynconf_dry_run",
            conf->advanced.dynconf_dry_run == 1
                ? (const u_char *) "on" : (const u_char *) "off",
            conf->advanced.dynconf_dry_run == 1 ? sizeof("on") - 1
                : sizeof("off") - 1,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DRY_RUN, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "error_policy", error_value, error_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_ERROR_POLICY, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "filter", filter_value, filter_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FILTER, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "flavor", flavor_value, flavor_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FLAVOR, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_bool(
            builder, "front_matter", conf->front_matter,
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
    const u_char  *log_value;
    size_t         log_len;

    if (conf->policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_ERROR) {
        log_value = (const u_char *) "error";
        log_len = sizeof("error") - 1;
    } else if (conf->policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN) {
        log_value = (const u_char *) "warn";
        log_len = sizeof("warn") - 1;
    } else if (conf->policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG) {
        log_value = (const u_char *) "debug";
        log_len = sizeof("debug") - 1;
    } else {
        log_value = (const u_char *) "info";
        log_len = sizeof("info") - 1;
    }

    if (ngx_http_markdown_manifest_field_limits(
            builder, conf, explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_LIMITS,
            0) != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "log_verbosity", log_value, log_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_LOG, 0) != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_bool(
            builder, "metrics", conf->ops.metrics_enabled,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_METRICS, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_number(
            builder, "metrics_shm_size", (uint64_t) main_conf->metrics_shm_size,
            0, 0) != NGX_OK)
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
    const u_char  *protection_value;
    const u_char  *selectors_value;
    const u_char  *streaming_value;
    size_t         protection_len;
    size_t         selectors_len;
    size_t         streaming_len;

    if (conf->advanced.prune_protection_selectors != NULL) {
        protection_value = conf->advanced.prune_protection_selectors->data;
        protection_len = conf->advanced.prune_protection_selectors->len;
    } else {
        protection_value = (const u_char *) "";
        protection_len = 0;
    }
    if (conf->advanced.prune_selectors != NULL) {
        selectors_value = conf->advanced.prune_selectors->data;
        selectors_len = conf->advanced.prune_selectors->len;
    } else {
        selectors_value = (const u_char *) "nav footer aside";
        selectors_len = sizeof("nav footer aside") - 1;
    }
    if (conf->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_OFF) {
        streaming_value = (const u_char *) "off";
        streaming_len = sizeof("off") - 1;
    } else if (conf->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_FORCE) {
        streaming_value = (const u_char *) "force";
        streaming_len = sizeof("force") - 1;
    } else {
        streaming_value = (const u_char *) "auto";
        streaming_len = sizeof("auto") - 1;
    }

    if (ngx_http_markdown_manifest_field_bool(
            builder, "prune_noise", conf->advanced.prune_noise,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_PRUNE, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "prune_protection_selectors", protection_value,
            protection_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_PROTECTION, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "prune_selectors", selectors_value, selectors_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_SELECTORS, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_array(
            builder, "stream_excluded_types", conf->stream.excluded_types,
            NULL, 0, explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_EXCLUDED, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_string(
            builder, "streaming", streaming_value, streaming_len,
            explicit & NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_STREAM, 0)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
    if (ngx_http_markdown_manifest_field_bool(
            builder, "token_estimate", conf->token_estimate,
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
    const u_char                          *pos;
    const u_char                          *start;
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

    manifest = ngx_palloc(pool, NGX_HTTP_MARKDOWN_DIAGNOSTICS_MANIFEST_BUF_SIZE);
    if (manifest == NULL) {
        return NGX_ERROR;
    }
    builder.pos = manifest;
    builder.last = manifest + NGX_HTTP_MARKDOWN_DIAGNOSTICS_MANIFEST_BUF_SIZE;
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
