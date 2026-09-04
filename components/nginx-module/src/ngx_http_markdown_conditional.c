/*
 * NGINX Markdown Filter Module - Conditional Request Handling
 *
 * This file implements conditional request support (If-Match,
 * If-Unmodified-Since, If-None-Match, If-Modified-Since) for Markdown
 * variants. Conditional decision policy is delegated to the Rust
 * FFI (markdown_decide_conditional), while NGINX lifecycle operations
 * (triggering conversion to generate ETag, sending 304 responses)
 * remain on the C side.
 *
 */

#include "ngx_http_markdown_filter_module.h"
#include "markdown_converter.h"

#include <string.h>


#define NGX_HTTP_MARKDOWN_IF_NONE_MATCH_MAX  8192


/*
 * Find a request header by name in nginx's generic linked-list container.
 *
 * This helper is used for conditional request processing so the parser can
 * inspect `If-None-Match` even when convenience header pointers are absent.
 *
 * Parameters:
 *   r        - the HTTP request whose incoming headers are searched
 *   name     - header name to search for (need not be NUL-terminated)
 *   name_len - length of the header name in bytes
 *
 * Returns:
 *   pointer to the matching header entry, or NULL if not found
 */
static ngx_table_elt_t *
ngx_http_markdown_find_request_header(ngx_http_request_t *r, u_char *name, size_t name_len)
{
    if (r == NULL || name == NULL || name_len == 0
        || r->headers_in.headers.part.nelts == 0) {
        return NULL;
    }

    for (ngx_list_part_t *part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        ngx_table_elt_t  *headers;

        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
            if (headers[i].key.len == name_len
                && ngx_strncasecmp(headers[i].key.data, name, name_len) == 0)
            {
                return &headers[i];
            }
        }
    }

    return NULL;
}

/*
 * Adopt orphaned conditional headers left by a capture before an internal
 * redirect.  NGINX internal redirect (ngx_http_internal_redirect) clears the
 * module context array (r->ctx is memzeroed), so a capture performed by the
 * first PREACCESS pass is lost, but the suppressed request-header entries
 * (hash == 0) survive on the request.  Restore their visibility so the
 * next capture can re-own them and the converted representation can still
 * produce 304 for a matching validator.  The value bytes remain intact
 * because suppression only clears the hash and zeroes the length.
 *
 * Every suppressed entry is restored — a request may legitimately carry
 * repeated validator fields, and capture records state for each one.
 * Typed convenience pointers (r->headers_in.if_none_match /
 * if_modified_since) are rebuilt from the first restored entry of each
 * name, matching the NGINX core convention that the typed pointer names
 * the first occurrence.
 */
static void
ngx_http_markdown_adopt_first_restored(
    ngx_table_elt_t **first_restored, ngx_table_elt_t *header)
{
    if (*first_restored == NULL) {
        *first_restored = header;
    }
}

/*
 * Validate every suppressed candidate of one header name without
 * mutating anything.  Returns NGX_OK when all candidates (if any) are
 * safely adoptable, NGX_ERROR when any candidate's value lacks a
 * terminating NUL within the bounded scan.  Used to make cross-name
 * adoption atomic: both name sets must validate before either is
 * committed.
 */
static ngx_int_t
ngx_http_markdown_validate_conditional_candidates(ngx_http_request_t *r,
    u_char *name, size_t name_len, size_t scan_limit)
{
    if (r == NULL || name == NULL || name_len == 0) {
        return NGX_ERROR;
    }
    if (r->headers_in.headers.part.nelts == 0) {
        return NGX_OK;
    }

    for (ngx_list_part_t *part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        ngx_table_elt_t  *headers;

        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].key.len != name_len
                || ngx_strncasecmp(headers[i].key.data, name, name_len) != 0)
            {
                continue;
            }

            if (headers[i].hash != 0) {
                continue;
            }

            if (headers[i].value.len != 0
                || headers[i].value.data == NULL)
            {
                continue;
            }

            if (memchr(headers[i].value.data, '\0', scan_limit) == NULL) {
                return NGX_ERROR;
            }
        }
    }

    return NGX_OK;
}

/* Bounded rollback stack for cross-name atomic adoption (Rule 39):
 * if any commit step fails midway, every mutated entry must return to
 * its suppressed state.  The stack capacity is fixed; the validation
 * pass runs first, so a realistic commit failure is a defensive
 * TOCTOU check, and the bounded snapshot must fail BEFORE mutating
 * anything rather than silently truncating rollback state. */
#define NGX_HTTP_MARKDOWN_ADOPT_ROLLBACK_MAX  16

typedef struct {
    ngx_table_elt_t  *entry;
} ngx_http_markdown_adopt_rollback_t;

/*
 * Shared adoption state threaded through the per-name commit passes:
 * the rollback stack, its cursor, the running adopted-entry count, and
 * the scan limit are identical across all four validator names, so they
 * live in one context instead of eight positional parameters.
 */
typedef struct {
    ngx_http_request_t                 *r;
    size_t                              scan_limit;
    ngx_uint_t                         *adopted_count;
    ngx_uint_t                         *rollback_count;
    ngx_http_markdown_adopt_rollback_t *rollback;
} ngx_http_markdown_adopt_ctx_t;

/* Roll back every adopted entry recorded so far: restored hash and
 * length both return to their suppressed-candidate values (0). */
static void
ngx_http_markdown_adopt_rollback_all(
    ngx_http_markdown_adopt_rollback_t *rollback, ngx_uint_t count)
{
    for (ngx_uint_t i = 0; i < count; i++) {
        rollback[i].entry->hash = 0;
        rollback[i].entry->value.len = 0;
    }
}

/*
 * Commit adoption for one validator name after the caller has validated all
 * suppressed candidates.  Returns NGX_ERROR if the NUL-termination invariant
 * no longer holds while committing; otherwise records the first visible
 * entry and increments the number of adopted entries.  Every mutation is
 * appended to the rollback stack, which fails before mutating anything
 * when it is full.
 */
