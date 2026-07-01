/*
 * Test: v070_default_values
 *
 * Validates that v0.7.0/v0.8.0 configuration directives resolve to the
 * current release defaults.
 *
 * Key principle: an unset config must resolve deterministically to the
 * current design defaults.
 *
 * Verified defaults:
 *   - decompress_max_size: inherits max_size (10MB default) when unset
 *   - parse_timeout: 30000ms (30 seconds)
 *   - parser_budget: 64MB (64 * 1024 * 1024 bytes)
 *   - dynconf_dry_run: 0 (off)
 *   - markdown_diagnostics: not yet a per-location directive (E01.4 pending)
 *
 * This test exercises the merge function with both parent and child at
 * their unset sentinels, confirming that the resolved defaults match
 * the design specification.
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

struct ngx_conf_s {
    ngx_pool_t *pool;
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
 */
#define ngx_conf_init_size_value(conf, default_value) \
    if ((conf) == NGX_CONF_UNSET_SIZE) { (conf) = (default_value); }

#define ngx_conf_init_uint_value(conf, default_value) \
    if ((conf) == NGX_CONF_UNSET_UINT) { (conf) = (default_value); }

#define ngx_conf_merge_size_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_SIZE) { \
        (conf) = ((prev) == NGX_CONF_UNSET_SIZE) ? (default_value) : (prev); \
    }

#define ngx_conf_merge_msec_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_MSEC) { \
        (conf) = ((prev) == NGX_CONF_UNSET_MSEC) ? (default_value) : (prev); \
    }

#define ngx_conf_merge_uint_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_UINT) { \
        (conf) = ((prev) == NGX_CONF_UNSET_UINT) ? (default_value) : (prev); \
    }

#define ngx_conf_merge_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET) { \
        (conf) = ((prev) == NGX_CONF_UNSET) ? (default_value) : (prev); \
    }

#define ngx_conf_merge_ptr_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_PTR) { \
        (conf) = ((prev) == NGX_CONF_UNSET_PTR) ? (default_value) : (prev); \
    }

/*
 * NGINX API stubs for unit test environment.
 */
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

static void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return calloc(1, size);
}

static void
ngx_memzero(void *p, size_t n)
{
    memset(p, 0, n);
}

static void *
ngx_slab_alloc(ngx_slab_pool_t *pool, size_t size)
{
    UNUSED(pool);
    if (g_slab_alloc_fail) {
        return NULL;
    }
    return calloc(1, size);
}

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

/* ── Test functions ───────────────────────────────────────────────── */

/*
 * Test 1: Verify new v0.7.0 directive defaults when both parent and
 * child are at their unset sentinels.
 */
static void
test_v070_defaults_both_unset(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;
    const char *rc;

    TEST_SUBSECTION("v0.7.0 defaults: both parent and child unset");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    parent = ngx_http_markdown_create_conf(&cf);
    child = ngx_http_markdown_create_conf(&cf);

    TEST_ASSERT(parent != NULL, "parent conf allocation");
    TEST_ASSERT(child != NULL, "child conf allocation");

    /* Verify initial unset state */
    TEST_ASSERT(child->decompress.max_size == NGX_CONF_UNSET_SIZE,
        "decompress_max_size should start as NGX_CONF_UNSET_SIZE");
    TEST_ASSERT(child->decompress.parse_timeout == NGX_CONF_UNSET_MSEC,
        "parse_timeout should start as NGX_CONF_UNSET_MSEC");
    TEST_ASSERT(child->decompress.parser_budget == NGX_CONF_UNSET_SIZE,
        "parser_budget should start as NGX_CONF_UNSET_SIZE");
    TEST_ASSERT(child->advanced.dynconf_dry_run == NGX_CONF_UNSET,
        "dynconf_dry_run should start as NGX_CONF_UNSET");

    /* Run merge with no directives configured. */
    rc = ngx_http_markdown_merge_conf(&cf, parent, child);
    TEST_ASSERT(rc == NGX_CONF_OK, "merge_conf should succeed");

    /* ── Verify resolved defaults ── */

    /*
     * decompress_max_size: after merge, should equal max_size (10MB default).
     * This ensures decompression budget tracks the conversion size limit,
     * preventing zip bombs without requiring explicit configuration.
     */
    TEST_ASSERT(child->decompress.max_size == 10 * 1024 * 1024,
        "decompress_max_size should default to max_size (10MB)");

    /*
     * parse_timeout: 30000ms (30 seconds).
     * Generous enough for large documents, strict enough to prevent
     * worker stalls from pathological inputs.
     */
    TEST_ASSERT(child->decompress.parse_timeout == 30000,
        "parse_timeout should default to 30000ms (30s)");

    /*
     * parser_budget: 64MB (64 * 1024 * 1024).
     * Allows parsing of large documents while preventing OOM from
     * adversarial inputs with deep nesting or excessive node counts.
     */
    TEST_ASSERT(child->decompress.parser_budget == 64 * 1024 * 1024,
        "parser_budget should default to 64MB");

    /*
     * dynconf_dry_run: 0 (off).
     * Normal reload behavior by default; dry-run must be explicitly
     * enabled by operators who want validation-only mode.
     */
    TEST_ASSERT(child->advanced.dynconf_dry_run == 0,
        "dynconf_dry_run should default to 0 (off)");

    TEST_PASS("All v0.7.0 defaults correct");

    free(parent);
    free(child);
}

