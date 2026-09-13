/*
 * Test: metrics_access
 *
 * Exercises the production ngx_http_markdown_metrics_check_access() from
 * ngx_http_markdown_metrics_impl.h (non-CORE section) so the loopback
 * boundary is measured against real code:
 *   - NULL connection/sockaddr → 403
 *   - AF_UNIX peer → allowed
 *   - IPv4 loopback: the whole 127.0.0.0/8 range allowed, others denied
 *   - IPv6 ::1 allowed
 *   - IPv4-mapped IPv6 loopback (::ffff:127.x.x.x) allowed — regression
 *     coverage for the double byte-swap: the embedded IPv4 octets are
 *     assembled in host order, so the mask comparison must NOT apply
 *     ntohl() again (little-endian hosts denied legitimate peers before).
 *   - non-loopback IPv6 denied
 *   - method check runs after access; non-GET/HEAD → 405
 */

#include "../include/test_common.h"

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>

#ifndef MARKDOWN_STREAMING_ENABLED
#define MARKDOWN_STREAMING_ENABLED 1
#endif

#include <ngx_config.h>
#include <ngx_core.h>
#include <ngx_http.h>

typedef struct {
    ngx_str_t  key;
    ngx_str_t  value;
    unsigned   hash;
} ngx_table_elt_t;

typedef struct ngx_list_part_s ngx_list_part_t;
struct ngx_list_part_s {
    ngx_table_elt_t  *elts;
    ngx_uint_t        nelts;
    ngx_list_part_t  *next;
};

typedef struct {
    ngx_list_part_t  part;
} ngx_list_t;

struct ngx_log_s {
    int dummy;
};

struct ngx_pool_s {
    ngx_log_t  *log;
};

typedef struct {
    ngx_log_t       *log;
    struct sockaddr *sockaddr;
} ngx_connection_t;

/* struct ngx_buf_s provided by nginx_stubs/ngx_core.h */

struct ngx_chain_s {
    ngx_buf_t    *buf;
    ngx_chain_t  *next;
};

typedef struct {
    ngx_table_elt_t  *content_encoding;
    ngx_list_t        headers;
    ngx_str_t         content_type;
    ngx_uint_t        content_type_len;
    void             *content_type_lowcase;
    ngx_uint_t        content_type_hash;
    time_t            last_modified_time;
    void             *last_modified;
    off_t             content_length_n;
    ngx_uint_t        status;
} ngx_http_headers_out_t;

struct ngx_http_request_s {
    ngx_pool_t                 *pool;
    ngx_connection_t           *connection;
    ngx_uint_t                  method;
    ngx_http_headers_out_t      headers_out;
    unsigned                    header_only:1;
    struct ngx_http_request_s  *main;
};

struct ngx_module_s {
    int dummy;
};

ngx_module_t ngx_http_markdown_filter_module;

#ifndef NGX_HAVE_UNIX_DOMAIN
#define NGX_HAVE_UNIX_DOMAIN 1
#endif
#ifndef NGX_HAVE_INET6
#define NGX_HAVE_INET6 1
#endif
#ifndef NGX_HTTP_GET
#define NGX_HTTP_GET  0x0002
#endif
#ifndef NGX_HTTP_HEAD
#define NGX_HTTP_HEAD 0x0004
#endif
#ifndef NGX_HTTP_POST
#define NGX_HTTP_POST 0x0008
#endif
#ifndef NGX_HTTP_OK
#define NGX_HTTP_OK   200
#endif
#ifndef NGX_HTTP_FORBIDDEN
#define NGX_HTTP_FORBIDDEN 403
#endif
#ifndef NGX_HTTP_NOT_ALLOWED
#define NGX_HTTP_NOT_ALLOWED 405
#endif
#ifndef NGX_HTTP_INTERNAL_SERVER_ERROR
#define NGX_HTTP_INTERNAL_SERVER_ERROR 500
#endif
#ifndef NGX_LOG_CRIT
#define NGX_LOG_CRIT  3
#endif
#ifndef NGX_LOG_ERR
#define NGX_LOG_ERR   1
#endif
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN  2
#endif

