/*
 * Test: effective_conf consistency
 */

#include "../include/test_common.h"

#include <ctype.h>
#include <stdarg.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_OK
#define NGX_OK       0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR   -1
#endif
#ifndef NGX_DECLINED
#define NGX_DECLINED -2
#endif

#ifndef NGX_LOG_ERR
#define NGX_LOG_ERR    1
#endif
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN   2
#endif
#ifndef NGX_LOG_INFO
#define NGX_LOG_INFO   3
#endif
#ifndef NGX_LOG_DEBUG
#define NGX_LOG_DEBUG  4
#endif

typedef intptr_t ngx_err_t;

typedef struct ngx_cycle_s     ngx_cycle_t;
typedef struct ngx_connection_s ngx_connection_t;

struct ngx_module_s {
    int dummy;
};

struct ngx_pool_s {
    int dummy;
};

struct ngx_log_s {
    int dummy;
};

struct ngx_cycle_s {
    ngx_pool_t *pool;
    ngx_log_t  *log;
};

struct ngx_connection_s {
    ngx_log_t *log;
};

struct ngx_http_request_s {
    ngx_pool_t       *pool;
    ngx_connection_t *connection;
};

struct ngx_http_complex_value_s {
    ngx_str_t  value;
    ngx_int_t  eval_rc;
};

ngx_module_t ngx_http_markdown_filter_module;
ngx_str_t ngx_http_markdown_metrics_shm_name = ngx_string("");
ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = NULL;

#define ngx_memzero(p, n)   memset((p), 0, (n))
#define ngx_memcpy(dst, src, n) memcpy((dst), (src), (n))
#define ngx_memmove(dst, src, n) memmove((dst), (src), (n))

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

#undef ngx_log_error
static void
test_effective_conf_log_ignore(const char *fmt, ...)
{
    UNUSED(fmt);
}

#define ngx_log_error(level, log, err, fmt, ...)                                     \
    do {                                                                              \
        UNUSED(level);                                                                \
        UNUSED(log);                                                                  \
        UNUSED(err);                                                                  \
        if (0) {                                                                      \
            test_effective_conf_log_ignore((fmt), ##__VA_ARGS__);                    \
        }                                                                             \
    } while (0)

#define NGX_MAX_PATH 1024

typedef time_t ngx_mtime_t;

#define ngx_file_info_t       struct stat
#define ngx_file_info(name, fi) stat((const char *)(name), (fi))
#define ngx_file_mtime(fi)    ((fi)->st_mtime)
#define NGX_FILE_ERROR        (-1)

typedef int ngx_fd_t;
#define NGX_INVALID_FILE     (-1)

#define NGX_FILE_RDONLY      0
#define NGX_FILE_OPEN        0

static ngx_fd_t
ngx_open_file(u_char *name, int mode, int create, int access)
{
    UNUSED(mode);
    UNUSED(create);
    UNUSED(access);
    return open((const char *) name, O_RDONLY);
}

#define ngx_close_file(fd) close(fd)

static ssize_t
ngx_read_fd(ngx_fd_t fd, void *buf, size_t size)
{
    return read(fd, buf, size);
}

static void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return calloc(1, size);
}

static void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

static ssize_t
ngx_parse_size(ngx_str_t *line)
{
    ssize_t     num;
    u_char     *last;

    num = 0;
    last = line->data + line->len;

    for (u_char *p = line->data; p < last; p++) {
        if (*p >= '0' && *p <= '9') {
            num = num * 10 + (*p - '0');
        } else {
            switch (*p) {
            case 'k': case 'K': return num * 1024;
            case 'm': case 'M': return num * 1024 * 1024;
            case 'g': case 'G': return num * 1024 * 1024 * 1024;
            default: return NGX_ERROR;
            }
        }
    }
    return num;
}

typedef struct ngx_event_s ngx_event_t;

struct ngx_event_s {
    void        (*handler)(ngx_event_t *ev);
    void         *data;
    ngx_log_t   *log;
    unsigned      timer_set;
};

static void
ngx_add_timer(ngx_event_t *ev, ngx_msec_t timer)
{
    UNUSED(ev);
    UNUSED(timer);
}

static void
ngx_del_timer(ngx_event_t *ev)
{
    UNUSED(ev);
}

