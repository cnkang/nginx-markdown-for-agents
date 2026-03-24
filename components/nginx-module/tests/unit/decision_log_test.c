/*
 * Test: decision_log
 * Description: decision log emission helper — failure classification
 *              and verbosity gating logic
 *
 * Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
 *
 * NOTE: This test redefines its own copies of the module enums and
 * helper functions rather than including the real header.  This means
 * it cannot catch issues like typedef ordering or constant drift.
 * For compile-time verification that the real header types are
 * consistent, see header_compile_test.c.  For full integration
 * coverage, use the e2e and integration test suites.
 */

#include "test_common.h"

/*
 * Minimal ngx_str_t definition matching NGINX's { len, data } layout.
 */

typedef unsigned char u_char;

typedef struct {
    size_t     len;
    u_char    *data;
} ngx_str_t;

#define ngx_string(str) { sizeof(str) - 1, (u_char *) str }
#define ngx_strncmp(s1, s2, n) strncmp((const char *) (s1), \
    (const char *) (s2), n)

/*
 * Module verbosity constants — mirrors the header definitions.
 */
#define NGX_HTTP_MARKDOWN_LOG_ERROR  0
#define NGX_HTTP_MARKDOWN_LOG_WARN   1
#define NGX_HTTP_MARKDOWN_LOG_INFO   2
#define NGX_HTTP_MARKDOWN_LOG_DEBUG  3

/*
 * NGINX log level constants (subset used by decision log).
 */
#define NGX_LOG_WARN   5
#define NGX_LOG_INFO   7

typedef int ngx_int_t;
typedef unsigned int ngx_uint_t;


/* Function prototypes */

static ngx_int_t is_failure_outcome(const ngx_str_t *reason_code);
static ngx_uint_t expected_log_level(const ngx_str_t *reason_code);
static int should_emit(ngx_uint_t verbosity,
    const ngx_str_t *reason_code);
static void test_failure_outcome_classification(void);
static void test_non_failure_outcome_classification(void);
static void test_log_level_selection(void);
static void test_verbosity_gating_info(void);
static void test_verbosity_gating_debug(void);
static void test_verbosity_gating_warn(void);
static void test_verbosity_gating_error(void);
static void test_null_inputs(void);


/*
 * Mirror of ngx_http_markdown_is_failure_outcome() from
 * ngx_http_markdown_decision_log_impl.h.
 *
 * Failure outcomes: reason codes starting with "ELIGIBLE_FAILED"
 * or "FAIL_".
 */
static ngx_int_t
is_failure_outcome(const ngx_str_t *reason_code)
{
    if (reason_code == NULL || reason_code->len == 0) {
        return 0;
    }

    /* Check for "ELIGIBLE_FAILED" prefix (15 chars) */
    if (reason_code->len >= 15
        && ngx_strncmp(reason_code->data,
                       (u_char *) "ELIGIBLE_FAILED", 15) == 0)
    {
        return 1;
    }

    /* Check for "FAIL_" prefix (5 chars) */
    if (reason_code->len >= 5
        && ngx_strncmp(reason_code->data,
                       (u_char *) "FAIL_", 5) == 0)
    {
        return 1;
    }

    return 0;
}


/*
 * Mirror of log level selection logic from
 * ngx_http_markdown_log_decision().
 *
 * NGX_LOG_WARN for failure outcomes, NGX_LOG_INFO otherwise.
 */
static ngx_uint_t
expected_log_level(const ngx_str_t *reason_code)
{
    return is_failure_outcome(reason_code) ? NGX_LOG_WARN
                                           : NGX_LOG_INFO;
}


/*
 * Mirror of verbosity gating logic from
 * ngx_http_markdown_log_decision().
 *
 * Returns:
 *   1 if a decision log entry should be emitted
 *   0 if suppressed by verbosity
 */
static int
should_emit(ngx_uint_t verbosity, const ngx_str_t *reason_code)
{
    ngx_int_t failure;

    failure = is_failure_outcome(reason_code);

    if (verbosity <= NGX_HTTP_MARKDOWN_LOG_WARN && !failure) {
        return 0;
    }

    return 1;
}


/* All 15 reason codes for iteration */

static ngx_str_t rc_skip_config = ngx_string("SKIP_CONFIG");
static ngx_str_t rc_skip_method = ngx_string("SKIP_METHOD");
static ngx_str_t rc_skip_status = ngx_string("SKIP_STATUS");
static ngx_str_t rc_skip_content_type =
    ngx_string("SKIP_CONTENT_TYPE");
static ngx_str_t rc_skip_size = ngx_string("SKIP_SIZE");
static ngx_str_t rc_skip_streaming =
    ngx_string("SKIP_STREAMING");
static ngx_str_t rc_skip_auth = ngx_string("SKIP_AUTH");
static ngx_str_t rc_skip_range = ngx_string("SKIP_RANGE");
static ngx_str_t rc_skip_accept = ngx_string("SKIP_ACCEPT");
static ngx_str_t rc_converted =
    ngx_string("ELIGIBLE_CONVERTED");
