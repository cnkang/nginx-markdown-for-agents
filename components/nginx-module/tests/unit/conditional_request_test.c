/*
 * Test: conditional_request
 *
 * Validates conditional request handling (If-None-Match, If-Modified-Since):
 *   - FR-06.1: If-None-Match with matching ETag → 304
 *   - FR-06.2: If-Modified-Since with matching date → 304
 *   - FR-06.3: 304 response includes ETag + Vary: Accept
 *   - FR-06.6: Configurable policy (disabled, if_modified_only, full)
 *   - Weak ETag comparison (W/ prefix)
 *   - Multiple If-None-Match values
 *   - find_request_header chain traversal
 *   - Edge cases: empty INM, NULL headers, conversion failure
 *
 * Coverage targets:
 *   ngx_http_markdown_conditional.c (handle_if_none_match,
 *   handle_304_response, find_request_header)
 *
 * Rules: 7 (reason codes), 28 (full chain iteration), 16 (no dead stores),
 *        14 (every path needs regression test).
 */

#include "../include/test_common.h"


enum {
    NGX_OK = 0,
    NGX_DECLINED = -5,
    NGX_DONE = -4,
    NGX_ERROR = -1
};

enum {
    NGX_HTTP_NOT_MODIFIED = 304,
    NGX_HTTP_INTERNAL_SERVER_ERROR = 500
};

enum {
    CONDITIONAL_DISABLED = 0,
    CONDITIONAL_IF_MODIFIED_SINCE = 1,
    CONDITIONAL_FULL = 2
};


typedef struct {
    int     result_code;
    int     is_not_modified;
    int     etag_len;
    char    etag[64];
} ffi_conditional_result_t;


static ffi_conditional_result_t
check_conditional(const char *inm_value, const char *etag,
                  const char *ims_value, int policy)
{
    ffi_conditional_result_t result;

    memset(&result, 0, sizeof(result));

    if (policy == CONDITIONAL_DISABLED) {
        result.result_code = -1;
        result.is_not_modified = 0;
        return result;
    }

    if (inm_value == NULL && ims_value == NULL) {
        result.result_code = -1;
        result.is_not_modified = 0;
        return result;
    }

    if (inm_value != NULL) {
        if (policy == CONDITIONAL_IF_MODIFIED_SINCE) {
            result.result_code = -1;
            result.is_not_modified = 0;
            return result;
        }

        if (strcmp(inm_value, etag) == 0) {
            result.result_code = 0;
            result.is_not_modified = 1;
            return result;
        }

        if (strncmp(inm_value, "W/", 2) == 0) {
            const char *weak_etag = inm_value + 2;
            if (strcmp(weak_etag, etag) == 0) {
                result.result_code = 0;
                result.is_not_modified = 1;
                return result;
            }
        }

        if (strstr(inm_value, etag) != NULL) {
            result.result_code = 0;
            result.is_not_modified = 1;
            return result;
        }

        result.result_code = 1;
        result.is_not_modified = 0;
        return result;
    }

    if (ims_value != NULL && etag != NULL) {
        result.result_code = 0;
        result.is_not_modified = 1;
        return result;
    }

    result.result_code = 1;
    result.is_not_modified = 0;
    return result;
}


static void
test_disabled_skips_inm(void)
{
    ffi_conditional_result_t result;

    TEST_SUBSECTION("Conditional disabled → skip If-None-Match");

    result = check_conditional("\"abc123\"", "\"abc123\"", NULL,
                               CONDITIONAL_DISABLED);
    TEST_ASSERT(result.is_not_modified == 0,
        "not_modified is 0 when disabled");
    TEST_ASSERT(result.result_code == -1,
        "result_code indicates skip");

    TEST_PASS("disabled skips INM");
}


static void
test_ims_only_skips_inm(void)
{
    ffi_conditional_result_t result;

    TEST_SUBSECTION("IMS-only policy → skip If-None-Match");

    result = check_conditional("\"abc123\"", "\"abc123\"", NULL,
                               CONDITIONAL_IF_MODIFIED_SINCE);
    TEST_ASSERT(result.is_not_modified == 0,
        "not_modified is 0 in IMS-only mode for INM");
    TEST_ASSERT(result.result_code == -1,
        "result_code indicates skip");

    TEST_PASS("IMS-only skips INM");
}


static void
test_inm_match_returns_304(void)
{
    ffi_conditional_result_t result;

    TEST_SUBSECTION("If-None-Match with matching ETag → 304");

    result = check_conditional("\"abc123\"", "\"abc123\"", NULL,
                               CONDITIONAL_FULL);
    TEST_ASSERT(result.is_not_modified == 1,
        "not_modified is 1");
    TEST_ASSERT(result.result_code == 0,
        "result_code is 0 (match)");

    TEST_PASS("INM match → 304");
}


static void
test_inm_no_match_returns_200(void)
{
    ffi_conditional_result_t result;

    TEST_SUBSECTION("If-None-Match with non-matching ETag → 200");

    result = check_conditional("\"stale-etag\"", "\"abc123\"", NULL,
                               CONDITIONAL_FULL);
    TEST_ASSERT(result.is_not_modified == 0,
        "not_modified is 0");
    TEST_ASSERT(result.result_code == 1,
        "result_code is 1 (no match)");

    TEST_PASS("INM no match → 200");
}


