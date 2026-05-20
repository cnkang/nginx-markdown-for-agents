/*
 * Test: decision_path
 *
 * Validates: REQ-0700-OPERABILITY-001 (structured decision path logging)
 *
 * Tests the decision path struct constants, failure detection logic,
 * and verbosity gating for the structured decision path log.
 */

#include "../include/test_common.h"
#include <string.h>

/*
 * Decision path component string constants — mirrors the header.
 */
#define NGX_HTTP_MARKDOWN_ACCEPT_CONVERT   "CONVERT"
#define NGX_HTTP_MARKDOWN_ACCEPT_SKIP      "SKIP"
#define NGX_HTTP_MARKDOWN_ACCEPT_REJECT    "REJECT"
#define NGX_HTTP_MARKDOWN_ACCEPT_NONE      "NONE"

#define NGX_HTTP_MARKDOWN_COND_NOT_MODIFIED "NOT_MODIFIED"
#define NGX_HTTP_MARKDOWN_COND_PROCEED     "PROCEED"
#define NGX_HTTP_MARKDOWN_COND_SKIPPED     "SKIPPED"

#define NGX_HTTP_MARKDOWN_CONV_SUCCESS     "SUCCESS"
#define NGX_HTTP_MARKDOWN_CONV_FAILED      "FAILED"
#define NGX_HTTP_MARKDOWN_CONV_SKIPPED     "SKIPPED"

/*
 * Module verbosity constants.
 */
#define NGX_HTTP_MARKDOWN_LOG_ERROR  0
#define NGX_HTTP_MARKDOWN_LOG_WARN   1
#define NGX_HTTP_MARKDOWN_LOG_INFO   2
#define NGX_HTTP_MARKDOWN_LOG_DEBUG  3

typedef unsigned long ngx_msec_t;

/*
 * Decision path struct — mirrors the header definition.
 */
typedef struct {
    const char   *accept_result;
    const char   *conditional_result;
    const char   *conversion_status;
    const char   *reason_code;
    ngx_msec_t    duration_ms;
} ngx_http_markdown_decision_path_t;


/* Function prototypes */
static int decision_path_is_failure(const char *status);
static int decision_path_should_emit(unsigned int verbosity,
    const char *conversion_status);
static void test_accept_result_constants(void);
static void test_conditional_result_constants(void);
static void test_conversion_status_constants(void);
static void test_failure_detection(void);
static void test_verbosity_gating(void);
static void test_decision_path_struct_init(void);
static void test_null_safety(void);


/*
 * Mirror of ngx_http_markdown_decision_path_is_failure().
 */
static int
decision_path_is_failure(const char *status)
{
    if (status == NULL) {
        return 0;
    }

    if (strcmp(status, NGX_HTTP_MARKDOWN_CONV_FAILED) == 0) {
        return 1;
    }

    return 0;
}


/*
 * Mirror of the verbosity gating logic in
 * ngx_http_markdown_log_decision_path().
 *
 * Returns 1 if the decision path log should be emitted.
 */
static int
decision_path_should_emit(unsigned int verbosity,
    const char *conversion_status)
{
    int  is_failure;

    is_failure = decision_path_is_failure(conversion_status);

    if (verbosity <= NGX_HTTP_MARKDOWN_LOG_WARN && !is_failure) {
        return 0;
    }

    return 1;
}


/*
 * Test: accept_result constants are distinct and non-empty.
 */
static void
test_accept_result_constants(void)
{
    TEST_SUBSECTION("accept_result constants");

    TEST_ASSERT(strlen(NGX_HTTP_MARKDOWN_ACCEPT_CONVERT) > 0,
        "CONVERT is non-empty");
    TEST_ASSERT(strlen(NGX_HTTP_MARKDOWN_ACCEPT_SKIP) > 0,
        "SKIP is non-empty");
    TEST_ASSERT(strlen(NGX_HTTP_MARKDOWN_ACCEPT_REJECT) > 0,
        "REJECT is non-empty");
    TEST_ASSERT(strlen(NGX_HTTP_MARKDOWN_ACCEPT_NONE) > 0,
        "NONE is non-empty");

    /* All distinct */
    TEST_ASSERT(
        strcmp(NGX_HTTP_MARKDOWN_ACCEPT_CONVERT,
               NGX_HTTP_MARKDOWN_ACCEPT_SKIP) != 0,
        "CONVERT != SKIP");
    TEST_ASSERT(
        strcmp(NGX_HTTP_MARKDOWN_ACCEPT_CONVERT,
               NGX_HTTP_MARKDOWN_ACCEPT_REJECT) != 0,
        "CONVERT != REJECT");
    TEST_ASSERT(
        strcmp(NGX_HTTP_MARKDOWN_ACCEPT_CONVERT,
               NGX_HTTP_MARKDOWN_ACCEPT_NONE) != 0,
        "CONVERT != NONE");
    TEST_ASSERT(
        strcmp(NGX_HTTP_MARKDOWN_ACCEPT_SKIP,
               NGX_HTTP_MARKDOWN_ACCEPT_REJECT) != 0,
        "SKIP != REJECT");

    TEST_PASS("accept_result constants valid");
}


