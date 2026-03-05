/*
 * Shared lightweight nginx-compatible declarations for header standalone tests.
 */

#ifndef NGX_HTTP_MARKDOWN_HEADERS_STANDALONE_TYPES_H
#define NGX_HTTP_MARKDOWN_HEADERS_STANDALONE_TYPES_H

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

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
    void *log;
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
    ngx_flag_t allow_ranges;
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

typedef struct MarkdownResult {
    uint8_t *markdown;
    uintptr_t markdown_len;
    uint8_t *etag;
    uintptr_t etag_len;
    uint32_t token_estimate;
    uint32_t error_code;
    uint8_t *error_message;
    uintptr_t error_len;
} MarkdownResult;

extern void *ngx_pnalloc(const ngx_pool_t *pool, size_t size);
extern ngx_table_elt_t *ngx_list_push(ngx_list_t *list);
extern void ngx_http_clear_content_length(ngx_http_request_t *r);
extern void ngx_log_error(int level, void *log, int err, const char *fmt);
extern void ngx_log_debug0(int level, void *log, int err, const char *fmt);
extern void ngx_http_markdown_log_debug1(int level, void *log, int err,
                                         const char *fmt, uintptr_t arg);
extern int ngx_strncasecmp(const u_char *s1, const u_char *s2, size_t n);
extern u_char *ngx_cpymem(u_char *dst, const void *src, size_t n);
extern u_char *ngx_http_markdown_sprintf_token(u_char *buf, ngx_uint_t token_count);

#define NGX_HTTP_MARKDOWN_LOG_DEBUG1(level, log, err, fmt, arg) \
    ngx_http_markdown_log_debug1((level), (log), (err), (fmt), (uintptr_t) (arg))

#define NGX_HTTP_MARKDOWN_SPRINTF_TOKEN(buf, token_count) \
    ngx_http_markdown_sprintf_token((buf), (token_count))

#define ngx_str_set(str, text) \
    (str)->len = sizeof(text) - 1; (str)->data = (u_char *) text

#define ngx_memcpy memcpy
#define NGX_HTTP_MARKDOWN_ENABLE_AUTH_CACHE_CONTROL 0

#endif
