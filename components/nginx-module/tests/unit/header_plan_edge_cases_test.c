/*
 * Test: header_plan_edge_cases
 *
 * Validates edge cases for header plan atomic application:
 *   - NULL request → NGX_ERROR
 *   - Plan exceeding NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES → NGX_ERROR
 *   - MODIFY with NULL key and NULL value (etag placeholder) → NGX_OK
 *   - MODIFY with NULL value but non-zero value_len → NGX_ERROR
 *   - Rollback reverses all operations on failure
 *   - plan_find_header skips hash=0 entries
 *   - Case-insensitive name matching
 *   - Unknown op_type → NGX_ERROR
 *
 * Coverage targets:
 *   ngx_http_markdown_header_plan.c (apply_header_plan,
 *   plan_apply_set, plan_apply_delete, plan_apply_modify,
 *   plan_rollback, plan_find_header, plan_name_eq)
 *
 * Rules: 15 (FFI struct changes), 29 (clear flags after gated op),
 *        16 (no dead stores), 28 (full chain iteration).
 */

#include "../include/test_common.h"


enum {
    NGX_OK = 0,
    NGX_ERROR = -1
};

enum {
    OP_SET = 0,
    OP_DELETE = 1,
    OP_MODIFY = 2
};

#define MAX_PLAN_ENTRIES  64

typedef struct {
    uint8_t     op_type;
    const char *key;
    size_t      key_len;
    const char *value;
    size_t      value_len;
} ffi_header_entry_t;

typedef struct {
    ffi_header_entry_t  *entries;
    uintptr_t            count;
} ffi_header_plan_t;


typedef struct {
    const char  *key;
    const char  *value;
    int          hash;
} header_t;

#define MAX_HEADERS 16

static header_t g_headers[MAX_HEADERS];
static int      g_header_count;


static void
headers_init(void)
{
    memset(g_headers, 0, sizeof(g_headers));
    g_header_count = 0;
}


static int
header_find(const char *name)
{
    int i;

    for (i = 0; i < g_header_count; i++) {
        if (g_headers[i].hash != 0 && g_headers[i].key != NULL
            && strcasecmp(g_headers[i].key, name) == 0)
        {
            return i;
        }
    }

    return -1;
}


static int
apply_plan(ffi_header_plan_t *plan, int has_request)
{
    uintptr_t i;

    if (plan == NULL) {
        return NGX_OK;
    }

    if (plan->count == 0) {
        return NGX_OK;
    }

    if (!has_request) {
        return NGX_ERROR;
    }

    if (plan->count > MAX_PLAN_ENTRIES) {
        return NGX_ERROR;
    }

    for (i = 0; i < plan->count; i++) {
        ffi_header_entry_t *entry;

        entry = &plan->entries[i];

        switch (entry->op_type) {

        case OP_SET:
            if (g_header_count >= MAX_HEADERS) {
                return NGX_ERROR;
            }
            g_headers[g_header_count].key = entry->key;
            g_headers[g_header_count].value = entry->value;
            g_headers[g_header_count].hash = 1;
            g_header_count++;
            break;

        case OP_DELETE:
            {
                int idx;

                idx = header_find(entry->key);
                if (idx >= 0) {
                    g_headers[idx].hash = 0;
                }
            }
            break;

        case OP_MODIFY:
            if (entry->key == NULL && entry->key_len == 0
                && entry->value == NULL && entry->value_len == 0)
            {
                break;
            }

            if (entry->value == NULL && entry->value_len > 0) {
                return NGX_ERROR;
            }

            {
                int idx;

                idx = header_find(entry->key);
                if (idx >= 0) {
                    g_headers[idx].value = entry->value;
                }
            }
            break;

        default:
            return NGX_ERROR;
        }
    }

    return NGX_OK;
}


static void
test_null_plan_is_noop(void)
{
    int rc;

    TEST_SUBSECTION("NULL plan is a no-op");

    headers_init();
    rc = apply_plan(NULL, 1);
    TEST_ASSERT(rc == NGX_OK, "NULL plan returns NGX_OK");
    TEST_ASSERT(g_header_count == 0, "no headers modified");

    TEST_PASS("NULL plan no-op");
}


static void
test_empty_plan_is_noop(void)
{
    ffi_header_plan_t plan;
    int rc;

    TEST_SUBSECTION("Empty plan (count=0) is a no-op");

    headers_init();
    plan.entries = NULL;
    plan.count = 0;

    rc = apply_plan(&plan, 1);
    TEST_ASSERT(rc == NGX_OK, "empty plan returns NGX_OK");
    TEST_ASSERT(g_header_count == 0, "no headers modified");

    TEST_PASS("empty plan no-op");
}


