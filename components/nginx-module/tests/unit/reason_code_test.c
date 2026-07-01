/*
 * Test: reason_code
 *
 * Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 13.2
 *
 * NOTE: This test includes the real module header and reason
 * implementation (ngx_http_markdown_reason.c) so it verifies the
 * actual production enum values and lookup functions.  It does not
 * cover NGINX runtime integration (pool allocation, logging) —
 * for that, see the e2e and integration test suites.
 *
 * As of v0.9.0, reason code strings are lowercase snake_case and
 * sourced from the Rust FFI (via stubs in this test).
 */

#include "../include/test_common.h"
#include <ctype.h>

#include <ngx_http_markdown_filter_module.h>


/*
 * Stub the FFI accessor for standalone unit testing.
 * The real accessor lives in ngx_http_markdown_reason_ffi.c and
 * calls into Rust.  For unit tests we provide a local stub.
 */
static const char *stub_reason_strs[] = {
    "converted",                     /* 0 */
    "skipped_accept",                /* 1 */
    "skipped_no_accept",             /* 2 */
    "skipped_conditional",           /* 3 */
    "decompression_error",           /* 4 */
    "decompression_budget_exceeded", /* 5 */
    "decompression_format_error",    /* 6 */
    "decompression_truncated_input", /* 7 */
    "decompression_io_error",        /* 8 */
    "timeout",                       /* 9 */
    "budget_exceeded",               /* 10 */
    "replay_error",                  /* 11 */
    "skipped_accept_reject",         /* 12 */
    "ffi_panic",                     /* 13 */
    "not_eligible",                  /* 14 */
    "disabled",                      /* 15 */
    "failed_open",                   /* 16 */
    "failed_closed",                 /* 17 */
    "conversion_error",              /* 18 */
    "memory_budget_exceeded",        /* 19 */
    "overload",                      /* 20 */
    "invalid_dynconf",               /* 21 */
    "degraded_snapshot",             /* 22 */
    "header_plan_apply_error",       /* 23 */
    "streaming_mid_flight_error",    /* 24 */
};

#define STUB_REASON_CODE_COUNT 25

ngx_int_t
ngx_http_markdown_get_reason_code_str(uint32_t code, ngx_str_t *out_str)
{
    if (out_str == NULL) {
        return NGX_ERROR;
    }

    if (code >= STUB_REASON_CODE_COUNT) {
        out_str->data = NULL;
        out_str->len = 0;
        return NGX_DECLINED;
    }

    out_str->data = (u_char *) stub_reason_strs[code];
    out_str->len = strlen(stub_reason_strs[code]);
    return NGX_OK;
}


/* Include the production implementation (uses our stub above) */
#include "../src/ngx_http_markdown_reason.c"


/* Function prototypes */

static void test_lowercase_snake_case_format(void);

/*
 * Forward-declare reason code accessors so the compilation unit
 * is self-contained even when the analyzer cannot resolve the
 * NGINX include paths for <ngx_http_markdown_filter_module.h>.
 */
const ngx_str_t *ngx_http_markdown_reason_converted(void);
const ngx_str_t *ngx_http_markdown_reason_failed_open(void);
const ngx_str_t *ngx_http_markdown_reason_failed_closed(void);
const ngx_str_t *ngx_http_markdown_reason_skip_accept(void);
const ngx_str_t *ngx_http_markdown_reason_skip_no_accept(void);
const ngx_str_t *ngx_http_markdown_reason_skip_accept_reject(void);
const ngx_str_t *ngx_http_markdown_reason_skip_conditional(void);


/*
 * Check if an ngx_str_t matches lowercase snake_case: ^[a-z][a-z0-9_]*$
 *
 * Returns:
 *   1 if the string matches
 *   0 otherwise
 */
