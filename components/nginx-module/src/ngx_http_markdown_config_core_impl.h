#ifndef NGX_HTTP_MARKDOWN_CONFIG_CORE_IMPL_H
#define NGX_HTTP_MARKDOWN_CONFIG_CORE_IMPL_H

/*
 * Configuration-core helpers.
 *
 * WARNING: This header is an implementation detail of the main translation unit
 * (ngx_http_markdown_filter_module.c). It must NOT be included from any other
 * .c file or used as a standalone compilation unit.
 *
 * This unit owns configuration object lifecycle, shared-metrics-zone
 * bootstrap, runtime markdown_filter resolution, and config logging/name
 * helpers used outside directive parsing.
 */

/* C99 declaration visibility for standalone static analysis of this impl header. */
ngx_int_t ngx_strncasecmp(u_char *s1, u_char *s2, size_t n);
ngx_int_t ngx_http_complex_value(ngx_http_request_t *r,
    ngx_http_complex_value_t *val, ngx_str_t *value);
void ngx_conf_log_error(ngx_uint_t level, ngx_conf_t *cf,
    ngx_err_t err, const char *fmt, ...);

/* Helper declared early because merge logic uses it before its definition. */
static void ngx_http_markdown_log_merged_conf(ngx_conf_t *cf,
    const ngx_http_markdown_conf_t *conf);

/*
 * Choose the RB-tree branch direction for a node vs an existing tree node.
 *
 * Compares by rbtree key (hash) first, then by path_len and path bytes
 * to resolve hash collisions.  Returns &temp->left or &temp->right.
 */
static ngx_rbtree_node_t **
ngx_http_markdown_path_rbtree_choose_branch(ngx_rbtree_node_t *temp,
    const ngx_rbtree_node_t *node)
{
    const ngx_http_markdown_path_metric_node_t  *n;
    const ngx_http_markdown_path_metric_node_t  *t;

    if (node->key < temp->key) {
        return &temp->left;
    }

    if (node->key > temp->key) {
        return &temp->right;
    }

    n = (const ngx_http_markdown_path_metric_node_t *) node;
    t = (const ngx_http_markdown_path_metric_node_t *) temp;

    if (n->path_len < t->path_len) {
        return &temp->left;
    }

    if (n->path_len > t->path_len) {
        return &temp->right;
    }

    if (ngx_memcmp(n->path, t->path, n->path_len) < 0) {
        return &temp->left;
    }

    return &temp->right;
}

/*
 * RB-tree insert callback for per-path metric nodes.
 *
 * Compares by rbnode.key (hash) first, then by path_len
 * and path bytes to resolve hash collisions.
 */
static void
ngx_http_markdown_path_rbtree_insert_value(ngx_rbtree_node_t *temp,
    ngx_rbtree_node_t *node, ngx_rbtree_node_t *sentinel)
{
    ngx_rbtree_node_t  **p;

    for ( ;; ) {
        p = ngx_http_markdown_path_rbtree_choose_branch(temp, node);

        if (*p == sentinel) {
            break;
        }

        temp = *p;
    }

    *p = node;
    node->parent = temp;
    node->left = sentinel;
    node->right = sentinel;
    ngx_rbt_red(node);
}

/*
 * Default per-path cardinality limit.
 *
 * Caps the number of distinct URI paths tracked in the shared
 * RB-tree to prevent unbounded memory growth in the slab pool.
 * Configurable via markdown_metrics_per_path_cardinality (future).
 */
#define NGX_HTTP_MARKDOWN_PER_PATH_CARDINALITY_DEFAULT  100

