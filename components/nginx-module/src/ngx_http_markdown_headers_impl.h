/*
 * Shared header-update implementation used by:
 * - ngx_http_markdown_headers.c (production module)
 * - tests/helpers/headers_standalone.c (unit-test harness)
 */

#ifndef NGX_HTTP_MARKDOWN_HEADERS_IMPL_H
#define NGX_HTTP_MARKDOWN_HEADERS_IMPL_H

#ifndef NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
#define NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL 1
#endif

#ifndef NGX_HTTP_MARKDOWN_LOG_DEBUG1
#define NGX_HTTP_MARKDOWN_LOG_DEBUG1(level, log, err, fmt, arg) \
    do {                                                            \
        ngx_log_debug1((level), (log), (err), (fmt), (arg));        \
    } while (0)
#endif

#include "ngx_http_markdown_exports.h"

/*
 * Include the header plan header for the atomic apply function.
 * In standalone test mode, the test harness provides its own stub.
 */
#ifndef NGX_HTTP_MARKDOWN_HEADERS_STANDALONE_TYPES_H
#include "ngx_http_markdown_header_plan.h"
#endif

#ifndef NGX_HTTP_MARKDOWN_SPRINTF_TOKEN
#define NGX_HTTP_MARKDOWN_SPRINTF_TOKEN(buf, token_count) \
    ngx_sprintf((buf), "%ui", (token_count))
#endif

#ifndef ngx_tolower
#define ngx_tolower(c) ((u_char) ((c) >= 'A' && (c) <= 'Z' ? ((c) | 0x20) : (c)))
#endif

static u_char ngx_http_markdown_hdr_vary[] = "Vary";
static u_char ngx_http_markdown_hdr_accept[] = "Accept";
static u_char ngx_http_markdown_hdr_etag[] = "ETag";
static u_char ngx_http_markdown_hdr_content_encoding[] = "Content-Encoding";
static u_char ngx_http_markdown_hdr_accept_ranges[] = "Accept-Ranges";
static u_char ngx_http_markdown_hdr_token_count[] = "X-Markdown-Tokens";
u_char ngx_http_markdown_content_type[] = NGX_HTTP_MARKDOWN_CONTENT_TYPE_LITERAL;
static u_char ngx_http_markdown_vary_suffix[] = ", Accept";

/*
 * Case-insensitive comparison of up to n bytes from two byte strings.
 *
 * Like ngx_strncasecmp, this function compares raw byte pointers and
 * returns early when both characters are NUL (treating it as a match
 * boundary).  Unlike a strict n-byte comparison, encountering NUL in
 * both strings before n bytes yields 0 even if the strings differ
 * beyond the NUL.  This mirrors strncasecmp semantics.
 *
 * Returns 0 when the compared bytes match (case-insensitively) or
 * both bytes are NUL, or the difference of the first mismatching
 * lowercase byte pair.
 *
 * Parameters:
 *   s1 - first byte string
 *   s2 - second byte string
 *   n  - number of bytes to compare
 *
 * Returns:
 *   0 if equal, negative if s1 < s2, positive if s1 > s2
 */
static ngx_int_t
ngx_http_markdown_strncasecmp_const(const u_char *s1, const u_char *s2, size_t n)
{
    ngx_uint_t c1;
    ngx_uint_t c2;

    while (n != 0) {
        c1 = (ngx_uint_t) *s1++;
        c2 = (ngx_uint_t) *s2++;
        c1 = ngx_tolower(c1);
        c2 = ngx_tolower(c2);

        if (c1 == c2) {
            if (c1 == 0) {
                return 0;
            }

            n--;
            continue;
        }

        return c1 - c2;
    }

    return 0;
}

/*
 * Search for a response header by name within a header list part.
 *
 * Walks the linked list of ngx_list_part_t nodes starting from
 * the given part, performing case-insensitive name comparison.
 *
 * part     - first list part to search
 * name     - header name to match (case-insensitive)
 * name_len - length of the header name
 *
 * Returns:
 *   pointer to the matching ngx_table_elt_t on success
 *   NULL if no header with the given name is found
 */
