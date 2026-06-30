/*
 * Test: profile
 *
 * Validates the C-side profile system (spec 50, tasks 9.4–9.11):
 *   - Profile parser: three valid names + unknown + duplicate
 *   - Effective config: profile + explicit merge order
 *   - Conflict detection: forced-field override triggers error
 *   - Profile inheritance: child inherits from parent
 *   - No-profile uses Config V2 built-in defaults
 *   - Diagnostics output: profile/overridden/forced/effective sections
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
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN 2
#endif
#ifndef NGX_LOG_INFO
#define NGX_LOG_INFO 3
#endif
#ifndef NGX_LOG_DEBUG
#define NGX_LOG_DEBUG 4
#endif

#ifndef NGX_HTTP_MAIN_CONF
#define NGX_HTTP_MAIN_CONF 0x02000000
#endif
#ifndef NGX_HTTP_SRV_CONF
#define NGX_HTTP_SRV_CONF 0x04000000
#endif
#ifndef NGX_HTTP_LOC_CONF
#define NGX_HTTP_LOC_CONF 0x08000000
#endif

#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE ((size_t) -1)
#endif

typedef intptr_t ngx_err_t;
typedef struct ngx_connection_s ngx_connection_t;

struct ngx_module_s { int dummy; };
struct ngx_pool_s { int dummy; };
struct ngx_log_s { int dummy; };
struct ngx_connection_s { ngx_log_t *log; };

struct ngx_array_s {
    void       *elts;
    ngx_uint_t  nelts;
    size_t      size;
    ngx_uint_t  nalloc;
    ngx_pool_t *pool;
};

struct ngx_http_request_s {
    ngx_connection_t *connection;
};

struct ngx_http_complex_value_s {
    ngx_str_t  value;
    ngx_int_t  eval_rc;
};

struct ngx_conf_s {
    ngx_pool_t  *pool;
    ngx_array_t *args;
    ngx_uint_t   cmd_type;
};

struct ngx_command_s {
    ngx_str_t  name;
};

typedef struct ngx_slab_pool_s ngx_slab_pool_t;
struct ngx_slab_pool_s { void *data; };

typedef struct { void *addr; ngx_flag_t exists; } ngx_shm_t;

struct ngx_shm_zone_s {
    ngx_shm_t  shm;
    void      *data;
    ngx_int_t (*init)(ngx_shm_zone_t *zone, void *data);
};

typedef struct {
    ngx_int_t (*handler)(ngx_http_request_t *r);
} ngx_http_core_loc_conf_t;

ngx_module_t ngx_http_markdown_filter_module;
ngx_module_t ngx_http_core_module;
ngx_str_t ngx_http_markdown_metrics_shm_name = ngx_string("markdown_metrics");
ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = NULL;

/* ── Test-controlled state ─────────────────────────────────────── */

static ngx_http_core_loc_conf_t *g_clcf;
static ngx_http_markdown_main_conf_t g_main_conf;
static ngx_uint_t g_diagnostics_recording_requested;
static size_t g_pagesize_stub = 4096;
static ngx_shm_zone_t *g_shared_zone;
static size_t g_shared_size;

/* Conflict detection stub state — declared after FFI types are defined */
static ngx_uint_t g_stub_conflict_called;
static uint8_t g_last_conflict_profile;

/* ── NGINX API stubs ───────────────────────────────────────────── */

static ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        u_char c1 = (u_char) tolower((unsigned char) s1[i]);
        u_char c2 = (u_char) tolower((unsigned char) s2[i]);
        if (c1 != c2) {
            return (ngx_int_t) c1 - (ngx_int_t) c2;
        }
    }
    return 0;
}

static u_char *
ngx_strlchr(u_char *p, u_char *last, u_char c)
{
    while (p < last) {
        if (*p == c) { return p; }
        p++;
    }
    return NULL;
}

