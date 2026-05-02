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

static ngx_int_t
ngx_http_markdown_arg_equals(
    const ngx_str_t *arg,
    u_char *expected,
    size_t expected_len)
{
    if (arg == NULL || arg->data == NULL) {
        return 0;
    }

    if (arg->len != expected_len) {
        return 0;
    }

    return ngx_strncasecmp(arg->data,
                           expected,
                           expected_len) == 0;
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
    char                *endptr;
    size_t               len;
    size_t               value;
    size_t               scale;
    unsigned long long   raw;
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

    suffix = '\0';
    if (len > 1) {
        switch (buf[len - 1]) {
        case 'k':
        case 'K':
        case 'm':
        case 'M':
        case 'g':
        case 'G':
            suffix = buf[len - 1];
            buf[len - 1] = '\0';
            break;
        default:
            break;
        }
    }

    errno = 0;
    raw = strtoull(buf, &endptr, 10);
    if (errno == ERANGE || *endptr != '\0'
        || raw > (unsigned long long) NGX_MAX_SIZE_T_VALUE)
    {
        return (size_t) NGX_ERROR;
    }

    value = (size_t) raw;

    switch (suffix) {
    case 'k':
    case 'K':
        scale = (size_t) 1024;
        break;
    case 'm':
    case 'M':
        scale = (size_t) 1024 * 1024;
        break;
    case 'g':
    case 'G':
        scale = (size_t) 1024 * 1024 * 1024;
        break;
    default:
        return value;
    }

    if (value > NGX_MAX_SIZE_T_VALUE / scale) {
        return (size_t) NGX_ERROR;
    }

    return value * scale;
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
    ngx_command_t *cmd, /* NOSONAR: nginx directive callback signature */
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

    if (mcf->enabled_source != NGX_HTTP_MARKDOWN_ENABLED_UNSET) {
        return "is duplicate";
    }

    value = cf->args->elts;

    if (value[1].len == 2
        && ngx_strcasecmp(value[1].data, on_str) == 0)
    {
        mcf->enabled = 1;
        mcf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
        mcf->enabled_complex = NULL;
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
    const ngx_str_t          *value;
    ngx_str_t                *type;
    const char               *type_value;
    const char               *slash;
    const char               *next_slash;

    value = cf->args->elts;

    if (mcf->content_types != NGX_CONF_UNSET_PTR) {
        return "is duplicate";
    }

    mcf->content_types = ngx_array_create(cf->pool, cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mcf->content_types == NULL) {
        return NGX_CONF_ERROR;
    }

    for (ngx_uint_t i = 1; i < cf->args->nelts; i++) {
        if (value[i].len == 0) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "empty content type in \"%V\" directive",
                               &cmd->name);
            return NGX_CONF_ERROR;
        }

        type_value = (const char *) value[i].data;
        slash = (const char *) ngx_strchr(type_value, '/');
        next_slash = NULL;

        if (slash != NULL && (size_t) (slash - type_value + 1) < value[i].len) {
            next_slash = (const char *) ngx_strchr(slash + 1, '/');
        }

        if (slash == NULL
            || slash == type_value
            || (size_t) (slash - type_value) == value[i].len - 1
            || next_slash != NULL)
        {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "invalid content type \"%V\" in \"%V\" directive, "
                               "must be in format \"type/subtype\"",
                               &value[i], &cmd->name);
            return NGX_CONF_ERROR;
        }

        type = ngx_array_push(mcf->content_types);
        if (type == NULL) {
            return NGX_CONF_ERROR;
        }

        *type = value[i];

        ngx_conf_log_error(NGX_LOG_DEBUG, cf, 0,
                           "markdown_content_types: added type \"%V\"",
                           type);
    }

    return NGX_CONF_OK;
}

