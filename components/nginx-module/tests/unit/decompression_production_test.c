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

/* NGX_OK/NGX_ERROR/NGX_AGAIN/NGX_DECLINED are provided by the shared
   ngx_core.h stub (guarded with #ifndef).  Do not redefine them here
   to avoid macro-redefinition warnings. */

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
#define ngx_log_debug2(level, log, err, fmt, a1, a2) \
    do { (void) (level); (void) (log); (void) (err); (void) (fmt); \
         (void) (a1); (void) (a2); } while (0)
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

/* struct ngx_buf_s provided by nginx_stubs/ngx_core.h */

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
void *ngx_alloc(size_t size, ngx_log_t *log);
void ngx_free(void *p);
ngx_buf_t *ngx_calloc_buf(ngx_pool_t *pool);
ngx_chain_t *ngx_alloc_chain_link(ngx_pool_t *pool);

#include "../../src/ngx_http_markdown_filter_module.h"

ngx_module_t ngx_http_markdown_filter_module;

static int g_pnalloc_fail_count;
static int g_alloc_fail_count;
static int g_calloc_buf_fail_once;
static int g_chain_link_fail_once;
static int g_pfree_count;
static size_t g_heap_alloc_count;
static size_t g_heap_free_count;

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
    if (g_pnalloc_fail_count > 0) {
        g_pnalloc_fail_count--;
        if (g_pnalloc_fail_count == 0) {
            return NULL;
        }
    }
    return malloc(size);
}

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    return ngx_pnalloc(pool, size);
}

void *
ngx_alloc(size_t size, ngx_log_t *log)
{
    void *p;

    (void) log;
    if (g_alloc_fail_count > 0) {
        g_alloc_fail_count--;
        if (g_alloc_fail_count == 0) {
            return NULL;
        }
    }

    p = malloc(size);
    if (p != NULL) {
        g_heap_alloc_count++;
    }
    return p;
}

void
ngx_free(void *p)
{
    if (p == NULL) {
        return;
    }

    g_heap_free_count++;
    free(p);
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
    if (g_calloc_buf_fail_once) {
        g_calloc_buf_fail_once = 0;
        return NULL;
    }
    return ngx_pcalloc(pool, sizeof(ngx_buf_t));
}

ngx_chain_t *
ngx_alloc_chain_link(ngx_pool_t *pool)
{
    if (g_chain_link_fail_once) {
        g_chain_link_fail_once = 0;
        return NULL;
    }
    return ngx_pcalloc(pool, sizeof(ngx_chain_t));
}

/*
 * Mock ngx_pfree: records any accidental attempt to release pool memory.
 * Transferable decompression output must exclusively use ngx_alloc/ngx_free.
 */