static ngx_int_t
ngx_http_markdown_commit_one_conditional_headers(ngx_http_request_t *r,
    u_char *name, size_t name_len, ngx_table_elt_t **first_restored,
    ngx_http_markdown_adopt_ctx_t *ctx)
{
    const u_char  *value_end;

    if (r == NULL || name == NULL || name_len == 0
        || first_restored == NULL || ctx == NULL)
    {
        return NGX_ERROR;
    }
    *first_restored = NULL;
    if (r->headers_in.headers.part.nelts == 0) {
        return NGX_OK;
    }

    for (ngx_list_part_t *part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        ngx_table_elt_t  *headers;

        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].key.len != name_len
                || ngx_strncasecmp(headers[i].key.data, name, name_len) != 0)
            {
                continue;
            }

            if (headers[i].hash != 0) {
                ngx_http_markdown_adopt_first_restored(
                    first_restored, &headers[i]);
                continue;
            }

            if (headers[i].value.len != 0
                || headers[i].value.data == NULL)
            {
                continue;
            }

            value_end = (const u_char *) memchr(
                headers[i].value.data, '\0', ctx->scan_limit);
            if (value_end == NULL) {
                return NGX_ERROR;
            }
            if (*ctx->rollback_count >= NGX_HTTP_MARKDOWN_ADOPT_ROLLBACK_MAX) {
                /* Snapshot capacity exhausted: fail before mutating this
                 * entry so the caller's rollback stays complete. */
                return NGX_ERROR;
            }
            headers[i].value.len = (size_t) (value_end
                - headers[i].value.data);
            headers[i].hash = 1;
            ctx->rollback[*ctx->rollback_count].entry = &headers[i];
            (*ctx->rollback_count)++;
            (*ctx->adopted_count)++;
            ngx_http_markdown_adopt_first_restored(
                first_restored, &headers[i]);
        }
    }

    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_adopt_orphan_conditional_headers(
    ngx_http_request_t *r, size_t scan_limit,
    ngx_http_markdown_conditional_ownership_t *ownership)
{
    static u_char  inm_name[] = "If-None-Match";
    static u_char  ims_name[] = "If-Modified-Since";
    static u_char  im_name[] = "If-Match";
    static u_char  ius_name[] = "If-Unmodified-Since";
    ngx_table_elt_t  *inm;
    ngx_table_elt_t  *ims;
    ngx_table_elt_t  *im;
    ngx_table_elt_t  *ius;
    ngx_uint_t       adopted_count;
    ngx_uint_t       rollback_count;
    ngx_http_markdown_adopt_rollback_t  rollback[
        NGX_HTTP_MARKDOWN_ADOPT_ROLLBACK_MAX];
    ngx_http_markdown_adopt_ctx_t  ctx;
    ngx_int_t        inm_rc;
    ngx_int_t        ims_rc;
    ngx_int_t        im_rc;
    ngx_int_t        ius_rc;

    if (r == NULL) {
        return NGX_ERROR;
    }

    ctx.r = r;
    ctx.scan_limit = scan_limit;
    ctx.adopted_count = &adopted_count;
    ctx.rollback_count = &rollback_count;
    ctx.rollback = rollback;

    /*
     * Cross-name atomic adoption: validate ALL suppressed sets before
     * committing any.  A failure in one name must leave the request
     * headers entirely unchanged — restoring If-None-Match and then
     * failing on If-Modified-Since would expose a partially re-owned
     * validator set to the next PREACCESS pass.
     */
    inm_rc = ngx_http_markdown_validate_conditional_candidates(
        r, inm_name, sizeof(inm_name) - 1, scan_limit);
    ims_rc = ngx_http_markdown_validate_conditional_candidates(
        r, ims_name, sizeof(ims_name) - 1, scan_limit);
    im_rc = ngx_http_markdown_validate_conditional_candidates(
        r, im_name, sizeof(im_name) - 1, scan_limit);
    ius_rc = ngx_http_markdown_validate_conditional_candidates(
        r, ius_name, sizeof(ius_name) - 1, scan_limit);
    if (inm_rc != NGX_OK || ims_rc != NGX_OK
        || im_rc != NGX_OK || ius_rc != NGX_OK)
    {
        r->headers_in.if_none_match = NULL;
        r->headers_in.if_modified_since = NULL;
        r->headers_in.if_match = NULL;
        r->headers_in.if_unmodified_since = NULL;
        return NGX_ERROR;
    }

    adopted_count = 0;
    rollback_count = 0;
    inm_rc = ngx_http_markdown_commit_one_conditional_headers(
        r, inm_name, sizeof(inm_name) - 1, &inm, &ctx);
    ims_rc = ngx_http_markdown_commit_one_conditional_headers(
        r, ims_name, sizeof(ims_name) - 1, &ims, &ctx);
    im_rc = ngx_http_markdown_commit_one_conditional_headers(
        r, im_name, sizeof(im_name) - 1, &im, &ctx);
    ius_rc = ngx_http_markdown_commit_one_conditional_headers(
        r, ius_name, sizeof(ius_name) - 1, &ius, &ctx);

    if (inm_rc != NGX_OK || ims_rc != NGX_OK
        || im_rc != NGX_OK || ius_rc != NGX_OK)
    {
        /* Undo every entry already adopted by the failing pass so the
         * request headers are left exactly as they were (Rule 39). */
        ngx_http_markdown_adopt_rollback_all(rollback, rollback_count);
        r->headers_in.if_none_match = NULL;
        r->headers_in.if_modified_since = NULL;
        r->headers_in.if_match = NULL;
        r->headers_in.if_unmodified_since = NULL;
        return NGX_ERROR;
    }

    r->headers_in.if_none_match = inm;
    r->headers_in.if_modified_since = ims;
    r->headers_in.if_match = im;
    r->headers_in.if_unmodified_since = ius;

    if (ownership != NULL) {
        ngx_memzero(ownership, sizeof(*ownership));
        if (adopted_count != 0) {
            ownership->adopter =
                NGX_HTTP_MARKDOWN_CONDITIONAL_ADOPTER_PREACCESS;
            ownership->phase = NGX_HTTP_MARKDOWN_CONDITIONAL_PHASE_PREACCESS;
            ownership->entry_count = adopted_count;
        }
    }

    return (inm_rc == NGX_OK && ims_rc == NGX_OK
            && im_rc == NGX_OK && ius_rc == NGX_OK)
        ? NGX_OK : NGX_ERROR;
}

static uint8_t
ngx_http_markdown_conditional_cache_validation(ngx_uint_t mode)
{
    switch (mode) {
    case NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED:
        return 0;
    case NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT:
        return 2;
    case NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE:
    default:
        return 1;
    }
}

/*
 * Invalidate every response header matching `name` (hash=0 per Rule 40).
 * Iterates the full headers_out list chain (Rule 28).
 */
static void
ngx_http_markdown_invalidate_response_header(ngx_http_request_t *r,
    const u_char *name, size_t name_len)
{
    for (ngx_list_part_t *part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        ngx_table_elt_t  *headers;

        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
            if (headers[i].key.len == name_len
                && ngx_strncasecmp(headers[i].key.data,
                                   (u_char *) name, /* NOSONAR: c:S859; ngx_strncasecmp API takes non-const u_char* (Rule 24 NGINX API contract) */
                                   name_len) == 0)
            {
                headers[i].hash = 0;
            }
        }
    }
}

static ngx_int_t
ngx_http_markdown_strncasecmp_const(const u_char *s1, const u_char *s2,
    size_t n)
{
    while (n != 0) {
        u_char  c1;
        u_char  c2;

        c1 = ngx_tolower(*s1);
        c2 = ngx_tolower(*s2);

        if (c1 != c2) {
            return c1 - c2;
        }

        s1++;
        s2++;
        n--;
    }

    return 0;
}

static ngx_flag_t
ngx_http_markdown_header_has_cache_directive(const ngx_table_elt_t *header,
    const u_char *directive, size_t directive_len)
{
    const u_char  *p;
    const u_char  *end;

    if (header == NULL || header->value.data == NULL
        || header->value.len == 0)
    {
        return 0;
    }

    p = header->value.data;
    end = p + header->value.len;

    while (p < end) {
        while (p < end && (*p == ' ' || *p == '\t' || *p == ',')) {
            p++;
        }

        if ((size_t)(end - p) >= directive_len
            && ngx_http_markdown_strncasecmp_const(
                   p, directive, directive_len) == 0)
        {
            const u_char *after = p + directive_len;

            if (after == end || *after == ',' || *after == ' '
                || *after == '\t')
            {
                return 1;
            }
        }

        while (p < end && *p != ',') {
            p++;
        }
    }

    return 0;
}

static ngx_int_t
ngx_http_markdown_convert_for_conditional(
    const ngx_http_markdown_ctx_t *ctx,
    struct MarkdownConverterHandle *converter,
    const struct MarkdownOptions *options,
    struct MarkdownResult *conv_result)
{
    markdown_convert(converter, ctx->buffer.data, ctx->buffer.size,
                     options, conv_result);
    return NGX_OK;
}


/*
 * Check if the response carries Cache-Control: no-transform.
 *
 * Scans all Cache-Control response headers for the "no-transform"
 * directive (RFC 9111 §5.2.2.6).  The check is case-insensitive
 * per RFC.
 *
 * Parameters:
 *   r - NGINX request (for response header access)
 *
 * Returns:
 *   1 if no-transform is present, 0 otherwise
 */
ngx_flag_t
ngx_http_markdown_has_no_transform(ngx_http_request_t *r)
{
    static u_char       cc_name[] = "Cache-Control";
    static u_char       directive[] = "no-transform";
    size_t              directive_len;

    directive_len = sizeof(directive) - 1;

    for (ngx_list_part_t *part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        ngx_table_elt_t  *headers;

        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0) {
                continue;
            }
            if (headers[i].key.len != sizeof(cc_name) - 1
                || ngx_strncasecmp(headers[i].key.data, cc_name,
                                   sizeof(cc_name) - 1) != 0)
            {
                continue;
            }

            if (ngx_http_markdown_header_has_cache_directive(
                    &headers[i], directive, directive_len))
            {
                return 1;
            }
        }
    }

    return 0;
}

/*
 * Gather conditional request headers.
 *
 * Reads If-Match, If-Unmodified-Since, If-None-Match, If-Modified-Since,
 * and Range from request headers.
 * Outputs are written through the caller-provided pointers.
 *
 * Response-side Last-Modified is consulted separately by the C-side
 * If-Unmodified-Since check when an active response header exists.  The
 * source HTML mtime is not used as a fallback because it describes a
 * different representation.
 */
