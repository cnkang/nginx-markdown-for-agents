#ifndef NGX_HTTP_MARKDOWN_CONFIG_CORE_IMPL_H
#define NGX_HTTP_MARKDOWN_CONFIG_CORE_IMPL_H

#include "ngx_http_markdown_dynconf_precedence.h"
#include "ngx_http_markdown_config_merge_impl.h"

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
 * Per-path RB-tree helpers removed from production in 0.9.2.
 * Retained under debug guard only.
 */
#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG
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
#endif /* MARKDOWN_METRICS_PER_PATH_DEBUG */

/*
 * Default per-path cardinality limit (debug builds only).
 *
 * Per-path metrics removed from production in 0.9.2 due to
 * unbounded cardinality risk.
 */
#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG
#define NGX_HTTP_MARKDOWN_PER_PATH_CARDINALITY_DEFAULT  100
#endif

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

#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG
    ngx_rbtree_init(&metrics->per_path.path_tree,
                    &metrics->per_path.sentinel,
                    ngx_http_markdown_path_rbtree_insert_value);
    metrics->per_path.cardinality_limit =
        NGX_HTTP_MARKDOWN_PER_PATH_CARDINALITY_DEFAULT;
#endif

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
    conf->dynconf_owner_conf = NULL;
    conf->loc_validation_index = ngx_pcalloc(
        cf->pool, sizeof(ngx_http_markdown_loc_validation_index_t));
    if (conf->loc_validation_index == NULL
        || ngx_http_markdown_loc_index_init(conf->loc_validation_index,
                                            cf->pool) != NGX_OK)
    {
        return NULL;
    }
    conf->trusted_proxies = NULL;
    conf->trusted_proxies_configured = 0;
    conf->trusted_proxies_manifest = NULL;
#ifdef NGX_HTTP_BROTLI
    conf->brotli_workspace_bytes = 0;
    conf->brotli_workspace_limit =
        NGX_HTTP_MARKDOWN_BROTLI_WORKSPACE_LIMIT;
#endif

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

static char *
ngx_http_markdown_check_streaming_cache_conflict(ngx_conf_t *cf,
    const ngx_http_markdown_conf_t *conf)
{
    if (!conf->stream.policy_explicit
        || conf->policy.conditional_requests
           != NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT)
    {
        return NGX_CONF_OK;
    }

    if (conf->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_FORCE) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"markdown_streaming force\" conflicts with "
            "\"markdown_cache_validation full\": the streaming path "
            "cannot generate a transformed-representation ETag; use "
            "\"markdown_cache_validation ims_only\" (or off), or "
            "\"markdown_streaming off|auto\"");
        return NGX_CONF_ERROR;
    }

    if (conf->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_AUTO) {
        ngx_conf_log_error(NGX_LOG_WARN, cf, 0,
            "\"markdown_streaming auto\" with "
            "\"markdown_cache_validation full\": streaming is blocked "
            "at runtime (reason streaming_block_full_cache_validation) "
            "and each request falls back to the full-buffer path; use "
            "\"markdown_cache_validation ims_only\" to allow streaming");
    }

    return NGX_CONF_OK;
}


static ngx_int_t
ngx_http_markdown_register_loc_validation(
    ngx_conf_t *cf, const ngx_http_markdown_conf_t *conf)
{
    const ngx_http_conf_ctx_t    *http_ctx;
    ngx_http_markdown_main_conf_t *main_conf;

    if (cf == NULL || cf->ctx == NULL) {
        return NGX_OK;
    }

    http_ctx = (ngx_http_conf_ctx_t *) cf->ctx;
    main_conf = http_ctx->main_conf[
        ngx_http_markdown_filter_module.ctx_index];
    if (main_conf == NULL) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "markdown: failed to access main configuration for "
            "dynconf validation index");
        return NGX_ERROR;
    }

    if (main_conf->loc_validation_index == NULL) {
        main_conf->loc_validation_index = ngx_pcalloc(
            cf->pool, sizeof(ngx_http_markdown_loc_validation_index_t));
    }

    if (main_conf->loc_validation_index == NULL
        || (main_conf->loc_validation_index->entries == NULL
            && ngx_http_markdown_loc_index_init(
                   main_conf->loc_validation_index, cf->pool) != NGX_OK))
    {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "markdown: failed to initialize dynconf validation index");
        return NGX_ERROR;
    }

    if (ngx_http_markdown_loc_index_add(
            main_conf->loc_validation_index,
            conf->limits.conversion_memory,
            conf->advanced.dynconf_block_mask) != NGX_OK)
    {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "markdown: dynconf validation index is full");
        return NGX_ERROR;
    }

    return NGX_OK;
}

