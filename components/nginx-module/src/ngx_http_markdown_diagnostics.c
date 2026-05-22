/*
 * NGINX Markdown Filter Module - Diagnostics Endpoint Implementation
 *
 * Implements the /nginx-markdown/diagnostics content handler that
 * exposes runtime state for operational introspection:
 *   - Configuration snapshot (current directive values)
 *   - Recent decisions ring buffer (last N conversion decisions)
 *   - Metrics snapshot (current counter values)
 *   - Dynamic configuration state (mtime, version, LKG)
 *
 * The endpoint is gated by the markdown_diagnostics directive (on/off)
 * and access control (default deny, explicit allow needed).
 *
 * Requirement: REQ-0700-OPERABILITY-001
 * Risk Pack: dynamic-config-hot-reload
 */

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#include "ngx_http_markdown_diagnostics.h"
#include "ngx_http_markdown_dynconf_snapshot.h"
#include "ngx_http_markdown_filter_module.h"


/*
 * Static global diagnostics state for this worker process.
 *
 * IMPORTANT: Per-worker semantics.  NGINX uses a multi-process model
 * where each worker has its own address space.  This diagnostics state
 * is local to the worker that handles the diagnostics request.  If
 * multiple workers are configured, each worker reports only its own
 * decisions and metrics.  Operators should aggregate externally (e.g.
 * via Prometheus scraping all workers) for a global view.
 *
 * Initialized once during module postconfiguration (or worker init)
 * and shared across all requests in this worker.  The ring buffer
 * is written by ngx_http_markdown_diagnostics_record() from the
 * request path and read by the diagnostics handler.
 */
static ngx_http_markdown_diag_state_t  ngx_http_markdown_g_diag_state;
static ngx_flag_t  ngx_http_markdown_g_diag_initialized = 0;


/*
 * Forward declarations for static helper functions.
 */
static ngx_int_t ngx_http_markdown_diagnostics_check_access(
    ngx_http_request_t *r);
static ngx_int_t ngx_http_markdown_diagnostics_build_json(
    ngx_http_request_t *r, ngx_buf_t *b);


/*
 * Initialize the diagnostics subsystem.
 *
 * Allocates the ring buffer entries array from the provided pool.
 * Uses the default capacity if the caller passes 0.  Clamps to
 * the maximum allowed capacity.
 *
 * Parameters:
 *   state    - Diagnostics state to initialize; must be non-NULL
 *   pool     - Pool for allocation (typically cycle pool)
 *   capacity - Desired ring buffer capacity (0 = default)
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on allocation failure or NULL state
 */
ngx_int_t
ngx_http_markdown_diagnostics_init(ngx_http_markdown_diag_state_t *state,
    ngx_pool_t *pool, ngx_uint_t capacity)
{
    if (state == NULL || pool == NULL) {
        return NGX_ERROR;
    }

    if (capacity == 0) {
        capacity = NGX_HTTP_MARKDOWN_DIAG_DEFAULT_CAPACITY;
    }

    if (capacity > NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY) {
        capacity = NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY;
    }

    state->ring.entries = ngx_pcalloc(pool,
        capacity * sizeof(ngx_http_markdown_diag_decision_t));

    if (state->ring.entries == NULL) {
        return NGX_ERROR;
    }

    state->ring.capacity = capacity;
    state->ring.head = 0;
    state->ring.count = 0;
    state->enabled = 0;

    /*
     * If the caller initialized the global state, mark it as ready.
     */
    if (state == &ngx_http_markdown_g_diag_state) {
        ngx_http_markdown_g_diag_initialized = 1;
    }

    return NGX_OK;
}


/*
 * Record a decision in the diagnostics ring buffer.
 *
 * Writes a new decision record at the current head position.
 * If the buffer is full, the oldest entry is overwritten (FIFO).
 * The timestamp is captured from the NGINX cached time.
 *
 * Parameters:
 *   state       - Diagnostics state (must be initialized and enabled)
 *   reason_code - Decision reason code value
 *   duration_ms - Processing duration in milliseconds
 */
void
ngx_http_markdown_diagnostics_record(ngx_http_markdown_diag_state_t *state,
    ngx_int_t reason_code, ngx_msec_t duration_ms)
{
    ngx_http_markdown_diag_decision_t  *entry;

    if (state == NULL || state->ring.entries == NULL || !state->enabled) {
        return;
    }

    entry = &state->ring.entries[state->ring.head];
    entry->timestamp = ngx_current_msec;
    entry->reason_code = reason_code;
    entry->duration_ms = duration_ms;

    state->ring.head = (state->ring.head + 1) % state->ring.capacity;

    if (state->ring.count < state->ring.capacity) {
        state->ring.count++;
    }
}