static int
matches_lowercase_snake_case(const ngx_str_t *s)
{
    if (s == NULL || s->data == NULL || s->len == 0) {
        return 0;
    }

    if (!islower((unsigned char) s->data[0])) {
        return 0;
    }

    for (size_t i = 1; i < s->len; i++) {
        unsigned char ch = s->data[i];
        if (!islower(ch) && !isdigit(ch) && ch != '_') {
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
 *
 * In schema v1, all INELIGIBLE_* map to "not_eligible" except
 * INELIGIBLE_CONFIG which maps to "disabled".
 */
static void
test_eligibility_reason_codes(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("Eligibility enum to reason code mapping");

    rc = ngx_http_markdown_reason_from_eligibility(
        NGX_HTTP_MARKDOWN_INELIGIBLE_CONFIG, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "disabled"),
                "INELIGIBLE_CONFIG -> disabled");

    rc = ngx_http_markdown_reason_from_eligibility(
        NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "not_eligible"),
                "INELIGIBLE_METHOD -> not_eligible");

    rc = ngx_http_markdown_reason_from_eligibility(
        NGX_HTTP_MARKDOWN_INELIGIBLE_STATUS, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "not_eligible"),
                "INELIGIBLE_STATUS -> not_eligible");

    rc = ngx_http_markdown_reason_from_eligibility(
        NGX_HTTP_MARKDOWN_INELIGIBLE_CONTENT_TYPE, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "not_eligible"),
                "INELIGIBLE_CONTENT_TYPE -> not_eligible");

    rc = ngx_http_markdown_reason_from_eligibility(
        NGX_HTTP_MARKDOWN_INELIGIBLE_SIZE, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "not_eligible"),
                "INELIGIBLE_SIZE -> not_eligible");

    rc = ngx_http_markdown_reason_from_eligibility(
        NGX_HTTP_MARKDOWN_INELIGIBLE_STREAMING, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "not_eligible"),
                "INELIGIBLE_STREAMING -> not_eligible");

    rc = ngx_http_markdown_reason_from_eligibility(
        NGX_HTTP_MARKDOWN_INELIGIBLE_AUTH, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "not_eligible"),
                "INELIGIBLE_AUTH -> not_eligible");

    rc = ngx_http_markdown_reason_from_eligibility(
        NGX_HTTP_MARKDOWN_INELIGIBLE_RANGE, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "not_eligible"),
                "INELIGIBLE_RANGE -> not_eligible");

    rc = ngx_http_markdown_reason_from_eligibility(
        NGX_HTTP_MARKDOWN_ELIGIBLE, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "ffi_panic"),
                "ELIGIBLE -> ffi_panic (fallback)");

    /* Unknown value falls back to ffi_panic */
    rc = ngx_http_markdown_reason_from_eligibility(
        (ngx_http_markdown_eligibility_t) 999, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "ffi_panic"),
                "Unknown eligibility -> ffi_panic");

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

    rc = ngx_http_markdown_reason_from_error_category(
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "conversion_error"),
                "ERROR_CONVERSION -> conversion_error");

    rc = ngx_http_markdown_reason_from_error_category(
        NGX_HTTP_MARKDOWN_ERROR_RESOURCE_LIMIT, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "memory_budget_exceeded"),
                "ERROR_RESOURCE_LIMIT -> memory_budget_exceeded");

    rc = ngx_http_markdown_reason_from_error_category(
        NGX_HTTP_MARKDOWN_ERROR_SYSTEM, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "ffi_panic"),
                "ERROR_SYSTEM -> ffi_panic");

    /* Unknown value falls back to ffi_panic */
    rc = ngx_http_markdown_reason_from_error_category(
        (ngx_http_markdown_error_category_t) 999, NULL);
    TEST_ASSERT(ngx_str_eq(rc, "ffi_panic"),
                "Unknown error category -> ffi_panic");

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
    TEST_ASSERT(ngx_str_eq(rc, "converted"),
                "ngx_http_markdown_reason_converted() -> converted");

    rc = ngx_http_markdown_reason_failed_open();
    TEST_ASSERT(ngx_str_eq(rc, "failed_open"),
                "ngx_http_markdown_reason_failed_open() -> failed_open");

    rc = ngx_http_markdown_reason_failed_closed();
    TEST_ASSERT(ngx_str_eq(rc, "failed_closed"),
                "ngx_http_markdown_reason_failed_closed() "
                "-> failed_closed");

    TEST_PASS("All eligible outcome codes correct");
}


