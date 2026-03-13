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
    ngx_http_markdown_conf_t          *mcf = conf;
    ngx_http_compile_complex_value_t   ccv;
    ngx_http_complex_value_t          *complex_value;
    ngx_str_t                         *value;
    u_char                            *var_marker;

    (void) cmd;

    if (mcf->enabled_source != NGX_HTTP_MARKDOWN_ENABLED_UNSET) {
        return "is duplicate";
    }

    value = cf->args->elts;

    if (value[1].len == 2
        && ngx_strcasecmp(value[1].data, (u_char *) "on") == 0)
    {
        mcf->enabled = 1;
        mcf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
        mcf->enabled_complex = NULL;
        return NGX_CONF_OK;
    }

    if (value[1].len == 3
        && ngx_strcasecmp(value[1].data, (u_char *) "off") == 0)
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

static char *
ngx_http_markdown_on_error(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    value = cf->args->elts;

    if (mcf->on_error != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_strcasecmp(value[1].data, (u_char *) "pass") == 0) {
        mcf->on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    } else if (ngx_strcasecmp(value[1].data, (u_char *) "reject") == 0) {
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

static char *
ngx_http_markdown_flavor(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    value = cf->args->elts;

    if (mcf->flavor != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_strcasecmp(value[1].data, (u_char *) "commonmark") == 0) {
        mcf->flavor = NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK;
    } else if (ngx_strcasecmp(value[1].data, (u_char *) "gfm") == 0) {
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

static char *
ngx_http_markdown_auth_policy(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    value = cf->args->elts;

    if (mcf->auth_policy != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_strcasecmp(value[1].data, (u_char *) "allow") == 0) {
        mcf->auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW;
    } else if (ngx_strcasecmp(value[1].data, (u_char *) "deny") == 0) {
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

static char *
ngx_http_markdown_auth_cookies(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    ngx_str_t                *pattern;
    ngx_uint_t                i;

    value = cf->args->elts;

    if (mcf->auth_cookies != NGX_CONF_UNSET_PTR) {
        return "is duplicate";
    }

    mcf->auth_cookies = ngx_array_create(cf->pool, cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mcf->auth_cookies == NULL) {
        return NGX_CONF_ERROR;
    }

    for (i = 1; i < cf->args->nelts; i++) {
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

static char *
ngx_http_markdown_conditional_requests(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    value = cf->args->elts;

    if (mcf->conditional_requests != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_strcasecmp(value[1].data, (u_char *) "full_support") == 0) {
        mcf->conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    } else if (ngx_strcasecmp(value[1].data, (u_char *) "if_modified_since_only") == 0) {
        mcf->conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;
    } else if (ngx_strcasecmp(value[1].data, (u_char *) "disabled") == 0) {
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

static char *
ngx_http_markdown_log_verbosity(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;

    value = cf->args->elts;

    if (mcf->log_verbosity != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    if (ngx_strcasecmp(value[1].data, (u_char *) "error") == 0) {
        mcf->log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    } else if (ngx_strcasecmp(value[1].data, (u_char *) "warn") == 0) {
        mcf->log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
    } else if (ngx_strcasecmp(value[1].data, (u_char *) "info") == 0) {
        mcf->log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    } else if (ngx_strcasecmp(value[1].data, (u_char *) "debug") == 0) {
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

static char *
ngx_http_markdown_stream_types(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    ngx_http_markdown_conf_t *mcf = conf;
    ngx_str_t                *value;
    ngx_str_t                *type;
    ngx_uint_t                i;

    value = cf->args->elts;

    if (mcf->stream_types != NGX_CONF_UNSET_PTR) {
        return "is duplicate";
    }

    mcf->stream_types = ngx_array_create(cf->pool, cf->args->nelts - 1, sizeof(ngx_str_t));
    if (mcf->stream_types == NULL) {
        return NGX_CONF_ERROR;
    }

    for (i = 1; i < cf->args->nelts; i++) {
        if (value[i].len == 0) {
            ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
                               "empty content type in \"%V\" directive",
                               &cmd->name);
            return NGX_CONF_ERROR;
        }

        if (ngx_strchr(value[i].data, '/') == NULL) {
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

    clcf->handler = ngx_http_markdown_metrics_handler;

    ngx_conf_log_error(NGX_LOG_INFO, cf, 0,
                       "markdown_metrics: endpoint enabled at this location");

    return NGX_CONF_OK;
}
