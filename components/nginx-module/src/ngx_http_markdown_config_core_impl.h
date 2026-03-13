/*
 * Configuration-core helpers.
 *
 * This unit owns configuration object lifecycle, shared-metrics-zone
 * bootstrap, runtime markdown_filter resolution, and config logging/name
 * helpers used outside directive parsing.
 */

static ngx_int_t
ngx_http_markdown_init_metrics_zone(ngx_shm_zone_t *shm_zone, void *data)
{
    ngx_slab_pool_t              *shpool;
    ngx_http_markdown_metrics_t  *metrics;

    if (data != NULL) {
        shm_zone->data = data;
        return NGX_OK;
    }

    shpool = (ngx_slab_pool_t *) shm_zone->shm.addr;
    if (shpool == NULL) {
        return NGX_ERROR;
    }

    if (shm_zone->shm.exists) {
        shm_zone->data = shpool->data;
        return (shm_zone->data != NULL) ? NGX_OK : NGX_ERROR;
    }

    metrics = ngx_slab_alloc(shpool, sizeof(ngx_http_markdown_metrics_t));
    if (metrics == NULL) {
        return NGX_ERROR;
    }

    ngx_memzero(metrics, sizeof(ngx_http_markdown_metrics_t));
    shpool->data = metrics;
    shm_zone->data = metrics;

    return NGX_OK;
}

static void *
ngx_http_markdown_create_main_conf(ngx_conf_t *cf)
{
    ngx_http_markdown_main_conf_t *conf;
    ngx_shm_zone_t                *zone;

    conf = ngx_pcalloc(cf->pool, sizeof(ngx_http_markdown_main_conf_t));
    if (conf == NULL) {
        return NULL;
    }

    zone = ngx_shared_memory_add(
        cf,
        &ngx_http_markdown_metrics_shm_name,
        (size_t) (8 * ngx_pagesize),
        &ngx_http_markdown_filter_module
    );
    if (zone == NULL) {
        return NULL;
    }

    zone->init = ngx_http_markdown_init_metrics_zone;
    conf->metrics_shm_zone = zone;
    ngx_http_markdown_metrics_shm_zone = zone;

    return conf;
}

/*
 * Create location configuration
 *
 * Allocates and initializes configuration structure with default values.
 * All values are set to NGX_CONF_UNSET* to enable proper inheritance.
 */
static void *
ngx_http_markdown_create_conf(ngx_conf_t *cf)
{
    ngx_http_markdown_conf_t *conf;

    conf = ngx_pcalloc(cf->pool, sizeof(ngx_http_markdown_conf_t));
    if (conf == NULL) {
        return NULL;
    }

    /* Set unset values (NGX_CONF_UNSET*) for proper inheritance */
    conf->enabled = NGX_CONF_UNSET;
    conf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
    conf->enabled_complex = NULL;
    conf->max_size = NGX_CONF_UNSET_SIZE;
    conf->timeout = NGX_CONF_UNSET_MSEC;
    conf->on_error = NGX_CONF_UNSET_UINT;
    conf->flavor = NGX_CONF_UNSET_UINT;
    conf->token_estimate = NGX_CONF_UNSET;
    conf->front_matter = NGX_CONF_UNSET;
    conf->on_wildcard = NGX_CONF_UNSET;
    conf->auth_policy = NGX_CONF_UNSET_UINT;
    conf->auth_cookies = NGX_CONF_UNSET_PTR;
    conf->generate_etag = NGX_CONF_UNSET;
    conf->conditional_requests = NGX_CONF_UNSET_UINT;
    conf->log_verbosity = NGX_CONF_UNSET_UINT;
    conf->buffer_chunked = NGX_CONF_UNSET;
    conf->stream_types = NGX_CONF_UNSET_PTR;
    conf->auto_decompress = NGX_CONF_UNSET;

    return conf;
}

/*
 * Merge location configuration
 *
 * Implements configuration inheritance: location > server > http.
 * Child configuration values override parent values if set.
 * Unset child values inherit from parent.
 */
