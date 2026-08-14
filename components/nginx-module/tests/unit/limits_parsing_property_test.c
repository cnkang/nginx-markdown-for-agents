/*
 * Test: limits_parsing_property
 *
 * Property-based test for unified markdown_limits key=value parsing
 * round-trip (Property 1).
 *
 * Feature: 62-final-pre-v1-breaking-freeze-fixed
 * Property 1: Unified limits key=value parsing round-trip
 *
 * Validates: Requirements 2.3
 *
 * For any valid combination of the 8 limit keys (conversion_timeout,
 * parser_timeout, conversion_memory, parser_memory, streaming_buffer,
 * decompressed_size, decompression_ratio, max_inflight) with values
 * within allowed ranges:
 *   - Parsing produces the same values as specified
 *   - Unmentioned keys retain their UNSET sentinel values
 *   - Duplicate keys are rejected
 *   - Unknown keys are rejected
 *   - Zero values are rejected
 *   - Overflow values are rejected
 *
 * Test approach (pseudo-random sequences):
 *   Generate many valid key subsets with random in-range values,
 *   invoke the limits handler, and verify the result struct matches.
 */

#include "../include/test_common.h"

#include <ctype.h>
#include <errno.h>
#include <stdarg.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_OK
#define NGX_OK 0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR -1
#endif
#ifndef NGX_CONF_OK
static char ngx_conf_ok_val[] = "OK";
#define NGX_CONF_OK ngx_conf_ok_val
#endif
#ifndef NGX_CONF_ERROR
static char ngx_conf_error_val[] = "ERROR";
#define NGX_CONF_ERROR ngx_conf_error_val
#endif

#ifndef NGX_CONF_UNSET
#define NGX_CONF_UNSET (-1)
#endif
#ifndef NGX_CONF_UNSET_UINT
#define NGX_CONF_UNSET_UINT ((ngx_uint_t) -1)
#endif
#ifndef NGX_CONF_UNSET_SIZE
#define NGX_CONF_UNSET_SIZE ((size_t) -1)
#endif
#ifndef NGX_CONF_UNSET_MSEC
#define NGX_CONF_UNSET_MSEC ((ngx_msec_t) -1)
#endif
#ifndef NGX_CONF_UNSET_PTR
#define NGX_CONF_UNSET_PTR ((void *) -1)
#endif

#ifndef NGX_LOG_EMERG
#define NGX_LOG_EMERG 1
#endif
#ifndef NGX_LOG_DEBUG
#define NGX_LOG_DEBUG 2
#endif
#ifndef NGX_LOG_INFO
#define NGX_LOG_INFO 3
#endif

#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE ((size_t) -1)
#endif

#ifndef NGX_HTTP_SRV_CONF
#define NGX_HTTP_SRV_CONF  0x02000000
#endif
#ifndef NGX_HTTP_LOC_CONF
#define NGX_HTTP_LOC_CONF  0x04000000
#endif

typedef intptr_t ngx_err_t;

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

struct ngx_pool_s {
    int dummy;
};

struct ngx_array_s {
    void       *elts;
    ngx_uint_t  nelts;
    size_t      size;
    ngx_uint_t  nalloc;
    ngx_pool_t *pool;
};

struct ngx_http_request_s {
    int dummy;
};

struct ngx_http_complex_value_s {
    ngx_str_t  value;
};

struct ngx_conf_s {
    ngx_pool_t  *pool;
    ngx_array_t *args;
    ngx_uint_t   cmd_type;
};

struct ngx_command_s {
    ngx_str_t  name;
    void      *post;
};

struct ngx_module_s {
    int dummy;
};

typedef struct {
    ngx_int_t (*handler)(ngx_http_request_t *r);
} ngx_http_core_loc_conf_t;

/* Global module symbols required by the config headers. */
ngx_module_t ngx_http_markdown_filter_module;
ngx_module_t ngx_http_core_module;

/* ----------------------------------------------------------------
 * NGINX primitive stubs
 * ---------------------------------------------------------------- */

static ngx_int_t
ngx_ascii_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    size_t i;

    for (i = 0; i < n; i++) {
        u_char c1 = (u_char) tolower((unsigned char) s1[i]);
        u_char c2 = (u_char) tolower((unsigned char) s2[i]);
        if (c1 != c2) {
            return (ngx_int_t) c1 - (ngx_int_t) c2;
        }
    }
    return 0;
}

static ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    return ngx_ascii_strncasecmp(s1, s2, n);
}

static u_char *
ngx_strlchr(u_char *p, u_char *last, u_char c)
{
    while (p < last) {
        if (*p == c) {
            return p;
        }
        p++;
    }
    return NULL;
}

