/*
 * Test: streaming_security
 *
 * Validates security-focused streaming scenarios:
 *   - Oversized body exceeding markdown_max_size
 *   - Budget exceeded metric increments (failures_resource_limit)
 *   - Correct on_error=pass (fail-open) and on_error=reject behavior
 *
 * streaming security and resource limits: Streaming Security, Resource Limits, and Compression
 * Validates: oversized body / replay overflow handling
 *
 * AGENTS.md compliance:
 *   Rule 14: security regression test with cross-boundary/malformed input
 *   Rule 16: no dead stores; every variable consumed by assertion
 */

#include "../include/test_common.h"


/* ================================================================
 * Minimal type/constant definitions mirroring production
 * ================================================================ */

/* NGINX-like integer types */
typedef intptr_t   ngx_int_t;
typedef uintptr_t  ngx_uint_t;
typedef intptr_t   ngx_flag_t;

/* NGINX return codes */
enum {
    NGX_OK       =  0,
    NGX_ERROR    = -1,
    NGX_AGAIN    = -2,
    NGX_DECLINED = -5
};

/* Streaming error codes (mirror Rust FFI / module header) */
enum {
    ERROR_SUCCESS                       = 0,
    ERROR_TIMEOUT                       = 3,
    ERROR_MEMORY_LIMIT                  = 4,
    ERROR_BUDGET_EXCEEDED               = 6,
    ERROR_STREAMING_FALLBACK            = 7,
    ERROR_POST_COMMIT                   = 8,
    ERROR_DECOMPRESSION_BUDGET_EXCEEDED = 9,
    ERROR_PARSE_TIMEOUT                 = 10,
    ERROR_PARSE_BUDGET_EXCEEDED         = 11,
    ERROR_INTERNAL                      = 99
};

/* On-error policy values */
#define ON_ERROR_PASS    0
#define ON_ERROR_REJECT  1

/* Commit state */
#define COMMIT_PRE   0
#define COMMIT_POST  1


/* ================================================================
 * Streaming context and metrics modelling production behavior
 * ================================================================ */

/*
 * Metric counters modelling the relevant subset of
 * ngx_http_markdown_metrics_t and streaming sub-struct.
 */
typedef struct {
    unsigned int budget_exceeded_total;
    unsigned int failed_total;
    unsigned int precommit_reject_total;
    unsigned int precommit_failopen_total;
} streaming_metrics_t;

typedef struct {
    unsigned int conversions_failed;
    unsigned int failures_resource_limit;
    unsigned int failures_conversion;
    streaming_metrics_t streaming;
} test_metrics_t;

/*
 * Per-request streaming context modelling
 * ngx_http_markdown_ctx_t streaming sub-struct.
 */
typedef struct {
    size_t       total_input_bytes;
    ngx_uint_t   commit_state;
    ngx_uint_t   chunks_processed;
    int          eligible;
} streaming_ctx_t;

/*
 * Configuration modelling relevant fields from
 * ngx_http_markdown_conf_t.
 */
typedef struct {
    size_t       max_size;
    ngx_uint_t   on_error;  /* ON_ERROR_PASS or ON_ERROR_REJECT */
} test_conf_t;


/* ================================================================
 * Pre-Commit error handler (models production logic)
 *
 * Routes by error_code and on_error policy:
 *   ERROR_STREAMING_FALLBACK: always NGX_DECLINED (full-buffer)
 *   Budget/resource errors + pass:   NGX_DECLINED (fail-open)
 *   Budget/resource errors + reject: NGX_ERROR (fail-closed)
 *
 * Increments metrics exactly as production does.
 * ================================================================ */

static ngx_int_t
test_precommit_error(streaming_ctx_t *ctx, const test_conf_t *conf,
    test_metrics_t *metrics, uint32_t error_code)
{
    if (error_code == ERROR_STREAMING_FALLBACK) {
        return NGX_DECLINED;
    }

    /*
     * Budget exceeded auxiliary classification.
     * Mirrors streaming_impl.h: budget_exceeded_total incremented
     * for resource-limit error codes.
     */
    if (error_code == ERROR_MEMORY_LIMIT
        || error_code == ERROR_BUDGET_EXCEEDED
        || error_code == ERROR_DECOMPRESSION_BUDGET_EXCEEDED
        || error_code == ERROR_PARSE_TIMEOUT
        || error_code == ERROR_PARSE_BUDGET_EXCEEDED)
    {
        metrics->streaming.budget_exceeded_total++;
    }

    metrics->streaming.failed_total++;
    metrics->conversions_failed++;

    /*
     * Failure-reason classification.
     * Resource-limit errors go to failures_resource_limit;
     * others go to failures_conversion.
     */
    if (error_code == ERROR_MEMORY_LIMIT
        || error_code == ERROR_BUDGET_EXCEEDED
        || error_code == ERROR_DECOMPRESSION_BUDGET_EXCEEDED
        || error_code == ERROR_PARSE_TIMEOUT
        || error_code == ERROR_PARSE_BUDGET_EXCEEDED)
    {
        metrics->failures_resource_limit++;
    } else {
        metrics->failures_conversion++;
    }

    /* Route by on_error policy */
    if (conf->on_error == ON_ERROR_REJECT) {
        metrics->streaming.precommit_reject_total++;
        return NGX_ERROR;
    }

    /* Fail-open */
    ctx->eligible = 0;
    metrics->streaming.precommit_failopen_total++;
    return NGX_DECLINED;
}


