/*
 * Test: header_plan_apply
 *
 * Validates the header plan atomic application logic:
 *   - NULL/empty plan handling
 *   - SET operation (new header + overwrite existing)
 *   - DELETE operation (existing + non-existing)
 *   - MODIFY operation (existing + non-existing)
 *   - Rollback on failure
 *   - Max entries limit enforcement
 *   - Unknown op_type handling
 *
 * Coverage targets:
 *   ngx_http_markdown_header_plan.c (apply_header_plan, plan_find_header,
 *   plan_apply_set, plan_apply_delete, plan_apply_modify, plan_rollback)
 */

#include "../include/test_common.h"

#include <ngx_config.h>
#include <ngx_core.h>

#define NGX_OK         0
#define NGX_ERROR     -1

#define NGX_HTTP_MARKDOWN_HEADER_PLAN_TEST_HOOKS 1

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

/* Pool stub */
static u_char g_pool_buf[1024 * 256];
static size_t g_pool_offset;
static int g_palloc_fail_count;
static int g_delete_all_visitor_fail_count;

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
    void *p;
    size_t aligned;
    (void) pool;
    if (g_palloc_fail_count > 0) {
        g_palloc_fail_count--;
        if (g_palloc_fail_count == 0) {
            return NULL;
        }
    }
    aligned = (size + 7) & ~(size_t)7;
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

/* Header list stub */
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
static ngx_uint_t g_header_count;

typedef struct {
    ngx_list_t  headers;
} ngx_http_headers_out_t;

typedef struct ngx_http_request_s {
    ngx_connection_t       *connection;
    ngx_pool_t             *pool;
    ngx_http_headers_out_t  headers_out;
    struct ngx_http_request_s *main;
} ngx_http_request_t;

static ngx_log_t g_log;
static ngx_connection_t g_conn = { &g_log };
static ngx_http_request_t g_request;

/* Stub ngx_list_push */
ngx_table_elt_t *
ngx_list_push(ngx_list_t *list)
{
    if (g_header_count >= MAX_HEADERS) {
        return NULL;
    }
    ngx_table_elt_t *h = &g_headers[g_header_count];
    g_header_count++;
    list->part.nelts = g_header_count;
    return h;
}

/* Stub ngx_strncasecmp */
ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    return (ngx_int_t) strncasecmp((char *)s1, (char *)s2, n);
}

/* Stub ngx_memcpy */
#define ngx_memcpy memcpy

/* Stub ngx_log_error / ngx_log_debug1 */
#undef ngx_log_error
#define ngx_log_error(level, log, err, ...) (void)0
#define ngx_log_debug1(level, log, err, fmt, arg) (void)0

#include "markdown_converter.h"

static int g_plan_freed;

void
markdown_header_plan_free(FFIHeaderPlan *plan)
{
    (void) plan;
    g_plan_freed = 1;
}

/* ── Undo record ──────────────────────────────────────────────── */

typedef struct {
    uint8_t          op_type;
    ngx_table_elt_t *header;
    ngx_uint_t       orig_hash;
    ngx_str_t        orig_value;
} plan_undo_t;

/* ── Helper: find header by name ──────────────────────────────── */

static ngx_table_elt_t *
find_header(const u_char *name, size_t name_len)
{

    for (ngx_uint_t i = 0; i < g_header_count; i++) {
        if (g_headers[i].hash == 0) {
            continue;
        }
        if (g_headers[i].key.len == name_len
            && strncasecmp((char *)g_headers[i].key.data,
                           (char *)name, name_len) == 0)
        {
            return &g_headers[i];
        }
    }
    return NULL;
}


static ngx_uint_t
count_active_headers(const u_char *name, size_t name_len)
{
    ngx_uint_t count;

    count = 0;

    for (ngx_uint_t i = 0; i < g_header_count; i++) {
        if (g_headers[i].hash == 0) {
            continue;
        }
        if (g_headers[i].key.len == name_len
            && strncasecmp((char *) g_headers[i].key.data,
                           (char *) name, name_len) == 0)
        {
            count++;
        }
    }

    return count;
}

/* ── Setup helpers ────────────────────────────────────────────── */

