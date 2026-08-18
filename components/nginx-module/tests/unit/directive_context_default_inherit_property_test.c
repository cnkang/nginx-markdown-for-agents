/*
 * Test: directive_context_default_inherit_property
 *
 * Property 3: Directive context, default, and inheritance contract.
 *
 * For each of the 25 retained active directives, verifies:
 *   1. Context acceptance: command table flags include correct context bits
 *      (H/S/L for most, H-only for trusted_proxies/metrics_shm_size/
 *       dynamic_config/dynamic_config_path/dynconf_dry_run,
 *       L-only for metrics/diagnostics)
 *   2. Default values: when unset, the merge function produces the
 *      documented default
 *   3. Inheritance: a parent block value is inherited by child blocks
 *      unless overridden
 *
 * **Validates: Requirements 2.6, 15.1, 15.10**
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
#define NGX_HTTP_LOC_CONF_OFFSET 0
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
        (conf) = ((prev) == NGX_CONF_UNSET_SIZE) ? (default_value) : (prev); \
    }

#define ngx_conf_merge_msec_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_MSEC) { \
        (conf) = ((prev) == NGX_CONF_UNSET_MSEC) ? (default_value) : (prev); \
    }

#define ngx_conf_merge_uint_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_UINT) { \
        (conf) = ((prev) == NGX_CONF_UNSET_UINT) ? (default_value) : (prev); \
    }

#define ngx_conf_merge_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET) { \
        (conf) = ((prev) == NGX_CONF_UNSET) ? (default_value) : (prev); \
    }

#define ngx_conf_merge_ptr_value(conf, prev, default_value) \
    if ((conf) == NGX_CONF_UNSET_PTR) { \
        (conf) = ((prev) == NGX_CONF_UNSET_PTR) ? (default_value) : (prev); \
    }

#include "../../src/ngx_http_markdown_config_handlers_impl.h"
#include "../../src/ngx_http_markdown_config_directives_impl.h"
#include "../../src/ngx_http_markdown_config_merge_impl.h"

static ngx_pool_t g_pool;

static void
run_merge(ngx_http_markdown_conf_t *child,
    const ngx_http_markdown_conf_t *parent)
{
    (void) ngx_http_markdown_merge_inherited_values(child, parent);
}

/* ================================================================
 * Helper: find a directive by name in the command table
 * ================================================================ */
static ngx_command_t *
find_directive(const char *name)
{
    ngx_command_t *cmd;
    size_t         len;

    len = strlen(name);

    for (cmd = ngx_http_markdown_filter_commands; cmd->name.len != 0; cmd++) {
        if (cmd->name.len == len
            && ngx_strncmp(cmd->name.data, name, len) == 0)
        {
            return cmd;
        }
    }

    return NULL;
}

/* ================================================================
 * Directive context contract table
 *
 * Each entry declares the expected context bits for a directive.
 * ================================================================ */
typedef struct {
    const char *name;
    int         expect_http;    /* 1 if allowed in http {} */
    int         expect_server;  /* 1 if allowed in server {} */
    int         expect_location; /* 1 if allowed in location {} */
} context_contract_t;

static const context_contract_t context_contracts[] = {
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
    /* H-only directives (5 entries) */
    { "markdown_trusted_proxies",             1, 0, 0 },
    { "markdown_metrics_shm_size",            1, 0, 0 },
    { "markdown_dynamic_config",              1, 0, 0 },
    { "markdown_dynamic_config_path",         1, 0, 0 },
    { "markdown_dynconf_dry_run",             1, 0, 0 },
    /* L-only directives (2 entries) */
    { "markdown_metrics",                     0, 0, 1 },
    { "markdown_diagnostics",                 0, 0, 1 },
};

#define CONTEXT_COUNT (sizeof(context_contracts) / sizeof(context_contracts[0]))

/* ================================================================
 * 1. Context acceptance property test
 * ================================================================ */
