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
 * The endpoint is gated by the markdown_diagnostics directive (on/off),
 * loopback-only peer validation, and native NGINX access-phase directives
 * (allow/deny).
 *
 * Requirement: REQ-0700-OPERABILITY-001
 * Risk Pack: dynamic-config-hot-reload
 */

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>
#include <string.h>
#include <time.h>

#include "ngx_http_markdown_diagnostics.h"
#include "markdown_reason_meta.h"
#include "ngx_http_markdown_dynconf_snapshot.h"
#include "ngx_http_markdown_filter_module.h"

/* Unit-test translation units may include system headers before this file. */
#if !defined(_WIN32)
extern struct tm *gmtime_r(const time_t *, struct tm *);
#endif

/*
 * Forward-declare the FFI wrapper for reason code string lookup.
 */
ngx_int_t ngx_http_markdown_get_reason_code_str(uint32_t code,
    ngx_str_t *out_str);


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
 * Process-global flag indicating that at least one location enabled the
 * diagnostics endpoint (markdown_diagnostics on).  Set at configuration
 * parse time via ngx_http_markdown_diagnostics_enable_recording() and read
 * once at worker init to decide whether the recent-decisions ring should
 * record.  Recording is gated so the request path stays free of ring writes
 * when diagnostics is never configured.
 */
static ngx_flag_t  ngx_http_markdown_g_diag_recording_requested = 0;


/*
 * Forward declarations for static helper functions.
 */
static ngx_int_t ngx_http_markdown_diagnostics_check_access(
    ngx_http_request_t *r);
static ngx_int_t ngx_http_markdown_diagnostics_build_json(
    ngx_http_request_t *r, ngx_buf_t *b);
static size_t ngx_http_markdown_diagnostics_json_size(
    const ngx_http_markdown_diag_state_t *state);
static ngx_int_t ngx_http_markdown_diag_json_string(
    u_char **pos, const u_char *last, const u_char *value, size_t len);
static ngx_int_t ngx_http_markdown_diag_json_control(
    u_char **pos, const u_char *last, u_char value);
static ngx_int_t ngx_http_markdown_diag_masked_keys(
    u_char **pos, const u_char *last, ngx_uint_t mask);
static ngx_int_t ngx_http_markdown_diag_render_dynconf(
    u_char **pos, u_char *last,
    const ngx_http_markdown_diag_dynconf_t *dynconf);
static const char *ngx_http_markdown_diag_outcome(ngx_int_t code);
static const char *ngx_http_markdown_diag_decision_stage(ngx_int_t code);
static const char *ngx_http_markdown_diag_error_origin(ngx_int_t code);


/*
 * Validate ring buffer invariants for safe iteration.
 *
 * Centralizes the defensive checks that both the JSON sizing logic and
 * the iteration logic must agree on.  Returns the number of valid entries
 * that can be safely iterated, or 0 if the ring state is invalid or empty.
 *
 * When the ring exists (entries != NULL) but invariants are violated,
 * sets *invalid to 1 so callers can distinguish "empty" from "broken".
 *
 * Invariants checked:
 *   - state and entries pointer are non-NULL
 *   - capacity is in (0, NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY]
 *   - count <= capacity (no overcount)
 *   - head < capacity (valid write position)
 *   - count <= NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY (double-bounded count)
 *
 * Parameters:
 *   state - diagnostics state to validate (may be NULL)
 *   invalid - if non-NULL, set to 1 when ring exists but is invalid
 *
 * Returns:
 *   Number of valid entries to iterate (state->ring.count), or 0 if
 *   the ring is empty, NULL, or any invariant is violated.
 */
static ngx_inline ngx_uint_t
ngx_http_markdown_diag_ring_valid_count(
    const ngx_http_markdown_diag_state_t *state,
    ngx_int_t *invalid)
{
    if (invalid != NULL) {
        *invalid = 0;
    }

    if (state == NULL || state->ring.entries == NULL) {
        return 0;
    }

    if (state->ring.capacity == 0
        || state->ring.capacity > NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY
        || state->ring.count > state->ring.capacity
        || state->ring.head >= state->ring.capacity
        || state->ring.count > NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY)
    {
        if (invalid != NULL) {
            *invalid = 1;
        }
        return 0;
    }

    return state->ring.count;
}


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
    const char *outcome;
    const char *error_origin;

    outcome = ngx_http_markdown_diag_outcome(reason_code);
    error_origin = (outcome[0] == 'f' || outcome[0] == 'a')
        ? ngx_http_markdown_diag_error_origin(reason_code) : NULL;
    ngx_http_markdown_diagnostics_record_classified(
        state, outcome, ngx_http_markdown_diag_decision_stage(reason_code),
        reason_code, error_origin, duration_ms);
}


void
ngx_http_markdown_diagnostics_record_reason(
    ngx_http_markdown_diag_state_t *state,
    const u_char *reason,
    size_t reason_len,
    const char *error_category,
    ngx_msec_t duration_ms)
{
    ngx_http_markdown_diagnostics_record_reason_at_stage(
        state, reason, reason_len, NULL, error_category, duration_ms);
}


