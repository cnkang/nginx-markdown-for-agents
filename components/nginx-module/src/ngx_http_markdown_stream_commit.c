/*
 * Streaming Fallback State Machine — Header Commit Sequence
 *
 * Implements the atomic header commit sequence for streaming mode.
 * This is the single authoritative path for all streaming header
 * mutations.  No other code path may mutate streaming response headers.
 *
 * Two-phase design with transactional rollback (Rule 39):
 *   Phase 1 (fallible): Vary: Accept, ETag removal, auth Cache-Control.
 *     Before Phase 1 begins, the original state of every header that
 *     Phase 1 may touch is snapshotted.  If any Phase 1 step fails,
 *     the snapshot is used to roll back all prior Phase 1 mutations
 *     so headers_out is restored to its pre-commit state.  This
 *     guarantees that the caller's fallback / fail-open path sees
 *     the original upstream headers, not a partially-mutated set.
 *   Phase 2 (infallible): Content-Type, Content-Length, Content-Encoding.
 *     These are pointer/integer writes that cannot fail.
 *   Only after both phases complete are headers_committed and state set.
 *
 * Rollback scope (Rule 39 transactional guarantee):
 *   The snapshot/rollback covers Vary, ETag, and Cache-Control.
 *   Modified entries have value/hash restored; newly-pushed entries
 *   are invalidated via hash=0 (Rule 40); typed ETag pointer restored.
 *   No use-after-free/double-free/dangling pointer under pool model.
 *
 * Header commit safety invariant:
 *   Header decisions MUST be completed before outgoing headers are
 *   mutated.  Header mutation is applied in a single final step
 *   after the decision is known.  If the decision fails before
 *   header send, no partial Markdown response may be committed.
 */

#include "ngx_http_markdown_stream_commit.h"


/* ------------------------------------------------------------------ */
/*  Snapshot / rollback structures for Rule 39 transactional atomicity */
/* ------------------------------------------------------------------ */

/*
 * Maximum number of header entries tracked per single header name
 * (Vary, ETag, Cache-Control).  Keep this bound aligned with the header-plan
 * capacity and fail before Phase 1 mutation when it is exceeded, preserving
 * Rule 39 atomicity without imposing the old eight-entry limit.
 */
#define NGX_HTTP_MARKDOWN_COMMIT_SNAPSHOT_MAX  64

/*
 * Per-header snapshot: records the original state of a header entry
 * that Phase 1 may mutate, so it can be restored on rollback.
 */
typedef struct {
    ngx_table_elt_t  *entry;       /* pointer to the header in headers_out */
    ngx_str_t         orig_value;  /* original value.data / value.len      */
    ngx_uint_t        orig_hash;   /* original hash (0 = absent/invalid)   */
} ngx_http_markdown_hdr_snap_entry_t;

/*
 * Snapshot for one named header (e.g. Vary).  Also records the
 * headers list nelts before Phase 1 so newly-pushed entries can be
 * invalidated on rollback.
 */
typedef struct {
    ngx_http_markdown_hdr_snap_entry_t  entries[NGX_HTTP_MARKDOWN_COMMIT_SNAPSHOT_MAX];
    ngx_uint_t                          count;       /* entries snapshotted */
    ngx_uint_t                          orig_nelts;  /* list nelts before Phase 1 */
} ngx_http_markdown_hdr_snap_t;

/*
 * Full Phase 1 snapshot: Vary, ETag, Cache-Control plus the typed
 * ETag pointer from headers_out.
 */
typedef struct {
    ngx_http_markdown_hdr_snap_t  vary;
    ngx_http_markdown_hdr_snap_t  etag;
    ngx_http_markdown_hdr_snap_t  cache_control;
    ngx_table_elt_t              *orig_etag_ptr;  /* r->headers_out.etag */
} ngx_http_markdown_commit_snap_t;


/* ------------------------------------------------------------------ */
/*  Snapshot helpers                                                   */
/* ------------------------------------------------------------------ */

static ngx_flag_t
ngx_http_markdown_stream_commit_header_matches(
    const ngx_table_elt_t *entry, const u_char *name, size_t name_len)
{
    if (entry->key.len != name_len) {
        return 0;
    }

    for (ngx_uint_t i = 0; i < name_len; i++) {
        if (ngx_tolower(entry->key.data[i]) != ngx_tolower(name[i])) {
            return 0;
        }
    }

    return 1;
}

