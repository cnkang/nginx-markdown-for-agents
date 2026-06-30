/*
 * Test: streaming_config_contract
 *
 * Validates the v0.8.0 streaming configuration directives (streaming configuration directives):
 * - Valid values for each directive (5.1)
 * - v0.8.0 stream_engine_handler direct tests (5.1b)
 * - Invalid values rejected (5.2)
 * - v0.8.0 stream_engine_handler rejection tests (5.2b)
 * - Allocation failure paths (5.2c)
 * - Default inheritance (5.3)
 * - Reserved directive rejected (5.4)
 * - Hard exclusions always present (5.5)
 * - Reserved directive absent from directive inventory (5.6)
 * - Hard exclusions match MIME types with parameters (5.7)
 *
 * Requirements: streaming configuration contract
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
#ifndef NGX_HTTP_PARTIAL_CONTENT
#define NGX_HTTP_PARTIAL_CONTENT 206
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
ngx_strcasecmp(u_char *s1, u_char *s2)
{
    if (s1 == NULL || s2 == NULL) {
        return NGX_ERROR;
    }
    size_t i = 0;
    while (s1[i] != '\0' && s2[i] != '\0') {
        ngx_int_t diff = ngx_ascii_strncasecmp(&s1[i], &s2[i], 1);
        if (diff != 0) {
            return diff;
        }
        i++;
    }
    return (ngx_int_t) tolower((unsigned char) s1[i])
         - (ngx_int_t) tolower((unsigned char) s2[i]);
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

static int g_next_palloc_fails;

static void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    if (g_next_palloc_fails > 0) {
        g_next_palloc_fails--;
        return NULL;
    }
    return malloc(size);
}

static ngx_array_t *
ngx_array_create(ngx_pool_t *pool, ngx_uint_t n, size_t size)
{
    ngx_array_t *a;
    UNUSED(pool);

    if (g_next_palloc_fails > 0) {
        g_next_palloc_fails--;
        return NULL;
    }

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
    UNUSED(cf);
    UNUSED(cmd);
    UNUSED(conf);
    return NGX_CONF_OK;
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

#include "../../src/ngx_http_markdown_config_handlers_impl.h"
#include "../../src/ngx_http_markdown_config_directives_impl.h"

/*
 * Include the production eligibility source so we test the real
 * ngx_http_markdown_stream_type_excluded() instead of a local
 * reimplementation.  All required stubs are defined above.
 */
#include "../../src/ngx_http_markdown_eligibility.c"

static ngx_pool_t g_pool;

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

static ngx_command_t *
stream_engine_directive(void)
{
    ngx_command_t *cmd;

    cmd = find_directive("markdown_streaming_engine");
    TEST_ASSERT(cmd != NULL,
        "markdown_streaming_engine directive should be registered");
    TEST_ASSERT(cmd->set != NULL,
        "markdown_streaming_engine directive should have setter");
    TEST_ASSERT(cmd->offset
            == offsetof(ngx_http_markdown_conf_t, stream.engine),
        "markdown_streaming_engine offset should target stream.engine");
    TEST_ASSERT(cmd->post == ngx_http_markdown_streaming_engine_enum,
        "markdown_streaming_engine post should use production enum table");

    return cmd;
}

static void
test_dynconf_directives_support_published_contexts(void)
{
    static const char *names[] = {
        "markdown_dynamic_config",
        "markdown_dynamic_config_path",
        "markdown_dynconf_dry_run"
    };
    ngx_command_t     *cmd;
    size_t             i;

    TEST_SUBSECTION("dynconf directives support published contexts");

    for (i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        cmd = find_directive(names[i]);
        TEST_ASSERT(cmd != NULL,
            "dynconf directive should be registered");
        TEST_ASSERT((cmd->type & NGX_HTTP_MAIN_CONF) != 0,
            "dynconf directive should allow HTTP context");
        TEST_ASSERT((cmd->type & NGX_HTTP_SRV_CONF) != 0,
            "dynconf directive should allow server context");
        TEST_ASSERT((cmd->type & NGX_HTTP_LOC_CONF) != 0,
            "dynconf directive should allow location context");
    }

    TEST_PASS("dynconf directives preserve all published contexts");
}