#define NGX_CONF_UNSET       (-1)
#define NGX_CONF_UNSET_UINT  (ngx_uint_t) -1
#define NGX_CONF_UNSET_SIZE  (size_t) -1

#define NGX_HTTP_MARKDOWN_LOG_ERROR  0
#define NGX_HTTP_MARKDOWN_LOG_WARN   1
#define NGX_HTTP_MARKDOWN_LOG_INFO   2
#define NGX_HTTP_MARKDOWN_LOG_DEBUG  3

#include "../../src/ngx_http_markdown_dynconf_impl.h"

static ngx_pool_t  g_pool;
static ngx_log_t   g_log;


static void
test_build_effective_conf_from_valid_snapshot(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_dynconf_snapshot_t snap;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("build_effective_conf from valid snapshot");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&snap, sizeof(snap));
    ngx_memzero(&eff, sizeof(eff));

    conf.enabled = 1;
    conf.enabled_source = 2;
    conf.advanced.prune_noise = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    conf.advanced.memory_budget = 4 * 1024 * 1024;
    conf.stream.budget = 2 * 1024 * 1024;

    ngx_http_markdown_dynconf_snapshot_from_conf(&snap, &conf);

    ngx_http_markdown_build_effective_conf(&eff, &snap, &conf);

    TEST_ASSERT(eff.enabled == 1,
                "effective enabled from snapshot");
    TEST_ASSERT(eff.enabled_source == 2,
                "effective enabled_source from snapshot");
    TEST_ASSERT(eff.prune_noise == 1,
                "effective prune_noise from snapshot");
    TEST_ASSERT(eff.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "effective log_verbosity from snapshot");
    TEST_ASSERT(eff.memory_budget == 4 * 1024 * 1024,
                "effective memory_budget from snapshot");
    TEST_ASSERT(eff.streaming_budget == 2 * 1024 * 1024,
                "effective streaming_budget from snapshot");

    TEST_PASS("build_effective_conf from valid snapshot");
}


static void
test_build_effective_conf_null_snapshot_falls_back_to_conf(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("build_effective_conf with NULL snapshot falls back to conf");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&eff, sizeof(eff));

    conf.enabled = 1;
    conf.enabled_source = 3;
    conf.advanced.prune_noise = 0;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
    conf.advanced.memory_budget = 8 * 1024 * 1024;
    conf.stream.budget = 4 * 1024 * 1024;

    ngx_http_markdown_build_effective_conf(&eff, NULL, &conf);

    TEST_ASSERT(eff.enabled == 1,
                "effective enabled from conf when snapshot NULL");
    TEST_ASSERT(eff.enabled_source == 3,
                "effective enabled_source from conf when snapshot NULL");
    TEST_ASSERT(eff.prune_noise == 0,
                "effective prune_noise from conf when snapshot NULL");
    TEST_ASSERT(eff.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN,
                "effective log_verbosity from conf when snapshot NULL");
    TEST_ASSERT(eff.memory_budget == 8 * 1024 * 1024,
                "effective memory_budget from conf when snapshot NULL");
    TEST_ASSERT(eff.streaming_budget == 4 * 1024 * 1024,
                "effective streaming_budget from conf when snapshot NULL");

    TEST_PASS("build_effective_conf with NULL snapshot falls back to conf");
}


static void
test_build_effective_conf_invalid_snapshot_falls_back(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_dynconf_snapshot_t snap;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("build_effective_conf with invalid snapshot falls back");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&snap, sizeof(snap));
    ngx_memzero(&eff, sizeof(eff));

    conf.enabled = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    conf.advanced.memory_budget = 16 * 1024 * 1024;
    conf.stream.budget = 8 * 1024 * 1024;

    snap.valid = 0;

    ngx_http_markdown_build_effective_conf(&eff, &snap, &conf);

    TEST_ASSERT(eff.log_verbosity == NGX_HTTP_MARKDOWN_LOG_INFO,
                "effective log_verbosity falls back when snapshot invalid");
    TEST_ASSERT(eff.memory_budget == 16 * 1024 * 1024,
                "effective memory_budget falls back when snapshot invalid");
    TEST_ASSERT(eff.streaming_budget == 8 * 1024 * 1024,
                "effective streaming_budget falls back when snapshot invalid");

    TEST_PASS("build_effective_conf with invalid snapshot falls back");
}