static ngx_int_t
ngx_http_markdown_stream_commit_snapshot_entry(
    ngx_http_markdown_hdr_snap_t *snap, ngx_table_elt_t *entry)
{
    if (snap->count >= NGX_HTTP_MARKDOWN_COMMIT_SNAPSHOT_MAX) {
        return NGX_ERROR;
    }

    snap->entries[snap->count].entry = entry;
    snap->entries[snap->count].orig_value = entry->value;
    snap->entries[snap->count].orig_hash = entry->hash;
    snap->count++;

    return NGX_OK;
}

/*
 * Snapshot all entries matching a header name in the headers_out list.
 * Records entry pointer, original value, and original hash for each.  Returns
 * NGX_ERROR before mutation if the bounded snapshot capacity is exceeded.
 */
static ngx_int_t
ngx_http_markdown_stream_commit_snapshot_header(
    ngx_http_request_t *r,
    const u_char *name, size_t name_len,
    ngx_http_markdown_hdr_snap_t *snap)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *elts;

    snap->count = 0;
    snap->orig_nelts = 0;

    part = &r->headers_out.headers.part;

    while (part != NULL) {
        elts = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            snap->orig_nelts++;

            if (elts[i].hash == 0) {
                continue;
            }

            if (!ngx_http_markdown_stream_commit_header_matches(
                    &elts[i], name, name_len))
            {
                continue;
            }

            if (ngx_http_markdown_stream_commit_snapshot_entry(
                    snap, &elts[i]) != NGX_OK)
            {
                return NGX_ERROR;
            }
        }
        part = part->next;
    }

    return NGX_OK;
}

/*
 * Roll back a single header: restore original value/hash on snapshotted
 * entries, then invalidate any entries that were pushed after the snapshot
 * (i.e. entries with index >= orig_nelts that match the header name).
 * orig_nelts is the total linear entry count across all list parts; rollback
 * uses the same linear traversal index.
 */
static void
ngx_http_markdown_stream_commit_rollback_header(
    ngx_http_request_t *r,
    const u_char *name, size_t name_len,
    ngx_http_markdown_hdr_snap_t *snap)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *elts;
    ngx_uint_t        idx;

    /* Restore snapshotted entries */
    for (ngx_uint_t i = 0; i < snap->count; i++) {
        if (snap->entries[i].entry != NULL) {
            snap->entries[i].entry->value = snap->entries[i].orig_value;
            snap->entries[i].entry->hash = snap->entries[i].orig_hash;
        }
    }

    /* Invalidate newly-pushed entries (hash=0 per Rule 40) */
    part = &r->headers_out.headers.part;
    idx = 0;

    while (part != NULL) {
        elts = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (idx >= snap->orig_nelts
                && elts[i].hash != 0
                && ngx_http_markdown_stream_commit_header_matches(
                    &elts[i], name, name_len))
            {
                elts[i].hash = 0;
            }
            idx++;
        }
        part = part->next;
    }
}

/*
 * Take a full Phase 1 snapshot: Vary, ETag, Cache-Control,
 * and the typed ETag pointer.
 */
static ngx_int_t
ngx_http_markdown_stream_commit_take_snapshot(
    ngx_http_request_t *r, ngx_http_markdown_commit_snap_t *snap)
{
    if (ngx_http_markdown_stream_commit_snapshot_header(
            r, (const u_char *) "Vary", 4, &snap->vary) != NGX_OK)
    {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_stream_commit_snapshot_header(
            r, (const u_char *) "ETag", 4, &snap->etag) != NGX_OK)
    {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_stream_commit_snapshot_header(
            r, (const u_char *) "Cache-Control", 13,
            &snap->cache_control) != NGX_OK)
    {
        return NGX_ERROR;
    }

    snap->orig_etag_ptr = r->headers_out.etag;

    return NGX_OK;
}

/*
 * Roll back all Phase 1 mutations using the snapshot.
 */