static void
test_context_acceptance_property(void)
{
    size_t         i;
    ngx_command_t *cmd;
    char           msg[256];

    TEST_SECTION("Property 3.1: Context acceptance");

    TEST_ASSERT(CONTEXT_COUNT == 25,
        "directive context contract table must have exactly 25 entries");

    for (i = 0; i < CONTEXT_COUNT; i++) {
        cmd = find_directive(context_contracts[i].name);

        snprintf(msg, sizeof(msg), "%s should be registered in command table",
            context_contracts[i].name);
        TEST_ASSERT(cmd != NULL, msg);

        if (context_contracts[i].expect_http) {
            snprintf(msg, sizeof(msg),
                "%s should allow http context", context_contracts[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) != 0, msg);
        } else {
            snprintf(msg, sizeof(msg),
                "%s should reject http context", context_contracts[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) == 0, msg);
        }

        if (context_contracts[i].expect_server) {
            snprintf(msg, sizeof(msg),
                "%s should allow server context", context_contracts[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) != 0, msg);
        } else {
            snprintf(msg, sizeof(msg),
                "%s should reject server context", context_contracts[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) == 0, msg);
        }

        if (context_contracts[i].expect_location) {
            snprintf(msg, sizeof(msg),
                "%s should allow location context",
                context_contracts[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) != 0, msg);
        } else {
            snprintf(msg, sizeof(msg),
                "%s should reject location context",
                context_contracts[i].name);
            TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) == 0, msg);
        }
    }

    TEST_PASS("All 25 directives have correct context flags");
}

/* ================================================================
 * 2. Default values property test
 *
 * Creates an unset parent and child conf, runs merge, and verifies
 * the documented default for each directive's stored field.
 * ================================================================ */
static ngx_http_markdown_conf_t *
create_unset_conf(void)
{
    ngx_http_markdown_conf_t *conf;
    conf = calloc(1, sizeof(ngx_http_markdown_conf_t));
    TEST_ASSERT(conf != NULL,
        "create_unset_conf allocation must succeed before initialization");

    conf->enabled = NGX_CONF_UNSET;
    conf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
    conf->enabled_complex = NULL;
    conf->max_size = NGX_CONF_UNSET_SIZE;
    conf->decompress.max_size_explicit = 0;
    conf->timeout = NGX_CONF_UNSET_MSEC;
    conf->on_error = NGX_CONF_UNSET_UINT;
    conf->error_status = NGX_CONF_UNSET_UINT;
    conf->flavor = NGX_CONF_UNSET_UINT;
    conf->token_estimate = NGX_CONF_UNSET;
    conf->front_matter = NGX_CONF_UNSET;
    conf->accept_policy = NGX_CONF_UNSET_UINT;
    conf->policy.auth_policy = NGX_CONF_UNSET_UINT;
    conf->policy.auth_cookies = NGX_CONF_UNSET_PTR;
    conf->policy.generate_etag = NGX_CONF_UNSET;
    conf->policy.conditional_requests = NGX_CONF_UNSET_UINT;
    conf->policy.log_verbosity = NGX_CONF_UNSET_UINT;
    conf->routing.content_types = NGX_CONF_UNSET_PTR;
    conf->decompress.auto_decompress = NGX_CONF_UNSET;
    conf->decompress.max_size = NGX_CONF_UNSET_SIZE;
    conf->decompress.parse_timeout = NGX_CONF_UNSET_MSEC;
    conf->decompress.parser_budget = NGX_CONF_UNSET_SIZE;
    conf->routing.large_body_threshold = NGX_CONF_UNSET_SIZE;
    conf->routing.max_inflight = NGX_CONF_UNSET_UINT;
    conf->ops.diagnostics_enabled = NGX_CONF_UNSET;

    conf->stream.policy = NGX_CONF_UNSET_UINT;
    conf->stream.policy_explicit = -1;
    conf->stream.excluded_types = NGX_CONF_UNSET_PTR;
    conf->stream.budget = NGX_CONF_UNSET_SIZE;
    conf->stream.budget_explicit = -1;

    conf->limits.conversion_timeout = NGX_CONF_UNSET_MSEC;
    conf->limits.parser_timeout = NGX_CONF_UNSET_MSEC;
    conf->limits.conversion_memory = NGX_CONF_UNSET_SIZE;
    conf->limits.parser_memory = NGX_CONF_UNSET_SIZE;
    conf->limits.streaming_buffer = NGX_CONF_UNSET_SIZE;
    conf->limits.decompressed_size = NGX_CONF_UNSET_SIZE;
    conf->limits.decompression_ratio = NGX_CONF_UNSET_UINT;
    conf->limits.max_inflight = NGX_CONF_UNSET_UINT;

    conf->advanced.prune_noise = NGX_CONF_UNSET;
    conf->advanced.prune_selectors = NGX_CONF_UNSET_PTR;
    conf->advanced.prune_protection_selectors = NGX_CONF_UNSET_PTR;
    conf->limits.conversion_memory = NGX_CONF_UNSET_SIZE;
    conf->advanced.dynconf_enabled = NGX_CONF_UNSET;
    conf->advanced.dynconf_path.len = 0;
    conf->advanced.dynconf_path.data = NULL;
    conf->advanced.dynconf_dry_run = NGX_CONF_UNSET;

    return conf;
}

