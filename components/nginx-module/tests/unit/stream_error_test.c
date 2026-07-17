/*
 * Test: stream_error
 *
 * Validates the streaming error handler integration module (streaming fallback state machine,
 * streaming error policy integration):
 *
 * 6.1: Pre-commit + pass -> PASS_HTML (replay)
 * 6.2: Pre-commit + fail_closed/status -> conf->error_status
 * 6.3: Post-commit + pass -> safe_finish (abort fallback)
 * 6.4: Post-commit + fail_closed/status -> abort
 *
 * Also covers edge cases: NULL parameters, passthrough state,
 * replay chain NULL, output filter failure, non-error on_error.
 */

#include "../include/test_common.h"

/* Pull in base NGINX types from stubs */
#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

/* Additional defines */
#ifndef NGX_HTTP_BAD_GATEWAY
#define NGX_HTTP_BAD_GATEWAY  502
#endif

#ifndef NGX_LOG_DEBUG_HTTP
#define NGX_LOG_DEBUG_HTTP 0
#endif

#define ngx_log_debug0(level, log, err, fmt) \
    do { (void)(level); (void)(log); (void)(err); (void)(fmt); } while (0)
#define ngx_log_debug1(level, log, err, fmt, a1) \
    do { (void)(level); (void)(log); (void)(err); (void)(fmt); (void)(a1); } while (0)
#define ngx_log_debug3(level, log, err, fmt, a1, a2, a3) \
    do { (void)(level); (void)(log); (void)(err); (void)(fmt); (void)(a1); (void)(a2); (void)(a3); } while (0)

#ifndef NGX_CONF_UNSET
#define NGX_CONF_UNSET (-1)
#endif

#ifndef NGX_CONF_UNSET_UINT
#define NGX_CONF_UNSET_UINT ((ngx_uint_t) -1)
#endif

#ifndef NGX_CONF_UNSET_SIZE
#define NGX_CONF_UNSET_SIZE ((size_t) -1)
#endif

#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE ((size_t) -1)
#endif

#define MARKDOWN_STREAMING_ENABLED 1

#ifndef ngx_str_set
#define ngx_str_set(str, text)                                    \
    (str)->len = sizeof(text) - 1; (str)->data = (u_char *) text
#endif

#ifndef ngx_strncasecmp
#define ngx_strncasecmp(s1, s2, n) \
    strncasecmp((const char *) (s1), (const char *) (s2), (n))
#endif

typedef intptr_t ngx_err_t;

/* Define structs that the stubs only forward-declare */
struct ngx_log_s { int dummy; };
struct ngx_pool_s { ngx_log_t *log; };
struct ngx_array_s {
    void       *elts;
    ngx_uint_t  nelts;
    size_t      size;
    ngx_uint_t  nalloc;
    ngx_pool_t *pool;
};
struct ngx_shm_zone_s { int dummy; };
struct ngx_module_s { int dummy; };
struct ngx_command_s { int dummy; };
struct ngx_conf_s { ngx_pool_t *pool; };
struct ngx_chain_s { ngx_buf_t *buf; struct ngx_chain_s *next; };
struct ngx_http_complex_value_s { ngx_str_t value; };

typedef struct {
    ngx_log_t *log;
} ngx_connection_impl_t;

typedef struct {
    ngx_str_t     content_type;
    size_t        content_type_len;
    u_char       *content_type_lowcase;
    ngx_uint_t    status;
    off_t         content_length_n;
} ngx_http_headers_out_t;

struct ngx_http_request_s {
    ngx_connection_impl_t  *connection;
    ngx_pool_t             *pool;
    ngx_http_headers_out_t  headers_out;
    struct ngx_http_request_s *main;
    ngx_uint_t              buffered;
};

/* Include the module header for types — must follow type/macro setup above */
#include "../../src/ngx_http_markdown_filter_module.h" /* NOSONAR(S954) test include-after-setup pattern */

ngx_module_t ngx_http_markdown_filter_module;

static ngx_int_t (*ngx_http_next_body_filter)(ngx_http_request_t *r,
    ngx_chain_t *in);
#include "../../src/ngx_http_markdown_filter_chain_impl.h"