static void
ngx_memzero(void *p, size_t n)
{
    memset(p, 0, n);
}

/* ----------------------------------------------------------------
 * ngx_conf_log_error stub (captures error for assertion)
 * ---------------------------------------------------------------- */

static char g_conf_log_buf[1024];

static void
ngx_conf_log_error(ngx_uint_t level, ngx_conf_t *cf, ngx_err_t err,
    const char *fmt, ...)
{
    va_list  ap;
    char    *p;
    char    *end;

    UNUSED(level);
    UNUSED(cf);
    UNUSED(err);

    p = g_conf_log_buf;
    end = g_conf_log_buf + sizeof(g_conf_log_buf) - 1;

    va_start(ap, fmt);
    while (*fmt && p < end) {
        if (*fmt == '%' && *(fmt + 1) == 'V') {
            ngx_str_t *s = va_arg(ap, ngx_str_t *);
            size_t n = (size_t) (end - p);
            if (n > s->len) {
                n = s->len;
            }
            memcpy(p, s->data, n);
            p += n;
            fmt += 2;
        } else if (*fmt == '%' && *(fmt + 1) == 's') {
            const char *s = va_arg(ap, const char *);
            size_t slen = strlen(s);
            size_t n = (size_t) (end - p);
            if (n > slen) {
                n = slen;
            }
            memcpy(p, s, n);
            p += n;
            fmt += 2;
        } else if (*fmt == '%' && (*(fmt + 1) == 'u'
                   && *(fmt + 2) == 'i'))
        {
            ngx_uint_t val = va_arg(ap, ngx_uint_t);
            size_t available = (size_t) (end - p);
            int written;

            if (available != 0) {
                written = snprintf(p, available, "%lu", (unsigned long) val);
                if (written > 0) {
                    p += (size_t) written >= available
                        ? available - 1 : (size_t) written;
                }
            }
            fmt += 3;
        } else {
            *p++ = *fmt++;
        }
    }
    va_end(ap);
    *p = '\0';
}

/* Stubs that the config header references but we don't need. */
static ngx_http_core_loc_conf_t *g_clcf;
static ngx_http_markdown_main_conf_t g_main_conf;
static ngx_uint_t g_diagnostics_recording_requested;

static ngx_int_t
ngx_http_markdown_metrics_handler(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

static ngx_int_t
ngx_http_markdown_diagnostics_handler(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

void
ngx_http_markdown_diagnostics_enable_recording(void)
{
    g_diagnostics_recording_requested = 1;
}

static void *
ngx_http_conf_get_module_loc_conf(ngx_conf_t *cf, ngx_module_t module)
{
    UNUSED(cf);
    UNUSED(module);
    return g_clcf;
}

static void *
ngx_http_conf_get_module_main_conf(ngx_conf_t *cf, ngx_module_t module)
{
    UNUSED(cf);
    UNUSED(module);
    return &g_main_conf;
}

typedef struct {
    ngx_conf_t                 *cf;
    ngx_str_t                  *value;
    ngx_http_complex_value_t   *complex_value;
} ngx_http_compile_complex_value_t;

#define NGX_HTTP_MAIN_CONF  0x0001

typedef struct {
    void (*handler)(void *data);
    void *data;
} ngx_pool_cleanup_t;

#define TRUSTED_PROXIES_PUSH_OK 0

static ngx_int_t
ngx_http_compile_complex_value(
    ngx_http_compile_complex_value_t *ccv)
{
    UNUSED(ccv);
    return NGX_OK;
}

static void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

static void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    void *p = malloc(size);
    if (p != NULL) {
        memset(p, 0, size);
    }
    return p;
}

static ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *pool, size_t size)
{
    static ngx_pool_cleanup_t cln;
    UNUSED(pool);
    UNUSED(size);
    memset(&cln, 0, sizeof(cln));
    return &cln;
}

/* Rust FFI stubs required by the config header. */
void markdown_trusted_proxies_free(void *handle)
{
    UNUSED(handle);
}

void *markdown_trusted_proxies_new(void)
{
    return NULL;
}

int markdown_trusted_proxies_push(void *set, u_char *data, size_t len)
{
    UNUSED(set);
    UNUSED(data);
    UNUSED(len);
    return TRUSTED_PROXIES_PUSH_OK;
}

static ngx_array_t *
ngx_array_create(ngx_pool_t *pool, ngx_uint_t n, size_t size)
{
    UNUSED(pool);
    UNUSED(n);
    UNUSED(size);
    static ngx_array_t arr;
    memset(&arr, 0, sizeof(arr));
    return &arr;
}

