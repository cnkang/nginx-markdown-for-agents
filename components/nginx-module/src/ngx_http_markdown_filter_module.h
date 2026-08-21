/*
 * NGINX Markdown Filter Module - Header File
 *
 * This header defines the NGINX module structures and functions for
 * the Markdown conversion filter.
 */

#ifndef NGX_HTTP_MARKDOWN_FILTER_MODULE_H
#define NGX_HTTP_MARKDOWN_FILTER_MODULE_H

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#ifndef NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
/* Standalone header harnesses do not link the auth implementation. */
#define NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL 0
#endif

/*
 * Public module version reported in diagnostics/metrics.  This is the
 * single definition; release tooling may override it via -D at build
 * time (mirrors the NGX_HTTP_MARKDOWN_SOURCE_SHA / RUST_VERSION pattern).
 * Bump together with the release version.
 */
#ifndef NGX_HTTP_MARKDOWN_PRODUCT_VERSION
#define NGX_HTTP_MARKDOWN_PRODUCT_VERSION "0.9.2"
#endif

struct MarkdownOptions;
struct MarkdownResult;

/* Forward declaration: defined in ngx_http_markdown_inflight_impl.h
 * (included after this header).  The request context stores a pointer
 * to the per-request inflight release data (subrequest subrequest lifecycle). */
typedef struct ngx_http_markdown_inflight_cleanup_s
    ngx_http_markdown_inflight_cleanup_t;

typedef struct ngx_http_markdown_loc_validation_summary_s {
    size_t      min_applicable_conversion_memory;
    ngx_flag_t  min_applicable_set;
    ngx_uint_t  block_mask_union;
} ngx_http_markdown_loc_validation_summary_t;

/*
 * NGINX represents unset size values as (size_t) -1.  Use the public macro
 * when the including translation unit exposes it; standalone unit tests may
 * include this header through minimal stubs that do not.
 */
#ifdef NGX_CONF_UNSET_SIZE
#define NGX_HTTP_MARKDOWN_CONF_UNSET_SIZE NGX_CONF_UNSET_SIZE
#else
#define NGX_HTTP_MARKDOWN_CONF_UNSET_SIZE ((size_t) -1)
#endif

/*
 * Brotli's decoder owns auxiliary allocations outside the output buffer.
 * Keep their aggregate bounded per worker so concurrent streaming requests
 * cannot bypass the module's decompression budgets through library state.
 */
#ifndef NGX_HTTP_MARKDOWN_BROTLI_WORKSPACE_LIMIT
#define NGX_HTTP_MARKDOWN_BROTLI_WORKSPACE_LIMIT (32 * 1024 * 1024)
#endif

#ifdef NGX_HTTP_BROTLI
static ngx_inline size_t
ngx_http_markdown_brotli_workspace_limit(ngx_atomic_uint_t configured_limit)
{
    if (configured_limit == 0
        || configured_limit > NGX_HTTP_MARKDOWN_BROTLI_WORKSPACE_LIMIT)
    {
        return NGX_HTTP_MARKDOWN_BROTLI_WORKSPACE_LIMIT;
    }

    return configured_limit;
}
#endif

/* C-side reload classification for file-system failures. */
#define NGX_HTTP_MARKDOWN_DYNCONF_ERR_IO 254

/*
 * Forward declaration for dynconf snapshot type.
 * Full definition is in ngx_http_markdown_dynconf_impl.h.
 */
typedef struct ngx_http_markdown_dynconf_snapshot_s
    ngx_http_markdown_dynconf_snapshot_t;

/*
 * Effective configuration view for per-request consistency.
 *
 * Constructed once at header_filter time from the dynconf snapshot (if
 * dynconf is enabled and the snapshot is valid) or from the live static
 * conf otherwise.  All request-lifetime code reads mutable fields through
 * this view rather than directly from ngx_http_markdown_conf_t, so that
 * a mid-request dynconf reload cannot change behaviour for in-flight
 * requests.
 *
 * Dynconf-mutable fields that MUST be read through this struct
 * (via ngx_http_markdown_effective_*() helpers) in all request-path
 * code (body filter, conversion, logging, budget, streaming):
 *   - filter (represented by enabled)
 *   - prune_noise
 *   - log_verbosity
 *   - error_policy
 *   - streaming_buffer
 *
 * Direct conf-> reads of these fields in request-path code are
 * violations of AGENTS.md Rule 34 and will be flagged by
 * tools/harness/detect_live_conf_reads.sh.
 */
struct ngx_http_markdown_effective_conf_s {
    ngx_flag_t   enabled;
    ngx_uint_t   enabled_source;
    ngx_flag_t   prune_noise;
    ngx_uint_t   log_verbosity;
    ngx_uint_t   error_policy;
    ngx_uint_t   error_status;
    size_t       memory_budget;   /* effective conversion_memory projection (frozen public limit) */
    size_t       streaming_buffer;
#ifdef MARKDOWN_STREAMING_ENABLED
    size_t       streaming_budget;
#endif
    /*
     * Per-field provenance after precedence resolution (0.9.2).
     *
     * Records the source of each dynconf-mutable field's effective
     * value: 0=static, 1=dynconf, 2=request_variable.
     * Only filter may be request_variable; others are static|dynconf.
     */
    ngx_uint_t   filter_provenance;
    ngx_uint_t   prune_noise_provenance;
    ngx_uint_t   log_verbosity_provenance;
    ngx_uint_t   error_policy_provenance;
    ngx_uint_t   streaming_buffer_provenance;
    /*
     * Copy of the location's dynconf block mask for diagnostics.
     */
    ngx_uint_t   block_mask;
};

typedef struct ngx_http_markdown_effective_conf_s
    ngx_http_markdown_effective_conf_t;

/* Delegate body output to the downstream filter saved during module init. */
ngx_int_t ngx_http_markdown_next_body_filter(ngx_http_request_t *r,
    ngx_chain_t *in);

/* Track the number of requests that currently own pending output chains. */
void ngx_http_markdown_pending_output_set(ngx_chain_t **slot,
    ngx_chain_t *value);
ngx_atomic_uint_t ngx_http_markdown_pending_output_current(void);

/*
 * Processing path constants for threshold router
 */
#define NGX_HTTP_MARKDOWN_PATH_FULLBUFFER   0  /* Full-buffer path */
#define NGX_HTTP_MARKDOWN_PATH_INCREMENTAL  1  /* Incremental path */
#define NGX_HTTP_MARKDOWN_TRUSTED_PROXIES_MAX  64  /* Requirement 13.1 */
#define NGX_HTTP_MARKDOWN_PATH_STREAMING    2  /* Streaming path */

/*
 * Input disposition constants for streaming backpressure lifecycle.
 *
 * Decouples downstream return code (NGX_AGAIN) from input ownership:
 * CONSUMED means Rust ate the chunk (advance pos, enqueue remainder);
 * RETAIN means the chunk's ngx_buf_t is shared with a pending fail-open
 * clone and must not be advanced (would corrupt undelivered HTML);
 * TERMINAL means the input is abandoned on post-commit fatal error.
 */
#define NGX_HTTP_MD_INPUT_CONSUMED   0
#define NGX_HTTP_MD_INPUT_RETAIN     1
#define NGX_HTTP_MD_INPUT_TERMINAL   2

/*
 * Request-level buffered flag for this module while it is accumulating or
 * preserving output for a later retry.
 *
 * Low bits 0x01/0x02/0x04 are used by core modules (SSI/SUB/COPY). 0x08 is
 * available for request-level buffering (image filter uses 0x08 on
 * connection->buffered, not r->buffered).
 */
#define NGX_HTTP_MARKDOWN_BUFFERED  0x08

#ifdef MARKDOWN_STREAMING_ENABLED
/*
 * Streaming commit state constants
 */
#define NGX_HTTP_MARKDOWN_STREAMING_COMMIT_PRE   0
#define NGX_HTTP_MARKDOWN_STREAMING_COMMIT_POST  1

/*
 * Post-commit send failure origin classification.
 *
 * Set by send_output before returning
 * NGX_ERROR so the caller (handle_success_output) can route the
 * failure to the correct metrics and recovery path:
 *
 * ALLOCATION: pool/buf/chain allocation failure (memory pressure).
 *   Maps to ERROR_MEMORY_LIMIT, increments failures_resource_limit.
 *
 * DOWNSTREAM: ngx_http_next_body_filter returned definitive failure.
 *   Does NOT increment failures_resource_limit; routes to
 *   failures_conversion.
 *
 * INVARIANT: internal state error (e.g. pending-output re-entry).
 *   Does NOT increment failures_resource_limit; routes to
 *   failures_conversion.
 */
#define NGX_HTTP_MD_SEND_ORIGIN_NONE         0
#define NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION   1
#define NGX_HTTP_MD_SEND_ORIGIN_DOWNSTREAM   2
#define NGX_HTTP_MD_SEND_ORIGIN_INVARIANT    3

/*
 * Default streaming budget: 2 MiB
 */
#define NGX_HTTP_MARKDOWN_STREAMING_BUDGET_DEFAULT \
    (2 * 1024 * 1024)

/*
 * Streaming engine path-selection states (internal implementation state).
 *
 * These values explain why a particular engine path was chosen inside the C
 * streaming router.  They are not entries in the canonical Rust reason
 * registry and are not a Prometheus or JSON label contract.  Operator-visible
 * outcome reasons must use the canonical reason accessors instead.
 */
typedef enum {
    /* Engine choice: true streaming */
    NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE = 0,

    /* Engine choice: full buffer */
    NGX_HTTP_MARKDOWN_STREAM_REASON_CONTENT_LENGTH_KNOWN,
    NGX_HTTP_MARKDOWN_STREAM_REASON_BELOW_THRESHOLD,
    NGX_HTTP_MARKDOWN_STREAM_REASON_CONFIG_DISABLED,

    /* Engine choice: passthrough */
    NGX_HTTP_MARKDOWN_STREAM_REASON_EXCLUDED_CONTENT_TYPE,
    NGX_HTTP_MARKDOWN_STREAM_REASON_NOT_HTML,
    NGX_HTTP_MARKDOWN_STREAM_REASON_COMPRESSED,

    /* Engine choice: not eligible */
    NGX_HTTP_MARKDOWN_STREAM_REASON_NOT_CANDIDATE,
    NGX_HTTP_MARKDOWN_STREAM_REASON_ACCEPT_MISMATCH,

    /* Fallback reasons */
    NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_HTML_ERROR,
    NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_BUDGET,
    NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_TIMEOUT,

    /* Post-commit failure reasons */
    NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_PARSE_ERROR,
    NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_BUDGET_EXCEEDED,
    NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_IO_ERROR,

    /* Sentinel — must be last */
    NGX_HTTP_MARKDOWN_STREAM_REASON_COUNT
} ngx_http_markdown_stream_reason_e;

/*
 * Map an internal streaming path state to its debug-log string.
 *
 * The returned strings are for internal diagnostics only.  Unknown values
 * return "unknown".
 */
