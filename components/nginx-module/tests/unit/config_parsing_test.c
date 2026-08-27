/*
 * Test: config_parsing
 *
 * Validates directive value validation (flavor, auth_policy,
 * conditional_requests, stream_types, cookie patterns),
 * default configuration values.
 */

#include "test_common.h"

#define FLAVOR_COMMONMARK 0
#define FLAVOR_GFM 1
#define AUTH_ALLOW 0
#define AUTH_DENY 1
#define CACHE_VALIDATION_NONE    0
#define CACHE_VALIDATION_IF_MODIFIED_SINCE 1
#define CACHE_VALIDATION_DISABLED          2

typedef struct {
    int markdown_filter;
    int markdown_flavor;
    int markdown_token_estimate;
    int markdown_front_matter;
    int markdown_accept;
    int markdown_auth_policy;
    int markdown_cache_validation;
    int markdown_auto_decompress;
} default_conf_t;

/*
 * Validate a flavor directive value.
 *
 * Returns: 1 if the value is "commonmark" or "gfm", 0 otherwise.
 */
static int valid_flavor(const char *v) { return STR_EQ(v, "commonmark") || STR_EQ(v, "gfm"); }

/*
 * Validate an auth_policy directive value.
 *
 * Returns: 1 if the value is "allow" or "deny", 0 otherwise.
 */
static int valid_auth_policy(const char *v) { return STR_EQ(v, "allow") || STR_EQ(v, "deny"); }

/*
 * Check whether a character is a valid NGINX variable name start character
 * (underscore or ASCII letter).
 *
 * Returns: 1 if valid, 0 otherwise.
 */
static int valid_var_start_char(char c)
{
    return c == '_' || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
}

/*
 * Validate a complex markdown_filter directive value containing
 * NGINX variables (e.g. "$convert", "${convert}", "pre_$var_post").
 *
 * Returns: 1 if at least one valid variable reference is found, 0 otherwise.
 */
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

/*
 * Validate a markdown_filter directive value: "on", "off", or a
 * complex expression containing NGINX variables.
 *
 * Returns: 1 if valid, 0 otherwise.
 */
static int valid_markdown_filter(const char *v) {
    return STR_EQ(v, "on") || STR_EQ(v, "off") || valid_markdown_filter_complex(v);
}

/*
 * Validate a conditional_requests directive value.
 *
 * Returns: 1 if "full", "ims_only", or "off", 0 otherwise.
 */
static int valid_conditional(const char *v) {
    return STR_EQ(v, "full") || STR_EQ(v, "ims_only") || STR_EQ(v, "off");
}

/*
 * Validate a cookie pattern: must be non-NULL and non-empty.
 *
 * Returns: 1 if valid, 0 otherwise.
 */
static int valid_cookie_pattern(const char *v) { return v != NULL && *v != '\0'; }

/*
 * Validate a content type token: must contain a slash (type/subtype format).
 *
 * Returns: 1 if valid, 0 otherwise.
 */
static int valid_content_type_token(const char *v) { return v != NULL && strchr(v, '/') != NULL; }

/* markdown_metrics_format removed in 0.9.2 — Prometheus is the only format */

/*
 * Create a default_conf_t with the module's documented default values.
 *
 * Returns:
 *   A default_conf_t with all fields initialized to their defaults.
 */
static default_conf_t
module_defaults(void)
{
    default_conf_t c;
    c.markdown_filter = 0;
    c.markdown_flavor = FLAVOR_COMMONMARK;
    c.markdown_token_estimate = 0;
    c.markdown_front_matter = 0;
    c.markdown_accept = 0;
    c.markdown_auth_policy = AUTH_ALLOW;
    c.markdown_cache_validation = CACHE_VALIDATION_IF_MODIFIED_SINCE;
    c.markdown_auto_decompress = 1;
    return c;
}

/*
 * Verify directive value validation for all supported directives.
 * Tests accepted values, rejected values, and case sensitivity.
 *
 * Expected: valid_* functions return 1 for accepted values, 0 for rejected.
 */
static void
test_value_validation(void)
{
    TEST_SUBSECTION("Directive value validation");
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
    TEST_ASSERT(valid_conditional("full"), "conditional should accept full");
    TEST_ASSERT(valid_conditional("ims_only"), "conditional should accept ims_only");
    TEST_ASSERT(valid_conditional("off"), "conditional should accept off");
    TEST_ASSERT(!valid_conditional("enabled"), "conditional should reject invalid value");
    TEST_ASSERT(valid_content_type_token("text/event-stream"), "stream type must include slash");
    TEST_ASSERT(!valid_content_type_token("texteventstream"), "stream type without slash is invalid");
    TEST_ASSERT(valid_cookie_pattern("session*"), "cookie pattern should accept non-empty");
    TEST_ASSERT(!valid_cookie_pattern(""), "cookie pattern should reject empty");
    TEST_PASS("Directive validation passed");
}

/*
 * Verify that module_defaults() returns the documented default values
 * for all configuration directives.
 *
 * Expected: each default matches the module's documented specification.
 */
static void
test_default_values(void)
{
    default_conf_t c = module_defaults();
    TEST_SUBSECTION("Default values");

    TEST_ASSERT(c.markdown_filter == 0, "markdown_filter default off");
    TEST_ASSERT(c.markdown_flavor == FLAVOR_COMMONMARK, "flavor default commonmark");
    TEST_ASSERT(c.markdown_cache_validation == CACHE_VALIDATION_IF_MODIFIED_SINCE,
                "cache_validation default if_modified_since");
    TEST_ASSERT(c.markdown_auto_decompress == 1, "auto_decompress default on");
    TEST_PASS("Default values verified");
}

/*
 * markdown_metrics_format directive removed in 0.9.2 — Prometheus is
 * the sole format; parse_metrics_format no longer exists.
 */

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
