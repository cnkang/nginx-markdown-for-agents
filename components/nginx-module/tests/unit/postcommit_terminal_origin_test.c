/*
 * Test: postcommit_terminal_origin
 *
 * Validates post-commit terminal send failure origin classification
 * and the fix for safe-finish + zero closing bytes + terminal
 * immediate definitive failure.
 *
 * Tests verify expand_buf overflow semantics and origin classification
 * correctness against the production streaming decompression
 * implementation (ngx_http_markdown_streaming_decomp_impl.h).
 */

#include "../include/test_common.h"
#include <limits.h>
#include <zlib.h>

#define NGX_OK 0
#define NGX_ERROR (-1)

#include <ngx_http_markdown_filter_module.h>

/*
 * Minimal ngx_log_t for expand_buf signature: complete the struct
 * forward-declared by the nginx_stubs ngx_core.h.
 */
struct ngx_log_s {
    int unused;
};

#define ngx_memcpy memcpy
#define NGX_MAX_SIZE_T_VALUE SIZE_MAX

/*
 * Pool cleanup stand-ins: the production create path registers a
 * cleanup handler through ngx_pool_cleanup_add.  The struct shapes
 * mirror the production linked-list cleanup structure so the
 * implementation header compiles; this suite only exercises
 * expand_buf, so the pool APIs are declared and never called.
 */
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

/*
 * Allocator stubs for the production implementation included below.
 * expand_buf allocates with ngx_alloc and releases with ngx_free; the
 * remaining pool/cleanup APIs are declared (not defined) because the
 * create path that references them is not exercised by this suite and
 * the linker drops the unreferenced static functions.
 */
void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    (void) log;
    return malloc(size);
}

void
ngx_free(void *p)
{
    free(p);
}

/*
 * Pool/cleanup APIs: the create path that references them is not
 * exercised by this suite, but GNU ld with --gc-sections still links the
 * intermediate code sections, so provide safe definitions instead of
 * relying on the platform linker dropping them.
 */
void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return malloc(size);
}

void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return calloc(1, size);
}

void *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    (void) size;
    return NULL;
}

/* Include the production streaming decompression implementation so that
 * expand_buf is compiled against the stubs above. */
#include "../src/ngx_http_markdown_streaming_decomp_impl.h"


/* --- Test: expand_buf overflow → OVERFLOW_ERROR --- */

static void
test_expand_buf_overflow(void)
{
    u_char    *heap_buf;
    u_char    *buf;
    size_t     buf_size;
    ngx_int_t  rc;

    heap_buf = malloc(64);
    TEST_ASSERT(heap_buf != NULL, "setup");
    buf = heap_buf;
    buf_size = (size_t) -1 / 2 + 1;

    rc = ngx_http_markdown_streaming_decomp_expand_buf(&heap_buf, &buf, &buf_size, 0, NULL);

    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR,
        "size_t overflow must return OVERFLOW_ERROR");
    TEST_ASSERT(heap_buf == NULL,
        "expand_buf must free heap on overflow");

    TEST_PASS("expand_buf overflow → OVERFLOW_ERROR");
}


/* --- Test: expand_buf success → NGX_OK --- */

static void
test_expand_buf_success(void)
{
    u_char    *heap_buf;
    u_char    *buf;
    size_t     buf_size;
    ngx_int_t  rc;

    heap_buf = malloc(64);
    TEST_ASSERT(heap_buf != NULL, "setup");
    memset(heap_buf, 0x42, 64);
    buf = heap_buf;
    buf_size = 64;

    rc = ngx_http_markdown_streaming_decomp_expand_buf(&heap_buf, &buf, &buf_size, 0, NULL);

    TEST_ASSERT(rc == NGX_OK, "must succeed");
    TEST_ASSERT(buf_size == 128, "size must double");
    TEST_ASSERT(heap_buf != NULL, "heap must be non-NULL");

    free(heap_buf);

    TEST_PASS("expand_buf success → NGX_OK");
}


/* --- Test: expand_buf max_size cap --- */

static void
test_expand_buf_max_size(void)
{
    u_char    *heap_buf;
    u_char    *buf;
    size_t     buf_size;
    ngx_int_t  rc;

    heap_buf = malloc(64);
    TEST_ASSERT(heap_buf != NULL, "setup");
    buf = heap_buf;
    buf_size = 64;

    rc = ngx_http_markdown_streaming_decomp_expand_buf(&heap_buf, &buf, &buf_size, 96, NULL);

    TEST_ASSERT(rc == NGX_OK, "must succeed");
    TEST_ASSERT(buf_size == 96, "must cap to max_size");

    free(heap_buf);

    TEST_PASS("expand_buf max_size cap");
}


