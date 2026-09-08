/*
 * NGINX Markdown Filter Module - Diagnostics Accessor Implementations
 *
 * Provides accessor functions that bridge the diagnostics compilation
 * unit with the module-internal state (metrics pointer, effective conf).
 *
 * This header MUST be included only from the main translation unit
 * (ngx_http_markdown_filter_module.c) after the module_state_impl.h
 * and effective_conf_impl.h headers have been included.
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


static uint32_t
ngx_http_markdown_sha256_ror(uint32_t value, ngx_uint_t bits)
{
    return (uint32_t) ((value >> bits) | (value << (32 - bits)));
}


static ngx_int_t
ngx_http_markdown_sha256_transform(uint32_t state[8], const u_char block[64])
{
    static const uint32_t k[64] = {
        0x428a2f98U, 0x71374491U, 0xb5c0fbcf, 0xe9b5dba5U,
        0x3956c25bU, 0x59f111f1U, 0x923f82a4U, 0xab1c5ed5U,
        0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
        0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U,
        0xe49b69c1U, 0xefbe4786U, 0x0fc19dc6U, 0x240ca1ccU,
        0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
        0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U,
        0xc6e00bf3U, 0xd5a79147U, 0x06ca6351U, 0x14292967U,
        0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
        0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U,
        0xa2bfe8a1U, 0xa81a664bU, 0xc24b8b70U, 0xc76c51a3U,
        0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
        0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U,
        0x391c0cb3U, 0x4ed8aa4aU, 0x5b9cca4fU, 0x682e6ff3U,
        0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
        0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U
    };
    uint32_t w[64];
    uint32_t a, b, c, d, e, f, g, h, t1, t2;
    ngx_uint_t i;

    for (i = 0; i < 16; i++) {
        w[i] = ((uint32_t) block[i * 4] << 24)
               | ((uint32_t) block[i * 4 + 1] << 16)
               | ((uint32_t) block[i * 4 + 2] << 8)
               | (uint32_t) block[i * 4 + 3];
    }
    for (i = 16; i < 64; i++) {
        uint32_t s0 = ngx_http_markdown_sha256_ror(w[i - 15], 7)
                       ^ ngx_http_markdown_sha256_ror(w[i - 15], 18)
                       ^ (w[i - 15] >> 3);
        uint32_t s1 = ngx_http_markdown_sha256_ror(w[i - 2], 17)
                       ^ ngx_http_markdown_sha256_ror(w[i - 2], 19)
                       ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }

    a = state[0]; b = state[1]; c = state[2]; d = state[3];
    e = state[4]; f = state[5]; g = state[6]; h = state[7];
    for (i = 0; i < 64; i++) {
        uint32_t s1 = ngx_http_markdown_sha256_ror(e, 6)
                      ^ ngx_http_markdown_sha256_ror(e, 11)
                      ^ ngx_http_markdown_sha256_ror(e, 25);
        uint32_t ch = (e & f) ^ ((~e) & g);
        uint32_t s0 = ngx_http_markdown_sha256_ror(a, 2)
                      ^ ngx_http_markdown_sha256_ror(a, 13)
                      ^ ngx_http_markdown_sha256_ror(a, 22);
        uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
        t1 = h + s1 + ch + k[i] + w[i];
        t2 = s0 + maj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
    return NGX_OK;
}


typedef struct {
    uint32_t  state[8];
    uint64_t  bits;
    u_char    block[64];
    size_t    block_len;
} ngx_http_markdown_sha256_t;


static void
ngx_http_markdown_sha256_init(ngx_http_markdown_sha256_t *ctx)
{
    static const uint32_t initial[8] = {
        0x6a09e667U, 0xbb67ae85U, 0x3c6ef372U, 0xa54ff53aU,
        0x510e527fU, 0x9b05688cU, 0x1f83d9abU, 0x5be0cd19U
    };
    ngx_memcpy(ctx->state, initial, sizeof(initial));
    ctx->bits = 0;
    ctx->block_len = 0;
}


static void
ngx_http_markdown_sha256_update(ngx_http_markdown_sha256_t *ctx,
    const u_char *data, size_t len)
{
    size_t take;

    while (len != 0) {
        take = ngx_min(len, sizeof(ctx->block) - ctx->block_len);
        ngx_memcpy(ctx->block + ctx->block_len, data, take);
        ctx->block_len += take;
        ctx->bits += (uint64_t) take * 8U;
        data += take;
        len -= take;
        if (ctx->block_len == sizeof(ctx->block)) {
            (void) ngx_http_markdown_sha256_transform(ctx->state, ctx->block);
            ctx->block_len = 0;
        }
    }
}


static void
ngx_http_markdown_sha256_final(ngx_http_markdown_sha256_t *ctx, u_char out[32])
{
    ngx_uint_t i;
    uint64_t bits;

    bits = ctx->bits;
    ctx->block[ctx->block_len++] = 0x80;
    while (ctx->block_len != 56) {
        if (ctx->block_len == sizeof(ctx->block)) {
            (void) ngx_http_markdown_sha256_transform(ctx->state, ctx->block);
            ctx->block_len = 0;
        }
        ctx->block[ctx->block_len++] = 0;
    }
    for (i = 0; i < 8; i++) {
        ctx->block[56 + i] = (u_char) (bits >> (56 - i * 8));
    }
    (void) ngx_http_markdown_sha256_transform(ctx->state, ctx->block);
    for (i = 0; i < 8; i++) {
        out[i * 4] = (u_char) (ctx->state[i] >> 24);
        out[i * 4 + 1] = (u_char) (ctx->state[i] >> 16);
        out[i * 4 + 2] = (u_char) (ctx->state[i] >> 8);
        out[i * 4 + 3] = (u_char) ctx->state[i];
    }
}


static ngx_int_t
ngx_http_markdown_sha256_hex(const u_char *data, size_t len, u_char out[64])
{
    static const u_char hex[] = "0123456789abcdef";
    ngx_http_markdown_sha256_t ctx;
    u_char digest[32];
    ngx_uint_t i;

    if (data == NULL && len != 0) {
        return NGX_ERROR;
    }
    ngx_http_markdown_sha256_init(&ctx);
    ngx_http_markdown_sha256_update(&ctx, data, len);
    ngx_http_markdown_sha256_final(&ctx, digest);
    for (i = 0; i < sizeof(digest); i++) {
        out[i * 2] = hex[digest[i] >> 4];
        out[i * 2 + 1] = hex[digest[i] & 0x0f];
    }
    return NGX_OK;
}


#ifdef NGINX_MARKDOWN_CONVERTER_H

void
ngx_http_markdown_diagnostics_get_effective(
    const void *opaque_conf,
    ngx_http_markdown_diag_effective_t *out)
{
    const ngx_http_markdown_conf_t          *conf;
    ngx_http_markdown_effective_conf_t       effective;

    if (out == NULL) {
        return;
    }

    ngx_memzero(out, sizeof(ngx_http_markdown_diag_effective_t));
    conf = (const ngx_http_markdown_conf_t *) opaque_conf;
    if (conf == NULL) {
        return;
    }

    ngx_memzero(&effective, sizeof(effective));
    ngx_http_markdown_build_effective_conf(&effective, conf);

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
            ",\"parser_budget\":") != NGX_OK
        || ngx_http_markdown_manifest_json_u64(builder,
            (uint64_t) conf->limits.parser_budget) != NGX_OK
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
    if (conf->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_FORCE) {
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
    const u_char     *error_value;
    const u_char     *filter_value;
    const u_char     *flavor_value;
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
    ngx_http_markdown_manifest_filter_value(
        conf, complex_value, &filter_value, &filter_len);
    ngx_http_markdown_manifest_flavor_value(conf, &flavor_value, &flavor_len);

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
    const u_char  *streaming_value;
    size_t         streaming_len;

    /*
     * Custom prune/protection selectors were removed in 0.9.2 (LTS-R009); the
     * diagnostics manifest no longer emits the prune_selectors /
     * prune_protection_selectors fields.  Built-in noise reduction is still
     * reported via prune_noise below.
     */
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
    if (ngx_http_markdown_sha256_hex(start, (size_t) (pos - start),
                            digest) != NGX_OK)
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