/*
 * Handle the "markdown_flavor" configuration directive.
 *
 * Accepts "commonmark", "gfm", or "mdx" and sets the flavor enum.
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
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

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
                   &value[1], mdx_str,
                   sizeof(mdx_str) - 1))
    {
        mcf->flavor = NGX_HTTP_MARKDOWN_FLAVOR_MDX;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "invalid value \"%V\" in \"%V\" directive, "
                           "it must be \"commonmark\", \"gfm\", or \"mdx\"",
                           &value[1], &cmd->name);
        return NGX_CONF_ERROR;
    }

    return NGX_CONF_OK;
}

/**
 * Handle the "markdown_stream_types" configuration directive by validating
 * and storing one or more MIME type strings in the form "type/subtype".
 *
 * Validates each argument is non-empty, contains exactly one '/' separator,
 * and has non-empty type and subtype segments. On success the provided values
 * are appended to the module's `stream_types` array in the configuration.
 *
 * @param cf Configuration parsing context.
 * @param cmd Directive metadata.
 * @param conf Pointer to the module configuration (ngx_http_markdown_conf_t).
 *
 * @return NGX_CONF_OK on success.
 * @return NGX_CONF_ERROR if an allocation fails or any argument is empty or
 *         malformed.
 * @return "is duplicate" if the directive has already been specified.
 */
static char *
ngx_http_markdown_stream_types(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    const ngx_str_t          *value;
    ngx_str_t                *type;
    const char               *type_value;
    const char               *slash;
    const char               *next_slash;

    value = cf->args->elts;

    if (mcf->stream_types != NGX_CONF_UNSET_PTR) {
        return "is duplicate";
    }

    mcf->stream_types = ngx_array_create(cf->pool, cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mcf->stream_types == NULL) {
        return NGX_CONF_ERROR;
    }

    for (ngx_uint_t i = 1; i < cf->args->nelts; i++) {
        if (value[i].len == 0) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "empty content type in \"%V\" directive",
                               &cmd->name);
            return NGX_CONF_ERROR;
        }

        type_value = (const char *) value[i].data;
        slash = (const char *) ngx_strchr(type_value, '/');
        next_slash = NULL;

        if (slash != NULL && (size_t) (slash - type_value + 1) < value[i].len) {
            next_slash = (const char *) ngx_strchr(slash + 1, '/');
        }

        if (slash == NULL
            || slash == type_value
            || (size_t) (slash - type_value) == value[i].len - 1
            || next_slash != NULL)
        {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "invalid content type \"%V\" in \"%V\" directive, "
                               "must be in format \"type/subtype\"",
                               &value[i], &cmd->name);
            return NGX_CONF_ERROR;
        }

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

/**
 * Parse and set the markdown_large_body_threshold directive.
 *
 * Processes a single argument which must be either "off" or a byte size
 * optionally suffixed with "k" or "m". Sets the module configuration's
 * large_body_threshold to 0 for "off" (or an explicit "0"), or to the parsed
 * byte size for valid nonzero sizes.
 *
 * @param cf Configuration parsing context.
 * @param cmd Directive definition.
 * @param conf Pointer to the module location configuration (ngx_http_markdown_conf_t *).
 * @returns NGX_CONF_OK on success;
 *          NGX_CONF_ERROR if the argument is not "off" and cannot be parsed as a size (error logged);
 *          the string "is duplicate" if the directive was already set.
 */
static char *
ngx_http_markdown_large_body_threshold(ngx_conf_t *cf,
    ngx_command_t *cmd, /* NOSONAR: nginx directive callback signature */
    void *conf)
{
    static u_char             off_str[] = "off";
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    (void) cmd;

    value = cf->args->elts;

    if (mcf->large_body_threshold != NGX_CONF_UNSET_SIZE) {
        return "is duplicate";
    }

    if (value[1].len == 3
        && ngx_strcasecmp(value[1].data, off_str) == 0)
    {
        mcf->large_body_threshold = 0;
        return NGX_CONF_OK;
    }

    mcf->large_body_threshold = ngx_http_markdown_parse_size(&value[1]);
    if (mcf->large_body_threshold == (size_t) NGX_ERROR) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid value \"%V\" in "
            "\"markdown_large_body_threshold\" "
            "directive, it must be \"off\" "
            "or a size (e.g., 512k, 1m)",
            &value[1]);
        return NGX_CONF_ERROR;
    }

    /* Explicit "0" is treated as off and already normalized above. */
    return NGX_CONF_OK;
}