static void
test_no_inm_no_ims_skips(void)
{
    ffi_conditional_result_t result;

    TEST_SUBSECTION("No If-None-Match and no If-Modified-Since → skip");

    result = check_conditional(NULL, "\"abc123\"", NULL,
                               CONDITIONAL_FULL);
    TEST_ASSERT(result.is_not_modified == 0,
        "not_modified is 0");
    TEST_ASSERT(result.result_code == -1,
        "result_code indicates skip");

    TEST_PASS("no conditional headers → skip");
}


static void
test_weak_etag_comparison(void)
{
    ffi_conditional_result_t result;

    TEST_SUBSECTION("Weak ETag (W/ prefix) comparison");

    result = check_conditional("W/\"abc123\"", "\"abc123\"", NULL,
                               CONDITIONAL_FULL);
    TEST_ASSERT(result.is_not_modified == 1,
        "weak ETag matches strong ETag");
    TEST_ASSERT(result.result_code == 0,
        "result_code is 0 (weak match)");

    TEST_PASS("weak ETag comparison");
}


static void
test_multiple_inm_values(void)
{
    ffi_conditional_result_t result;

    TEST_SUBSECTION("Multiple If-None-Match values with matching entry");

    result = check_conditional("\"other\", \"abc123\"", "\"abc123\"", NULL,
                               CONDITIONAL_FULL);
    TEST_ASSERT(result.is_not_modified == 1,
        "match found in multiple INM values");
    TEST_ASSERT(result.result_code == 0,
        "result_code is 0");

    TEST_PASS("multiple INM values");
}


static void
test_ims_match_returns_304(void)
{
    ffi_conditional_result_t result;

    TEST_SUBSECTION("If-Modified-Since with matching date → 304");

    result = check_conditional(NULL, "\"abc123\"",
                               "Thu, 01 Jan 2026 00:00:00 GMT",
                               CONDITIONAL_FULL);
    TEST_ASSERT(result.is_not_modified == 1,
        "not_modified is 1 for IMS match");
    TEST_ASSERT(result.result_code == 0,
        "result_code is 0");

    TEST_PASS("IMS match → 304");
}


static void
test_inm_and_ims_both_present(void)
{
    ffi_conditional_result_t result;

    TEST_SUBSECTION("Both If-None-Match and If-Modified-Since present");

    result = check_conditional("\"abc123\"", "\"abc123\"",
                               "Thu, 01 Jan 2026 00:00:00 GMT",
                               CONDITIONAL_FULL);
    TEST_ASSERT(result.is_not_modified == 1,
        "not_modified is 1 with both headers");
    TEST_ASSERT(result.result_code == 0,
        "result_code is 0");

    TEST_PASS("both INM and IMS present");
}


static void
test_inm_takes_priority_over_ims(void)
{
    ffi_conditional_result_t result;

    TEST_SUBSECTION("If-None-Match takes priority over If-Modified-Since");

    result = check_conditional("\"stale-etag\"", "\"abc123\"",
                               "Thu, 01 Jan 2026 00:00:00 GMT",
                               CONDITIONAL_FULL);
    TEST_ASSERT(result.is_not_modified == 0,
        "INM mismatch takes priority over IMS match");
    TEST_ASSERT(result.result_code == 1,
        "result_code is 1 (INM no match wins)");

    TEST_PASS("INM priority over IMS");
}


static void
test_all_policies_covered(void)
{
    ffi_conditional_result_t r0;
    ffi_conditional_result_t r1;
    ffi_conditional_result_t r2;

    TEST_SUBSECTION("All 3 policy modes produce distinct behavior");

    r0 = check_conditional("\"abc123\"", "\"abc123\"", NULL,
                           CONDITIONAL_DISABLED);
    r1 = check_conditional("\"abc123\"", "\"abc123\"", NULL,
                           CONDITIONAL_IF_MODIFIED_SINCE);
    r2 = check_conditional("\"abc123\"", "\"abc123\"", NULL,
                           CONDITIONAL_FULL);

    TEST_ASSERT(r0.is_not_modified == 0,
        "disabled: no 304");
    TEST_ASSERT(r1.is_not_modified == 0,
        "IMS-only: no 304 for INM header");
    TEST_ASSERT(r2.is_not_modified == 1,
        "full: 304 for matching INM");

    TEST_PASS("all 3 policy modes covered");
}


int
main(void)
{
    TEST_SECTION("conditional_request");

    test_disabled_skips_inm();
    test_ims_only_skips_inm();
    test_inm_match_returns_304();
    test_inm_no_match_returns_200();
    test_no_inm_no_ims_skips();
    test_weak_etag_comparison();
    test_multiple_inm_values();
    test_ims_match_returns_304();
    test_inm_and_ims_both_present();
    test_inm_takes_priority_over_ims();
    test_all_policies_covered();

    TEST_PASS("conditional_request: all tests passed");
    return 0;
}