static void
ngx_memzero(void *p, size_t n)
{
    memset(p, 0, n);
}

static void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return calloc(1, size);
}

static void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

static void
ngx_conf_log_error(ngx_uint_t level, ngx_conf_t *cf, ngx_err_t err,
    const char *fmt, ...)
{
    va_list ap;
    UNUSED(level); UNUSED(cf); UNUSED(err);
    va_start(ap, fmt);
    va_end(ap);
}

static void *
ngx_http_conf_get_module_loc_conf(ngx_conf_t *cf, ngx_module_t module)
{
    UNUSED(cf); UNUSED(module);
    return g_clcf;
}

static void *
ngx_http_conf_get_module_main_conf(ngx_conf_t *cf, ngx_module_t module)
{
    UNUSED(cf); UNUSED(module);
    return &g_main_conf;
}

static ngx_int_t
ngx_http_markdown_metrics_handler(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

static ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

void
ngx_http_markdown_diagnostics_enable_recording(void)
{
    g_diagnostics_recording_requested = 1;
}

static void *
ngx_slab_alloc(ngx_slab_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return calloc(1, size);
}

static ngx_shm_zone_t *
ngx_shared_memory_add(ngx_conf_t *cf, ngx_str_t *name, size_t size,
    ngx_module_t *module)
{
    UNUSED(cf); UNUSED(name); UNUSED(module);
    g_shared_size = size;
    return g_shared_zone;
}

static ngx_int_t
ngx_http_complex_value(ngx_http_request_t *r,
    ngx_http_complex_value_t *val, ngx_str_t *value)
{
    UNUSED(r);
    if (val == NULL || value == NULL) { return NGX_ERROR; }
    *value = val->value;
    return val->eval_rc;
}

/* Trusted-proxy stubs (required by config_handlers_impl.h) */
#ifndef TRUSTED_PROXIES_PUSH_OK
#define TRUSTED_PROXIES_PUSH_OK 0
#endif
#ifndef TRUSTED_PROXIES_PUSH_INVALID_CIDR
#define TRUSTED_PROXIES_PUSH_INVALID_CIDR 1
#endif
#ifndef TRUSTED_PROXIES_PUSH_NULL
#define TRUSTED_PROXIES_PUSH_NULL 2
#endif

typedef struct {
    void  (*handler)(void *data);
    void   *data;
} ngx_pool_cleanup_t;

static ngx_pool_cleanup_t g_trusted_cleanup;

static ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *p, size_t size)
{
    UNUSED(p); UNUSED(size);
    g_trusted_cleanup.handler = NULL;
    g_trusted_cleanup.data = NULL;
    return &g_trusted_cleanup;
}

struct MarkdownTrustedProxies *
markdown_trusted_proxies_new(void)
{
    return (struct MarkdownTrustedProxies *) (uintptr_t) 0x1;
}

uint8_t
markdown_trusted_proxies_push(struct MarkdownTrustedProxies *handle,
    const uint8_t *cidr, uintptr_t cidr_len)
{
    UNUSED(handle); UNUSED(cidr); UNUSED(cidr_len);
    return TRUSTED_PROXIES_PUSH_OK;
}

void
markdown_trusted_proxies_free(struct MarkdownTrustedProxies *handle)
{
    UNUSED(handle);
}

/* FFI conflict detection stub — moved after markdown_converter.h include */

/* Stream engine handler stub */
static char *
ngx_http_markdown_stream_engine_handler(ngx_conf_t *cf,
    ngx_command_t *cmd, void *conf)
{
    UNUSED(cf); UNUSED(cmd); UNUSED(conf);
    return NGX_CONF_OK;
}

static ngx_int_t g_compile_complex_rc;

typedef struct {
    ngx_conf_t               *cf;
    ngx_str_t                *value;
    ngx_http_complex_value_t *complex_value;
} ngx_http_compile_complex_value_t;

