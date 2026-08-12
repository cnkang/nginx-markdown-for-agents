#ifndef NGX_HTTP_MARKDOWN_CONFIG_HANDLERS_IMPL_H
#define NGX_HTTP_MARKDOWN_CONFIG_HANDLERS_IMPL_H

/*
 * Directive-handler helpers.
 *
 * WARNING: This header is an implementation detail of the main translation unit
 * (ngx_http_markdown_filter_module.c). It must NOT be included from any other
 * .c file or used as a standalone compilation unit.
 *
 * This unit keeps custom directive parsing and validation separate from
 * configuration object lifecycle and the directive registry table.
 */

#include <errno.h>
#include <stdlib.h>
#include "ngx_http_markdown_diagnostics.h"
#include "ngx_http_markdown_directive_names.h"

static ngx_int_t
ngx_http_markdown_conf_args_ready(
    const ngx_conf_t *cf,
    ngx_uint_t min_nelts)
{
    return cf != NULL
        && cf->args != NULL
        && cf->args->elts != NULL
        && cf->args->nelts >= min_nelts;
}

/*
 * Case-insensitive comparison of an ngx_str_t argument against a
 * known NUL-terminated expected string.
 *
 * Returns 1 if arg has the same length and case-insensitive content
 * as expected, 0 otherwise.  NULL or empty arguments return 0.
 *
 * Parameters:
 *   arg          - argument to compare
 *   expected     - expected string bytes
 *   expected_len - length of expected string
 *
 * Returns:
 *   1 if equal (case-insensitive), 0 otherwise
 */
static ngx_int_t
ngx_http_markdown_arg_equals(
    const ngx_str_t *arg,
    const u_char *expected,
    size_t expected_len)
{
    if (arg == NULL || arg->data == NULL) {
        return 0;
    }

    if (arg->len != expected_len) {
        return 0;
    }

    for (size_t i = 0; i < expected_len; i++) {
        if (ngx_tolower(arg->data[i]) != ngx_tolower(expected[i])) {
            return 0;
        }
    }

    return 1;
}

/*
 * Resolve a size suffix character (k/K, m/M, g/G) to its multiplier.
 * Returns 0 for no suffix (bare number), or NGX_ERROR for an unrecognized
 * character so the caller can distinguish "no suffix" from "invalid
 * suffix".  Caller must check: 0 = bare, NGX_ERROR = invalid.
 */
static ngx_int_t
ngx_http_markdown_size_suffix_scale(char suffix)
{
    switch (suffix) {
    case 'k':
    case 'K':
        return 1024;
    case 'm':
    case 'M':
        return 1024 * 1024;
    case 'g':
    case 'G':
        return 1024 * 1024 * 1024;
    default:
        return NGX_ERROR;
    }
}

static ngx_int_t
ngx_http_markdown_parse_size_number(
    char *buf,
    size_t len,
    size_t *value,
    char *suffix)
{
    char                *endptr;
    const char          *number;
    unsigned long long   raw;

    if (buf == NULL || value == NULL || suffix == NULL || len == 0) {
        return NGX_ERROR;
    }

    number = buf;
    while (*number == ' ' || *number == '\t' || *number == '\r'
           || *number == '\n' || *number == '\f' || *number == '\v')
    {
        number++;
    }
    if (*number == '\0' || *number == '-') {
        return NGX_ERROR;
    }

    *suffix = '\0';
    if (len > 1) {
        switch (buf[len - 1]) {
        case 'k': case 'K': case 'm': case 'M': case 'g': case 'G':
            *suffix = buf[len - 1];
            buf[len - 1] = '\0';
            break;
        default:
            break;
        }
    }

    errno = 0;
    raw = strtoull(number, &endptr, 10);
    if (errno == ERANGE || endptr == number || *endptr != '\0'
        || raw > (unsigned long long) NGX_MAX_SIZE_T_VALUE)
    {
        return NGX_ERROR;
    }
    *value = (size_t) raw;
    return NGX_OK;
}

/*
 * Parse an ngx_str_t size token using the module's directive
 * semantics.  Supports the same suffix families as NGINX's size
 * parser (k/K, m/M, g/G) and rejects overflow or malformed input.
 */
static size_t
ngx_http_markdown_parse_size(const ngx_str_t *line)
{
    char                 buf[64];
    size_t               len;
    size_t               value;
    ngx_int_t            scale;
    char                 suffix;

    if (line == NULL || line->data == NULL || line->len == 0) {
        return (size_t) NGX_ERROR;
    }

    len = line->len;
    if (len >= sizeof(buf)) {
        return (size_t) NGX_ERROR;
    }

    memcpy(buf, line->data, len);
    buf[len] = '\0';

    if (ngx_http_markdown_parse_size_number(
            buf, len, &value, &suffix) != NGX_OK)
    {
        return (size_t) NGX_ERROR;
    }

    if (suffix == '\0') {
        return value;
    }

    scale = ngx_http_markdown_size_suffix_scale(suffix);
    if (scale == NGX_ERROR) {
        return (size_t) NGX_ERROR;
    }

    if (value > NGX_MAX_SIZE_T_VALUE / (size_t) scale) {
        return (size_t) NGX_ERROR;
    }

    return value * (size_t) scale;
}

/*
 * Parse an ngx_str_t time token into milliseconds.
 *
 * Self-contained (libc only) so the directive parser is identical in the
 * production module and in the standalone unit harness.  Supports the suffix
 * families ms, s, m, h.  A bare number is interpreted as seconds (NGINX
 * convention).  Returns the value in milliseconds, or (ngx_msec_t) NGX_ERROR
 * on overflow or malformed input.
 */
static ngx_msec_t
ngx_http_markdown_parse_time_ms(const ngx_str_t *line)
{
    char                 buf[64];
    char                *endptr;
    size_t               len;
    size_t               suffix_len;
    unsigned long long   raw;
    unsigned long long   scale;

    if (line == NULL || line->data == NULL || line->len == 0) {
        return (ngx_msec_t) NGX_ERROR;
    }

    len = line->len;
    if (len >= sizeof(buf)) {
        return (ngx_msec_t) NGX_ERROR;
    }

    memcpy(buf, line->data, len);
    buf[len] = '\0';

    if (buf[0] == '-') {
        return (ngx_msec_t) NGX_ERROR;
    }

    scale = 1000; /* bare number => seconds */
    suffix_len = 0;

    if (len >= 2 && buf[len - 2] == 'm' && buf[len - 1] == 's') {
        scale = 1;
        suffix_len = 2;
    } else if (len >= 1) {
        switch (buf[len - 1]) {
        case 's': scale = 1000; suffix_len = 1; break;
        case 'm': scale = 60ULL * 1000; suffix_len = 1; break;
        case 'h': scale = 60ULL * 60 * 1000; suffix_len = 1; break;
        default: break;
        }
    }

    if (suffix_len > 0) {
        buf[len - suffix_len] = '\0';
    }

    if (buf[0] == '\0') {
        return (ngx_msec_t) NGX_ERROR;
    }

    errno = 0;
    raw = strtoull(buf, &endptr, 10);
    if (errno == ERANGE || endptr == buf || *endptr != '\0') {
        return (ngx_msec_t) NGX_ERROR;
    }

    if (raw > (unsigned long long) NGX_MAX_SIZE_T_VALUE / scale) {
        return (ngx_msec_t) NGX_ERROR;
    }

    return (ngx_msec_t) (raw * scale);
}