static void
ngx_http_markdown_collect_conditional_headers(ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx,
    ngx_table_elt_t **inm_header, ngx_table_elt_t **ims_header,
    ngx_table_elt_t **im_header, ngx_table_elt_t **ius_header,
    ngx_table_elt_t **range_header)
{
    if (ctx != NULL && ctx->conditional.captured) {
        *inm_header = ctx->conditional.if_none_match;
        *ims_header = ctx->conditional.if_modified_since;
        *im_header = ctx->conditional.if_match;
        *ius_header = ctx->conditional.if_unmodified_since;
    } else {
        static u_char  if_none_match_name[] = "If-None-Match";
        *inm_header = ngx_http_markdown_find_request_header(
            r, if_none_match_name, sizeof(if_none_match_name) - 1);

        static u_char  if_modified_since_name[] = "If-Modified-Since";
        *ims_header = ngx_http_markdown_find_request_header(
            r, if_modified_since_name, sizeof(if_modified_since_name) - 1);

        static u_char  if_match_name[] = "If-Match";
        *im_header = ngx_http_markdown_find_request_header(
            r, if_match_name, sizeof(if_match_name) - 1);

        static u_char  if_unmodified_since_name[] = "If-Unmodified-Since";
        *ius_header = ngx_http_markdown_find_request_header(
            r, if_unmodified_since_name,
            sizeof(if_unmodified_since_name) - 1);
    }

    {
        static u_char  range_name[] = "Range";
        *range_header = ngx_http_markdown_find_request_header(
            r, range_name, sizeof(range_name) - 1);
    }
}

/* Return whether a request-header name is a captured validator. */
static ngx_flag_t
ngx_http_markdown_is_captured_conditional_name(const ngx_str_t *key)
{
    static u_char  if_none_match_name[] = "If-None-Match";
    static u_char  if_modified_since_name[] = "If-Modified-Since";
    static u_char  if_match_name[] = "If-Match";
    static u_char  if_unmodified_since_name[] = "If-Unmodified-Since";

    if (key == NULL || key->data == NULL) {
        return 0;
    }

    if (key->len == sizeof(if_none_match_name) - 1
        && ngx_strncasecmp(key->data, if_none_match_name, key->len) == 0)
    {
        return 1;
    }

    if (key->len == sizeof(if_modified_since_name) - 1
        && ngx_strncasecmp(key->data, if_modified_since_name,
                           key->len) == 0)
    {
        return 1;
    }

    if (key->len == sizeof(if_match_name) - 1
        && ngx_strncasecmp(key->data, if_match_name, key->len) == 0)
    {
        return 1;
    }

    return (key->len == sizeof(if_unmodified_since_name) - 1
            && ngx_strncasecmp(key->data, if_unmodified_since_name,
                               key->len) == 0);
}

/*
 * Suppress only entries recorded by capture, preserving pre-existing state.
 * Empty values keep generic upstream header forwarders from evaluating the
 * validators even when they do not honor hash==0 entries.
 */
static void
ngx_http_markdown_suppress_captured_conditional_headers(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    for (ngx_http_markdown_conditional_header_state_t *state =
             ctx->conditional.header_states;
         state != NULL;
         state = state->next)
    {
        /* Clear the hash AND zero the value length.  NGINX's proxy module
         * forwards request headers without checking hash==0; the empty
         * value length is what actually suppresses the validator from the
         * upstream request.  The value bytes stay request-pool owned and
         * are restored by restore_captured_conditional_headers (or rebuilt
         * via ngx_strlen after an internal redirect orphans the entry). */
        state->header->hash = 0;
        state->header->value.len = 0;
    }

    r->headers_in.if_none_match = NULL;
    r->headers_in.if_modified_since = NULL;
    r->headers_in.if_match = NULL;
    r->headers_in.if_unmodified_since = NULL;
}

/* Restore each entry to the state observed before capture. */
static void
ngx_http_markdown_restore_captured_conditional_headers(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    for (ngx_http_markdown_conditional_header_state_t *state =
             ctx->conditional.header_states;
         state != NULL;
         state = state->next)
    {
        state->header->hash = state->original_hash;
        state->header->value.len = state->original_value_len;
    }

    r->headers_in.if_none_match = ctx->conditional.if_none_match;
    r->headers_in.if_modified_since = ctx->conditional.if_modified_since;
    r->headers_in.if_match = ctx->conditional.if_match;
    r->headers_in.if_unmodified_since = ctx->conditional.if_unmodified_since;
}

/*
 * Return the captured validator length while its request header is hidden.
 * The value bytes remain request-pool owned and are safe to inspect for the
 * converted-representation decision.
 */
static size_t
ngx_http_markdown_conditional_value_len(
    const ngx_http_markdown_ctx_t *ctx, const ngx_table_elt_t *header)
{
    if (header == NULL || ctx == NULL || !ctx->conditional.captured) {
        return (header == NULL) ? 0 : header->value.len;
    }

    for (const ngx_http_markdown_conditional_header_state_t *state =
             ctx->conditional.header_states;
         state != NULL;
         state = state->next)
    {
        if (state->header == header) {
            return state->original_value_len;
        }
    }

    return header->value.len;
}

/* Return whether a header entry carries an If-Match field value. */
static ngx_flag_t
ngx_http_markdown_is_if_match_header(const ngx_table_elt_t *header)
{
    static u_char  if_match_name[] = "If-Match";

    return header != NULL
           && header->key.data != NULL
           && header->key.len == sizeof(if_match_name) - 1
           && ngx_strncasecmp(header->key.data, if_match_name,
                              header->key.len) == 0;
}

/* Return whether an entity tag uses weak comparison. */
static ngx_flag_t
ngx_http_markdown_is_weak_etag(const u_char *etag, size_t etag_len)
{
    return etag != NULL && etag_len >= 2
           && (etag[0] == 'W' || etag[0] == 'w')
           && etag[1] == '/';
}

static ngx_flag_t
ngx_http_markdown_if_match_token_matches(const u_char *token_start,
    size_t token_len, const u_char *etag, size_t etag_len,
    ngx_flag_t etag_is_weak)
{
    if (token_start == NULL || token_len == 0) {
        return 0;
    }

    if (token_len == 1 && token_start[0] == '*') {
        return 1;
    }

    if (token_len >= 2
        && (token_start[0] == 'W' || token_start[0] == 'w')
        && token_start[1] == '/')
    {
        return 0;
    }

    return !etag_is_weak && etag != NULL && token_len == etag_len
           && memcmp(token_start, etag, etag_len) == 0;
}

/*
 * Apply the strong If-Match comparison to one field value.  Wildcard values
 * match any current representation; weak request tags never satisfy the
 * strong comparison required by RFC 7232.
 */
static ngx_flag_t
ngx_http_markdown_if_match_value_matches(const u_char *value,
    size_t value_len, const u_char *etag, size_t etag_len)
{
    const u_char  *p;
    const u_char  *end;
    const u_char  *token_start;
    const u_char  *token_end;
    ngx_flag_t     etag_is_weak;

    if (value == NULL || value_len == 0) {
        return 0;
    }

    etag_is_weak = ngx_http_markdown_is_weak_etag(etag, etag_len);
    p = value;
    end = value + value_len;

    while (p < end) {
        while (p < end && (*p == ' ' || *p == '\t' || *p == ',')) {
            p++;
        }
        token_start = p;
        while (p < end && *p != ',') {
            p++;
        }
        token_end = p;
        while (token_end > token_start
               && (token_end[-1] == ' ' || token_end[-1] == '\t'))
        {
            token_end--;
        }

        if (token_end == token_start) {
            continue;
        }

        if (ngx_http_markdown_if_match_token_matches(
                token_start, (size_t) (token_end - token_start), etag,
                etag_len, etag_is_weak))
        {
            return 1;
        }
    }

    return 0;
}

/*
 * Scan the request's If-Match field lines for a strong match against the
 * generated entity tag.
 *
 * Returns:
 *   1  a matching field line was found
 *   0  no matching field line was found (fallback evaluation may apply)
 *  -1  the request header list is malformed, so the preconditions cannot
 *      be evaluated and the caller must fail the check outright
 */
static ngx_int_t
ngx_http_markdown_request_has_matching_if_match(
    const ngx_http_request_t *r, const u_char *etag, size_t etag_len)
{
    for (const ngx_list_part_t *part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        const ngx_table_elt_t  *headers;

        headers = part->elts;
        if (headers == NULL && part->nelts != 0) {
            return -1;
        }

        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash != 0
                && ngx_http_markdown_is_if_match_header(&headers[i])
                && ngx_http_markdown_if_match_value_matches(
                       headers[i].value.data, headers[i].value.len,
                       etag, etag_len))
            {
                return 1;
            }
        }
    }

    return 0;
}

