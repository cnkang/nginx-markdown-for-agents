/*
 * Minimal Test Template
 * 
 * This template provides a minimal test structure that doesn't require
 * full nginx headers. It defines only the necessary types and functions.
 */

#include "test_common.h"

/* Minimal nginx type definitions for testing */
typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef unsigned char   u_char;

#define NGX_OK          0
#define NGX_ERROR      -1
#define NGX_DECLINED   -5

/* Minimal structure definitions */
typedef struct {
    void *data;
} ngx_pool_t;

typedef struct {
    u_char *pos;
    u_char *last;
    u_char *end;
} ngx_buf_t;

typedef struct ngx_chain_s {
    ngx_buf_t *buf;
    struct ngx_chain_s *next;
} ngx_chain_t;

typedef struct {
    void *data;
} ngx_log_t;

typedef struct {
    ngx_log_t *log;
} ngx_connection_t;

typedef struct {
    ngx_connection_t *connection;
    ngx_pool_t *pool;
} ngx_http_request_t;

/* Mock nginx functions */
ngx_int_t ngx_http_discard_request_body(ngx_http_request_t *r) {
    UNUSED(r);
    return NGX_OK;
}

ngx_buf_t *ngx_create_temp_buf(ngx_pool_t *pool, size_t size) {
    UNUSED(pool);
    ngx_buf_t *b = malloc(sizeof(ngx_buf_t) + size);
    if (b) {
        b->pos = (u_char *)(b + 1);
        b->last = b->pos;
        b->end = b->pos + size;
    }
    return b;
}

ngx_int_t ngx_http_send_header(ngx_http_request_t *r) {
    UNUSED(r);
    return NGX_OK;
}

ngx_int_t ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *in) {
    UNUSED(r);
    UNUSED(in);
    return NGX_OK;
}

u_char *ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...) {
    size_t avail = 0;
    if (last > buf) {
        avail = (size_t)(last - buf);
    }
    if (avail == 0) {
        return buf;
    }

    va_list args;
    va_start(args, fmt);
    int len = vsnprintf((char *)buf, avail, fmt, args);
    va_end(args);

    if (len < 0) {
        return buf;
    }
    if ((size_t)len >= avail) {
        return buf + avail - 1;
    }

    return buf + len;
}

void ngx_log_error_core(ngx_uint_t level, ngx_log_t *log, int err,
                        const char *fmt, ...) {
    UNUSED(level);
    UNUSED(log);
    UNUSED(err);
    UNUSED(fmt);
}

/* Test functions */
void test_example() {
    TEST_SUBSECTION("Example test");
    
    // Example test logic
    ngx_int_t result = NGX_OK;
    TEST_ASSERT(result == NGX_OK, "Result should be NGX_OK");
    
    TEST_PASS("Example test passed");
}

int main() {
    printf("\n");
    printf("========================================\n");
    printf("Minimal Test Template\n");
    printf("========================================\n");
    
    test_example();
    
    printf("\n");
    printf("========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n");
    printf("\n");
    
    return 0;
}