static char *
ngx_http_markdown_merge_conf(ngx_conf_t *cf, void *parent, void *child)
{
    ngx_http_markdown_conf_t *prev = parent;
    ngx_http_markdown_conf_t *conf = child;

    /*
     * Merge markdown_filter with explicit source tracking.
     *
     * Priority:
     * 1. Child explicit value (on/off or complex expression)
     * 2. Parent inherited value
     * 3. Default off
     */
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
    } else if (conf->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_STATIC) {
        conf->enabled_complex = NULL;
    }

    ngx_conf_merge_size_value(conf->max_size, prev->max_size, 10 * 1024 * 1024);
    ngx_conf_merge_msec_value(conf->timeout, prev->timeout, 5000);
    ngx_conf_merge_uint_value(conf->on_error, prev->on_error,
                              NGX_HTTP_MARKDOWN_ON_ERROR_PASS);
    ngx_conf_merge_uint_value(conf->flavor, prev->flavor,
                              NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK);
    ngx_conf_merge_value(conf->token_estimate, prev->token_estimate, 0);
    ngx_conf_merge_value(conf->front_matter, prev->front_matter, 0);
    ngx_conf_merge_value(conf->on_wildcard, prev->on_wildcard, 0);
    ngx_conf_merge_uint_value(conf->auth_policy, prev->auth_policy,
                              NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW);
    ngx_conf_merge_value(conf->generate_etag, prev->generate_etag, 1);
    ngx_conf_merge_uint_value(conf->conditional_requests, prev->conditional_requests,
                              NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT);
    ngx_conf_merge_uint_value(conf->log_verbosity, prev->log_verbosity,
                              NGX_HTTP_MARKDOWN_LOG_INFO);
    ngx_conf_merge_value(conf->buffer_chunked, prev->buffer_chunked, 1);
    ngx_conf_merge_value(conf->auto_decompress, prev->auto_decompress, 1);

    ngx_conf_merge_ptr_value(conf->auth_cookies, prev->auth_cookies, NULL);
    ngx_conf_merge_ptr_value(conf->stream_types, prev->stream_types, NULL);

    ngx_http_markdown_log_merged_conf(cf, conf);

    return NGX_CONF_OK;
}

static ngx_uint_t
ngx_http_markdown_log_verbosity_to_ngx_level(ngx_uint_t verbosity)
{
    switch (verbosity) {
        case NGX_HTTP_MARKDOWN_LOG_ERROR:
            return NGX_LOG_ERR;
        case NGX_HTTP_MARKDOWN_LOG_WARN:
            return NGX_LOG_WARN;
        case NGX_HTTP_MARKDOWN_LOG_DEBUG:
            return NGX_LOG_DEBUG;
        case NGX_HTTP_MARKDOWN_LOG_INFO:
        default:
            return NGX_LOG_INFO;
    }
}

static const ngx_str_t *
ngx_http_markdown_on_error_name(ngx_uint_t value)
{
    static ngx_str_t pass = ngx_string("pass");
    static ngx_str_t reject = ngx_string("reject");
    static ngx_str_t unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_ON_ERROR_PASS:
            return &pass;
        case NGX_HTTP_MARKDOWN_ON_ERROR_REJECT:
            return &reject;
        default:
            return &unknown;
    }
}

static const ngx_str_t *
ngx_http_markdown_flavor_name(ngx_uint_t value)
{
    static ngx_str_t commonmark = ngx_string("commonmark");
    static ngx_str_t gfm = ngx_string("gfm");
    static ngx_str_t unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK:
            return &commonmark;
        case NGX_HTTP_MARKDOWN_FLAVOR_GFM:
            return &gfm;
        default:
            return &unknown;
    }
}

static const ngx_str_t *
ngx_http_markdown_auth_policy_name(ngx_uint_t value)
{
    static ngx_str_t allow = ngx_string("allow");
    static ngx_str_t deny = ngx_string("deny");
    static ngx_str_t unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW:
            return &allow;
        case NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY:
            return &deny;
        default:
            return &unknown;
    }
}

