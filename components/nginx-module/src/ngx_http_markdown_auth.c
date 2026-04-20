/*
 * NGINX Markdown Filter Module - Authentication Detection and Cache Control
 *
 * This file implements authentication detection and cache control modifications
 * for authenticated content to ensure secure caching behavior.
 *
 * Requirements: FR-08.1, FR-08.2, FR-08.3, FR-08.5, FR-08.6
 * Task: 17.5 Implement authenticated content cache control
 *
 * Security Rationale:
 * 
 * Authenticated and personalized content must not be cached publicly to prevent
 * exposure of sensitive information. This module detects authenticated requests
 * through:
 * 1. Authorization header (HTTP Basic, Bearer tokens, etc.)
 * 2. Authentication cookies (session IDs, auth tokens)
 *
 * When authenticated content is converted to Markdown, the Cache-Control header
 * is modified to ensure private caching only:
 * - If no Cache-Control: Add "Cache-Control: private"
 * - If Cache-Control allows public caching: Upgrade to "private"
 * - If Cache-Control is "no-store" or "private, no-store": Preserve (never downgrade)
 *
 * This prevents:
 * - Public CDN caching of personalized content
 * - Shared cache exposure of authenticated data
 * - Cross-user information leakage
 */

#include "ngx_http_markdown_filter_module.h"

static u_char ngx_http_markdown_hdr_cache_control[] = "Cache-Control";
static u_char ngx_http_markdown_cc_private[] = "private";
static u_char ngx_http_markdown_cc_public[] = "public";
static u_char ngx_http_markdown_cc_no_store[] = "no-store";
static u_char ngx_http_markdown_cc_suffix_private[] = ", private";

static ngx_int_t ngx_http_markdown_cookie_matches_pattern(
    const ngx_str_t *cookie_name,
    const ngx_str_t *pattern);
static void ngx_http_markdown_skip_cache_control_separators(
    const u_char **cursor, const u_char *end);
static void ngx_http_markdown_trim_cache_control_token(
    const u_char **token_start, const u_char **token_end);
static ngx_int_t ngx_http_markdown_next_cache_control_token(
    const u_char **cursor, const u_char *end,
    const u_char **token_start, const u_char **token_end);
static ngx_flag_t ngx_http_markdown_cache_control_token_is_public(
    const u_char *token_start, const u_char *token_end);
static ngx_flag_t ngx_http_markdown_token_equals_ignore_case(
    const u_char *left, const u_char *right, size_t len);

/*
 * Find a response header by name in the outgoing headers list.
 *
 * Iterates the response's headers_out.headers list and returns the
 * first header whose key matches the given name (case-insensitive).
 *
 * Parameters:
 *   r        - the HTTP request whose outgoing headers are searched
 *   name     - header name to search for (need not be NUL-terminated)
 *   name_len - length of the header name in bytes
 *
 * Returns:
 *   pointer to the matching header entry, or NULL if not found
 */
