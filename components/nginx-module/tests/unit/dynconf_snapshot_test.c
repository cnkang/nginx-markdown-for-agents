/*
 * Test: dynconf_snapshot
 *
 * Validates that ngx_http_markdown_dynconf_snapshot_to_json() returns
 * a complete configuration snapshot with all expected fields.
 *
 * Test cases:
 *   1. Default configuration → all keys present with default values
 *   2. Custom configuration → keys reflect custom values
 *   3. NULL pool → returns NGX_ERROR
 *   4. NULL conf → returns NGX_ERROR
 *
 * Requirement: REQ-0700-OPERABILITY-003
 * Task: E03.3
 */

#include "../include/test_common.h"

#include <ctype.h>
#include <stdarg.h>

#define MARKDOWN_STREAMING_ENABLED 1

#include "../../src/ngx_http_markdown_filter_module.h"

#ifndef NGX_OK
#define NGX_OK 0
#endif
#ifndef NGX_ERROR
#define NGX_ERROR -1
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

typedef intptr_t ngx_err_t;
typedef struct ngx_connection_s ngx_connection_t;

struct ngx_module_s {
    int dummy;
};

struct ngx_pool_s {
    int dummy;
};

struct ngx_array_s {
    ngx_uint_t nelts;
};

struct ngx_log_s {
    int dummy;
};

struct ngx_connection_s {
    ngx_log_t *log;
};

struct ngx_http_request_s {
    ngx_connection_t *connection;
};

struct ngx_http_complex_value_s {
    ngx_str_t  value;
    ngx_int_t  eval_rc;
};

typedef struct ngx_slab_pool_s ngx_slab_pool_t;

struct ngx_slab_pool_s {
    void *data;
};

typedef struct {
    void      *addr;
    ngx_flag_t exists;
} ngx_shm_t;

struct ngx_shm_zone_s {
    ngx_shm_t  shm;
    void      *data;
    ngx_int_t (*init)(ngx_shm_zone_t *zone, void *data);
};

struct ngx_conf_s {
    ngx_pool_t *pool;
};

/* Global module symbol required by the module header. */
ngx_module_t ngx_http_markdown_filter_module;
ngx_str_t ngx_http_markdown_metrics_shm_name = ngx_string("markdown_metrics");
ngx_shm_zone_t *ngx_http_markdown_metrics_shm_zone = NULL;

/* Test-controlled state for allocator stub. */
static ngx_int_t g_palloc_fail;

/*
 * NGINX API stubs for unit test environment.
 */
static void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    if (g_palloc_fail) {
        return NULL;
    }
    return malloc(size);
}

/*
 * Minimal ngx_slprintf stub that handles NGINX format specifiers:
 *   %s  - C string (char *)
 *   %uz - size_t
 *   %M  - ngx_msec_t (unsigned long on most platforms)
 *
 * Rewrites NGINX-specific format specifiers to standard printf ones
 * before forwarding to vsnprintf.
 */
static u_char *
test_vslprintf(u_char *buf, u_char *last, const char *fmt, va_list ap)
{
    char   rewritten[4096];
    size_t fi;
    size_t oi;
    size_t rem;
    int    n;

    if (buf >= last) {
        return buf;
    }

    fi = 0;
    oi = 0;
    while (fmt[fi] != '\0' && oi < sizeof(rewritten) - 8) {
        if (fmt[fi] == '%') {
            size_t start = oi;
            rewritten[oi++] = fmt[fi++];

            /* Copy optional width/flags */
            while (fmt[fi] >= '0' && fmt[fi] <= '9') {
                rewritten[oi++] = fmt[fi++];
            }

            /* Check for NGINX-specific specifiers */
            if (fmt[fi] == 'u' && fmt[fi + 1] == 'z') {
                /* %uz -> %zu (size_t) */
                rewritten[oi++] = 'z';
                rewritten[oi++] = 'u';
                fi += 2;
            } else if (fmt[fi] == 'M') {
                /* %M -> %lu (ngx_msec_t = ngx_uint_t = uintptr_t) */
                rewritten[oi++] = 'l';
                rewritten[oi++] = 'u';
                fi += 1;
            } else if (fmt[fi] == 'u' && fmt[fi + 1] == 'A') {
                /* %uA -> %d (ngx_atomic_uint_t) */
                rewritten[oi++] = 'd';
                fi += 2;
            } else {
                /* Standard specifier, copy as-is */
                rewritten[oi++] = fmt[fi++];
            }
            UNUSED(start);
        } else {
            rewritten[oi++] = fmt[fi++];
        }
    }
    rewritten[oi] = '\0';

    rem = (size_t) (last - buf);
    n = vsnprintf((char *) buf, rem, rewritten, ap);
    if (n < 0) {
        return buf;
    }
    if ((size_t) n >= rem) {
        return last;
    }
    return buf + n;
}