static int g_send_header_calls;
static int g_output_filter_calls;
static int g_discard_rc;
static int g_list_push_fail;

ngx_int_t
ngx_http_discard_request_body(ngx_http_request_t *r)
{
    (void) r;
    return g_discard_rc;
}

ngx_int_t
ngx_http_send_header(ngx_http_request_t *r)
{
    (void) r;
    g_send_header_calls++;
    return NGX_OK;
}

ngx_int_t
ngx_http_output_filter(ngx_http_request_t *r, ngx_chain_t *out)
{
    (void) r;
    (void) out;
    g_output_filter_calls++;
    return NGX_OK;
}

void *
ngx_list_push(ngx_list_t *list)
{
    static ngx_table_elt_t  allow_hdr;

    (void) list;
    if (g_list_push_fail) {
        return NULL;
    }
    memset(&allow_hdr, 0, sizeof(allow_hdr));
    return &allow_hdr;
}

ngx_buf_t *
ngx_create_temp_buf(ngx_pool_t *pool, size_t size)
{
    ngx_buf_t  *b;

    (void) pool;
    b = calloc(1, sizeof(ngx_buf_t));
    if (b == NULL) {
        return NULL;
    }
    b->start = malloc(size);
    if (b->start == NULL) {
        free(b);
        return NULL;
    }
    b->pos = b->start;
    b->last = b->start;
    b->end = b->start + size;
    return b;
}

void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return calloc(1, size);
}

void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return malloc(size);
}

void *
ngx_pnalloc(ngx_pool_t *pool, size_t size)
{
    (void) pool;
    return malloc(size);
}

void
ngx_free(void *p)
{
    free(p);
}

void *
ngx_http_get_module_loc_conf(ngx_http_request_t *r, ngx_module_t module)
{
    (void) module;
    return r->headers_out.content_encoding; /* unused by these tests */
}

static u_char *
ngx_slprintf_stub(u_char *buf, u_char *last, const char *fmt, ...)
{
    (void) fmt;
    if (buf == NULL || last == NULL || buf >= last) {
        return buf;
    }
    /* Renderer output is irrelevant to the access tests. */
    *buf = '\0';
    return buf;
}

#define ngx_slprintf ngx_slprintf_stub

#include "../../src/ngx_http_markdown_filter_module.h"

/* Shared metrics state pointer (owned by module_state_impl.h in the
 * production TU): NULL here, so validate_request exercises the
 * fail-closed 500 branch and collect_metrics_snapshot returns a zeroed
 * snapshot. */
static ngx_http_markdown_metrics_t *ngx_http_markdown_metrics = NULL;

#ifndef NGINX_VERSION
#define NGINX_VERSION "test"
#endif

/*
 * The production loopback gate classifies the peer through realip_remote_addr
 * with a fallback to the socket address.  This stub request carries only a
 * socket address, so the stub delegates to the shared socket-address predicate
 * and the access-control cases below keep their expectations.
 */
static ngx_inline ngx_flag_t
ngx_http_markdown_peer_is_loopback(ngx_http_request_t *r)
{
    if (r == NULL || r->connection == NULL) {
        return 0;
    }

    return ngx_http_markdown_sockaddr_is_loopback(r->connection->sockaddr);
}

#include "../../src/ngx_http_markdown_metrics_impl.h"
#undef ngx_slprintf

/* ── Helpers ───────────────────────────────────────────────────── */

static void
init_request(ngx_http_request_t *r, ngx_connection_t *c,
    struct sockaddr_storage *addr, int family, const void *src)
{
    static ngx_pool_t  pool;
    static ngx_log_t   log;

    memset(r, 0, sizeof(*r));
    memset(c, 0, sizeof(*c));
    memset(addr, 0, sizeof(*addr));

    addr->ss_family = family;
    if (src != NULL) {
        size_t  len = (family == AF_INET)
            ? sizeof(struct sockaddr_in)
            : sizeof(struct sockaddr_in6);
        memcpy(addr, src, len);
    }

