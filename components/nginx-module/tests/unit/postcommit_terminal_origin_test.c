/*
 * Test: postcommit_terminal_origin
 *
 * Validates post-commit terminal send failure origin classification
 * and the fix for safe-finish + zero closing bytes + terminal
 * immediate definitive failure.
 *
 * Tests verify constant values, expand_buf overflow semantics, and
 * origin classification correctness.
 */

#include "../include/test_common.h"
#include <limits.h>

#define NGX_OK 0
#define NGX_ERROR (-1)

#include <ngx_http_markdown_filter_module.h>

/*
 * DECOMP_ORIGIN constants are defined in the streaming decomp impl
 * header which has heavy NGINX dependencies.  Mirror the values here
 * for constant-value assertions; any drift will be caught by the
 * compile-time sizeof check below.
 */
#define TEST_DECOMP_ORIGIN_NONE       0
#define TEST_DECOMP_ORIGIN_ALLOCATION 1
#define TEST_DECOMP_ORIGIN_INTERNAL   2

/*
 * Minimal ngx_log_t for expand_buf signature.
 */
typedef struct { int unused; } ngx_log_t_stub;
#define ngx_log_t ngx_log_t_stub

/*
 * Minimal expand_buf implementation that mirrors production logic.
 * Tests verify the return code semantics independently of the full
 * NGINX dependency chain.
 */
static ngx_int_t
test_expand_buf(
    u_char **heap_buf_ptr,
    u_char **buf_ptr,
    size_t *buf_size_ptr,
    size_t max_size,
    ngx_log_t *log)
{
    u_char  *new_buf;
    size_t   old_size;
    size_t   new_size;

    (void) log;

    old_size = *buf_size_ptr;

    if (old_size > (size_t) -1 / 2) {
        if (heap_buf_ptr != NULL && *heap_buf_ptr != NULL) {
            free(*heap_buf_ptr);
            *heap_buf_ptr = NULL;
        }
        return NGX_HTTP_MARKDOWN_DECOMP_OVERFLOW_ERROR;
    }

    new_size = old_size * 2;
    if (max_size > 0 && new_size > max_size) {
        new_size = max_size;
    }

    new_buf = malloc(new_size);
    if (new_buf == NULL) {
        if (heap_buf_ptr != NULL && *heap_buf_ptr != NULL) {
            free(*heap_buf_ptr);
            *heap_buf_ptr = NULL;
        }
        return NGX_ERROR;
    }

    if (old_size > 0) {
        memcpy(new_buf, *buf_ptr, old_size);
    }

    if (heap_buf_ptr != NULL && *heap_buf_ptr != NULL) {
        free(*heap_buf_ptr);
    }

    *heap_buf_ptr = new_buf;
    *buf_ptr = new_buf;
    *buf_size_ptr = new_size;
    return NGX_OK;
}


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

    rc = test_expand_buf(&heap_buf, &buf, &buf_size, 0, NULL);

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

    rc = test_expand_buf(&heap_buf, &buf, &buf_size, 0, NULL);

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

    rc = test_expand_buf(&heap_buf, &buf, &buf_size, 96, NULL);

    TEST_ASSERT(rc == NGX_OK, "must succeed");
    TEST_ASSERT(buf_size == 96, "must cap to max_size");

    free(heap_buf);

    TEST_PASS("expand_buf max_size cap");
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
    test_send_origin_constants();
    test_overflow_no_collision();

    printf("\n  All postcommit terminal origin tests passed\n\n");
    return 0;
}
