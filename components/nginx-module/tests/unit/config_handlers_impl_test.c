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
static char ngx_conf_ok[] = "OK";
#define NGX_CONF_OK ngx_conf_ok
#endif
#ifndef NGX_CONF_ERROR
static char ngx_conf_error[] = "ERROR";
#define NGX_CONF_ERROR ngx_conf_error
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

/*
 * Global module symbols required by the config implementation header.
 * These are referenced by the production code but never dereferenced
 * in the unit harness.
 */
ngx_module_t ngx_http_markdown_filter_module;
ngx_module_t ngx_http_core_module;

/*
 * Test-controlled state for stub return values.
 * g_clcf:          pointer returned by ngx_http_conf_get_module_loc_conf;
 *                  tests set it to control the core location configuration.
 * g_compile_complex_rc: return code for ngx_http_compile_complex_value;
 *                  tests set it to simulate compilation success or failure.
 */
static ngx_http_core_loc_conf_t *g_clcf;
static ngx_int_t g_compile_complex_rc;

/*
 * No-op stub for the metrics content handler.
 *
 * Parameters:
 *   r  - HTTP request (unused in the unit harness).
 *
 * Return: NGX_OK unconditionally; the unit harness does not exercise
 *         HTTP request processing.
 *
 * Side effects: none.
 */
static ngx_int_t
ngx_http_markdown_metrics_handler(ngx_http_request_t *r)
{
    UNUSED(r);
    return NGX_OK;
}

/*
 * Case-insensitive comparison of the first n bytes.
 *
 * Test-local reimplementation of the NGINX primitive because the
 * production symbol cannot be linked in the unit harness.
 *
 * Divergence risk: low — logic is a direct transliteration of
 * ngx_ascii_strncasecmp in src/core/ngx_string.c; any future
 * change to the production locale handling or byte folding would
 * require a corresponding update here.
 *
 * Parameters:
 *   s1 - first byte string.
 *   s2 - second byte string.
 *   n  - number of leading bytes to compare.
 *
 * Return: 0 if all n bytes match case-insensitively; otherwise the
 *         difference between the lowercased mismatching bytes
 *         (s1[i] - s2[i]).
 *
 * Side effects: none.
 */
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

/*
 * Null-terminated case-insensitive string comparison.
 *
 * Test-local reimplementation of the NGINX primitive because the
 * production symbol cannot be linked in the unit harness.
 *
 * Divergence risk: low — mirrors ngx_strcasecmp in
 * src/core/ngx_string.c; changes to the production early-exit or
 * NULL-handling semantics would require a corresponding update.
 *
 * Parameters:
 *   s1 - first null-terminated byte string.
 *   s2 - second null-terminated byte string.
 *
 * Return: NGX_ERROR if either pointer is NULL; otherwise 0 on
 *         equality, or the difference between the lowercased
 *         mismatching bytes.
 *
 * Side effects: none.
 */
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

/*
 * Length-bounded case-insensitive comparison delegating to
 * ngx_ascii_strncasecmp.
 *
 * Test-local reimplementation of the NGINX primitive because the
 * production symbol cannot be linked in the unit harness.
 *
 * Divergence risk: low — thin wrapper; any change to the
 * production delegation target would require an update here.
 *
 * Parameters:
 *   s1 - first byte string.
 *   s2 - second byte string.
 *   n  - maximum number of bytes to compare.
 *
 * Return: value from ngx_ascii_strncasecmp.
 *
 * Side effects: none.
 */
static ngx_int_t
ngx_strncasecmp(u_char *s1, u_char *s2, size_t n)
{
    return ngx_ascii_strncasecmp(s1, s2, n);
}

/*
 * Search for byte c in the range [p, last).
 *
 * Test-local reimplementation of the NGINX primitive because the
 * production symbol cannot be linked in the unit harness.
 *
 * Divergence risk: low — mirrors ngx_strlchr in
 * src/core/ngx_string.c.
 *
 * Parameters:
 *   p    - start of search range (inclusive).
 *   last - end of search range (exclusive).
 *   c    - byte value to find.
 *
 * Return: pointer to the first occurrence of c, or NULL if not found.
 *
 * Side effects: none.
 */
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