static void
setup_request(void)
{
    pool_init();
    memset(g_headers, 0, sizeof(g_headers));
    g_header_count = 0;
    g_plan_freed = 0;
    g_palloc_fail_count = 0;
    g_delete_all_visitor_fail_count = 0;

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
    ngx_table_elt_t *h = &g_headers[g_header_count];
    h->key.data = (u_char *) name;
    h->key.len = strlen(name);
    h->value.data = (u_char *) value;
    h->value.len = strlen(value);
    h->hash = 1;
    g_header_count++;
    g_request.headers_out.headers.part.nelts = g_header_count;
}

#ifdef NGX_HTTP_MARKDOWN_HEADER_PLAN_TEST_HOOKS
static ngx_int_t
ngx_http_markdown_plan_test_delete_all_visitor_hook(ngx_table_elt_t *h,
    void *ctx)
{
    (void) h;
    (void) ctx;

    if (g_delete_all_visitor_fail_count > 0) {
        g_delete_all_visitor_fail_count--;
        if (g_delete_all_visitor_fail_count == 0) {
            return NGX_ERROR;
        }
    }

    return NGX_OK;
}
#endif

#define apply_header_plan ngx_http_markdown_apply_header_plan
#include "ngx_http_markdown_header_plan.c"


/* ── Tests ─────────────────────────────────────────────────────── */

static void
test_null_plan(void)
{
    TEST_SUBSECTION("NULL plan");

    setup_request();
    TEST_ASSERT(apply_header_plan(&g_request, NULL) == NGX_OK,
                "NULL plan should return NGX_OK");
    TEST_ASSERT(g_plan_freed == 0, "NULL plan should not call free");

    TEST_PASS("NULL plan handled correctly");
}

static void
test_empty_plan(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;

    TEST_SUBSECTION("empty plan");

    setup_request();
    plan.handle = &handle;
    plan.entries = NULL;
    plan.count = 0;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "empty plan should return NGX_OK");
    TEST_ASSERT(g_plan_freed == 1, "empty plan should free handle");

    TEST_PASS("Empty plan handled correctly");
}

static void
test_null_request(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
        (const uint8_t *)"X-Test", 6,
        (const uint8_t *)"val", 3 };

    TEST_SUBSECTION("NULL request");

    setup_request();
    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(NULL, &plan) == NGX_ERROR,
                "NULL request should return NGX_ERROR");
    TEST_ASSERT(g_plan_freed == 1, "should free plan on error");

    TEST_PASS("NULL request handled correctly");
}

static void
test_exceed_max_entries(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { 0, (const uint8_t *)"X", 1,
        (const uint8_t *)"v", 1 };

    TEST_SUBSECTION("exceed max entries");

    setup_request();
    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES + 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "exceeding max entries should return NGX_ERROR");
    TEST_ASSERT(g_plan_freed == 1, "should free plan on error");

    TEST_PASS("Max entries limit enforced");
}

static void
test_set_new_header(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
        (const uint8_t *)"X-Test-Header", 13,
        (const uint8_t *)"some-value", 10 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("SET new header");

    setup_request();
    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "SET new header should succeed");

    h = find_header((const u_char *)"X-Test-Header", 13);
    TEST_ASSERT(h != NULL, "header should exist after SET");
    TEST_ASSERT(h->hash == 1, "header hash should be 1");
    TEST_ASSERT(h->value.len == 10, "value length should be 10");
    TEST_ASSERT(memcmp(h->value.data, "some-value", 10) == 0,
                "value should be some-value");

    TEST_PASS("SET new header correct");
}

static void
test_set_overwrite_existing(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
        (const uint8_t *)"X-Test-Header", 13,
        (const uint8_t *)"new-value", 9 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("SET overwrite existing header");

    setup_request();
    add_existing_header("X-Test-Header", "old-value");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "SET overwrite should succeed");

    h = find_header((const u_char *)"X-Test-Header", 13);
    TEST_ASSERT(h != NULL, "header should still exist");
    TEST_ASSERT(h->value.len == 9, "value length should be updated");
    TEST_ASSERT(memcmp(h->value.data, "new-value", 9) == 0,
                "value should be updated to new-value");

    TEST_PASS("SET overwrite existing correct");
}