void
ngx_http_markdown_metrics_record_postcommit_pending(size_t bytes)
{
    UNUSED(bytes);
}

void
ngx_http_markdown_metrics_record_postcommit_copied_delivery(size_t bytes)
{
    UNUSED(bytes);
}

/* Include the decision engine source directly */
#include "../../src/ngx_http_markdown_stream_state.h"
#include "../../src/ngx_http_markdown_stream_state.c"

/*
 * Include the postcommit source directly so we can test the real
 * safe_finish/abort functions instead of mocking them.
 */
#include "../../src/ngx_http_markdown_stream_postcommit.h"
#include "../../src/markdown_converter.h"

#ifndef ngx_memcpy
#define ngx_memcpy(dst, src, n) memcpy((dst), (src), (n))
#endif

/*
 * Test state: track calls to mocked functions.
 */
static int test_output_filter_called;
static int test_output_filter_rc;
static ngx_chain_t *test_output_filter_chain;
static int test_poison_top_filter_called;
static int test_replay_chain_called;
static ngx_chain_t *test_replay_chain_result;

/* Track ngx_calloc_buf behavior for send_terminal */
static int test_calloc_buf_called;
static ngx_buf_t *test_calloc_buf_result;
static ngx_buf_t  test_calloc_buf_storage;
static ngx_chain_t test_chain_link_storage;
static u_char test_palloc_storage[256];
static int test_palloc_called;
static int test_palloc_fail;
static ngx_int_t test_finalize_status;

/* Mocked request infrastructure */
static ngx_log_t             test_log;
static struct ngx_pool_s     test_pool;
static ngx_connection_impl_t test_connection;
static ngx_http_request_t    test_request;

/* Function prototypes */
static void test_setup(void);

/* Saved downstream body filter used by the production delegation helper. */
static ngx_int_t
test_next_body_filter(ngx_http_request_t *r, /* NOSONAR(S995) NGINX filter signature */
                      ngx_chain_t *in)
{
    UNUSED(r);
    test_output_filter_called++;
    test_output_filter_chain = in;
    return test_output_filter_rc;
}

/* Poison top filter: replay and postcommit output must bypass it. */
ngx_int_t
ngx_http_output_filter(ngx_http_request_t *r, /* NOSONAR(S995) NGINX API signature */
                       ngx_chain_t *in) /* NOSONAR(S995) NGINX API signature */
{
    UNUSED(r);
    UNUSED(in);
    test_poison_top_filter_called++;
    return NGX_ERROR;
}

/* Mock: replay_chain */
ngx_chain_t *
ngx_http_markdown_stream_replay_chain(const ngx_http_markdown_ctx_t *ctx,
                                       ngx_pool_t *pool) /* NOSONAR(S995) match real signature */
{
    UNUSED(ctx); UNUSED(pool);
    test_replay_chain_called++;
    return test_replay_chain_result;
}

/* Mock: replay_available */
ngx_flag_t
ngx_http_markdown_stream_replay_available(
    const ngx_http_markdown_ctx_t *ctx)
{
    if (ctx == NULL) { return 0; }
    if (!ctx->stream_sm.replay_initialized) { return 0; }
    if (ctx->stream_sm.replay_buf.size > ctx->stream_sm.replay_capacity) {
        return 0;
    }
    return 1;
}

/* Mock: ngx_calloc_buf (for send_terminal) */
ngx_buf_t *
ngx_calloc_buf(ngx_pool_t *pool) /* NOSONAR(S995) NGINX API signature */
{
    UNUSED(pool);
    test_calloc_buf_called++;
    if (test_calloc_buf_result != NULL) {
        return test_calloc_buf_result;
    }
    memset(&test_calloc_buf_storage, 0, sizeof(test_calloc_buf_storage));
    return &test_calloc_buf_storage;
}

void *
ngx_palloc(ngx_pool_t *pool, size_t size) /* NOSONAR(S995) NGINX API signature */
{
    UNUSED(pool);
    test_palloc_called++;

    if (test_palloc_fail || size > sizeof(test_palloc_storage)) {
        return NULL;
    }

    memset(test_palloc_storage, 0, sizeof(test_palloc_storage));
    return test_palloc_storage;
}

ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool) /* NOSONAR(S995) NGINX API signature */
{
    UNUSED(pool);
    memset(&test_chain_link_storage, 0, sizeof(test_chain_link_storage));
    return &test_chain_link_storage;
}

