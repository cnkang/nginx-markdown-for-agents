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
    memset(&r, 0, sizeof(r));

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
    memset(&r, 0, sizeof(r));

    r.should_convert = 1;
    r.reason = NEGOTIATE_REASON_CONVERT;

    TEST_ASSERT(r.should_convert == 1, "should_convert must be 1");
    TEST_ASSERT(r.reason == 0, "reason must be 0 for CONVERT");
}


static void
test_ffi_conditional_result_field_access(void)
{
    FFIConditionalResult r;
    memset(&r, 0, sizeof(r));

    r.result_code = 0;
    r.matched_etag_len = 0;

    TEST_ASSERT(r.result_code == 0, "result_code must be 0 for NotModified");
}


static void
test_ffi_decision_result_field_access(void)
{
    FFIDecisionResult r;
    memset(&r, 0, sizeof(r));

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
    TEST_ASSERT(ERROR_DECOMPRESSION_FORMAT_ERROR == 12, "ERROR_DECOMPRESSION_FORMAT_ERROR must be 12");
    TEST_ASSERT(ERROR_DECOMPRESSION_TRUNCATED_INPUT == 13, "ERROR_DECOMPRESSION_TRUNCATED_INPUT must be 13");
    TEST_ASSERT(ERROR_DECOMPRESSION_IO_ERROR == 14, "ERROR_DECOMPRESSION_IO_ERROR must be 14");
    TEST_ASSERT(ERROR_INTERNAL == 99, "ERROR_INTERNAL must be 99");

    /* DECOMP_CATEGORY_* namespace (101-105, separate from ERROR_*) */
    TEST_ASSERT(DECOMP_CATEGORY_BUDGET_EXCEEDED == 101, "DECOMP_CATEGORY_BUDGET_EXCEEDED must be 101");
    TEST_ASSERT(DECOMP_CATEGORY_FORMAT_ERROR == 102, "DECOMP_CATEGORY_FORMAT_ERROR must be 102");
    TEST_ASSERT(DECOMP_CATEGORY_TRUNCATED_INPUT == 103, "DECOMP_CATEGORY_TRUNCATED_INPUT must be 103");
    TEST_ASSERT(DECOMP_CATEGORY_IO_ERROR == 104, "DECOMP_CATEGORY_IO_ERROR must be 104");
    TEST_ASSERT(DECOMP_CATEGORY_INVALID_ARGS == 105, "DECOMP_CATEGORY_INVALID_ARGS must be 105");
}


static void
test_markdown_options_field_access(void)
{
    MarkdownOptions opts;
    memset(&opts, 0, sizeof(opts));

    opts.flavor = 0;
    opts.timeout_ms = 5000;
    opts.generate_etag = 1;
    opts.estimate_tokens = 0;
    opts.front_matter = 0;
    opts.content_type = NULL;
    opts.content_type_len = 0;
    opts.base_url = NULL;
    opts.base_url_len = 0;
    opts.streaming_budget = 0;
    opts.prune_noise = 0;
    opts.prune_selectors = NULL;
    opts.prune_selector_len = 0;
    opts.prune_protection_selectors = NULL;
    opts.prune_protection_selector_len = 0;
    opts.memory_budget = 0;
    opts.llm_provider = 0;
    opts.chars_per_token_fixed = 0;
    opts.parse_timeout_ms = 0;
    opts.parser_memory_budget = 0;

    TEST_ASSERT(opts.flavor == 0, "flavor must be 0 after init");
    TEST_ASSERT(opts.timeout_ms == 5000, "timeout_ms must be 5000");
    TEST_ASSERT(opts.generate_etag == 1, "generate_etag must be 1");
    TEST_ASSERT(opts.content_type == NULL, "content_type must be NULL");
    TEST_ASSERT(opts.parse_timeout_ms == 0, "parse_timeout_ms must be 0");
    TEST_ASSERT(opts.parser_memory_budget == 0,
        "parser_memory_budget must be 0");
}


static void
test_ffi_header_entry_field_access(void)
{
    FFIHeaderEntry entry;
    memset(&entry, 0, sizeof(entry));

    entry.op_type = 0;
    entry.key = NULL;
    entry.key_len = 0;
    entry.value = NULL;
    entry.value_len = 0;

    TEST_ASSERT(entry.op_type == 0, "op_type must be 0 for set");
    TEST_ASSERT(entry.key == NULL, "key must be NULL after init");
}


static void
test_ffi_header_plan_field_access(void)
{
    FFIHeaderPlan plan;
    memset(&plan, 0, sizeof(plan));

    plan.handle = NULL;
    plan.entries = NULL;
    plan.count = 0;

    TEST_ASSERT(plan.handle == NULL, "handle must be NULL after init");
    TEST_ASSERT(plan.count == 0, "count must be 0 after init");
}


int
main(void)
{
    test_markdown_result_field_access();
    test_ffi_accept_result_field_access();
    test_ffi_conditional_result_field_access();
    test_ffi_decision_result_field_access();
    test_error_codes_compile();
    test_markdown_options_field_access();
    test_ffi_header_entry_field_access();
    test_ffi_header_plan_field_access();

    TEST_PASS("ffi_layout_check: all tests passed");
    return 0;
}