static void
ngx_http_markdown_mark_static_explicit_fields(
    ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    ngx_uint_t  mask;

    mask = conf->advanced.static_explicit_mask;
    if (conf->enabled_source != NGX_HTTP_MARKDOWN_ENABLED_UNSET) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FILTER;
    }
    if (conf->limits.configured) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_LIMITS;
    }
    if (conf->flavor != NGX_CONF_UNSET_UINT) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FLAVOR;
    }
    if (conf->token_estimate != NGX_CONF_UNSET) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_TOKEN;
    }
    if (conf->front_matter != NGX_CONF_UNSET) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FRONT_MATTER;
    }
    if (conf->accept_policy != NGX_CONF_UNSET_UINT) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_ACCEPT;
    }
    if (conf->policy.auth_policy != NGX_CONF_UNSET_UINT) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_AUTH_POLICY;
    }
    if (conf->policy.auth_cookies != NGX_CONF_UNSET_PTR) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_AUTH_COOKIES;
    }
    if (conf->policy.conditional_requests != NGX_CONF_UNSET_UINT
        || conf->policy.generate_etag != NGX_CONF_UNSET)
    {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_CACHE;
    }
    if (conf->stream.policy != NGX_CONF_UNSET_UINT) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_STREAM;
    }
    if (conf->policy.log_verbosity != NGX_CONF_UNSET_UINT) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_LOG;
    }
    if (conf->routing.content_types != NGX_CONF_UNSET_PTR) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_CONTENT;
    }
    if (conf->advanced.prune_noise != NGX_CONF_UNSET) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_PRUNE;
    }
    if (conf->advanced.prune_selectors != NGX_CONF_UNSET_PTR) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_SELECTORS;
    }
    if (conf->advanced.prune_protection_selectors != NGX_CONF_UNSET_PTR) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_PROTECTION;
    }
    if (conf->decompress.auto_decompress != NGX_CONF_UNSET) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DECOMPRESS;
    }
    if (conf->advanced.dynconf_enabled != NGX_CONF_UNSET
        || conf->advanced.dynconf_path.data != NULL)
    {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DYNCONF;
    }
    if (conf->advanced.dynconf_dry_run != NGX_CONF_UNSET) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DRY_RUN;
    }
    if (conf->ops.diagnostics_enabled != NGX_CONF_UNSET) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DIAGNOSTICS;
    }
    if (conf->stream.excluded_types != NGX_CONF_UNSET_PTR) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_EXCLUDED;
    }
    if (conf->ops.metrics_enabled != NGX_CONF_UNSET) {
        mask |= NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_METRICS;
    }
    conf->advanced.static_explicit_mask = mask
        | prev->advanced.static_explicit_mask;
}