/*
 * Simulate feeding a chunk through the streaming path.
 *
 * Models the production body filter logic:
 *   1. Accumulate total_input_bytes
 *   2. Check against max_size
 *   3. If exceeded, route through precommit_error
 *   4. Otherwise process normally
 *
 * Returns:
 *   NGX_OK       - chunk processed successfully
 *   NGX_DECLINED - fail-open (budget exceeded, on_error=pass)
 *   NGX_ERROR    - fail-closed (budget exceeded, on_error=reject)
 */
static ngx_int_t
test_feed_chunk(streaming_ctx_t *ctx, const test_conf_t *conf,
    test_metrics_t *metrics, size_t chunk_size)
{
    ctx->total_input_bytes += chunk_size;
    ctx->chunks_processed++;

    if (conf->max_size > 0 && ctx->total_input_bytes > conf->max_size) {
        return test_precommit_error(ctx, conf, metrics,
            ERROR_MEMORY_LIMIT);
    }

    return NGX_OK;
}


/* ================================================================
 * Test: oversized body with on_error=pass (fail-open)
 *
 * Scenario: max_size = 1024 bytes
 *   chunk 1: 512 bytes (within budget, OK)
 *   chunk 2: 256 bytes (within budget, OK)
 *   chunk 3: 512 bytes (accumulated 1280 > 1024, EXCEEDED)
 *
 * Expected:
 *   - Returns NGX_DECLINED (fail-open)
 *   - failures_resource_limit == 1
 *   - streaming.budget_exceeded_total == 1
 *   - streaming.failed_total == 1
 *   - streaming.precommit_failopen_total == 1
 *   - ctx->eligible == 0 (no longer eligible for conversion)
 * ================================================================ */
static void
test_oversized_body_fail_open(void)
{
    streaming_ctx_t ctx;
    test_conf_t     conf;
    test_metrics_t  metrics;
    ngx_int_t       rc;

    TEST_SUBSECTION("Oversized body: on_error=pass (fail-open)");

    memset(&ctx, 0, sizeof(ctx));
    memset(&metrics, 0, sizeof(metrics));
    ctx.commit_state = COMMIT_PRE;
    ctx.eligible = 1;
    conf.max_size = 1024;
    conf.on_error = ON_ERROR_PASS;

    /* Chunk 1: 512 bytes, within budget */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 512);
    TEST_ASSERT(rc == NGX_OK,
        "chunk 1 (512B) within 1024B limit returns NGX_OK");
    TEST_ASSERT(ctx.total_input_bytes == 512,
        "accumulated bytes == 512 after chunk 1");
    TEST_ASSERT(metrics.failures_resource_limit == 0,
        "no resource limit failure after chunk 1");

    /* Chunk 2: 256 bytes, still within budget */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 256);
    TEST_ASSERT(rc == NGX_OK,
        "chunk 2 (256B) within 1024B limit returns NGX_OK");
    TEST_ASSERT(ctx.total_input_bytes == 768,
        "accumulated bytes == 768 after chunk 2");
    TEST_ASSERT(metrics.failures_resource_limit == 0,
        "no resource limit failure after chunk 2");

    /* Chunk 3: 512 bytes, exceeds budget (768+512=1280 > 1024) */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 512);
    TEST_ASSERT(rc == NGX_DECLINED,
        "chunk 3 exceeds limit, on_error=pass returns NGX_DECLINED");
    TEST_ASSERT(ctx.total_input_bytes == 1280,
        "accumulated bytes == 1280 after oversized chunk");
    TEST_ASSERT(ctx.eligible == 0,
        "eligible cleared after fail-open");

    /* Verify metric increments */
    TEST_ASSERT(metrics.failures_resource_limit == 1,
        "failures_resource_limit == 1 after oversized body");
    TEST_ASSERT(metrics.streaming.budget_exceeded_total == 1,
        "budget_exceeded_total == 1");
    TEST_ASSERT(metrics.streaming.failed_total == 1,
        "streaming.failed_total == 1");
    TEST_ASSERT(metrics.streaming.precommit_failopen_total == 1,
        "precommit_failopen_total == 1");
    TEST_ASSERT(metrics.streaming.precommit_reject_total == 0,
        "precommit_reject_total == 0 (pass policy)");
    TEST_ASSERT(metrics.conversions_failed == 1,
        "conversions_failed == 1");
    TEST_ASSERT(metrics.failures_conversion == 0,
        "failures_conversion == 0 (not a conversion error)");

    TEST_PASS("Oversized body fail-open: correct");
}


