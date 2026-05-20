/*
 * Test: reason_code_ffi
 *
 * Validates: REQ-0700-RUST-006 (B06.2)
 *
 * Tests the C-side FFI accessor wrappers that call through to the
 * Rust-defined reason code enum.  Since unit tests do not link against
 * the Rust library, this file provides stub implementations of the
 * Rust FFI functions to verify the C wrapper logic.
 */

#include "../include/test_common.h"

/*
 * Minimal NGINX type stubs for ngx_int_t (needed by the wrapper).
 * The test_common.h already provides stdint.h and string.h.
 * We define the NGINX types that the production code expects.
 */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef unsigned char   u_char;

typedef struct {
    size_t      len;
    u_char     *data;
} ngx_str_t;

#define NGX_OK        0
#define NGX_ERROR    -1
#define NGX_DECLINED -5

/*
 * Stub implementations of the Rust FFI functions.
 *
 * These simulate the behavior of the real Rust functions defined in
 * components/rust-converter/src/decision/reason_code.rs.
 */

static const char *stub_reason_strs[] = {
    "CONVERTED",
    "SKIPPED_ACCEPT",
    "SKIPPED_NO_ACCEPT",
    "SKIPPED_CONDITIONAL",
    "FAILED_DECOMPRESSION",
    "DECOMPRESSION_BUDGET_EXCEEDED",
    "DECOMPRESSION_FORMAT_ERROR",
    "DECOMPRESSION_TRUNCATED_INPUT",
    "DECOMPRESSION_IO_ERROR",
    "PARSE_TIMEOUT",
    "PARSE_BUDGET_EXCEEDED",
    "REPLAY_BUFFER_ERROR",
    "SKIPPED_ACCEPT_REJECT",
    "FFI_CALL_ERROR",
    "NOT_ELIGIBLE",
    "DISABLED",
    "FAILED_OPEN",
    "FAILED_CLOSED",
};

static const char *stub_metric_keys[] = {
    "markdown_conversions_total",
    "markdown_skipped_accept_total",
    "markdown_skipped_no_accept_total",
    "markdown_skipped_conditional_total",
    "markdown_failed_decompression_total",
    "markdown_decompression_budget_exceeded_total",
    "markdown_decompression_format_error_total",
    "markdown_decompression_truncated_input_total",
    "markdown_decompression_io_error_total",
    "markdown_parse_timeouts_total",
    "markdown_parse_budget_exceeded_total",
    "markdown_replay_buffer_errors_total",
    "markdown_skipped_accept_reject_total",
    "markdown_ffi_call_errors_total",
    "markdown_skipped_not_eligible_total",
    "markdown_skipped_disabled_total",
    "markdown_failed_open_total",
    "markdown_failed_closed_total",
};

#define STUB_REASON_CODE_COUNT 18

const uint8_t *
markdown_reason_code_str(uint32_t code, uintptr_t *out_len)
{
    if (code >= STUB_REASON_CODE_COUNT) {
        if (out_len != NULL) {
            *out_len = 0;
        }
        return NULL;
    }

    if (out_len != NULL) {
        *out_len = strlen(stub_reason_strs[code]);
    }
    return (const uint8_t *) stub_reason_strs[code];
}

const uint8_t *
markdown_reason_code_metric_key(uint32_t code, uintptr_t *out_len)
{
    if (code >= STUB_REASON_CODE_COUNT) {
        if (out_len != NULL) {
            *out_len = 0;
        }
        return NULL;
    }

    if (out_len != NULL) {
        *out_len = strlen(stub_metric_keys[code]);
    }
    return (const uint8_t *) stub_metric_keys[code];
}

uint32_t
markdown_reason_code_count(void)
{
    return STUB_REASON_CODE_COUNT;
}

/*
 * Inline the production wrapper functions directly.
 * We cannot #include the .c file because it includes NGINX headers
 * that conflict with our stubs.  Instead we replicate the logic here
 * (it is trivial and the real integration is tested via make build).
 */