static ngx_inline const char *
ngx_http_markdown_stream_reason_str(
    ngx_http_markdown_stream_reason_e reason)
{
    static const char *reason_strings[] = {
        [NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE] = "eligible",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_CONTENT_LENGTH_KNOWN] =
            "content_length_known",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_BELOW_THRESHOLD] =
            "below_threshold",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_CONFIG_DISABLED] =
            "config_disabled",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_EXCLUDED_CONTENT_TYPE] =
            "excluded_content_type",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_NOT_HTML] = "not_html",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_COMPRESSED] = "compressed",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_NOT_CANDIDATE] =
            "not_candidate",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_ACCEPT_MISMATCH] =
            "accept_mismatch",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_HTML_ERROR] =
            "precommit_html_error",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_BUDGET] =
            "precommit_budget",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_PRECOMMIT_TIMEOUT] =
            "precommit_timeout",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_PARSE_ERROR] =
            "postcommit_parse_error",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_BUDGET_EXCEEDED] =
            "postcommit_budget_exceeded",
        [NGX_HTTP_MARKDOWN_STREAM_REASON_POSTCOMMIT_IO_ERROR] =
            "postcommit_io_error"
    };

    _Static_assert(
        sizeof(reason_strings) / sizeof(reason_strings[0])
        == NGX_HTTP_MARKDOWN_STREAM_REASON_COUNT,
        "stream reason strings must match reason enum");

    _Static_assert(
        NGX_HTTP_MARKDOWN_STREAM_REASON_ELIGIBLE == 0,
        "reason enum must start at 0");
    _Static_assert(
        NGX_HTTP_MARKDOWN_STREAM_REASON_COUNT == 15,
        "reason enum count must match designated initializer coverage");

    if ((unsigned) reason >= NGX_HTTP_MARKDOWN_STREAM_REASON_COUNT) {
        return "unknown";
    }

    return reason_strings[(unsigned) reason];
}

typedef struct {
    ngx_uint_t                         path;
    ngx_http_markdown_stream_reason_e  reason;
} ngx_http_markdown_path_selection_t;

static ngx_inline ngx_http_markdown_path_selection_t
ngx_http_markdown_path_selection(ngx_uint_t path,
    ngx_http_markdown_stream_reason_e reason)
{
    ngx_http_markdown_path_selection_t selection;

    selection.path = path;
    selection.reason = reason;

    return selection;
}

#endif /* MARKDOWN_STREAMING_ENABLED */

/*
 * Streaming fallback state machine types (v0.8.0 streaming fallback state machine).
 *
 * These types implement the pure-function decision engine defined in
 * the streaming fallback state machine design.  The state machine governs runtime transitions
 * between streaming, full-buffer, passthrough, and failure modes.
 *
 * Placement: unconditionally available (not gated by
 * MARKDOWN_STREAMING_ENABLED) because the v0.8.0 streaming architecture
 * uses these types regardless of the legacy compile-time feature flag.
 */

/* State enum: every request follows exactly one deterministic path */
typedef enum {
    NGX_HTTP_MD_STATE_NOT_ELIGIBLE = 0,
    NGX_HTTP_MD_STATE_STREAMING_CANDIDATE,
    NGX_HTTP_MD_STATE_PRE_COMMIT,
    NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE,
    NGX_HTTP_MD_STATE_FULL_BUFFER_FALLBACK,
    NGX_HTTP_MD_STATE_PASSTHROUGH,
    NGX_HTTP_MD_STATE_COMMITTED,
    NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
    NGX_HTTP_MD_STATE_POST_COMMIT_ABORT
} ngx_http_markdown_stream_state_e;

/* Action enum: what the module does on each state transition */
typedef enum {
    NGX_HTTP_MD_ACTION_NONE = 0,
    NGX_HTTP_MD_ACTION_PASS_HTML,
    NGX_HTTP_MD_ACTION_REJECT_STATUS,
    NGX_HTTP_MD_ACTION_COMMIT_HEADERS,
    NGX_HTTP_MD_ACTION_CONTINUE_STREAMING,
    NGX_HTTP_MD_ACTION_SWITCH_FULL_BUFFER,
    NGX_HTTP_MD_ACTION_SAFE_FINISH,
    NGX_HTTP_MD_ACTION_ABORT,
    NGX_HTTP_MD_ACTION_PASSTHROUGH
} ngx_http_markdown_action_e;

/* Reason code enum: why the transition occurred (metrics/logging) */
typedef enum {
    NGX_HTTP_MD_REASON_ELIGIBLE = 0,
    NGX_HTTP_MD_REASON_NOT_ELIGIBLE,
    NGX_HTTP_MD_REASON_PARSER_UNSUITABLE,
    NGX_HTTP_MD_REASON_HARD_EXCLUDED,
    NGX_HTTP_MD_REASON_FULL_DOC_FEATURE,
    NGX_HTTP_MD_REASON_BUDGET_INIT_FAILURE,
    NGX_HTTP_MD_REASON_REPLAY_OVERFLOW,
    NGX_HTTP_MD_REASON_RESOURCE_LIMIT_EXCEEDED,
    NGX_HTTP_MD_REASON_STRICT_ETAG,
    NGX_HTTP_MD_REASON_LOOK_BEHIND_OVERFLOW,
    NGX_HTTP_MD_REASON_AUTO_RISK,
    NGX_HTTP_MD_REASON_COMMIT_SUCCESS,
    NGX_HTTP_MD_REASON_POST_COMMIT_ERROR,
    NGX_HTTP_MD_REASON_ON_ERROR_PASS,
    NGX_HTTP_MD_REASON_ON_ERROR_REJECT
} ngx_http_markdown_reason_code_e;

/* Decision struct: output of the pure decision engine */
typedef struct {
    ngx_http_markdown_stream_state_e  new_state;
    ngx_http_markdown_action_e        action;
    ngx_http_markdown_reason_code_e   reason;
} ngx_http_markdown_decision_t;

/*
 * Streaming policy mode constants (markdown_streaming directive, 0.9.0).
 *
 * markdown_streaming off|auto|force is the sole processing-path selector.
 */
#define NGX_HTTP_MARKDOWN_STREAMING_OFF    0
#define NGX_HTTP_MARKDOWN_STREAMING_AUTO   1
#define NGX_HTTP_MARKDOWN_STREAMING_FORCE  2

/*
 * Profile constants removed in 0.9.2; retained as a zero-value sentinel
 * for diagnostics compatibility only.
 */
#define NGX_HTTP_MARKDOWN_PROFILE_NONE             0

#define NGX_HTTP_MARKDOWN_EXPLICIT_LIMIT_MEMORY      0x0001
#define NGX_HTTP_MARKDOWN_EXPLICIT_LIMIT_TIMEOUT     0x0002
#define NGX_HTTP_MARKDOWN_EXPLICIT_ERROR_POLICY      0x0004
#define NGX_HTTP_MARKDOWN_EXPLICIT_ACCEPT_POLICY     0x0008
#define NGX_HTTP_MARKDOWN_EXPLICIT_CACHE_VALIDATION  0x0010
#define NGX_HTTP_MARKDOWN_EXPLICIT_STREAM_POLICY     0x0020
#define NGX_HTTP_MARKDOWN_EXPLICIT_STREAM_BUDGET     0x0080

/* Canonical static-manifest explicitness bits. */
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FILTER       0x00000100
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_LIMITS       0x00000200
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FLAVOR       0x00000400
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_TOKEN        0x00000800
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_FRONT_MATTER 0x00001000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_ACCEPT       0x00002000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_AUTH_POLICY  0x00004000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_AUTH_COOKIES 0x00008000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_CACHE         0x00010000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_STREAM       0x00020000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_LOG           0x00040000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_CONTENT      0x00080000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_PRUNE        0x00100000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_SELECTORS    0x00200000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_PROTECTION   0x00400000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DECOMPRESS   0x00800000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DYNCONF      0x01000000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DRY_RUN      0x02000000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_DIAGNOSTICS  0x04000000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_EXCLUDED     0x08000000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_METRICS       0x10000000
#define NGX_HTTP_MARKDOWN_STATIC_EXPLICIT_ERROR_POLICY  0x20000000

/*
 * Dynconf block mask bits are part of the shared configuration contract.
 * The precedence header repeats these definitions only as a standalone
 * fallback for translation units that do not include this public header.
 */
#define NGX_HTTP_MARKDOWN_BLOCK_FILTER           (1 << 0)
#define NGX_HTTP_MARKDOWN_BLOCK_PRUNE_NOISE      (1 << 1)
#define NGX_HTTP_MARKDOWN_BLOCK_LOG_VERBOSITY    (1 << 2)
#define NGX_HTTP_MARKDOWN_BLOCK_ERROR_POLICY     (1 << 3)
#define NGX_HTTP_MARKDOWN_BLOCK_STREAMING_BUFFER (1 << 4)
#define NGX_HTTP_MARKDOWN_DYNCONF_FIELD_COUNT    5

#define NGX_HTTP_MARKDOWN_PROVENANCE_STATIC           0
#define NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF          1
#define NGX_HTTP_MARKDOWN_PROVENANCE_REQUEST_VARIABLE 2

/*
 * Threshold off sentinel — used in merge and path selection logic.
 */
#define NGX_HTTP_MARKDOWN_THRESHOLD_OFF     0

/*
 * Configuration constants for on_error / error_policy directive.
 *
 * The C runtime uses a two-field model:
 *   conf->on_error  = PASS (0) or REJECT (1)
 *   conf->error_status = actual HTTP status code (429/503; 502 is fail_closed default)
 *
 * The unified C error-policy path uses the same three-value semantic model:
 *   0 = pass, 1 = status, 2 = fail_closed.
 */
#define NGX_HTTP_MARKDOWN_ON_ERROR_PASS    0  /* fail-open: return original HTML */
#define NGX_HTTP_MARKDOWN_ON_ERROR_REJECT  1  /* fail-closed: return error status */

/*
 * Default pre-commit error status for markdown_error_policy (Config V2).
 *
 * markdown_error_policy fail_closed uses this (default: 502);
 * markdown_error_policy status <code> overrides it with 429 or 503.  Stored in
 * conf->error_status; honored by the unified error-policy path.
 */
#define NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT  502

/*
 * Configuration constants for markdown_accept directive (Config V2, 0.9.0).
 *
 * markdown_accept strict|wildcard|force replaces the removed
 * markdown_on_wildcard on|off directive.
 *   strict   - convert only on an explicit text/markdown Accept match
 *   wildcard - additionally convert on wildcard Accept (star/slash-star,
 *              text/star); equivalent to the old "markdown_on_wildcard on"
 *   force    - convert regardless of the Accept header (dangerous)
 */
#define NGX_HTTP_MARKDOWN_ACCEPT_STRICT    0  /* explicit text/markdown only */
#define NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD  1  /* also wildcard Accept */
#define NGX_HTTP_MARKDOWN_ACCEPT_FORCE     2  /* convert regardless of Accept */

/*
 * Default for markdown_limits max_inflight (0.9.0 production protection
 * default).  The value is parsed and stored in Config V2; enforcement is
 * implemented by the worker inflight guard.  Zero is rejected at config
 * parse time (must be 1..65535) — there is no "unlimited" sentinel.
 */