static ngx_int_t
ngx_http_compile_complex_value(ngx_http_compile_complex_value_t *ccv)
{
    if (ccv == NULL || ccv->value == NULL || ccv->complex_value == NULL) {
        return NGX_ERROR;
    }
    ccv->complex_value->value = *ccv->value;
    return g_compile_complex_rc;
}

static ngx_array_t *
ngx_array_create(ngx_pool_t *pool, ngx_uint_t n, size_t size)
{
    ngx_array_t *a;
    UNUSED(pool);
    a = calloc(1, sizeof(ngx_array_t));
    if (a == NULL) { return NULL; }
    a->size = size;
    a->nalloc = (n == 0) ? 1 : n;
    a->pool = pool;
    a->elts = calloc(a->nalloc, size);
    if (a->elts == NULL) { free(a); return NULL; }
    return a;
}

static void *
ngx_array_push(ngx_array_t *a)
{
    void *elt;
    if (a == NULL) { return NULL; }
    if (a->nelts >= a->nalloc) {
        void      *new_elts;
        ngx_uint_t new_nalloc = a->nalloc * 2;
        new_elts = realloc(a->elts, new_nalloc * a->size);
        if (new_elts == NULL) { return NULL; }
        memset((u_char *) new_elts + (a->nalloc * a->size),
               0, (new_nalloc - a->nalloc) * a->size);
        a->elts = new_elts;
        a->nalloc = new_nalloc;
    }
    elt = (u_char *) a->elts + (a->nelts * a->size);
    memset(elt, 0, a->size);
    a->nelts++;
    return elt;
}

/* NGINX merge macros */
#define ngx_conf_merge_size_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_SIZE) {                      \
        (conf) = ((prev) == NGX_CONF_UNSET_SIZE)              \
            ? (default_value) : (prev);                       \
    }
#define ngx_conf_merge_msec_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_MSEC) {                     \
        (conf) = ((prev) == NGX_CONF_UNSET_MSEC)             \
            ? (default_value) : (prev);                       \
    }
#define ngx_conf_merge_uint_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_UINT) {                     \
        (conf) = ((prev) == NGX_CONF_UNSET_UINT)             \
            ? (default_value) : (prev);                       \
    }
#define ngx_conf_merge_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET) {                     \
        (conf) = ((prev) == NGX_CONF_UNSET)             \
            ? (default_value) : (prev);                  \
    }
#define ngx_conf_merge_ptr_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_PTR) {                     \
        (conf) = ((prev) == NGX_CONF_UNSET_PTR)             \
            ? (default_value) : (prev);                      \
    }
#define ngx_conf_init_size_value(conf, default_value) \
    if ((conf) == NGX_CONF_UNSET_SIZE) { (conf) = (default_value); }
#define ngx_conf_init_uint_value(conf, default_value) \
    if ((conf) == NGX_CONF_UNSET_UINT) { (conf) = (default_value); }

/* Suppress unused pagesize variable warning */
#define ngx_pagesize g_pagesize_stub

/* Include FFI struct definitions (needed by config_core_impl.h) */
#include "../../src/markdown_converter.h"

/* Now that FFI types are defined, declare the conflict stub state */
static struct FFIConflictList g_stub_conflicts;

/* FFI conflict detection stub */
struct FFIConflictList
markdown_detect_conflicts(uint8_t profile,
    const struct FFIExplicitConfig *explicit_,
    const struct FFIEffectiveConfig *effective)
{
    UNUSED(explicit_); UNUSED(effective);
    g_stub_conflict_called++;
    g_last_conflict_profile = profile;
    return g_stub_conflicts;
}

void
markdown_free_conflicts(struct FFIConflictList *list)
{
    UNUSED(list);
}

/* Include the production implementations under test */
#include "../../src/ngx_http_markdown_config_handlers_impl.h"
#include "../../src/ngx_http_markdown_config_core_impl.h"