ngx_int_t
ngx_http_markdown_get_reason_code_str(uint32_t code, ngx_str_t *out_str)
{
    const uint8_t  *ptr;
    uintptr_t       len;

    if (out_str == NULL) {
        return NGX_ERROR;
    }

    len = 0;
    ptr = markdown_reason_code_str(code, &len);

    if (ptr == NULL) {
        out_str->len = 0;
        out_str->data = NULL;
        return NGX_DECLINED;
    }

    out_str->len = len;
    out_str->data = (u_char *) ptr;

    return NGX_OK;
}

ngx_int_t
ngx_http_markdown_get_reason_code_metric_key(uint32_t code,
    ngx_str_t *out_str)
{
    const uint8_t  *ptr;
    uintptr_t       len;

    if (out_str == NULL) {
        return NGX_ERROR;
    }

    len = 0;
    ptr = markdown_reason_code_metric_key(code, &len);

    if (ptr == NULL) {
        out_str->len = 0;
        out_str->data = NULL;
        return NGX_DECLINED;
    }

    out_str->len = len;
    out_str->data = (u_char *) ptr;

    return NGX_OK;
}

uint32_t
ngx_http_markdown_reason_code_total_count(void)
{
    return markdown_reason_code_count();
}


/* ── Tests ─────────────────────────────────────────────────────── */

/*
 * Test: valid reason code returns NGX_OK and populates ngx_str_t
 */
static void
test_get_reason_code_str_valid(void)
{
    ngx_str_t  str;
    ngx_int_t  rc;

    TEST_SUBSECTION("get_reason_code_str with valid codes");

    rc = ngx_http_markdown_get_reason_code_str(0, &str);
    TEST_ASSERT(rc == NGX_OK, "code 0 should return NGX_OK");
    TEST_ASSERT(str.len == strlen("CONVERTED"),
                "code 0 length should match CONVERTED");
    TEST_ASSERT(memcmp(str.data, "CONVERTED", str.len) == 0,
                "code 0 data should be CONVERTED");

    rc = ngx_http_markdown_get_reason_code_str(9, &str);
    TEST_ASSERT(rc == NGX_OK, "code 9 should return NGX_OK");
    TEST_ASSERT(str.len == strlen("PARSE_TIMEOUT"),
                "code 9 length should match PARSE_TIMEOUT");
    TEST_ASSERT(memcmp(str.data, "PARSE_TIMEOUT", str.len) == 0,
                "code 9 data should be PARSE_TIMEOUT");

    rc = ngx_http_markdown_get_reason_code_str(17, &str);
    TEST_ASSERT(rc == NGX_OK, "code 17 should return NGX_OK");
    TEST_ASSERT(str.len == strlen("FAILED_CLOSED"),
                "code 17 length should match FAILED_CLOSED");
    TEST_ASSERT(memcmp(str.data, "FAILED_CLOSED", str.len) == 0,
                "code 17 data should be FAILED_CLOSED");

    TEST_PASS("Valid reason code strings returned correctly");
}


/*
 * Test: invalid reason code returns NGX_DECLINED and zeroes ngx_str_t
 */
static void
test_get_reason_code_str_invalid(void)
{
    ngx_str_t  str;
    ngx_int_t  rc;

    TEST_SUBSECTION("get_reason_code_str with invalid codes");

    str.len = 99;
    str.data = (u_char *) "garbage";
    rc = ngx_http_markdown_get_reason_code_str(255, &str);
    TEST_ASSERT(rc == NGX_DECLINED, "code 255 should return NGX_DECLINED");
    TEST_ASSERT(str.len == 0, "invalid code should zero len");
    TEST_ASSERT(str.data == NULL, "invalid code should NULL data");

    rc = ngx_http_markdown_get_reason_code_str(18, &str);
    TEST_ASSERT(rc == NGX_DECLINED,
                "code 18 (one past last) should return NGX_DECLINED");

    TEST_PASS("Invalid reason codes handled correctly");
}


/*
 * Test: NULL output pointer returns NGX_ERROR
 */
static void
test_get_reason_code_str_null_output(void)
{
    ngx_int_t  rc;

    TEST_SUBSECTION("get_reason_code_str with NULL output");

    rc = ngx_http_markdown_get_reason_code_str(0, NULL);
    TEST_ASSERT(rc == NGX_ERROR,
                "NULL output pointer should return NGX_ERROR");

    TEST_PASS("NULL output pointer handled correctly");
}


