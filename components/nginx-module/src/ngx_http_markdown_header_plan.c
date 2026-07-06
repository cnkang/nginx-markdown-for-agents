/*
 * NGINX Markdown Filter Module - Header Plan Atomic Application
 *
 * Implements atomic application of Rust-built header plans to NGINX
 * response headers using an explicit two-phase prepare/commit model
 * (spec 48):
 *
 *   prepare  - performs every fallible step (pool allocation, string
 *              copies, header lookups, list-capacity and operation
 *              validation) and records a prepared-operation array.  It
 *              MUST NOT mutate any pre-existing r->headers_out field.
 *              The single exception is that a freshly pushed list slot
 *              is initialized to the inert (hash==0) state so that an
 *              aborted prepare is observably equivalent to a no-op
 *              (Rule 40 filters hash==0 entries everywhere).
 *
 *   commit   - executes the already-prepared mutations.  It performs
 *              ONLY pointer/scalar assignments (no allocation, no
 *              lookup, no validation) and therefore has no failure
 *              path: once prepare succeeds, commit cannot fail.
 *
 * This replaces the prior rollback model (allocate-while-mutating, undo
 * on failure).  Because NGINX pool allocations cannot be released
 * individually, all allocation is hoisted into prepare; a prepare
 * failure leaves r->headers_out unchanged and the partially allocated
 * pool memory is reclaimed when the request pool is destroyed.
 *
 * REQ-0700-RUST-004: Header plan atomicity.
 * spec 48: prepare/commit two-phase, allocation-free commit, no partial
 * mutation.
 * Rule 28: full ngx_list_part_t chain iteration.
 * Rule 29: clear/free flags after a gated op succeeds, not before.
 * Rule 39: multi-step header modification is atomic (abort on first
 *          failure, no partial apply); bounded snapshots fail before
 *          mutation.
 * Rule 40: header lookup/iteration filters hash==0 (invalidated) entries.
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
 * Commit actions derived during prepare.  The commit phase dispatches on
 * these (never on the raw FFI op_type) so that all validation/branching
 * lives in prepare and commit stays a flat, allocation-free apply loop.
 */
#define NGX_HTTP_MARKDOWN_PLAN_ACT_NOOP        0
#define NGX_HTTP_MARKDOWN_PLAN_ACT_SET_NEW     1
#define NGX_HTTP_MARKDOWN_PLAN_ACT_OVERWRITE   2
#define NGX_HTTP_MARKDOWN_PLAN_ACT_DELETE      3
#define NGX_HTTP_MARKDOWN_PLAN_ACT_DELETE_ALL  4

/*
 * Maximum number of header plan entries supported.  Plans exceeding this
 * count are rejected to bound prepared-array and matches-array sizes.
 */
#define NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES  64


/*
 * A single prepared operation produced by the prepare phase and consumed
 * (assignment-only) by the commit phase.
 *
 * Invariants:
 *   - All memory referenced here (new_key/new_value bytes, matches array)
 *     is allocated from r->pool during prepare.
 *   - commit performs only the assignment dictated by `action`; it never
 *     allocates, looks up, or validates.
 */
typedef struct {
    uint8_t            action;

    /*
     * SET_NEW   - freshly pushed (inert hash==0) list slot to populate.
     * OVERWRITE - existing list entry whose value is replaced.
     * DELETE    - existing list entry to invalidate (hash=0).
     */
    ngx_table_elt_t   *header;

    /* Pre-copied key bytes (SET_NEW only). */
    ngx_str_t          new_key;

    /* Pre-copied value bytes (SET_NEW / OVERWRITE). */
    ngx_str_t          new_value;

    /* DELETE_ALL: pre-collected matching entries to invalidate. */
    ngx_table_elt_t  **matches;
    ngx_uint_t         match_count;
} ngx_http_markdown_plan_prepared_t;


#ifdef NGX_MARKDOWN_FAULT_INJECTION
/*
 * Test-only fault injection: when set to a non-negative op index, the
 * prepare phase fails before preparing the operation at that index,
 * simulating an allocation/validation failure at an arbitrary position.
 * Compiled out of production builds (macro undefined).
 */