ngx_int_t
ngx_pfree(ngx_pool_t *pool, void *p)
{
    (void) pool;
    (void) p;
    g_pfree_count++;
    return NGX_OK;
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
    g_conf.decompress.max_size = 1024 * 1024;
    g_pnalloc_fail_count = 0;
    g_alloc_fail_count = 0;
    g_calloc_buf_fail_once = 0;
    g_chain_link_fail_once = 0;
    g_pfree_count = 0;
    g_heap_alloc_count = 0;
    g_heap_free_count = 0;

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

static void
release_output(ngx_chain_t *out)
{
    if (out == NULL || out->buf == NULL || out->buf->start == NULL) {
        return;
    }

    ngx_free(out->buf->start);
    out->buf->pos = NULL;
    out->buf->last = NULL;
    out->buf->start = NULL;
    out->buf->end = NULL;
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

static size_t
zlib_compress(const u_char *input, size_t input_len, u_char *out,
    size_t out_cap)
{
    z_stream stream;
    int rc;

    memset(&stream, 0, sizeof(stream));

    rc = deflateInit2(&stream, Z_DEFAULT_COMPRESSION, Z_DEFLATED,
                      MAX_WBITS, 8, Z_DEFAULT_STRATEGY);
    TEST_ASSERT(rc == Z_OK, "deflateInit2 should initialize zlib stream");

    stream.next_in = (Bytef *) input;
    stream.avail_in = (uInt) input_len;
    stream.next_out = out;
    stream.avail_out = (uInt) out_cap;

    rc = deflate(&stream, Z_FINISH);
    TEST_ASSERT(rc == Z_STREAM_END, "zlib compression should finish");

    out_cap = stream.total_out;
    deflateEnd(&stream);
    return out_cap;
}

/*
 * Compress input as raw deflate (RFC 1951, no zlib wrapper).
 * Used to test the full-buffer raw deflate fallback path and its
 * trailing-data rejection.
 */
static size_t
raw_deflate_compress(const u_char *input, size_t input_len, u_char *out,
    size_t out_cap)
{
    z_stream stream;
    int rc;

    memset(&stream, 0, sizeof(stream));

    rc = deflateInit2(&stream, Z_DEFAULT_COMPRESSION, Z_DEFLATED,
                      -MAX_WBITS, 8, Z_DEFAULT_STRATEGY);
    TEST_ASSERT(rc == Z_OK, "deflateInit2 should initialize raw deflate stream");

    stream.next_in = (Bytef *) input;
    stream.avail_in = (uInt) input_len;
    stream.next_out = out;
    stream.avail_out = (uInt) out_cap;

    rc = deflate(&stream, Z_FINISH);
    TEST_ASSERT(rc == Z_STREAM_END, "raw deflate compression should finish");

    out_cap = stream.total_out;
    deflateEnd(&stream);
    return out_cap;
}

static void
test_chain_helpers_boundaries(void)
{
    u_char d1[] = "ab";
    u_char d2[] = "cd";
    ngx_buf_t b1;
    ngx_buf_t b2;
    ngx_chain_t c1;
    ngx_chain_t c2;
    ngx_chain_t null_buf;
    u_char dest[4];

    memset(dest, 0, sizeof(dest));
    c1 = make_chain(d1, sizeof(d1) - 1, &b1);
    c2 = make_chain(d2, sizeof(d2) - 1, &b2);
    c1.next = &null_buf;
    null_buf.buf = NULL;
    null_buf.next = &c2;

    TEST_ASSERT(ngx_http_markdown_chain_size(NULL) == 0,
                "NULL chain should have size 0");
    TEST_ASSERT(ngx_http_markdown_chain_size(&c1) == 4,
                "chain_size should skip NULL buffers");
    TEST_ASSERT(ngx_http_markdown_chain_to_buffer(&c1, dest, 4) == NGX_OK,
                "chain_to_buffer should copy complete chain");
    TEST_ASSERT(memcmp(dest, "abcd", 4) == 0,
                "chain_to_buffer should preserve byte order");
    TEST_ASSERT(ngx_http_markdown_chain_to_buffer(&c1, dest, 3) == NGX_ERROR,
                "chain_to_buffer should reject insufficient destination");
}

static void
test_calc_output_size_boundaries(void)
{
    ngx_http_request_t r;
    size_t output_size;

    init_request(&r);

    TEST_ASSERT(ngx_http_markdown_calc_output_size(&r, 10, 0,
                &output_size) == NGX_ERROR,
                "zero decompression budget should be rejected");
    TEST_ASSERT(ngx_http_markdown_calc_output_size(&r, 0, 1024,
                &output_size) == NGX_ERROR,
                "zero estimated output should be rejected");
    TEST_ASSERT(ngx_http_markdown_calc_output_size(&r, 20, 1024,
                &output_size) == NGX_OK && output_size == 200,
                "normal estimate should use 10x heuristic");
    TEST_ASSERT(ngx_http_markdown_calc_output_size(&r, 200, 1024,
                &output_size) == NGX_OK && output_size == 1024,
                "estimate should be capped by decompression budget");
    TEST_ASSERT(ngx_http_markdown_calc_output_size(&r, ((size_t) -1), 4096,
                &output_size) == NGX_OK && output_size == 4096,
                "multiplication overflow guard should use budget cap");
    TEST_ASSERT(ngx_http_markdown_calc_output_size(&r,
                ((size_t) UINT_MAX / 10) + 100,
                (size_t) UINT_MAX + 100, &output_size) == NGX_OK
                && output_size == (size_t) UINT_MAX,
                "large estimate should be clamped to UINT_MAX");
    TEST_ASSERT(ngx_http_markdown_calc_output_size(&r, 6 * 1024 * 1024,
                100 * 1024 * 1024, &output_size) == NGX_OK
                && output_size == 60 * 1024 * 1024,
                "large estimate warning path should still succeed");
    TEST_ASSERT(ngx_http_markdown_calc_output_size(&r, 16, (size_t) UINT_MAX + 1,
                &output_size) == NGX_OK && output_size == 160,
                "large budget should still allow small estimates");
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
    release_output(out);
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "successful gzip output should remain independently freeable");
}

static void
test_gzip_concatenated_members(void)
{
    static const u_char first[] = "<html><body>";
    static const u_char second[] = "joined</body></html>";
    u_char compressed[512];
    size_t first_len;
    size_t second_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    first_len = gzip_compress(first, sizeof(first) - 1,
                              compressed, sizeof(compressed));
    second_len = gzip_compress(second, sizeof(second) - 1,
                               compressed + first_len,
                               sizeof(compressed) - first_len);

    init_request(&r);
    in = make_chain(compressed, first_len + second_len, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out);

    TEST_ASSERT(rc == NGX_OK,
                "concatenated gzip members should decompress");
    TEST_ASSERT(out != NULL && out->buf != NULL,
                "concatenated gzip should create output chain");
    TEST_ASSERT((size_t) (out->buf->last - out->buf->pos)
                    == sizeof(first) + sizeof(second) - 2,
                "concatenated gzip length should include every member");
    TEST_ASSERT(memcmp(out->buf->pos, first, sizeof(first) - 1) == 0,
                "concatenated gzip should preserve first member");
    TEST_ASSERT(memcmp(out->buf->pos + sizeof(first) - 1,
                       second, sizeof(second) - 1) == 0,
                "concatenated gzip should append later members");
    release_output(out);
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "concatenated gzip output should be released exactly once");
}