static ngx_table_elt_t *
ngx_http_markdown_find_header_in_part(ngx_list_part_t *part,
                                      const u_char *name,
                                      size_t name_len)
{
    while (part != NULL) {
        ngx_table_elt_t *headers;
        ngx_uint_t i;

        headers = part->elts;
        i = 0;
        while (i < part->nelts) {
            if (headers[i].hash == 0) {
                i++;
                continue;
            }

            if (headers[i].key.len == name_len
                && ngx_http_markdown_strncasecmp_const(headers[i].key.data,
                                                       name,
                                                       name_len)
                   == 0)
            {
                return &headers[i];
            }
            i++;
        }

        part = part->next;
    }

    return NULL;
}

/*
 * Search for a response header by name in the request's output headers.
 *
 * Convenience wrapper around ngx_http_markdown_find_header_in_part
 * that starts from r->headers_out.headers.part.
 *
 * r        - current HTTP request
 * name     - header name to match (case-insensitive)
 * name_len - length of the header name
 *
 * Returns:
 *   pointer to the matching ngx_table_elt_t on success
 *   NULL if the header list is empty or no match is found
 */
static ngx_table_elt_t *
ngx_http_markdown_find_header(ngx_http_request_t *r, const u_char *name, size_t name_len)
{
    return ngx_http_markdown_find_header_in_part(&r->headers_out.headers.part,
                                                 name, name_len);
}

/*
 * Invalidate response headers by name within a header list part.
 *
 * Walks the linked list of ngx_list_part_t nodes and sets hash = 0
 * on each matching header, which marks it as removed in NGINX's
 * header output. Optionally stops after the first match and emits
 * a debug log message.
 *
 * r                - current HTTP request (used for logging)
 * part             - first list part to search
 * name             - header name to match (case-insensitive)
 * name_len         - length of the header name
 * stop_after_first - if 1, return after invalidating the first match
 * log_message      - debug message to log per invalidation, or NULL
 */
static void
ngx_http_markdown_invalidate_headers_in_part(const ngx_http_request_t *r,
                                             ngx_list_part_t *part,
                                             const u_char *name,
                                             size_t name_len,
                                             ngx_flag_t stop_after_first,
                                             const char *log_message)
{
    (void) r;

    while (part != NULL) {
        ngx_table_elt_t *headers;
        ngx_uint_t i;

        headers = part->elts;
        i = 0;
        while (i < part->nelts) {
            if (headers[i].hash == 0) {
                i++;
                continue;
            }

            if (headers[i].key.len != name_len
                || ngx_http_markdown_strncasecmp_const(headers[i].key.data,
                                                       name,
                                                       name_len)
                   != 0)
            {
                i++;
                continue;
            }

            headers[i].hash = 0;
            if (log_message != NULL) {
                ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0, log_message);
            }

            if (stop_after_first) {
                return;
            }
            i++;
        }

        part = part->next;
    }
}

/*
 * Invalidate response headers by name in the request's output headers.
 *
 * Convenience wrapper around ngx_http_markdown_invalidate_headers_in_part
 * that starts from r->headers_out.headers.part.
 *
 * r                - current HTTP request
 * name             - header name to match (case-insensitive)
 * name_len         - length of the header name
 * stop_after_first - if 1, stop after invalidating the first match
 * log_message      - debug message to log per invalidation, or NULL
 */
static void
ngx_http_markdown_invalidate_headers(ngx_http_request_t *r,
                                     const u_char *name,
                                     size_t name_len,
                                     ngx_flag_t stop_after_first,
                                     const char *log_message)
{
    ngx_http_markdown_invalidate_headers_in_part(r, &r->headers_out.headers.part,
                                                 name, name_len, stop_after_first,
                                                 log_message);
}

/*
 * Check whether a comma-separated header value contains a specific token.
 *
 * Parses the value as a comma-delimited list, trims whitespace from
 * each token, and performs case-insensitive comparison against the
 * target token.
 *
 * value     - the header value string to search
 * token     - the token to look for (case-insensitive)
 * token_len - length of the token
 *
 * Returns:
 *   1 if the token is found in the CSV list
 *   0 otherwise
 */
