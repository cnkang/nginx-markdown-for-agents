/*
 * Test: header_plan_prepare_commit
 *
 * Validates the spec 48 two-phase prepare/commit header plan model:
 *   - prepare performs all fallible work without mutating headers_out
 *   - a prepare failure at op index N leaves r->headers_out unchanged
 *     (no partial mutation), regardless of N (1st, 2nd, 3rd op)
 *   - the commit phase performs ZERO pool allocation (allocation-free
 *     commit invariant)
 *   - fault injection (NGX_MARKDOWN_FAULT_INJECTION) can fail prepare at an
 *     arbitrary op index
 *
 * The test compiles the production source directly with its own NGINX
 * stubs (mirroring header_plan_apply_test.c) plus:
 *   - NGX_MARKDOWN_FAULT_INJECTION             (fault hook)
 *   - NGX_HTTP_MARKDOWN_HEADER_PLAN_COMMIT_HOOK (commit-begin hook)
 *
 * Coverage targets:
 *   ngx_http_markdown_header_plan.c prepare/commit phases
 */

#include "../include/test_common.h"

#include <ngx_config.h>
#include <ngx_core.h>

#define NGX_OK         0
#define NGX_ERROR     -1

#define NGX_MARKDOWN_FAULT_INJECTION 1
#define NGX_HTTP_MARKDOWN_HEADER_PLAN_COMMIT_HOOK 1

#undef NGX_LOG_ERR
#define NGX_LOG_ERR    4
#define NGX_LOG_DEBUG  8
#define NGX_LOG_DEBUG_HTTP  NGX_LOG_DEBUG

struct ngx_log_s {
    int dummy;
};

typedef struct ngx_connection_s ngx_connection_t;

struct ngx_connection_s {
    ngx_log_t  *log;
};

/* ── Pool stub with allocation accounting ─────────────────────── */

static u_char g_pool_buf[1024 * 256];
static size_t g_pool_offset;
static int    g_palloc_fail_count;
static int    g_palloc_calls;
static int    g_in_commit;
static int    g_commit_allocs;

typedef struct ngx_pool_s {
    int dummy;
} ngx_pool_t;

static ngx_pool_t g_pool;

static void
pool_init(void)
{
    memset(g_pool_buf, 0, sizeof(g_pool_buf));
    g_pool_offset = 0;
}

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    void   *p;
    size_t  aligned;
    (void) pool;

    g_palloc_calls++;
    if (g_in_commit) {
        /* spec 48: commit must perform no allocation. */
        g_commit_allocs++;
    }

    if (g_palloc_fail_count > 0) {
        g_palloc_fail_count--;
        if (g_palloc_fail_count == 0) {
            return NULL;
        }
    }

    aligned = (size + 7) & ~(size_t) 7;
    if (g_pool_offset + aligned > sizeof(g_pool_buf)) {
        return NULL;
    }
    p = g_pool_buf + g_pool_offset;
    g_pool_offset += aligned;
    return p;
}

void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    return ngx_palloc(pool, size);
}

/* ── Header list stub ─────────────────────────────────────────── */

#define MAX_HEADERS 64

typedef struct {
    ngx_str_t    key;
    ngx_str_t    value;
    ngx_uint_t   hash;
} ngx_table_elt_t;

typedef struct ngx_list_part_s {
    void                    *elts;
    ngx_uint_t               nelts;
    struct ngx_list_part_s  *next;
} ngx_list_part_t;

typedef struct {
    ngx_list_part_t  part;
    ngx_list_part_t *last;
    size_t           size;
    ngx_uint_t       nalloc;
} ngx_list_t;

static ngx_table_elt_t g_headers[MAX_HEADERS];
static ngx_uint_t      g_header_count;

typedef struct {
    ngx_list_t  headers;
} ngx_http_headers_out_t;

typedef struct ngx_http_request_s {
    ngx_connection_t           *connection;
    ngx_pool_t                 *pool;
    ngx_http_headers_out_t      headers_out;
    struct ngx_http_request_s  *main;
} ngx_http_request_t;

static ngx_log_t        g_log;
static ngx_connection_t g_conn = { &g_log };
static ngx_http_request_t g_request;

ngx_table_elt_t *
ngx_list_push(ngx_list_t *list)
{
    ngx_table_elt_t  *h;

    if (g_header_count >= MAX_HEADERS) {
        return NULL;
    }
    h = &g_headers[g_header_count];
    g_header_count++;
    list->part.nelts = g_header_count;
    return h;
}

ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    return (ngx_int_t) strncasecmp((char *) s1, (char *) s2, n);
}

#define ngx_memcpy memcpy

#undef ngx_log_error
#define ngx_log_error(level, log, err, ...) (void) 0
#define ngx_log_debug1(level, log, err, fmt, arg) (void) 0

#include "markdown_converter.h"

static int g_plan_freed;