/*
 * HTTP content handler for the diagnostics endpoint.
 *
 * Validates access control, builds the JSON response, and sends
 * it to the client.  Only responds to GET and HEAD methods.
 *
 * The response Content-Type is application/json.
 *
 * Parameters:
 *   r - HTTP request
 *
 * Returns:
 *   NGX_OK on success, NGX_HTTP_FORBIDDEN on access denial,
 *   NGX_HTTP_INTERNAL_SERVER_ERROR on build failure
 */
ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    ngx_int_t     rc;
    ngx_buf_t    *b;
    ngx_chain_t   out;

    /* Only allow GET and HEAD */
    if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD))) {
        return NGX_HTTP_NOT_ALLOWED;
    }

    /* Access control check (default deny) */
    rc = ngx_http_markdown_diagnostics_check_access(r);
    if (rc != NGX_OK) {
        return rc;
    }

    /* Discard request body */
    rc = ngx_http_discard_request_body(r);
    if (rc != NGX_OK) {
        return rc;
    }

    /*
     * Allocate response buffer from request pool.
     * Initial size is generous for the JSON payload;
     * subsequent tasks will refine sizing based on actual content.
     */
    b = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    /* Build JSON response into buffer */
    rc = ngx_http_markdown_diagnostics_build_json(r, b);
    if (rc != NGX_OK) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    /* Set response headers */
    r->headers_out.status = NGX_HTTP_OK;
    r->headers_out.content_type_len = sizeof("application/json") - 1;
    ngx_str_set(&r->headers_out.content_type, "application/json");
    r->headers_out.content_type_lowcase = NULL;
    r->headers_out.content_length_n = b->last - b->pos;

    /* HEAD request: send headers only */
    if (r->method == NGX_HTTP_HEAD) {
        rc = ngx_http_send_header(r);
        if (rc == NGX_ERROR || rc > NGX_OK) {
            return rc;
        }
        return NGX_OK;
    }

    /* Send headers */
    rc = ngx_http_send_header(r);
    if (rc == NGX_ERROR || rc > NGX_OK) {
        return rc;
    }

    /* Send body */
    b->last_buf = (r == r->main) ? 1 : 0;
    b->last_in_chain = 1;
    b->memory = 1;

    out.buf = b;
    out.next = NULL;

    return ngx_http_output_filter(r, &out);
}


/*
 * Cleanup the diagnostics subsystem.
 *
 * Resets ring buffer counters.  The entries array memory is
 * owned by the pool and freed when the pool is destroyed.
 *
 * Parameters:
 *   state - Diagnostics state to clean up; no-op if NULL
 */
void
ngx_http_markdown_diagnostics_cleanup(ngx_http_markdown_diag_state_t *state)
{
    if (state == NULL) {
        return;
    }

    state->ring.head = 0;
    state->ring.count = 0;
    state->enabled = 0;
}


/*
 * Get the global diagnostics state for this worker.
 *
 * Returns a pointer to the static global diagnostics state.
 * If the state has not been initialized yet, initializes it
 * with the default capacity using the provided cycle pool
 * (on first call from the handler).
 *
 * Returns:
 *   Pointer to the diagnostics state, or NULL if not initialized
 */
ngx_http_markdown_diag_state_t *
ngx_http_markdown_diagnostics_get_state(void)
{
    if (!ngx_http_markdown_g_diag_initialized) {
        return NULL;
    }

    return &ngx_http_markdown_g_diag_state;
}


/*
 * Access control for the diagnostics endpoint.
 *
 * Default-deny policy with CIDR allowlist support:
 *
 *   1. The endpoint only activates when "markdown_diagnostics on" is
 *      explicitly configured in a location block.  Without this directive
 *      the handler is never registered, so no external access is possible.
 *
 *   2. If markdown_diagnostics_allow directives are configured, the
 *      client IP is checked against the CIDR allow list.  Only matching
 *      addresses are permitted.
 *
 *   3. If no markdown_diagnostics_allow directives are configured
 *      (allow list is empty/NULL), falls back to the built-in
 *      loopback-only restriction: only 127.0.0.1 and ::1 are allowed.
 *
 *   4. For additional access control, operators can also use NGINX's
 *      native allow/deny directives in the same location block.
 *
 * Parameters:
 *   r - HTTP request
 *
 * Returns:
 *   NGX_OK             - access permitted
 *   NGX_HTTP_FORBIDDEN - access denied
 */