static ngx_flag_t
ngx_http_markdown_contains_csv_token(const ngx_str_t *value,
                                     const u_char *token,
                                     size_t token_len)
{
    size_t i;

    i = 0;
    while (i < value->len) {
        size_t start;
        size_t end;

        while (i < value->len && (value->data[i] == ' ' || value->data[i] == ',')) {
            i++;
        }

        start = i;
        while (i < value->len && value->data[i] != ',') {
            i++;
        }
        end = i;

        while (end > start && value->data[end - 1] == ' ') {
            end--;
        }

        if (end - start == token_len
            && ngx_http_markdown_strncasecmp_const(value->data + start,
                                                   token,
                                                   token_len)
               == 0)
        {
            return 1;
        }

        if (i < value->len) {
            i++;
        }
    }

    return 0;
}

/*
 * Add or append "Accept" to the Vary response header.
 *
 * If no Vary header exists, creates one with value "Accept".
 * If a Vary header exists but does not already contain the
 * "Accept" token, appends ", Accept" to the existing value.
 * Skips modification if "Accept" is already present.
 *
 * r - current HTTP request
 *
 * Returns:
 *   NGX_OK    on success (header added, appended, or already present)
 *   NGX_ERROR on allocation failure or overflow
 */
ngx_int_t
ngx_http_markdown_add_vary_accept(ngx_http_request_t *r)
{
    ngx_table_elt_t *vary;
    ngx_table_elt_t *h;
    u_char *p;
    size_t len;

    vary = ngx_http_markdown_find_header(r,
                                         ngx_http_markdown_hdr_vary,
                                         sizeof(ngx_http_markdown_hdr_vary) - 1);

    if (vary == NULL) {
        h = ngx_list_push(&r->headers_out.headers);
        if (h == NULL) {
            return NGX_ERROR;
        }

        h->hash = 1;
        h->key.data = ngx_http_markdown_hdr_vary;
        h->key.len = sizeof(ngx_http_markdown_hdr_vary) - 1;
        h->value.data = ngx_http_markdown_hdr_accept;
        h->value.len = sizeof(ngx_http_markdown_hdr_accept) - 1;

        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: added Vary: Accept header");
        return NGX_OK;
    }

    if (ngx_http_markdown_contains_csv_token(&vary->value,
                                             ngx_http_markdown_hdr_accept,
                                             sizeof(ngx_http_markdown_hdr_accept) - 1))
    {
        NGX_HTTP_MARKDOWN_LOG_DEBUG1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                                     "markdown: Vary header already contains Accept: \"%V\"",
                                     &vary->value);
        return NGX_OK;
    }

    if (vary->value.len > ((size_t) -1) - (sizeof(ngx_http_markdown_vary_suffix) - 1)) {
        return NGX_ERROR;
    }

    len = vary->value.len + sizeof(ngx_http_markdown_vary_suffix) - 1;
    p = ngx_pnalloc(r->pool, len);
    if (p == NULL) {
        return NGX_ERROR;
    }

    p = ngx_cpymem(p, vary->value.data, vary->value.len);
    p = ngx_cpymem(p,
                   ngx_http_markdown_vary_suffix,
                   sizeof(ngx_http_markdown_vary_suffix) - 1);

    vary->value.data = p - len;
    vary->value.len = len;

    NGX_HTTP_MARKDOWN_LOG_DEBUG1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                                 "markdown: updated Vary header: \"%V\"",
                                 &vary->value);

    return NGX_OK;
}

/*
 * Set or clear the ETag response header.
 *
 * First invalidates all existing ETag headers in the output list.
 * If etag is NULL or etag_len is 0, clears r->headers_out.etag
 * (removes the ETag). Otherwise, allocates a new header entry
 * with the provided ETag value.
 *
 * r        - current HTTP request
 * etag     - ETag value bytes, or NULL to clear
 * etag_len - length of the ETag value
 *
 * Returns:
 *   NGX_OK    on success
 *   NGX_ERROR on allocation failure
 */