/*
 * Thin wrapper around strchr(3) with u_char* return type.
 *
 * Test-local reimplementation of the NGINX primitive because the
 * production symbol cannot be linked in the unit harness.
 *
 * Divergence risk: low — one-line wrapper; any change to the
 * production signature would require an update here.
 *
 * Parameters:
 *   s - null-terminated byte string to search.
 *   c - character value to find.
 *
 * Return: pointer to the first occurrence of c, or NULL.
 *
 * Side effects: none.
 */
static u_char *
ngx_strchr(const u_char *s, int c)
{
    return (u_char *) strchr((const char *) s, c);
}

/*
 * Zero-fill wrapper delegating to memset(3).
 *
 * Mirrors production ngx_memzero; test-local reimplementation
 * because the production macro cannot be linked in the unit harness.
 *
 * Divergence risk: low — direct transliteration of the production
 * macro.
 *
 * Parameters:
 *   p - pointer to the memory region to zero.
 *   n - number of bytes to zero.
 *
 * Return: void.
 *
 * Side effects: modifies the memory region pointed to by p.
 */
static void
ngx_memzero(void *p, size_t n)
{
    memset(p, 0, n);
}

/*
 * Pool allocator stub delegating to malloc(3).
 *
 * Does not zero-initialize (unlike ngx_pcalloc).  The pool
 * argument is ignored; the unit harness does not simulate pool
 * ownership or cleanup.
 *
 * Divergence risk: medium — production ngx_palloc may return
 * previously-freed pool memory or trigger pool expansion; this
 * stub always calls malloc, so allocation patterns differ.
 *
 * Parameters:
 *   pool - memory pool (unused).
 *   size - number of bytes to allocate.
 *
 * Return: pointer to allocated memory, or NULL on failure.
 *
 * Side effects: allocates heap memory via malloc(3).
 */
static void *
ngx_palloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return malloc(size);
}

/*
 * Pool allocator stub delegating to calloc(3).
 *
 * Always zero-initializes.  The pool argument is ignored; the unit
 * harness does not simulate pool ownership or cleanup.
 *
 * Divergence risk: medium — production ngx_pcalloc may return
 * previously-freed pool memory; this stub always calls calloc,
 * so allocation patterns differ.
 *
 * Parameters:
 *   pool - memory pool (unused).
 *   size - number of bytes to allocate.
 *
 * Return: pointer to zero-initialized memory, or NULL on failure.
 *
 * Side effects: allocates heap memory via calloc(3).
 */
static void *
ngx_pcalloc(ngx_pool_t *pool, size_t size)
{
    UNUSED(pool);
    return calloc(1, size);
}

/*
 * Array creation stub.  Allocates the array header and element
 * storage via calloc(3).  The pool argument is ignored.
 *
 * Divergence risk: medium — production ngx_array_create allocates
 * from the pool and does not guarantee zero-initialization of
 * elements; this stub uses calloc, so elements are always zeroed.
 *
 * Parameters:
 *   pool - memory pool (unused).
 *   n    - initial number of elements to allocate (0 is promoted to 1).
 *   size - size of each element in bytes.
 *
 * Return: pointer to the new array, or NULL on allocation failure.
 *
 * Side effects: allocates heap memory via calloc(3).
 */
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

/*
 * Array push stub.  Doubles storage on overflow via realloc(3),
 * zero-initializing new slots.  Returns NULL on NULL input or
 * allocation failure.
 *
 * Divergence risk: medium — production ngx_array_push allocates
 * from the pool and may not zero-initialize new elements; this
 * stub uses realloc and explicit memset, so behaviour differs
 * for uninitialized-element reads.
 *
 * Parameters:
 *   a - pointer to the array to push into.
 *
 * Return: pointer to the newly pushed element (zero-initialized),
 *         or NULL on NULL input or allocation failure.
 *
 * Side effects: may reallocate the element buffer; modifies a->nelts
 *               and a->nalloc on growth.
 */
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

/*
 * Complex value compilation stub.  Copies the input string to the
 * output complex_value and returns g_compile_complex_rc, allowing
 * tests to control compilation success or failure.
 *
 * Divergence risk: high — production ngx_http_compile_complex_value
 * parses NGINX variable expressions and builds a bytecode program;
 * this stub performs a shallow copy only, so variable resolution
 * at request time is not exercised.
 *
 * Parameters:
 *   ccv - compilation context containing cf, value, and
 *         complex_value pointers.
 *
 * Return: g_compile_complex_rc (NGX_OK or NGX_ERROR as set by
 *         the test), or NGX_ERROR if ccv or its members are NULL.
 *
 * Side effects: writes to ccv->complex_value->value on success.
 */