static void
test_default_values_property(void)
{
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;

    TEST_SECTION("Property 3.2: Default values when unset");

    parent = create_unset_conf();
    child = create_unset_conf();
    TEST_ASSERT(parent != NULL && child != NULL,
        "allocation must succeed");

    run_merge(child, parent);

    /* markdown_filter: default off (enabled=0, source=STATIC) */
    TEST_ASSERT(child->enabled == 0,
        "markdown_filter default should be off (0)");
    TEST_ASSERT(child->enabled_source == NGX_HTTP_MARKDOWN_ENABLED_STATIC,
        "markdown_filter source should be STATIC after default merge");

    /* markdown_flavor: default commonmark (0) */
    TEST_ASSERT(child->flavor == 0,
        "markdown_flavor default should be commonmark (0)");

    /* markdown_accept: default strict */
    TEST_ASSERT(child->accept_policy == NGX_HTTP_MARKDOWN_ACCEPT_STRICT,
        "markdown_accept default should be strict");

    /* markdown_token_estimate: default off (0) */
    TEST_ASSERT(child->token_estimate == 0,
        "markdown_token_estimate default should be off");

    /* markdown_front_matter: default off (0) */
    TEST_ASSERT(child->front_matter == 0,
        "markdown_front_matter default should be off");

    /* markdown_auto_decompress: default on (1) */
    TEST_ASSERT(child->decompress.auto_decompress == 1,
        "markdown_auto_decompress default should be on");

    /* markdown_error_policy: default pass */
    TEST_ASSERT(child->on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS,
        "markdown_error_policy default should be pass");

    /* markdown_cache_validation: default ims_only (via conditional) */
    TEST_ASSERT(child->policy.conditional_requests
        == NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE,
        "markdown_cache_validation default resolves via conditional");

    /* markdown_auth_policy: default allow (0) */
    TEST_ASSERT(child->policy.auth_policy == 0,
        "markdown_auth_policy default should be allow (0)");

    /* markdown_auth_cookies: default none (NULL) */
    TEST_ASSERT(child->policy.auth_cookies == NULL,
        "markdown_auth_cookies default should be NULL");

    /* markdown_content_types: default NULL (text/html checked at runtime) */
    TEST_ASSERT(child->routing.content_types == NULL,
        "markdown_content_types default should be NULL");

    /* markdown_streaming: default auto (via stream merge) */
    TEST_ASSERT(child->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_AUTO,
        "markdown_streaming default should be auto");

    /* markdown_stream_excluded_types: default NULL */
    TEST_ASSERT(child->stream.excluded_types == NULL,
        "markdown_stream_excluded_types default should be NULL");

    /* markdown_prune_noise: default on (1) */
    TEST_ASSERT(child->advanced.prune_noise == 1,
        "markdown_prune_noise default should be on");

    /* markdown_prune_selectors: default NULL (built-in nav/footer/aside) */
    TEST_ASSERT(child->advanced.prune_selectors == NULL,
        "markdown_prune_selectors default should be NULL");

    /* markdown_prune_protection_selectors: default NULL */
    TEST_ASSERT(child->advanced.prune_protection_selectors == NULL,
        "markdown_prune_protection_selectors default should be NULL");

    /* markdown_log_verbosity: default info */
    TEST_ASSERT(child->policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_INFO,
        "markdown_log_verbosity default should be info");

    /* markdown_dynamic_config: default off (0) */
    TEST_ASSERT(child->advanced.dynconf_enabled == 0,
        "markdown_dynamic_config default should be off");

    /* markdown_dynamic_config_path: default empty */
    TEST_ASSERT(child->advanced.dynconf_path.len == 0,
        "markdown_dynamic_config_path default should be empty");

    /* markdown_dynconf_dry_run: default off (0) */
    TEST_ASSERT(child->advanced.dynconf_dry_run == 0,
        "markdown_dynconf_dry_run default should be off");

    /* markdown_diagnostics: default off (0) */
    TEST_ASSERT(child->ops.diagnostics_enabled == 0,
        "markdown_diagnostics default should be off");

    /* markdown_limits defaults (8 keys) */
    TEST_ASSERT(child->limits.conversion_timeout
        == NGX_HTTP_MARKDOWN_LIMITS_CONVERSION_TIMEOUT_DEFAULT,
        "limits.conversion_timeout default should be 30s");
    TEST_ASSERT(child->limits.parser_timeout
        == NGX_HTTP_MARKDOWN_LIMITS_PARSER_TIMEOUT_DEFAULT,
        "limits.parser_timeout default should be 10s");

    TEST_ASSERT(child->limits.conversion_memory
        == NGX_HTTP_MARKDOWN_LIMITS_CONVERSION_MEMORY_DEFAULT,
        "limits.conversion_memory default should be 64m");
    TEST_ASSERT(child->limits.parser_memory
        == NGX_HTTP_MARKDOWN_LIMITS_PARSER_MEMORY_DEFAULT,
        "limits.parser_memory default should be 32m");
    TEST_ASSERT(child->limits.streaming_buffer
        == NGX_HTTP_MARKDOWN_LIMITS_STREAMING_BUFFER_DEFAULT,
        "limits.streaming_buffer default should be 2m");
    TEST_ASSERT(child->limits.decompressed_size
        == NGX_HTTP_MARKDOWN_LIMITS_DECOMPRESSED_SIZE_DEFAULT,
        "limits.decompressed_size default should be 10m");
    TEST_ASSERT(child->limits.decompression_ratio
        == NGX_HTTP_MARKDOWN_LIMITS_DECOMPRESSION_RATIO_DEFAULT,
        "limits.decompression_ratio default should be 100");
    TEST_ASSERT(child->limits.max_inflight
        == NGX_HTTP_MARKDOWN_LIMITS_MAX_INFLIGHT_DEFAULT,
        "limits.max_inflight default should be 64");

    free(parent);
    free(child);

    TEST_PASS("All directive defaults match documented contract");
}