/* Compare every captured or active If-Match field against the generated tag. */
static ngx_flag_t
ngx_http_markdown_if_match_satisfied(const ngx_http_request_t *r,
    const ngx_http_markdown_ctx_t *ctx, const ngx_table_elt_t *fallback,
    const u_char *etag, size_t etag_len)
{
    if (ctx != NULL && ctx->conditional.captured) {
        for (const ngx_http_markdown_conditional_header_state_t *state =
                 ctx->conditional.header_states;
             state != NULL;
             state = state->next)
        {
            if (ngx_http_markdown_is_if_match_header(state->header)
                && ngx_http_markdown_if_match_value_matches(
                       state->header->value.data,
                       state->original_value_len, etag, etag_len))
            {
                return 1;
            }
        }

        return 0;
    }

    if (r != NULL) {
        ngx_int_t  match_rc;

        match_rc = ngx_http_markdown_request_has_matching_if_match(
            r, etag, etag_len);
        /* 1 = matched, -1 = malformed header list (fail the check). */
        if (match_rc != 0) {
            return (match_rc == 1) ? 1 : 0;
        }
    }

    return ngx_http_markdown_if_match_value_matches(
        (fallback == NULL) ? NULL : fallback->value.data,
        ngx_http_markdown_conditional_value_len(ctx, fallback),
        etag, etag_len);
}

/* Return whether a header entry carries a Last-Modified field value. */
static ngx_flag_t
ngx_http_markdown_is_last_modified_header(const ngx_table_elt_t *header)
{
    static u_char  last_modified_name[] = "Last-Modified";

    return header != NULL
           && header->key.data != NULL
           && header->key.len == sizeof(last_modified_name) - 1
           && ngx_strncasecmp(header->key.data, last_modified_name,
                              header->key.len) == 0;
}

/* Find the active response Last-Modified header, ignoring invalidated slots. */
static const ngx_table_elt_t *
ngx_http_markdown_find_last_modified_header(const ngx_http_request_t *r)
{
    if (r == NULL) {
        return NULL;
    }

    if (r->headers_out.last_modified != NULL
        && r->headers_out.last_modified->hash != 0
        && ngx_http_markdown_is_last_modified_header(
               r->headers_out.last_modified))
    {
        return r->headers_out.last_modified;
    }

    for (const ngx_list_part_t *part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        const ngx_table_elt_t  *headers;

        headers = part->elts;
        if (headers == NULL && part->nelts != 0) {
            return NULL;
        }

        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash != 0
                && ngx_http_markdown_is_last_modified_header(&headers[i]))
            {
                return &headers[i];
            }
        }
    }

    return NULL;
}

/*
 * Evaluate If-Unmodified-Since against the response representation date.
 * Invalid dates and a missing Last-Modified header leave the condition
 * satisfied, as required for a transformed response without that validator.
 */
static ngx_flag_t
ngx_http_markdown_if_unmodified_since_satisfied(
    const ngx_http_markdown_ctx_t *ctx,
    const ngx_table_elt_t *ius_header,
    const ngx_table_elt_t *last_modified_header)
{
    time_t  request_time;
    time_t  last_modified_time;
    size_t  ius_len;

    if (ius_header == NULL || last_modified_header == NULL
        || last_modified_header->value.data == NULL
        || last_modified_header->value.len == 0)
    {
        return 1;
    }

    ius_len = ngx_http_markdown_conditional_value_len(ctx, ius_header);
    if (ius_len == 0 || ius_header->value.data == NULL) {
        return 1;
    }

    request_time = ngx_parse_http_time(ius_header->value.data, ius_len);
    last_modified_time = ngx_parse_http_time(
        last_modified_header->value.data, last_modified_header->value.len);
    if (request_time == (time_t) -1
        || last_modified_time == (time_t) -1)
    {
        return 1;
    }

    return last_modified_time <= request_time;
}

static ngx_int_t
ngx_http_markdown_validate_if_unmodified_since(
    const ngx_http_markdown_ctx_t *ctx,
    const ngx_table_elt_t *ius_header,
    const ngx_table_elt_t *last_modified_header)
{
    if (ius_header != NULL
        && !ngx_http_markdown_if_unmodified_since_satisfied(
               ctx, ius_header, last_modified_header))
    {
        return NGX_HTTP_PRECONDITION_FAILED;
    }

    return NGX_OK;
}

typedef struct {
    u_char         *single_data;
    size_t         single_len;
    ngx_uint_t     match_count;
    size_t         total_len;
} ngx_http_markdown_if_none_match_measurement_t;

/* Return whether a header entry carries an If-None-Match field value. */
static ngx_flag_t
ngx_http_markdown_is_if_none_match_header(const ngx_table_elt_t *header)
{
    static u_char  if_none_match_name[] = "If-None-Match";

    return header != NULL
           && header->key.data != NULL
           && header->key.len == sizeof(if_none_match_name) - 1
           && ngx_strncasecmp(header->key.data, if_none_match_name,
                              header->key.len) == 0;
}

/* Add one field-line length, including its RFC combined-value separator. */
static ngx_int_t
ngx_http_markdown_add_if_none_match_length(
    ngx_http_markdown_if_none_match_measurement_t *measurement,
    size_t value_len)
{
    if (measurement->match_count > 0) {
        if (measurement->total_len > (size_t) -1 - 2
            || measurement->total_len + 2
               > NGX_HTTP_MARKDOWN_IF_NONE_MATCH_MAX)
        {
            return NGX_ERROR;
        }
        measurement->total_len += 2;
    }

    if (value_len > (size_t) -1 - measurement->total_len
        || value_len > NGX_HTTP_MARKDOWN_IF_NONE_MATCH_MAX
                       - measurement->total_len)
    {
        return NGX_ERROR;
    }

    measurement->total_len += value_len;
    return NGX_OK;
}

/* Measure all captured If-None-Match entries, including suppressed values. */
static ngx_int_t
ngx_http_markdown_measure_captured_if_none_match(
    const ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_if_none_match_measurement_t *measurement)
{
    ngx_int_t  rc;

    for (const ngx_http_markdown_conditional_header_state_t *state =
             ctx->conditional.header_states;
         state != NULL;
         state = state->next)
    {
        if (!ngx_http_markdown_is_if_none_match_header(state->header)) {
            continue;
        }
        if (state->original_value_len != 0
            && state->header->value.data == NULL)
        {
            return NGX_ERROR;
        }

        rc = ngx_http_markdown_add_if_none_match_length(
            measurement, state->original_value_len);
        if (rc != NGX_OK) {
            return rc;
        }

        if (measurement->match_count == 0) {
            measurement->single_data = state->header->value.data;
            measurement->single_len = state->original_value_len;
        }
        measurement->match_count++;
    }

    return NGX_OK;
}

/* Measure captured If-None-Match entries and the typed fallback. */
static ngx_int_t
ngx_http_markdown_measure_captured_if_none_match_with_fallback(
    const ngx_http_markdown_ctx_t *ctx,
    ngx_http_markdown_if_none_match_measurement_t *measurement)
{
    ngx_table_elt_t  *fallback;
    ngx_int_t          rc;

    rc = ngx_http_markdown_measure_captured_if_none_match(
        ctx, measurement);
    if (rc != NGX_OK) {
        return rc;
    }

    if (measurement->match_count != 0
        || ctx->conditional.if_none_match == NULL)
    {
        return NGX_OK;
    }

    fallback = ctx->conditional.if_none_match;
    measurement->single_data = fallback->value.data;
    measurement->single_len =
        ngx_http_markdown_conditional_value_len(ctx, fallback);
    if (measurement->single_len != 0
        && measurement->single_data == NULL)
    {
        return NGX_ERROR;
    }

    rc = ngx_http_markdown_add_if_none_match_length(
        measurement, measurement->single_len);
    if (rc != NGX_OK) {
        return rc;
    }
    measurement->match_count = 1;

    return NGX_OK;
}


/* Measure all active If-None-Match entries in request-list order. */
static ngx_int_t
ngx_http_markdown_measure_request_if_none_match(
    const ngx_http_request_t *r,
    ngx_http_markdown_if_none_match_measurement_t *measurement)
{
    ngx_int_t  rc;

    for (const ngx_list_part_t *part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        const ngx_table_elt_t  *headers;

        headers = part->elts;
        if (headers == NULL && part->nelts != 0) {
            return NGX_ERROR;
        }

        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0
                || !ngx_http_markdown_is_if_none_match_header(&headers[i]))
            {
                continue;
            }
            if (headers[i].value.len != 0
                && headers[i].value.data == NULL)
            {
                return NGX_ERROR;
            }

            rc = ngx_http_markdown_add_if_none_match_length(
                measurement, headers[i].value.len);
            if (rc != NGX_OK) {
                return rc;
            }

            if (measurement->match_count == 0) {
                measurement->single_data = headers[i].value.data;
                measurement->single_len = headers[i].value.len;
            }
            measurement->match_count++;
        }
    }

    return NGX_OK;
}

