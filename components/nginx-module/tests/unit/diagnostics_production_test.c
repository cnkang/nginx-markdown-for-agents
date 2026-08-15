/*
 * Test: diagnostics_production
 *
 * Directly exercises ngx_http_markdown_diagnostics.c production code so
 * SonarCloud and gcov measure the real diagnostics implementation rather than
 * a model copy.
 */

#include "../include/test_common.h"
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#define NGX_HTTP_GET                 0x0002
#define NGX_HTTP_HEAD                0x0004
#define NGX_HTTP_POST                0x0008
#define NGX_HTTP_OK                  200
#define NGX_HTTP_FORBIDDEN           403
#define NGX_HTTP_NOT_ALLOWED         405
#define NGX_HTTP_INTERNAL_SERVER_ERROR 500
#define NGX_ERROR                    -1
#define NGX_OK                       0
#define NGX_DECLINED                 -5
#define NGX_LOG_DEBUG                8
#define NGX_LOG_INFO                 4

#define NGX_HTTP_MARKDOWN_LOG_ERROR  0
#define NGX_HTTP_MARKDOWN_LOG_WARN   1
#define NGX_HTTP_MARKDOWN_LOG_INFO   2
#define NGX_HTTP_MARKDOWN_LOG_DEBUG  3

#define NGX_HTTP_MARKDOWN_ACCEPT_STRICT    0
#define NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD  1
#define NGX_HTTP_MARKDOWN_ACCEPT_FORCE     2

#define NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT         0
#define NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE    1
#define NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED             2

#define NGX_HTTP_MARKDOWN_STREAMING_OFF    0
#define NGX_HTTP_MARKDOWN_STREAMING_AUTO   1
#define NGX_HTTP_MARKDOWN_STREAMING_FORCE  2

#define NGX_HTTP_MARKDOWN_ON_ERROR_PASS    0
#define NGX_HTTP_MARKDOWN_ON_ERROR_REJECT  1
#define NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT  502
#define NGX_HTTP_MARKDOWN_PROVENANCE_STATIC           0
#define NGX_HTTP_MARKDOWN_PROVENANCE_DYNCONF          1
#define NGX_HTTP_MARKDOWN_PROVENANCE_REQUEST_VARIABLE 2
#define NGINX_VERSION "1.26.3"
#define ngx_pid 1234
#define ngx_time() ((time_t) 1700000000)

#define ngx_memcpy(dst, src, n)      memcpy(dst, src, n)
#define ngx_memzero(dst, n)          memset((dst), 0, (n))
#define ngx_strcmp(s1, s2)           strcmp((const char *) (s1), (const char *) (s2))

#define ngx_str_set(str, text)                                                \
    do {                                                                      \
        (str)->len = sizeof(text) - 1;                                         \
        (str)->data = (u_char *) text;                                         \
    } while (0)

struct ngx_log_s {
    int dummy;
};

struct ngx_pool_s {
    int dummy;
};

struct ngx_cycle_s {
    ngx_pool_t  *pool;
    ngx_log_t   *log;
};

/* struct ngx_buf_s provided by nginx_stubs/ngx_core.h */

struct ngx_chain_s {
    ngx_buf_t    *buf;
    ngx_chain_t  *next;
};

struct ngx_array_s {
    void        *elts;
    ngx_uint_t   nelts;
};

typedef struct {
    ngx_int_t   family;
    union {
        struct {
            in_addr_t  addr;
            in_addr_t  mask;
        } in;
    } u;
} ngx_cidr_t;

typedef struct {
    void        *elts;
    ngx_uint_t   nelts;
    size_t       size;
    ngx_uint_t   nalloc;
    ngx_pool_t  *pool;
} ngx_list_part_t;

typedef struct {
    ngx_list_part_t  last_part;
    ngx_list_part_t *last;
    ngx_uint_t       nalloc;
    size_t           size;
    ngx_pool_t      *pool;
} ngx_list_t;

typedef struct {
    ngx_uint_t  status;
    size_t      content_type_len;
    ngx_str_t   content_type;
    u_char     *content_type_lowcase;
    off_t       content_length_n;
    ngx_list_t  headers;
} ngx_http_headers_out_t;

typedef struct {
    ngx_log_t        *log;
    struct sockaddr  *sockaddr;
} ngx_connection_t;

typedef struct {
    ngx_uint_t  hash;
    ngx_str_t   key;
    ngx_str_t   value;
} ngx_table_elt_t;

typedef struct {
    ngx_table_elt_t  *authorization;
} ngx_http_headers_in_t;

struct ngx_http_request_s {
    ngx_uint_t               method;
    ngx_str_t                args;
    ngx_pool_t              *pool;
    ngx_connection_t        *connection;
    ngx_http_headers_in_t    headers_in;
    ngx_http_headers_out_t   headers_out;
    ngx_http_request_t      *main;
    void                    *loc_conf;
};

typedef struct {
    ngx_flag_t   diagnostics_enabled;
} ngx_http_markdown_ops_cfg_t;

typedef struct {
    ngx_uint_t  log_verbosity;
    ngx_uint_t  conditional_requests;
} ngx_http_markdown_policy_cfg_t;

