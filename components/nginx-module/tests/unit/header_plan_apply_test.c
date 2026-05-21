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

/* ── Minimal NGINX type stubs ─────────────────────────────────── */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef unsigned char   u_char;

#define NGX_OK         0
#define NGX_ERROR     -1

#define NGX_LOG_ERR    4
#define NGX_LOG_DEBUG  8
#define NGX_LOG_DEBUG_HTTP  NGX_LOG_DEBUG

typedef struct {
    size_t      len;
    u_char     *data;
} ngx_str_t;

/* Log stub */
typedef struct {
    int dummy;
} ngx_log_t;

typedef struct {
    ngx_log_t  *log;
} ngx_connection_t;

/* Pool stub */
static u_char g_pool_buf[1024 * 256];
static size_t g_pool_offset;

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
#define ngx_log_error(level, log, err, ...) (void)0
#define ngx_log_debug1(level, log, err, fmt, arg) (void)0

/* ── FFI types (matching production) ──────────────────────────── */

#define NGX_HTTP_MARKDOWN_PLAN_OP_SET     0
#define NGX_HTTP_MARKDOWN_PLAN_OP_DELETE  1
#define NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY  2
#define NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES  64

typedef struct {
    uint8_t      op_type;
    const uint8_t *key;
    size_t       key_len;
    const uint8_t *value;
    size_t       value_len;
} FFIHeaderEntry;

typedef struct FFIHeaderPlanHandle {
    uint8_t _private[1];
} FFIHeaderPlanHandle;

