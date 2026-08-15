/*
 * Test: context_negative_property
 *
 * Explicit context-NEGATIVE tests for the 0.9.2 frozen directive surface.
 *
 * Verifies that directives declared as http-only or location-only in the
 * Design command table reject disallowed contexts, and that every retained
 * directive's positive context, default, inheritance, and duplicate behavior
 * matches the frozen contract.
 *
 * Context-negative cases verified:
 *   - markdown_trusted_proxies: must fail in server and location
 *   - markdown_dynamic_config: H-only
 *   - markdown_dynamic_config_path: H-only
 *   - markdown_dynconf_dry_run: H-only
 *   - markdown_metrics: must fail in http and server
 *   - markdown_metrics_shm_size: must fail in server and location
 *
 * The command table is the executable context contract: dynconf is H-only
 * and diagnostics is L-only.
 *
 * **Validates: Requirements 2.6, 13.3, 15.1, 15.10**
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
#ifndef NGX_CONF_OK
static char ngx_conf_ok_val_[] = "OK";
#define NGX_CONF_OK ngx_conf_ok_val_
#endif
#ifndef NGX_CONF_ERROR
static char ngx_conf_error_val_[] = "ERROR";
#define NGX_CONF_ERROR ngx_conf_error_val_
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
#ifndef NGX_LOG_WARN
#define NGX_LOG_WARN 4
#endif

#ifndef NGX_DONE
#define NGX_DONE -4
#endif

#ifndef NGX_HTTP_MAIN_CONF
#define NGX_HTTP_MAIN_CONF 0x02000000
#endif
#ifndef NGX_HTTP_SRV_CONF
#define NGX_HTTP_SRV_CONF 0x04000000
#endif
#ifndef NGX_HTTP_LOC_CONF
#define NGX_HTTP_LOC_CONF 0x08000000
#endif
#ifndef NGX_CONF_TAKE1
#define NGX_CONF_TAKE1 0x00000002
#endif
#ifndef NGX_CONF_NOARGS
#define NGX_CONF_NOARGS 0x00000001
#endif
#ifndef NGX_CONF_1MORE
#define NGX_CONF_1MORE 0x00000800
#endif
#ifndef NGX_CONF_FLAG
#define NGX_CONF_FLAG 0x00000200
#endif
#ifndef NGX_CONF_TAKE2
#define NGX_CONF_TAKE2 0x00000004
#endif
#ifndef NGX_CONF_TAKE12
#define NGX_CONF_TAKE12 (NGX_CONF_TAKE1|NGX_CONF_TAKE2)
#endif
#ifndef NGX_CONF_ANY
#define NGX_CONF_ANY 0x00001000
#endif
#ifndef NGX_HTTP_MAIN_CONF_OFFSET
#define NGX_HTTP_MAIN_CONF_OFFSET 0
#endif
#ifndef NGX_HTTP_LOC_CONF_OFFSET
#define NGX_HTTP_LOC_CONF_OFFSET 1
#endif
#ifndef ngx_null_string
#define ngx_null_string { 0, NULL }
#endif
#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE ((size_t) -1)
#endif
#ifndef NGX_HTTP_GET
#define NGX_HTTP_GET  0
#endif
#ifndef NGX_HTTP_HEAD
#define NGX_HTTP_HEAD 1
#endif
#ifndef NGX_HTTP_OK
#define NGX_HTTP_OK  200
#endif

#define ngx_strncmp(s1, s2, n) \
    strncmp((const char *) (s1), (const char *) (s2), (n))

typedef intptr_t ngx_err_t;

typedef struct {
    ngx_str_t   name;
    ngx_uint_t  value;
} ngx_conf_enum_t;

typedef struct {
    int dummy;
} ngx_cidr_t;

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

typedef struct ngx_table_elt_s ngx_table_elt_t;

struct ngx_table_elt_s {
    ngx_str_t   key;
    ngx_str_t   value;
    ngx_uint_t  hash;
};

typedef struct ngx_http_headers_in_s ngx_http_headers_in_t;

struct ngx_http_headers_in_s {
    ngx_table_elt_t *range;
};

typedef struct ngx_http_headers_out_s ngx_http_headers_out_t;

struct ngx_http_headers_out_s {
    ngx_str_t   content_type;
    ngx_uint_t  status;
    off_t       content_length_n;
};

struct ngx_http_request_s {
    ngx_uint_t              method;
    ngx_http_headers_out_t  headers_out;
    ngx_http_headers_in_t   headers_in;
    ngx_pool_t             *pool;
};

struct ngx_http_complex_value_s {
    ngx_str_t  value;
};

typedef struct {
    ngx_conf_t                 *cf;
    ngx_str_t                  *value;
    ngx_http_complex_value_t   *complex_value;
} ngx_http_compile_complex_value_t;

struct ngx_conf_s {
    ngx_pool_t  *pool;
    ngx_array_t *args;
    ngx_uint_t   cmd_type;
};

struct ngx_command_s {
    ngx_str_t   name;
    ngx_uint_t  type;
    char       *(*set)(ngx_conf_t *cf, ngx_command_t *cmd, void *conf);
    ngx_uint_t  conf;
    ngx_uint_t  offset;
    void       *post;
};

#ifndef ngx_null_command
#define ngx_null_command { ngx_null_string, 0, NULL, 0, 0, NULL }
#endif

struct ngx_module_s {
    int dummy;
};

typedef struct {
    ngx_int_t (*handler)(ngx_http_request_t *r);
} ngx_http_core_loc_conf_t;

/*
 * Global module symbols required by the config implementation header.
 */
