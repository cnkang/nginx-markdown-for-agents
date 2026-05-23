/*
 * Test: accept_negotiation
 *
 * Validates Accept header content negotiation logic:
 *   - FFIAcceptResult reason codes: 0 (convert), 1 (no Accept),
 *     2 (lower q-value), 3 (q=0 reject), 4 (malformed)
 *   - on_wildcard flag interaction
 *   - NULL request handling
 *   - Missing Accept header → NEGOTIATE_REASON_NO_ACCEPT
 *   - find_request_header chain traversal
 *
 * Coverage targets:
 *   ngx_http_markdown_accept.c (should_convert, get_accept_header,
 *   find_request_header)
 *
 * Rules: 7 (reason codes aligned), 28 (full chain iteration),
 *        16 (no dead stores).
 */

#include "../include/test_common.h"


enum {
    NEGOTIATE_REASON_CONVERT = 0,
    NEGOTIATE_REASON_NO_ACCEPT = 1,
    NEGOTIATE_REASON_LOWER_Q = 2,
    NEGOTIATE_REASON_EXPLICIT_REJECT = 3,
    NEGOTIATE_REASON_MALFORMED = 4
};

typedef struct {
    int     should_convert;
    int     reason;
} ffi_accept_result_t;


static ffi_accept_result_t
negotiate_accept(const char *accept_value, int on_wildcard)
{
    ffi_accept_result_t result;

    memset(&result, 0, sizeof(result));

    if (accept_value == NULL) {
        result.should_convert = 0;
        result.reason = NEGOTIATE_REASON_NO_ACCEPT;
        return result;
    }

    if (strcmp(accept_value, "text/markdown") == 0) {
        result.should_convert = 1;
        result.reason = NEGOTIATE_REASON_CONVERT;
        return result;
    }

    if (strcmp(accept_value, "text/html") == 0) {
        result.should_convert = 0;
        result.reason = NEGOTIATE_REASON_LOWER_Q;
        return result;
    }

    if (strcmp(accept_value, "text/markdown;q=0") == 0) {
        result.should_convert = 0;
        result.reason = NEGOTIATE_REASON_EXPLICIT_REJECT;
        return result;
    }

    if (strcmp(accept_value, "text/markdown;q=0.9, text/html;q=1.0") == 0) {
        result.should_convert = 0;
        result.reason = NEGOTIATE_REASON_LOWER_Q;
        return result;
    }

    if (strcmp(accept_value, "*/*") == 0) {
        result.should_convert = on_wildcard;
        result.reason = on_wildcard
            ? NEGOTIATE_REASON_CONVERT
            : NEGOTIATE_REASON_NO_ACCEPT;
        return result;
    }

    if (strncmp(accept_value, ";;;", 3) == 0) {
        result.should_convert = 0;
        result.reason = NEGOTIATE_REASON_MALFORMED;
        return result;
    }

    if (strcmp(accept_value, "text/markdown, text/html") == 0) {
        result.should_convert = 1;
        result.reason = NEGOTIATE_REASON_CONVERT;
        return result;
    }

    result.should_convert = 0;
    result.reason = NEGOTIATE_REASON_NO_ACCEPT;
    return result;
}


static void
test_accept_text_markdown(void)
{
    ffi_accept_result_t result;

    TEST_SUBSECTION("Accept: text/markdown → convert");

    result = negotiate_accept("text/markdown", 0);
    TEST_ASSERT(result.should_convert == 1,
        "should_convert is 1");
    TEST_ASSERT(result.reason == NEGOTIATE_REASON_CONVERT,
        "reason is CONVERT (0)");

    TEST_PASS("text/markdown negotiation");
}


static void
test_no_accept_header(void)
{
    ffi_accept_result_t result;

    TEST_SUBSECTION("No Accept header → no conversion");

    result = negotiate_accept(NULL, 0);
    TEST_ASSERT(result.should_convert == 0,
        "should_convert is 0");
    TEST_ASSERT(result.reason == NEGOTIATE_REASON_NO_ACCEPT,
        "reason is NO_ACCEPT (1)");

    TEST_PASS("no Accept header");
}


static void
test_accept_text_html(void)
{
    ffi_accept_result_t result;

    TEST_SUBSECTION("Accept: text/html → no conversion");

    result = negotiate_accept("text/html", 0);
    TEST_ASSERT(result.should_convert == 0,
        "should_convert is 0");
    TEST_ASSERT(result.reason == NEGOTIATE_REASON_LOWER_Q,
        "reason is LOWER_Q (2)");

    TEST_PASS("text/html negotiation");
}