static void
test_effective_helpers_read_from_eff_when_present(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("effective_* helpers read from eff when present");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&eff, sizeof(eff));

    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    conf.advanced.prune_noise = 0;
    conf.advanced.memory_budget = 1024;
    conf.stream.budget = 512;

    eff.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    eff.prune_noise = 1;
    eff.memory_budget = 2048;
    eff.streaming_budget = 1024;

    TEST_ASSERT(
        ngx_http_markdown_effective_log_verbosity(&eff, &conf)
            == NGX_HTTP_MARKDOWN_LOG_DEBUG,
        "effective_log_verbosity reads from eff");
    TEST_ASSERT(
        ngx_http_markdown_effective_prune_noise(&eff, &conf) == 1,
        "effective_prune_noise reads from eff");
    TEST_ASSERT(
        ngx_http_markdown_effective_memory_budget(&eff, &conf) == 2048,
        "effective_memory_budget reads from eff");
    TEST_ASSERT(
        ngx_http_markdown_effective_streaming_budget(&eff, &conf) == 1024,
        "effective_streaming_budget reads from eff");
    TEST_ASSERT(
        ngx_http_markdown_effective_enabled(&eff, &conf) == eff.enabled,
        "effective_enabled reads from eff");
    TEST_ASSERT(
        ngx_http_markdown_effective_enabled_source(&eff, &conf)
            == eff.enabled_source,
        "effective_enabled_source reads from eff");

    TEST_PASS("effective_* helpers read from eff when present");
}


static void
test_effective_helpers_fall_back_when_eff_null(void)
{
    ngx_http_markdown_conf_t conf;

    TEST_SUBSECTION("effective_* helpers fall back to conf when eff is NULL");

    ngx_memzero(&conf, sizeof(conf));

    conf.enabled = 1;
    conf.enabled_source = 5;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
    conf.advanced.prune_noise = 1;
    conf.advanced.memory_budget = 4096;
    conf.stream.budget = 2048;

    TEST_ASSERT(
        ngx_http_markdown_effective_log_verbosity(NULL, &conf)
            == NGX_HTTP_MARKDOWN_LOG_WARN,
        "effective_log_verbosity falls back to conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_prune_noise(NULL, &conf) == 1,
        "effective_prune_noise falls back to conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_memory_budget(NULL, &conf) == 4096,
        "effective_memory_budget falls back to conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_streaming_budget(NULL, &conf) == 2048,
        "effective_streaming_budget falls back to conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_enabled(NULL, &conf) == 1,
        "effective_enabled falls back to conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_enabled_source(NULL, &conf) == 5,
        "effective_enabled_source falls back to conf");

    TEST_PASS("effective_* helpers fall back to conf when eff is NULL");
}


static void
test_request_snapshot_consistency_after_conf_change(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_dynconf_snapshot_t snap_at_request_start;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("request snapshot consistency after live conf change");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&snap_at_request_start, sizeof(snap_at_request_start));
    ngx_memzero(&eff, sizeof(eff));

    conf.enabled = 1;
    conf.advanced.prune_noise = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    conf.advanced.memory_budget = 4 * 1024 * 1024;
    conf.stream.budget = 2 * 1024 * 1024;

    ngx_http_markdown_dynconf_snapshot_from_conf(&snap_at_request_start, &conf);

    ngx_http_markdown_build_effective_conf(
        &eff, &snap_at_request_start, &conf);

    TEST_ASSERT(eff.prune_noise == 1,
                "before reload: prune_noise is 1");
    TEST_ASSERT(eff.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "before reload: log_verbosity is DEBUG");
    TEST_ASSERT(eff.memory_budget == 4 * 1024 * 1024,
                "before reload: memory_budget is 4M");
    TEST_ASSERT(eff.streaming_budget == 2 * 1024 * 1024,
                "before reload: streaming_budget is 2M");

    conf.advanced.prune_noise = 0;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    conf.advanced.memory_budget = 16 * 1024 * 1024;
    conf.stream.budget = 8 * 1024 * 1024;

    TEST_ASSERT(eff.prune_noise == 1,
                "after conf change: effective prune_noise still 1");
    TEST_ASSERT(eff.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "after conf change: effective log_verbosity still DEBUG");
    TEST_ASSERT(eff.memory_budget == 4 * 1024 * 1024,
                "after conf change: effective memory_budget still 4M");
    TEST_ASSERT(eff.streaming_budget == 2 * 1024 * 1024,
                "after conf change: effective streaming_budget still 2M");

    TEST_ASSERT(
        ngx_http_markdown_effective_prune_noise(&eff, &conf) == 1,
        "effective_prune_noise helper returns snapshot value, not live conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_log_verbosity(&eff, &conf)
            == NGX_HTTP_MARKDOWN_LOG_DEBUG,
        "effective_log_verbosity helper returns snapshot value, not live conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_memory_budget(&eff, &conf)
            == 4 * 1024 * 1024,
        "effective_memory_budget helper returns snapshot value, not live conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_streaming_budget(&eff, &conf)
            == 2 * 1024 * 1024,
        "effective_streaming_budget helper returns snapshot value, not live conf");

    TEST_PASS("request snapshot consistency after live conf change");
}


