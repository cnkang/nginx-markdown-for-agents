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
#define NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL  3

/*
 * Maximum number of header plan entries supported for rollback.
 * Plans exceeding this count are rejected to bound stack usage.
 */
#define NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES  64


static ngx_int_t
ngx_http_markdown_plan_name_eq(const u_char *left, const u_char *right,
    size_t len)
{
    for (size_t i = 0; i < len; i++) {
        if (ngx_tolower(left[i]) != ngx_tolower(right[i])) {
            return 0;
        }
    }

    return 1;
}


/*
 * Determine whether a header name is "Content-Type" (case-insensitive).
 *
 * Content-Type is special: NGINX stores the response Content-Type in the
 * dedicated r->headers_out.content_type field, which the header filter
 * always emits, in addition to iterating the headers list.  A SET of
 * Content-Type must therefore NOT push a list entry (that would emit a
 * second, duplicate Content-Type header on the wire).  The dedicated field
 * is owned by the caller (set after the plan commits); the plan only needs
 * to ensure no stale Content-Type list entry survives.
 */
static ngx_int_t
ngx_http_markdown_plan_is_content_type(const u_char *name, size_t name_len)
{
    static const u_char  content_type[] = "Content-Type";

    if (name_len != sizeof(content_type) - 1) {
        return 0;
    }

    return ngx_http_markdown_plan_name_eq(name, content_type, name_len);
}


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
    ngx_table_elt_t     *header;
    ngx_uint_t           orig_hash;
    ngx_str_t            orig_value;
} ngx_http_markdown_plan_saved_header_t;


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

    ngx_http_markdown_plan_saved_header_t  *saved;
    ngx_uint_t                              saved_count;
} ngx_http_markdown_plan_undo_t;


/*
 * Validate that an FFIHeaderEntry has a non-NULL/non-empty key and a
 * value pointer consistent with its declared length.
 *
 * Shared by the SET and MODIFY apply paths so they cannot drift apart on
 * FFI boundary validation (Rule 46).
 *
 * r     - current HTTP request (for logging)
 * entry - FFI plan entry to validate
 *
 * Returns:
 *   NGX_OK if the entry is usable, NGX_ERROR otherwise (error logged).
 */
static ngx_int_t
ngx_http_markdown_plan_validate_entry(ngx_http_request_t *r,
    const FFIHeaderEntry *entry)
{
    (void) r;

    if (entry->key == NULL || entry->key_len == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: plan entry has NULL/empty key");
        return NGX_ERROR;
    }

    if (entry->value == NULL && entry->value_len > 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: plan entry has NULL value "
            "with non-zero length %uz",
            (size_t) entry->value_len);
        return NGX_ERROR;
    }

    return NGX_OK;
}


/*
 * Save the pre-modification header state into an undo record.
 *
 * Used by both SET (overwrite-existing) and MODIFY to populate the
 * rollback record with op_type / header / orig_hash / orig_value before
 * the value is replaced.
 */
static void
ngx_http_markdown_plan_save_modify_undo(
    ngx_http_markdown_plan_undo_t *undo, ngx_uint_t op_type,
    ngx_table_elt_t *h)
{
    undo->op_type = (uint8_t) op_type;
    undo->header = h;
    undo->orig_hash = h->hash;
    undo->orig_value = h->value;
}


/*
 * Copy an FFI entry's value bytes into a fresh pool allocation and
 * assign it to a header's value field.
 *
 * Returns NGX_OK on success, NGX_ERROR on allocation failure.
 */
