/*
 * Test: markdown_filter_runtime
 * Description: markdown_filter runtime parsing and resolution behavior
 *
 * NOTE:
 * This file uses a lightweight behavioral model (not direct linkage against
 * nginx module objects). Real runtime wiring is covered by integration tests
 * in tests/integration/run_integration_tests.sh.
 */

#include "test_common.h"

#define TEST_NGX_OK 0
#define TEST_NGX_ERROR (-1)

#define ENABLED_UNSET 0
#define ENABLED_STATIC 1
#define ENABLED_COMPLEX 2
#define TEST_RUNTIME_VALUE_MAX 256

typedef struct {
    const char *value;
    int eval_status;
} request_t;

typedef struct {
    int enabled;
    int enabled_source;
    const char *enabled_complex;
} conf_t;

typedef struct {
    int filter_enabled;
} ctx_t;

static int
is_ascii_space(char ch)
{
    return ch == ' ' || ch == '\t' || ch == '\r'
        || ch == '\n' || ch == '\f' || ch == '\v';
}

static char
ascii_lower(char ch)
{
    if (ch >= 'A' && ch <= 'Z') {
        return (char)(ch - 'A' + 'a');
    }
    return ch;
}

static int
eq_nocase_lit(const char *start, size_t len, const char *lit)
{
    size_t lit_len;

    size_t i;

    if (start == NULL || lit == NULL) {
        return 0;
    }

    lit_len = test_cstrnlen(lit, 16);
    if (lit_len == 16) {
        return 0;
    }
    if (len != lit_len) {
        return 0;
    }

    for (i = 0; i < len; i++) {
        if (ascii_lower(start[i]) != lit[i]) {
            return 0;
        }
    }

    return 1;
}

static int
parse_filter_flag_runtime(const char *raw, int *enabled)
{
    const char *start;
    const char *end;
    size_t len;

    if (raw == NULL || enabled == NULL) {
        return TEST_NGX_ERROR;
    }

    start = raw;
    end = raw + test_cstrnlen(raw, TEST_RUNTIME_VALUE_MAX);

    while (start < end && is_ascii_space(*start)) {
        start++;
    }

    while (end > start && is_ascii_space(*(end - 1))) {
        end--;
    }

    len = (size_t)(end - start);
    if (len == 0) {
        *enabled = 0;
        return TEST_NGX_OK;
    }

    if (len == 1 && start[0] == '1') {
        *enabled = 1;
        return TEST_NGX_OK;
    }
    if (len == 1 && start[0] == '0') {
        *enabled = 0;
        return TEST_NGX_OK;
    }
    if (eq_nocase_lit(start, len, "on")) {
        *enabled = 1;
        return TEST_NGX_OK;
    }
    if (eq_nocase_lit(start, len, "off")) {
        *enabled = 0;
        return TEST_NGX_OK;
    }
    if (eq_nocase_lit(start, len, "yes")) {
        *enabled = 1;
        return TEST_NGX_OK;
    }
    if (eq_nocase_lit(start, len, "no")) {
        *enabled = 0;
        return TEST_NGX_OK;
    }
    if (eq_nocase_lit(start, len, "true")) {
        *enabled = 1;
        return TEST_NGX_OK;
    }
    if (eq_nocase_lit(start, len, "false")) {
        *enabled = 0;
        return TEST_NGX_OK;
    }

    return TEST_NGX_ERROR;
}

static int
is_enabled_runtime(const request_t *r, const conf_t *conf)
{
    int enabled;

    if (conf == NULL) {
        return 0;
    }

    if (conf->enabled_source != ENABLED_COMPLEX
        || conf->enabled_complex == NULL)
    {
        return conf->enabled;
    }

    if (r == NULL || r->eval_status != TEST_NGX_OK || r->value == NULL) {
        return 0;
    }

    enabled = 0;
    if (parse_filter_flag_runtime(r->value, &enabled) != TEST_NGX_OK) {
        return 0;
    }

    return enabled;
}

static int
body_enabled_from_cached_ctx(const conf_t *conf, const ctx_t *ctx)
{
    if (conf == NULL || ctx == NULL) {
        return 0;
    }

    return ctx->filter_enabled;
}