static ngx_table_elt_t *
ngx_http_markdown_find_response_header(ngx_http_request_t *r,
                                       u_char *name,
                                       size_t name_len)
{
    if (r->headers_out.headers.part.nelts == 0) {
        return NULL;
    }

    for (ngx_list_part_t *part = &r->headers_out.headers.part;
         part != NULL;
         part = part->next)
    {
        ngx_table_elt_t  *headers;

        headers = part->elts;
        for (ngx_uint_t i = 0; i < part->nelts; i++) {
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
 * Insert a new "Cache-Control: private" header when none exists.
 *
 * Allocates a new header entry in the response's outgoing headers list
 * and sets it to "Cache-Control: private".
 *
 * Parameters:
 *   r - the HTTP request to modify
 *
 * Returns:
 *   NGX_OK    - header added successfully
 *   NGX_ERROR - allocation failed
 */
static ngx_int_t
ngx_http_markdown_add_private_cache_control_header(ngx_http_request_t *r)
{
    ngx_table_elt_t  *h;

    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 1;
    h->key.data = ngx_http_markdown_hdr_cache_control;
    h->key.len = sizeof(ngx_http_markdown_hdr_cache_control) - 1;
    h->value.data = ngx_http_markdown_cc_private;
    h->value.len = sizeof(ngx_http_markdown_cc_private) - 1;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: added Cache-Control: private for authenticated content");

    return NGX_OK;
}

/*
 * Append ", private" to an existing Cache-Control header value.
 *
 * Allocates a new buffer in the request pool, copies the existing value
 * plus the ", private" suffix, and updates the header's value pointer
 * and length. The old value is not freed (it remains in the pool).
 *
 * Parameters:
 *   r             - the HTTP request (pool used for allocation)
 *   cache_control - the Cache-Control header entry to modify
 *
 * Returns:
 *   NGX_OK    - directive appended successfully
 *   NGX_ERROR - allocation failed or length overflow
 */
static ngx_int_t
ngx_http_markdown_append_private_directive(ngx_http_request_t *r,
                                           ngx_table_elt_t *cache_control)
{
    u_char  *new_value;
    size_t   new_len;

    if (cache_control->value.len > ((size_t) -1) - (sizeof(ngx_http_markdown_cc_suffix_private) - 1)) {
        return NGX_ERROR;
    }

    new_len = cache_control->value.len + sizeof(ngx_http_markdown_cc_suffix_private) - 1;
    new_value = ngx_pnalloc(r->pool, new_len);
    if (new_value == NULL) {
        return NGX_ERROR;
    }

    ngx_memcpy(new_value, cache_control->value.data, cache_control->value.len);
    ngx_memcpy(new_value + cache_control->value.len,
               ngx_http_markdown_cc_suffix_private,
               sizeof(ngx_http_markdown_cc_suffix_private) - 1);

    cache_control->value.data = new_value;
    cache_control->value.len = new_len;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: added private to Cache-Control: \"%V\"",
                  &cache_control->value);

    return NGX_OK;
}

/*
 * Remove "public" token from Cache-Control and append "private".
 *
 * Rebuilds the Cache-Control value by tokenizing it, skipping any
 * "public" token, copying all other tokens, and appending "private".
 * Allocates a new buffer up to 2x original length + suffix length.
 *
 * Parameters:
 *   r             - the HTTP request (pool used for allocation, logging)
 *   cache_control - the Cache-Control header entry to modify
 *
 * Returns:
 *   NGX_OK    - Cache-Control rebuilt successfully
 *   NGX_ERROR - allocation failed
 */
static ngx_int_t
ngx_http_markdown_strip_public_and_append_private(ngx_http_request_t *r,
                                                  ngx_table_elt_t *cache_control)
{
    u_char         *new_value;
    size_t          new_len;
    const u_char   *p;
    const u_char   *end;
    const u_char   *token_start;
    const u_char   *token_end;
    u_char         *dst;
    ngx_flag_t      wrote_token;

    if (cache_control->value.len > (((size_t) -1) - (sizeof(ngx_http_markdown_cc_suffix_private) - 1)) / 2) {
        return NGX_ERROR;
    }

    new_len = (cache_control->value.len * 2) + (sizeof(ngx_http_markdown_cc_suffix_private) - 1);
    new_value = ngx_pnalloc(r->pool, new_len);
    if (new_value == NULL) {
        return NGX_ERROR;
    }

    p = (const u_char *) cache_control->value.data;
    end = p + cache_control->value.len;
    dst = new_value;
    wrote_token = 0;

    while (ngx_http_markdown_next_cache_control_token(
               &p, end, &token_start, &token_end) == NGX_OK)
    {
        if (ngx_http_markdown_cache_control_token_is_public(
                token_start, token_end))
        {
            continue;
        }

        if (token_end <= token_start) {
            continue;
        }

        if (wrote_token) {
            *dst++ = ',';
            *dst++ = ' ';
        }
        dst = ngx_cpymem(dst, token_start,
                         (size_t) (token_end - token_start));
        wrote_token = 1;
    }

    if (wrote_token) {
        *dst++ = ',';
        *dst++ = ' ';
    }
    dst = ngx_cpymem(dst,
                     ngx_http_markdown_cc_private,
                     sizeof(ngx_http_markdown_cc_private) - 1);

    cache_control->value.data = new_value;
    cache_control->value.len = (size_t) (dst - new_value);

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: upgraded Cache-Control from public to private: \"%V\"",
                  &cache_control->value);

    return NGX_OK;
}

/*
 * Skip Cache-Control separator characters (space, tab, comma).
 *
 * Advances *cursor past any whitespace or comma characters in the
 * Cache-Control header value, stopping at the first non-separator
 * byte or when *cursor reaches end.
 *
 * Parameters:
 *   cursor - pointer to current parse position; advanced in-place
 *   end    - pointer one past the last byte of the header value
 */
static void
ngx_http_markdown_skip_cache_control_separators(const u_char **cursor,
                                                const u_char *end)
{
    while (*cursor < end
           && (**cursor == ' ' || **cursor == '\t' || **cursor == ','))
    {
        (*cursor)++;
    }
}

/*
 * Trim leading and trailing whitespace from a Cache-Control token.
 *
 * Adjusts *token_start forward past leading spaces/tabs and
 * *token_end backward past trailing spaces/tabs, so that
 * [*token_start, *token_end) contains the trimmed token.
 *
 * Parameters:
 *   token_start - pointer to start of token; advanced in-place
 *   token_end   - pointer to end of token; retreated in-place
 */
static void
ngx_http_markdown_trim_cache_control_token(const u_char **token_start,
                                           const u_char **token_end)
{
    while (*token_start < *token_end
           && (**token_start == ' ' || **token_start == '\t'))
    {
        (*token_start)++;
    }

    while (*token_end > *token_start
           && ((*(*token_end - 1) == ' ')
               || (*(*token_end - 1) == '\t')))
    {
        (*token_end)--;
    }
}

/*
 * Parse the next Cache-Control directive token.
 *
 * Skips leading separators, records the token start, advances to the
 * next comma or end-of-input, and trims whitespace from the result.
 *
 * Parameters:
 *   cursor      - pointer to current parse position; advanced in-place
 *   end         - pointer one past the last byte of the header value
 *   token_start - set to the start of the trimmed token on success
 *   token_end   - set to the end of the trimmed token on success
 *
 * Returns:
 *   NGX_OK      - a token was found; bounds stored in token_start/token_end
 *   NGX_DECLINED - no more tokens (cursor reached end)
 */
static ngx_int_t
ngx_http_markdown_next_cache_control_token(const u_char **cursor,
                                           const u_char *end,
                                           const u_char **token_start,
                                           const u_char **token_end)
{
    ngx_http_markdown_skip_cache_control_separators(cursor, end);
    if (*cursor >= end) {
        return NGX_DECLINED;
    }

    *token_start = *cursor;
    while (*cursor < end && **cursor != ',') {
        (*cursor)++;
    }
    *token_end = *cursor;

    ngx_http_markdown_trim_cache_control_token(token_start, token_end);
    return NGX_OK;
}

/*
 * Case-insensitive byte comparison of two memory regions.
 *
 * Compares len bytes from left and right after lowercasing each byte
 * via ngx_tolower. Both regions must contain at least len bytes.
 *
 * Parameters:
 *   left  - start of first memory region
 *   right - start of second memory region
 *   len   - number of bytes to compare
 *
 * Returns:
 *   1 - regions are equal (case-insensitive)
 *   0 - regions differ
 */
static ngx_flag_t
ngx_http_markdown_token_equals_ignore_case(const u_char *left,
                                           const u_char *right,
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
 * Check whether a Cache-Control token equals "public" (case-insensitive).
 *
 * Compares the token bounded by [token_start, token_end) against the
 * literal string "public" using case-insensitive comparison.
 *
 * Parameters:
 *   token_start - start of the token to compare
 *   token_end   - end of the token to compare
 *
 * Returns:
 *   1 - token equals "public"
 *   0 - token does not equal "public"
 */
static ngx_flag_t
ngx_http_markdown_cache_control_token_is_public(const u_char *token_start,
                                                const u_char *token_end)
{
    if ((size_t) (token_end - token_start)
        != sizeof(ngx_http_markdown_cc_public) - 1)
    {
        return 0;
    }

    return ngx_http_markdown_token_equals_ignore_case(
        token_start, ngx_http_markdown_cc_public,
        sizeof(ngx_http_markdown_cc_public) - 1);
}

/*
 * Return configured auth-cookie patterns, falling back to built-in defaults.
 *
 * If the module configuration provides custom auth_cookies patterns, those
 * are returned. Otherwise, the built-in default patterns are used:
 * "session*", "auth*", "PHPSESSID", "wordpress_logged_in_*".
 *
 * Parameters:
 *   conf          - module configuration (may be NULL)
 *   patterns      - set to point to the patterns array
 *   pattern_count - set to the number of patterns
 */
static void
ngx_http_markdown_get_auth_patterns(const ngx_http_markdown_conf_t *conf,
                                    ngx_str_t **patterns,
                                    ngx_uint_t *pattern_count)
{
    static ngx_str_t default_patterns[] = {
        ngx_string("session*"),
        ngx_string("auth*"),
        ngx_string("PHPSESSID"),
        ngx_string("wordpress_logged_in_*"),
        ngx_null_string
    };

    if (conf != NULL && conf->auth_cookies != NULL && conf->auth_cookies->nelts > 0) {
        *patterns = conf->auth_cookies->elts;
        *pattern_count = conf->auth_cookies->nelts;
        return;
    }

    *patterns = default_patterns;
    *pattern_count = (sizeof(default_patterns)
                      / sizeof(default_patterns[0])) - 1;
}

/*
 * Advance cursor past optional whitespace in a Cookie header value.
 *
 * Parameters:
 *   cursor - pointer to current parse position; advanced in-place
 *   end    - pointer one past the last byte of the Cookie header value
 */
static void
ngx_http_markdown_skip_cookie_whitespace(u_char **cursor, const u_char *end)
{
    while (*cursor < end && (**cursor == ' ' || **cursor == '\t')) {
        (*cursor)++;
    }
}

/*
 * Skip past the cookie value and its trailing semicolon delimiter.
 *
 * Advances *cursor past all bytes until a semicolon is found (or end
 * of input), then advances past the semicolon itself if present.
 *
 * Parameters:
 *   cursor - pointer to current parse position; advanced in-place
 *   end    - pointer one past the last byte of the Cookie header value
 */
static void
ngx_http_markdown_skip_cookie_value(u_char **cursor, const u_char *end)
{
    while (*cursor < end && **cursor != ';') {
        (*cursor)++;
    }
    if (*cursor < end && **cursor == ';') {
        (*cursor)++;
    }
}

/*
 * Extract the next cookie name from the cursor position, trimming whitespace.
 *
 * Skips leading whitespace, reads the cookie name (up to '=' or ';'),
 * trims whitespace from the name, then skips the cookie value and
 * delimiter. The cursor is left positioned at the start of the next
 * cookie (or at end).
 *
 * Parameters:
 *   cursor       - pointer to current parse position; advanced in-place
 *   end          - pointer one past the last byte of the Cookie header value
 *   cookie_name  - set to the trimmed cookie name on success
 *
 * Returns:
 *   NGX_OK       - cookie name extracted successfully
 *   NGX_DECLINED - no more cookies (cursor reached end)
 */
static ngx_int_t
ngx_http_markdown_read_cookie_name(u_char **cursor, const u_char *end,
                                   ngx_str_t *cookie_name)
{
    u_char  *name_start;
    const u_char  *name_end;

    ngx_http_markdown_skip_cookie_whitespace(cursor, end);
    if (*cursor >= end) {
        return NGX_DECLINED;
    }

    name_start = *cursor;
    while (*cursor < end && **cursor != '=' && **cursor != ';') {
        (*cursor)++;
    }
    name_end = *cursor;

    while (name_start < name_end && (*name_start == ' ' || *name_start == '\t')) {
        name_start++;
    }
    while (name_end > name_start && (*(name_end - 1) == ' ' || *(name_end - 1) == '\t')) {
        name_end--;
    }

    cookie_name->data = name_start;
    cookie_name->len = (size_t) (name_end - name_start);

    ngx_http_markdown_skip_cookie_value(cursor, end);
    return NGX_OK;
}

/*
 * Test a cookie name against all configured auth patterns, returning the first match.
 *
 * Iterates all auth-cookie patterns and checks if cookie_name matches
 * any of them via ngx_http_markdown_cookie_matches_pattern. Returns
 * on the first match, optionally setting matched_pattern to the
 * matching pattern for diagnostics.
 *
 * Parameters:
 *   cookie_name     - the cookie name to test
 *   patterns        - array of auth-cookie patterns
 *   pattern_count   - number of patterns in the array
 *   matched_pattern - if non-NULL, set to the matching pattern on match
 *
 * Returns:
 *   1 - cookie name matches at least one pattern
 *   0 - cookie name matches no patterns
 */
static ngx_int_t
ngx_http_markdown_cookie_matches_any_pattern(const ngx_str_t *cookie_name,
                                             ngx_str_t *patterns,
                                             ngx_uint_t pattern_count,
                                             ngx_str_t **matched_pattern)
{
    for (ngx_uint_t j = 0; j < pattern_count; j++) {
        if (ngx_http_markdown_cookie_matches_pattern(cookie_name, &patterns[j])) {
            if (matched_pattern != NULL) {
                *matched_pattern = &patterns[j];
            }
            return 1;
        }
    }

    return 0;
}

/*
 * Check if request has Authorization header
 *
 * Detects HTTP authentication via Authorization header (Basic, Bearer, etc.).
 * The presence of this header indicates the request is authenticated.
 *
 * Requirements: FR-08.1
 *
 * @param r  The request structure
 * @return   1 if Authorization header present, 0 otherwise
 */
static ngx_int_t
ngx_http_markdown_has_authorization_header(const ngx_http_request_t *r)
{
    if (r == NULL || r->headers_in.authorization == NULL) {
        return 0;
    }

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: detected Authorization header");

    return 1;
}

/*
 * Check if cookie name matches a pattern
 *
 * Supports three matching modes:
 * 1. Exact match: "session" matches "session" only
 * 2. Prefix match with wildcard: "session*" matches "session", "session_id", etc.
 * 3. Suffix match with wildcard: "*_logged_in" matches "wordpress_logged_in", etc.
 *
 * @param cookie_name  The cookie name to check
 * @param pattern      The pattern to match against
 * @return             1 if matches, 0 otherwise
 */
static ngx_int_t
ngx_http_markdown_cookie_matches_pattern(const ngx_str_t *cookie_name,
    const ngx_str_t *pattern)
{
    size_t         pattern_len;
    size_t         cookie_len;
    const u_char  *pattern_data;
    const u_char  *cookie_data;

    if (cookie_name == NULL || pattern == NULL ||
        cookie_name->len == 0 || pattern->len == 0)
    {
        return 0;
    }

    pattern_data = pattern->data;
    pattern_len = pattern->len;
    cookie_data = cookie_name->data;
    cookie_len = cookie_name->len;

    /* Check for wildcard patterns */
    if (pattern_data[pattern_len - 1] == '*') {
        size_t prefix_len;

        prefix_len = pattern_len - 1;
        if (cookie_len < prefix_len) {
            return 0;
        }

        return ngx_strncmp(cookie_data, pattern_data, prefix_len) == 0;
    }

    if (pattern_data[0] == '*') {
        size_t suffix_len;

        suffix_len = pattern_len - 1;
        if (cookie_len < suffix_len) {
            return 0;
        }

        return ngx_strncmp(cookie_data + (cookie_len - suffix_len),
                           pattern_data + 1, suffix_len) == 0;
    }

    if (cookie_len != pattern_len) {
        return 0;
    }

    return ngx_strncmp(cookie_data, pattern_data, pattern_len) == 0;
}

/*
 * Check if request has authentication cookies
 *
 * Parses the Cookie header and checks if any cookie names match the
 * configured authentication cookie patterns.
 *
 * Default patterns (if none configured):
 * - session*
 * - auth*
 * - PHPSESSID
 * - wordpress_logged_in_*
 *
 * Requirements: FR-08.2, FR-08.5
 *
 * @param r     The request structure
 * @param conf  Module configuration with auth_cookies patterns
 * @return      1 if authentication cookie found, 0 otherwise
 */
static ngx_int_t
ngx_http_markdown_has_auth_cookies(ngx_http_request_t *r,
                                   const ngx_http_markdown_conf_t *conf)
{
    ngx_table_elt_t  *cookie_header;
    ngx_str_t        *patterns;
    ngx_uint_t        pattern_count;
    ngx_str_t        *matched_pattern;
    ngx_str_t         cookie_name;

    if (r == NULL) {
        return 0;
    }

    /* Get Cookie header chain (NGINX may link multiple Cookie headers via ->next) */
    cookie_header = r->headers_in.cookie;
    if (cookie_header == NULL) {
        return 0;
    }

    ngx_http_markdown_get_auth_patterns(conf, &patterns, &pattern_count);

    for (; cookie_header != NULL; cookie_header = cookie_header->next) {
        u_char        *p;
        const u_char  *end;

        p = cookie_header->value.data;
        end = p + cookie_header->value.len;

        while (p < end) {
            if (ngx_http_markdown_read_cookie_name(&p, end, &cookie_name) != NGX_OK) {
                continue;
            }

            if (cookie_name.len == 0) {
                continue;
            }

            if (ngx_http_markdown_cookie_matches_any_pattern(&cookie_name,
                                                             patterns,
                                                             pattern_count,
                                                             &matched_pattern))
            {
                ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                              "markdown filter: detected auth cookie \"%V\" "
                              "matching pattern \"%V\"",
                              &cookie_name, matched_pattern);
                return 1;
            }
        }
    }

    return 0;
}

/*
 * Check if request is authenticated
 *
 * A request is considered authenticated if it has:
 * 1. Authorization header, OR
 * 2. Authentication cookies matching configured patterns
 *
 * Requirements: FR-08.1, FR-08.2
 *
 * @param r     The request structure
 * @param conf  Module configuration
 * @return      1 if authenticated, 0 otherwise
 */
ngx_int_t
ngx_http_markdown_is_authenticated(ngx_http_request_t *r,
                                   const ngx_http_markdown_conf_t *conf)
{
    if (r == NULL) {
        return 0;
    }

    /* Check Authorization header */
    if (ngx_http_markdown_has_authorization_header(r)) {
        return 1;
    }

    /* Check authentication cookies */
    if (ngx_http_markdown_has_auth_cookies(r, conf)) {
        return 1;
    }

    return 0;
}

/*
 * Parse Cache-Control header value
 *
 * Checks if Cache-Control header contains specific directives.
 *
 * @param value      Cache-Control header value
 * @param directive  Directive to search for (e.g., "public", "private", "no-store")
 * @return           1 if directive found, 0 otherwise
 */
static ngx_int_t
ngx_http_markdown_cache_control_has_directive(const ngx_str_t *value,
    const ngx_str_t *directive)
{
    size_t directive_len;
    u_char *p;
    const u_char *end;

    if (value == NULL || value->len == 0 || directive == NULL) {
        return 0;
    }

    directive_len = directive->len;
    p = value->data;
    end = p + value->len;

    /* Search for directive in Cache-Control value */
    while (p < end) {
        /* Skip whitespace and commas */
        while (p < end && (*p == ' ' || *p == '\t' || *p == ',')) {
            p++;
        }

        if (p >= end) {
            break;
        }

        /* Check if this token matches the directive as a complete token */
        if ((size_t)(end - p) >= directive_len
            && ngx_strncasecmp(p, directive->data, directive_len) == 0
            && (p + directive_len == end
                || p[directive_len] == ' '
                || p[directive_len] == '\t'
                || p[directive_len] == ','
                || p[directive_len] == '='))
        {
            return 1;
        }

        /* Skip to next token */
        while (p < end && *p != ',') {
            p++;
        }
        if (p < end && *p == ',') {
            p++;
        }
    }

    return 0;
}

/*
 * Modify Cache-Control header for authenticated content
 *
 * Applies the following rules to ensure secure caching:
 * 1. If no Cache-Control: Add "Cache-Control: private"
 * 2. If Cache-Control allows public caching: Upgrade to "private"
 * 3. If Cache-Control is "no-store" or "private, no-store": Preserve as-is
 *
 * CRITICAL: Never downgrade "no-store" to "private" - this would weaken
 * security by allowing caching of content that should not be cached at all.
 *
 * Requirements: FR-08.3
 *
 * @param r  The request structure
 * @return   NGX_OK on success, NGX_ERROR on failure
 */
ngx_int_t
ngx_http_markdown_modify_cache_control_for_auth(ngx_http_request_t *r)
{
    static ngx_str_t  ngx_http_markdown_no_store =
        { sizeof(ngx_http_markdown_cc_no_store) - 1, ngx_http_markdown_cc_no_store };
    static ngx_str_t  ngx_http_markdown_private =
        { sizeof(ngx_http_markdown_cc_private) - 1, ngx_http_markdown_cc_private };
    static ngx_str_t  ngx_http_markdown_public =
        { sizeof(ngx_http_markdown_cc_public) - 1, ngx_http_markdown_cc_public };
    ngx_table_elt_t  *cache_control;
    ngx_int_t         has_no_store;
    ngx_int_t         has_private;
    ngx_int_t         has_public;

    if (r == NULL) {
        return NGX_ERROR;
    }

    cache_control = ngx_http_markdown_find_response_header(
        r,
        ngx_http_markdown_hdr_cache_control,
        sizeof(ngx_http_markdown_hdr_cache_control) - 1);

    if (cache_control == NULL) {
        return ngx_http_markdown_add_private_cache_control_header(r);
    }

    /*
     * Cache-Control header exists - check directives
     */
    has_no_store = ngx_http_markdown_cache_control_has_directive(
        &cache_control->value, &ngx_http_markdown_no_store);
    has_private = ngx_http_markdown_cache_control_has_directive(
        &cache_control->value, &ngx_http_markdown_private);
    has_public = ngx_http_markdown_cache_control_has_directive(
        &cache_control->value, &ngx_http_markdown_public);

    /*
     * Rule 3: Preserve "no-store" - NEVER downgrade
     * If Cache-Control contains "no-store", leave it unchanged.
     * This is the most restrictive caching directive and must be preserved.
     */
    if (has_no_store) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: preserving Cache-Control with no-store: \"%V\"",
                      &cache_control->value);
        return NGX_OK;
    }

    /*
     * If already has "private", no modification needed
     */
    if (has_private) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: Cache-Control already has private: \"%V\"",
                      &cache_control->value);
        return NGX_OK;
    }

    if (has_public) {
        return ngx_http_markdown_strip_public_and_append_private(r, cache_control);
    }

    return ngx_http_markdown_append_private_directive(r, cache_control);
}