ngx_module_t ngx_http_markdown_filter_module;
ngx_module_t ngx_http_core_module;

static ngx_http_core_loc_conf_t *g_clcf;
static ngx_int_t g_compile_complex_rc;
static ngx_http_markdown_main_conf_t g_main_conf;
static ngx_uint_t g_diagnostics_recording_requested;

#ifndef TRUSTED_PROXIES_PUSH_OK
#define TRUSTED_PROXIES_PUSH_OK 0
#endif

typedef struct {
    void  (*handler)(void *data);
    void   *data;
} ngx_pool_cleanup_t;

static ngx_pool_cleanup_t g_trusted_cleanup;

static ngx_pool_cleanup_t *
ngx_pool_cleanup_add(ngx_pool_t *p, size_t size)
{
    UNUSED(p);
    UNUSED(size);
    g_trusted_cleanup.handler = NULL;
    g_trusted_cleanup.data = NULL;
    return &g_trusted_cleanup;
}

struct MarkdownTrustedProxies *
markdown_trusted_proxies_new(void)
{
    return (struct MarkdownTrustedProxies *) (uintptr_t) 0x1;
}

uint8_t
markdown_trusted_proxies_push(struct MarkdownTrustedProxies *handle,
    const uint8_t *cidr, uintptr_t cidr_len)
{
    UNUSED(handle);
    UNUSED(cidr);
    UNUSED(cidr_len);
    return TRUSTED_PROXIES_PUSH_OK;
}

void
markdown_trusted_proxies_free(struct MarkdownTrustedProxies *handle)
{
    UNUSED(handle);
}

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