static void
test_gzip_concatenated_truncated_second_member(void)
{
    static const u_char first[] = "first member";
    static const u_char second[] = "second member must finish";
    u_char compressed[512];
    size_t first_len;
    size_t second_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    first_len = gzip_compress(first, sizeof(first) - 1,
                              compressed, sizeof(compressed));
    second_len = gzip_compress(second, sizeof(second) - 1,
                               compressed + first_len,
                               sizeof(compressed) - first_len);

    init_request(&r);
    in = make_chain(compressed, first_len + second_len - 4, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out);

    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
                "truncated later gzip member should be rejected");
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "truncated later member should release decompression output");
}

static void
test_gzip_concatenated_members_share_budget(void)
{
    u_char first[160];
    u_char second[160];
    u_char compressed[512];
    size_t first_len;
    size_t second_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    memset(first, 'A', sizeof(first));
    memset(second, 'B', sizeof(second));
    first_len = gzip_compress(first, sizeof(first),
                              compressed, sizeof(compressed));
    second_len = gzip_compress(second, sizeof(second),
                               compressed + first_len,
                               sizeof(compressed) - first_len);

    init_request(&r);
    g_conf.decompress.max_size = 256;
    in = make_chain(compressed, first_len + second_len, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out);

    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
                "gzip members should share one decompression budget");
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "shared-budget failure should release decompression output");
}

static void
test_gzip_empty_later_member_at_exact_budget(void)
{
    u_char first[256];
    u_char compressed[512];
    size_t first_len;
    size_t second_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    memset(first, 'A', sizeof(first));
    first_len = gzip_compress(first, sizeof(first),
                              compressed, sizeof(compressed));
    second_len = gzip_compress((const u_char *) "", 0,
                               compressed + first_len,
                               sizeof(compressed) - first_len);

    init_request(&r);
    g_conf.decompress.max_size = sizeof(first);
    in = make_chain(compressed, first_len + second_len, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out);

    TEST_ASSERT(rc == NGX_OK,
                "empty later gzip member should fit an exact budget");
    TEST_ASSERT((size_t) (out->buf->last - out->buf->pos) == sizeof(first),
                "empty later gzip member should not change output length");
    release_output(out);
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "exact-budget output should be released exactly once");
}

static void
test_gzip_later_member_grows_at_member_boundary(void)
{
    u_char first[64];
    static const u_char second[] = "second";
    u_char compressed[512];
    u_char *output_data;
    size_t first_len;
    size_t second_len;
    size_t output_size;
    size_t total_out;
    z_stream stream;
    ngx_http_request_t r;
    ngx_int_t rc;

    memset(first, 'A', sizeof(first));
    first_len = gzip_compress(first, sizeof(first),
                              compressed, sizeof(compressed));
    second_len = gzip_compress(second, sizeof(second) - 1,
                               compressed + first_len,
                               sizeof(compressed) - first_len);

    init_request(&r);
    g_conf.decompress.max_size = 256;
    output_size = sizeof(first);
    output_data = ngx_alloc(output_size, r.connection->log);
    TEST_ASSERT(output_data != NULL, "member-boundary output alloc");

    memset(&stream, 0, sizeof(stream));
    stream.next_in = compressed;
    stream.avail_in = (uInt) (first_len + second_len);
    stream.next_out = output_data;
    stream.avail_out = (uInt) output_size;
    TEST_ASSERT(inflateInit2(&stream, MAX_WBITS + 16) == Z_OK,
                "member-boundary inflate init");

    rc = ngx_http_markdown_inflate_loop(&r, &g_conf, &stream,
        &output_data, &output_size, NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        &total_out);

    TEST_ASSERT(rc == NGX_OK,
                "later gzip member should grow a full current buffer");
    TEST_ASSERT(total_out == sizeof(first) + sizeof(second) - 1,
                "member-boundary growth should preserve total output");
    TEST_ASSERT(memcmp(output_data, first, sizeof(first)) == 0,
                "member-boundary growth should preserve first member");
    TEST_ASSERT(memcmp(output_data + sizeof(first),
                       second, sizeof(second) - 1) == 0,
                "member-boundary growth should append later member");

    inflateEnd(&stream);
    ngx_free(output_data);
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "member-boundary growth should release each heap allocation");
    TEST_ASSERT(g_pfree_count == 0,
                "member-boundary growth must not free through the pool");
}