static void *
ngx_array_push(ngx_array_t *a)
{
    UNUSED(a);
    static u_char buf[256];
    return buf;
}

/* ----------------------------------------------------------------
 * Include the production config handlers header (contains the
 * limits parsing functions we're testing)
 * ---------------------------------------------------------------- */
#include "../../src/ngx_http_markdown_config_handlers_impl.h"

/* ----------------------------------------------------------------
 * Simple PRNG (xorshift32) for deterministic pseudo-random sequences
 * ---------------------------------------------------------------- */

static unsigned int g_prng_state = 42;

static unsigned int
prng_next(void)
{
    g_prng_state ^= g_prng_state << 13;
    g_prng_state ^= g_prng_state >> 17;
    g_prng_state ^= g_prng_state << 5;
    return g_prng_state;
}

static void
prng_seed(unsigned int seed)
{
    g_prng_state = seed ? seed : 1;
}

/* ----------------------------------------------------------------
 * Helper: build an ngx_str_t argument array for the limits handler
 *
 * The handler expects cf->args to contain:
 *   argv[0] = directive name ("markdown_limits")
 *   argv[1..n] = key=value tokens
 * ---------------------------------------------------------------- */

#define MAX_ARGS 10

static ngx_str_t  g_args[MAX_ARGS];
static ngx_array_t g_args_array;
static ngx_pool_t  g_pool;
static ngx_conf_t  g_cf;
static ngx_command_t g_cmd;
static u_char g_cmd_name[] = "markdown_limits";

static void
setup_conf_context(ngx_uint_t nargs)
{
    g_args_array.elts = g_args;
    g_args_array.nelts = nargs;
    g_args_array.size = sizeof(ngx_str_t);
    g_args_array.nalloc = MAX_ARGS;
    g_args_array.pool = &g_pool;

    g_cf.pool = &g_pool;
    g_cf.args = &g_args_array;
    g_cf.cmd_type = 0;

    g_cmd.name.data = g_cmd_name;
    g_cmd.name.len = sizeof(g_cmd_name) - 1;
    g_cmd.post = NULL;

    /* argv[0] is the directive name */
    g_args[0].data = g_cmd_name;
    g_args[0].len = sizeof(g_cmd_name) - 1;

    g_conf_log_buf[0] = '\0';
}

/* ----------------------------------------------------------------
 * Helper: initialize conf limits to UNSET sentinel values
 * ---------------------------------------------------------------- */

static void
init_limits_unset(ngx_http_markdown_conf_t *mcf)
{
    memset(mcf, 0, sizeof(*mcf));
    mcf->limits.conversion_timeout = NGX_CONF_UNSET_MSEC;
    mcf->limits.parser_timeout = NGX_CONF_UNSET_MSEC;
    mcf->limits.conversion_memory = NGX_CONF_UNSET_SIZE;
    mcf->limits.parser_memory = NGX_CONF_UNSET_SIZE;
    mcf->limits.streaming_buffer = NGX_CONF_UNSET_SIZE;
    mcf->limits.decompressed_size = NGX_CONF_UNSET_SIZE;
    mcf->limits.decompression_ratio = NGX_CONF_UNSET_UINT;
    mcf->limits.max_inflight = NGX_CONF_UNSET_UINT;
}

/* ----------------------------------------------------------------
 * Value generation helpers
 * ---------------------------------------------------------------- */

/*
 * Generate a random duration in milliseconds within [1, 3600000].
 */
static ngx_msec_t
random_duration_ms(void)
{
    return (ngx_msec_t) ((prng_next() % 3600000) + 1);
}

/*
 * Generate a random size within [65536, 1073741824].
 */
static size_t
random_size(void)
{
    /* Produce value in [64k, 1g] */
    size_t range = NGX_HTTP_MARKDOWN_LIMITS_SIZE_MAX
                 - NGX_HTTP_MARKDOWN_LIMITS_SIZE_MIN + 1;
    return NGX_HTTP_MARKDOWN_LIMITS_SIZE_MIN
         + (size_t)(prng_next() % range);
}

/*
 * Generate a random ratio within [1, 10000].
 */
static ngx_uint_t
random_ratio(void)
{
    return (ngx_uint_t) ((prng_next() % 10000) + 1);
}

/*
 * Generate a random inflight count within [1, 65535].
 */
static ngx_uint_t
random_inflight(void)
{
    return (ngx_uint_t) ((prng_next() % 65535) + 1);
}

/* ----------------------------------------------------------------
 * Format helpers: produce "key=value" strings
 * ---------------------------------------------------------------- */

static char g_arg_bufs[8][128];

