/*
 * NGINX Markdown Filter Module - Accept Header Parser
 *
 * This file implements Accept header parsing with RFC 9110 tie-break rules.
 * The parser handles media types with q-values and wildcards, applying
 * proper precedence rules when multiple media types match.
 *
 * Tie-Break Rules (RFC 9110):
 * 1. Exact match (text/markdown) > subtype wildcard (text slash star) > all wildcard (star slash star)
 * 2. Higher q-value wins
 * 3. Equal q-value: more specific media type wins
 * 4. Equal specificity: preserve header order
 *
 * Examples:
 *   "text/markdown, text/html" -> Convert (both q=1.0, exact match for markdown)
 *   "text/html;q=0.9, text/markdown;q=0.8" -> No conversion (html has higher q)
 *   "text slash star;q=0.9, text/markdown;q=0.8" -> No conversion (text slash star has higher q)
 *   "text/markdown;q=0.9, text/html;q=0.9" -> Convert (equal q, markdown first)
 *   "star slash star, text/markdown" -> Convert (both q=1.0, markdown more specific)
 */

#include "ngx_http_markdown_filter_module.h"

/* Media type specificity levels for tie-breaking */
typedef enum {
    NGX_HTTP_MARKDOWN_SPECIFICITY_EXACT = 3,      /* text/markdown */
    NGX_HTTP_MARKDOWN_SPECIFICITY_SUBTYPE = 2,    /* text slash star */
    NGX_HTTP_MARKDOWN_SPECIFICITY_ALL = 1         /* star slash star */
} ngx_http_markdown_specificity_t;

/* Parsed Accept header entry */
typedef struct {
    ngx_str_t                          type;        /* e.g., "text" */
    ngx_str_t                          subtype;     /* e.g., "markdown" */
    float                              q_value;     /* Quality value (0.0-1.0) */
    ngx_http_markdown_specificity_t    specificity; /* Specificity level */
    ngx_uint_t                         order;       /* Original order in header */
} ngx_http_markdown_accept_entry_t;

/* Forward declarations */
static ngx_int_t ngx_http_markdown_parse_accept_entry(ngx_str_t *entry_str,
    ngx_http_markdown_accept_entry_t *entry, ngx_uint_t order);
static float ngx_http_markdown_parse_q_value(ngx_str_t *params);
static ngx_http_markdown_specificity_t ngx_http_markdown_get_specificity(
    ngx_str_t *type, ngx_str_t *subtype);
static int ngx_http_markdown_compare_entries(const void *a, const void *b);
static ngx_int_t ngx_http_markdown_matches_markdown(
    ngx_http_markdown_accept_entry_t *entry, ngx_flag_t on_wildcard);

/*
 * Parse Accept header into structured entries
 *
 * Parses the Accept header value into an array of media type entries,
 * each with type, subtype, q-value, and specificity information.
 *
 * @param r        The request structure
 * @param accept   The Accept header value to parse
 * @param entries  Output array of parsed entries (allocated by caller)
 * @return         NGX_OK on success, NGX_ERROR on parse error
 */
ngx_int_t
ngx_http_markdown_parse_accept(ngx_http_request_t *r, ngx_str_t *accept,
    ngx_array_t *entries)
{
    u_char                              *p, *start, *end;
    ngx_str_t                            entry_str;
    ngx_http_markdown_accept_entry_t    *entry;
    ngx_uint_t                           order;
    
    if (accept == NULL || accept->len == 0) {
        return NGX_ERROR;
    }
    
    p = accept->data;
    end = accept->data + accept->len;
    order = 0;
    
    /*
     * Parse comma-separated media types
     * Example: "text/markdown, text/html;q=0.9, star/slash-star;q=0.8"
     */
    while (p < end) {
        /* Skip leading whitespace */
        while (p < end && (*p == ' ' || *p == '\t')) {
            p++;
        }
        
        if (p >= end) {
            break;
        }
        
        /* Find end of this entry (comma or end of string) */
        start = p;
        while (p < end && *p != ',') {
            p++;
        }
        
        /* Parse this entry */
        entry_str.data = start;
        entry_str.len = p - start;
        
        /* Trim trailing whitespace */
        while (entry_str.len > 0 && 
               (entry_str.data[entry_str.len - 1] == ' ' ||
                entry_str.data[entry_str.len - 1] == '\t')) {
            entry_str.len--;
        }
        
        if (entry_str.len > 0) {
            /* Allocate entry in array */
            entry = ngx_array_push(entries);
            if (entry == NULL) {
                ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                             "markdown: failed to allocate Accept entry");
                return NGX_ERROR;
            }
            
            /* Parse the entry */
            if (ngx_http_markdown_parse_accept_entry(&entry_str, entry, order) != NGX_OK) {
                ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                             "markdown: failed to parse Accept entry: \"%V\"",
                             &entry_str);
                /* Remove the failed entry from array */
                entries->nelts--;
                /* Continue parsing other entries */
            } else {
                order++;
            }
        }
        
        /* Skip comma */
        if (p < end && *p == ',') {
            p++;
        }
    }
    
    return NGX_OK;
}

