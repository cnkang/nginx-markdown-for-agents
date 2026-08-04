/*
 * Internal module-state declarations shared by implementation includes.
 *
 * WARNING: This header is an implementation detail of the main translation unit
 * (ngx_http_markdown_filter_module.c). It must NOT be included from any other
 * .c file or used as a standalone compilation unit.
 *
 * This header is intentionally included only from the main translation unit so
 * helper implementation headers can rely on a stable set of shared globals,
 * filter-chain pointers, metric macros, and forward declarations while keeping
 * all #include directives at the top of the .c file.
 */

#ifndef NGX_HTTP_MARKDOWN_MODULE_STATE_IMPL_H
#define NGX_HTTP_MARKDOWN_MODULE_STATE_IMPL_H

/* Bound eager reservation to avoid huge one-shot allocations on giant responses. */
#define NGX_HTTP_MARKDOWN_PRERESERVE_LIMIT (16 * 1024 * 1024)

/* Global converter instance for this worker process */
static struct MarkdownConverterHandle *ngx_http_markdown_converter = NULL;

/* Global pointer to shared metrics state, resolved during worker init. */
static ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;
static ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = NULL;
/*
 * Keep the metrics SHM zone name layout-versioned.
 *
 * ngx_http_markdown_init_metrics_zone() currently reattaches existing slab
 * data directly from shpool->data. When ngx_http_markdown_metrics_t layout
 * changes (for example fields appended at the tail), this version suffix
 * prevents attaching an incompatible old allocation after hot reload.
 *
 * v8: unretained per-path conversion and time counters added to preserve
 *     aggregate accounting when a path cannot be stored in the slab.
 */
static ngx_str_t ngx_http_markdown_metrics_shm_name =
    ngx_string("nginx_markdown_metrics_v8");
static u_char ngx_http_markdown_empty_string[] = "";

/* Global dynamic config watcher for this worker process.
 * active_snapshot holds the currently effective configuration;
 * staging_snapshot is used during two-phase reload;
 * last_known_good holds the previous active snapshot for diagnostics and
 * failed-reload protection. */
static ngx_http_markdown_dynconf_watcher_t ngx_http_markdown_dynconf_watcher = { 0 };

#define NGX_HTTP_MARKDOWN_METRIC_ADD(field, value)                                  \
    do {                                                                            \
        if (ngx_http_markdown_metrics != NULL) {                                    \
            ngx_atomic_fetch_add(&ngx_http_markdown_metrics->field,                 \
                (ngx_atomic_uint_t) (value));                                       \
        }                                                                           \
    } while (0)

#define NGX_HTTP_MARKDOWN_METRIC_INC(field)                                         \
    NGX_HTTP_MARKDOWN_METRIC_ADD(field, 1)

/* Record one successfully finalized decompression for the public v1 family. */
static void
ngx_http_markdown_record_decompression_success_metrics(
    const ngx_http_markdown_ctx_t *ctx)
{
    if (ctx == NULL) {
        return;
    }

    NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.succeeded);
    switch (ctx->decompression.type) {
    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.gzip);
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.deflate);
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.brotli);
        break;
    default:
        break;
    }
}

/* Keep decompression failure reasons split by codec for the frozen v1 family. */
static void
ngx_http_markdown_record_decompression_failure_budget(
    ngx_http_markdown_compression_type_e type)
{
    switch (type) {
    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.gzip_failures.budget);
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.deflate_failures.budget);
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.brotli_failures.budget);
        break;
    default:
        break;
    }
}

static void
ngx_http_markdown_record_decompression_failure_format(
    ngx_http_markdown_compression_type_e type)
{
    switch (type) {
    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.gzip_failures.format);
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.deflate_failures.format);
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.brotli_failures.format);
        break;
    default:
        break;
    }
}

static void
ngx_http_markdown_record_decompression_failure_truncated(
    ngx_http_markdown_compression_type_e type)
{
    switch (type) {
    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.gzip_failures.truncated);
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.deflate_failures.truncated);
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.brotli_failures.truncated);
        break;
    default:
        break;
    }
}

