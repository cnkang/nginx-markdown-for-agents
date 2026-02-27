/*
 * Test: metrics_decompression
 * Description: decompression metrics
 */

#include "test_common.h"

typedef enum {
    COMP_GZIP = 1,
    COMP_DEFLATE = 2,
    COMP_BROTLI = 3
} compression_t;

typedef struct {
    unsigned long decompressions_attempted;
    unsigned long decompressions_succeeded;
    unsigned long decompressions_failed;
    unsigned long decompressions_gzip;
    unsigned long decompressions_deflate;
    unsigned long decompressions_brotli;
} metrics_t;

static void
record_decompression_result(metrics_t *m, compression_t type, int success)
{
    m->decompressions_attempted++;

    if (success) {
        m->decompressions_succeeded++;
        switch (type) {
            case COMP_GZIP: m->decompressions_gzip++; break;
            case COMP_DEFLATE: m->decompressions_deflate++; break;
            case COMP_BROTLI: m->decompressions_brotli++; break;
            default: break;
        }
    } else {
        m->decompressions_failed++;
    }
}

static void
test_metrics_counters(void)
{
    metrics_t m;
    TEST_SUBSECTION("Type-specific and total counters");

    memset(&m, 0, sizeof(m));
    record_decompression_result(&m, COMP_GZIP, 1);
    record_decompression_result(&m, COMP_DEFLATE, 1);
    record_decompression_result(&m, COMP_BROTLI, 1);
    record_decompression_result(&m, COMP_GZIP, 0);

    TEST_ASSERT(m.decompressions_attempted == 4, "attempted should be 4");
    TEST_ASSERT(m.decompressions_succeeded == 3, "succeeded should be 3");
    TEST_ASSERT(m.decompressions_failed == 1, "failed should be 1");
    TEST_ASSERT(m.decompressions_gzip == 1, "gzip success should be 1");
    TEST_ASSERT(m.decompressions_deflate == 1, "deflate success should be 1");
    TEST_ASSERT(m.decompressions_brotli == 1, "brotli success should be 1");

    TEST_PASS("Counters are updated correctly");
}

static void
test_failure_does_not_increment_type_counter(void)
{
    metrics_t m;
    TEST_SUBSECTION("Failure does not increment type-specific success counter");

    memset(&m, 0, sizeof(m));
    record_decompression_result(&m, COMP_GZIP, 0);

    TEST_ASSERT(m.decompressions_attempted == 1, "attempted should be incremented");
    TEST_ASSERT(m.decompressions_failed == 1, "failed should be incremented");
    TEST_ASSERT(m.decompressions_gzip == 0, "gzip type counter should count successes only");
    TEST_PASS("Failure accounting is correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("metrics_decompression Tests\n");
    printf("========================================\n");

    test_metrics_counters();
    test_failure_does_not_increment_type_counter();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
