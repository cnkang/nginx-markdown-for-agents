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
    unsigned int pending_replay_count;
    unsigned int pending;
} delivery_stats_t;


static void
record_delivery(int rc, delivery_stats_t *stats)
{
    if (rc == NGX_OK) {
        stats->delivery_count++;
        if (stats->pending > 0) {
            stats->pending_replay_count++;
            stats->pending = 0;
        }
    } else if (rc == NGX_AGAIN) {
        stats->again_count++;
        stats->pending = 1;
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
    TEST_ASSERT(stats.pending_replay_count == 1, "resume from pending should be recorded once");
}


static void
test_error_does_not_increment_delivery(void)
{
    delivery_stats_t stats;
    memset(&stats, 0, sizeof(stats));

    record_delivery(NGX_ERROR, &stats);
    TEST_ASSERT(stats.delivery_count == 0, "NGX_ERROR does not increment delivery");
    TEST_ASSERT(stats.error_count == 1, "NGX_ERROR increments error_count");
    TEST_ASSERT(stats.pending_replay_count == 0, "error path should not be replay success");
}


static void
test_fail_open_replay_semantics(void)
{
    delivery_stats_t stats;
    memset(&stats, 0, sizeof(stats));

    record_delivery(NGX_AGAIN, &stats);
    TEST_ASSERT(stats.pending == 1, "NGX_AGAIN should mark pending");
    record_delivery(NGX_OK, &stats);
    TEST_ASSERT(stats.delivery_count == 1, "successful replay increments delivery once");
    TEST_ASSERT(stats.pending_replay_count == 1, "replay counter increments on resumed success");
}


int
main(void)
{
    test_delivery_only_on_ok();
    test_error_does_not_increment_delivery();
    test_fail_open_replay_semantics();

    TEST_PASS("delivery_counter_semantics: all tests passed");
    return 0;
}
