/*
 * Test: decompression_production
 *
 * Exercises ngx_http_markdown_decompression.c directly.  The older
 * decompression_dispatch tests model the state machine; these tests cover the
 * production detector, dispatcher, zlib success path, and bounded error paths.
 */

#include "../include/test_common.h"

#include <arpa/inet.h>
#include <strings.h>
#include <zlib.h>

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

#define NGX_OK         0
#define NGX_ERROR     -1
#define NGX_DECLINED  -5

#define NGX_LOG_DEBUG       8
#define NGX_LOG_DEBUG_HTTP  NGX_LOG_DEBUG
#define NGX_LOG_INFO        6

#define ngx_memzero(buf, n)       memset(buf, 0, n)
#define ngx_memcpy(dst, src, n)   memcpy(dst, src, n)
#define ngx_strncasecmp(s1, s2, n) \
    strncasecmp((const char *) (s1), (const char *) (s2), (n))

#define ngx_log_debug0(level, log, err, fmt) \
    do { (void) (level); (void) (log); (void) (err); (void) (fmt); } while (0)
#define ngx_log_debug1(level, log, err, fmt, a1) \
    do { (void) (level); (void) (log); (void) (err); (void) (fmt); \
         (void) (a1); } while (0)
#define ngx_log_debug3(level, log, err, fmt, a1, a2, a3) \
    do { (void) (level); (void) (log); (void) (err); (void) (fmt); \
         (void) (a1); (void) (a2); (void) (a3); } while (0)

typedef struct {
    ngx_str_t  key;
    ngx_str_t  value;
    unsigned   hash;
} ngx_table_elt_t;

typedef struct {
    ngx_table_elt_t  *content_encoding;
} ngx_http_headers_out_t;

struct ngx_log_s {
    int dummy;
};

struct ngx_pool_s {
    int dummy;
};

typedef struct {
    ngx_log_t  *log;
} ngx_connection_t;

struct ngx_buf_s {
    u_char      *pos;
    u_char      *last;
    u_char      *start;
    u_char      *end;
    unsigned     temporary:1;
    unsigned     last_buf:1;
};

struct ngx_chain_s {
    ngx_buf_t    *buf;
    ngx_chain_t  *next;
};

struct ngx_http_request_s {
    ngx_pool_t               *pool;
    ngx_connection_t         *connection;
    ngx_http_headers_out_t    headers_out;
    void                     *loc_conf;
};

struct ngx_module_s {
    int dummy;
};

void *ngx_pnalloc(ngx_pool_t *pool, size_t size);
ngx_buf_t *ngx_calloc_buf(ngx_pool_t *pool);
ngx_chain_t *ngx_alloc_chain_link(ngx_pool_t *pool);

#include "../../src/ngx_http_markdown_filter_module.h"

ngx_module_t ngx_http_markdown_filter_module;

ngx_http_markdown_conf_t *
ngx_http_get_module_loc_conf(ngx_http_request_t *r, ngx_module_t module)
{
    (void) module;
    return (ngx_http_markdown_conf_t *) r->loc_conf;
}

void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return malloc(size);
}

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    return ngx_pnalloc(pool, size);
}

void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return calloc(1, size);
}

ngx_buf_t *
ngx_calloc_buf(ngx_pool_t *pool)
{
    return ngx_pcalloc(pool, sizeof(ngx_buf_t));
}

ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool)
{
    return ngx_pcalloc(pool, sizeof(ngx_chain_t));
}

#include "../../src/ngx_http_markdown_decompression.c"

static ngx_log_t               g_log;
static ngx_pool_t              g_pool;
static ngx_connection_t        g_conn = { &g_log };
static ngx_http_markdown_conf_t g_conf;

static void
init_request(ngx_http_request_t *r)
{
    memset(&g_conf, 0, sizeof(g_conf));
    g_conf.decompress_max_size = 1024 * 1024;

    memset(r, 0, sizeof(*r));
    r->pool = &g_pool;
    r->connection = &g_conn;
    r->loc_conf = &g_conf;
}

static void
set_encoding(ngx_http_request_t *r, const char *value)
{
    static ngx_table_elt_t h;

    memset(&h, 0, sizeof(h));

    if (value == NULL) {
        r->headers_out.content_encoding = NULL;
        return;
    }

    h.value.data = (u_char *) value;
    h.value.len = strlen(value);
    r->headers_out.content_encoding = &h;
}

static ngx_chain_t
make_chain(u_char *data, size_t len, ngx_buf_t *buf)
{
    ngx_chain_t cl;

    memset(buf, 0, sizeof(*buf));
    buf->pos = data;
    buf->last = data + len;
    buf->start = data;
    buf->end = data + len;

    cl.buf = buf;
    cl.next = NULL;
    return cl;
}

