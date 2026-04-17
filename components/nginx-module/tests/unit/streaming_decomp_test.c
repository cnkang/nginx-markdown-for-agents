/*
 * Test: streaming_decomp
 * Description: direct coverage for streaming decompression helpers
 */

#include "../include/test_common.h"
#include <limits.h>
#include <zlib.h>

#define NGX_OK 0
#define NGX_ERROR -1
#define NGX_AGAIN -2
#define NGX_DECLINED -5
#define NGX_DONE -4

#include <ngx_http_markdown_filter_module.h>

#ifndef MARKDOWN_STREAMING_ENABLED

int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_decomp Tests (SKIPPED)\n");
    printf("MARKDOWN_STREAMING_ENABLED not defined\n");
    printf("========================================\n\n");
    return 0;
}

#else /* MARKDOWN_STREAMING_ENABLED */

typedef struct test_pool_cleanup_s test_pool_cleanup_t;
typedef test_pool_cleanup_t ngx_pool_cleanup_t;

struct test_pool_cleanup_s {
    void                 (*handler)(void *data);
    void                  *data;
    ngx_pool_cleanup_t    *next;
};

struct ngx_pool_s {
    ngx_pool_cleanup_t    *cleanups;
};

struct ngx_log_s {
    int                    unused;
};

#define ngx_memcpy memcpy
#define NGX_MAX_SIZE_T_VALUE SIZE_MAX

static ngx_log_t test_log;

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return calloc(1, size);
}

void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    UNUSED(log);
    return malloc(size);
}

void
ngx_free(void *p)
{
    free(p);
}

ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    ngx_pool_cleanup_t  *cln;

    UNUSED(size);

    if (pool == NULL) {
        return NULL;
    }

    cln = calloc(1, sizeof(ngx_pool_cleanup_t));
    if (cln == NULL) {
        return NULL;
    }

    cln->next = pool->cleanups;
    pool->cleanups = cln;
    return cln;
}

#include "../src/ngx_http_markdown_streaming_decomp_impl.h"

typedef struct {
    ngx_pool_t            pool;
} test_pool_t;

static void
test_pool_reset(test_pool_t *tp)
{
    memset(tp, 0, sizeof(*tp));
}

static void
test_pool_run_cleanups(test_pool_t *tp)
{
    ngx_pool_cleanup_t  *cln;
    ngx_pool_cleanup_t  *next;

    cln = tp->pool.cleanups;
    while (cln != NULL) {
        next = cln->next;
        if (cln->handler != NULL) {
            cln->handler(cln->data);
        }
        free(cln);
        cln = next;
    }

    tp->pool.cleanups = NULL;
}

static int
compress_payload(const u_char *in, size_t in_len,
    ngx_http_markdown_compression_type_e type,
    u_char **out, size_t *out_len)
{
    z_stream  s;
    int       rc;
    int       window_bits;
    size_t    cap;
    u_char   *in_copy;

    if (in == NULL || in_len == 0 || out == NULL || out_len == NULL) {
        return NGX_ERROR;
    }

    memset(&s, 0, sizeof(s));

    if (type == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP) {
        window_bits = MAX_WBITS + 16;
    } else if (type == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE) {
        window_bits = -MAX_WBITS;
    } else {
        return NGX_ERROR;
    }

    rc = deflateInit2(&s, Z_DEFAULT_COMPRESSION, Z_DEFLATED,
                      window_bits, 8, Z_DEFAULT_STRATEGY);
    if (rc != Z_OK) {
        return NGX_ERROR;
    }

    in_copy = malloc(in_len);
    if (in_copy == NULL) {
        deflateEnd(&s);
        return NGX_ERROR;
    }
    memcpy(in_copy, in, in_len);

    cap = in_len + (in_len / 8) + 64;
    *out = malloc(cap);
    if (*out == NULL) {
        free(in_copy);
        deflateEnd(&s);
        return NGX_ERROR;
    }

    s.next_in = in_copy;
    s.avail_in = (uInt) in_len;
    s.next_out = *out;
    s.avail_out = (uInt) cap;

    rc = deflate(&s, Z_FINISH);
    if (rc != Z_STREAM_END) {
        free(*out);
        free(in_copy);
        *out = NULL;
        deflateEnd(&s);
        return NGX_ERROR;
    }

    *out_len = s.total_out;
    free(in_copy);
    deflateEnd(&s);
    return NGX_OK;
}

