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
    ngx_buf_t          *buf;
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
    ngx_pool_t       *pool;
} ngx_http_request_t;

/* Function prototypes */
static void test_example(void);

/* Mock nginx functions */
ngx_int_t
ngx_http_discard_request_body(const ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

ngx_buf_t *
ngx_create_temp_buf(const ngx_pool_t *pool, size_t size)
{
    ngx_buf_t *b;

    UNUSED(pool);
    b = malloc(sizeof(ngx_buf_t) + size);
    if (b) {
        b->pos  = (u_char *)(b + 1);
        b->last = b->pos;
        b->end  = b->pos + size;
    }
    return b;
}

ngx_int_t
ngx_http_send_header(const ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

ngx_int_t
ngx_http_output_filter(const ngx_http_request_t *r, const ngx_chain_t *in)
{
    UNUSED(r);
    UNUSED(in);
    return NGX_OK;
}

u_char *
ngx_slprintf(u_char *buf, const u_char *last, const char *fmt)
{
    size_t avail;
    int    len;

    avail = 0;
    if (last > buf) {
        avail = (size_t)(last - buf);
    }
    if (avail == 0) {
        return buf;
    }
    if (fmt == NULL) {
        return buf;
    }

    /*
     * Minimal template helper: preserve deterministic behavior without
     * evaluating arbitrary format strings in this lightweight mock.
     */
    len = snprintf((char *) buf, avail, "%s", fmt);

    if (len < 0) {
        return buf;
    }
    if ((size_t) len >= avail) {
        return buf + avail - 1;
    }

    return buf + len;
}

void
ngx_log_error_core(ngx_uint_t level, const ngx_log_t *log, int err,
    const char *fmt)
{
    UNUSED(level);
    UNUSED(log);
    UNUSED(err);
    UNUSED(fmt);
}

/* Test functions */
static void
test_example(void)
{
    ngx_int_t  result;

    TEST_SUBSECTION("Example test");

    /* Example test logic */
    result = NGX_OK;
    TEST_ASSERT(result == NGX_OK, "Result should be NGX_OK");

    TEST_PASS("Example test passed");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("Minimal Test Template\n");
    printf("========================================\n");

    test_example();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