static void
test_set_new_header_value_allocation_failure_cleans_up(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
        (const uint8_t *)"X-Test-Header", 13,
        (const uint8_t *)"some-value", 10 };

    TEST_SUBSECTION("SET new header value allocation failure");

    setup_request();
    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;
    /* Fail the third allocation: undo array, header key, then header value. */
    g_palloc_fail_count = 3;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "SET new header should fail when value allocation fails");
    TEST_ASSERT(g_header_count == 1,
                "failed SET may leave a single invalidated list entry");
    TEST_ASSERT(g_headers[0].hash == 0,
                "failed SET must invalidate the partial header");
    TEST_ASSERT(count_active_headers((const u_char *)"X-Test-Header", 13) == 0,
                "failed SET must not leave an active header");

    TEST_PASS("SET new header failure cleans up partial state");
}

static void
test_set_overwrite_existing_value_allocation_failure(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
        (const uint8_t *)"X-Test-Header", 13,
        (const uint8_t *)"new-value", 9 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("SET overwrite value allocation failure");

    setup_request();
    add_existing_header("X-Test-Header", "old-value");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;
    /* Fail the second allocation: undo array, then the replacement value. */
    g_palloc_fail_count = 2;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "SET overwrite should fail when value allocation fails");

    h = find_header((const u_char *)"X-Test-Header", 13);
    TEST_ASSERT(h != NULL, "header should still exist after rollback");
    TEST_ASSERT(h->hash == 1, "header should remain active after rollback");
    TEST_ASSERT(h->value.len == 9, "value length should remain original");
    TEST_ASSERT(memcmp(h->value.data, "old-value", 9) == 0,
                "value should be restored to old-value");

    TEST_PASS("SET overwrite allocation failure rolls back");
}

static void
test_set_content_type_no_list_entry(void)
{
    /*
     * Regression (CMOD-1): a Content-Type SET must NOT push a list entry.
     * NGINX emits Content-Type from the dedicated headers_out.content_type
     * field; a list entry would duplicate the header on the wire. With no
     * pre-existing Content-Type list entry, the list must stay empty.
     */
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
        (const uint8_t *)"Content-Type", 12,
        (const uint8_t *)"text/markdown; charset=utf-8", 28 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("SET Content-Type does not push list entry");

    setup_request();
    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "Content-Type SET should succeed");
    TEST_ASSERT(g_header_count == 0,
                "Content-Type SET must not push a list entry");
    h = find_header((const u_char *)"Content-Type", 12);
    TEST_ASSERT(h == NULL,
                "no Content-Type list entry should be visible");

    TEST_PASS("Content-Type SET avoids duplicate list entry");
}

static void
test_set_content_type_invalidates_stale_entry(void)
{
    /*
     * Regression (CMOD-1): if a stale Content-Type already exists in the
     * list (e.g. a future upstream placed one there), the Content-Type SET
     * must invalidate it (hash=0) so only the dedicated field is emitted.
     */
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
        (const uint8_t *)"Content-Type", 12,
        (const uint8_t *)"text/markdown; charset=utf-8", 28 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("SET Content-Type invalidates stale list entry");

    setup_request();
    add_existing_header("Content-Type", "text/html");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "Content-Type SET should succeed");
    h = find_header((const u_char *)"Content-Type", 12);
    TEST_ASSERT(h == NULL,
                "stale Content-Type list entry must be invalidated");

    TEST_PASS("Content-Type SET invalidates stale list entry");
}


static void
test_set_content_type_invalidates_all_stale_entries(void)
{
    /*
     * Multiple stale Content-Type list entries must all be invalidated so
     * NGINX emits only the dedicated headers_out.content_type field.
     */
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
        (const uint8_t *)"Content-Type", 12,
        (const uint8_t *)"text/markdown; charset=utf-8", 28 };

    TEST_SUBSECTION("SET Content-Type invalidates all stale list entries");

    setup_request();
    add_existing_header("Content-Type", "text/html");
    add_existing_header("content-type", "application/xhtml+xml");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "Content-Type SET should succeed");
    TEST_ASSERT(count_active_headers((const u_char *)"Content-Type", 12) == 0,
                "all stale Content-Type list entries must be invalidated");

    TEST_PASS("Content-Type SET invalidates all stale list entries");
}