static ngx_int_t  ngx_http_markdown_plan_fault_op = -1;

void
ngx_http_markdown_header_plan_set_fault_injection(ngx_int_t op_index)
{
    ngx_http_markdown_plan_fault_op = op_index;
}
#endif


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
 * Validate that an FFIHeaderEntry has a non-NULL/non-empty key and a
 * value pointer consistent with its declared length.
 *
 * Shared by the SET and MODIFY prepare paths so they cannot drift apart on
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
 * Copy an FFI entry's value bytes into a fresh pool allocation, returning
 * the result through *out.  A zero-length value yields {NULL, 0} (Rule 46:
 * zero-length value maps to a NULL field, not a zero-length allocation).
 *
 * Returns NGX_OK on success, NGX_ERROR on allocation failure.
 */
static ngx_int_t
ngx_http_markdown_plan_dup_value(ngx_http_request_t *r,
    const FFIHeaderEntry *entry, ngx_str_t *out)
{
    u_char  *pool_val;

    if (entry->value_len == 0) {
        out->data = NULL;
        out->len = 0;
        return NGX_OK;
    }

    pool_val = ngx_pnalloc(r->pool, entry->value_len);
    if (pool_val == NULL) {
        return NGX_ERROR;
    }

    ngx_memcpy(pool_val, entry->value, entry->value_len);
    out->data = pool_val;
    out->len = entry->value_len;

    return NGX_OK;
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
 * delete_all collection visitor context: carries the matches array, its
 * capacity, and the running fill count.  The visitor only RECORDS matching
 * entries; it never mutates them (mutation happens in commit).
 */
typedef struct {
    ngx_table_elt_t  **matches;
    ngx_uint_t         capacity;
    ngx_uint_t         count;
} ngx_http_markdown_plan_collect_ctx_t;

#ifdef NGX_HTTP_MARKDOWN_HEADER_PLAN_TEST_HOOKS
static ngx_int_t ngx_http_markdown_plan_test_delete_all_visitor_hook(
    ngx_table_elt_t *h, void *ctx);
#endif

#ifdef NGX_HTTP_MARKDOWN_HEADER_PLAN_COMMIT_HOOK
/*
 * Test-only hook invoked exactly once at the prepare->commit boundary so
 * tests can assert the commit phase performs no pool allocation.  Compiled
 * out of production builds (macro undefined).
 */
void ngx_http_markdown_plan_test_commit_begin_hook(void);
#endif


/*
 * delete_all collection visitor: record the matching header pointer.
 *
 * Stops with NGX_ERROR if the matches budget is exceeded (capacity was
 * sized from a pre-count, so this is a defensive guard against a list that
 * grew between counting and collection).  Performs NO mutation, so an
 * aborted collection leaves r->headers_out unchanged.
 */
static ngx_int_t
ngx_http_markdown_plan_collect_visitor(ngx_table_elt_t *h, void *ctx)
{
    ngx_http_markdown_plan_collect_ctx_t  *cctx = ctx;

    if (cctx->count >= cctx->capacity) {
        return NGX_ERROR;
    }

#ifdef NGX_HTTP_MARKDOWN_HEADER_PLAN_TEST_HOOKS
    if (ngx_http_markdown_plan_test_delete_all_visitor_hook(h, cctx)
        != NGX_OK)
    {
        return NGX_ERROR;
    }
#endif

    cctx->matches[cctx->count] = h;
    cctx->count++;

    return NGX_OK;
}


/*
 * Prepare a DELETE_ALL (or Content-Type-as-delete-all) operation.
 *
 * Counts matching entries, allocates a pointer array from the pool, and
 * collects the matching entries WITHOUT invalidating them.  The commit
 * phase performs the hash=0 invalidation.
 *
 * Returns NGX_OK on success (including the no-match no-op) or NGX_ERROR on
 * a NULL key, count overflow, or allocation failure.
 */
static ngx_int_t
ngx_http_markdown_plan_prepare_delete_all(ngx_http_request_t *r,
    const FFIHeaderEntry *entry,
    ngx_http_markdown_plan_prepared_t *prep)
{
    ngx_table_elt_t                     **matches;
    ngx_uint_t                            count;
    ngx_http_markdown_plan_collect_ctx_t  cctx;

    prep->action = NGX_HTTP_MARKDOWN_PLAN_ACT_NOOP;
    prep->matches = NULL;
    prep->match_count = 0;

    if (entry->key == NULL || entry->key_len == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: DELETE_ALL entry has NULL/empty key");
        return NGX_ERROR;
    }

    count = ngx_http_markdown_plan_count_headers(r,
        (const u_char *) entry->key, entry->key_len);

    if (count == 0) {
        return NGX_OK;
    }

    if (count > NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: DELETE_ALL matched header count %uz exceeds "
            "maximum %d",
            (size_t) count,
            NGX_HTTP_MARKDOWN_PLAN_MAX_ENTRIES);
        return NGX_ERROR;
    }

    matches = ngx_palloc(r->pool, count * sizeof(ngx_table_elt_t *));
    if (matches == NULL) {
        return NGX_ERROR;
    }

    cctx.matches = matches;
    cctx.capacity = count;
    cctx.count = 0;

    if (ngx_http_markdown_plan_for_each_header_named(r,
            (const u_char *) entry->key, entry->key_len,
            ngx_http_markdown_plan_collect_visitor, &cctx) != NGX_OK)
    {
        return NGX_ERROR;
    }

    prep->action = NGX_HTTP_MARKDOWN_PLAN_ACT_DELETE_ALL;
    prep->matches = matches;
    prep->match_count = cctx.count;

    return NGX_OK;
}