void
ngx_http_markdown_diagnostics_record_reason_at_stage(
    ngx_http_markdown_diag_state_t *state,
    const u_char *reason,
    size_t reason_len,
    const char *stage,
    const char *error_category,
    ngx_msec_t duration_ms)
{
    ngx_int_t    reason_code;
    const char  *outcome;
    const char  *error_origin;

    if (reason == NULL && reason_len != 0) {
        return;
    }

    reason_code = ngx_http_markdown_diagnostics_reason_to_code(
        reason, reason_len);
    outcome = ngx_http_markdown_diag_outcome(reason_code);
    /*
     * ErrorOrigin is canonical registry metadata, not a caller-selected
     * coarse category. Keep the legacy parameter for the frozen API while
     * deriving the emitted value from the resolved reason code.
     */
    (void) error_category;
    error_origin = (outcome[0] == 'f' || outcome[0] == 'a')
        ? ngx_http_markdown_diag_error_origin(reason_code) : NULL;
    ngx_http_markdown_diagnostics_record_classified(
        state,
        outcome,
        stage != NULL ? stage
                      : ngx_http_markdown_diag_decision_stage(reason_code),
        reason_code,
        error_origin,
        duration_ms);
}


void
ngx_http_markdown_diagnostics_record_classified(
    ngx_http_markdown_diag_state_t *state,
    const char *outcome,
    const char *stage,
    ngx_int_t reason_code,
    const char *error_origin,
    ngx_msec_t duration_ms)
{
    ngx_http_markdown_diag_decision_t  *entry;

    if (state == NULL || state->ring.entries == NULL || !state->enabled) {
        return;
    }

    entry = &state->ring.entries[state->ring.head];
    /* Store wall-clock seconds so the v1 endpoint can emit RFC 3339 time. */
    entry->timestamp = (ngx_msec_t) ngx_time();
    entry->outcome = outcome != NULL ? outcome : "failed_closed";
    entry->stage = stage != NULL ? stage : "delivery";
    entry->reason_code = reason_code;
    entry->error_origin = error_origin;
    entry->duration_ms = duration_ms;

    state->ring.head = (state->ring.head + 1) % state->ring.capacity;

    if (state->ring.count < state->ring.capacity) {
        state->ring.count++;
    }
}


/*
 * Request that the per-worker diagnostics ring record decisions.
 *
 * Called from the markdown_diagnostics directive handler at configuration
 * parse time when a location enables the diagnostics endpoint.  The actual
 * ring allocation and enabling happens later in
 * ngx_http_markdown_diagnostics_init_worker() (worker init), because the
 * cycle pool is the correct allocation arena for per-worker state.
 */
void
ngx_http_markdown_diagnostics_enable_recording(void)
{
    ngx_http_markdown_g_diag_recording_requested = 1;
}


/*
 * Reset the configuration-cycle recording request flag.
 *
 * NGINX may parse a fresh configuration in a long-lived master process during
 * reload.  The parse-time request flag must therefore start false for each
 * cycle, otherwise a previous config with markdown_diagnostics on would keep
 * future workers recording even after the endpoint is removed.
 */
void
ngx_http_markdown_diagnostics_reset_recording_request(void)
{
    ngx_http_markdown_g_diag_recording_requested = 0;
}


/*
 * Initialize the per-worker diagnostics ring during worker startup.
 *
 * Allocates the global ring from the cycle pool and enables recording iff
 * a location requested diagnostics (markdown_diagnostics on).  When no
 * location enabled diagnostics, this is a no-op so the request path performs
 * no ring writes.
 *
 * Parameters:
 *   cycle - NGINX cycle (provides the per-worker allocation pool and log)
 *
 * Returns:
 *   NGX_OK on success or when diagnostics is not requested (no-op);
 *   NGX_ERROR if ring allocation fails.
 */
ngx_int_t
ngx_http_markdown_diagnostics_init_worker(struct ngx_cycle_s *cycle)
{
    if (!ngx_http_markdown_g_diag_recording_requested) {
        return NGX_OK;
    }

    if (cycle == NULL || cycle->pool == NULL) {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_diagnostics_init(
            &ngx_http_markdown_g_diag_state, cycle->pool, 0)
        != NGX_OK)
    {
        ngx_log_error(NGX_LOG_ERR, cycle->log, 0,
            "markdown: failed to allocate diagnostics ring buffer");
        return NGX_ERROR;
    }

    ngx_http_markdown_g_diag_state.enabled = 1;

    ngx_log_error(NGX_LOG_INFO, cycle->log, 0,
        "markdown: diagnostics recent-decisions ring initialized "
        "(capacity %ui)",
        ngx_http_markdown_g_diag_state.ring.capacity);

    return NGX_OK;
}