ngx_int_t
ngx_http_markdown_set_etag(ngx_http_request_t *r, const u_char *etag, size_t etag_len)
{
    ngx_table_elt_t *h;

    ngx_http_markdown_invalidate_headers(r,
                                         ngx_http_markdown_hdr_etag,
                                         sizeof(ngx_http_markdown_hdr_etag) - 1,
                                         0,
                                         NULL);

    if (etag == NULL || etag_len == 0) {
        r->headers_out.etag = NULL;
        return NGX_OK;
    }

    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 1;
    h->key.data = ngx_http_markdown_hdr_etag;
    h->key.len = sizeof(ngx_http_markdown_hdr_etag) - 1;

    h->value.data = ngx_pnalloc(r->pool, etag_len);
    if (h->value.data == NULL) {
        return NGX_ERROR;
    }

    ngx_memcpy(h->value.data, etag, etag_len);
    h->value.len = etag_len;
    r->headers_out.etag = h;

    NGX_HTTP_MARKDOWN_LOG_DEBUG1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                                 "markdown: set ETag: \"%V\"", &h->value);

    return NGX_OK;
}

/*
 * Remove the Content-Encoding response header.
 *
 * Clears r->headers_out.content_encoding and invalidates the
 * matching Content-Encoding entries in the output header list.
 * Called after decompression so the downstream response does
 * not claim a transfer encoding that no longer applies.
 *
 * r - current HTTP request
 */
void
ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r)
{
    r->headers_out.content_encoding = NULL;

    ngx_http_markdown_invalidate_headers(r,
                                         ngx_http_markdown_hdr_content_encoding,
                                         sizeof(ngx_http_markdown_hdr_content_encoding) - 1,
                                         0,
                                         "markdown: removed Content-Encoding header");
}

/*
 * Full-coverage HeaderPlan prepare/commit for full-buffer conversion.
 *
 * 0.9.2 two-phase protocol (Requirement 9, Properties 14–15):
 *
 *   PREPARE PHASE — performs ALL fallible operations:
 *     - Rust FFI plan build and atomic apply (Content-Type delete-all,
 *       Content-Encoding delete-all, Content-Length delete-all, ETag
 *       placeholder)
 *     - ETag header allocation and value copy
 *     - Vary: Accept lookup, dedup, and append allocation
 *     - X-Markdown-Tokens header allocation and value formatting
 *     - Cache-Control auth modification allocation
 *     A bounded transaction snapshot is taken before the first operation.
 *     Helpers may use inert list slots or legacy in-place auth rewrites
 *     during prepare, but ANY failure restores the snapshot exactly.
 *     Rust-owned plan resources are freed and `header_plan_apply_error` is
 *     logged.
 *
 *   COMMIT PHASE — pointer/scalar assignment only, zero allocations,
 *     unconditional success after successful prepare:
 *     - Content-Type dedicated field assignment
 *     - ETag header entry populated from pre-allocated memory
 *     - Vary header entry populated or value pointer swapped
 *     - Content-Length numeric field set
 *     - X-Markdown-Tokens entry populated from pre-allocated memory
 *     - Accept-Ranges invalidation (hash=0, pointer clear)
 *     - Cache-Control value pointer swap
 *     - Content-Encoding pointer clear
 *
 *   Nothing occurs between commit and ngx_http_send_header.
 *
 * Exception inventory (<5 entries):
 *   - Metrics endpoint (full response synthesis)
 *   - Diagnostics endpoint (full response synthesis)
 *   No postcommit HeaderPlan exception — postcommit body errors do NOT
 *   produce new status/header modifications.
 *
 * r      - current HTTP request
 * result - completed MarkdownResult from the Rust converter
 * conf   - location configuration
 *
 * Returns:
 *   NGX_OK    on success (all headers committed)
 *   NGX_ERROR on prepare failure (headers restored, plan freed,
 *             header_plan_apply_error logged)
 */