#define NGX_HTTP_MARKDOWN_MAX_INFLIGHT_DEFAULT  64

/*
 * Default streaming threshold (1 MiB) — fixed internal constant.
 * Responses with Content-Length >= this threshold use streaming mode
 * in auto mode.  Previously operator-configurable; now internalized
 * as a non-configurable heuristic.
 */
#define NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT \
    (1024 * 1024)

/*
 * Default streaming budget for v0.8.0 stream.budget field.
 * Same value as NGX_HTTP_MARKDOWN_STREAMING_BUDGET_DEFAULT (2 MiB),
 * but available without MARKDOWN_STREAMING_ENABLED.
 */
#define NGX_HTTP_MARKDOWN_STREAM_BUDGET_DEFAULT \
    (2 * 1024 * 1024)

/*
 * Unified resource-limits structure (0.9.2 markdown_limits key=value).
 *
 * Single source of truth for all resource-limit configuration.
 * Populated by the markdown_limits directive handler; merged per-key
 * from parent blocks.  Runtime consumers read from this structure
 * via the loc_conf.
 */
typedef struct {
    ngx_msec_t    conversion_timeout;   /* NGX_CONF_UNSET_MSEC */
    ngx_msec_t    parser_timeout;       /* NGX_CONF_UNSET_MSEC */
    size_t        conversion_memory;    /* NGX_CONF_UNSET_SIZE */
    size_t        parser_memory;        /* NGX_CONF_UNSET_SIZE */
    size_t        streaming_buffer;     /* NGX_CONF_UNSET_SIZE */
    size_t        decompressed_size;    /* NGX_CONF_UNSET_SIZE */
    ngx_uint_t    decompression_ratio;  /* NGX_CONF_UNSET_UINT */
    ngx_uint_t    max_inflight;         /* NGX_CONF_UNSET_UINT */
    ngx_flag_t    configured;            /* directive present in this block */
    /* Per-key explicit flags (0 = not set at this or any parent level). */
    ngx_flag_t    conversion_timeout_explicit; /* operator set conversion_timeout */
    ngx_flag_t    parser_timeout_explicit;     /* operator set parser_timeout */
    ngx_flag_t    conversion_memory_explicit;  /* operator set conversion_memory */
    ngx_flag_t    parser_memory_explicit;      /* operator set parser_memory */
    ngx_flag_t    streaming_buffer_explicit;   /* operator set streaming_buffer */
} ngx_http_markdown_limits_t;

/*
 * Defaults for the unified limits (0.9.2 frozen contract).
 */
#define NGX_HTTP_MARKDOWN_LIMITS_CONVERSION_TIMEOUT_DEFAULT  30000
#define NGX_HTTP_MARKDOWN_LIMITS_PARSER_TIMEOUT_DEFAULT      10000
#define NGX_HTTP_MARKDOWN_LIMITS_CONVERSION_MEMORY_DEFAULT \
    (64 * 1024 * 1024)
#define NGX_HTTP_MARKDOWN_LIMITS_PARSER_MEMORY_DEFAULT \
    (32 * 1024 * 1024)
#define NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT \
    (2 * 1024 * 1024)
#define NGX_HTTP_MARKDOWN_LIMITS_DECOMPRESSED_SIZE_DEFAULT \
    (10 * 1024 * 1024)
#define NGX_HTTP_MARKDOWN_LIMITS_DECOMPRESSION_RATIO_DEFAULT  100
#define NGX_HTTP_MARKDOWN_LIMITS_MAX_INFLIGHT_DEFAULT         64

/*
 * Fixed internal streaming flush minimum (16k).
 * Previously the markdown_stream_flush_min directive; now internalized.
 */
#define NGX_HTTP_MARKDOWN_STREAM_FLUSH_MIN_FIXED  16384

/*
 * Configuration constants for flavor directive
 */
#define NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK  0  /* CommonMark flavor */
#define NGX_HTTP_MARKDOWN_FLAVOR_GFM         1  /* GitHub Flavored Markdown */

/*
 * Configuration constants for auth_policy directive
 */
#define NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW  0  /* Allow conversion of authenticated requests */
#define NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY   1  /* Deny conversion of authenticated requests */

/*
 * Configuration constants for conditional_requests directive
 */
#define NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT         0  /* Full If-None-Match support */
#define NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE    1  /* If-Modified-Since only */
#define NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED             2  /* No conditional request support */

/*
 * Configuration constants for log verbosity directive
 *
 * This is a module-local verbosity filter for module-generated logs.
 * NGINX's global `error_log` level still applies as the outer filter.
 */
#define NGX_HTTP_MARKDOWN_LOG_ERROR  0  /* Only error/critical */
#define NGX_HTTP_MARKDOWN_LOG_WARN   1  /* Warnings and above */
#define NGX_HTTP_MARKDOWN_LOG_INFO   2  /* Informational and above (default) */
#define NGX_HTTP_MARKDOWN_LOG_DEBUG  3  /* Debug and above */

/*
 */

/*
 * Configuration source for markdown_filter directive
 */
#define NGX_HTTP_MARKDOWN_ENABLED_UNSET    0  /* Not configured in this scope */
#define NGX_HTTP_MARKDOWN_ENABLED_STATIC   1  /* markdown_filter on|off */
#define NGX_HTTP_MARKDOWN_ENABLED_COMPLEX  2  /* markdown_filter <variable/expr> */

/*
 * Compression type enumeration
 *
 * Identifies the compression format of upstream response content.
 * Used for automatic decompression of compressed HTML before conversion.
 */
typedef enum {
    NGX_HTTP_MARKDOWN_COMPRESSION_NONE = 0,     /* No compression */
    NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,         /* gzip compression */
    NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,      /* deflate compression */
    NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,       /* brotli compression */
    NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN       /* Unknown/unsupported compression */
} ngx_http_markdown_compression_type_e;

/*
 * Active location configuration structure
 *
 * This structure holds the effective configuration directives for the
 * Markdown filter at a location scope. It supports NGINX's
 * configuration inheritance model (http, server, location); the merged
 * values are the active ones used for request handling. Zero-value
 * compatibility fields for the internal Config V2 FFI are limited to
 * the legacy fields (max_size, timeout, and the corresponding fields
 * in decompress), documented on the compat bundle below.
 *
 * Configuration defaults (defined in ngx_http_markdown_create_loc_conf):
 * - enabled: NGX_CONF_UNSET (inherit from parent)
 * - enabled_source: NGX_HTTP_MARKDOWN_ENABLED_UNSET (inherit from parent)
 * - enabled_complex: NULL
 * - on_error: NGX_HTTP_MARKDOWN_ON_ERROR_PASS (fail-open)
 * - flavor: NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK
 * - token_estimate: NGX_CONF_UNSET (off by default)
 * - front_matter: NGX_CONF_UNSET (off by default)
 * - accept_policy: NGX_CONF_UNSET_UINT (strict by default)
 * - auth_policy: NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW
 * - auth_cookies: NULL (no patterns configured)
 * - generate_etag: 0 (off by default — ims_only mode)
 * - conditional_requests: NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE
 * - log_verbosity: NGX_HTTP_MARKDOWN_LOG_INFO
 * - stream_excluded_types: NULL (no exclusions by default)
 * - auto_decompress: 1 (on by default)
 * - ops.diagnostics_enabled: 0 (off by default)
 * - advanced.dynconf_dry_run: 0 (off by default)
 *
 * Unified limits defaults (0.9.2 frozen contract; merged via the
 * NGX_HTTP_MARKDOWN_LIMITS_*_DEFAULT macros):
 * - limits.conversion_timeout:
 *   NGX_HTTP_MARKDOWN_LIMITS_CONVERSION_TIMEOUT_DEFAULT (30000ms)
 * - limits.parser_timeout:
 *   NGX_HTTP_MARKDOWN_LIMITS_PARSER_TIMEOUT_DEFAULT (10000ms)
 * - limits.conversion_memory:
 *   NGX_HTTP_MARKDOWN_LIMITS_CONVERSION_MEMORY_DEFAULT (64MB)
 * - limits.parser_memory:
 *   NGX_HTTP_MARKDOWN_LIMITS_PARSER_MEMORY_DEFAULT (32MB)
 * - limits.streaming_buffer:
 *   NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT (2MB)
 * - limits.decompressed_size:
 *   NGX_HTTP_MARKDOWN_LIMITS_DECOMPRESSED_SIZE_DEFAULT (10MB)
 * - limits.decompression_ratio:
 *   NGX_HTTP_MARKDOWN_LIMITS_DECOMPRESSION_RATIO_DEFAULT (100)
 * - limits.max_inflight:
 *   NGX_HTTP_MARKDOWN_LIMITS_MAX_INFLIGHT_DEFAULT (64)
 *
 * v0.8.0 streaming config defaults (streaming configuration directives):
 * - stream.policy: auto
 * - stream.excluded_types: NULL
 */
/* sonarcloud-c:S1820: intentionally exceeded; fields are already logically
 * grouped via the ops sub-struct and #ifdef-gated streaming section.  Further
 * grouping (auth, content, pruning, llm, dynconf, response) would require
 * updating 160+ call sites across 15 files (offsetof directives, merge logic,
 * eligibility checks, conversion paths, tests) for no semantic benefit and
 * significant regression risk.  The field count reflects NGINX module
 * configuration breadth, not poor structure design. */
typedef struct {
    ngx_uint_t   auth_policy;          /* markdown_auth_policy allow|deny (default: allow) */
    ngx_array_t *auth_cookies;         /* markdown_auth_cookies patterns (default: NULL) */
    ngx_flag_t   generate_etag;        /* markdown_cache_validation (etag component) */
    ngx_uint_t   conditional_requests; /* markdown_cache_validation (conditional component) */
    ngx_uint_t   log_verbosity;        /* markdown_log_verbosity error|warn|info|debug (default: info) */
} ngx_http_markdown_policy_cfg_t;

typedef struct {
    ngx_flag_t   prune_noise;               /* markdown_prune_noise on|off (default: on) */
    ngx_str_t   *prune_selectors;           /* markdown_prune_selectors (default: built-in list) */
    ngx_str_t   *prune_protection_selectors; /* markdown_prune_protection_selectors (default: empty) */
    ngx_flag_t   dynconf_enabled;           /* markdown_dynamic_config on|off (default: off) */
    ngx_str_t    dynconf_path;              /* markdown_dynamic_config_path (default: empty) */
    ngx_flag_t   dynconf_dry_run;           /* markdown_dynconf_dry_run on|off (default: off) */
    /*
     * Per-field dynconf block mask (0.9.2 precedence model).
     *
     * One bit per dynconf-mutable field:
     *   bit 0: filter         (NGX_HTTP_MARKDOWN_BLOCK_FILTER)
     *   bit 1: prune_noise    (NGX_HTTP_MARKDOWN_BLOCK_PRUNE_NOISE)
     *   bit 2: log_verbosity  (NGX_HTTP_MARKDOWN_BLOCK_LOG_VERBOSITY)
     *   bit 3: error_policy   (NGX_HTTP_MARKDOWN_BLOCK_ERROR_POLICY)
     *   bit 4: streaming_buffer (NGX_HTTP_MARKDOWN_BLOCK_STREAMING_BUFFER)
     *
     * Bit is set when a server/location block explicitly configures
     * that field.  Propagated from parent to child via OR during merge.
     * An explicit http-block setting does NOT set the bit.
     */
    ngx_uint_t   dynconf_block_mask;
    ngx_uint_t   static_explicit_mask;
} ngx_http_markdown_advanced_cfg_t;