static void
ngx_http_markdown_record_decompression_failure_io(
    ngx_http_markdown_compression_type_e type)
{
    switch (type) {
    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.gzip_failures.io);
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.deflate_failures.io);
        break;
    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        NGX_HTTP_MARKDOWN_METRIC_INC(decompressions.brotli_failures.io);
        break;
    default:
        break;
    }
}

#define NGX_HTTP_MARKDOWN_METRIC_DEC(field)                                         \
    NGX_HTTP_MARKDOWN_METRIC_ADD(field, -1)

/*
 * Safe decrement: only decrements if the counter is currently
 * positive.  Prevents underflow/wraparound when a metrics zone
 * reset (e.g. worker restart) leaves the counter at zero.
 */
#define NGX_HTTP_MARKDOWN_METRIC_SAFE_DEC(field)                                    \
    do {                                                                            \
        if (ngx_http_markdown_metrics != NULL) {                                    \
            ngx_atomic_uint_t  _cur;                                                \
            for ( ;; ) {                                                            \
                _cur = ngx_http_markdown_metrics->field;                            \
                if (_cur == 0) { break; }                                           \
                if (ngx_atomic_cmp_set(                                            \
                        &ngx_http_markdown_metrics->field,                          \
                        _cur, _cur - 1))                                            \
                {                                                                   \
                    break;                                                          \
                }                                                                   \
            }                                                                       \
        }                                                                           \
    } while (0)

/*
 * Monotonically non-decreasing gauge update via CAS loop.
 * Only updates the field if `value` exceeds the current maximum.
 */
#define NGX_HTTP_MARKDOWN_METRIC_WATERMARK(field, value)                            \
    do {                                                                            \
        if (ngx_http_markdown_metrics != NULL) {                                    \
            ngx_atomic_t  _wm_cur;                                                  \
            ngx_atomic_t  _wm_new = (ngx_atomic_t) (value);                        \
            for ( ;; ) {                                                            \
                _wm_cur = ngx_http_markdown_metrics->field;                         \
                if (_wm_new <= _wm_cur) { break; }                                  \
                if (ngx_atomic_cmp_set(                                            \
                        &ngx_http_markdown_metrics->field,                          \
                        _wm_cur, _wm_new))                                          \
                {                                                                   \
                    break;                                                          \
                }                                                                   \
            }                                                                       \
        }                                                                           \
    } while (0)

void
ngx_http_markdown_record_dynconf_reload(ngx_uint_t error_code)
{
    if (ngx_http_markdown_metrics == NULL) {
        return;
    }

    switch (error_code) {
    case DYNCONF_OK:
        NGX_HTTP_MARKDOWN_METRIC_INC(results.dynconf_reloads.success);
        break;
    case DYNCONF_ERR_MISSING_SCHEMA_VERSION:
    case DYNCONF_ERR_INVALID_SCHEMA_VERSION:
        NGX_HTTP_MARKDOWN_METRIC_INC(
            results.dynconf_reloads.failure_schema_version);
        break;
    case DYNCONF_ERR_UNKNOWN_KEY:
        NGX_HTTP_MARKDOWN_METRIC_INC(
            results.dynconf_reloads.failure_unknown_key);
        break;
    case DYNCONF_ERR_DUPLICATE_KEY:
        NGX_HTTP_MARKDOWN_METRIC_INC(
            results.dynconf_reloads.failure_duplicate_key);
        break;
    case DYNCONF_ERR_INVALID_TYPE:
        NGX_HTTP_MARKDOWN_METRIC_INC(
            results.dynconf_reloads.failure_invalid_type);
        break;
    case DYNCONF_ERR_VALUE_OUT_OF_RANGE:
        NGX_HTTP_MARKDOWN_METRIC_INC(
            results.dynconf_reloads.failure_out_of_range);
        break;
    case DYNCONF_ERR_TOO_LARGE:
        NGX_HTTP_MARKDOWN_METRIC_INC(
            results.dynconf_reloads.failure_size_exceeded);
        break;
    case DYNCONF_ERR_INVALID_JSON:
    case DYNCONF_ERR_TOKEN_BUDGET:
    case DYNCONF_ERR_NESTING_DEPTH:
    case DYNCONF_ERR_INVALID_UTF8:
    case DYNCONF_ERR_INTERNAL:
        NGX_HTTP_MARKDOWN_METRIC_INC(
            results.dynconf_reloads.failure_parse_error);
        break;
    default:
        NGX_HTTP_MARKDOWN_METRIC_INC(
            results.dynconf_reloads.failure_file_error);
        break;
    }
}