/*
 * Parse an ngx_str_t positive-integer token.
 *
 * Self-contained (libc only).  Returns the parsed value, or
 * (ngx_uint_t) NGX_ERROR on overflow or malformed input.  A leading sign or
 * any non-digit character is rejected.
 */
static ngx_uint_t
ngx_http_markdown_parse_uint(const ngx_str_t *line)
{
    char                 buf[32];
    char                *endptr;
    size_t               len;
    unsigned long long   raw;

    if (line == NULL || line->data == NULL || line->len == 0) {
        return (ngx_uint_t) NGX_ERROR;
    }

    len = line->len;
    if (len >= sizeof(buf)) {
        return (ngx_uint_t) NGX_ERROR;
    }

    memcpy(buf, line->data, len);
    buf[len] = '\0';

    if (buf[0] < '0' || buf[0] > '9') {
        return (ngx_uint_t) NGX_ERROR;
    }

    errno = 0;
    raw = strtoull(buf, &endptr, 10);
    if (errno == ERANGE || endptr == buf || *endptr != '\0'
        || raw > (unsigned long long) NGX_MAX_SIZE_T_VALUE)
    {
        return (ngx_uint_t) NGX_ERROR;
    }

    return (ngx_uint_t) raw;
}

typedef struct {
    ngx_uint_t  conversion_timeout;
    ngx_uint_t  parser_timeout;
    ngx_uint_t  conversion_memory;
    ngx_uint_t  parser_memory;
    ngx_uint_t  streaming_buffer;
    ngx_uint_t  decompressed_size;
    ngx_uint_t  decompression_ratio;
    ngx_uint_t  max_inflight;
} ngx_http_markdown_limits_seen_t;

static u_char  ngx_http_markdown_limit_key_conversion_timeout[] =
    "conversion_timeout";
static u_char  ngx_http_markdown_limit_key_parser_timeout[] =
    "parser_timeout";
static u_char  ngx_http_markdown_limit_key_conversion_memory[] =
    "conversion_memory";
static u_char  ngx_http_markdown_limit_key_parser_memory[] =
    "parser_memory";
static u_char  ngx_http_markdown_limit_key_streaming_buffer[] =
    "streaming_buffer";
static u_char  ngx_http_markdown_limit_key_decompressed_size[] =
    "decompressed_size";
static u_char  ngx_http_markdown_limit_key_decompression_ratio[] =
    "decompression_ratio";
static u_char  ngx_http_markdown_limit_key_max_inflight[] =
    "max_inflight";

/*
 * Range constants for markdown_limits (0.9.2 frozen contract).
 *
 * Duration: minimum 1ms, maximum 1h (3600000ms).
 * Size:     minimum 64k (65536), maximum 1g (1073741824).
 * Ratio:    minimum 1, maximum 10000.
 * Inflight: minimum 1, maximum 65535.
 */
#define NGX_HTTP_MARKDOWN_LIMITS_DURATION_MIN     1        /* 1ms */
#define NGX_HTTP_MARKDOWN_LIMITS_DURATION_MAX     3600000  /* 1h */
#define NGX_HTTP_MARKDOWN_LIMITS_SIZE_MIN         65536    /* 64k */
#define NGX_HTTP_MARKDOWN_LIMITS_SIZE_MAX         1073741824  /* 1g */
#define NGX_HTTP_MARKDOWN_LIMITS_RATIO_MIN        1
#define NGX_HTTP_MARKDOWN_LIMITS_RATIO_MAX        10000
#define NGX_HTTP_MARKDOWN_LIMITS_INFLIGHT_MIN     1
#define NGX_HTTP_MARKDOWN_LIMITS_INFLIGHT_MAX     65535