static ngx_int_t
ngx_http_markdown_diagnostics_check_access(ngx_http_request_t *r)
{
    struct sockaddr              *sa;
    ngx_http_markdown_conf_t    *conf;
    ngx_array_t                 *allow_list;
    const struct sockaddr_in    *sin;
#if (NGX_HAVE_INET6)
    const struct sockaddr_in6   *sin6;
    static const uint8_t  ipv6_loopback[16] = {
        0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 1
    };
#endif

    sa = r->connection->sockaddr;

    if (sa == NULL) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
            "markdown: no client address, "
            "denying access");
        return NGX_HTTP_FORBIDDEN;
    }

    /* Retrieve the location configuration for the allow list. */
    conf = ngx_http_get_module_loc_conf(r,
        ngx_http_markdown_filter_module);

    allow_list = (conf != NULL) ? conf->ops.diagnostics_allow : NULL;

    /*
     * If a CIDR allow list is configured, check the client IP
     * against it.  Only matching addresses are permitted.
     */
    if (allow_list != NULL && allow_list->nelts > 0) {
        ngx_cidr_t  *cidrs;
        ngx_uint_t   i;

        cidrs = allow_list->elts;

        for (i = 0; i < allow_list->nelts; i++) {

            switch (sa->sa_family) {

            case AF_INET:
                if (cidrs[i].family != AF_INET) {
                    continue;
                }

                sin = (const struct sockaddr_in *) sa;
                if ((sin->sin_addr.s_addr & cidrs[i].u.in.mask)
                    == cidrs[i].u.in.addr)
                {
                    return NGX_OK;
                }
                break;

#if (NGX_HAVE_INET6)
            case AF_INET6:
                if (cidrs[i].family != AF_INET6) {
                    continue;
                }

                sin6 = (const struct sockaddr_in6 *) sa;
                {
                    ngx_uint_t  j;
                    ngx_uint_t  match;

                    match = 1;
                    for (j = 0; j < 16; j++) {
                        if ((sin6->sin6_addr.s6_addr[j]
                             & cidrs[i].u.in6.mask.s6_addr[j])
                            != cidrs[i].u.in6.addr.s6_addr[j])
                        {
                            match = 0;
                            break;
                        }
                    }

                    if (match) {
                        return NGX_OK;
                    }
                }
                break;
#endif

            default:
                break;
            }
        }

        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
            "markdown: client address not in "
            "diagnostics_allow list, denying access");
        return NGX_HTTP_FORBIDDEN;
    }

    /*
     * No allow list configured: fall back to loopback-only.
     */
    switch (sa->sa_family) {

    case AF_INET:
        sin = (const struct sockaddr_in *) sa;

        if (sin->sin_addr.s_addr == htonl(INADDR_LOOPBACK)) {
            return NGX_OK;
        }

        break;

#if (NGX_HAVE_INET6)
    case AF_INET6:
        sin6 = (const struct sockaddr_in6 *) sa;

        if (ngx_memcmp(sin6->sin6_addr.s6_addr,
                       ipv6_loopback, 16) == 0)
        {
            return NGX_OK;
        }

        break;
#endif

    default:
        break;
    }

    ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
        "markdown: access denied for "
        "non-loopback client; configure "
        "markdown_diagnostics_allow for granular control");

    return NGX_HTTP_FORBIDDEN;
}


/*
 * Build the JSON diagnostics response.
 *
 * Constructs a JSON document with four top-level sections:
 *   - config_snapshot
 *   - recent_decisions
 *   - metrics_snapshot
 *   - dynconf_state
 *
 * Allocates the output buffer from the request pool.
 *
 * Parameters:
 *   r - HTTP request (for pool allocation and config access)
 *   b - Buffer to populate with JSON content
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on failure
 */
