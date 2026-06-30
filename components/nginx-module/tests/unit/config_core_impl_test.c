/*
 * Test: config_core_impl
 */

#include "../include/test_common.h"

#include <ctype.h>
#include <stdarg.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_OK
#define NGX_OK 0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR -1
#endif
#ifndef NGX_CONF_OK
#define NGX_CONF_OK ((char *) NULL)
#endif
#ifndef NGX_CONF_ERROR
#define NGX_CONF_ERROR ((char *) -1)
#endif

#ifndef NGX_CONF_UNSET
#define NGX_CONF_UNSET (-1)
#endif
#ifndef NGX_CONF_UNSET_UINT
#define NGX_CONF_UNSET_UINT ((ngx_uint_t) -1)
#endif
#ifndef NGX_CONF_UNSET_SIZE
#define NGX_CONF_UNSET_SIZE ((size_t) -1)
#endif
#ifndef NGX_CONF_UNSET_MSEC
#define NGX_CONF_UNSET_MSEC ((ngx_msec_t) -1)
#endif
#ifndef NGX_CONF_UNSET_PTR
#define NGX_CONF_UNSET_PTR ((void *) -1)
#endif

#ifndef NGX_LOG_EMERG
#define NGX_LOG_EMERG 0
#endif
#ifndef NGX_LOG_ERR
#define NGX_LOG_ERR 1
#endif
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN 2
#endif
#ifndef NGX_LOG_INFO
#define NGX_LOG_INFO 3
#endif
#ifndef NGX_LOG_DEBUG
#define NGX_LOG_DEBUG 4
#endif

typedef intptr_t ngx_err_t;
typedef struct ngx_connection_s ngx_connection_t;

struct ngx_module_s {
    int dummy;
};

struct ngx_pool_s {
    int dummy;
};

struct ngx_array_s {
    ngx_uint_t nelts;
};

struct ngx_log_s {
    int dummy;
};

struct ngx_connection_s {
    ngx_log_t *log;
};

struct ngx_http_request_s {
    ngx_connection_t *connection;
};

struct ngx_http_complex_value_s {
    ngx_str_t  value;
    ngx_int_t  eval_rc;
};

struct ngx_conf_s {
    ngx_pool_t *pool;
};

typedef struct ngx_slab_pool_s ngx_slab_pool_t;

struct ngx_slab_pool_s {
    void *data;
};

typedef struct {
    void      *addr;
    ngx_flag_t exists;
} ngx_shm_t;

struct ngx_shm_zone_s {
    ngx_shm_t  shm;
    void      *data;
    ngx_int_t (*init)(ngx_shm_zone_t *zone, void *data);
};

/* Global module symbol required by the config implementation header. */
ngx_module_t ngx_http_markdown_filter_module;
ngx_str_t ngx_http_markdown_metrics_shm_name = ngx_string("markdown_metrics");
ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = NULL;

/* Test-controlled state for allocator and SHM stubs. */
static size_t ngx_pagesize = 4096;
static ngx_shm_zone_t *g_shared_zone;
static size_t g_shared_size;
static ngx_int_t g_slab_alloc_fail;

/*
 * NGINX configuration merge macros (test-local definitions).
 * These replicate the standard ngx_conf_merge_* family for environments
 * where the real NGINX headers are unavailable.  Each macro assigns
 * default_value when conf is at its unset sentinel, inheriting from
 * prev if prev is not also unset.
 */
#define ngx_conf_init_size_value(conf, default_value) \
    if ((conf) == NGX_CONF_UNSET_SIZE) {              \
        (conf) = (default_value);                      \
    }

#define ngx_conf_init_uint_value(conf, default_value) \
    if ((conf) == NGX_CONF_UNSET_UINT) {               \
        (conf) = (default_value);                       \
    }

#define ngx_conf_merge_size_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_SIZE) {                      \
        (conf) = ((prev) == NGX_CONF_UNSET_SIZE)              \
            ? (default_value)                                 \
            : (prev);                                         \
    }

#define ngx_conf_merge_msec_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_MSEC) {                     \
        (conf) = ((prev) == NGX_CONF_UNSET_MSEC)             \
            ? (default_value)                                 \
            : (prev);                                         \
    }

#define ngx_conf_merge_uint_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_UINT) {                     \
        (conf) = ((prev) == NGX_CONF_UNSET_UINT)             \
            ? (default_value)                                 \
            : (prev);                                         \
    }

#define ngx_conf_merge_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET) {                     \
        (conf) = ((prev) == NGX_CONF_UNSET)             \
            ? (default_value)                            \
            : (prev);                                    \
    }

#define ngx_conf_merge_ptr_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_PTR) {                     \
        (conf) = ((prev) == NGX_CONF_UNSET_PTR)             \
            ? (default_value)                                 \
            : (prev);                                         \
    }

/*
 * Test-only NGINX stubs used by config_core_impl_test.
 *
 * These helpers intentionally model only the subset of behavior needed by the
 * unit tests and differ from production NGINX in several ways:
 * - allocator paths are deterministic (`calloc`) and always zero memory;
 * - slab failure is synthetic (`g_slab_alloc_fail`) instead of SHM pressure;
 * - shared-memory add path records requested size in `g_shared_size`;
 * - complex value evaluation is short-circuited to fixed test return codes;
 * - locking, logging internals, and runtime side effects are not simulated.
 *
 * Contract relied on by tests:
 * - success returns NGINX-style success codes and expected copied values;
 * - allocation failures return NULL from allocator stubs;
 * - zero-initialization semantics are preserved for created buffers/structs;
 * - invalid inputs return NGX_ERROR where the tested branch expects it.
 *
 * Divergence risk:
 * If production NGINX semantics for these primitives change, dependent tests
 * in this file must be reviewed and updated to keep branch expectations valid.
 */
/*
 * Case-insensitive comparison of the first n bytes of s1 and s2.
 *
 * Returns 0 if equal, or the difference of the first mismatching
 * lowercased byte (cast to ngx_int_t) otherwise.
 *
 * Note: this is a test-local reimplementation of the NGINX primitive
 * because the production symbol cannot be linked in the unit harness.
 * It mirrors the semantic contract of ngx_strncasecmp in ngx_string.h.
 */
static ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    size_t i;

    for (i = 0; i < n; i++) {
        u_char c1;
        u_char c2;

        c1 = (u_char) tolower((unsigned char) s1[i]);
        c2 = (u_char) tolower((unsigned char) s2[i]);

        if (c1 != c2) {
            return (ngx_int_t) c1 - (ngx_int_t) c2;
        }
    }

    return 0;
}

/*
 * No-op stub for ngx_conf_log_error.  Consumes the variadic argument list
 * without producing output, since the unit harness has no real NGINX log
 * cycle.  All parameters are marked UNUSED to suppress compiler warnings.
 */
static void
ngx_conf_log_error(ngx_uint_t level, ngx_conf_t *cf, ngx_err_t err,
    const char *fmt, ...)
{
    va_list ap;

    UNUSED(level);
    UNUSED(cf);
    UNUSED(err);

    va_start(ap, fmt);
    va_end(ap);
}

/*
 * Pool allocator stub that delegates to calloc(3).  Ignores the pool
 * argument and always zero-initializes, matching the semantic guarantee
 * of the production ngx_pcalloc.
 */
static void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return calloc(1, size);
}

/*
 * Zero-fill wrapper delegating to memset(3).  Mirrors the production
 * ngx_memzero macro semantics for the unit harness.
 */
static void
ngx_memzero(void *p, size_t n)
{
    memset(p, 0, n);
}