/* ================================================================
 * 3. Inheritance property test
 *
 * Sets a value in the parent conf, leaves child unset, runs merge,
 * and confirms child inherits the parent value. Then sets both
 * parent and child to different values and confirms child wins.
 * ================================================================ */
static void
test_inheritance_property(void)
{
    ngx_http_markdown_conf_t *parent;
    ngx_http_markdown_conf_t *child;

    TEST_SECTION("Property 3.3: Parent-to-child inheritance");

    /* --- Test: child inherits flavor from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->flavor = 1;  /* GFM */
    run_merge(child, parent);
    TEST_ASSERT(child->flavor == 1,
        "child should inherit flavor=GFM from parent");
    free(parent);
    free(child);

    /* --- Test: child overrides flavor --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->flavor = 1;  /* GFM */
    child->flavor = 0;   /* commonmark */
    run_merge(child, parent);
    TEST_ASSERT(child->flavor == 0,
        "child flavor override should be preserved");
    free(parent);
    free(child);

    /* --- Test: child inherits token_estimate from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->token_estimate = 1;
    run_merge(child, parent);
    TEST_ASSERT(child->token_estimate == 1,
        "child should inherit token_estimate=on from parent");
    free(parent);
    free(child);

    /* --- Test: child inherits front_matter from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->front_matter = 1;
    run_merge(child, parent);
    TEST_ASSERT(child->front_matter == 1,
        "child should inherit front_matter=on from parent");
    free(parent);
    free(child);

    /* --- Test: child inherits auto_decompress from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->decompress.auto_decompress = 0;  /* off */
    run_merge(child, parent);
    TEST_ASSERT(child->decompress.auto_decompress == 0,
        "child should inherit auto_decompress=off from parent");
    free(parent);
    free(child);

    /* --- Test: child inherits auth_policy from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->policy.auth_policy = 1;  /* deny */
    run_merge(child, parent);
    TEST_ASSERT(child->policy.auth_policy == 1,
        "child should inherit auth_policy=deny from parent");
    free(parent);
    free(child);

    /* --- Test: child inherits log_verbosity from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->policy.log_verbosity = NGX_HTTP_MARKDOWN_LOG_WARN;
    run_merge(child, parent);
    TEST_ASSERT(child->policy.log_verbosity == NGX_HTTP_MARKDOWN_LOG_WARN,
        "child should inherit log_verbosity=warn from parent");
    free(parent);
    free(child);

    /* --- Test: child inherits prune_noise from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->advanced.prune_noise = 0;  /* off */
    run_merge(child, parent);
    TEST_ASSERT(child->advanced.prune_noise == 0,
        "child should inherit prune_noise=off from parent");
    free(parent);
    free(child);

    /* --- Test: child inherits dynconf_enabled from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->advanced.dynconf_enabled = 1;
    run_merge(child, parent);
    TEST_ASSERT(child->advanced.dynconf_enabled == 1,
        "child should inherit dynconf_enabled=on from parent");
    free(parent);
    free(child);

    /* --- Test: child inherits dynconf_dry_run from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->advanced.dynconf_dry_run = 1;
    run_merge(child, parent);
    TEST_ASSERT(child->advanced.dynconf_dry_run == 1,
        "child should inherit dynconf_dry_run=on from parent");
    free(parent);
    free(child);

    /* --- Test: child inherits diagnostics_enabled from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->ops.diagnostics_enabled = 1;
    run_merge(child, parent);
    TEST_ASSERT(child->ops.diagnostics_enabled == 1,
        "child should inherit diagnostics_enabled=on from parent");
    free(parent);
    free(child);

    /* --- Test: child inherits streaming policy from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->stream.policy = NGX_HTTP_MARKDOWN_STREAMING_FORCE;
    run_merge(child, parent);
    TEST_ASSERT(child->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_FORCE,
        "child should inherit streaming policy=force from parent");
    free(parent);
    free(child);

    /* --- Test: child overrides streaming policy --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->stream.policy = NGX_HTTP_MARKDOWN_STREAMING_FORCE;
    child->stream.policy = NGX_HTTP_MARKDOWN_STREAMING_OFF;
    run_merge(child, parent);
    TEST_ASSERT(child->stream.policy == NGX_HTTP_MARKDOWN_STREAMING_OFF,
        "child streaming policy override should be preserved");
    free(parent);
    free(child);

    /* --- Test: child inherits limits.conversion_timeout from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->limits.conversion_timeout = 60000;  /* 60s */
    run_merge(child, parent);
    TEST_ASSERT(child->limits.conversion_timeout == 60000,
        "child should inherit limits.conversion_timeout from parent");
    free(parent);
    free(child);

    /* --- Test: child overrides limits.conversion_timeout --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->limits.conversion_timeout = 60000;
    child->limits.conversion_timeout = 15000;
    run_merge(child, parent);
    TEST_ASSERT(child->limits.conversion_timeout == 15000,
        "child limits.conversion_timeout override should win");
    free(parent);
    free(child);

    /* --- Test: child inherits limits.streaming_buffer from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->limits.streaming_buffer = 4 * 1024 * 1024;  /* 4m */
    run_merge(child, parent);
    TEST_ASSERT(child->limits.streaming_buffer == 4 * 1024 * 1024,
        "child should inherit limits.streaming_buffer from parent");
    free(parent);
    free(child);

    /* --- Test: child inherits limits.max_inflight from parent --- */
    parent = create_unset_conf();
    child = create_unset_conf();
    parent->limits.max_inflight = 128;
    run_merge(child, parent);
    TEST_ASSERT(child->limits.max_inflight == 128,
        "child should inherit limits.max_inflight from parent");
    free(parent);
    free(child);

    TEST_PASS("Inheritance works: parent value inherits, child override wins");
}