static const ngx_str_t *
ngx_http_markdown_conditional_requests_name(ngx_uint_t value)
{
    static ngx_str_t full_support = ngx_string("full_support");
    static ngx_str_t if_modified_since_only = ngx_string("if_modified_since_only");
    static ngx_str_t disabled = ngx_string("disabled");
    static ngx_str_t unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT:
            return &full_support;
        case NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE:
            return &if_modified_since_only;
        case NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED:
            return &disabled;
        default:
            return &unknown;
    }
}

static const ngx_str_t *
ngx_http_markdown_log_verbosity_name(ngx_uint_t value)
{
    static ngx_str_t error = ngx_string("error");
    static ngx_str_t warn = ngx_string("warn");
    static ngx_str_t info = ngx_string("info");
    static ngx_str_t debug = ngx_string("debug");
    static ngx_str_t unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_LOG_ERROR:
            return &error;
        case NGX_HTTP_MARKDOWN_LOG_WARN:
            return &warn;
        case NGX_HTTP_MARKDOWN_LOG_INFO:
            return &info;
        case NGX_HTTP_MARKDOWN_LOG_DEBUG:
            return &debug;
        default:
            return &unknown;
    }
}

static const ngx_str_t *
ngx_http_markdown_compression_name(ngx_http_markdown_compression_type_e compression_type)
{
    static ngx_str_t gzip = ngx_string("gzip");
    static ngx_str_t deflate = ngx_string("deflate");
    static ngx_str_t brotli = ngx_string("brotli");
    static ngx_str_t unknown = ngx_string("unknown");
    static ngx_str_t invalid = ngx_string("invalid");

    switch (compression_type) {
        case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
            return &gzip;
        case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
            return &deflate;
        case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
            return &brotli;
        case NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN:
            return &unknown;
        default:
            return &invalid;
    }
}

static const ngx_str_t *
ngx_http_markdown_enabled_source_name(ngx_uint_t value)
{
    static ngx_str_t unset = ngx_string("unset");
    static ngx_str_t static_value = ngx_string("static");
    static ngx_str_t complex = ngx_string("complex");
    static ngx_str_t unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_ENABLED_UNSET:
            return &unset;
        case NGX_HTTP_MARKDOWN_ENABLED_STATIC:
            return &static_value;
        case NGX_HTTP_MARKDOWN_ENABLED_COMPLEX:
            return &complex;
        default:
            return &unknown;
    }
}

static ngx_uint_t
ngx_http_markdown_is_ascii_space(u_char ch)
{
    return (ch == ' ' || ch == '\t' || ch == '\r'
            || ch == '\n' || ch == '\f' || ch == '\v');
}

static u_char ngx_http_markdown_flag_on[] = "on";
static u_char ngx_http_markdown_flag_off[] = "off";
static u_char ngx_http_markdown_flag_yes[] = "yes";
static u_char ngx_http_markdown_flag_no[] = "no";
static u_char ngx_http_markdown_flag_true[] = "true";
static u_char ngx_http_markdown_flag_false[] = "false";