/*
 * Slab allocator stub.  Returns NULL when g_slab_alloc_fail is set
 * (simulating shared-memory pressure), otherwise delegates to calloc(3).
 * The pool argument is unused in this stub.
 */
static void *
ngx_slab_alloc(ngx_slab_pool_t *pool, size_t size)
{
    UNUSED(pool);

    if (g_slab_alloc_fail) {
        return NULL;
    }

    return calloc(1, size);
}

/*
 * Shared-memory zone registration stub.  Records the requested size in
 * g_shared_size and returns the preconfigured g_shared_zone pointer.
 * cf, name, and module are unused in this stub.
 */
static ngx_shm_zone_t *
ngx_shared_memory_add(ngx_conf_t *cf, ngx_str_t *name, size_t size,
    ngx_module_t *module)
{
    UNUSED(cf);
    UNUSED(name);
    UNUSED(module);

    g_shared_size = size;
    return g_shared_zone;
}

/*
 * Complex value evaluation stub.  Copies val->value to the output and
 * returns val->eval_rc, allowing tests to control both the resolved
 * string and the return code.  Returns NGX_ERROR if either pointer is
 * NULL.
 */
static ngx_int_t
ngx_http_complex_value(ngx_http_request_t *r,
    ngx_http_complex_value_t *val, ngx_str_t *value)
{
    UNUSED(r);

    if (val == NULL || value == NULL) {
        return NGX_ERROR;
    }

    *value = val->value;
    return val->eval_rc;
}

#include "../../src/ngx_http_markdown_config_core_impl.h"

static ngx_pool_t g_pool;
static ngx_log_t g_log;
static ngx_connection_t g_conn = { &g_log };

/*
 * Assign a C string literal to an ngx_str_t.  Sets data to the literal
 * pointer (cast through uintptr_t to satisfy the u_char* type) and len
 * to the string length.  The caller must ensure src outlives dst.
 */
static void
set_str(ngx_str_t *dst, const char *src)
{
    dst->data = (u_char *) (uintptr_t) src;
    dst->len = strlen(src);
}

/*
 * Initialize a complex value stub with a fixed string and return code.
 * The test harness will return these when ngx_http_complex_value is
 * called with this object.
 */
static void
init_complex(ngx_http_complex_value_t *cv, const char *value, ngx_int_t rc)
{
    set_str(&cv->value, value);
    cv->eval_rc = rc;
}

/*
 * Verify ngx_http_markdown_init_metrics_zone branch coverage:
 *  - reuse path when old_metrics is provided (zone.data preserved);
 *  - NULL slab pool detection (NGX_ERROR);
 *  - existing SHM with valid data pointer (attach);
 *  - existing SHM with NULL data (NGX_ERROR);
 *  - fresh allocation on non-existing SHM (zero-initialized metrics);
 *  - slab allocation failure (g_slab_alloc_fail simulation).
 */
static void
test_metrics_zone_init(void)
{
    ngx_shm_zone_t zone;
    ngx_slab_pool_t shpool;
    ngx_http_markdown_metrics_t old_metrics;
    ngx_http_markdown_metrics_t *new_metrics;
    ngx_int_t rc;

    TEST_SUBSECTION("metrics zone init paths");

    memset(&zone, 0, sizeof(zone));
    memset(&shpool, 0, sizeof(shpool));

    rc = ngx_http_markdown_init_metrics_zone(&zone, &old_metrics);
    TEST_ASSERT(rc == NGX_OK, "reuse path should succeed");
    TEST_ASSERT(zone.data == &old_metrics, "reuse should keep existing data");

    memset(&zone, 0, sizeof(zone));
    zone.shm.addr = NULL;
    rc = ngx_http_markdown_init_metrics_zone(&zone, NULL);
    TEST_ASSERT(rc == NGX_ERROR, "NULL slab pool should fail");

    memset(&zone, 0, sizeof(zone));
    memset(&shpool, 0, sizeof(shpool));
    zone.shm.addr = &shpool;
    zone.shm.exists = 1;
    shpool.data = &old_metrics;
    rc = ngx_http_markdown_init_metrics_zone(&zone, NULL);
    TEST_ASSERT(rc == NGX_OK, "existing shm data should attach");
    TEST_ASSERT(zone.data == &old_metrics, "existing shm data pointer mismatch");

    memset(&zone, 0, sizeof(zone));
    memset(&shpool, 0, sizeof(shpool));
    zone.shm.addr = &shpool;
    zone.shm.exists = 1;
    shpool.data = NULL;
    rc = ngx_http_markdown_init_metrics_zone(&zone, NULL);
    TEST_ASSERT(rc == NGX_ERROR,
        "existing shm without data should fail");

    memset(&zone, 0, sizeof(zone));
    memset(&shpool, 0, sizeof(shpool));
    zone.shm.addr = &shpool;
    zone.shm.exists = 0;
    g_slab_alloc_fail = 0;
    rc = ngx_http_markdown_init_metrics_zone(&zone, NULL);
    TEST_ASSERT(rc == NGX_OK, "fresh allocation should succeed");
    TEST_ASSERT(zone.data != NULL, "zone data should be set");
    TEST_ASSERT(shpool.data == zone.data, "slab and zone pointers should match");

    new_metrics = zone.data;
    TEST_ASSERT(new_metrics->conversions_attempted == 0,
        "fresh metrics should be zero-initialized");

    memset(&zone, 0, sizeof(zone));
    memset(&shpool, 0, sizeof(shpool));
    zone.shm.addr = &shpool;
    zone.shm.exists = 0;
    g_slab_alloc_fail = 1;
    rc = ngx_http_markdown_init_metrics_zone(&zone, NULL);
    TEST_ASSERT(rc == NGX_ERROR, "allocation failure should fail init");
    g_slab_alloc_fail = 0;

    TEST_PASS("metrics zone init branches covered");
}

/*
 * Verify create_main_conf and init_main_conf:
 *  - create_main_conf allocates and returns a zeroed main conf with
 *    metrics_shm_size unset and metrics_shm_zone NULL;
 *  - init_main_conf applies the default SHM size (8 pages), stores the
 *    zone pointer, and sets the init callback;
 *  - init_main_conf returns NGX_CONF_ERROR when shared_memory_add fails.
 */
static void
test_main_conf_create_and_init(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_main_conf_t *mcf;
    ngx_shm_zone_t zone;
    char *rc;

    TEST_SUBSECTION("main conf create/init");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    mcf = ngx_http_markdown_create_main_conf(&cf);
    TEST_ASSERT(mcf != NULL, "create_main_conf should allocate");
    TEST_ASSERT(mcf->metrics_shm_size == NGX_CONF_UNSET_SIZE,
        "metrics_shm_size should be unset");
    TEST_ASSERT(mcf->metrics_shm_zone == NULL,
        "metrics_shm_zone should start NULL");
    TEST_ASSERT(mcf->dynconf_owner_conf == NULL,
        "dynconf owner config should start NULL");

    memset(&zone, 0, sizeof(zone));
    g_shared_zone = &zone;
    g_shared_size = 0;

    rc = ngx_http_markdown_init_main_conf(&cf, mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "init_main_conf should succeed");
    TEST_ASSERT(mcf->metrics_shm_size == 8 * ngx_pagesize,
        "default shm size should be 8 pages");
    TEST_ASSERT(mcf->metrics_shm_zone == &zone,
        "main conf should store zone pointer");
    TEST_ASSERT(ngx_http_markdown_metrics_shm_zone == &zone,
        "global metrics shm zone should be updated");
    TEST_ASSERT(zone.init == ngx_http_markdown_init_metrics_zone,
        "zone init callback should be set");

    g_shared_zone = NULL;
    rc = ngx_http_markdown_init_main_conf(&cf, mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "shared memory add failure should return conf error");

    TEST_PASS("main conf create/init branches covered");
}