static ngx_int_t
ngx_http_markdown_plan_copy_value_to_pool(ngx_http_request_t *r,
    const FFIHeaderEntry *entry, ngx_table_elt_t *h)
{
    u_char  *pool_val;

    if (entry->value_len == 0) {
        h->value.data = NULL;
        h->value.len = 0;
        return NGX_OK;
    }

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
 * Initialize an undo record to a no-op (header-not-found) shape.
 *
 * Shared by DELETE and MODIFY (and any future lookup-based apply op) so
 * the no-op rollback record stays identical across all apply paths.
 */
static void
ngx_http_markdown_plan_set_noop_undo(ngx_http_markdown_plan_undo_t *undo,
    ngx_uint_t op_type)
{
    undo->op_type = (uint8_t) op_type;
    undo->header = NULL;
    undo->orig_hash = 0;
    undo->orig_value.data = NULL;
    undo->orig_value.len = 0;
}


/*
 * Callback signature for ngx_http_markdown_plan_for_each_header_named.
 *
 * Called once per output header matching `name` (case-insensitive, per
 * ngx_http_markdown_plan_name_eq). The opaque `ctx` is forwarded unchanged.
 * Returning NGX_OK continues iteration; any other value stops iteration
 * and is returned to the caller as the iterator result.
 */
typedef ngx_int_t (*ngx_http_markdown_plan_header_visitor_t)(
    ngx_table_elt_t *h, void *ctx);


/*
 * Walk every output header matching `name` and invoke `visitor` for each.
 *
 * Centralizes the ngx_list_part_t chain walk (Rule 28) + the hash==0
 * invalidation filter (Rule 40) + case-insensitive name matching, so the
 * find / count / delete-all paths cannot drift apart on traversal.
 *
 * Parameters:
 *   r        - current HTTP request
 *   name     - header name bytes
 *   name_len - length of header name
 *   visitor  - per-match callback
 *   ctx      - opaque pointer forwarded to the visitor
 *
 * Returns:
 *   NGX_OK if every visitor returned NGX_OK, otherwise the first non-OK
 *   value returned by the visitor (iteration stops on the first non-OK).
 */
static ngx_int_t
ngx_http_markdown_plan_for_each_header_named(ngx_http_request_t *r,
    const u_char *name, size_t name_len,
    ngx_http_markdown_plan_header_visitor_t visitor, void *ctx)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *headers;
    ngx_int_t         rc;

    part = &r->headers_out.headers.part;

    while (part != NULL) {
        headers = part->elts;

        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0
                || headers[i].key.data == NULL
                || headers[i].key.len != name_len
                || !ngx_http_markdown_plan_name_eq(headers[i].key.data,
                                                   name, name_len))
            {
                continue;
            }

            rc = visitor(&headers[i], ctx);
            if (rc != NGX_OK) {
                return rc;
            }
        }

        part = part->next;
    }

    return NGX_OK;
}


/*
 * find_header visitor: returns the first match via ctx (a pointer to
 * ngx_table_elt_t*). Stops iteration immediately by returning NGX_DONE.
 */
static ngx_int_t
ngx_http_markdown_plan_find_header_visitor(ngx_table_elt_t *h, void *ctx)
{
    ngx_table_elt_t  **out = ctx;

    *out = h;
    return NGX_DONE; /* stop after first match */
}


/*
 * Find a response header by name in the output header list.
 *
 * Walks the full ngx_list_part_t chain (Rule 28) performing
 * case-insensitive name comparison. Filters invalidated (hash==0)
 * entries per Rule 40.
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
    ngx_table_elt_t  *found = NULL;

    (void) ngx_http_markdown_plan_for_each_header_named(r, name, name_len,
        ngx_http_markdown_plan_find_header_visitor, &found);

    return found;
}


/*
 * count_headers visitor: increments a counter (ctx is ngx_uint_t*).
 */
static ngx_int_t
ngx_http_markdown_plan_count_header_visitor(ngx_table_elt_t *h, void *ctx) /* NOSONAR: h type dictated by ngx_http_markdown_plan_header_visitor_t callback typedef (Rule 24) */
{
    ngx_uint_t  *count = ctx;

    (void) h;
    (*count)++;
    return NGX_OK;
}


static ngx_uint_t
ngx_http_markdown_plan_count_headers(ngx_http_request_t *r,
    const u_char *name, size_t name_len)
{
    ngx_uint_t  count = 0;

    (void) ngx_http_markdown_plan_for_each_header_named(r, name, name_len,
        ngx_http_markdown_plan_count_header_visitor, &count);

    return count;
}


/*
 * Resolve an FFIHeaderEntry to its matching ngx_table_elt_t by walking
 * the output header list with the entry's key bytes.
 *
 * Thin wrapper around ngx_http_markdown_plan_find_header that takes an
 * FFIHeaderEntry directly, so the apply paths don't each repeat the
 * (const u_char *) cast + entry->key_len dereference.
 *
 * Returns:
 *   pointer to the matching header, or NULL if not present.
 */
static ngx_table_elt_t *
ngx_http_markdown_plan_find_header_from_entry(ngx_http_request_t *r,
    const FFIHeaderEntry *entry)
{
    return ngx_http_markdown_plan_find_header(r,
        (const u_char *) entry->key, entry->key_len);
}


/*
 * delete_all visitor context: carries the saved-header array + count.
 */
typedef struct {
    ngx_http_markdown_plan_saved_header_t  *saved;
    ngx_uint_t                              count;
    ngx_uint_t                              saved_count;
} ngx_http_markdown_plan_delete_all_ctx_t;

#ifdef NGX_HTTP_MARKDOWN_HEADER_PLAN_TEST_HOOKS
static ngx_int_t ngx_http_markdown_plan_test_delete_all_visitor_hook(
    ngx_table_elt_t *h, void *ctx);
#endif