static char *
ngx_http_markdown_apply_conversion_timeout_limit(ngx_conf_t *cf,
    ngx_command_t *cmd, ngx_http_markdown_conf_t *mcf,
    const ngx_str_t *val, ngx_http_markdown_limits_seen_t *seen)
{
    ngx_msec_t  ms;

    if (seen->conversion_timeout) {
        return "has a duplicate \"conversion_timeout\" key";
    }

    seen->conversion_timeout = 1;
    ms = ngx_http_markdown_parse_time_ms(val);
    if (ms == (ngx_msec_t) NGX_ERROR || ms == 0) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid \"conversion_timeout\" value \"%V\" in \"%V\"; "
            "must be a positive duration (e.g. 30s, 500ms)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (ms > NGX_HTTP_MARKDOWN_LIMITS_DURATION_MAX)
    {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"conversion_timeout\" value \"%V\" in \"%V\" "
            "is outside the allowed range (1ms..1h)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    mcf->limits.conversion_timeout = ms;
    return NGX_CONF_OK;
}

static char *
ngx_http_markdown_apply_parser_timeout_limit(ngx_conf_t *cf,
    ngx_command_t *cmd, ngx_http_markdown_conf_t *mcf,
    const ngx_str_t *val, ngx_http_markdown_limits_seen_t *seen)
{
    ngx_msec_t  ms;

    if (seen->parser_timeout) {
        return "has a duplicate \"parser_timeout\" key";
    }

    seen->parser_timeout = 1;
    ms = ngx_http_markdown_parse_time_ms(val);
    if (ms == (ngx_msec_t) NGX_ERROR || ms == 0) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid \"parser_timeout\" value \"%V\" in \"%V\"; "
            "must be a positive duration (e.g. 10s, 500ms)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (ms > NGX_HTTP_MARKDOWN_LIMITS_DURATION_MAX)
    {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"parser_timeout\" value \"%V\" in \"%V\" "
            "is outside the allowed range (1ms..1h)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    mcf->limits.parser_timeout = ms;
    return NGX_CONF_OK;
}

static char *
ngx_http_markdown_apply_conversion_memory_limit(ngx_conf_t *cf,
    ngx_command_t *cmd, ngx_http_markdown_conf_t *mcf,
    const ngx_str_t *val, ngx_http_markdown_limits_seen_t *seen)
{
    size_t  sz;

    if (seen->conversion_memory) {
        return "has a duplicate \"conversion_memory\" key";
    }

    seen->conversion_memory = 1;
    sz = ngx_http_markdown_parse_size(val);
    if (sz == (size_t) NGX_ERROR || sz == 0) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid \"conversion_memory\" value \"%V\" in \"%V\"; "
            "must be a positive size (e.g. 64m)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (sz < NGX_HTTP_MARKDOWN_LIMITS_SIZE_MIN
        || sz > NGX_HTTP_MARKDOWN_LIMITS_SIZE_MAX)
    {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"conversion_memory\" value \"%V\" in \"%V\" "
            "is outside the allowed range (64k..1g)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    mcf->limits.conversion_memory = sz;
    return NGX_CONF_OK;
}

static char *
ngx_http_markdown_apply_parser_memory_limit(ngx_conf_t *cf,
    ngx_command_t *cmd, ngx_http_markdown_conf_t *mcf,
    const ngx_str_t *val, ngx_http_markdown_limits_seen_t *seen)
{
    size_t  sz;

    if (seen->parser_memory) {
        return "has a duplicate \"parser_memory\" key";
    }

    seen->parser_memory = 1;
    sz = ngx_http_markdown_parse_size(val);
    if (sz == (size_t) NGX_ERROR || sz == 0) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid \"parser_memory\" value \"%V\" in \"%V\"; "
            "must be a positive size (e.g. 32m)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (sz < NGX_HTTP_MARKDOWN_LIMITS_SIZE_MIN
        || sz > NGX_HTTP_MARKDOWN_LIMITS_SIZE_MAX)
    {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"parser_memory\" value \"%V\" in \"%V\" "
            "is outside the allowed range (64k..1g)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    mcf->limits.parser_memory = sz;
    return NGX_CONF_OK;
}

static char *
ngx_http_markdown_apply_streaming_buffer_limit(ngx_conf_t *cf,
    ngx_command_t *cmd, ngx_http_markdown_conf_t *mcf,
    const ngx_str_t *val, ngx_http_markdown_limits_seen_t *seen)
{
    size_t  sz;

    if (seen->streaming_buffer) {
        return "has a duplicate \"streaming_buffer\" key";
    }

    seen->streaming_buffer = 1;
    sz = ngx_http_markdown_parse_size(val);
    if (sz == (size_t) NGX_ERROR || sz == 0) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid \"streaming_buffer\" value \"%V\" in \"%V\"; "
            "must be a positive size (e.g. 2m)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (sz < NGX_HTTP_MARKDOWN_LIMITS_SIZE_MIN
        || sz > NGX_HTTP_MARKDOWN_LIMITS_SIZE_MAX)
    {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"streaming_buffer\" value \"%V\" in \"%V\" "
            "is outside the allowed range (64k..1g)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    mcf->limits.streaming_buffer = sz;
    return NGX_CONF_OK;
}

static char *
ngx_http_markdown_apply_decompressed_size_limit(ngx_conf_t *cf,
    ngx_command_t *cmd, ngx_http_markdown_conf_t *mcf,
    const ngx_str_t *val, ngx_http_markdown_limits_seen_t *seen)
{
    size_t  sz;

    if (seen->decompressed_size) {
        return "has a duplicate \"decompressed_size\" key";
    }

    seen->decompressed_size = 1;
    sz = ngx_http_markdown_parse_size(val);
    if (sz == (size_t) NGX_ERROR || sz == 0) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid \"decompressed_size\" value \"%V\" in \"%V\"; "
            "must be a positive size (e.g. 10m)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (sz < NGX_HTTP_MARKDOWN_LIMITS_SIZE_MIN
        || sz > NGX_HTTP_MARKDOWN_LIMITS_SIZE_MAX)
    {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"decompressed_size\" value \"%V\" in \"%V\" "
            "is outside the allowed range (64k..1g)",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    mcf->limits.decompressed_size = sz;
    return NGX_CONF_OK;
}

static char *
ngx_http_markdown_apply_decompression_ratio_limit(ngx_conf_t *cf,
    ngx_command_t *cmd, ngx_http_markdown_conf_t *mcf,
    const ngx_str_t *val, ngx_http_markdown_limits_seen_t *seen)
{
    ngx_uint_t  n;

    if (seen->decompression_ratio) {
        return "has a duplicate \"decompression_ratio\" key";
    }

    seen->decompression_ratio = 1;
    n = ngx_http_markdown_parse_uint(val);
    if (n == (ngx_uint_t) NGX_ERROR || n == 0) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid \"decompression_ratio\" value \"%V\" in \"%V\"; "
            "must be an integer greater than 0",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (n > 10000) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"decompression_ratio\" value %ui in \"%V\" "
            "exceeds maximum 10000",
            n, &cmd->name);
        return NGX_CONF_ERROR;
    }

    mcf->limits.decompression_ratio = n;
    return NGX_CONF_OK;
}

static char *
ngx_http_markdown_apply_max_inflight_limit(ngx_conf_t *cf,
    ngx_command_t *cmd, ngx_http_markdown_conf_t *mcf,
    const ngx_str_t *val, ngx_http_markdown_limits_seen_t *seen)
{
    ngx_uint_t  n;

    if (seen->max_inflight) {
        return "has a duplicate \"max_inflight\" key";
    }

    seen->max_inflight = 1;
    n = ngx_http_markdown_parse_uint(val);
    if (n == (ngx_uint_t) NGX_ERROR || n == 0) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid \"max_inflight\" value \"%V\" in \"%V\"; "
            "must be an integer greater than 0",
            val, &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (n > 65535) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"max_inflight\" value %ui in \"%V\" "
            "exceeds maximum 65535",
            n, &cmd->name);
        return NGX_CONF_ERROR;
    }

    mcf->limits.max_inflight = n;
    return NGX_CONF_OK;
}

static char *
ngx_http_markdown_apply_limit_arg(ngx_conf_t *cf, ngx_command_t *cmd,
    ngx_http_markdown_conf_t *mcf, const ngx_str_t *key,
    const ngx_str_t *val, ngx_http_markdown_limits_seen_t *seen)
{
    if (ngx_http_markdown_arg_equals(
            key, ngx_http_markdown_limit_key_conversion_timeout,
            sizeof(ngx_http_markdown_limit_key_conversion_timeout) - 1))
    {
        return ngx_http_markdown_apply_conversion_timeout_limit(
            cf, cmd, mcf, val, seen);
    }

    if (ngx_http_markdown_arg_equals(
            key, ngx_http_markdown_limit_key_parser_timeout,
            sizeof(ngx_http_markdown_limit_key_parser_timeout) - 1))
    {
        return ngx_http_markdown_apply_parser_timeout_limit(
            cf, cmd, mcf, val, seen);
    }

    if (ngx_http_markdown_arg_equals(
            key, ngx_http_markdown_limit_key_conversion_memory,
            sizeof(ngx_http_markdown_limit_key_conversion_memory) - 1))
    {
        return ngx_http_markdown_apply_conversion_memory_limit(
            cf, cmd, mcf, val, seen);
    }

    if (ngx_http_markdown_arg_equals(
            key, ngx_http_markdown_limit_key_parser_memory,
            sizeof(ngx_http_markdown_limit_key_parser_memory) - 1))
    {
        return ngx_http_markdown_apply_parser_memory_limit(
            cf, cmd, mcf, val, seen);
    }

    if (ngx_http_markdown_arg_equals(
            key, ngx_http_markdown_limit_key_streaming_buffer,
            sizeof(ngx_http_markdown_limit_key_streaming_buffer) - 1))
    {
        return ngx_http_markdown_apply_streaming_buffer_limit(
            cf, cmd, mcf, val, seen);
    }

    if (ngx_http_markdown_arg_equals(
            key, ngx_http_markdown_limit_key_decompressed_size,
            sizeof(ngx_http_markdown_limit_key_decompressed_size) - 1))
    {
        return ngx_http_markdown_apply_decompressed_size_limit(
            cf, cmd, mcf, val, seen);
    }

    if (ngx_http_markdown_arg_equals(
            key, ngx_http_markdown_limit_key_decompression_ratio,
            sizeof(ngx_http_markdown_limit_key_decompression_ratio)
            - 1))
    {
        return ngx_http_markdown_apply_decompression_ratio_limit(
            cf, cmd, mcf, val, seen);
    }

    if (ngx_http_markdown_arg_equals(
            key, ngx_http_markdown_limit_key_max_inflight,
            sizeof(ngx_http_markdown_limit_key_max_inflight) - 1))
    {
        return ngx_http_markdown_apply_max_inflight_limit(
            cf, cmd, mcf, val, seen);
    }

    ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
        "unknown key \"%V\" in \"%V\" directive; valid keys are "
        "conversion_timeout, parser_timeout, conversion_memory, "
        "parser_memory, streaming_buffer, decompressed_size, "
        "decompression_ratio, max_inflight",
        key, &cmd->name);
    return NGX_CONF_ERROR;
}

