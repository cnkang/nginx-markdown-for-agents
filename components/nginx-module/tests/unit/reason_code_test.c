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

/*
 * Minimal ngx_str_t definition matching NGINX's { len, data } layout.
 * The test mirrors the reason code lookup logic from
 * components/nginx-module/src/ngx_http_markdown_reason.c so that it
 * can run standalone without linking against the full module.
 */

typedef unsigned char u_char;

typedef struct {
    size_t     len;
    u_char    *data;
} ngx_str_t;

#define ngx_string(str) { sizeof(str) - 1, (u_char *) str }

/*
 * Eligibility enum — mirrors ngx_http_markdown_eligibility_t
 */
typedef enum {
    NGX_HTTP_MARKDOWN_ELIGIBLE = 0,
    NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD,
    NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS,
    NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE,
    NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE,
    NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING,
    NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH,
    NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE,
    NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG
} eligibility_t;

/*
 * Error category enum — mirrors ngx_http_markdown_error_category_t
 */
typedef enum {
    NGX_HTTP_MARKDOWN_ERROR_CONVERSION = 0,
    NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT,
    NGX_HTTP_MARKDOWN_ERROR_SYSTEM
} error_category_t;


/* Skip reason code strings */

static ngx_str_t reason_skip_config_str =
    ngx_string("SKIP_CONFIG");
static ngx_str_t reason_skip_method_str =
    ngx_string("SKIP_METHOD");
static ngx_str_t reason_skip_status_str =
    ngx_string("SKIP_STATUS");
static ngx_str_t reason_skip_content_type_str =
    ngx_string("SKIP_CONTENT_TYPE");
static ngx_str_t reason_skip_size_str =
    ngx_string("SKIP_SIZE");
static ngx_str_t reason_skip_streaming_str =
    ngx_string("SKIP_STREAMING");
static ngx_str_t reason_skip_auth_str =
    ngx_string("SKIP_AUTH");
static ngx_str_t reason_skip_range_str =
    ngx_string("SKIP_RANGE");
static ngx_str_t reason_skip_accept_str =
    ngx_string("SKIP_ACCEPT");

/* Eligible outcome reason code strings */

static ngx_str_t reason_converted_str =
    ngx_string("ELIGIBLE_CONVERTED");
static ngx_str_t reason_failed_open_str =
    ngx_string("ELIGIBLE_FAILED_OPEN");
static ngx_str_t reason_failed_closed_str =
    ngx_string("ELIGIBLE_FAILED_CLOSED");

/* Failure sub-classification reason code strings */

static ngx_str_t reason_fail_conversion_str =
    ngx_string("FAIL_CONVERSION");
static ngx_str_t reason_fail_resource_limit_str =
    ngx_string("FAIL_RESOURCE_LIMIT");
static ngx_str_t reason_fail_system_str =
    ngx_string("FAIL_SYSTEM");


/* Function prototypes */

static const ngx_str_t *reason_from_eligibility(eligibility_t e);
static const ngx_str_t *reason_from_error_category(error_category_t c);
static const ngx_str_t *reason_converted(void);
static const ngx_str_t *reason_failed_open(void);
static const ngx_str_t *reason_failed_closed(void);
static const ngx_str_t *reason_skip_accept(void);
static int matches_snake_case(const ngx_str_t *s);
static int ngx_str_eq(const ngx_str_t *a, const char *expected);
static void test_eligibility_reason_codes(void);
static void test_error_category_reason_codes(void);
static void test_eligible_outcome_codes(void);
static void test_skip_accept_code(void);
static void test_snake_case_format(void);


/*
 * Map eligibility enum to reason code string.
 * Mirrors ngx_http_markdown_reason_from_eligibility().
 */
static const ngx_str_t *
reason_from_eligibility(eligibility_t e)
{
    switch (e) {

    case NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG:
        return &reason_skip_config_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD:
        return &reason_skip_method_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS:
        return &reason_skip_status_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE:
        return &reason_skip_content_type_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE:
        return &reason_skip_size_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING:
        return &reason_skip_streaming_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH:
        return &reason_skip_auth_str;

    case NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE:
        return &reason_skip_range_str;

    case NGX_HTTP_MARKDOWN_ELIGIBLE:
        return &reason_converted_str;

    default:
        return &reason_fail_system_str;
    }
}


/*
 * Map error category enum to failure reason code string.
 * Mirrors ngx_http_markdown_reason_from_error_category().
 */
static const ngx_str_t *
reason_from_error_category(error_category_t c)
{
    switch (c) {

    case NGX_HTTP_MARKDOWN_ERROR_CONVERSION:
        return &reason_fail_conversion_str;

    case NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT:
        return &reason_fail_resource_limit_str;

    case NGX_HTTP_MARKDOWN_ERROR_SYSTEM:
        return &reason_fail_system_str;

    default:
        return &reason_fail_system_str;
    }
}


static const ngx_str_t *
reason_converted(void)
{
    return &reason_converted_str;
}


static const ngx_str_t *
reason_failed_open(void)
{
    return &reason_failed_open_str;
}


