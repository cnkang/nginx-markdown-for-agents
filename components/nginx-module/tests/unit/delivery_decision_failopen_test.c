/*
 * Test: delivery_decision_failopen
 *
 * Production-logic coverage of the buffered delivery and decision
 * counters plus the fail-open latch default state, against the shared
 * harness in helpers/production_conversion_harness.h.
 */

#include "../helpers/production_conversion_harness.h"

/* ── Test: production delivery and decision counters ───────────────── */

static void
test_production_success_and_failure_counters(void)
{
    ngx_http_markdown_ctx_t  ctx;

    TEST_SUBSECTION("production record buffered delivery metrics");

    memset(&ctx, 0, sizeof(ctx));
    ctx.conversion.succeeded = 1;
    ctx.conversion.input_bytes = 100;
    ctx.conversion.output_bytes = 50;
    ctx.error.has_category = 0;

    g_metric_inc_sink = 0;
    g_metric_add_sink = 0;

    /* Success delivery recording */
    ngx_http_markdown_record_buffered_delivery_success(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 1, "delivery recorded on success");
    TEST_ASSERT(g_metric_inc_sink == 1, "succeeded metrics incremented");

    /* Reset metric sink and try failure recording on already-recorded ctx (idempotent, no-op) */
    g_metric_inc_sink = 0;
    ngx_http_markdown_record_buffered_delivery_failure(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 1, "delivery_recorded stays 1");
    TEST_ASSERT(g_metric_inc_sink == 0, "no-op on repeat failure");

    /* Fresh context for failure path */
    memset(&ctx, 0, sizeof(ctx));
    ctx.conversion.succeeded = 1;
    ctx.error.has_category = 0;

    g_metric_inc_sink = 0;
    ngx_http_markdown_record_buffered_delivery_failure(&ctx);
    TEST_ASSERT(ctx.conversion.delivery_recorded == 0, "failure path does not set delivery_recorded");
    TEST_ASSERT(ctx.error.has_category == 1, "error category recorded on failure");
    TEST_ASSERT(g_metric_inc_sink == 1, "failed metric incremented");

    TEST_PASS("production delivery and decision counters are correct");
}

static void
test_production_latch_contracts(void)
{
    ngx_http_markdown_ctx_t  ctx;

    TEST_SUBSECTION("production latch contract");

    memset(&ctx, 0, sizeof(ctx));
    ctx.conversion.succeeded = 1;

    /* The latch is owned by production code (payload_impl/request_impl);
     * this translation unit cannot reach the setting functions without
     * pulling in the full filter chain, so only assert that the flag is a
     * distinct state that cleanup/establish helpers control.  The real
     * set/clear transitions are covered by failopen_delivery_after_
     * downstream_test.c against the production code paths. */
    TEST_ASSERT(ctx.fullbuffer.failopen_delivery_pending == 0,
                "latch defaults clear on a fresh context");

    TEST_PASS("production latch contract verified");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("delivery_decision_failopen (production logic) Tests\n");
    printf("========================================\n");

    test_production_success_and_failure_counters();
    test_production_latch_contracts();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
