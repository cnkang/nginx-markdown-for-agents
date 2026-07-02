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
 * Forward declaration of the NGINX cycle struct tag.
 *
 * The worker-init helper below takes a cycle pointer.  Real builds get the
 * full type from <ngx_core.h>; lightweight unit-test stub headers may omit
 * it.  Declaring the tag at file scope (rather than only inside the
 * prototype) keeps the type visible without depending on the stub headers
 * and avoids a -Wvisibility warning.  The helper itself is compiled only in
 * the production translation unit, where the full type is available.
 */
struct ngx_cycle_s;


/*
 * Default ring buffer capacity for recent decisions.
 *
 * The capacity is currently fixed at this default; there is no
 * markdown_diagnostics_capacity directive.  If a configurable capacity is
 * introduced later, pass the desired value to
 * ngx_http_markdown_diagnostics_init() (0 selects this default).
 */
#define NGX_HTTP_MARKDOWN_DIAG_DEFAULT_CAPACITY  100

/*
 * Maximum allowed ring buffer capacity.
 */
#define NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY      10000

/*
 * Diagnostics JSON response sizing constants.
 *
 * Used by ngx_http_markdown_diagnostics_json_size() to pre-allocate the
 * response buffer.  Both production code and tests must use the same
 * function (or these constants) to compute expected buffer sizes.
 *
 * NGX_HTTP_MARKDOWN_DIAG_JSON_BASE_SIZE:
 *   Covers the fixed-size JSON envelope: config_snapshot (dynconf
 *   snapshot serialization), metrics_snapshot (4 counters), dynconf_state
 *   (4 fields), streaming_config (3 fields), streaming_metrics
 *   (8 counters), section keys, braces, commas, and whitespace.
 *   Must be >= the maximum rendered size of all non-decision sections
 *   combined.
 *
 * NGX_HTTP_MARKDOWN_DIAG_JSON_DECISION_SIZE:
 *   Covers one recent_decisions entry: {"timestamp": <ms>, "reason_code":
 *   <int>, "duration_ms": <ms>} plus trailing comma and indentation.
 *   Must be >= the maximum rendered size of a single decision object.
 *
 * If the JSON shape changes (new sections, wider fields), these constants
 * must be updated.  Truncation is detected at runtime by build_json and
 * returns NGX_ERROR (500) rather than serving incomplete JSON.
 */
#define NGX_HTTP_MARKDOWN_DIAG_JSON_BASE_SIZE    34392
#define NGX_HTTP_MARKDOWN_DIAG_JSON_DECISION_SIZE 192


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
 *   - streaming_config: streaming engine configuration
 *
 * Access control: by default (no markdown_diagnostics_allow directives),
 * only loopback clients (127.0.0.1 or ::1) are permitted.  When one or more
 * markdown_diagnostics_allow CIDR entries are configured, access is granted
 * to clients whose address matches the allow-list; non-matching clients
 * receive NGX_HTTP_FORBIDDEN.  Requests with no/unknown peer address are
 * denied.  Only GET and HEAD are accepted.
 *
 * Parameters:
 *   r - HTTP request
 *
 * Returns:
 *   NGX_OK, NGX_ERROR, or HTTP status code
 */
ngx_int_t ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r);


/*
 * Request that the per-worker diagnostics ring record decisions.
 *
 * Called at configuration parse time when a location enables the
 * diagnostics endpoint (markdown_diagnostics on).  The ring itself is
 * allocated and enabled later by
 * ngx_http_markdown_diagnostics_init_worker() during worker startup.
 */
void ngx_http_markdown_diagnostics_enable_recording(void);


/*
 * Reset the parse-time diagnostics recording request for a new config cycle.
 *
 * Called from module preconfiguration before directives are parsed so reloads
 * that remove markdown_diagnostics do not inherit a stale request flag from an
 * earlier configuration.
 */
void ngx_http_markdown_diagnostics_reset_recording_request(void);


/*
 * Initialize the per-worker diagnostics ring during worker startup.
 *
 * No-op unless a location requested diagnostics via
 * ngx_http_markdown_diagnostics_enable_recording().
 *
 * Parameters:
 *   cycle - NGINX cycle (per-worker pool and log)
 *
 * Returns:
 *   NGX_OK on success or no-op; NGX_ERROR on allocation failure.
 */
ngx_int_t ngx_http_markdown_diagnostics_init_worker(struct ngx_cycle_s *cycle);


/*
 * Whether the per-worker diagnostics ring is actively recording.
 *
 * Returns 1 when the ring is initialized and enabled, 0 otherwise.
 */
ngx_int_t ngx_http_markdown_diagnostics_recording_active(void);


/*
 * Map a decision-path reason code string to its canonical numeric
 * ReasonCode discriminant (decision/reason_code.rs is the source of truth).
 *
 * Parameters:
 *   reason - NUL-terminated reason code string (may be NULL)
 *
 * Returns:
 *   Canonical discriminant (0..17), or -1 for NULL/unknown strings.
 */
ngx_int_t ngx_http_markdown_diagnostics_reason_to_code(const char *reason);


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
#ifdef MARKDOWN_STREAMING_ENABLED
    /* Streaming metrics (streaming observability) */
    ngx_atomic_uint_t  streaming_requests_total;
    ngx_atomic_uint_t  streaming_succeeded_total;
    ngx_atomic_uint_t  streaming_failed_total;
    ngx_atomic_uint_t  streaming_fallback_total;
    ngx_atomic_uint_t  streaming_candidate_total;
    ngx_atomic_uint_t  streaming_output_bytes_total;
    ngx_atomic_uint_t  engine_choice_streaming;
    ngx_atomic_uint_t  engine_choice_full_buffer;
#endif
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
/* Conditional result string constants for diagnostics */
#define NGX_HTTP_MARKDOWN_COND_NOT_MODIFIED "NOT_MODIFIED"
#define NGX_HTTP_MARKDOWN_COND_PROCEED     "PROCEED"
#define NGX_HTTP_MARKDOWN_COND_SKIPPED     "SKIPPED"
#define NGX_HTTP_MARKDOWN_COND_BYPASS      "BYPASS"

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