/*
 * Retained zero-value compatibility bundle for the internal Config V2 FFI.
 *
 * No public `markdown_profile` directive or request-path profile decision
 * exists in 0.9.2.  The bundle is not populated by the active command table
 * and remains only to keep the internal FFI layout stable.
 *
 * The bundle is value data only; it adds no request-path decision branch.
 */
typedef struct {
    ngx_flag_t   enabled;              /* markdown_filter static resolved value */
    ngx_uint_t   enabled_source;       /* markdown_filter source (static|complex|unset) */
    ngx_http_complex_value_t *enabled_complex; /* markdown_filter variable/complex expression */
    size_t       max_size;             /* legacy compat; decompressed_size via markdown_limits */
    ngx_msec_t   timeout;              /* legacy compat; conversion_timeout via markdown_limits */
    ngx_uint_t   on_error;             /* markdown_error_policy pass|fail_closed|status (default: pass) */
    ngx_uint_t   error_status;         /* markdown_error_policy status <code> (default: 502; honored on fail-closed) */
    ngx_uint_t   flavor;               /* markdown_flavor commonmark|gfm (default: commonmark) */
    ngx_flag_t   token_estimate;       /* markdown_token_estimate on|off (default: off) */
    ngx_flag_t   front_matter;         /* markdown_front_matter on|off (default: off) */
    ngx_uint_t   accept_policy;        /* markdown_accept strict|wildcard|force (default: strict) */
    ngx_http_markdown_policy_cfg_t policy;

    struct {
        ngx_array_t *content_types;        /* markdown_content_types allowlist */
        size_t       large_body_threshold; /* markdown_large_body_threshold */
        ngx_uint_t   max_inflight;         /* markdown_limits max_inflight */
    } routing;

    /*
     * Decompression/parsing limits.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_conf_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_flag_t   auto_decompress;      /* markdown_auto_decompress on|off (default: on) */
        size_t       max_size;             /* markdown_limits decompressed_size (default: same as max_size) */
        ngx_msec_t   parse_timeout;        /* markdown_limits parser_timeout (default: NGX_HTTP_MARKDOWN_LIMITS_PARSER_TIMEOUT_DEFAULT = 10000ms) */
        size_t       parser_budget;        /* legacy compat; parser_memory via markdown_limits */
        ngx_flag_t   max_size_explicit;    /* 1 if operator set markdown_limits memory at this or parent level */
    } decompress;

    /*
     * Operational settings.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_conf_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_flag_t   diagnostics_enabled; /* markdown_diagnostics on|off (default: off) */
        ngx_flag_t   metrics_enabled;     /* markdown_metrics endpoint enabled */
    } ops;

    /*
     * Unified resource limits (0.9.2 markdown_limits key=value).
     */
    ngx_http_markdown_limits_t  limits;

    /*
     * Unified streaming configuration (v0.8.0+).
     *
     * This is the sole runtime source-of-truth for all streaming
     * directives.  There is no compatibility layer from v0.6.x.
     */
    struct {
        ngx_uint_t    policy;              /* markdown_streaming off|auto|force */
        ngx_flag_t    policy_explicit;     /* 1 if operator set markdown_streaming */
        ngx_array_t  *excluded_types;      /* markdown_stream_excluded_types (default: NULL) */
        size_t        budget;              /* markdown_limits streaming_buffer (default: 2m) */
    } stream;

    /*
     * Noise pruning configuration.
     */
    ngx_http_markdown_advanced_cfg_t advanced;

} ngx_http_markdown_conf_t;


static ngx_inline size_t
ngx_http_markdown_effective_body_buffer_limit(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    size_t  budget;

    if (eff != NULL) {
        budget = eff->memory_budget;
    } else {
        budget = conf->limits.conversion_memory;
    }

    if (budget == 0 || budget == NGX_HTTP_MARKDOWN_CONF_UNSET_SIZE) {
        return conf->max_size;
    }

    if (conf->max_size == 0) {
        return budget;
    }

    return (budget < conf->max_size) ? budget : conf->max_size;
}


static ngx_inline ngx_uint_t
ngx_http_markdown_effective_error_policy(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return eff != NULL ? eff->error_policy : conf->on_error;
}


static ngx_inline ngx_uint_t
ngx_http_markdown_effective_error_status(
    const ngx_http_markdown_effective_conf_t *eff,
    const ngx_http_markdown_conf_t *conf)
{
    return eff != NULL ? eff->error_status : conf->error_status;
}


static ngx_inline void
ngx_http_markdown_merge_stream_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev)
{
/*
 * Helper macro: merge a single stream configuration field.
 * If the current value equals the unset sentinel, inherit from
 * the previous level or fall back to the compile-time default.
 */
#define NGX_MD_MERGE_STREAM(field, type, unset, dflt)                         \
    do {                                                                      \
        if (conf->stream.field == (type) (unset)) {                          \
            conf->stream.field = (prev->stream.field != (type) (unset))       \
                ? prev->stream.field : (dflt);                               \
        }                                                                    \
    } while (0)

    NGX_MD_MERGE_STREAM(policy, ngx_uint_t, -1,
                        NGX_HTTP_MARKDOWN_STREAMING_AUTO);
    NGX_MD_MERGE_STREAM(policy_explicit, ngx_flag_t, -1, 0);

    if (conf->stream.excluded_types == (ngx_array_t *) -1) {
        conf->stream.excluded_types =
            (prev->stream.excluded_types != (ngx_array_t *) -1)
                ? prev->stream.excluded_types : NULL;
    }

    NGX_MD_MERGE_STREAM(budget, size_t, -1,
                        NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT);

#undef NGX_MD_MERGE_STREAM
}

/*
 * Main configuration structure
 *
 * Holds process-wide shared state that is initialized once during
 * configuration parsing and then reused by all worker processes.
 *
 * The dynconf fields track the unique markdown_dynamic_config_path
 * directive and the location configuration that owns it.  The owner
 * pointer lets worker startup bind the single global watcher to an
 * http, server, or location configuration after inheritance merges.
 */
/* Forward declaration of the Rust-owned opaque trusted-proxy CIDR set
 * (defined by cbindgen in markdown_converter.h, included after this header
 * in the main translation unit).  A pointer to an incomplete type is all the
 * main conf needs. */
struct MarkdownTrustedProxies;

typedef struct {
    ngx_shm_zone_t *metrics_shm_zone;  /* Shared-memory zone for cross-worker metrics */
    size_t          metrics_shm_size;  /* Configured metrics SHM size (default: 8 pages) */
    ngx_flag_t      dynconf_path_configured; /* 1 after first markdown_dynamic_config_path directive */
    ngx_str_t       dynconf_first_path;      /* Path value from the first directive (for diagnostics) */
    /* Merged config that owns the unique dynconf path. */
    ngx_http_markdown_conf_t *dynconf_owner_conf;
    ngx_http_markdown_loc_validation_summary_t *loc_validation_summary;
    /*
     * spec 47: http-only trusted-proxy CIDR set for forwarded-header trust.
     * trusted_proxies is a Rust-owned opaque handle (NULL when the directive
     * is absent or set to "off"); trusted_proxies_configured records whether
     * the directive was present (so "off" can be distinguished from "unset"
     * for reason-code selection).  The handle is freed by an NGINX pool
     * cleanup handler, so it lives for the configuration cycle.
     */
    struct MarkdownTrustedProxies *trusted_proxies;
    ngx_flag_t      trusted_proxies_configured;
    ngx_array_t    *trusted_proxies_manifest;
#ifdef NGX_HTTP_BROTLI
    ngx_atomic_uint_t brotli_workspace_bytes;
    ngx_atomic_uint_t brotli_workspace_limit;
#endif
} ngx_http_markdown_main_conf_t;

/* Return the merged config selected to own the per-worker dynconf watcher. */
static ngx_inline ngx_http_markdown_conf_t *
ngx_http_markdown_dynconf_owner(
    const ngx_http_markdown_main_conf_t *main_conf)
{
    return main_conf != NULL ? main_conf->dynconf_owner_conf : NULL;
}

/*
 * Response buffer structure
 *
 * This structure accumulates upstream response body chunks before conversion.
 * It enforces size limits during buffering to prevent resource exhaustion.
 */
typedef struct {
    u_char      *data;      /* Buffer data */
    size_t       size;      /* Current size (bytes used) */
    size_t       capacity;  /* Current allocated capacity (bytes) */
    size_t       max_size;  /* Maximum allowed size (bytes) */
    ngx_pool_t  *pool;      /* Request pool for cleanup registration/logging */
} ngx_http_markdown_buffer_t;

/*
 * Error classification
 *
 * These enums and functions classify conversion failures into categories
 * for logging and metrics (FR-09.5, FR-09.6, FR-09.7).
 *
 * Defined before ngx_http_markdown_ctx_t because the context struct
 * contains a last_error_category field of this type.
 */

/* Error category enum */
typedef enum {
    NGX_HTTP_MARKDOWN_ERROR_CONVERSION,      /* HTML parsing errors, invalid input, conversion logic failures */
    NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,  /* Size limits exceeded, timeout exceeded */
    NGX_HTTP_MARKDOWN_ERROR_SYSTEM           /* Memory allocation failures, converter not initialized */
} ngx_http_markdown_error_category_t;

/*
 * Response eligibility validation
 *
 * Defined before ngx_http_markdown_ctx_t because function prototypes
 * referencing this type appear before the context struct definition.
 */

/* Eligibility result enum */
typedef enum {
    NGX_HTTP_MARKDOWN_ELIGIBLE,                /* Response is eligible for conversion */
    NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD,       /* Not GET/HEAD */
    NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,       /* Not 200; 206 Partial Content is ineligible (routed to INELIGIBLE_RANGE) */
    NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE, /* Not text/html */
    NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE,         /* Exceeds max_size */
    NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING,    /* Unbounded streaming (SSE, etc.) */
    NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH,         /* Auth policy denies */
    NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE,        /* Range request (partial content) */
    NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG        /* Disabled by config */
} ngx_http_markdown_eligibility_t;

/*
 * Request context structure
 *
 * This structure maintains per-request state for the Markdown filter.
 */
typedef struct {
    time_t      source_last_modified_time; /* Preserved upstream Last-Modified */
    ngx_flag_t  has_last_modified_time;    /* Whether Last-Modified was present */
} ngx_http_markdown_last_modified_state_t;