/*
 * Bounded transaction snapshot for full-buffer header preparation.
 *
 * The individual header helpers have their own prepare/commit behavior, but
 * the full-buffer path combines several helpers and the authentication
 * policy still has legacy in-place rewrites.  Snapshot all existing header
 * entries and dedicated response fields before the first helper so any
 * prepare failure can restore the exact pre-conversion representation.
 * Newly pushed list slots are made unreachable by restoring the saved list
 * metadata.  The snapshot is bounded to keep request-path allocation
 * explicit and predictable.
 */
#define NGX_HTTP_MARKDOWN_HEADER_SNAPSHOT_MAX_ENTRIES  1024

typedef struct {
    ngx_table_elt_t  *header;
    ngx_table_elt_t   saved;
} ngx_http_markdown_header_snapshot_entry_t;

typedef struct {
    ngx_http_headers_out_t  headers_out;
    unsigned int            allow_ranges;
    ngx_http_markdown_header_snapshot_entry_t  *entries;
    ngx_uint_t              entry_count;
#ifndef NGX_HTTP_MARKDOWN_HEADERS_STANDALONE_TYPES_H
    ngx_list_part_t        *original_last;
    ngx_uint_t              original_last_nelts;
#endif
} ngx_http_markdown_header_snapshot_t;


static ngx_int_t
ngx_http_markdown_header_snapshot_prepare(
    ngx_http_request_t *r,
    ngx_http_markdown_header_snapshot_t *snapshot)
{
    ngx_list_part_t  *part;
    ngx_table_elt_t  *headers;
    ngx_uint_t        count;

    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->headers_out = r->headers_out;
    snapshot->allow_ranges = r->allow_ranges ? 1U : 0U;
#ifndef NGX_HTTP_MARKDOWN_HEADERS_STANDALONE_TYPES_H
    snapshot->original_last = r->headers_out.headers.last;
    if (snapshot->original_last != NULL) {
        snapshot->original_last_nelts = snapshot->original_last->nelts;
    }
#endif

    count = 0;
    for (part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        if (part->nelts > NGX_HTTP_MARKDOWN_HEADER_SNAPSHOT_MAX_ENTRIES
            - count)
        {
            return NGX_ERROR;
        }
        count += part->nelts;
    }

    snapshot->entry_count = count;
    if (count == 0) {
        return NGX_OK;
    }

    snapshot->entries = ngx_pnalloc(r->pool,
        (size_t) count * sizeof(ngx_http_markdown_header_snapshot_entry_t));
    if (snapshot->entries == NULL) {
        return NGX_ERROR;
    }

    count = 0;
    for (part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        if (part->nelts > 0 && part->elts == NULL) {
            return NGX_ERROR;
        }

        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            snapshot->entries[count].header = &headers[i];
            snapshot->entries[count].saved = headers[i];
            count++;
        }
    }

    return NGX_OK;
}


static void
ngx_http_markdown_header_snapshot_restore(
    ngx_http_request_t *r,
    const ngx_http_markdown_header_snapshot_t *snapshot)
{
    if (snapshot == NULL) {
        return;
    }

#ifndef NGX_HTTP_MARKDOWN_HEADERS_STANDALONE_TYPES_H
    if (snapshot->original_last != NULL) {
        snapshot->original_last->nelts = snapshot->original_last_nelts;
    }
#endif

    r->headers_out = snapshot->headers_out;
    r->allow_ranges = snapshot->allow_ranges;

    for (ngx_uint_t i = 0; i < snapshot->entry_count; i++) {
        *snapshot->entries[i].header = snapshot->entries[i].saved;
    }
}


/*
 * Prepared state for the full-coverage commit phase.
 * All memory referenced here is allocated from r->pool during prepare.
 * The commit phase consumes these fields via assignment only.
 */