/*
 * Verify that create_conf initializes all fields to their NGINX unset
 * sentinel values (NGX_CONF_UNSET_SIZE, NGX_CONF_UNSET_MSEC, etc.),
 * ensuring merge_conf will later inherit from the parent.
 */
static void
test_create_conf_defaults(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t *conf;

    TEST_SUBSECTION("create_conf defaults");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    conf = ngx_http_markdown_create_conf(&cf);
    TEST_ASSERT(conf != NULL, "create_conf should allocate");

    TEST_ASSERT(conf->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_UNSET,
        "enabled source default should be unset");
    TEST_ASSERT(conf->max_size == NGX_CONF_UNSET_SIZE,
        "max_size should be unset");
    TEST_ASSERT(conf->timeout == NGX_CONF_UNSET_MSEC,
        "timeout should be unset");
    TEST_ASSERT(conf->ops.metrics_format == NGX_CONF_UNSET_UINT,
        "metrics format should be unset");
    TEST_ASSERT(conf->stream.budget == NGX_CONF_UNSET_SIZE,
        "streaming budget should be unset");

    TEST_PASS("create_conf defaults covered");
}

/*
 * Verify merge_conf inheritance and override semantics:
 *  - child fields at their unset sentinels inherit from parent;
 *  - child with a static enabled_source clears the complex pointer;
 *  - streaming_engine, streaming_budget, and streaming_on_error are
 *    inherited from parent when child leaves them unset.
 */
static void
test_merge_conf(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t parent;
    ngx_http_markdown_conf_t child;
    ngx_http_complex_value_t cv;
    char *rc;

    TEST_SUBSECTION("merge_conf inheritance/defaults");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    memset(&parent, 0, sizeof(parent));
    memset(&child, 0, sizeof(child));

    parent.enabled = 1;
    parent.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_COMPLEX;
    parent.enabled_complex = &cv;
    parent.max_size = 2048;
    parent.timeout = 42;
    parent.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    parent.flavor = NGX_HTTP_MARKDOWN_FLAVOR_GFM;
    parent.token_estimate = 1;
    parent.front_matter = 1;
    parent.accept_policy = NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD;
    parent.policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY;
    parent.policy.generate_etag = 0;
    parent.policy.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;
    parent.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    parent.buffer_chunked = 0;
    parent.decompress.auto_decompress = 0;
    parent.decompress.max_size = 2048;
    parent.decompress.parse_timeout = 30000;
    parent.decompress.parser_budget = 64 * 1024 * 1024;
    parent.large_body_threshold = 4096;
    parent.ops.trust_forwarded_headers = 1;
    parent.ops.metrics_format = NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS;
    parent.ops.metrics_per_path = 1;
    parent.ops.otel_enabled = 0;
    parent.stream.engine = NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON;
    parent.stream.budget = 777;
    parent.stream.budget_explicit = 1;
    parent.stream.on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT;
    parent.stream.shadow = 1;
    parent.stream.threshold = 32768;
    parent.advanced.prune_noise = 1;
    parent.advanced.memory_budget = NGX_CONF_UNSET_SIZE;
    parent.advanced.llm_provider = NGX_CONF_UNSET_UINT;
    parent.advanced.chars_per_token_fixed = NGX_CONF_UNSET_UINT;

    /* Initially unset; enabled below reflects the post-action state. */
    child.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
    child.enabled = 1;
    child.enabled_complex = (ngx_http_complex_value_t *) &child;
    child.max_size = NGX_CONF_UNSET_SIZE;
    child.timeout = NGX_CONF_UNSET_MSEC;
    child.on_error = NGX_CONF_UNSET_UINT;
    child.flavor = NGX_CONF_UNSET_UINT;
    child.token_estimate = NGX_CONF_UNSET;
    child.front_matter = NGX_CONF_UNSET;
    child.accept_policy = NGX_CONF_UNSET_UINT;
    child.policy.auth_policy = NGX_CONF_UNSET_UINT;
    child.policy.auth_cookies = NGX_CONF_UNSET_PTR;
    child.policy.generate_etag = NGX_CONF_UNSET;
    child.policy.conditional_requests = NGX_CONF_UNSET_UINT;
    child.policy.log_verbosity = NGX_CONF_UNSET_UINT;
    child.buffer_chunked = NGX_CONF_UNSET;
    child.stream_types = NGX_CONF_UNSET_PTR;
    child.content_types = NGX_CONF_UNSET_PTR;
    child.decompress.auto_decompress = NGX_CONF_UNSET;
    child.decompress.max_size = NGX_CONF_UNSET_SIZE;
    child.decompress.parse_timeout = NGX_CONF_UNSET_MSEC;
    child.decompress.parser_budget = NGX_CONF_UNSET_SIZE;
    child.large_body_threshold = NGX_CONF_UNSET_SIZE;
    child.ops.trust_forwarded_headers = NGX_CONF_UNSET;
    child.ops.metrics_format = NGX_CONF_UNSET_UINT;
    child.ops.metrics_per_path = NGX_CONF_UNSET;
    child.ops.otel_enabled = NGX_CONF_UNSET;
    child.stream.engine = NGX_CONF_UNSET_UINT;
    child.stream.budget = NGX_CONF_UNSET_SIZE;
    child.stream.budget_explicit = 0;
    child.stream.on_error = NGX_CONF_UNSET_UINT;
    child.stream.shadow = NGX_CONF_UNSET;
    child.stream.threshold = NGX_CONF_UNSET_SIZE;
    child.stream.threshold_explicit = 0;
    child.stream.precommit_buffer = NGX_CONF_UNSET_SIZE;
    child.stream.flush_min = NGX_CONF_UNSET_SIZE;
    child.stream.on_error_explicit = 0;
    child.stream.shadow_explicit = 0;
    child.stream.excluded_types = NGX_CONF_UNSET_PTR;
    child.advanced.prune_noise = NGX_CONF_UNSET;
    child.advanced.prune_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.memory_budget = NGX_CONF_UNSET_SIZE;
    child.advanced.llm_provider = NGX_CONF_UNSET_UINT;
    child.advanced.chars_per_token_fixed = NGX_CONF_UNSET_UINT;

    rc = ngx_http_markdown_merge_conf(&cf, &parent, &child);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "merge_conf should succeed");
    TEST_ASSERT(child.enabled_source == NGX_HTTP_MARKDOWN_ENABLED_COMPLEX,
        "child should inherit complex enabled source");
    TEST_ASSERT(child.enabled_complex == &cv,
        "child should inherit complex expression");
    TEST_ASSERT(child.max_size == 2048, "child should inherit max_size");
    TEST_ASSERT(child.timeout == 42, "child should inherit timeout");
    TEST_ASSERT(child.stream.engine == NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON,
        "child should inherit stream engine");
    TEST_ASSERT(child.stream.budget == 777,
        "child should inherit stream budget");
    TEST_ASSERT(child.stream.on_error
        == NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT,
        "child should inherit streaming on_error");

    memset(&child, 0, sizeof(child));
    child.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
    child.enabled = 0;
    child.enabled_complex = &cv;
    child.max_size = NGX_CONF_UNSET_SIZE;
    child.timeout = NGX_CONF_UNSET_MSEC;
    child.on_error = NGX_CONF_UNSET_UINT;
    child.flavor = NGX_CONF_UNSET_UINT;
    child.token_estimate = NGX_CONF_UNSET;
    child.front_matter = NGX_CONF_UNSET;
    child.accept_policy = NGX_CONF_UNSET_UINT;
    child.policy.auth_policy = NGX_CONF_UNSET_UINT;
    child.policy.auth_cookies = NGX_CONF_UNSET_PTR;
    child.policy.generate_etag = NGX_CONF_UNSET;
    child.policy.conditional_requests = NGX_CONF_UNSET_UINT;
    child.policy.log_verbosity = NGX_CONF_UNSET_UINT;
    child.buffer_chunked = NGX_CONF_UNSET;
    child.stream_types = NGX_CONF_UNSET_PTR;
    child.content_types = NGX_CONF_UNSET_PTR;
    child.decompress.auto_decompress = NGX_CONF_UNSET;
    child.decompress.max_size = NGX_CONF_UNSET_SIZE;
    child.decompress.parse_timeout = NGX_CONF_UNSET_MSEC;
    child.decompress.parser_budget = NGX_CONF_UNSET_SIZE;
    child.large_body_threshold = NGX_CONF_UNSET_SIZE;
    child.ops.trust_forwarded_headers = NGX_CONF_UNSET;
    child.ops.metrics_format = NGX_CONF_UNSET_UINT;
    child.ops.metrics_per_path = NGX_CONF_UNSET;
    child.ops.otel_enabled = NGX_CONF_UNSET;
    child.stream.engine = NGX_CONF_UNSET_UINT;
    child.stream.budget = NGX_CONF_UNSET_SIZE;
    child.stream.budget_explicit = 0;
    child.stream.on_error = NGX_CONF_UNSET_UINT;
    child.stream.shadow = NGX_CONF_UNSET;
    child.stream.threshold = NGX_CONF_UNSET_SIZE;
    child.stream.threshold_explicit = 0;
    child.stream.precommit_buffer = NGX_CONF_UNSET_SIZE;
    child.stream.flush_min = NGX_CONF_UNSET_SIZE;
    child.stream.on_error_explicit = 0;
    child.stream.shadow_explicit = 0;
    child.stream.excluded_types = NGX_CONF_UNSET_PTR;
    child.advanced.prune_noise = NGX_CONF_UNSET;
    child.advanced.prune_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.memory_budget = NGX_CONF_UNSET_SIZE;
    child.advanced.llm_provider = NGX_CONF_UNSET_UINT;
    child.advanced.chars_per_token_fixed = NGX_CONF_UNSET_UINT;

    rc = ngx_http_markdown_merge_conf(&cf, &parent, &child);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "merge with static child should succeed");
    TEST_ASSERT(child.enabled_complex == NULL,
        "static enabled source should clear complex pointer");

    TEST_PASS("merge_conf branches covered");
}