static void
test_request_snapshot_consistency_with_dynconf_apply_snapshot(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_dynconf_snapshot_t snap_at_request_start;
    ngx_http_markdown_dynconf_snapshot_t reload_snapshot;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("request consistency when dynconf_apply_snapshot modifies live conf");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&snap_at_request_start, sizeof(snap_at_request_start));
    ngx_memzero(&reload_snapshot, sizeof(reload_snapshot));
    ngx_memzero(&eff, sizeof(eff));

    conf.enabled = 1;
    conf.advanced.prune_noise = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    conf.advanced.memory_budget = 4 * 1024 * 1024;
    conf.stream.budget = 2 * 1024 * 1024;

    ngx_http_markdown_dynconf_snapshot_from_conf(&snap_at_request_start, &conf);

    ngx_http_markdown_build_effective_conf(
        &eff, &snap_at_request_start, &conf);

    reload_snapshot.enabled = 1;
    reload_snapshot.prune_noise = 0;
    reload_snapshot.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    reload_snapshot.memory_budget = 32 * 1024 * 1024;
    reload_snapshot.streaming_budget = 16 * 1024 * 1024;
    reload_snapshot.valid = 1;

    ngx_http_markdown_dynconf_apply_snapshot(&conf, &reload_snapshot);

    TEST_ASSERT(conf.advanced.prune_noise == 0,
                "live conf prune_noise changed by apply_snapshot");
    TEST_ASSERT(conf.policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_ERROR,
                "live conf log_verbosity changed by apply_snapshot");
    TEST_ASSERT(conf.advanced.memory_budget == 32 * 1024 * 1024,
                "live conf memory_budget changed by apply_snapshot");

    TEST_ASSERT(
        ngx_http_markdown_effective_prune_noise(&eff, &conf) == 1,
        "effective prune_noise still from original snapshot (1)");
    TEST_ASSERT(
        ngx_http_markdown_effective_log_verbosity(&eff, &conf)
            == NGX_HTTP_MARKDOWN_LOG_INFO,
        "effective log_verbosity still from original snapshot (INFO)");
    TEST_ASSERT(
        ngx_http_markdown_effective_memory_budget(&eff, &conf)
            == 4 * 1024 * 1024,
        "effective memory_budget still from original snapshot (4M)");
    TEST_ASSERT(
        ngx_http_markdown_effective_streaming_budget(&eff, &conf)
            == 2 * 1024 * 1024,
        "effective streaming_budget still from original snapshot (2M)");

    TEST_PASS("request consistency preserved when dynconf_apply_snapshot modifies live conf");
}


static void
test_build_effective_conf_null_inputs(void)
{
    ngx_http_markdown_effective_conf_t eff;
    ngx_http_markdown_conf_t           conf;

    TEST_SUBSECTION("build_effective_conf with NULL inputs does not crash");

    ngx_memzero(&eff, sizeof(eff));
    ngx_memzero(&conf, sizeof(conf));

    ngx_http_markdown_build_effective_conf(NULL, NULL, NULL);
    ngx_http_markdown_build_effective_conf(NULL, NULL, &conf);
    ngx_http_markdown_build_effective_conf(&eff, NULL, NULL);

    TEST_PASS("build_effective_conf with NULL inputs does not crash");
}