static void
test_set_content_type_rollback_restores_all_stale_entries(void)
{
    /*
     * The all-entry invalidation is still part of an atomic plan.  A later
     * failure must restore every stale list entry, not just the first.
     */
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entries[2] = {
        { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
          (const uint8_t *)"Content-Type", 12,
          (const uint8_t *)"text/markdown; charset=utf-8", 28 },
        { 99, (const uint8_t *)"X-Bad", 5, NULL, 0 }
    };

    TEST_SUBSECTION("SET Content-Type rollback restores all stale entries");

    setup_request();
    add_existing_header("Content-Type", "text/html");
    add_existing_header("content-type", "application/xhtml+xml");

    plan.handle = &handle;
    plan.entries = entries;
    plan.count = 2;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "unknown later op should fail the plan");
    TEST_ASSERT(count_active_headers((const u_char *)"Content-Type", 12) == 2,
                "rollback must restore all stale Content-Type entries");

    TEST_PASS("Content-Type rollback restores all stale entries");
}


static void
test_delete_existing(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_DELETE,
        (const uint8_t *)"ETag", 4, NULL, 0 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("DELETE existing header");

    setup_request();
    add_existing_header("ETag", "\"abc123\"");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "DELETE should succeed");

    h = find_header((const u_char *)"ETag", 4);
    TEST_ASSERT(h == NULL, "header should not be found after DELETE");

    TEST_PASS("DELETE existing header correct");
}

static void
test_delete_nonexistent(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_DELETE,
        (const uint8_t *)"X-Missing", 9, NULL, 0 };

    TEST_SUBSECTION("DELETE non-existent header");

    setup_request();

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "DELETE non-existent should succeed (no-op)");

    TEST_PASS("DELETE non-existent is no-op");
}

static void
test_modify_existing(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY,
        (const uint8_t *)"Content-Length", 14,
        (const uint8_t *)"42", 2 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("MODIFY existing header");

    setup_request();
    add_existing_header("Content-Length", "1024");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "MODIFY should succeed");

    h = find_header((const u_char *)"Content-Length", 14);
    TEST_ASSERT(h != NULL, "header should exist");
    TEST_ASSERT(h->value.len == 2, "value length should be 2");
    TEST_ASSERT(memcmp(h->value.data, "42", 2) == 0,
                "value should be 42");

    TEST_PASS("MODIFY existing header correct");
}

static void
test_modify_existing_value_allocation_failure(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY,
        (const uint8_t *)"Content-Length", 14,
        (const uint8_t *)"42", 2 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("MODIFY value allocation failure");

    setup_request();
    add_existing_header("Content-Length", "1024");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;
    /* Fail the second allocation: undo array, then the replacement value. */
    g_palloc_fail_count = 2;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "MODIFY should fail when value allocation fails");

    h = find_header((const u_char *)"Content-Length", 14);
    TEST_ASSERT(h != NULL, "header should still exist after rollback");
    TEST_ASSERT(h->hash == 1, "header should remain active after rollback");
    TEST_ASSERT(h->value.len == 4, "value length should remain original");
    TEST_ASSERT(memcmp(h->value.data, "1024", 4) == 0,
                "value should be restored to original");

    TEST_PASS("MODIFY allocation failure rolls back");
}

static void
test_modify_nonexistent(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY,
        (const uint8_t *)"X-Missing", 9,
        (const uint8_t *)"val", 3 };

    TEST_SUBSECTION("MODIFY non-existent header");

    setup_request();

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "MODIFY non-existent should succeed (no-op)");

    TEST_PASS("MODIFY non-existent is no-op");
}


static void
test_set_etag_placeholder_noop(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY,
        NULL, 0, NULL, 0 };

    TEST_SUBSECTION("set-etag-placeholder no-op");

    setup_request();

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "set-etag-placeholder should be accepted as no-op");
    TEST_ASSERT(g_header_count == 0,
                "set-etag-placeholder should not mutate headers");
    TEST_ASSERT(g_plan_freed == 1,
                "plan should be freed after placeholder no-op");

    TEST_PASS("Set-ETag placeholder no-op accepted");
}