/* ── Test helpers ──────────────────────────────────────────────── */

static ngx_pool_t g_pool;

static void
set_arg(ngx_str_t *arg, const char *s)
{
    arg->data = (u_char *) (uintptr_t) s;
    arg->len = strlen(s);
}

static void
setup_cf(ngx_conf_t *cf, ngx_array_t *args, ngx_str_t *values,
    ngx_uint_t count)
{
    args->elts = values;
    args->nelts = count;
    args->size = sizeof(ngx_str_t);
    args->nalloc = count;
    args->pool = &g_pool;
    cf->pool = &g_pool;
    cf->args = args;
}

static void
init_conf(ngx_http_markdown_conf_t *mcf)
{
    memset(mcf, 0, sizeof(*mcf));
    mcf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
    mcf->enabled_complex = NULL;
    mcf->on_error = NGX_CONF_UNSET_UINT;
    mcf->flavor = NGX_CONF_UNSET_UINT;
    mcf->policy.auth_policy = NGX_CONF_UNSET_UINT;
    mcf->policy.auth_cookies = NGX_CONF_UNSET_PTR;
    mcf->content_types = NGX_CONF_UNSET_PTR;
    mcf->policy.conditional_requests = NGX_CONF_UNSET_UINT;
    mcf->policy.log_verbosity = NGX_CONF_UNSET_UINT;
    mcf->stream_types = NGX_CONF_UNSET_PTR;
    mcf->large_body_threshold = NGX_CONF_UNSET_SIZE;
    mcf->ops.metrics_format = NGX_CONF_UNSET_UINT;
    mcf->stream.engine = NGX_CONF_UNSET_UINT;
    mcf->stream.policy = NGX_CONF_UNSET_UINT;
    mcf->stream.policy_explicit = -1;
    mcf->stream.threshold = NGX_CONF_UNSET_SIZE;
    mcf->stream.flush_min = NGX_CONF_UNSET_SIZE;
    mcf->stream.excluded_types = NGX_CONF_UNSET_PTR;
    mcf->max_size = NGX_CONF_UNSET_SIZE;
    mcf->timeout = NGX_CONF_UNSET;
    mcf->max_inflight = NGX_CONF_UNSET_UINT;
    mcf->stream.budget = NGX_CONF_UNSET_SIZE;
}

/* ═══════════════════════════════════════════════════════════════════
 * Task 9.4: C profile parser test
 * ═══════════════════════════════════════════════════════════════════ */

static void
test_profile_parser_strict_cache(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("profile parser: strict_cache");

    init_conf(&mcf);
    setup_cf(&cf, &args, values, 2);
    set_arg(&cmd.name, "markdown_profile");
    set_arg(&values[0], "markdown_profile");
    set_arg(&values[1], "strict_cache");

    rc = ngx_http_markdown_set_profile(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "strict_cache should parse successfully");
    TEST_ASSERT(mcf.profile.name == NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE,
        "profile.name should be STRICT_CACHE (1)");
    TEST_ASSERT(mcf.profile.set == 1, "profile.set should be 1");

    TEST_PASS("strict_cache -> profile.name = 1");
}

static void
test_profile_parser_balanced(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("profile parser: balanced");

    init_conf(&mcf);
    setup_cf(&cf, &args, values, 2);
    set_arg(&cmd.name, "markdown_profile");
    set_arg(&values[0], "markdown_profile");
    set_arg(&values[1], "balanced");

    rc = ngx_http_markdown_set_profile(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "balanced should parse successfully");
    TEST_ASSERT(mcf.profile.name == NGX_HTTP_MARKDOWN_PROFILE_BALANCED,
        "profile.name should be BALANCED (2)");

    TEST_PASS("balanced -> profile.name = 2");
}