static const ngx_str_t *
reason_failed_closed(void)
{
    return &reason_failed_closed_str;
}


static const ngx_str_t *
reason_skip_accept(void)
{
    return &reason_skip_accept_str;
}


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
    size_t i;

    if (s == NULL || s->data == NULL || s->len == 0) {
        return 0;
    }

    /* First character must be uppercase letter */
    if (!isupper((unsigned char) s->data[0])) {
        return 0;
    }

    /* Remaining characters must be uppercase letter, digit, or underscore */
    for (i = 1; i < s->len; i++) {
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

    if (a == NULL || expected == NULL) {
        return 0;
    }

    expected_len = strlen(expected);

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

    rc = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_CONFIG"),
                "INELIGIBLE_CONFIG -> SKIP_CONFIG");

    rc = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_METHOD"),
                "INELIGIBLE_METHOD -> SKIP_METHOD");

    rc = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_STATUS"),
                "INELIGIBLE_STATUS -> SKIP_STATUS");

    rc = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_CONTENT_TYPE"),
                "INELIGIBLE_CONTENT_TYPE -> SKIP_CONTENT_TYPE");

    rc = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_SIZE"),
                "INELIGIBLE_SIZE -> SKIP_SIZE");

    rc = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_STREAMING"),
                "INELIGIBLE_STREAMING -> SKIP_STREAMING");

    rc = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_AUTH"),
                "INELIGIBLE_AUTH -> SKIP_AUTH");

    rc = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE);
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_RANGE"),
                "INELIGIBLE_RANGE -> SKIP_RANGE");

    rc = reason_from_eligibility(NGX_HTTP_MARKDOWN_ELIGIBLE);
    TEST_ASSERT(ngx_str_eq(rc, "ELIGIBLE_CONVERTED"),
                "ELIGIBLE -> ELIGIBLE_CONVERTED (fallback)");

    /* Unknown value falls back to FAIL_SYSTEM */
    rc = reason_from_eligibility((eligibility_t) 999);
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

    rc = reason_from_error_category(NGX_HTTP_MARKDOWN_ERROR_CONVERSION);
    TEST_ASSERT(ngx_str_eq(rc, "FAIL_CONVERSION"),
                "ERROR_CONVERSION -> FAIL_CONVERSION");

    rc = reason_from_error_category(NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT);
    TEST_ASSERT(ngx_str_eq(rc, "FAIL_RESOURCE_LIMIT"),
                "ERROR_RESOURCE_LIMIT -> FAIL_RESOURCE_LIMIT");

    rc = reason_from_error_category(NGX_HTTP_MARKDOWN_ERROR_SYSTEM);
    TEST_ASSERT(ngx_str_eq(rc, "FAIL_SYSTEM"),
                "ERROR_SYSTEM -> FAIL_SYSTEM");

    /* Unknown value falls back to FAIL_SYSTEM */
    rc = reason_from_error_category((error_category_t) 999);
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

    rc = reason_converted();
    TEST_ASSERT(ngx_str_eq(rc, "ELIGIBLE_CONVERTED"),
                "reason_converted() -> ELIGIBLE_CONVERTED");

    rc = reason_failed_open();
    TEST_ASSERT(ngx_str_eq(rc, "ELIGIBLE_FAILED_OPEN"),
                "reason_failed_open() -> ELIGIBLE_FAILED_OPEN");

    rc = reason_failed_closed();
    TEST_ASSERT(ngx_str_eq(rc, "ELIGIBLE_FAILED_CLOSED"),
                "reason_failed_closed() -> ELIGIBLE_FAILED_CLOSED");

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

    rc = reason_skip_accept();
    TEST_ASSERT(ngx_str_eq(rc, "SKIP_ACCEPT"),
                "reason_skip_accept() -> SKIP_ACCEPT");

    TEST_PASS("SKIP_ACCEPT code correct");
}


/*
 * Test: all reason code strings match uppercase snake_case ^[A-Z][A-Z0-9_]*$
 */
static void
test_snake_case_format(void)
{
    const ngx_str_t *codes[15];
    size_t           i;

    TEST_SUBSECTION("All reason codes match uppercase snake_case format");

    /* Collect all 15 reason codes */
    codes[0]  = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG);
    codes[1]  = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD);
    codes[2]  = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS);
    codes[3]  = reason_from_eligibility(
                    NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE);
    codes[4]  = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE);
    codes[5]  = reason_from_eligibility(
                    NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING);
    codes[6]  = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH);
    codes[7]  = reason_from_eligibility(NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE);
    codes[8]  = reason_skip_accept();
    codes[9]  = reason_converted();
    codes[10] = reason_failed_open();
    codes[11] = reason_failed_closed();
    codes[12] = reason_from_error_category(
                    NGX_HTTP_MARKDOWN_ERROR_CONVERSION);
    codes[13] = reason_from_error_category(
                    NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT);
    codes[14] = reason_from_error_category(NGX_HTTP_MARKDOWN_ERROR_SYSTEM);

    for (i = 0; i < ARRAY_SIZE(codes); i++) {
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
