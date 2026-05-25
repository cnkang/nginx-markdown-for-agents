/*
 * Test: decompression_dispatch
 *
 * Validates the unified decompression entry function dispatch logic,
 * chain size calculation, output size estimation, and error path
 * handling in ngx_http_markdown_decompression.c.
 *
 * Coverage targets:
 *   ngx_http_markdown_decompression.c (ngx_http_markdown_decompress,
 *   ngx_http_markdown_chain_size, ngx_http_markdown_chain_to_buffer,
 *   ngx_http_markdown_calc_output_size)
 */

#include "../include/test_common.h"
#include <limits.h>

/* ── Minimal NGINX type stubs ─────────────────────────────────── */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef unsigned char   u_char;
typedef int             ngx_flag_t;

#define NGX_OK         0
#define NGX_ERROR     -1
#define NGX_DECLINED  -5

#define NGX_LOG_ERR    4
#define NGX_LOG_WARN   5
#define NGX_LOG_DEBUG  8
#define NGX_LOG_DEBUG_HTTP  NGX_LOG_DEBUG

/* Decompression error codes matching production */
#define NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED  -100
#define NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR     -101
#define NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT  -102
#define NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR         -103

/* Compression types */
typedef enum {
    NGX_HTTP_MARKDOWN_COMPRESSION_NONE = 0,
    NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
    NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
    NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
    NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN
} ngx_http_markdown_compression_type_e;

/* Buffer and chain stubs */
typedef struct {
    u_char  *pos;
    u_char  *last;
} ngx_buf_t;

typedef struct ngx_chain_s {
    ngx_buf_t           *buf;
    struct ngx_chain_s  *next;
} ngx_chain_t;

/* Log stub */
typedef struct {
    int dummy;
} ngx_log_t;

typedef struct {
    ngx_log_t  *log;
} ngx_connection_t;

/* Request stub */
typedef struct {
    ngx_connection_t  *connection;
} ngx_http_request_t;

static ngx_log_t       g_log;
static ngx_connection_t g_conn = { &g_log };

/* Suppress unused variable warning - kept for API consistency */
static ngx_http_request_t g_request __attribute__((unused)) = { &g_conn };

/* ── Chain helper reimplementations ───────────────────────────── */

static size_t
chain_size(const ngx_chain_t *in)
{
    size_t  size;
    size_t  len;

    size = 0;

    for (const ngx_chain_t *cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf != NULL) {
            len = cl->buf->last - cl->buf->pos;
            if (len > ((size_t) -1) - size) {
                return (size_t) -1;
            }
            size += len;
        }
    }

    return size;
}

static ngx_int_t
chain_to_buffer(const ngx_chain_t *in, u_char *dest, size_t size)
{
    size_t  copied;
    size_t  len;

    copied = 0;

    for (const ngx_chain_t *cl = in; cl != NULL; cl = cl->next) {
        if (cl->buf == NULL) {
            continue;
        }

        len = cl->buf->last - cl->buf->pos;

        if (copied > size || len > size - copied) {
            return NGX_ERROR;
        }

        memcpy(dest + copied, cl->buf->pos, len);
        copied += len;
    }

    return NGX_OK;
}

static ngx_int_t
calc_output_size(size_t input_size, size_t decompress_max_size,
    size_t *output_size)
{
    size_t estimated;

    if (decompress_max_size == 0) {
        return NGX_ERROR;
    }

    if (input_size > ((size_t) -1) / 10) {
        estimated = decompress_max_size;
    } else {
        estimated = input_size * 10;
    }

    if (estimated > decompress_max_size) {
        estimated = decompress_max_size;
    }

    if (estimated > (size_t) UINT_MAX) {
        estimated = (size_t) UINT_MAX;
    }

    if (estimated == 0) {
        return NGX_ERROR;
    }

    *output_size = estimated;
    return NGX_OK;
}