static void
format_duration_arg(int idx, const char *key, ngx_msec_t ms)
{
    if (ms >= 1000 && (ms % 1000) == 0) {
        snprintf(g_arg_bufs[idx], sizeof(g_arg_bufs[idx]),
                 "%s=%lus", key, (unsigned long)(ms / 1000));
    } else {
        snprintf(g_arg_bufs[idx], sizeof(g_arg_bufs[idx]),
                 "%s=%lums", key, (unsigned long)ms);
    }
}

static void
format_size_arg(int idx, const char *key, size_t sz)
{
    if (sz >= (1024 * 1024) && (sz % (1024 * 1024)) == 0) {
        snprintf(g_arg_bufs[idx], sizeof(g_arg_bufs[idx]),
                 "%s=%lum", key, (unsigned long)(sz / (1024*1024)));
    } else if (sz >= 1024 && (sz % 1024) == 0) {
        snprintf(g_arg_bufs[idx], sizeof(g_arg_bufs[idx]),
                 "%s=%luk", key, (unsigned long)(sz / 1024));
    } else {
        snprintf(g_arg_bufs[idx], sizeof(g_arg_bufs[idx]),
                 "%s=%lu", key, (unsigned long)sz);
    }
}

static void
format_uint_arg(int idx, const char *key, ngx_uint_t val)
{
    snprintf(g_arg_bufs[idx], sizeof(g_arg_bufs[idx]),
             "%s=%lu", key, (unsigned long)val);
}

/* ----------------------------------------------------------------
 * Property 1a: Valid combinations parse to expected values;
 *              unmentioned keys retain UNSET sentinel.
 *
 * For each iteration:
 *   - Choose a random subset of the 8 keys (bitmask 1..255)
 *   - Generate random values in allowed ranges for chosen keys
 *   - Build the args array and invoke the handler
 *   - Assert specified keys have the generated values
 *   - Assert unspecified keys remain at UNSET
 *
 * Validates: Requirements 2.3
 * ---------------------------------------------------------------- */

#define PROPERTY_ITERATIONS 500