static void
merge_enabled_fields(conf_t *child, const conf_t *parent)
{
    if (child->enabled_source == ENABLED_UNSET) {
        if (parent->enabled_source == ENABLED_UNSET) {
            child->enabled_source = ENABLED_STATIC;
            child->enabled = 0;
            child->enabled_complex = NULL;
        } else {
            child->enabled_source = parent->enabled_source;
            child->enabled = parent->enabled;
            child->enabled_complex = parent->enabled_complex;
        }
    } else if (child->enabled_source == ENABLED_STATIC) {
        child->enabled_complex = NULL;
    }
}

static conf_t
unset_conf(void)
{
    conf_t c;
    c.enabled = 0;
    c.enabled_source = ENABLED_UNSET;
    c.enabled_complex = NULL;
    return c;
}

static void
test_parse_filter_flag_values(void)
{
    int flag;

    TEST_SUBSECTION("parse_filter_flag supported values and trimming");

    flag = -1;
    TEST_ASSERT(parse_filter_flag_runtime("1", &flag) == TEST_NGX_OK, "should parse \"1\"");
    TEST_ASSERT(flag == 1, "\"1\" should map to enabled");

    flag = -1;
    TEST_ASSERT(parse_filter_flag_runtime(" off ", &flag) == TEST_NGX_OK, "should trim and parse \" off \"");
    TEST_ASSERT(flag == 0, "\" off \" should map to disabled");

    flag = -1;
    TEST_ASSERT(parse_filter_flag_runtime("\tYes\n", &flag) == TEST_NGX_OK, "should trim and parse \"yes\"");
    TEST_ASSERT(flag == 1, "\"yes\" should map to enabled");

    flag = -1;
    TEST_ASSERT(parse_filter_flag_runtime("False", &flag) == TEST_NGX_OK, "should parse case-insensitive false");
    TEST_ASSERT(flag == 0, "\"False\" should map to disabled");

    flag = 7;
    TEST_ASSERT(parse_filter_flag_runtime("maybe", &flag) == TEST_NGX_ERROR, "invalid value should fail");
    TEST_ASSERT(flag == 7, "invalid parse must not overwrite output flag");

    TEST_ASSERT(parse_filter_flag_runtime(NULL, &flag) == TEST_NGX_ERROR, "NULL value should fail");
    TEST_ASSERT(parse_filter_flag_runtime("on", NULL) == TEST_NGX_ERROR, "NULL output flag should fail");

    TEST_PASS("parse_filter_flag behavior is correct");
}

static void
test_is_enabled_static_and_edge_cases(void)
{
    conf_t conf;
    request_t req;

    TEST_SUBSECTION("is_enabled static config and edge cases");

    TEST_ASSERT(is_enabled_runtime(NULL, NULL) == 0, "NULL conf should be disabled");

    conf = unset_conf();
    conf.enabled = 1;
    conf.enabled_source = ENABLED_STATIC;
    TEST_ASSERT(is_enabled_runtime(NULL, &conf) == 1, "static enabled should not require request");

    conf.enabled = 0;
    req.value = "1";
    req.eval_status = TEST_NGX_OK;
    TEST_ASSERT(is_enabled_runtime(&req, &conf) == 0, "static disabled should remain disabled");

    conf.enabled = 1;
    conf.enabled_source = ENABLED_COMPLEX;
    conf.enabled_complex = "compiled";
    TEST_ASSERT(is_enabled_runtime(NULL, &conf) == 0, "complex mode should disable when request is NULL");

    conf.enabled = 1;
    conf.enabled_source = ENABLED_COMPLEX;
    conf.enabled_complex = NULL;
    TEST_ASSERT(is_enabled_runtime(NULL, &conf) == 1,
                "complex source without compiled value should fall back to static enabled");

    TEST_PASS("is_enabled static and edge behavior is correct");
}

static void
test_is_enabled_complex_resolution(void)
{
    conf_t conf;
    request_t req;

    TEST_SUBSECTION("is_enabled complex resolution");

    conf = unset_conf();
    conf.enabled = 0;
    conf.enabled_source = ENABLED_COMPLEX;
    conf.enabled_complex = "compiled";

    req.eval_status = TEST_NGX_OK;
    req.value = "on";
    TEST_ASSERT(is_enabled_runtime(&req, &conf) == 1, "\"on\" should enable");

    req.value = " 0 ";
    TEST_ASSERT(is_enabled_runtime(&req, &conf) == 0, "\" 0 \" should disable");

    req.value = "true";
    TEST_ASSERT(is_enabled_runtime(&req, &conf) == 1, "\"true\" should enable");

    req.value = "no";
    TEST_ASSERT(is_enabled_runtime(&req, &conf) == 0, "\"no\" should disable");

    req.value = "maybe";
    TEST_ASSERT(is_enabled_runtime(&req, &conf) == 0, "invalid value should disable");

    req.eval_status = TEST_NGX_ERROR;
    req.value = "1";
    TEST_ASSERT(is_enabled_runtime(&req, &conf) == 0, "evaluation failure should disable");

    TEST_PASS("is_enabled complex behavior is correct");
}