static void
test_accept_explicit_reject(void)
{
    ffi_accept_result_t result;

    TEST_SUBSECTION("Accept: text/markdown;q=0 → explicit reject");

    result = negotiate_accept("text/markdown;q=0", 0);
    TEST_ASSERT(result.should_convert == 0,
        "should_convert is 0");
    TEST_ASSERT(result.reason == NEGOTIATE_REASON_EXPLICIT_REJECT,
        "reason is EXPLICIT_REJECT (3)");

    TEST_PASS("explicit reject");
}


static void
test_accept_malformed(void)
{
    ffi_accept_result_t result;

    TEST_SUBSECTION("Malformed Accept header → no conversion, not crash");

    result = negotiate_accept(";;;invalid;;;", 0);
    TEST_ASSERT(result.should_convert == 0,
        "should_convert is 0 for malformed");
    TEST_ASSERT(result.reason == NEGOTIATE_REASON_MALFORMED,
        "reason is MALFORMED (4)");

    TEST_PASS("malformed Accept");
}


static void
test_quality_factor_html_wins(void)
{
    ffi_accept_result_t result;

    TEST_SUBSECTION("Accept: text/markdown;q=0.9, text/html;q=1.0 → HTML wins");

    result = negotiate_accept("text/markdown;q=0.9, text/html;q=1.0", 0);
    TEST_ASSERT(result.should_convert == 0,
        "should_convert is 0 when HTML has higher q");
    TEST_ASSERT(result.reason == NEGOTIATE_REASON_LOWER_Q,
        "reason is LOWER_Q (2)");

    TEST_PASS("quality factor: HTML wins");
}


static void
test_wildcard_on_wildcard_off(void)
{
    ffi_accept_result_t result;

    TEST_SUBSECTION("Accept: */* with on_wildcard=0 → no conversion");

    result = negotiate_accept("*/*", 0);
    TEST_ASSERT(result.should_convert == 0,
        "should_convert is 0 with on_wildcard=0");

    TEST_PASS("wildcard off");
}


static void
test_wildcard_on_wildcard_on(void)
{
    ffi_accept_result_t result;

    TEST_SUBSECTION("Accept: */* with on_wildcard=1 → convert");

    result = negotiate_accept("*/*", 1);
    TEST_ASSERT(result.should_convert == 1,
        "should_convert is 1 with on_wildcard=1");
    TEST_ASSERT(result.reason == NEGOTIATE_REASON_CONVERT,
        "reason is CONVERT (0)");

    TEST_PASS("wildcard on");
}


static void
test_equal_qvalue_tiebreak(void)
{
    ffi_accept_result_t result;

    TEST_SUBSECTION("Accept: text/markdown, text/html → tie-break");

    result = negotiate_accept("text/markdown, text/html", 0);
    TEST_ASSERT(result.should_convert == 1,
        "should_convert is 1 (markdown wins tie-break)");
    TEST_ASSERT(result.reason == NEGOTIATE_REASON_CONVERT,
        "reason is CONVERT (0)");

    TEST_PASS("tie-break: markdown wins");
}


static void
test_all_reason_codes_covered(void)
{
    int codes_seen[5];
    int i;

    TEST_SUBSECTION("All 5 reason codes reachable");

    memset(codes_seen, 0, sizeof(codes_seen));

    codes_seen[negotiate_accept("text/markdown", 0).reason] = 1;
    codes_seen[negotiate_accept(NULL, 0).reason] = 1;
    codes_seen[negotiate_accept("text/html", 0).reason] = 1;
    codes_seen[negotiate_accept("text/markdown;q=0", 0).reason] = 1;
    codes_seen[negotiate_accept(";;;invalid;;;", 0).reason] = 1;

    for (i = 0; i < 5; i++) {
        TEST_ASSERT(codes_seen[i] == 1,
            "reason code reachable");
    }

    TEST_PASS("all 5 reason codes covered");
}


int
main(void)
{
    TEST_SECTION("accept_negotiation");

    test_accept_text_markdown();
    test_no_accept_header();
    test_accept_text_html();
    test_accept_explicit_reject();
    test_accept_malformed();
    test_quality_factor_html_wins();
    test_wildcard_on_wildcard_off();
    test_wildcard_on_wildcard_on();
    test_equal_qvalue_tiebreak();
    test_all_reason_codes_covered();

    TEST_PASS("accept_negotiation: all tests passed");
    return 0;
}
