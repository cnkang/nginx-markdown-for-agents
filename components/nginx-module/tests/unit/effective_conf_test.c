/*
 * Test: effective_conf consistency (static-only projection)
 *
 * The dynamic-configuration overlay was removed in 0.9.2 (LTS-R006/R007).
 * The effective-configuration projection, the per-request accessor helpers,
 * and the bind-once request seam are retained and now project purely from
 * the static (merged/inherited) configuration.  These tests protect that
 * retained shared logic (LTS-R007 "no dead layers"; AGENTS.md Rules 34/45).
 */

#include "../include/test_common.h"

#include <ctype.h>
#include <stdarg.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_OK
#define NGX_OK       0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR   -1
#endif
#ifndef NGX_HTTP_TOO_MANY_REQUESTS
#define NGX_HTTP_TOO_MANY_REQUESTS 429
#endif
#ifndef NGX_HTTP_SERVICE_UNAVAILABLE
#define NGX_HTTP_SERVICE_UNAVAILABLE 503
#endif
#ifndef NGX_HTTP_BAD_GATEWAY
#define NGX_HTTP_BAD_GATEWAY 502
#endif

typedef intptr_t ngx_err_t;

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

#define NGX_CONF_UNSET       (-1)
#define NGX_CONF_UNSET_UINT  (ngx_uint_t) -1
#define NGX_CONF_UNSET_SIZE  (size_t) -1

#define NGX_HTTP_MARKDOWN_LOG_ERROR  0
#define NGX_HTTP_MARKDOWN_LOG_WARN   1
#define NGX_HTTP_MARKDOWN_LOG_INFO   2
#define NGX_HTTP_MARKDOWN_LOG_DEBUG  3

#include "../../src/ngx_http_markdown_effective_conf_impl.h"


/*
 * build_effective_conf projects every field from the static conf.
 */
static void
test_build_effective_conf_projects_static_conf(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("build_effective_conf projects from static conf");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&eff, sizeof(eff));

    conf.enabled = 1;
    conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
    conf.advanced.prune_noise = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    conf.error_status = NGX_HTTP_SERVICE_UNAVAILABLE;
    conf.limits.conversion_memory = 4 * 1024 * 1024;
    conf.stream.budget = 2 * 1024 * 1024;
    conf.advanced.static_block_mask = NGX_HTTP_MARKDOWN_BLOCK_PRUNE_NOISE;

    ngx_http_markdown_build_effective_conf(&eff, &conf);

    TEST_ASSERT(eff.enabled == 1, "effective enabled from conf");
    TEST_ASSERT(eff.enabled_source == NGX_HTTP_MARKDOWN_ENABLED_STATIC,
                "effective enabled_source from conf");
    TEST_ASSERT(eff.prune_noise == 1, "effective prune_noise from conf");
    TEST_ASSERT(eff.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "effective log_verbosity from conf");
    TEST_ASSERT(eff.error_policy == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT,
                "effective error_policy from conf");
    TEST_ASSERT(eff.error_status == NGX_HTTP_SERVICE_UNAVAILABLE,
                "effective error_status from conf");
    TEST_ASSERT(eff.memory_budget == 4 * 1024 * 1024,
                "effective memory_budget from conf");
    TEST_ASSERT(eff.streaming_budget == 2 * 1024 * 1024,
                "effective streaming_budget from conf");
    TEST_ASSERT(eff.block_mask == NGX_HTTP_MARKDOWN_BLOCK_PRUNE_NOISE,
                "effective block_mask copied for diagnostics");

    TEST_PASS("build_effective_conf projects from static conf");
}


/*
 * Every field's provenance is STATIC, except a complex-value filter, whose
 * provenance is REQUEST_VARIABLE (resolved later at is_enabled time).
 */
static void
test_build_effective_conf_provenance(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("build_effective_conf provenance is static (or request-var)");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&eff, sizeof(eff));

    conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_STATIC;
    ngx_http_markdown_build_effective_conf(&eff, &conf);
    TEST_ASSERT(eff.filter_provenance == NGX_HTTP_MARKDOWN_PROVENANCE_STATIC,
                "static filter provenance is STATIC");
    TEST_ASSERT(
        eff.prune_noise_provenance == NGX_HTTP_MARKDOWN_PROVENANCE_STATIC,
        "prune_noise provenance is STATIC");
    TEST_ASSERT(
        eff.error_policy_provenance == NGX_HTTP_MARKDOWN_PROVENANCE_STATIC,
        "error_policy provenance is STATIC");

    ngx_memzero(&eff, sizeof(eff));
    conf.enabled_source = NGX_HTTP_MARKDOWN_ENABLED_COMPLEX;
    ngx_http_markdown_build_effective_conf(&eff, &conf);
    TEST_ASSERT(
        eff.filter_provenance
            == NGX_HTTP_MARKDOWN_PROVENANCE_REQUEST_VARIABLE,
        "complex-value filter provenance is REQUEST_VARIABLE");

    TEST_PASS("build_effective_conf provenance is static (or request-var)");
}