static u_char *
ngx_slprintf(u_char *buf, u_char *last, const char *fmt, ...)
{
    va_list ap;
    u_char *p;

    va_start(ap, fmt);
    p = test_vslprintf(buf, last, fmt, ap);
    va_end(ap);
    return p;
}

/* Include the snapshot implementation directly. */
#include "../../src/ngx_http_markdown_dynconf_snapshot.c"

/* ── Helper: check if a key is present in the output ─────────────── */

static int
output_contains_key(const u_char *buf, size_t len, const char *key)
{
    char   search[256];
    size_t search_len;

    snprintf(search, sizeof(search), "\"%s\":", key);
    search_len = strlen(search);

    for (size_t i = 0; i + search_len <= len; i++) {
        if (memcmp(buf + i, search, search_len) == 0) {
            return 1;
        }
    }
    return 0;
}

/* ── Helper: extract value for a key from the output ─────────────── */

static int
output_contains_key_value(const u_char *buf, size_t len,
    const char *key, const char *expected_value)
{
    char   search[512];
    size_t search_len;

    snprintf(search, sizeof(search), "\"%s\": \"%s\"", key, expected_value);
    search_len = strlen(search);

    for (size_t i = 0; i + search_len <= len; i++) {
        if (memcmp(buf + i, search, search_len) == 0) {
            return 1;
        }
    }
    return 0;
}

/* ── Test 1: Default configuration returns all keys ──────────────── */