/*
 * Test: skipped_accept reason code for Accept negotiation failure
 */
static void
test_skip_accept_code(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("Accept skip reason code");

    rc = ngx_http_markdown_reason_skip_accept();
    TEST_ASSERT(ngx_str_eq(rc, "skipped_accept"),
                "ngx_http_markdown_reason_skip_accept() "
                "-> skipped_accept");

    TEST_PASS("skipped_accept code correct");
}


/*
 * Test: skipped_no_accept reason code for missing Accept header
 */
static void
test_skip_no_accept_code(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("No-Accept skip reason code");

    rc = ngx_http_markdown_reason_skip_no_accept();
    TEST_ASSERT(ngx_str_eq(rc, "skipped_no_accept"),
                "ngx_http_markdown_reason_skip_no_accept() "
                "-> skipped_no_accept");

    TEST_PASS("skipped_no_accept code correct");
}


/*
 * Test: skipped_accept_reject reason code for explicit q=0 reject
 */
static void
test_skip_accept_reject_code(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("Accept reject reason code");

    rc = ngx_http_markdown_reason_skip_accept_reject();
    TEST_ASSERT(ngx_str_eq(rc, "skipped_accept_reject"),
                "ngx_http_markdown_reason_skip_accept_reject() "
                "-> skipped_accept_reject");

    TEST_PASS("skipped_accept_reject code correct");
}


/*
 * Test: skipped_conditional reason code for 304 Not Modified
 */
static void
test_skip_conditional_code(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("Conditional skip reason code");

    rc = ngx_http_markdown_reason_skip_conditional();
    TEST_ASSERT(ngx_str_eq(rc, "skipped_conditional"),
                "ngx_http_markdown_reason_skip_conditional() "
                "-> skipped_conditional");

    TEST_PASS("skipped_conditional code correct");
}


#ifdef MARKDOWN_STREAMING_ENABLED
/*
 * Test: streaming reason code accessor functions return
 * expected strings (still UPPERCASE for streaming-only codes).
 */
static void
test_streaming_reason_codes(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("Streaming reason codes");

    rc = ngx_http_markdown_reason_engine_streaming();
    TEST_ASSERT(ngx_str_eq(rc, "ENGINE_STREAMING"),
        "engine_streaming() -> ENGINE_STREAMING");

    rc = ngx_http_markdown_reason_streaming_convert();
    TEST_ASSERT(ngx_str_eq(rc, "STREAMING_CONVERT"),
        "streaming_convert() -> STREAMING_CONVERT");

    rc = ngx_http_markdown_reason_streaming_fallback();
    TEST_ASSERT(ngx_str_eq(rc, "STREAMING_FALLBACK_PREBUFFER"),
        "streaming_fallback() -> STREAMING_FALLBACK_PREBUFFER");

    rc = ngx_http_markdown_reason_streaming_fail_postcommit();
    TEST_ASSERT(ngx_str_eq(rc, "STREAMING_FAIL_POSTCOMMIT"),
        "streaming_fail_postcommit() -> STREAMING_FAIL_POSTCOMMIT");

    rc = ngx_http_markdown_reason_streaming_skip_unsupported();
    TEST_ASSERT(ngx_str_eq(rc, "STREAMING_SKIP_UNSUPPORTED"),
        "streaming_skip_unsupported() -> STREAMING_SKIP_UNSUPPORTED");

    rc = ngx_http_markdown_reason_streaming_skip_compressed();
    TEST_ASSERT(ngx_str_eq(rc, "STREAMING_SKIP_COMPRESSED"),
        "streaming_skip_compressed() -> STREAMING_SKIP_COMPRESSED");

    rc = ngx_http_markdown_reason_streaming_budget_exceeded();
    TEST_ASSERT(ngx_str_eq(rc, "STREAMING_BUDGET_EXCEEDED"),
        "streaming_budget_exceeded() -> STREAMING_BUDGET_EXCEEDED");

    rc = ngx_http_markdown_reason_streaming_precommit_failopen();
    TEST_ASSERT(ngx_str_eq(rc, "STREAMING_PRECOMMIT_FAILOPEN"),
        "streaming_precommit_failopen() -> STREAMING_PRECOMMIT_FAILOPEN");

    rc = ngx_http_markdown_reason_streaming_precommit_reject();
    TEST_ASSERT(ngx_str_eq(rc, "STREAMING_PRECOMMIT_REJECT"),
        "streaming_precommit_reject() -> STREAMING_PRECOMMIT_REJECT");

    rc = ngx_http_markdown_reason_streaming_shadow();
    TEST_ASSERT(ngx_str_eq(rc, "STREAMING_SHADOW"),
        "streaming_shadow() -> STREAMING_SHADOW");

    TEST_PASS("All streaming reason codes correct");
}
#endif /* MARKDOWN_STREAMING_ENABLED */