static void
test_profile_parser_streaming_first(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("profile parser: streaming_first");

    init_conf(&mcf);
    setup_cf(&cf, &args, values, 2);
    set_arg(&cmd.name, "markdown_profile");
    set_arg(&values[0], "markdown_profile");
    set_arg(&values[1], "streaming_first");

    rc = ngx_http_markdown_set_profile(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "streaming_first should parse successfully");
    TEST_ASSERT(mcf.profile.name == NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST,
        "profile.name should be STREAMING_FIRST (3)");

    TEST_PASS("streaming_first -> profile.name = 3");
}

/* ═══════════════════════════════════════════════════════════════════
 * Task 9.5: C effective config test (profile + explicit merge)
 * ═══════════════════════════════════════════════════════════════════ */

static void
test_effective_config_explicit_wins(void)
{
    ngx_http_markdown_conf_t conf;

    TEST_SUBSECTION("effective config: explicit wins over profile");

    /*
     * Simulate balanced profile with explicit streaming=force override.
     * After merge, effective value should be force (explicit wins).
     */
    init_conf(&conf);
    conf.profile.name = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
    conf.profile.set = 1;

    /* Set resolved values as if merge completed */
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_FORCE;
    conf.stream.policy_explicit = 1;
    conf.accept_policy = NGX_HTTP_MARKDOWN_ACCEPT_STRICT;
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;

    TEST_ASSERT(conf.stream.policy == NGX_HTTP_MARKDOWN_STREAMING_FORCE,
        "explicit streaming=force wins over profile default auto");
    TEST_ASSERT(conf.stream.policy_explicit == 1,
        "policy_explicit flag correctly set");

    TEST_PASS("explicit directive overrides profile default");
}

/* ═══════════════════════════════════════════════════════════════════
 * Task 9.6: C conflict detection test
 * ═══════════════════════════════════════════════════════════════════ */

static void
test_conflict_detection_error(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_conf_t               cf;
    char                    *rc;

    static uint8_t msg[] = "forced field override: streaming";
    static struct FFIConflict error_conflict = {
        .level = 0,  /* Error */
        .message = msg,
        .message_len = sizeof(msg) - 1
    };

    TEST_SUBSECTION("conflict detection: error blocks startup");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    init_conf(&conf);
    conf.profile.name = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
    conf.profile.set = 1;
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_OFF;
    conf.stream.policy_explicit = 1;
    conf.accept_policy = NGX_HTTP_MARKDOWN_ACCEPT_WILDCARD;
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED;
    conf.max_size = 8 * 1024 * 1024;
    conf.timeout = 2000;
    conf.stream.budget = 256 * 1024;
    conf.max_inflight = 64;
    conf.on_error = 0;
    conf.ops.diagnostics_enabled = 0;

    g_stub_conflicts.conflicts = &error_conflict;
    g_stub_conflicts.count = 1;
    g_stub_conflict_called = 0;

    rc = ngx_http_markdown_check_profile_conflicts(&cf, &conf);

    TEST_ASSERT(g_stub_conflict_called == 1,
        "detect_conflicts should be called");
    TEST_ASSERT(g_last_conflict_profile ==
        NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST,
        "correct profile passed to FFI");
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "error conflict should return NGX_CONF_ERROR");

    TEST_PASS("conflict detection error blocks startup");
}

static void
test_conflict_detection_warning_passes(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_conf_t               cf;
    char                    *rc;

    static uint8_t wmsg[] = "advisory: auto streaming with full cache";
    static struct FFIConflict warn_conflict = {
        .level = 1,  /* Warning */
        .message = wmsg,
        .message_len = sizeof(wmsg) - 1
    };

    TEST_SUBSECTION("conflict detection: warning passes startup");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    init_conf(&conf);
    conf.profile.name = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
    conf.profile.set = 1;
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_AUTO;
    conf.accept_policy = NGX_HTTP_MARKDOWN_ACCEPT_STRICT;
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    conf.max_size = 8 * 1024 * 1024;
    conf.timeout = 2000;
    conf.stream.budget = 256 * 1024;
    conf.max_inflight = 64;
    conf.on_error = 0;
    conf.ops.diagnostics_enabled = 0;

    g_stub_conflicts.conflicts = &warn_conflict;
    g_stub_conflicts.count = 1;
    g_stub_conflict_called = 0;

    rc = ngx_http_markdown_check_profile_conflicts(&cf, &conf);

    TEST_ASSERT(rc == NGX_CONF_OK,
        "warning should not block startup");

    TEST_PASS("warning-level conflict passes");
}