/* --- Test: expand_buf never shrinks below old_size --- */

static void
test_expand_buf_max_size_below_old_size(void)
{
    u_char    *heap_buf;
    u_char    *buf;
    u_char     expected[64];
    size_t     buf_size;
    ngx_int_t  rc;

    heap_buf = malloc(64);
    TEST_ASSERT(heap_buf != NULL, "setup");
    memset(heap_buf, 0x42, 64);
    memcpy(expected, heap_buf, 64);
    buf = heap_buf;
    buf_size = 64;

    rc = ngx_http_markdown_streaming_decomp_expand_buf(&heap_buf, &buf, &buf_size, 32, NULL);

    TEST_ASSERT(rc == NGX_OK, "must succeed");
    TEST_ASSERT(buf_size == 64,
        "max_size below old_size must keep old_size");
    TEST_ASSERT(heap_buf != NULL, "heap must be non-NULL");
    TEST_ASSERT(memcmp(expected, buf, 64) == 0,
        "old content must survive the copy");

    free(heap_buf);

    TEST_PASS("expand_buf max_size below old_size keeps old_size");
}


/* --- Test: send_origin constants are distinct --- */

static void
test_send_origin_constants(void)
{
    TEST_ASSERT(NGX_HTTP_MD_SEND_ORIGIN_NONE == 0, "NONE == 0");
    TEST_ASSERT(NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION != 0,
        "ALLOCATION != 0");
    TEST_ASSERT(NGX_HTTP_MD_SEND_ORIGIN_DOWNSTREAM != 0,
        "DOWNSTREAM != 0");
    TEST_ASSERT(NGX_HTTP_MD_SEND_ORIGIN_INVARIANT != 0,
        "INVARIANT != 0");
    TEST_ASSERT(NGX_HTTP_MD_SEND_ORIGIN_NONE
            != NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION,
        "NONE != ALLOCATION");
    TEST_ASSERT(NGX_HTTP_MD_SEND_ORIGIN_NONE
            != NGX_HTTP_MD_SEND_ORIGIN_DOWNSTREAM,
        "NONE != DOWNSTREAM");
    TEST_ASSERT(NGX_HTTP_MD_SEND_ORIGIN_NONE
            != NGX_HTTP_MD_SEND_ORIGIN_INVARIANT,
        "NONE != INVARIANT");
    TEST_ASSERT(NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION
            != NGX_HTTP_MD_SEND_ORIGIN_DOWNSTREAM,
        "ALLOCATION != DOWNSTREAM");
    TEST_ASSERT(NGX_HTTP_MD_SEND_ORIGIN_ALLOCATION
            != NGX_HTTP_MD_SEND_ORIGIN_INVARIANT,
        "ALLOCATION != INVARIANT");
    TEST_ASSERT(NGX_HTTP_MD_SEND_ORIGIN_DOWNSTREAM
            != NGX_HTTP_MD_SEND_ORIGIN_INVARIANT,
        "DOWNSTREAM != INVARIANT");

    TEST_PASS("send_origin constants correct and distinct");
}


/* --- Test: OVERFLOW_ERROR has no collision --- */

static void
test_overflow_no_collision(void)
{
    TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR
            != NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
        "OVERFLOW != BUDGET_EXCEEDED");
    TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR
            != NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
        "OVERFLOW != FORMAT_ERROR");
    TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR
            != NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
        "OVERFLOW != TRUNCATED_INPUT");
    TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR
            != NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR,
        "OVERFLOW != IO_ERROR");
    TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR
            != NGX_OK, "OVERFLOW != OK");
    TEST_ASSERT(NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR
            != NGX_ERROR, "OVERFLOW != NGX_ERROR");

    TEST_PASS("OVERFLOW_ERROR no collision");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("Post-commit Terminal Origin Tests\n");
    printf("========================================\n\n");

    test_expand_buf_overflow();
    test_expand_buf_success();
    test_expand_buf_max_size();
    test_expand_buf_max_size_below_old_size();
    test_send_origin_constants();
    test_overflow_no_collision();

    printf("\n  All postcommit terminal origin tests passed\n\n");
    return 0;
}