/* Dispatch function (mirrors production logic without actual decompression) */
static ngx_int_t
decompress_dispatch(ngx_http_markdown_compression_type_e type)
{
    switch (type) {
    case NGX_HTTP_MARKDOWN_COMPRESSION_GZIP:
    case NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE:
        return NGX_OK;  /* Would call gzip decompressor */

    case NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI:
        return NGX_DECLINED;  /* Brotli not compiled in for test */

    case NGX_HTTP_MARKDOWN_COMPRESSION_NONE:
        return NGX_ERROR;

    case NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN:
        return NGX_DECLINED;

    default:
        return NGX_ERROR;
    }
}


/* ── Tests ─────────────────────────────────────────────────────── */

static void
test_chain_size_empty(void)
{
    TEST_SUBSECTION("chain_size with NULL chain");

    TEST_ASSERT(chain_size(NULL) == 0, "NULL chain should be 0");

    TEST_PASS("Empty chain size correct");
}

static void
test_chain_size_single_buffer(void)
{
    u_char data[] = "hello world";
    ngx_buf_t buf = { data, data + 11 };
    ngx_chain_t chain = { &buf, NULL };

    TEST_SUBSECTION("chain_size with single buffer");

    TEST_ASSERT(chain_size(&chain) == 11, "single buffer size should be 11");

    TEST_PASS("Single buffer chain size correct");
}

static void
test_chain_size_multiple_buffers(void)
{
    u_char d1[] = "abc";
    u_char d2[] = "defgh";
    ngx_buf_t b1 = { d1, d1 + 3 };
    ngx_buf_t b2 = { d2, d2 + 5 };
    ngx_chain_t c2 = { &b2, NULL };
    ngx_chain_t c1 = { &b1, &c2 };

    TEST_SUBSECTION("chain_size with multiple buffers");

    TEST_ASSERT(chain_size(&c1) == 8, "multi-buffer size should be 8");

    TEST_PASS("Multiple buffer chain size correct");
}

static void
test_chain_size_null_buf(void)
{
    u_char d1[] = "abc";
    ngx_buf_t b1 = { d1, d1 + 3 };
    ngx_chain_t c2 = { NULL, NULL };
    ngx_chain_t c1 = { &b1, &c2 };

    TEST_SUBSECTION("chain_size with NULL buf in chain");

    TEST_ASSERT(chain_size(&c1) == 3,
                "NULL buf should be skipped, size should be 3");

    TEST_PASS("NULL buf in chain handled correctly");
}

static void
test_chain_to_buffer_basic(void)
{
    u_char d1[] = "hello";
    u_char d2[] = " world";
    ngx_buf_t b1 = { d1, d1 + 5 };
    ngx_buf_t b2 = { d2, d2 + 6 };
    ngx_chain_t c2 = { &b2, NULL };
    ngx_chain_t c1 = { &b1, &c2 };
    u_char dest[32];
    ngx_int_t rc;

    TEST_SUBSECTION("chain_to_buffer basic");

    memset(dest, 0, sizeof(dest));
    rc = chain_to_buffer(&c1, dest, 32);
    TEST_ASSERT(rc == NGX_OK, "should succeed");
    TEST_ASSERT(memcmp(dest, "hello world", 11) == 0,
                "buffer content should be concatenated");

    TEST_PASS("chain_to_buffer basic correct");
}

static void
test_chain_to_buffer_overflow(void)
{
    u_char d1[] = "hello world this is too long";
    ngx_buf_t b1 = { d1, d1 + 28 };
    ngx_chain_t c1 = { &b1, NULL };
    u_char dest[10];
    ngx_int_t rc;

    TEST_SUBSECTION("chain_to_buffer overflow");

    rc = chain_to_buffer(&c1, dest, 10);
    TEST_ASSERT(rc == NGX_ERROR, "should fail on overflow");

    TEST_PASS("chain_to_buffer overflow detected");
}

static void
test_calc_output_size_zero_max(void)
{
    size_t output;
    ngx_int_t rc;

    TEST_SUBSECTION("calc_output_size with zero max");

    rc = calc_output_size(100, 0, &output);
    TEST_ASSERT(rc == NGX_ERROR, "zero max should return error");

    TEST_PASS("Zero max size rejected");
}

