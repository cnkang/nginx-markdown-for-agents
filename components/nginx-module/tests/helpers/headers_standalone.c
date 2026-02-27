/*
 * Standalone version of header update functions for testing
 * This file contains the same logic as ngx_http_markdown_headers.c
 * but without NGINX header dependencies.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdint.h>
#include <sys/types.h>

/* Mock NGINX types */
typedef unsigned char u_char;
typedef intptr_t ngx_int_t;
typedef uintptr_t ngx_uint_t;
typedef uintptr_t ngx_flag_t;
typedef uintptr_t ngx_msec_t;

#define NGX_OK 0
#define NGX_ERROR -1
#define NGX_LOG_DEBUG_HTTP 0
#define NGX_LOG_ERR 1
#define NGX_INT32_LEN 11

typedef struct {
    u_char *data;
    size_t len;
} ngx_str_t;

typedef struct {
    ngx_str_t key;
    ngx_str_t value;
    ngx_uint_t hash;
} ngx_table_elt_t;

typedef struct ngx_list_part_s ngx_list_part_t;
struct ngx_list_part_s {
    void *elts;
    ngx_uint_t nelts;
    ngx_list_part_t *next;
};

typedef struct {
    ngx_list_part_t part;
    size_t size;
    ngx_uint_t nalloc;
    void *pool;
} ngx_list_t;

typedef struct {
    void *dummy;
} ngx_pool_t;

typedef struct {
    void *log;  /* Mock log pointer */
} ngx_connection_t;

typedef struct {
    ngx_str_t content_type;
    ngx_str_t charset;
    size_t content_type_len;
    off_t content_length_n;
    ngx_table_elt_t *etag;
    ngx_table_elt_t *content_encoding;
    ngx_table_elt_t *accept_ranges;
    ngx_list_t headers;
} ngx_http_headers_out_t;

typedef struct {
    ngx_pool_t *pool;
    ngx_connection_t *connection;
    ngx_http_headers_out_t headers_out;
    void *main;
} ngx_http_request_t;

typedef struct {
    ngx_flag_t enabled;
    size_t max_size;
    ngx_msec_t timeout;
    ngx_uint_t on_error;
    ngx_uint_t flavor;
    ngx_flag_t token_estimate;
    ngx_flag_t front_matter;
    ngx_flag_t on_wildcard;
    ngx_uint_t auth_policy;
    void *auth_cookies;
    ngx_flag_t generate_etag;
    ngx_uint_t conditional_requests;
    ngx_flag_t buffer_chunked;
    void *stream_types;
} ngx_http_markdown_conf_t;

typedef struct {
    uint8_t *markdown;
    uintptr_t markdown_len;
    uint8_t *etag;
    uintptr_t etag_len;
    uint32_t token_estimate;
    uint32_t error_code;
    uint8_t *error_message;
    uintptr_t error_len;
} MarkdownResult;

/* External mock functions (provided by test) */
extern void *ngx_pnalloc(ngx_pool_t *pool, size_t size);
extern ngx_table_elt_t *ngx_list_push(ngx_list_t *list);
extern void ngx_http_clear_content_length(ngx_http_request_t *r);
extern void ngx_log_error(int level, void *log, int err, const char *fmt, ...);
extern void ngx_log_debug0(int level, void *log, int err, const char *fmt);
extern void ngx_log_debug1(int level, void *log, int err, const char *fmt, ...);
extern int ngx_strncasecmp(u_char *s1, u_char *s2, size_t n);
extern u_char *ngx_cpymem(u_char *dst, const void *src, size_t n);
extern u_char *ngx_sprintf(u_char *buf, const char *fmt, ...);

#define ngx_str_set(str, text) \
    (str)->len = sizeof(text) - 1; (str)->data = (u_char *) text

#define ngx_memcpy memcpy

/*
 * Add or update Vary header to include "Accept"
 */
