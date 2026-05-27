/*
 * NGINX Markdown Filter Module - Diagnostics Endpoint
 *
 * Provides runtime introspection for the markdown filter module:
 * configuration snapshot, recent decision history, metrics snapshot,
 * and dynamic configuration state.
 *
 * Requirement: REQ-0700-OPERABILITY-001
 * Risk Pack: dynamic-config-hot-reload
 */

#ifndef _NGX_HTTP_MARKDOWN_DIAGNOSTICS_H_INCLUDED_
#define _NGX_HTTP_MARKDOWN_DIAGNOSTICS_H_INCLUDED_

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>


/*
 * Default ring buffer capacity for recent decisions.
 * Configurable via markdown_diagnostics_capacity directive.
 */
#define NGX_HTTP_MARKDOWN_DIAG_DEFAULT_CAPACITY  100

/*
 * Maximum allowed ring buffer capacity.
 */
#define NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY      10000

/*
 * Diagnostics JSON response sizing.
 *
 * The base size covers config snapshot, metrics, and dynconf sections.  The
 * per-decision estimate covers one compact recent_decisions JSON object plus
 * separators and indentation.
 */
#define NGX_HTTP_MARKDOWN_DIAG_JSON_BASE_SIZE    32768
#define NGX_HTTP_MARKDOWN_DIAG_JSON_DECISION_SIZE 128


/*
 * Single decision record stored in the ring buffer.
 *
 * Captures the outcome of one request's conversion decision
 * for diagnostic introspection.
 */
typedef struct {
    ngx_msec_t    timestamp;       /* Decision time (monotonic ms) */
    ngx_int_t     reason_code;     /* Reason code enum value */
    ngx_msec_t    duration_ms;     /* Processing duration in ms */
} ngx_http_markdown_diag_decision_t;


/*
 * Ring buffer for recent decisions.
 *
 * Fixed-capacity circular buffer allocated from the cycle pool.
 * Entries are overwritten in FIFO order when the buffer is full.
 * Access is not locked; concurrent writes from multiple workers
 * are tolerated (diagnostics is best-effort, not transactional).
 */
typedef struct {
    ngx_http_markdown_diag_decision_t  *entries;  /* Decision records */
    ngx_uint_t                          capacity; /* Total slots */
    ngx_uint_t                          head;     /* Next write pos */
    ngx_uint_t                          count;    /* Valid entries */
} ngx_http_markdown_diag_ring_t;


/*
 * Diagnostics module state.
 *
 * Holds the ring buffer and references needed to produce
 * the diagnostics JSON response.  Allocated once per worker
 * from the cycle pool.
 */
typedef struct {
    ngx_http_markdown_diag_ring_t   ring;      /* Recent decisions */
    ngx_flag_t                      enabled;   /* Diagnostics active */
} ngx_http_markdown_diag_state_t;


/*
 * Initialize the diagnostics subsystem.
 *
 * Allocates the ring buffer from the provided pool with the
 * given capacity.  Must be called once during module init
 * (e.g. postconfiguration or worker init).
 *
 * Parameters:
 *   state    - Diagnostics state to initialize
 *   pool     - Pool for allocation (cycle pool)
 *   capacity - Ring buffer capacity (0 uses default)
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on allocation failure
 */
ngx_int_t ngx_http_markdown_diagnostics_init(
    ngx_http_markdown_diag_state_t *state,
    ngx_pool_t *pool,
    ngx_uint_t capacity);


/*
 * Record a decision in the diagnostics ring buffer.
 *
 * Appends a decision record to the ring buffer.  If the buffer
 * is full, the oldest entry is overwritten.
 *
 * Parameters:
 *   state       - Diagnostics state (must be initialized)
 *   reason_code - Decision reason code
 *   duration_ms - Processing duration in milliseconds
 */
void ngx_http_markdown_diagnostics_record(
    ngx_http_markdown_diag_state_t *state,
    ngx_int_t reason_code,
    ngx_msec_t duration_ms);


/*
 * HTTP content handler for the diagnostics endpoint.
 *
 * Responds with a JSON document containing:
 *   - config_snapshot: current module configuration values
 *   - recent_decisions: ring buffer contents (newest first)
 *   - metrics_snapshot: current metrics counters
 *   - dynconf_state: dynamic configuration watcher state
 *
 * Access control: returns NGX_HTTP_FORBIDDEN if the client
 * address is not a loopback address (127.0.0.1 or ::1).
 * For more granular control, operators should use NGINX's
 * native allow/deny directives in the location block.
 *
 * Parameters:
 *   r - HTTP request
 *
 * Returns:
 *   NGX_OK, NGX_ERROR, or HTTP status code
 */
ngx_int_t ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r);


/*
 * Cleanup the diagnostics subsystem.
 *
 * Resets the ring buffer state.  The actual memory is freed
 * when the owning pool is destroyed.
 *
 * Parameters:
 *   state - Diagnostics state to clean up
 */
void ngx_http_markdown_diagnostics_cleanup(
    ngx_http_markdown_diag_state_t *state);


/*
 * Get the global diagnostics state for this worker.
 *
 * Returns a pointer to the static global diagnostics state
 * that is shared across all requests in this worker process.
 * The state is initialized once during module postconfiguration.
 *
 * Returns:
 *   Pointer to the diagnostics state, or NULL if not initialized
 */
ngx_http_markdown_diag_state_t *
ngx_http_markdown_diagnostics_get_state(void);


