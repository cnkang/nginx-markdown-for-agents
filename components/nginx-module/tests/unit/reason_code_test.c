/*
 * Test: reason_code
 * Description: reason code lookup functions for decision logging
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 13.2
 *
 * NOTE: This test redefines its own copies of the module enums and
 * lookup functions rather than including the real header.  This means
 * it cannot catch issues like typedef ordering or enum value drift.
 * For compile-time verification that the real header types are
 * consistent, see header_compile_test.c.  For full integration
 * coverage, use the e2e and integration test suites.
 */

#include "test_common.h"
#include <ctype.h>

#include <ngx_http_markdown_filter_module.h>
#include "../src/ngx_http_markdown_reason.c"


/* Function prototypes */

static void test_snake_case_format(void);


/*
 * Check if an ngx_str_t matches uppercase snake_case: ^[A-Z][A-Z0-9_]*$
 *
 * Returns:
 *   1 if the string matches
 *   0 otherwise
 */
static int
matches_snake_case(const ngx_str_t *s)
{
    for (size_t i = 1; i < s->len; i++) {
        unsigned char ch = s->data[i];
        if (!isupper(ch) && !isdigit(ch) && ch != '_') {
            return 0;
        }
    }

    return 1;
}


/*
 * Compare ngx_str_t against a C string for equality.
 *
 * Returns:
 *   1 if equal
 *   0 otherwise
 */
static int
ngx_str_eq(const ngx_str_t *a, const char *expected)
{
    size_t expected_len;

    if (a == NULL || a->data == NULL || expected == NULL) {
        return 0;
    }

    /* safe: expected is guaranteed non-NULL by the guard above */
    expected_len = strlen(expected);  /* NOLINT — expected is a C literal */

    if (a->len != expected_len) {
        return 0;
    }

    return memcmp(a->data, expected, expected_len) == 0;
}


/*
 * Test: each eligibility enum value maps to the expected reason code
 */
static void
test_eligibility_reason_codes(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("Eligibility enum to reason code mapping");

    rc = ngx_http_markdown_reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_CONFIG"),
                "INELIGIBLE_CONFIG -> SKIP_CONFIG");

    rc = ngx_http_markdown_reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_METHOD"),
                "INELIGIBLE_METHOD -> SKIP_METHOD");

    rc = ngx_http_markdown_reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_STATUS"),
                "INELIGIBLE_STATUS -> SKIP_STATUS");

    rc = ngx_http_markdown_reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_CONTENT_TYPE"),
                "INELIGIBLE_CONTENT_TYPE -> SKIP_CONTENT_TYPE");

    rc = ngx_http_markdown_reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_SIZE"),
                "INELIGIBLE_SIZE -> SKIP_SIZE");

    rc = ngx_http_markdown_reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_STREAMING"),
                "INELIGIBLE_STREAMING -> SKIP_STREAMING");

    rc = ngx_http_markdown_reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_AUTH"),
                "INELIGIBLE_AUTH -> SKIP_AUTH");

    rc = ngx_http_markdown_reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_RANGE"),
                "INELIGIBLE_RANGE -> SKIP_RANGE");

    rc = ngx_http_markdown_reason_from_eligibility(NGX_HTTP_MARKDOWN_ELIGIBLE, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "FAIL_SYSTEM"),
                "ELIGIBLE -> FAIL_SYSTEM (log warning, returns fallback)");

    /* Unknown value falls back to FAIL_SYSTEM */
    rc = ngx_http_markdown_reason_from_eligibility((ngx_http_markdown_eligibility_t) 999, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "FAIL_SYSTEM"),
                "Unknown eligibility -> FAIL_SYSTEM");

    TEST_PASS("All eligibility reason codes correct");
}


/*
 * Test: each error category maps to the expected failure reason code
 */