/*
 * effective_* helpers read from eff when present.
 */
static void
test_effective_helpers_read_from_eff_when_present(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("effective_* helpers read from eff when present");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&eff, sizeof(eff));

    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    conf.enabled = 0;
    conf.enabled_source = 3;
    conf.advanced.prune_noise = 0;
    conf.limits.conversion_memory = 1024;
    conf.stream.budget = 512;

    eff.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    eff.enabled = 1;
    eff.enabled_source = 7;
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
        ngx_http_markdown_effective_enabled(&eff, &conf) == 1,
        "effective_enabled reads from eff");
    TEST_ASSERT(
        ngx_http_markdown_effective_enabled_source(&eff, &conf) == 7,
        "effective_enabled_source reads from eff");

    TEST_PASS("effective_* helpers read from eff when present");
}


/*
 * effective_* helpers fall back to conf when eff is NULL.
 */
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
    conf.limits.conversion_memory = 4096;
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

    /* Both-NULL guard: accessors must not dereference a NULL conf. */
    TEST_ASSERT(
        ngx_http_markdown_effective_log_verbosity(NULL, NULL)
            == NGX_HTTP_MARKDOWN_LOG_ERROR,
        "effective_log_verbosity guards NULL conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_prune_noise(NULL, NULL) == 0,
        "effective_prune_noise guards NULL conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_memory_budget(NULL, NULL) == 0,
        "effective_memory_budget guards NULL conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_streaming_budget(NULL, NULL) == 0,
        "effective_streaming_budget guards NULL conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_enabled(NULL, NULL) == 0,
        "effective_enabled guards NULL conf");
    TEST_ASSERT(
        ngx_http_markdown_effective_enabled_source(NULL, NULL)
            == NGX_HTTP_MARKDOWN_ENABLED_STATIC,
        "effective_enabled_source guards NULL conf");

    TEST_PASS("effective_* helpers fall back to conf when eff is NULL");
}


/*
 * effective error policy/status helpers (retained in filter_module.h).
 */
static void
test_effective_error_policy_and_status(void)
{
    ngx_http_markdown_conf_t          conf;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("effective error policy and status helpers");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&eff, sizeof(eff));

    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    conf.error_status = NGX_HTTP_BAD_GATEWAY;
    eff.error_policy = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    eff.error_status = NGX_HTTP_SERVICE_UNAVAILABLE;

    TEST_ASSERT(ngx_http_markdown_effective_error_policy(&eff, &conf)
                    == NGX_HTTP_MARKDOWN_ON_ERROR_REJECT,
                "effective error policy reads from eff");
    TEST_ASSERT(ngx_http_markdown_effective_error_status(&eff, &conf)
                    == NGX_HTTP_SERVICE_UNAVAILABLE,
                "effective error status reads from eff");
    TEST_ASSERT(ngx_http_markdown_effective_error_policy(NULL, &conf)
                    == NGX_HTTP_MARKDOWN_ON_ERROR_PASS,
                "error policy falls back when effective view is unavailable");
    TEST_ASSERT(ngx_http_markdown_effective_error_status(NULL, &conf)
                    == NGX_HTTP_BAD_GATEWAY,
                "error status falls back when effective view is unavailable");

    TEST_PASS("effective error policy and status helpers");
}


/*
 * The bound effective view stays consistent for the request even after the
 * live conf changes (bind-once invariant, Rules 34/45).
 */
static void
test_effective_view_consistency_after_conf_change(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("effective view consistency after live conf change");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&eff, sizeof(eff));

    conf.enabled = 1;
    conf.advanced.prune_noise = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    conf.limits.conversion_memory = 4 * 1024 * 1024;
    conf.stream.budget = 2 * 1024 * 1024;

    ngx_http_markdown_build_effective_conf(&eff, &conf);

    /* Mutate the live conf after the effective view was captured. */
    conf.advanced.prune_noise = 0;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_ERROR;
    conf.limits.conversion_memory = 16 * 1024 * 1024;
    conf.stream.budget = 8 * 1024 * 1024;

    TEST_ASSERT(eff.prune_noise == 1,
                "captured effective prune_noise unchanged by live conf");
    TEST_ASSERT(eff.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG,
                "captured effective log_verbosity unchanged by live conf");
    TEST_ASSERT(eff.memory_budget == 4 * 1024 * 1024,
                "captured effective memory_budget unchanged by live conf");
    TEST_ASSERT(eff.streaming_budget == 2 * 1024 * 1024,
                "captured effective streaming_budget unchanged by live conf");

    TEST_ASSERT(
        ngx_http_markdown_effective_prune_noise(&eff, &conf) == 1,
        "effective_prune_noise helper returns captured value, not live conf");

    TEST_PASS("effective view consistency after live conf change");
}


/*
 * build_effective_conf must not crash on NULL inputs.
 */