static void
ngx_http_markdown_stream_commit_rollback(
    ngx_http_request_t *r, ngx_http_markdown_commit_snap_t *snap)
{
    ngx_http_markdown_stream_commit_rollback_header(
        r, (const u_char *) "Vary", 4, &snap->vary);

    ngx_http_markdown_stream_commit_rollback_header(
        r, (const u_char *) "ETag", 4, &snap->etag);

    ngx_http_markdown_stream_commit_rollback_header(
        r, (const u_char *) "Cache-Control", 13, &snap->cache_control);

    /* Restore typed ETag pointer */
    r->headers_out.etag = snap->orig_etag_ptr;
}


/* ------------------------------------------------------------------ */
/*  Function prototypes                                                */
/* ------------------------------------------------------------------ */

static ngx_int_t
ngx_http_markdown_stream_commit_remove_content_length(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_maybe_remove_content_encoding(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx);

static ngx_int_t
ngx_http_markdown_stream_commit_set_content_type(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_set_vary(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_remove_etag(
    ngx_http_request_t *r);

static ngx_int_t
ngx_http_markdown_stream_commit_remove_representation_metadata(
    ngx_http_request_t *r);


/*
 * Execute the streaming header commit sequence.
 *
 * Phase 1 (fallible): Vary, ETag, auth Cache-Control.
 *   If any fails, no mutations are visible, NGX_ERROR returned.
 *   The caller can safely fall back to upstream HTML passthrough.
 *
 * Phase 2 (infallible): Content-Type, Content-Length, Content-Encoding.
 *   These are pointer/integer assignments that cannot fail.
 *
 * On success, applies all header mutations and records the COMMITTED state.
 * The streaming caller treats NGX_AGAIN from the downstream header filter as
 * accepted headers (canonical NGINX model) and publishes its commit latches
 * and committed state; no rollback is performed on NGX_AGAIN.
 *
 * Returns:
 *   NGX_OK    - All mutations applied, committed flag set
 *   NGX_ERROR - Precondition failure or fallible-mutation error
 */
ngx_int_t
ngx_http_markdown_stream_commit_headers(ngx_http_request_t *r,
                                         ngx_http_markdown_ctx_t *ctx,
                                         const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t                        rc;
    ngx_flag_t                       auth_cache_control_required;
    ngx_http_markdown_commit_snap_t  snap;

    auth_cache_control_required = 0;

    if (r == NULL || ctx == NULL) {
        return NGX_ERROR;
    }

    if (ctx->stream_sm.headers_committed) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: stream commit: "
                      "headers already committed");
        return NGX_ERROR;
    }

    if (ctx->stream_sm.state != NGX_HTTP_MD_STATE_PRE_COMMIT
        && ctx->stream_sm.state
           != NGX_HTTP_MD_STATE_PRE_COMMIT_REPLAY_UNAVAILABLE)
    {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: stream commit: "
                      "invalid state %ui for commit",
                      (ngx_uint_t) ctx->stream_sm.state);
        return NGX_ERROR;
    }

    /*
     * --- Phase 1: Fallible header operations ---
     *
     * Snapshot the original header state before any mutation.
     * If any step fails, roll back all prior mutations so
     * headers_out is restored to its pre-commit state.
     */

    rc = ngx_http_markdown_stream_commit_take_snapshot(r, &snap);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: stream commit: "
                      "snapshot capacity exceeded before header mutation");
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_stream_commit_set_vary(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: stream commit: "
                      "failed to set Vary header");
        ngx_http_markdown_stream_commit_rollback(r, &snap);
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_stream_commit_remove_etag(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: stream commit: "
                      "failed to remove ETag");
        ngx_http_markdown_stream_commit_rollback(r, &snap);
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_auth_cache_control_required(
        r, conf, &auth_cache_control_required);
    if (rc == NGX_OK && auth_cache_control_required) {
        rc = ngx_http_markdown_modify_cache_control_for_auth(r);
    }
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: stream commit: "
                      "failed to apply auth Cache-Control");
        ngx_http_markdown_stream_commit_rollback(r, &snap);
        return NGX_ERROR;
    }

    /*
     * --- Phase 2: Infallible mutations ---
     *
     * These operations are pointer/integer assignments that cannot
     * fail.  They are ordered so that Content-Type is set first
     * (establishing the Markdown content type) followed by
     * Content-Length and Content-Encoding removal.  Return values
     * are intentionally not checked — all three functions are
     * documented as always returning NGX_OK.
     */

    (void) ngx_http_markdown_stream_commit_set_content_type(r);
    (void) ngx_http_markdown_stream_commit_remove_content_length(r);
    (void) ngx_http_markdown_stream_commit_maybe_remove_content_encoding(
        r, ctx);
    (void) ngx_http_markdown_stream_commit_remove_representation_metadata(r);

    /*
     * All mutations succeeded — set committed flag.
     * After this point, no HTML fallback is possible.
     */
    ctx->stream_sm.headers_committed = 1;
    ctx->stream_sm.state = NGX_HTTP_MD_STATE_COMMITTED;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: stream commit: "
                   "headers committed successfully");

    return NGX_OK;
}


