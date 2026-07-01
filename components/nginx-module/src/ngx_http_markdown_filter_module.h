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

struct MarkdownOptions;

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
 *   - enabled, enabled_source
 *   - prune_noise
 *   - log_verbosity
 *   - memory_budget
 *   - streaming_budget
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
    size_t       memory_budget;
#ifdef MARKDOWN_STREAMING_ENABLED
    size_t       streaming_budget;
#endif
};

typedef struct ngx_http_markdown_effective_conf_s
    ngx_http_markdown_effective_conf_t;

/* Delegate body output to the downstream filter saved during module init. */
ngx_int_t ngx_http_markdown_next_body_filter(ngx_http_request_t *r,
    ngx_chain_t *in);

/*
 * Forward declaration for OTel span type.
 * Full definition is in ngx_http_markdown_otel_impl.h.
 */
typedef struct ngx_http_markdown_otel_span_s  ngx_http_markdown_otel_span_t;


/*
 * Processing path constants for threshold router
 */
#define NGX_HTTP_MARKDOWN_PATH_FULLBUFFER   0  /* Full-buffer path */
#define NGX_HTTP_MARKDOWN_PATH_INCREMENTAL  1  /* Incremental path */
#define NGX_HTTP_MARKDOWN_PATH_STREAMING    2  /* Streaming path */

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
 * Default streaming budget: 2 MiB
 */
#define NGX_HTTP_MARKDOWN_STREAMING_BUDGET_DEFAULT \
    (2 * 1024 * 1024)

/*
 * Streaming on_error policy constants
 *
 * Controls Pre_Commit_Phase failure behavior for the
 * markdown_streaming_on_error directive.
 */
#define NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS    0
#define NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT  1

/*
 * Streaming engine reason codes (streaming observability).
 *
 * Stable identifiers explaining why a particular engine path was chosen.
 * Additive only — removal requires major version bump.
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
 * Map streaming reason code to its stable string identifier.
 *
 * Returns a static NUL-terminated string suitable for logs,
 * JSON output, and Prometheus labels.  Unknown values return
 * "unknown".
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
    NGX_HTTP_MD_ACTION_REJECT_502,
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
 * Streaming engine mode constants (markdown_streaming_engine directive).
 *
 * These use a simple enum stored as ngx_uint_t
 * rather than a complex value.
 */
#define NGX_HTTP_MARKDOWN_STREAM_ENGINE_OFF   0
#define NGX_HTTP_MARKDOWN_STREAM_ENGINE_AUTO  1
#define NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON    2

/*
 * Streaming policy mode constants (markdown_streaming directive, 0.9.0).
 *
 * markdown_streaming off|auto|force is the streaming *enablement* selector
 * (Config V2, spec 49).  It is distinct from markdown_streaming_engine,
 * which is the *implementation* selector (off|auto|on).  Do not conflate
 * the two: policy decides whether streaming is attempted, engine decides
 * which backend implementation is used.
 */
#define NGX_HTTP_MARKDOWN_STREAMING_OFF    0
#define NGX_HTTP_MARKDOWN_STREAMING_AUTO   1
#define NGX_HTTP_MARKDOWN_STREAMING_FORCE  2

/*
 * Production-profile constants (markdown_profile directive, 0.9.0).
 *
 * markdown_profile strict_cache|balanced|streaming_first selects a preset
 * bundle of Config V2 defaults.  A profile only supplies DEFAULTS: an
 * explicit directive (at the same or an inheriting scope) always overrides
 * the profile value.  NONE means no markdown_profile is in effect, in which
 * case the built-in Config V2 defaults apply unchanged.
 *
 * The names are frozen for the 1.0 stability contract; new profiles may be
 * added after 1.0 but existing names/semantics must not change.
 */
#define NGX_HTTP_MARKDOWN_PROFILE_NONE             0
#define NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE     1
#define NGX_HTTP_MARKDOWN_PROFILE_BALANCED         2
#define NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST  3