typedef struct ngx_http_markdown_conf_s {
    ngx_http_markdown_ops_cfg_t     ops;
    ngx_http_markdown_policy_cfg_t  policy;
    ngx_uint_t                      accept_policy;
    ngx_uint_t                      on_error;
    ngx_uint_t                      error_status;
    size_t                          max_size;
    ngx_msec_t                      timeout;
    struct {
        ngx_uint_t                  max_inflight;
    } routing;
    struct {
        ngx_uint_t    policy;
        ngx_flag_t    policy_explicit;
        size_t        threshold;
        ngx_flag_t    threshold_explicit;
        size_t        precommit_buffer;
        size_t        flush_min;
        ngx_array_t  *excluded_types;
        size_t        budget;
        ngx_flag_t    budget_explicit;
        ngx_flag_t    shadow;
    } stream;
} ngx_http_markdown_conf_t;

typedef struct ngx_http_markdown_effective_conf_s {
    ngx_uint_t  log_verbosity;
} ngx_http_markdown_effective_conf_t;

typedef struct ngx_module_s {
    int dummy;
} ngx_module_t;

static ngx_module_t ngx_http_markdown_filter_module;
static ngx_msec_t ngx_current_msec;
static int g_send_header_calls;
static ngx_int_t g_send_header_rc;
static int g_output_filter_calls;
static ngx_chain_t *g_last_output_chain;
static ngx_table_elt_t g_allow_header;
static int g_discard_rc;
static int g_list_push_fail;
static int g_alloc_fail_after = -1;
static size_t g_effective_streaming_buffer = 2 * 1024 * 1024;

static void *
test_alloc(size_t size, int zero)
{
    if (g_alloc_fail_after == 0) {
        return NULL;
    }
    if (g_alloc_fail_after > 0) {
        g_alloc_fail_after--;
    }
    return zero ? calloc(1, size) : malloc(size);
}

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return test_alloc(size, 0);
}

void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return test_alloc(size, 1);
}

void *
ngx_list_push(ngx_list_t *list)
{
    UNUSED(list);
    if (g_list_push_fail) {
        return NULL;
    }
    memset(&g_allow_header, 0, sizeof(g_allow_header));
    return &g_allow_header;
}

void *
ngx_http_get_module_loc_conf(ngx_http_request_t *r, ngx_module_t module)
{
    UNUSED(module);
    return r->loc_conf;
}

ngx_int_t
ngx_http_discard_request_body(ngx_http_request_t *r)
{
    UNUSED(r);
    return g_discard_rc;
}

ngx_int_t
ngx_http_send_header(ngx_http_request_t *r)
{
    UNUSED(r);
    g_send_header_calls++;
    return g_send_header_rc;
}

ngx_int_t
ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *out)
{
    UNUSED(r);
    g_output_filter_calls++;
    g_last_output_chain = out;
    return NGX_OK;
}

u_char *
ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...)
{
    char translated[1024];
    char *dst;
    const char *src;
    va_list args;
    int n;
    size_t remaining;

    dst = translated;
    remaining = sizeof(translated);

    for (src = fmt; *src != '\0' && remaining > 1; src++) {
        if (*src == '%' && src[1] == 'P') {
            *dst++ = '%';
            *dst++ = 'd';
            src++;
            remaining -= 2;
            continue;
        }
        if (*src == '%' && src[1] == 'M') {
            *dst++ = '%';
            *dst++ = 'l';
            *dst++ = 'u';
            src++;
            remaining -= 3;
            continue;
        }
        if (*src == '%' && src[1] == 'u' && src[2] == 'A') {
            *dst++ = '%';
            *dst++ = 'l';
            *dst++ = 'u';
            src += 2;
            remaining -= 3;
            continue;
        }
        if (*src == '%' && src[1] == 'u' && src[2] == 'i') {
            *dst++ = '%';
            *dst++ = 'l';
            *dst++ = 'u';
            src += 2;
            remaining -= 3;
            continue;
        }
        if (*src == '%' && src[1] == 'T') {
            *dst++ = '%';
            *dst++ = 'l';
            *dst++ = 'd';
            src++;
            remaining -= 3;
            continue;
        }
        if (*src == '%' && src[1] == 'z') {
            *dst++ = '%';
            *dst++ = 'z';
            src++;
            remaining -= 2;
            continue;
        }
        *dst++ = *src;
        remaining--;
    }
    *dst = '\0';

    va_start(args, fmt);
    n = vsnprintf((char *) buf, (size_t) (last - buf), translated, args);
    va_end(args);

    if (n < 0) {
        return last;
    }
    if ((size_t) n >= (size_t) (last - buf)) {
        return last;
    }
    return buf + n;
}

ngx_int_t
ngx_http_markdown_dynconf_snapshot_to_json(ngx_pool_t *pool,
    const ngx_http_markdown_conf_t *conf, u_char **out_buf, size_t *out_len)
{
    static u_char snapshot[] = "    \"diagnostics_enabled\": \"on\"\n";

    UNUSED(pool);
    UNUSED(conf);
    *out_buf = snapshot;
    *out_len = sizeof(snapshot) - 1;
    return NGX_OK;
}

#define NGX_HTTP_MARKDOWN_FILTER_MODULE_H

/* Constants needed by diagnostics.c streaming_config formatter */
#ifndef NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT
#define NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT  (1024 * 1024)
#endif
#ifndef NGX_HTTP_MARKDOWN_STREAM_FLUSH_MIN_FIXED
#define NGX_HTTP_MARKDOWN_STREAM_FLUSH_MIN_FIXED  16384
#endif

#include "../src/ngx_http_markdown_diagnostics.c"

