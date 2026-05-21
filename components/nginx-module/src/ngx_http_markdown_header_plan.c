/*
 * NGINX Markdown Filter Module - Header Plan Atomic Application
 *
 * Implements atomic application of Rust-built header plans to NGINX
 * response headers.  All operations succeed or all are rolled back.
 *
 * REQ-0700-RUST-004: Header plan atomicity.
 * Rule 29: Clear flags after gated op succeeds, not before.
 */

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#include "markdown_converter.h"
#include "ngx_http_markdown_header_plan.h"


/*
 * Operation type constants matching FFIHeaderEntry.op_type.
 */
#define NGX_HTTP_MARKDOWN_PLAN_OP_SET     0
#define NGX_HTTP_MARKDOWN_PLAN_OP_DELETE  1
#define NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY  2

/*
 * Maximum number of header plan entries supported for rollback.
 * Plans exceeding this count are rejected to bound stack usage.
 */
#define NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES  64


/*
 * Saved state for a single header modification (for rollback).
 *
 * For SET operations on new headers: the pushed ngx_table_elt_t is
 * invalidated (hash=0) on rollback.
 *
 * For DELETE operations: the original hash is restored on rollback.
 *
 * For MODIFY operations: the original value is restored on rollback.
 */
typedef struct {
    uint8_t              op_type;

    /*
     * For SET: pointer to the newly pushed header (invalidate on rollback).
     * For DELETE/MODIFY: pointer to the existing header that was changed.
     */
    ngx_table_elt_t     *header;

    /* Original state saved before modification. */
    ngx_uint_t           orig_hash;
    ngx_str_t            orig_value;
} ngx_http_markdown_plan_undo_t;


/*
 * Find a response header by name in the output header list.
 *
 * Walks the full ngx_list_part_t chain (Rule 28) performing
 * case-insensitive name comparison.
 *
 * r        - current HTTP request
 * name     - header name bytes
 * name_len - length of header name
 *
 * Returns:
 *   pointer to matching ngx_table_elt_t, or NULL if not found
 */
static ngx_table_elt_t *
ngx_http_markdown_plan_find_header(ngx_http_request_t *r,
    const u_char *name, size_t name_len)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *headers;
    ngx_uint_t        i;

    part = &r->headers_out.headers.part;

    while (part != NULL) {
        headers = part->elts;

        for (i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }

            if (headers[i].key.len == name_len
                && ngx_strncasecmp(headers[i].key.data,
                                   (u_char *) name,
                                   name_len) == 0)
            {
                return &headers[i];
            }
        }

        part = part->next;
    }

    return NULL;
}


/*
 * Apply a single SET operation: create or overwrite a header.
 *
 * If the header already exists, overwrites its value (saving the
 * original for rollback as a MODIFY-style undo).  If it does not
 * exist, pushes a new entry onto the header list.
 *
 * r     - current HTTP request
 * entry - plan entry with key/value to set
 * undo  - rollback record to populate
 *
 * Returns:
 *   NGX_OK    on success
 *   NGX_ERROR on allocation failure
 */
static ngx_int_t
ngx_http_markdown_plan_apply_set(ngx_http_request_t *r,
    const FFIHeaderEntry *entry,
    ngx_http_markdown_plan_undo_t *undo)
{
    ngx_table_elt_t  *h;
    u_char           *pool_val;

    h = ngx_http_markdown_plan_find_header(r,
        (const u_char *) entry->key, entry->key_len);

    if (h != NULL) {
        /*
         * Header exists: save original value, then overwrite.
         * Treat as a modify for rollback purposes.
         */
        undo->op_type = NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY;
        undo->header = h;
        undo->orig_hash = h->hash;
        undo->orig_value = h->value;

        pool_val = ngx_pnalloc(r->pool, entry->value_len);
        if (pool_val == NULL) {
            return NGX_ERROR;
        }

        ngx_memcpy(pool_val, entry->value, entry->value_len);
        h->value.data = pool_val;
        h->value.len = entry->value_len;

        return NGX_OK;
    }

    /* Header does not exist: push a new entry. */
    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->key.data = ngx_pnalloc(r->pool, entry->key_len);
    if (h->key.data == NULL) {
        /*
         * Cannot undo the list push, but we can invalidate the
         * entry so it is not emitted.
         */
        h->hash = 0;
        return NGX_ERROR;
    }

    ngx_memcpy(h->key.data, entry->key, entry->key_len);
    h->key.len = entry->key_len;

    h->value.data = ngx_pnalloc(r->pool, entry->value_len);
    if (h->value.data == NULL) {
        h->hash = 0;
        return NGX_ERROR;
    }

    ngx_memcpy(h->value.data, entry->value, entry->value_len);
    h->value.len = entry->value_len;
    h->hash = 1;

    undo->op_type = NGX_HTTP_MARKDOWN_PLAN_OP_SET;
    undo->header = h;
    undo->orig_hash = 0;
    undo->orig_value.data = NULL;
    undo->orig_value.len = 0;

    return NGX_OK;
}


/*
 * Apply a single DELETE operation: remove a header by name.
 *
 * Sets hash=0 on the matching header to mark it as removed.
 * If the header does not exist, the operation is a no-op (success).
 *
 * r     - current HTTP request
 * entry - plan entry with key to delete
 * undo  - rollback record to populate
 *
 * Returns:
 *   NGX_OK always (delete cannot fail)
 */