static void
ngx_http_markdown_mark_dynconf_block_fields(
    ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
    if (conf->advanced.prune_noise != NGX_CONF_UNSET) {
        conf->advanced.dynconf_block_mask |=
            NGX_HTTP_MARKDOWN_BLOCK_PRUNE_NOISE;
    }
    if (conf->policy.log_verbosity != NGX_CONF_UNSET_UINT) {
        conf->advanced.dynconf_block_mask |=
            NGX_HTTP_MARKDOWN_BLOCK_LOG_VERBOSITY;
    }
    if (conf->on_error != NGX_CONF_UNSET_UINT) {
        conf->advanced.dynconf_block_mask |=
            NGX_HTTP_MARKDOWN_BLOCK_ERROR_POLICY;
    }
    if (conf->limits.streaming_buffer != NGX_CONF_UNSET_SIZE) {
        conf->advanced.dynconf_block_mask |=
            NGX_HTTP_MARKDOWN_BLOCK_STREAMING_BUFFER;
    }
    conf->advanced.dynconf_block_mask |= prev->advanced.dynconf_block_mask;
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
    conf->decompress.max_size_explicit = 0;
    conf->timeout = NGX_CONF_UNSET_MSEC;
    conf->on_error = NGX_CONF_UNSET_UINT;
    conf->error_status = NGX_CONF_UNSET_UINT;
    conf->flavor = NGX_CONF_UNSET_UINT;
    conf->token_estimate = NGX_CONF_UNSET;
    conf->front_matter = NGX_CONF_UNSET;
    conf->accept_policy = NGX_CONF_UNSET_UINT;
    conf->policy.auth_policy = NGX_CONF_UNSET_UINT;
    conf->policy.auth_cookies = NGX_CONF_UNSET_PTR;
    conf->policy.generate_etag = NGX_CONF_UNSET;
    conf->policy.conditional_requests = NGX_CONF_UNSET_UINT;
    conf->policy.log_verbosity = NGX_CONF_UNSET_UINT;
    conf->routing.content_types = NGX_CONF_UNSET_PTR;
    conf->decompress.auto_decompress = NGX_CONF_UNSET;
    conf->decompress.max_size = NGX_CONF_UNSET_SIZE;
    conf->decompress.parse_timeout = NGX_CONF_UNSET_MSEC;
    conf->decompress.parser_budget = NGX_CONF_UNSET_SIZE;
    conf->routing.large_body_threshold = NGX_CONF_UNSET_SIZE;
    conf->routing.max_inflight = NGX_CONF_UNSET_UINT;
    conf->ops.diagnostics_enabled = NGX_CONF_UNSET;
    conf->ops.metrics_enabled = NGX_CONF_UNSET;

    /* v0.8.0 streaming config */
    conf->stream.policy = NGX_CONF_UNSET_UINT;
    conf->stream.policy_explicit = -1;
    conf->stream.excluded_types = NGX_CONF_UNSET_PTR;
    conf->stream.budget = NGX_CONF_UNSET_SIZE;
    conf->stream.budget_explicit = -1;

    /* 0.9.2 unified limits */
    conf->limits.conversion_timeout = NGX_CONF_UNSET_MSEC;
    conf->limits.parser_timeout = NGX_CONF_UNSET_MSEC;
    conf->limits.conversion_memory = NGX_CONF_UNSET_SIZE;
    conf->limits.parser_memory = NGX_CONF_UNSET_SIZE;
    conf->limits.streaming_buffer = NGX_CONF_UNSET_SIZE;
    conf->limits.decompressed_size = NGX_CONF_UNSET_SIZE;
    conf->limits.decompression_ratio = NGX_CONF_UNSET_UINT;
    conf->limits.max_inflight = NGX_CONF_UNSET_UINT;

    conf->advanced.prune_noise = NGX_CONF_UNSET;
    conf->advanced.prune_selectors = NGX_CONF_UNSET_PTR;
    conf->advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
    conf->advanced.memory_budget = NGX_CONF_UNSET_SIZE;
    conf->advanced.dynconf_enabled = NGX_CONF_UNSET;
    conf->advanced.dynconf_path.len = 0;
    conf->advanced.dynconf_path.data = NULL;
    conf->advanced.dynconf_dry_run = NGX_CONF_UNSET;

    /* 0.9.2 dynconf precedence model: block mask starts at 0 (no fields blocked) */
    conf->advanced.dynconf_block_mask = 0;
    conf->advanced.static_explicit_mask = 0;

    return conf;
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
    const ngx_http_markdown_conf_t      *prev = parent;
    ngx_http_markdown_conf_t            *conf = child;

    ngx_http_markdown_mark_static_explicit_fields(conf, prev);
    ngx_http_markdown_mark_dynconf_block_fields(conf, prev);

    ngx_flag_t  max_size_set;

    max_size_set = ngx_http_markdown_merge_inherited_values(conf, prev);

    /*
     * Cross-key constraint validation (0.9.2 frozen contract).
     *
     * After inheritance/merge resolves all 8 effective values, verify:
     *   parser_timeout <= conversion_timeout
     *   parser_memory  <= conversion_memory
     *   streaming_buffer <= conversion_memory
     *
     * Violation fails nginx -t atomically.
     */
    if (conf->limits.parser_timeout > conf->limits.conversion_timeout) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "markdown_limits cross-key constraint violated: "
            "parser_timeout (%Mms) must not exceed "
            "conversion_timeout (%Mms)",
            conf->limits.parser_timeout,
            conf->limits.conversion_timeout);
        return NGX_CONF_ERROR;
    }

    if (conf->limits.parser_memory > conf->limits.conversion_memory) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "markdown_limits cross-key constraint violated: "
            "parser_memory (%uz) must not exceed "
            "conversion_memory (%uz)",
            conf->limits.parser_memory,
            conf->limits.conversion_memory);
        return NGX_CONF_ERROR;
    }

    if (conf->limits.streaming_buffer > conf->limits.conversion_memory) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "markdown_limits cross-key constraint violated: "
            "streaming_buffer (%uz) must not exceed "
            "conversion_memory (%uz)",
            conf->limits.streaming_buffer,
            conf->limits.conversion_memory);
        return NGX_CONF_ERROR;
    }

    ngx_http_markdown_apply_memory_budget_override(conf, prev, max_size_set);

    /*
     * Resolve decompress_max_size default: if not explicitly set at any
     * level, inherit max_size.  This must run after memory_budget override
     * so the default tracks the effective max_size.
     */
    if (conf->decompress.max_size == NGX_CONF_UNSET_SIZE) {
        conf->decompress.max_size = conf->max_size;
    }

    /*
     * Reject zero decompress.max_size when auto_decompress is enabled:
     * a budget of 0 would reject all decompression unconditionally,
     * which is almost certainly a misconfiguration.
     */
    if (conf->decompress.auto_decompress && conf->decompress.max_size == 0) {
        ngx_conf_log_error(NGX_LOG_EMERG, cf, 0,
            "\"decompressed_size\" must be greater "
            "than 0 when auto_decompress is enabled");
        return NGX_CONF_ERROR;
    }

    if (ngx_http_markdown_check_streaming_cache_conflict(cf, conf)
        != NGX_CONF_OK)
    {
        return NGX_CONF_ERROR;
    }

    ngx_http_markdown_log_merged_conf(cf, conf);

    /*
     * Location validation index entry (0.9.2 dynconf precedence).
     *
     * After all merges complete and cross-key constraints are validated,
     * add this location to the global validation index.  The index is
     * used during dynconf reload to validate streaming_buffer candidates
     * against per-location conversion_memory limits.
     *
     * The main configuration owns the bounded index and each merged
     * location registers its effective conversion_memory and block mask.
     * Locations with streaming_buffer blocked (block bit set) are
     * recorded but marked not-applicable for the constraint check.
     *
     * The ngx_http_markdown_loc_index_add() call below uses the finalized
     * conf->limits.conversion_memory and conf->advanced.dynconf_block_mask.
     */

    if (ngx_http_markdown_register_loc_validation(cf, conf) != NGX_OK) {
        return NGX_CONF_ERROR;
    }

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
 *   value - flavor constant (COMMONMARK or GFM)
 *
 * Returns:
 *   Static ngx_str_t with the flavor name or "unknown"
 */
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
                      "markdown: failed to evaluate markdown_filter variable");
        return 0;
    }

    rc = ngx_http_markdown_parse_filter_flag(&evaluated, &enabled);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown: markdown_filter variable resolved to invalid value "
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
    ngx_uint_t content_type_count = (conf->routing.content_types != NULL) ? conf->routing.content_types->nelts : 0;