static void
test_unknown_op_type_rollback(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entries[2] = {
        { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
          (const uint8_t *)"X-New", 5,
          (const uint8_t *)"val", 3 },
        { 99,  /* unknown op_type */
          (const uint8_t *)"X-Bad", 5,
          (const uint8_t *)"bad", 3 }
    };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("unknown op_type triggers rollback");

    setup_request();

    plan.handle = &handle;
    plan.entries = entries;
    plan.count = 2;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "unknown op_type should return NGX_ERROR");

    /* The first SET should have been rolled back */
    h = find_header((const u_char *)"X-New", 5);
    TEST_ASSERT(h == NULL, "rolled-back SET header should not be visible");
    TEST_ASSERT(g_plan_freed == 1, "plan should be freed after rollback");

    TEST_PASS("Unknown op_type rollback correct");
}

static void
test_delete_all_removes_duplicate_headers(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL,
        (const uint8_t *)"Content-Encoding", 16, NULL, 0 };

    TEST_SUBSECTION("DELETE_ALL removes duplicate headers");

    setup_request();
    add_existing_header("Content-Encoding", "gzip");
    add_existing_header("Content-Encoding", "br");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "DELETE_ALL should succeed");
    TEST_ASSERT(count_active_headers((const u_char *)"Content-Encoding",
                                     16) == 0,
                "all Content-Encoding headers must be invalidated");
    TEST_ASSERT(g_plan_freed == 1, "plan should be freed after success");

    TEST_PASS("DELETE_ALL removes duplicate headers");
}

static void
test_delete_all_visitor_failure_restores_duplicate_headers(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL,
        (const uint8_t *)"Content-Encoding", 16, NULL, 0 };

    TEST_SUBSECTION("DELETE_ALL visitor failure restores duplicate headers");

    setup_request();
    add_existing_header("Content-Encoding", "gzip");
    add_existing_header("Content-Encoding", "br");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;
    g_delete_all_visitor_fail_count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "DELETE_ALL should fail when visitor fails");
    TEST_ASSERT(count_active_headers((const u_char *)"Content-Encoding",
                                     16) == 2,
                "DELETE_ALL visitor failure must restore every header");

    TEST_PASS("DELETE_ALL visitor failure restores state");
}


static void
test_delete_all_rollback_restores_duplicate_headers(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entries[2] = {
        { NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL,
          (const uint8_t *)"Content-Encoding", 16, NULL, 0 },
        { 99, (const uint8_t *)"X-Bad", 5, NULL, 0 }
    };

    TEST_SUBSECTION("DELETE_ALL rollback restores duplicate headers");

    setup_request();
    add_existing_header("Content-Encoding", "gzip");
    add_existing_header("Content-Encoding", "br");

    plan.handle = &handle;
    plan.entries = entries;
    plan.count = 2;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "unknown later op should fail the plan");
    TEST_ASSERT(count_active_headers((const u_char *)"Content-Encoding",
                                     16) == 2,
                "rollback must restore all Content-Encoding entries");
    TEST_ASSERT(g_plan_freed == 1, "plan should be freed after rollback");

    TEST_PASS("DELETE_ALL rollback restores duplicate headers");
}


static void
test_delete_all_null_key_returns_error(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL,
        NULL, 5, NULL, 0 };

    TEST_SUBSECTION("DELETE_ALL NULL key returns NGX_ERROR");

    setup_request();
    add_existing_header("Content-Encoding", "gzip");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "DELETE_ALL with NULL key must return NGX_ERROR");
    TEST_ASSERT(g_plan_freed == 1, "plan should be freed on error");

    TEST_PASS("DELETE_ALL NULL key returns NGX_ERROR");
}


static void
test_set_empty_value(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
        (const uint8_t *)"X-Empty", 7,
        (const uint8_t *)"", 0 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("SET with empty value (value_len=0)");

    setup_request();
    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "SET empty value should succeed");

    h = find_header((const u_char *)"X-Empty", 7);
    TEST_ASSERT(h != NULL, "header should exist after SET");
    TEST_ASSERT(h->hash == 1, "header hash should be 1");
    TEST_ASSERT(h->value.len == 0, "value length should be 0");
    TEST_ASSERT(h->value.data == NULL, "value data should be NULL");

    TEST_PASS("SET empty value correct");
}

static void
test_set_empty_value_rollback(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entries[2] = {
        { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
          (const uint8_t *)"X-Empty", 7,
          (const uint8_t *)"", 0 },
        { 99, (const uint8_t *)"X-Bad", 5, NULL, 0 }
    };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("SET empty value rollback on later failure");

    setup_request();
    plan.handle = &handle;
    plan.entries = entries;
    plan.count = 2;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "unknown later op should fail the plan");

    h = find_header((const u_char *)"X-Empty", 7);
    TEST_ASSERT(h == NULL,
                "rolled-back SET with empty value should not be visible");

    TEST_PASS("SET empty value rollback correct");
}