/* Stub: ngx_log_error_core — variadic matches NGINX core signature */
void
ngx_log_error_core(ngx_uint_t level, ngx_log_t *log, /* NOSONAR(S923, S995) NGINX API: variadic + non-const */
                   ngx_err_t err, const char *fmt, ...)
{
    UNUSED(level); UNUSED(log); UNUSED(err); UNUSED(fmt);
}

uint32_t
markdown_streaming_safe_finish(struct StreamingConverterHandle *handle, /* NOSONAR(S995) FFI signature */
    u_char **out_data, uintptr_t *out_len)
{
    UNUSED(handle);
    if (out_data != NULL) {
        *out_data = NULL;
    }
    if (out_len != NULL) {
        *out_len = 0;
    }
    return POST_COMMIT_ABORT;
}

void
markdown_streaming_abort(struct StreamingConverterHandle *handle) /* NOSONAR(S995) FFI signature */
{
    UNUSED(handle);
}

void
markdown_streaming_output_free(u_char *data, uintptr_t len) /* NOSONAR(S995) FFI signature */
{
    UNUSED(data);
    UNUSED(len);
}

/* Include the postcommit source (for safe_finish, abort, guard, log) */
#include "../../src/ngx_http_markdown_stream_postcommit.c"

static ngx_int_t
test_filter_finalize_request(ngx_http_request_t *r, ngx_module_t *module,
    ngx_int_t status)
{
    UNUSED(r);
    UNUSED(module);
    test_finalize_status = status;
    return NGX_ERROR;
}

/* Include the error handler source directly */
#define ngx_http_filter_finalize_request test_filter_finalize_request
#include "../../src/ngx_http_markdown_stream_error.c"
#undef ngx_http_filter_finalize_request


static void test_setup(void)
{
    test_output_filter_called = 0;
    test_output_filter_rc = NGX_OK;
    test_output_filter_chain = NULL;
    test_poison_top_filter_called = 0;
    ngx_http_next_body_filter = test_next_body_filter;
    test_replay_chain_called = 0;
    test_replay_chain_result = NULL;
    test_calloc_buf_called = 0;
    test_calloc_buf_result = NULL;
    memset(&test_calloc_buf_storage, 0, sizeof(test_calloc_buf_storage));
    memset(&test_chain_link_storage, 0, sizeof(test_chain_link_storage));
    memset(test_palloc_storage, 0, sizeof(test_palloc_storage));
    test_palloc_called = 0;
    test_palloc_fail = 0;
    test_finalize_status = 0;
    memset(&test_log, 0, sizeof(test_log));
    memset(&test_pool, 0, sizeof(test_pool));
    memset(&test_connection, 0, sizeof(test_connection));
    memset(&test_request, 0, sizeof(test_request));
    test_pool.log = &test_log;
    test_connection.log = &test_log;
    test_request.connection = &test_connection;
    test_request.main = &test_request;
    test_request.pool = &test_pool;
}


/* --- pre-commit pass: replay HTML --- */

static void test_task_6_1_precommit_pass_replay_html(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_chain_t fc;
    ngx_buf_t fb;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&fc, 0, sizeof(fc));
    memset(&fb, 0, sizeof(fb));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_capacity = 1024;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    fc.buf = &fb;
    fc.next = NULL;
    test_replay_chain_result = &fc;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_OK, "6.1: returns NGX_OK");
    TEST_ASSERT(test_replay_chain_called == 1, "6.1: replay_chain called");
    TEST_ASSERT(test_output_filter_called == 1,
                "6.1: saved downstream filter called");
    TEST_ASSERT(test_output_filter_chain == &fc,
                "6.1: helper forwarded replay chain");
    TEST_ASSERT(test_poison_top_filter_called == 0,
                "6.1: replay bypassed poison top filter");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "6.1: state PASSTHROUGH");
    TEST_ASSERT(test_request.headers_out.content_type_len
                == sizeof("text/html") - 1, "6.1: CT=text/html");
    TEST_PASS("pre-commit pass: replay HTML");
}