/*
 * Override hook for the dynconf-state stub.  When NULL (default) the stub
 * returns the canonical ACTIVE snapshot used by the existing tests.  When
 * non-NULL, the stub copies *g_dynconf_override into *out so tests can drive
 * ngx_http_markdown_diag_render_dynconf through every state branch
 * (ACTIVE/LKG_PRESERVED/INVALID_NO_LKG/disabled) via build_json.
 */
static const ngx_http_markdown_diag_dynconf_t *g_dynconf_override;

void
ngx_http_markdown_diagnostics_collect_metrics(
    ngx_http_markdown_diag_metrics_t *out)
{
    /*
     * Zero the full struct first: the JSON builder renders every field
     * (including inflight/pending_output), so unset members would leak
     * indeterminate stack values.  The production accessor memzeros too
     * (diagnostics_accessors_impl.h).
     */
    memset(out, 0, sizeof(*out));
    out->conversions_total = 7;
    out->delivery_total = 6;
    out->requests_total = 9;
    out->failopen_total = 1;
    out->streaming_requests_total = 9;
    out->precommit_failopen_total = 0;
    out->zero_copy_output_total = 8;
    out->copied_output_total = 1;
}

void
ngx_http_markdown_diagnostics_get_dynconf_state(
    ngx_http_markdown_diag_dynconf_t *out)
{
    static const char digest[] =
        "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    memset(out, 0, sizeof(*out));

    if (g_dynconf_override != NULL) {
        *out = *g_dynconf_override;
        return;
    }

    out->state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_ACTIVE;
    snprintf((char *) out->source_digest, sizeof(out->source_digest), "%s",
             digest);
    snprintf((char *) out->active_digest, sizeof(out->active_digest), "%s",
             digest);
    snprintf((char *) out->lkg_digest, sizeof(out->lkg_digest), "%s",
             digest);
    out->generation = 1;
    out->has_last_success = 1;
    out->last_success = 1700000000;
    out->active_mtime = 100;
    out->config_version = 3;
    out->last_known_good_mtime = 90;
    out->lkg_valid = 1;
    out->masked_fields = NGX_HTTP_MARKDOWN_DIAG_MASK_FILTER
                        | NGX_HTTP_MARKDOWN_DIAG_MASK_ERROR_POLICY;
}

void
ngx_http_markdown_diagnostics_get_effective(
    const void *conf, ngx_http_markdown_diag_effective_t *out)
{
    const ngx_http_markdown_conf_t *mcf = conf;

    memset(out, 0, sizeof(*out));
    out->filter = 1;
    out->prune_noise = 1;
    out->log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    out->error_policy = mcf->on_error;
    out->error_status = mcf->error_status;
    out->streaming_buffer = g_effective_streaming_buffer;
    out->filter_source = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    out->prune_noise_source = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    out->log_verbosity_source = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    out->error_policy_source = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
    out->streaming_buffer_source = NGX_HTTP_MARKDOWN_PROVENANCE_STATIC;
}