/*
 * Threshold off sentinel — used in merge and path selection logic.
 */
#define NGX_HTTP_MARKDOWN_THRESHOLD_OFF     0

/*
 * Configuration constants for on_error directive
 */
#define NGX_HTTP_MARKDOWN_ON_ERROR_PASS    0  /* fail-open: return original HTML */
#define NGX_HTTP_MARKDOWN_ON_ERROR_REJECT  1  /* fail-closed: return error status */

/*
 * Default pre-commit error status for markdown_error_policy (Config V2).
 *
 * markdown_error_policy fail_closed uses this; markdown_error_policy
 * status <code> overrides it with 429, 502, or 503.  Stored in
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
 * implemented by the worker inflight guard.  0 means unlimited.
 */
#define NGX_HTTP_MARKDOWN_MAX_INFLIGHT_DEFAULT  64

/*
 * Default streaming threshold for v0.8.0 stream.threshold field (1 MiB).
 * Responses with Content-Length >= this threshold use streaming mode.
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
 * Configuration constants for flavor directive
 */
#define NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK  0  /* CommonMark flavor */
#define NGX_HTTP_MARKDOWN_FLAVOR_GFM         1  /* GitHub Flavored Markdown */
#define NGX_HTTP_MARKDOWN_FLAVOR_MDX         2  /* MDX (Markdown + JSX) */
#define NGX_HTTP_MARKDOWN_FLAVOR_ORG_MODE    3  /* Org-mode */

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
 * Configuration constants for metrics_format directive
 */
#define NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO        0  /* JSON or plain-text (default) */
#define NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS   1  /* Prometheus text exposition */

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
 * Module configuration structure
 *
 * This structure holds configuration directives for the Markdown filter.
 * It supports NGINX's configuration inheritance model (http, server, location).
 *
 * Configuration defaults (defined in ngx_http_markdown_create_loc_conf):
 * - enabled: NGX_CONF_UNSET (inherit from parent)
 * - enabled_source: NGX_HTTP_MARKDOWN_ENABLED_UNSET (inherit from parent)
 * - enabled_complex: NULL
 * - max_size: 10MB (10 * 1024 * 1024 bytes)
 * - timeout: 5000ms (5 seconds)
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
 * - buffer_chunked: 1 (on by default)
 * - stream_types: NULL (no exclusions by default)
 * - auto_decompress: 1 (on by default)
 * - decompress_max_size: same as max_size (inherited after memory_budget override)
 * - parse_timeout: 30000ms (30 seconds)
 * - parser_budget: 64MB (64 * 1024 * 1024 bytes)
 * - large_body_threshold: NGX_HTTP_MARKDOWN_THRESHOLD_OFF
 * - ops.trust_forwarded_headers: 0 (off by default)
 * - ops.metrics_format: NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO
 * - ops.diagnostics_enabled: 0 (off by default)
 * - advanced.dynconf_dry_run: 0 (off by default)
 *
 * Streaming defaults when MARKDOWN_STREAMING_ENABLED is compiled in:
 * - stream.engine: auto (1) — NGX_HTTP_MARKDOWN_STREAM_ENGINE_AUTO
 * - stream.budget: NGX_HTTP_MARKDOWN_STREAMING_BUDGET_DEFAULT
 * - stream.on_error: NGX_HTTP_MARKDOWN_ON_ERROR_PASS
 * - stream.shadow: 0 (off by default)
 * - stream.threshold: NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT (1m)
 *
 * v0.8.0 streaming config defaults (streaming configuration directives):
 * - stream.engine: auto (1)
 * - stream.threshold: NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT (1m)
 * - stream.precommit_buffer: 262144 (256k)
 * - stream.flush_min: 16384 (16k)
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
    ngx_flag_t   generate_etag;        /* markdown_etag on|off (default: on) */
    ngx_uint_t   conditional_requests; /* markdown_conditional_requests mode (default: full_support) */
    ngx_uint_t   log_verbosity;        /* markdown_log_verbosity error|warn|info|debug (default: info) */
} ngx_http_markdown_policy_cfg_t;