/*
 * Increment the skip counter for the given eligibility result.
 *
 * Maps ngx_http_markdown_eligibility_t enum values to the
 * corresponding skips.* atomic counter in shared memory.
 * Unknown values fall back to skips.config with a warning.
 *
 * NGX_HTTP_MARKDOWN_ELIGIBLE is a no-op (should not be
 * called for eligible requests).
 *
 * Parameters:
 *   eligibility - the eligibility check result
 */
static void
ngx_http_markdown_metric_inc_skip(
    ngx_http_markdown_eligibility_t eligibility)
{
    switch (eligibility) {

    case NGX_HTTP_MARKDOWN_ELIGIBLE:
        /* No-op: eligible requests are not skips */
        return;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD:
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.method);
        return;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS:
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.status);
        return;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE:
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.content_type);
        return;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE:
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.size);
        return;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING:
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.streaming);
        return;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH:
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.auth);
        return;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE:
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.range);
        return;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG:
    default:
        /*
         * Unknown eligibility value — count under
         * skips.config as a safe fallback.
         */
        NGX_HTTP_MARKDOWN_METRIC_INC(skips.config);
        return;
    }
}

/*
 * Increment the fail-open delivery counter after the downstream filter
 * has confirmed successful delivery of the original response.
 *
 * Rule 38/23: `results.failopen_count` is a DELIVERY counter, not a
 * decision counter.  Callers MUST only invoke this helper after the
 * downstream body/header filter returns `NGX_OK` or `NGX_DONE`
 * (i.e. `delivery_ok`).  The decision to fail-open is recorded
 * separately via `log_decision()` / `log_failure_decision()` at the
 * decision point; this helper must not be called at the decision point.
 *
 * Centralizes the repeated guard so every fail-open path uses
 * the same check and future fail-open paths cannot drift.
 *
 * Parameters:
 *   conf - module location configuration
 */
static void
ngx_http_markdown_metric_inc_failopen(
    const ngx_http_markdown_conf_t *conf)
{
    if (conf == NULL) {
        return;
    }

    if (conf->on_error
        == NGX_HTTP_MARKDOWN_ON_ERROR_PASS)
    {
        NGX_HTTP_MARKDOWN_METRIC_INC(results.failopen_count);
    }
}

/*
 * Lifecycle hooks registered with nginx module callbacks.
 *
 * - preconfiguration resets parse-cycle globals before directives are read.
 * - filter_init wires this module into header/body filter chains.
 * - init_worker prepares per-worker converter + metrics pointers.
 * - exit_worker releases per-worker converter resources.
 */
static ngx_int_t ngx_http_markdown_preconfiguration(ngx_conf_t *cf);
static ngx_int_t ngx_http_markdown_filter_init(ngx_conf_t *cf);
static ngx_int_t ngx_http_markdown_init_worker(ngx_cycle_t *cycle);
static void ngx_http_markdown_exit_worker(ngx_cycle_t *cycle);

/*
 * Filter entrypoints.
 *
 * Header filter decides eligibility and initializes request context;
 * body filter buffers payload, optionally decompresses, converts, and emits
 * final downstream output.
 */
static ngx_int_t ngx_http_markdown_header_filter(ngx_http_request_t *r);
static ngx_int_t ngx_http_markdown_body_filter(ngx_http_request_t *r, ngx_chain_t *in);

/* Expose runtime counters as the module metrics endpoint response. */
static ngx_int_t ngx_http_markdown_metrics_handler(ngx_http_request_t *r);
#if !(NGX_HTTP_HEADERS)
/* Fallback request-header lookup when nginx does not expose typed header fields. */
static ngx_table_elt_t *ngx_http_markdown_find_request_header(ngx_http_request_t *r,
    const ngx_str_t *name);
#endif

/* Next filter pointers preserved so this module can delegate in chain order. */
static ngx_http_output_header_filter_pt ngx_http_next_header_filter;
static ngx_http_output_body_filter_pt ngx_http_next_body_filter;

#endif