/*
 * Test 2: Verify that decompress_max_size tracks memory_budget override.
 * When memory_budget is set and max_size is not explicitly configured,
 * max_size takes the memory_budget value, and decompress_max_size
 * should follow.
 */
static void
test_decompress_max_size_tracks_memory_budget(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;
    const char *rc;

    TEST_SUBSECTION("decompress_max_size tracks memory_budget override");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    parent = ngx_http_markdown_create_conf(&cf);
    child = ngx_http_markdown_create_conf(&cf);

    TEST_ASSERT(parent != NULL, "parent conf allocation");
    TEST_ASSERT(child != NULL, "child conf allocation");

    /* Set memory_budget on child (simulates markdown_memory_budget 20m) */
    child->advanced.memory_budget = 20 * 1024 * 1024;

    rc = ngx_http_markdown_merge_conf(&cf, parent, child);
    TEST_ASSERT(rc == NGX_CONF_OK, "merge_conf should succeed");

    /* max_size should be overridden by memory_budget */
    TEST_ASSERT(child->max_size == 20 * 1024 * 1024,
        "max_size should be overridden by memory_budget (20MB)");

    /* decompress_max_size should track the effective max_size */
    TEST_ASSERT(child->decompress.max_size == 20 * 1024 * 1024,
        "decompress_max_size should track memory_budget-overridden max_size");

    TEST_PASS("decompress_max_size correctly tracks memory_budget");

    free(parent);
    free(child);
}

/*
 * Test 3: Verify that explicit decompress_max_size is preserved
 * and not overridden by the max_size default.
 */
static void
test_explicit_decompress_max_size_preserved(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;
    const char *rc;

    TEST_SUBSECTION("explicit decompress_max_size preserved");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    parent = ngx_http_markdown_create_conf(&cf);
    child = ngx_http_markdown_create_conf(&cf);

    TEST_ASSERT(parent != NULL, "parent conf allocation");
    TEST_ASSERT(child != NULL, "child conf allocation");

    /* Explicitly set decompress_max_size (simulates markdown_decompress_max_size 20m) */
    child->decompress.max_size = 20 * 1024 * 1024;

    rc = ngx_http_markdown_merge_conf(&cf, parent, child);
    TEST_ASSERT(rc == NGX_CONF_OK, "merge_conf should succeed");

    /* Explicit value should be preserved, not overridden */
    TEST_ASSERT(child->decompress.max_size == 20 * 1024 * 1024,
        "explicit decompress_max_size (20MB) should be preserved");

    /* max_size should still be at its default (10MB) */
    TEST_ASSERT(child->max_size == 10 * 1024 * 1024,
        "max_size should remain at default (10MB)");

    TEST_PASS("explicit decompress_max_size not overridden");

    free(parent);
    free(child);
}

/*
 * Test 4: Verify parse_timeout and parser_budget inheritance from parent.
 */
