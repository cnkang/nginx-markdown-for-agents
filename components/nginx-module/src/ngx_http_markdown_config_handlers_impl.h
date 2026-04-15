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
 * Configuration directive handler: markdown_filter
 *
 * Supported values:
 * - on | off          (static configuration)
 * - $variable         (dynamic per-request switch)
 * - complex value containing variables
 */
static char *
ngx_http_markdown_filter(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
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

    if (value[1].len == 3
        && ngx_strcasecmp(value[1].data, off_str) == 0)
    {
        mcf->enabled = 0;
        mcf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
        mcf->enabled_complex = NULL;
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

    return NGX_CONF_OK;
}

/* Configuration directive handler: markdown_on_error (pass | reject). */
static char *
ngx_http_markdown_on_error(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char             pass_str[]   = "pass";
    static u_char             reject_str[] = "reject";
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    value = cf->args->elts;

    if (mcf->on_error != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(&value[1], pass_str,
                                     sizeof(pass_str) - 1))
    {
        mcf->on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], reject_str,
                   sizeof(reject_str) - 1))
    {
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

/* Configuration directive handler: markdown_flavor (commonmark | gfm). */
static char *
ngx_http_markdown_flavor(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char             cm_str[]  = "commonmark";
    static u_char             gfm_str[] = "gfm";
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
    } else {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                           "invalid value \"%V\" in \"%V\" directive, "
                           "it must be \"commonmark\" or \"gfm\"",
                           &value[1], &cmd->name);
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

    value = cf->args->elts;

    if (mcf->auth_policy != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(&value[1], allow_str,
                                     sizeof(allow_str) - 1))
    {
        mcf->auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], deny_str,
                   sizeof(deny_str) - 1))
    {
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

/* Configuration directive handler: markdown_auth_cookies <pattern ...>. */
static char *
ngx_http_markdown_auth_cookies(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    const ngx_str_t          *value;
    ngx_str_t                *pattern;

    value = cf->args->elts;

    if (mcf->auth_cookies != NGX_CONF_UNSET_PTR) {
        return "is duplicate";
    }

    mcf->auth_cookies = ngx_array_create(cf->pool, cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mcf->auth_cookies == NULL) {
        return NGX_CONF_ERROR;
    }

    for (ngx_uint_t i = 1; i < cf->args->nelts; i++) {
        if (value[i].len == 0) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "empty cookie pattern in \"%V\" directive",
                               &cmd->name);
            return NGX_CONF_ERROR;
        }

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

/* Configuration directive handler: markdown_conditional_requests. */
static char *
ngx_http_markdown_conditional_requests(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    static u_char             full_str[] = "full_support";
    static u_char             ims_str[]  =
        "if_modified_since_only";
    static u_char             dis_str[]  = "disabled";
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    value = cf->args->elts;

    if (mcf->conditional_requests != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(&value[1], full_str,
                                     sizeof(full_str) - 1))
    {
        mcf->conditional_requests =
            NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], ims_str,
                   sizeof(ims_str) - 1))
    {
        mcf->conditional_requests =
            NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], dis_str,
                   sizeof(dis_str) - 1))
    {
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

    value = cf->args->elts;

    if (mcf->log_verbosity != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_http_markdown_arg_equals(&value[1], err_str,
                                     sizeof(err_str) - 1))
    {
        mcf->log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], warn_str,
                   sizeof(warn_str) - 1))
    {
        mcf->log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], info_str,
                   sizeof(info_str) - 1))
    {
        mcf->log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    } else if (ngx_http_markdown_arg_equals(
                   &value[1], debug_str,
                   sizeof(debug_str) - 1))
    {
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
    ngx_command_t *cmd, void *conf)
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

    mcf->large_body_threshold = ngx_parse_size(&value[1]);
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