static ngx_int_t
ngx_http_markdown_plan_apply_delete(ngx_http_request_t *r,
    const FFIHeaderEntry *entry,
    ngx_http_markdown_plan_undo_t *undo)
{
    ngx_table_elt_t  *h;

    h = ngx_http_markdown_plan_find_header(r,
        (const u_char *) entry->key, entry->key_len);

    if (h == NULL) {
        /* Header not present; nothing to do. */
        undo->op_type = NGX_HTTP_MARKDOWN_PLAN_OP_DELETE;
        undo->header = NULL;
        undo->orig_hash = 0;
        undo->orig_value.data = NULL;
        undo->orig_value.len = 0;
        return NGX_OK;
    }

    /* Save original state and invalidate. */
    undo->op_type = NGX_HTTP_MARKDOWN_PLAN_OP_DELETE;
    undo->header = h;
    undo->orig_hash = h->hash;
    undo->orig_value = h->value;

    h->hash = 0;

    return NGX_OK;
}


/*
 * Apply a single MODIFY operation: change an existing header's value.
 *
 * If the header does not exist, the operation is a no-op (success).
 *
 * r     - current HTTP request
 * entry - plan entry with key and new value
 * undo  - rollback record to populate
 *
 * Returns:
 *   NGX_OK    on success or no-op
 *   NGX_ERROR on allocation failure
 */
static ngx_int_t
ngx_http_markdown_plan_apply_modify(ngx_http_request_t *r,
    const FFIHeaderEntry *entry,
    ngx_http_markdown_plan_undo_t *undo)
{
    ngx_table_elt_t  *h;
    u_char           *pool_val;

    h = ngx_http_markdown_plan_find_header(r,
        (const u_char *) entry->key, entry->key_len);

    if (h == NULL) {
        /* Header not present; nothing to modify. */
        undo->op_type = NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY;
        undo->header = NULL;
        undo->orig_hash = 0;
        undo->orig_value.data = NULL;
        undo->orig_value.len = 0;
        return NGX_OK;
    }

    /* Save original value. */
    undo->op_type = NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY;
    undo->header = h;
    undo->orig_hash = h->hash;
    undo->orig_value = h->value;

    /* Allocate and copy new value. */
    pool_val = ngx_pnalloc(r->pool, entry->value_len);
    if (pool_val == NULL) {
        return NGX_ERROR;
    }

    ngx_memcpy(pool_val, entry->value, entry->value_len);
    h->value.data = pool_val;
    h->value.len = entry->value_len;

    return NGX_OK;
}


/*
 * Roll back all previously applied operations.
 *
 * Iterates the undo array in reverse order, restoring each header
 * to its original state.
 *
 * undo  - array of undo records
 * count - number of records to roll back
 */
static void
ngx_http_markdown_plan_rollback(ngx_http_markdown_plan_undo_t *undo,
    uintptr_t count)
{
    uintptr_t  i;

    /* Roll back in reverse order. */
    i = count;
    while (i > 0) {
        i--;

        if (undo[i].header == NULL) {
            /* No-op entry (header was not found); skip. */
            continue;
        }

        switch (undo[i].op_type) {

        case NGX_HTTP_MARKDOWN_PLAN_OP_SET:
            /* Undo a newly pushed header by invalidating it. */
            undo[i].header->hash = 0;
            break;

        case NGX_HTTP_MARKDOWN_PLAN_OP_DELETE:
            /* fall through */
        case NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY:
            /* Restore the original hash and value. */
            undo[i].header->hash = undo[i].orig_hash;
            undo[i].header->value = undo[i].orig_value;
            break;

        default:
            /* Unknown op_type; skip to avoid corruption. */
            break;
        }
    }
}


ngx_int_t
ngx_http_markdown_apply_header_plan(ngx_http_request_t *r,
    struct FFIHeaderPlan *plan)
{
    ngx_http_markdown_plan_undo_t  *undo;
    const FFIHeaderEntry           *entry;
    uintptr_t                       i;
    ngx_int_t                       rc;

    /* NULL plan or empty plan is a no-op. */
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

    /* Reject plans that exceed the rollback array bound. */
    if (plan->count > NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: entry count %uz exceeds "
            "maximum %d",
            (size_t) plan->count,
            NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES);
        markdown_header_plan_free(plan);
        return NGX_ERROR;
    }

    /* Allocate undo array from request pool. */
    undo = ngx_palloc(r->pool,
        plan->count * sizeof(ngx_http_markdown_plan_undo_t));
    if (undo == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: failed to allocate "
            "undo array for %uz entries",
            (size_t) plan->count);
        markdown_header_plan_free(plan);
        return NGX_ERROR;
    }

    /* Apply each entry, recording undo state. */
    for (i = 0; i < plan->count; i++) {
        entry = &plan->entries[i];

        switch (entry->op_type) {

        case NGX_HTTP_MARKDOWN_PLAN_OP_SET:
            rc = ngx_http_markdown_plan_apply_set(r, entry, &undo[i]);
            break;

        case NGX_HTTP_MARKDOWN_PLAN_OP_DELETE:
            rc = ngx_http_markdown_plan_apply_delete(r, entry,
                &undo[i]);
            break;

        case NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY:
            rc = ngx_http_markdown_plan_apply_modify(r, entry,
                &undo[i]);
            break;

        default:
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown: unknown op_type %d "
                "at index %uz",
                (int) entry->op_type, (size_t) i);
            rc = NGX_ERROR;
            break;
        }

        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown: operation %d failed "
                "at index %uz, rolling back %uz entries",
                (int) entry->op_type, (size_t) i, (size_t) i);

            ngx_http_markdown_plan_rollback(undo, i);
            markdown_header_plan_free(plan);
            return NGX_ERROR;
        }
    }

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
        "markdown: applied %uz entries "
        "successfully",
        (size_t) plan->count);

    /*
     * All operations succeeded.  Free the Rust-owned plan.
     * Rule 29: clear/free after gated op succeeds.
     */
    markdown_header_plan_free(plan);

    return NGX_OK;
}