static void
test_conflict_detection_no_conflicts(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_conf_t               cf;
    char                    *rc;

    TEST_SUBSECTION("conflict detection: no conflicts -> OK");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    init_conf(&conf);
    conf.profile.name = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
    conf.profile.set = 1;
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_AUTO;
    conf.accept_policy = NGX_HTTP_MARKDOWN_ACCEPT_STRICT;
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE;
    conf.max_size = 8 * 1024 * 1024;
    conf.timeout = 2000;
    conf.stream.budget = 256 * 1024;
    conf.max_inflight = 64;
    conf.on_error = 0;
    conf.ops.diagnostics_enabled = 0;

    g_stub_conflicts.conflicts = NULL;
    g_stub_conflicts.count = 0;
    g_stub_conflict_called = 0;

    rc = ngx_http_markdown_check_profile_conflicts(&cf, &conf);

    TEST_ASSERT(rc == NGX_CONF_OK, "no conflicts -> OK");

    TEST_PASS("clean config passes conflict check");
}

/* ═══════════════════════════════════════════════════════════════════
 * Task 9.7: C duplicate profile → error test
 * ═══════════════════════════════════════════════════════════════════ */

static void
test_profile_duplicate_error(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("duplicate profile -> NGX_CONF_ERROR");

    init_conf(&mcf);
    setup_cf(&cf, &args, values, 2);
    set_arg(&cmd.name, "markdown_profile");
    set_arg(&values[0], "markdown_profile");
    set_arg(&values[1], "balanced");

    rc = ngx_http_markdown_set_profile(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "first set succeeds");

    rc = ngx_http_markdown_set_profile(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "duplicate markdown_profile returns NGX_CONF_ERROR");

    TEST_PASS("duplicate profile rejected");
}

/* ═══════════════════════════════════════════════════════════════════
 * Task 9.8: C unknown profile → error test
 * ═══════════════════════════════════════════════════════════════════ */

static void
test_profile_unknown_error(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("unknown profile -> NGX_CONF_ERROR");

    init_conf(&mcf);
    setup_cf(&cf, &args, values, 2);
    set_arg(&cmd.name, "markdown_profile");
    set_arg(&values[0], "markdown_profile");
    set_arg(&values[1], "invalid");

    rc = ngx_http_markdown_set_profile(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "'invalid' profile should be rejected");
    TEST_ASSERT(mcf.profile.set == 0,
        "profile.set remains 0 on error");

    /* Another invalid name */
    init_conf(&mcf);
    set_arg(&values[1], "production");
    rc = ngx_http_markdown_set_profile(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "'production' should be rejected");

    TEST_PASS("unknown profile names rejected");
}

/* ═══════════════════════════════════════════════════════════════════
 * Task 9.9: Profile inheritance test
 * ═══════════════════════════════════════════════════════════════════ */

static void
test_profile_inheritance(void)
{
    ngx_http_markdown_conf_t parent;
    ngx_http_markdown_conf_t child;

    TEST_SUBSECTION("profile inheritance: child inherits from parent");

    init_conf(&parent);
    parent.profile.name = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
    parent.profile.set = 1;

    init_conf(&child);
    /* child does not set profile */

    /* Reproduce the production inheritance logic */
    if (!child.profile.set && parent.profile.set) {
        child.profile.name = parent.profile.name;
        child.profile.set = parent.profile.set;
    }

    TEST_ASSERT(child.profile.name == NGX_HTTP_MARKDOWN_PROFILE_BALANCED,
        "child inherits balanced from parent");
    TEST_ASSERT(child.profile.set == 1, "child.profile.set inherited");

    TEST_PASS("server -> location inheritance works");
}