static void
test_v070_directives_inherit_from_parent(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;
    const char *rc;

    TEST_SUBSECTION("v0.7.0 directives inherit from parent");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    parent = ngx_http_markdown_create_conf(&cf);
    child = ngx_http_markdown_create_conf(&cf);

    TEST_ASSERT(parent != NULL, "parent conf allocation");
    TEST_ASSERT(child != NULL, "child conf allocation");

    /* Set parent values (simulates http-level config) */
    parent->decompress.parse_timeout = 10000;              /* 10s */
    parent->decompress.parser_budget = 32 * 1024 * 1024;   /* 32MB */
    parent->advanced.dynconf_dry_run = 1;       /* on */

    rc = ngx_http_markdown_merge_conf(&cf, parent, child);
    TEST_ASSERT(rc == NGX_CONF_OK, "merge_conf should succeed");

    /* Child should inherit parent values */
    TEST_ASSERT(child->decompress.parse_timeout == 10000,
        "parse_timeout should inherit from parent (10s)");
    TEST_ASSERT(child->decompress.parser_budget == 32 * 1024 * 1024,
        "parser_budget should inherit from parent (32MB)");
    TEST_ASSERT(child->advanced.dynconf_dry_run == 1,
        "dynconf_dry_run should inherit from parent (on)");

    TEST_PASS("v0.7.0 directives correctly inherit from parent");

    free(parent);
    free(child);
}

/*
 * Test 5: Verify that child explicit values override parent for
 * new v0.7.0 directives.
 */
static void
test_v070_directives_child_override(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;
    const char *rc;

    TEST_SUBSECTION("v0.7.0 directives child override");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    parent = ngx_http_markdown_create_conf(&cf);
    child = ngx_http_markdown_create_conf(&cf);

    TEST_ASSERT(parent != NULL, "parent conf allocation");
    TEST_ASSERT(child != NULL, "child conf allocation");

    /* Set parent values */
    parent->decompress.parse_timeout = 10000;
    parent->decompress.parser_budget = 32 * 1024 * 1024;
    parent->advanced.dynconf_dry_run = 1;

    /* Set child overrides */
    child->decompress.parse_timeout = 5000;                /* 5s */
    child->decompress.parser_budget = 16 * 1024 * 1024;    /* 16MB */
    child->advanced.dynconf_dry_run = 0;        /* off */

    rc = ngx_http_markdown_merge_conf(&cf, parent, child);
    TEST_ASSERT(rc == NGX_CONF_OK, "merge_conf should succeed");

    /* Child explicit values should win */
    TEST_ASSERT(child->decompress.parse_timeout == 5000,
        "child parse_timeout override (5s) should win");
    TEST_ASSERT(child->decompress.parser_budget == 16 * 1024 * 1024,
        "child parser_budget override (16MB) should win");
    TEST_ASSERT(child->advanced.dynconf_dry_run == 0,
        "child dynconf_dry_run override (off) should win");

    TEST_PASS("v0.7.0 child overrides work correctly");

    free(parent);
    free(child);
}

/*
 * Test 6: Verify that core defaults remain stable.
 */
static void
test_06x_defaults_unchanged(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;
    const char *rc;

    TEST_SUBSECTION("core defaults unchanged");

    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    parent = ngx_http_markdown_create_conf(&cf);
    child = ngx_http_markdown_create_conf(&cf);

    TEST_ASSERT(parent != NULL, "parent conf allocation");
    TEST_ASSERT(child != NULL, "child conf allocation");

    rc = ngx_http_markdown_merge_conf(&cf, parent, child);
    TEST_ASSERT(rc == NGX_CONF_OK, "merge_conf should succeed");

    /* Core defaults must remain unchanged */
    TEST_ASSERT(child->enabled == 0,
        "enabled should default to off");
    TEST_ASSERT(child->max_size == 10 * 1024 * 1024,
        "max_size should default to 10MB");
    TEST_ASSERT(child->timeout == 5000,
        "timeout should default to 5000ms");
    TEST_ASSERT(child->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS,
        "on_error should default to pass");
    TEST_ASSERT(child->flavor == NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK,
        "flavor should default to commonmark");
    TEST_ASSERT(child->token_estimate == 0,
        "token_estimate should default to off");
    TEST_ASSERT(child->front_matter == 0,
        "front_matter should default to off");
    TEST_ASSERT(child->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_STRICT,
        "accept_policy should default to strict");
    TEST_ASSERT(child->buffer_chunked == 1,
        "buffer_chunked should default to on");
    TEST_ASSERT(child->decompress.auto_decompress == 1,
        "auto_decompress should default to on");
    TEST_ASSERT(child->policy.generate_etag == 0,
        "generate_etag should default to off (ims_only mode)");
    TEST_ASSERT(child->advanced.dynconf_enabled == 0,
        "dynconf_enabled should default to off");
    TEST_ASSERT(child->advanced.prune_noise == 1,
        "prune_noise should default to on");

    TEST_PASS("All core defaults preserved");

    free(parent);
    free(child);
}


/*
 * Test: v0.8.0 default threshold regression guard.
 *
 * Confirms that after full merge_conf() with no directives configured,
 * stream.threshold retains its 0.8.0 default of 1m.
 *
 * Also confirms that when the directive IS explicitly set,
 * the value is preserved correctly.
 */