/*
 * Configuration directive handler: markdown_limits (Config V2, 0.9.2).
 *
 * Unified limits block with 8 semantically orthogonal keys.  Grammar:
 *
 *   markdown_limits conversion_timeout=<time> parser_timeout=<time>
 *                   conversion_memory=<size> parser_memory=<size>
 *                   streaming_buffer=<size> decompressed_size=<size>
 *                   decompression_ratio=<N> max_inflight=<N>;
 *
 * Keys are space-separated key=value tokens; any subset may be given and
 * unspecified keys inherit via normal merge (per-key inheritance).  Each
 * key writes into the unified conf->limits structure.
 *
 * Validation (rejected at nginx -t):
 *   - duplicate key within one directive
 *   - unknown key
 *   - zero value for any key (an invalid limit)
 *   - malformed size/time/integer value
 *   - overflow
 */
static char *
ngx_http_markdown_limits(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    ngx_http_markdown_limits_seen_t  seen;

    if (mcf == NULL || !ngx_http_markdown_conf_args_ready(cf, 2)) {
        return NGX_CONF_ERROR;
    }

    if (mcf->limits.configured) {
        return "is duplicate";
    }

    ngx_memzero(&seen, sizeof(seen));
    value = cf->args->elts;

    /*
     * Iterate over directive arguments (skipping argv[0] = directive name).
     * Each argument must be key=value; keys are matched case-insensitively
     * and mapped to the corresponding conf->limits field.  Duplicate keys,
     * unknown keys, and zero/malformed values are rejected at nginx -t time.
     */
    for (ngx_uint_t i = 1; i < cf->args->nelts; i++) {
        u_char    *eq;
        char      *rc;
        ngx_str_t  key;
        ngx_str_t  val;
        size_t     vlen;

        /* Split argument at '=' into key and value substrings. */
        eq = ngx_strlchr(value[i].data, value[i].data + value[i].len, '=');
        if (eq == NULL || eq == value[i].data
            || (size_t) (eq - value[i].data) == value[i].len - 1)
        {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                "invalid value \"%V\" in \"%V\" directive, "
                "each argument must be key=value "
                "(conversion_timeout|parser_timeout|conversion_memory|"
                "parser_memory|streaming_buffer|decompressed_size|"
                "decompression_ratio|max_inflight)",
                &value[i], &cmd->name);
            return NGX_CONF_ERROR;
        }

        key.data = value[i].data;
        key.len = (size_t) (eq - value[i].data);
        vlen = value[i].len - key.len - 1;
        val.data = eq + 1;
        val.len = vlen;

        rc = ngx_http_markdown_apply_limit_arg(
            cf, cmd, mcf, &key, &val, &seen);
        if (rc != NGX_CONF_OK) {
            return rc;
        }
    }

    mcf->limits.configured = 1;
    return NGX_CONF_OK;
}

/*
 * Pool cleanup handler that frees the Rust-owned trusted-proxy CIDR set.
 *
 * Registered against the configuration pool so the handle lives for the
 * configuration cycle and is released on reload/shutdown.
 */
static void
ngx_http_markdown_trusted_proxies_cleanup(void *data)
{
    markdown_trusted_proxies_free(data);
}


/*
 * Configuration directive handler: markdown_trusted_proxies (spec 47).
 *
 *   markdown_trusted_proxies <CIDR>...;
 *   markdown_trusted_proxies off;
 *
 * Context: http only.  server/location context is rejected with a clear
 * migration hint (no per-location trust to avoid local trust-bypass risk).
 *
 * Each CIDR is validated at config time by the Rust core
 * (markdown_trusted_proxies_push); a malformed IPv4/IPv6 CIDR fails
 * "nginx -t" with the offending value.  "off" disables trust entirely
 * (configured, empty set).  CIDR parsing happens once here; request-time
 * matching is performed in Rust.  The handle is stored on the main conf and
 * freed by an NGINX pool cleanup handler.
 */