static void
test_size_to_uint_guards(void)
{
    uInt    narrowed;
    size_t  too_large;

    TEST_SUBSECTION("size_to_uint guard branches");

    narrowed = 0;
    too_large = (size_t) UINT_MAX + 1;
    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_size_to_uint(
            too_large, &narrowed) == 1,
        "values above UINT_MAX should overflow");

    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_size_to_uint(123, &narrowed) == 0,
        "values within UINT_MAX should narrow successfully");
    TEST_ASSERT(narrowed == 123, "narrowed value should be preserved");

    TEST_PASS("size_to_uint guard branches covered");
}

static void
test_create_and_cleanup(void)
{
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;

    TEST_SUBSECTION("create and cleanup branches");

    test_pool_reset(&tp);

    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_create(
            NULL, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 1024)
        == NULL,
        "NULL pool should return NULL");

    TEST_ASSERT(
        ngx_http_markdown_streaming_decomp_create(
            &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN, 1024)
        == NULL,
        "unsupported compression type should return NULL");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 1024);
    TEST_ASSERT(decomp != NULL, "gzip decompressor should be created");
    TEST_ASSERT(decomp->initialized == 1,
        "gzip decompressor should initialize");
    TEST_ASSERT(tp.pool.cleanups != NULL,
        "cleanup handler should be registered");

    ngx_http_markdown_streaming_decomp_cleanup(NULL);
    {
        ngx_http_markdown_streaming_decomp_t  empty;

        memset(&empty, 0, sizeof(empty));
        ngx_http_markdown_streaming_decomp_cleanup(&empty);
        TEST_ASSERT(empty.initialized == 0,
            "cleanup should leave uninitialized state untouched");
    }

    test_pool_run_cleanups(&tp);
    TEST_ASSERT(decomp->initialized == 0,
        "registered cleanup should clear initialized state");

    free(decomp);
    TEST_PASS("create and cleanup branches covered");
}

static void
test_roundtrip_and_empty_feed(void)
{
    const char                          *text;
    size_t                               text_len;
    u_char                              *compressed;
    size_t                               compressed_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;
    ngx_http_markdown_compression_type_e  types[2];

    TEST_SUBSECTION("feed round-trip, empty input, and finish no-op");

    text = "Hello from the streaming decompressor test. "
           "This payload should round-trip.";
    text_len = test_cstrnlen(text, 1024);
    types[0] = NGX_HTTP_MARKDOWN_COMPRESSION_GZIP;
    types[1] = NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE;

    for (size_t i = 0; i < ARRAY_SIZE(types); i++) {
        test_pool_reset(&tp);
        compressed = NULL;
        out = NULL;
        out_len = 0;

        rc = compress_payload((const u_char *) text, text_len,
                              types[i], &compressed, &compressed_len);
        TEST_ASSERT(rc == NGX_OK, "compression should succeed");

        decomp = ngx_http_markdown_streaming_decomp_create(
            &tp.pool, types[i], 0);
        TEST_ASSERT(decomp != NULL, "decompressor should be created");

        rc = ngx_http_markdown_streaming_decomp_feed(
            decomp, compressed, compressed_len,
            &out, &out_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_OK, "feed should succeed");
        TEST_ASSERT(out != NULL, "feed should produce output");
        TEST_ASSERT(out_len == text_len,
            "feed output length should match source");
        TEST_ASSERT(MEM_EQ(out, text, out_len),
            "feed output should match source");

        {
            u_char  *empty_out;
            size_t   empty_len;

            empty_out = (u_char *) 0x1;
            empty_len = 1;
            rc = ngx_http_markdown_streaming_decomp_feed(
                decomp, NULL, 0, &empty_out, &empty_len,
                &tp.pool, &test_log);
            TEST_ASSERT(rc == NGX_OK,
                "empty input should be a no-op");
            TEST_ASSERT(empty_out == NULL && empty_len == 0,
                "empty input should not emit output");
        }

        {
            u_char  *finish_out;
            size_t   finish_len;

            finish_out = NULL;
            finish_len = 0;
            rc = ngx_http_markdown_streaming_decomp_finish(
                decomp, &finish_out, &finish_len,
                &tp.pool, &test_log);
            TEST_ASSERT(rc == NGX_OK,
                "finish should succeed after a completed feed");
            TEST_ASSERT(finish_out == NULL && finish_len == 0,
                "finish should be a no-op after stream end");
        }

        free(compressed);
        free(out);
        free(decomp);
    }

    TEST_PASS("feed round-trip and empty-input branches covered");
}