static ngx_int_t
ngx_http_markdown_add_vary_accept(ngx_http_request_t *r)
{
    ngx_table_elt_t  *vary;
    ngx_table_elt_t  *h;
    u_char           *p;
    size_t            len;
    ngx_flag_t        has_accept;

    /* Check if Vary header already exists */
    vary = NULL;
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

            if (header[i].key.len == sizeof("Vary") - 1
                && ngx_strncasecmp(header[i].key.data,
                                  (u_char *) "Vary",
                                  sizeof("Vary") - 1) == 0)
            {
                vary = &header[i];
                break;
            }
        }
    }

    if (vary == NULL) {
        /* No Vary header exists - create new one with "Accept" */
        h = ngx_list_push(&r->headers_out.headers);
        if (h == NULL) {
            return NGX_ERROR;
        }

        h->hash = 1;
        ngx_str_set(&h->key, "Vary");
        ngx_str_set(&h->value, "Accept");

        ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: added Vary: Accept header");

        return NGX_OK;
    }

    /* Vary header exists - check if it already contains "Accept" */
    has_accept = 0;
    p = vary->value.data;
    len = vary->value.len;

    if (len >= 6) {
        size_t i;
        for (i = 0; i <= len - 6; i++) {
            if (ngx_strncasecmp(p + i, (u_char *) "Accept", 6) == 0) {
                if ((i == 0 || p[i-1] == ' ' || p[i-1] == ',') &&
                    (i + 6 == len || p[i+6] == ' ' || p[i+6] == ','))
                {
                    has_accept = 1;
                    break;
                }
            }
        }
    }

    if (has_accept) {
        ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                      "markdown filter: Vary header already contains Accept: \"%V\"",
                      &vary->value);
        return NGX_OK;
    }

    /* Vary header exists but doesn't contain "Accept" - append it */
    len = vary->value.len + sizeof(", Accept") - 1;
    p = ngx_pnalloc(r->pool, len);
    if (p == NULL) {
        return NGX_ERROR;
    }

    p = ngx_cpymem(p, vary->value.data, vary->value.len);
    p = ngx_cpymem(p, ", Accept", sizeof(", Accept") - 1);

    vary->value.data = p - len;
    vary->value.len = len;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: updated Vary header: \"%V\"",
                  &vary->value);

    return NGX_OK;
}

/*
 * Set or update ETag header
 */
static ngx_int_t
ngx_http_markdown_set_etag(ngx_http_request_t *r, const u_char *etag, size_t etag_len)
{
    ngx_table_elt_t  *h;
    ngx_list_part_t  *part;
    ngx_table_elt_t  *header;
    ngx_uint_t        i;

    if (r->headers_out.headers.part.nelts > 0) {
        part = &r->headers_out.headers.part;
        header = part->elts;

        for ( ;; ) {
            for (i = 0; i < part->nelts; i++) {
                if (header[i].key.len == sizeof("ETag") - 1
                    && ngx_strncasecmp(header[i].key.data,
                                      (u_char *) "ETag",
                                      sizeof("ETag") - 1) == 0)
                {
                    header[i].hash = 0;
                }
            }

            if (part->next == NULL) {
                break;
            }

            part = part->next;
            header = part->elts;
        }
    }

    if (etag == NULL || etag_len == 0) {
        r->headers_out.etag = NULL;
        return NGX_OK;
    }

    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 1;
    ngx_str_set(&h->key, "ETag");

    h->value.data = ngx_pnalloc(r->pool, etag_len);
    if (h->value.data == NULL) {
        return NGX_ERROR;
    }

    ngx_memcpy(h->value.data, etag, etag_len);
    h->value.len = etag_len;

    r->headers_out.etag = h;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: set ETag: \"%V\"", &h->value);

    return NGX_OK;
}

/*
 * Add X-Markdown-Tokens header
 */