/*
 * Remove Content-Length header.
 *
 * Streaming responses have unknown final length.  Clear the
 * Content-Length numeric field and invalidate the header entry
 * (hash=0, pointer=NULL) per Rule 40 so NGINX does not emit it
 * downstream.
 *
 * Returns:
 *   NGX_OK always (removal cannot fail)
 */
static ngx_int_t
ngx_http_markdown_stream_commit_remove_content_length(
    ngx_http_request_t *r)
{
    r->headers_out.content_length_n = -1;

    if (r->headers_out.content_length != NULL) {
        r->headers_out.content_length->hash = 0;
        r->headers_out.content_length = NULL;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: stream commit: "
                   "removed Content-Length");

    return NGX_OK;
}


/*
 * Remove Content-Encoding header conditionally.
 *
 * Only removes Content-Encoding when decompression was performed or
 * needed for this request.  When no decompression occurred, the
 * upstream encoding is valid and must be preserved.
 *
 * Returns:
 *   NGX_OK always
 */
static ngx_int_t
ngx_http_markdown_stream_commit_maybe_remove_content_encoding(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx)
{
    if (ctx->decompression.needed) {
        ngx_http_markdown_remove_content_encoding(r);
    }

    return NGX_OK;
}


/*
 * Set Content-Type to text/markdown; charset=utf-8.
 *
 * Uses the shared NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL constant.
 * Sets the dedicated r->headers_out.content_type field (NGINX emits
 * Content-Type from this field, not from the headers list).
 *
 * Returns:
 *   NGX_OK always (assignment cannot fail)
 */
static ngx_int_t
ngx_http_markdown_stream_commit_set_content_type(
    ngx_http_request_t *r)
{
    /* Shared representation helper: deletes stale Content-Type list
     * entries first, then sets the dedicated field and its charset/
     * lowcase/hash mirrors to the Markdown media type. */
    ngx_http_markdown_set_representation_content_type(r);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: stream commit: "
                   "set Content-Type: text/markdown; charset=utf-8");

    return NGX_OK;
}


/*
 * Set Vary: Accept response header.
 *
 * Delegates to the shared ngx_http_markdown_add_vary_accept()
 * helper.  This is the only fallible operation in the commit
 * sequence that can fail due to pool allocation.
 *
 * Returns:
 *   NGX_OK    on success
 *   NGX_ERROR on allocation failure
 */
static ngx_int_t
ngx_http_markdown_stream_commit_set_vary(
    ngx_http_request_t *r)
{
    return ngx_http_markdown_add_vary_accept(r);
}


/*
 * Remove upstream ETag header.
 *
 * The upstream HTML ETag is invalid for the Markdown variant since
 * the content is different.  Delegates to the shared
 * ngx_http_markdown_set_etag() helper with NULL/0 to clear it.
 *
 * Returns:
 *   NGX_OK    on success
 *   NGX_ERROR on failure
 */
static ngx_int_t
ngx_http_markdown_stream_commit_remove_etag(
    ngx_http_request_t *r)
{
    return ngx_http_markdown_set_etag(r, NULL, 0);
}


/*
 * Invalidate a response header by name across the full headers_out list
 * (Rule 28: iterate every list part; Rule 40: hash=0 marks invalidated).
 *
 * Returns:
 *   NGX_OK always (invalidation cannot fail)
 */