static void
test_build_effective_conf_null_inputs(void)
{
    ngx_http_markdown_effective_conf_t eff;
    ngx_http_markdown_conf_t           conf;

    TEST_SUBSECTION("build_effective_conf with NULL inputs does not crash");

    ngx_memzero(&eff, sizeof(eff));
    ngx_memzero(&conf, sizeof(conf));

    ngx_http_markdown_build_effective_conf(NULL, NULL);
    ngx_http_markdown_build_effective_conf(NULL, &conf);
    ngx_http_markdown_build_effective_conf(&eff, NULL);

    TEST_PASS("build_effective_conf with NULL inputs does not crash");
}


/*
 * effective_* helpers with edge values (zero, max).
 */
static void
test_effective_helpers_edge_values(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_http_markdown_effective_conf_t eff;

    TEST_SUBSECTION("effective_* helpers with edge values (zero, max)");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&eff, sizeof(eff));

    conf.policy.log_verbosity = 0;
    conf.limits.conversion_memory = 0;
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


/* Simulated request context slots used by the shared production binder. */
typedef struct {
    ngx_http_markdown_effective_conf_t   *effective_conf;
    ngx_http_markdown_effective_conf_t    effective_conf_storage;
} test_ctx_t;


/*
 * bind_request_snapshot copies the early effective view by value into
 * caller-owned storage and points the effective slot at it (bind-once seam).
 */
static void
test_bind_request_snapshot_binds_once(void)
{
    ngx_http_markdown_conf_t            conf;
    ngx_http_markdown_effective_conf_t  early_eff;
    test_ctx_t                          tctx;
    ngx_http_request_t                  r;
    ngx_connection_t                    conn;
    ngx_log_t                           log;

    TEST_SUBSECTION("bind_request_snapshot copies the effective view once");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&early_eff, sizeof(early_eff));
    ngx_memzero(&tctx, sizeof(tctx));
    ngx_memzero(&r, sizeof(r));
    ngx_memzero(&conn, sizeof(conn));
    ngx_memzero(&log, sizeof(log));

    r.connection = &conn;
    conn.log = &log;

    conf.enabled = 1;
    conf.advanced.prune_noise = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    conf.limits.conversion_memory = 4 * 1024 * 1024;
    conf.stream.budget = 2 * 1024 * 1024;

    ngx_http_markdown_build_effective_conf(&early_eff, &conf);

    ngx_http_markdown_bind_request_snapshot(
        &r, &conf, &early_eff,
        &tctx.effective_conf_storage, &tctx.effective_conf);

    TEST_ASSERT(tctx.effective_conf == &tctx.effective_conf_storage,
                "effective slot points at caller-owned storage");
    TEST_ASSERT(tctx.effective_conf->prune_noise == 1,
                "bound effective prune_noise is from early_eff (1)");
    TEST_ASSERT(tctx.effective_conf->log_verbosity
                    == NGX_HTTP_MARKDOWN_LOG_INFO,
                "bound effective log_verbosity is from early_eff (INFO)");
    TEST_ASSERT(tctx.effective_conf->memory_budget == 4 * 1024 * 1024,
                "bound effective memory_budget is from early_eff (4M)");

    /* Mutating early_eff after the bind must not affect the bound copy. */
    early_eff.prune_noise = 0;
    early_eff.memory_budget = 99;
    TEST_ASSERT(tctx.effective_conf->prune_noise == 1,
                "bound copy is independent of the source view");
    TEST_ASSERT(tctx.effective_conf->memory_budget == 4 * 1024 * 1024,
                "bound copy memory_budget unchanged after source mutation");

    TEST_PASS("bind_request_snapshot copies the effective view once");
}


/*
 * bind_request_snapshot must be NULL-safe.
 */
static void
test_bind_request_snapshot_null_safe(void)
{
    ngx_http_markdown_conf_t            conf;
    ngx_http_markdown_effective_conf_t  early_eff;
    test_ctx_t                          tctx;
    ngx_http_request_t                  r;

    TEST_SUBSECTION("bind_request_snapshot is NULL-safe");

    ngx_memzero(&conf, sizeof(conf));
    ngx_memzero(&early_eff, sizeof(early_eff));
    ngx_memzero(&tctx, sizeof(tctx));
    ngx_memzero(&r, sizeof(r));

    /* NULL request must be a no-op (no slot bound). */
    ngx_http_markdown_bind_request_snapshot(
        NULL, &conf, &early_eff,
        &tctx.effective_conf_storage, &tctx.effective_conf);
    TEST_ASSERT(tctx.effective_conf == NULL,
                "NULL request leaves the effective slot unbound");

    TEST_PASS("bind_request_snapshot is NULL-safe");
}


int
main(void)
{
    TEST_SECTION("Effective Configuration View Tests");

    test_build_effective_conf_projects_static_conf();
    test_build_effective_conf_provenance();
    test_effective_helpers_read_from_eff_when_present();
    test_effective_helpers_fall_back_when_eff_null();
    test_effective_error_policy_and_status();
    test_effective_view_consistency_after_conf_change();
    test_build_effective_conf_null_inputs();
    test_effective_helpers_edge_values();
    test_bind_request_snapshot_binds_once();
    test_bind_request_snapshot_null_safe();

    printf("\nAll effective_conf consistency tests passed.\n");
    return 0;
}