static ngx_str_t rc_failed_open =
    ngx_string("ELIGIBLE_FAILED_OPEN");
static ngx_str_t rc_failed_closed =
    ngx_string("ELIGIBLE_FAILED_CLOSED");
static ngx_str_t rc_fail_conversion =
    ngx_string("FAIL_CONVERSION");
static ngx_str_t rc_fail_resource =
    ngx_string("FAIL_RESOURCE_LIMIT");
static ngx_str_t rc_fail_system = ngx_string("FAIL_SYSTEM");


/*
 * Test: failure outcomes are correctly classified
 */
static void
test_failure_outcome_classification(void)
{
    TEST_SUBSECTION("Failure outcome classification");

    TEST_ASSERT(is_failure_outcome(&rc_failed_open) == 1,
                "ELIGIBLE_FAILED_OPEN is failure");
    TEST_ASSERT(is_failure_outcome(&rc_failed_closed) == 1,
                "ELIGIBLE_FAILED_CLOSED is failure");
    TEST_ASSERT(is_failure_outcome(&rc_fail_conversion) == 1,
                "FAIL_CONVERSION is failure");
    TEST_ASSERT(is_failure_outcome(&rc_fail_resource) == 1,
                "FAIL_RESOURCE_LIMIT is failure");
    TEST_ASSERT(is_failure_outcome(&rc_fail_system) == 1,
                "FAIL_SYSTEM is failure");

    TEST_PASS("All failure outcomes correctly classified");
}


/*
 * Test: non-failure outcomes are correctly classified
 */
static void
test_non_failure_outcome_classification(void)
{
    TEST_SUBSECTION("Non-failure outcome classification");

    TEST_ASSERT(is_failure_outcome(&rc_skip_config) == 0,
                "SKIP_CONFIG is not failure");
    TEST_ASSERT(is_failure_outcome(&rc_skip_method) == 0,
                "SKIP_METHOD is not failure");
    TEST_ASSERT(is_failure_outcome(&rc_skip_status) == 0,
                "SKIP_STATUS is not failure");
    TEST_ASSERT(is_failure_outcome(&rc_skip_content_type) == 0,
                "SKIP_CONTENT_TYPE is not failure");
    TEST_ASSERT(is_failure_outcome(&rc_skip_size) == 0,
                "SKIP_SIZE is not failure");
    TEST_ASSERT(is_failure_outcome(&rc_skip_streaming) == 0,
                "SKIP_STREAMING is not failure");
    TEST_ASSERT(is_failure_outcome(&rc_skip_auth) == 0,
                "SKIP_AUTH is not failure");
    TEST_ASSERT(is_failure_outcome(&rc_skip_range) == 0,
                "SKIP_RANGE is not failure");
    TEST_ASSERT(is_failure_outcome(&rc_skip_accept) == 0,
                "SKIP_ACCEPT is not failure");
    TEST_ASSERT(is_failure_outcome(&rc_converted) == 0,
                "ELIGIBLE_CONVERTED is not failure");

    TEST_PASS("All non-failure outcomes correctly classified");
}


/*
 * Test: NGINX log level matches outcome type (FR-03.5)
 */
static void
test_log_level_selection(void)
{
    TEST_SUBSECTION("NGINX log level selection");

    /* Non-failure outcomes use NGX_LOG_INFO */
    TEST_ASSERT(expected_log_level(&rc_skip_config) == NGX_LOG_INFO,
                "SKIP_CONFIG -> NGX_LOG_INFO");
    TEST_ASSERT(expected_log_level(&rc_skip_method) == NGX_LOG_INFO,
                "SKIP_METHOD -> NGX_LOG_INFO");
    TEST_ASSERT(expected_log_level(&rc_converted) == NGX_LOG_INFO,
                "ELIGIBLE_CONVERTED -> NGX_LOG_INFO");

    /* Failure outcomes use NGX_LOG_WARN */
    TEST_ASSERT(expected_log_level(&rc_failed_open) == NGX_LOG_WARN,
                "ELIGIBLE_FAILED_OPEN -> NGX_LOG_WARN");
    TEST_ASSERT(expected_log_level(&rc_failed_closed) == NGX_LOG_WARN,
                "ELIGIBLE_FAILED_CLOSED -> NGX_LOG_WARN");
    TEST_ASSERT(expected_log_level(&rc_fail_conversion) == NGX_LOG_WARN,
                "FAIL_CONVERSION -> NGX_LOG_WARN");
    TEST_ASSERT(expected_log_level(&rc_fail_resource) == NGX_LOG_WARN,
                "FAIL_RESOURCE_LIMIT -> NGX_LOG_WARN");
    TEST_ASSERT(expected_log_level(&rc_fail_system) == NGX_LOG_WARN,
                "FAIL_SYSTEM -> NGX_LOG_WARN");

    TEST_PASS("Log level selection correct for all outcomes");
}


/*
 * Test: info verbosity emits all outcomes (FR-03.4)
 */