/* Copy captured If-None-Match entries using their original value lengths. */
static u_char *
ngx_http_markdown_copy_captured_if_none_match(
    const ngx_http_markdown_ctx_t *ctx, u_char *data)
{
    ngx_uint_t  copied;
    u_char      *p;

    copied = 0;
    p = data;
    for (const ngx_http_markdown_conditional_header_state_t *state =
             ctx->conditional.header_states;
         state != NULL;
         state = state->next)
    {
        if (!ngx_http_markdown_is_if_none_match_header(state->header)) {
            continue;
        }
        if (copied != 0) {
            *p++ = ',';
            *p++ = ' ';
        }
        if (state->original_value_len != 0) {
            p = ngx_cpymem(p, state->header->value.data,
                           state->original_value_len);
        }
        copied++;
    }

    return p;
}

/* Copy active If-None-Match entries using request-list order. */
static u_char *
ngx_http_markdown_copy_request_if_none_match(
    const ngx_http_request_t *r, u_char *data)
{
    ngx_uint_t  copied;
    u_char      *p;

    copied = 0;
    p = data;
    for (const ngx_list_part_t *part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        const ngx_table_elt_t  *headers;

        headers = part->elts;
        if (headers == NULL && part->nelts != 0) {
            return data;
        }
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0
                || !ngx_http_markdown_is_if_none_match_header(&headers[i]))
            {
                continue;
            }
            if (copied != 0) {
                *p++ = ',';
                *p++ = ' ';
            }
            if (headers[i].value.len != 0) {
                p = ngx_cpymem(p, headers[i].value.data,
                               headers[i].value.len);
            }
            copied++;
        }
    }

    return p;
}

/* Combine all If-None-Match field-lines before the FFI decision. */
static ngx_int_t
ngx_http_markdown_collect_if_none_match_value(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx,
    ngx_str_t *out)
{
    ngx_http_markdown_if_none_match_measurement_t  measurement;
    ngx_int_t                                       rc;
    const u_char                                   *end;

    if (r == NULL || out == NULL) {
        return NGX_ERROR;
    }

    memset(&measurement, 0, sizeof(measurement));
    out->data = NULL;
    out->len = 0;

    if (ctx != NULL && ctx->conditional.captured) {
        rc = ngx_http_markdown_measure_captured_if_none_match_with_fallback(
            ctx, &measurement);
    } else {
        rc = ngx_http_markdown_measure_request_if_none_match(
            r, &measurement);
    }
    if (rc != NGX_OK) {
        return rc;
    }

    if (measurement.match_count == 0) {
        return NGX_DECLINED;
    }
    if (measurement.match_count == 1) {
        out->data = measurement.single_data;
        out->len = measurement.single_len;
        return NGX_OK;
    }

    out->data = ngx_pnalloc(r->pool, measurement.total_len);
    if (out->data == NULL) {
        return NGX_ERROR;
    }

    if (ctx != NULL && ctx->conditional.captured) {
        end = ngx_http_markdown_copy_captured_if_none_match(
            ctx, out->data);
    } else {
        end = ngx_http_markdown_copy_request_if_none_match(r, out->data);
    }
    if ((size_t) (end - out->data) != measurement.total_len) {
        return NGX_ERROR;
    }

    out->len = measurement.total_len;
    return NGX_OK;
}

/*
 * Return whether the request has a conditional validator that can be held
 * while a negotiated Markdown response is obtained.  Range requests remain
 * source-representation requests and must not be intercepted here.
 */
ngx_flag_t
ngx_http_markdown_has_conditional_request(ngx_http_request_t *r)
{
    ngx_table_elt_t  *inm_header;
    ngx_table_elt_t  *ims_header;
    ngx_table_elt_t  *im_header;
    ngx_table_elt_t  *ius_header;
    ngx_table_elt_t  *range_header;

    if (r == NULL) {
        return 0;
    }

    ngx_http_markdown_collect_conditional_headers(
        r, NULL, &inm_header, &ims_header, &im_header, &ius_header,
        &range_header);

    return (range_header == NULL
            && (inm_header != NULL || ims_header != NULL
                || im_header != NULL || ius_header != NULL));
}

/*
 * Capture source validators before the upstream content handler or cache can
 * produce a conditional response.  Repeated phase execution re-applies the
 * suppression to the same request-owned entries, which also covers internal
 * redirects before the response header is generated.
 */
ngx_int_t
ngx_http_markdown_capture_conditional_request(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    ngx_table_elt_t  *inm_header;
    ngx_table_elt_t  *ims_header;
    ngx_table_elt_t  *im_header;
    ngx_table_elt_t  *ius_header;
    ngx_table_elt_t  *range_header;
    ngx_http_markdown_conditional_header_state_t  *state;
    ngx_http_markdown_conditional_header_state_t  *tail;
    ngx_table_elt_t  *headers;

    if (r == NULL || ctx == NULL) {
        return NGX_ERROR;
    }

    /* A subrequest shares the parent request's headers_in.  Capturing on a
     * subrequest would suppress the parent's validators (hash = 0) while the
     * parent is still mid-flight and cannot restore them (its ctx is
     * independent).  Only convert validators on the main request. */
    if (r->parent != NULL) {
        return NGX_DECLINED;
    }

    if (ctx->conditional.captured) {
        ngx_http_markdown_collect_conditional_headers(
            r, NULL, &inm_header, &ims_header, &im_header, &ius_header,
            &range_header);
        if (range_header != NULL) {
            ngx_http_markdown_restore_conditional_request(r, ctx);
            return NGX_DECLINED;
        }

        ngx_http_markdown_suppress_captured_conditional_headers(r, ctx);
        ctx->conditional.suppressed = 1;
        return NGX_OK;
    }

    ngx_http_markdown_collect_conditional_headers(
        r, NULL, &inm_header, &ims_header, &im_header, &ius_header,
        &range_header);
    if (range_header != NULL
        || (inm_header == NULL && ims_header == NULL
            && im_header == NULL && ius_header == NULL))
    {
        return NGX_DECLINED;
    }

    ctx->conditional.header_states = NULL;
    tail = NULL;
    for (ngx_list_part_t *part = &r->headers_in.headers.part;
         part != NULL;
         part = part->next)
    {
        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            if (headers[i].hash == 0
                || !ngx_http_markdown_is_captured_conditional_name(
                       &headers[i].key))
            {
                continue;
            }

            state = ngx_pcalloc(r->pool, sizeof(*state));
            if (state == NULL) {
                return NGX_ERROR;
            }

            state->header = &headers[i];
            state->original_hash = headers[i].hash;
            state->original_value_len = headers[i].value.len;
            if (tail == NULL) {
                ctx->conditional.header_states = state;
            } else {
                tail->next = state;
            }
            tail = state;
        }
    }

    ctx->conditional.if_none_match = inm_header;
    ctx->conditional.if_modified_since = ims_header;
    ctx->conditional.if_match = im_header;
    ctx->conditional.if_unmodified_since = ius_header;
    ctx->conditional.captured = 1;

    ngx_http_markdown_suppress_captured_conditional_headers(r, ctx);
    ctx->conditional.suppressed = 1;
    return NGX_OK;
}

/* Restore captured validators before a source representation is forwarded. */
void
ngx_http_markdown_restore_conditional_request(
    ngx_http_request_t *r, ngx_http_markdown_ctx_t *ctx)
{
    if (r == NULL || ctx == NULL || !ctx->conditional.captured
        || !ctx->conditional.suppressed)
    {
        return;
    }

    ngx_http_markdown_restore_captured_conditional_headers(r, ctx);
    ctx->conditional.suppressed = 0;
}

/*
 * Translate an early (non-entity-ETag) FFI conditional decision outcome
 * into the NGINX return code the caller should return.  Encapsulated as a
 * helper so the main function's cognitive complexity stays below threshold.
 */
static ngx_int_t
ngx_http_markdown_conditional_early_outcome(
    const struct FFIConditionalDecision *cond_decision)
{
    if (cond_decision->outcome == 0) {
        return NGX_HTTP_NOT_MODIFIED;
    }

    if (cond_decision->outcome == 2) {
        return NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT;
    }

    return NGX_DECLINED;
}