ngx_int_t
ngx_http_markdown_diagnostics_get_static_digest(
    const void *request, ngx_pool_t *pool, u_char *out, size_t out_len)
{
    static const u_char digest[] =
        "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

    UNUSED(request);
    UNUSED(pool);
    if (out == NULL || out_len < sizeof(digest)) {
        return NGX_ERROR;
    }
    memcpy(out, digest, sizeof(digest));
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_get_reason_code_str(uint32_t code, ngx_str_t *out_str)
{
    static u_char reason[] = "converted";

    UNUSED(code);
    out_str->data = reason;
    out_str->len = sizeof(reason) - 1;
    return NGX_OK;
}

static void
reset_test_state(void)
{
    g_send_header_calls = 0;
    g_send_header_rc = NGX_OK;
    g_output_filter_calls = 0;
    g_last_output_chain = NULL;
    memset(&g_allow_header, 0, sizeof(g_allow_header));
    g_discard_rc = NGX_OK;
    g_list_push_fail = 0;
    g_alloc_fail_after = -1;
    g_dynconf_override = NULL;
    g_effective_streaming_buffer = 2 * 1024 * 1024;
    ngx_current_msec = 1000;
    memset(&ngx_http_markdown_g_diag_state, 0,
           sizeof(ngx_http_markdown_g_diag_state));
    ngx_http_markdown_g_diag_initialized = 0;
    ngx_http_markdown_g_diag_recording_requested = 0;
}

static void
init_request(ngx_http_request_t *r, ngx_connection_t *c,
    ngx_http_markdown_conf_t *conf, struct sockaddr_in *addr)
{
    static ngx_pool_t pool;
    static ngx_log_t log;

    memset(r, 0, sizeof(*r));
    memset(c, 0, sizeof(*c));
    memset(conf, 0, sizeof(*conf));
    memset(addr, 0, sizeof(*addr));

    addr->sin_family = AF_INET;
    addr->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    c->sockaddr = (struct sockaddr *) addr;
    c->log = &log;

    r->method = NGX_HTTP_GET;
    r->pool = &pool;
    r->connection = c;
    r->main = r;
    r->loc_conf = conf;
    conf->policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
}

static void
test_lifecycle_and_ring_wrap(void)
{
    ngx_http_markdown_diag_state_t state;
    ngx_pool_t pool;
    ngx_int_t rc;

    TEST_SUBSECTION("diagnostics lifecycle and ring wrap");

    reset_test_state();
    memset(&state, 0, sizeof(state));

    rc = ngx_http_markdown_diagnostics_init(&state, &pool, 2);
    TEST_ASSERT(rc == NGX_OK, "init should succeed");
    TEST_ASSERT(state.ring.capacity == 2, "capacity should be set");

    ngx_http_markdown_diagnostics_record(&state, 1, 10);
    TEST_ASSERT(state.ring.count == 0, "disabled state should not record");

    state.enabled = 1;
    ngx_current_msec = 1001;
    ngx_http_markdown_diagnostics_record(&state, 1, 10);
    ngx_current_msec = 1002;
    ngx_http_markdown_diagnostics_record(&state, 2, 20);
    ngx_current_msec = 1003;
    ngx_http_markdown_diagnostics_record(&state, 3, 30);

    TEST_ASSERT(state.ring.count == 2, "ring should cap count");
    TEST_ASSERT(state.ring.head == 1, "ring head should wrap");
    TEST_ASSERT(state.ring.entries[0].reason_code == 3,
                "newest wrapped entry should be present");
    TEST_ASSERT(strcmp(state.ring.entries[0].outcome, "skipped") == 0,
                "ring stores the classified outcome");
    TEST_ASSERT(strcmp(state.ring.entries[0].stage, "eligibility") == 0,
                "ring stores the classified stage");
    TEST_ASSERT(state.ring.entries[0].error_origin == NULL,
                "skip decisions have no error origin");

    ngx_http_markdown_diagnostics_cleanup(&state);
    TEST_ASSERT(state.ring.count == 0, "cleanup should reset count");
    TEST_ASSERT(state.enabled == 0, "cleanup should disable diagnostics");

    TEST_PASS("Lifecycle and ring wrap covered");
}

static void
test_lifecycle_failure_branches(void)
{
    ngx_http_markdown_diag_state_t state;
    ngx_pool_t pool;
    ngx_int_t rc;

    TEST_SUBSECTION("diagnostics lifecycle failure branches");

    reset_test_state();
    rc = ngx_http_markdown_diagnostics_init(NULL, &pool, 2);
    TEST_ASSERT(rc == NGX_ERROR, "NULL state should fail");

    rc = ngx_http_markdown_diagnostics_init(&state, NULL, 2);
    TEST_ASSERT(rc == NGX_ERROR, "NULL pool should fail");

    memset(&state, 0, sizeof(state));
    rc = ngx_http_markdown_diagnostics_init(&state, &pool, 0);
    TEST_ASSERT(rc == NGX_OK, "zero capacity should use default");
    TEST_ASSERT(state.ring.capacity == NGX_HTTP_MARKDOWN_DIAG_DEFAULT_CAPACITY,
                "default capacity should be applied");

    memset(&state, 0, sizeof(state));
    rc = ngx_http_markdown_diagnostics_init(
        &state, &pool, NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY + 1);
    TEST_ASSERT(rc == NGX_OK, "oversize capacity should clamp");
    TEST_ASSERT(state.ring.capacity == NGX_HTTP_MARKDOWN_DIAG_MAX_CAPACITY,
                "max capacity should be applied");

    memset(&state, 0, sizeof(state));
    g_alloc_fail_after = 0;
    rc = ngx_http_markdown_diagnostics_init(&state, &pool, 2);
    TEST_ASSERT(rc == NGX_ERROR, "allocation failure should fail init");

    ngx_http_markdown_diagnostics_cleanup(NULL);

    TEST_PASS("Lifecycle failure branches covered");
}


static void
test_recording_request_resets_between_config_cycles(void)
{
    ngx_int_t rc;

    TEST_SUBSECTION("diagnostics recording request resets per config cycle");

    reset_test_state();

    rc = ngx_http_markdown_diagnostics_init_worker(NULL);
    TEST_ASSERT(rc == NGX_OK,
                "unrequested diagnostics init should be a no-op");

    ngx_http_markdown_diagnostics_enable_recording();
    rc = ngx_http_markdown_diagnostics_init_worker(NULL);
    TEST_ASSERT(rc == NGX_ERROR,
                "requested diagnostics should validate worker cycle");

    ngx_http_markdown_diagnostics_reset_recording_request();
    rc = ngx_http_markdown_diagnostics_init_worker(NULL);
    TEST_ASSERT(rc == NGX_OK,
                "reset request should prevent stale worker init");

    TEST_PASS("Recording request resets between config cycles");
}

static void
test_decision_path_records_once_with_explicit_duration(void)
{
    ngx_http_request_t r;
    ngx_connection_t c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in addr;
    ngx_http_markdown_decision_path_t path;
    ngx_int_t rc;

    TEST_SUBSECTION("decision path records one explicit diagnostics entry");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    rc = ngx_http_markdown_diagnostics_init(
        &ngx_http_markdown_g_diag_state, r.pool, 4);
    TEST_ASSERT(rc == NGX_OK, "global init should succeed");
    ngx_http_markdown_g_diag_state.enabled = 1;

    memset(&path, 0, sizeof(path));
    path.accept_result = "CONVERT";
    path.conditional_result = "PROCEED";
    path.conversion_status = "SUCCESS";
    path.reason_code = "converted";
    path.stage = "postcommit";
    path.duration_ms = 37;
    ngx_http_markdown_log_decision_path(&r, &conf, NULL, &path);

    TEST_ASSERT(ngx_http_markdown_g_diag_state.ring.count == 1,
                "one terminal path must create exactly one ring entry");
    TEST_ASSERT(ngx_http_markdown_g_diag_state.ring.entries[0].reason_code == 0,
                "path reason should map without C-string overread");
    TEST_ASSERT(ngx_http_markdown_g_diag_state.ring.entries[0].duration_ms == 37,
                "path duration must be preserved");
    TEST_ASSERT(strcmp(ngx_http_markdown_g_diag_state.ring.entries[0].stage,
                       "postcommit") == 0,
                "path stage must use explicit production provenance");

    TEST_PASS("Decision path ring ownership covered");
}


static void
test_access_and_json_builder(void)
{
    ngx_http_request_t r;
    ngx_connection_t c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in addr;
    ngx_buf_t b;
    ngx_int_t rc;
    const char *json;

    TEST_SUBSECTION("diagnostics access and JSON builder");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_FORCE;
    conf.stream.policy_explicit = 1;

    rc = ngx_http_markdown_diagnostics_init(
        &ngx_http_markdown_g_diag_state, r.pool, 2);
    TEST_ASSERT(rc == NGX_OK, "global init should succeed");
    ngx_http_markdown_g_diag_state.enabled = 1;
    ngx_current_msec = 2001;
    ngx_http_markdown_diagnostics_record(&ngx_http_markdown_g_diag_state,
                                         11, 33);

    memset(&b, 0, sizeof(b));
    rc = ngx_http_markdown_diagnostics_check_access(&r);
    TEST_ASSERT(rc == NGX_OK, "loopback access should be allowed");

    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_OK, "JSON builder should succeed");
    TEST_ASSERT(b.pos != NULL && b.last > b.pos, "buffer should be populated");
    TEST_ASSERT((size_t) (b.end - b.start)
                == NGX_HTTP_MARKDOWN_DIAG_JSON_BASE_SIZE
                   + (6 * (sizeof(((ngx_http_markdown_diag_dynconf_t *) 0)
                               ->last_error) - 1))
                   + NGX_HTTP_MARKDOWN_DIAG_JSON_DECISION_SIZE,
                "JSON buffer should account for recorded decisions");

    json = (const char *) b.pos;
    TEST_ASSERT(strstr(json, "\"schema_version\":2") != NULL,
                "JSON should expose diagnostics schema v2");
    TEST_ASSERT(strstr(json, "\"worker\":{\"pid\":1234") != NULL,
                "JSON should expose worker identity");
    TEST_ASSERT(strstr(json, "\"configuration\":") != NULL,
                "JSON should include configuration");
    TEST_ASSERT(strstr(json, "\"masked_keys\":[\"filter\",\"error_policy\"]")
                != NULL,
                "JSON should report dynconf keys masked by static config");
    TEST_ASSERT(strstr(json, "\"static_digest\":\"sha256:") != NULL,
                "JSON should include a static configuration digest");
    TEST_ASSERT(strstr(json, "\"recent_decisions\"") != NULL,
                "JSON should include recent decisions");
    TEST_ASSERT(strstr(json, "\"module_metrics\":") != NULL
                && strstr(json, "\"zero_copy_output_total\":8") != NULL
                && strstr(json, "\"copied_output_total\":1") != NULL,
                "JSON should include exact module evidence counters");
    TEST_ASSERT(strstr(json, "\"profile\"") == NULL
                && strstr(json, "\"streaming_config\"") == NULL
                && strstr(json, "\"metrics_snapshot\"") == NULL
                && strstr(json, "\"streaming_metrics\"") == NULL,
                "JSON should not expose removed compatibility fields");
    TEST_ASSERT(strstr(json, "\"reason\":\"converted\"") != NULL,
                "JSON should include the canonical reason string");

    TEST_PASS("Access and JSON builder covered");
}


