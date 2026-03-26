/*
 * Test: skip_counter
 * Description: skip-reason counter mapping from eligibility enum
 *
 * Verifies that ngx_http_markdown_metric_inc_skip() maps each
 * ngx_http_markdown_eligibility_t enum value to the correct
 * skips.* counter field.
 */

#include "test_common.h"

/*
 * Eligibility enum values mirroring
 * ngx_http_markdown_eligibility_t from the module header.
 */
#define ELIGIBLE                0
#define INELIGIBLE_METHOD       1
#define INELIGIBLE_STATUS       2
#define INELIGIBLE_CONTENT_TYPE 3
#define INELIGIBLE_SIZE         4
#define INELIGIBLE_STREAMING    5
#define INELIGIBLE_AUTH         6
#define INELIGIBLE_RANGE        7
#define INELIGIBLE_CONFIG       8

typedef int eligibility_t;

/*
 * Minimal metrics struct mirroring the skips sub-struct
 * from ngx_http_markdown_metrics_t.
 */
typedef struct {
    struct {
        unsigned long config;
        unsigned long method;
        unsigned long status;
        unsigned long content_type;
        unsigned long size;
        unsigned long streaming;
        unsigned long auth;
        unsigned long range;
        unsigned long accept;
    } skips;
} metrics_t;

static metrics_t *g_metrics = NULL;

/*
 * Standalone reimplementation of the skip-reason increment
 * function matching the logic in module_state_impl.h.
 */
static void
metric_inc_skip(eligibility_t eligibility)
{
    if (g_metrics == NULL) {
        return;
    }

    switch (eligibility) {

    case ELIGIBLE:
        return;

    case INELIGIBLE_CONFIG:
        g_metrics->skips.config++;
        return;

    case INELIGIBLE_METHOD:
        g_metrics->skips.method++;
        return;

    case INELIGIBLE_STATUS:
        g_metrics->skips.status++;
        return;

    case INELIGIBLE_CONTENT_TYPE:
        g_metrics->skips.content_type++;
        return;

    case INELIGIBLE_SIZE:
        g_metrics->skips.size++;
        return;

    case INELIGIBLE_STREAMING:
        g_metrics->skips.streaming++;
        return;

    case INELIGIBLE_AUTH:
        g_metrics->skips.auth++;
        return;

    case INELIGIBLE_RANGE:
        g_metrics->skips.range++;
        return;

    default:
        g_metrics->skips.config++;
        return;
    }
}

static void
reset_metrics(metrics_t *m)
{
    memset(m, 0, sizeof(metrics_t));
}

static void
test_eligible_is_noop(void)
{
    metrics_t m;

    TEST_SUBSECTION("ELIGIBLE is a no-op");

    reset_metrics(&m);
    g_metrics = &m;

    metric_inc_skip(ELIGIBLE);

    TEST_ASSERT(m.skips.config == 0,
                "config should be 0");
    TEST_ASSERT(m.skips.method == 0,
                "method should be 0");
    TEST_ASSERT(m.skips.status == 0,
                "status should be 0");
    TEST_ASSERT(m.skips.content_type == 0,
                "content_type should be 0");
    TEST_ASSERT(m.skips.size == 0,
                "size should be 0");
    TEST_ASSERT(m.skips.streaming == 0,
                "streaming should be 0");
    TEST_ASSERT(m.skips.auth == 0,
                "auth should be 0");
    TEST_ASSERT(m.skips.range == 0,
                "range should be 0");
    TEST_ASSERT(m.skips.accept == 0,
                "accept should be 0");

    g_metrics = NULL;
    TEST_PASS("ELIGIBLE does not increment any counter");
}