/*
 * Test: valid metric key returns NGX_OK and populates ngx_str_t
 */
static void
test_get_reason_code_metric_key_valid(void)
{
    ngx_str_t  str;
    ngx_int_t  rc;

    TEST_SUBSECTION("get_reason_code_metric_key with valid codes");

    rc = ngx_http_markdown_get_reason_code_metric_key(0, &str);
    TEST_ASSERT(rc == NGX_OK, "code 0 metric key should return NGX_OK");
    TEST_ASSERT(str.len == strlen("markdown_conversions_total"),
                "code 0 metric key length correct");
    TEST_ASSERT(memcmp(str.data, "markdown_conversions_total", str.len) == 0,
                "code 0 metric key data correct");

    rc = ngx_http_markdown_get_reason_code_metric_key(5, &str);
    TEST_ASSERT(rc == NGX_OK, "code 5 metric key should return NGX_OK");
    TEST_ASSERT(memcmp(str.data,
                       "markdown_decompression_budget_exceeded_total",
                       str.len) == 0,
                "code 5 metric key data correct");

    TEST_PASS("Valid metric keys returned correctly");
}


/*
 * Test: invalid metric key returns NGX_DECLINED
 */
static void
test_get_reason_code_metric_key_invalid(void)
{
    ngx_str_t  str;
    ngx_int_t  rc;

    TEST_SUBSECTION("get_reason_code_metric_key with invalid codes");

    rc = ngx_http_markdown_get_reason_code_metric_key(100, &str);
    TEST_ASSERT(rc == NGX_DECLINED,
                "code 100 metric key should return NGX_DECLINED");
    TEST_ASSERT(str.len == 0, "invalid metric key should zero len");
    TEST_ASSERT(str.data == NULL, "invalid metric key should NULL data");

    TEST_PASS("Invalid metric keys handled correctly");
}


/*
 * Test: total count accessor returns expected value
 */
static void
test_reason_code_total_count(void)
{
    uint32_t count;

    TEST_SUBSECTION("reason_code_total_count");

    count = ngx_http_markdown_reason_code_total_count();
    TEST_ASSERT(count == STUB_REASON_CODE_COUNT,
                "total count should match REASON_CODE_COUNT");
    TEST_ASSERT(count == 18, "total count should be 18");

    TEST_PASS("Total count accessor correct");
}


/*
 * Test: all valid codes produce non-empty strings
 */
static void
test_all_codes_produce_strings(void)
{
    ngx_str_t  str;
    ngx_int_t  rc;
    uint32_t   count;
    uint32_t   i;

    TEST_SUBSECTION("All valid codes produce non-empty strings");

    count = ngx_http_markdown_reason_code_total_count();

    for (i = 0; i < count; i++) {
        rc = ngx_http_markdown_get_reason_code_str(i, &str);
        TEST_ASSERT(rc == NGX_OK, "each valid code should return NGX_OK");
        TEST_ASSERT(str.len > 0, "each valid code should have non-empty str");
        TEST_ASSERT(str.data != NULL,
                    "each valid code should have non-NULL data");
    }

    for (i = 0; i < count; i++) {
        rc = ngx_http_markdown_get_reason_code_metric_key(i, &str);
        TEST_ASSERT(rc == NGX_OK,
                    "each valid code metric key should return NGX_OK");
        TEST_ASSERT(str.len > 0,
                    "each valid code metric key should be non-empty");
        TEST_ASSERT(str.data != NULL,
                    "each valid code metric key should be non-NULL");
    }

    TEST_PASS("All valid codes produce non-empty strings and metric keys");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("reason_code_ffi Tests (B06.2)\n");
    printf("========================================\n");

    test_get_reason_code_str_valid();
    test_get_reason_code_str_invalid();
    test_get_reason_code_str_null_output();
    test_get_reason_code_metric_key_valid();
    test_get_reason_code_metric_key_invalid();
    test_reason_code_total_count();
    test_all_codes_produce_strings();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