static void
test_property1a_valid_combinations_roundtrip(void)
{
    ngx_http_markdown_conf_t mcf;
    char *rc;
    int iter;

    TEST_SUBSECTION(
        "Property 1a: Valid key=value combinations parse to "
        "expected values; unmentioned keys retain UNSET");

    for (iter = 0; iter < PROPERTY_ITERATIONS; iter++) {
        ngx_msec_t exp_ct, exp_pt;
        size_t     exp_cm, exp_pm, exp_sb, exp_ds;
        ngx_uint_t exp_dr, exp_mi;
        unsigned int mask;
        ngx_uint_t arg_idx;

        prng_seed((unsigned int)(iter + 1));
        mask = (prng_next() % 255) + 1; /* at least one key */

        exp_ct = exp_pt = 0;
        exp_cm = exp_pm = exp_sb = exp_ds = 0;
        exp_dr = exp_mi = 0;
        arg_idx = 1; /* argv[0] is directive name */

        if (mask & 0x01) {
            exp_ct = random_duration_ms();
            format_duration_arg((int)arg_idx - 1,
                "conversion_timeout", exp_ct);
            g_args[arg_idx].data = (u_char *)g_arg_bufs[arg_idx-1];
            g_args[arg_idx].len = strlen(g_arg_bufs[arg_idx-1]);
            arg_idx++;
        }
        if (mask & 0x02) {
            exp_pt = random_duration_ms();
            format_duration_arg((int)arg_idx - 1,
                "parser_timeout", exp_pt);
            g_args[arg_idx].data = (u_char *)g_arg_bufs[arg_idx-1];
            g_args[arg_idx].len = strlen(g_arg_bufs[arg_idx-1]);
            arg_idx++;
        }
        if (mask & 0x04) {
            exp_cm = random_size();
            format_size_arg((int)arg_idx - 1,
                "conversion_memory", exp_cm);
            g_args[arg_idx].data = (u_char *)g_arg_bufs[arg_idx-1];
            g_args[arg_idx].len = strlen(g_arg_bufs[arg_idx-1]);
            arg_idx++;
        }
        if (mask & 0x08) {
            exp_pm = random_size();
            format_size_arg((int)arg_idx - 1,
                "parser_memory", exp_pm);
            g_args[arg_idx].data = (u_char *)g_arg_bufs[arg_idx-1];
            g_args[arg_idx].len = strlen(g_arg_bufs[arg_idx-1]);
            arg_idx++;
        }
        if (mask & 0x10) {
            exp_sb = random_size();
            format_size_arg((int)arg_idx - 1,
                "streaming_buffer", exp_sb);
            g_args[arg_idx].data = (u_char *)g_arg_bufs[arg_idx-1];
            g_args[arg_idx].len = strlen(g_arg_bufs[arg_idx-1]);
            arg_idx++;
        }
        if (mask & 0x20) {
            exp_ds = random_size();
            format_size_arg((int)arg_idx - 1,
                "decompressed_size", exp_ds);
            g_args[arg_idx].data = (u_char *)g_arg_bufs[arg_idx-1];
            g_args[arg_idx].len = strlen(g_arg_bufs[arg_idx-1]);
            arg_idx++;
        }

        if (mask & 0x40) {
            exp_dr = random_ratio();
            format_uint_arg((int)arg_idx - 1,
                "decompression_ratio", exp_dr);
            g_args[arg_idx].data = (u_char *)g_arg_bufs[arg_idx-1];
            g_args[arg_idx].len = strlen(g_arg_bufs[arg_idx-1]);
            arg_idx++;
        }
        if (mask & 0x80) {
            exp_mi = random_inflight();
            format_uint_arg((int)arg_idx - 1,
                "max_inflight", exp_mi);
            g_args[arg_idx].data = (u_char *)g_arg_bufs[arg_idx-1];
            g_args[arg_idx].len = strlen(g_arg_bufs[arg_idx-1]);
            arg_idx++;
        }

        init_limits_unset(&mcf);
        setup_conf_context(arg_idx);

        rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
        TEST_ASSERT(rc == NGX_CONF_OK,
            "valid combination must parse successfully");

        /* Verify specified keys have expected values */
        if (mask & 0x01) {
            TEST_ASSERT(mcf.limits.conversion_timeout == exp_ct,
                "conversion_timeout must match generated value");
        } else {
            TEST_ASSERT(
                mcf.limits.conversion_timeout == NGX_CONF_UNSET_MSEC,
                "conversion_timeout must remain UNSET");
        }

        if (mask & 0x02) {
            TEST_ASSERT(mcf.limits.parser_timeout == exp_pt,
                "parser_timeout must match generated value");
        } else {
            TEST_ASSERT(
                mcf.limits.parser_timeout == NGX_CONF_UNSET_MSEC,
                "parser_timeout must remain UNSET");
        }

        if (mask & 0x04) {
            TEST_ASSERT(mcf.limits.conversion_memory == exp_cm,
                "conversion_memory must match generated value");
        } else {
            TEST_ASSERT(
                mcf.limits.conversion_memory == NGX_CONF_UNSET_SIZE,
                "conversion_memory must remain UNSET");
        }

        if (mask & 0x08) {
            TEST_ASSERT(mcf.limits.parser_memory == exp_pm,
                "parser_memory must match generated value");
        } else {
            TEST_ASSERT(
                mcf.limits.parser_memory == NGX_CONF_UNSET_SIZE,
                "parser_memory must remain UNSET");
        }

        if (mask & 0x10) {
            TEST_ASSERT(mcf.limits.streaming_buffer == exp_sb,
                "streaming_buffer must match generated value");
        } else {
            TEST_ASSERT(
                mcf.limits.streaming_buffer == NGX_CONF_UNSET_SIZE,
                "streaming_buffer must remain UNSET");
        }

        if (mask & 0x20) {
            TEST_ASSERT(mcf.limits.decompressed_size == exp_ds,
                "decompressed_size must match generated value");
        } else {
            TEST_ASSERT(
                mcf.limits.decompressed_size == NGX_CONF_UNSET_SIZE,
                "decompressed_size must remain UNSET");
        }

        if (mask & 0x40) {
            TEST_ASSERT(mcf.limits.decompression_ratio == exp_dr,
                "decompression_ratio must match generated value");
        } else {
            TEST_ASSERT(
                mcf.limits.decompression_ratio == NGX_CONF_UNSET_UINT,
                "decompression_ratio must remain UNSET");
        }

        if (mask & 0x80) {
            TEST_ASSERT(mcf.limits.max_inflight == exp_mi,
                "max_inflight must match generated value");
        } else {
            TEST_ASSERT(
                mcf.limits.max_inflight == NGX_CONF_UNSET_UINT,
                "max_inflight must remain UNSET");
        }
    }

    TEST_PASS("Property 1a: 500 valid key subsets parse correctly; "
              "unmentioned keys retain UNSET");
}

/* ----------------------------------------------------------------
 * Property 1b: Duplicate keys are always rejected.
 *
 * For each of the 8 keys, set it twice and verify the handler
 * returns NGX_CONF_ERROR (or a non-OK string indicating error).
 *
 * Validates: Requirements 2.3
 * ---------------------------------------------------------------- */