static void
ngx_http_markdown_plan_restore_delete_all_saved(
    ngx_http_markdown_plan_delete_all_ctx_t *dctx)
{
    ngx_uint_t  j;

    if (dctx->saved == NULL) {
        return;
    }

    /*
     * Restore both hash and value so every rollback path returns the
     * header list to its exact pre-delete_all state.  The overflow path
     * and the test-hook failure path both reuse this helper.
     */
    for (j = 0; j < dctx->saved_count; j++) {
        dctx->saved[j].header->hash = dctx->saved[j].orig_hash;
        dctx->saved[j].header->value = dctx->saved[j].orig_value;
    }
}


/*
 * delete_all visitor: save the matching header's original state and
 * invalidate it (hash=0).  Stops with NGX_ERROR if the saved-count
 * budget is exceeded (restoring previously saved entries first).
 */
static ngx_int_t
ngx_http_markdown_plan_delete_all_visitor(ngx_table_elt_t *h, void *ctx)
{
    ngx_http_markdown_plan_delete_all_ctx_t  *dctx = ctx;

    if (dctx->saved_count >= dctx->count) {
        /*
         * Overflow is a rollback path too: restore the saved headers
         * before failing so the caller never sees a partial delete_all.
         */
        ngx_http_markdown_plan_restore_delete_all_saved(dctx);
        return NGX_ERROR;
    }

    dctx->saved[dctx->saved_count].header = h;
    dctx->saved[dctx->saved_count].orig_hash = h->hash;
    dctx->saved[dctx->saved_count].orig_value = h->value;
    dctx->saved_count++;
    h->hash = 0;

#ifdef NGX_HTTP_MARKDOWN_HEADER_PLAN_TEST_HOOKS
    if (ngx_http_markdown_plan_test_delete_all_visitor_hook(h, dctx)
        != NGX_OK)
    {
        /*
         * Test-hook failures must also unwind the current header.  The
         * shared restore helper returns the full saved set to its original
         * hash/value state before we report failure upstream.
         */
        ngx_http_markdown_plan_restore_delete_all_saved(dctx);
        return NGX_ERROR;
    }
#endif

    return NGX_OK;
}


static ngx_int_t
ngx_http_markdown_plan_delete_all_headers(ngx_http_request_t *r,
    const FFIHeaderEntry *entry,
    ngx_http_markdown_plan_undo_t *undo)
{
    ngx_http_markdown_plan_saved_header_t  *saved;
    ngx_uint_t                              count;
    ngx_http_markdown_plan_delete_all_ctx_t dctx;

    if (entry->key == NULL || entry->key_len == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: DELETE_ALL entry has NULL/empty key");
        return NGX_ERROR;
    }

    undo->op_type = NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL;
    undo->header = NULL;
    undo->orig_hash = 0;
    undo->orig_value.data = NULL;
    undo->orig_value.len = 0;
    undo->saved = NULL;
    undo->saved_count = 0;

    count = ngx_http_markdown_plan_count_headers(r,
        (const u_char *) entry->key, entry->key_len);

    if (count == 0) {
        return NGX_OK;
    }

    if (count > NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: DELETE_ALL saved header count %uz exceeds "
            "maximum %d",
            (size_t) count,
            NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES);
        return NGX_ERROR;
    }

    saved = ngx_palloc(r->pool,
        count * sizeof(ngx_http_markdown_plan_saved_header_t));
    if (saved == NULL) {
        return NGX_ERROR;
    }

    dctx.saved = saved;
    dctx.count = count;
    dctx.saved_count = 0;

    if (ngx_http_markdown_plan_for_each_header_named(r,
            (const u_char *) entry->key, entry->key_len,
            ngx_http_markdown_plan_delete_all_visitor, &dctx) != NGX_OK)
    {
        return NGX_ERROR;
    }

    undo->saved = saved;
    undo->saved_count = dctx.saved_count;

    return NGX_OK;
}