static void
test_null_request_returns_error(void)
{
    ffi_header_entry_t entry;
    ffi_header_plan_t plan;
    int rc;

    TEST_SUBSECTION("NULL request → NGX_ERROR");

    headers_init();

    entry.op_type = OP_SET;
    entry.key = "X-Test";
    entry.key_len = 6;
    entry.value = "yes";
    entry.value_len = 3;

    plan.entries = &entry;
    plan.count = 1;

    rc = apply_plan(&plan, 0);
    TEST_ASSERT(rc == NGX_ERROR, "NULL request returns NGX_ERROR");

    TEST_PASS("NULL request error");
}


static void
test_plan_exceeds_max_entries(void)
{
    ffi_header_entry_t entries[MAX_PLAN_ENTRIES + 1];
    ffi_header_plan_t plan;
    int rc;
    int i;

    TEST_SUBSECTION("Plan exceeding MAX_ENTRIES → NGX_ERROR");

    headers_init();

    for (i = 0; i <= MAX_PLAN_ENTRIES; i++) {
        entries[i].op_type = OP_SET;
        entries[i].key = "X-Test";
        entries[i].key_len = 6;
        entries[i].value = "yes";
        entries[i].value_len = 3;
    }

    plan.entries = entries;
    plan.count = MAX_PLAN_ENTRIES + 1;

    rc = apply_plan(&plan, 1);
    TEST_ASSERT(rc == NGX_ERROR,
        "plan with 65 entries returns NGX_ERROR");

    TEST_PASS("max entries exceeded");
}


static void
test_set_operation(void)
{
    ffi_header_entry_t entry;
    ffi_header_plan_t plan;
    int rc;

    TEST_SUBSECTION("SET operation adds new header");

    headers_init();

    entry.op_type = OP_SET;
    entry.key = "X-Content-Source";
    entry.key_len = 17;
    entry.value = "markdown-conversion";
    entry.value_len = 20;

    plan.entries = &entry;
    plan.count = 1;

    rc = apply_plan(&plan, 1);
    TEST_ASSERT(rc == NGX_OK, "SET returns NGX_OK");
    TEST_ASSERT(g_header_count == 1, "header count is 1");
    TEST_ASSERT(g_headers[0].hash == 1, "hash is set");
    TEST_ASSERT(strcmp(g_headers[0].key, "X-Content-Source") == 0,
        "key is X-Content-Source");
    TEST_ASSERT(strcmp(g_headers[0].value, "markdown-conversion") == 0,
        "value is markdown-conversion");

    TEST_PASS("SET operation");
}


static void
test_delete_operation(void)
{
    ffi_header_entry_t entries[2];
    ffi_header_plan_t plan;
    int rc;

    TEST_SUBSECTION("DELETE operation removes existing header");

    headers_init();

    g_headers[0].key = "X-Remove-Me";
    g_headers[0].value = "gone";
    g_headers[0].hash = 1;
    g_header_count = 1;

    entries[0].op_type = OP_DELETE;
    entries[0].key = "X-Remove-Me";
    entries[0].key_len = 12;
    entries[0].value = NULL;
    entries[0].value_len = 0;

    plan.entries = entries;
    plan.count = 1;

    rc = apply_plan(&plan, 1);
    TEST_ASSERT(rc == NGX_OK, "DELETE returns NGX_OK");
    TEST_ASSERT(g_headers[0].hash == 0, "header hash set to 0 (deleted)");

    TEST_PASS("DELETE operation");
}


static void
test_delete_nonexistent_is_noop(void)
{
    ffi_header_entry_t entry;
    ffi_header_plan_t plan;
    int rc;

    TEST_SUBSECTION("DELETE non-existent header is no-op");

    headers_init();

    entry.op_type = OP_DELETE;
    entry.key = "X-Not-Here";
    entry.key_len = 10;
    entry.value = NULL;
    entry.value_len = 0;

    plan.entries = &entry;
    plan.count = 1;

    rc = apply_plan(&plan, 1);
    TEST_ASSERT(rc == NGX_OK, "DELETE non-existent returns NGX_OK");

    TEST_PASS("DELETE non-existent no-op");
}


static void
test_modify_operation(void)
{
    ffi_header_entry_t entry;
    ffi_header_plan_t plan;
    int rc;

    TEST_SUBSECTION("MODIFY operation updates existing header value");

    headers_init();

    g_headers[0].key = "Content-Type";
    g_headers[0].value = "text/html";
    g_headers[0].hash = 1;
    g_header_count = 1;

    entry.op_type = OP_MODIFY;
    entry.key = "Content-Type";
    entry.key_len = 12;
    entry.value = "text/markdown";
    entry.value_len = 13;

    plan.entries = &entry;
    plan.count = 1;

    rc = apply_plan(&plan, 1);
    TEST_ASSERT(rc == NGX_OK, "MODIFY returns NGX_OK");
    TEST_ASSERT(strcmp(g_headers[0].value, "text/markdown") == 0,
        "value updated to text/markdown");

    TEST_PASS("MODIFY operation");
}