/*
 * Metrics snapshot for the diagnostics endpoint.
 *
 * Contains the key counters needed by the diagnostics JSON
 * response.  Populated by ngx_http_markdown_diagnostics_collect_metrics().
 */
typedef struct {
    ngx_atomic_uint_t  conversions_total;
    ngx_atomic_uint_t  delivery_total;
    ngx_atomic_uint_t  requests_total;
    ngx_atomic_uint_t  failopen_total;
} ngx_http_markdown_diag_metrics_t;


/*
 * Collect key metrics counters for the diagnostics endpoint.
 *
 * Reads the SHM metrics zone (if available) and populates
 * the output struct with current counter values.  If the
 * metrics zone is not available, all fields are zeroed.
 *
 * Parameters:
 *   out - Output struct to populate
 */
void ngx_http_markdown_diagnostics_collect_metrics(
    ngx_http_markdown_diag_metrics_t *out);


/*
 * Dynconf state snapshot for the diagnostics endpoint.
 *
 * Contains the watcher state needed by the diagnostics JSON
 * response.  Populated by ngx_http_markdown_diagnostics_get_dynconf_state().
 */
typedef struct {
    time_t      active_mtime;
    ngx_uint_t  config_version;
    time_t      last_known_good_mtime;
    ngx_flag_t  lkg_valid;
} ngx_http_markdown_diag_dynconf_t;


/*
 * Get the current dynconf watcher state for the diagnostics endpoint.
 *
 * Reads the global dynconf watcher and populates the output
 * struct with current state values.  If dynconf is not active,
 * all fields are zeroed.
 *
 * Parameters:
 *   out - Output struct to populate
 */
void ngx_http_markdown_diagnostics_get_dynconf_state(
    ngx_http_markdown_diag_dynconf_t *out);


/*
 * Decision path component string constants.
 *
 * These are the valid values for each field in the structured
 * decision path log line.  Using constants avoids typos and
 * enables compile-time length computation.
 */

/* accept_result values */
#define NGX_HTTP_MARKDOWN_ACCEPT_CONVERT   "CONVERT"
#define NGX_HTTP_MARKDOWN_ACCEPT_SKIP      "SKIP"
#define NGX_HTTP_MARKDOWN_ACCEPT_REJECT    "REJECT"
#define NGX_HTTP_MARKDOWN_ACCEPT_NONE      "NONE"

/* conditional_result values */
#define NGX_HTTP_MARKDOWN_COND_NOT_MODIFIED "NOT_MODIFIED"
#define NGX_HTTP_MARKDOWN_COND_PROCEED     "PROCEED"
#define NGX_HTTP_MARKDOWN_COND_SKIPPED     "SKIPPED"

/* conversion_status values */
#define NGX_HTTP_MARKDOWN_CONV_SUCCESS     "SUCCESS"
#define NGX_HTTP_MARKDOWN_CONV_FAILED      "FAILED"
#define NGX_HTTP_MARKDOWN_CONV_SKIPPED     "SKIPPED"


/*
 * Structured decision path log entry.
 *
 * Captures the complete decision chain for a single request.
 * All fields use pre-formatted string pointers (no allocation
 * needed at log time).
 */
typedef struct {
    const char   *accept_result;      /* CONVERT/SKIP/REJECT/NONE */
    const char   *conditional_result; /* NOT_MODIFIED/PROCEED/SKIPPED */
    const char   *conversion_status;  /* SUCCESS/FAILED/SKIPPED */
    const char   *reason_code;        /* Final reason code string */
    ngx_msec_t    duration_ms;        /* Processing duration in ms */
} ngx_http_markdown_decision_path_t;


/*
 * Log the structured decision path for a request.
 *
 * Emits a single structured log line containing the complete
 * decision chain in key=value format for easy parsing:
 *
 *   markdown: accept_result=CONVERT
 *       conditional_result=PROCEED conversion_status=SUCCESS
 *       reason_code=CONVERTED duration_ms=12
 *
 * The log level is determined by the effective log_verbosity:
 *   - NGX_HTTP_MARKDOWN_LOG_DEBUG: NGX_LOG_DEBUG
 *   - NGX_HTTP_MARKDOWN_LOG_INFO:  NGX_LOG_INFO
 *   - NGX_HTTP_MARKDOWN_LOG_WARN:  NGX_LOG_WARN (failures only)
 *   - NGX_HTTP_MARKDOWN_LOG_ERROR: NGX_LOG_ERR (failures only)
 *
 * This function does NOT allocate memory.  All formatting uses
 * a stack buffer.  Safe to call from both header_filter and
 * body_filter paths.
 *
 * Parameters:
 *   r    - HTTP request (for connection log)
 *   conf - Module location configuration (for diagnostics flag)
 *   eff  - Effective configuration view (for log_verbosity);
 *          may be NULL (falls back to conf->policy.log_verbosity)
 *   path - Decision path components to log
 *
 * The function is a no-op when:
 *   - diagnostics is disabled AND log_verbosity < info
 *   - the outcome is non-failure AND log_verbosity <= warn
 */
void ngx_http_markdown_log_decision_path(
    ngx_http_request_t *r,
    const void *conf,
    const void *eff,
    const ngx_http_markdown_decision_path_t *path);


#endif /* _NGX_HTTP_MARKDOWN_DIAGNOSTICS_H_INCLUDED_ */