static ngx_int_t
ngx_http_markdown_parse_filter_flag(ngx_str_t *value, ngx_flag_t *enabled)
{
    ngx_str_t  normalized;
    u_char    *start;
    u_char    *end;

    if (value == NULL || enabled == NULL) {
        return NGX_ERROR;
    }

    start = value->data;
    end = value->data + value->len;

    while (start < end && ngx_http_markdown_is_ascii_space(*start)) {
        start++;
    }

    while (end > start && ngx_http_markdown_is_ascii_space(*(end - 1))) {
        end--;
    }

    normalized.data = start;
    normalized.len = (size_t) (end - start);
    value = &normalized;

    if (value->len == 0) {
        *enabled = 0;
        return NGX_OK;
    }

    if (value->len == 1) {
        if (value->data[0] == '1') {
            *enabled = 1;
            return NGX_OK;
        }

        if (value->data[0] == '0') {
            *enabled = 0;
            return NGX_OK;
        }
    }

    if (value->len == 2
        && ngx_strncasecmp(value->data, ngx_http_markdown_flag_on, 2) == 0)
    {
        *enabled = 1;
        return NGX_OK;
    }

    if (value->len == 3
        && ngx_strncasecmp(value->data, ngx_http_markdown_flag_off, 3) == 0)
    {
        *enabled = 0;
        return NGX_OK;
    }

    if (value->len == 3
        && ngx_strncasecmp(value->data, ngx_http_markdown_flag_yes, 3) == 0)
    {
        *enabled = 1;
        return NGX_OK;
    }

    if (value->len == 2
        && ngx_strncasecmp(value->data, ngx_http_markdown_flag_no, 2) == 0)
    {
        *enabled = 0;
        return NGX_OK;
    }

    if (value->len == 4
        && ngx_strncasecmp(value->data, ngx_http_markdown_flag_true, 4) == 0)
    {
        *enabled = 1;
        return NGX_OK;
    }

    if (value->len == 5
        && ngx_strncasecmp(value->data, ngx_http_markdown_flag_false, 5) == 0)
    {
        *enabled = 0;
        return NGX_OK;
    }

    return NGX_ERROR;
}

ngx_flag_t
ngx_http_markdown_is_enabled(ngx_http_request_t *r, ngx_http_markdown_conf_t *conf)
{
    ngx_str_t    evaluated;
    ngx_flag_t   enabled;
    ngx_int_t    rc;

    if (conf == NULL) {
        return 0;
    }

    if (conf->enabled_source != NGX_HTTP_MARKDOWN_ENABLED_COMPLEX
        || conf->enabled_complex == NULL)
    {
        return conf->enabled;
    }

    if (r == NULL) {
        return 0;
    }

    if (ngx_http_complex_value(r, conf->enabled_complex, &evaluated) != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown filter: failed to evaluate markdown_filter variable");
        return 0;
    }

    rc = ngx_http_markdown_parse_filter_flag(&evaluated, &enabled);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown filter: markdown_filter variable resolved to invalid value "
                      "\"%V\", treating as off", &evaluated);
        return 0;
    }

    return enabled;
}

static void
ngx_http_markdown_log_merged_conf(ngx_conf_t *cf, ngx_http_markdown_conf_t *conf)
{
    ngx_uint_t log_level;
    ngx_uint_t auth_cookie_count = (conf->auth_cookies != NULL) ? conf->auth_cookies->nelts : 0;
    ngx_uint_t stream_type_count = (conf->stream_types != NULL) ? conf->stream_types->nelts : 0;

    if (cf == NULL) {
        return;
    }

    log_level = ngx_http_markdown_log_verbosity_to_ngx_level(conf->log_verbosity);

    ngx_conf_log_error(log_level, cf, 0,
                       "markdown filter config: enabled=%ui enabled_source=%V max_size=%uz timeout_ms=%M "
                       "on_error=%V flavor=%V token_estimate=%ui front_matter=%ui "
                       "on_wildcard=%ui auth_policy=%V auth_cookie_patterns=%ui "
                       "etag=%ui conditional_requests=%V log_verbosity=%V "
                       "buffer_chunked=%ui stream_types=%ui",
                       (ngx_uint_t) conf->enabled,
                       ngx_http_markdown_enabled_source_name(conf->enabled_source),
                       conf->max_size,
                       conf->timeout,
                       ngx_http_markdown_on_error_name(conf->on_error),
                       ngx_http_markdown_flavor_name(conf->flavor),
                       (ngx_uint_t) conf->token_estimate,
                       (ngx_uint_t) conf->front_matter,
                       (ngx_uint_t) conf->on_wildcard,
                       ngx_http_markdown_auth_policy_name(conf->auth_policy),
                       auth_cookie_count,
                       (ngx_uint_t) conf->generate_etag,
                       ngx_http_markdown_conditional_requests_name(conf->conditional_requests),
                       ngx_http_markdown_log_verbosity_name(conf->log_verbosity),
                       (ngx_uint_t) conf->buffer_chunked,
                       stream_type_count);
}
