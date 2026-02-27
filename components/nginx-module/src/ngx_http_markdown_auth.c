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
ngx_http_markdown_has_authorization_header(ngx_http_request_t *r)
{
    if (r == NULL || r->headers_in.authorization == NULL) {
        return 0;
    }

    /* Authorization header is present */
    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: detected Authorization header: \"%V\"",
                  &r->headers_in.authorization->value);

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
ngx_http_markdown_cookie_matches_pattern(ngx_str_t *cookie_name, ngx_str_t *pattern)
{
    size_t pattern_len, cookie_len;
    u_char *pattern_data, *cookie_data;

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
        /* Prefix match: "session*" matches "session", "session_id", etc. */
        size_t prefix_len = pattern_len - 1;
        
        if (cookie_len < prefix_len) {
            return 0;
        }
        
        return (ngx_strncmp(cookie_data, pattern_data, prefix_len) == 0);
    }
    else if (pattern_data[0] == '*') {
        /* Suffix match: "*_logged_in" matches "wordpress_logged_in", etc. */
        size_t suffix_len = pattern_len - 1;
        
        if (cookie_len < suffix_len) {
            return 0;
        }
        
        return (ngx_strncmp(cookie_data + (cookie_len - suffix_len),
                           pattern_data + 1, suffix_len) == 0);
    }
    else {
        /* Exact match */
        if (cookie_len != pattern_len) {
            return 0;
        }
        
        return (ngx_strncmp(cookie_data, pattern_data, pattern_len) == 0);
    }
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
                                   ngx_http_markdown_conf_t *conf)
{
    ngx_table_elt_t  *cookie_header;
    u_char           *p, *end, *name_start, *name_end;
    ngx_str_t         cookie_name;

    /* Default patterns if none configured */
    static ngx_str_t default_patterns[] = {
        ngx_string("session*"),
        ngx_string("auth*"),
        ngx_string("PHPSESSID"),
        ngx_string("wordpress_logged_in_*"),
        ngx_null_string
    };

    if (r == NULL) {
        return 0;
    }

    /* Get Cookie header chain (NGINX may link multiple Cookie headers via ->next) */
    cookie_header = r->headers_in.cookie;
    if (cookie_header == NULL) {
        return 0;
    }

    /* Determine which patterns to use */
    ngx_str_t *patterns;
    ngx_uint_t pattern_count;

    if (conf != NULL && conf->auth_cookies != NULL && conf->auth_cookies->nelts > 0) {
        /* Use configured patterns */
        patterns = conf->auth_cookies->elts;
        pattern_count = conf->auth_cookies->nelts;
    } else {
        /* Use default patterns */
        patterns = default_patterns;
        pattern_count = 4;  /* Number of default patterns */
    }

    /* Parse each Cookie header (there can be multiple linked headers) */
    for (; cookie_header != NULL; cookie_header = cookie_header->next) {
        p = cookie_header->value.data;
        end = p + cookie_header->value.len;

        /* Parse cookies in format: "name1=value1; name2=value2; ..." */
        while (p < end) {
            /* Skip whitespace */
            while (p < end && (*p == ' ' || *p == '\t')) {
                p++;
            }

            if (p >= end) {
                break;
            }

            /* Find cookie name */
            name_start = p;
            while (p < end && *p != '=' && *p != ';') {
                p++;
            }
            name_end = p;

            /* Extract cookie name */
            cookie_name.data = name_start;
            cookie_name.len = name_end - name_start;

            /* Check against patterns */
            for (ngx_uint_t j = 0; j < pattern_count; j++) {
                if (ngx_http_markdown_cookie_matches_pattern(&cookie_name, &patterns[j])) {
                    ngx_log_debug2(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                                  "markdown filter: detected auth cookie \"%V\" "
                                  "matching pattern \"%V\"",
                                  &cookie_name, &patterns[j]);
                    return 1;
                }
            }

            /* Skip to next cookie */
            while (p < end && *p != ';') {
                p++;
            }
            if (p < end && *p == ';') {
                p++;
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
                                   ngx_http_markdown_conf_t *conf)
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
ngx_http_markdown_cache_control_has_directive(ngx_str_t *value, const char *directive)
{
    size_t directive_len;
    u_char *p, *end;

    if (value == NULL || value->len == 0 || directive == NULL) {
        return 0;
    }

    directive_len = ngx_strlen(directive);
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

        /* Check if this token matches the directive */
        if ((size_t)(end - p) >= directive_len &&
            ngx_strncasecmp(p, (u_char *)directive, directive_len) == 0)
        {
            /* Verify it's a complete token (not part of another word) */
            if (p + directive_len == end ||
                p[directive_len] == ' ' ||
                p[directive_len] == '\t' ||
                p[directive_len] == ',' ||
                p[directive_len] == '=')
            {
                return 1;
            }
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
    ngx_table_elt_t  *cache_control;
    ngx_table_elt_t  *h;
    ngx_int_t         has_no_store;
    ngx_int_t         has_private;
    ngx_int_t         has_public;

    if (r == NULL) {
        return NGX_ERROR;
    }

    /* Find existing Cache-Control header */
    cache_control = NULL;
    if (r->headers_out.headers.part.nelts > 0) {
        ngx_list_part_t  *part;
        ngx_table_elt_t  *header;
        ngx_uint_t        i;

        part = &r->headers_out.headers.part;
        header = part->elts;

        for (i = 0; /* void */; i++) {
            if (i >= part->nelts) {
                if (part->next == NULL) {
                    break;
                }
                part = part->next;
                header = part->elts;
                i = 0;
            }

            /* Case-insensitive comparison for Cache-Control */
            if (header[i].key.len == sizeof("Cache-Control") - 1
                && ngx_strncasecmp(header[i].key.data,
                                  (u_char *) "Cache-Control",
                                  sizeof("Cache-Control") - 1) == 0)
            {
                cache_control = &header[i];
                break;
            }
        }
    }

    if (cache_control == NULL) {
        /*
         * Rule 1: No Cache-Control header present
         * Add "Cache-Control: private" to prevent public caching
         */
        h = ngx_list_push(&r->headers_out.headers);
        if (h == NULL) {
            return NGX_ERROR;
        }

        h->hash = 1;
        ngx_str_set(&h->key, "Cache-Control");
        ngx_str_set(&h->value, "private");

        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: added Cache-Control: private for authenticated content");

        return NGX_OK;
    }

    /*
     * Cache-Control header exists - check directives
     */
    has_no_store = ngx_http_markdown_cache_control_has_directive(
        &cache_control->value, "no-store");
    has_private = ngx_http_markdown_cache_control_has_directive(
        &cache_control->value, "private");
    has_public = ngx_http_markdown_cache_control_has_directive(
        &cache_control->value, "public");

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

    /*
     * Rule 2: Upgrade public caching to private
     * If Cache-Control allows public caching (has "public" or no privacy directive),
     * upgrade to "private" to prevent public cache storage.
     */
    if (has_public) {
        /*
         * Rewrite directives by removing "public" tokens and appending
         * "private". We pre-allocate using an upper bound to avoid
         * under-allocation when replacing 6-byte "public" with 7-byte "private".
         */
        u_char *new_value;
        size_t  new_len;
        u_char *p, *end, *token_start, *token_end, *dst;
        ngx_flag_t wrote_token;

        /* Worst-case output can add spaces after commas, so reserve a safe upper bound. */
        if (cache_control->value.len > (((size_t) -1) - (sizeof(", private") - 1)) / 2) {
            return NGX_ERROR;
        }
        new_len = (cache_control->value.len * 2) + (sizeof(", private") - 1);
        new_value = ngx_pnalloc(r->pool, new_len);
        if (new_value == NULL) {
            return NGX_ERROR;
        }

        p = cache_control->value.data;
        end = cache_control->value.data + cache_control->value.len;
        dst = new_value;
        wrote_token = 0;

        while (p < end) {
            while (p < end && (*p == ' ' || *p == '\t' || *p == ',')) {
                p++;
            }
            if (p >= end) {
                break;
            }

            token_start = p;
            while (p < end && *p != ',') {
                p++;
            }
            token_end = p;

            while (token_start < token_end
                   && (*token_start == ' ' || *token_start == '\t'))
            {
                token_start++;
            }
            while (token_end > token_start
                   && (*(token_end - 1) == ' ' || *(token_end - 1) == '\t'))
            {
                token_end--;
            }

            if ((size_t) (token_end - token_start) == sizeof("public") - 1
                && ngx_strncasecmp(token_start, (u_char *) "public",
                                   sizeof("public") - 1) == 0)
            {
                continue;
            }

            if (token_end > token_start) {
                if (wrote_token) {
                    *dst++ = ',';
                    *dst++ = ' ';
                }
                dst = ngx_cpymem(dst, token_start, token_end - token_start);
                wrote_token = 1;
            }
        }

        if (wrote_token) {
            *dst++ = ',';
            *dst++ = ' ';
        }
        dst = ngx_cpymem(dst, "private", sizeof("private") - 1);

        cache_control->value.data = new_value;
        cache_control->value.len = dst - new_value;

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: upgraded Cache-Control from public to private: \"%V\"",
                      &cache_control->value);
    } else {
        /* No "public" or "private" - add "private" to existing directives */
        u_char *new_value;
        size_t  new_len;

        new_len = cache_control->value.len + sizeof(", private") - 1;
        new_value = ngx_pnalloc(r->pool, new_len);
        if (new_value == NULL) {
            return NGX_ERROR;
        }

        /* Copy existing value and append ", private" */
        ngx_memcpy(new_value, cache_control->value.data, cache_control->value.len);
        ngx_memcpy(new_value + cache_control->value.len, ", private", sizeof(", private") - 1);

        cache_control->value.data = new_value;
        cache_control->value.len = new_len;

        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: added private to Cache-Control: \"%V\"",
                      &cache_control->value);
    }

    return NGX_OK;
}