typedef struct {
    /* ETag: pre-allocated header slot and value copy */
    ngx_flag_t          has_etag;
    ngx_table_elt_t    *etag_header;       /* pushed inert slot or NULL */
    u_char             *etag_value_data;    /* pool copy of ETag bytes */
    size_t              etag_value_len;

    /* Vary: Accept */
    ngx_flag_t          vary_needs_new;     /* no existing Vary: push new */
    ngx_flag_t          vary_needs_append;  /* existing Vary: append Accept */
    ngx_flag_t          vary_already_has;   /* existing Vary already has Accept */
    ngx_table_elt_t    *vary_header;        /* new slot or existing entry */
    u_char             *vary_value_data;    /* pool copy of new/appended value */
    size_t              vary_value_len;

    /* X-Markdown-Tokens */
    ngx_flag_t          has_token_header;
    ngx_table_elt_t    *token_header;       /* pushed inert slot */
    u_char             *token_value_data;   /* pool-formatted decimal */
    size_t              token_value_len;

} ngx_http_markdown_fullcov_prepared_t;


/*
 * Prepare ETag: invalidate stale entries, push inert slot, copy value.
 *
 * Returns NGX_OK on success (or no-op when ETag not needed),
 * NGX_ERROR on allocation failure.
 */
static ngx_int_t
ngx_http_markdown_fullcov_prepare_etag(ngx_http_request_t *r,
    const struct MarkdownResult *result,
    const ngx_http_markdown_conf_t *conf,
    ngx_http_markdown_fullcov_prepared_t *prep)
{
    ngx_table_elt_t  *h;

    ngx_http_markdown_invalidate_headers(r,
        ngx_http_markdown_hdr_etag,
        sizeof(ngx_http_markdown_hdr_etag) - 1,
        0, NULL);
    r->headers_out.etag = NULL;

    if (!conf->policy.generate_etag
        || result->etag == NULL
        || result->etag_len == 0)
    {
        return NGX_OK;
    }

    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    /* Inert until commit (Rule 40: hash==0 filtered everywhere) */
    h->hash = 0;
    h->key.data = NULL;
    h->key.len = 0;
    h->value.data = NULL;
    h->value.len = 0;

    prep->etag_value_data = ngx_pnalloc(r->pool, result->etag_len);
    if (prep->etag_value_data == NULL) {
        return NGX_ERROR;
    }

    ngx_memcpy(prep->etag_value_data, result->etag, result->etag_len);
    prep->etag_value_len = result->etag_len;
    prep->etag_header = h;
    prep->has_etag = 1;

    return NGX_OK;
}


/*
 * Prepare Vary: Accept — lookup, dedup, push inert slot or allocate
 * appended value.
 *
 * Returns NGX_OK on success, NGX_ERROR on allocation/overflow failure.
 */
static ngx_int_t
ngx_http_markdown_fullcov_prepare_vary(ngx_http_request_t *r,
    ngx_http_markdown_fullcov_prepared_t *prep)
{
    ngx_table_elt_t  *vary;
    ngx_table_elt_t  *h;
    u_char           *p;
    size_t            len;

    vary = ngx_http_markdown_find_header(r,
        ngx_http_markdown_hdr_vary,
        sizeof(ngx_http_markdown_hdr_vary) - 1);

    if (vary == NULL) {
        h = ngx_list_push(&r->headers_out.headers);
        if (h == NULL) {
            return NGX_ERROR;
        }

        h->hash = 0;
        h->key.data = NULL;
        h->key.len = 0;
        h->value.data = NULL;
        h->value.len = 0;

        prep->vary_needs_new = 1;
        prep->vary_header = h;
        prep->vary_value_data = ngx_http_markdown_hdr_accept;
        prep->vary_value_len = sizeof(ngx_http_markdown_hdr_accept) - 1;
        return NGX_OK;
    }

    if (ngx_http_markdown_contains_csv_token(&vary->value,
            ngx_http_markdown_hdr_accept,
            sizeof(ngx_http_markdown_hdr_accept) - 1))
    {
        prep->vary_already_has = 1;
        prep->vary_header = vary;
        return NGX_OK;
    }

    /* Append ", Accept" to existing value */
    if (vary->value.len
        > ((size_t) -1) - (sizeof(ngx_http_markdown_vary_suffix) - 1))
    {
        return NGX_ERROR;
    }

    len = vary->value.len + sizeof(ngx_http_markdown_vary_suffix) - 1;
    p = ngx_pnalloc(r->pool, len);
    if (p == NULL) {
        return NGX_ERROR;
    }

    ngx_memcpy(p, vary->value.data, vary->value.len);
    ngx_memcpy(p + vary->value.len,
        ngx_http_markdown_vary_suffix,
        sizeof(ngx_http_markdown_vary_suffix) - 1);

    prep->vary_needs_append = 1;
    prep->vary_header = vary;
    prep->vary_value_data = p;
    prep->vary_value_len = len;

    return NGX_OK;
}