/*
 * Whether the per-worker diagnostics ring is actively recording.
 *
 * Returns 1 when the global ring has been initialized and enabled, 0
 * otherwise.  Used by the decision-path logger to (a) decide whether to
 * record a decision and (b) apply the diagnostics-enabled verbosity
 * override.
 */
ngx_int_t
ngx_http_markdown_diagnostics_recording_active(void)
{
    return (ngx_http_markdown_g_diag_initialized
            && ngx_http_markdown_g_diag_state.enabled) ? 1 : 0;
}


/*
 * HTTP content handler for the diagnostics endpoint.
 *
 * Enforces access control before method handling, then:
 *   - GET/HEAD: builds and sends the full diagnostics JSON response
 *   - Other methods: returns 405 Not Allowed; the endpoint has no mutation
 *     operation.  Keeping access control first prevents unauthorized callers
 *     from learning handler behavior through the method-rejection branch.
 *
 * The response Content-Type is application/json.
 *
 * Parameters:
 *   r - HTTP request
 *
 * Returns:
 *   NGX_OK on success, NGX_HTTP_FORBIDDEN on access denial,
 *   NGX_HTTP_INTERNAL_SERVER_ERROR on build failure.  Method errors return
 *   NGX_OK after sending a short 405 response body.
 */
static ngx_int_t
ngx_http_markdown_diagnostics_method_not_allowed(ngx_http_request_t *r)
{
    static u_char body[] =
        "Method Not Allowed. Use GET or HEAD; rollback is available through "
        "the dynamic-config file watcher.\n";
    ngx_table_elt_t  *allow_hdr;
    ngx_buf_t    *b;
    ngx_chain_t   out;
    ngx_int_t     rc;

    /*
     * Discard any request body before sending the 405 response so a
     * non-GET/HEAD client body is not left unread.
     */
    rc = ngx_http_discard_request_body(r);
    if (rc != NGX_OK) {
        return rc;
    }

    /*
     * Add Allow: GET, HEAD header per RFC 9110 Section 15.5.6.
     * NGINX does not automatically add the Allow header for 405
     * responses from content handlers, so we set it explicitly.
     *
     * The header and the response body are constructed transactionally:
     * if the Allow header allocation fails, abort with 500 instead of
     * emitting a 405 that violates the Allow contract.
     */
    allow_hdr = ngx_list_push(&r->headers_out.headers);
    if (allow_hdr == NULL) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }
    allow_hdr->hash = 1;
    ngx_str_set(&allow_hdr->key, "Allow");
    ngx_str_set(&allow_hdr->value, "GET, HEAD");

    b = ngx_pcalloc(r->pool, sizeof(ngx_buf_t));
    if (b == NULL) {
        return NGX_HTTP_INTERNAL_SERVER_ERROR;
    }

    b->pos = body;
    b->last = body + sizeof(body) - 1;
    b->memory = 1;
    b->last_in_chain = 1;
    b->last_buf = (r == r->main) ? 1 : 0;

    r->headers_out.status = NGX_HTTP_NOT_ALLOWED;
    r->headers_out.content_type_len = sizeof("text/plain") - 1;
    ngx_str_set(&r->headers_out.content_type, "text/plain");
    r->headers_out.content_type_lowcase = NULL;
    r->headers_out.content_length_n = b->last - b->pos;

    rc = ngx_http_send_header(r);
    if (rc == NGX_ERROR || rc > NGX_OK) {
        return rc;
    }

    out.buf = b;
    out.next = NULL;
    return ngx_http_output_filter(r, &out);
}

ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    ngx_int_t     rc;
    ngx_buf_t    *b;
    ngx_chain_t   out;

    /*
     * Check access before method handling so denied requests do not disclose
     * the endpoint's 405 behavior or receive an Allow header.
     */
    rc = ngx_http_markdown_diagnostics_check_access(r);
    if (rc != NGX_OK) {
        r->headers_out.status = (ngx_uint_t) rc;
        return rc;
    }

    /* Only allow read-only GET and HEAD requests. */
    if (!(r->method & (NGX_HTTP_GET | NGX_HTTP_HEAD))) {
        /*
         * The 405 response (including the mandatory Allow: GET, HEAD
         * header per RFC 9110 Section 15.5.6) is constructed
         * transactionally inside method_not_allowed: any allocation
         * failure yields 500 instead of an incomplete 405.
         */
        return ngx_http_markdown_diagnostics_method_not_allowed(r);
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
 * Diagnostics are loopback-only by default. Native NGINX access-phase
 * directives (allow/deny/auth_basic/satisfy) may add restrictions but cannot
 * broaden the peer boundary. The content handler runs after that phase.
 *
 * Operators should use standard NGINX access directives:
 *
 *   location /nginx-markdown/diagnostics {
 *       markdown_diagnostics on;
 *       allow 127.0.0.1;
 *       allow ::1;
 *       deny all;
 *   }
 *
 * Unknown address families and missing peer addresses are denied.
 *
 * Parameters:
 *   r - HTTP request
 *
 * Returns:
 *   NGX_OK             - access permitted
 *   NGX_HTTP_FORBIDDEN - access denied (no sockaddr)
 */
static ngx_int_t
ngx_http_markdown_diagnostics_check_access(ngx_http_request_t *r)
{
    if (r == NULL || r->connection == NULL) {
        return NGX_HTTP_FORBIDDEN;
    }

    if (r->connection->sockaddr == NULL) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
            "markdown: no client address, "
            "denying diagnostics access");
        return NGX_HTTP_FORBIDDEN;
    }

    if (r->connection->sockaddr->sa_family == AF_INET) {
        const struct sockaddr_in *sin =
            (const struct sockaddr_in *) r->connection->sockaddr;

        /* Accept any address in 127.0.0.0/8, not only INADDR_LOOPBACK. */
        if ((ntohl(sin->sin_addr.s_addr) & 0xFF000000U) != 0x7F000000U) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                "markdown: access denied from non-loopback IPv4 address");
            return NGX_HTTP_FORBIDDEN;
        }
    }