static void
test_verbosity_gating_info(void)
{
    const ngx_str_t *all_codes[] = {
        &rc_skip_config, &rc_skip_method, &rc_skip_status,
        &rc_skip_content_type, &rc_skip_size,
        &rc_skip_streaming, &rc_skip_auth, &rc_skip_range,
        &rc_skip_accept, &rc_converted, &rc_failed_open,
        &rc_failed_closed, &rc_fail_conversion,
        &rc_fail_resource, &rc_fail_system
    };
    size_t i;

    TEST_SUBSECTION("Verbosity gating: info emits all");

    for (i = 0; i < ARRAY_SIZE(all_codes); i++) {
        TEST_ASSERT(
            should_emit(NGX_HTTP_MARKDOWN_LOG_INFO,
                        all_codes[i]) == 1,
            "info verbosity should emit all outcomes");
    }

    TEST_PASS("info verbosity emits all 15 outcomes");
}


/*
 * Test: debug verbosity emits all outcomes (FR-03.4)
 */
static void
test_verbosity_gating_debug(void)
{
    const ngx_str_t *all_codes[] = {
        &rc_skip_config, &rc_skip_method, &rc_skip_status,
        &rc_skip_content_type, &rc_skip_size,
        &rc_skip_streaming, &rc_skip_auth, &rc_skip_range,
        &rc_skip_accept, &rc_converted, &rc_failed_open,
        &rc_failed_closed, &rc_fail_conversion,
        &rc_fail_resource, &rc_fail_system
    };
    size_t i;

    TEST_SUBSECTION("Verbosity gating: debug emits all");

    for (i = 0; i < ARRAY_SIZE(all_codes); i++) {
        TEST_ASSERT(
            should_emit(NGX_HTTP_MARKDOWN_LOG_DEBUG,
                        all_codes[i]) == 1,
            "debug verbosity should emit all outcomes");
    }

    TEST_PASS("debug verbosity emits all 15 outcomes");
}


/*
 * Test: warn verbosity emits only failure outcomes (FR-03.4)
 */
static void
test_verbosity_gating_warn(void)
{
    TEST_SUBSECTION("Verbosity gating: warn emits failures only");

    /* Non-failure outcomes suppressed */
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
                    &rc_skip_config) == 0,
        "warn suppresses SKIP_CONFIG");
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
                    &rc_skip_method) == 0,
        "warn suppresses SKIP_METHOD");
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
                    &rc_converted) == 0,
        "warn suppresses ELIGIBLE_CONVERTED");

    /* Failure outcomes emitted */
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
                    &rc_failed_open) == 1,
        "warn emits ELIGIBLE_FAILED_OPEN");
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
                    &rc_failed_closed) == 1,
        "warn emits ELIGIBLE_FAILED_CLOSED");
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
                    &rc_fail_conversion) == 1,
        "warn emits FAIL_CONVERSION");
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
                    &rc_fail_resource) == 1,
        "warn emits FAIL_RESOURCE_LIMIT");
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
                    &rc_fail_system) == 1,
        "warn emits FAIL_SYSTEM");

    TEST_PASS("warn verbosity gates correctly");
}


/*
 * Test: error verbosity emits only failure outcomes (FR-03.4)
 */
static void
test_verbosity_gating_error(void)
{
    TEST_SUBSECTION("Verbosity gating: error emits failures only");

    /* Non-failure outcomes suppressed */
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_ERROR,
                    &rc_skip_config) == 0,
        "error suppresses SKIP_CONFIG");
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_ERROR,
                    &rc_converted) == 0,
        "error suppresses ELIGIBLE_CONVERTED");

    /* Failure outcomes emitted */
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_ERROR,
                    &rc_failed_open) == 1,
        "error emits ELIGIBLE_FAILED_OPEN");
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_ERROR,
                    &rc_failed_closed) == 1,
        "error emits ELIGIBLE_FAILED_CLOSED");
    TEST_ASSERT(
        should_emit(NGX_HTTP_MARKDOWN_LOG_ERROR,
                    &rc_fail_system) == 1,
        "error emits FAIL_SYSTEM");

    TEST_PASS("error verbosity gates correctly");
}


/*
 * Test: NULL inputs handled safely
 */
static void
test_null_inputs(void)
{
    ngx_str_t empty = { 0, NULL };

    TEST_SUBSECTION("NULL and empty input handling");

    TEST_ASSERT(is_failure_outcome(NULL) == 0,
                "NULL reason_code -> not failure");
    TEST_ASSERT(is_failure_outcome(&empty) == 0,
                "Empty reason_code -> not failure");

    TEST_PASS("NULL/empty inputs handled safely");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("decision_log Tests\n");
    printf("========================================\n");

    test_failure_outcome_classification();
    test_non_failure_outcome_classification();
    test_log_level_selection();
    test_verbosity_gating_info();
    test_verbosity_gating_debug();
    test_verbosity_gating_warn();
    test_verbosity_gating_error();
    test_null_inputs();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