static void
test_json_preserves_unified_error_policy(void)
{
    static const struct {
        ngx_uint_t   on_error;
        ngx_uint_t   error_status;
        const char  *value;
    } cases[] = {
        { NGX_HTTP_MARKDOWN_ON_ERROR_PASS,
          NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT, "pass" },
        { NGX_HTTP_MARKDOWN_ON_ERROR_REJECT,
          NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT, "fail_closed" },
        { NGX_HTTP_MARKDOWN_ON_ERROR_REJECT, 429, "status 429" },
        { NGX_HTTP_MARKDOWN_ON_ERROR_REJECT, 503, "status 503" },
    };
    ngx_http_request_t       r;
    ngx_connection_t         c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in       addr;
    ngx_buf_t                b;
    ngx_int_t                rc;
    char                     effective_expected[96];
    ngx_uint_t               i;

    TEST_SUBSECTION("diagnostics preserve every unified error policy");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);

    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        conf.on_error = cases[i].on_error;
        conf.error_status = cases[i].error_status;
        memset(&b, 0, sizeof(b));

        rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
        TEST_ASSERT(rc == NGX_OK,
                    "diagnostics JSON should render each error policy");

        snprintf(effective_expected, sizeof(effective_expected),
                 "\"error_policy\":\"%s\"", cases[i].value);
        TEST_ASSERT(strstr((const char *) b.pos, effective_expected) != NULL,
                    "effective should preserve the unified policy");
    }

    TEST_PASS("Every unified error policy is preserved in diagnostics JSON");
}


/*
 * Drive ngx_http_markdown_diag_render_dynconf (extracted from build_json to
 * satisfy Rule 17 / S3776 cognitive-complexity limit) through every dynconf
 * state branch.  Regression coverage for the refactor: the byte layout for
 * each branch must match the pre-extraction output.
 */
