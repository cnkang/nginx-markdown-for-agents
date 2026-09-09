/*
 * Test: failopen_delivery_after_downstream
 *
 * Production-logic coverage of the buffered delivery recording and the
 * fail-open delivery latch against the shared harness in
 * helpers/production_conversion_harness.h.
 */

#include "../helpers/production_conversion_harness.h"

/* ── Test: production delivery recording ─────────────────────── */

static void
test_success_records_delivery_once(void)
{
    ngx_http_markdown_ctx_t  ctx;

    TEST_SUBSECTION("production record_buffered_delivery_success");

    memset(&ctx, 0, sizeof(ctx));
    ctx.conversion.succeeded = 1;
    ctx.conversion.input_bytes = 100;
    ctx.conversion.output_bytes = 50;

    g_metric_inc_sink = 0;
    g_metric_add_sink = 0;

    ngx_http_markdown_record_buffered_delivery_success(&ctx);

    TEST_ASSERT(ctx.conversion.delivery_recorded == 1,
        "delivery recorded after success");
    TEST_ASSERT(g_metric_inc_sink == 1,
        "conversions_succeeded metric incremented");

    /* Idempotent: second call must not re-record. */
    g_metric_inc_sink = 0;
    ngx_http_markdown_record_buffered_delivery_success(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 1,
        "delivery_recorded stays set");
    TEST_ASSERT(g_metric_inc_sink == 0,
        "no duplicate metric increment on repeat call");

    TEST_PASS("success records delivery exactly once");
}

static void
test_failure_records_terminal_failure(void)
{
    ngx_http_markdown_ctx_t  ctx;

    TEST_SUBSECTION("production record_buffered_delivery_failure");

    memset(&ctx, 0, sizeof(ctx));
    ctx.conversion.succeeded = 1;
    ctx.error.has_category = 0;

    ngx_http_markdown_record_buffered_delivery_failure(&ctx);

    /* Failure path records system error metrics but does not set delivery_recorded
     * (only success path sets delivery_recorded). The function is idempotent. */
    TEST_ASSERT(ctx.conversion.delivery_recorded == 0,
        "delivery_recorded not set for terminal failure");
    TEST_ASSERT(ctx.error.has_category == 1,
        "error category recorded for terminal failure");

    /* Already-recorded: no-op. */
    ngx_http_markdown_record_buffered_delivery_failure(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 0,
        "repeat failure call is a no-op");
    TEST_ASSERT(ctx.error.has_category == 1,
        "error category remains set");

    TEST_PASS("failure records terminal delivery once");
}

static void
test_not_succeeded_never_records(void)
{
    ngx_http_markdown_ctx_t  ctx;

    TEST_SUBSECTION("conversion not succeeded -> no delivery record");

    memset(&ctx, 0, sizeof(ctx));
    ctx.conversion.succeeded = 0;

    ngx_http_markdown_record_buffered_delivery_success(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 0,
        "no delivery record when conversion did not succeed");

    ngx_http_markdown_record_buffered_delivery_failure(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 0,
        "no delivery record on failure when conversion did not succeed");

    TEST_PASS("not-succeeded conversions never record delivery");
}

static void
test_null_ctx_is_safe(void)
{
    TEST_SUBSECTION("NULL ctx is a safe no-op");

    ngx_http_markdown_record_buffered_delivery_success(NULL);
    ngx_http_markdown_record_buffered_delivery_failure(NULL);

    TEST_PASS("NULL ctx handled safely");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("failopen_delivery_after_downstream (production logic) Tests\n");
    printf("========================================\n");

    test_success_records_delivery_once();
    test_failure_records_terminal_failure();
    test_not_succeeded_never_records();
    test_null_ctx_is_safe();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