static void
test_modify_existing_empty_value(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY,
        (const uint8_t *)"X-Change", 8,
        (const uint8_t *)"", 0 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("MODIFY existing header to empty value");

    setup_request();
    add_existing_header("X-Change", "original");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "MODIFY to empty value should succeed");

    h = find_header((const u_char *)"X-Change", 8);
    TEST_ASSERT(h != NULL, "header should still exist");
    TEST_ASSERT(h->value.len == 0, "value length should be 0");
    TEST_ASSERT(h->value.data == NULL, "value data should be NULL");

    TEST_PASS("MODIFY to empty value correct");
}

static void
test_modify_existing_empty_value_rollback(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entries[2] = {
        { NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY,
          (const uint8_t *)"X-Change", 8,
          (const uint8_t *)"", 0 },
        { 99, (const uint8_t *)"X-Bad", 5, NULL, 0 }
    };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("MODIFY to empty value rollback restores original");

    setup_request();
    add_existing_header("X-Change", "original");

    plan.handle = &handle;
    plan.entries = entries;
    plan.count = 2;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_ERROR,
                "unknown later op should fail the plan");

    h = find_header((const u_char *)"X-Change", 8);
    TEST_ASSERT(h != NULL, "header should exist after rollback");
    TEST_ASSERT(h->value.len == 8, "value length should be restored");
    TEST_ASSERT(memcmp(h->value.data, "original", 8) == 0,
                "value should be restored to original");

    TEST_PASS("MODIFY empty value rollback restores original");
}

static void
test_multi_op_plan(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entries[3] = {
        { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
          (const uint8_t *)"X-Added", 7,
          (const uint8_t *)"new", 3 },
        { NGX_HTTP_MARKDOWN_PLAN_OP_DELETE,
          (const uint8_t *)"X-Remove", 8, NULL, 0 },
        { NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY,
          (const uint8_t *)"X-Change", 8,
          (const uint8_t *)"updated", 7 }
    };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("multi-operation plan");

    setup_request();
    add_existing_header("X-Remove", "old");
    add_existing_header("X-Change", "original");

    plan.handle = &handle;
    plan.entries = entries;
    plan.count = 3;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "multi-op plan should succeed");

    h = find_header((const u_char *)"X-Added", 7);
    TEST_ASSERT(h != NULL && h->hash == 1, "X-Added should exist");

    h = find_header((const u_char *)"X-Remove", 8);
    TEST_ASSERT(h == NULL, "X-Remove should be deleted");

    h = find_header((const u_char *)"X-Change", 8);
    TEST_ASSERT(h != NULL, "X-Change should exist");
    TEST_ASSERT(h->value.len == 7 && memcmp(h->value.data, "updated", 7) == 0,
                "X-Change value should be updated");

    TEST_PASS("Multi-operation plan correct");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("header_plan_apply Tests\n");
    printf("========================================\n");

    test_null_plan();
    test_empty_plan();
    test_null_request();
    test_exceed_max_entries();
    test_set_new_header();
    test_set_overwrite_existing();
    test_set_new_header_value_allocation_failure_cleans_up();
    test_set_overwrite_existing_value_allocation_failure();
    test_set_content_type_no_list_entry();
    test_set_content_type_invalidates_stale_entry();
    test_set_content_type_invalidates_all_stale_entries();
    test_set_content_type_rollback_restores_all_stale_entries();
    test_delete_existing();
    test_delete_nonexistent();
    test_modify_existing();
    test_modify_nonexistent();
    test_modify_existing_value_allocation_failure();
    test_set_etag_placeholder_noop();
    test_unknown_op_type_rollback();
    test_delete_all_removes_duplicate_headers();
    test_delete_all_visitor_failure_restores_duplicate_headers();
    test_delete_all_rollback_restores_duplicate_headers();
    test_delete_all_null_key_returns_error();
    test_set_empty_value();
    test_set_empty_value_rollback();
    test_modify_existing_empty_value();
    test_modify_existing_empty_value_rollback();
    test_multi_op_plan();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