static void
set_arg(ngx_str_t *arg, const char *s)
{
    arg->data = (u_char *) (uintptr_t) s;
    arg->len = strlen(s);
}

static void
setup_cf(ngx_conf_t *cf, ngx_array_t *args, ngx_str_t *values,
    ngx_uint_t count)
{
    args->elts = values;
    args->nelts = count;
    args->size = sizeof(ngx_str_t);
    args->nalloc = count;
    args->pool = &g_pool;

    cf->pool = &g_pool;
    cf->args = args;
}

static void
init_conf(ngx_http_markdown_conf_t *mcf)
{
    memset(mcf, 0, sizeof(*mcf));
    mcf->enabled_source = NGX_HTTP_MARKDOWN_ENABLED_UNSET;
    mcf->enabled_complex = NULL;
    mcf->on_error = NGX_CONF_UNSET_UINT;
    mcf->flavor = NGX_CONF_UNSET_UINT;
    mcf->policy.auth_policy = NGX_CONF_UNSET_UINT;
    mcf->policy.auth_cookies = NGX_CONF_UNSET_PTR;
    mcf->content_types = NGX_CONF_UNSET_PTR;
    mcf->policy.conditional_requests = NGX_CONF_UNSET_UINT;
    mcf->policy.log_verbosity = NGX_CONF_UNSET_UINT;
    mcf->stream_types = NGX_CONF_UNSET_PTR;
    mcf->large_body_threshold = NGX_CONF_UNSET_SIZE;
    mcf->ops.metrics_format = NGX_CONF_UNSET_UINT;

    /* v0.8.0 stream config fields */
    mcf->stream.engine = NGX_CONF_UNSET_UINT;
    mcf->stream.threshold = NGX_CONF_UNSET_SIZE;
    mcf->stream.threshold_explicit = -1;
    mcf->stream.precommit_buffer = NGX_CONF_UNSET_SIZE;
    mcf->stream.flush_min = NGX_CONF_UNSET_SIZE;
    mcf->stream.excluded_types = NGX_CONF_UNSET_PTR;
    mcf->stream.on_error = NGX_CONF_UNSET_UINT;
    mcf->stream.on_error_explicit = -1;
    mcf->stream.budget = NGX_CONF_UNSET_SIZE;
    mcf->stream.budget_explicit = -1;
    mcf->stream.shadow = -1;
    mcf->stream.shadow_explicit = -1;
}

/* ================================================================
 * 5.1 Valid values for each directive
 * ================================================================ */
