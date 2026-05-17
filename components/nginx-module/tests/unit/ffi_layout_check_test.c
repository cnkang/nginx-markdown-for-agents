/*
 * Test: ffi_layout_check
 *
 * Compile-time verification that FFI struct layouts agree between
 * the C and Rust sides of the ABI boundary.  If this file compiles,
 * all _Static_assert checks in ngx_http_markdown_ffi_layout_check.h
 * have passed.
 *
 * Corresponds to tasks A07.2 and A07.10.
 */

#include "test_common.h"
#include <ngx_http_markdown_ffi_layout_check.h>


/*
 * Runtime smoke test: instantiate each struct and verify the
 * static-assert-validated fields are accessible without UB.
 */
static void
test_markdown_result_field_access(void)
{
    MarkdownResult r;
    ngx_memzero(&r, sizeof(r));

    r.markdown = NULL;
    r.markdown_len = 0;
    r.etag = NULL;
    r.etag_len = 0;
    r.token_estimate = 0;
    r.error_code = ERROR_SUCCESS;
    r.error_message = NULL;
    r.error_len = 0;
    r.peak_memory_estimate = 0;

    TEST_ASSERT(r.error_code == ERROR_SUCCESS, "error_code must be SUCCESS after init");
    TEST_ASSERT(r.markdown == NULL, "markdown must be NULL after init");
}


static void
test_ffi_accept_result_field_access(void)
{
    FFIAcceptResult r;
    ngx_memzero(&r, sizeof(r));

    r.should_convert = 1;
    r.reason = NEGOTIATE_REASON_CONVERT;

    TEST_ASSERT(r.should_convert == 1, "should_convert must be 1");
    TEST_ASSERT(r.reason == 0, "reason must be 0 for CONVERT");
}


static void
test_ffi_conditional_result_field_access(void)
{
    FFIConditionalResult r;
    ngx_memzero(&r, sizeof(r));

    r.result_code = 0;
    r.matched_etag_len = 0;

    TEST_ASSERT(r.result_code == 0, "result_code must be 0 for NotModified");
}


static void
test_ffi_decision_result_field_access(void)
{
    FFIDecisionResult r;
    ngx_memzero(&r, sizeof(r));

    r.decision = 0;
    r.reason_code = 0;

    TEST_ASSERT(r.decision == 0, "decision must be 0 for Convert");
}


static void
test_error_codes_compile(void)
{
    TEST_ASSERT(ERROR_SUCCESS == 0, "ERROR_SUCCESS must be 0");
    TEST_ASSERT(ERROR_PARSE == 1, "ERROR_PARSE must be 1");
    TEST_ASSERT(ERROR_ENCODING == 2, "ERROR_ENCODING must be 2");
    TEST_ASSERT(ERROR_TIMEOUT == 3, "ERROR_TIMEOUT must be 3");
    TEST_ASSERT(ERROR_MEMORY_LIMIT == 4, "ERROR_MEMORY_LIMIT must be 4");
    TEST_ASSERT(ERROR_INVALID_INPUT == 5, "ERROR_INVALID_INPUT must be 5");
    TEST_ASSERT(ERROR_DECOMPRESSION_BUDGET_EXCEEDED == 9, "ERROR_DECOMPRESSION_BUDGET_EXCEEDED must be 9");
    TEST_ASSERT(ERROR_PARSE_TIMEOUT == 10, "ERROR_PARSE_TIMEOUT must be 10");
    TEST_ASSERT(ERROR_PARSE_BUDGET_EXCEEDED == 11, "ERROR_PARSE_BUDGET_EXCEEDED must be 11");
    TEST_ASSERT(ERROR_INTERNAL == 99, "ERROR_INTERNAL must be 99");
}


int
main(void)
{
    test_markdown_result_field_access();
    test_ffi_accept_result_field_access();
    test_ffi_conditional_result_field_access();
    test_ffi_decision_result_field_access();
    test_error_codes_compile();

    TEST_LOG("ffi_layout_check: all tests passed");
    return 0;
}