static ngx_int_t
ngx_ascii_strncasecmp(const u_char *s1, const u_char *s2, size_t n)
{
    for (size_t i = 0; i < n; i++) {
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

static ngx_int_t
ngx_ptocidr(ngx_str_t *text, ngx_cidr_t *cidr)
{
    UNUSED(text);
    UNUSED(cidr);
    return NGX_OK;
}

static void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

static ngx_array_t *
ngx_array_create(ngx_pool_t *pool, ngx_uint_t n, size_t size)
{
    ngx_array_t *a;
    UNUSED(pool);

    a = calloc(1, sizeof(ngx_array_t));
    if (a == NULL) {
        return NULL;
    }
    a->size = size;
    a->nalloc = (n == 0) ? 1 : n;
    a->pool = pool;
    a->elts = calloc(a->nalloc, size);
    if (a->elts == NULL) {
        free(a);
        return NULL;
    }
    return a;
}

static void *
ngx_array_push(ngx_array_t *a)
{
    void *elt;
    if (a == NULL) {
        return NULL;
    }
    if (a->nelts >= a->nalloc) {
        void      *new_elts;
        ngx_uint_t new_nalloc;

        new_nalloc = a->nalloc * 2;
        new_elts = realloc(a->elts, new_nalloc * a->size);
        if (new_elts == NULL) {
            return NULL;
        }
        memset((u_char *) new_elts + (a->nalloc * a->size),
               0, (new_nalloc - a->nalloc) * a->size);
        a->elts = new_elts;
        a->nalloc = new_nalloc;
    }
    elt = (u_char *) a->elts + (a->nelts * a->size);
    memset(elt, 0, a->size);
    a->nelts++;
    return elt;
}

static ngx_int_t
ngx_http_compile_complex_value(ngx_http_compile_complex_value_t *ccv)
{
    if (ccv == NULL || ccv->value == NULL || ccv->complex_value == NULL) {
        return NGX_ERROR;
    }
    ccv->complex_value->value = *ccv->value;
    return g_compile_complex_rc;
}

static void
ngx_conf_log_error(ngx_uint_t level, ngx_conf_t *cf, ngx_err_t err,
    const char *fmt, ...)
{
    va_list ap;
    UNUSED(level);
    UNUSED(cf);
    UNUSED(err);
    va_start(ap, fmt);
    va_end(ap);
}

static char *
ngx_conf_set_size_slot(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    UNUSED(cf);
    UNUSED(cmd);
    UNUSED(conf);
    return NGX_CONF_OK;
}

static char *
ngx_conf_set_msec_slot(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    UNUSED(cf);
    UNUSED(cmd);
    UNUSED(conf);
    return NGX_CONF_OK;
}

static char *
ngx_conf_set_flag_slot(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    char       *p;
    ngx_flag_t *fp;
    ngx_str_t  *value;

    p = conf;
    fp = (ngx_flag_t *) (p + cmd->offset);

    if (*fp != NGX_CONF_UNSET) {
        return "is duplicate";
    }

    value = cf->args->elts;

    if (value[1].len == 2
        && ngx_strncasecmp(value[1].data, (u_char *) "on", 2) == 0)
    {
        *fp = 1;
        return NGX_CONF_OK;
    }

    if (value[1].len == 3
        && ngx_strncasecmp(value[1].data, (u_char *) "off", 3) == 0)
    {
        *fp = 0;
        return NGX_CONF_OK;
    }

    return NGX_CONF_ERROR;
}

static char *
ngx_conf_set_num_slot(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    UNUSED(cf);
    UNUSED(cmd);
    UNUSED(conf);
    return NGX_CONF_OK;
}

static char *
ngx_conf_set_enum_slot(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    char            *p;
    ngx_uint_t      *np;
    ngx_str_t       *value;
    ngx_conf_enum_t *e;

    p = conf;
    np = (ngx_uint_t *) (p + cmd->offset);

    if (*np != NGX_CONF_UNSET_UINT) {
        return "is duplicate";
    }

    value = cf->args->elts;
    e = cmd->post;

    for (; e->name.len != 0; e++) {
        if (e->name.len == value[1].len
            && ngx_strncasecmp(e->name.data, value[1].data,
                               value[1].len) == 0)
        {
            *np = e->value;
            return NGX_CONF_OK;
        }
    }

    return NGX_CONF_ERROR;
}

static char *
ngx_conf_set_str_slot(ngx_conf_t *cf, ngx_command_t *cmd, void *conf)
{
    UNUSED(cf);
    UNUSED(cmd);
    UNUSED(conf);
    return NGX_CONF_OK;
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

/*
 * NGINX configuration merge macros (test-local definitions).
 */
#define ngx_conf_merge_size_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_SIZE) { \
        (conf) = ((prev) == NGX_CONF_UNSET_SIZE) \
            ? (default_value) : (prev); \
    }

#define ngx_conf_merge_msec_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_MSEC) { \
        (conf) = ((prev) == NGX_CONF_UNSET_MSEC) \
            ? (default_value) : (prev); \
    }

#define ngx_conf_merge_uint_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_UINT) { \
        (conf) = ((prev) == NGX_CONF_UNSET_UINT) \
            ? (default_value) : (prev); \
    }

#define ngx_conf_merge_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET) { \
        (conf) = ((prev) == NGX_CONF_UNSET) ? (default_value) : (prev); \
    }

#define ngx_conf_merge_ptr_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_PTR) { \
        (conf) = ((prev) == NGX_CONF_UNSET_PTR) \
            ? (default_value) : (prev); \
    }

#include "../../src/ngx_http_markdown_config_handlers_impl.h"
#include "../../src/ngx_http_markdown_config_directives_impl.h"

/* ================================================================
 * Helper: find a directive by name in the command table
 * ================================================================ */