typedef struct {
    ngx_http_request_t          *request;
    ngx_chain_t                 *in;           /* Input chain */
    ngx_chain_t                 *out;          /* Output chain */
    ngx_http_markdown_buffer_t   buffer;       /* Response buffer */
    ngx_flag_t                   filter_enabled; /* Cached markdown_filter decision from header phase */
    ngx_flag_t                   buffer_initialized;
    ngx_flag_t                   eligible;     /* Eligible for conversion */
    ngx_flag_t                   headers_forwarded; /* Whether downstream headers were sent */

    /* Request-lifetime fields with matching release/representation scope. */
    struct {
        ngx_http_markdown_last_modified_state_t
                                    last_modified;
        ngx_http_markdown_inflight_cleanup_t *inflight_cleanup;
    } lifecycle;

    /*
     * Conversion tracking state.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_ctx_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_flag_t                   attempted;
        ngx_flag_t                   succeeded;
        ngx_flag_t                   delivery_recorded;
        ngx_flag_t                   bypass_counted;
        size_t                       input_bytes;
        size_t                       output_bytes;
    } conversion;

    /* Fail-open completed flag: prevents duplicate ngx_http_finalize_request
     * calls when fail-open path has already finalized the request.
     * Rule 38: set once, never cleared within a request lifetime. */
    ngx_flag_t                   failopen_completed;

    /*
     * Full-buffer backpressure state.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_ctx_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_chain_t             *pending_output;
        ngx_flag_t               pending_has_data;
        /* Fail-open delivery latch (Rule 38): set when a buffered
         * fail-open send returns NGX_AGAIN (downstream backpressure) so
         * the recovery pass-through can increment failopen_count after
         * the downstream filter confirms delivery.  Mirrors the
         * streaming path's pending_failopen_delivery latch. */
        ngx_flag_t               failopen_delivery_pending;
    } fullbuffer;

    /* Threshold router path selection (NGX_HTTP_MARKDOWN_PATH_FULLBUFFER,
     * NGX_HTTP_MARKDOWN_PATH_INCREMENTAL, or
     * NGX_HTTP_MARKDOWN_PATH_STREAMING) */
    ngx_uint_t                   processing_path;

    /* Copy of the active dynconf snapshot into request pool at header_filter
     * time.  NULL if dynconf is not enabled or pool allocation failed.
     * Prefer reading through effective_conf below rather than dereferencing
     * this directly. */
    ngx_http_markdown_dynconf_snapshot_t *dynconf_snapshot;

    /* Effective configuration view built at header_filter time.
     * Provides request-consistent values for all dynconf-mutable fields.
     * All body/conversion/logging/budget code should read mutable fields
     * through this view instead of directly from ngx_http_markdown_conf_t.
     *
     * Stored inline (by value) in the context so that no pool allocation
     * is needed: a request whose snapshot allocation failed must still
     * bind the header-time view, otherwise the body phase would fall
     * back to static live-conf values and observe a different
     * configuration than the header phase (bind-once violation). */
    ngx_http_markdown_effective_conf_t  effective_conf_storage;
    ngx_http_markdown_effective_conf_t *effective_conf;

    /*
     * Decompression state.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_ctx_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_http_markdown_compression_type_e  type;      /* Detected compression type */
        ngx_flag_t                            needed;    /* Whether decompression is needed */
        ngx_flag_t                            done;      /* Whether decompression completed */
        size_t                                compressed_size;   /* Size before decompression */
        size_t                                decompressed_size; /* Size after decompression */
        ngx_uint_t                            layer_count; /* Non-identity layers (0..3) */
        u_char                                layers[3];  /* Layer codes in application order */
        ngx_flag_t                            chain_parsed; /* Chain grammar validated */
    } decompression;

    /*
     * Error state.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_ctx_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_http_markdown_error_category_t    last_category;
        ngx_flag_t                           has_category;
        ngx_flag_t                           terminal_decision_recorded;
    } error;

    /*
     * v0.8.0 streaming state machine context (streaming fallback state machine).
     *
     * Unconditional (not feature-gated) because the state machine
     * governs all requests regardless of the streaming converter
     * feature flag.  Grouped into a sub-struct for SonarCloud
     * c:S1820 compliance.
     */
    struct {
        ngx_http_markdown_stream_state_e  state;           /* Current state machine state */
        ngx_http_markdown_buffer_t        replay_buf;      /* Replay buffer for pre-commit fallback */
        size_t                            replay_capacity; /* Max replay buffer size (from config) */
        ngx_flag_t                        replay_initialized;
        ngx_flag_t                        headers_committed; /* Header chain accepted (incl. NGX_AGAIN) */
    } stream_sm;

    /*
     * Streaming state sub-struct.
     *
     * This is unconditional because request-level pending output,
     * terminal-send latches, and post-commit completion state are NGINX
     * filter/backpressure concerns even when the Rust streaming FFI symbols
     * are not available in the linked static library.
     *
     * Grouped to comply with SonarCloud c:S1820 20-field limit.
     */
    struct {
        /* Streaming converter handle (Rust opaque pointer) */
        struct StreamingConverterHandle  *handle;

        /* Commit state: PRE or POST */
        ngx_uint_t                        commit_state;

        /* Engine choice reason code (streaming observability) */
#ifdef MARKDOWN_STREAMING_ENABLED
        ngx_http_markdown_stream_reason_e reason;
#else
        ngx_uint_t                        reason;
#endif

        /* Pending output chain for backpressure */
        ngx_chain_t                      *pending_output;

        /* Incremental decompressor state */
        void                             *decompressor;

        /* Per-request statistics */
        ngx_uint_t                        chunks_processed;
        ngx_uint_t                        flushes_sent;
        size_t                            total_input_bytes;
        struct {
            size_t                        bytes;
            unsigned                      overflowed:1;
        } output;
        unsigned                          main_terminal_sent:1;
        unsigned                          subrequest_terminal_sent:1;

        /* TTFB tracking (from first feed to first non-empty output) */
        struct {
            ngx_msec_t                        feed_start_ms;
            ngx_flag_t                        recorded;
        } ttfb;

        /*
         * Pending-output metadata: auxiliary flags/counters describing the
         * pending_output chain. Grouped into a sub-struct to keep the parent
         * streaming struct below SonarCloud c:S1820 20-field limit.
         */
        struct {
            /* Pending output chain has non-empty data (for TTFB resume path) */
            ngx_flag_t                        has_data;

            /* Pending output byte count (for deferred metric accounting) */
            size_t                            bytes;

            /*
             * Terminal metadata captured BEFORE the first downstream
             * body-filter call (Rule 1/47 ownership boundary).
             *
             * The pending_output chain may be multi-link (fail-open replay
             * prefix + cloned input + terminal tail).  Scanning only the
             * chain head in resume_pending() misses a terminal tail.
             * Re-scanning the downstream-retained chain after NGX_AGAIN is
             * unreliable because downstream may mutate buf metadata.
             *
             * Instead, the caller that crosses the downstream ownership
             * boundary captures terminal state from the full chain and
             * stores it here.  resume_pending() consumes these latches
             * only after downstream confirms delivery (NGX_OK/NGX_DONE),
             * preserving Rule 47 (terminal-sent latch only after confirmed
             * delivery).
             *
             * main_terminal: any link carries last_buf (main request EOF).
             * subrequest_terminal: any link carries last_in_chain
             *   (subrequest EOF — must NOT latch main_terminal_sent).
             */
            ngx_flag_t                        main_terminal;
            ngx_flag_t                        subrequest_terminal;

            /*
             * Abort terminal is pending downstream delivery. This marker
             * is set only after an abort terminal returns NGX_AGAIN and is
             * consumed after the downstream-owned chain gets a definitive
             * result.
             */
            ngx_flag_t                        pending_abort_terminal;

            /*
             * Rust output produced before header commit completed.  This
             * buffer is module-owned, not downstream-owned, and must be
             * delivered only after the deferred header filter succeeds.
             */
            u_char                            *pending_header_output;
            size_t                             pending_header_output_len;
        } pending_meta;

        /* Pre-Commit prebuffer for fallback */
        ngx_http_markdown_buffer_t        prebuffer;
        size_t                            prebuffer_limit;
        ngx_flag_t                        prebuffer_initialized;

        /*
         * Fail-open replay buffer: a request-owned copy of original upstream
         * bytes consumed during Pre-Commit.  On fail-open, we rebuild the
         * output chain from this buffer rather than restoring upstream
         * ngx_buf_t* positions, which is fragile across filter chain
         * invocations, temporary buffers, and subrequest scenarios.
         */
        ngx_http_markdown_buffer_t        failopen_replay_buf;
        ngx_flag_t                        failopen_replay_initialized;

        /*
         * Input/send state classification fields.
         *
         * Grouped into a sub-struct to keep the parent streaming struct
         * below the SonarCloud c:S1820 20-field limit.
         *
         * input_disposition: decoupled from downstream return code.
         *   NGX_HTTP_MD_INPUT_CONSUMED (0) - Rust ate the input chunk;
         *     advance buf->pos and enqueue remainder to pending_input.
         *   NGX_HTTP_MD_INPUT_RETAIN (1) - fail-open shared ngx_buf_t;
         *     do NOT advance pos (would corrupt pending fail-open output).
         *   NGX_HTTP_MD_INPUT_TERMINAL (2) - post-commit fatal; input
         *     abandoned, release upstream buffers.
         *
         * last_send_failure_origin: set by send_output /
         *   send_output on NGX_ERROR return.
         *   Read by handle_success_output to classify post-commit
         *   failures into allocation, downstream, or invariant.
         *   Reset to NONE before each send call.
         */
        struct {
            ngx_uint_t                    input_disposition;
            ngx_uint_t                    last_send_failure_origin;
        } classify;

        /*
         * Module-owned pending input chain for backpressure continuation.
         *
         * When downstream returns NGX_AGAIN, the current chunk has been
         * fed to Rust (CONSUMED) but the remaining links (cl->next) in
         * the input chain must be retained so they are not stranded in
         * u->busy_bufs.  Links are pool-allocated ngx_chain_t copies that
         * share the original ngx_buf_t (no payload duplication).  NGINX
         * keeps busy buffers alive while pos < last, so the shared bufs
         * remain valid until we advance pos after feeding them to Rust.
         *
         * Also captures future non-NULL input arriving while pending
         * output exists (the body-filter entry enqueues instead of
         * rejecting).
         */
        struct {
            ngx_chain_t              *head;
            ngx_chain_t              *tail;
            size_t                    bytes;
            ngx_uint_t                links;
        } pending_input;

        /*
         * Finalize-path state latches.
         *
         * Grouped to keep the parent streaming struct below SonarCloud
         * c:S1820 field-count threshold while preserving semantics.
         */
        struct {
            /* Deferred terminal last_buf (backpressure during finalize) */
            ngx_flag_t                    finalize_pending_lastbuf;

            /* Upstream EOF survives output backpressure independently of
             * whether any remainder was queued in pending_input. */
            ngx_flag_t                    upstream_terminal_seen;

            /* Metrics deferred for terminal last_buf (backpressure on
             * terminal send — set when send_output(last_buf=1)
             * returns NGX_AGAIN, cleared when resume drain succeeds
             * or fails). */
            ngx_flag_t                    pending_terminal_metrics;

            /* Post-commit failure metrics recorded for this request. */
            ngx_flag_t                    failure_recorded;

            /* Continue finalize() after tail-output backpressure drains. */
            ngx_flag_t                    finalize_after_pending;

            /* Finalize output deferred behind a header NGX_AGAIN retry.
             * Body output must not run ahead of headers, so the final
             * markdown chunk waits for the header retry to succeed.  The
             * pointer owns the MarkdownResult and its Rust-allocated
             * buffers; release with markdown_result_free(). */
            struct MarkdownResult        *finalize_pending_result;

            /* Pending output is a fail-open delivery; resume_pending
             * should increment results.failopen_count on downstream
             * success (Rule 38). */
            ngx_flag_t                    pending_failopen_delivery;

            /* Request has selected fail-open passthrough. Future input
             * bypasses Rust and continues directly downstream. */
            ngx_flag_t                    failopen_active;

            /* Fail-open mode selected and future input could not be
             * retained behind pending output (budget/allocation).
             * After pending output drains, abort without a clean last_buf;
             * known missing bytes must remain protocol-visible. */
            ngx_flag_t                    failopen_abort_after_pending;
            uint32_t                      failopen_abort_error_code;

            /* Post-commit input failure waiting for older downstream-owned
             * output to drain before safe_finish is allowed to run. */
            ngx_flag_t                    postcommit_error_after_pending;
            uint32_t                      postcommit_error_code;

            /* A safe-finish terminal or closing chain is downstream-owned.
             * Record the original decoder/parser failure only after that
             * pending chain has a definitive delivery result. */
            ngx_flag_t                    safe_finish_error_pending;
            uint32_t                      safe_finish_error_code;

            /* Safe-finish produced closing Markdown that could not be
             * constructed or delivered.  The Rust handle is consumed, so
             * the caller must hard-abort instead of sending a clean terminal. */
            ngx_flag_t                    safe_finish_output_loss;

            /* Rust safe-finish succeeded with zero closing bytes, but the
             * empty terminal chain send failed definitively.  The caller
             * must NOT retry the terminal via abort — propagate the send
             * failure directly. */
            ngx_flag_t                    safe_finish_terminal_send_failed;

            /* One-shot latch: protocol-safe abort metric has been recorded
             * for this request. Independent of stream_sm.state because the
             * post-commit error path may pre-transition the state before
             * invoking postcommit_abort(). */
            ngx_flag_t                    postcommit_abort_recorded;

            /* One-shot latch: terminal abort outcome has been recorded.
             * Set when the abort terminal chain is confirmed delivered
             * (NGX_OK/NGX_DONE), ensuring exactly-one terminal outcome
             * per request (Rule 38/23: delivery ≠ decision counters). */
            ngx_flag_t                    terminal_aborted_recorded;
        } completion;
    } streaming;

} ngx_http_markdown_ctx_t;