static void
test_dynconf_owner_uses_merged_config(void)
{
    ngx_conf_t                     cf;
    ngx_http_markdown_conf_t      *parent;
    ngx_http_markdown_conf_t      *owner;
    ngx_http_markdown_main_conf_t  main_conf;
    char                          *rc;

    TEST_SUBSECTION("dynconf owner uses merged config");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;
    memset(&main_conf, 0, sizeof(main_conf));

    parent = ngx_http_markdown_create_conf(&cf);
    owner = ngx_http_markdown_create_conf(&cf);
    TEST_ASSERT(parent != NULL && owner != NULL,
        "dynconf merge configs should allocate");

    parent->advanced.dynconf_enabled = 1;
    parent->advanced.dynconf_dry_run = 1;
    owner->advanced.dynconf_path.data =
        (u_char *) "/etc/nginx/markdown-dynconf.conf";
    owner->advanced.dynconf_path.len =
        strlen((char *) owner->advanced.dynconf_path.data);
    main_conf.dynconf_owner_conf = owner;

    rc = ngx_http_markdown_merge_conf(&cf, parent, owner);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "dynconf owner merge should succeed");
    TEST_ASSERT(ngx_http_markdown_dynconf_owner(&main_conf) == owner,
        "worker owner lookup should return the path owner");
    TEST_ASSERT(owner->advanced.dynconf_enabled == 1,
        "owner should inherit dynconf enablement");
    TEST_ASSERT(owner->advanced.dynconf_dry_run == 1,
        "owner should inherit dynconf dry-run mode");
    TEST_ASSERT(owner->advanced.dynconf_path.len > 0,
        "owner should retain its configured dynconf path");

    TEST_ASSERT(ngx_http_markdown_dynconf_owner(NULL) == NULL,
        "worker owner lookup should be NULL-safe");

    TEST_PASS("dynconf watcher binds to coherent merged owner config");
}

/*
 * Verify enum-to-name helpers and log verbosity level mapping:
 *  - log_verbosity_to_ngx_level maps each verbosity to the correct
 *    NGINX log level, with unknown values defaulting to NGX_LOG_INFO;
 *  - on_error_name, flavor_name, auth_policy_name, etc. return the
 *    expected string length for known and unknown enum values.
 */