void
markdown_header_plan_free(FFIHeaderPlan *plan)
{
    (void) plan;
    g_plan_freed = 1;
}

/* Commit-begin hook: flip the in-commit flag so the pool stub can detect
 * any allocation performed during commit. */
void
ngx_http_markdown_plan_test_commit_begin_hook(void)
{
    g_in_commit = 1;
}

#include "ngx_http_markdown_header_plan.c"

#define apply_header_plan ngx_http_markdown_apply_header_plan

/* ── Helpers ──────────────────────────────────────────────────── */

static ngx_table_elt_t *
find_active(const char *name)
{
    size_t  len = strlen(name);

    for (ngx_uint_t i = 0; i < g_header_count; i++) {
        if (g_headers[i].hash == 0) {
            continue;
        }
        if (g_headers[i].key.len == len
            && strncasecmp((char *) g_headers[i].key.data, name, len) == 0)
        {
            return &g_headers[i];
        }
    }
    return NULL;
}

static ngx_uint_t
count_active(const char *name)
{
    size_t      len = strlen(name);
    ngx_uint_t  c = 0;

    for (ngx_uint_t i = 0; i < g_header_count; i++) {
        if (g_headers[i].hash == 0) {
            continue;
        }
        if (g_headers[i].key.len == len
            && strncasecmp((char *) g_headers[i].key.data, name, len) == 0)
        {
            c++;
        }
    }
    return c;
}

static void
setup_request(void)
{
    pool_init();
    memset(g_headers, 0, sizeof(g_headers));
    g_header_count = 0;
    g_plan_freed = 0;
    g_palloc_fail_count = 0;
    g_palloc_calls = 0;
    g_in_commit = 0;
    g_commit_allocs = 0;
    ngx_http_markdown_plan_fault_op = -1;

    g_request.connection = &g_conn;
    g_request.pool = &g_pool;
    g_request.headers_out.headers.part.elts = g_headers;
    g_request.headers_out.headers.part.nelts = 0;
    g_request.headers_out.headers.part.next = NULL;
    g_request.main = &g_request;
}

static void
add_existing_header(const char *name, const char *value)
{
    ngx_table_elt_t  *h = &g_headers[g_header_count];

    h->key.data = (u_char *) name;
    h->key.len = strlen(name);
    h->value.data = (u_char *) value;
    h->value.len = strlen(value);
    h->hash = 1;
    g_header_count++;
    g_request.headers_out.headers.part.nelts = g_header_count;
}

/* A representative 3-op plan: DELETE ETag, SET new X-Added, DELETE_ALL
 * Content-Length.  Used by the no-partial-mutation tests. */
static FFIHeaderEntry g_three_ops[3] = {
    { NGX_HTTP_MARKDOWN_PLAN_OP_DELETE,
      (const uint8_t *) "ETag", 4, NULL, 0 },
    { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
      (const uint8_t *) "X-Added", 7,
      (const uint8_t *) "yes", 3 },
    { NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL,
      (const uint8_t *) "Content-Length", 14, NULL, 0 },
};

static void
seed_three_op_headers(void)
{
    add_existing_header("ETag", "\"abc\"");
    add_existing_header("Content-Length", "1024");
    add_existing_header("Content-Length", "2048");
}

/* ── Tests ────────────────────────────────────────────────────── */

static void
test_prepare_does_not_mutate_on_fault(void)
{
    FFIHeaderPlan        plan;
    FFIHeaderPlanHandle  handle;

    TEST_SUBSECTION("prepare fault leaves headers_out unchanged");

    setup_request();
    seed_three_op_headers();

    plan.handle = &handle;
    plan.entries = g_three_ops;
    plan.count = 3;

    /* Inject a fault before the FIRST op: nothing should be prepared. */
    ngx_http_markdown_plan_fault_op = 0;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "fault at op 0 must return NGX_ERROR");
    TEST_ASSERT(find_active("ETag") != NULL,
                "ETag must remain active after aborted prepare");
    TEST_ASSERT(find_active("X-Added") == NULL,
                "X-Added must not appear after aborted prepare");
    TEST_ASSERT(count_active("Content-Length") == 2,
                "both Content-Length entries must remain active");
    TEST_ASSERT(g_commit_allocs == 0, "commit phase must not run");
    TEST_ASSERT(g_plan_freed == 1, "plan must be freed on error");

    TEST_PASS("Prepare fault leaves headers_out untouched");
}