/*
 * Shared-memory initializer for cross-worker metrics storage.
 *
 * On reload, nginx may pass previous zone data (`data != NULL`), which is
 * reattached instead of allocating a fresh counter block.  The SHM zone
 * name is versioned (v5) so an incompatible layout after hot reload
 * allocates a fresh slab instead of reattaching stale data.
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

    ngx_rbtree_init(&metrics->per_path.path_tree,
                    &metrics->per_path.sentinel,
                    ngx_http_markdown_path_rbtree_insert_value);
    metrics->per_path.cardinality_limit =
        NGX_HTTP_MARKDOWN_PER_PATH_CARDINALITY_DEFAULT;

    shpool->data = metrics;
    shm_zone->data = metrics;

    return NGX_OK;
}

/*
 * Allocate and zero-initialize the main-level configuration structure.
 *
 * Called once during configuration parsing to create the process-wide
 * shared state for metrics SHM zone settings and dynconf duplicate
 * detection.
 *
 * Parameters:
 *   cf - NGINX configuration context (provides the memory pool)
 *
 * Returns:
 *   pointer to the allocated ngx_http_markdown_main_conf_t, or NULL on failure
 */
static void *
ngx_http_markdown_create_main_conf(ngx_conf_t *cf)
{
    ngx_http_markdown_main_conf_t *conf;

    conf = ngx_pcalloc(cf->pool, sizeof(ngx_http_markdown_main_conf_t));
    if (conf == NULL) {
        return NULL;
    }

    conf->metrics_shm_size = NGX_CONF_UNSET_SIZE;
    conf->metrics_shm_zone = NULL;
    conf->dynconf_path_configured = 0;
    conf->dynconf_first_path.data = NULL;
    conf->dynconf_first_path.len = 0;
    conf->metrics_per_path_cardinality = NGX_CONF_UNSET_UINT;

    return conf;
}

/*
 * Finalize main-level defaults and register the shared-memory zone.
 *
 * Sets the default metrics SHM size (8 pages) if not explicitly configured,
 * registers the shared memory zone with ngx_shared_memory_add(), and stores
 * the zone pointer in both the main conf and the module-global variable.
 *
 * Parameters:
 *   cf   - NGINX configuration context
 *   conf - pointer to ngx_http_markdown_main_conf_t
 *
 * Returns:
 *   NGX_CONF_OK on success, NGX_CONF_ERROR on failure
 */
static char *
ngx_http_markdown_init_main_conf(ngx_conf_t *cf, void *conf)
{
    ngx_http_markdown_main_conf_t *mcf = conf;
    ngx_shm_zone_t                *zone;

    /*
     * Default to 8 pages so the shared slab has enough room for the metrics
     * struct plus allocator metadata without oversizing small deployments.
     */
    ngx_conf_init_size_value(mcf->metrics_shm_size, 8 * ngx_pagesize);
    ngx_conf_init_uint_value(mcf->metrics_per_path_cardinality,
                             NGX_HTTP_MARKDOWN_PER_PATH_CARDINALITY_DEFAULT);

    zone = ngx_shared_memory_add(
        cf,
        &ngx_http_markdown_metrics_shm_name,
        mcf->metrics_shm_size,
        &ngx_http_markdown_filter_module
    );
    if (zone == NULL) {
        return NGX_CONF_ERROR;
    }

    zone->init = ngx_http_markdown_init_metrics_zone;
    mcf->metrics_shm_zone = zone;
    ngx_http_markdown_metrics_shm_zone = zone;

    return NGX_CONF_OK;
}