/*
 * Parse a single Accept header entry
 *
 * Parses a media type entry like "text/markdown;q=0.9" into its components.
 *
 * @param entry_str  The entry string to parse
 * @param entry      Output structure to fill
 * @param order      Original order in header
 * @return           NGX_OK on success, NGX_ERROR on parse error
 */
static ngx_int_t
ngx_http_markdown_parse_accept_entry(ngx_str_t *entry_str,
    ngx_http_markdown_accept_entry_t *entry, ngx_uint_t order)
{
    u_char      *p, *slash, *semicolon;
    ngx_str_t    params;
    
    p = entry_str->data;
    
    /* Find slash separator between type and subtype */
    slash = ngx_strlchr(p, p + entry_str->len, '/');
    if (slash == NULL) {
        return NGX_ERROR;
    }
    
    /* Find semicolon (start of parameters) */
    semicolon = ngx_strlchr(p, p + entry_str->len, ';');
    
    /* Extract type */
    entry->type.data = p;
    entry->type.len = slash - p;
    
    /* Trim whitespace from type */
    while (entry->type.len > 0 && 
           (entry->type.data[entry->type.len - 1] == ' ' ||
            entry->type.data[entry->type.len - 1] == '\t')) {
        entry->type.len--;
    }
    
    /* Extract subtype */
    if (semicolon != NULL) {
        entry->subtype.data = slash + 1;
        entry->subtype.len = semicolon - (slash + 1);
    } else {
        entry->subtype.data = slash + 1;
        entry->subtype.len = (p + entry_str->len) - (slash + 1);
    }
    
    /* Trim whitespace from subtype */
    while (entry->subtype.len > 0 && 
           (entry->subtype.data[0] == ' ' || entry->subtype.data[0] == '\t')) {
        entry->subtype.data++;
        entry->subtype.len--;
    }
    while (entry->subtype.len > 0 && 
           (entry->subtype.data[entry->subtype.len - 1] == ' ' ||
            entry->subtype.data[entry->subtype.len - 1] == '\t')) {
        entry->subtype.len--;
    }
    
    /* Validate type and subtype are not empty */
    if (entry->type.len == 0 || entry->subtype.len == 0) {
        return NGX_ERROR;
    }
    
    /* Parse parameters (q-value) */
    if (semicolon != NULL) {
        params.data = semicolon + 1;
        params.len = (p + entry_str->len) - (semicolon + 1);
        entry->q_value = ngx_http_markdown_parse_q_value(&params);
    } else {
        entry->q_value = 1.0f; /* Default q-value */
    }
    
    /* Determine specificity */
    entry->specificity = ngx_http_markdown_get_specificity(&entry->type, &entry->subtype);
    
    /* Store original order */
    entry->order = order;
    
    return NGX_OK;
}

/*
 * Parse q-value from parameters
 *
 * Extracts the q-value from parameter string like "q=0.9".
 * Returns 1.0 if no q-value is found or if parsing fails.
 *
 * @param params  The parameter string
 * @return        The q-value (0.0-1.0)
 */