static void
test_deflate_success(void)
{
    static const u_char plain[] = "<html><p>deflate</p></html>";
    u_char compressed[256];
    size_t compressed_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    init_request(&r);
    compressed_len = zlib_compress(plain, sizeof(plain) - 1,
                                   compressed, sizeof(compressed));
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE, &in, &out);

    TEST_ASSERT(rc == NGX_OK, "valid deflate should decompress");
    TEST_ASSERT(out != NULL && out->buf != NULL,
                "deflate should create output chain");
    TEST_ASSERT((size_t) (out->buf->last - out->buf->pos) == sizeof(plain) - 1,
                "deflate decompressed length should match plain input");
    TEST_ASSERT(memcmp(out->buf->pos, plain, sizeof(plain) - 1) == 0,
                "deflate decompressed bytes should match plain input");
    release_output(out);
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "successful deflate output should be released exactly once");
}

static void
test_gzip_empty_input_error(void)
{
    ngx_chain_t *out;
    ngx_http_request_t r;

    init_request(&r);
    out = NULL;

    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, NULL, &out) == NGX_ERROR,
                "empty gzip input should return generic error");
}

static void
test_gzip_allocation_and_budget_setup_errors(void)
{
    static const u_char plain[] = "<html><p>allocation</p></html>";
    u_char compressed[256];
    size_t compressed_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;

    compressed_len = gzip_compress(plain, sizeof(plain) - 1,
                                   compressed, sizeof(compressed));

    init_request(&r);
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;
    g_pnalloc_fail_count = 1;
    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out) == NGX_ERROR,
                "input buffer allocation failure should error");

    init_request(&r);
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;
    g_conf.decompress.max_size = 0;
    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out) == NGX_ERROR,
                "invalid decompression budget should error after inflate init");

    init_request(&r);
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;
    g_alloc_fail_count = 1;
    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out) == NGX_ERROR,
                "output buffer allocation failure should error");
    TEST_ASSERT(g_heap_alloc_count == 0 && g_heap_free_count == 0,
                "failed output allocation should not create ownership");
    TEST_ASSERT(g_pfree_count == 0,
                "output allocation failures must not use pool free");
}

static void
test_gzip_output_chain_allocation_errors(void)
{
    static const u_char plain[] = "<html><p>chain</p></html>";
    u_char compressed[256];
    size_t compressed_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;

    compressed_len = gzip_compress(plain, sizeof(plain) - 1,
                                   compressed, sizeof(compressed));

    init_request(&r);
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;
    g_calloc_buf_fail_once = 1;
    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out) == NGX_ERROR,
                "output ngx_buf_t allocation failure should error");
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "ngx_buf_t failure should release decompression output");
    TEST_ASSERT(g_pfree_count == 0,
                "ngx_buf_t failure must not release output through the pool");

    init_request(&r);
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;
    g_chain_link_fail_once = 1;
    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out) == NGX_ERROR,
                "output chain link allocation failure should error");
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "chain-link failure should release decompression output");
    TEST_ASSERT(g_pfree_count == 0,
                "chain-link failure must not release output through the pool");
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
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "format errors should release decompression output");
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
    g_conf.decompress.max_size = 64;
    compressed_len = gzip_compress(plain, sizeof(plain),
                                   compressed, sizeof(compressed));
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;

    TEST_ASSERT(ngx_http_markdown_decompress(&r,
                NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out)
                == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
                "output larger than budget should be classified");
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "budget errors should release decompression output");
}

/*
 * Test grow_output_buffer directly (lines 261-311).
 * Exercises the buffer growth path that is normally only reachable
 * when inflate fills the output buffer and needs more space.
 */