/**
 * Create and initialize a per-location Markdown filter configuration structure.
 *
 * Allocates a ngx_http_markdown_conf_t and initializes its fields to NGX_CONF_UNSET* or NULL
 * so merge logic can distinguish unspecified values from explicit settings.
 * @param cf configuration context providing the memory pool for allocation.
 * @returns Pointer to the initialized ngx_http_markdown_conf_t, or NULL if allocation fails.
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
    conf->max_size_explicit = 0;
    conf->timeout = NGX_CONF_UNSET_MSEC;
    conf->on_error = NGX_CONF_UNSET_UINT;
    conf->flavor = NGX_CONF_UNSET_UINT;
    conf->token_estimate = NGX_CONF_UNSET;
    conf->front_matter = NGX_CONF_UNSET;
    conf->on_wildcard = NGX_CONF_UNSET;
    conf->policy.auth_policy = NGX_CONF_UNSET_UINT;
    conf->policy.auth_cookies = NGX_CONF_UNSET_PTR;
    conf->policy.generate_etag = NGX_CONF_UNSET;
    conf->policy.conditional_requests = NGX_CONF_UNSET_UINT;
    conf->policy.log_verbosity = NGX_CONF_UNSET_UINT;
    conf->buffer_chunked = NGX_CONF_UNSET;
    conf->stream_types = NGX_CONF_UNSET_PTR;
    conf->content_types = NGX_CONF_UNSET_PTR;
    conf->auto_decompress = NGX_CONF_UNSET;
    conf->decompress_max_size = NGX_CONF_UNSET_SIZE;
    conf->parse_timeout = NGX_CONF_UNSET_MSEC;
    conf->parser_budget = NGX_CONF_UNSET_SIZE;
    conf->large_body_threshold = NGX_CONF_UNSET_SIZE;
    conf->ops.trust_forwarded_headers = NGX_CONF_UNSET;
    conf->ops.metrics_format = NGX_CONF_UNSET_UINT;
    conf->ops.metrics_per_path = NGX_CONF_UNSET;
    conf->ops.diagnostics_enabled = NGX_CONF_UNSET;
    conf->ops.diagnostics_allow = NULL;
    conf->ops.otel_enabled = NGX_CONF_UNSET;
    conf->ops.otel_tracing = NGX_CONF_UNSET;
    conf->ops.otel_metrics = NGX_CONF_UNSET;
    conf->ops.otel_endpoint.len = 0;
    conf->ops.otel_endpoint.data = NULL;
    conf->ops.otel_service_name.len = 0;
    conf->ops.otel_service_name.data = NULL;
    conf->ops.otel_span_buffer_size = NGX_CONF_UNSET_UINT;
    conf->ops.otel_export_timeout = NGX_CONF_UNSET_MSEC;

#ifdef MARKDOWN_STREAMING_ENABLED
    conf->streaming.engine = NULL;
    conf->streaming.budget = NGX_CONF_UNSET_SIZE;
    conf->streaming.budget_explicit = 0;
    conf->streaming.on_error = NGX_CONF_UNSET_UINT;
    conf->streaming.shadow = NGX_CONF_UNSET;
    conf->streaming.auto_threshold = NGX_CONF_UNSET_SIZE;
#endif

    conf->advanced.prune_noise = NGX_CONF_UNSET;
    conf->advanced.prune_selectors = NGX_CONF_UNSET_PTR;
    conf->advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
    conf->advanced.memory_budget = NGX_CONF_UNSET_SIZE;
    conf->advanced.llm_provider = NGX_CONF_UNSET_UINT;
    conf->advanced.chars_per_token_fixed = NGX_CONF_UNSET_UINT;
    conf->advanced.dynconf_enabled = NGX_CONF_UNSET;
    conf->advanced.dynconf_path.len = 0;
    conf->advanced.dynconf_path.data = NULL;
    conf->advanced.dynconf_dry_run = NGX_CONF_UNSET;

    return conf;
}

/*
 * Merge the enabled/source/complex triple from parent into child.
 *
 * Priority: child explicit > parent inherited > default off.
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

/*
 * Merge an ngx_str_t field from parent into child when child is empty.
 *
 * If the child string has zero length and the parent string is non-empty,
 * copies the parent value into the child.
 */
static void
ngx_http_markdown_merge_str_if_unset(ngx_str_t *child, const ngx_str_t *parent)
{
    if (child->len == 0 && parent->len > 0) {
        *child = *parent;
    }
}

/*
 * Apply the unified memory_budget → max_size override.
 *
 * If memory_budget is set and max_size was not explicitly configured
 * at this or any parent level, max_size takes the memory_budget value.
 */
static void
ngx_http_markdown_apply_memory_budget_override(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev, ngx_flag_t max_size_set)
{
    conf->max_size_explicit = max_size_set || prev->max_size_explicit;

    if (conf->advanced.memory_budget != NGX_CONF_UNSET_SIZE
        && !conf->max_size_explicit)
    {
        conf->max_size = conf->advanced.memory_budget;
    }
}

/*
 * Merge base conversion/runtime options and operational flags.
 */