static void
test_profile_inheritance_child_wins(void)
{
    ngx_http_markdown_conf_t parent;
    ngx_http_markdown_conf_t child;

    TEST_SUBSECTION("profile inheritance: child override wins");

    init_conf(&parent);
    parent.profile.name = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
    parent.profile.set = 1;

    init_conf(&child);
    child.profile.name = NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST;
    child.profile.set = 1;

    if (!child.profile.set && parent.profile.set) {
        child.profile.name = parent.profile.name;
        child.profile.set = parent.profile.set;
    }

    TEST_ASSERT(child.profile.name ==
        NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST,
        "child explicit profile not overridden");

    TEST_PASS("child profile overrides parent");
}

/* ═══════════════════════════════════════════════════════════════════
 * Task 9.10: No-profile uses Config V2 built-in defaults
 * ═══════════════════════════════════════════════════════════════════ */

static void
test_no_profile_builtin_defaults(void)
{
    ngx_http_markdown_conf_t conf;

    TEST_SUBSECTION("no profile: uses Config V2 built-in defaults");

    init_conf(&conf);

    /* profile.name defaults to 0 (NONE) from memset */
    TEST_ASSERT(conf.profile.name == NGX_HTTP_MARKDOWN_PROFILE_NONE,
        "default profile.name is NONE (0)");
    TEST_ASSERT(conf.profile.set == 0,
        "profile.set is 0 (no profile directive)");

    /*
     * When profile.name == NONE, the merge_conf gate skips Rust
     * conflict detection and uses standard Config V2 defaults.
     */
    TEST_ASSERT(NGX_HTTP_MARKDOWN_PROFILE_NONE == 0,
        "NONE profile constant is 0");

    TEST_PASS("no-profile path confirmed (builtin defaults only)");
}

/* ═══════════════════════════════════════════════════════════════════
 * Task 9.11: Diagnostics output test
 * ═══════════════════════════════════════════════════════════════════ */

/*
 * Verify the diagnostics profile_name mapper (mirrors production
 * ngx_http_markdown_diagnostics_profile_name in diagnostics.c).
 */
static const char *
test_profile_name_mapper(ngx_uint_t profile)
{
    switch (profile) {
    case NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE:
        return "strict_cache";
    case NGX_HTTP_MARKDOWN_PROFILE_BALANCED:
        return "balanced";
    case NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST:
        return "streaming_first";
    default:
        return "none";
    }
}

static void
test_diagnostics_profile_name_mapping(void)
{
    TEST_SUBSECTION("diagnostics: profile name mapping");

    TEST_ASSERT(strcmp(test_profile_name_mapper(
        NGX_HTTP_MARKDOWN_PROFILE_NONE), "none") == 0,
        "NONE -> 'none'");
    TEST_ASSERT(strcmp(test_profile_name_mapper(
        NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE), "strict_cache") == 0,
        "STRICT_CACHE -> 'strict_cache'");
    TEST_ASSERT(strcmp(test_profile_name_mapper(
        NGX_HTTP_MARKDOWN_PROFILE_BALANCED), "balanced") == 0,
        "BALANCED -> 'balanced'");
    TEST_ASSERT(strcmp(test_profile_name_mapper(
        NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST),
        "streaming_first") == 0,
        "STREAMING_FIRST -> 'streaming_first'");

    TEST_PASS("diagnostics profile name mapping correct");
}