/*
 * Prepare X-Markdown-Tokens: push inert slot, format decimal value.
 *
 * Returns NGX_OK on success (or no-op when tokens disabled/zero),
 * NGX_ERROR on allocation failure.
 */
static ngx_int_t
ngx_http_markdown_fullcov_prepare_token(ngx_http_request_t *r,
    const struct MarkdownResult *result,
    const ngx_http_markdown_conf_t *conf,
    ngx_http_markdown_fullcov_prepared_t *prep)
{
    ngx_table_elt_t  *h;
    u_char           *p;

    if (!conf->token_estimate || result->token_estimate == 0) {
        return NGX_OK;
    }

    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 0;
    h->key.data = NULL;
    h->key.len = 0;
    h->value.data = NULL;
    h->value.len = 0;

    p = ngx_pnalloc(r->pool, NGX_INT32_LEN);
    if (p == NULL) {
        return NGX_ERROR;
    }

    prep->token_value_data = p;
    prep->token_value_len = (size_t)
        (NGX_HTTP_MARKDOWN_SPRINTF_TOKEN(p, result->token_estimate) - p);
    prep->token_header = h;
    prep->has_token_header = 1;

    return NGX_OK;
}


/*
 * Commit all prepared header mutations (infallible, zero allocations).
 */
static void
ngx_http_markdown_fullcov_commit(ngx_http_request_t *r,
    const struct MarkdownResult *result,
    const ngx_http_markdown_fullcov_prepared_t *prep)
{
    /* C1: Content-Type dedicated field */
    r->headers_out.content_type.data = ngx_http_markdown_content_type;
    r->headers_out.content_type.len = NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN;
    r->headers_out.content_type_len = NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN;
    r->headers_out.charset.len = 0;
    r->headers_out.charset.data = NULL;
    r->headers_out.content_encoding = NULL;

    /* C2: ETag — populate the pre-allocated inert slot */
    if (prep->has_etag) {
        prep->etag_header->key.data = ngx_http_markdown_hdr_etag;
        prep->etag_header->key.len = sizeof(ngx_http_markdown_hdr_etag) - 1;
        prep->etag_header->value.data = prep->etag_value_data;
        prep->etag_header->value.len = prep->etag_value_len;
        prep->etag_header->hash = 1;
        r->headers_out.etag = prep->etag_header;
    }

    /* C3: Vary: Accept — populate new slot or swap value pointer */
    if (prep->vary_needs_new) {
        prep->vary_header->key.data = ngx_http_markdown_hdr_vary;
        prep->vary_header->key.len = sizeof(ngx_http_markdown_hdr_vary) - 1;
        prep->vary_header->value.data = prep->vary_value_data;
        prep->vary_header->value.len = prep->vary_value_len;
        prep->vary_header->hash = 1;
    } else if (prep->vary_needs_append) {
        prep->vary_header->value.data = prep->vary_value_data;
        prep->vary_header->value.len = prep->vary_value_len;
    }

    /* C4: Content-Length — scalar assignment */
    ngx_http_clear_content_length(r);
    r->headers_out.content_length_n = result->markdown_len;

    /* C5: X-Markdown-Tokens — populate the pre-allocated inert slot */
    if (prep->has_token_header) {
        prep->token_header->key.data = ngx_http_markdown_hdr_token_count;
        prep->token_header->key.len =
            sizeof(ngx_http_markdown_hdr_token_count) - 1;
        prep->token_header->value.data = prep->token_value_data;
        prep->token_header->value.len = prep->token_value_len;
        prep->token_header->hash = 1;
    }

    /* C6: Accept-Ranges removal — scalar assignment */
    r->allow_ranges = 0;
    r->headers_out.accept_ranges = NULL;
    ngx_http_markdown_invalidate_headers(r,
        ngx_http_markdown_hdr_accept_ranges,
        sizeof(ngx_http_markdown_hdr_accept_ranges) - 1,
        1, NULL);
}