/*
 * Test: conditional_result constants are distinct and non-empty.
 */
static void
test_conditional_result_constants(void)
{
    TEST_SUBSECTION("conditional_result constants");

    TEST_ASSERT(
        strlen(NGX_HTTP_MARKDOWN_COND_NOT_MODIFIED) > 0,
        "NOT_MODIFIED is non-empty");
    TEST_ASSERT(strlen(NGX_HTTP_MARKDOWN_COND_PROCEED) > 0,
        "PROCEED is non-empty");
    TEST_ASSERT(strlen(NGX_HTTP_MARKDOWN_COND_SKIPPED) > 0,
        "SKIPPED is non-empty");

    /* All distinct */
    TEST_ASSERT(
        strcmp(NGX_HTTP_MARKDOWN_COND_NOT_MODIFIED,
               NGX_HTTP_MARKDOWN_COND_PROCEED) != 0,
        "NOT_MODIFIED != PROCEED");
    TEST_ASSERT(
        strcmp(NGX_HTTP_MARKDOWN_COND_NOT_MODIFIED,
               NGX_HTTP_MARKDOWN_COND_SKIPPED) != 0,
        "NOT_MODIFIED != SKIPPED");
    TEST_ASSERT(
        strcmp(NGX_HTTP_MARKDOWN_COND_PROCEED,
               NGX_HTTP_MARKDOWN_COND_SKIPPED) != 0,
        "PROCEED != SKIPPED");

    TEST_PASS("conditional_result constants valid");
}


/*
 * Test: conversion_status constants are distinct and non-empty.
 */
static void
test_conversion_status_constants(void)
{
    TEST_SUBSECTION("conversion_status constants");

    TEST_ASSERT(strlen(NGX_HTTP_MARKDOWN_CONV_SUCCESS) > 0,
        "SUCCESS is non-empty");
    TEST_ASSERT(strlen(NGX_HTTP_MARKDOWN_CONV_FAILED) > 0,
        "FAILED is non-empty");
    TEST_ASSERT(strlen(NGX_HTTP_MARKDOWN_CONV_SKIPPED) > 0,
        "SKIPPED is non-empty");

    /* All distinct */
    TEST_ASSERT(
        strcmp(NGX_HTTP_MARKDOWN_CONV_SUCCESS,
               NGX_HTTP_MARKDOWN_CONV_FAILED) != 0,
        "SUCCESS != FAILED");
    TEST_ASSERT(
        strcmp(NGX_HTTP_MARKDOWN_CONV_SUCCESS,
               NGX_HTTP_MARKDOWN_CONV_SKIPPED) != 0,
        "SUCCESS != SKIPPED");
    TEST_ASSERT(
        strcmp(NGX_HTTP_MARKDOWN_CONV_FAILED,
               NGX_HTTP_MARKDOWN_CONV_SKIPPED) != 0,
        "FAILED != SKIPPED");

    TEST_PASS("conversion_status constants valid");
}


/*
 * Test: failure detection logic.
 */
static void
test_failure_detection(void)
{
    TEST_SUBSECTION("Failure detection");

    TEST_ASSERT(
        decision_path_is_failure(NGX_HTTP_MARKDOWN_CONV_FAILED)
            == 1,
        "FAILED is detected as failure");
    TEST_ASSERT(
        decision_path_is_failure(NGX_HTTP_MARKDOWN_CONV_SUCCESS)
            == 0,
        "SUCCESS is not failure");
    TEST_ASSERT(
        decision_path_is_failure(NGX_HTTP_MARKDOWN_CONV_SKIPPED)
            == 0,
        "SKIPPED is not failure");
    TEST_ASSERT(decision_path_is_failure(NULL) == 0,
        "NULL is not failure");

    TEST_PASS("Failure detection correct");
}


/*
 * Test: verbosity gating for decision path log.
 */
