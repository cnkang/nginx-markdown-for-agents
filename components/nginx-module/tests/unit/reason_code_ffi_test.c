/*
 * Test: reason_code_ffi
 *
 * Validates: REQ-0700-RUST-006 (B06.2)
 *
 * Tests the C-side FFI accessor wrappers that call through to the
 * Rust-defined reason code enum.  Since unit tests do not link against
 * the Rust library, this file provides stub implementations of the
 * Rust FFI functions to verify the C wrapper logic.
 *
 * Updated for schema v1 (26 reason codes, lowercase snake_case).
 */

#include "../include/test_common.h"

#include <ngx_config.h>
#include <ngx_core.h>

#define NGX_OK        0
#define NGX_ERROR    -1
#define NGX_DECLINED -5

/*
 * Stub implementations of the Rust FFI functions.
 *
 * These simulate the behavior of the real Rust functions defined in
 * components/rust-converter/src/decision/reason_code.rs (schema v1).
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
    "bypass_no_transform",           /* 25 */
};

static const char *stub_metric_keys[] = {
    "markdown_conversions_total",    /* 0 */
    "markdown_skipped_total",        /* 1 */
    "markdown_skipped_total",        /* 2 */
    "markdown_skipped_total",        /* 3 */
    "markdown_errors_total",         /* 4 */
    "markdown_errors_total",         /* 5 */
    "markdown_errors_total",         /* 6 */
    "markdown_errors_total",         /* 7 */
    "markdown_errors_total",         /* 8 */
    "markdown_errors_total",         /* 9 */
    "markdown_errors_total",         /* 10 */
    "markdown_errors_total",         /* 11 */
    "markdown_skipped_total",        /* 12 */
    "markdown_errors_total",         /* 13 */
    "markdown_skipped_total",        /* 14 */
    "markdown_skipped_total",        /* 15 */
    "markdown_failed_open_total",    /* 16 */
    "markdown_failed_closed_total",  /* 17 */
    "markdown_errors_total",         /* 18 */
    "markdown_errors_total",         /* 19 */
    "markdown_errors_total",         /* 20 */
    "markdown_errors_total",         /* 21 */
    "markdown_errors_total",         /* 22 */
    "markdown_errors_total",         /* 23 */
    "markdown_errors_total",         /* 24 */
    "markdown_skipped_total",        /* 25 — bypass_no_transform */
};

#define STUB_REASON_CODE_COUNT 26

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

#include "ngx_http_markdown_reason_ffi.c"


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
    TEST_ASSERT(str.len == strlen("converted"),
                "code 0 length should match 'converted'");
    TEST_ASSERT(memcmp(str.data, "converted", str.len) == 0,
                "code 0 data should be 'converted'");

    rc = ngx_http_markdown_get_reason_code_str(9, &str);
    TEST_ASSERT(rc == NGX_OK, "code 9 should return NGX_OK");
    TEST_ASSERT(str.len == strlen("timeout"),
                "code 9 length should match 'timeout'");
    TEST_ASSERT(memcmp(str.data, "timeout", str.len) == 0,
                "code 9 data should be 'timeout'");

    rc = ngx_http_markdown_get_reason_code_str(17, &str);
    TEST_ASSERT(rc == NGX_OK, "code 17 should return NGX_OK");
    TEST_ASSERT(str.len == strlen("failed_closed"),
                "code 17 length should match 'failed_closed'");
    TEST_ASSERT(memcmp(str.data, "failed_closed", str.len) == 0,
                "code 17 data should be 'failed_closed'");

    rc = ngx_http_markdown_get_reason_code_str(24, &str);
    TEST_ASSERT(rc == NGX_OK, "code 24 should return NGX_OK");
    TEST_ASSERT(str.len == strlen("streaming_mid_flight_error"),
                "code 24 length should match "
                "'streaming_mid_flight_error'");
    TEST_ASSERT(memcmp(str.data, "streaming_mid_flight_error",
                       str.len) == 0,
                "code 24 data should be "
                "'streaming_mid_flight_error'");

    rc = ngx_http_markdown_get_reason_code_str(25, &str);
    TEST_ASSERT(rc == NGX_OK, "code 25 should return NGX_OK");
    TEST_ASSERT(str.len == strlen("bypass_no_transform"),
                "code 25 length should match 'bypass_no_transform'");
    TEST_ASSERT(memcmp(str.data, "bypass_no_transform", str.len) == 0,
                "code 25 data should be 'bypass_no_transform'");

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

    rc = ngx_http_markdown_get_reason_code_str(26, &str);
    TEST_ASSERT(rc == NGX_DECLINED,
                "code 26 (one past last) should return NGX_DECLINED");

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
    TEST_ASSERT(memcmp(str.data, "markdown_conversions_total",
                       str.len) == 0,
                "code 0 metric key data correct");

    rc = ngx_http_markdown_get_reason_code_metric_key(5, &str);
    TEST_ASSERT(rc == NGX_OK, "code 5 metric key should return NGX_OK");
    TEST_ASSERT(memcmp(str.data,
                       "markdown_errors_total",
                       str.len) == 0,
                "code 5 metric key data correct");

    rc = ngx_http_markdown_get_reason_code_metric_key(16, &str);
    TEST_ASSERT(rc == NGX_OK,
                "code 16 metric key should return NGX_OK");
    TEST_ASSERT(memcmp(str.data,
                       "markdown_failed_open_total",
                       str.len) == 0,
                "code 16 metric key should be "
                "markdown_failed_open_total");

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
 * Test: total count accessor returns expected value (26)
 */
static void
test_reason_code_total_count(void)
{
    uint32_t count;

    TEST_SUBSECTION("reason_code_total_count");

    count = ngx_http_markdown_reason_code_total_count();
    TEST_ASSERT(count == STUB_REASON_CODE_COUNT,
                "total count should match REASON_CODE_COUNT");
    TEST_ASSERT(count == 26, "total count should be 26");

    TEST_PASS("Total count accessor correct");
}


/*
 * Test: code 25 (bypass_no_transform) metric key is markdown_skipped_total
 */
static void
test_bypass_no_transform_metric_key(void)
{
    ngx_str_t  str;
    ngx_int_t  rc;

    TEST_SUBSECTION("bypass_no_transform metric key");

    rc = ngx_http_markdown_get_reason_code_metric_key(25, &str);
    TEST_ASSERT(rc == NGX_OK,
                "code 25 metric key should return NGX_OK");
    TEST_ASSERT(str.len == sizeof("markdown_skipped_total") - 1,
                "code 25 metric key length should match expected value");
    TEST_ASSERT(memcmp(str.data,
                       "markdown_skipped_total",
                       str.len) == 0,
                "code 25 metric key should be markdown_skipped_total");

    TEST_PASS("bypass_no_transform metric key correct");
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
        TEST_ASSERT(rc == NGX_OK,
                    "each valid code should return NGX_OK");
        TEST_ASSERT(str.len > 0,
                    "each valid code should have non-empty str");
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

    TEST_PASS("All valid codes produce non-empty strings "
              "and metric keys");
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
    test_bypass_no_transform_metric_key();
    test_all_codes_produce_strings();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