/*
 * Metrics structure for observability
 *
 * This structure tracks conversion operations for monitoring and troubleshooting.
 * All counters use atomic operations for thread-safe updates across NGINX workers.
 *
 * Requirements: FR-09.8, FR-13.4
 *
 * Minimum Observability Fields (v1 Required):
 * - conversion_triggered: Total number of conversion attempts
 * - conversion_result: Counts by result type (success, failure, bypassed)
 * - failure_reason: Counts by failure category (conversion_error, resource_limit, system_error)
 * - conversion_time_ms: Sum of conversion times for averaging
 * - input_size_bytes: Sum of input sizes
 * - output_size_bytes: Sum of output sizes
 *
 * Usage Example:
 *
 *   On conversion attempt:
 *   ngx_atomic_fetch_add(&metrics->conversions_attempted, 1);
 *
 *   On success:
 *   ngx_atomic_fetch_add(&metrics->conversions_succeeded, 1);
 *   ngx_atomic_fetch_add(&metrics->input_bytes, html_len);
 *   ngx_atomic_fetch_add(&metrics->output_bytes, markdown_len);
 *   ngx_atomic_fetch_add(&metrics->conversion_time_sum_ms, elapsed_ms);
 *
 *   On failure:
 *   ngx_atomic_fetch_add(&metrics->conversions_failed, 1);
 *   switch (category) {
 *       case NGX_HTTP_MARKDOWN_ERROR_CONVERSION:
 *           ngx_atomic_fetch_add(&metrics->failures_conversion, 1);
 *           break;
 *       case NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT:
 *           ngx_atomic_fetch_add(&metrics->failures_resource_limit, 1);
 *           break;
 *       case NGX_HTTP_MARKDOWN_ERROR_SYSTEM:
 *           ngx_atomic_fetch_add(&metrics->failures_system, 1);
 *           break;
 *   }
 *
 *   On bypass (ineligible request):
 *   ngx_atomic_fetch_add(&metrics->conversions_bypassed, 1);
 *
 * Thread Safety:
 * - All fields use ngx_atomic_t for lock-free atomic operations
 * - Safe to update from multiple NGINX worker processes concurrently
 * - No per-request mutex or spinlock required for counter updates
 *
 * Memory Layout:
 * - Structure is allocated in shared memory for cross-worker visibility
 * - All workers update the same counters, so the metrics endpoint reports
 *   aggregate values instead of worker-local snapshots
 */
typedef struct {
    /* Conversion attempt tracking */
    ngx_atomic_t  conversions_attempted;    /* Total conversion attempts (conversion_triggered) */
    ngx_atomic_t  conversions_succeeded;    /* Successful conversions (conversion_result=success) */
    ngx_atomic_t  conversions_failed;       /* Failed conversions (conversion_result=failure) */
    ngx_atomic_t  conversions_bypassed;     /* Bypassed/ineligible requests (conversion_result=bypassed) */

    /* Failure classification (failure_reason breakdown) */
    ngx_atomic_t  failures_conversion;      /* Conversion errors (HTML parse, encoding, invalid input) */
    ngx_atomic_t  failures_resource_limit;  /* Resource limit errors (timeout, memory limit) */
    ngx_atomic_t  failures_system;          /* System errors (internal errors, unexpected conditions) */

    /* Performance metrics (optional but recommended) */
    ngx_atomic_t  conversion_time_sum_ms;   /* Sum of conversion times in milliseconds (for averaging) */
    ngx_atomic_t  input_bytes;              /* Sum of input HTML sizes in bytes */
    ngx_atomic_t  output_bytes;             /* Sum of output Markdown sizes in bytes */

    /*
     * Latency histogram buckets.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_metrics_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     * The JSON/text output format is unaffected — keys are still
     * emitted as flat "conversion_latency_buckets" sub-object.
     */
    struct {
        ngx_atomic_t  le_10ms;     /* Completed conversions <= 10ms */
        ngx_atomic_t  le_100ms;    /* Completed conversions <= 100ms */
        ngx_atomic_t  le_1000ms;   /* Completed conversions <= 1000ms */
        ngx_atomic_t  gt_1000ms;   /* Completed conversions > 1000ms */
    } conversion_latency;

    /*
     * Frozen v1 histogram storage, split by conversion engine.  The
     * legacy aggregate above remains for the pre-v1 JSON/text helpers;
     * these bounded fields provide the engine label required by the
     * Prometheus v1 contract without per-request or per-path state.
     */
    struct {
        struct {
            ngx_atomic_t  le_1ms;
            ngx_atomic_t  le_5ms;
            ngx_atomic_t  le_10ms;
            ngx_atomic_t  le_25ms;
            ngx_atomic_t  le_50ms;
            ngx_atomic_t  le_100ms;
            ngx_atomic_t  le_250ms;
            ngx_atomic_t  le_500ms;
            ngx_atomic_t  le_1000ms;
            ngx_atomic_t  le_5000ms;
            ngx_atomic_t  sum_ms;
            ngx_atomic_t  count;
        } full_buffer;
        struct {
            ngx_atomic_t  le_1ms;
            ngx_atomic_t  le_5ms;
            ngx_atomic_t  le_10ms;
            ngx_atomic_t  le_25ms;
            ngx_atomic_t  le_50ms;
            ngx_atomic_t  le_100ms;
            ngx_atomic_t  le_250ms;
            ngx_atomic_t  le_500ms;
            ngx_atomic_t  le_1000ms;
            ngx_atomic_t  le_5000ms;
            ngx_atomic_t  sum_ms;
            ngx_atomic_t  count;
        } streaming;
    } conversion_latency_v1;

    /*
     * Decompression metrics.
     *
     * Grouped into an anonymous sub-struct so that the parent
     * ngx_http_markdown_metrics_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     * The JSON/text output format is unaffected — keys are still
     * emitted as flat "decompressions_*" names.
     */
    struct {
        ngx_atomic_t  attempted;   /* Total decompression attempts */
        ngx_atomic_t  succeeded;   /* Successful decompressions */
        ngx_atomic_t  failed;      /* Failed decompressions */
        ngx_atomic_t  gzip;        /* Gzip decompressions */
        ngx_atomic_t  deflate;     /* Deflate decompressions */
        ngx_atomic_t  brotli;      /* Brotli decompressions */
        ngx_atomic_t  budget_exceeded_total;  /* Decompression budget exceeded */
        ngx_atomic_t  format_error_total;     /* Invalid compression format */
        ngx_atomic_t  truncated_input_total;  /* Truncated compressed input */
        ngx_atomic_t  io_error_total;         /* Decompression I/O error */
        struct {
            ngx_atomic_t  budget;
            ngx_atomic_t  format;
            ngx_atomic_t  truncated;
            ngx_atomic_t  io;
        } gzip_failures;
        struct {
            ngx_atomic_t  budget;
            ngx_atomic_t  format;
            ngx_atomic_t  truncated;
            ngx_atomic_t  io;
        } deflate_failures;
        struct {
            ngx_atomic_t  budget;
            ngx_atomic_t  format;
            ngx_atomic_t  truncated;
            ngx_atomic_t  io;
        } brotli_failures;
    } decompressions;

    /*
     * Path hit metrics (threshold router).
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_metrics_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_atomic_t  fullbuffer;      /* Requests routed to full-buffer path */
        ngx_atomic_t  incremental;     /* Requests routed to incremental path */
#ifdef MARKDOWN_STREAMING_ENABLED
        ngx_atomic_t  streaming;       /* Requests routed to streaming path */
#endif
    } path_hits;

    /*
     * Total requests that entered the decision chain.
     *
     * Incremented in the header filter when a request reaches the module
     * decision chain, including requests later classified as disabled.
     * This is the broad denominator for module decision-rate calculations.
     */
    ngx_atomic_t  requests_entered;