static void
test_valid_values(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[4];
    ngx_command_t             cmd;
    ngx_command_t            *engine_cmd;
    ngx_http_markdown_conf_t mcf;
    const char              *rc;

    TEST_SUBSECTION("Valid values for each directive");

    setup_cf(&cf, &args, values, 2);
    g_compile_complex_rc = NGX_OK;

    /* markdown_streaming_engine: off */
    init_conf(&mcf);
    engine_cmd = stream_engine_directive();
    set_arg(&values[0], "markdown_streaming_engine");
    set_arg(&values[1], "off");
    rc = engine_cmd->set(&cf, engine_cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_engine_handler 'off' should be accepted");
    TEST_ASSERT(mcf.stream.engine == NGX_HTTP_MARKDOWN_STREAM_ENGINE_OFF,
        "stream_engine_handler should set engine to OFF for 'off'");

    /* markdown_streaming_engine: auto */
    init_conf(&mcf);
    set_arg(&values[1], "auto");
    rc = engine_cmd->set(&cf, engine_cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_engine_handler 'auto' should be accepted");

    /* markdown_streaming_engine: on */
    init_conf(&mcf);
    set_arg(&values[1], "on");
    rc = engine_cmd->set(&cf, engine_cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_engine_handler 'on' should be accepted");

    /* markdown_stream_threshold: 1m */
    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_stream_threshold");
    set_arg(&values[0], "markdown_stream_threshold");
    set_arg(&values[1], "1m");
    rc = ngx_http_markdown_stream_threshold_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_threshold '1m' should be accepted");
    TEST_ASSERT(mcf.stream.threshold == 1048576,
        "stream_threshold '1m' should parse to 1048576");

    /* markdown_stream_threshold: 512k */
    init_conf(&mcf);
    set_arg(&values[1], "512k");
    rc = ngx_http_markdown_stream_threshold_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_threshold '512k' should be accepted");
    TEST_ASSERT(mcf.stream.threshold == 512 * 1024,
        "stream_threshold '512k' should parse to 524288");

    /* markdown_stream_precommit_buffer: 256k (parse_size validation) */
    {
        ngx_str_t sz;
        size_t    parsed;

        set_arg(&sz, "256k");
        parsed = ngx_http_markdown_parse_size(&sz);
        TEST_ASSERT(parsed == 256 * 1024,
            "precommit_buffer '256k' should parse to 262144");

        set_arg(&sz, "128k");
        parsed = ngx_http_markdown_parse_size(&sz);
        TEST_ASSERT(parsed == 128 * 1024,
            "precommit_buffer '128k' should parse to 131072");

        set_arg(&sz, "0");
        parsed = ngx_http_markdown_parse_size(&sz);
        TEST_ASSERT(parsed == 0,
            "precommit_buffer '0' is valid (disables replay)");
    }

    /* markdown_stream_flush_min: 16k */
    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_stream_flush_min");
    set_arg(&values[0], "markdown_stream_flush_min");
    set_arg(&values[1], "16k");
    rc = ngx_http_markdown_stream_flush_min_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_flush_min '16k' should be accepted");
    TEST_ASSERT(mcf.stream.flush_min == 16 * 1024,
        "stream_flush_min '16k' should parse to 16384");

    /* markdown_stream_flush_min: 32k */
    init_conf(&mcf);
    set_arg(&values[1], "32k");
    rc = ngx_http_markdown_stream_flush_min_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_flush_min '32k' should be accepted");
    TEST_ASSERT(mcf.stream.flush_min == 32 * 1024,
        "stream_flush_min '32k' should parse to 32768");

    /* markdown_stream_excluded_types: space-separated MIME types */
    init_conf(&mcf);
    setup_cf(&cf, &args, values, 3);
    set_arg(&cmd.name, "markdown_stream_excluded_types");
    set_arg(&values[0], "markdown_stream_excluded_types");
    set_arg(&values[1], "text/csv");
    set_arg(&values[2], "application/xml");
    rc = ngx_http_markdown_stream_excluded_types_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_excluded_types should accept valid MIME types");
    TEST_ASSERT(mcf.stream.excluded_types != NULL,
        "excluded_types array should be allocated");
    TEST_ASSERT(mcf.stream.excluded_types->nelts == 2,
        "excluded_types should have 2 entries");

    TEST_PASS("5.1 Valid values accepted for all directives");
}

/* ================================================================
 * 5.1b v0.8.0 stream_engine_handler direct
 * ================================================================ */
static void
test_stream_engine_handler_valid(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            *cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("v0.8.0 stream_engine_handler direct");

    setup_cf(&cf, &args, values, 2);
    cmd = stream_engine_directive();
    set_arg(&values[0], "markdown_streaming_engine");

    /* off -> STREAM_ENGINE_OFF */
    init_conf(&mcf);
    set_arg(&values[1], "off");
    rc = cmd->set(&cf, cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_engine_handler 'off' should return NGX_CONF_OK");
    TEST_ASSERT(mcf.stream.engine == NGX_HTTP_MARKDOWN_STREAM_ENGINE_OFF,
        "stream_engine_handler 'off' should set engine to OFF (0)");

    /* auto -> STREAM_ENGINE_AUTO */
    init_conf(&mcf);
    set_arg(&values[1], "auto");
    rc = cmd->set(&cf, cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_engine_handler 'auto' should return NGX_CONF_OK");
    TEST_ASSERT(mcf.stream.engine == NGX_HTTP_MARKDOWN_STREAM_ENGINE_AUTO,
        "stream_engine_handler 'auto' should set engine to AUTO (1)");

    /* on -> STREAM_ENGINE_ON */
    init_conf(&mcf);
    set_arg(&values[1], "on");
    rc = cmd->set(&cf, cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_engine_handler 'on' should return NGX_CONF_OK");
    TEST_ASSERT(mcf.stream.engine == NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON,
        "stream_engine_handler 'on' should set engine to ON (2)");

    TEST_PASS("5.1b v0.8.0 stream_engine_handler accepts valid values");
}

/* ================================================================
 * 5.2 Invalid values rejected
 * ================================================================ */
static void
test_invalid_values(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t             cmd;
    ngx_command_t            *engine_cmd;
    ngx_http_markdown_conf_t mcf;
    const char              *rc;

    TEST_SUBSECTION("Invalid values rejected");

    setup_cf(&cf, &args, values, 2);
    g_compile_complex_rc = NGX_OK;

    /* markdown_streaming_engine: invalid_value */
    init_conf(&mcf);
    engine_cmd = stream_engine_directive();
    set_arg(&values[0], "markdown_streaming_engine");
    set_arg(&values[1], "invalid_value");
    rc = engine_cmd->set(&cf, engine_cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "stream_engine_handler 'invalid_value' should be rejected");

    /* markdown_streaming_engine: yes */
    init_conf(&mcf);
    set_arg(&values[1], "yes");
    rc = engine_cmd->set(&cf, engine_cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "stream_engine_handler 'yes' should be rejected");

    /* markdown_stream_threshold: 0 (must be > 0) */
    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_stream_threshold");
    set_arg(&values[0], "markdown_stream_threshold");
    set_arg(&values[1], "0");
    rc = ngx_http_markdown_stream_threshold_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "stream_threshold '0' should be rejected (must be > 0)");

    /* markdown_stream_threshold: invalid */
    init_conf(&mcf);
    set_arg(&values[1], "invalid");
    rc = ngx_http_markdown_stream_threshold_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "stream_threshold 'invalid' should be rejected");

    /* markdown_stream_flush_min: 0 (must be > 0) */
    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_stream_flush_min");
    set_arg(&values[0], "markdown_stream_flush_min");
    set_arg(&values[1], "0");
    rc = ngx_http_markdown_stream_flush_min_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "stream_flush_min '0' should be rejected (must be > 0)");

    /* markdown_stream_flush_min: bad */
    init_conf(&mcf);
    set_arg(&values[1], "bad");
    rc = ngx_http_markdown_stream_flush_min_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "stream_flush_min 'bad' should be rejected");

    /* markdown_stream_excluded_types: invalid (no slash) */
    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_stream_excluded_types");
    set_arg(&values[0], "markdown_stream_excluded_types");
    set_arg(&values[1], "noslash");
    rc = ngx_http_markdown_stream_excluded_types_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "excluded_types 'noslash' should be rejected");

    /* markdown_stream_excluded_types: empty string */
    init_conf(&mcf);
    set_arg(&values[1], "");
    rc = ngx_http_markdown_stream_excluded_types_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "excluded_types empty string should be rejected");

    /* markdown_stream_excluded_types: leading slash */
    init_conf(&mcf);
    set_arg(&values[1], "/html");
    rc = ngx_http_markdown_stream_excluded_types_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "excluded_types '/html' should be rejected");

    /* markdown_stream_excluded_types: trailing slash */
    init_conf(&mcf);
    set_arg(&values[1], "text/");
    rc = ngx_http_markdown_stream_excluded_types_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "excluded_types 'text/' should be rejected");

    TEST_PASS("5.2 Invalid values correctly rejected");
}

