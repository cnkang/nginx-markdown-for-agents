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

#ifdef MARKDOWN_STREAMING_ENABLED
#define NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS    0
#define NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT  1

typedef struct {
    ngx_http_complex_value_t  *engine;
    size_t                     budget;
    ngx_flag_t                 budget_explicit;
    ngx_uint_t                 on_error;
    ngx_flag_t                 shadow;
    size_t                     auto_threshold;
    ngx_flag_t                 auto_threshold_explicit;
} ngx_http_markdown_streaming_cfg_t;
#endif

#define ngx_memcpy(dst, src, n)      memcpy(dst, src, n)
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
    ngx_uint_t  status;
    size_t      content_type_len;
    ngx_str_t   content_type;
    u_char     *content_type_lowcase;
    off_t       content_length_n;
} ngx_http_headers_out_t;

typedef struct {
    ngx_log_t        *log;
    struct sockaddr  *sockaddr;
} ngx_connection_t;

struct ngx_http_request_s {
    ngx_uint_t               method;
    ngx_pool_t              *pool;
    ngx_connection_t        *connection;
    ngx_http_headers_out_t   headers_out;
    ngx_http_request_t      *main;
    void                    *loc_conf;
};

typedef struct {
    ngx_array_t *diagnostics_allow;
} ngx_http_markdown_ops_cfg_t;

typedef struct {
    ngx_uint_t  log_verbosity;
} ngx_http_markdown_policy_cfg_t;

typedef struct ngx_http_markdown_conf_s {
    ngx_http_markdown_ops_cfg_t     ops;
    ngx_http_markdown_policy_cfg_t  policy;
#ifdef MARKDOWN_STREAMING_ENABLED
    ngx_http_markdown_streaming_cfg_t streaming;
#endif
    struct {
        ngx_uint_t    engine;
        size_t        threshold;
        size_t        precommit_buffer;
        size_t        flush_min;
        ngx_array_t  *excluded_types;
        ngx_uint_t    on_error;
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
static int g_discard_rc;
static int g_alloc_fail_after = -1;

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

#define NGX_HTTP_MARKDOWN_STREAM_ENGINE_OFF   0
#define NGX_HTTP_MARKDOWN_STREAM_ENGINE_AUTO  1
#define NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON    2
#define NGX_HTTP_MARKDOWN_ON_ERROR_REJECT  1

#define NGX_HTTP_MARKDOWN_FILTER_MODULE_H
#include "../src/ngx_http_markdown_diagnostics.c"

void
ngx_http_markdown_diagnostics_collect_metrics(
    ngx_http_markdown_diag_metrics_t *out)
{
    out->conversions_total = 7;
    out->delivery_total = 6;
    out->requests_total = 9;
    out->failopen_total = 1;
}

void
ngx_http_markdown_diagnostics_get_dynconf_state(
    ngx_http_markdown_diag_dynconf_t *out)
{
    out->active_mtime = 100;
    out->config_version = 3;
    out->last_known_good_mtime = 90;
    out->lkg_valid = 1;
}

static void
reset_test_state(void)
{
    g_send_header_calls = 0;
    g_send_header_rc = NGX_OK;
    g_output_filter_calls = 0;
    g_last_output_chain = NULL;
    g_discard_rc = NGX_OK;
    g_alloc_fail_after = -1;
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
                   + NGX_HTTP_MARKDOWN_DIAG_JSON_DECISION_SIZE,
                "JSON buffer should account for recorded decisions");

    json = (const char *) b.pos;
    TEST_ASSERT(strstr(json, "\"config_snapshot\"") != NULL,
                "JSON should include config snapshot");
    TEST_ASSERT(strstr(json, "\"recent_decisions\"") != NULL,
                "JSON should include recent decisions");
    TEST_ASSERT(strstr(json, "\"metrics_snapshot\"") != NULL,
                "JSON should include metrics");
    TEST_ASSERT(strstr(json, "\"dynconf_state\"") != NULL,
                "JSON should include dynconf state");
    TEST_ASSERT(strstr(json,
                "\"legacy_auto_threshold_explicit\": false") != NULL,
                "JSON should expose legacy threshold bridge state");
    TEST_ASSERT(strstr(json, "\"reason_code\": 11") != NULL,
                "JSON should include recorded reason");

    TEST_PASS("Access and JSON builder covered");
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
                    + (150 * NGX_HTTP_MARKDOWN_DIAG_JSON_DECISION_SIZE);
    TEST_ASSERT((size_t) (b.end - b.start) == expected_size,
                "JSON buffer should scale with recorded decisions");
    TEST_ASSERT(strstr((const char *) b.pos, "\"reason_code\": 149") != NULL,
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
    TEST_ASSERT(rc == NGX_HTTP_NOT_ALLOWED,
                "non-GET/HEAD should be denied");

    reset_test_state();
    init_request(&r, &c, &conf, &addr);
    addr.sin_addr.s_addr = htonl(0x0a000001);
    rc = ngx_http_markdown_diagnostics_handler(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN,
                "non-loopback should be forbidden");

    TEST_PASS("Handler paths covered");
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
    test_access_and_json_builder();
    test_json_buffer_scales_with_ring_count();
    test_json_builder_rejects_invalid_ring_state();
    test_access_json_and_logging_failure_branches();
    test_handler_get_head_and_denials();
    test_handler_failure_branches();

    TEST_PASS("All diagnostics production tests passed");
    return 0;
}