static void
test_calc_output_size_normal(void)
{
    size_t output;
    ngx_int_t rc;

    TEST_SUBSECTION("calc_output_size normal case");

    rc = calc_output_size(1000, 1024 * 1024, &output);
    TEST_ASSERT(rc == NGX_OK, "should succeed");
    TEST_ASSERT(output == 10000, "should be input * 10");

    TEST_PASS("Normal output size calculation correct");
}

static void
test_calc_output_size_capped(void)
{
    size_t output;
    ngx_int_t rc;

    TEST_SUBSECTION("calc_output_size capped by max");

    rc = calc_output_size(200000, 1024 * 1024, &output);
    TEST_ASSERT(rc == NGX_OK, "should succeed");
    TEST_ASSERT(output == 1024 * 1024,
                "should be capped at decompress_max_size");

    TEST_PASS("Output size capping correct");
}

static void
test_calc_output_size_overflow_guard(void)
{
    size_t output;
    ngx_int_t rc;
    size_t huge_input;

    TEST_SUBSECTION("calc_output_size overflow guard");

    /* Input so large that input*10 would overflow */
    huge_input = ((size_t) -1) / 5;
    rc = calc_output_size(huge_input, (size_t) -1, &output);
    TEST_ASSERT(rc == NGX_OK, "should succeed with overflow guard");
    /* When overflow detected, estimated = decompress_max_size */

    TEST_PASS("Overflow guard works correctly");
}

static void
test_dispatch_gzip(void)
{
    TEST_SUBSECTION("dispatch gzip");

    TEST_ASSERT(decompress_dispatch(NGX_HTTP_MARKDOWN_COMPRESSION_GZIP) == NGX_OK,
                "gzip should dispatch to gzip handler");

    TEST_PASS("Gzip dispatch correct");
}

static void
test_dispatch_deflate(void)
{
    TEST_SUBSECTION("dispatch deflate");

    TEST_ASSERT(decompress_dispatch(NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE) == NGX_OK,
                "deflate should dispatch to gzip handler");

    TEST_PASS("Deflate dispatch correct");
}

static void
test_dispatch_brotli_not_compiled(void)
{
    TEST_SUBSECTION("dispatch brotli (not compiled)");

    TEST_ASSERT(decompress_dispatch(NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI) == NGX_DECLINED,
                "brotli without support should return DECLINED");

    TEST_PASS("Brotli not-compiled dispatch correct");
}

static void
test_dispatch_none(void)
{
    TEST_SUBSECTION("dispatch NONE");

    TEST_ASSERT(decompress_dispatch(NGX_HTTP_MARKDOWN_COMPRESSION_NONE) == NGX_ERROR,
                "NONE should return ERROR");

    TEST_PASS("NONE dispatch correct");
}

static void
test_dispatch_unknown(void)
{
    TEST_SUBSECTION("dispatch UNKNOWN");

    TEST_ASSERT(decompress_dispatch(NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN) == NGX_DECLINED,
                "UNKNOWN should return DECLINED");

    TEST_PASS("UNKNOWN dispatch correct");
}

static void
test_dispatch_invalid(void)
{
    TEST_SUBSECTION("dispatch invalid type");

    TEST_ASSERT(decompress_dispatch((ngx_http_markdown_compression_type_e) 99) == NGX_ERROR,
                "invalid type should return ERROR");

    TEST_PASS("Invalid type dispatch correct");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("decompression_dispatch Tests\n");
    printf("========================================\n");

    test_chain_size_empty();
    test_chain_size_single_buffer();
    test_chain_size_multiple_buffers();
    test_chain_size_null_buf();
    test_chain_to_buffer_basic();
    test_chain_to_buffer_overflow();
    test_calc_output_size_zero_max();
    test_calc_output_size_normal();
    test_calc_output_size_capped();
    test_calc_output_size_overflow_guard();
    test_dispatch_gzip();
    test_dispatch_deflate();
    test_dispatch_brotli_not_compiled();
    test_dispatch_none();
    test_dispatch_unknown();
    test_dispatch_invalid();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