static char *
ngx_http_markdown_trusted_proxies(ngx_conf_t *cf, ngx_command_t *cmd,
    void *conf)
{
    static u_char                   off_str[] = "off";
    ngx_http_markdown_main_conf_t  *mmcf = conf;
    ngx_pool_cleanup_t             *cln;
    struct MarkdownTrustedProxies  *set;
    ngx_str_t                      *manifest_entry;
    ngx_str_t                      *value;
    uint8_t                         rc;

    /*
     * The command table is MAIN_CONF-only, so NGINX rejects server/location
     * use before dispatch.  Keep this guard for direct calls and defensive
     * protection if the command flags ever regress.
     */
    if (cf == NULL || cmd == NULL || mmcf == NULL
        || !ngx_http_markdown_conf_args_ready(cf, 2))
    {
        return NGX_CONF_ERROR;
    }

    if (!(cf->cmd_type & NGX_HTTP_MAIN_CONF)) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"%V\" directive is only valid in the http context, not in "
            "server or location (see docs/guides/MIGRATION-0.9.md)",
            &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (mmcf->trusted_proxies_configured) {
        return "is duplicate";
    }

    value = cf->args->elts;

    /* markdown_trusted_proxies off; -> configured, no trusted CIDRs. */
    if (cf->args->nelts == 2
        && ngx_http_markdown_arg_equals(&value[1], off_str,
                                        sizeof(off_str) - 1))
    {
        mmcf->trusted_proxies_manifest = NULL;
        mmcf->trusted_proxies_configured = 1;
        mmcf->trusted_proxies = NULL;
        return NGX_CONF_OK;
    }

    if (cf->args->nelts - 1 > NGX_HTTP_MARKDOWN_TRUSTED_PROXIES_MAX) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"%V\" directive accepts at most %ui entries, got %ui",
            &cmd->name,
            (ngx_uint_t) NGX_HTTP_MARKDOWN_TRUSTED_PROXIES_MAX,
            cf->args->nelts - 1);
        return NGX_CONF_ERROR;
    }

    mmcf->trusted_proxies_manifest = ngx_array_create(
        cf->pool, cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mmcf->trusted_proxies_manifest == NULL) {
        return NGX_CONF_ERROR;
    }

    set = markdown_trusted_proxies_new();
    if (set == NULL) {
        return NGX_CONF_ERROR;
    }

    /*
     * Register the cleanup before pushing CIDRs so the handle is always
     * released, including on a mid-loop validation failure.
     */
    cln = ngx_pool_cleanup_add(cf->pool, 0);
    if (cln == NULL) {
        markdown_trusted_proxies_free(set);
        return NGX_CONF_ERROR;
    }
    cln->handler = ngx_http_markdown_trusted_proxies_cleanup;
    cln->data = set;

    for (ngx_uint_t i = 1; i < cf->args->nelts; i++) {
        manifest_entry = ngx_array_push(mmcf->trusted_proxies_manifest);
        if (manifest_entry == NULL) {
            return NGX_CONF_ERROR;
        }
        *manifest_entry = value[i];
        rc = markdown_trusted_proxies_push(set, value[i].data, value[i].len);
        if (rc != TRUSTED_PROXIES_PUSH_OK) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                "invalid CIDR \"%V\" in \"%V\" directive; expected an IPv4 "
                "or IPv6 CIDR (e.g. 10.0.0.0/8, 2001:db8::/32) or \"off\"",
                &value[i], &cmd->name);
            return NGX_CONF_ERROR;
        }
    }

    mmcf->trusted_proxies = set;
    mmcf->trusted_proxies_configured = 1;

    return NGX_CONF_OK;
}

/*
 * Configuration directive handler: markdown_filter
 *
 * Supported values:
 * - on | off          (static configuration)
 * - $variable         (dynamic per-request switch)
 * - complex value containing variables
 */
static char *
ngx_http_markdown_filter(ngx_conf_t *cf,
    ngx_command_t *cmd,
    void *conf)
{
    static u_char                      on_str[]  = "on";
    static u_char                      off_str[] = "off";
    ngx_http_markdown_conf_t          *mcf = conf;
    ngx_http_compile_complex_value_t   ccv;
    ngx_http_complex_value_t          *complex_value;
    ngx_str_t                         *value;
    const u_char                      *var_marker;

    (void) cmd;

    if (mcf == NULL || cmd == NULL
        || !ngx_http_markdown_conf_args_ready(cf, 2))
    {
        return NGX_CONF_ERROR;
    }

    if (mcf->enabled_source != NGX_HTTP_MARKDOWN_ENABLED_UNSET) {
        return "is duplicate";
    }

    value = cf->args->elts;

    if (ngx_http_markdown_arg_equals(&value[1], on_str,
                                     sizeof(on_str) - 1))
    {
        mcf->enabled = 1;
        mcf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
        mcf->enabled_complex = NULL;
        /*
         * Set block bit when at server/location level (not http).
         * http-level settings are the baseline dynconf overrides.
         */
        if (cf->cmd_type & (NGX_HTTP_SRV_CONF | NGX_HTTP_LOC_CONF)) {
            mcf->advanced.dynconf_block_mask |=
                NGX_HTTP_MARKDOWN_BLOCK_FILTER;
        }
        return NGX_CONF_OK;
    }

    if (ngx_http_markdown_arg_equals(&value[1], off_str,
                                     sizeof(off_str) - 1))
    {
        mcf->enabled = 0;
        mcf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
        mcf->enabled_complex = NULL;
        /*
         * Set block bit when at server/location level (not http).
         */
        if (cf->cmd_type & (NGX_HTTP_SRV_CONF | NGX_HTTP_LOC_CONF)) {
            mcf->advanced.dynconf_block_mask |=
                NGX_HTTP_MARKDOWN_BLOCK_FILTER;
        }
        return NGX_CONF_OK;
    }

    var_marker = ngx_strlchr(value[1].data, value[1].data + value[1].len, '$');
    if (var_marker == NULL) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "invalid value \"%V\" in \"markdown_filter\" directive, "
                           "it must be \"on\", \"off\", or contain a variable",
                           &value[1]);
        return NGX_CONF_ERROR;
    }

    complex_value = ngx_palloc(cf->pool, sizeof(ngx_http_complex_value_t));
    if (complex_value == NULL) {
        return NGX_CONF_ERROR;
    }

    ngx_memzero(&ccv, sizeof(ngx_http_compile_complex_value_t));
    ccv.cf = cf;
    ccv.value = &value[1];
    ccv.complex_value = complex_value;

    if (ngx_http_compile_complex_value(&ccv) != NGX_OK) {
        return NGX_CONF_ERROR;
    }

    mcf->enabled = 0;
    mcf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_COMPLEX;
    mcf->enabled_complex = complex_value;

    /*
     * Request variable evaluation always wins (tier 1).
     * The block bit is NOT set for complex values — tier 1 overrides
     * all other tiers including dynconf.
     */

    return NGX_CONF_OK;
}

/*
 * Configuration directive handler: markdown_error_policy
 * (pass | fail_closed | status <code>).
 *
 * Config V2 (0.9.0): unified error policy consolidating the removed legacy
 * on-error and streaming-on-error directives.  The single
 * on_error field is the runtime source of truth for every conversion path:
 *   pass        -> on_error=PASS
 *   fail_closed -> on_error=REJECT, error_status=502
 *   status <c>  -> on_error=REJECT, error_status=<c>
 *
 * Allowed status codes: 429, 503 (502 rejected; use fail_closed instead).
 */
static char *
ngx_http_markdown_error_policy(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char             pass_str[]   = "pass";
    static u_char             closed_str[] = "fail_closed";
    static u_char             status_str[] = "status";
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    if (mcf == NULL || cmd == NULL
        || !ngx_http_markdown_conf_args_ready(cf, 2))
    {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;

    if (mcf->on_error != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (cf->args->nelts == 2
        && ngx_http_markdown_arg_equals(&value[1], pass_str,
                                        sizeof(pass_str) - 1))
    {
        mcf->on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    } else if (cf->args->nelts == 2
               && ngx_http_markdown_arg_equals(&value[1], closed_str,
                                               sizeof(closed_str) - 1))
    {
        mcf->on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
        mcf->error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;

    } else if (cf->args->nelts == 3
               && ngx_http_markdown_arg_equals(&value[1], status_str,
                                               sizeof(status_str) - 1))
    {
        ngx_uint_t code;

        code = ngx_http_markdown_parse_uint(&value[2]);
        if (code == 502) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                "invalid status code \"502\" in \"%V\" directive, "
                "please use \"fail_closed\" instead",
                &cmd->name);
            return NGX_CONF_ERROR;
        }
        if (code != 429 && code != 503) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                "invalid status code \"%V\" in \"%V\" directive, "
                "it must be 429 or 503",
                &value[2], &cmd->name);
            return NGX_CONF_ERROR;
        }
        mcf->on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
        mcf->error_status = code;

    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid value in \"%V\" directive, it must be "
            "\"pass\", \"fail_closed\", or \"status <429|503>\"",
            &cmd->name);
        return NGX_CONF_ERROR;
    }

    return NGX_CONF_OK;
}