static void
test_grow_output_buffer_direct(void)
{
    ngx_http_request_t r;
    u_char *output_data;
    size_t output_size;
    size_t frees_before;
    u_char *initial_buf;
    ngx_int_t rc;

    init_request(&r);

    /* Normal growth: used < max_size, should succeed */
    initial_buf = ngx_alloc(400, r.connection->log);
    TEST_ASSERT(initial_buf != NULL,
                "test setup should allocate initial heap buffer");
    output_data = initial_buf;
    output_size = 400;
    g_conf.decompress.max_size = 1500;
    frees_before = g_heap_free_count;
    rc = ngx_http_markdown_grow_output_buffer(
        &r, &g_conf, &output_data, &output_size, 300);
    TEST_ASSERT(rc == NGX_OK,
                "grow_output_buffer should succeed when under budget");
    /* new_size = max(300*2, 300+4096) = 4396, capped at max_size=1500 */
    TEST_ASSERT(output_size == 1500,
                "grow_output_buffer should cap at max_size");
    TEST_ASSERT(output_data != initial_buf,
                "successful growth should replace the old buffer");
    TEST_ASSERT(g_heap_free_count == frees_before + 1,
                "successful growth should free the old heap buffer once");
    ngx_free(output_data);

    /* Budget exceeded: used >= max_size */
    initial_buf = ngx_alloc(400, r.connection->log);
    TEST_ASSERT(initial_buf != NULL,
                "test setup should allocate budget-case heap buffer");
    output_data = initial_buf;
    output_size = 400;
    g_conf.decompress.max_size = 200;
    frees_before = g_heap_free_count;
    rc = ngx_http_markdown_grow_output_buffer(
        &r, &g_conf, &output_data, &output_size, 250);
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
                "grow_output_buffer should fail when used >= max_size");
    TEST_ASSERT(output_data == initial_buf,
                "budget failure should retain the old buffer");
    TEST_ASSERT(g_heap_free_count == frees_before,
                "budget failure should leave old-buffer ownership to caller");
    ngx_free(output_data);

    /* Minimum growth: used * 2 < used + 4096, should use +4096 */
    initial_buf = ngx_alloc(400, r.connection->log);
    TEST_ASSERT(initial_buf != NULL,
                "test setup should allocate minimum-growth heap buffer");
    output_data = initial_buf;
    output_size = 400;
    g_conf.decompress.max_size = 10000;
    frees_before = g_heap_free_count;
    rc = ngx_http_markdown_grow_output_buffer(
        &r, &g_conf, &output_data, &output_size, 100);
    TEST_ASSERT(rc == NGX_OK,
                "grow_output_buffer should succeed for small used");
    TEST_ASSERT(output_size == 4196,
                "grow_output_buffer should use used+4096 when 2*used < used+4096");
    TEST_ASSERT(g_heap_free_count == frees_before + 1,
                "minimum growth should free its own old heap buffer");
    ngx_free(output_data);

    /* Capped by max_size */
    initial_buf = ngx_alloc(400, r.connection->log);
    TEST_ASSERT(initial_buf != NULL,
                "test setup should allocate capped-growth heap buffer");
    output_data = initial_buf;
    output_size = 400;
    g_conf.decompress.max_size = 500;
    frees_before = g_heap_free_count;
    rc = ngx_http_markdown_grow_output_buffer(
        &r, &g_conf, &output_data, &output_size, 300);
    TEST_ASSERT(rc == NGX_OK,
                "grow_output_buffer should succeed when capped by max_size");
    TEST_ASSERT(output_size == 500,
                "grow_output_buffer new_size should be capped at max_size");
    TEST_ASSERT(g_heap_free_count == frees_before + 1,
                "capped growth should free its own old heap buffer");
    ngx_free(output_data);

    /* Allocation failure */
    initial_buf = ngx_alloc(400, r.connection->log);
    TEST_ASSERT(initial_buf != NULL,
                "test setup should allocate allocation-failure heap buffer");
    output_data = initial_buf;
    output_size = 400;
    g_conf.decompress.max_size = 1500;
    g_alloc_fail_count = 1;
    frees_before = g_heap_free_count;
    rc = ngx_http_markdown_grow_output_buffer(
        &r, &g_conf, &output_data, &output_size, 300);
    TEST_ASSERT(rc == NGX_ERROR,
                "grow_output_buffer should fail on allocation failure");
    TEST_ASSERT(output_data == initial_buf,
                "allocation failure should retain the old buffer");
    TEST_ASSERT(g_heap_free_count == frees_before,
                "allocation failure should not free the old buffer");
    ngx_free(output_data);

    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "direct growth cases should balance heap ownership");
    TEST_ASSERT(g_pfree_count == 0,
                "grow_output_buffer must never use ngx_pfree");
}

/*
 * Test handle_inflate_stall directly (lines 386-409).
 * Exercises the three branches: avail_out==0 (grow), avail_in==0
 * (truncated), and unexpected stall.
 */
static void
test_handle_inflate_stall_direct(void)
{
    ngx_http_request_t r;
    z_stream stream;
    u_char output_buf[256];
    u_char *output_data;
    size_t output_size;
    ngx_int_t rc;
    ngx_http_markdown_inflate_ctx_t ctx;

    init_request(&r);
    ctx.request = &r;
    ctx.conf = &g_conf;
    ctx.stream = &stream;
    ctx.output_data = &output_data;
    ctx.output_size = &output_size;
    ctx.type = NGX_HTTP_MARKDOWN_COMPRESSION_GZIP;
    ctx.completed_out = 0;

    /* Branch 1: avail_out==0 -> should grow buffer and return NGX_AGAIN */
    output_data = ngx_alloc(sizeof(output_buf), r.connection->log);
    TEST_ASSERT(output_data != NULL,
                "stall growth setup should allocate a heap buffer");
    output_size = 256;
    g_conf.decompress.max_size = 4096;
    memset(&stream, 0, sizeof(stream));
    stream.avail_out = 0;
    stream.avail_in = 10;
    stream.total_out = 256;
    rc = ngx_http_markdown_handle_inflate_stall(
        &ctx, NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR, "Z_OK");
    TEST_ASSERT(rc == NGX_AGAIN,
                "handle_inflate_stall should return AGAIN when avail_out==0");
    ngx_free(output_data);

    /* Branch 2: avail_in==0 -> truncated input */
    output_data = output_buf;
    output_size = 256;
    g_conf.decompress.max_size = 4096;
    memset(&stream, 0, sizeof(stream));
    stream.avail_out = 100;
    stream.avail_in = 0;
    rc = ngx_http_markdown_handle_inflate_stall(
        &ctx, NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR, "Z_OK");
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_TRUNCATED_INPUT,
                "handle_inflate_stall should detect truncated input");

    /* Branch 3: unexpected stall (avail_in>0, avail_out>0) */
    output_data = output_buf;
    output_size = 256;
    g_conf.decompress.max_size = 4096;
    memset(&stream, 0, sizeof(stream));
    stream.avail_out = 50;
    stream.avail_in = 10;
    rc = ngx_http_markdown_handle_inflate_stall(
        &ctx, NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR, "Z_BUF_ERROR");
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
                "handle_inflate_stall should return stall_code on unexpected stall");

    /* Branch 4: avail_out==0 but grow fails (budget exceeded) */
    output_data = ngx_alloc(sizeof(output_buf), r.connection->log);
    TEST_ASSERT(output_data != NULL,
                "budget stall setup should allocate a heap buffer");
    output_size = 256;
    g_conf.decompress.max_size = 200;
    memset(&stream, 0, sizeof(stream));
    stream.avail_out = 0;
    stream.avail_in = 10;
    stream.total_out = 256;
    rc = ngx_http_markdown_handle_inflate_stall(
        &ctx, NGX_HTTP_MARKDOWN_DECOMP_IO_ERROR, "Z_OK");
    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
                "handle_inflate_stall should propagate budget exceeded from grow");
    ngx_free(output_data);
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "stall handling should preserve heap-buffer ownership");
    TEST_ASSERT(g_pfree_count == 0,
                "stall handling must never use ngx_pfree");
}