ngx_int_t
ngx_http_markdown_update_headers(ngx_http_request_t *r,
                                 const struct MarkdownResult *result,
                                 const ngx_http_markdown_conf_t *conf)
{
    ngx_int_t                              rc;
    struct FFIHeaderPlan                   plan;
    ngx_http_markdown_fullcov_prepared_t   prep;
    ngx_http_markdown_header_snapshot_t    snapshot;

    if (r == NULL || result == NULL || conf == NULL) {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_header_snapshot_prepare(r, &snapshot)
        != NGX_OK)
    {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: header_plan_apply_error: "
            "header snapshot prepare failed");
        return NGX_ERROR;
    }

    memset(&prep, 0, sizeof(ngx_http_markdown_fullcov_prepared_t));

    /* ================================================================
     * PREPARE PHASE: all fallible operations, no r->headers_out mutation
     * (except inert hash==0 pushes which are observably no-op).
     * ================================================================ */

    /* P1: FFI plan (Content-Type/Encoding/Length delete-all, ETag placeholder) */
    markdown_header_plan_init(&plan);
    markdown_build_header_plan(
        ngx_http_markdown_content_type,
        NGX_HTTP_MARKDOWN_CONTENT_TYPE_LEN,
        (conf->policy.generate_etag
         && result->etag != NULL
         && result->etag_len > 0) ? 1 : 0,
        &plan);

    rc = ngx_http_markdown_apply_header_plan(r, &plan);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: header_plan_apply_error: "
            "FFI plan prepare aborted");
        ngx_http_markdown_header_snapshot_restore(r, &snapshot);
        return NGX_ERROR;
    }

    /* P2: ETag */
    rc = ngx_http_markdown_fullcov_prepare_etag(r, result, conf, &prep);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: header_plan_apply_error: "
            "ETag prepare failed");
        ngx_http_markdown_header_snapshot_restore(r, &snapshot);
        return NGX_ERROR;
    }

    /* P3: Vary: Accept */
    rc = ngx_http_markdown_fullcov_prepare_vary(r, &prep);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: header_plan_apply_error: "
            "Vary prepare failed");
        ngx_http_markdown_header_snapshot_restore(r, &snapshot);
        return NGX_ERROR;
    }

    /* P4: X-Markdown-Tokens */
    rc = ngx_http_markdown_fullcov_prepare_token(r, result, conf, &prep);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
            "markdown: header_plan_apply_error: "
            "token header prepare failed");
        ngx_http_markdown_header_snapshot_restore(r, &snapshot);
        return NGX_ERROR;
    }

    /* P5: Cache-Control auth modification */
#if NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL
    if (ngx_http_markdown_is_authenticated(r, conf)) {
        rc = ngx_http_markdown_modify_cache_control_for_auth(r);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                "markdown: header_plan_apply_error: "
                "Cache-Control auth modification failed");
            ngx_http_markdown_header_snapshot_restore(r, &snapshot);
            return NGX_ERROR;
        }
    }
#endif

    /* ================================================================
     * COMMIT PHASE: pointer/scalar assignment only, zero allocations,
     * unconditional success after successful prepare.
     * Nothing occurs between this commit and ngx_http_send_header.
     * ================================================================ */
    ngx_http_markdown_fullcov_commit(r, result, &prep);

    NGX_HTTP_MARKDOWN_LOG_DEBUG1(NGX_LOG_DEBUG_HTTP,
        r->connection->log, 0,
        "markdown: headers committed (full-coverage); "
        "Content-Length: %uz",
        result->markdown_len);

    return NGX_OK;
}

#endif /* NGX_HTTP_MARKDOWN_HEADERS_IMPL_H */