/*
 * Evaluate and handle a conditional request for a Markdown response.
 *
 * When full cache validation needs an entity ETag, this function performs a
 * conversion to generate the Markdown variant ETag, evaluates the request
 * preconditions, then delegates the remaining cache decision to Rust FFI
 * (markdown_decide_conditional).
 *
 * Validator freeze: the FFI decision input carries no If-Modified-Since and
 * no source Last-Modified value.  Every request reaching this function would
 * be answered with the transformed Markdown representation, and that
 * representation is validated solely by its own ETag for cache revalidation;
 * source HTML freshness must never synthesize a Not Modified answer for
 * content this module replaces.  The C-side If-Unmodified-Since check uses
 * only an active response Last-Modified header, and requests carrying only
 * If-Modified-Since therefore fall through to conversion and receive a fresh
 * 200 response.
 *
 * @param r        The request structure.
 * @param conf     Module configuration controlling conditional request behavior and ETag generation.
 * @param ctx      Request context containing the prepared input buffer and processing path.
 * @param converter Worker-scoped converter handle required for FFI conversion (must not be NULL when conversion is needed).
 * @param result   Output pointer; on successful conversion this will be set to a newly allocated MarkdownResult.
 * @returns        NGX_HTTP_PRECONDITION_FAILED (412) when an If-Match or
 *                 If-Unmodified-Since precondition fails,
 *                 NGX_HTTP_NOT_MODIFIED (304) if the generated ETag matches
 *                 the client's If-None-Match,
 *                 NGX_DECLINED if no match or processing is skipped,
 *                 NGX_ERROR on failure (parsing, allocation, conversion, or internal errors).
 */
static ngx_flag_t
ngx_http_markdown_is_downstream_transcoding(const ngx_http_request_t *r)
{
    static u_char  utf8[] = "utf-8";

    return (r->headers_out.override_charset != NULL
            && r->headers_out.override_charset->len > 0
            && (r->headers_out.override_charset->len != 5
                || ngx_strncasecmp(r->headers_out.override_charset->data,
                                   utf8, 5) != 0));
}

static ngx_flag_t
ngx_http_markdown_can_compare_etag(const ngx_http_request_t *r,
    const ngx_http_markdown_conf_t *conf)
{
    if (!conf->policy.generate_etag) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: ETag generation disabled, "
                      "cannot perform entity-tag comparison");
        return 0;
    }

    if (ngx_http_markdown_is_downstream_transcoding(r)) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: downstream charset transcoding configured, "
                      "disabling entity-tag comparison");
        return 0;
    }

    return 1;
}

/* Bundled preconditions for the entity-tag generation pass. */
typedef struct {
    const ngx_table_elt_t  *im_header;
    const ngx_table_elt_t  *ius_header;
    const ngx_table_elt_t  *last_modified_header;
} ngx_http_markdown_conditional_validators_t;

static ngx_int_t
ngx_http_markdown_generate_conditional_result(
    ngx_http_request_t *r, const ngx_http_markdown_ctx_t *ctx,
    const ngx_http_markdown_conf_t *conf,
    struct MarkdownConverterHandle *converter,
    const ngx_http_markdown_conditional_validators_t *validators,
    struct MarkdownResult **result)
{
    struct MarkdownOptions  options;
    struct MarkdownResult   *conv_result;

    *result = NULL;

    if (!ctx->buffer_initialized || ctx->buffer.size == 0) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: buffer not initialized for "
                     "entity-tag conditional check");
        return NGX_ERROR;
    }

    if (converter == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: converter handle is NULL during "
                     "entity-tag conditional check");
        return NGX_ERROR;
    }

    if (ngx_http_markdown_prepare_conversion_options(
            r, conf, ctx->effective_conf, &options)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    options.generate_etag = 1;
    conv_result = ngx_pcalloc(r->pool, sizeof(struct MarkdownResult));
    if (conv_result == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate conversion result");
        return NGX_ERROR;
    }

    if (ngx_http_markdown_convert_for_conditional(
            ctx, converter, &options, conv_result)
        != NGX_OK)
    {
        ngx_pfree(r->pool, conv_result);
        return NGX_ERROR;
    }

    if (conv_result->error_code != 0) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown: conversion failed during conditional check: "
                     "error_code=%ud message=\"%*s\"",
                     conv_result->error_code,
                     (conv_result->error_message != NULL) ? (ngx_int_t) conv_result->error_len : 0,
                     (conv_result->error_message != NULL) ? conv_result->error_message : (u_char *) "");

        markdown_result_free(conv_result);
        return NGX_ERROR;
    }

    if (validators->im_header != NULL
        && !ngx_http_markdown_if_match_satisfied(
               r, ctx, validators->im_header,
               conv_result->etag, conv_result->etag_len))
    {
        markdown_result_free(conv_result);
        return NGX_HTTP_PRECONDITION_FAILED;
    }

    if (ngx_http_markdown_validate_if_unmodified_since(
            ctx, validators->ius_header, validators->last_modified_header)
        != NGX_OK)
    {
        markdown_result_free(conv_result);
        return NGX_HTTP_PRECONDITION_FAILED;
    }

    *result = conv_result;
    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_handle_if_none_match(ngx_http_request_t *r,
                                       const ngx_http_markdown_conf_t *conf,
                                       const ngx_http_markdown_ctx_t *ctx,
                                       struct MarkdownConverterHandle *converter,
                                       struct MarkdownResult **result)
{
    struct MarkdownResult    *conv_result;
    struct FFIConditionalInput  cond_input;
    struct FFIConditionalDecision cond_decision;
    ngx_table_elt_t         *inm_header;
    ngx_table_elt_t         *ims_header;
    ngx_table_elt_t         *im_header;
    ngx_table_elt_t         *ius_header;
    ngx_table_elt_t         *range_header;
    const ngx_table_elt_t   *last_modified_header;
    ngx_http_markdown_conditional_validators_t  validators;
    const u_char            *inm_data;
    size_t                   inm_len;
    ngx_str_t                inm_value;
    ngx_int_t                inm_rc;
    ngx_int_t                conditional_rc;
    ngx_flag_t               needs_entity_etag;

    if (conf->policy.conditional_requests == NGX_HTTP_MARKDOWN_CONDITIONAL_DISABLED) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: conditional requests disabled, "
                      "skipping conditional evaluation");
        return NGX_DECLINED;
    }

    ngx_http_markdown_collect_conditional_headers(
        r, ctx, &inm_header, &ims_header, &im_header, &ius_header,
        &range_header);

    /* RFC 7232 section 3.3: a recipient MUST ignore If-Unmodified-Since
     * when the request carries an If-Match header field.  The two
     * validators address the same precondition; If-Match (entity tag)
     * wins because it is evaluated first and is more specific. */
    if (im_header != NULL) {
        ius_header = NULL;
    }


    inm_value.data = NULL;
    inm_value.len = 0;
    if (inm_header != NULL) {
        inm_rc = ngx_http_markdown_collect_if_none_match_value(
            r, ctx, &inm_value);
        if (inm_rc != NGX_OK) {
            return NGX_ERROR;
        }
        inm_data = inm_value.data;
        inm_len = inm_value.len;
    } else {
        inm_data = NULL;
        inm_len = 0;
    }

    /* If-Modified-Since presence still matters for bypass eligibility of a
     * bare-conditional request below; its VALUE never reaches the decision
     * input. */
    if (inm_header == NULL && ims_header == NULL
        && im_header == NULL && ius_header == NULL)
    {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: no conditional request headers");
        return NGX_DECLINED;
    }

    memset(&cond_input, 0, sizeof(cond_input));
    memset(&cond_decision, 0, sizeof(cond_decision));
    cond_input.cache_validation = ngx_http_markdown_conditional_cache_validation(
        conf->policy.conditional_requests);
    cond_input.has_range = (range_header != NULL) ? 1 : 0;
    cond_input.no_transform =
        ngx_http_markdown_has_no_transform(r) ? 1 : 0;
    cond_input.if_none_match = inm_data;
    cond_input.if_none_match_len = inm_len;
    last_modified_header = ngx_http_markdown_find_last_modified_header(r);

    needs_entity_etag =
        (conf->policy.conditional_requests
            == NGX_HTTP_MARKDOWN_CONDITIONAL_FULL_SUPPORT
         && (inm_header != NULL || im_header != NULL));

    if (!needs_entity_etag) {
        if (ngx_http_markdown_validate_if_unmodified_since(
                ctx, ius_header, last_modified_header)
            != NGX_OK)
        {
            return NGX_HTTP_PRECONDITION_FAILED;
        }

        markdown_decide_conditional(&cond_input, &cond_decision);
        return ngx_http_markdown_conditional_early_outcome(&cond_decision);
    }

    if (!ngx_http_markdown_can_compare_etag(r, conf)) {
        if (ngx_http_markdown_validate_if_unmodified_since(
                ctx, ius_header, last_modified_header)
            != NGX_OK)
        {
            return NGX_HTTP_PRECONDITION_FAILED;
        }

        return NGX_DECLINED;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: entity-tag precondition present, "
                  "performing conversion "
                  "to generate ETag for comparison (performance cost)");

    validators.im_header = im_header;
    validators.ius_header = ius_header;
    validators.last_modified_header = last_modified_header;

    conditional_rc = ngx_http_markdown_generate_conditional_result(
        r, ctx, conf, converter, &validators, &conv_result);
    if (conditional_rc != NGX_OK) {
        return conditional_rc;
    }

    cond_input.entity_etag = conv_result->etag;
    cond_input.entity_etag_len = conv_result->etag_len;
    markdown_decide_conditional(&cond_input, &cond_decision);

    if (cond_decision.outcome == 0) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: conditional matched, returning 304 Not Modified");

        *result = conv_result;

        return NGX_HTTP_NOT_MODIFIED;
    }

    if (cond_decision.outcome == 2) {
        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: conditional bypass after conversion "
                      "(Range or no-transform), delivering upstream "
                      "unmodified");

        /*
         * The conversion was performed to generate the ETag, but the
         * conditional decision says Bypass.  Free the conversion result
         * and signal bypass to the caller.
         */
        markdown_result_free(conv_result);

        return NGX_HTTP_MARKDOWN_COND_BYPASS_RESULT;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: conditional proceeded, returning 200 with content");

    *result = conv_result;

    return NGX_DECLINED;
}