/* Configuration directive handler: markdown_auth_policy (allow | deny). */
static char *
ngx_http_markdown_auth_policy(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char             allow_str[] = "allow";
    static u_char             deny_str[]  = "deny";
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    if (mcf == NULL || cmd == NULL
        || !ngx_http_markdown_conf_args_ready(cf, 2))
    {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;

    if (mcf->policy.auth_policy != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(&value[1], allow_str,
                                     sizeof(allow_str) - 1))
    {
        mcf->policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], deny_str,
                   sizeof(deny_str) - 1))
    {
        mcf->policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "invalid value \"%V\" in \"%V\" directive, "
                           "it must be \"allow\" or \"deny\"",
                           &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }

    return NGX_CONF_OK;
}

/* Configuration directive handler: markdown_auth_cookies <pattern ...>. */
static char *
ngx_http_markdown_auth_cookies(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    const ngx_str_t          *value;
    ngx_str_t                *pattern;

    if (mcf == NULL || !ngx_http_markdown_conf_args_ready(cf, 2))
    {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;

    if (mcf->policy.auth_cookies != NGX_CONF_UNSET_PTR) {
        return "is duplicate";
    }

    mcf->policy.auth_cookies = ngx_array_create(cf->pool, cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mcf->policy.auth_cookies == NULL) {
        return NGX_CONF_ERROR;
    }

    for (ngx_uint_t i = 1; i < cf->args->nelts; i++) {
        if (value[i].len == 0) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "empty cookie pattern in \"%V\" directive",
                               &cmd->name);
            return NGX_CONF_ERROR;
        }

        pattern = ngx_array_push(mcf->policy.auth_cookies);
        if (pattern == NULL) {
            return NGX_CONF_ERROR;
        }

        *pattern = value[i];

        ngx_conf_log_error(NGX_LOG_DEBUG, cf, 0,
                           "markdown: added pattern \"%V\"",
                           pattern);
    }

    return NGX_CONF_OK;
}

/* Configuration directive handler: markdown_cache_validation. */
static char *
ngx_http_markdown_cache_validation(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char             off_str[]  = "off";
    static u_char             ims_str[]  = "ims_only";
    static u_char             full_str[] = "full";
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    if (mcf == NULL || cmd == NULL
        || !ngx_http_markdown_conf_args_ready(cf, 2))
    {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;

    /*
     * markdown_cache_validation (Config V2, 0.9.0) consolidates the removed
     * legacy etag and conditional-request directives.  It writes
     * both backing fields (policy.generate_etag, policy.conditional_requests),
     * which remain the runtime source of truth for the conversion and
     * conditional-request paths.
     *
     *   off      - no ETag, no conditional request handling
     *   ims_only - no ETag, If-Modified-Since only
     *   full     - generate transformed ETag, If-None-Match + If-Modified-Since
     */
    if (mcf->policy.conditional_requests != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(&value[1], off_str,
                                     sizeof(off_str) - 1))
    {
        mcf->policy.generate_etag = 0;
        mcf->policy.conditional_requests =
            NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], ims_str,
                   sizeof(ims_str) - 1))
    {
        mcf->policy.generate_etag = 0;
        mcf->policy.conditional_requests =
            NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], full_str,
                   sizeof(full_str) - 1))
    {
        mcf->policy.generate_etag = 1;
        mcf->policy.conditional_requests =
            NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "invalid value \"%V\" in \"%V\" directive, "
                           "it must be \"off\", \"ims_only\", or \"full\"",
                           &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }

    return NGX_CONF_OK;
}

/* Configuration directive handler: markdown_streaming off|auto|force. */
static char *
ngx_http_markdown_streaming(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char             off_str[]   = "off";
    static u_char             auto_str[]  = "auto";
    static u_char             force_str[] = "force";
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    if (mcf == NULL || !ngx_http_markdown_conf_args_ready(cf, 2))
    {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;

    /*
     * markdown_streaming (Config V2, 0.9.0) is the sole processing-path
     * selector. policy_explicit records that an operator set this directive
     * so the cache-validation conflict check in merge_conf does not fire for
     * default configurations.
     *
     *   off   - never stream
     *   auto  - stream large responses, full-buffer small ones
     *   force - always stream (subject to runtime hard blocks)
     */
    if (mcf->stream.policy != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(&value[1], off_str,
                                     sizeof(off_str) - 1))
    {
        mcf->stream.policy = NGX_HTTP_MARKDOWN_STREAMING_OFF;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], auto_str,
                   sizeof(auto_str) - 1))
    {
        mcf->stream.policy = NGX_HTTP_MARKDOWN_STREAMING_AUTO;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], force_str,
                   sizeof(force_str) - 1))
    {
        mcf->stream.policy = NGX_HTTP_MARKDOWN_STREAMING_FORCE;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "invalid value \"%V\" in \"%V\" directive, "
                           "it must be \"off\", \"auto\", or \"force\"",
                           &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }

    mcf->stream.policy_explicit = 1;

    return NGX_CONF_OK;
}

/* Configuration directive handler: markdown_log_verbosity. */
static char *
ngx_http_markdown_log_verbosity(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char             err_str[]   = "error";
    static u_char             warn_str[]  = "warn";
    static u_char             info_str[]  = "info";
    static u_char             debug_str[] = "debug";
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    if (mcf == NULL || !ngx_http_markdown_conf_args_ready(cf, 2))
    {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;

    if (mcf->policy.log_verbosity != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(&value[1], err_str,
                                     sizeof(err_str) - 1))
    {
        mcf->policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], warn_str,
                   sizeof(warn_str) - 1))
    {
        mcf->policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], info_str,
                   sizeof(info_str) - 1))
    {
        mcf->policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], debug_str,
                   sizeof(debug_str) - 1))
    {
        mcf->policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "invalid value \"%V\" in \"%V\" directive, "
                           "it must be \"error\", \"warn\", \"info\", or \"debug\"",
                           &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }

    return NGX_CONF_OK;
}


/*
 * Parse markdown_content_types directive.
 *
 * Accepts one or more content type/subtype strings, validates
 * format (must contain exactly one '/'), and stores in the
 * content_types array.
 *
 * Parameters:
 *   cf  - Configuration parsing context
 *   cmd - Directive definition
 *   conf - Module location configuration
 *
 * Returns:
 *   NGX_CONF_OK on success
 *   NGX_CONF_ERROR on allocation or validation failure
 *   "is duplicate" if directive already set
 */