static void
test_json_dynconf_state_branches(void)
{
    static const char digest[] =
        "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    ngx_http_markdown_diag_dynconf_t dynconf;
    ngx_http_request_t       r;
    ngx_connection_t         c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in       addr;
    ngx_buf_t                b;
    ngx_int_t                rc;
    const char              *json;

    TEST_SUBSECTION("diagnostics render_dynconf state branches");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);

    /* ACTIVE: lkg_digest present, last_success present, no last_error. */
    memset(&dynconf, 0, sizeof(dynconf));
    dynconf.state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_ACTIVE;
    snprintf((char *) dynconf.source_digest, sizeof(dynconf.source_digest),
             "%s", digest);
    snprintf((char *) dynconf.active_digest, sizeof(dynconf.active_digest),
             "%s", digest);
    snprintf((char *) dynconf.lkg_digest, sizeof(dynconf.lkg_digest),
             "%s", digest);
    dynconf.generation = 7;
    dynconf.has_last_success = 1;
    dynconf.last_success = 1700000000;
    dynconf.lkg_valid = 1;
    g_dynconf_override = &dynconf;

    memset(&b, 0, sizeof(b));
    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_OK, "ACTIVE branch build should succeed");
    json = (const char *) b.pos;
    TEST_ASSERT(strstr(json, "\"generation\":7") != NULL,
                "ACTIVE branch should render generation");
    TEST_ASSERT(strstr(json, "\"lkg_digest\":\"sha256:") != NULL,
                "ACTIVE branch should render lkg_digest string");
    TEST_ASSERT(strstr(json, "\"last_success\":\"") != NULL,
                "ACTIVE branch should render last_success timestamp");
    TEST_ASSERT(strstr(json, "\"last_error\":null") != NULL,
                "ACTIVE branch should render last_error null");

    /* LKG_PRESERVED with last_error: lkg_digest present + last_error string. */
    memset(&dynconf, 0, sizeof(dynconf));
    dynconf.state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_LKG_PRESERVED;
    snprintf((char *) dynconf.source_digest, sizeof(dynconf.source_digest),
             "%s", digest);
    snprintf((char *) dynconf.active_digest, sizeof(dynconf.active_digest),
             "%s", digest);
    snprintf((char *) dynconf.lkg_digest, sizeof(dynconf.lkg_digest),
             "%s", digest);
    dynconf.generation = 9;
    dynconf.lkg_valid = 1;
    memcpy(dynconf.last_error, "boom", 4);
    dynconf.last_error_len = 4;
    g_dynconf_override = &dynconf;

    memset(&b, 0, sizeof(b));
    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_OK, "LKG_PRESERVED branch build should succeed");
    json = (const char *) b.pos;
    TEST_ASSERT(strstr(json, "\"generation\":9") != NULL,
                "LKG_PRESERVED branch should render generation");
    TEST_ASSERT(strstr(json, "\"last_error\":\"boom\"") != NULL,
                "LKG_PRESERVED branch should render last_error string");

    /* INVALID_NO_LKG with last_error: all-null fields + last_error string. */
    memset(&dynconf, 0, sizeof(dynconf));
    dynconf.state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_INVALID_NO_LKG;
    memcpy(dynconf.last_error, "parse failed", 12);
    dynconf.last_error_len = 12;
    g_dynconf_override = &dynconf;

    memset(&b, 0, sizeof(b));
    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_OK, "INVALID_NO_LKG branch build should succeed");
    json = (const char *) b.pos;
    TEST_ASSERT(strstr(json, "\"generation\":null") != NULL
                && strstr(json, "\"source_digest\":null") != NULL
                && strstr(json, "\"lkg_digest\":null") != NULL,
                "INVALID_NO_LKG branch should render null digest fields");
    TEST_ASSERT(strstr(json, "\"last_error\":\"parse failed\"") != NULL,
                "INVALID_NO_LKG branch should render last_error string");

    /* Disabled/other: every field null including last_error. */
    memset(&dynconf, 0, sizeof(dynconf));
    dynconf.state = NGX_HTTP_MARKDOWN_DIAG_DYNCONF_DISABLED;
    g_dynconf_override = &dynconf;

    memset(&b, 0, sizeof(b));
    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_OK, "DISABLED branch build should succeed");
    json = (const char *) b.pos;
    TEST_ASSERT(strstr(json, "\"generation\":null") != NULL
                && strstr(json, "\"last_error\":null") != NULL,
                "DISABLED branch should render all-null dynconf fields");

    g_dynconf_override = NULL;
    TEST_PASS("render_dynconf covers every state branch");
}


static void
test_json_preserves_effective_streaming_buffer(void)
{
    ngx_http_request_t       r;
    ngx_connection_t         c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in       addr;
    ngx_buf_t                 b;
    ngx_int_t                 rc;

    TEST_SUBSECTION("diagnostics preserve effective streaming buffer");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    g_effective_streaming_buffer = 65535;
    memset(&b, 0, sizeof(b));

    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_OK,
                "diagnostics JSON should render the effective buffer");
    TEST_ASSERT(strstr((const char *) b.pos,
                       "\"streaming_buffer\":65535") != NULL,
                "diagnostics must not replace the effective buffer value");

    TEST_PASS("Diagnostics preserve the effective streaming buffer");
}