#ifdef MARKDOWN_STREAMING_ENABLED
    /*
     * Streaming metrics sub-struct.
     *
     * Grouped to comply with SonarCloud c:S1820
     * 20-field limit.
     */
    struct {
        ngx_atomic_t  requests_total;          /* Entered streaming path */
        ngx_atomic_t  fallback_total;          /* Pre-Commit fallbacks */
        ngx_atomic_t  succeeded_total;         /* Streaming successes */
        ngx_atomic_t  commit_total;            /* Successful header commits */
        ngx_atomic_t  failed_total;            /* Streaming failures */
        ngx_atomic_t  postcommit_error_total;  /* Post-Commit errors */
        ngx_atomic_t  precommit_failopen_total;  /* Pre-Commit fail-open */
        ngx_atomic_t  precommit_reject_total;    /* Pre-Commit fail-closed */
        ngx_atomic_t  budget_exceeded_total;     /* Memory budget exceeded */
#ifdef MARKDOWN_STREAMING_SHADOW_DEBUG
        ngx_atomic_t  shadow_total;              /* Shadow mode runs (debug only) */
        ngx_atomic_t  shadow_diff_total;         /* Shadow output diffs (debug only) */
#endif
        ngx_atomic_t  last_ttfb_ms;              /* Last streaming TTFB (milliseconds) */
        ngx_atomic_t  last_peak_memory_bytes;    /* Last streaming peak estimate (bytes; not RSS) */

        /* Fallback/failure counters */
        ngx_atomic_t  streaming_fallback_precommit_pass;  /* Pre-commit HTML pass-through */
        ngx_atomic_t  streaming_fallback_precommit_reject; /* Pre-commit rejection */
        ngx_atomic_t  streaming_failure_postcommit_abort;  /* Post-commit abort */
        ngx_atomic_t  streaming_failure_postcommit_safe_finish; /* Post-commit safe finish */
        ngx_atomic_t  terminal_aborted_total;             /* Terminal abort outcome (delivery confirmed) */

        /* Engine choice counters (v0.8.0 observability) */
        struct {
            ngx_atomic_t  streaming;   /* Chose true streaming engine */
            ngx_atomic_t  full_buffer; /* Chose full-buffer engine */
            ngx_atomic_t  passthrough; /* Marked passthrough */
            ngx_atomic_t  not_eligible; /* Not eligible for streaming */
        } engine_choice;

        /* Candidate and selection counters */
        struct {
            ngx_atomic_t  candidate_total;       /* Total candidates evaluated */
            ngx_atomic_t  true_streaming_selected_total;   /* Final true streaming selections */
            ngx_atomic_t  output_bytes_total;    /* Total Markdown bytes via streaming */
            ngx_atomic_t  excluded_content_type_total;     /* Excluded due to content type */
        } selection;
    } streaming;
#endif

    /*
     * Skip counters by reason code.
     *
     * Each field corresponds to a specific eligibility check
     * failure.  Grouped into a sub-struct so that the parent
     * ngx_http_markdown_metrics_t stays within the 20-field
     * limit enforced by static analysis (SonarCloud rule
     * c:S1820).
     */
    struct {
        ngx_atomic_t  config;        /* SKIP_CONFIG */
        ngx_atomic_t  method;        /* SKIP_METHOD */
        ngx_atomic_t  status;        /* SKIP_STATUS */
        ngx_atomic_t  content_type;  /* SKIP_CONTENT_TYPE */
        ngx_atomic_t  size;          /* SKIP_SIZE */
        ngx_atomic_t  streaming;     /* SKIP_STREAMING */
        ngx_atomic_t  auth;          /* SKIP_AUTH */
        ngx_atomic_t  range;         /* SKIP_RANGE */
        ngx_atomic_t  accept;        /* SKIP_ACCEPT */
        ngx_atomic_t  no_accept;     /* SKIPPED_NO_ACCEPT */
        ngx_atomic_t  conditional;   /* SKIPPED_CONDITIONAL */
        ngx_atomic_t  compression_passthrough; /* SKIP_COMPRESSION_PASSTHROUGH */
        ngx_atomic_t  no_transform;  /* BYPASS_NO_TRANSFORM */
    } skips;

    /*
     * Conversion result counters.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_metrics_t stays within the 20-field
     * limit enforced by static analysis (SonarCloud rule
     * c:S1820).  The JSON/text output format is unaffected
     * — keys are still emitted as flat names.
     */
    struct {
        ngx_atomic_t  failopen_count;
        ngx_atomic_t  delivery_count;
        ngx_atomic_t  full_buffer_delivery_count;
        ngx_atomic_t  decision_count;
        ngx_atomic_t  estimated_token_savings;
        ngx_atomic_t  replay_buffer_errors_total;

        struct {
            ngx_atomic_t  success;
            ngx_atomic_t  failure_schema_version;
            ngx_atomic_t  failure_unknown_key;
            ngx_atomic_t  failure_duplicate_key;
            ngx_atomic_t  failure_invalid_type;
            ngx_atomic_t  failure_out_of_range;
            ngx_atomic_t  failure_size_exceeded;
            ngx_atomic_t  failure_parse_error;
            ngx_atomic_t  failure_file_error;
        } dynconf_reloads;

        struct {
            ngx_atomic_t  parse_timeouts_total;
            ngx_atomic_t  parse_budget_exceeded_total;
        } parse_interrupts;
    } results;

    /*
     * Performance metrics: backpressure, decompression path,
     * and output delivery mode.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_metrics_t stays within the 20-field
     * limit enforced by static analysis (SonarCloud rule
     * c:S1820).
     */
    struct {
        ngx_atomic_t  backpressure_total;
        ngx_atomic_t  backpressure_resume_total;
        ngx_atomic_t  pending_output_high_watermark_bytes;
        ngx_atomic_t  decompression_streaming_total;
        ngx_atomic_t  decompression_fullbuffer_total;
        ngx_atomic_t  decompression_budget_exceeded_total;
        ngx_atomic_t  copied_output_total;
    } perf;

    /*
     * Per-path metrics (removed in 0.9.2 — unbounded cardinality risk).
     * Retained under debug guard for development diagnostics only.
     */
#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG

#define NGX_HTTP_MARKDOWN_PER_PATH_MAX_RETAINED_LEN  1024
    struct {
        ngx_rbtree_t       path_tree;
        ngx_rbtree_node_t  sentinel;
        ngx_atomic_t       path_entries;
        ngx_atomic_t       path_conversions;
        ngx_atomic_t       path_conversion_time_sum_ms;
        ngx_uint_t         cardinality_limit;
        ngx_atomic_t       overflow_count;
        ngx_atomic_t       unretained_conversions;
        ngx_atomic_t       unretained_conversion_time_sum_ms;
    } per_path;
#endif /* MARKDOWN_METRICS_PER_PATH_DEBUG */
} ngx_http_markdown_metrics_t;

/* Called by the production dynconf watcher after each reload attempt. */
void ngx_http_markdown_record_dynconf_reload(ngx_uint_t error_code);

/*
 * Cross-translation-unit metric ownership helpers used by postcommit output.
 * Pending metrics are recorded when downstream returns NGX_AGAIN and the
 * module retains the output anchor.  Copied delivery metrics are recorded only
 * after immediate downstream delivery is confirmed; deferred delivery is
 * accounted by the shared streaming pending-resume path.
 */
void ngx_http_markdown_metrics_record_postcommit_pending(size_t bytes);
void ngx_http_markdown_metrics_record_postcommit_copied_delivery(size_t bytes);
void ngx_http_markdown_metrics_record_postcommit_abort(void);
void ngx_http_markdown_metrics_record_postcommit_safe_finish(void);

/*
 * Per-path metric node stored in the shared RB-tree.
 * Removed from production in 0.9.2 (unbounded cardinality risk).
 * Retained under debug guard for development diagnostics only.
 */
#ifdef MARKDOWN_METRICS_PER_PATH_DEBUG
typedef struct {
    ngx_rbtree_node_t  rbnode;
    ngx_uint_t         path_len;
    u_char            *path;
    ngx_atomic_t       conversions;
    ngx_atomic_t       conversion_time_sum_ms;
    ngx_atomic_t       entries;
} ngx_http_markdown_path_metric_node_t;
#endif /* MARKDOWN_METRICS_PER_PATH_DEBUG */

/* Module declaration */
extern ngx_module_t ngx_http_markdown_filter_module;

/* Forward declarations for FFI types used in public module interfaces */
struct MarkdownConverterHandle;

/*
 * Accept header negotiation
 *
 * Delegates to Rust FFI markdown_negotiate_accept for RFC 7231 / 9110
 * content negotiation. The C side extracts the Accept header from the
 * request and maps the FFI result to skip metrics.
 */

/* Determine if request should be converted based on Accept header */
ngx_int_t ngx_http_markdown_should_convert(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf, ngx_uint_t *out_reason);

/* Resolve markdown_filter on/off state for the current request */
ngx_flag_t ngx_http_markdown_is_enabled(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff);

/*
 * Response buffer functions
 *
 * These functions manage buffering of upstream response bodies.
 */

/* Initialize response buffer with size limit */
ngx_int_t ngx_http_markdown_buffer_init(ngx_http_markdown_buffer_t *buf,
    size_t max_size, ngx_pool_t *pool);

/* Pre-reserve buffer capacity (bounded by max_size) when size hints are available */
ngx_int_t ngx_http_markdown_buffer_reserve(ngx_http_markdown_buffer_t *buf,
    size_t capacity_hint);

/* Append data to buffer with size limit checking */
ngx_int_t ngx_http_markdown_buffer_append(ngx_http_markdown_buffer_t *buf,
    const u_char *data, size_t len);

/* Actively release the heap-allocated backing store (subrequest subrequest
 * lifecycle).  Idempotent; the pool cleanup registered by init remains
 * as the fallback. */
void ngx_http_markdown_buffer_release(ngx_http_markdown_buffer_t *buf);

/*
 * Error classification functions
 *
 * (Enum ngx_http_markdown_error_category_t is defined above,
 * before ngx_http_markdown_ctx_t.)
 */

/* Map Rust error code to error category */
ngx_http_markdown_error_category_t ngx_http_markdown_classify_error(uint32_t error_code);

/* Get human-readable string for error category */
const ngx_str_t *ngx_http_markdown_error_category_string(
    ngx_http_markdown_error_category_t category);

/*
 * Response eligibility validation functions
 *
 * (Enum ngx_http_markdown_eligibility_t is defined above,
 * before ngx_http_markdown_ctx_t.)
 */

/* Check if response is eligible for conversion */
ngx_http_markdown_eligibility_t ngx_http_markdown_check_eligibility(
    const ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf,
    ngx_flag_t filter_enabled,
    const ngx_http_markdown_effective_conf_t *eff);

/* Get human-readable string for eligibility result */
const ngx_str_t *ngx_http_markdown_eligibility_string(
    ngx_http_markdown_eligibility_t eligibility);

/* Check whether a content type is excluded from streaming (streaming configuration directives) */
ngx_int_t ngx_http_markdown_stream_type_excluded(
    const ngx_str_t *content_type,
    const ngx_http_markdown_conf_t *conf);

/*
 * Reason code lookup functions
 *
 * These functions map existing eligibility enum values and error categories
 * to stable uppercase snake_case reason code strings.  The returned strings
 * are shared between decision log entries and Prometheus metrics labels so
 * that operators can correlate logs with metric counters without translating
 * between different vocabularies.
 */

/* Map eligibility enum to reason code string */
const ngx_str_t *ngx_http_markdown_reason_from_eligibility(
    ngx_http_markdown_eligibility_t eligibility, ngx_log_t *log);

/* Map error category enum to failure reason code string */
const ngx_str_t *ngx_http_markdown_reason_from_error_category(
    ngx_http_markdown_error_category_t category, ngx_log_t *log);