static ngx_int_t
ngx_http_compile_complex_value(ngx_http_compile_complex_value_t *ccv)
{
    if (ccv == NULL || ccv->value == NULL || ccv->complex_value == NULL) {
        return NGX_ERROR;
    }

    ccv->complex_value->value = *ccv->value;
    return g_compile_complex_rc;
}

/*
 * No-op stub for ngx_conf_log_error.  Consumes variadic args
 * without output, silencing configuration error logging in the
 * unit harness.
 *
 * Parameters:
 *   level - log level (unused).
 *   cf    - configuration context (unused).
 *   err   - error code (unused).
 *   fmt   - printf-style format string (consumed but not printed).
 *   ...   - format arguments (consumed but not printed).
 *
 * Return: void.
 *
 * Side effects: none (va_start/va_end pair consumes args only).
 */
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

/*
 * Returns the preconfigured g_clcf pointer, allowing tests to
 * control the core location configuration without linking the
 * full NGINX HTTP module infrastructure.
 *
 * Parameters:
 *   cf     - configuration context (unused).
 *   module - module identifier (unused).
 *
 * Return: g_clcf as set by the test.
 *
 * Side effects: none.
 */
static void *
ngx_http_conf_get_module_loc_conf(ngx_conf_t *cf, ngx_module_t module)
{
    UNUSED(cf);
    UNUSED(module);
    return g_clcf;
}

#include "../../src/ngx_http_markdown_config_handlers_impl.h"

static ngx_pool_t g_pool;

/*
 * Assign a C string literal to an ngx_str_t.  Casts through
 * uintptr_t for u_char* compatibility to avoid compiler warnings
 * about discarding const from string literals.
 *
 * Parameters:
 *   arg - pointer to the ngx_str_t to populate.
 *   s   - null-terminated C string literal.
 *
 * Return: void.
 *
 * Side effects: modifies arg->data and arg->len.
 */
static void
set_arg(ngx_str_t *arg, const char *s)
{
    arg->data = (u_char *) (uintptr_t) s;
    arg->len = strlen(s);
}

/*
 * Wire up an ngx_conf_t with a pool and an args array pointing to
 * the given values array.  Prepares the configuration structure
 * used by directive handler invocations in tests.
 *
 * Parameters:
 *   cf    - pointer to the ngx_conf_t to initialize.
 *   args  - pointer to the ngx_array_t to use as the args array.
 *   values - array of ngx_str_t values to serve as array elements.
 *   count  - number of elements in values.
 *
 * Return: void.
 *
 * Side effects: modifies cf->pool, cf->args, and all fields of args.
 */
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

/*
 * Zero-initialize a location conf and set all enum/pointer fields
 * to their NGINX unset sentinels.  Ensures each test starts from a
 * clean, well-defined configuration state.
 *
 * Parameters:
 *   mcf - pointer to the ngx_http_markdown_conf_t to initialize.
 *
 * Return: void.
 *
 * Side effects: overwrites all fields of *mcf.
 */
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

/*
 * Verify ngx_http_markdown_arg_equals: NULL data, case-insensitive
 * match, and length mismatch.
 *
 * Semantic contract mirrored: the production arg_equals helper
 * returns 1 for an exact (case-insensitive, length-equal) match
 * and 0 otherwise.
 *
 * Return: void.
 *
 * Side effects: none (assertions only).
 */
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

/*
 * Verify the markdown_filter directive handler: on/off, duplicate
 * detection, variable compilation, invalid token, and compile
 * failure.
 *
 * Semantic contract mirrored: ngx_http_markdown_filter sets
 * mcf->enabled and mcf->enabled_source for static tokens, or
 * mcf->enabled_complex for variable expressions; it rejects
 * duplicates and invalid tokens.
 *
 * Return: void.
 *
 * Side effects: modifies g_compile_complex_rc; asserts on mcf fields.
 */
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

