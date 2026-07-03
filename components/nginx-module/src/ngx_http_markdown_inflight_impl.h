/*
 * Per-worker inflight counter (spec 52).
 *
 * Provides a per-worker atomic counter that tracks the number of
 * markdown conversions currently in-flight.  When the counter reaches
 * max_inflight, new conversions are rejected via the unified error
 * policy (spec 51).
 *
 * WARNING: This header is an implementation detail of the main
 * translation unit (ngx_http_markdown_filter_module.c).  It must NOT
 * be included from any other .c file or used as a standalone
 * compilation unit.
 *
 * Design decisions:
 *   - Per-worker counter (not shared memory) avoids cross-worker lock
 *     contention.  NGINX workers are single-threaded event loops.
 *   - ngx_atomic_t fields allow metrics snapshot reads from any
 *     context without tearing.
 *   - Cleanup handler registered on r->pool guarantees decrement on
 *     every exit path (normal, abort, timeout, error).
 *   - Cleanup handler is idempotent (decremented flag prevents double
 *     decrement).
 */

#ifndef NGX_HTTP_MARKDOWN_INFLIGHT_IMPL_H
#define NGX_HTTP_MARKDOWN_INFLIGHT_IMPL_H

/*
 * Error class discriminant for Overload (from spec 51 FFI).
 * Must match ErrorClass::Overload in Rust (value = 5).
 */
#define NGX_HTTP_MARKDOWN_ERROR_CLASS_OVERLOAD  5

/*
 * Per-worker inflight counter state.
 *
 * All fields use ngx_atomic_t so that metrics snapshot reads are
 * safe from any execution context within the same worker.
 */
typedef struct {
    ngx_atomic_t   current;          /* Currently in-flight conversions */
    ngx_atomic_t   high_watermark;   /* Peak inflight value observed */
    ngx_atomic_t   overload_total;   /* Total requests rejected for overload */
} ngx_http_markdown_inflight_t;

/*
 * Per-request cleanup data attached to r->pool.
 *
 * The decremented flag ensures idempotent cleanup: multiple calls
 * to the handler (which should not happen but is defended against)
 * will not produce a double decrement.
 */
typedef struct {
    ngx_http_markdown_inflight_t  *counter;
    ngx_flag_t                     decremented;
} ngx_http_markdown_inflight_cleanup_t;

/* Per-worker global counter instance. */
static ngx_http_markdown_inflight_t  ngx_http_markdown_g_inflight;

static void
ngx_http_markdown_inflight_update_high_watermark(ngx_atomic_int_t new_val)
{
    for ( ;; ) {
        ngx_atomic_int_t  hw;

        hw = ngx_http_markdown_g_inflight.high_watermark;
        if (new_val <= hw) {
            return;
        }

        if (ngx_atomic_cmp_set(
                (ngx_atomic_uint_t *)
                    &ngx_http_markdown_g_inflight.high_watermark,
                (ngx_atomic_uint_t) hw,
                (ngx_atomic_uint_t) new_val))
        {
            return;
        }
    }
}

/*
 * Pool cleanup handler for inflight decrement.
 *
 * Called when r->pool is destroyed (normal completion, client abort,
 * timeout, error — any exit path).  Idempotent: checks the
 * decremented flag before modifying the counter.
 *
 * Parameters:
 *   data - pointer to ngx_http_markdown_inflight_cleanup_t
 */
static void
ngx_http_markdown_inflight_cleanup_handler(void *data)
{
    ngx_http_markdown_inflight_cleanup_t  *cd = data;

    if (cd->decremented) {
        return;
    }

    if (cd->counter->current > 0) {
        (void) ngx_atomic_fetch_add(&cd->counter->current, -1);
    }

    cd->decremented = 1;
}