/*
 * Prepare a SET operation: create-or-overwrite a header.
 *
 * Content-Type is redirected to the delete-all path (it lives in the
 * dedicated headers_out.content_type field, never the list; see
 * ngx_http_markdown_plan_is_content_type).
 *
 * For an existing header: copy the new value to the pool and record an
 * OVERWRITE (the value is NOT written until commit).
 *
 * For a new header: push a list slot, initialize it to the inert state
 * (hash=0) so an aborted prepare is observably a no-op, copy the key and
 * value to the pool, and record a SET_NEW (key/value/hash written at
 * commit).
 *
 * Returns NGX_OK on success, NGX_ERROR on validation/allocation failure.
 */
static ngx_int_t
ngx_http_markdown_plan_prepare_set(ngx_http_request_t *r,
    const FFIHeaderEntry *entry,
    ngx_http_markdown_plan_prepared_t *prep)
{
    ngx_table_elt_t  *h;

    if (ngx_http_markdown_plan_validate_entry(r, entry) != NGX_OK) {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_plan_is_content_type(
            (const u_char *) entry->key, entry->key_len))
    {
        return ngx_http_markdown_plan_prepare_delete_all(r, entry, prep);
    }

    h = ngx_http_markdown_plan_find_header_from_entry(r, entry);

    if (h != NULL) {
        if (ngx_http_markdown_plan_dup_value(r, entry, &prep->new_value)
            != NGX_OK)
        {
            return NGX_ERROR;
        }

        prep->action = NGX_HTTP_MARKDOWN_PLAN_ACT_OVERWRITE;
        prep->header = h;

        return NGX_OK;
    }

    /* Header does not exist: push an inert slot, copy key and value. */
    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    /*
     * Initialize the freshly pushed slot to inert (hash==0) so that if a
     * later prepare step fails, this slot is never emitted (Rule 40).  Its
     * key/value are written by commit.  This is the only headers_out write
     * prepare performs, and it touches only a brand-new slot, never a
     * pre-existing header.
     */
    h->hash = 0;
    h->key.data = NULL;
    h->key.len = 0;
    h->value.data = NULL;
    h->value.len = 0;

    prep->new_key.data = ngx_pnalloc(r->pool, entry->key_len);
    if (prep->new_key.data == NULL) {
        return NGX_ERROR;
    }
    ngx_memcpy(prep->new_key.data, entry->key, entry->key_len);
    prep->new_key.len = entry->key_len;

    if (ngx_http_markdown_plan_dup_value(r, entry, &prep->new_value)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    prep->action = NGX_HTTP_MARKDOWN_PLAN_ACT_SET_NEW;
    prep->header = h;

    return NGX_OK;
}


/*
 * Prepare a DELETE operation: locate the first matching header.
 *
 * Records a DELETE (commit invalidates) or a no-op when the header is
 * absent.  Performs no mutation.
 *
 * Returns NGX_OK on success/no-op, NGX_ERROR on a NULL key.
 */
static ngx_int_t
ngx_http_markdown_plan_prepare_delete(ngx_http_request_t *r,
    const FFIHeaderEntry *entry,
    ngx_http_markdown_plan_prepared_t *prep)
{
    ngx_table_elt_t  *h;

    if (entry->key == NULL || entry->key_len == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: DELETE entry has NULL/empty key");
        return NGX_ERROR;
    }

    h = ngx_http_markdown_plan_find_header_from_entry(r, entry);

    if (h == NULL) {
        prep->action = NGX_HTTP_MARKDOWN_PLAN_ACT_NOOP;
        return NGX_OK;
    }

    prep->action = NGX_HTTP_MARKDOWN_PLAN_ACT_DELETE;
    prep->header = h;

    return NGX_OK;
}


/*
 * Prepare a MODIFY operation: change an existing header's value.
 *
 * The all-zero entry (NULL key/value, zero lengths) is the
 * set-etag-placeholder contract (FFI op_type 2): the real ETag is written
 * by the caller after the plan commits, so this is recorded as a no-op.
 *
 * For a present header: copy the new value to the pool and record an
 * OVERWRITE.  For an absent header: no-op.
 *
 * Returns NGX_OK on success/no-op, NGX_ERROR on validation/allocation
 * failure.
 */
static ngx_int_t
ngx_http_markdown_plan_prepare_modify(ngx_http_request_t *r,
    const FFIHeaderEntry *entry,
    ngx_http_markdown_plan_prepared_t *prep)
{
    ngx_table_elt_t  *h;

    if (entry->key == NULL && entry->key_len == 0
        && entry->value == NULL && entry->value_len == 0)
    {
        /* set-etag-placeholder: resolved by the caller post-commit. */
        prep->action = NGX_HTTP_MARKDOWN_PLAN_ACT_NOOP;
        return NGX_OK;
    }

    if (ngx_http_markdown_plan_validate_entry(r, entry) != NGX_OK) {
        return NGX_ERROR;
    }

    h = ngx_http_markdown_plan_find_header_from_entry(r, entry);

    if (h == NULL) {
        prep->action = NGX_HTTP_MARKDOWN_PLAN_ACT_NOOP;
        return NGX_OK;
    }

    if (ngx_http_markdown_plan_dup_value(r, entry, &prep->new_value)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    prep->action = NGX_HTTP_MARKDOWN_PLAN_ACT_OVERWRITE;
    prep->header = h;

    return NGX_OK;
}


/*
 * Prepare a single plan entry: dispatch on op_type and populate `prep`.
 *
 * Returns NGX_OK on success, NGX_ERROR on validation/allocation failure or
 * an unknown op_type.
 */
static ngx_int_t
ngx_http_markdown_plan_prepare_entry(ngx_http_request_t *r,
    const FFIHeaderEntry *entry,
    ngx_http_markdown_plan_prepared_t *prep)
{
    switch (entry->op_type) {

    case NGX_HTTP_MARKDOWN_PLAN_OP_SET:
        return ngx_http_markdown_plan_prepare_set(r, entry, prep);

    case NGX_HTTP_MARKDOWN_PLAN_OP_DELETE:
        return ngx_http_markdown_plan_prepare_delete(r, entry, prep);

    case NGX_HTTP_MARKDOWN_PLAN_OP_DELETE_ALL:
        return ngx_http_markdown_plan_prepare_delete_all(r, entry, prep);

    case NGX_HTTP_MARKDOWN_PLAN_OP_MODIFY:
        return ngx_http_markdown_plan_prepare_modify(r, entry, prep);

    default:
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: unknown op_type %d",
            (int) entry->op_type);
        return NGX_ERROR;
    }
}


/*
 * Commit a single prepared operation.
 *
 * Performs ONLY assignments using memory allocated during prepare.  This
 * function has no allocation, lookup, validation, or failure path
 * (spec 48 commit invariant).
 */
static void
ngx_http_markdown_plan_commit_one(
    const ngx_http_markdown_plan_prepared_t *prep)
{
    switch (prep->action) {

    case NGX_HTTP_MARKDOWN_PLAN_ACT_SET_NEW:
        prep->header->key = prep->new_key;
        prep->header->value = prep->new_value;
        prep->header->hash = 1;
        break;

    case NGX_HTTP_MARKDOWN_PLAN_ACT_OVERWRITE:
        prep->header->value = prep->new_value;
        break;

    case NGX_HTTP_MARKDOWN_PLAN_ACT_DELETE:
        prep->header->hash = 0;
        break;

    case NGX_HTTP_MARKDOWN_PLAN_ACT_DELETE_ALL:
        for (ngx_uint_t j = 0; j < prep->match_count; j++) {
            prep->matches[j]->hash = 0;
        }
        break;

    case NGX_HTTP_MARKDOWN_PLAN_ACT_NOOP:
    default:
        break;
    }
}


ngx_int_t
ngx_http_markdown_apply_header_plan(ngx_http_request_t *r,
    struct FFIHeaderPlan *plan)
{
    ngx_http_markdown_plan_prepared_t  *prepared;
    const FFIHeaderEntry               *entry;

    /* NULL plan is a no-op; nothing to free. */
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

    /* Reject plans that exceed the prepared-array bound. */
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

    /*
     * Prepare phase: allocate the prepared-operation array, then prepare
     * each entry (allocation + validation + lookup).  On any failure the
     * plan is aborted before commit, so r->headers_out is unchanged
     * (aborted SET_NEW slots stay inert, hash==0).  Pool memory allocated
     * so far is reclaimed when the request pool is destroyed.
     */
    prepared = ngx_palloc(r->pool,
        plan->count * sizeof(ngx_http_markdown_plan_prepared_t));
    if (prepared == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: failed to allocate "
            "prepared array for %uz entries",
            (size_t) plan->count);
        markdown_header_plan_free(plan);
        return NGX_ERROR;
    }

    for (uintptr_t i = 0; i < plan->count; i++) {
        entry = &plan->entries[i];

        /* Initialize the prepared slot to a safe no-op shape. */
        prepared[i].action = NGX_HTTP_MARKDOWN_PLAN_ACT_NOOP;
        prepared[i].header = NULL;
        prepared[i].new_key.data = NULL;
        prepared[i].new_key.len = 0;
        prepared[i].new_value.data = NULL;
        prepared[i].new_value.len = 0;
        prepared[i].matches = NULL;
        prepared[i].match_count = 0;

#ifdef NGX_MARKDOWN_FAULT_INJECTION
        if (ngx_http_markdown_plan_fault_op >= 0
            && (uintptr_t) ngx_http_markdown_plan_fault_op == i)
        {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown: fault injection at prepare index %uz",
                (size_t) i);
            markdown_header_plan_free(plan);
            return NGX_ERROR;
        }
#endif

        if (ngx_http_markdown_plan_prepare_entry(r, entry, &prepared[i])
            != NGX_OK)
        {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown: prepare failed for op %d "
                "at index %uz; no header changes applied",
                (int) entry->op_type, (size_t) i);

            markdown_header_plan_free(plan);
            return NGX_ERROR;
        }
    }

    /*
     * Commit phase: assignment-only, no allocation, no failure path.
     * Either every prepared mutation is applied or (had prepare failed)
     * none were.
     */
#ifdef NGX_HTTP_MARKDOWN_HEADER_PLAN_COMMIT_HOOK
    ngx_http_markdown_plan_test_commit_begin_hook();
#endif
    for (uintptr_t i = 0; i < plan->count; i++) {
        ngx_http_markdown_plan_commit_one(&prepared[i]);
    }

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
        "markdown: applied %uz entries "
        "successfully",
        (size_t) plan->count);

    /*
     * All operations committed.  Free the Rust-owned plan.
     * Rule 29: free after the gated op succeeds.
     */
    markdown_header_plan_free(plan);

    return NGX_OK;
}