static void
ngx_http_markdown_merge_core_base_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_conf_merge_size_value(conf->max_size, prev->max_size, 10 * 1024 * 1024);
    ngx_conf_merge_msec_value(conf->timeout, prev->timeout, 5000);
    ngx_conf_merge_uint_value(conf->on_error, prev->on_error,
                              NGX_HTTP_MARKDOWN_ON_ERROR_PASS);
    ngx_conf_merge_uint_value(conf->flavor, prev->flavor,
                              NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK);
    ngx_conf_merge_value(conf->token_estimate, prev->token_estimate, 0);
    ngx_conf_merge_value(conf->front_matter, prev->front_matter, 0);
    ngx_conf_merge_value(conf->on_wildcard, prev->on_wildcard, 0);
    ngx_conf_merge_uint_value(conf->policy.auth_policy, prev->policy.auth_policy,
                              NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW);
    ngx_conf_merge_value(conf->policy.generate_etag, prev->policy.generate_etag, 1);
    ngx_conf_merge_uint_value(conf->policy.conditional_requests, prev->policy.conditional_requests,
                              NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT);
    ngx_conf_merge_uint_value(conf->policy.log_verbosity, prev->policy.log_verbosity,
                              NGX_HTTP_MARKDOWN_LOG_INFO);
    ngx_conf_merge_value(conf->buffer_chunked, prev->buffer_chunked, 1);
    ngx_conf_merge_value(conf->auto_decompress, prev->auto_decompress, 1);

    /*
     * Merge decompress_max_size: inherit from parent if not explicitly set.
     * After merge, if still NGX_CONF_UNSET_SIZE, resolve to max_size at
     * post-merge time (ngx_http_markdown_apply_decompress_max_size_default)
     * so the default tracks max_size even when max_size comes from
     * memory_budget override.
     */
    ngx_conf_merge_size_value(conf->decompress_max_size,
                              prev->decompress_max_size,
                              NGX_CONF_UNSET_SIZE);

    ngx_conf_merge_msec_value(conf->parse_timeout, prev->parse_timeout, 30000);
    ngx_conf_merge_size_value(conf->parser_budget, prev->parser_budget,
                              64 * 1024 * 1024);
}

/*
 * Merge operational telemetry/metrics values.
 */
static void
ngx_http_markdown_merge_core_ops_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_conf_merge_value(conf->ops.trust_forwarded_headers,
                         prev->ops.trust_forwarded_headers, 0);
    ngx_conf_merge_uint_value(conf->ops.metrics_format, prev->ops.metrics_format,
                              NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO);
    ngx_conf_merge_value(conf->ops.metrics_per_path, prev->ops.metrics_per_path, 0);
    ngx_conf_merge_value(conf->ops.diagnostics_enabled,
                         prev->ops.diagnostics_enabled, 0);

    if (conf->ops.diagnostics_allow == NULL) {
        conf->ops.diagnostics_allow = prev->ops.diagnostics_allow;
    }

    ngx_conf_merge_value(conf->ops.otel_enabled, prev->ops.otel_enabled, 0);
    ngx_conf_merge_value(conf->ops.otel_tracing, prev->ops.otel_tracing, 0);
    ngx_conf_merge_value(conf->ops.otel_metrics, prev->ops.otel_metrics, 0);

    if (conf->ops.otel_tracing || conf->ops.otel_metrics) {
        conf->ops.otel_enabled = 1;
    }

    ngx_http_markdown_merge_str_if_unset(&conf->ops.otel_endpoint,
                                         &prev->ops.otel_endpoint);
    ngx_http_markdown_merge_str_if_unset(&conf->ops.otel_service_name,
                                         &prev->ops.otel_service_name);
    ngx_conf_merge_uint_value(conf->ops.otel_span_buffer_size,
                              prev->ops.otel_span_buffer_size, 1024);
    ngx_conf_merge_msec_value(conf->ops.otel_export_timeout,
                              prev->ops.otel_export_timeout, 5000);
}

/*
 * Merge remaining core pointer/threshold values.
 */