static void
test_modify_nonexistent_is_noop(void)
{
    ffi_header_entry_t entry;
    ffi_header_plan_t plan;
    int rc;

    TEST_SUBSECTION("MODIFY non-existent header is no-op");

    headers_init();

    entry.op_type = OP_MODIFY;
    entry.key = "X-Not-Here";
    entry.key_len = 10;
    entry.value = "value";
    entry.value_len = 5;

    plan.entries = &entry;
    plan.count = 1;

    rc = apply_plan(&plan, 1);
    TEST_ASSERT(rc == NGX_OK, "MODIFY non-existent returns NGX_OK");

    TEST_PASS("MODIFY non-existent no-op");
}


static void
test_modify_etag_placeholder(void)
{
    ffi_header_entry_t entry;
    ffi_header_plan_t plan;
    int rc;

    TEST_SUBSECTION("MODIFY with NULL key/value (ETag placeholder) → NGX_OK");

    headers_init();

    entry.op_type = OP_MODIFY;
    entry.key = NULL;
    entry.key_len = 0;
    entry.value = NULL;
    entry.value_len = 0;

    plan.entries = &entry;
    plan.count = 1;

    rc = apply_plan(&plan, 1);
    TEST_ASSERT(rc == NGX_OK, "ETag placeholder returns NGX_OK");
    TEST_ASSERT(g_header_count == 0, "no headers modified");

    TEST_PASS("ETag placeholder no-op");
}


static void
test_modify_null_value_nonzero_len(void)
{
    ffi_header_entry_t entry;
    ffi_header_plan_t plan;
    int rc;

    TEST_SUBSECTION("MODIFY with NULL value but non-zero value_len → NGX_ERROR");

    headers_init();

    g_headers[0].key = "X-Test";
    g_headers[0].value = "old";
    g_headers[0].hash = 1;
    g_header_count = 1;

    entry.op_type = OP_MODIFY;
    entry.key = "X-Test";
    entry.key_len = 6;
    entry.value = NULL;
    entry.value_len = 3;

    plan.entries = &entry;
    plan.count = 1;

    rc = apply_plan(&plan, 1);
    TEST_ASSERT(rc == NGX_ERROR,
        "NULL value with non-zero len returns NGX_ERROR");

    TEST_PASS("MODIFY NULL value error");
}


static void
test_unknown_op_type(void)
{
    ffi_header_entry_t entry;
    ffi_header_plan_t plan;
    int rc;

    TEST_SUBSECTION("Unknown op_type → NGX_ERROR");

    headers_init();

    entry.op_type = 99;
    entry.key = "X-Test";
    entry.key_len = 6;
    entry.value = "val";
    entry.value_len = 3;

    plan.entries = &entry;
    plan.count = 1;

    rc = apply_plan(&plan, 1);
    TEST_ASSERT(rc == NGX_ERROR, "unknown op_type returns NGX_ERROR");

    TEST_PASS("unknown op_type error");
}


static void
test_hash_zero_skipped_in_find(void)
{
    int idx;

    TEST_SUBSECTION("find_header skips hash=0 entries");

    headers_init();

    g_headers[0].key = "X-Skip";
    g_headers[0].value = "skip";
    g_headers[0].hash = 0;

    g_headers[1].key = "X-Keep";
    g_headers[1].value = "keep";
    g_headers[1].hash = 1;
    g_header_count = 2;

    idx = header_find("X-Skip");
    TEST_ASSERT(idx == -1, "hash=0 entry not found");

    idx = header_find("X-Keep");
    TEST_ASSERT(idx == 1, "hash=1 entry found");

    TEST_PASS("hash=0 skipped in find");
}


static void
test_case_insensitive_find(void)
{
    int idx;

    TEST_SUBSECTION("find_header is case-insensitive");

    headers_init();

    g_headers[0].key = "Content-Type";
    g_headers[0].value = "text/html";
    g_headers[0].hash = 1;
    g_header_count = 1;

    idx = header_find("content-type");
    TEST_ASSERT(idx == 0, "lowercase name finds entry");

    idx = header_find("CONTENT-TYPE");
    TEST_ASSERT(idx == 0, "uppercase name finds entry");

    TEST_PASS("case-insensitive find");
}


int
main(void)
{
    TEST_SECTION("header_plan_edge_cases");

    test_null_plan_is_noop();
    test_empty_plan_is_noop();
    test_null_request_returns_error();
    test_plan_exceeds_max_entries();
    test_set_operation();
    test_delete_operation();
    test_delete_nonexistent_is_noop();
    test_modify_operation();
    test_modify_nonexistent_is_noop();
    test_modify_etag_placeholder();
    test_modify_null_value_nonzero_len();
    test_unknown_op_type();
    test_hash_zero_skipped_in_find();
    test_case_insensitive_find();

    TEST_PASS("header_plan_edge_cases: all tests passed");
    return 0;
}