static void
test_property1b_duplicate_keys_rejected(void)
{
    ngx_http_markdown_conf_t mcf;
    char *rc;
    static u_char conversion_timeout_a[] = "conversion_timeout=5s";
    static u_char conversion_timeout_b[] = "conversion_timeout=10s";
    static u_char parser_timeout_a[] = "parser_timeout=2s";
    static u_char parser_timeout_b[] = "parser_timeout=3s";
    static u_char conversion_memory_a[] = "conversion_memory=128m";
    static u_char conversion_memory_b[] = "conversion_memory=256m";
    static u_char parser_memory_a[] = "parser_memory=128m";
    static u_char parser_memory_b[] = "parser_memory=256m";
    static u_char streaming_buffer_a[] = "streaming_buffer=128k";
    static u_char streaming_buffer_b[] = "streaming_buffer=256k";
    static u_char decompressed_size_a[] = "decompressed_size=1m";
    static u_char decompressed_size_b[] = "decompressed_size=2m";
    static u_char decompression_ratio_a[] = "decompression_ratio=50";
    static u_char decompression_ratio_b[] = "decompression_ratio=100";
    static u_char max_inflight_a[] = "max_inflight=10";
    static u_char max_inflight_b[] = "max_inflight=20";

#define ASSERT_DUPLICATE(first, second, label) \
    do { \
        init_limits_unset(&mcf); \
        setup_conf_context(3); \
        g_args[1].data = (u_char *) (first); \
        g_args[1].len = sizeof(first) - 1; \
        g_args[2].data = (u_char *) (second); \
        g_args[2].len = sizeof(second) - 1; \
        rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf); \
        TEST_ASSERT(rc != NGX_CONF_OK, label); \
    } while (0)

    TEST_SUBSECTION(
        "Property 1b: Duplicate keys are rejected");

    ASSERT_DUPLICATE(conversion_timeout_a, conversion_timeout_b,
        "duplicate conversion_timeout must be rejected");
    ASSERT_DUPLICATE(parser_timeout_a, parser_timeout_b,
        "duplicate parser_timeout must be rejected");
    ASSERT_DUPLICATE(conversion_memory_a, conversion_memory_b,
        "duplicate conversion_memory must be rejected");
    ASSERT_DUPLICATE(parser_memory_a, parser_memory_b,
        "duplicate parser_memory must be rejected");
    ASSERT_DUPLICATE(streaming_buffer_a, streaming_buffer_b,
        "duplicate streaming_buffer must be rejected");
    ASSERT_DUPLICATE(decompressed_size_a, decompressed_size_b,
        "duplicate decompressed_size must be rejected");
    ASSERT_DUPLICATE(decompression_ratio_a, decompression_ratio_b,
        "duplicate decompression_ratio must be rejected");
    ASSERT_DUPLICATE(max_inflight_a, max_inflight_b,
        "duplicate max_inflight must be rejected");

#undef ASSERT_DUPLICATE

    TEST_PASS("Property 1b: all duplicate key cases rejected");
}

/* A second directive in one block must fail even when it names a different
 * key.  The parser's per-invocation duplicate set cannot enforce this alone. */
static void
test_property1b_duplicate_directive_rejected(void)
{
    ngx_http_markdown_conf_t mcf;
    char *rc;
    static u_char first[] = "conversion_timeout=5s";
    static u_char second[] = "parser_timeout=2s";

    TEST_SUBSECTION("Property 1b: repeated markdown_limits is rejected");

    init_limits_unset(&mcf);
    setup_conf_context(2);
    g_args[1].data = first;
    g_args[1].len = sizeof(first) - 1;
    rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "first markdown_limits directive must be accepted");

    setup_conf_context(2);
    g_args[1].data = second;
    g_args[1].len = sizeof(second) - 1;
    rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
    TEST_ASSERT(rc != NULL && strcmp(rc, "is duplicate") == 0,
        "second markdown_limits directive must be rejected");

    TEST_PASS("Property 1b: repeated markdown_limits is rejected");
}

/* ----------------------------------------------------------------
 * Property 1c: Unknown keys are rejected.
 *
 * Validates: Requirements 2.3
 * ---------------------------------------------------------------- */