static void
test_diagnostics_has_no_legacy_profile_surface(void)
{
    ngx_http_request_t       r;
    ngx_connection_t         c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in       addr;
    ngx_buf_t                b;
    ngx_int_t                rc;
    const char              *json;

    TEST_SUBSECTION("diagnostics has no legacy profile surface");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_AUTO;
    conf.stream.policy_explicit = 0;
    memset(&b, 0, sizeof(b));

    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_OK,
                "effective configuration diagnostics JSON should succeed");
    json = (const char *) b.pos;
    TEST_ASSERT(strstr(json, "\"profile\"") == NULL
                && strstr(json, "\"streaming_config\"") == NULL,
                "removed profile and streaming fields must stay absent");
    TEST_ASSERT(strstr(json, "\"effective\":") != NULL
                && strstr(json, "\"effective_sources\":") != NULL,
                "effective configuration must carry the replacement surface");

    TEST_PASS("Effective streaming source is preserved");
}

static void
test_json_buffer_scales_with_ring_count(void)
{
    ngx_http_request_t r;
    ngx_connection_t c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in addr;
    ngx_buf_t b;
    ngx_int_t rc;
    size_t expected_size;

    TEST_SUBSECTION("diagnostics JSON buffer scales with ring count");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);

    rc = ngx_http_markdown_diagnostics_init(
        &ngx_http_markdown_g_diag_state, r.pool, 150);
    TEST_ASSERT(rc == NGX_OK, "global init should succeed");
    ngx_http_markdown_g_diag_state.enabled = 1;

    for (ngx_uint_t i = 0; i < 150; i++) {
        ngx_current_msec = 3000 + i;
        ngx_http_markdown_diagnostics_record(
            &ngx_http_markdown_g_diag_state, (ngx_int_t) i, i);
    }

    memset(&b, 0, sizeof(b));
    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_OK, "large diagnostics JSON should succeed");

    expected_size = NGX_HTTP_MARKDOWN_DIAG_JSON_BASE_SIZE
                    + (6 * (sizeof(((ngx_http_markdown_diag_dynconf_t *) 0)
                                ->last_error) - 1))
                    + (150 * NGX_HTTP_MARKDOWN_DIAG_JSON_DECISION_SIZE);
    TEST_ASSERT((size_t) (b.end - b.start) == expected_size,
                "JSON buffer should scale with recorded decisions");
    TEST_ASSERT(strstr((const char *) b.pos, "\"reason\":\"converted\"")
                != NULL,
                "JSON should include newest high-count decision");

    TEST_PASS("Diagnostics JSON buffer scaling covered");
}

static void
test_json_builder_rejects_invalid_ring_state(void)
{
    ngx_http_request_t r;
    ngx_connection_t c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in addr;
    ngx_buf_t b;
    ngx_int_t rc;

    TEST_SUBSECTION("diagnostics JSON rejects invalid ring state");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);

    rc = ngx_http_markdown_diagnostics_init(
        &ngx_http_markdown_g_diag_state, r.pool, 2);
    TEST_ASSERT(rc == NGX_OK, "global init should succeed");

    ngx_http_markdown_g_diag_state.ring.capacity = 0;
    memset(&b, 0, sizeof(b));
    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_ERROR, "zero capacity with entries should fail");

    rc = ngx_http_markdown_diagnostics_init(
        &ngx_http_markdown_g_diag_state, r.pool, 2);
    TEST_ASSERT(rc == NGX_OK, "global reinit should succeed");
    ngx_http_markdown_g_diag_state.enabled = 1;
    ngx_http_markdown_diagnostics_record(&ngx_http_markdown_g_diag_state,
                                         7, 11);

    ngx_http_markdown_g_diag_state.ring.head =
        ngx_http_markdown_g_diag_state.ring.capacity;
    memset(&b, 0, sizeof(b));
    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_ERROR, "out-of-range head should fail");

    ngx_http_markdown_diagnostics_cleanup(&ngx_http_markdown_g_diag_state);

    TEST_PASS("Invalid diagnostics ring state covered");
}

static void
test_access_json_and_logging_failure_branches(void)
{
    ngx_http_request_t r;
    ngx_connection_t c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in addr;
    ngx_buf_t b;
    ngx_int_t rc;
    ngx_http_markdown_decision_path_t path;

    TEST_SUBSECTION("diagnostics access, JSON, and logging failure branches");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    c.sockaddr = NULL;
    rc = ngx_http_markdown_diagnostics_check_access(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN,
                "missing sockaddr should be forbidden");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    g_alloc_fail_after = 0;
    memset(&b, 0, sizeof(b));
    rc = ngx_http_markdown_diagnostics_build_json(&r, &b);
    TEST_ASSERT(rc == NGX_ERROR, "JSON buffer allocation failure should fail");

    memset(&path, 0, sizeof(path));
    ngx_http_markdown_log_decision_path(NULL, NULL, NULL, &path);

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    ngx_http_markdown_log_decision_path(&r, NULL, NULL, &path);

    TEST_PASS("Access, JSON, and logging failure branches covered");
}