    c->sockaddr = (struct sockaddr *) addr;
    c->log = &log;
    pool.log = &log;

    r->pool = &pool;
    r->connection = c;
    r->main = r;
    r->method = NGX_HTTP_GET;
}

static void
set_v4(struct sockaddr_in *sin, unsigned a, unsigned b, unsigned c, unsigned d)
{
    memset(sin, 0, sizeof(*sin));
    sin->sin_family = AF_INET;
    sin->sin_addr.s_addr = htonl((a << 24) | (b << 16) | (c << 8) | d);
}

static void
set_v6_mapped(struct sockaddr_in6 *sin6, unsigned a, unsigned b,
    unsigned c, unsigned d)
{
    memset(sin6, 0, sizeof(*sin6));
    sin6->sin6_family = AF_INET6;
    sin6->sin6_addr.s6_addr[10] = 0xff;
    sin6->sin6_addr.s6_addr[11] = 0xff;
    sin6->sin6_addr.s6_addr[12] = (uint8_t) a;
    sin6->sin6_addr.s6_addr[13] = (uint8_t) b;
    sin6->sin6_addr.s6_addr[14] = (uint8_t) c;
    sin6->sin6_addr.s6_addr[15] = (uint8_t) d;
}

static void
set_v6(struct sockaddr_in6 *sin6, const uint8_t bytes[16])
{
    memset(sin6, 0, sizeof(*sin6));
    sin6->sin6_family = AF_INET6;
    memcpy(sin6->sin6_addr.s6_addr, bytes, 16);
}

/* ── Tests ─────────────────────────────────────────────────────── */

static void
test_null_guards(void)
{
    ngx_int_t  rc;

    TEST_SUBSECTION("NULL request/connection/sockaddr denied");

    rc = ngx_http_markdown_metrics_check_access(NULL);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN, "NULL request returns 403");

    ngx_http_request_t      r;
    ngx_connection_t        c;
    struct sockaddr_storage addr;
    init_request(&r, &c, &addr, AF_INET, NULL);
    c.sockaddr = NULL;
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN, "NULL sockaddr returns 403");

    TEST_PASS("NULL guards deny access");
}

static void
test_unix_peer_allowed(void)
{
#if (NGX_HAVE_UNIX_DOMAIN)
    ngx_http_request_t       r;
    ngx_connection_t         c;
    struct sockaddr          unix_addr;
    struct sockaddr_storage  addr;
    ngx_int_t                rc;

    TEST_SUBSECTION("AF_UNIX peer allowed");

    memset(&unix_addr, 0, sizeof(unix_addr));
    unix_addr.sa_family = AF_UNIX;
    memcpy(&addr, &unix_addr, sizeof(unix_addr));
    init_request(&r, &c, &addr, AF_UNIX, NULL);

    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_OK, "AF_UNIX peer is inherently local");

    TEST_PASS("AF_UNIX peer allowed");
#else
    TEST_PASS("AF_UNIX support compiled out");
#endif
}

static void
test_ipv4_loopback_range(void)
{
    ngx_http_request_t       r;
    ngx_connection_t         c;
    struct sockaddr_storage  addr;
    struct sockaddr_in       sin;
    ngx_int_t                rc;

    TEST_SUBSECTION("IPv4 127.0.0.0/8 allowed, others denied");

    set_v4(&sin, 127, 0, 0, 1);
    init_request(&r, &c, &addr, AF_INET, &sin);
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_OK, "127.0.0.1 allowed");

    set_v4(&sin, 127, 255, 0, 9);
    init_request(&r, &c, &addr, AF_INET, &sin);
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_OK, "127.255.0.9 (full /8) allowed");

    set_v4(&sin, 10, 0, 0, 5);
    init_request(&r, &c, &addr, AF_INET, &sin);
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN, "10.0.0.5 denied");

    set_v4(&sin, 126, 255, 255, 254);
    init_request(&r, &c, &addr, AF_INET, &sin);
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN, "126.255.255.254 (below /8) denied");

    TEST_PASS("IPv4 loopback range enforced");
}

