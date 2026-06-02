/*
 * Test: buf_len_safe
 *
 * Validates the ngx_http_markdown_buf_len_safe() helper function.
 * Covers: NULL buf, NULL pos, NULL last, last < pos, and valid buffer.
 */

#include "../include/test_common.h"
#include "../../src/ngx_http_markdown_filter_module.h"

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#define NGX_LOG_DEBUG       8
#define NGX_LOG_DEBUG_HTTP  NGX_LOG_DEBUG
#define NGX_LOG_INFO        6

#define ngx_memzero(buf, n)       memset(buf, 0, n)
#define ngx_memcpy(dst, src, n)   memcpy(dst, src, n)

#define ngx_log_debug0(level, log, err, fmt) \
    do { (void) (level); (void) (log); (void) (err); \
         (void) (fmt); } while (0)
#define ngx_log_debug1(level, log, err, fmt, a1) \
    do { (void) (level); (void) (log); (void) (err); \
         (void) (fmt); (void) (a1); } while (0)

struct ngx_log_s {
    int dummy;
};

struct ngx_pool_s {
    int dummy;
};

/* ngx_buf_s is already defined in nginx_stubs/ngx_core.h */

struct ngx_chain_s {
    ngx_buf_t    *buf;
    ngx_chain_t  *next;
};

struct ngx_module_s {
    int dummy;
};

struct ngx_http_request_s {
    int dummy;
};


ngx_module_t ngx_http_markdown_filter_module;

static void
test_null_buf(void)
{
    size_t  len;

    len = ngx_http_markdown_buf_len_safe(NULL);
    TEST_ASSERT(len == 0, "NULL buf returns 0");
    TEST_PASS("NULL buf returns 0");
}

static void
test_null_pos(void)
{
    ngx_buf_t  buf;
    size_t     len;

    memset(&buf, 0, sizeof(buf));
    buf.pos = NULL;
    buf.last = (u_char *) "data";

    len = ngx_http_markdown_buf_len_safe(&buf);
    TEST_ASSERT(len == 0, "NULL pos returns 0");
    TEST_PASS("NULL pos returns 0");
}

static void
test_null_last(void)
{
    ngx_buf_t  buf;
    size_t     len;

    memset(&buf, 0, sizeof(buf));
    buf.pos = (u_char *) "data";
    buf.last = NULL;

    len = ngx_http_markdown_buf_len_safe(&buf);
    TEST_ASSERT(len == 0, "NULL last returns 0");
    TEST_PASS("NULL last returns 0");
}

static void
test_last_before_pos(void)
{
    u_char     data[16];
    ngx_buf_t  buf;
    size_t     len;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data + 8;
    buf.last = data + 4;

    len = ngx_http_markdown_buf_len_safe(&buf);
    TEST_ASSERT(len == 0, "last < pos returns 0");
    TEST_PASS("last < pos returns 0");
}

static void
test_valid_buffer(void)
{
    u_char     data[32];
    ngx_buf_t  buf;
    size_t     len;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data + 13;

    len = ngx_http_markdown_buf_len_safe(&buf);
    TEST_ASSERT(len == 13, "valid buffer returns correct length");
    TEST_PASS("valid buffer returns correct length (13)");
}

static void
test_empty_buffer(void)
{
    u_char     data[8];
    ngx_buf_t  buf;
    size_t     len;

    memset(&buf, 0, sizeof(buf));
    buf.pos = data;
    buf.last = data;

    len = ngx_http_markdown_buf_len_safe(&buf);
    TEST_ASSERT(len == 0, "pos == last returns 0");
    TEST_PASS("pos == last returns 0");
}

int
main(void)
{
    TEST_SECTION("ngx_http_markdown_buf_len_safe() tests");

    test_null_buf();
    test_null_pos();
    test_null_last();
    test_last_before_pos();
    test_valid_buffer();
    test_empty_buffer();

    printf("\nAll buf_len_safe tests passed.\n");
    return 0;
}