static void
ngx_http_markdown_merge_core_ptr_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_conf_merge_size_value(conf->large_body_threshold,
                              prev->large_body_threshold,
                              NGX_HTTP_MARKDOWN_THRESHOLD_OFF);
    ngx_conf_merge_ptr_value(conf->policy.auth_cookies, prev->policy.auth_cookies, NULL);
    ngx_conf_merge_ptr_value(conf->stream_types, prev->stream_types, NULL);
    ngx_conf_merge_ptr_value(conf->content_types, prev->content_types, NULL);
}

/*
 * Merge base conversion/runtime options and operational flags.
 */
static void
ngx_http_markdown_merge_core_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_http_markdown_merge_core_base_values(conf, prev);
    ngx_http_markdown_merge_core_ops_values(conf, prev);
    ngx_http_markdown_merge_core_ptr_values(conf, prev);
}

#ifdef MARKDOWN_STREAMING_ENABLED
/*
 * Merge streaming-only options.
 */
static void
ngx_http_markdown_merge_streaming_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev, ngx_flag_t streaming_budget_set)
{
    if (conf->streaming.engine == NULL) {
        conf->streaming.engine = prev->streaming.engine;
    }
    ngx_conf_merge_size_value(conf->streaming.budget,
                              prev->streaming.budget,
                              NGX_HTTP_MARKDOWN_STREAMING_BUDGET_DEFAULT);
    conf->streaming.budget_explicit = streaming_budget_set
        || prev->streaming.budget_explicit;
    ngx_conf_merge_uint_value(conf->streaming.on_error,
                              prev->streaming.on_error,
                              NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS);
    ngx_conf_merge_value(conf->streaming.shadow, prev->streaming.shadow, 0);
    ngx_conf_merge_size_value(conf->streaming.auto_threshold,
                              prev->streaming.auto_threshold,
                              NGX_HTTP_MARKDOWN_STREAMING_AUTO_THRESHOLD_DEFAULT);
}
#endif

/*
 * Merge v0.6.0-specific configuration surfaces.
 */
static void
ngx_http_markdown_merge_v060_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_conf_merge_value(conf->advanced.prune_noise, prev->advanced.prune_noise, 1);
    ngx_conf_merge_ptr_value(conf->advanced.prune_selectors, prev->advanced.prune_selectors, NULL);
    ngx_conf_merge_ptr_value(conf->advanced.prune_protection_selectors,
                             prev->advanced.prune_protection_selectors, NULL);
    ngx_conf_merge_size_value(conf->advanced.memory_budget,
                              prev->advanced.memory_budget,
                              NGX_CONF_UNSET_SIZE);
    ngx_conf_merge_uint_value(conf->advanced.llm_provider, prev->advanced.llm_provider, 0);
    ngx_conf_merge_uint_value(conf->advanced.chars_per_token_fixed,
                              prev->advanced.chars_per_token_fixed, 0);
    ngx_conf_merge_value(conf->advanced.dynconf_enabled, prev->advanced.dynconf_enabled, 0);
    ngx_http_markdown_merge_str_if_unset(&conf->advanced.dynconf_path, &prev->advanced.dynconf_path);
    ngx_conf_merge_value(conf->advanced.dynconf_dry_run, prev->advanced.dynconf_dry_run, 0);
}

/**
 * Merge per-location markdown filter configuration with inheritance from parent.
 *
 * Performs inheritance and defaults for a child location configuration by
 * applying parent values where the child is unset and enforcing sensible
 * defaults for all configuration fields.
 *
 * @param cf Configuration parsing context used for logging and error reporting.
 * @param parent Pointer to the parent (server/http) ngx_http_markdown_conf_t.
 * @param child Pointer to the child (location) ngx_http_markdown_conf_t to merge into.
 * @return NGX_CONF_OK when merge completes successfully, NGX_CONF_ERROR on failure.
 */