/* ================================================================
 * Test: oversized body with on_error=reject (fail-closed)
 *
 * Same data pattern but on_error=reject should return NGX_ERROR.
 * ================================================================ */
static void
test_oversized_body_fail_closed(void)
{
    streaming_ctx_t ctx;
    test_conf_t     conf;
    test_metrics_t  metrics;
    ngx_int_t       rc;

    TEST_SUBSECTION("Oversized body: on_error=reject (fail-closed)");

    memset(&ctx, 0, sizeof(ctx));
    memset(&metrics, 0, sizeof(metrics));
    ctx.commit_state = COMMIT_PRE;
    ctx.eligible = 1;
    conf.max_size = 1024;
    conf.on_error = ON_ERROR_REJECT;

    /* Chunk 1: 512 bytes, within budget */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 512);
    TEST_ASSERT(rc == NGX_OK,
        "chunk 1 (512B) within 1024B limit returns NGX_OK");

    /* Chunk 2: 256 bytes, still within budget */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 256);
    TEST_ASSERT(rc == NGX_OK,
        "chunk 2 (256B) within 1024B limit returns NGX_OK");

    /* Chunk 3: 512 bytes, exceeds budget */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 512);
    TEST_ASSERT(rc == NGX_ERROR,
        "chunk 3 exceeds limit, on_error=reject returns NGX_ERROR");
    TEST_ASSERT(ctx.total_input_bytes == 1280,
        "accumulated bytes == 1280 after oversized chunk");

    /* Verify metric increments */
    TEST_ASSERT(metrics.failures_resource_limit == 1,
        "failures_resource_limit == 1 after oversized body");
    TEST_ASSERT(metrics.streaming.budget_exceeded_total == 1,
        "budget_exceeded_total == 1");
    TEST_ASSERT(metrics.streaming.failed_total == 1,
        "streaming.failed_total == 1");
    TEST_ASSERT(metrics.streaming.precommit_reject_total == 1,
        "precommit_reject_total == 1 (reject policy)");
    TEST_ASSERT(metrics.streaming.precommit_failopen_total == 0,
        "precommit_failopen_total == 0 (reject policy)");
    TEST_ASSERT(metrics.conversions_failed == 1,
        "conversions_failed == 1");
    TEST_ASSERT(metrics.failures_conversion == 0,
        "failures_conversion == 0 (resource limit, not conversion)");

    TEST_PASS("Oversized body fail-closed: correct");
}


/* ================================================================
 * Test: exactly at boundary — not exceeded
 *
 * Verifies that accumulated bytes == max_size does NOT trigger
 * the budget exceeded path (condition is strictly greater-than).
 * ================================================================ */
static void
test_oversized_body_boundary_exact(void)
{
    streaming_ctx_t ctx;
    test_conf_t     conf;
    test_metrics_t  metrics;
    ngx_int_t       rc;

    TEST_SUBSECTION("Oversized body: exactly at limit (not exceeded)");

    memset(&ctx, 0, sizeof(ctx));
    memset(&metrics, 0, sizeof(metrics));
    ctx.commit_state = COMMIT_PRE;
    ctx.eligible = 1;
    conf.max_size = 1024;
    conf.on_error = ON_ERROR_PASS;

    /* Single chunk exactly at limit */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 1024);
    TEST_ASSERT(rc == NGX_OK,
        "exactly at limit (1024 == 1024) returns NGX_OK");
    TEST_ASSERT(ctx.total_input_bytes == 1024,
        "accumulated bytes == 1024");
    TEST_ASSERT(metrics.failures_resource_limit == 0,
        "no resource limit failure at exact boundary");
    TEST_ASSERT(metrics.streaming.budget_exceeded_total == 0,
        "budget_exceeded_total == 0 at exact boundary");
    TEST_ASSERT(ctx.eligible == 1,
        "still eligible at exact boundary");

    TEST_PASS("Exact boundary not exceeded: correct");
}


/* ================================================================
 * Test: one byte over boundary
 *
 * Verifies that accumulated bytes == max_size + 1 triggers the
 * budget exceeded path.
 * ================================================================ */