/* ================================================================
 * 5.2b v0.8.0 stream_engine_handler rejection
 * ================================================================ */
static void
test_stream_engine_handler_rejection(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            *cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("v0.8.0 stream_engine_handler rejection");

    setup_cf(&cf, &args, values, 2);
    cmd = stream_engine_directive();
    set_arg(&values[0], "markdown_streaming_engine");

    /* Invalid value: "yes" */
    init_conf(&mcf);
    set_arg(&values[1], "yes");
    rc = cmd->set(&cf, cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "stream_engine_handler 'yes' should return NGX_CONF_ERROR");
    TEST_ASSERT(mcf.stream.engine == NGX_CONF_UNSET_UINT,
        "stream_engine_handler 'yes' should leave engine unchanged");

    /* Invalid value: "always" */
    init_conf(&mcf);
    set_arg(&values[1], "always");
    rc = cmd->set(&cf, cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "stream_engine_handler 'always' should return NGX_CONF_ERROR");

    /* Invalid value: empty string */
    init_conf(&mcf);
    set_arg(&values[1], "");
    rc = cmd->set(&cf, cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "stream_engine_handler '' should return NGX_CONF_ERROR");

    /* Duplicate detection: call twice, second should return "is duplicate" */
    init_conf(&mcf);
    set_arg(&values[1], "on");
    rc = cmd->set(&cf, cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "stream_engine_handler first call should succeed");
    TEST_ASSERT(mcf.stream.engine == NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON,
        "stream_engine_handler first call should set engine");

    /* Second call with engine already set -> "is duplicate" */
    set_arg(&values[1], "off");
    rc = cmd->set(&cf, cmd, &mcf);
    TEST_ASSERT(rc != NGX_CONF_OK && rc != NGX_CONF_ERROR,
        "stream_engine_handler duplicate should return error string");
    TEST_ASSERT(strcmp(rc, "is duplicate") == 0,
        "stream_engine_handler duplicate should return 'is duplicate'");

    TEST_PASS("5.2b v0.8.0 stream_engine_handler rejects invalid/duplicate");
}