static void
test_default_config_all_keys_present(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    u_char                  *out_buf;
    size_t                   out_len;
    ngx_int_t                rc;

    TEST_SUBSECTION("Default config: all keys present with default values");

    memset(&conf, 0, sizeof(conf));
    memset(&pool, 0, sizeof(pool));
    g_palloc_fail = 0;

    /* Set default values matching post-merge state */
    conf.enabled = 0;
    conf.max_size = 10 * 1024 * 1024;
    conf.timeout = 5000;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_PASS;
    conf.flavor = NGX_HTTP_MARKDOWN_FLAVOR_COMMONMARK;
    conf.token_estimate = 0;
    conf.front_matter = 0;
    conf.on_wildcard = 0;
    conf.buffer_chunked = 1;
    conf.auto_decompress = 1;
    conf.decompress_max_size = 10 * 1024 * 1024;
    conf.parse_timeout = 30000;
    conf.parser_budget = 64 * 1024 * 1024;
    conf.large_body_threshold = 0;
    conf.advanced.prune_noise = 1;
    conf.advanced.memory_budget = 0;
    conf.advanced.dynconf_enabled = 0;
    conf.advanced.dynconf_dry_run = 0;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_INFO;
    conf.policy.generate_etag = 1;
    conf.ops.metrics_format = NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO;
    conf.ops.trust_forwarded_headers = 0;
    conf.streaming.engine = NULL;
    conf.streaming.budget = 2 * 1024 * 1024;
    conf.streaming.on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_PASS;
    conf.streaming.shadow = 0;
    conf.streaming.auto_threshold = 32 * 1024;

    rc = ngx_http_markdown_dynconf_snapshot_to_json(&pool, &conf,
        &out_buf, &out_len);
    TEST_ASSERT(rc == NGX_OK, "snapshot_to_json should return NGX_OK");
    TEST_ASSERT(out_buf != NULL, "output buffer should not be NULL");
    TEST_ASSERT(out_len > 0, "output length should be > 0");

    /* Verify all expected keys are present */
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_filter"),
        "should contain markdown_filter key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_max_size"),
        "should contain markdown_max_size key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_timeout"),
        "should contain markdown_timeout key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_on_error"),
        "should contain markdown_on_error key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_flavor"),
        "should contain markdown_flavor key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_token_estimate"),
        "should contain markdown_token_estimate key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_front_matter"),
        "should contain markdown_front_matter key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_on_wildcard"),
        "should contain markdown_on_wildcard key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_buffer_chunked"),
        "should contain markdown_buffer_chunked key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_auto_decompress"),
        "should contain markdown_auto_decompress key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_decompression_budget"),
        "should contain markdown_decompression_budget key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_parse_timeout"),
        "should contain markdown_parse_timeout key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_parser_budget"),
        "should contain markdown_parser_budget key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_prune_noise"),
        "should contain markdown_prune_noise key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_memory_budget"),
        "should contain markdown_memory_budget key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_dynamic_config"),
        "should contain markdown_dynamic_config key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_dynconf_dry_run"),
        "should contain markdown_dynconf_dry_run key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_log_verbosity"),
        "should contain markdown_log_verbosity key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_generate_etag"),
        "should contain markdown_generate_etag key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_metrics_format"),
        "should contain markdown_metrics_format key");
    TEST_ASSERT(output_contains_key(out_buf, out_len,
        "markdown_trust_forwarded_headers"),
        "should contain markdown_trust_forwarded_headers key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_large_body_threshold"),
        "should contain markdown_large_body_threshold key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_streaming_engine"),
        "should contain markdown_streaming_engine key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_streaming_budget"),
        "should contain markdown_streaming_budget key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_streaming_on_error"),
        "should contain markdown_streaming_on_error key");
    TEST_ASSERT(output_contains_key(out_buf, out_len, "markdown_streaming_shadow"),
        "should contain markdown_streaming_shadow key");
    TEST_ASSERT(output_contains_key(out_buf, out_len,
        "markdown_streaming_auto_threshold"),
        "should contain markdown_streaming_auto_threshold key");

    /* Verify default values match expected */
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_filter", "off"),
        "markdown_filter should be 'off'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_on_error", "pass"),
        "markdown_on_error should be 'pass'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_flavor", "commonmark"),
        "markdown_flavor should be 'commonmark'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_token_estimate", "off"),
        "markdown_token_estimate should be 'off'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_front_matter", "off"),
        "markdown_front_matter should be 'off'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_buffer_chunked", "on"),
        "markdown_buffer_chunked should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_auto_decompress", "on"),
        "markdown_auto_decompress should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_prune_noise", "on"),
        "markdown_prune_noise should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_dynamic_config", "off"),
        "markdown_dynamic_config should be 'off'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_dynconf_dry_run", "off"),
        "markdown_dynconf_dry_run should be 'off'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_log_verbosity", "info"),
        "markdown_log_verbosity should be 'info'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_generate_etag", "on"),
        "markdown_generate_etag should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_metrics_format", "auto"),
        "markdown_metrics_format should be 'auto'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_streaming_engine", "auto"),
        "markdown_streaming_engine should be 'auto'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_streaming_on_error", "pass"),
        "markdown_streaming_on_error should be 'pass'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_streaming_shadow", "off"),
        "markdown_streaming_shadow should be 'off'");

    TEST_PASS("Default config: all keys present with correct default values");

    free(out_buf);
}

/* ── Test 2: Custom configuration reflects custom values ─────────── */