static void
test_error_category_reason_codes(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("Error category to failure reason code mapping");

    rc = ngx_http_markdown_reason_from_error_category(NGX_HTTP_MARKDOWN_ERROR_CONVERSION, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "FAIL_CONVERSION"),
                "ERROR_CONVERSION -> FAIL_CONVERSION");

    rc = ngx_http_markdown_reason_from_error_category(NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "FAIL_RESOURCE_LIMIT"),
                "ERROR_RESOURCE_LIMIT -> FAIL_RESOURCE_LIMIT");

    rc = ngx_http_markdown_reason_from_error_category(NGX_HTTP_MARKDOWN_ERROR_SYSTEM, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "FAIL_SYSTEM"),
                "ERROR_SYSTEM -> FAIL_SYSTEM");

    /* Unknown value falls back to FAIL_SYSTEM */
    rc = ngx_http_markdown_reason_from_error_category((ngx_http_markdown_error_category_t) 999, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "FAIL_SYSTEM"),
                "Unknown error category -> FAIL_SYSTEM");

    TEST_PASS("All error category reason codes correct");
}


/*
 * Test: eligible outcome helper functions return expected strings
 */
static void
test_eligible_outcome_codes(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("Eligible outcome reason codes");

    rc = ngx_http_markdown_reason_converted();
    TEST_ASSERT(ngx_str_eq(rc, "ELIGIBLE_CONVERTED"),
                "ngx_http_markdown_reason_converted() -> ELIGIBLE_CONVERTED");

    rc = ngx_http_markdown_reason_failed_open();
    TEST_ASSERT(ngx_str_eq(rc, "ELIGIBLE_FAILED_OPEN"),
                "ngx_http_markdown_reason_failed_open() -> ELIGIBLE_FAILED_OPEN");

    rc = ngx_http_markdown_reason_failed_closed();
    TEST_ASSERT(ngx_str_eq(rc, "ELIGIBLE_FAILED_CLOSED"),
                "ngx_http_markdown_reason_failed_closed() -> ELIGIBLE_FAILED_CLOSED");

    TEST_PASS("All eligible outcome codes correct");
}


/*
 * Test: SKIP_ACCEPT reason code for Accept negotiation failure
 */
static void
test_skip_accept_code(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("Accept skip reason code");

    rc = ngx_http_markdown_reason_skip_accept();
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_ACCEPT"),
                "ngx_http_markdown_reason_skip_accept() -> SKIP_ACCEPT");

    TEST_PASS("SKIP_ACCEPT code correct");
}


/* All 15 reason code string pointers for format validation */

static const ngx_str_t *codes[] = {
    &ngx_http_markdown_reason_skip_config_str,
    &ngx_http_markdown_reason_skip_method_str,
    &ngx_http_markdown_reason_skip_status_str,
    &ngx_http_markdown_reason_skip_content_type_str,
    &ngx_http_markdown_reason_skip_size_str,
    &ngx_http_markdown_reason_skip_streaming_str,
    &ngx_http_markdown_reason_skip_auth_str,
    &ngx_http_markdown_reason_skip_range_str,
    &ngx_http_markdown_reason_skip_accept_str,
    &ngx_http_markdown_reason_converted_str,
    &ngx_http_markdown_reason_failed_open_str,
    &ngx_http_markdown_reason_failed_closed_str,
    &ngx_http_markdown_reason_fail_conversion_str,
    &ngx_http_markdown_reason_fail_resource_limit_str,
    &ngx_http_markdown_reason_fail_system_str
};


/*
 * Test: all reason code strings match uppercase snake_case ^[A-Z][A-Z0-9_]*$
 */
static void
test_snake_case_format(void)
{
    for (size_t i = 0; i < ARRAY_SIZE(codes); i++) {
        TEST_ASSERT(codes[i] != NULL,
                    "Reason code pointer should not be NULL");
        TEST_ASSERT(codes[i]->len > 0,
                    "Reason code string should not be empty");
        TEST_ASSERT(matches_snake_case(codes[i]),
                    "Reason code should match ^[A-Z][A-Z0-9_]*$");
    }

    TEST_PASS("All 15 reason codes match uppercase snake_case format");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("reason_code Tests\n");
    printf("========================================\n");

    test_eligibility_reason_codes();
    test_error_category_reason_codes();
    test_eligible_outcome_codes();
    test_skip_accept_code();
    test_snake_case_format();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