typedef struct {
    ngx_flag_t   prune_noise;               /* markdown_prune_noise on|off (default: on) */
    ngx_str_t   *prune_selectors;           /* markdown_prune_selectors (default: built-in list) */
    ngx_str_t   *prune_protection_selectors; /* markdown_prune_protection_selectors (default: empty) */
    size_t       memory_budget;             /* markdown_memory_budget (default: NGX_CONF_UNSET_SIZE) */
    ngx_uint_t   llm_provider;              /* markdown_llm_provider (default: 0=default) */
    ngx_uint_t   chars_per_token_fixed;     /* markdown_chars_per_token (default: 0=use provider) */
    ngx_flag_t   dynconf_enabled;           /* markdown_dynamic_config on|off (default: off) */
    ngx_str_t    dynconf_path;              /* markdown_dynamic_config_path (default: empty) */
    ngx_flag_t   dynconf_dry_run;           /* markdown_dynconf_dry_run on|off (default: off) */
} ngx_http_markdown_advanced_cfg_t;

/*
 * Resolved profile-default value bundle (markdown_profile, 0.9.0, spec 50).
 *
 * Pure config-time data: each field carries the default value a given
 * profile contributes for the corresponding Config V2 directive.  The
 * merge logic feeds these values as the fallback "default" argument of the
 * standard ngx_conf_merge_* calls, so an explicit (or inherited-explicit)
 * directive always wins over the profile, and the profile in turn wins over
 * the built-in default.  For NGX_HTTP_MARKDOWN_PROFILE_NONE the fields equal
 * the built-in Config V2 defaults, making profile expansion a no-op.
 *
 * The bundle is value data only; it adds no request-path decision branch.
 */
typedef struct {
    ngx_uint_t   accept_policy;            /* markdown_accept */
    ngx_uint_t   conditional_requests;     /* markdown_cache_validation mode */
    ngx_flag_t   generate_etag;            /* markdown_cache_validation ETag */
    ngx_uint_t   streaming_policy;         /* markdown_streaming */
    ngx_uint_t   streaming_engine;         /* markdown_streaming_engine */
    size_t       limits_memory;            /* markdown_limits memory= */
    ngx_msec_t   limits_timeout;           /* markdown_limits timeout= */
    size_t       limits_streaming_buffer;  /* markdown_limits streaming_buffer= */
    ngx_uint_t   limits_max_inflight;      /* markdown_limits max_inflight= */
    ngx_uint_t   error_policy;             /* markdown_error_policy (on_error) */
    ngx_uint_t   auth_policy;              /* markdown_auth_policy */
    ngx_uint_t   flavor;                   /* markdown_flavor */
    ngx_flag_t   diagnostics;              /* markdown_diagnostics */
} ngx_http_markdown_profile_defaults_t;