/*
 * Apply a single SET operation: create or overwrite a header.
 *
 * If the header already exists, overwrites its value (saving the
 * original for rollback as a MODIFY-style undo).  If it does not
 * exist, pushes a new entry onto the header list.
 *
 * Special case: Content-Type is NOT stored as a list entry.  NGINX keeps
 * the response Content-Type in r->headers_out.content_type (a dedicated
 * field the header filter always emits).  A Content-Type SET therefore only
 * invalidates any stale list entry; the caller writes the dedicated field
 * after the plan commits.  This prevents a duplicate Content-Type header on
 * the wire.
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

    /* Defensive: validate key and value pointers from FFI (Rule 46). */
    if (ngx_http_markdown_plan_validate_entry(r, entry) != NGX_OK) {
        return NGX_ERROR;
    }

    /*
     * Content-Type is stored in r->headers_out.content_type (a dedicated
     * field the header filter always emits), not in the headers list.  The
     * caller sets that field after the plan commits.  If we pushed a list
     * entry here, the wire response would carry two Content-Type headers.
     * Instead, invalidate any stale Content-Type list entry (e.g. one a
     * future upstream might place in the list) and treat the operation as a
     * delete-style undo so rollback restores the original list state.  The
     * dedicated field itself is the caller's responsibility, kept outside
     * the plan's rollback scope by design.
     */
    if (ngx_http_markdown_plan_is_content_type(
            (const u_char *) entry->key, entry->key_len))
    {
        return ngx_http_markdown_plan_delete_all_headers(r, entry, undo);
    }

    h = ngx_http_markdown_plan_find_header_from_entry(r, entry);

    if (h != NULL) {
        /*
         * Header exists: save original value, then overwrite.
         * Treat as a modify for rollback purposes.
         */
        ngx_http_markdown_plan_save_modify_undo(undo,
            NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY, h);

        if (ngx_http_markdown_plan_copy_value_to_pool(r, entry, h) != NGX_OK) {
            return NGX_ERROR;
        }

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

    if (ngx_http_markdown_plan_copy_value_to_pool(r, entry, h) != NGX_OK) {
        h->hash = 0;
        return NGX_ERROR;
    }

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

    /* Defensive: validate key pointer from FFI. */
    if (entry->key == NULL || entry->key_len == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: DELETE entry has NULL/empty key");
        return NGX_ERROR;
    }

    h = ngx_http_markdown_plan_find_header_from_entry(r, entry);

    if (h == NULL) {
        /* Header not present; nothing to do. */
        ngx_http_markdown_plan_set_noop_undo(undo,
            NGX_HTTP_MARKDOWN_PLAN_OP_DELETE);
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

    /* Defensive: validate key and value pointers from FFI (Rule 46). */
    if (ngx_http_markdown_plan_validate_entry(r, entry) != NGX_OK) {
        return NGX_ERROR;
    }

    h = ngx_http_markdown_plan_find_header_from_entry(r, entry);

    if (h == NULL) {
        /* Header not present; nothing to modify. */
        ngx_http_markdown_plan_set_noop_undo(undo,
            NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY);
        return NGX_OK;
    }

    /* Save original value. */
    ngx_http_markdown_plan_save_modify_undo(undo,
        NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY, h);

    /* Allocate and copy new value. */
    if (ngx_http_markdown_plan_copy_value_to_pool(r, entry, h) != NGX_OK) {
        return NGX_ERROR;
    }

    return NGX_OK;
}


static void
ngx_http_markdown_plan_rollback_saved(
    ngx_http_markdown_plan_undo_t *entry)
{
    if (entry->saved == NULL) {
        return;
    }

    for (ngx_uint_t j = 0; j < entry->saved_count; j++) {
        entry->saved[j].header->hash = entry->saved[j].orig_hash;
        entry->saved[j].header->value = entry->saved[j].orig_value;
    }
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

        if (undo[i].header == NULL
            && undo[i].op_type != NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL)
        {
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

        case NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL:
            ngx_http_markdown_plan_rollback_saved(&undo[i]);
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

    /* Defensive check: entries pointer must be valid when count > 0. */
    if (plan->entries == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: plan has %uz entries "
            "but entries pointer is NULL",
            (size_t) plan->count);
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
    for (uintptr_t i = 0; i < plan->count; i++) {
        entry = &plan->entries[i];

        switch (entry->op_type) {

        case NGX_HTTP_MARKDOWN_PLAN_OP_SET:
            rc = ngx_http_markdown_plan_apply_set(r, entry, &undo[i]);
            break;

        case NGX_HTTP_MARKDOWN_PLAN_OP_DELETE:
            rc = ngx_http_markdown_plan_apply_delete(r, entry,
                &undo[i]);
            break;

        case NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL:
            rc = ngx_http_markdown_plan_delete_all_headers(r, entry,
                &undo[i]);
            break;

        case NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY:
            if (entry->key == NULL && entry->key_len == 0
                && entry->value == NULL && entry->value_len == 0)
            {
                /*
                 * Rust uses op_type 2 for set-etag-placeholder.  The real
                 * ETag is written by the caller after the plan commits.
                 */
                undo[i].op_type = NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY;
                undo[i].header = NULL;
                undo[i].orig_hash = 0;
                undo[i].orig_value.data = NULL;
                undo[i].orig_value.len = 0;
                rc = NGX_OK;
                break;
            }

            rc = ngx_http_markdown_plan_apply_modify(r, entry, &undo[i]);
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