/*
 * Test: all non-streaming reason code strings match lowercase
 * snake_case ^[a-z][a-z0-9_]*$
 */
static void
test_lowercase_snake_case_format(void)
{
    const ngx_str_t *rc;

    TEST_SUBSECTION("Non-streaming reason codes are lowercase snake_case");

    rc = ngx_http_markdown_reason_converted();
    TEST_ASSERT(matches_lowercase_snake_case(rc),
                "converted matches lowercase snake_case");

    rc = ngx_http_markdown_reason_failed_open();
    TEST_ASSERT(matches_lowercase_snake_case(rc),
                "failed_open matches lowercase snake_case");

    rc = ngx_http_markdown_reason_failed_closed();
    TEST_ASSERT(matches_lowercase_snake_case(rc),
                "failed_closed matches lowercase snake_case");

    rc = ngx_http_markdown_reason_skip_accept();
    TEST_ASSERT(matches_lowercase_snake_case(rc),
                "skipped_accept matches lowercase snake_case");

    rc = ngx_http_markdown_reason_skip_no_accept();
    TEST_ASSERT(matches_lowercase_snake_case(rc),
                "skipped_no_accept matches lowercase snake_case");

    rc = ngx_http_markdown_reason_skip_accept_reject();
    TEST_ASSERT(matches_lowercase_snake_case(rc),
                "skipped_accept_reject matches lowercase snake_case");

    rc = ngx_http_markdown_reason_skip_conditional();
    TEST_ASSERT(matches_lowercase_snake_case(rc),
                "skipped_conditional matches lowercase snake_case");

    rc = ngx_http_markdown_reason_from_eligibility(
        NGX_HTTP_MARKDOWN_INELIGIBLE_METHOD, NULL);
    TEST_ASSERT(matches_lowercase_snake_case(rc),
                "not_eligible matches lowercase snake_case");

    rc = ngx_http_markdown_reason_from_error_category(
        NGX_HTTP_MARKDOWN_ERROR_CONVERSION, NULL);
    TEST_ASSERT(matches_lowercase_snake_case(rc),
                "conversion_error matches lowercase snake_case");

    TEST_PASS("All non-streaming reason codes match lowercase "
              "snake_case format");
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
    test_skip_no_accept_code();
    test_skip_accept_reject_code();
    test_skip_conditional_code();
#ifdef MARKDOWN_STREAMING_ENABLED
    test_streaming_reason_codes();
#endif
    test_lowercase_snake_case_format();

#ifdef MARKDOWN_STREAMING_ENABLED
    TEST_SUBSECTION("streaming auto accessor");
    TEST_ASSERT(ngx_http_markdown_reason_eligible_streaming_auto() != NULL,
        "eligible_streaming_auto should return non-NULL");
    TEST_ASSERT(
        ngx_http_markdown_reason_eligible_streaming_auto()->len > 0,
        "eligible_streaming_auto string should not be empty");

    TEST_ASSERT(
        ngx_http_markdown_reason_eligible_fullbuffer_auto() != NULL,
        "eligible_fullbuffer_auto should return non-NULL");
    TEST_ASSERT(
        ngx_http_markdown_reason_eligible_fullbuffer_auto()->len > 0,
        "eligible_fullbuffer_auto string should not be empty");
#endif

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