#if (NGX_HAVE_INET6)
    else if (r->connection->sockaddr->sa_family == AF_INET6) {
        const struct sockaddr_in6 *sin6 =
            (const struct sockaddr_in6 *) r->connection->sockaddr;

        if (!IN6_IS_ADDR_LOOPBACK(&sin6->sin6_addr)) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                "markdown: access denied from non-localhost IPv6 address");
            return NGX_HTTP_FORBIDDEN;
        }
    }
#endif
    else {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
            "markdown: access denied from unknown address family");
        return NGX_HTTP_FORBIDDEN;
    }

    return NGX_OK;
}


/*
 * Build the JSON diagnostics response.
 *
 * Constructs a JSON document with seven top-level fields:
 *   - schema_version
 *   - product_version
 *   - worker
 *   - build
 *   - configuration
 *   - runtime
 *   - recent_decisions
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
/*
 * Diagnostics v2 is deliberately rendered in one bounded pass.  The
 * endpoint is a strict machine-readable contract: no compatibility fields
 * are emitted and every string is either a closed enum or copied through the
 * bounded dynconf error buffer.
 */
#ifndef NGX_HTTP_MARKDOWN_SOURCE_SHA
#define NGX_HTTP_MARKDOWN_SOURCE_SHA \
    "0000000000000000000000000000000000000000"
#endif

#ifndef NGX_HTTP_MARKDOWN_RUST_VERSION
#define NGX_HTTP_MARKDOWN_RUST_VERSION "unknown"
#endif

static const char *
ngx_http_markdown_diag_dynconf_state_name(ngx_uint_t state)
{
    switch (state) {
    case NGX_HTTP_MARKDOWN_DIAG_DYNCONF_NO_FILE:
        return "no_file";
    case NGX_HTTP_MARKDOWN_DIAG_DYNCONF_INVALID_NO_LKG:
        return "invalid_without_lkg";
    case NGX_HTTP_MARKDOWN_DIAG_DYNCONF_ACTIVE:
        return "active";
    case NGX_HTTP_MARKDOWN_DIAG_DYNCONF_LKG_PRESERVED:
        return "lkg_preserved";
    default:
        return "disabled";
    }
}


static const char *
ngx_http_markdown_diag_bool(ngx_flag_t value)
{
    return value ? "on" : "off";
}


static const char *
ngx_http_markdown_diag_log_name(ngx_uint_t value)
{
    switch (value) {
    case NGX_HTTP_MARKDOWN_LOG_ERROR:
        return "error";
    case NGX_HTTP_MARKDOWN_LOG_WARN:
        return "warn";
    case NGX_HTTP_MARKDOWN_LOG_DEBUG:
        return "debug";
    default:
        return "info";
    }
}


static const char *
ngx_http_markdown_diag_error_name(ngx_uint_t policy, ngx_uint_t status)
{
    if (policy == NGX_HTTP_MARKDOWN_ON_ERROR_PASS) {
        return "pass";
    }
    if (status == 429) {
        return "status 429";
    }
    if (status == 503) {
        return "status 503";
    }
    return "fail_closed";
}


static const char *
ngx_http_markdown_diag_source_name(ngx_uint_t source)
{
    switch (source) {
    case NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF:
        return "dynconf";
    case NGX_HTTP_MARKDOWN_PROVENANCE_REQUEST_VARIABLE:
        return "request_variable";
    default:
        return "static";
    }
}


static u_char *
ngx_http_markdown_diag_time(u_char *p, u_char *last, ngx_msec_t stamp)
{
    time_t       value;
    struct tm    tm_value;

    value = (time_t) stamp;
    if (gmtime_r(&value, &tm_value) == NULL) {
        time_t epoch;

        epoch = 0;
        if (gmtime_r(&epoch, &tm_value) == NULL) {
            /* Preserve a valid RFC 3339 epoch even if libc is unavailable. */
            ngx_memzero(&tm_value, sizeof(tm_value));
            tm_value.tm_year = 70;
            tm_value.tm_mday = 1;
        }
    }

    return ngx_slprintf(p, last,
        "%04d-%02d-%02dT%02d:%02d:%02dZ",
        tm_value.tm_year + 1900, tm_value.tm_mon + 1, tm_value.tm_mday,
        tm_value.tm_hour, tm_value.tm_min, tm_value.tm_sec);
}