static char *
ngx_http_markdown_merge_conf(ngx_conf_t *cf, void *parent, void *child)
{
    const ngx_http_markdown_conf_t *prev = parent;
    ngx_http_markdown_conf_t *conf = child;

    ngx_http_markdown_merge_enabled(conf, prev);

    /*
     * Save whether max_size and streaming_budget were explicitly set at
     * this configuration level BEFORE ngx_conf_merge_size_value replaces
     * NGX_CONF_UNSET_SIZE with the inherited/default value.  This is
     * needed for the unified memory_budget priority chain below.
     */
    ngx_flag_t  max_size_set = (conf->max_size != NGX_CONF_UNSET_SIZE);
#ifdef MARKDOWN_STREAMING_ENABLED
    ngx_flag_t  streaming_budget_set = (conf->streaming.budget != NGX_CONF_UNSET_SIZE);
#endif

    ngx_http_markdown_merge_core_values(conf, prev);

#ifdef MARKDOWN_STREAMING_ENABLED
    ngx_http_markdown_merge_streaming_values(conf, prev, streaming_budget_set);
#endif

    ngx_http_markdown_merge_v060_values(conf, prev);

    ngx_http_markdown_apply_memory_budget_override(conf, prev, max_size_set);

    /*
     * Resolve decompress_max_size default: if not explicitly set at any
     * level, inherit max_size.  This must run after memory_budget override
     * so the default tracks the effective max_size.
     */
    if (conf->decompress_max_size == NGX_CONF_UNSET_SIZE) {
        conf->decompress_max_size = conf->max_size;
    }

    ngx_http_markdown_log_merged_conf(cf, conf);

    return NGX_CONF_OK;
}

/*
 * Map module log verbosity enum to NGINX native log level.
 *
 * Converts the module-local NGX_HTTP_MARKDOWN_LOG_* constant to the
 * corresponding NGX_LOG_* value used by ngx_log_error().
 *
 * Parameters:
 *   verbosity - module log verbosity constant
 *
 * Returns:
 *   NGX_LOG_ERR, NGX_LOG_WARN, NGX_LOG_INFO, or NGX_LOG_DEBUG
 */
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

/*
 * Return human-readable name for on_error directive value.
 *
 * Parameters:
 *   value - NGX_HTTP_MARKDOWN_ON_ERROR_PASS or _REJECT
 *
 * Returns:
 *   Static ngx_str_t with "pass", "reject", or "unknown"
 */
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

/*
 * Return human-readable name for markdown_flavor directive value.
 *
 * Parameters:
 *   value - flavor constant (COMMONMARK, GFM, MDX, ORG_MODE)
 *
 * Returns:
 *   Static ngx_str_t with the flavor name or "unknown"
 */
static const ngx_str_t *
ngx_http_markdown_flavor_name(ngx_uint_t value)
{
    static ngx_str_t commonmark = ngx_string("commonmark");
    static ngx_str_t gfm = ngx_string("gfm");
    static ngx_str_t mdx = ngx_string("mdx");
    static ngx_str_t org_mode = ngx_string("org-mode");
    static ngx_str_t unknown = ngx_string("unknown");

    switch (value) {
        case NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK:
            return &commonmark;
        case NGX_HTTP_MARKDOWN_FLAVOR_GFM:
            return &gfm;
        case NGX_HTTP_MARKDOWN_FLAVOR_MDX:
            return &mdx;
        case NGX_HTTP_MARKDOWN_FLAVOR_ORG_MODE:
            return &org_mode;
        default:
            return &unknown;
    }
}

/*
 * Return human-readable name for auth_policy directive value.
 *
 * Parameters:
 *   value - NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW or _DENY
 *
 * Returns:
 *   Static ngx_str_t with "allow", "deny", or "unknown"
 */
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

/*
 * Return human-readable name for conditional_requests directive value.
 *
 * Parameters:
 *   value - conditional mode constant (FULL, IMS_ONLY, DISABLED)
 *
 * Returns:
 *   Static ngx_str_t with the mode name or "unknown"
 */
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

/*
 * Return human-readable name for log_verbosity directive value.
 *
 * Parameters:
 *   value - NGX_HTTP_MARKDOWN_LOG_* constant
 *
 * Returns:
 *   Static ngx_str_t with "error", "warn", "info", "debug", or "unknown"
 */
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