typedef struct {
    ngx_flag_t   enabled;              /* markdown_filter static resolved value */
    ngx_uint_t   enabled_source;       /* markdown_filter source (static|complex|unset) */
    ngx_http_complex_value_t *enabled_complex; /* markdown_filter variable/complex expression */
    size_t       max_size;             /* markdown_max_size (default: 10MB) */
    ngx_msec_t   timeout;              /* markdown_timeout (default: 5000ms) */
    ngx_uint_t   on_error;             /* markdown_error_policy pass|fail_closed|status (default: pass) */
    ngx_uint_t   error_status;         /* markdown_error_policy status <code> (default: 502; honored on fail-closed) */
    ngx_uint_t   flavor;               /* markdown_flavor commonmark|gfm (default: commonmark) */
    ngx_flag_t   token_estimate;       /* markdown_token_estimate on|off (default: off) */
    ngx_flag_t   front_matter;         /* markdown_front_matter on|off (default: off) */
    ngx_uint_t   accept_policy;        /* markdown_accept strict|wildcard|force (default: strict) */
    ngx_http_markdown_policy_cfg_t policy;
    ngx_flag_t   buffer_chunked;       /* markdown_buffer_chunked on|off (default: on) */
    ngx_array_t *stream_types;         /* markdown_stream_types exclusion list (default: NULL) */
    ngx_array_t *content_types;        /* markdown_content_types allowlist (default: text/html) */
    size_t       large_body_threshold; /* markdown_large_body_threshold (NGX_HTTP_MARKDOWN_THRESHOLD_OFF = off) */
    ngx_uint_t   max_inflight;         /* markdown_limits max_inflight (default: 64; enforced by the worker inflight guard) */

    /*
     * Decompression/parsing limits.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_conf_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_flag_t   auto_decompress;      /* markdown_auto_decompress on|off (default: on) */
        size_t       max_size;             /* markdown_decompress_max_size (default: same as max_size) */
        ngx_msec_t   parse_timeout;        /* markdown_parse_timeout (default: 30000ms) */
        size_t       parser_budget;        /* markdown_parser_budget (default: 64MB) */
        ngx_flag_t   max_size_explicit;    /* 1 if operator set markdown_max_size at this or parent level */
    } decompress;

    /*
     * Operational settings.
     *
     * Grouped into a sub-struct so that the parent
     * ngx_http_markdown_conf_t stays within the 20-field limit
     * enforced by static analysis (SonarCloud rule c:S1820).
     */
    struct {
        ngx_flag_t   trust_forwarded_headers; /* markdown_trust_forwarded_headers on|off (default: off) */
        ngx_uint_t   metrics_format;       /* markdown_metrics_format auto|prometheus (default: auto) */
        ngx_flag_t   metrics_per_path;    /* markdown_metrics_per_path on|off (default: off) */
        ngx_flag_t   diagnostics_enabled; /* markdown_diagnostics on|off (default: off) */
        ngx_array_t *diagnostics_allow;   /* markdown_diagnostics_allow CIDR list (default: NULL = loopback only) */
        ngx_flag_t   otel_enabled;       /* markdown_otel on|off (default: off) */
        ngx_flag_t   otel_tracing;      /* markdown_otel_tracing on|off (default: off) */
        ngx_flag_t   otel_metrics;      /* markdown_otel_metrics on|off (default: off) */
        ngx_str_t    otel_endpoint;      /* markdown_otel_endpoint: internal NGINX URI for subrequest export (default: empty) */
        ngx_str_t    otel_service_name;  /* markdown_otel_service_name (default: nginx-markdown) */
        ngx_uint_t   otel_span_buffer_size; /* markdown_otel_span_buffer_size (default: 1024) */
        ngx_msec_t   otel_export_timeout;   /* markdown_otel_export_timeout (default: 5000ms) */
    } ops;

    /*
     * Unified streaming configuration (v0.8.0+).
     *
     * This is the sole runtime source-of-truth for all streaming
     * directives.  There is no compatibility layer from v0.6.x.
     */
    struct {
        ngx_uint_t    engine;              /* markdown_streaming_engine off|auto|on */
        ngx_uint_t    policy;              /* markdown_streaming off|auto|force */
        ngx_flag_t    policy_explicit;     /* 1 if operator set markdown_streaming */
        size_t        threshold;           /* markdown_stream_threshold (default: 1m) */
        ngx_flag_t    threshold_explicit;  /* 1 if operator set markdown_stream_threshold */
        size_t        precommit_buffer;    /* markdown_stream_precommit_buffer (default: 256k) */
        size_t        flush_min;           /* markdown_stream_flush_min (default: 16k) */
        ngx_array_t  *excluded_types;      /* markdown_stream_excluded_types (default: NULL) */
        ngx_uint_t    on_error;            /* markdown_streaming_on_error pass|reject */
        ngx_flag_t    on_error_explicit;   /* 1 if operator set streaming_on_error */
        size_t        budget;              /* markdown_streaming_budget (default: 2m) */
        ngx_flag_t    budget_explicit;     /* 1 if operator set streaming_budget */
        ngx_flag_t    shadow;              /* markdown_streaming_shadow on|off */
        ngx_flag_t    shadow_explicit;     /* 1 if operator set streaming_shadow */
    } stream;

    /*
     * Noise pruning configuration.
     */
    ngx_http_markdown_advanced_cfg_t advanced;

    /*
     * Production-profile selection (markdown_profile, 0.9.0, spec 50).
     *
     * Grouped into a sub-struct so the parent stays logically organized
     * (sonarcloud-c:S1820 is already intentionally exceeded; see the
     * struct-level note above).  The profile only supplies defaults; its
     * values are fed as the fallback default of the standard merge calls.
     */
    struct {
        ngx_uint_t   name;                      /* NGX_HTTP_MARKDOWN_PROFILE_* (default: NONE) */
        ngx_flag_t   set;                       /* 1 if markdown_profile set at this scope (duplicate guard) */
        ngx_flag_t   cache_validation_explicit; /* 1 if markdown_cache_validation set (this or ancestor) */
    } profile;
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
        budget = conf->advanced.memory_budget;
    }

    if (budget == 0 || budget == NGX_HTTP_MARKDOWN_CONF_UNSET_SIZE) {
        return conf->max_size;
    }

    if (conf->max_size == 0) {
        return budget;
    }

    return (budget < conf->max_size) ? budget : conf->max_size;
}