static char *
ngx_http_markdown_content_types(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    ngx_str_t                *type;
    u_char                   *slash;
    const u_char             *next_slash;

    if (mcf == NULL || cmd == NULL
        || !ngx_http_markdown_conf_args_ready(cf, 2))
    {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;

    if (mcf->routing.content_types != NGX_CONF_UNSET_PTR) {
        return "is duplicate";
    }

    mcf->routing.content_types = ngx_array_create(cf->pool, cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mcf->routing.content_types == NULL) {
        return NGX_CONF_ERROR;
    }

    for (ngx_uint_t i = 1; i < cf->args->nelts; i++) {
        if (value[i].len == 0) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "empty content type in \"%V\" directive",
                               &cmd->name);
            return NGX_CONF_ERROR;
        }

        slash = ngx_strlchr(value[i].data,
                            value[i].data + value[i].len, '/');
        next_slash = NULL;

        if (slash != NULL && (size_t) ((slash - value[i].data) + 1) < value[i].len) {
            next_slash = ngx_strlchr(slash + 1,
                                     value[i].data + value[i].len, '/');
        }

        if (slash == NULL
            || slash == value[i].data
            || (size_t) (slash - value[i].data) == value[i].len - 1
            || next_slash != NULL)
        {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "invalid content type \"%V\" in \"%V\" directive, "
                               "must be in format \"type/subtype\"",
                               &value[i], &cmd->name);
            return NGX_CONF_ERROR;
        }

        type = ngx_array_push(mcf->routing.content_types);
        if (type == NULL) {
            return NGX_CONF_ERROR;
        }

        *type = value[i];

        ngx_conf_log_error(NGX_LOG_DEBUG, cf, 0,
                           "markdown: added type \"%V\"",
                           type);
    }

    return NGX_CONF_OK;
}

/*
 * Handle the "markdown_flavor" configuration directive.
 *
 * Accepts "commonmark" or "gfm" and sets the flavor enum.  The former
 * experimental MDX and Org-mode selectors are rejected because they never
 * had distinct conversion semantics.
 *
 * Parameters:
 *   cf  - Configuration parsing context
 *   cmd - Directive metadata
 *   conf - Module configuration
 *
 * Returns:
 *   NGX_CONF_OK on success
 *   NGX_CONF_ERROR on invalid value
 *   "is duplicate" if already set
 */
static char *
ngx_http_markdown_flavor(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char             cm_str[]  = "commonmark";
    static u_char             gfm_str[] = "gfm";
    static u_char             mdx_str[] = "mdx";
    static u_char             org_str[] = "org-mode";
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    if (mcf == NULL || cmd == NULL
        || !ngx_http_markdown_conf_args_ready(cf, 2))
    {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;

    if (mcf->flavor != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(&value[1], cm_str,
                                     sizeof(cm_str) - 1))
    {
        mcf->flavor = NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], gfm_str,
                   sizeof(gfm_str) - 1))
    {
        mcf->flavor = NGX_HTTP_MARKDOWN_FLAVOR_GFM;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], mdx_str, sizeof(mdx_str) - 1)
               || ngx_http_markdown_arg_equals(
                   &value[1], org_str, sizeof(org_str) - 1))
    {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"markdown_flavor %V\" is no longer supported because it "
            "never had distinct conversion semantics; use "
            "\"markdown_flavor commonmark\" or \"markdown_flavor gfm\"",
            &value[1]);
        return NGX_CONF_ERROR;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "invalid value \"%V\" in \"%V\" directive, "
                           "it must be \"commonmark\" or \"gfm\"",
                           &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }

    return NGX_CONF_OK;
}

/**
 * Register the markdown_metrics content handler for the current location.
 *
 * @param cf The configuration parsing context.
 * @param cmd The directive being processed.
 * @param conf Module configuration pointer (unused).
 * @returns `NGX_CONF_OK` on success, `NGX_CONF_ERROR` if the core location configuration cannot be obtained.
 */
static char *
ngx_http_markdown_metrics_directive(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_http_core_loc_conf_t *clcf;

    clcf = ngx_http_conf_get_module_loc_conf(cf, ngx_http_core_module);
    if (clcf == NULL) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "failed to get core location configuration for \"%V\" directive",
                           &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (mcf != NULL && mcf->ops.metrics_enabled != NGX_CONF_UNSET) {
        return "is duplicate";
    }

    if (clcf->handler != NULL) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "\"%V\" cannot be combined with another content handler "
                           "in the same location",
                           &cmd->name);
        return NGX_CONF_ERROR;
    }

    if (mcf != NULL) {
        mcf->ops.metrics_enabled = 1;
    }
    clcf->handler = ngx_http_markdown_metrics_handler;

    ngx_conf_log_error(NGX_LOG_INFO, cf, 0,
                       "markdown: endpoint enabled at this location");

    return NGX_CONF_OK;
}


/**
 * Register the markdown_diagnostics content handler for the current location.
 *
 * @param cf The configuration parsing context.
 * @param cmd The directive being processed.
 * @param conf Module configuration pointer.
 * @returns `NGX_CONF_OK` on success, `NGX_CONF_ERROR` on failure.
 */
static char *
ngx_http_markdown_diagnostics_directive(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char               on_str[]  = "on";
    static u_char               off_str[] = "off";
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_http_core_loc_conf_t *clcf;
    const ngx_str_t          *value;

    (void) cmd;

    if (mcf == NULL || !ngx_http_markdown_conf_args_ready(cf, 2)) {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;

    if (ngx_http_markdown_arg_equals(&value[1],
                                      on_str, sizeof(on_str) - 1))
    {
        mcf->ops.diagnostics_enabled = 1;

        clcf = ngx_http_conf_get_module_loc_conf(cf, ngx_http_core_module);
        if (clcf == NULL) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "failed to get core location configuration for \"%V\" directive",
                               &cmd->name);
            return NGX_CONF_ERROR;
        }

        if (clcf->handler != NULL) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "\"%V\" cannot be combined with another content handler "
                               "in the same location",
                               &cmd->name);
            return NGX_CONF_ERROR;
        }

        clcf->handler = ngx_http_markdown_diagnostics_handler;

        /*
         * Enabling the endpoint at any location also activates the
         * per-worker recent-decisions ring (allocated in init_worker).
         */
        ngx_http_markdown_diagnostics_enable_recording();

        ngx_conf_log_error(NGX_LOG_INFO, cf, 0,
                           "markdown: diagnostics endpoint enabled at this location");
    } else if (ngx_http_markdown_arg_equals(&value[1],
                                             off_str, sizeof(off_str) - 1))
    {
        mcf->ops.diagnostics_enabled = 0;
    } else {
        return "invalid value";
    }

    return NGX_CONF_OK;
}


/**
 * Custom directive handler for markdown_dynamic_config_path.
 *
 * Sets the dynconf_path field, then checks whether another location
 * has already configured a path.  If so, returns NGX_CONF_ERROR
 * to reject the configuration immediately — before nginx -t or
 * worker startup — preventing ambiguous multi-location dynconf.
 *
 * Dynconf supports only a single global instance; the operator may
 * place the directive at http, server, or location level, but only
 * one configuration object may own the global watcher.
 *
 * Duplicate detection reads from ngx_http_markdown_main_conf_t
 * (config-parse scope) rather than a file-scope static, so the flag
 * is reset correctly on reload.
 *
 * @param cf    Configuration context.
 * @param cmd   Directive definition.
 * @param conf  Target configuration struct (ngx_http_markdown_conf_t).
 * @return NGX_CONF_OK on success, NGX_CONF_ERROR on duplicate.
 */