static void
test_budget_and_invalid_type_branches(void)
{
    const char                          *text;
    size_t                               text_len;
    u_char                              *compressed;
    size_t                               compressed_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;

    TEST_SUBSECTION("budget exceed and invalid type branches");

    text = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
           "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
           "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
           "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
    text_len = test_cstrnlen(text, 1024);
    test_pool_reset(&tp);
    compressed = NULL;
    out = NULL;
    out_len = 0;

    rc = compress_payload((const u_char *) text, text_len,
                          NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
                          &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "compression should succeed");

    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 64);
    TEST_ASSERT(decomp != NULL, "bounded decompressor should be created");

    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, compressed_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
        "feed should report budget exceed");
    TEST_ASSERT(out == NULL && out_len == 0,
        "budget exceed should not publish output");

    free(compressed);
    free(decomp);

    {
        ngx_http_markdown_streaming_decomp_t  fake;
        u_char                                *fake_out;
        size_t                                 fake_len;

        memset(&fake, 0, sizeof(fake));
        fake.initialized = 1;
        fake.type = NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN;
        fake_out = NULL;
        fake_len = 0;

        rc = ngx_http_markdown_streaming_decomp_feed(
            &fake, (const u_char *) "x", 1,
            &fake_out, &fake_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_DECLINED,
            "unsupported type should decline in feed");

        rc = ngx_http_markdown_streaming_decomp_finish(
            &fake, &fake_out, &fake_len, &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_ERROR,
            "unsupported type should error in finish");
    }

    TEST_PASS("budget and invalid-type branches covered");
}

static void
test_truncated_finish_errors(void)
{
    const char                          *text;
    size_t                               text_len;
    u_char                              *compressed;
    size_t                               compressed_len;
    test_pool_t                          tp;
    ngx_http_markdown_streaming_decomp_t *decomp;
    u_char                              *out;
    size_t                               out_len;
    ngx_int_t                            rc;
    size_t                               truncated_len;

    TEST_SUBSECTION("finish error on truncated stream");

    text = "Truncated gzip input should fail at finish time.";
    text_len = test_cstrnlen(text, 1024);
    test_pool_reset(&tp);
    compressed = NULL;
    out = NULL;
    out_len = 0;

    rc = compress_payload((const u_char *) text, text_len,
                          NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
                          &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "compression should succeed");
    TEST_ASSERT(compressed_len > 8, "compressed payload should be long enough");

    truncated_len = compressed_len - 8;
    decomp = ngx_http_markdown_streaming_decomp_create(
        &tp.pool, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, 0);
    TEST_ASSERT(decomp != NULL, "decompressor should be created");

    rc = ngx_http_markdown_streaming_decomp_feed(
        decomp, compressed, truncated_len,
        &out, &out_len, &tp.pool, &test_log);
    TEST_ASSERT(rc == NGX_OK, "truncated feed should still succeed");
    TEST_ASSERT(out != NULL, "truncated feed should emit partial output");

    {
        u_char  *finish_out;
        size_t   finish_len;

        finish_out = (u_char *) 0x1;
        finish_len = 1;
        rc = ngx_http_markdown_streaming_decomp_finish(
            decomp, &finish_out, &finish_len,
            &tp.pool, &test_log);
        TEST_ASSERT(rc == NGX_ERROR,
            "finish should fail for an incomplete gzip stream");
    }

    free(compressed);
    free(out);
    free(decomp);
    TEST_PASS("finish error branch covered");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_decomp Tests\n");
    printf("========================================\n");

    test_size_to_uint_guards();
    test_create_and_cleanup();
    test_roundtrip_and_empty_feed();
    test_budget_and_invalid_type_branches();
    test_truncated_finish_errors();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}

#endif /* MARKDOWN_STREAMING_ENABLED */