static ngx_inline void
ngx_http_markdown_merge_stream_values(ngx_http_markdown_conf_t *conf,
    const ngx_http_markdown_conf_t *prev,
    const ngx_http_markdown_profile_defaults_t *profile_defaults)
{
/*
 * Helper macro: merge a single stream configuration field.
 * If the current value equals the unset sentinel, inherit from
 * the previous level or fall back to the compile-time default.
 */
#define NGX_MD_MERGE_STREAM(field, type, unset, dflt)                        \
    do {                                                                      \
        if (conf->stream.field == (type) (unset)) {                          \
            conf->stream.field = (prev->stream.field != (type) (unset))      \
                ? prev->stream.field : (dflt);                               \
        }                                                                    \
    } while (0)

    NGX_MD_MERGE_STREAM(engine, ngx_uint_t, -1,
                        profile_defaults->streaming_engine);
    NGX_MD_MERGE_STREAM(policy, ngx_uint_t, -1,
                        profile_defaults->streaming_policy);
    NGX_MD_MERGE_STREAM(policy_explicit, ngx_flag_t, -1, 0);
    NGX_MD_MERGE_STREAM(threshold, size_t, -1,
                        NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT);
    NGX_MD_MERGE_STREAM(threshold_explicit, ngx_flag_t, -1, 0);
    NGX_MD_MERGE_STREAM(precommit_buffer, size_t, -1, 262144);
    NGX_MD_MERGE_STREAM(flush_min, size_t, -1, 16384);

    if (conf->stream.excluded_types == (ngx_array_t *) -1) {
        conf->stream.excluded_types =
            (prev->stream.excluded_types != (ngx_array_t *) -1)
                ? prev->stream.excluded_types : NULL;
    }

    NGX_MD_MERGE_STREAM(on_error, ngx_uint_t, -1,
                        profile_defaults->error_policy);
    NGX_MD_MERGE_STREAM(on_error_explicit, ngx_flag_t, -1, 0);
    NGX_MD_MERGE_STREAM(budget, size_t, -1,
                        profile_defaults->limits_streaming_buffer);
    NGX_MD_MERGE_STREAM(budget_explicit, ngx_flag_t, -1, 0);
    NGX_MD_MERGE_STREAM(shadow, ngx_flag_t, -1, 0);
    NGX_MD_MERGE_STREAM(shadow_explicit, ngx_flag_t, -1, 0);

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
    ngx_uint_t      metrics_per_path_cardinality; /* markdown_metrics_per_path_cardinality (default: 100, global) */
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
    NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,       /* Not 200 or 206 Partial Content */
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
    ngx_http_markdown_last_modified_state_t
                                last_modified;

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
        ngx_flag_t                   bypass_counted;
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
    } fullbuffer;
    
    /* Threshold router path selection (NGX_HTTP_MARKDOWN_PATH_FULLBUFFER or NGX_HTTP_MARKDOWN_PATH_INCREMENTAL) */
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
     * NULL only on pool allocation failure (falls back to live conf). */
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
    } error;

    /* OpenTelemetry span for per-request conversion tracing */
    ngx_http_markdown_otel_span_t        *otel_span;

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
        ngx_flag_t                        headers_committed; /* Headers sent downstream */
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
        size_t                            total_output_bytes;
        unsigned                          total_output_bytes_overflowed:1;
        unsigned                          main_terminal_sent:1;

        /* TTFB tracking (from first feed to first non-empty output) */
        struct {
            ngx_msec_t                        feed_start_ms;
            ngx_flag_t                        recorded;
        } ttfb;

        /* Pending output chain has non-empty data (for TTFB resume path) */
        ngx_flag_t                        pending_has_data;

        /* Pending output byte count (for deferred metric accounting) */
        size_t                            pending_output_bytes;

        /* Pending output is a fail-open delivery; resume_pending should
           increment results.failopen_count on downstream success. */
        ngx_flag_t                        pending_failopen_delivery;

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
         * Finalize-path state latches.
         *
         * Grouped to keep the parent streaming struct below SonarCloud
         * c:S1820 field-count threshold while preserving semantics.
         */
        struct {
            /* Deferred terminal last_buf (backpressure during finalize) */
            ngx_flag_t                    finalize_pending_lastbuf;

            /* Metrics deferred for terminal last_buf (backpressure on
             * terminal send — set when send_output(last_buf=1)
             * returns NGX_AGAIN, cleared when resume drain succeeds
             * or fails). */
            ngx_flag_t                    pending_terminal_metrics;

            /* Post-commit failure metrics recorded for this request. */
            ngx_flag_t                    failure_recorded;

            /* Continue finalize() after tail-output backpressure drains. */
            ngx_flag_t                    finalize_after_pending;
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
     * decision chain, including requests later classified as SKIP_CONFIG.
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
        ngx_atomic_t  failed_total;            /* Streaming failures */
        ngx_atomic_t  postcommit_error_total;  /* Post-Commit errors */
        ngx_atomic_t  precommit_failopen_total;  /* Pre-Commit fail-open */
        ngx_atomic_t  precommit_reject_total;    /* Pre-Commit fail-closed */
        ngx_atomic_t  budget_exceeded_total;     /* Memory budget exceeded */
        ngx_atomic_t  shadow_total;              /* Shadow mode runs */
        ngx_atomic_t  shadow_diff_total;         /* Shadow output diffs */
        ngx_atomic_t  last_ttfb_ms;              /* Last streaming TTFB (milliseconds) */
        ngx_atomic_t  last_peak_memory_bytes;    /* Last streaming peak estimate (bytes; not RSS) */

        /* Fallback/failure counters */
        ngx_atomic_t  streaming_fallback_precommit_pass;  /* Pre-commit HTML pass-through */
        ngx_atomic_t  streaming_fallback_precommit_reject; /* Pre-commit rejection */
        ngx_atomic_t  streaming_failure_postcommit_abort;  /* Post-commit abort */
        ngx_atomic_t  streaming_failure_postcommit_safe_finish; /* Post-commit safe finish */

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
        ngx_atomic_t  decision_count;
        ngx_atomic_t  estimated_token_savings;
        ngx_atomic_t  replay_buffer_errors_total;
    } results;

    struct {
        ngx_atomic_t  parse_timeouts_total;
        ngx_atomic_t  parse_budget_exceeded_total;
    } parse_interrupts;

    /*
     * Per-path metrics.
     *
     * When markdown_metrics_per_path is enabled, URI paths are
     * tracked individually in an RB-tree allocated from the slab
     * allocator.  Each node holds per-path conversion and timing
     * counters.  The tree is protected by the slab pool mutex.
     *
     * cardinality_limit caps the number of distinct paths stored;
     * overflow_count tracks paths dropped when at capacity.
     * Aggregate counters (path_conversions, path_conversion_time_sum_ms)
     * accumulate across all per-path nodes for fast rendering
     * without tree traversal.
     */
    struct {
        ngx_rbtree_t       path_tree;
        ngx_rbtree_node_t  sentinel;
        ngx_atomic_t       path_entries;
        ngx_atomic_t       path_conversions;
        ngx_atomic_t       path_conversion_time_sum_ms;
        ngx_uint_t         cardinality_limit;
        ngx_atomic_t       overflow_count;
    } per_path;
} ngx_http_markdown_metrics_t;