/*
 * Test chain_size handling for invalid reversed buffer pointers.
 */
static void
test_chain_size_invalid_reversed_buffers(void)
{
    /*
     * A previous test attempted to exercise the addition-overflow branch by
     * manufacturing pointers far beyond a one-byte object.  That violates the
     * C pointer model and conflicts with the module baseline that forbids
     * arithmetic on invalid pointer values.  Use only pointers inside real
     * arrays here: last < pos is malformed input, and the safe helper must
     * treat it as zero-length.
     */
    u_char backing1[2];
    u_char backing2[2];
    ngx_buf_t buf1;
    ngx_buf_t buf2;
    ngx_chain_t cl1;
    ngx_chain_t cl2;
    size_t result;

    memset(&buf1, 0, sizeof(buf1));
    buf1.pos = backing1 + 1;
    buf1.last = backing1;

    memset(&buf2, 0, sizeof(buf2));
    buf2.pos = backing2 + 1;
    buf2.last = backing2;

    cl1.buf = &buf1;
    cl1.next = &cl2;
    cl2.buf = &buf2;
    cl2.next = NULL;

    result = ngx_http_markdown_chain_size(&cl1);
    TEST_ASSERT(result == 0,
                "chain_size should ignore invalid reversed buffers");
}

/*
 * Integration test: gzip decompression with buffer growth.
 * Uses a small max_size to force the inflate loop to trigger
 * ngx_http_markdown_grow_decomp_buffer when the output buffer
 * fills up.
 */
static void
test_gzip_buffer_growth(void)
{
    /*
     * Use 3000 bytes of highly compressible data. After gzip
     * compression this is roughly 100-200 bytes. Setting
     * max_size=400 means the initial buffer estimate
     * (min(compressed*10, 400)) is ~400 bytes, but the
     * decompressed output is ~3000 bytes. The inflate loop
     * must grow the buffer multiple times.
     */
    u_char plain[3000];
    u_char compressed[512];
    size_t compressed_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    memset(plain, 'C', sizeof(plain));
    compressed_len = gzip_compress(plain, sizeof(plain),
                                   compressed, sizeof(compressed));

    init_request(&r);
    g_conf.decompress.max_size = sizeof(plain) + 1024;
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out);

    TEST_ASSERT(rc == NGX_OK,
                "gzip with buffer growth should succeed");
    TEST_ASSERT(out != NULL && out->buf != NULL,
                "buffer growth should produce output chain");
    TEST_ASSERT((size_t) (out->buf->last - out->buf->pos) == sizeof(plain),
                "decompressed length should match original");
    TEST_ASSERT(memcmp(out->buf->pos, plain, sizeof(plain)) == 0,
                "decompressed content should match original");
    release_output(out);
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "grown gzip output should release every heap generation");
    TEST_ASSERT(g_pfree_count == 0,
                "grown gzip output must not use pool free");
}

/*
 * Integration test: budget exceeded during buffer growth.
 * The initial buffer fits, but the decompressed output exceeds
 * max_size, causing the grow path to return BUDGET_EXCEEDED.
 */
