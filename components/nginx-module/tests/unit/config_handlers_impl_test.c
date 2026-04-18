/*
 * Test: config_handlers_impl
 * Description: direct branch coverage for config directive handlers.
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
#define NGX_CONF_OK ((char *) "OK")
#endif
#ifndef NGX_CONF_ERROR
#define NGX_CONF_ERROR ((char *) "ERROR")
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

#ifndef NGX_MAX_SIZE_T_VALUE
#define NGX_MAX_SIZE_T_VALUE ((size_t) -1)
#endif

typedef intptr_t ngx_err_t;

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
    ngx_str_t  name;
};

struct ngx_module_s {
    int dummy;
};

typedef struct {
    ngx_int_t (*handler)(ngx_http_request_t *r);
} ngx_http_core_loc_conf_t;

ngx_module_t ngx_http_markdown_filter_module;
ngx_module_t ngx_http_core_module;

static ngx_http_core_loc_conf_t *g_clcf;
static ngx_int_t g_compile_complex_rc;

static ngx_int_t
ngx_http_markdown_metrics_handler(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

static ngx_int_t
ngx_ascii_strncasecmp(const u_char *s1, const u_char *s2, size_t n)
{
    size_t i;

    for (i = 0; i < n; i++) {
        u_char c1;
        u_char c2;

        c1 = (u_char) tolower((unsigned char) s1[i]);
        c2 = (u_char) tolower((unsigned char) s2[i]);

        if (c1 != c2) {
            return (ngx_int_t) c1 - (ngx_int_t) c2;
        }
    }

    return 0;
}

static ngx_int_t
ngx_strcasecmp(u_char *s1, u_char *s2)
{
    size_t i;

    if (s1 == NULL || s2 == NULL) {
        return NGX_ERROR;
    }

    i = 0;
    while (s1[i] != '\0' && s2[i] != '\0') {
        ngx_int_t diff;

        diff = ngx_ascii_strncasecmp(&s1[i], &s2[i], 1);
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

static u_char *
ngx_strchr(const u_char *s, int c)
{
    return (u_char *) strchr((const char *) s, c);
}

static void
ngx_memzero(void *p, size_t n)
{
    memset(p, 0, n);
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
    return calloc(1, size);
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
               0,
               (new_nalloc - a->nalloc) * a->size);

        a->elts = new_elts;
        a->nalloc = new_nalloc;
    }

    elt = (u_char *) a->elts + (a->nelts * a->size);
    memset(elt, 0, a->size);
    a->nelts++;

    return elt;
}

static size_t
ngx_parse_size(ngx_str_t *line)
{
    char    buf[64];
    char   *endptr;
    size_t  len;
    size_t  value;

    if (line == NULL || line->data == NULL || line->len == 0) {
        return (size_t) NGX_ERROR;
    }

    len = line->len;
    if (len >= sizeof(buf)) {
        return (size_t) NGX_ERROR;
    }

    memcpy(buf, line->data, len);
    buf[len] = '\0';

    if (len > 1 && (buf[len - 1] == 'k' || buf[len - 1] == 'K'
                 || buf[len - 1] == 'm' || buf[len - 1] == 'M'))
    {
        char suffix;

        suffix = buf[len - 1];
        buf[len - 1] = '\0';

        value = (size_t) strtoull(buf, &endptr, 10);
        if (*endptr != '\0') {
            return (size_t) NGX_ERROR;
        }

        if (suffix == 'k' || suffix == 'K') {
            return value * 1024;
        }

        return value * 1024 * 1024;
    }

    value = (size_t) strtoull(buf, &endptr, 10);
    if (*endptr != '\0') {
        return (size_t) NGX_ERROR;
    }

    return value;
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

static void *
ngx_http_conf_get_module_loc_conf(ngx_conf_t *cf, ngx_module_t module)
{
    UNUSED(cf);
    UNUSED(module);
    return g_clcf;
}

#include "../../src/ngx_http_markdown_config_handlers_impl.h"

static ngx_pool_t g_pool;

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
    mcf->auth_policy = NGX_CONF_UNSET_UINT;
    mcf->auth_cookies = NGX_CONF_UNSET_PTR;
    mcf->conditional_requests = NGX_CONF_UNSET_UINT;
    mcf->log_verbosity = NGX_CONF_UNSET_UINT;
    mcf->stream_types = NGX_CONF_UNSET_PTR;
    mcf->large_body_threshold = NGX_CONF_UNSET_SIZE;
    mcf->ops.metrics_format = NGX_CONF_UNSET_UINT;
    mcf->streaming_engine = NULL;
}

static void
test_arg_equals(void)
{
    ngx_str_t arg;

    TEST_SUBSECTION("arg_equals helper");

    arg.data = NULL;
    arg.len = 0;
    TEST_ASSERT(ngx_http_markdown_arg_equals(&arg, (u_char *) "on", 2) == 0,
        "NULL arg data should not match");

    set_arg(&arg, "On");
    TEST_ASSERT(ngx_http_markdown_arg_equals(&arg, (u_char *) "on", 2) == 1,
        "matching token should be case-insensitive");

    TEST_ASSERT(ngx_http_markdown_arg_equals(&arg, (u_char *) "on", 3) == 0,
        "mismatched length should fail");

    TEST_PASS("arg_equals branches covered");
}

static void
test_markdown_filter_handler(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("markdown_filter handler");

    init_conf(&mcf);
    setup_cf(&cf, &args, values, 2);
    set_arg(&cmd.name, "markdown_filter");
    set_arg(&values[0], "markdown_filter");

    set_arg(&values[1], "on");
    rc = ngx_http_markdown_filter(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "on should parse");
    TEST_ASSERT(mcf.enabled == 1, "enabled should be 1");
    TEST_ASSERT(mcf.enabled_source == NGX_HTTP_MARKDOWN_ENABLED_STATIC,
        "enabled source should be static");

    rc = ngx_http_markdown_filter(&cf, &cmd, &mcf);
    TEST_ASSERT(strcmp(rc, "is duplicate") == 0,
        "duplicate should be rejected");

    init_conf(&mcf);
    set_arg(&values[1], "off");
    rc = ngx_http_markdown_filter(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "off should parse");
    TEST_ASSERT(mcf.enabled == 0, "enabled should be 0");

    init_conf(&mcf);
    set_arg(&values[1], "$convert");
    g_compile_complex_rc = NGX_OK;
    rc = ngx_http_markdown_filter(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "variable should compile");
    TEST_ASSERT(mcf.enabled_source == NGX_HTTP_MARKDOWN_ENABLED_COMPLEX,
        "variable should set complex source");
    TEST_ASSERT(mcf.enabled_complex != NULL,
        "complex value should be stored");

    init_conf(&mcf);
    set_arg(&values[1], "yes");
    rc = ngx_http_markdown_filter(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "invalid static token should fail");

    init_conf(&mcf);
    set_arg(&values[1], "$bad_compile");
    g_compile_complex_rc = NGX_ERROR;
    rc = ngx_http_markdown_filter(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "compile failure should return error");

    g_compile_complex_rc = NGX_OK;

    TEST_PASS("markdown_filter branches covered");
}

static void
test_simple_enum_handlers(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("on_error/flavor/auth_policy handlers");

    setup_cf(&cf, &args, values, 2);

    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_on_error");
    set_arg(&values[0], "markdown_on_error");
    set_arg(&values[1], "pass");
    rc = ngx_http_markdown_on_error(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "pass should parse");
    TEST_ASSERT(mcf.on_error == NGX_HTTP_MARKDOWN_ON_ERROR_PASS,
        "on_error should be pass");
    rc = ngx_http_markdown_on_error(&cf, &cmd, &mcf);
    TEST_ASSERT(strcmp(rc, "is duplicate") == 0,
        "duplicate on_error should fail");

    init_conf(&mcf);
    set_arg(&values[1], "bad");
    rc = ngx_http_markdown_on_error(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "invalid on_error should fail");

    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_flavor");
    set_arg(&values[0], "markdown_flavor");
    set_arg(&values[1], "gfm");
    rc = ngx_http_markdown_flavor(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "gfm should parse");
    TEST_ASSERT(mcf.flavor == NGX_HTTP_MARKDOWN_FLAVOR_GFM,
        "flavor should be gfm");

    init_conf(&mcf);
    set_arg(&values[1], "markdown");
    rc = ngx_http_markdown_flavor(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "invalid flavor should fail");

    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_auth_policy");
    set_arg(&values[0], "markdown_auth_policy");
    set_arg(&values[1], "deny");
    rc = ngx_http_markdown_auth_policy(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "deny should parse");
    TEST_ASSERT(mcf.auth_policy == NGX_HTTP_MARKDOWN_AUTH_POLICY_DENY,
        "auth_policy should be deny");

    init_conf(&mcf);
    set_arg(&values[1], "block");
    rc = ngx_http_markdown_auth_policy(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "invalid auth policy should fail");

    TEST_PASS("simple enum handlers covered");
}

static void
test_auth_cookies_handler(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[4];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    ngx_str_t               *elts;
    char                    *rc;

    TEST_SUBSECTION("auth_cookies handler");

    init_conf(&mcf);
    setup_cf(&cf, &args, values, 4);
    set_arg(&cmd.name, "markdown_auth_cookies");

    set_arg(&values[0], "markdown_auth_cookies");
    set_arg(&values[1], "session*");
    set_arg(&values[2], "*token");
    set_arg(&values[3], "auth");

    rc = ngx_http_markdown_auth_cookies(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "cookie patterns should parse");
    TEST_ASSERT(mcf.auth_cookies != NULL, "cookie array should be created");
    TEST_ASSERT(mcf.auth_cookies->nelts == 3, "three patterns expected");

    elts = mcf.auth_cookies->elts;
    TEST_ASSERT(elts[0].len == strlen("session*"), "pattern 0 length");
    TEST_ASSERT(elts[1].len == strlen("*token"), "pattern 1 length");
    TEST_ASSERT(elts[2].len == strlen("auth"), "pattern 2 length");

    rc = ngx_http_markdown_auth_cookies(&cf, &cmd, &mcf);
    TEST_ASSERT(strcmp(rc, "is duplicate") == 0,
        "duplicate auth_cookies should fail");

    init_conf(&mcf);
    set_arg(&values[1], "");
    rc = ngx_http_markdown_auth_cookies(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "empty pattern should fail");

    TEST_PASS("auth_cookies branches covered");
}

static void
test_conditional_and_log_verbosity_handlers(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("conditional_requests/log_verbosity handlers");

    setup_cf(&cf, &args, values, 2);
    set_arg(&values[0], "directive");

    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_conditional_requests");
    set_arg(&values[1], "if_modified_since_only");
    rc = ngx_http_markdown_conditional_requests(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "ims_only should parse");
    TEST_ASSERT(mcf.conditional_requests
        == NGX_HTTP_MARKDOWN_CONDITIONAL_IF_MODIFIED_SINCE,
        "conditional mode should match");

    init_conf(&mcf);
    set_arg(&values[1], "disabled");
    rc = ngx_http_markdown_conditional_requests(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "disabled should parse");

    init_conf(&mcf);
    set_arg(&values[1], "invalid");
    rc = ngx_http_markdown_conditional_requests(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "invalid conditional mode should fail");

    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_log_verbosity");
    set_arg(&values[1], "debug");
    rc = ngx_http_markdown_log_verbosity(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "debug should parse");
    TEST_ASSERT(mcf.log_verbosity == NGX_HTTP_MARKDOWN_LOG_DEBUG,
        "log verbosity should be debug");

    init_conf(&mcf);
    set_arg(&values[1], "error");
    rc = ngx_http_markdown_log_verbosity(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "error should parse");

    init_conf(&mcf);
    set_arg(&values[1], "trace");
    rc = ngx_http_markdown_log_verbosity(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "invalid log verbosity should fail");

    TEST_PASS("conditional/log_verbosity branches covered");
}

static void
test_stream_types_handler(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[3];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("stream_types handler");

    init_conf(&mcf);
    setup_cf(&cf, &args, values, 3);

    set_arg(&cmd.name, "markdown_stream_types");
    set_arg(&values[0], "markdown_stream_types");
    set_arg(&values[1], "text/event-stream");
    set_arg(&values[2], "application/json");
    rc = ngx_http_markdown_stream_types(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "valid stream types should parse");
    TEST_ASSERT(mcf.stream_types != NULL, "stream types array should exist");
    TEST_ASSERT(mcf.stream_types->nelts == 2,
        "two stream types should be stored");

    rc = ngx_http_markdown_stream_types(&cf, &cmd, &mcf);
    TEST_ASSERT(strcmp(rc, "is duplicate") == 0,
        "duplicate stream_types should fail");

    init_conf(&mcf);
    set_arg(&values[1], "noslash");
    rc = ngx_http_markdown_stream_types(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "type without slash should fail");

    init_conf(&mcf);
    set_arg(&values[1], "/leading");
    rc = ngx_http_markdown_stream_types(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "leading slash should fail");

    init_conf(&mcf);
    set_arg(&values[1], "trailing/");
    rc = ngx_http_markdown_stream_types(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "trailing slash should fail");

    init_conf(&mcf);
    set_arg(&values[1], "a/b/c");
    rc = ngx_http_markdown_stream_types(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "multiple slashes should fail");

    TEST_PASS("stream_types branches covered");
}

static void
test_large_body_threshold_handler(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("large_body_threshold handler");

    setup_cf(&cf, &args, values, 2);
    set_arg(&values[0], "markdown_large_body_threshold");
    set_arg(&cmd.name, "markdown_large_body_threshold");

    init_conf(&mcf);
    set_arg(&values[1], "off");
    rc = ngx_http_markdown_large_body_threshold(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "off should parse");
    TEST_ASSERT(mcf.large_body_threshold == 0, "off should map to zero");

    init_conf(&mcf);
    set_arg(&values[1], "512k");
    rc = ngx_http_markdown_large_body_threshold(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "size token should parse");
    TEST_ASSERT(mcf.large_body_threshold == 512 * 1024,
        "512k should map correctly");

    init_conf(&mcf);
    set_arg(&values[1], "1m");
    rc = ngx_http_markdown_large_body_threshold(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "1m should parse");
    TEST_ASSERT(mcf.large_body_threshold == 1024 * 1024,
        "1m should map correctly");

    init_conf(&mcf);
    set_arg(&values[1], "bad");
    rc = ngx_http_markdown_large_body_threshold(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "invalid threshold token should fail");

    init_conf(&mcf);
    mcf.large_body_threshold = 1;
    set_arg(&values[1], "off");
    rc = ngx_http_markdown_large_body_threshold(&cf, &cmd, &mcf);
    TEST_ASSERT(strcmp(rc, "is duplicate") == 0,
        "duplicate threshold directive should fail");

    TEST_PASS("large_body_threshold branches covered");
}

static void
test_metrics_handlers(void)
{
    ngx_conf_t                 cf;
    ngx_array_t                args;
    ngx_str_t                  values[2];
    ngx_command_t              cmd;
    ngx_http_markdown_conf_t   mcf;
    ngx_http_core_loc_conf_t   clcf;
    char                      *rc;

    TEST_SUBSECTION("metrics handlers");

    setup_cf(&cf, &args, values, 2);

    init_conf(&mcf);
    set_arg(&cmd.name, "markdown_metrics_format");
    set_arg(&values[0], "markdown_metrics_format");
    set_arg(&values[1], "auto");
    rc = ngx_http_markdown_metrics_format(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "auto metrics format should parse");
    TEST_ASSERT(mcf.ops.metrics_format == NGX_HTTP_MARKDOWN_METRICS_FORMAT_AUTO,
        "metrics format should be auto");

    init_conf(&mcf);
    set_arg(&values[1], "prometheus");
    rc = ngx_http_markdown_metrics_format(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "prometheus metrics format should parse");
    TEST_ASSERT(mcf.ops.metrics_format == NGX_HTTP_MARKDOWN_METRICS_FORMAT_PROMETHEUS,
        "metrics format should be prometheus");

    init_conf(&mcf);
    set_arg(&values[1], "json");
    rc = ngx_http_markdown_metrics_format(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "invalid metrics format should fail");

    set_arg(&cmd.name, "markdown_metrics");

    g_clcf = NULL;
    rc = ngx_http_markdown_metrics_directive(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "missing core loc conf should fail");

    memset(&clcf, 0, sizeof(clcf));
    g_clcf = &clcf;
    rc = ngx_http_markdown_metrics_directive(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "metrics directive should install handler");
    TEST_ASSERT(clcf.handler == ngx_http_markdown_metrics_handler,
        "metrics handler should be registered");

    rc = ngx_http_markdown_metrics_directive(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "duplicate content handler should fail");

    TEST_PASS("metrics handler branches covered");
}

static void
test_streaming_engine_handler(void)
{
    ngx_conf_t               cf;
    ngx_array_t              args;
    ngx_str_t                values[2];
    ngx_command_t            cmd;
    ngx_http_markdown_conf_t mcf;
    char                    *rc;

    TEST_SUBSECTION("streaming_engine handler");

    setup_cf(&cf, &args, values, 2);
    set_arg(&cmd.name, "markdown_streaming_engine");
    set_arg(&values[0], "markdown_streaming_engine");

    init_conf(&mcf);
    set_arg(&values[1], "auto");
    rc = ngx_http_markdown_streaming_engine(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "auto should parse");
    TEST_ASSERT(mcf.streaming_engine != NULL,
        "compiled streaming engine value should be set");

    rc = ngx_http_markdown_streaming_engine(&cf, &cmd, &mcf);
    TEST_ASSERT(strcmp(rc, "is duplicate") == 0,
        "duplicate streaming_engine should fail");

    init_conf(&mcf);
    set_arg(&values[1], "invalid");
    rc = ngx_http_markdown_streaming_engine(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "invalid static value should fail");

    init_conf(&mcf);
    set_arg(&values[1], "$streaming_mode");
    g_compile_complex_rc = NGX_OK;
    rc = ngx_http_markdown_streaming_engine(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK,
        "variable expression should compile");

    init_conf(&mcf);
    set_arg(&values[1], "$compile_fails");
    g_compile_complex_rc = NGX_ERROR;
    rc = ngx_http_markdown_streaming_engine(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "compile failure should return error");

    g_compile_complex_rc = NGX_OK;

    TEST_PASS("streaming_engine branches covered");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("config_handlers_impl Tests\n");
    printf("========================================\n");

    g_compile_complex_rc = NGX_OK;

    test_arg_equals();
    test_markdown_filter_handler();
    test_simple_enum_handlers();
    test_auth_cookies_handler();
    test_conditional_and_log_verbosity_handlers();
    test_stream_types_handler();
    test_large_body_threshold_handler();
    test_metrics_handlers();
    test_streaming_engine_handler();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");

    return 0;
}
