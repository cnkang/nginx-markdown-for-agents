/*
 * Test: config_parsing
 * Description: configuration parsing
 */

#include "test_common.h"

#define ON_ERROR_PASS 0
#define ON_ERROR_REJECT 1
#define FLAVOR_COMMONMARK 0
#define FLAVOR_GFM 1
#define AUTH_ALLOW 0
#define AUTH_DENY 1
#define COND_FULL_SUPPORT 0
#define COND_IF_MODIFIED_SINCE_ONLY 1
#define COND_DISABLED 2
#define METRICS_FORMAT_AUTO 0
#define METRICS_FORMAT_PROMETHEUS 1

typedef struct {
    int markdown_filter;
    size_t markdown_max_size;
    unsigned long markdown_timeout_ms;
    int markdown_on_error;
    int markdown_flavor;
    int markdown_token_estimate;
    int markdown_front_matter;
    int markdown_on_wildcard;
    int markdown_auth_policy;
    int markdown_etag;
    int markdown_conditional_requests;
    int markdown_buffer_chunked;
    int markdown_auto_decompress;
    int markdown_metrics_format;
} default_conf_t;

static int valid_on_error(const char *v) { return STR_EQ(v, "pass") || STR_EQ(v, "reject"); }
static int valid_flavor(const char *v) { return STR_EQ(v, "commonmark") || STR_EQ(v, "gfm"); }
static int valid_auth_policy(const char *v) { return STR_EQ(v, "allow") || STR_EQ(v, "deny"); }
static int valid_var_start_char(char c)
{
    return c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
}

static int valid_markdown_filter_complex(const char *v)
{
    const char *p;

    if (v == NULL) {
        return 0;
    }

    p = v;
    while ((p = strchr(p, '$')) != NULL) {
        if (p[1] == '{') {
            const char *close = strchr(p + 2, '}');
            if (close != NULL && close > (p + 2)) {
                return 1;
            }
        } else if (valid_var_start_char(p[1])) {
            return 1;
        }
        p++;
    }

    return 0;
}

static int valid_markdown_filter(const char *v) {
    return STR_EQ(v, "on") || STR_EQ(v, "off") || valid_markdown_filter_complex(v);
}
static int valid_conditional(const char *v) {
    return STR_EQ(v, "full_support") || STR_EQ(v, "if_modified_since_only") || STR_EQ(v, "disabled");
}
static int valid_cookie_pattern(const char *v) { return v != NULL && *v != '\0'; }
static int valid_content_type_token(const char *v) { return v != NULL && strchr(v, '/') != NULL; }
static int valid_metrics_format(const char *v) { return STR_EQ(v, "auto") || STR_EQ(v, "prometheus"); }

static int
parse_metrics_format(const char *v)
{
    if (STR_EQ(v, "auto")) {
        return METRICS_FORMAT_AUTO;
    }
    if (STR_EQ(v, "prometheus")) {
        return METRICS_FORMAT_PROMETHEUS;
    }
    return -1;
}

static default_conf_t
module_defaults(void)
{
    default_conf_t c;
    c.markdown_filter = 0;
    c.markdown_max_size = 10 * 1024 * 1024;
    c.markdown_timeout_ms = 5000;
    c.markdown_on_error = ON_ERROR_PASS;
    c.markdown_flavor = FLAVOR_COMMONMARK;
    c.markdown_token_estimate = 0;
    c.markdown_front_matter = 0;
    c.markdown_on_wildcard = 0;
    c.markdown_auth_policy = AUTH_ALLOW;
    c.markdown_etag = 1;
    c.markdown_conditional_requests = COND_FULL_SUPPORT;
    c.markdown_buffer_chunked = 1;
    c.markdown_auto_decompress = 1;
    c.markdown_metrics_format = METRICS_FORMAT_AUTO;
    return c;
}