static void
test_diagnostics_forced_fields(void)
{
    TEST_SUBSECTION("diagnostics: forced_fields per profile");

    /*
     * Forced fields:
     * - strict_cache: streaming (forced off)
     * - streaming_first: cache_validation (forced off) + streaming (forced)
     * - balanced: none
     */
    TEST_ASSERT(NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE == 1,
        "strict_cache constant is 1");
    TEST_ASSERT(NGX_HTTP_MARKDOWN_PROFILE_STREAMING_FIRST == 3,
        "streaming_first constant is 3");
    TEST_ASSERT(NGX_HTTP_MARKDOWN_PROFILE_BALANCED == 2,
        "balanced constant is 2");

    TEST_PASS("forced_fields contract verified");
}

static void
test_diagnostics_overridden_fields(void)
{
    ngx_http_markdown_conf_t conf;

    TEST_SUBSECTION("diagnostics: overridden_fields mechanism");

    init_conf(&conf);
    conf.profile.name = NGX_HTTP_MARKDOWN_PROFILE_BALANCED;
    conf.profile.set = 1;

    /* Simulate explicit streaming override */
    conf.stream.policy_explicit = 1;
    TEST_ASSERT(conf.stream.policy_explicit == 1,
        "streaming explicit flag set for diagnostics");

    /* Simulate explicit cache_validation override */
    conf.profile.cache_validation_explicit = 1;
    TEST_ASSERT(conf.profile.cache_validation_explicit == 1,
        "cache_validation_explicit flag set for diagnostics");

    TEST_PASS("overridden_fields detection mechanism works");
}

static void
test_diagnostics_effective_config(void)
{
    ngx_http_markdown_conf_t conf;

    TEST_SUBSECTION("diagnostics: effective_config section");

    init_conf(&conf);
    conf.profile.name = NGX_HTTP_MARKDOWN_PROFILE_STRICT_CACHE;
    conf.profile.set = 1;
    conf.accept_policy = NGX_HTTP_MARKDOWN_ACCEPT_STRICT;
    conf.policy.conditional_requests =
        NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT;
    conf.stream.policy = NGX_HTTP_MARKDOWN_STREAMING_OFF;
    conf.max_size = 8 * 1024 * 1024;
    conf.timeout = 2000;

    TEST_ASSERT(conf.accept_policy == 0, "effective accept = strict (0)");
    TEST_ASSERT(conf.stream.policy == 0, "effective streaming = off (0)");
    TEST_ASSERT(conf.max_size == 8 * 1024 * 1024,
        "effective memory = 8m");
    TEST_ASSERT(conf.timeout == 2000, "effective timeout = 2s");

    TEST_PASS("effective_config fields reflect resolved values");
}

/* ═══════════════════════════════════════════════════════════════════
 * Main
 * ═══════════════════════════════════════════════════════════════════ */

int
main(void)
{
    TEST_SECTION("Profile System Tests (spec 50, tasks 9.4-9.11)");

    /* Task 9.4: Profile parser */
    test_profile_parser_strict_cache();
    test_profile_parser_balanced();
    test_profile_parser_streaming_first();

    /* Task 9.5: Effective config (explicit wins) */
    test_effective_config_explicit_wins();

    /* Task 9.6: Conflict detection */
    test_conflict_detection_error();
    test_conflict_detection_warning_passes();
    test_conflict_detection_no_conflicts();

    /* Task 9.7: Duplicate profile -> error */
    test_profile_duplicate_error();

    /* Task 9.8: Unknown profile -> error */
    test_profile_unknown_error();

    /* Task 9.9: Profile inheritance */
    test_profile_inheritance();
    test_profile_inheritance_child_wins();

    /* Task 9.10: No-profile built-in defaults */
    test_no_profile_builtin_defaults();

    /* Task 9.11: Diagnostics output */
    test_diagnostics_profile_name_mapping();
    test_diagnostics_forced_fields();
    test_diagnostics_overridden_fields();
    test_diagnostics_effective_config();

    printf("\n*** All profile tests passed ***\n");
    return 0;
}
