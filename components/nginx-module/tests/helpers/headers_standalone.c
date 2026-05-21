/*
 * Standalone harness for header update logic in unit tests.
 * It provides minimal nginx-compatible types/mocks and reuses
 * the shared production implementation.
 */

#include "headers_standalone_types.h"

/*
 * Stub implementations of Rust FFI header plan functions.
 *
 * These stubs simulate the Rust-side header plan builder for
 * unit testing without linking the Rust library.  The stub
 * plan always produces a minimal set of entries matching the
 * production behavior (Content-Type set, Content-Encoding
 * delete, Vary set).
 */

static struct FFIHeaderEntry g_stub_entries[5];
static uintptr_t g_stub_entry_count = 0;

static const uint8_t stub_ct_key[] = "Content-Type";
static const uint8_t stub_ct_val[] =
    "text/markdown; charset=utf-8";
static const uint8_t stub_ce_key[] = "Content-Encoding";
static const uint8_t stub_cl_key[] = "Content-Length";
/* stub_vary_key/stub_vary_val removed: Vary is handled post-plan */

void
markdown_header_plan_init(struct FFIHeaderPlan *result)
{
    if (result == NULL) {
        return;
    }
    memset(result, 0, sizeof(*result));
}

void
markdown_build_header_plan(const uint8_t *content_type,
    uintptr_t content_type_len, uint8_t has_etag,
    struct FFIHeaderPlan *result)
{
    (void) content_type;
    (void) content_type_len;

    if (result == NULL) {
        return;
    }

    g_stub_entry_count = 0;

    /* Entry 0: SET Content-Type */
    g_stub_entries[0].op_type = 0;
    g_stub_entries[0].key = stub_ct_key;
    g_stub_entries[0].key_len = sizeof(stub_ct_key) - 1;
    g_stub_entries[0].value = stub_ct_val;
    g_stub_entries[0].value_len = sizeof(stub_ct_val) - 1;
    g_stub_entry_count++;

    /* Entry 1: DELETE Content-Encoding */
    g_stub_entries[1].op_type = 1;
    g_stub_entries[1].key = stub_ce_key;
    g_stub_entries[1].key_len = sizeof(stub_ce_key) - 1;
    g_stub_entries[1].value = NULL;
    g_stub_entries[1].value_len = 0;
    g_stub_entry_count++;

    /* Entry 2: DELETE Content-Length */
    g_stub_entries[2].op_type = 1;
    g_stub_entries[2].key = stub_cl_key;
    g_stub_entries[2].key_len = sizeof(stub_cl_key) - 1;
    g_stub_entries[2].value = NULL;
    g_stub_entries[2].value_len = 0;
    g_stub_entry_count++;

    /* Entry 3: MODIFY ETag (op_type=2) — always included;
     * the C-side handler decides whether to set or clear
     * based on conf->policy.generate_etag and result->etag. */
    g_stub_entries[3].op_type = 2;
    g_stub_entries[3].key = (const uint8_t *) "ETag";
    g_stub_entries[3].key_len = 4;
    g_stub_entries[3].value = NULL;
    g_stub_entries[3].value_len = 0;
    g_stub_entry_count++;

    (void) has_etag;

    result->handle = NULL;
    result->entries =
        (const struct FFIHeaderEntry *) g_stub_entries;
    result->count = g_stub_entry_count;
}

void
markdown_header_plan_free(struct FFIHeaderPlan *plan)
{
    if (plan == NULL) {
        return;
    }
    plan->handle = NULL;
    plan->entries = NULL;
    plan->count = 0;
}

/*
 * Stub for ngx_http_markdown_apply_header_plan.
 *
 * In the standalone test harness, this delegates to the best-effort
 * loop in the impl header (which calls the stub plan builder above).
 * For unit tests, we simulate atomic success by applying each entry
 * via the same logic the production code uses.
 */
ngx_int_t
ngx_http_markdown_apply_header_plan(ngx_http_request_t *r,
    struct FFIHeaderPlan *plan)
{
    uintptr_t                    i;
    const struct FFIHeaderEntry *entry;

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

    /*
     * Simple stub: iterate entries and apply SET/DELETE.
     * This is sufficient for unit tests that verify the
     * update_headers function behavior.
     */
    for (i = 0; i < plan->count; i++) {
        entry = &plan->entries[i];

        switch (entry->op_type) {
        case 0: {
            /* SET: handle known headers directly */
            if (entry->key_len == sizeof("Content-Type") - 1
                && ngx_strncasecmp((u_char *) entry->key,
                    (u_char *) "Content-Type",
                    entry->key_len) == 0)
            {
                /* Content-Type: set directly on headers_out */
                r->headers_out.content_type.data =
                    (u_char *) entry->value;
                r->headers_out.content_type.len =
                    entry->value_len;
                r->headers_out.content_type_len =
                    entry->value_len;
            } else {
                /* Other SET headers: push to list */
                ngx_table_elt_t *h;
                u_char *pool_key;
                u_char *pool_val;

                h = ngx_list_push(
                    &r->headers_out.headers);
                if (h == NULL) {
                    markdown_header_plan_free(plan);
                    return NGX_ERROR;
                }

                pool_key = ngx_pnalloc(r->pool,
                    entry->key_len);
                if (pool_key == NULL) {
                    h->hash = 0;
                    markdown_header_plan_free(plan);
                    return NGX_ERROR;
                }
                memcpy(pool_key, entry->key,
                    entry->key_len);

                pool_val = ngx_pnalloc(r->pool,
                    entry->value_len);
                if (pool_val == NULL) {
                    h->hash = 0;
                    markdown_header_plan_free(plan);
                    return NGX_ERROR;
                }
                memcpy(pool_val, entry->value,
                    entry->value_len);

                h->hash = 1;
                h->key.data = pool_key;
                h->key.len = entry->key_len;
                h->value.data = pool_val;
                h->value.len = entry->value_len;
            }
            break;
        }

        case 1:
            /* DELETE: clear the header pointer */
            if (entry->key_len
                == sizeof("Content-Encoding") - 1
                && ngx_strncasecmp((u_char *) entry->key,
                    (u_char *) "Content-Encoding",
                    entry->key_len) == 0)
            {
                r->headers_out.content_encoding = NULL;
            }
            break;

        case 2:
            /* MODIFY/ETag placeholder: no-op in stub */
            break;

        default:
            markdown_header_plan_free(plan);
            return NGX_ERROR;
        }
    }

    markdown_header_plan_free(plan);
    return NGX_OK;
}

#include "../../src/ngx_http_markdown_headers_impl.h"