/*
 * Return human-readable name for metrics_format directive value.
 *
 * Parameters:
 *   value - NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO or _PROMETHEUS
 *
 * Returns:
 *   Static ngx_str_t with "auto", "prometheus", or "unknown"
 */
static const ngx_str_t *
ngx_http_markdown_metrics_format_name(ngx_uint_t value)
{
    static ngx_str_t auto_fmt = ngx_string("auto");
    static ngx_str_t prometheus = ngx_string("prometheus");
    static ngx_str_t unknown = ngx_string("unknown");

    switch (value) {
    case NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO:
        return &auto_fmt;
    case NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS:
        return &prometheus;
    default:
        return &unknown;
    }
}

/*
 * Return human-readable name for a compression type enum value.
 *
 * Parameters:
 *   compression_type - ngx_http_markdown_compression_type_e value
 *
 * Returns:
 *   Static ngx_str_t with "none", "gzip", "deflate", "brotli",
 *   "unknown", or "invalid"
 */
static const ngx_str_t *
ngx_http_markdown_compression_name(ngx_http_markdown_compression_type_e compression_type)
{
    static ngx_str_t none = ngx_string("none");
    static ngx_str_t gzip = ngx_string("gzip");
    static ngx_str_t deflate = ngx_string("deflate");
    static ngx_str_t brotli = ngx_string("brotli");
    static ngx_str_t unknown = ngx_string("unknown");
    static ngx_str_t invalid = ngx_string("invalid");

    switch (compression_type) {
        case NGX_HTTP_MARKDOWN_COMPRESSION_NONE:
            return &none;
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

/*
 * Return human-readable name for markdown_filter enabled_source value.
 *
 * Parameters:
 *   value - NGX_HTTP_MARKDOWN_ENABLED_UNSET, _STATIC, or _COMPLEX
 *
 * Returns:
 *   Static ngx_str_t with "unset", "static", "complex", or "unknown"
 */
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

/*
 * Check if a byte is an ASCII whitespace character.
 *
 * Recognizes space, tab, carriage return, newline, form feed, and
 * vertical tab.  Used in directive parser hot paths where a fast,
 * dependency-free whitespace check is needed.
 *
 * Parameters:
 *   ch - byte to test
 *
 * Returns:
 *   1 if ch is whitespace, 0 otherwise
 */
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

/* Parse markdown_filter boolean-like token into enabled on/off flag. */
static ngx_int_t
ngx_http_markdown_parse_filter_flag(ngx_str_t *value, ngx_flag_t *enabled)
{
    /* Normalize surrounding ASCII whitespace before token matching. */
    ngx_str_t  normalized;
    u_char    *start;
    const u_char *end;

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

/**
 * Resolve the effective markdown_filter on/off state for the current request.
 *
 * Uses effective_conf to read enabled/enabled_source, ensuring consistency
 * with the request-local snapshot.  When eff is NULL (e.g. pool allocation
 * failure), falls back to live conf values.
 *
 * For NGX_HTTP_MARKDOWN_ENABLED_COMPLEX, evaluates the complex variable
 * at runtime; conf->enabled_complex is not a dynconf-mutable field and
 * is read directly from conf.
 *
 * @param r    The active NGINX request; may be NULL for non-request contexts.
 * @param conf Module location configuration; must be non-NULL for meaningful results.
 * @param eff  Request-local effective configuration view; may be NULL to fall back to live conf.
 * @return 1 if conversion is enabled, 0 otherwise.
 */
ngx_flag_t
ngx_http_markdown_is_enabled(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff)
{
    ngx_str_t    evaluated;
    ngx_flag_t   enabled;
    ngx_int_t    rc;
    ngx_uint_t   effective_source;
    ngx_flag_t   effective_enabled;

    if (conf == NULL) {
        return 0;
    }

    /* Read enabled_source and enabled from effective view when available,
     * falling back to live conf when eff is NULL.  Inline reads here
     * (rather than calling effective_* helpers) to avoid a dependency
     * on dynconf_impl.h from config_core_impl.h. */
    effective_source = (eff != NULL)
        ? eff->enabled_source
        : conf->enabled_source;

    if (effective_source != NGX_HTTP_MARKDOWN_ENABLED_COMPLEX
        || conf->enabled_complex == NULL)
    {
        effective_enabled = (eff != NULL)
            ? eff->enabled
            : conf->enabled;
        return effective_enabled;
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

/**
 * Log the merged markdown filter configuration for a configuration context.
 *
 * Emits a single formatted entry describing the merged `conf` fields to the
 * nginx error log using the log level derived from `conf->policy.log_verbosity`.
 * If `cf` is NULL, the function returns without logging.
 *
 * @param cf   Configuration context used for emitting the log entry.
 * @param conf Merged per-location markdown filter configuration to describe.
 */
static void
ngx_http_markdown_log_merged_conf(ngx_conf_t *cf,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_uint_t log_level;
    ngx_uint_t auth_cookie_count = (conf->policy.auth_cookies != NULL) ? conf->policy.auth_cookies->nelts : 0;
    ngx_uint_t stream_type_count = (conf->stream_types != NULL) ? conf->stream_types->nelts : 0;
    ngx_uint_t content_type_count = (conf->content_types != NULL) ? conf->content_types->nelts : 0;

    if (cf == NULL) {
        return;
    }

    log_level = ngx_http_markdown_log_verbosity_to_ngx_level(conf->policy.log_verbosity);

    ngx_conf_log_error(log_level, cf, 0,
                       "markdown filter config: enabled=%ui "
                       "enabled_source=%V max_size=%uz "
                       "timeout_ms=%M on_error=%V flavor=%V "
                       "token_estimate=%ui front_matter=%ui "
                       "on_wildcard=%ui auth_policy=%V "
                       "auth_cookie_patterns=%ui etag=%ui "
                       "conditional_requests=%V "
                       "log_verbosity=%V buffer_chunked=%ui "
                        "stream_types=%ui "
                        "content_types=%ui "
                       "large_body_threshold=%uz "
                       "trust_forwarded_headers=%ui "
                        "metrics_format=%V metrics_per_path=%i otel=%i"
#ifdef MARKDOWN_STREAMING_ENABLED
                        " streaming_engine=%s"
                        " streaming_budget=%uz"
                        " streaming_on_error=%V"
                        " streaming_shadow=%i"
                        " streaming_auto_threshold=%uz"
#endif
                       ,
                       (ngx_uint_t) conf->enabled,
                       ngx_http_markdown_enabled_source_name(conf->enabled_source),
                       conf->max_size,
                       conf->timeout,
                       ngx_http_markdown_on_error_name(conf->on_error),
                       ngx_http_markdown_flavor_name(conf->flavor),
                       (ngx_uint_t) conf->token_estimate,
                       (ngx_uint_t) conf->front_matter,
                       (ngx_uint_t) conf->on_wildcard,
                       ngx_http_markdown_auth_policy_name(conf->policy.auth_policy),
                       auth_cookie_count,
                       (ngx_uint_t) conf->policy.generate_etag,
                       ngx_http_markdown_conditional_requests_name(conf->policy.conditional_requests),
                       ngx_http_markdown_log_verbosity_name(conf->policy.log_verbosity),
                       (ngx_uint_t) conf->buffer_chunked,
                        stream_type_count,
                        content_type_count,
                       conf->large_body_threshold,
                       (ngx_uint_t) conf->ops.trust_forwarded_headers,
                        ngx_http_markdown_metrics_format_name(
                            conf->ops.metrics_format)
                        , (ngx_int_t) conf->ops.metrics_per_path
                        , (ngx_int_t) conf->ops.otel_enabled
#ifdef MARKDOWN_STREAMING_ENABLED
                        , conf->streaming.engine != NULL
                            ? "configured" : "auto (default)"
                       , conf->streaming.budget
                       , ngx_http_markdown_on_error_name(
                             conf->streaming.on_error)
                        , (ngx_int_t) conf->streaming.shadow
                        , conf->streaming.auto_threshold
#endif
                       );
}

#endif /* NGX_HTTP_MARKDOWN_CONFIG_CORE_IMPL_H */