static char *
ngx_http_markdown_set_dynconf_path(ngx_conf_t *cf, ngx_command_t *cmd,
    void *conf)
{
    ngx_str_t                      *value;
    ngx_http_markdown_conf_t       *mcf;
    ngx_http_markdown_main_conf_t  *mmcf;

    (void) cmd;
    (void) conf;

    /* H-only dynconf directives still own the location snapshot consumed by
     * workers.  Resolve that loc_conf explicitly instead of treating the
     * command's main-conf offset as a location-config pointer. */
    if (cf == NULL || !ngx_http_markdown_conf_args_ready(cf, 2)) {
        return NGX_CONF_ERROR;
    }

    mcf = ngx_http_conf_get_module_loc_conf(
        cf, ngx_http_markdown_filter_module);

    if (mcf == NULL) {
        return NGX_CONF_ERROR;
    }

    /* Let NGINX set the string slot first */
    value = cf->args->elts;

    if (cf->args->nelts < 2) {
        return NGX_CONF_ERROR;
    }

    if (value[1].len == 0) {
        return NGX_CONF_OK;
    }

    mmcf = ngx_http_conf_get_module_main_conf(
        cf, ngx_http_markdown_filter_module);
    if (mmcf == NULL) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "markdown: failed to get main conf");
        return NGX_CONF_ERROR;
    }

    if (mmcf->dynconf_path_configured) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "markdown: duplicate configuration; "
            "dynconf supports only a single global instance. "
            "First path: \"%V\", this path: \"%V\". "
            "Place the directive at http/server level or in only one location",
            &mmcf->dynconf_first_path, &value[1]);
        return NGX_CONF_ERROR;
    }

    mcf->advanced.dynconf_path = value[1];
    mmcf->dynconf_path_configured = 1;
    mmcf->dynconf_first_path = value[1];
    mmcf->dynconf_owner_conf = mcf;

    return NGX_CONF_OK;
}


/* Store an H-only dynconf flag in the default location configuration. */
static char *
ngx_http_markdown_dynconf_flag(ngx_conf_t *cf,
    ngx_command_t *cmd, /* NOSONAR: c:S995; NGINX callback signature
                          * requires mutable command pointer */
    void *conf)
{
    ngx_http_markdown_conf_t  *mcf;
    const ngx_str_t           *value;
    ngx_flag_t                *slot;

    (void) conf;
    if (cf == NULL || !ngx_http_markdown_conf_args_ready(cf, 2)) {
        return NGX_CONF_ERROR;
    }

    mcf = ngx_http_conf_get_module_loc_conf(
        cf, ngx_http_markdown_filter_module);
    if (mcf == NULL || cf->args->nelts != 2)
    {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;
    if (cmd == NULL || cmd->name.data == NULL) {
        return NGX_CONF_ERROR;
    }
    if (ngx_http_markdown_arg_equals(
            &cmd->name,
            (const u_char *) NGX_HTTP_MARKDOWN_DIRECTIVE_DYNAMIC_CONFIG,
            sizeof(NGX_HTTP_MARKDOWN_DIRECTIVE_DYNAMIC_CONFIG) - 1))
    {
        slot = &mcf->advanced.dynconf_enabled;
    } else if (ngx_http_markdown_arg_equals(
                   &cmd->name,
                   (const u_char *) NGX_HTTP_MARKDOWN_DIRECTIVE_DYNCONF_DRY_RUN,
                   sizeof(NGX_HTTP_MARKDOWN_DIRECTIVE_DYNCONF_DRY_RUN) - 1))
    {
        slot = &mcf->advanced.dynconf_dry_run;
    } else {
        return NGX_CONF_ERROR;
    }
    if (*slot != NGX_CONF_UNSET) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(
            &value[1], (const u_char *) "on", sizeof("on") - 1))
    {
        *slot = 1;
        return NGX_CONF_OK;
    }
    if (ngx_http_markdown_arg_equals(
            &value[1], (const u_char *) "off", sizeof("off") - 1))
    {
        *slot = 0;
        return NGX_CONF_OK;
    }

    return "is not a valid flag";
}

/*
 * Configuration directive handler: markdown_stream_excluded_types (v0.8.0)
 *
 * Parses a space-separated list of MIME types and stores them in
 * conf->stream.excluded_types array.  Each type must be in
 * "type/subtype" format.
 *
 * Parameters:
 *   cf   - configuration context
 *   cmd  - directive definition
 *   conf - location configuration pointer
 *
 * Returns:
 *   NGX_CONF_OK on success, NGX_CONF_ERROR on allocation or
 *   validation failure
 */
static char *
ngx_http_markdown_stream_excluded_types_handler(ngx_conf_t *cf,
    ngx_command_t *cmd, void *conf) /* NOSONAR: NGINX callback signature */
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    ngx_str_t                *type;
    u_char                   *slash;
    const u_char             *next_slash;

    (void) cmd;

    if (mcf == NULL || !ngx_http_markdown_conf_args_ready(cf, 2)) {
        return NGX_CONF_ERROR;
    }

    value = cf->args->elts;

    if (mcf->stream.excluded_types != NGX_CONF_UNSET_PTR) {
        return "is duplicate";
    }

    mcf->stream.excluded_types = ngx_array_create(cf->pool,
        cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mcf->stream.excluded_types == NULL) {
        return NGX_CONF_ERROR;
    }

    for (ngx_uint_t i = 1; i < cf->args->nelts; i++) {
        if (value[i].len == 0) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                "empty content type in "
                "\"markdown_stream_excluded_types\" "
                "directive");
            return NGX_CONF_ERROR;
        }

        slash = ngx_strlchr(value[i].data,
            value[i].data + value[i].len, '/');
        next_slash = NULL;

        if (slash != NULL
            && (size_t) ((slash - value[i].data) + 1)
               < value[i].len)
        {
            next_slash = ngx_strlchr(slash + 1,
                value[i].data + value[i].len, '/');
        }

        if (slash == NULL
            || slash == value[i].data
            || (size_t) (slash - value[i].data)
               == value[i].len - 1
            || next_slash != NULL)
        {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                "invalid content type \"%V\" in "
                "\"markdown_stream_excluded_types\" "
                "directive, must be in format "
                "\"type/subtype\"",
                &value[i]);
            return NGX_CONF_ERROR;
        }

        type = ngx_array_push(mcf->stream.excluded_types);
        if (type == NULL) {
            return NGX_CONF_ERROR;
        }

        *type = value[i];
    }

    return NGX_CONF_OK;
}
#endif /* NGX_HTTP_MARKDOWN_CONFIG_HANDLERS_IMPL_H */