static void
test_ipv6_loopback_and_v4mapped(void)
{
#if (NGX_HAVE_INET6)
    ngx_http_request_t       r;
    ngx_connection_t         c;
    struct sockaddr_storage  addr;
    struct sockaddr_in6      sin6;
    ngx_int_t                rc;

    static const uint8_t  loopback[16] = {
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1
    };
    static const uint8_t  remote[16] = {
        0x20, 0x01, 0x0d, 0xb8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1
    };

    TEST_SUBSECTION("IPv6 ::1 and v4-mapped 127.0.0.0/8 allowed");

    set_v6(&sin6, loopback);
    init_request(&r, &c, &addr, AF_INET6, &sin6);
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_OK, "::1 allowed");

    set_v6(&sin6, remote);
    init_request(&r, &c, &addr, AF_INET6, &sin6);
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN, "2001:db8::1 denied");

    /* Regression for the double byte-swap: the octets are assembled in
     * host order, so ::ffff:127.0.0.1 must be accepted on little-endian
     * hosts too. */
    set_v6_mapped(&sin6, 127, 0, 0, 1);
    init_request(&r, &c, &addr, AF_INET6, &sin6);
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_OK, "::ffff:127.0.0.1 allowed");

    set_v6_mapped(&sin6, 127, 255, 0, 9);
    init_request(&r, &c, &addr, AF_INET6, &sin6);
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_OK, "::ffff:127.255.0.9 (v4-mapped /8) allowed");

    set_v6_mapped(&sin6, 10, 0, 0, 5);
    init_request(&r, &c, &addr, AF_INET6, &sin6);
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN, "::ffff:10.0.0.5 denied");

    set_v6_mapped(&sin6, 126, 255, 255, 254);
    init_request(&r, &c, &addr, AF_INET6, &sin6);
    rc = ngx_http_markdown_metrics_check_access(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN,
                "::ffff:126.255.255.254 (below /8) denied");

    TEST_PASS("IPv6 loopback and v4-mapped range enforced");
#else
    TEST_PASS("IPv6 support compiled out");
#endif
}

static void
test_method_after_access(void)
{
    ngx_http_request_t       r;
    ngx_connection_t         c;
    struct sockaddr_storage  addr;
    struct sockaddr_in       sin;
    ngx_int_t                rc;

    TEST_SUBSECTION("method handling follows access control");

    set_v4(&sin, 10, 0, 0, 5);
    init_request(&r, &c, &addr, AF_INET, &sin);
    r.method = NGX_HTTP_POST;
    rc = ngx_http_markdown_metrics_validate_request(&r);
    TEST_ASSERT(rc == NGX_HTTP_FORBIDDEN,
                "remote peer receives 403 before any method handling");

    set_v4(&sin, 127, 0, 0, 1);
    init_request(&r, &c, &addr, AF_INET, &sin);
    r.method = NGX_HTTP_POST;
    rc = ngx_http_markdown_metrics_validate_request(&r);
    TEST_ASSERT(rc == NGX_HTTP_NOT_ALLOWED,
                "local POST receives 405 only after passing access");

    set_v4(&sin, 127, 0, 0, 1);
    init_request(&r, &c, &addr, AF_INET, &sin);
    ngx_http_markdown_metrics = NULL;
    r.method = NGX_HTTP_GET;
    rc = ngx_http_markdown_metrics_validate_request(&r);
    TEST_ASSERT(rc == NGX_HTTP_INTERNAL_SERVER_ERROR,
                "missing shared metrics state fails closed with 500");

    TEST_PASS("access precedes method handling (Rule 68)");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_access Tests\n");
    printf("========================================\n");

    test_null_guards();
    test_unix_peer_allowed();
    test_ipv4_loopback_range();
    test_ipv6_loopback_and_v4mapped();
    test_method_after_access();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