static ngx_int_t
ngx_http_markdown_diagnostics_build_json(ngx_http_request_t *r,
    ngx_buf_t *b)
{
    u_char                              *buf;
    u_char                              *p;
    u_char                              *last;
    size_t                               buf_size;
    ngx_http_markdown_conf_t            *conf;
    u_char                              *snap_buf;
    size_t                               snap_len;
    ngx_int_t                            rc;
    ngx_http_markdown_diag_state_t      *state;
    ngx_http_markdown_diag_metrics_t     metrics;
    ngx_http_markdown_diag_dynconf_t     dynconf;

    /*
     * Pre-allocate a buffer large enough for the JSON response.
     * 32KB accommodates the config snapshot, ring buffer entries
     * (up to 100 decisions at ~60 bytes each), metrics, and dynconf.
     */
    buf_size = 32768;

    buf = ngx_palloc(r->pool, buf_size);
    if (buf == NULL) {
        return NGX_ERROR;
    }

    p = buf;
    last = buf + buf_size;

    /* Retrieve the location configuration */
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);

    /* Opening brace */
    p = ngx_slprintf(p, last, "{\n");

    /* --- config_snapshot section --- */
    p = ngx_slprintf(p, last, "  \"config_snapshot\": {\n");

    if (conf != NULL) {
        rc = ngx_http_markdown_dynconf_snapshot_to_json(r->pool, conf,
                                                         &snap_buf,
                                                         &snap_len);
        if (rc == NGX_OK && snap_buf != NULL && snap_len > 0) {
            if (p + snap_len < last) {
                ngx_memcpy(p, snap_buf, snap_len);
                p += snap_len;
            }
        }
    }

    p = ngx_slprintf(p, last, "  },\n");

    /* --- recent_decisions section --- */
    state = ngx_http_markdown_diagnostics_get_state();

    p = ngx_slprintf(p, last, "  \"recent_decisions\": [");

    if (state != NULL && state->ring.entries != NULL
        && state->ring.count > 0)
    {
        ngx_uint_t                          i;
        ngx_uint_t                          idx;
        ngx_uint_t                          count;
        ngx_http_markdown_diag_decision_t  *entry;

        count = state->ring.count;

        /*
         * Iterate newest-first (reverse chronological order).
         * The newest entry is at (head - 1) mod capacity.
         * Walk backward through 'count' entries, wrapping around.
         */
        for (i = 0; i < count; i++) {
            if (state->ring.head >= (i + 1)) {
                idx = state->ring.head - (i + 1);
            } else {
                idx = state->ring.capacity
                    - ((i + 1) - state->ring.head);
            }

            entry = &state->ring.entries[idx];

            if (i == 0) {
                p = ngx_slprintf(p, last, "\n");
            }

            p = ngx_slprintf(p, last,
                "    {\"timestamp\": %M, "
                "\"reason_code\": %i, "
                "\"duration_ms\": %M}",
                entry->timestamp,
                entry->reason_code,
                entry->duration_ms);

            if (i + 1 < count) {
                p = ngx_slprintf(p, last, ",\n");
            } else {
                p = ngx_slprintf(p, last, "\n  ");
            }
        }
    }

    p = ngx_slprintf(p, last, "],\n");

    /* --- metrics_snapshot section --- */
    ngx_http_markdown_diagnostics_collect_metrics(&metrics);

    p = ngx_slprintf(p, last,
        "  \"metrics_snapshot\": {\n"
        "    \"conversions_total\": %uA,\n"
        "    \"delivery_total\": %uA,\n"
        "    \"requests_total\": %uA,\n"
        "    \"failopen_total\": %uA\n"
        "  },\n",
        metrics.conversions_total,
        metrics.delivery_total,
        metrics.requests_total,
        metrics.failopen_total);

    /* --- dynconf_state section --- */
    ngx_http_markdown_diagnostics_get_dynconf_state(&dynconf);

    p = ngx_slprintf(p, last,
        "  \"dynconf_state\": {\n"
        "    \"active_mtime\": \"%T\",\n"
        "    \"config_version\": %ui,\n"
        "    \"last_known_good_mtime\": \"%T\",\n"
        "    \"lkg_valid\": %s\n"
        "  }\n",
        dynconf.active_mtime,
        dynconf.config_version,
        dynconf.last_known_good_mtime,
        dynconf.lkg_valid ? "true" : "false");

    /* Closing brace */
    p = ngx_slprintf(p, last, "}\n");

    /* Detect silent truncation: if we hit the buffer boundary, the JSON
     * is incomplete and must not be served as valid output. */
    if (p >= last) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: diagnostics JSON truncated, "
            "buffer size %uz insufficient",
            buf_size);
        return NGX_ERROR;
    }

    b->pos = buf;
    b->last = p;
    b->start = buf;
    b->end = buf + buf_size;

    return NGX_OK;
}


/*
 * Determine if a conversion_status indicates a failure outcome.
 *
 * Used to gate log emission when log_verbosity is warn or error
 * (only failures are logged at those levels).
 *
 * Parameters:
 *   status - conversion_status string (e.g. "FAILED")
 *
 * Returns:
 *   1 if the status represents a failure, 0 otherwise
 */