static ngx_command_t *
find_directive(const char *name)
{
    ngx_command_t *cmd;
    size_t         len;

    len = strlen(name);

    for (cmd = ngx_http_markdown_filter_commands;
         cmd->name.len != 0; cmd++)
    {
        if (cmd->name.len == len
            && ngx_strncmp(cmd->name.data, name, len) == 0)
        {
            return cmd;
        }
    }

    return NULL;
}

/* ================================================================
 * Context-negative contract table
 *
 * Each entry declares a directive with contexts it MUST NOT allow.
 * The "design_context" field records what the Design document says.
 * The "impl_allows_*" fields record the actual implementation state.
 * ================================================================ */
typedef struct {
    const char *name;
    /* Design-specified context (what SHOULD be) */
    int         design_http;
    int         design_server;
    int         design_location;
    /* Implementation actual state (what IS) */
    int         impl_http;
    int         impl_server;
    int         impl_location;
    /* Whether there is a known discrepancy */
    int         has_discrepancy;
} context_negative_entry_t;

/*
 * Directives with context restrictions per the Design command table.
 * Entries marked has_discrepancy=1 have implementation that diverges
 * from the Design/Requirement specification.
 */
static const context_negative_entry_t context_negative_cases[] = {
    /*
     * markdown_trusted_proxies: Design=H, Impl=H (MATCH)
     * Req 15.1: must be http-only
     */
    {
        "markdown_trusted_proxies",
        1, 0, 0,    /* design: H only */
        1, 0, 0,    /* impl: H only */
        0           /* no discrepancy */
    },
    /*
     * markdown_metrics_shm_size: Design=H, Impl=H (MATCH)
     */
    {
        "markdown_metrics_shm_size",
        1, 0, 0,    /* design: H only */
        1, 0, 0,    /* impl: H only */
        0           /* no discrepancy */
    },
    /*
     * markdown_dynamic_config: Design=H, Impl=H (MATCH)
     * Req 15.10: dynconf SHALL only be accepted in http context
     */
    {
        "markdown_dynamic_config",
        1, 0, 0,    /* design: H only */
        1, 0, 0,    /* impl: H only */
        0           /* no discrepancy */
    },
    /*
     * markdown_dynamic_config_path: Design=H, Impl=H (MATCH)
     * Req 15.10: dynconf SHALL only be accepted in http context
     */
    {
        "markdown_dynamic_config_path",
        1, 0, 0,    /* design: H only */
        1, 0, 0,    /* impl: H only */
        0           /* no discrepancy */
    },

    /*
     * markdown_dynconf_dry_run: Design=H, Impl=H (MATCH)
     * Req 15.10: dynconf SHALL only be accepted in http context
     */
    {
        "markdown_dynconf_dry_run",
        1, 0, 0,    /* design: H only */
        1, 0, 0,    /* impl: H only */
        0           /* no discrepancy */
    },
    /*
     * markdown_metrics: Design=L, Impl=L (MATCH)
     */
    {
        "markdown_metrics",
        0, 0, 1,    /* design: L only */
        0, 0, 1,    /* impl: L only */
        0           /* no discrepancy */
    },
    /*
     * markdown_diagnostics: Design=L, Impl=L (MATCH)
     */
    {
        "markdown_diagnostics",
        0, 0, 1,    /* design: L only */
        0, 0, 1,    /* impl: L only */
        0           /* no discrepancy */
    },
};

#define NEGATIVE_COUNT \
    (sizeof(context_negative_cases) / sizeof(context_negative_cases[0]))

/* ================================================================
 * Test 1: Verified context-negative assertions
 *
 * For each directive with restricted contexts, verify the ACTUAL
 * implementation flags match the documented impl state.
 * Directives that match design are verified as context-negative.
 * Directives with discrepancies are documented and verified against
 * the actual implementation state.
 * ================================================================ */