static ngx_int_t
ngx_http_markdown_diag_json_put_byte(
    u_char **pos, const u_char *last, u_char value)
{
    u_char  *current;

    if (pos == NULL || last == NULL) {
        return NGX_ERROR;
    }

    current = *pos;
    if (current == NULL || current >= last) {
        return NGX_ERROR;
    }

    *current = value;
    *pos = current + 1;

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_diag_json_control(
    u_char **pos, const u_char *last, u_char value)
{
    static const u_char  hex[] = "0123456789abcdef";
    u_char               escaped[6];

    escaped[0] = '\\';
    escaped[1] = 'u';
    escaped[2] = '0';
    escaped[3] = '0';
    escaped[4] = hex[value >> 4];
    escaped[5] = hex[value & 0x0f];

    for (size_t i = 0; i < sizeof(escaped); i++) {
        if (ngx_http_markdown_diag_json_put_byte(
                pos, last, escaped[i]) != NGX_OK)
        {
            return NGX_ERROR;
        }
    }

    return NGX_OK;
}


/* Append one length-bounded JSON string, escaping syntax and controls. */
static ngx_int_t
ngx_http_markdown_diag_json_string(
    u_char **pos, const u_char *last, const u_char *value, size_t len)
{
    u_char               ch;

    if (pos == NULL || *pos == NULL || last == NULL || value == NULL
        || *pos > last)
    {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_diag_json_put_byte(pos, last, '"') != NGX_OK) {
        return NGX_ERROR;
    }

    for (size_t i = 0; i < len; i++) {
        ch = value[i];

        if (ch == '"' || ch == '\\') {
            if (ngx_http_markdown_diag_json_put_byte(
                    pos, last, '\\') != NGX_OK
                || ngx_http_markdown_diag_json_put_byte(
                    pos, last, ch) != NGX_OK)
            {
                return NGX_ERROR;
            }
        } else if (ch < 0x20) {
            if (ngx_http_markdown_diag_json_control(pos, last, ch)
                != NGX_OK)
            {
                return NGX_ERROR;
            }
        } else {
            if (ngx_http_markdown_diag_json_put_byte(
                    pos, last, ch) != NGX_OK)
            {
                return NGX_ERROR;
            }
        }
    }

    if (ngx_http_markdown_diag_json_put_byte(pos, last, '"') != NGX_OK) {
        return NGX_ERROR;
    }

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_diag_masked_keys(
    u_char **pos, const u_char *last, ngx_uint_t mask)
{
    static const struct {
        ngx_uint_t bit;
        const char *name;
    } fields[] = {
        { NGX_HTTP_MARKDOWN_DIAG_MASK_FILTER, "filter" },
        { NGX_HTTP_MARKDOWN_DIAG_MASK_PRUNE_NOISE, "prune_noise" },
        { NGX_HTTP_MARKDOWN_DIAG_MASK_LOG_VERBOSITY, "log_verbosity" },
        { NGX_HTTP_MARKDOWN_DIAG_MASK_ERROR_POLICY, "error_policy" },
        { NGX_HTTP_MARKDOWN_DIAG_MASK_STREAMING_BUFFER, "streaming_buffer" }
    };
    ngx_flag_t emitted;

    if (pos == NULL || *pos == NULL || last == NULL || *pos > last) {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_diag_json_put_byte(pos, last, '[') != NGX_OK) {
        return NGX_ERROR;
    }

    emitted = 0;
    for (ngx_uint_t i = 0; i < sizeof(fields) / sizeof(fields[0]); i++) {
        if ((mask & fields[i].bit) == 0) {
            continue;
        }
        if (emitted
            && ngx_http_markdown_diag_json_put_byte(
                   pos, last, ',') != NGX_OK)
        {
            return NGX_ERROR;
        }
        if (ngx_http_markdown_diag_json_string(
                pos, last, (const u_char *) fields[i].name,
                strlen(fields[i].name)) != NGX_OK)
        {
            return NGX_ERROR;
        }
        emitted = 1;
    }

    return ngx_http_markdown_diag_json_put_byte(pos, last, ']');
}


/*
 * Reason metadata is now generated from reason_registry.toml by
 * tools/reason-codegen/generate.py into markdown_reason_meta.h.
 * The generated table includes an unknown sentinel at index
 * MARKDOWN_REASON_META_COUNT with safe defaults (failed_closed /
 * delivery / internal) — never the "converted" outcome that the
 * former hand-written table returned for invalid codes.
 */

static const markdown_reason_meta_t *
ngx_http_markdown_diag_reason_meta_for(ngx_int_t code)
{
    if (code < 0 || code >= MARKDOWN_REASON_META_COUNT) {
        return &markdown_reason_meta[MARKDOWN_REASON_META_COUNT];
    }
    return &markdown_reason_meta[code];
}

static const char *
ngx_http_markdown_diag_outcome(ngx_int_t code)
{
    return ngx_http_markdown_diag_reason_meta_for(code)->outcome;
}

static const char *
ngx_http_markdown_diag_decision_stage(ngx_int_t code)
{
    return ngx_http_markdown_diag_reason_meta_for(code)->stage;
}

static const char *
ngx_http_markdown_diag_error_origin(ngx_int_t code)
{
    return ngx_http_markdown_diag_reason_meta_for(code)->error_origin;
}
static ngx_int_t
ngx_http_markdown_diagnostics_fmt_decisions(
    u_char **pos, u_char *last,
    const ngx_http_markdown_diag_state_t *state)
{
    ngx_uint_t  count;

    if (pos == NULL || *pos == NULL || last == NULL || *pos > last) {
        return NGX_ERROR;
    }

    count = ngx_http_markdown_diag_ring_valid_count(state, NULL);
    for (ngx_uint_t i = 0; i < count; i++) {
        ngx_uint_t  idx;
        ngx_int_t   code;
        ngx_str_t   reason;
        const char *outcome;
        const char *stage;
        const char *error_origin;

        /* Reserve one complete entry before writing any of its fields. */
        if ((size_t) (last - *pos)
            < NGX_HTTP_MARKDOWN_DIAG_JSON_DECISION_SIZE)
        {
            return NGX_ERROR;
        }

        if (state->ring.head >= i + 1) {
            idx = state->ring.head - (i + 1);
        } else {
            idx = state->ring.capacity - ((i + 1) - state->ring.head);
        }

        code = state->ring.entries[idx].reason_code;
        if (ngx_http_markdown_get_reason_code_str((uint32_t) code, &reason)
            != NGX_OK || reason.data == NULL || reason.len == 0)
        {
            ngx_str_set(&reason, "internal");
        }

        outcome = state->ring.entries[idx].outcome != NULL
            ? state->ring.entries[idx].outcome : "failed_closed";
        stage = state->ring.entries[idx].stage != NULL
            ? state->ring.entries[idx].stage : "delivery";
        error_origin = state->ring.entries[idx].error_origin;

        if (i != 0) {
            *pos = ngx_slprintf(*pos, last, ",");
        }
        *pos = ngx_slprintf(*pos, last, "{"
            "\"timestamp\":\"");
        *pos = ngx_http_markdown_diag_time(
            *pos, last, state->ring.entries[idx].timestamp);
        *pos = ngx_slprintf(*pos, last,
            "\",\"outcome\":\"%s\",\"stage\":\"%s\","
            "\"reason\":",
            outcome, stage);
        if (ngx_http_markdown_diag_json_string(
                pos, last, reason.data, reason.len) != NGX_OK)
        {
            return NGX_ERROR;
        }
        *pos = ngx_slprintf(*pos, last, ",\"error_origin\":");
        if (error_origin == NULL) {
            *pos = ngx_slprintf(*pos, last, "null");
        } else {
            *pos = ngx_slprintf(*pos, last, "\"%s\"", error_origin);
        }
        *pos = ngx_slprintf(*pos, last,
            ",\"duration_ms\":%M}", state->ring.entries[idx].duration_ms);
    }

    return (*pos < last) ? NGX_OK : NGX_ERROR;
}


/*
 * Render the dynconf JSON fragment (everything after "state" through the
 * trailing fields) into the output buffer.  Extracted from build_json to
 * keep the caller's Cognitive Complexity under the S3776 threshold.
 *
 * Layout per dynconf.state:
 *   ACTIVE | LKG_PRESERVED   generation, source/active/lkg digests,
 *                            last_success, last_error (LKG_PRESERVED only)
 *   INVALID_NO_LKG (error)   null fields + last_error when present
 *   other                    null fields, null last_error
 *
 * Returns NGX_OK on success, NGX_ERROR on a truncated/invalid write.
 */
static ngx_int_t
ngx_http_markdown_diag_render_dynconf(
    u_char **pos, u_char *last,
    const ngx_http_markdown_diag_dynconf_t *dynconf)
{
    if (pos == NULL || *pos == NULL || last == NULL || dynconf == NULL
        || *pos > last)
    {
        return NGX_ERROR;
    }

    if (dynconf->state == NGX_HTTP_MARKDOWN_DIAG_DYNCONF_ACTIVE
        || dynconf->state == NGX_HTTP_MARKDOWN_DIAG_DYNCONF_LKG_PRESERVED)
    {
        *pos = ngx_slprintf(*pos, last,
            "\"generation\":%ui,\"source_digest\":\"%s\","
            "\"active_digest\":\"%s\",\"lkg_digest\":",
            dynconf->generation, dynconf->source_digest,
            dynconf->active_digest);
        if (dynconf->lkg_valid && dynconf->lkg_digest[0] != '\0') {
            *pos = ngx_slprintf(*pos, last, "\"%s\"", dynconf->lkg_digest);
        } else {
            *pos = ngx_slprintf(*pos, last, "null");
        }
        *pos = ngx_slprintf(*pos, last, ",\"last_success\":");
        if (dynconf->has_last_success) {
            *pos = ngx_slprintf(*pos, last, "\"");
            *pos = ngx_http_markdown_diag_time(*pos, last,
                (ngx_msec_t) dynconf->last_success);
            *pos = ngx_slprintf(*pos, last, "\"");
        } else {
            *pos = ngx_slprintf(*pos, last, "null");
        }
        *pos = ngx_slprintf(*pos, last, ",\"last_error\":");
        if (dynconf->state == NGX_HTTP_MARKDOWN_DIAG_DYNCONF_LKG_PRESERVED
            && dynconf->last_error_len > 0)
        {
            if (ngx_http_markdown_diag_json_string(
                    pos, last, dynconf->last_error,
                    dynconf->last_error_len) != NGX_OK)
            {
                return NGX_ERROR;
            }
        } else {
            *pos = ngx_slprintf(*pos, last, "null");
        }
        return NGX_OK;
    }

    if (dynconf->state == NGX_HTTP_MARKDOWN_DIAG_DYNCONF_INVALID_NO_LKG
        && dynconf->last_error_len > 0)
    {
        *pos = ngx_slprintf(*pos, last,
            "\"generation\":null,\"source_digest\":null,"
            "\"active_digest\":null,\"lkg_digest\":null,"
            "\"last_success\":null,\"last_error\":");
        return ngx_http_markdown_diag_json_string(
            pos, last, dynconf->last_error, dynconf->last_error_len);
    }

    *pos = ngx_slprintf(*pos, last,
        "\"generation\":null,\"source_digest\":null,"
        "\"active_digest\":null,\"lkg_digest\":null,"
        "\"last_success\":null,\"last_error\":null");
    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_diagnostics_build_json(ngx_http_request_t *r,
    ngx_buf_t *b)
{
    const ngx_http_markdown_conf_t  *conf;
    const ngx_http_markdown_diag_state_t  *state;
    ngx_http_markdown_diag_dynconf_t dynconf;
    ngx_http_markdown_diag_effective_t effective;
    ngx_http_markdown_diag_metrics_t metrics;
    u_char *buf;
    u_char *p;
    u_char *last;
    size_t buf_size;
    size_t streaming_buffer;
    u_char static_digest[72];
    const char *dynconf_state;

    state = ngx_http_markdown_diagnostics_get_state();
    buf_size = ngx_http_markdown_diagnostics_json_size(state);
    if (buf_size == 0) {
        return NGX_ERROR;
    }

    buf = ngx_palloc(r->pool, buf_size);
    if (buf == NULL) {
        return NGX_ERROR;
    }
    p = buf;
    last = buf + buf_size;
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    ngx_http_markdown_diagnostics_get_dynconf_state(&dynconf);
    ngx_http_markdown_diagnostics_get_effective(conf, &effective);
    ngx_http_markdown_diagnostics_collect_metrics(&metrics);
    if (ngx_http_markdown_diagnostics_get_static_digest(
            r, r->pool, static_digest, sizeof(static_digest)) != NGX_OK)
    {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: failed to compute static configuration digest");
        return NGX_ERROR;
    }

    streaming_buffer = effective.streaming_buffer;

    dynconf_state = ngx_http_markdown_diag_dynconf_state_name(dynconf.state);
    p = ngx_slprintf(p, last,
        "{\"schema_version\":2,\"product_version\":\"0.9.2\","
        "\"worker\":{\"pid\":%P,\"scope\":\"worker-local\"},"
        "\"build\":{\"source_sha\":\"%s\","
        "\"nginx_version\":\"%s\",\"rust_version\":\"%s\","
        "\"features\":[",
        ngx_pid, NGX_HTTP_MARKDOWN_SOURCE_SHA, NGINX_VERSION,
        NGX_HTTP_MARKDOWN_RUST_VERSION);
#ifdef MARKDOWN_STREAMING_ENABLED
    p = ngx_slprintf(p, last, "\"dynconf\",\"streaming\"");
#else
    p = ngx_slprintf(p, last, "\"dynconf\"");
#endif
    p = ngx_slprintf(p, last,
        "]},\"configuration\":{\"static_digest\":\"%s\","
        "\"dynconf\":{\"state\":\"%s\",",
        static_digest, dynconf_state);

    if (ngx_http_markdown_diag_render_dynconf(&p, last, &dynconf)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    p = ngx_slprintf(p, last, ",\"masked_keys\":");
    if (ngx_http_markdown_diag_masked_keys(
            &p, last, dynconf.masked_fields) != NGX_OK)
    {
        return NGX_ERROR;
    }

    p = ngx_slprintf(p, last,
        "},\"effective\":{\"filter\":\"%s\","
        "\"prune_noise\":\"%s\",\"log_verbosity\":\"%s\","
        "\"error_policy\":\"%s\",\"streaming_buffer\":%uz},"
        "\"effective_sources\":{\"filter\":\"%s\","
        "\"prune_noise\":\"%s\",\"log_verbosity\":\"%s\","
        "\"error_policy\":\"%s\",\"streaming_buffer\":\"%s\"}},"
        "\"runtime\":{\"inflight\":%uA,\"pending_output\":%uA,"
        "\"module_metrics\":{\"streaming_requests_total\":%uA,"
        "\"precommit_failopen_total\":%uA,"
        "\"zero_copy_output_total\":%uA,"
        "\"copied_output_total\":%uA}},"
        "\"recent_decisions\":[",
        ngx_http_markdown_diag_bool(effective.filter),
        ngx_http_markdown_diag_bool(effective.prune_noise),
        ngx_http_markdown_diag_log_name(effective.log_verbosity),
        ngx_http_markdown_diag_error_name(effective.error_policy,
                                           effective.error_status),
        streaming_buffer,
        ngx_http_markdown_diag_source_name(effective.filter_source),
        ngx_http_markdown_diag_source_name(effective.prune_noise_source),
        ngx_http_markdown_diag_source_name(effective.log_verbosity_source),
        ngx_http_markdown_diag_source_name(effective.error_policy_source),
        ngx_http_markdown_diag_source_name(effective.streaming_buffer_source),
        metrics.inflight, metrics.pending_output,
        metrics.streaming_requests_total, metrics.precommit_failopen_total,
        metrics.zero_copy_output_total, metrics.copied_output_total);
    if (ngx_http_markdown_diagnostics_fmt_decisions(&p, last, state)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    p = ngx_slprintf(p, last, "]}\n");
    if (p >= last) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: diagnostics v2 JSON truncated");
        return NGX_ERROR;
    }

    b->pos = buf;
    b->last = p;
    b->start = buf;
    b->end = buf + buf_size;
    return NGX_OK;
}


static size_t
ngx_http_markdown_diagnostics_json_size(
    const ngx_http_markdown_diag_state_t *state)
{
    ngx_uint_t  decision_count;
    ngx_int_t   invalid;

    /*
     * Use the centralized ring validation helper.
     *
     * Sizing contract:
     *   NGX_HTTP_MARKDOWN_DIAG_JSON_BASE_SIZE covers the fixed JSON
     *   envelope (schema_version, product_version, worker, build,
     *   configuration, runtime, recent_decisions, braces, keys, and
     *   whitespace).
     *   NGX_HTTP_MARKDOWN_DIAG_JSON_DECISION_SIZE covers one compact
     *   recent_decisions entry including separators and indentation.
     *   The total must be >= the actual rendered output; truncation
     *   is detected at the end of build_json and returns NGX_ERROR.
     */
    decision_count = ngx_http_markdown_diag_ring_valid_count(state, &invalid);

    if (invalid) {
        return 0;
    }

    return NGX_HTTP_MARKDOWN_DIAG_JSON_BASE_SIZE
           + (6 * (sizeof(((ngx_http_markdown_diag_dynconf_t *) 0)
                       ->last_error) - 1))
           + ((size_t) decision_count
              * NGX_HTTP_MARKDOWN_DIAG_JSON_DECISION_SIZE);
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
     * Record the decision in the recent-decisions ring (best-effort).
     * The ring is only active when a location enabled markdown_diagnostics;
     * otherwise this is a cheap no-op.  Recorded unconditionally (regardless
     * of log verbosity) so the diagnostics endpoint reflects all decisions,
     * not just the ones that were logged.
     */
    if (ngx_http_markdown_diagnostics_recording_active()) {
        ngx_http_markdown_diagnostics_record_reason_at_stage(
            ngx_http_markdown_diagnostics_get_state(),
            (const u_char *) path->reason_code,
            path->reason_code != NULL
                ? strlen(path->reason_code) : 0,
            path->stage,
            path->error_category,
            path->duration_ms);
    }

    /*
     * Verbosity gating:
     * - LOG_DEBUG: NGX_LOG_DEBUG (all outcomes)
     * - LOG_INFO or lower + success: NGX_LOG_INFO
     * - LOG_INFO or lower + failure: NGX_LOG_WARN
     * - Non-failure outcomes at LOG_WARN or LOG_ERROR are suppressed entirely
     *   (unless the diagnostics endpoint is enabled, see override below)
     */
    if (effective_verbosity <= NGX_HTTP_MARKDOWN_LOG_WARN
        && !is_failure
        && !ngx_http_markdown_diagnostics_recording_active())
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
        "reason_code=%s error_category=%s "
        "duration_ms=%M",
        accept_str,
        cond_str,
        conv_str,
        reason_str,
        path->error_category != NULL ? path->error_category : "-",
        path->duration_ms);
}