/* ================================================================
 * 4. Requirement 15.1: markdown_trusted_proxies http-only enforcement
 * ================================================================ */
static void
test_trusted_proxies_http_only(void)
{
    ngx_command_t *cmd;

    TEST_SECTION("Property 3.4: Req 15.1 trusted_proxies http-only");

    cmd = find_directive("markdown_trusted_proxies");
    TEST_ASSERT(cmd != NULL, "markdown_trusted_proxies must be registered");
    TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) != 0,
        "trusted_proxies must allow http context");
    TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) == 0,
        "trusted_proxies must reject server context (Req 15.1)");
    TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) == 0,
        "trusted_proxies must reject location context (Req 15.1)");
    TEST_ASSERT(cmd->conf == NGX_HTTP_MAIN_CONF_OFFSET,
        "trusted_proxies must use main conf offset");

    TEST_PASS("Req 15.1: trusted_proxies http-only enforced");
}

/* ================================================================
 * 5. Requirement 15.10: dynconf directives context verification
 *
 * Requirement 15.10 specifies that dynconf directives are accepted only
 * in the http context.  Verify the command-table bits directly.
 * ================================================================ */
static void
test_dynconf_context(void)
{
    static const char *dynconf_names[] = {
        "markdown_dynamic_config",
        "markdown_dynamic_config_path",
        "markdown_dynconf_dry_run"
    };
    ngx_command_t *cmd;
    size_t         i;

    TEST_SECTION("Property 3.5: Dynconf directives context");

    for (i = 0; i < 3; i++) {
        cmd = find_directive(dynconf_names[i]);
        TEST_ASSERT(cmd != NULL, "dynconf directive must be registered");
        TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) != 0,
            "dynconf directive must allow http context");
    }

    TEST_PASS("Dynconf directives have expected context flags");
}

/* ================================================================
 * 6. Command table count = exactly 25
 * ================================================================ */
static void
test_command_table_count(void)
{
    ngx_command_t *cmd;
    int            count = 0;

    TEST_SECTION("Property 3.6: Command table has exactly 25 entries");

    for (cmd = ngx_http_markdown_filter_commands; cmd->name.len != 0; cmd++) {
        count++;
    }

    TEST_ASSERT(count == 25,
        "command table must have exactly 25 entries (0.9.2 frozen target)");

    TEST_PASS("Command table count = 25");
}

/* ================================================================
 * main
 * ================================================================ */
int
main(void)
{
    printf("=== Property 3: Directive context/default/inheritance ===\n");

    test_context_acceptance_property();
    test_default_values_property();
    test_inheritance_property();
    test_trusted_proxies_http_only();
    test_dynconf_context();
    test_command_table_count();

    printf("\n=== All Property 3 tests passed ===\n");
    return 0;
}