/*
 * Send 304 Not Modified response
 *
 * Constructs and sends a 304 Not Modified response with appropriate headers.
 * The response includes:
 * - Status: 304 Not Modified
 * - ETag: The matching ETag
 * - Vary: Accept (for cache correctness)
 * - No body (per HTTP specification)
 *
 * @param r         The request structure
 * @param result    Conversion result (contains ETag)
 * @return          NGX_DONE on success (the caller finalizes the request),
 *                  NGX_ERROR on failure,
 *                  or rc from ngx_http_send_header on partial failure
 */
/*
 * The 304 path has to preserve the same failure contract as the normal
 * representation update.  The helpers below snapshot both outgoing lists and
 * every dedicated field touched by ngx_http_markdown_send_304() before any
 * mutation.  A failed ETag, Vary, or authenticated Cache-Control operation
 * can therefore restore the exact upstream representation.
 */
#define NGX_HTTP_MARKDOWN_304_SNAPSHOT_MAX_ENTRIES  1024

typedef struct {
    ngx_table_elt_t  saved;
} ngx_http_markdown_304_snapshot_entry_t;

typedef struct {
    ngx_http_markdown_304_snapshot_entry_t  *entries;
    ngx_uint_t                              entry_count;
    ngx_list_part_t                         *original_last;
    ngx_uint_t                              original_last_nelts;
    ngx_list_part_t                         *original_last_next;
} ngx_http_markdown_304_list_snapshot_t;

typedef struct {
    ngx_uint_t                              status;
    ngx_str_t                               status_line;
    ngx_list_t                              headers;
    ngx_list_t                              trailers;
    ngx_str_t                               content_type;
    u_char                                  *content_type_lowcase;
    ngx_uint_t                              content_type_hash;
    ngx_str_t                               charset;
    size_t                                  content_type_len;
    ngx_table_elt_t                         *content_length;
    ngx_table_elt_t                         *content_encoding;
    ngx_table_elt_t                         *etag;
    ngx_table_elt_t                         *accept_ranges;
    ngx_table_elt_t                         *last_modified;
    off_t                                   content_length_n;
    time_t                                  last_modified_time;
    unsigned                                allow_ranges;
    ngx_http_markdown_304_list_snapshot_t  headers_snapshot;
    ngx_http_markdown_304_list_snapshot_t  trailers_snapshot;
} ngx_http_markdown_304_snapshot_t;

static ngx_int_t
ngx_http_markdown_304_snapshot_list(ngx_pool_t *pool, ngx_list_t *list,
    ngx_http_markdown_304_list_snapshot_t *snapshot)
{
    ngx_list_part_t          *part;
    const ngx_table_elt_t    *entries;
    ngx_uint_t                count;

    if (list == NULL || snapshot == NULL) {
        return NGX_ERROR;
    }

    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->original_last = list->last;
    if (snapshot->original_last != NULL) {
        snapshot->original_last_nelts = snapshot->original_last->nelts;
        snapshot->original_last_next = snapshot->original_last->next;
    }

    count = 0;
    for (part = &list->part; part != NULL; part = part->next) {
        if (part->nelts > NGX_HTTP_MARKDOWN_304_SNAPSHOT_MAX_ENTRIES
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

    if ((size_t) count
        > ((size_t) -1) / sizeof(ngx_http_markdown_304_snapshot_entry_t))
    {
        return NGX_ERROR;
    }

    snapshot->entries = ngx_pnalloc(pool,
        (size_t) count * sizeof(ngx_http_markdown_304_snapshot_entry_t));
    if (snapshot->entries == NULL) {
        return NGX_ERROR;
    }

    count = 0;
    for (part = &list->part; part != NULL; part = part->next) {
        if (part->nelts > 0 && part->elts == NULL) {
            return NGX_ERROR;
        }

        entries = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            snapshot->entries[count].saved = entries[i];
            count++;
        }
    }

    return NGX_OK;
}

static void
ngx_http_markdown_304_restore_list(ngx_list_t *list,
    const ngx_http_markdown_304_list_snapshot_t *snapshot)
{
    ngx_table_elt_t  *entries;
    ngx_uint_t        restored;

    if (list == NULL || snapshot == NULL) {
        return;
    }

    list->last = snapshot->original_last;
    if (snapshot->original_last != NULL) {
        snapshot->original_last->nelts = snapshot->original_last_nelts;
        snapshot->original_last->next = snapshot->original_last_next;
    }

    if (snapshot->entry_count == 0 || snapshot->entries == NULL) {
        return;
    }

    restored = 0;
    for (ngx_list_part_t *part = &list->part;
         part != NULL && restored < snapshot->entry_count;
         part = part->next)
    {
        if (part->nelts > snapshot->entry_count - restored
            || (part->nelts != 0 && part->elts == NULL))
        {
            return;
        }

        entries = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
            entries[i] = snapshot->entries[restored].saved;
            restored++;
        }
    }
}

static ngx_int_t
ngx_http_markdown_304_snapshot_prepare(ngx_http_request_t *r,
    ngx_http_markdown_304_snapshot_t *snapshot)
{
    if (r == NULL || snapshot == NULL) {
        return NGX_ERROR;
    }

    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->status = r->headers_out.status;
    snapshot->status_line = r->headers_out.status_line;
    snapshot->headers = r->headers_out.headers;
    snapshot->trailers = r->headers_out.trailers;
    snapshot->content_type = r->headers_out.content_type;
    snapshot->content_type_lowcase = r->headers_out.content_type_lowcase;
    snapshot->content_type_hash = r->headers_out.content_type_hash;
    snapshot->charset = r->headers_out.charset;
    snapshot->content_type_len = r->headers_out.content_type_len;
    snapshot->content_length = r->headers_out.content_length;
    snapshot->content_encoding = r->headers_out.content_encoding;
    snapshot->etag = r->headers_out.etag;
    snapshot->accept_ranges = r->headers_out.accept_ranges;
    snapshot->last_modified = r->headers_out.last_modified;
    snapshot->content_length_n = r->headers_out.content_length_n;
    snapshot->last_modified_time = r->headers_out.last_modified_time;
    snapshot->allow_ranges = r->allow_ranges;

    if (ngx_http_markdown_304_snapshot_list(r->pool,
            &r->headers_out.headers, &snapshot->headers_snapshot)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_304_snapshot_list(r->pool,
            &r->headers_out.trailers, &snapshot->trailers_snapshot)
        != NGX_OK)
    {
        return NGX_ERROR;
    }

    return NGX_OK;
}

static void
ngx_http_markdown_304_snapshot_restore(ngx_http_request_t *r,
    const ngx_http_markdown_304_snapshot_t *snapshot)
{
    if (r == NULL || snapshot == NULL) {
        return;
    }

    r->headers_out.status = snapshot->status;
    r->headers_out.status_line = snapshot->status_line;
    r->headers_out.headers = snapshot->headers;
    r->headers_out.trailers = snapshot->trailers;
    r->headers_out.content_type = snapshot->content_type;
    r->headers_out.content_type_lowcase = snapshot->content_type_lowcase;
    r->headers_out.content_type_hash = snapshot->content_type_hash;
    r->headers_out.charset = snapshot->charset;
    r->headers_out.content_type_len = snapshot->content_type_len;
    r->headers_out.content_length = snapshot->content_length;
    r->headers_out.content_encoding = snapshot->content_encoding;
    r->headers_out.etag = snapshot->etag;
    r->headers_out.accept_ranges = snapshot->accept_ranges;
    r->headers_out.last_modified = snapshot->last_modified;
    r->headers_out.content_length_n = snapshot->content_length_n;
    r->headers_out.last_modified_time = snapshot->last_modified_time;
    r->allow_ranges = snapshot->allow_ranges;

    ngx_http_markdown_304_restore_list(&r->headers_out.headers,
        &snapshot->headers_snapshot);
    ngx_http_markdown_304_restore_list(&r->headers_out.trailers,
        &snapshot->trailers_snapshot);
}