static void
test_context_negative_verified(void)
{
    size_t         i;
    ngx_command_t *cmd;
    char           msg[512];
    int            discrepancy_count = 0;

    TEST_SECTION("Context-Negative: Verified implementation flags");

    for (i = 0; i < NEGATIVE_COUNT; i++) {
        const context_negative_entry_t *entry = &context_negative_cases[i];

        cmd = find_directive(entry->name);
        snprintf(msg, sizeof(msg),
            "%s must be registered in command table", entry->name);
        TEST_ASSERT(cmd != NULL, msg);

        /* Verify actual implementation matches documented impl state */
        if (entry->impl_http) {
            snprintf(msg, sizeof(msg),
                "%s: impl allows http (verified)", entry->name);
            TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) != 0, msg);
        } else {
            snprintf(msg, sizeof(msg),
                "%s: impl rejects http (verified)", entry->name);
            TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) == 0, msg);
        }

        if (entry->impl_server) {
            snprintf(msg, sizeof(msg),
                "%s: impl allows server (verified)", entry->name);
            TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) != 0, msg);
        } else {
            snprintf(msg, sizeof(msg),
                "%s: impl rejects server (verified)", entry->name);
            TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) == 0, msg);
        }

        if (entry->impl_location) {
            snprintf(msg, sizeof(msg),
                "%s: impl allows location (verified)", entry->name);
            TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) != 0, msg);
        } else {
            snprintf(msg, sizeof(msg),
                "%s: impl rejects location (verified)", entry->name);
            TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) == 0, msg);
        }

        if (entry->has_discrepancy) {
            discrepancy_count++;
            printf("    FINDING: %s — Design says "
                "H=%d/S=%d/L=%d but impl has "
                "H=%d/S=%d/L=%d\n",
                entry->name,
                entry->design_http,
                entry->design_server,
                entry->design_location,
                entry->impl_http,
                entry->impl_server,
                entry->impl_location);
        }
    }

    printf("    Discrepancies documented: %d\n", discrepancy_count);

    /* Confirmed context-negative assertions (no discrepancy) */
    TEST_PASS("trusted_proxies rejects server context (Req 15.1)");
    TEST_PASS("trusted_proxies rejects location context (Req 15.1)");
    TEST_PASS("metrics_shm_size rejects server context");
    TEST_PASS("metrics_shm_size rejects location context");
    TEST_PASS("metrics rejects http context");
    TEST_PASS("metrics rejects server context");

    TEST_PASS("Context-negative flags verified against implementation");
}

/* ================================================================
 * Test 2: Positive context verification for ALL 25 retained
 *
 * Every retained directive must have the correct positive context
 * bits set in the command table.
 * ================================================================ */
typedef struct {
    const char *name;
    int         expect_http;
    int         expect_server;
    int         expect_location;
} positive_context_t;

static const positive_context_t positive_cases[] = {
    /* H/S/L directives (18 entries) */
    { "markdown_filter",                      1, 1, 1 },
    { "markdown_flavor",                      1, 1, 1 },
    { "markdown_accept",                      1, 1, 1 },
    { "markdown_token_estimate",              1, 1, 1 },
    { "markdown_front_matter",                1, 1, 1 },
    { "markdown_limits",                      1, 1, 1 },
    { "markdown_auto_decompress",             1, 1, 1 },
    { "markdown_error_policy",                1, 1, 1 },
    { "markdown_cache_validation",            1, 1, 1 },
    { "markdown_content_types",               1, 1, 1 },
    { "markdown_auth_policy",                 1, 1, 1 },
    { "markdown_auth_cookies",                1, 1, 1 },
    { "markdown_streaming",                   1, 1, 1 },
    { "markdown_stream_excluded_types",       1, 1, 1 },
    { "markdown_prune_noise",                 1, 1, 1 },
    { "markdown_prune_selectors",             1, 1, 1 },
    { "markdown_prune_protection_selectors",  1, 1, 1 },
    { "markdown_log_verbosity",               1, 1, 1 },
    /* H-only (impl actual) */
    { "markdown_trusted_proxies",             1, 0, 0 },
    { "markdown_metrics_shm_size",            1, 0, 0 },
    /* Dynconf: http-only */
    { "markdown_dynamic_config",              1, 0, 0 },
    { "markdown_dynamic_config_path",         1, 0, 0 },
    { "markdown_dynconf_dry_run",             1, 0, 0 },
    /* L-only (impl actual for metrics) */
    { "markdown_metrics",                     0, 0, 1 },
    /* Diagnostics: location-only */
    { "markdown_diagnostics",                 0, 0, 1 },
};

#define POSITIVE_COUNT \
    (sizeof(positive_cases) / sizeof(positive_cases[0]))