static float
ngx_http_markdown_parse_q_value(ngx_str_t *params)
{
    u_char  *p, *end, *q_start;
    float    q_value;
    ngx_int_t  n;
    
    p = params->data;
    end = params->data + params->len;
    
    /* Find "q=" parameter */
    while (p < end) {
        /* Skip whitespace */
        while (p < end && (*p == ' ' || *p == '\t')) {
            p++;
        }
        
        if (p >= end) {
            break;
        }
        
        /* Check for "q=" */
        if (p + 2 <= end && *p == 'q' && *(p + 1) == '=') {
            p += 2;
            q_start = p;
            
            /* Find end of q-value (semicolon, comma, or end) */
            while (p < end && *p != ';' && *p != ',') {
                p++;
            }
            
            /* Parse q-value */
            n = ngx_atofp(q_start, p - q_start, 3);
            if (n == NGX_ERROR) {
                return 1.0f; /* Invalid q-value, use default */
            }
            
            q_value = (float)n / 1000.0f;
            
            /* Clamp to valid range [0.0, 1.0] */
            if (q_value < 0.0f) {
                q_value = 0.0f;
            } else if (q_value > 1.0f) {
                q_value = 1.0f;
            }
            
            return q_value;
        }
        
        /* Skip to next parameter */
        while (p < end && *p != ';') {
            p++;
        }
        if (p < end && *p == ';') {
            p++;
        }
    }
    
    return 1.0f; /* No q-value found, use default */
}

/*
 * Determine specificity level of a media type
 *
 * Returns the specificity level based on wildcards:
 * - text/markdown: EXACT (3)
 * - text slash star: SUBTYPE (2)
 * - star/slash-star: ALL (1)
 *
 * @param type     The type part (e.g., "text")
 * @param subtype  The subtype part (e.g., "markdown")
 * @return         The specificity level
 */
static ngx_http_markdown_specificity_t
ngx_http_markdown_get_specificity(ngx_str_t *type, ngx_str_t *subtype)
{
    /* Check for star/slash-star */
    if (type->len == 1 && type->data[0] == '*' &&
        subtype->len == 1 && subtype->data[0] == '*') {
        return NGX_HTTP_MARKDOWN_SPECIFICITY_ALL;
    }
    
    /* Check for type slash star */
    if (subtype->len == 1 && subtype->data[0] == '*') {
        return NGX_HTTP_MARKDOWN_SPECIFICITY_SUBTYPE;
    }
    
    /* Exact type/subtype */
    return NGX_HTTP_MARKDOWN_SPECIFICITY_EXACT;
}

/*
 * Sort Accept entries by precedence
 *
 * Sorts entries according to RFC 9110 tie-break rules:
 * 1. Higher q-value wins
 * 2. Equal q-value: more specific media type wins
 * 3. Equal specificity: preserve header order
 *
 * @param entries  Array of entries to sort
 */
void
ngx_http_markdown_sort_accept_entries(ngx_array_t *entries)
{
    if (entries == NULL || entries->nelts <= 1) {
        return;
    }
    
    ngx_qsort(entries->elts, entries->nelts, 
              sizeof(ngx_http_markdown_accept_entry_t),
              ngx_http_markdown_compare_entries);
}

/*
 * Compare two Accept entries for sorting
 *
 * Comparison function for qsort that implements RFC 9110 tie-break rules.
 * Returns negative if a should come before b, positive if b should come before a.
 *
 * @param a  First entry
 * @param b  Second entry
 * @return   Comparison result
 */
static int
ngx_http_markdown_compare_entries(const void *a, const void *b)
{
    const ngx_http_markdown_accept_entry_t *entry_a = a;
    const ngx_http_markdown_accept_entry_t *entry_b = b;
    
    /* Rule 1: Higher q-value wins (sort descending) */
    if (entry_a->q_value > entry_b->q_value) {
        return -1;
    } else if (entry_a->q_value < entry_b->q_value) {
        return 1;
    }
    
    /* Rule 2: Equal q-value, more specific wins (sort descending) */
    if (entry_a->specificity > entry_b->specificity) {
        return -1;
    } else if (entry_a->specificity < entry_b->specificity) {
        return 1;
    }
    
    /* Rule 3: Equal specificity, preserve header order (sort ascending) */
    if (entry_a->order < entry_b->order) {
        return -1;
    } else if (entry_a->order > entry_b->order) {
        return 1;
    }
    
    return 0;
}

/*
 * Check if an entry matches text/markdown
 *
 * Determines if a media type entry matches text/markdown, considering
 * wildcards based on configuration.
 *
 * @param entry        The entry to check
 * @param on_wildcard  Whether to match wildcards (star/slash-star or text slash star)
 * @return             1 if matches, 0 if not
 */