static void
test_gzip_budget_exceeded_during_growth(void)
{
    u_char plain[4096];
    u_char compressed[512];
    size_t compressed_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    memset(plain, 'B', sizeof(plain));
    compressed_len = gzip_compress(plain, sizeof(plain),
                                   compressed, sizeof(compressed));

    init_request(&r);
    /*
     * max_size=256: initial buffer is 256 bytes. The decompressed
     * output is 4096 bytes. The inflate loop fills the 256-byte
     * buffer, then grow_output_buffer is called. Since used(256)
     * < max_size(256) is false (they're equal), it returns
     * BUDGET_EXCEEDED immediately.
     */
    g_conf.decompress.max_size = 256;
    in = make_chain(compressed, compressed_len, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out);

    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_BUDGET_EXCEEDED,
                "gzip budget exceeded during growth should be classified");
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "growth budget failure should release the active output");
}

/*
 * Test truncated gzip input returns an error.
 * Passes only a partial gzip stream to exercise error handling
 * in the inflate loop when the stream ends prematurely.
 */
static void
test_gzip_truncated_input(void)
{
    static const u_char plain[] = "<html><body>truncated test data here</body></html>";
    u_char compressed[256];
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    (void) gzip_compress(plain, sizeof(plain) - 1,
                         compressed, sizeof(compressed));

    /*
     * Pass only the first 12 bytes of the gzip stream (header only,
     * no deflate payload). The inflate loop should detect the
     * truncated stream and return an error.
     */
    init_request(&r);
    g_conf.decompress.max_size = 1024 * 1024;
    in = make_chain(compressed, 12, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out);

    TEST_ASSERT(rc != NGX_OK && rc != NGX_DECLINED,
                "truncated gzip should return an error");
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "truncated gzip should release decompression output");
}

/*
 * Test that zlib-wrapped deflate with trailing garbage is rejected as
 * FORMAT_ERROR.  A complete deflate stream must consume every byte of
 * the compressed payload; any remaining input after Z_STREAM_END is
 * trailing data that must not be silently accepted.
 */
static void
test_deflate_zlib_trailing_data(void)
{
    static const u_char plain[] = "<html><p>zlib deflate trailing</p></html>";
    u_char compressed[256];
    u_char combined[512];
    size_t compressed_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    compressed_len = zlib_compress(plain, sizeof(plain) - 1,
                                   compressed, sizeof(compressed));

    /* Append trailing garbage after the valid zlib-wrapped deflate stream */
    memcpy(combined, compressed, compressed_len);
    memcpy(combined + compressed_len, "TRAILING_GARBAGE", 16);

    init_request(&r);
    in = make_chain(combined, compressed_len + 16, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE, &in, &out);

    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
                "zlib-wrapped deflate with trailing garbage should be FORMAT_ERROR");
    TEST_ASSERT(out == NULL,
                "trailing-data error should not produce output chain");
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "zlib trailing-data error should release output");
}

/*
 * Test that raw deflate with trailing garbage is rejected as FORMAT_ERROR.
 * The raw deflate fallback path (triggered after zlib-wrapped FORMAT_ERROR)
 * must also enforce complete input consumption.
 */
static void
test_deflate_raw_trailing_data(void)
{
    static const u_char plain[] = "<html><p>raw deflate trailing</p></html>";
    u_char compressed[256];
    u_char combined[512];
    size_t compressed_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    compressed_len = raw_deflate_compress(plain, sizeof(plain) - 1,
                                          compressed, sizeof(compressed));

    /* Append trailing garbage after the valid raw deflate stream */
    memcpy(combined, compressed, compressed_len);
    memcpy(combined + compressed_len, "RAW_TRAILING_GARBAGE", 20);

    init_request(&r);
    in = make_chain(combined, compressed_len + 20, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE, &in, &out);

    TEST_ASSERT(rc == NGX_HTTP_MARKDOWN_DECOMP_FORMAT_ERROR,
                "raw deflate with trailing garbage should be FORMAT_ERROR");
    TEST_ASSERT(out == NULL,
                "raw trailing-data error should not produce output chain");
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "raw trailing-data error should release output");
}

/*
 * Test that clean deflate (no trailing data) still succeeds for both
 * zlib-wrapped and raw deflate formats.  This is the positive control
 * ensuring the trailing-data guard does not over-reject valid streams.
 */
