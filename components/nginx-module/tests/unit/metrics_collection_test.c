/*
 * Test: metrics_collection
 * Description: metrics collection
 */

#include "test_common.h"

typedef enum {
    CAT_CONVERSION = 0,
    CAT_RESOURCE_LIMIT,
    CAT_SYSTEM
} error_category_t;

typedef struct {
    unsigned long conversions_attempted;
    unsigned long conversions_succeeded;
    unsigned long conversions_failed;
    unsigned long conversions_bypassed;
    unsigned long failures_conversion;
    unsigned long failures_resource_limit;
    unsigned long failures_system;
    unsigned long conversion_time_sum_ms;
    unsigned long input_bytes;
    unsigned long output_bytes;
    unsigned long conversion_latency_le_10ms;
    unsigned long conversion_latency_le_100ms;
    unsigned long conversion_latency_le_1000ms;
    unsigned long conversion_latency_gt_1000ms;
} metrics_t;

static void
record_latency(metrics_t *m, unsigned long elapsed_ms)
{
    m->conversion_time_sum_ms += elapsed_ms;

    if (elapsed_ms <= 10) {
        m->conversion_latency_le_10ms++;
    } else if (elapsed_ms <= 100) {
        m->conversion_latency_le_100ms++;
    } else if (elapsed_ms <= 1000) {
        m->conversion_latency_le_1000ms++;
    } else {
        m->conversion_latency_gt_1000ms++;
    }
}

static void
record_success(metrics_t *m, size_t input, size_t output, unsigned long elapsed_ms)
{
    m->conversions_attempted++;
    m->conversions_succeeded++;
    m->input_bytes += input;
    m->output_bytes += output;
    record_latency(m, elapsed_ms);
}

static void
record_failure(metrics_t *m, error_category_t category, unsigned long elapsed_ms)
{
    m->conversions_attempted++;
    m->conversions_failed++;
    record_latency(m, elapsed_ms);
    switch (category) {
        case CAT_CONVERSION: m->failures_conversion++; break;
        case CAT_RESOURCE_LIMIT: m->failures_resource_limit++; break;
        case CAT_SYSTEM: m->failures_system++; break;
        default: break;
    }
}

static void
record_bypass(metrics_t *m)
{
    m->conversions_bypassed++;
}

static void
test_metrics_accounting(void)
{
    metrics_t m;
    TEST_SUBSECTION("Attempt/success/failure/bypass accounting");

    memset(&m, 0, sizeof(m));
    record_success(&m, 1000, 300, 12);
    record_failure(&m, CAT_CONVERSION, 80);
    record_failure(&m, CAT_RESOURCE_LIMIT, 800);
    record_failure(&m, CAT_SYSTEM, 1600);
    record_bypass(&m);

    TEST_ASSERT(m.conversions_attempted == 4, "attempted should include success+failures");
    TEST_ASSERT(m.conversions_succeeded == 1, "succeeded should be 1");
    TEST_ASSERT(m.conversions_failed == 3, "failed should be 3");
    TEST_ASSERT(m.conversions_bypassed == 1, "bypassed should be tracked separately");
    TEST_ASSERT(m.failures_conversion == 1, "conversion failures should be classified");
    TEST_ASSERT(m.failures_resource_limit == 1, "resource_limit failures should be classified");
    TEST_ASSERT(m.failures_system == 1, "system failures should be classified");
    TEST_ASSERT(m.input_bytes == 1000, "input bytes should accumulate");
    TEST_ASSERT(m.output_bytes == 300, "output bytes should accumulate");
    TEST_ASSERT(m.conversion_time_sum_ms == 2492, "elapsed time should accumulate for completed conversions");
    TEST_ASSERT(m.conversion_latency_le_10ms == 0, "sub-10ms bucket should remain empty");
    TEST_ASSERT(m.conversion_latency_le_100ms == 2, "up to 100ms bucket should include success and fast failure");
    TEST_ASSERT(m.conversion_latency_le_1000ms == 1, "up to 1000ms bucket should include medium failure");
    TEST_ASSERT(m.conversion_latency_gt_1000ms == 1, "greater than 1000ms bucket should include slow failure");
    TEST_PASS("Metrics accounting works");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_collection Tests\n");
    printf("========================================\n");

    test_metrics_accounting();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