static void test_precommit_uses_unified_pass_policy(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_chain_t fc;
    ngx_buf_t fb;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&fc, 0, sizeof(fc));
    memset(&fb, 0, sizeof(fb));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_capacity = 1024;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    fc.buf = &fb;
    fc.next = NULL;
    test_replay_chain_result = &fc;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_OK,
        "unified pass policy should replay buffered HTML");
    TEST_ASSERT(test_output_filter_called == 1,
        "unified pass policy should call output filter");
    TEST_PASS("Pre-commit error uses unified pass policy");
}

static void test_precommit_uses_unified_reject_policy(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_chain_t fc;
    ngx_buf_t fb;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&fc, 0, sizeof(fc));
    memset(&fb, 0, sizeof(fb));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_capacity = 1024;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    conf.error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;

    fc.buf = &fb;
    fc.next = NULL;
    test_replay_chain_result = &fc;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_ERROR,
        "unified reject policy finalizes with the configured error status");
    TEST_ASSERT(test_output_filter_called == 0,
        "unified reject policy should not replay HTML");
    TEST_PASS("Pre-commit error uses unified reject policy");
}

static void test_precommit_pass_replay_html_backpressure(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_chain_t fc;
    ngx_buf_t fb;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&fc, 0, sizeof(fc));
    memset(&fb, 0, sizeof(fb));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_capacity = 1024;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    fc.buf = &fb;
    fc.next = NULL;
    test_replay_chain_result = &fc;
    test_output_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_AGAIN, "replay backpressure -> NGX_AGAIN");
    TEST_ASSERT(test_output_filter_called == 1,
        "replay backpressure came from saved downstream filter");
    TEST_ASSERT(test_poison_top_filter_called == 0,
        "replay backpressure bypassed poison top filter");
    TEST_ASSERT(test_output_filter_chain == &fc,
        "output filter should receive replay chain");
    TEST_ASSERT(ctx.streaming.pending_output == &fc,
        "replay chain saved as pending output");
    TEST_ASSERT(ctx.streaming.pending_meta.has_data == 1,
        "replay pending chain records data");
    TEST_ASSERT(ctx.streaming.pending_meta.bytes == 100,
        "replay pending byte count preserved");
    TEST_ASSERT(ctx.streaming.completion.pending_failopen_delivery == 1,
        "fail-open delivery latch set for replay pending chain");
    TEST_ASSERT((test_request.buffered & NGX_HTTP_MARKDOWN_BUFFERED) != 0,
        "request buffered flag set on replay backpressure");
    TEST_PASS("Pre-commit pass replay preserves NGX_AGAIN pending chain");
}

static void test_precommit_pass_replay_preserves_existing_pending(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_chain_t existing;
    ngx_chain_t fc;
    ngx_buf_t fb;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&existing, 0, sizeof(existing));
    memset(&fc, 0, sizeof(fc));
    memset(&fb, 0, sizeof(fb));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_capacity = 1024;
    ctx.streaming.pending_output = &existing;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    fc.buf = &fb;
    fc.next = NULL;
    test_replay_chain_result = &fc;
    test_output_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_ERROR,
        "existing pending chain should reject replay overwrite");
    TEST_ASSERT(ctx.streaming.pending_output == &existing,
        "existing pending chain should be preserved");
    TEST_PASS("Pre-commit replay backpressure preserves existing pending");
}

/* --- pre-commit reject: return configured error_status --- */

static void test_task_6_2_precommit_reject_502(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_capacity = 1024;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    conf.error_status = NGX_HTTP_MARKDOWN_ERROR_STATUS_DEFAULT;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_ERROR, "6.2: finalizer returns NGX_ERROR (error_status=502)");
    TEST_ASSERT(test_finalize_status == 502,
                "6.2: fail_closed finalizes with 502");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_PASSTHROUGH,
                "6.2: state PASSTHROUGH");
    TEST_ASSERT(test_replay_chain_called == 0, "6.2: no replay");
    TEST_ASSERT(test_output_filter_called == 0, "6.2: no output_filter");
    TEST_PASS("pre-commit reject: finalize with configured error_status");
}