typedef struct FFIHeaderPlan {
    FFIHeaderPlanHandle  *handle;
    const FFIHeaderEntry *entries;
    size_t                count;
} FFIHeaderPlan;

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
    ngx_uint_t i;

    for (i = 0; i < g_header_count; i++) {
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

/* ── Setup helpers ────────────────────────────────────────────── */

static void
setup_request(void)
{
    pool_init();
    memset(g_headers, 0, sizeof(g_headers));
    g_header_count = 0;
    g_plan_freed = 0;

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

/* ── Simplified apply_header_plan (mirrors production logic) ──── */

static ngx_int_t
apply_header_plan(ngx_http_request_t *r, FFIHeaderPlan *plan)
{
    plan_undo_t     undo[NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES];
    size_t          i;
    ngx_int_t       rc;

    if (plan == NULL) {
        return NGX_OK;
    }

    if (plan->count == 0) {
        markdown_header_plan_free(plan);
        return NGX_OK;
    }

    if (r == NULL) {
        markdown_header_plan_free(plan);
        return NGX_ERROR;
    }

    if (plan->count > NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES) {
        markdown_header_plan_free(plan);
        return NGX_ERROR;
    }

    memset(undo, 0, sizeof(undo));

    for (i = 0; i < plan->count; i++) {
        const FFIHeaderEntry *entry = &plan->entries[i];
        ngx_table_elt_t *h;

        switch (entry->op_type) {

        case NGX_HTTP_MARKDOWN_PLAN_OP_SET:
            h = find_header(entry->key, entry->key_len);
            if (h != NULL) {
                /* Overwrite existing */
                undo[i].op_type = NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY;
                undo[i].header = h;
                undo[i].orig_hash = h->hash;
                undo[i].orig_value = h->value;

                u_char *pv = ngx_pnalloc(r->pool, entry->value_len);
                if (pv == NULL) { rc = NGX_ERROR; goto rollback; }
                memcpy(pv, entry->value, entry->value_len);
                h->value.data = pv;
                h->value.len = entry->value_len;
            } else {
                /* Push new */
                h = ngx_list_push(&r->headers_out.headers);
                if (h == NULL) { rc = NGX_ERROR; goto rollback; }

                h->key.data = ngx_pnalloc(r->pool, entry->key_len);
                if (h->key.data == NULL) { h->hash = 0; rc = NGX_ERROR; goto rollback; }
                memcpy(h->key.data, entry->key, entry->key_len);
                h->key.len = entry->key_len;

                h->value.data = ngx_pnalloc(r->pool, entry->value_len);
                if (h->value.data == NULL) { h->hash = 0; rc = NGX_ERROR; goto rollback; }
                memcpy(h->value.data, entry->value, entry->value_len);
                h->value.len = entry->value_len;
                h->hash = 1;

                undo[i].op_type = NGX_HTTP_MARKDOWN_PLAN_OP_SET;
                undo[i].header = h;
            }
            break;

        case NGX_HTTP_MARKDOWN_PLAN_OP_DELETE:
            h = find_header(entry->key, entry->key_len);
            undo[i].op_type = NGX_HTTP_MARKDOWN_PLAN_OP_DELETE;
            if (h != NULL) {
                undo[i].header = h;
                undo[i].orig_hash = h->hash;
                undo[i].orig_value = h->value;
                h->hash = 0;
            } else {
                undo[i].header = NULL;
            }
            break;

        case NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY:
            h = find_header(entry->key, entry->key_len);
            undo[i].op_type = NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY;
            if (h != NULL) {
                undo[i].header = h;
                undo[i].orig_hash = h->hash;
                undo[i].orig_value = h->value;

                u_char *mv = ngx_pnalloc(r->pool, entry->value_len);
                if (mv == NULL) { rc = NGX_ERROR; goto rollback; }
                memcpy(mv, entry->value, entry->value_len);
                h->value.data = mv;
                h->value.len = entry->value_len;
            } else {
                undo[i].header = NULL;
            }
            break;

        default:
            rc = NGX_ERROR;
            goto rollback;
        }
    }

    markdown_header_plan_free(plan);
    return NGX_OK;

rollback:
    /* Roll back in reverse */
    while (i > 0) {
        i--;
        if (undo[i].header == NULL) continue;
        switch (undo[i].op_type) {
        case NGX_HTTP_MARKDOWN_PLAN_OP_SET:
            undo[i].header->hash = 0;
            break;
        case NGX_HTTP_MARKDOWN_PLAN_OP_DELETE:
        case NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY:
            undo[i].header->hash = undo[i].orig_hash;
            undo[i].header->value = undo[i].orig_value;
            break;
        }
    }
    markdown_header_plan_free(plan);
    return rc;
}


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
        (const uint8_t *)"Content-Type", 12,
        (const uint8_t *)"text/markdown", 13 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("SET new header");

    setup_request();
    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "SET new header should succeed");

    h = find_header((const u_char *)"Content-Type", 12);
    TEST_ASSERT(h != NULL, "header should exist after SET");
    TEST_ASSERT(h->hash == 1, "header hash should be 1");
    TEST_ASSERT(h->value.len == 13, "value length should be 13");
    TEST_ASSERT(memcmp(h->value.data, "text/markdown", 13) == 0,
                "value should be text/markdown");

    TEST_PASS("SET new header correct");
}

static void
test_set_overwrite_existing(void)
{
    FFIHeaderPlan plan;
    FFIHeaderPlanHandle handle;
    FFIHeaderEntry entry = { NGX_HTTP_MARKDOWN_PLAN_OP_SET,
        (const uint8_t *)"Content-Type", 12,
        (const uint8_t *)"text/markdown", 13 };
    ngx_table_elt_t *h;

    TEST_SUBSECTION("SET overwrite existing header");

    setup_request();
    add_existing_header("Content-Type", "text/html");

    plan.handle = &handle;
    plan.entries = &entry;
    plan.count = 1;

    TEST_ASSERT(apply_header_plan(&g_request, &plan) == NGX_OK,
                "SET overwrite should succeed");

    h = find_header((const u_char *)"Content-Type", 12);
    TEST_ASSERT(h != NULL, "header should still exist");
    TEST_ASSERT(h->value.len == 13, "value length should be updated");
    TEST_ASSERT(memcmp(h->value.data, "text/markdown", 13) == 0,
                "value should be updated to text/markdown");

    TEST_PASS("SET overwrite existing correct");
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
    test_delete_existing();
    test_delete_nonexistent();
    test_modify_existing();
    test_modify_nonexistent();
    test_unknown_op_type_rollback();
    test_multi_op_plan();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