/* ================================================================
 * 5.2c Allocation failure paths
 * ================================================================ */
static void
test_allocation_failure(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t             cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("5.2c Allocation failure paths");

    setup_cf(&cf, &args, values, 2);
    set_arg(&cmd.name, "markdown_stream_excluded_types");
    set_arg(&values[0], "markdown_stream_excluded_types");
    set_arg(&values[1], "text/csv");

    /* Force the next allocation call to fail */
    init_conf(&mcf);
    g_next_palloc_fails = 1;
    rc = ngx_http_markdown_stream_excluded_types_handler(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "excluded_types handler should return NGX_CONF_ERROR on alloc failure");
    TEST_ASSERT(mcf.stream.excluded_types == NULL,
        "excluded_types should be NULL after array allocation failure");
    g_next_palloc_fails = 0;

    TEST_PASS("5.2c Allocation failure correctly handled");
}

/* ================================================================
 * 5.3 Default inheritance
 * ================================================================ */

static void
merge_stream_config(ngx_http_markdown_conf_t *child,
    const ngx_http_markdown_conf_t *parent)
{
    ngx_flag_t stream_threshold_set;
    ngx_flag_t stream_budget_set;

    stream_threshold_set = (child->stream.threshold != NGX_CONF_UNSET_SIZE);
    stream_budget_set = (child->stream.budget != NGX_CONF_UNSET_SIZE);
    ngx_http_markdown_merge_stream_values(child, parent);
    if (stream_threshold_set) {
        child->stream.threshold_explicit = 1;
    }
    if (stream_budget_set) {
        child->stream.budget_explicit = 1;
    }
}