/* Configuration directive handler: markdown_metrics_format (auto | prometheus). */
static char *
ngx_http_markdown_metrics_format(ngx_conf_t *cf,
    ngx_command_t *cmd, void *conf)
{
    static u_char              auto_str[] = "auto";
    static u_char              prom_str[] = "prometheus";
    ngx_http_markdown_conf_t  *mcf = conf;
    ngx_str_t                 *value;

    value = cf->args->elts;

    if (mcf->ops.metrics_format != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(&value[1], auto_str,
                                     sizeof(auto_str) - 1))
    {
        mcf->ops.metrics_format =
            NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], prom_str,
                   sizeof(prom_str) - 1))
    {
        mcf->ops.metrics_format =
            NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS;
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "invalid value \"%V\" in \"%V\" "
            "directive, it must be "
            "\"auto\" or \"prometheus\"",
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
    ngx_http_core_loc_conf_t *clcf;

    (void) conf;

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

    clcf->handler = ngx_http_markdown_metrics_handler;

    ngx_conf_log_error(NGX_LOG_INFO, cf, 0,
                       "markdown_metrics: endpoint enabled at this location");

    return NGX_CONF_OK;
}

#ifdef MARKDOWN_STREAMING_ENABLED
/*
 * Configuration directive handler: markdown_streaming_engine
 *
 * Supported values:
 * - off | on | auto   (static configuration)
 * - $variable          (dynamic per-request switch)
 *
 * Uses ngx_http_set_complex_value_slot pattern: static
 * values are compiled as constant complex values so the
 * engine selector can evaluate them uniformly at runtime.
 */
static char *
ngx_http_markdown_streaming_engine(ngx_conf_t *cf,
    ngx_command_t *cmd, void *conf)
{
    static u_char                      on_str[]   = "on";
    static u_char                      off_str[]  = "off";
    static u_char                      auto_str[] = "auto";
    ngx_http_markdown_conf_t          *mcf = conf;
    ngx_http_compile_complex_value_t   ccv;
    ngx_http_complex_value_t          *cv;
    ngx_str_t                         *value;
    u_char                            *var_marker;

    if (mcf->streaming_engine != NULL) {
        return "is duplicate";
    }

    value = cf->args->elts;

    /* Check for variable marker '$' in the value */
    var_marker = ngx_strlchr(value[1].data,
                             value[1].data + value[1].len,
                             '$');

    if (var_marker == NULL) {
        /* Static value: must be "off", "on", or "auto" */
        if (!ngx_http_markdown_arg_equals(
                &value[1], off_str,
                sizeof(off_str) - 1)
            && !ngx_http_markdown_arg_equals(
                &value[1], on_str,
                sizeof(on_str) - 1)
            && !ngx_http_markdown_arg_equals(
                &value[1], auto_str,
                sizeof(auto_str) - 1))
        {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                "invalid value \"%V\" in \"%V\" "
                "directive, it must be \"off\", "
                "\"on\", \"auto\", or contain "
                "a variable",
                &value[1], &cmd->name);
            return NGX_CONF_ERROR;
        }
    }

    cv = ngx_palloc(cf->pool,
                    sizeof(ngx_http_complex_value_t));
    if (cv == NULL) {
        return NGX_CONF_ERROR;
    }

    ngx_memzero(&ccv,
                sizeof(ngx_http_compile_complex_value_t));
    ccv.cf = cf;
    ccv.value = &value[1];
    ccv.complex_value = cv;

    if (ngx_http_compile_complex_value(&ccv) != NGX_OK) {
        return NGX_CONF_ERROR;
    }

    mcf->streaming_engine = cv;
    return NGX_CONF_OK;
}
#endif /* MARKDOWN_STREAMING_ENABLED */

#endif /* NGX_HTTP_MARKDOWN_CONFIG_HANDLERS_IMPL_H */