static size_t
gzip_compress(const u_char *input, size_t input_len, u_char *out,
    size_t out_cap)
{
    z_stream stream;
    int rc;

    memset(&stream, 0, sizeof(stream));

    rc = deflateInit2(&stream, Z_DEFAULT_COMPRESSION, Z_DEFLATED,
                      MAX_WBITS + 16, 8, Z_DEFAULT_STRATEGY);
    TEST_ASSERT(rc == Z_OK, "deflateInit2 should initialize gzip stream");

    stream.next_in = (Bytef *) input;
    stream.avail_in = (uInt) input_len;
    stream.next_out = out;
    stream.avail_out = (uInt) out_cap;

    rc = deflate(&stream, Z_FINISH);
    TEST_ASSERT(rc == Z_STREAM_END, "gzip compression should finish");

    out_cap = stream.total_out;
    deflateEnd(&stream);
    return out_cap;
}

static void
test_detect_compression_variants(void)
{
    ngx_http_request_t r;

    init_request(&r);

    set_encoding(&r, NULL);
    TEST_ASSERT(ngx_http_markdown_detect_compression(&r)
                == NGX_HTTP_MARKDOWN_COMPRESSION_NONE,
                "missing Content-Encoding should be none");

    set_encoding(&r, "");
    TEST_ASSERT(ngx_http_markdown_detect_compression(&r)
                == NGX_HTTP_MARKDOWN_COMPRESSION_NONE,
                "empty Content-Encoding should be none");

    set_encoding(&r, "GZip");
    TEST_ASSERT(ngx_http_markdown_detect_compression(&r)
                == NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
                "gzip detection should be case-insensitive");

    set_encoding(&r, "deflate");
    TEST_ASSERT(ngx_http_markdown_detect_compression(&r)
                == NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE,
                "deflate should be detected");

    set_encoding(&r, "br");
    TEST_ASSERT(ngx_http_markdown_detect_compression(&r)
                == NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI,
                "brotli should be detected");

    set_encoding(&r, "zstd");
    TEST_ASSERT(ngx_http_markdown_detect_compression(&r)
                == NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN,
                "unknown encoding should be classified");
}

static void
test_dispatch_non_decompressing_cases(void)
{
    ngx_http_request_t r;
    ngx_chain_t *out;

    init_request(&r);
    out = NULL;

    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_NONE, NULL, &out) == NGX_ERROR,
                "none should be a caller error");
    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN, NULL, &out)
                == NGX_DECLINED,
                "unknown should decline for fail-open passthrough");
    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, NULL, &out)
                == NGX_DECLINED,
                "brotli should decline when not compiled in");
    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                (ngx_http_markdown_compression_type_e) 99, NULL, &out)
                == NGX_ERROR,
                "invalid compression type should error");
}

static void
test_gzip_success(void)
{
    static const u_char plain[] = "<html><body>Hello</body></html>";
    u_char compressed[256];
    size_t compressed_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    init_request(&r);
    compressed_len = gzip_compress(plain, sizeof(plain) - 1,
                                   compressed, sizeof(compressed));
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out);

    TEST_ASSERT(rc == NGX_OK, "valid gzip should decompress");
    TEST_ASSERT(out != NULL && out->buf != NULL,
                "decompress should create output chain");
    TEST_ASSERT((size_t) (out->buf->last - out->buf->pos) == sizeof(plain) - 1,
                "decompressed length should match plain input");
    TEST_ASSERT(memcmp(out->buf->pos, plain, sizeof(plain) - 1) == 0,
                "decompressed bytes should match plain input");
}

static void
test_gzip_format_error(void)
{
    u_char invalid[] = "not gzip data";
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;

    init_request(&r);
    in = make_chain(invalid, sizeof(invalid) - 1, &in_buf);
    out = NULL;

    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out)
                == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
                "invalid gzip should be classified as format error");
}

static void
test_gzip_budget_exceeded(void)
{
    u_char plain[4096];
    u_char compressed[256];
    size_t compressed_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;

    memset(plain, 'A', sizeof(plain));

    init_request(&r);
    g_conf.decompress_max_size = 64;
    compressed_len = gzip_compress(plain, sizeof(plain),
                                   compressed, sizeof(compressed));
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;

    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out)
                == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
                "output larger than budget should be classified");
}

int
main(void)
{
    test_detect_compression_variants();
    test_dispatch_non_decompressing_cases();
    test_gzip_success();
    test_gzip_format_error();
    test_gzip_budget_exceeded();

    TEST_PASS("decompression_production: all tests passed");
    return 0;
}