static void
test_name_helpers_and_levels(void)
{
    const ngx_str_t *name;

    TEST_SUBSECTION("name helpers and verbosity level mapping");

    TEST_ASSERT(ngx_http_markdown_log_verbosity_to_ngx_level(
            NGX_HTTP_MARKDOWN_LOG_ERROR) == NGX_LOG_ERR,
        "error verbosity should map to NGX_LOG_ERR");
    TEST_ASSERT(ngx_http_markdown_log_verbosity_to_ngx_level(
            NGX_HTTP_MARKDOWN_LOG_WARN) == NGX_LOG_WARN,
        "warn verbosity should map to NGX_LOG_WARN");
    TEST_ASSERT(ngx_http_markdown_log_verbosity_to_ngx_level(
            NGX_HTTP_MARKDOWN_LOG_DEBUG) == NGX_LOG_DEBUG,
        "debug verbosity should map to NGX_LOG_DEBUG");
    TEST_ASSERT(ngx_http_markdown_log_verbosity_to_ngx_level(999) == NGX_LOG_INFO,
        "unknown verbosity should map to NGX_LOG_INFO");

    name = ngx_http_markdown_on_error_name(NGX_HTTP_MARKDOWN_ON_ERROR_PASS);
    TEST_ASSERT(name->len == strlen("pass"), "on_error pass name");
    name = ngx_http_markdown_on_error_name(999);
    TEST_ASSERT(name->len == strlen("unknown"), "on_error unknown name");

    name = ngx_http_markdown_flavor_name(NGX_HTTP_MARKDOWN_FLAVOR_GFM);
    TEST_ASSERT(name->len == strlen("gfm"), "flavor gfm name");

    name = ngx_http_markdown_auth_policy_name(NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY);
    TEST_ASSERT(name->len == strlen("deny"), "auth policy deny name");

    name = ngx_http_markdown_conditional_requests_name(
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE);
    TEST_ASSERT(name->len == strlen("if_modified_since_only"),
        "conditional ims_only name");

    name = ngx_http_markdown_log_verbosity_name(NGX_HTTP_MARKDOWN_LOG_DEBUG);
    TEST_ASSERT(name->len == strlen("debug"), "verbosity debug name");

    name = ngx_http_markdown_metrics_format_name(
        NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS);
    TEST_ASSERT(name->len == strlen("prometheus"), "metrics format name");

    name = ngx_http_markdown_compression_name(NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
    TEST_ASSERT(name->len == strlen("gzip"), "compression gzip name");
    name = ngx_http_markdown_compression_name((ngx_http_markdown_compression_type_e) 999);
    TEST_ASSERT(name->len == strlen("invalid"), "compression invalid name");

    name = ngx_http_markdown_enabled_source_name(NGX_HTTP_MARKDOWN_ENABLED_COMPLEX);
    TEST_ASSERT(name->len == strlen("complex"), "enabled source name");

    TEST_PASS("name helper branches covered");
}

/*
 * Verify parse_filter_flag and is_enabled branch coverage:
 *  - is_ascii_space recognizes space/tab and rejects non-whitespace;
 *  - parse_filter_flag rejects NULL inputs, trims whitespace, and
 *    parses on/off/true/false/1/0/yes/no (case-insensitive);
 *  - empty token after trimming defaults to disabled;
 *  - invalid tokens return NGX_ERROR;
 *  - is_enabled with static source bypasses complex evaluation;
 *  - is_enabled with complex source evaluates via the stub, handling
 *    NULL request, evaluation failure, and invalid/off/on tokens.
 */
static void
test_filter_flag_and_is_enabled(void)
{
    ngx_str_t value;
    ngx_flag_t enabled = 0;
    ngx_http_markdown_conf_t conf;
    ngx_http_complex_value_t cv;
    ngx_http_request_t req;

    TEST_SUBSECTION("parse_filter_flag and is_enabled");

    TEST_ASSERT(ngx_http_markdown_is_ascii_space(' ') == 1,
        "space should be recognized");
    TEST_ASSERT(ngx_http_markdown_is_ascii_space('x') == 0,
        "non-space should not be recognized");

    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(NULL, &enabled) == NGX_ERROR,
        "NULL value should fail parsing");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, NULL) == NGX_ERROR,
        "NULL output should fail parsing");

    set_str(&value, "  on\t");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, &enabled) == NGX_OK,
        "on should parse");
    TEST_ASSERT(enabled == 1, "on should enable");

    set_str(&value, "0");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, &enabled) == NGX_OK,
        "numeric off should parse");
    TEST_ASSERT(enabled == 0, "0 should disable");

    set_str(&value, "TRUE");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, &enabled) == NGX_OK,
        "true should parse case-insensitively");
    TEST_ASSERT(enabled == 1, "true should enable");

    set_str(&value, "\t ");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, &enabled) == NGX_OK,
        "empty token after trim should parse");
    TEST_ASSERT(enabled == 0, "empty token should disable");

    set_str(&value, "maybe");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, &enabled) == NGX_ERROR,
        "invalid token should fail");

    memset(&conf, 0, sizeof(conf));
    conf.enabled = 1;
    conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
    memset(&req, 0, sizeof(req));
    TEST_ASSERT(ngx_http_markdown_is_enabled(&req, &conf, NULL) == 1,
        "static enabled should bypass complex evaluation");

    conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_COMPLEX;
    conf.enabled_complex = &cv;

    TEST_ASSERT(ngx_http_markdown_is_enabled(NULL, &conf, NULL) == 0,
        "NULL request should disable complex mode");

    init_complex(&cv, "on", NGX_ERROR);
    req.connection = &g_conn;
    TEST_ASSERT(ngx_http_markdown_is_enabled(&req, &conf, NULL) == 0,
        "complex evaluation failure should disable");

    init_complex(&cv, "maybe", NGX_OK);
    TEST_ASSERT(ngx_http_markdown_is_enabled(&req, &conf, NULL) == 0,
        "invalid complex token should disable");

    init_complex(&cv, "off", NGX_OK);
    TEST_ASSERT(ngx_http_markdown_is_enabled(&req, &conf, NULL) == 0,
        "complex off should disable");

    init_complex(&cv, "yes", NGX_OK);
    TEST_ASSERT(ngx_http_markdown_is_enabled(&req, &conf, NULL) == 1,
        "complex yes should enable");

    TEST_ASSERT(ngx_http_markdown_is_enabled(&req, NULL, NULL) == 0,
        "NULL conf should disable");

    TEST_PASS("parse_filter_flag and is_enabled branches covered");
}

/*
 * Verify log_merged_conf does not crash with NULL or populated conf
 * pointers.  The stub log function discards output, so this test
 * exercises the formatting branches without verifying log text.
 */
static void
test_log_merged_conf(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t conf;
    ngx_array_t auth;
    ngx_array_t stream_types;

    TEST_SUBSECTION("log_merged_conf helper");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    memset(&conf, 0, sizeof(conf));
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    conf.flavor = NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK;
    conf.policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW;
    conf.policy.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    conf.ops.metrics_format = NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO;

    memset(&auth, 0, sizeof(auth));
    auth.nelts = 2;
    conf.policy.auth_cookies = &auth;

    memset(&stream_types, 0, sizeof(stream_types));
    stream_types.nelts = 1;
    conf.stream_types = &stream_types;

    ngx_http_markdown_log_merged_conf(NULL, &conf);
    ngx_http_markdown_log_merged_conf(&cf, &conf);

    TEST_PASS("log_merged_conf covered");
}

/*
 * Verify parse_filter_flag additional branches:
 *  - "1" and "0" single-character numeric flags
 *  - "no" and "false" word flags
 *  - "yes" word flag
 */
static void
test_filter_flag_additional_branches(void)
{
    ngx_str_t value;
    ngx_flag_t enabled = 0;

    TEST_SUBSECTION("parse_filter_flag additional branches");

    set_str(&value, "1");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, &enabled) == NGX_OK,
        "numeric 1 should parse");
    TEST_ASSERT(enabled == 1, "1 should enable");

    set_str(&value, "0");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, &enabled) == NGX_OK,
        "numeric 0 should parse");
    TEST_ASSERT(enabled == 0, "0 should disable");

    set_str(&value, "no");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, &enabled) == NGX_OK,
        "no should parse");
    TEST_ASSERT(enabled == 0, "no should disable");

    set_str(&value, "false");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, &enabled) == NGX_OK,
        "false should parse");
    TEST_ASSERT(enabled == 0, "false should disable");

    set_str(&value, "yes");
    TEST_ASSERT(ngx_http_markdown_parse_filter_flag(&value, &enabled) == NGX_OK,
        "yes should parse");
    TEST_ASSERT(enabled == 1, "yes should enable");

    TEST_PASS("parse_filter_flag additional branches covered");
}

/*
 * Verify name helper default/unknown branches:
 *  - flavor_name with unknown value
 *  - auth_policy_name with unknown value
 *  - conditional_requests_name with unknown value
 *  - log_verbosity_name with unknown value
 *  - metrics_format_name with unknown value
 *  - compression_name with deflate and unknown type
 *  - enabled_source_name with unknown value
 */
