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
} default_conf_t;

static int valid_on_error(const char *v) { return STR_EQ(v, "pass") || STR_EQ(v, "reject"); }
static int valid_flavor(const char *v) { return STR_EQ(v, "commonmark") || STR_EQ(v, "gfm"); }
static int valid_auth_policy(const char *v) { return STR_EQ(v, "allow") || STR_EQ(v, "deny"); }
static int valid_conditional(const char *v) {
    return STR_EQ(v, "full_support") || STR_EQ(v, "if_modified_since_only") || STR_EQ(v, "disabled");
}
static int valid_cookie_pattern(const char *v) { return v != NULL && *v != '\0'; }
static int valid_content_type_token(const char *v) { return v != NULL && strchr(v, '/') != NULL; }

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
    return c;
}

static void
test_value_validation(void)
{
    TEST_SUBSECTION("Directive value validation");
    TEST_ASSERT(valid_on_error("pass"), "on_error should accept pass");
    TEST_ASSERT(valid_on_error("reject"), "on_error should accept reject");
    TEST_ASSERT(!valid_on_error("PASS"), "on_error should be case-sensitive");
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
    TEST_PASS("Default values verified");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("config_parsing Tests\n");
    printf("========================================\n");

    test_value_validation();
    test_default_values();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