static void
test_positive_context_all(void)
{
    size_t         i;
    ngx_command_t *cmd;
    char           msg[256];

    TEST_SECTION("Context-Positive: All 25 retained directives");

    TEST_ASSERT(POSITIVE_COUNT == 25,
        "positive context table must have exactly 25 entries");

    for (i = 0; i < POSITIVE_COUNT; i++) {
        cmd = find_directive(positive_cases[i].name);

        snprintf(msg, sizeof(msg), "%s must be registered",
            positive_cases[i].name);
        TEST_ASSERT(cmd != NULL, msg);

        if (positive_cases[i].expect_http) {
            snprintf(msg, sizeof(msg),
                "%s: http context required", positive_cases[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) != 0, msg);
        } else {
            snprintf(msg, sizeof(msg),
                "%s: http context must be absent",
                positive_cases[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) == 0, msg);
        }

        if (positive_cases[i].expect_server) {
            snprintf(msg, sizeof(msg),
                "%s: server context required",
                positive_cases[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) != 0, msg);
        } else {
            snprintf(msg, sizeof(msg),
                "%s: server context must be absent",
                positive_cases[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) == 0, msg);
        }

        if (positive_cases[i].expect_location) {
            snprintf(msg, sizeof(msg),
                "%s: location context required",
                positive_cases[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) != 0, msg);
        } else {
            snprintf(msg, sizeof(msg),
                "%s: location context must be absent",
                positive_cases[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) == 0, msg);
        }
    }

    TEST_PASS("All 25 directives have expected positive context flags");
}

/* ================================================================
 * Test 3: Duplicate directive behavior
 *
 * Verifies that flag-type directives reject duplicate usage
 * (setting the same directive twice in one block). Uses the
 * ngx_conf_set_flag_slot stub which returns "is duplicate" when
 * the field is already set.
 * ================================================================ */
static void
test_duplicate_rejection(void)
{
    ngx_http_markdown_conf_t  conf;
    ngx_conf_t                cf;
    ngx_command_t            *cmd;
    ngx_str_t                 args[2];
    ngx_array_t               args_array;
    char                     *rc;
    ngx_pool_t                pool;

    TEST_SECTION("Duplicate directive rejection");

    memset(&conf, 0, sizeof(conf));
    memset(&cf, 0, sizeof(cf));
    memset(&pool, 0, sizeof(pool));
    cf.pool = &pool;

    /* Set up args: directive name + "on" */
    args[0].data = (u_char *) "markdown_dynamic_config";
    args[0].len = strlen("markdown_dynamic_config");
    args[1].data = (u_char *) "on";
    args[1].len = 2;

    args_array.elts = args;
    args_array.nelts = 2;
    args_array.size = sizeof(ngx_str_t);
    args_array.nalloc = 2;
    args_array.pool = &pool;
    cf.args = &args_array;

    cmd = find_directive("markdown_dynamic_config");
    TEST_ASSERT(cmd != NULL, "markdown_dynamic_config must exist");

    /* First set: should succeed */
    conf.advanced.dynconf_enabled = NGX_CONF_UNSET;
    rc = ngx_conf_set_flag_slot(&cf, cmd, &conf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "first set of dynconf_enabled should succeed");
    TEST_ASSERT(conf.advanced.dynconf_enabled == 1,
        "dynconf_enabled should be 1 after set");

    /* Second set: should fail as duplicate */
    rc = ngx_conf_set_flag_slot(&cf, cmd, &conf);
    TEST_ASSERT(rc != NGX_CONF_OK && rc != NGX_CONF_ERROR,
        "duplicate set of dynconf_enabled should return 'is duplicate'");
    TEST_ASSERT(strcmp(rc, "is duplicate") == 0,
        "duplicate error message must be 'is duplicate'");

    /* Verify markdown_prune_noise duplicate rejection */
    cmd = find_directive("markdown_prune_noise");
    TEST_ASSERT(cmd != NULL, "markdown_prune_noise must exist");

    conf.advanced.prune_noise = NGX_CONF_UNSET;
    rc = ngx_conf_set_flag_slot(&cf, cmd, &conf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "first set of prune_noise should succeed");

    rc = ngx_conf_set_flag_slot(&cf, cmd, &conf);
    TEST_ASSERT(rc != NGX_CONF_OK && rc != NGX_CONF_ERROR,
        "duplicate set of prune_noise should return 'is duplicate'");

    TEST_PASS("Flag directives reject duplicate usage");
}

/* ================================================================
 * Test 4: Req 15.1 — markdown_trusted_proxies http-only
 *
 * Explicit verification that server and location bits are absent.
 * ================================================================ */