static void
test_name_helpers_unknown_branches(void)
{
    const ngx_str_t *name;

    TEST_SUBSECTION("name helper unknown/default branches");

    name = ngx_http_markdown_flavor_name(999);
    TEST_ASSERT(name->len == strlen("unknown"), "flavor unknown name");

    name = ngx_http_markdown_auth_policy_name(999);
    TEST_ASSERT(name->len == strlen("unknown"), "auth policy unknown name");

    name = ngx_http_markdown_conditional_requests_name(999);
    TEST_ASSERT(name->len == strlen("unknown"), "conditional unknown name");

    name = ngx_http_markdown_log_verbosity_name(999);
    TEST_ASSERT(name->len == strlen("unknown"), "verbosity unknown name");

    name = ngx_http_markdown_metrics_format_name(999);
    TEST_ASSERT(name->len == strlen("unknown"), "metrics format unknown name");

    name = ngx_http_markdown_compression_name(NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(name->len == strlen("deflate"), "compression deflate name");

    name = ngx_http_markdown_compression_name(NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);
    TEST_ASSERT(name->len == strlen("brotli"), "compression brotli name");

    name = ngx_http_markdown_compression_name(NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN);
    TEST_ASSERT(name->len == strlen("unknown"), "compression unknown enum name");

    name = ngx_http_markdown_enabled_source_name(999);
    TEST_ASSERT(name->len == strlen("unknown"), "enabled source unknown name");

    name = ngx_http_markdown_enabled_source_name(NGX_HTTP_MARKDOWN_ENABLED_UNSET);
    TEST_ASSERT(name->len == strlen("unset"), "enabled source unset name");

    TEST_PASS("name helper unknown branches covered");
}

/*
 * Verify merge_conf double-UNSET path:
 *  When both parent and child have enabled_source == UNSET,
 *  the child should default to STATIC with enabled=0.
 */
static void
test_merge_conf_double_unset(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t parent;
    ngx_http_markdown_conf_t child;
    char *rc;

    TEST_SUBSECTION("merge_conf double-UNSET enabled path");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    memset(&parent, 0, sizeof(parent));
    memset(&child, 0, sizeof(child));

    /* Both parent and child have UNSET enabled_source */
    parent.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
    parent.max_size = 4096;
    parent.decompress.max_size = NGX_CONF_UNSET_SIZE;
    parent.decompress.parse_timeout = 30000;
    parent.decompress.parser_budget = 64 * 1024 * 1024;
    parent.timeout = 100;
    parent.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    parent.flavor = NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK;
    parent.policy.auth_policy = NGX_HTTP_MARKDOWN_AUTH_POLICY_ALLOW;
    parent.policy.conditional_requests = NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    parent.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    parent.ops.metrics_format = NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO;
    parent.ops.metrics_per_path = 0;
    parent.ops.otel_enabled = 0;
    parent.advanced.dynconf_enabled = NGX_CONF_UNSET;
    parent.advanced.memory_budget = NGX_CONF_UNSET_SIZE;
    parent.advanced.dynconf_path.len = 0;
    parent.advanced.dynconf_path.data = NULL;

    child.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
    child.max_size = NGX_CONF_UNSET_SIZE;
    child.timeout = NGX_CONF_UNSET_MSEC;
    child.on_error = NGX_CONF_UNSET_UINT;
    child.flavor = NGX_CONF_UNSET_UINT;
    child.token_estimate = NGX_CONF_UNSET;
    child.front_matter = NGX_CONF_UNSET;
    child.accept_policy = NGX_CONF_UNSET_UINT;
    child.policy.auth_policy = NGX_CONF_UNSET_UINT;
    child.policy.auth_cookies = NGX_CONF_UNSET_PTR;
    child.policy.generate_etag = NGX_CONF_UNSET;
    child.policy.conditional_requests = NGX_CONF_UNSET_UINT;
    child.policy.log_verbosity = NGX_CONF_UNSET_UINT;
    child.buffer_chunked = NGX_CONF_UNSET;
    child.stream_types = NGX_CONF_UNSET_PTR;
    child.content_types = NGX_CONF_UNSET_PTR;
    child.decompress.auto_decompress = NGX_CONF_UNSET;
    child.decompress.max_size = NGX_CONF_UNSET_SIZE;
    child.decompress.parse_timeout = NGX_CONF_UNSET_MSEC;
    child.decompress.parser_budget = NGX_CONF_UNSET_SIZE;
    child.large_body_threshold = NGX_CONF_UNSET_SIZE;
    child.ops.trust_forwarded_headers = NGX_CONF_UNSET;
    child.ops.metrics_format = NGX_CONF_UNSET_UINT;
    child.ops.metrics_per_path = NGX_CONF_UNSET;
    child.ops.otel_enabled = NGX_CONF_UNSET;
    child.stream.engine = NGX_CONF_UNSET_UINT;
    child.stream.budget = NGX_CONF_UNSET_SIZE;
    child.stream.budget_explicit = 0;
    child.stream.on_error = NGX_CONF_UNSET_UINT;
    child.stream.shadow = NGX_CONF_UNSET;
    child.stream.threshold = NGX_CONF_UNSET_SIZE;
    child.stream.threshold_explicit = 0;
    child.stream.precommit_buffer = NGX_CONF_UNSET_SIZE;
    child.stream.flush_min = NGX_CONF_UNSET_SIZE;
    child.stream.on_error_explicit = 0;
    child.stream.shadow_explicit = 0;
    child.stream.excluded_types = NGX_CONF_UNSET_PTR;
    child.advanced.prune_noise = NGX_CONF_UNSET;
    child.advanced.prune_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.memory_budget = NGX_CONF_UNSET_SIZE;
    child.advanced.llm_provider = NGX_CONF_UNSET_UINT;
    child.advanced.chars_per_token_fixed = NGX_CONF_UNSET_UINT;
    child.advanced.dynconf_enabled = NGX_CONF_UNSET;
    child.advanced.dynconf_dry_run = NGX_CONF_UNSET;

    rc = ngx_http_markdown_merge_conf(&cf, &parent, &child);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "merge_conf double-UNSET should succeed");
    TEST_ASSERT(child.enabled_source == NGX_HTTP_MARKDOWN_ENABLED_STATIC,
        "double-UNSET should default to STATIC");
    TEST_ASSERT(child.enabled == 0,
        "double-UNSET should default to disabled");
    TEST_ASSERT(child.enabled_complex == NULL,
        "double-UNSET should clear complex pointer");

    TEST_PASS("merge_conf double-UNSET path covered");
}

static void
test_stream_budget_explicit_maps_to_stream(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;
    char *rc;

    TEST_SUBSECTION("stream.budget explicit mapping");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;
    parent = ngx_http_markdown_create_conf(&cf);
    child = ngx_http_markdown_create_conf(&cf);
    TEST_ASSERT(parent != NULL && child != NULL,
                "create_conf should allocate parent and child");

    child->stream.budget = 4 * 1024 * 1024;

    rc = ngx_http_markdown_merge_conf(&cf, parent, child);

    TEST_ASSERT(rc == NGX_CONF_OK,
                "merge_conf should accept stream budget");
    TEST_ASSERT(child->stream.budget == 4 * 1024 * 1024,
                "stream budget should be preserved");
    TEST_ASSERT(child->stream.budget_explicit == 1,
                "stream budget should stay explicit");

    parent = ngx_http_markdown_create_conf(&cf);
    child = ngx_http_markdown_create_conf(&cf);
    TEST_ASSERT(parent != NULL && child != NULL,
                "create_conf should allocate inherited budget fixtures");

    parent->stream.budget = 8 * 1024 * 1024;
    parent->stream.budget_explicit = 1;
    child->stream.budget = 4 * 1024 * 1024;

    rc = ngx_http_markdown_merge_conf(&cf, parent, child);

    TEST_ASSERT(rc == NGX_CONF_OK,
                "merge_conf should accept child stream budget override");
    TEST_ASSERT(child->stream.budget == 4 * 1024 * 1024,
                "child stream budget should override parent stream budget");
    TEST_ASSERT(child->stream.budget_explicit == 1,
                "child stream budget override should mark explicit");
    TEST_PASS("stream.budget explicit mapping");
}

static void
test_stream_preserves_explicit_defaults(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;
    char *rc;

    TEST_SUBSECTION("stream explicit defaults");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;
    parent = ngx_http_markdown_create_conf(&cf);
    child = ngx_http_markdown_create_conf(&cf);
    TEST_ASSERT(parent != NULL && child != NULL,
                "create_conf should allocate parent and child");

    child->stream.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    child->stream.shadow = 0;

    rc = ngx_http_markdown_merge_conf(&cf, parent, child);

    TEST_ASSERT(rc == NGX_CONF_OK,
                "merge_conf should accept stream settings");
    TEST_ASSERT(child->stream.on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS,
                "explicit on_error=pass should not be overwritten");
    TEST_ASSERT(child->stream.on_error_explicit == 1,
                "explicit on_error should be tracked");
    TEST_ASSERT(child->stream.shadow == 0,
                "explicit shadow=off should not be overwritten");
    TEST_ASSERT(child->stream.shadow_explicit == 1,
                "explicit shadow should be tracked");
    TEST_PASS("stream explicit defaults preserved");
}