static void
test_body_phase_uses_cached_header_decision(void)
{
    conf_t conf;
    request_t header_req;
    request_t body_req;
    ctx_t ctx;

    TEST_SUBSECTION("body phase uses cached header decision");

    conf = unset_conf();
    conf.enabled = 0;
    conf.enabled_source = ENABLED_COMPLEX;
    conf.enabled_complex = "compiled";

    header_req.eval_status = TEST_NGX_OK;
    header_req.value = "on";
    ctx.filter_enabled = is_enabled_runtime(&header_req, &conf);
    TEST_ASSERT(ctx.filter_enabled == 1, "header should cache enabled decision");

    body_req.eval_status = TEST_NGX_OK;
    body_req.value = "off";

    TEST_ASSERT(is_enabled_runtime(&body_req, &conf) == 0,
                "fresh body-time evaluation can resolve differently");
    TEST_ASSERT(body_enabled_from_cached_ctx(&conf, &ctx) == 1,
                "body phase should use cached header decision");

    TEST_ASSERT(body_enabled_from_cached_ctx(NULL, &ctx) == 0,
                "NULL conf should disable body processing");
    TEST_ASSERT(body_enabled_from_cached_ctx(&conf, NULL) == 0,
                "NULL ctx should disable body processing");

    TEST_PASS("cached header decision behavior is correct");
}

static void
test_enabled_merge_behavior(void)
{
    conf_t parent;
    conf_t child;

    TEST_SUBSECTION("enabled field merge behavior");

    parent = unset_conf();
    child = unset_conf();
    merge_enabled_fields(&child, &parent);
    TEST_ASSERT(child.enabled_source == ENABLED_STATIC, "unset parent/child should default source to static");
    TEST_ASSERT(child.enabled == 0, "unset parent/child should default to disabled");
    TEST_ASSERT(child.enabled_complex == NULL, "unset parent/child should clear complex pointer");

    parent = unset_conf();
    parent.enabled = 0;
    parent.enabled_source = ENABLED_COMPLEX;
    parent.enabled_complex = "parent_complex";
    child = unset_conf();
    merge_enabled_fields(&child, &parent);
    TEST_ASSERT(child.enabled_source == ENABLED_COMPLEX, "child should inherit complex source");
    TEST_ASSERT(child.enabled_complex == parent.enabled_complex, "child should inherit complex pointer");

    parent = unset_conf();
    parent.enabled = 1;
    parent.enabled_source = ENABLED_COMPLEX;
    parent.enabled_complex = "parent_complex";
    child = unset_conf();
    child.enabled = 0;
    child.enabled_source = ENABLED_STATIC;
    child.enabled_complex = "stale_value";
    merge_enabled_fields(&child, &parent);
    TEST_ASSERT(child.enabled_source == ENABLED_STATIC, "child static source should override parent");
    TEST_ASSERT(child.enabled == 0, "child static value should be preserved");
    TEST_ASSERT(child.enabled_complex == NULL, "child static source should clear complex pointer");

    parent = unset_conf();
    parent.enabled = 1;
    parent.enabled_source = ENABLED_STATIC;
    parent.enabled_complex = NULL;
    child = unset_conf();
    child.enabled = 0;
    child.enabled_source = ENABLED_COMPLEX;
    child.enabled_complex = "child_complex";
    merge_enabled_fields(&child, &parent);
    TEST_ASSERT(child.enabled_source == ENABLED_COMPLEX, "child complex source should override parent static");
    TEST_ASSERT(child.enabled_complex != NULL, "child complex pointer should be preserved");

    TEST_PASS("enabled merge behavior is correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("markdown_filter_runtime Tests\n");
    printf("========================================\n");

    test_parse_filter_flag_values();
    test_is_enabled_static_and_edge_cases();
    test_is_enabled_complex_resolution();
    test_body_phase_uses_cached_header_decision();
    test_enabled_merge_behavior();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