static ngx_int_t
ngx_http_markdown_add_token_header(ngx_http_request_t *r, uint32_t token_count)
{
    ngx_table_elt_t  *h;
    u_char           *p;

    if (token_count == 0) {
        return NGX_OK;
    }

    h = ngx_list_push(&r->headers_out.headers);
    if (h == NULL) {
        return NGX_ERROR;
    }

    h->hash = 1;
    ngx_str_set(&h->key, "X-Markdown-Tokens");

    h->value.data = ngx_pnalloc(r->pool, NGX_INT32_LEN);
    if (h->value.data == NULL) {
        return NGX_ERROR;
    }

    p = ngx_sprintf(h->value.data, "%ui", token_count);
    h->value.len = p - h->value.data;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: added X-Markdown-Tokens: %ui", token_count);

    return NGX_OK;
}

/*
 * Remove Content-Encoding header
 */
static void
ngx_http_markdown_remove_content_encoding(ngx_http_request_t *r)
{
    r->headers_out.content_encoding = NULL;

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

            if (header[i].key.len == sizeof("Content-Encoding") - 1
                && ngx_strncasecmp(header[i].key.data,
                                  (u_char *) "Content-Encoding",
                                  sizeof("Content-Encoding") - 1) == 0)
            {
                header[i].hash = 0;
                ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                              "markdown filter: removed Content-Encoding header");
                break;
            }
        }
    }
}

/*
 * Remove Accept-Ranges header
 */
static void
ngx_http_markdown_remove_accept_ranges(ngx_http_request_t *r)
{
    r->headers_out.accept_ranges = NULL;

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

            if (header[i].key.len == sizeof("Accept-Ranges") - 1
                && ngx_strncasecmp(header[i].key.data,
                                  (u_char *) "Accept-Ranges",
                                  sizeof("Accept-Ranges") - 1) == 0)
            {
                header[i].hash = 0;
                ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                              "markdown filter: removed Accept-Ranges header");
                break;
            }
        }
    }
}

/*
 * Update response headers for Markdown variant
 */
ngx_int_t
ngx_http_markdown_update_headers(ngx_http_request_t *r,
                                 MarkdownResult *result,
                                 ngx_http_markdown_conf_t *conf)
{
    ngx_int_t  rc;

    if (r == NULL || result == NULL || conf == NULL) {
        return NGX_ERROR;
    }

    /* Set Content-Type */
    r->headers_out.content_type.len = sizeof("text/markdown; charset=utf-8") - 1;
    r->headers_out.content_type.data = (u_char *) "text/markdown; charset=utf-8";
    r->headers_out.content_type_len = r->headers_out.content_type.len;
    r->headers_out.charset.len = 0;
    r->headers_out.charset.data = NULL;

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: set Content-Type: text/markdown; charset=utf-8");

    /* Add Vary: Accept */
    rc = ngx_http_markdown_add_vary_accept(r);
    if (rc != NGX_OK) {
        ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                     "markdown filter: failed to add Vary header");
        return NGX_ERROR;
    }

    /* Update Content-Length */
    ngx_http_clear_content_length(r);
    r->headers_out.content_length_n = result->markdown_len;

    ngx_log_debug1(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: set Content-Length: %uz", result->markdown_len);

    /* Set ETag if generated */
    if (conf->generate_etag && result->etag != NULL && result->etag_len > 0) {
        rc = ngx_http_markdown_set_etag(r, result->etag, result->etag_len);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: failed to set ETag header");
            return NGX_ERROR;
        }
    } else {
        rc = ngx_http_markdown_set_etag(r, NULL, 0);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: failed to clear ETag header");
            return NGX_ERROR;
        }
    }

    /* Add X-Markdown-Tokens if enabled */
    if (conf->token_estimate && result->token_estimate > 0) {
        rc = ngx_http_markdown_add_token_header(r, result->token_estimate);
        if (rc != NGX_OK) {
            ngx_log_error(NGX_LOG_ERR, r->connection->log, 0,
                         "markdown filter: failed to add X-Markdown-Tokens header");
        }
    }

    /* Remove Content-Encoding */
    ngx_http_markdown_remove_content_encoding(r);

    /* Remove Accept-Ranges */
    ngx_http_markdown_remove_accept_ranges(r);

    ngx_log_debug0(NGX_LOG_DEBUG_HTTP, r->connection->log, 0,
                  "markdown filter: headers updated successfully");

    return NGX_OK;
}