static ngx_int_t
ngx_http_markdown_matches_markdown(ngx_http_markdown_accept_entry_t *entry,
    ngx_flag_t on_wildcard)
{
    /* Check for explicit text/markdown */
    if (entry->type.len == 4 && ngx_strncasecmp(entry->type.data, (u_char *)"text", 4) == 0 &&
        entry->subtype.len == 8 && ngx_strncasecmp(entry->subtype.data, (u_char *)"markdown", 8) == 0) {
        return 1;
    }
    
    /* Check wildcards if configured */
    if (on_wildcard) {
        /* Check for text slash star */
        if (entry->type.len == 4 && ngx_strncasecmp(entry->type.data, (u_char *)"text", 4) == 0 &&
            entry->subtype.len == 1 && entry->subtype.data[0] == '*') {
            return 1;
        }
        
        /* Check for star/slash-star */
        if (entry->type.len == 1 && entry->type.data[0] == '*' &&
            entry->subtype.len == 1 && entry->subtype.data[0] == '*') {
            return 1;
        }
    }
    
    return 0;
}

/*
 * Determine if request should be converted to Markdown
 *
 * Applies complete Accept header evaluation with tie-break rules:
 * 1. Parse Accept header into entries
 * 2. Sort entries by precedence (q-value, specificity, order)
 * 3. Check if highest-precedence entry matches text/markdown
 * 4. Verify q-value > 0 (not explicitly rejected)
 *
 * @param r     The request structure
 * @param conf  Module configuration
 * @return      1 if should convert, 0 if not
 */
ngx_int_t
ngx_http_markdown_should_convert(ngx_http_request_t *r,
    ngx_http_markdown_conf_t *conf)
{
    ngx_str_t                            *accept;
    ngx_array_t                          *entries;
    ngx_http_markdown_accept_entry_t     *entry;
    ngx_uint_t                            i;
    
    /* Check if conversion is enabled */
    if (!conf->enabled) {
        return 0;
    }
    
    /* Get Accept header */
    accept = &r->headers_in.accept->value;
    if (accept == NULL || accept->len == 0) {
        return 0;
    }
    
    /* Create array for parsed entries */
    entries = ngx_array_create(r->pool, 8, sizeof(ngx_http_markdown_accept_entry_t));
    if (entries == NULL) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown: failed to allocate Accept entries array");
        return 0;
    }
    
    /* Parse Accept header */
    if (ngx_http_markdown_parse_accept(r, accept, entries) != NGX_OK) {
        ngx_log_error(NGX_LOG_WARN, r->connection->log, 0,
                     "markdown: failed to parse Accept header: \"%V\"", accept);
        return 0;
    }
    
    if (entries->nelts == 0) {
        return 0;
    }
    
    /* Sort entries by precedence */
    ngx_http_markdown_sort_accept_entries(entries);
    
    /*
     * Honor explicit rejection before wildcard matching.
     * A client can explicitly deny text/markdown with q=0 even if it also
     * sends a wildcard (for example "star/slash-star;q=1, text/markdown;q=0").
     */
    entry = entries->elts;
    for (i = 0; i < entries->nelts; i++) {
        if (entry[i].q_value == 0.0f &&
            entry[i].type.len == 4 &&
            ngx_strncasecmp(entry[i].type.data, (u_char *)"text", 4) == 0 &&
            entry[i].subtype.len == 8 &&
            ngx_strncasecmp(entry[i].subtype.data, (u_char *)"markdown", 8) == 0)
        {
            ngx_log_error(NGX_LOG_DEBUG, r->connection->log, 0,
                         "markdown: text/markdown explicitly rejected (q=0)");
            return 0;
        }
    }

    /*
     * After sorting, the first entry is the highest-precedence media range.
     * Convert only if that top entry matches markdown (or allowed wildcard)
     * and is not explicitly rejected.
     */
    if (ngx_http_markdown_matches_markdown(&entry[0], conf->on_wildcard)
        && entry[0].q_value > 0.0f)
    {
        ngx_log_error(NGX_LOG_DEBUG, r->connection->log, 0,
                     "markdown: top Accept entry matches text/markdown "
                     "(q=%f, specificity=%d, order=%ui)",
                     entry[0].q_value, entry[0].specificity, entry[0].order);
        return 1;
    }

    ngx_log_error(NGX_LOG_DEBUG, r->connection->log, 0,
                 "markdown: highest-precedence Accept entry does not permit markdown");
    return 0;
}