static void
test_property1c_unknown_keys_rejected(void)
{
    ngx_http_markdown_conf_t mcf;
    char *rc;
    static u_char arg_unknown[] = "bogus_key=123";
    static u_char arg_typo[] = "conversoin_timeout=5s";
    static u_char arg_extra[] = "max_connections=100";

    TEST_SUBSECTION(
        "Property 1c: Unknown keys are rejected");

    init_limits_unset(&mcf);
    setup_conf_context(2);
    g_args[1].data = arg_unknown;
    g_args[1].len = sizeof(arg_unknown) - 1;
    rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
    TEST_ASSERT(rc != NGX_CONF_OK,
        "unknown key 'bogus_key' must be rejected");

    init_limits_unset(&mcf);
    setup_conf_context(2);
    g_args[1].data = arg_typo;
    g_args[1].len = sizeof(arg_typo) - 1;
    rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
    TEST_ASSERT(rc != NGX_CONF_OK,
        "typo key 'conversoin_timeout' must be rejected");

    init_limits_unset(&mcf);
    setup_conf_context(2);
    g_args[1].data = arg_extra;
    g_args[1].len = sizeof(arg_extra) - 1;
    rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
    TEST_ASSERT(rc != NGX_CONF_OK,
        "unknown key 'max_connections' must be rejected");

    TEST_PASS("Property 1c: all unknown keys rejected");
}

/* ----------------------------------------------------------------
 * Property 1d: Zero values are rejected for all keys.
 *
 * Validates: Requirements 2.3
 * ---------------------------------------------------------------- */

static void
test_property1d_zero_values_rejected(void)
{
    ngx_http_markdown_conf_t mcf;
    char *rc;
    static u_char z1[] = "conversion_timeout=0ms";
    static u_char z2[] = "parser_timeout=0s";
    static u_char z3[] = "conversion_memory=0k";
    static u_char z4[] = "parser_memory=0m";
    static u_char z5[] = "streaming_buffer=0g";
    static u_char z6[] = "decompressed_size=0k";
    static u_char z7[] = "decompression_ratio=0";
    static u_char z8[] = "max_inflight=0";

    u_char *zero_args[] = { z1, z2, z3, z4, z5, z6, z7, z8 };
    size_t  zero_lens[] = {
        sizeof(z1)-1, sizeof(z2)-1, sizeof(z3)-1, sizeof(z4)-1,
        sizeof(z5)-1, sizeof(z6)-1, sizeof(z7)-1, sizeof(z8)-1
    };
    size_t  i;

    TEST_SUBSECTION("Property 1d: Zero values rejected");

    for (i = 0; i < 8; i++) {
        init_limits_unset(&mcf);
        setup_conf_context(2);
        g_args[1].data = zero_args[i];
        g_args[1].len = zero_lens[i];
        rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
        TEST_ASSERT(rc != NGX_CONF_OK,
            "zero value must be rejected");
    }

    TEST_PASS("Property 1d: all zero values rejected");
}

/* ----------------------------------------------------------------
 * Property 1e: Overflow values are rejected.
 *
 * Duration: > 1h (3600001ms), Size: > 1g, Ratio: > 10000,
 * Inflight: > 65535.
 *
 * Validates: Requirements 2.3
 * ---------------------------------------------------------------- */

static void
test_property1e_overflow_values_rejected(void)
{
    ngx_http_markdown_conf_t mcf;
    char *rc;
    static u_char o1[] = "conversion_timeout=2h";
    static u_char o2[] = "parser_timeout=3601s";
    static u_char o3[] = "conversion_memory=2g";
    static u_char o4[] = "parser_memory=2g";
    static u_char o5[] = "streaming_buffer=2g";
    static u_char o6[] = "decompressed_size=2g";
    static u_char o7[] = "decompression_ratio=10001";
    static u_char o8[] = "max_inflight=65536";

    u_char *overflow_args[] = { o1, o2, o3, o4, o5, o6, o7, o8 };
    size_t  overflow_lens[] = {
        sizeof(o1)-1, sizeof(o2)-1, sizeof(o3)-1, sizeof(o4)-1,
        sizeof(o5)-1, sizeof(o6)-1, sizeof(o7)-1, sizeof(o8)-1
    };
    size_t  i;

    TEST_SUBSECTION("Property 1e: Overflow values rejected");

    for (i = 0; i < 8; i++) {
        init_limits_unset(&mcf);
        setup_conf_context(2);
        g_args[1].data = overflow_args[i];
        g_args[1].len = overflow_lens[i];
        rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
        TEST_ASSERT(rc != NGX_CONF_OK,
            "overflow value must be rejected");
    }

    TEST_PASS("Property 1e: all overflow values rejected");
}

/* ----------------------------------------------------------------
 * Property 1f: Malformed arguments (no '=') are rejected.
 *
 * Validates: Requirements 2.3
 * ---------------------------------------------------------------- */