static int
command_table_contains(const char *name)
{
    for (ngx_command_t *cmd = ngx_http_markdown_filter_commands;
         cmd->name.len != 0;
         cmd++)
    {
        if (cmd->name.len == strlen(name)
            && ngx_strncmp(cmd->name.data, (u_char *) name,
                           cmd->name.len) == 0)
        {
            return 1;
        }
    }

    return 0;
}

static void
test_default_inheritance(void)
{
    ngx_http_markdown_conf_t parent;
    ngx_http_markdown_conf_t child;

    TEST_SUBSECTION("Default inheritance");

    /* Test 1: Both unset -> defaults applied */
    init_conf(&parent);
    init_conf(&child);
    merge_stream_config(&child, &parent);

    TEST_ASSERT(child.stream.engine == NGX_HTTP_MARKDOWN_STREAM_ENGINE_AUTO,
        "default engine should be auto");
    TEST_ASSERT(child.stream.threshold == 1048576,
        "default threshold should be 1m (1048576)");
    TEST_ASSERT(child.stream.precommit_buffer == 262144,
        "default precommit_buffer should be 256k (262144)");
    TEST_ASSERT(child.stream.flush_min == 16384,
        "default flush_min should be 16k (16384)");
    TEST_ASSERT(child.stream.excluded_types == NULL,
        "default excluded_types should be NULL");

    /* Test 2: Parent sets value, child inherits */
    init_conf(&parent);
    init_conf(&child);
    parent.stream.engine = NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON;
    parent.stream.threshold = 512 * 1024;
    parent.stream.precommit_buffer = 128 * 1024;
    parent.stream.flush_min = 32 * 1024;
    merge_stream_config(&child, &parent);

    TEST_ASSERT(child.stream.engine == NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON,
        "child should inherit engine from parent");
    TEST_ASSERT(child.stream.threshold == 512 * 1024,
        "child should inherit threshold from parent");
    TEST_ASSERT(child.stream.precommit_buffer == 128 * 1024,
        "child should inherit precommit_buffer from parent");
    TEST_ASSERT(child.stream.flush_min == 32 * 1024,
        "child should inherit flush_min from parent");

    /* Test 3: Child overrides parent */
    init_conf(&parent);
    init_conf(&child);
    parent.stream.engine = NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON;
    parent.stream.threshold = 2 * 1024 * 1024;
    child.stream.engine = NGX_HTTP_MARKDOWN_STREAM_ENGINE_OFF;
    child.stream.threshold = 256 * 1024;
    merge_stream_config(&child, &parent);

    TEST_ASSERT(child.stream.engine == NGX_HTTP_MARKDOWN_STREAM_ENGINE_OFF,
        "child engine override should be preserved");
    TEST_ASSERT(child.stream.threshold == 256 * 1024,
        "child threshold override should be preserved");

    /* Verify default enum values match design doc */
    TEST_ASSERT(NGX_HTTP_MARKDOWN_STREAM_ENGINE_OFF == 0,
        "STREAM_ENGINE_OFF must be 0");
    TEST_ASSERT(NGX_HTTP_MARKDOWN_STREAM_ENGINE_AUTO == 1,
        "STREAM_ENGINE_AUTO must be 1");
    TEST_ASSERT(NGX_HTTP_MARKDOWN_STREAM_ENGINE_ON == 2,
        "STREAM_ENGINE_ON must be 2");

    TEST_PASS("5.3 Default inheritance works correctly");
}

/* ================================================================
 * 5.4 Reserved directive rejected
 * ================================================================ */
static void
test_reserved_directive_rejected(void)
{
    ngx_str_t bad_directive;
    size_t    result;

    TEST_SUBSECTION("Reserved directive rejected");

    /*
     * The reserved directive markdown_stream_flush_interval is NOT
     * registered in the production module command array. In production,
     * using it would cause nginx -t to fail with "unknown directive".
     */

    set_arg(&bad_directive, "flush_interval");
    result = ngx_http_markdown_parse_size(&bad_directive);
    TEST_ASSERT(result == (size_t) NGX_ERROR,
        "parse_size should reject non-numeric 'flush_interval'");

    TEST_ASSERT(!command_table_contains("markdown_stream_flush_interval"),
        "reserved directive name absent from production command table");

    TEST_PASS("5.4 Reserved directive is not registered");
}