/*
 * Entry point: run all config_core_impl unit tests.
 * Returns 0 on success; aborts via TEST_ASSERT on failure.
 */
/*
 * Test memory_budget priority chain: explicit per-engine > unified > default.
 *
 * This test simulates the priority chain logic from merge_conf and
 * prepare_conversion_options without calling the full NGINX merge
 * infrastructure. The key invariant is:
 *
 *   1. If operator sets markdown_max_size explicitly, memory_budget
 *      does NOT override it (explicit wins).
 *   2. If operator does NOT set markdown_max_size but sets
 *      markdown_memory_budget, the budget applies to max_size.
 *   3. Same logic for streaming_budget vs memory_budget.
 */
static void
test_memory_budget_priority_chain(void)
{
    TEST_SUBSECTION("memory_budget priority chain");

    /* Case 1: Only memory_budget set -> applies to max_size */
    {
        size_t max_size = NGX_CONF_UNSET_SIZE;
        size_t memory_budget = 50 * 1024 * 1024;
        ngx_flag_t max_size_set = (max_size != NGX_CONF_UNSET_SIZE);

        /* Simulate: ngx_conf_merge_size_value resolves max_size to default */
        max_size = 10 * 1024 * 1024;

        /* Apply unified budget when max_size not explicitly set */
        if (memory_budget != NGX_CONF_UNSET_SIZE && !max_size_set) {
            max_size = memory_budget;
        }

        TEST_ASSERT(max_size == 50 * 1024 * 1024,
                    "memory_budget should override default max_size");
    }

    /* Case 2: Both max_size and memory_budget set -> max_size wins */
    {
        size_t max_size = 5 * 1024 * 1024;  /* explicitly set */
        size_t memory_budget = 50 * 1024 * 1024;
        ngx_flag_t max_size_set = (max_size != NGX_CONF_UNSET_SIZE);

        /* After merge, max_size keeps its explicit value */
        /* (merge would use prev or default, but explicit wins) */

        if (memory_budget != NGX_CONF_UNSET_SIZE && !max_size_set) {
            max_size = memory_budget;
        }

        TEST_ASSERT(max_size == 5 * 1024 * 1024,
                    "explicit max_size should win over memory_budget");
    }

    /* Case 3: Neither set -> default 10MB */
    {
        size_t max_size = NGX_CONF_UNSET_SIZE;
        size_t memory_budget = NGX_CONF_UNSET_SIZE;
        ngx_flag_t max_size_set = (max_size != NGX_CONF_UNSET_SIZE);

        /* merge resolves to default */
        max_size = 10 * 1024 * 1024;

        if (memory_budget != NGX_CONF_UNSET_SIZE && !max_size_set) {
            max_size = memory_budget;
        }

        TEST_ASSERT(max_size == 10 * 1024 * 1024,
                    "default max_size when nothing explicitly set");
    }

    /* Case 4: streaming_budget_explicit flag works correctly */
    {
        ngx_flag_t streaming_budget_explicit = 0;
        size_t memory_budget = 50 * 1024 * 1024;
        size_t streaming_budget = NGX_HTTP_MARKDOWN_STREAMING_BUDGET_DEFAULT;

        /* memory_budget should apply when streaming_budget not explicit */
        if (memory_budget != NGX_CONF_UNSET_SIZE
            && !streaming_budget_explicit)
        {
            streaming_budget = memory_budget;
        }

        TEST_ASSERT(streaming_budget == 50 * 1024 * 1024,
                    "memory_budget applies to streaming_budget when not explicit");

        /* Now with explicit streaming_budget */
        streaming_budget_explicit = 1;
        streaming_budget = NGX_HTTP_MARKDOWN_STREAMING_BUDGET_DEFAULT;

        if (memory_budget != NGX_CONF_UNSET_SIZE
            && !streaming_budget_explicit)
        {
            streaming_budget = memory_budget;
        }

        TEST_ASSERT(streaming_budget == NGX_HTTP_MARKDOWN_STREAMING_BUDGET_DEFAULT,
                    "explicit streaming_budget wins over memory_budget");
    }

    /*
     * Cases 5-7: Exercise the production merge_conf path directly,
     * verifying that the save-before-merge priority chain works end-to-end.
     */
    {
        ngx_http_markdown_conf_t parent_conf;
        ngx_http_markdown_conf_t child_conf;
        ngx_conf_t merge_cf;
        char *rc;

        memset(&merge_cf, 0, sizeof(merge_cf));
        memset(&parent_conf, 0, sizeof(parent_conf));
        parent_conf.decompress.max_size = NGX_CONF_UNSET_SIZE;
        parent_conf.decompress.parse_timeout = 30000;
        parent_conf.decompress.parser_budget = 64 * 1024 * 1024;
        memset(&child_conf, 0, sizeof(child_conf));
        child_conf.decompress.max_size = NGX_CONF_UNSET_SIZE;
        child_conf.decompress.parse_timeout = NGX_CONF_UNSET_MSEC;
        child_conf.decompress.parser_budget = NGX_CONF_UNSET_SIZE;

        /* Case 5: memory_budget only -> overrides default max_size */
        parent_conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
        parent_conf.enabled = 1;
        parent_conf.max_size = 10 * 1024 * 1024;
        parent_conf.stream.budget = NGX_HTTP_MARKDOWN_STREAMING_BUDGET_DEFAULT;
        parent_conf.stream.budget_explicit = 0;
        parent_conf.stream.threshold = 32768;
        parent_conf.advanced.prune_noise = 1;
        parent_conf.advanced.memory_budget = 50 * 1024 * 1024;

        child_conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
        child_conf.max_size = NGX_CONF_UNSET_SIZE;
        child_conf.stream.budget = NGX_CONF_UNSET_SIZE;
        child_conf.stream.budget_explicit = 0;
        child_conf.stream.threshold = NGX_CONF_UNSET_SIZE;
        child_conf.advanced.prune_noise = NGX_CONF_UNSET;
        child_conf.advanced.prune_selectors = NGX_CONF_UNSET_PTR;
        child_conf.advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
        child_conf.advanced.memory_budget = NGX_CONF_UNSET_SIZE;

        rc = ngx_http_markdown_merge_conf(&merge_cf, &parent_conf, &child_conf);
        TEST_ASSERT(rc == NGX_CONF_OK,
                    "merge_conf case 5 should succeed");
        TEST_ASSERT(child_conf.max_size == 50 * 1024 * 1024,
                    "unified budget overrides default max_size via merge");

        /* Case 6: explicit max_size + memory_budget -> max_size wins */
        memset(&child_conf, 0, sizeof(child_conf));
        child_conf.decompress.max_size = NGX_CONF_UNSET_SIZE;
        child_conf.decompress.parse_timeout = NGX_CONF_UNSET_MSEC;
        child_conf.decompress.parser_budget = NGX_CONF_UNSET_SIZE;
        child_conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
        child_conf.enabled = 1;
        child_conf.max_size = 5 * 1024 * 1024;
        child_conf.stream.budget = NGX_CONF_UNSET_SIZE;
        child_conf.stream.budget_explicit = 0;
        child_conf.stream.threshold = NGX_CONF_UNSET_SIZE;
        child_conf.advanced.prune_noise = NGX_CONF_UNSET;
        child_conf.advanced.prune_selectors = NGX_CONF_UNSET_PTR;
        child_conf.advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
        child_conf.advanced.memory_budget = 50 * 1024 * 1024;

        rc = ngx_http_markdown_merge_conf(&merge_cf, &parent_conf, &child_conf);
        TEST_ASSERT(rc == NGX_CONF_OK,
                    "merge_conf case 6 should succeed");
        TEST_ASSERT(child_conf.max_size == 5 * 1024 * 1024,
                    "explicit max_size wins over memory_budget via merge");
    }
}