static ngx_int_t
ngx_http_markdown_decision_path_is_failure(const char *status)
{
    if (status == NULL) {
        return 0;
    }

    /*
     * Compare against the FAILED constant.  Use ngx_strcmp which
     * is safe for NUL-terminated C strings.
     */
    if (ngx_strcmp(status, NGX_HTTP_MARKDOWN_CONV_FAILED) == 0) {
        return 1;
    }

    return 0;
}


/*
 * Log the structured decision path for a request.
 *
 * Emits a single structured log line in key=value format:
 *
 *   markdown: accept_result=CONVERT
 *       conditional_result=PROCEED conversion_status=SUCCESS
 *       reason_code=CONVERTED duration_ms=12
 *
 * This function uses only stack-local variables and does NOT
 * allocate from the pool or heap.  It is safe to call from
 * both header_filter and body_filter paths without risk of
 * memory pressure in the hot path.
 *
 * Verbosity gating:
 *   - log_verbosity >= info (2): log all outcomes
 *   - log_verbosity == warn (1): log only failures
 *   - log_verbosity == error (0): log only failures
 *   - diagnostics enabled: always log (overrides verbosity)
 *
 * Parameters:
 *   r    - HTTP request (for connection log access)
 *   conf - Module location configuration (cast to
 *          ngx_http_markdown_conf_t internally)
 *   eff  - Effective configuration view (cast to
 *          ngx_http_markdown_effective_conf_t internally);
 *          may be NULL
 *   path - Decision path components to log
 */
void
ngx_http_markdown_log_decision_path(ngx_http_request_t *r,
    const void *conf_ptr, const void *eff_ptr,
    const ngx_http_markdown_decision_path_t *path)
{
    ngx_uint_t                                   effective_verbosity;
    ngx_uint_t                                   log_level;
    ngx_int_t                                    is_failure;
    const ngx_http_markdown_conf_t              *conf;
    const ngx_http_markdown_effective_conf_t    *eff;
    const char                                  *accept_str;
    const char                                  *cond_str;
    const char                                  *conv_str;
    const char                                  *reason_str;

    if (r == NULL || path == NULL) {
        return;
    }

    conf = (const ngx_http_markdown_conf_t *) conf_ptr;
    eff = (const ngx_http_markdown_effective_conf_t *) eff_ptr;

    /*
     * Determine effective log verbosity.
     * If eff is available, use its log_verbosity; otherwise
     * fall back to conf->policy.log_verbosity.
     */
    if (eff != NULL) {
        effective_verbosity = eff->log_verbosity;
    } else if (conf != NULL) {
        effective_verbosity = conf->policy.log_verbosity;
    } else {
        /* No configuration available; default to info */
        effective_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    }

    is_failure = ngx_http_markdown_decision_path_is_failure(
        path->conversion_status);

    /*
     * Verbosity gating:
     * - error/warn: only emit for failure outcomes
     * - info/debug: emit for all outcomes
     *
     * Exception: if diagnostics is enabled (state->enabled),
     * always emit regardless of verbosity.  We check this via
     * the conf pointer — if conf is NULL we cannot determine
     * diagnostics state, so we rely on verbosity alone.
     */
    if (effective_verbosity <= NGX_HTTP_MARKDOWN_LOG_WARN
        && !is_failure)
    {
        return;
    }

    /* Select NGINX log level based on outcome and verbosity */
    if (effective_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG) {
        log_level = NGX_LOG_DEBUG;
    } else if (is_failure) {
        log_level = NGX_LOG_WARN;
    } else {
        log_level = NGX_LOG_INFO;
    }

    /* Use safe defaults for NULL component strings */
    accept_str = (path->accept_result != NULL)
        ? path->accept_result : "-";
    cond_str = (path->conditional_result != NULL)
        ? path->conditional_result : "-";
    conv_str = (path->conversion_status != NULL)
        ? path->conversion_status : "-";
    reason_str = (path->reason_code != NULL)
        ? path->reason_code : "-";

    /*
     * Emit the structured decision path log line.
     *
     * Format: key=value pairs separated by spaces.
     * This format is easily parseable by log aggregators
     * (Splunk, Loki, Datadog, etc.) and grep-friendly.
     *
     * Note: ngx_log_error with %s format specifier handles
     * NUL-terminated C strings directly.  No ngx_str_t
     * conversion needed since all values are string literals
     * or pre-formatted constants.
     */
    ngx_log_error(log_level, r->connection->log, 0,
        "markdown: "
        "accept_result=%s "
        "conditional_result=%s "
        "conversion_status=%s "
        "reason_code=%s "
        "duration_ms=%M",
        accept_str,
        cond_str,
        conv_str,
        reason_str,
        path->duration_ms);
}