ngx_int_t
ngx_http_markdown_send_304(ngx_http_request_t *r,
                           const struct MarkdownResult *result)
{
    ngx_int_t                           rc;
    ngx_flag_t                          auth_cache_control_required;
    const ngx_http_markdown_conf_t     *conf = NULL;
    ngx_http_markdown_304_snapshot_t    snapshot;

    auth_cache_control_required = 0;

    if (r == NULL) {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_304_snapshot_prepare(r, &snapshot) != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: 304 header snapshot prepare failed");
        return NGX_ERROR;
    }

    /*
     * Conditional responses take a separate path from the normal header
     * plan.  Apply the same authenticated-response cache policy before any
     * 304 headers are sent, so a conditional hit cannot retain a shared
     * cache directive from the source response.
     */
    conf = ngx_http_get_module_loc_conf(r, ngx_http_markdown_filter_module);
    rc = ngx_http_markdown_auth_cache_control_required(
        r, conf, &auth_cache_control_required);
    if (rc == NGX_OK && auth_cache_control_required) {
        rc = ngx_http_markdown_modify_cache_control_for_auth(r);
    }
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: 304 auth Cache-Control update failed");
        ngx_http_markdown_304_snapshot_restore(r, &snapshot);
        return NGX_ERROR;
    }

    r->headers_out.status = NGX_HTTP_NOT_MODIFIED;
    r->headers_out.status_line.len = 0;

    ngx_http_clear_content_length(r);
    r->headers_out.content_length_n = -1;

    /* The 304 describes the transformed Markdown representation: clear
     * Accept-Ranges (byte ranges apply to the HTML representation) and
     * any representation digests of the upstream HTML body. */
    r->allow_ranges = 0;
    r->headers_out.accept_ranges = NULL;
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Accept-Ranges", sizeof("Accept-Ranges") - 1);
    r->headers_out.content_encoding = NULL;
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Content-Encoding", sizeof("Content-Encoding") - 1);
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Content-MD5", sizeof("Content-MD5") - 1);
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Digest", sizeof("Digest") - 1);
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Content-Digest", sizeof("Content-Digest") - 1);
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Repr-Digest", sizeof("Repr-Digest") - 1);
    /* The upstream X-Markdown-Tokens counts HTML tokens; invalidate it so
     * the 304 must not describe the source representation. */
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "X-Markdown-Tokens", sizeof("X-Markdown-Tokens") - 1);
    /* Upstream trailers describe the HTML body; a 304 of the Markdown
     * representation must not declare them. */
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Trailer", sizeof("Trailer") - 1);
    /* Clear the actual trailer entries too: headers_out.trailers is an
     * independent list emitted by HTTP/2/3 and chunked encodings without
     * an HTTP/1.1 Trailer declaration.  Suppress source-HTML trailers. */
    ngx_http_markdown_clear_trailers(r);

    /* Decision G: the 304 describes the Markdown representation; the weak
     * validator must not reference the source HTML mtime.  ETag is the
     * sole validator for converted responses.  Clear the typed pointer
     * too: the header filter synthesizes Last-Modified whenever
     * last_modified_time != -1 AND last_modified == NULL is false, so
     * both fields must be reset to guarantee no stale mtime is emitted. */
    r->headers_out.last_modified_time = (time_t) -1;
    r->headers_out.last_modified = NULL;
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Last-Modified", sizeof("Last-Modified") - 1);

    /* The 304 describes the Markdown representation: apply the
     * Markdown Content-Type through the shared representation helper,
     * which deletes stale header-list entries before pointing the
     * dedicated field at the shared writable array (a surviving list
     * entry would emit a second Content-Type). */
    ngx_http_markdown_set_representation_content_type(r);

    if (result != NULL && result->etag != NULL && result->etag_len > 0) {
        rc = ngx_http_markdown_set_etag(r, result->etag, result->etag_len);
        if (rc != NGX_OK) {
            ngx_http_markdown_304_snapshot_restore(r, &snapshot);
            return NGX_ERROR;
        }

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown: 304 response with ETag: \"%V\"",
                      &r->headers_out.etag->value);
    }

    /* Reuse the shared Vary: Accept helper (same contract as the 200/HEAD
     * paths): it deduplicates against an upstream-provided Vary header
     * instead of pushing a second Vary entry, and appends with overflow
     * checks.  Any failure restores the exact upstream representation. */
    rc = ngx_http_markdown_add_vary_accept(r);
    if (rc != NGX_OK) {
        ngx_http_markdown_304_snapshot_restore(r, &snapshot);
        return NGX_ERROR;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: 304 response with Vary: Accept");

    rc = ngx_http_send_header(r);
    if (rc == NGX_AGAIN) {
        /* Keep the prepared 304 representation and let the header chain
         * resume it on the next filter invocation. */
        return NGX_AGAIN;
    }
    if (rc != NGX_OK && rc != NGX_DONE) {
        return rc;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: 304 Not Modified response sent");

    return NGX_DONE;
}

/*
 * Send 412 Precondition Failed.
 *
 * Mirrors the 304 path's failure contract: snapshot both outgoing lists and
 * every dedicated field touched before any mutation, so a failed header
 * operation restores the exact upstream representation.  The 412 carries no
 * body: Content-Length, representation digests, Accept-Ranges, and trailers
 * of the source HTML representation are cleared.  Vary: Accept is retained
 * because the representation is still negotiated.
 */
ngx_int_t
ngx_http_markdown_send_412(ngx_http_request_t *r)
{
    ngx_int_t                           rc;
    ngx_http_markdown_304_snapshot_t    snapshot;

    if (r == NULL) {
        return NGX_ERROR;
    }

    if (ngx_http_markdown_304_snapshot_prepare(r, &snapshot) != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                      "markdown: 412 header snapshot prepare failed");
        return NGX_ERROR;
    }

    r->headers_out.status = NGX_HTTP_PRECONDITION_FAILED;
    r->headers_out.status_line.len = 0;

    /* The 412 carries no body: mark the request header-only so the
     * HTTP/1.1 header filter does not switch to chunked encoding for a
     * body that is never sent.  The core only sets header_only for 204,
     * 304 and HEAD; the module must declare it explicitly for 412. */
    r->header_only = 1;

    ngx_http_clear_content_length(r);
    r->headers_out.content_length_n = -1;

    /* The 412 describes the transformed Markdown representation: clear
     * byte-range and representation-digest headers of the upstream HTML
     * body, and any upstream trailers. */
    r->allow_ranges = 0;
    r->headers_out.accept_ranges = NULL;
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Accept-Ranges", sizeof("Accept-Ranges") - 1);
    r->headers_out.content_encoding = NULL;
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Content-Encoding", sizeof("Content-Encoding") - 1);
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Content-MD5", sizeof("Content-MD5") - 1);
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Digest", sizeof("Digest") - 1);
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Content-Digest", sizeof("Content-Digest") - 1);
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Repr-Digest", sizeof("Repr-Digest") - 1);
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "X-Markdown-Tokens", sizeof("X-Markdown-Tokens") - 1);
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Trailer", sizeof("Trailer") - 1);
    ngx_http_markdown_clear_trailers(r);

    /* The 412 describes the Markdown representation; the weak validator
     * must not reference the source HTML mtime. */
    r->headers_out.last_modified_time = (time_t) -1;
    r->headers_out.last_modified = NULL;
    ngx_http_markdown_invalidate_response_header(
        r, (const u_char *) "Last-Modified", sizeof("Last-Modified") - 1);

    ngx_http_markdown_set_representation_content_type(r);

    rc = ngx_http_markdown_add_vary_accept(r);
    if (rc != NGX_OK) {
        ngx_http_markdown_304_snapshot_restore(r, &snapshot);
        return NGX_ERROR;
    }

    rc = ngx_http_send_header(r);
    if (rc == NGX_AGAIN) {
        /* Same NGX_AGAIN contract as the 304 path: the prepared
         * representation stays prepared and the header chain resumes
         * it on the next filter invocation. */
        return NGX_AGAIN;
    }
    if (rc != NGX_OK && rc != NGX_DONE) {
        return rc;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown: 412 Precondition Failed response sent");

    return NGX_DONE;
}