/* ================================================================
 * 5.5 Hard exclusions always present
 * ================================================================ */
static void
test_hard_exclusions_always_present(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_str_t                ct;

    TEST_SUBSECTION("Hard exclusions always present");

    init_conf(&conf);
    conf.stream.excluded_types = NULL;

    /* text/event-stream */
    set_arg(&ct, "text/event-stream");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "text/event-stream should be hard-excluded");

    /* application/x-ndjson */
    set_arg(&ct, "application/x-ndjson");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/x-ndjson should be hard-excluded");

    /* application/stream+json */
    set_arg(&ct, "application/stream+json");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/stream+json should be hard-excluded");

    /* Non-excluded type should not be excluded */
    set_arg(&ct, "text/html");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/html should NOT be excluded");

    /* NULL content_type should return 0 */
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(NULL, &conf) == 0,
        "NULL content_type should not be excluded");

    /* Empty content_type should return 0 */
    ct.data = NULL;
    ct.len = 0;
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "empty content_type should not be excluded");

    /* User cannot remove hard exclusions */
    {
        ngx_array_t user_types;
        ngx_str_t   user_elts[1];

        set_arg(&user_elts[0], "text/csv");
        user_types.elts = user_elts;
        user_types.nelts = 1;
        user_types.size = sizeof(ngx_str_t);
        user_types.nalloc = 1;
        user_types.pool = NULL;

        conf.stream.excluded_types = &user_types;

        /* Hard exclusion still present with user config */
        set_arg(&ct, "text/event-stream");
        TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
            "hard exclusion preserved with user config");

        set_arg(&ct, "application/x-ndjson");
        TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
            "application/x-ndjson hard exclusion preserved");

        set_arg(&ct, "application/stream+json");
        TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
            "application/stream+json hard exclusion preserved");

        /* User-configured type also excluded */
        set_arg(&ct, "text/csv");
        TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
            "user-configured type should also be excluded");
    }

    TEST_PASS("5.5 Hard exclusions always enforced");
}

/* ================================================================
 * 5.6 Reserved directive absent from directive inventory
 * ================================================================ */
static void
test_reserved_directive_absent_from_inventory(void)
{
    ngx_str_t fi_str;
    size_t    parsed;

    TEST_SUBSECTION("Reserved directive absent from inventory");

    TEST_ASSERT(!command_table_contains("markdown_stream_flush_interval"),
        "reserved directive NOT in production command table");

    /* Verify "flush_interval" is not a parseable size token */
    set_arg(&fi_str, "flush_interval");
    parsed = ngx_http_markdown_parse_size(&fi_str);
    TEST_ASSERT(parsed == (size_t) NGX_ERROR,
        "'flush_interval' is not a valid size token");

    TEST_PASS("5.6 Reserved directive not in command table");
}

/* ================================================================
 * 5.7 Hard exclusions match MIME types with parameters
 * ================================================================ */
