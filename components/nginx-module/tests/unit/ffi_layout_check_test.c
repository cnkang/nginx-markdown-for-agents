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
test_abi_version_alignment(void)
{
    TEST_ASSERT(ngx_http_markdown_ffi_abi_matches(MARKDOWN_ABI_VERSION),
        "matching ABI version must be accepted");
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_matches(MARKDOWN_ABI_VERSION + 1),
        "mismatched ABI version must be rejected");
}


static void
test_abi_tuple_handshake(void)
{
    /* Full tuple match */
    TEST_ASSERT(ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
        "full tuple handshake must pass with correct values");

    /* Numeric mismatch */
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION + 1, MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
        "numeric ABI mismatch must fail the tuple handshake");

    /* Header hash mismatch */
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH ^ 1,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
        "header hash mismatch must fail the tuple handshake");

    /* Symbol set hash mismatch */
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH ^ 1, MARKDOWN_LAYOUT_FINGERPRINT),
        "symbol set hash mismatch must fail the tuple handshake");

    /* Layout fingerprint mismatch */
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT ^ 1),
        "layout fingerprint mismatch must fail the tuple handshake");
}


/*
 * Startup-rejection contract (task 11.3; Requirements LTS-R023;
 * design 14(b), Error Handling).
 *
 * The 4-tuple handshake is the gate the NGINX module evaluates during
 * preconfiguration before it trusts a linked Rust archive. This test
 * proves an old or otherwise mismatched binary is REJECTED at that gate
 * and can never silently load:
 *
 *   - The previous incompatible ABI (version 2) carrying the CURRENT
 *     header/symbol-set/layout fingerprints is rejected. This is the
 *     concrete "old binary" case: a stale library reporting the current
 *     hashes but the prior ABI number must not pass.
 *   - A mismatch in EACH of the four legs, taken independently, is
 *     rejected, so a single stale leg is sufficient to fail the gate.
 *
 * MARKDOWN_ABI_VERSION is 3 for this release; the immediately prior
 * incompatible ABI was 2 (design 14(b)(h): dynconf export removal and
 * MarkdownOptions selector-field removal).
 */
static void
test_abi_old_binary_startup_rejection(void)
{
    const uint32_t old_abi_version = MARKDOWN_ABI_VERSION - 1; /* = 2 */

    /* Sanity: this release is ABI 3, so the "old" ABI under test is 2. */
    TEST_ASSERT(MARKDOWN_ABI_VERSION == 3,
        "this release must be ABI version 3");
    TEST_ASSERT(old_abi_version == 2,
        "the prior incompatible ABI under test must be version 2");

    /* Baseline: the current 4-tuple is accepted (gate opens for a match). */
    TEST_ASSERT(ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
        "current 4-tuple must be accepted at startup");

    /*
     * Old binary: prior ABI (2) reporting the CURRENT hashes must be
     * rejected. Proves the ABI leg alone blocks a stale library and it
     * never silently loads.
     */
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
        old_abi_version, MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
        "old ABI 2 with current hashes must be rejected at startup");

    /* Each leg independently mismatched must be rejected. */

    /* Wrong ABI only (distinct wrong value, not merely a bit flip). */
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
        old_abi_version, MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
        "wrong ABI leg alone must reject the handshake");

    /* Wrong header hash only. */
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH + 1,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT),
        "wrong header-hash leg alone must reject the handshake");

    /* Wrong symbol-set hash only. */
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH + 1, MARKDOWN_LAYOUT_FINGERPRINT),
        "wrong symbol-set-hash leg alone must reject the handshake");

    /* Wrong layout fingerprint only. */
    TEST_ASSERT(!ngx_http_markdown_ffi_abi_tuple_matches(
        MARKDOWN_ABI_VERSION, MARKDOWN_HEADER_HASH,
        MARKDOWN_SYMBOL_SET_HASH, MARKDOWN_LAYOUT_FINGERPRINT + 1),
        "wrong layout-fingerprint leg alone must reject the handshake");
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
    /*
     * The four custom-selector fields (prune_selectors, prune_selector_len,
     * prune_protection_selectors, prune_protection_selector_len) were removed
     * from MarkdownOptions in 0.9.2 (ABI 2 -> 3; design 14(h), LTS-R009 /
     * LTS-R023), shrinking the struct from 128 to 96 bytes.
     */
    opts.memory_budget = 0;
    opts.parse_timeout_ms = 0;
    opts.parser_memory_budget = 0;
    opts.flush_threshold = 0;

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
    test_error_codes_compile();
    test_abi_version_alignment();
    test_abi_tuple_handshake();
    test_abi_old_binary_startup_rejection();
    test_markdown_options_field_access();
    test_ffi_header_entry_field_access();
    test_ffi_header_plan_field_access();

    TEST_PASS("ffi_layout_check: all tests passed");
    return 0;
}