/*
 * Per-path metric node stored in the shared RB-tree.
 *
 * rbnode.key holds a hash of the path for O(1) first-level
 * lookup; collisions are resolved by comparing path_len then
 * path bytes.  The path string is slab-owned and must be
 * freed with ngx_slab_free() when the node is removed.
 */
typedef struct {
    ngx_rbtree_node_t  rbnode;
    ngx_uint_t         path_len;
    u_char            *path;
    ngx_atomic_t       conversions;
    ngx_atomic_t       conversion_time_sum_ms;
    ngx_atomic_t       entries;
} ngx_http_markdown_path_metric_node_t;

/* Module declaration */
extern ngx_module_t ngx_http_markdown_filter_module;

/* Forward declarations for FFI types used in public module interfaces */
struct MarkdownConverterHandle;
struct MarkdownResult;

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

/* Return the SKIP_ACCEPT reason code (not in eligibility enum) */
const ngx_str_t *ngx_http_markdown_reason_skip_accept(void);

/* Return the SKIPPED_NO_ACCEPT reason code (no Accept header) */
const ngx_str_t *ngx_http_markdown_reason_skip_no_accept(void);

/* Return the SKIPPED_ACCEPT_REJECT reason code (q=0 explicit reject) */
const ngx_str_t *ngx_http_markdown_reason_skip_accept_reject(void);