static void
test_effective_helpers_edge_values(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("effective_* helpers with edge values (zero, max)");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&eff, sizeof(eff));

    conf.policy.log_verbosity = 0;
    conf.advanced.memory_budget = 0;
    conf.stream.budget = 0;

    eff.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    eff.memory_budget = SIZE_MAX;
    eff.streaming_budget = SIZE_MAX;

    TEST_ASSERT(
        ngx_http_markdown_effective_log_verbosity(&eff, &conf)
            == NGX_HTTP_MARKDOWN_LOG_DEBUG,
        "effective_log_verbosity returns eff value (DEBUG)");
    TEST_ASSERT(
        ngx_http_markdown_effective_memory_budget(&eff, &conf) == SIZE_MAX,
        "effective_memory_budget returns eff value (SIZE_MAX)");
    TEST_ASSERT(
        ngx_http_markdown_effective_streaming_budget(&eff, &conf) == SIZE_MAX,
        "effective_streaming_budget returns eff value (SIZE_MAX)");

    TEST_ASSERT(
        ngx_http_markdown_effective_memory_budget(NULL, &conf) == 0,
        "effective_memory_budget falls back to conf (0)");
    TEST_ASSERT(
        ngx_http_markdown_effective_streaming_budget(NULL, &conf) == 0,
        "effective_streaming_budget falls back to conf (0)");

    TEST_PASS("effective_* helpers with edge values");
}


/*
 * Simulated request context for bind_request_snapshot test.
 * Only the fields exercised by bind_request_snapshot are included.
 * Field order and types must match ngx_http_markdown_ctx_t so that
 * casting to (ngx_http_markdown_ctx_t*) for bind_request_snapshot
 * is layout-compatible.
 */
typedef struct {
    ngx_http_markdown_dynconf_snapshot_t *dynconf_snapshot;
    ngx_http_markdown_effective_conf_t   *effective_conf;
} test_ctx_t;


/*
 * Test helper that mirrors the production ngx_http_markdown_bind_request_snapshot
 * logic.  Kept in sync with ngx_http_markdown_request_impl.h — any change
 * to the production function must be reflected here.
 *
 * DIVERGENCE RISK: This is a mirror, not a direct call.  If the production
 * bind_request_snapshot logic changes (e.g. new allocation, different gating
 * condition), this helper must be updated in the same changeset or the test
 * will pass while behavior drifts.  The ideal fix is to extract the core
 * bind/copy logic into a shared helper that both production and test code
 * can include without pulling in the full NGINX request-path dependency set.
 * That refactoring is deferred pending a seam in request_impl.h.
 *
 * This helper exists because the production function is a static inline
 * in request_impl.h, which has NGINX-internal dependencies not available
 * in the unit test compilation environment.
 */
static void
test_bind_request_snapshot(
    ngx_http_request_t *r,
    test_ctx_t *ctx,
    const ngx_http_markdown_dynconf_snapshot_t *snap_copy,
    const ngx_http_markdown_effective_conf_t *early_eff,
    const ngx_http_markdown_conf_t *conf)
{
    if (conf->advanced.dynconf_enabled) {
        ctx->dynconf_snapshot =
            ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_dynconf_snapshot_t));
        if (ctx->dynconf_snapshot != NULL && snap_copy != NULL) {
            *ctx->dynconf_snapshot = *snap_copy;
        } else if (ctx->dynconf_snapshot == NULL) {
            ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                          "markdown filter: failed to allocate dynconf snapshot "
                          "from request pool; request will use live conf values");
        }
    }

    ctx->effective_conf =
        ngx_pcalloc(r->pool, sizeof(ngx_http_markdown_effective_conf_t));
    if (ctx->effective_conf != NULL) {
        *ctx->effective_conf = *early_eff;
    } else {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                      "markdown filter: failed to allocate effective conf "
                      "from request pool; request will use live conf values");
    }
}