#ifdef MARKDOWN_STREAMING_ENABLED
    const char *streaming_policy_str;
#endif

    if (cf == NULL) {
        return;
    }

#ifdef MARKDOWN_STREAMING_ENABLED
    if (conf->stream.policy != NGX_HTTP_MARKDOWN_STREAMING_AUTO) {
        streaming_policy_str =
            (conf->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_OFF)
            ? "off" : "force";
    } else {
        streaming_policy_str = "auto (default)";
    }
#endif

    log_level = ngx_http_markdown_log_verbosity_to_ngx_level(conf->policy.log_verbosity);

    ngx_conf_log_error(log_level, cf, 0,
                       "markdown: enabled=%ui "
                       "enabled_source=%V max_size=%uz "
                       "timeout_ms=%M on_error=%V flavor=%V "
                       "token_estimate=%ui front_matter=%ui "
                       "accept_policy=%ui auth_policy=%V "
                       "auth_cookie_patterns=%ui etag=%ui "
                       "conditional_requests=%V "
                       "log_verbosity=%V "
                        "content_types=%ui "
                       "large_body_threshold=%uz"
#ifdef MARKDOWN_STREAMING_ENABLED
                        " streaming_policy=%s"
                        " streaming_budget=%uz"
                        " streaming_error_policy=%V"
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
                       conf->accept_policy,
                       ngx_http_markdown_auth_policy_name(conf->policy.auth_policy),
                       auth_cookie_count,
                       (ngx_uint_t) conf->policy.generate_etag,
                       ngx_http_markdown_conditional_requests_name(conf->policy.conditional_requests),
                       ngx_http_markdown_log_verbosity_name(conf->policy.log_verbosity),
                        content_type_count,
                       conf->routing.large_body_threshold
#ifdef MARKDOWN_STREAMING_ENABLED
                        , streaming_policy_str
                        , conf->stream.budget
                        , ngx_http_markdown_on_error_name(conf->on_error)
#endif
                       );
}

#endif /* NGX_HTTP_MARKDOWN_CONFIG_CORE_IMPL_H */