static void
test_precommit_explicit_status_policies(void)
{
    static const ngx_uint_t statuses[] = { 429, 503 };
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;
    ngx_uint_t i;

    for (i = 0; i < sizeof(statuses) / sizeof(statuses[0]); i++) {
        test_setup();
        memset(&ctx, 0, sizeof(ctx));
        memset(&conf, 0, sizeof(conf));

        ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
        ctx.stream_sm.replay_initialized = 1;
        ctx.stream_sm.replay_capacity = 1024;
        conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
        conf.error_status = statuses[i];

        rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

        TEST_ASSERT(rc == NGX_ERROR,
                    "explicit status finalizer return should propagate");
        TEST_ASSERT(test_finalize_status == (ngx_int_t) statuses[i],
                    "explicit policy must finalize with configured status");
        TEST_ASSERT(test_replay_chain_called == 0,
                    "explicit status policy must not replay HTML");
    }

    TEST_PASS("pre-commit status 429/503 finalize exactly");
}

/* --- post-commit pass: safe_finish --- */

static void test_task_6_3_postcommit_pass_safe_finish(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.stream_sm.headers_committed = 1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_OK, "6.3: returns NGX_OK");
    TEST_ASSERT(ctx.stream_sm.state
                == NGX_HTTP_MD_STATE_POST_COMMIT_SAFE_FINISH,
                "6.3: state POST_COMMIT_SAFE_FINISH");
    TEST_ASSERT(test_replay_chain_called == 0, "6.3: no replay");
    TEST_ASSERT(test_calloc_buf_called >= 1, "6.3: send_terminal called");
    TEST_PASS("post-commit pass: safe_finish");
}

/* --- safe_finish send_terminal fails -> abort fallback --- */

static void test_task_6_3_postcommit_pass_safe_finish_fails(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.stream_sm.headers_committed = 1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    /* Make send_terminal fail (calloc_buf returns NULL) */
    test_calloc_buf_result = NULL;
    /* We need to track calls to detect abort fallback.
     * After safe_finish fails, the error handler calls abort which
     * also calls send_terminal. Let's make the first call fail
     * and the second succeed. */
    test_output_filter_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_ERROR,
        "6.3f: returns abort failure instead of swallowing it");
    TEST_PASS("post-commit pass: safe_finish fails -> abort fallback");
}

static void test_task_6_3_abort_fallback_returns_again(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.stream_sm.headers_committed = 1;
    ctx.streaming.handle = (struct StreamingConverterHandle *) 0x1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    test_output_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_AGAIN,
        "safe_finish fallback abort should propagate NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "abort fallback should preserve pending terminal output");
    TEST_PASS("post-commit pass: abort fallback propagates NGX_AGAIN");
}

/* --- post-commit reject: abort --- */

static void test_task_6_4_postcommit_reject_abort(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.stream_sm.headers_committed = 1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_OK, "6.4: returns NGX_OK (NOT 502)");
    TEST_ASSERT(ctx.stream_sm.state == NGX_HTTP_MD_STATE_POST_COMMIT_ABORT,
                "6.4: state POST_COMMIT_ABORT");
    TEST_ASSERT(test_replay_chain_called == 0, "6.4: no replay");
    TEST_ASSERT(test_calloc_buf_called >= 1, "6.4: send_terminal called");
    TEST_PASS("post-commit reject: abort");
}

static void test_task_6_4_postcommit_abort_returns_again(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;
    ctx.stream_sm.headers_committed = 1;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    test_output_filter_rc = NGX_AGAIN;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_AGAIN,
        "6.4: abort should propagate NGX_AGAIN");
    TEST_ASSERT(ctx.streaming.pending_output != NULL,
        "6.4: abort should preserve pending terminal output");
    TEST_PASS("post-commit reject: abort propagates NGX_AGAIN");
}

/* --- NULL parameters --- */

static void test_null_parameters(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    TEST_ASSERT(ngx_http_markdown_stream_on_error(NULL, &ctx, &conf)
                == NGX_ERROR, "null r");
    TEST_ASSERT(ngx_http_markdown_stream_on_error(&test_request, NULL, &conf)
                == NGX_ERROR, "null ctx");
    TEST_ASSERT(ngx_http_markdown_stream_on_error(&test_request, &ctx, NULL)
                == NGX_ERROR, "null conf");
    TEST_PASS("Null parameter validation");
}