static void
test_oversized_body_boundary_plus_one(void)
{
    streaming_ctx_t ctx;
    test_conf_t     conf;
    test_metrics_t  metrics;
    ngx_int_t       rc;

    TEST_SUBSECTION("Oversized body: one byte over limit");

    memset(&ctx, 0, sizeof(ctx));
    memset(&metrics, 0, sizeof(metrics));
    ctx.commit_state = COMMIT_PRE;
    ctx.eligible = 1;
    conf.max_size = 1024;
    conf.on_error = ON_ERROR_PASS;

    /* First chunk at limit */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 1024);
    TEST_ASSERT(rc == NGX_OK,
        "at limit returns NGX_OK");

    /* Second chunk: just 1 byte, triggers exceeded */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 1);
    TEST_ASSERT(rc == NGX_DECLINED,
        "one byte over limit triggers fail-open");
    TEST_ASSERT(ctx.total_input_bytes == 1025,
        "accumulated bytes == 1025");
    TEST_ASSERT(metrics.failures_resource_limit == 1,
        "failures_resource_limit == 1");
    TEST_ASSERT(metrics.streaming.budget_exceeded_total == 1,
        "budget_exceeded_total == 1");

    TEST_PASS("One byte over boundary: correct");
}


/* ================================================================
 * Test: single oversized chunk (larger than max_size)
 *
 * Verifies that a single chunk exceeding max_size in one shot
 * correctly triggers the budget exceeded path.
 * ================================================================ */
static void
test_oversized_body_single_large_chunk(void)
{
    streaming_ctx_t ctx;
    test_conf_t     conf;
    test_metrics_t  metrics;
    ngx_int_t       rc;

    TEST_SUBSECTION("Oversized body: single large chunk");

    memset(&ctx, 0, sizeof(ctx));
    memset(&metrics, 0, sizeof(metrics));
    ctx.commit_state = COMMIT_PRE;
    ctx.eligible = 1;
    conf.max_size = 1024;
    conf.on_error = ON_ERROR_REJECT;

    /* Single chunk far exceeding limit */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 8192);
    TEST_ASSERT(rc == NGX_ERROR,
        "single 8192B chunk exceeds 1024B limit, reject returns NGX_ERROR");
    TEST_ASSERT(ctx.total_input_bytes == 8192,
        "accumulated bytes == 8192");
    TEST_ASSERT(metrics.failures_resource_limit == 1,
        "failures_resource_limit == 1");
    TEST_ASSERT(metrics.streaming.budget_exceeded_total == 1,
        "budget_exceeded_total == 1");
    TEST_ASSERT(metrics.streaming.precommit_reject_total == 1,
        "precommit_reject_total == 1");
    TEST_ASSERT(metrics.conversions_failed == 1,
        "conversions_failed == 1");

    TEST_PASS("Single large chunk reject: correct");
}


/* ================================================================
 * Test: max_size == 0 means unlimited (no enforcement)
 *
 * Verifies that when max_size is configured as 0, no budget
 * check is performed regardless of input size.
 * ================================================================ */
static void
test_oversized_body_unlimited(void)
{
    streaming_ctx_t ctx;
    test_conf_t     conf;
    test_metrics_t  metrics;
    ngx_int_t       rc;

    TEST_SUBSECTION("Oversized body: max_size=0 (unlimited)");

    memset(&ctx, 0, sizeof(ctx));
    memset(&metrics, 0, sizeof(metrics));
    ctx.commit_state = COMMIT_PRE;
    ctx.eligible = 1;
    conf.max_size = 0;
    conf.on_error = ON_ERROR_REJECT;

    /* Feed a very large chunk — should not trigger budget */
    rc = test_feed_chunk(&ctx, &conf, &metrics, 10 * 1024 * 1024);
    TEST_ASSERT(rc == NGX_OK,
        "10MB chunk with max_size=0 returns NGX_OK (unlimited)");
    TEST_ASSERT(ctx.total_input_bytes == 10 * 1024 * 1024,
        "accumulated bytes tracked even when unlimited");
    TEST_ASSERT(metrics.failures_resource_limit == 0,
        "no resource limit failure when unlimited");
    TEST_ASSERT(metrics.streaming.budget_exceeded_total == 0,
        "budget_exceeded_total == 0 when unlimited");
    TEST_ASSERT(ctx.eligible == 1,
        "still eligible when unlimited");

    TEST_PASS("Unlimited max_size: correct");
}


/* ================================================================
 * Entry point
 * ================================================================ */
int
main(void)
{
    printf("\n========================================\n");
    printf("streaming_security Tests\n");
    printf("streaming security and resource limits: Oversized Body Security\n");
    printf("========================================\n\n");

    TEST_SECTION("Oversized body — fail-open/reject semantics");

    test_oversized_body_fail_open();
    test_oversized_body_fail_closed();
    test_oversized_body_boundary_exact();
    test_oversized_body_boundary_plus_one();
    test_oversized_body_single_large_chunk();
    test_oversized_body_unlimited();

    printf("\n");
    TEST_PASS("streaming_security: all oversized body tests passed");
    return 0;
}