/* Return the SKIPPED_CONDITIONAL reason code (304 Not Modified) */
const ngx_str_t *ngx_http_markdown_reason_skip_conditional(void);

#ifdef MARKDOWN_STREAMING_ENABLED
/* Streaming reason code accessors */
const ngx_str_t *ngx_http_markdown_reason_engine_streaming(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_convert(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_fallback(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_fail_postcommit(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_skip_unsupported(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_skip_compressed(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_budget_exceeded(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_precommit_failopen(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_precommit_reject(void);
const ngx_str_t *ngx_http_markdown_reason_streaming_shadow(void);
const ngx_str_t *ngx_http_markdown_reason_eligible_streaming_auto(void);
const ngx_str_t *ngx_http_markdown_reason_eligible_fullbuffer_auto(void);
#endif /* MARKDOWN_STREAMING_ENABLED */

/*
 * Rust FFI reason code accessors (v0.7.0+)
 *
 * These functions access reason code strings from the Rust-defined enum
 * via FFI.  The Rust enum (decision/reason_code.rs) is the SINGLE SOURCE
 * OF TRUTH for all reason codes.
 *
 * New code should prefer these accessors over the legacy C-side string
 * literals defined above.  The legacy functions remain for backward
 * compatibility during the migration period.
 *
 * DO NOT define new reason code constants in C.  All reason codes must
 * come from the Rust enum via these FFI accessors.
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
 * Decompression functions
 *
 * These functions implement automatic decompression of upstream compressed content.
 */

/* Detect compression type from Content-Encoding header */
ngx_http_markdown_compression_type_e
ngx_http_markdown_detect_compression(ngx_http_request_t *r);

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