static void
test_no_partial_mutation_second_op(void)
{
    FFIHeaderPlan        plan;
    FFIHeaderPlanHandle  handle;

    TEST_SUBSECTION("2nd op prepare failure -> no partial mutation");

    setup_request();
    seed_three_op_headers();

    plan.handle = &handle;
    plan.entries = g_three_ops;
    plan.count = 3;

    /* Fault at op index 1 (the SET): op 0 (DELETE ETag) was prepared but
     * NOT committed, so ETag must still be active. */
    ngx_http_markdown_plan_fault_op = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "fault at op 1 must return NGX_ERROR");
    TEST_ASSERT(find_active("ETag") != NULL,
                "ETag must remain active (op 0 prepared, not committed)");
    TEST_ASSERT(find_active("X-Added") == NULL,
                "X-Added must not be present");
    TEST_ASSERT(count_active("Content-Length") == 2,
                "Content-Length entries must remain active");
    TEST_ASSERT(g_commit_allocs == 0, "commit phase must not run");

    TEST_PASS("2nd op prepare failure causes no partial mutation");
}

static void
test_no_partial_mutation_third_op(void)
{
    FFIHeaderPlan        plan;
    FFIHeaderPlanHandle  handle;

    TEST_SUBSECTION("3rd op prepare failure -> no partial mutation");

    setup_request();
    seed_three_op_headers();

    plan.handle = &handle;
    plan.entries = g_three_ops;
    plan.count = 3;

    /* Fault at op index 2 (DELETE_ALL): ops 0 and 1 were prepared (a new
     * inert slot was pushed for X-Added) but nothing committed. */
    ngx_http_markdown_plan_fault_op = 2;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "fault at op 2 must return NGX_ERROR");
    TEST_ASSERT(find_active("ETag") != NULL,
                "ETag must remain active");
    TEST_ASSERT(find_active("X-Added") == NULL,
                "pushed X-Added slot must stay inert (hash==0)");
    TEST_ASSERT(count_active("Content-Length") == 2,
                "Content-Length entries must remain active");
    TEST_ASSERT(g_commit_allocs == 0, "commit phase must not run");

    TEST_PASS("3rd op prepare failure causes no partial mutation");
}

static void
test_alloc_failure_mid_prepare_no_mutation(void)
{
    FFIHeaderPlan        plan;
    FFIHeaderPlanHandle  handle;

    TEST_SUBSECTION("allocation failure mid-prepare -> no mutation");

    setup_request();
    seed_three_op_headers();

    plan.handle = &handle;
    plan.entries = g_three_ops;
    plan.count = 3;

    /*
     * Allocation order: [1] prepared array, [2] X-Added key copy,
     * [3] X-Added value copy.  Fail the value copy (3rd alloc) so the
     * SET op fails mid-prepare after a slot was already pushed.
     */
    g_palloc_fail_count = 3;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "allocation failure must return NGX_ERROR");
    TEST_ASSERT(find_active("ETag") != NULL,
                "ETag must remain active after allocation failure");
    TEST_ASSERT(find_active("X-Added") == NULL,
                "partially prepared X-Added must stay inert");
    TEST_ASSERT(count_active("Content-Length") == 2,
                "Content-Length entries must remain active");
    TEST_ASSERT(g_commit_allocs == 0, "commit phase must not run");

    TEST_PASS("Allocation failure mid-prepare causes no mutation");
}

static void
test_commit_is_allocation_free(void)
{
    FFIHeaderPlan        plan;
    FFIHeaderPlanHandle  handle;

    TEST_SUBSECTION("commit phase performs zero allocation");

    setup_request();
    seed_three_op_headers();

    plan.handle = &handle;
    plan.entries = g_three_ops;
    plan.count = 3;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "full plan should commit successfully");
    TEST_ASSERT(g_in_commit == 1, "commit-begin hook must have fired");
    TEST_ASSERT(g_commit_allocs == 0,
                "commit phase must perform no pool allocation");

    /* Confirm the commit actually applied every prepared mutation. */
    TEST_ASSERT(find_active("ETag") == NULL, "ETag must be deleted");
    TEST_ASSERT(find_active("X-Added") != NULL, "X-Added must be set");
    TEST_ASSERT(count_active("Content-Length") == 0,
                "all Content-Length entries must be deleted");
    TEST_ASSERT(g_plan_freed == 1, "plan must be freed after commit");

    TEST_PASS("Commit phase is allocation-free");
}

static void
test_fault_injection_disabled_by_default(void)
{
    FFIHeaderPlan        plan;
    FFIHeaderPlanHandle  handle;

    TEST_SUBSECTION("fault injection off by default (op_index = -1)");

    setup_request();
    seed_three_op_headers();

    plan.handle = &handle;
    plan.entries = g_three_ops;
    plan.count = 3;

    /* fault_op left at -1 by setup_request: plan must succeed. */
    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "with fault injection disabled the plan should succeed");

    TEST_PASS("Fault injection disabled leaves normal behavior");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("header_plan_prepare_commit Tests\n");
    printf("========================================\n");

    test_prepare_does_not_mutate_on_fault();
    test_no_partial_mutation_second_op();
    test_no_partial_mutation_third_op();
    test_alloc_failure_mid_prepare_no_mutation();
    test_commit_is_allocation_free();
    test_fault_injection_disabled_by_default();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