/* Return the ELIGIBLE_CONVERTED reason code */
const ngx_str_t *ngx_http_markdown_reason_converted(void);

/* Return the ELIGIBLE_FAILED_OPEN reason code */
const ngx_str_t *ngx_http_markdown_reason_failed_open(void);

/* Return the ELIGIBLE_FAILED_CLOSED reason code */
const ngx_str_t *ngx_http_markdown_reason_failed_closed(void);
const ngx_str_t *ngx_http_markdown_reason_header_plan_apply_err(void);

/* Return the SKIP_ACCEPT reason code (not in eligibility enum) */
const ngx_str_t *ngx_http_markdown_reason_skip_accept(void);

/* Return the SKIPPED_NO_ACCEPT reason code (no Accept header) */
const ngx_str_t *ngx_http_markdown_reason_skip_no_accept(void);

/* Return the SKIPPED_ACCEPT_REJECT reason code (q=0 explicit reject) */
const ngx_str_t *ngx_http_markdown_reason_skip_accept_reject(void);

/* Return the SKIPPED_CONDITIONAL reason code (304 Not Modified) */
const ngx_str_t *ngx_http_markdown_reason_skip_conditional(void);

/* Return the BYPASS_NO_TRANSFORM reason code (RFC 9111 §5.2.2.6) */
const ngx_str_t *ngx_http_markdown_reason_bypass_no_transform(void);
const ngx_str_t *ngx_http_markdown_reason_encoding_header_invalid(void);
const ngx_str_t *ngx_http_markdown_reason_decompression_format_error(void);

/* Return the compressed-response passthrough reason in every build mode. */
const ngx_str_t *ngx_http_markdown_reason_streaming_skip_compressed(void);

#ifdef MARKDOWN_STREAMING_ENABLED
/* Streaming reason code accessors */
const ngx_str_t *ngx_http_markdown_reason_engine_streaming(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_convert(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_fallback(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_fail_postcommit(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_skip_unsupported(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_budget_exceeded(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_precommit_failopen(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_precommit_reject(void);
#ifdef MARKDOWN_STREAMING_SHADOW_DEBUG
const ngx_str_t *ngx_http_markdown_reason_streaming_shadow(void);
#endif
const ngx_str_t *ngx_http_markdown_reason_eligible_streaming_auto(void);
const ngx_str_t *ngx_http_markdown_reason_eligible_fullbuffer_auto(void);
#endif /* MARKDOWN_STREAMING_ENABLED */

/*
 * Rust FFI reason code accessors (v0.7.0+)
 *
 * These functions access reason code strings from the Rust-defined enum
 * via FFI. The declarative reason_registry.toml is the single source; the
 * Rust enum and C metadata are generated projections.
 *
 * New code should prefer these accessors over the legacy C-side string
 * literals defined above.  The legacy functions remain for backward
 * compatibility during the migration period.
 *
 * DO NOT define new reason code constants in C.  All reason codes must
 * come from the generated registry projections via these FFI accessors.
 */

/* Get reason code string from Rust enum (returns NGX_OK/NGX_DECLINED) */
ngx_int_t ngx_http_markdown_get_reason_code_str(uint32_t code,
    ngx_str_t *out_str);

/* Get Prometheus metric key from Rust enum (returns NGX_OK/NGX_DECLINED) */
ngx_int_t ngx_http_markdown_get_reason_code_metric_key(uint32_t code,
    ngx_str_t *out_str);

/* Get total number of reason codes defined in Rust */
uint32_t ngx_http_markdown_reason_code_total_count(void);

/*
 * Header management functions
 *
 * These functions handle HTTP header updates for Markdown responses.
 */

/* Update response headers after successful conversion */
ngx_int_t ngx_http_markdown_update_headers(ngx_http_request_t *r,
    const struct MarkdownResult *result, const ngx_http_markdown_conf_t *conf);
ngx_int_t ngx_http_markdown_head_representation_headers(ngx_http_request_t *r);
void ngx_http_markdown_clear_trailers(ngx_http_request_t *r);

/* Remove Content-Encoding header (called after decompression) */
void ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r);

/* Shared header helpers used by both full-buffer and streaming paths */
#define NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL  "text/markdown; charset=utf-8"
extern u_char ngx_http_markdown_content_type[];
#define NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN \
    (sizeof(NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL) - 1)
ngx_int_t ngx_http_markdown_add_vary_accept(ngx_http_request_t *r);
ngx_int_t ngx_http_markdown_set_etag(ngx_http_request_t *r,
    const u_char *etag, size_t etag_len);

/*
 * Authentication detection and cache control functions
 *
 * These functions detect authenticated requests and modify cache headers
 * to ensure secure caching behavior.
 */

ngx_int_t ngx_http_markdown_is_authenticated(const ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf);
ngx_int_t ngx_http_markdown_modify_cache_control_for_auth(
    ngx_http_request_t *r);

#ifndef NGX_HTTP_MARKDOWN_AUTH_CACHE_CONTROL_HELPER_DEFINED
#define NGX_HTTP_MARKDOWN_AUTH_CACHE_CONTROL_HELPER_DEFINED 1

/*
 * Apply the authenticated-response cache policy at the final header
 * emission boundary.  This helper is static because the implementation
 * header is included by several independent translation units and unit
 * harnesses; keeping the policy here prevents full-buffer, streaming,
 * conditional, HEAD, and pass-through paths from drifting apart.
 */
static ngx_int_t
ngx_http_markdown_apply_auth_cache_control(
    ngx_http_request_t *r, const ngx_http_markdown_conf_t *conf)
{
#if NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
    if (r == NULL) {
        return NGX_ERROR;
    }

    if (conf != NULL && ngx_http_markdown_is_authenticated(r, conf)) {
        return ngx_http_markdown_modify_cache_control_for_auth(r);
    }
#else
    (void) r;
    (void) conf;
#endif

    return NGX_OK;
}

#endif /* NGX_HTTP_MARKDOWN_AUTH_CACHE_CONTROL_HELPER_DEFINED */

/*
 * Shared conversion-option helpers
 *
 * These helpers populate Rust FFI options consistently for both the normal
 * conversion path and conditional-request revalidation.
 */
ngx_int_t ngx_http_markdown_construct_base_url(ngx_http_request_t *r,
    ngx_pool_t *pool, ngx_str_t *base_url);
ngx_int_t ngx_http_markdown_prepare_conversion_options(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_effective_conf_t *eff,
    struct MarkdownOptions *options);

/*
 * Conditional request handling functions
 *
 * These functions implement If-None-Match and If-Modified-Since support
 * for Markdown variants with configurable behavior.
 */

/*
 * Handle If-None-Match conditional request with configurable behavior.
 *
 * The Rust FFI converter handle is required when conversion is performed to
 * generate a Markdown-variant ETag for comparison.
 */
ngx_int_t ngx_http_markdown_handle_if_none_match(ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf, const ngx_http_markdown_ctx_t *ctx,
    struct MarkdownConverterHandle *converter,
    struct MarkdownResult **result);

/* Send 304 Not Modified response */
ngx_int_t ngx_http_markdown_send_304(ngx_http_request_t *r,
    const struct MarkdownResult *result);

/*
 * Check if the response carries Cache-Control: no-transform.
 *
 * Scans all Cache-Control response headers for the "no-transform"
 * directive (RFC 9111 §5.2.2.6).  Used in the header filter to bypass
 * conversion entirely when the upstream response must not be
 * transformed.
 *
 * Parameters:
 *   r - NGINX request (for response header access)
 *
 * Returns:
 *   1 if no-transform is present, 0 otherwise
 */
ngx_flag_t ngx_http_markdown_has_no_transform(ngx_http_request_t *r);

/*
 * Decompression functions
 *
 * These functions implement automatic decompression of upstream compressed content.
 */

/* Detect compression type from Content-Encoding header */
ngx_http_markdown_compression_type_e
ngx_http_markdown_detect_compression(ngx_http_request_t *r);
ngx_int_t
ngx_http_markdown_collect_content_encoding(ngx_http_request_t *r,
                                           ngx_str_t *out);
u_char
ngx_http_markdown_parse_encoding_chain_ffi(const ngx_http_request_t *r,
                                           ngx_http_markdown_ctx_t *ctx,
                                           const ngx_str_t *combined);

/* Decompress gzip/deflate compressed data using zlib */
ngx_int_t
ngx_http_markdown_decompress_gzip(ngx_http_request_t *r,
                                   ngx_http_markdown_compression_type_e type,
                                   const ngx_chain_t *in,
                                   ngx_chain_t **out);

/* Decompress brotli compressed data using brotli library */
ngx_int_t
ngx_http_markdown_decompress_brotli(ngx_http_request_t *r,
                                    const ngx_chain_t *in,
                                    ngx_chain_t **out);

/* Unified decompression entry function */
ngx_int_t
ngx_http_markdown_decompress(ngx_http_request_t *r,
                              ngx_http_markdown_compression_type_e type,
                              const ngx_chain_t *in,
                              ngx_chain_t **out);

/*
 * Sentinel return code: decompressed size budget exceeded.
 *
 * Returned by decompress functions (both buffered and streaming) when
 * the cumulative decompressed output exceeds decompress_max_size.
 * Callers must map this to ERROR_DECOMPRESSION_BUDGET_EXCEEDED for
 * proper metrics/reason-code classification, distinguishing it from
 * a generic NGX_ERROR (which callers would classify as conversion).
 *
 * Value -100 avoids collision with NGX_OK (0), NGX_ERROR (-1),
 * NGX_AGAIN (-2), NGX_DONE (-4), NGX_DECLINED (-5).
 */
#define NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED  -100
#define NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR     -101
#define NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT  -102
#define NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR         -103
#define NGX_HTTP_MARKDOWN_DECOMP_RATIO_EXCEEDED   -104
#define NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR   -105

/*
 * Internal return code for conditional-request Bypass outcome
 * (ConditionalOutcome::Bypass = 2).  The C caller should deliver the
 * upstream response unmodified.  Value -106 keeps it disjoint from the
 * decompression return-code domain (-100..-105) so a confused caller can
 * never misread a decompression failure as a conditional bypass.
 */
#define NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT     -106

/*
 * Safe buffer length helper.
 *
 * Computes the number of bytes between buf->pos and buf->last
 * with full NULL/validity guards.  Returns 0 on any validation
 * failure rather than invoking undefined behaviour on NULL or
 * invalid pointer arithmetic.
 *
 * Parameters:
 *   buf - pointer to an ngx_buf_t (may be NULL)
 *
 * Returns:
 *   (size_t)(buf->last - buf->pos) on success, 0 otherwise.
 */
static ngx_inline size_t
ngx_http_markdown_buf_len_safe(const ngx_buf_t *buf)
{
    ptrdiff_t diff;

    if (buf == NULL || buf->pos == NULL || buf->last == NULL) {
        return 0;
    }

    diff = buf->last - buf->pos;
    if (diff < 0) {
        return 0;
    }

    return (size_t) diff;
}

#endif /* NGX_HTTP_MARKDOWN_FILTER_MODULE_H */