static void
test_verbosity_gating(void)
{
    TEST_SUBSECTION("Verbosity gating");

    /* info: emit all */
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_INFO,
            NGX_HTTP_MARKDOWN_CONV_SUCCESS) == 1,
        "info emits SUCCESS");
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_INFO,
            NGX_HTTP_MARKDOWN_CONV_FAILED) == 1,
        "info emits FAILED");
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_INFO,
            NGX_HTTP_MARKDOWN_CONV_SKIPPED) == 1,
        "info emits SKIPPED");

    /* debug: emit all */
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_DEBUG,
            NGX_HTTP_MARKDOWN_CONV_SUCCESS) == 1,
        "debug emits SUCCESS");
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_DEBUG,
            NGX_HTTP_MARKDOWN_CONV_FAILED) == 1,
        "debug emits FAILED");

    /* warn: only failures */
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
            NGX_HTTP_MARKDOWN_CONV_SUCCESS) == 0,
        "warn suppresses SUCCESS");
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
            NGX_HTTP_MARKDOWN_CONV_SKIPPED) == 0,
        "warn suppresses SKIPPED");
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
            NGX_HTTP_MARKDOWN_CONV_FAILED) == 1,
        "warn emits FAILED");

    /* error: only failures */
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_ERROR,
            NGX_HTTP_MARKDOWN_CONV_SUCCESS) == 0,
        "error suppresses SUCCESS");
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_ERROR,
            NGX_HTTP_MARKDOWN_CONV_FAILED) == 1,
        "error emits FAILED");

    TEST_PASS("Verbosity gating correct");
}


/*
 * Test: decision path struct can be initialized on the stack.
 */
static void
test_decision_path_struct_init(void)
{
    ngx_http_markdown_decision_path_t  dp;

    TEST_SUBSECTION("Decision path struct initialization");

    /* Simulate a successful conversion path */
    dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_CONVERT;
    dp.conditional_result = NGX_HTTP_MARKDOWN_COND_PROCEED;
    dp.conversion_status = NGX_HTTP_MARKDOWN_CONV_SUCCESS;
    dp.reason_code = "ELIGIBLE_CONVERTED";
    dp.duration_ms = 42;

    TEST_ASSERT(
        strcmp(dp.accept_result,
               NGX_HTTP_MARKDOWN_ACCEPT_CONVERT) == 0,
        "accept_result set correctly");
    TEST_ASSERT(
        strcmp(dp.conditional_result,
               NGX_HTTP_MARKDOWN_COND_PROCEED) == 0,
        "conditional_result set correctly");
    TEST_ASSERT(
        strcmp(dp.conversion_status,
               NGX_HTTP_MARKDOWN_CONV_SUCCESS) == 0,
        "conversion_status set correctly");
    TEST_ASSERT(strcmp(dp.reason_code, "ELIGIBLE_CONVERTED") == 0,
        "reason_code set correctly");
    TEST_ASSERT(dp.duration_ms == 42,
        "duration_ms set correctly");

    /* Simulate a skip path */
    dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_SKIP;
    dp.conditional_result = NGX_HTTP_MARKDOWN_COND_SKIPPED;
    dp.conversion_status = NGX_HTTP_MARKDOWN_CONV_SKIPPED;
    dp.reason_code = "SKIP_ACCEPT";
    dp.duration_ms = 0;

    TEST_ASSERT(
        strcmp(dp.accept_result,
               NGX_HTTP_MARKDOWN_ACCEPT_SKIP) == 0,
        "skip path: accept_result correct");
    TEST_ASSERT(dp.duration_ms == 0,
        "skip path: duration_ms is 0");

    /* Simulate a 304 Not Modified path */
    dp.accept_result = NGX_HTTP_MARKDOWN_ACCEPT_CONVERT;
    dp.conditional_result = NGX_HTTP_MARKDOWN_COND_NOT_MODIFIED;
    dp.conversion_status = NGX_HTTP_MARKDOWN_CONV_SKIPPED;
    dp.reason_code = "ELIGIBLE_CONVERTED";
    dp.duration_ms = 5;

    TEST_ASSERT(
        strcmp(dp.conditional_result,
               NGX_HTTP_MARKDOWN_COND_NOT_MODIFIED) == 0,
        "304 path: conditional_result correct");

    TEST_PASS("Decision path struct initialization correct");
}


/*
 * Test: NULL fields handled safely by failure detection.
 */
static void
test_null_safety(void)
{
    TEST_SUBSECTION("NULL safety");

    TEST_ASSERT(decision_path_is_failure(NULL) == 0,
        "NULL conversion_status -> not failure");
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_INFO,
            NULL) == 1,
        "info + NULL status -> emit (safe default)");
    TEST_ASSERT(
        decision_path_should_emit(NGX_HTTP_MARKDOWN_LOG_WARN,
            NULL) == 0,
        "warn + NULL status -> suppress (not failure)");

    TEST_PASS("NULL safety correct");
}


int
main(void)
{
    printf("\n========================================\n");
    printf("decision_path Tests\n");
    printf("========================================\n");

    test_accept_result_constants();
    test_conditional_result_constants();
    test_conversion_status_constants();
    test_failure_detection();
    test_verbosity_gating();
    test_decision_path_struct_init();
    test_null_safety();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