static void
test_bind_request_snapshot_preserves_captured_snapshot(void)
{
    ngx_http_markdown_conf_t            conf;
    ngx_http_markdown_dynconf_snapshot_t snap_a;
    ngx_http_markdown_effective_conf_t  early_eff_a;
    ngx_http_markdown_dynconf_snapshot_t snap_b;
    ngx_http_markdown_effective_conf_t  eff_b_ignore;
    test_ctx_t                          tctx;
    ngx_http_request_t                  r;
    ngx_connection_t                    conn;
    ngx_log_t                           log;

    TEST_SUBSECTION("bind_request_snapshot preserves captured snapshot A "
                    "even after global snapshot becomes B");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&snap_a, sizeof(snap_a));
    ngx_memzero(&early_eff_a, sizeof(early_eff_a));
    ngx_memzero(&snap_b, sizeof(snap_b));
    ngx_memzero(&eff_b_ignore, sizeof(eff_b_ignore));
    ngx_memzero(&tctx, sizeof(tctx));
    ngx_memzero(&r, sizeof(r));
    ngx_memzero(&conn, sizeof(conn));
    ngx_memzero(&log, sizeof(log));

    r.pool = NULL;
    r.connection = &conn;
    conn.log = &log;

    conf.enabled = 1;
    conf.advanced.prune_noise = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    conf.advanced.memory_budget = 4 * 1024 * 1024;
    conf.stream.budget = 2 * 1024 * 1024;
    conf.advanced.dynconf_enabled = 1;

    ngx_http_markdown_dynconf_snapshot_from_conf(&snap_a, &conf);
    ngx_http_markdown_build_effective_conf(&early_eff_a, &snap_a, &conf);

    snap_b.enabled = 1;
    snap_b.prune_noise = 0;
    snap_b.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    snap_b.memory_budget = 32 * 1024 * 1024;
    snap_b.streaming_budget = 16 * 1024 * 1024;
    snap_b.valid = 1;

    ngx_http_markdown_build_effective_conf(
        &eff_b_ignore, &snap_b, &conf);

    /* Call bind_request_snapshot with snapshot A (mirrors production code) */
    test_bind_request_snapshot(&r, &tctx, &snap_a, &early_eff_a, &conf);

    TEST_ASSERT(tctx.dynconf_snapshot != NULL,
                "ctx dynconf_snapshot allocated (dynconf_enabled=1)");
    TEST_ASSERT(tctx.effective_conf != NULL,
                "ctx effective_conf allocated");

    TEST_ASSERT(tctx.dynconf_snapshot->prune_noise == 1,
                "ctx snapshot prune_noise is from A (1)");
    TEST_ASSERT(tctx.dynconf_snapshot->log_verbosity
                    == NGX_HTTP_MARKDOWN_LOG_INFO,
                "ctx snapshot log_verbosity is from A (INFO)");
    TEST_ASSERT(tctx.dynconf_snapshot->memory_budget == 4 * 1024 * 1024,
                "ctx snapshot memory_budget is from A (4M)");
    TEST_ASSERT(tctx.dynconf_snapshot->streaming_budget == 2 * 1024 * 1024,
                "ctx snapshot streaming_budget is from A (2M)");

    TEST_ASSERT(tctx.effective_conf->prune_noise == 1,
                "ctx effective prune_noise is from A (1)");
    TEST_ASSERT(tctx.effective_conf->log_verbosity
                    == NGX_HTTP_MARKDOWN_LOG_INFO,
                "ctx effective log_verbosity is from A (INFO)");
    TEST_ASSERT(tctx.effective_conf->memory_budget == 4 * 1024 * 1024,
                "ctx effective memory_budget is from A (4M)");
    TEST_ASSERT(tctx.effective_conf->streaming_budget == 2 * 1024 * 1024,
                "ctx effective streaming_budget is from A (2M)");

    TEST_ASSERT(snap_b.prune_noise == 0,
                "global snapshot B prune_noise is 0 (different)");
    TEST_ASSERT(snap_b.log_verbosity == NGX_HTTP_MARKDOWN_LOG_ERROR,
                "global snapshot B log_verbosity is ERROR (different)");

    free(tctx.dynconf_snapshot);
    free(tctx.effective_conf);

    TEST_PASS("bind_request_snapshot preserves captured snapshot A "
              "even after global snapshot becomes B");
}


/*
 * Regression test for Finding 1 (High): dynconf snapshot must NOT leak
 * into a location that has dynconf_enabled=0.
 *
 * Scenario: Global snapshot contains different values from live conf.
 * A location with dynconf_enabled=0 must only see its own static/inherited
 * conf values, not the global snapshot values.
 */