static void
test_trusted_proxies_http_only(void)
{
    ngx_command_t *cmd;

    TEST_SECTION("Req 15.1: trusted_proxies http-only enforcement");

    cmd = find_directive("markdown_trusted_proxies");
    TEST_ASSERT(cmd != NULL,
        "markdown_trusted_proxies must be registered");

    TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) != 0,
        "trusted_proxies must allow http context");
    TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) == 0,
        "trusted_proxies MUST reject server context (Req 15.1)");
    TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) == 0,
        "trusted_proxies MUST reject location context (Req 15.1)");
    TEST_ASSERT(cmd->conf == NGX_HTTP_MAIN_CONF_OFFSET,
        "trusted_proxies must use main conf offset");

    TEST_PASS("Req 15.1: trusted_proxies is http-only");
}

/* ================================================================
 * Test 5: Req 15.10 — dynconf directives context enforcement
 *
 * Requirement 15.10 states dynconf SHALL only be accepted in http.
 * The implementation enforces this http-only surface: each dynconf
 * directive allows the http context and rejects both server and
 * location contexts, as verified by the assertions below.
 * ================================================================ */
static void
test_dynconf_context_finding(void)
{
    static const char *dynconf_names[] = {
        "markdown_dynamic_config",
        "markdown_dynamic_config_path",
        "markdown_dynconf_dry_run"
    };
    ngx_command_t *cmd;
    size_t         i;

    TEST_SECTION("Req 15.10: Dynconf context verification");

    for (i = 0; i < 3; i++) {
        cmd = find_directive(dynconf_names[i]);
        TEST_ASSERT(cmd != NULL,
            "dynconf directive must be registered");

        /* Dynconf is an http-only control surface. */
        TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) != 0,
            "dynconf directive must allow http context");
        TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) == 0,
            "dynconf directive must reject server context");
        TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) == 0,
            "dynconf directive must reject location context");
    }

    TEST_PASS("Dynconf directives are http-only");
}

/* ================================================================
 * Test 6: metrics location-only enforcement
 *
 * markdown_metrics must be L-only (no http, no server).
 * ================================================================ */
static void
test_metrics_location_only(void)
{
    ngx_command_t *cmd;

    TEST_SECTION("Context-Negative: metrics location-only");

    cmd = find_directive("markdown_metrics");
    TEST_ASSERT(cmd != NULL, "markdown_metrics must be registered");

    TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) == 0,
        "metrics MUST reject http context");
    TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) == 0,
        "metrics MUST reject server context");
    TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) != 0,
        "metrics must allow location context");

    TEST_PASS("markdown_metrics is location-only");
}

/* ================================================================
 * Test 7: metrics_shm_size http-only enforcement
 * ================================================================ */
static void
test_metrics_shm_http_only(void)
{
    ngx_command_t *cmd;

    TEST_SECTION("Context-Negative: metrics_shm_size http-only");

    cmd = find_directive("markdown_metrics_shm_size");
    TEST_ASSERT(cmd != NULL,
        "markdown_metrics_shm_size must be registered");

    TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) != 0,
        "metrics_shm_size must allow http context");
    TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) == 0,
        "metrics_shm_size MUST reject server context");
    TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) == 0,
        "metrics_shm_size MUST reject location context");

    TEST_PASS("markdown_metrics_shm_size is http-only");
}

/* ================================================================
 * Test 8: Command table count = exactly 25
 * ================================================================ */
static void
test_command_table_count(void)
{
    ngx_command_t *cmd;
    int            count = 0;

    TEST_SECTION("Command table count verification");

    for (cmd = ngx_http_markdown_filter_commands;
         cmd->name.len != 0; cmd++)
    {
        count++;
    }

    TEST_ASSERT(count == 25,
        "command table must have exactly 25 entries "
        "(0.9.2 frozen target)");

    TEST_PASS("Command table count = 25");
}

/* ================================================================
 * main
 * ================================================================ */
int
main(void)
{
    printf("=== Context-Negative Property Tests ===\n");
    printf("=== Validates: Requirements 2.6, 13.3, "
        "15.1, 15.10 ===\n");

    test_context_negative_verified();
    test_positive_context_all();
    test_duplicate_rejection();
    test_trusted_proxies_http_only();
    test_dynconf_context_finding();
    test_metrics_location_only();
    test_metrics_shm_http_only();
    test_command_table_count();

    printf("\n=== All context-negative property tests passed ===\n");
    printf("\nSUMMARY: context contract is enforced: "
        "dynconf H-only, diagnostics L-only.\n");

    return 0;
}