static void
test_custom_config_values_reflected(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_pool_t               pool;
    u_char                  *out_buf;
    size_t                   out_len;
    ngx_int_t                rc;

    TEST_SUBSECTION("Custom config: keys reflect custom values");

    memset(&conf, 0, sizeof(conf));
    memset(&pool, 0, sizeof(pool));
    g_palloc_fail = 0;

    /* Set custom values */
    conf.enabled = 1;
    conf.max_size = 20 * 1024 * 1024;
    conf.timeout = 10000;
    conf.on_error = NGX_HTTP_MARKDOWN_ON_ERROR_REJECT;
    conf.flavor = NGX_HTTP_MARKDOWN_FLAVOR_GFM;
    conf.token_estimate = 1;
    conf.front_matter = 1;
    conf.on_wildcard = 1;
    conf.buffer_chunked = 0;
    conf.auto_decompress = 0;
    conf.decompress_max_size = 5 * 1024 * 1024;
    conf.parse_timeout = 15000;
    conf.parser_budget = 32 * 1024 * 1024;
    conf.large_body_threshold = 1024;
    conf.advanced.prune_noise = 0;
    conf.advanced.memory_budget = 16 * 1024 * 1024;
    conf.advanced.dynconf_enabled = 1;
    conf.advanced.dynconf_dry_run = 1;
    conf.policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_DEBUG;
    conf.policy.generate_etag = 0;
    conf.ops.metrics_format = NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS;
    conf.ops.trust_forwarded_headers = 1;
    conf.streaming.engine = NULL;
    conf.streaming.budget = 4 * 1024 * 1024;
    conf.streaming.on_error = NGX_HTTP_MARKDOWN_STREAMING_ON_ERROR_REJECT;
    conf.streaming.shadow = 1;
    conf.streaming.auto_threshold = 64 * 1024;

    rc = ngx_http_markdown_dynconf_snapshot_to_json(&pool, &conf,
        &out_buf, &out_len);
    TEST_ASSERT(rc == NGX_OK, "snapshot_to_json should return NGX_OK");
    TEST_ASSERT(out_buf != NULL, "output buffer should not be NULL");
    TEST_ASSERT(out_len > 0, "output length should be > 0");

    /* Verify custom values are reflected */
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_filter", "on"),
        "markdown_filter should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_on_error", "reject"),
        "markdown_on_error should be 'reject'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_flavor", "gfm"),
        "markdown_flavor should be 'gfm'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_token_estimate", "on"),
        "markdown_token_estimate should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_front_matter", "on"),
        "markdown_front_matter should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_on_wildcard", "on"),
        "markdown_on_wildcard should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_buffer_chunked", "off"),
        "markdown_buffer_chunked should be 'off'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_auto_decompress", "off"),
        "markdown_auto_decompress should be 'off'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_prune_noise", "off"),
        "markdown_prune_noise should be 'off'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_dynamic_config", "on"),
        "markdown_dynamic_config should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_dynconf_dry_run", "on"),
        "markdown_dynconf_dry_run should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_log_verbosity", "debug"),
        "markdown_log_verbosity should be 'debug'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_generate_etag", "off"),
        "markdown_generate_etag should be 'off'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_metrics_format", "prometheus"),
        "markdown_metrics_format should be 'prometheus'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_trust_forwarded_headers", "on"),
        "markdown_trust_forwarded_headers should be 'on'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_streaming_on_error", "reject"),
        "markdown_streaming_on_error should be 'reject'");
    TEST_ASSERT(output_contains_key_value(out_buf, out_len,
        "markdown_streaming_shadow", "on"),
        "markdown_streaming_shadow should be 'on'");

    TEST_PASS("Custom config: all custom values correctly reflected");

    free(out_buf);
}

/* ── Test 3: NULL pool returns NGX_ERROR ─────────────────────────── */

static void
test_null_pool_returns_error(void)
{
    ngx_http_markdown_conf_t conf;
    u_char                  *out_buf;
    size_t                   out_len;
    ngx_int_t                rc;

    TEST_SUBSECTION("NULL pool: returns NGX_ERROR");

    memset(&conf, 0, sizeof(conf));
    out_buf = NULL;
    out_len = 0;

    rc = ngx_http_markdown_dynconf_snapshot_to_json(NULL, &conf,
        &out_buf, &out_len);
    TEST_ASSERT(rc == NGX_ERROR, "NULL pool should return NGX_ERROR");
    TEST_ASSERT(out_buf == NULL, "output buffer should remain NULL");
    TEST_ASSERT(out_len == 0, "output length should remain 0");

    TEST_PASS("NULL pool correctly returns NGX_ERROR");
}

/* ── Test 4: NULL conf returns NGX_ERROR ─────────────────────────── */

static void
test_null_conf_returns_error(void)
{
    ngx_pool_t  pool;
    u_char     *out_buf;
    size_t      out_len;
    ngx_int_t   rc;

    TEST_SUBSECTION("NULL conf: returns NGX_ERROR");

    memset(&pool, 0, sizeof(pool));
    out_buf = NULL;
    out_len = 0;

    rc = ngx_http_markdown_dynconf_snapshot_to_json(&pool, NULL,
        &out_buf, &out_len);
    TEST_ASSERT(rc == NGX_ERROR, "NULL conf should return NGX_ERROR");
    TEST_ASSERT(out_buf == NULL, "output buffer should remain NULL");
    TEST_ASSERT(out_len == 0, "output length should remain 0");

    TEST_PASS("NULL conf correctly returns NGX_ERROR");
}

/* ── Main ─────────────────────────────────────────────────────────── */

int
main(void)
{
    printf("\n========================================\n");
    printf("Dynconf Snapshot Introspection [E03.3]\n");
    printf("========================================\n");

    test_default_config_all_keys_present();
    test_custom_config_values_reflected();
    test_null_pool_returns_error();
    test_null_conf_returns_error();

    printf("\n========================================\n");
    printf("All dynconf snapshot tests passed!\n");
    printf("========================================\n\n");

    return 0;
}