static void
test_value_validation(void)
{
    TEST_SUBSECTION("Directive value validation");
    TEST_ASSERT(valid_on_error("pass"), "on_error should accept pass");
    TEST_ASSERT(valid_on_error("reject"), "on_error should accept reject");
    TEST_ASSERT(!valid_on_error("PASS"), "on_error should be case-sensitive");
    TEST_ASSERT(valid_markdown_filter("on"), "markdown_filter should accept on");
    TEST_ASSERT(valid_markdown_filter("off"), "markdown_filter should accept off");
    TEST_ASSERT(valid_markdown_filter("$convert_html"), "markdown_filter should accept variable");
    TEST_ASSERT(valid_markdown_filter("${convert_html}"), "markdown_filter should accept braced variable");
    TEST_ASSERT(valid_markdown_filter("pre_$convert_html_post"), "markdown_filter should accept complex expression");
    TEST_ASSERT(!valid_markdown_filter("convert_html"), "markdown_filter should reject expression without variable");
    TEST_ASSERT(!valid_markdown_filter("$"), "markdown_filter should reject degenerate variable marker");
    TEST_ASSERT(!valid_markdown_filter("yes"), "markdown_filter should reject invalid static value");
    TEST_ASSERT(!valid_markdown_filter("1"), "markdown_filter should reject numeric literal");
    TEST_ASSERT(valid_flavor("commonmark"), "flavor should accept commonmark");
    TEST_ASSERT(valid_flavor("gfm"), "flavor should accept gfm");
    TEST_ASSERT(!valid_flavor("markdown"), "flavor should reject invalid value");
    TEST_ASSERT(valid_auth_policy("allow"), "auth_policy should accept allow");
    TEST_ASSERT(valid_auth_policy("deny"), "auth_policy should accept deny");
    TEST_ASSERT(!valid_auth_policy("block"), "auth_policy should reject invalid value");
    TEST_ASSERT(valid_conditional("full_support"), "conditional should accept full_support");
    TEST_ASSERT(valid_conditional("if_modified_since_only"), "conditional should accept if_modified_since_only");
    TEST_ASSERT(valid_conditional("disabled"), "conditional should accept disabled");
    TEST_ASSERT(!valid_conditional("enabled"), "conditional should reject invalid value");
    TEST_ASSERT(valid_content_type_token("text/event-stream"), "stream type must include slash");
    TEST_ASSERT(!valid_content_type_token("texteventstream"), "stream type without slash is invalid");
    TEST_ASSERT(valid_cookie_pattern("session*"), "cookie pattern should accept non-empty");
    TEST_ASSERT(!valid_cookie_pattern(""), "cookie pattern should reject empty");
    TEST_ASSERT(valid_metrics_format("auto"), "metrics_format should accept auto");
    TEST_ASSERT(valid_metrics_format("prometheus"), "metrics_format should accept prometheus");
    TEST_ASSERT(!valid_metrics_format("json"), "metrics_format should reject json");
    TEST_ASSERT(!valid_metrics_format("text"), "metrics_format should reject text");
    TEST_ASSERT(!valid_metrics_format(""), "metrics_format should reject empty");
    TEST_PASS("Directive validation passed");
}

static void
test_default_values(void)
{
    default_conf_t c = module_defaults();
    TEST_SUBSECTION("Default values");

    TEST_ASSERT(c.markdown_filter == 0, "markdown_filter default off");
    TEST_ASSERT(c.markdown_max_size == 10 * 1024 * 1024, "max_size default 10MB");
    TEST_ASSERT(c.markdown_timeout_ms == 5000, "timeout default 5s");
    TEST_ASSERT(c.markdown_on_error == ON_ERROR_PASS, "on_error default pass");
    TEST_ASSERT(c.markdown_flavor == FLAVOR_COMMONMARK, "flavor default commonmark");
    TEST_ASSERT(c.markdown_etag == 1, "etag default on");
    TEST_ASSERT(c.markdown_conditional_requests == COND_FULL_SUPPORT, "conditional default full_support");
    TEST_ASSERT(c.markdown_buffer_chunked == 1, "buffer_chunked default on");
    TEST_ASSERT(c.markdown_auto_decompress == 1, "auto_decompress default on");
    TEST_ASSERT(c.markdown_metrics_format == METRICS_FORMAT_AUTO, "metrics_format default auto");
    TEST_PASS("Default values verified");
}

static void
test_metrics_format_parsing(void)
{
    int result;

    TEST_SUBSECTION("markdown_metrics_format directive parsing");

    /* "auto" maps to METRICS_FORMAT_AUTO (0) */
    result = parse_metrics_format("auto");
    TEST_ASSERT(result == METRICS_FORMAT_AUTO,
                "auto should parse to METRICS_FORMAT_AUTO");
    TEST_PASS("auto -> METRICS_FORMAT_AUTO");

    /* "prometheus" maps to METRICS_FORMAT_PROMETHEUS (1) */
    result = parse_metrics_format("prometheus");
    TEST_ASSERT(result == METRICS_FORMAT_PROMETHEUS,
                "prometheus should parse to METRICS_FORMAT_PROMETHEUS");
    TEST_PASS("prometheus -> METRICS_FORMAT_PROMETHEUS");

    /* Invalid values are rejected */
    result = parse_metrics_format("json");
    TEST_ASSERT(result == -1, "json should be rejected");
    TEST_PASS("json rejected");

    result = parse_metrics_format("text");
    TEST_ASSERT(result == -1, "text should be rejected");
    TEST_PASS("text rejected");

    result = parse_metrics_format("openmetrics");
    TEST_ASSERT(result == -1, "openmetrics should be rejected");
    TEST_PASS("openmetrics rejected");

    result = parse_metrics_format("");
    TEST_ASSERT(result == -1, "empty string should be rejected");
    TEST_PASS("empty string rejected");

    /* Verify constant values match design */
    TEST_ASSERT(METRICS_FORMAT_AUTO == 0,
                "METRICS_FORMAT_AUTO must be 0");
    TEST_ASSERT(METRICS_FORMAT_PROMETHEUS == 1,
                "METRICS_FORMAT_PROMETHEUS must be 1");
    TEST_PASS("Constant values match design");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("config_parsing Tests\n");
    printf("========================================\n");

    test_value_validation();
    test_default_values();
    test_metrics_format_parsing();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