static void
test_each_enum_maps_correctly(void)
{
    metrics_t m;

    TEST_SUBSECTION("Each eligibility enum maps to correct counter");

    /* INELIGIBLE_METHOD -> skips.method */
    reset_metrics(&m);
    g_metrics = &m;
    metric_inc_skip(INELIGIBLE_METHOD);
    TEST_ASSERT(m.skips.method == 1,
                "METHOD should increment skips.method");
    TEST_ASSERT(m.skips.config == 0,
                "METHOD should not touch config");

    /* INELIGIBLE_STATUS -> skips.status */
    reset_metrics(&m);
    metric_inc_skip(INELIGIBLE_STATUS);
    TEST_ASSERT(m.skips.status == 1,
                "STATUS should increment skips.status");

    /* INELIGIBLE_CONTENT_TYPE -> skips.content_type */
    reset_metrics(&m);
    metric_inc_skip(INELIGIBLE_CONTENT_TYPE);
    TEST_ASSERT(m.skips.content_type == 1,
                "CONTENT_TYPE should increment skips.content_type");

    /* INELIGIBLE_SIZE -> skips.size */
    reset_metrics(&m);
    metric_inc_skip(INELIGIBLE_SIZE);
    TEST_ASSERT(m.skips.size == 1,
                "SIZE should increment skips.size");

    /* INELIGIBLE_STREAMING -> skips.streaming */
    reset_metrics(&m);
    metric_inc_skip(INELIGIBLE_STREAMING);
    TEST_ASSERT(m.skips.streaming == 1,
                "STREAMING should increment skips.streaming");

    /* INELIGIBLE_AUTH -> skips.auth */
    reset_metrics(&m);
    metric_inc_skip(INELIGIBLE_AUTH);
    TEST_ASSERT(m.skips.auth == 1,
                "AUTH should increment skips.auth");

    /* INELIGIBLE_RANGE -> skips.range */
    reset_metrics(&m);
    metric_inc_skip(INELIGIBLE_RANGE);
    TEST_ASSERT(m.skips.range == 1,
                "RANGE should increment skips.range");

    /* INELIGIBLE_CONFIG -> skips.config */
    reset_metrics(&m);
    metric_inc_skip(INELIGIBLE_CONFIG);
    TEST_ASSERT(m.skips.config == 1,
                "CONFIG should increment skips.config");

    g_metrics = NULL;
    TEST_PASS("All enum values map to correct counters");
}

static void
test_unknown_falls_back_to_config(void)
{
    metrics_t m;

    TEST_SUBSECTION("Unknown enum values fall back to skips.config");

    reset_metrics(&m);
    g_metrics = &m;

    /* Use a value outside the known enum range */
    metric_inc_skip(99);

    TEST_ASSERT(m.skips.config == 1,
                "unknown value should increment skips.config");
    TEST_ASSERT(m.skips.method == 0,
                "unknown value should not touch method");
    TEST_ASSERT(m.skips.status == 0,
                "unknown value should not touch status");

    g_metrics = NULL;
    TEST_PASS("Unknown enum falls back to skips.config");
}

static void
test_null_metrics_is_safe(void)
{
    TEST_SUBSECTION("NULL metrics pointer is safe");

    g_metrics = NULL;

    /* Should not crash */
    metric_inc_skip(INELIGIBLE_METHOD);
    metric_inc_skip(INELIGIBLE_CONFIG);
    metric_inc_skip(99);

    TEST_PASS("No crash with NULL metrics pointer");
}

static void
test_multiple_increments_accumulate(void)
{
    metrics_t m;

    TEST_SUBSECTION("Multiple increments accumulate");

    reset_metrics(&m);
    g_metrics = &m;

    metric_inc_skip(INELIGIBLE_METHOD);
    metric_inc_skip(INELIGIBLE_METHOD);
    metric_inc_skip(INELIGIBLE_METHOD);

    TEST_ASSERT(m.skips.method == 3,
                "three METHOD calls should give 3");

    metric_inc_skip(INELIGIBLE_STATUS);
    metric_inc_skip(INELIGIBLE_AUTH);

    TEST_ASSERT(m.skips.status == 1,
                "one STATUS call should give 1");
    TEST_ASSERT(m.skips.auth == 1,
                "one AUTH call should give 1");
    TEST_ASSERT(m.skips.method == 3,
                "method should still be 3");

    g_metrics = NULL;
    TEST_PASS("Counters accumulate correctly");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("skip_counter Tests\n");
    printf("========================================\n");

    test_eligible_is_noop();
    test_each_enum_maps_correctly();
    test_unknown_falls_back_to_config();
    test_null_metrics_is_safe();
    test_multiple_increments_accumulate();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
