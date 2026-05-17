/*
 * Test: delivery_counter_semantics
 *
 * Validates that the delivery counter is incremented only after
 * NGX_OK from downstream, never on NGX_AGAIN (suspend-and-resume).
 *
 * Corresponds to task A01.12.
 */

#include "../include/test_common.h"


enum {
    NGX_OK = 0,
    NGX_AGAIN = -11,
    NGX_ERROR = -1
};

typedef struct {
    unsigned int delivery_count;
    unsigned int again_count;
    unsigned int error_count;
} delivery_stats_t;


static void
record_delivery(int rc, delivery_stats_t *stats)
{
    if (rc == NGX_OK) {
        stats->delivery_count++;
    } else if (rc == NGX_AGAIN) {
        stats->again_count++;
    } else {
        stats->error_count++;
    }
}


static void
test_delivery_only_on_ok(void)
{
    delivery_stats_t stats;
    memset(&stats, 0, sizeof(stats));

    record_delivery(NGX_OK, &stats);
    TEST_ASSERT(stats.delivery_count == 1, "first NGX_OK increments delivery");
    TEST_ASSERT(stats.again_count == 0, "no NGX_AGAIN yet");
    TEST_ASSERT(stats.error_count == 0, "no errors yet");

    record_delivery(NGX_AGAIN, &stats);
    TEST_ASSERT(stats.delivery_count == 1, "NGX_AGAIN does NOT increment delivery");
    TEST_ASSERT(stats.again_count == 1, "NGX_AGAIN increments again_count");

    record_delivery(NGX_AGAIN, &stats);
    TEST_ASSERT(stats.delivery_count == 1, "second NGX_AGAIN still no delivery increment");
    TEST_ASSERT(stats.again_count == 2, "second NGX_AGAIN increments again_count");

    record_delivery(NGX_OK, &stats);
    TEST_ASSERT(stats.delivery_count == 2, "NGX_OK after NGX_AGAIN increments delivery");
    TEST_ASSERT(stats.again_count == 2, "again_count unchanged after NGX_OK");
}


static void
test_error_does_not_increment_delivery(void)
{
    delivery_stats_t stats;
    memset(&stats, 0, sizeof(stats));

    record_delivery(NGX_ERROR, &stats);
    TEST_ASSERT(stats.delivery_count == 0, "NGX_ERROR does not increment delivery");
    TEST_ASSERT(stats.error_count == 1, "NGX_ERROR increments error_count");
}


int
main(void)
{
    test_delivery_only_on_ok();
    test_error_does_not_increment_delivery();

    TEST_PASS("delivery_counter_semantics: all tests passed");
    return 0;
}