static void
test_deflate_clean_still_succeeds(void)
{
    static const u_char plain[] = "<html><p>clean deflate full-buffer</p></html>";
    u_char zlib_compressed[256];
    u_char raw_compressed[256];
    size_t zlib_len;
    size_t raw_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    zlib_len = zlib_compress(plain, sizeof(plain) - 1,
                             zlib_compressed, sizeof(zlib_compressed));
    raw_len = raw_deflate_compress(plain, sizeof(plain) - 1,
                                    raw_compressed, sizeof(raw_compressed));

    /* zlib-wrapped deflate, clean stream */
    init_request(&r);
    in = make_chain(zlib_compressed, zlib_len, &in_buf);
    out = NULL;
    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE, &in, &out);
    TEST_ASSERT(rc == NGX_OK,
                "clean zlib-wrapped deflate should succeed");
    TEST_ASSERT(out != NULL && out->buf != NULL,
                "clean zlib-wrapped deflate should produce output");
    TEST_ASSERT((size_t)(out->buf->last - out->buf->pos) == sizeof(plain) - 1,
                "clean zlib-wrapped deflate length should match");
    TEST_ASSERT(memcmp(out->buf->pos, plain, sizeof(plain) - 1) == 0,
                "clean zlib-wrapped deflate bytes should match");
    release_output(out);
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "clean zlib output should be released exactly once");

    /* raw deflate, clean stream — the fallback path should also succeed */
    init_request(&r);
    in = make_chain(raw_compressed, raw_len, &in_buf);
    out = NULL;
    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE, &in, &out);
    /*
     * Raw deflate data does not have a zlib header, so the first
     * inflateInit2(MAX_WBITS) attempt will fail with FORMAT_ERROR and
     * the fallback retry with -MAX_WBITS should succeed.
     */
    TEST_ASSERT(rc == NGX_OK,
                "clean raw deflate should succeed via fallback");
    TEST_ASSERT(out != NULL && out->buf != NULL,
                "clean raw deflate should produce output");
    TEST_ASSERT((size_t)(out->buf->last - out->buf->pos) == sizeof(plain) - 1,
                "clean raw deflate length should match");
    TEST_ASSERT(memcmp(out->buf->pos, plain, sizeof(plain) - 1) == 0,
                "clean raw deflate bytes should match");
    release_output(out);
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "clean raw output should be released exactly once");
}

/*
 * Test that gzip concatenated members still succeed after the deflate
 * trailing-data fix.  This is the full-buffer anti-regression guard:
 * the trailing-data rejection applies only to deflate, not gzip.
 */
static void
test_gzip_concatenated_not_regressed(void)
{
    static const u_char first[] = "<p>first gzip member</p>";
    static const u_char second[] = "<p>second gzip member</p>";
    u_char compressed[512];
    size_t first_len;
    size_t second_len;
    ngx_buf_t in_buf;
    ngx_chain_t in;
    ngx_chain_t *out;
    ngx_http_request_t r;
    ngx_int_t rc;

    first_len = gzip_compress(first, sizeof(first) - 1,
                               compressed, sizeof(compressed));
    second_len = gzip_compress(second, sizeof(second) - 1,
                                compressed + first_len,
                                sizeof(compressed) - first_len);

    init_request(&r);
    in = make_chain(compressed, first_len + second_len, &in_buf);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP, &in, &out);

    TEST_ASSERT(rc == NGX_OK,
                "concatenated gzip members should still succeed");
    TEST_ASSERT(out != NULL && out->buf != NULL,
                "concatenated gzip should produce output");
    TEST_ASSERT((size_t)(out->buf->last - out->buf->pos)
                == sizeof(first) - 1 + sizeof(second) - 1,
                "concatenated gzip output length should include both members");
    TEST_ASSERT(memcmp(out->buf->pos, first, sizeof(first) - 1) == 0,
                "first gzip member output should match");
    TEST_ASSERT(memcmp(out->buf->pos + sizeof(first) - 1,
                       second, sizeof(second) - 1) == 0,
                "second gzip member output should match");
    release_output(out);
    TEST_ASSERT(g_heap_alloc_count == g_heap_free_count,
                "concatenated gzip output should be released exactly once");
}

/*
 * Test brotli dispatch returns NGX_DECLINED when brotli is not
 * compiled in (the #else branch of decompress_brotli).
 */
static void
test_brotli_not_compiled_in(void)
{
    ngx_http_request_t r;
    ngx_chain_t *out;
    ngx_int_t rc;

    init_request(&r);
    out = NULL;

    rc = ngx_http_markdown_decompress(&r,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI, NULL, &out);
    TEST_ASSERT(rc == NGX_DECLINED,
                "brotli without compiled support should decline");
}

int
main(void)
{
    test_chain_helpers_boundaries();
    test_chain_size_invalid_reversed_buffers();
    test_calc_output_size_boundaries();
    test_detect_compression_variants();
    test_dispatch_non_decompressing_cases();
    test_gzip_success();
    test_gzip_concatenated_members();
    test_gzip_concatenated_truncated_second_member();
    test_gzip_concatenated_members_share_budget();
    test_gzip_empty_later_member_at_exact_budget();
    test_gzip_later_member_grows_at_member_boundary();
    test_deflate_success();
    test_gzip_empty_input_error();
    test_gzip_allocation_and_budget_setup_errors();
    test_gzip_output_chain_allocation_errors();
    test_gzip_format_error();
    test_gzip_budget_exceeded();
    test_grow_output_buffer_direct();
    test_handle_inflate_stall_direct();
    test_gzip_buffer_growth();
    test_gzip_budget_exceeded_during_growth();
    test_gzip_truncated_input();
    test_deflate_zlib_trailing_data();
    test_deflate_raw_trailing_data();
    test_deflate_clean_still_succeeds();
    test_gzip_concatenated_not_regressed();
    test_brotli_not_compiled_in();

    TEST_PASS("decompression_production: all tests passed");
    return 0;
}