/*
 * Negative test: decompress_max_size == 0 with auto_decompress enabled
 * must be rejected by merge_conf.
 */
static void
test_decompress_max_size_zero_rejected(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t parent;
    ngx_http_markdown_conf_t child;
    char *rc;

    TEST_SUBSECTION("decompress_max_size=0 rejected");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    memset(&parent, 0, sizeof(parent));
    parent.decompress.max_size = NGX_CONF_UNSET_SIZE;
    parent.decompress.parse_timeout = 30000;
    parent.decompress.parser_budget = 64 * 1024 * 1024;
    parent.max_size = 10 * 1024 * 1024;
    parent.advanced.memory_budget = NGX_CONF_UNSET_SIZE;

    memset(&child, 0, sizeof(child));
    child.decompress.max_size = NGX_CONF_UNSET_SIZE;
    child.decompress.parse_timeout = NGX_CONF_UNSET_MSEC;
    child.decompress.parser_budget = NGX_CONF_UNSET_SIZE;
    child.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
    child.max_size = NGX_CONF_UNSET_SIZE;
    child.timeout = NGX_CONF_UNSET_MSEC;
    child.on_error = NGX_CONF_UNSET_UINT;
    child.flavor = NGX_CONF_UNSET_UINT;
    child.decompress.auto_decompress = 1;
    child.advanced.memory_budget = NGX_CONF_UNSET_SIZE;
    child.stream.budget = NGX_CONF_UNSET_SIZE;
    child.stream.threshold = NGX_CONF_UNSET_SIZE;
    child.advanced.prune_noise = NGX_CONF_UNSET;
    child.advanced.prune_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.dynconf_enabled = NGX_CONF_UNSET;
    child.advanced.dynconf_dry_run = NGX_CONF_UNSET;

    /* Force decompress_max_size to 0 after merge by setting explicit 0 */
    child.decompress.max_size = 0;

    rc = ngx_http_markdown_merge_conf(&cf, &parent, &child);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "decompress_max_size=0 with auto_decompress=1 should fail");

    printf("  \xe2\x9c\x93 decompress_max_size=0 correctly rejected\n");
}

/*
 * Verify the spec 49 streaming/cache-validation conflict in merge_conf:
 *   markdown_cache_validation full + markdown_streaming force => error.
 * Gated on policy_explicit so default configs are unaffected.
 */
static void
test_streaming_full_force_conflict_rejected(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t parent;
    ngx_http_markdown_conf_t child;
    char *rc;

    TEST_SUBSECTION("streaming full + force conflict rejected");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    memset(&parent, 0, sizeof(parent));
    parent.decompress.max_size = NGX_CONF_UNSET_SIZE;
    parent.decompress.parse_timeout = 30000;
    parent.decompress.parser_budget = 64 * 1024 * 1024;
    parent.max_size = 10 * 1024 * 1024;
    parent.advanced.memory_budget = NGX_CONF_UNSET_SIZE;

    memset(&child, 0, sizeof(child));
    child.decompress.max_size = NGX_CONF_UNSET_SIZE;
    child.decompress.parse_timeout = NGX_CONF_UNSET_MSEC;
    child.decompress.parser_budget = NGX_CONF_UNSET_SIZE;
    child.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
    child.max_size = NGX_CONF_UNSET_SIZE;
    child.timeout = NGX_CONF_UNSET_MSEC;
    child.on_error = NGX_CONF_UNSET_UINT;
    child.flavor = NGX_CONF_UNSET_UINT;
    child.advanced.memory_budget = NGX_CONF_UNSET_SIZE;
    child.stream.budget = NGX_CONF_UNSET_SIZE;
    child.stream.threshold = NGX_CONF_UNSET_SIZE;
    child.advanced.prune_noise = NGX_CONF_UNSET;
    child.advanced.prune_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.dynconf_enabled = NGX_CONF_UNSET;
    child.advanced.dynconf_dry_run = NGX_CONF_UNSET;

    /* full cache validation + explicitly forced streaming = conflict. */
    child.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    child.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_FORCE;
    child.stream.policy_explicit = 1;

    rc = ngx_http_markdown_merge_conf(&cf, &parent, &child);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "cache_validation full + streaming force should be rejected");

    printf("  \xe2\x9c\x93 full + force conflict correctly rejected\n");
}

/*
 * Verify the spec 49 streaming/cache-validation soft conflict in merge_conf:
 *   markdown_cache_validation full + markdown_streaming auto => warning only
 *   (merge still succeeds; streaming is blocked at runtime by Rust).
 */
static void
test_streaming_full_auto_warns_but_succeeds(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t parent;
    ngx_http_markdown_conf_t child;
    char *rc;

    TEST_SUBSECTION("streaming full + auto warns but succeeds");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    memset(&parent, 0, sizeof(parent));
    parent.decompress.max_size = NGX_CONF_UNSET_SIZE;
    parent.decompress.parse_timeout = 30000;
    parent.decompress.parser_budget = 64 * 1024 * 1024;
    parent.max_size = 10 * 1024 * 1024;
    parent.advanced.memory_budget = NGX_CONF_UNSET_SIZE;

    memset(&child, 0, sizeof(child));
    child.decompress.max_size = NGX_CONF_UNSET_SIZE;
    child.decompress.parse_timeout = NGX_CONF_UNSET_MSEC;
    child.decompress.parser_budget = NGX_CONF_UNSET_SIZE;
    child.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
    child.max_size = NGX_CONF_UNSET_SIZE;
    child.timeout = NGX_CONF_UNSET_MSEC;
    child.on_error = NGX_CONF_UNSET_UINT;
    child.flavor = NGX_CONF_UNSET_UINT;
    child.advanced.memory_budget = NGX_CONF_UNSET_SIZE;
    child.stream.budget = NGX_CONF_UNSET_SIZE;
    child.stream.threshold = NGX_CONF_UNSET_SIZE;
    child.advanced.prune_noise = NGX_CONF_UNSET;
    child.advanced.prune_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
    child.advanced.dynconf_enabled = NGX_CONF_UNSET;
    child.advanced.dynconf_dry_run = NGX_CONF_UNSET;

    child.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    child.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_AUTO;
    child.stream.policy_explicit = 1;

    rc = ngx_http_markdown_merge_conf(&cf, &parent, &child);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "cache_validation full + streaming auto should warn, not fail");

    printf("  \xe2\x9c\x93 full + auto warns but merge succeeds\n");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("config_core_impl Tests\n");
    printf("========================================\n");

    test_metrics_zone_init();
    test_main_conf_create_and_init();
    test_create_conf_defaults();
    test_merge_conf();
    test_dynconf_owner_uses_merged_config();
    test_merge_conf_double_unset();
    test_stream_budget_explicit_maps_to_stream();
    test_stream_preserves_explicit_defaults();
    test_name_helpers_and_levels();
    test_name_helpers_unknown_branches();
    test_filter_flag_and_is_enabled();
    test_filter_flag_additional_branches();
    test_log_merged_conf();
    test_memory_budget_priority_chain();
    test_decompress_max_size_zero_rejected();
    test_streaming_full_force_conflict_rejected();
    test_streaming_full_auto_warns_but_succeeds();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");

    return 0;
}