static void
test_dynconf_snapshot_not_consumed_when_dynconf_disabled(void)
{
    ngx_http_markdown_conf_t            conf;
    ngx_http_markdown_dynconf_snapshot_t global_snap;
    ngx_http_markdown_effective_conf_t  early_eff;
    test_ctx_t                          tctx;
    ngx_http_request_t                  r;
    ngx_connection_t                    conn;
    ngx_log_t                           log;

    TEST_SUBSECTION("dynconf snapshot not consumed when dynconf_enabled=0");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&global_snap, sizeof(global_snap));
    ngx_memzero(&early_eff, sizeof(early_eff));
    ngx_memzero(&tctx, sizeof(tctx));
    ngx_memzero(&r, sizeof(r));
    ngx_memzero(&conn, sizeof(conn));
    ngx_memzero(&log, sizeof(log));

    r.pool = NULL;
    r.connection = &conn;
    conn.log = &log;

    /* Location config: dynconf disabled, different static values */
    conf.enabled = 1;
    conf.advanced.prune_noise = 0;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    conf.advanced.memory_budget = 1 * 1024 * 1024;
    conf.stream.budget = 512 * 1024;
    conf.advanced.dynconf_enabled = 0;

    /* Global snapshot has DIFFERENT values (from another location's reload) */
    global_snap.enabled = 1;
    global_snap.prune_noise = 1;
    global_snap.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    global_snap.memory_budget = 32 * 1024 * 1024;
    global_snap.streaming_budget = 16 * 1024 * 1024;
    global_snap.valid = 1;

    /*
     * Build effective conf with NULL snapshot (mirrors header_filter
     * behavior when dynconf_enabled=0: passes NULL instead of &snap_copy).
     */
    ngx_http_markdown_build_effective_conf(&early_eff, NULL, &conf);

    /* Bind: with dynconf_enabled=0, dynconf_snapshot must NOT be allocated */
    test_bind_request_snapshot(&r, &tctx, &global_snap, &early_eff, &conf);

    TEST_ASSERT(tctx.dynconf_snapshot == NULL,
                "ctx dynconf_snapshot is NULL when dynconf_enabled=0");

    TEST_ASSERT(tctx.effective_conf != NULL,
                "ctx effective_conf allocated (always)");

    /* Effective conf must reflect live conf, NOT the global snapshot */
    TEST_ASSERT(tctx.effective_conf->prune_noise == 0,
                "effective prune_noise from conf (0), not snapshot (1)");
    TEST_ASSERT(tctx.effective_conf->log_verbosity
                    == NGX_HTTP_MARKDOWN_LOG_ERROR,
                "effective log_verbosity from conf (ERROR), not snapshot (DEBUG)");
    TEST_ASSERT(tctx.effective_conf->memory_budget == 1 * 1024 * 1024,
                "effective memory_budget from conf (1M), not snapshot (32M)");
    TEST_ASSERT(tctx.effective_conf->streaming_budget == 512 * 1024,
                "effective streaming_budget from conf (512K), not snapshot (16M)");

    free(tctx.effective_conf);

    TEST_PASS("dynconf snapshot not consumed when dynconf_enabled=0");
}

static void
test_dynconf_start_stop_symbols(void)
{
    ngx_cycle_t                          cycle;
    ngx_http_markdown_conf_t             conf;
    ngx_str_t                            path;
    ngx_int_t                            rc;

    TEST_SUBSECTION("dynconf start/stop symbol coverage");

    ngx_memzero(&cycle, sizeof(cycle));
    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&path, sizeof(path));

    cycle.pool = &g_pool;
    cycle.log = &g_log;

    rc = ngx_http_markdown_dynconf_start(NULL, &cycle, &path, &conf, &g_log);
    TEST_ASSERT(rc == NGX_OK, "NULL watcher should return NGX_OK");

    ngx_http_markdown_dynconf_stop(NULL, &g_log);
    TEST_PASS("dynconf start/stop symbols exercised");
}


int
main(void)
{
    TEST_SECTION("Effective Configuration View Tests");

    test_build_effective_conf_from_valid_snapshot();
    test_build_effective_conf_null_snapshot_falls_back_to_conf();
    test_build_effective_conf_invalid_snapshot_falls_back();
    test_effective_helpers_read_from_eff_when_present();
    test_effective_helpers_fall_back_when_eff_null();
    test_request_snapshot_consistency_after_conf_change();
    test_request_snapshot_consistency_with_dynconf_apply_snapshot();
    test_build_effective_conf_null_inputs();
    test_effective_helpers_edge_values();
    test_bind_request_snapshot_preserves_captured_snapshot();
    test_dynconf_snapshot_not_consumed_when_dynconf_disabled();
    test_dynconf_start_stop_symbols();

    printf("\nAll effective_conf consistency tests passed.\n");
    return 0;
}