static void
test_hard_exclusions_with_parameters(void)
{
    ngx_http_markdown_conf_t conf;
    ngx_str_t                ct;

    TEST_SUBSECTION("Hard exclusions match MIME types with parameters");

    init_conf(&conf);
    conf.stream.excluded_types = NULL;

    /* text/event-stream; charset=utf-8 */
    set_arg(&ct, "text/event-stream; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "text/event-stream; charset=utf-8 should be excluded");

    /* TEXT/EVENT-STREAM (case variation) */
    set_arg(&ct, "TEXT/EVENT-STREAM");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "TEXT/EVENT-STREAM (uppercase) should be excluded");

    /* Text/Event-Stream (mixed case) */
    set_arg(&ct, "Text/Event-Stream");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "Text/Event-Stream (mixed case) should be excluded");

    /* application/x-ndjson; boundary=something */
    set_arg(&ct, "application/x-ndjson; boundary=something");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/x-ndjson; boundary=something should be excluded");

    /* APPLICATION/X-NDJSON */
    set_arg(&ct, "APPLICATION/X-NDJSON");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "APPLICATION/X-NDJSON (uppercase) should be excluded");

    /* application/stream+json; charset=utf-8 */
    set_arg(&ct, "application/stream+json; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "application/stream+json with params should be excluded");

    /* APPLICATION/STREAM+JSON */
    set_arg(&ct, "APPLICATION/STREAM+JSON");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "APPLICATION/STREAM+JSON (uppercase) should be excluded");

    /* text/event-stream with trailing space before semicolon */
    set_arg(&ct, "text/event-stream ;charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 1,
        "text/event-stream with space before params should be excluded");

    /* Non-excluded type with parameters should not be excluded */
    set_arg(&ct, "text/html; charset=utf-8");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/html; charset=utf-8 should NOT be excluded");

    /* Partial match should not be excluded */
    set_arg(&ct, "text/event-streaming");
    TEST_ASSERT(ngx_http_markdown_stream_type_excluded(&ct, &conf) == 0,
        "text/event-streaming (longer) should NOT be excluded");

    TEST_PASS("5.7 Hard exclusions match with parameters and case");
}


/* ================================================================
 * 5.8 stream.threshold defaults and override
 *
 * Tests that the stream.threshold default is preserved when no
 * directive is explicitly set, and that explicit overrides are
 * correctly inherited from parent and applied from child.
 * ================================================================ */
static void
test_stream_threshold_defaults_and_override(void)
{
    ngx_http_markdown_conf_t parent;
    ngx_http_markdown_conf_t child;

    TEST_SUBSECTION("stream.threshold defaults and override");

    /*
     * Test 1: No directives set at all.
     * stream.threshold must remain at the 0.8.0 default (1m).
     */
    init_conf(&parent);
    init_conf(&child);

    merge_stream_config(&child, &parent);

    TEST_ASSERT(child.stream.threshold
        == NGX_HTTP_MARKDOWN_STREAM_THRESHOLD_DEFAULT,
        "default threshold must remain 1m (1048576) when no "
        "directive was explicitly set");

    /*
     * Test 2: Operator explicitly sets markdown_stream_threshold 64k.
     */
    init_conf(&parent);
    init_conf(&child);

    parent.stream.threshold = 64 * 1024;
    parent.stream.threshold_explicit = 1;
    child.stream.threshold = 64 * 1024;
    child.stream.threshold_explicit = 1;

    merge_stream_config(&child, &parent);

    TEST_ASSERT(child.stream.threshold == 64 * 1024,
        "explicit stream.threshold 64k must be preserved");

    /*
     * Test 3: Child overrides parent threshold.
     */
    init_conf(&parent);
    init_conf(&child);

    parent.stream.threshold = 64 * 1024;
    parent.stream.threshold_explicit = 1;
    child.stream.threshold = 512 * 1024;
    child.stream.threshold_explicit = 1;

    merge_stream_config(&child, &parent);

    TEST_ASSERT(child.stream.threshold == 512 * 1024,
        "explicit child stream.threshold must override parent");

    TEST_PASS("5.8 stream.threshold defaults and override correct");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_config_contract Tests (streaming configuration contract)\n");
    printf("========================================\n");

    g_compile_complex_rc = NGX_OK;
    memset(&g_main_conf, 0, sizeof(g_main_conf));

    test_valid_values();
    test_stream_engine_handler_valid();
    test_dynconf_directives_support_published_contexts();
    test_invalid_values();
    test_stream_engine_handler_rejection();
    test_allocation_failure();
    test_default_inheritance();
    test_reserved_directive_rejected();
    test_hard_exclusions_always_present();
    test_reserved_directive_absent_from_inventory();
    test_hard_exclusions_with_parameters();
    test_stream_threshold_defaults_and_override();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");

    return 0;
}