static void
test_handler_get_head_and_denials(void)
{
    ngx_http_request_t r;
    ngx_connection_t c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in addr;
    ngx_int_t rc;

    TEST_SUBSECTION("diagnostics handler GET/HEAD and denial paths");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);

    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_OK, "GET handler should return output status");
    TEST_ASSERT(g_send_header_calls == 1, "GET should send headers");
    TEST_ASSERT(g_output_filter_calls == 1, "GET should send body");
    TEST_ASSERT(g_last_output_chain != NULL, "GET should pass output chain");
    TEST_ASSERT(r.headers_out.status == NGX_HTTP_OK,
                "GET should set 200 status");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    r.method = NGX_HTTP_HEAD;

    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_OK, "HEAD handler should succeed");
    TEST_ASSERT(g_send_header_calls == 1, "HEAD should send headers");
    TEST_ASSERT(g_output_filter_calls == 0, "HEAD should not send body");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    r.method = 0;
    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_OK,
                "non-GET/HEAD should return a rendered error response");
    TEST_ASSERT(r.headers_out.status == NGX_HTTP_NOT_ALLOWED,
                "non-GET/HEAD should set 405 status");
    TEST_ASSERT(g_allow_header.hash == 1,
                "405 response should include an active Allow header");
    TEST_ASSERT(g_allow_header.key.len == sizeof("Allow") - 1
                && memcmp(g_allow_header.key.data, "Allow",
                          sizeof("Allow") - 1) == 0,
                "405 response should name the Allow header");
    TEST_ASSERT(g_allow_header.value.len == sizeof("GET, HEAD") - 1
                && memcmp(g_allow_header.value.data, "GET, HEAD",
                          sizeof("GET, HEAD") - 1) == 0,
                "405 Allow header should advertise GET and HEAD");
    TEST_ASSERT(g_output_filter_calls == 1
                && g_last_output_chain != NULL
                && g_last_output_chain->buf != NULL,
                "405 response should send a body");
    TEST_ASSERT(g_last_output_chain->buf->last - g_last_output_chain->buf->pos
                == (off_t) (sizeof("Method Not Allowed. Use GET or HEAD; "
                                  "rollback is available through the "
                                  "dynamic-config file watcher.\n") - 1),
                "405 body should have the expected length");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    addr.sin_addr.s_addr = htonl(0x0a000001);
    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN,
                "non-loopback access should be rejected");
    TEST_ASSERT(r.headers_out.status == NGX_HTTP_FORBIDDEN,
                "diagnostics must deny non-loopback peers");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    addr.sin_addr.s_addr = htonl(0x0a000001);
    r.method = NGX_HTTP_POST;
    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN,
                "access denial must precede method validation");
    TEST_ASSERT(r.headers_out.status == NGX_HTTP_FORBIDDEN,
                "denied mutation requests must not disclose 405");
    TEST_ASSERT(g_allow_header.hash == 0,
                "denied mutation requests must not receive Allow");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    r.method = 0;
    g_list_push_fail = 1;
    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_HTTP_INTERNAL_SERVER_ERROR,
                "405 Allow header allocation failure must yield 500");
    TEST_ASSERT(r.headers_out.status != NGX_HTTP_NOT_ALLOWED,
                "a failed Allow allocation must not emit a 405");
    TEST_ASSERT(g_send_header_calls == 0 && g_output_filter_calls == 0,
                "no response may be sent when Allow allocation fails");

    TEST_PASS("Handler paths covered");
}

/*
 * Verify that the diagnostics endpoint remains read-only and rejects
 * mutation methods even when a rollback action is supplied.
 */
static void
test_handler_post_not_allowed(void)
{
    ngx_http_request_t r;
    ngx_connection_t c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in addr;
    ngx_int_t rc;
    static u_char rollback_args[] = "action=rollback";

    TEST_SUBSECTION("diagnostics handler rejects mutation methods");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    r.method = NGX_HTTP_POST;
    r.args.data = rollback_args;
    r.args.len = sizeof(rollback_args) - 1;

    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_OK,
                "POST rollback action must return a rendered 405 response");
    TEST_ASSERT(r.headers_out.status == NGX_HTTP_NOT_ALLOWED,
                "POST rollback action must set 405 status");
    TEST_ASSERT(g_send_header_calls == 1,
                "rejected POST must send headers");
    TEST_ASSERT(g_output_filter_calls == 1,
                "rejected POST must send a body");

    TEST_PASS("diagnostics endpoint is read-only");
}

static void
test_handler_failure_branches(void)
{
    ngx_http_request_t r;
    ngx_connection_t c;
    ngx_http_markdown_conf_t conf;
    struct sockaddr_in addr;
    ngx_int_t rc;

    TEST_SUBSECTION("diagnostics handler failure branches");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    g_discard_rc = NGX_ERROR;
    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_ERROR, "discard failure should propagate");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    g_alloc_fail_after = 0;
    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_HTTP_INTERNAL_SERVER_ERROR,
                "response buffer allocation failure should return 500");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    r.method = NGX_HTTP_HEAD;
    g_send_header_rc = NGX_ERROR;
    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_ERROR, "HEAD send_header failure should propagate");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    g_send_header_rc = NGX_ERROR;
    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_ERROR, "GET send_header failure should propagate");

    TEST_PASS("Handler failure branches covered");
}

int
main(void)
{
    TEST_SECTION("Diagnostics Production Tests");

    test_lifecycle_and_ring_wrap();
    test_lifecycle_failure_branches();
    test_recording_request_resets_between_config_cycles();
    test_decision_path_records_once_with_explicit_duration();
    test_access_and_json_builder();
    test_json_preserves_unified_error_policy();
    test_json_dynconf_state_branches();
    test_json_preserves_effective_streaming_buffer();
    test_diagnostics_has_no_legacy_profile_surface();
    test_json_buffer_scales_with_ring_count();
    test_json_builder_rejects_invalid_ring_state();
    test_access_json_and_logging_failure_branches();
    test_handler_get_head_and_denials();
    test_handler_post_not_allowed();
    test_handler_failure_branches();

    TEST_PASS("All diagnostics production tests passed");
    return 0;
}