static ngx_int_t
ngx_http_markdown_stream_commit_invalidate_header(
    ngx_http_request_t *r, const u_char *name, size_t name_len)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *elts;

    part = &r->headers_out.headers.part;

    while (part != NULL) {
        elts = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (elts[i].hash == 0) {
                continue;
            }
            if (ngx_http_markdown_stream_commit_header_matches(
                    &elts[i], name, name_len))
            {
                elts[i].hash = 0;
            }
        }
        part = part->next;
    }

    return NGX_OK;
}


/*
 * Remove representation-integrity metadata that describes the upstream
 * HTML representation, not the transformed Markdown body.
 *
 * After a successful conversion the response body bytes have changed, so
 * the following upstream headers are stale and must not be forwarded:
 *   - Accept-Ranges (byte ranges apply to the HTML representation; the
 *     module bypasses transformation for Range requests)
 *   - X-Markdown-Tokens (upstream token count for the HTML body)
 *   - Content-MD5 / Digest / Content-Digest / Repr-Digest (digests of the
 *     HTML body; consumers may rely on them for integrity checks)
 *
 * This mirrors the full-buffer path (headers_impl.h C6 Accept-Ranges
 * removal) so streaming and buffered responses share one mutation
 * contract.  Infallible: only pointer/integer writes and hash=0
 * invalidations.
 *
 * Returns:
 *   NGX_OK always
 */
static ngx_int_t
ngx_http_markdown_stream_commit_remove_representation_metadata(
    ngx_http_request_t *r)
{
    static u_char  hdr_accept_ranges[] = "Accept-Ranges";
    static u_char  hdr_token_count[] = "X-Markdown-Tokens";
    static u_char  hdr_content_md5[] = "Content-MD5";
    static u_char  hdr_digest[] = "Digest";
    static u_char  hdr_content_digest[] = "Content-Digest";
    static u_char  hdr_repr_digest[] = "Repr-Digest";
    static u_char  hdr_last_modified[] = "Last-Modified";
    static u_char  hdr_trailer[] = "Trailer";

    /* Accept-Ranges: clear the typed field and invalidate list entries. */
    r->allow_ranges = 0;
    r->headers_out.accept_ranges = NULL;
    (void) ngx_http_markdown_stream_commit_invalidate_header(
        r, hdr_accept_ranges, sizeof(hdr_accept_ranges) - 1);

    /* Upstream X-Markdown-Tokens describes the HTML body. */
    (void) ngx_http_markdown_stream_commit_invalidate_header(
        r, hdr_token_count, sizeof(hdr_token_count) - 1);

    /* Representation digests describe the HTML body. */
    (void) ngx_http_markdown_stream_commit_invalidate_header(
        r, hdr_content_md5, sizeof(hdr_content_md5) - 1);
    (void) ngx_http_markdown_stream_commit_invalidate_header(
        r, hdr_digest, sizeof(hdr_digest) - 1);
    (void) ngx_http_markdown_stream_commit_invalidate_header(
        r, hdr_content_digest, sizeof(hdr_content_digest) - 1);
    (void) ngx_http_markdown_stream_commit_invalidate_header(
        r, hdr_repr_digest, sizeof(hdr_repr_digest) - 1);

    /* Decision G: the streamed Markdown representation must not carry the
     * source HTML mtime as its weak validator; ETag is the sole validator
     * for converted responses.  Clear the typed pointer too: the header
     * filter synthesizes Last-Modified whenever last_modified_time != -1
     * AND last_modified == NULL is false, so both fields must be reset. */
    r->headers_out.last_modified_time = (time_t) -1;
    r->headers_out.last_modified = NULL;
    (void) ngx_http_markdown_stream_commit_invalidate_header(
        r, hdr_last_modified, sizeof(hdr_last_modified) - 1);

    /* Upstream trailers describe the HTML body; the streamed Markdown
     * body replaces it, so the Trailer declaration must not be
     * forwarded. */
    (void) ngx_http_markdown_stream_commit_invalidate_header(
        r, hdr_trailer, sizeof(hdr_trailer) - 1);

    /* Clear the actual trailer entries too: headers_out.trailers is an
     * independent list emitted by HTTP/2/3 and chunked encodings without
     * an HTTP/1.1 Trailer declaration.  Suppress source-HTML trailers. */
    ngx_http_markdown_clear_trailers(r);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                   "markdown: stream commit: "
                   "removed representation-integrity metadata");

    return NGX_OK;
}