static void
test_080_threshold_bridge_full_merge(void)
{
    ngx_conf_t cf;
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;
    const char *rc;

    TEST_SUBSECTION("v0.8.0 threshold defaults via full merge_conf");

    /*
     * Case 1: No threshold directives set.
     * stream.threshold must remain at 0.8.0 default (1m).
     */
    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    parent = ngx_http_markdown_create_conf(&cf);
    child = ngx_http_markdown_create_conf(&cf);

    TEST_ASSERT(parent != NULL, "parent conf allocation");
    TEST_ASSERT(child != NULL, "child conf allocation");

    rc = ngx_http_markdown_merge_conf(&cf, parent, child);
    TEST_ASSERT(rc == NGX_CONF_OK, "merge_conf should succeed");

    TEST_ASSERT(child->stream.threshold
        == NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT,
        "stream.threshold must be 1m (1048576) when no "
        "directive was explicitly set");
    TEST_ASSERT(child->stream.threshold_explicit == 0,
        "threshold_explicit must be 0 when unset");

    free(parent);
    free(child);

    /*
     * Case 2: markdown_stream_threshold 64k is explicitly set.
     * The explicit value should be preserved through merge.
     */
    memset(&cf, 0, sizeof(cf));
    cf.pool = &g_pool;

    {
        ngx_http_markdown_conf_t *grandparent;

        grandparent = ngx_http_markdown_create_conf(&cf);
        parent = ngx_http_markdown_create_conf(&cf);
        child = ngx_http_markdown_create_conf(&cf);

        TEST_ASSERT(grandparent != NULL, "grandparent conf allocation");
        TEST_ASSERT(parent != NULL, "parent conf allocation (case 2)");
        TEST_ASSERT(child != NULL, "child conf allocation (case 2)");

        /* Simulate: operator set markdown_stream_threshold 64k at parent level */
        parent->stream.threshold = 64 * 1024;

        /* Merge parent against grandparent (sets threshold_explicit=1) */
        rc = ngx_http_markdown_merge_conf(&cf, grandparent, parent);
        TEST_ASSERT(rc == NGX_CONF_OK, "merge parent should succeed");

        TEST_ASSERT(parent->stream.threshold_explicit == 1,
            "parent threshold_explicit must be 1 after merge");

        /* Merge child against parent */
        rc = ngx_http_markdown_merge_conf(&cf, parent, child);
        TEST_ASSERT(rc == NGX_CONF_OK, "merge child should succeed");

        TEST_ASSERT(child->stream.threshold_explicit == 1,
            "threshold_explicit must be 1 when parent set it");
        TEST_ASSERT(child->stream.threshold == 64 * 1024,
            "stream.threshold must be 64k when "
            "explicitly set");

        free(grandparent);
        free(parent);
        free(child);
    }

    TEST_PASS("v0.8.0 threshold defaults: full merge_conf regression guard");
}

/* ── Main ─────────────────────────────────────────────────────────── */

int
main(void)
{
    /* Suppress -Wunused-function for functions not exercised by this test */
    (void) ngx_http_markdown_compression_name;
    (void) ngx_http_markdown_init_main_conf;
    (void) ngx_http_markdown_create_main_conf;

    ngx_shm_zone_t zone;

    memset(&zone, 0, sizeof(zone));
    g_shared_zone = &zone;
    g_slab_alloc_fail = 0;

    printf("\n========================================\n");
    printf("v0.7.0 Default Values Verification [F02.2]\n");
    printf("========================================\n");

    test_v070_defaults_both_unset();
    test_decompress_max_size_tracks_memory_budget();
    test_explicit_decompress_max_size_preserved();
    test_v070_directives_inherit_from_parent();
    test_v070_directives_child_override();
    test_06x_defaults_unchanged();
    test_080_threshold_bridge_full_merge();

    printf("\n========================================\n");
    printf("All v0.7.0 default value tests passed!\n");
    printf("========================================\n");
    printf("\nVerified defaults summary:\n");
    printf("  decompress_max_size : inherits max_size (10MB default)\n");
    printf("  parse_timeout       : 30000ms (30 seconds)\n");
    printf("  parser_budget       : 67108864 bytes (64MB)\n");
    printf("  dynconf_dry_run     : 0 (off)\n");
    printf("  markdown_diagnostics: not yet a per-location directive\n");
    printf("\n");

    return 0;
}