static void
test_property1f_malformed_args_rejected(void)
{
    ngx_http_markdown_conf_t mcf;
    char *rc;
    static u_char m1[] = "conversion_timeout";
    static u_char m2[] = "=5s";
    static u_char m3[] = "parser_memory:32m";

    TEST_SUBSECTION("Property 1f: Malformed arguments rejected");

    /* No equals sign */
    init_limits_unset(&mcf);
    setup_conf_context(2);
    g_args[1].data = m1;
    g_args[1].len = sizeof(m1) - 1;
    rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
    TEST_ASSERT(rc != NGX_CONF_OK,
        "argument without '=' must be rejected");

    /* Empty key (starts with '=') */
    init_limits_unset(&mcf);
    setup_conf_context(2);
    g_args[1].data = m2;
    g_args[1].len = sizeof(m2) - 1;
    rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
    TEST_ASSERT(rc != NGX_CONF_OK,
        "argument with empty key must be rejected");

    /* Wrong separator (colon instead of equals) */
    init_limits_unset(&mcf);
    setup_conf_context(2);
    g_args[1].data = m3;
    g_args[1].len = sizeof(m3) - 1;
    rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
    TEST_ASSERT(rc != NGX_CONF_OK,
        "argument with colon separator must be rejected");

    TEST_PASS("Property 1f: all malformed arguments rejected");
}

/* ----------------------------------------------------------------
 * Property 1g: All 8 keys specified simultaneously parse correctly.
 *
 * Validates: Requirements 2.3
 * ---------------------------------------------------------------- */

static void
test_property1g_all_keys_specified(void)
{
    ngx_http_markdown_conf_t mcf;
    char *rc;
    int iter;

    TEST_SUBSECTION(
        "Property 1g: All 8 keys specified parse correctly "
        "(100 random iterations)");

    for (iter = 0; iter < 100; iter++) {
        ngx_msec_t ct, pt;
        size_t     cm, pm, sb, ds;
        ngx_uint_t dr, mi;

        prng_seed((unsigned int)(iter + 5000));
        ct = random_duration_ms();
        pt = random_duration_ms();
        cm = random_size();
        pm = random_size();
        sb = random_size();
        ds = random_size();
        dr = random_ratio();
        mi = random_inflight();

        format_duration_arg(0, "conversion_timeout", ct);
        format_duration_arg(1, "parser_timeout", pt);
        format_size_arg(2, "conversion_memory", cm);
        format_size_arg(3, "parser_memory", pm);
        format_size_arg(4, "streaming_buffer", sb);
        format_size_arg(5, "decompressed_size", ds);
        format_uint_arg(6, "decompression_ratio", dr);
        format_uint_arg(7, "max_inflight", mi);

        init_limits_unset(&mcf);
        setup_conf_context(9); /* 1 name + 8 keys */

        for (int k = 0; k < 8; k++) {
            g_args[k + 1].data = (u_char *)g_arg_bufs[k];
            g_args[k + 1].len = strlen(g_arg_bufs[k]);
        }

        rc = ngx_http_markdown_limits(&g_cf, &g_cmd, &mcf);
        TEST_ASSERT(rc == NGX_CONF_OK,
            "all 8 valid keys must parse successfully");

        TEST_ASSERT(mcf.limits.conversion_timeout == ct,
            "conversion_timeout roundtrip");
        TEST_ASSERT(mcf.limits.parser_timeout == pt,
            "parser_timeout roundtrip");
        TEST_ASSERT(mcf.limits.conversion_memory == cm,
            "conversion_memory roundtrip");
        TEST_ASSERT(mcf.limits.parser_memory == pm,
            "parser_memory roundtrip");
        TEST_ASSERT(mcf.limits.streaming_buffer == sb,
            "streaming_buffer roundtrip");
        TEST_ASSERT(mcf.limits.decompressed_size == ds,
            "decompressed_size roundtrip");
        TEST_ASSERT(mcf.limits.decompression_ratio == dr,
            "decompression_ratio roundtrip");
        TEST_ASSERT(mcf.limits.max_inflight == mi,
            "max_inflight roundtrip");
    }

    TEST_PASS("Property 1g: all 8 keys × 100 iterations pass");
}

/* ----------------------------------------------------------------
 * main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION("Property 1: Unified limits key=value parsing "
                 "round-trip");

    test_property1a_valid_combinations_roundtrip();
    test_property1b_duplicate_keys_rejected();
    test_property1b_duplicate_directive_rejected();
    test_property1c_unknown_keys_rejected();
    test_property1d_zero_values_rejected();
    test_property1e_overflow_values_rejected();
    test_property1f_malformed_args_rejected();
    test_property1g_all_keys_specified();

    printf("\n✓ All Property 1 tests passed.\n");
    return 0;
}