/*
 * Verify on_error, flavor, and auth_policy handlers: valid values,
 * duplicate detection, and invalid values.
 *
 * Semantic contract mirrored: each handler maps a string token to
 * the corresponding enum value in mcf, rejects duplicates, and
 * returns NGX_CONF_ERROR for unrecognized tokens.
 *
 * Return: void.
 *
 * Side effects: asserts on mcf enum fields.
 */
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

/*
 * Verify auth_cookies handler: multiple patterns, duplicate
 * detection, and empty pattern rejection.
 *
 * Semantic contract mirrored: ngx_http_markdown_auth_cookies
 * collects one or more glob patterns into an ngx_array_t stored in
 * mcf->auth_cookies; it rejects duplicates and empty patterns.
 *
 * Return: void.
 *
 * Side effects: asserts on mcf.auth_cookies array contents.
 */
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

/*
 * Verify conditional_requests and log_verbosity handlers: valid
 * and invalid enum values.
 *
 * Semantic contract mirrored: each handler maps a string token to
 * the corresponding enum value, rejects duplicates, and returns
 * NGX_CONF_ERROR for unrecognized tokens.
 *
 * Return: void.
 *
 * Side effects: asserts on mcf enum fields.
 */
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

/*
 * Verify stream_types handler: valid types, duplicate detection,
 * and format validation (no-slash, leading-slash, trailing-slash,
 * multiple-slashes).
 *
 * Semantic contract mirrored: ngx_http_markdown_stream_types
 * collects MIME type strings of the form "type/subtype" into an
 * ngx_array_t stored in mcf->stream_types; it rejects duplicates
 * and malformed type strings.
 *
 * Return: void.
 *
 * Side effects: asserts on mcf.stream_types array contents.
 */
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

/*
 * Verify large_body_threshold handler: off, size suffixes (k/m/g),
 * invalid token, and duplicate detection.
 *
 * Semantic contract mirrored: ngx_http_markdown_large_body_threshold
 * maps "off" to 0, parses size tokens with optional k/K/m/M/g/G
 * suffixes via the shared production parser helper, rejects
 * duplicates, and returns NGX_CONF_ERROR for invalid tokens.
 *
 * Return: void.
 *
 * Side effects: asserts on mcf.large_body_threshold.
 */
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
    set_arg(&values[1], "1g");
    rc = ngx_http_markdown_large_body_threshold(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_OK, "1g should parse");
    TEST_ASSERT(mcf.large_body_threshold == (size_t) 1024 * 1024 * 1024,
        "1g should map correctly");

    init_conf(&mcf);
    {
        char overflow_token[32];
        size_t overflow_value;

        overflow_value = (NGX_MAX_SIZE_T_VALUE
                          / ((size_t) 1024 * 1024 * 1024)) + 1;
        snprintf(overflow_token, sizeof(overflow_token), "%zuG",
            overflow_value);
        set_arg(&values[1], overflow_token);
    }
    rc = ngx_http_markdown_large_body_threshold(&cf, &cmd, &mcf);
    TEST_ASSERT(rc == NGX_CONF_ERROR,
        "overflow threshold token should fail");

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

/*
 * Verify metrics_format and metrics directive handlers: valid
 * formats, invalid format, handler installation, and duplicate
 * content handler.
 *
 * Semantic contract mirrored: ngx_http_markdown_metrics_format
 * maps format tokens to enum values; ngx_http_markdown_metrics_directive
 * installs the metrics content handler in the core location
 * configuration and rejects duplicates.
 *
 * Return: void.
 *
 * Side effects: modifies g_clcf; asserts on mcf.ops.metrics_format
 *               and clcf.handler.
 */
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

/*
 * Verify streaming_engine handler: static values, duplicate
 * detection, variable compilation, and compile failure.
 *
 * Semantic contract mirrored: ngx_http_markdown_streaming_engine
 * accepts static tokens or NGINX variable expressions, stores the
 * compiled complex value in mcf->streaming_engine, rejects
 * duplicates, and returns NGX_CONF_ERROR for invalid tokens or
 * compilation failures.
 *
 * Return: void.
 *
 * Side effects: modifies g_compile_complex_rc; asserts on
 *               mcf.streaming_engine.
 */
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

/*
 * Entry point: run all config_handlers_impl unit tests.
 *
 * Return: 0 on success (all assertions pass).
 *
 * Side effects: writes test progress to stdout.
 */
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