/* --- Passthrough terminal state --- */

static void test_passthrough_state(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PASSTHROUGH;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_OK, "passthrough: NGX_OK");
    TEST_ASSERT(test_replay_chain_called == 0, "passthrough: no replay");
    TEST_ASSERT(test_calloc_buf_called == 0, "passthrough: no send_terminal");
    TEST_PASS("Passthrough (terminal) state");
}

/* --- Replay chain NULL (allocation failure) --- */

static void test_replay_chain_null(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_capacity = 1024;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    /* replay_chain returns NULL */
    test_replay_chain_result = NULL;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_ERROR, "replay chain NULL -> NGX_ERROR");
    TEST_PASS("Replay chain NULL returns NGX_ERROR");
}

/* --- Output filter failure during replay --- */

static void test_output_filter_failure(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_chain_t fc;
    ngx_buf_t fb;
    ngx_int_t rc;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&fc, 0, sizeof(fc));
    memset(&fb, 0, sizeof(fb));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 100;
    ctx.stream_sm.replay_capacity = 1024;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    fc.buf = &fb;
    fc.next = NULL;
    test_replay_chain_result = &fc;
    test_output_filter_rc = NGX_ERROR;

    rc = ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(rc == NGX_ERROR, "output filter error -> NGX_ERROR");
    TEST_PASS("Output filter failure returns NGX_ERROR");
}

/* --- Content-Type restoration in pass_html --- */

static void test_content_type_restored(void)
{
    ngx_http_markdown_ctx_t ctx;
    ngx_http_markdown_conf_t conf;
    ngx_chain_t fc;
    ngx_buf_t fb;

    test_setup();
    memset(&ctx, 0, sizeof(ctx));
    memset(&conf, 0, sizeof(conf));
    memset(&fc, 0, sizeof(fc));
    memset(&fb, 0, sizeof(fb));

    ctx.stream_sm.state = NGX_HTTP_MD_STATE_PRE_COMMIT;
    ctx.stream_sm.headers_committed = 0;
    ctx.stream_sm.replay_initialized = 1;
    ctx.stream_sm.replay_buf.size = 50;
    ctx.stream_sm.replay_capacity = 1024;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;

    /* Set a different Content-Type before the call */
    test_request.headers_out.content_type_len = 30;
    test_request.headers_out.content_type_lowcase =
        (u_char *) test_palloc_storage;  /* mutable buffer avoids const-drop */

    fc.buf = &fb;
    fc.next = NULL;
    test_replay_chain_result = &fc;

    ngx_http_markdown_stream_on_error(&test_request, &ctx, &conf);

    TEST_ASSERT(test_request.headers_out.content_type_len
                == sizeof("text/html") - 1,
                "content_type_len reset to text/html");
    TEST_ASSERT(test_request.headers_out.content_type_lowcase == NULL,
                "content_type_lowcase cleared");
    TEST_PASS("Content-Type restored to text/html in pass_html");
}


int main(void)
{
    TEST_SECTION("Stream Error Handler (streaming error policy integration)");
    test_task_6_1_precommit_pass_replay_html();
    test_precommit_uses_unified_pass_policy();
    test_precommit_uses_unified_reject_policy();
    test_precommit_pass_replay_html_backpressure();
    test_precommit_pass_replay_preserves_existing_pending();
    test_task_6_2_precommit_reject_502();
    test_precommit_explicit_status_policies();
    test_task_6_3_postcommit_pass_safe_finish();
    test_task_6_3_postcommit_pass_safe_finish_fails();
    test_task_6_3_abort_fallback_returns_again();
    test_task_6_4_postcommit_reject_abort();
    test_task_6_4_postcommit_abort_returns_again();
    test_null_parameters();
    test_passthrough_state();
    test_replay_chain_null();
    test_output_filter_failure();
    test_content_type_restored();

    /*
     * Unknown action test: the decision engine's closed enum cannot
     * produce unknown actions through normal call paths.  The default
     * branch in on_error's action switch returns NGX_OK with ERR-level
     * logging, which is verified by code inspection and the comment
     * above the default case.  A future refactoring could introduce
     * a mock decision engine to exercise this branch directly.
     */

    printf("\n  All stream error handler tests passed\n\n");
    return 0;
}