/*
 * Try to increment the inflight counter and register cleanup.
 *
 * Called after eligibility check passes, before Rust conversion.
 * If the current inflight count >= max_inflight, the request is
 * rejected (overload).
 *
 * On success (NGX_OK):
 *   - counter.current is incremented
 *   - high_watermark is updated if new peak
 *   - cleanup handler registered on r->pool
 *
 * On overload (NGX_DECLINED):
 *   - counter.overload_total is incremented
 *   - counter.current is NOT incremented
 *   - no cleanup handler registered
 *
 * Parameters:
 *   r    - NGINX request (for pool and logging)
 *   conf - module config (for max_inflight)
 *
 * Returns:
 *   NGX_OK       - allowed, counter incremented
 *   NGX_DECLINED - overloaded, counter not incremented
 *   NGX_ERROR    - pool cleanup allocation failed (treat as error)
 */
static ngx_int_t
ngx_http_markdown_inflight_try_increment(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf)
{
    ngx_atomic_int_t                        cur;
    ngx_atomic_int_t                        new_val;
    ngx_pool_cleanup_t                     *cln;
    ngx_http_markdown_inflight_cleanup_t   *cd;

    cur = ngx_http_markdown_g_inflight.current;

    /* max_inflight == 0 means unlimited (no enforcement) */
    if (conf->routing.max_inflight == 0) {
        /* Fall through to CAS increment without limit check */
    } else if ((ngx_uint_t) cur >= conf->routing.max_inflight) {
        /* Overload: reject */
        (void) ngx_atomic_fetch_add(
            &ngx_http_markdown_g_inflight.overload_total, 1);

        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown: inflight limit reached "
                      "(current=%d, max=%ui), rejecting request",
                      (int) cur, conf->routing.max_inflight);

        return NGX_DECLINED;
    }

    /* Increment counter */
    new_val = ngx_atomic_fetch_add(
        &ngx_http_markdown_g_inflight.current, 1) + 1;

    ngx_http_markdown_inflight_update_high_watermark(new_val);

    /* Register cleanup handler on r->pool */
    cln = ngx_pool_cleanup_add(r->pool, sizeof(*cd));
    if (cln == NULL) {
        /* Cleanup alloc failed — decrement immediately to avoid leak */
        (void) ngx_atomic_fetch_add(
            &ngx_http_markdown_g_inflight.current, -1);

        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: failed to register inflight "
                      "cleanup handler, decrementing immediately");

        return NGX_ERROR;
    }

    cd = cln->data;
    cd->counter = &ngx_http_markdown_g_inflight;
    cd->decremented = 0;
    cln->handler = ngx_http_markdown_inflight_cleanup_handler;

    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: inflight increment "
                   "(current=%d, max=%ui)",
                   (int) new_val, conf->routing.max_inflight);

    return NGX_OK;
}

/*
 * Reset the per-worker inflight counter.
 *
 * Called during worker initialization or for testing.
 */
static ngx_inline void
ngx_http_markdown_inflight_reset(void)
{
    ngx_http_markdown_g_inflight.current = 0;
    ngx_http_markdown_g_inflight.high_watermark = 0;
    ngx_http_markdown_g_inflight.overload_total = 0;
}

/*
 * Read current inflight value (for metrics/diagnostics).
 */
static ngx_inline ngx_atomic_int_t
ngx_http_markdown_inflight_current(void)
{
    return ngx_http_markdown_g_inflight.current;
}

/*
 * Read high watermark (for metrics/diagnostics).
 */
static ngx_inline ngx_atomic_int_t
ngx_http_markdown_inflight_high_watermark(void)
{
    return ngx_http_markdown_g_inflight.high_watermark;
}

/*
 * Read overload total (for metrics/diagnostics).
 */
static ngx_inline ngx_atomic_int_t
ngx_http_markdown_inflight_overload_total(void)
{
    return ngx_http_markdown_g_inflight.overload_total;
}

#endif /* NGX_HTTP_MARKDOWN_INFLIGHT_IMPL_H */
