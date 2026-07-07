/*
 * Test: perf_metrics_property
 *
 * Property-based tests for metric correctness (Properties 8, 9).
 *
 * Feature: 0.9.1-performance-optimization
 * Property 8: Watermark Gauge Tracks Maximum
 * Property 9: Metric Write-Site Fires at Correct Event
 *
 * Validates: Requirements 7.1, 7.2, 7.3, 7.4
 *
 * These tests use pseudo-random sequences to exhaustively verify
 * that the CAS watermark gauge always equals the maximum value in
 * any sequence, and that each metric counter increments only at
 * its designated trigger event.
 */

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef intptr_t        ngx_flag_t;
typedef volatile long   ngx_atomic_t;
typedef long            ngx_atomic_int_t;
typedef unsigned long   ngx_atomic_uint_t;

enum {
    NGX_OK    =  0,
    NGX_ERROR = -1,
    NGX_AGAIN = -2
};

/* ----------------------------------------------------------------
 * Atomic CAS stub (single-threaded test environment)
 * ---------------------------------------------------------------- */

static ngx_inline ngx_atomic_uint_t
ngx_atomic_cmp_set(ngx_atomic_t *lock, ngx_atomic_uint_t old,
    ngx_atomic_uint_t set)
{
    if ((ngx_atomic_uint_t) *lock == old) {
        *lock = (ngx_atomic_int_t) set;
        return 1;
    }
    return 0;
}

static ngx_inline ngx_atomic_int_t
ngx_atomic_fetch_add(ngx_atomic_t *value, ngx_atomic_int_t add)
{
    ngx_atomic_int_t old = *value;
    *value += add;
    return old;
}

/* ----------------------------------------------------------------
 * Performance metrics struct (mirrors production layout)
 * ---------------------------------------------------------------- */

typedef struct {
    ngx_atomic_t  backpressure_total;
    ngx_atomic_t  backpressure_resume_total;
    ngx_atomic_t  pending_output_high_watermark_bytes;
    ngx_atomic_t  decompression_streaming_total;
    ngx_atomic_t  decompression_fullbuffer_total;
    ngx_atomic_t  decompression_budget_exceeded_total;
    ngx_atomic_t  zero_copy_output_total;
    ngx_atomic_t  copied_output_total;
} perf_metrics_t;

/* ----------------------------------------------------------------
 * CAS watermark update (mirrors the production pattern from design)
 * ---------------------------------------------------------------- */

/*
 * Update the watermark gauge via CAS loop.
 * The watermark only increases (monotonically non-decreasing).
 *
 * This replicates the exact production CAS pattern from the design:
 *   for ( ;; ) {
 *       old = metrics->pending_output_high_watermark_bytes;
 *       if (current <= old) break;
 *       if (ngx_atomic_cmp_set(..., old, current)) break;
 *   }
 */
static void
watermark_cas_update(perf_metrics_t *metrics, ngx_atomic_uint_t current)
{
    ngx_atomic_uint_t old;

    for ( ;; ) {
        old = (ngx_atomic_uint_t)
            metrics->pending_output_high_watermark_bytes;
        if (current <= old) {
            break;
        }
        if (ngx_atomic_cmp_set(
                &metrics->pending_output_high_watermark_bytes,
                old, current))
        {
            break;
        }
    }
}

/* ----------------------------------------------------------------
 * Trigger event enumeration for Property 9
 * ---------------------------------------------------------------- */

typedef enum {
    EVENT_BODY_FILTER_NGX_AGAIN = 0,
    EVENT_DRAIN_NGX_OK,
    EVENT_DECOMPRESSION_BUDGET_EXCEEDED,
    EVENT_STREAMING_DECOMPRESS_ENTRY,
    EVENT_FULLBUFFER_DECOMPRESS_ENTRY,
    EVENT_ZERO_COPY_SEND_OK,
    EVENT_COPIED_SEND_OK,
    EVENT_BODY_FILTER_NGX_OK,
    EVENT_COUNT
} trigger_event_t;

/*
 * Record a metric write site based on the trigger event.
 * This models the production behavior where each metric increments
 * at exactly one designated trigger event.
 */
static void
record_metric_for_event(perf_metrics_t *metrics, trigger_event_t event)
{
    switch (event) {
    case EVENT_BODY_FILTER_NGX_AGAIN:
        ngx_atomic_fetch_add(&metrics->backpressure_total, 1);
        break;
    case EVENT_DRAIN_NGX_OK:
        ngx_atomic_fetch_add(&metrics->backpressure_resume_total, 1);
        break;
    case EVENT_DECOMPRESSION_BUDGET_EXCEEDED:
        ngx_atomic_fetch_add(
            &metrics->decompression_budget_exceeded_total, 1);
        break;
    case EVENT_STREAMING_DECOMPRESS_ENTRY:
        ngx_atomic_fetch_add(
            &metrics->decompression_streaming_total, 1);
        break;
    case EVENT_FULLBUFFER_DECOMPRESS_ENTRY:
        ngx_atomic_fetch_add(
            &metrics->decompression_fullbuffer_total, 1);
        break;
    case EVENT_ZERO_COPY_SEND_OK:
        ngx_atomic_fetch_add(&metrics->zero_copy_output_total, 1);
        break;
    case EVENT_COPIED_SEND_OK:
        ngx_atomic_fetch_add(&metrics->copied_output_total, 1);
        break;
    default:
        /* Events that don't map to any metric (e.g. NGX_OK) */
        break;
    }
}

/* ----------------------------------------------------------------
 * Simple PRNG for deterministic pseudo-random sequences
 * ---------------------------------------------------------------- */

static unsigned int g_prng_state = 12345;

static unsigned int
prng_next(void)
{
    /* xorshift32 */
    g_prng_state ^= g_prng_state << 13;
    g_prng_state ^= g_prng_state >> 17;
    g_prng_state ^= g_prng_state << 5;
    return g_prng_state;
}

static void
prng_seed(unsigned int seed)
{
    g_prng_state = seed ? seed : 1;
}

/* ----------------------------------------------------------------
 * Property 8: Watermark Gauge Tracks Maximum
 *
 * For any sequence of values v1, v2, ..., vN applied to the
 * watermark, the final watermark value equals max(v1, ..., vN).
 *
 * Validates: Requirements 7.3
 * ---------------------------------------------------------------- */

#define WATERMARK_ITERATIONS 200
#define WATERMARK_SEQ_LEN    50

static void
test_watermark_tracks_maximum_single_sequence(
    ngx_atomic_uint_t *seq, size_t len)
{
    perf_metrics_t metrics;
    ngx_atomic_uint_t expected_max;
    size_t i;

    memset(&metrics, 0, sizeof(metrics));
    expected_max = 0;

    for (i = 0; i < len; i++) {
        watermark_cas_update(&metrics, seq[i]);
        if (seq[i] > expected_max) {
            expected_max = seq[i];
        }
    }

    TEST_ASSERT(
        (ngx_atomic_uint_t) metrics
            .pending_output_high_watermark_bytes == expected_max,
        "watermark must equal max value in sequence");
}

static void
test_property8_watermark_random_sequences(void)
{
    ngx_atomic_uint_t seq[WATERMARK_SEQ_LEN];
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 8: Watermark gauge tracks maximum "
        "(random sequences)");

    for (iter = 0; iter < WATERMARK_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 1));
        for (j = 0; j < WATERMARK_SEQ_LEN; j++) {
            seq[j] = (ngx_atomic_uint_t)(prng_next() % 100000);
        }
        test_watermark_tracks_maximum_single_sequence(
            seq, WATERMARK_SEQ_LEN);
    }

    TEST_PASS(
        "Property 8: watermark == max(sequence) for 200 "
        "random sequences");
}

static void
test_property8_watermark_monotone_increasing(void)
{
    ngx_atomic_uint_t seq[20];
    size_t i;

    TEST_SUBSECTION(
        "Property 8: Watermark with monotone increasing input");

    for (i = 0; i < 20; i++) {
        seq[i] = (ngx_atomic_uint_t)((i + 1) * 100);
    }
    test_watermark_tracks_maximum_single_sequence(seq, 20);

    TEST_PASS("Property 8: monotone increasing → last value");
}

static void
test_property8_watermark_monotone_decreasing(void)
{
    ngx_atomic_uint_t seq[20];
    size_t i;

    TEST_SUBSECTION(
        "Property 8: Watermark with monotone decreasing input");

    for (i = 0; i < 20; i++) {
        seq[i] = (ngx_atomic_uint_t)((20 - i) * 100);
    }
    test_watermark_tracks_maximum_single_sequence(seq, 20);

    TEST_PASS("Property 8: monotone decreasing → first value");
}

static void
test_property8_watermark_all_same(void)
{
    ngx_atomic_uint_t seq[10];
    size_t i;

    TEST_SUBSECTION(
        "Property 8: Watermark with constant input");

    for (i = 0; i < 10; i++) {
        seq[i] = 42;
    }
    test_watermark_tracks_maximum_single_sequence(seq, 10);

    TEST_PASS("Property 8: constant input → that value");
}

static void
test_property8_watermark_single_element(void)
{
    ngx_atomic_uint_t seq[1];

    TEST_SUBSECTION("Property 8: Single element sequence");

    seq[0] = 9999;
    test_watermark_tracks_maximum_single_sequence(seq, 1);

    TEST_PASS("Property 8: single element → that element");
}

static void
test_property8_watermark_zero_values(void)
{
    ngx_atomic_uint_t seq[5];
    size_t i;

    TEST_SUBSECTION("Property 8: All-zero sequence");

    for (i = 0; i < 5; i++) {
        seq[i] = 0;
    }
    test_watermark_tracks_maximum_single_sequence(seq, 5);

    TEST_PASS("Property 8: all zeros → 0");
}

static void
test_property8_watermark_spike_then_low(void)
{
    ngx_atomic_uint_t seq[10];
    size_t i;

    TEST_SUBSECTION(
        "Property 8: Spike at start then lower values");

    seq[0] = 99999;
    for (i = 1; i < 10; i++) {
        seq[i] = (ngx_atomic_uint_t)(i * 10);
    }
    test_watermark_tracks_maximum_single_sequence(seq, 10);

    TEST_PASS("Property 8: spike at start retained");
}

static void
test_property8_watermark_intermediate_invariant(void)
{
    /*
     * Stronger property: after each update in the sequence,
     * the watermark must equal the running maximum (not just
     * the final max).
     */
    perf_metrics_t metrics;
    ngx_atomic_uint_t running_max;
    ngx_atomic_uint_t val;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 8: Intermediate invariant "
        "(watermark == running max at each step)");

    for (iter = 0; iter < WATERMARK_ITERATIONS; iter++) {
        memset(&metrics, 0, sizeof(metrics));
        running_max = 0;
        prng_seed((unsigned int)(iter + 1000));

        for (j = 0; j < WATERMARK_SEQ_LEN; j++) {
            val = (ngx_atomic_uint_t)(prng_next() % 50000);
            watermark_cas_update(&metrics, val);
            if (val > running_max) {
                running_max = val;
            }
            TEST_ASSERT(
                (ngx_atomic_uint_t) metrics
                    .pending_output_high_watermark_bytes
                    == running_max,
                "watermark must equal running max at "
                "each step");
        }
    }

    TEST_PASS(
        "Property 8: intermediate invariant holds for 200 "
        "sequences × 50 steps");
}

/* ----------------------------------------------------------------
 * Property 9: Metric Write-Site Fires at Correct Event
 *
 * For any sequence of trigger events, each metric counter
 * increments by exactly 1 at its designated trigger event
 * and no other metric changes.
 *
 * Validates: Requirements 7.1, 7.2, 7.3, 7.4
 * ---------------------------------------------------------------- */

#define EVENT_ITERATIONS 200
#define EVENT_SEQ_LEN    100

/*
 * Capture all metric values as a flat array for comparison.
 */
typedef struct {
    ngx_atomic_uint_t backpressure_total;
    ngx_atomic_uint_t backpressure_resume_total;
    ngx_atomic_uint_t decompression_streaming_total;
    ngx_atomic_uint_t decompression_fullbuffer_total;
    ngx_atomic_uint_t decompression_budget_exceeded_total;
    ngx_atomic_uint_t zero_copy_output_total;
    ngx_atomic_uint_t copied_output_total;
} metric_snapshot_t;

static void
take_snapshot(const perf_metrics_t *m, metric_snapshot_t *s)
{
    s->backpressure_total =
        (ngx_atomic_uint_t) m->backpressure_total;
    s->backpressure_resume_total =
        (ngx_atomic_uint_t) m->backpressure_resume_total;
    s->decompression_streaming_total =
        (ngx_atomic_uint_t) m->decompression_streaming_total;
    s->decompression_fullbuffer_total =
        (ngx_atomic_uint_t) m->decompression_fullbuffer_total;
    s->decompression_budget_exceeded_total =
        (ngx_atomic_uint_t) m->decompression_budget_exceeded_total;
    s->zero_copy_output_total =
        (ngx_atomic_uint_t) m->zero_copy_output_total;
    s->copied_output_total =
        (ngx_atomic_uint_t) m->copied_output_total;
}

/*
 * Verify that exactly one counter changed by +1 after a
 * trigger event, and no other counter changed.
 */
static void
verify_single_metric_increment(
    const metric_snapshot_t *before,
    const metric_snapshot_t *after,
    trigger_event_t event)
{
    ngx_atomic_uint_t delta_bp, delta_resume, delta_stream;
    ngx_atomic_uint_t delta_fb, delta_budget, delta_zc, delta_cp;

    delta_bp = after->backpressure_total
        - before->backpressure_total;
    delta_resume = after->backpressure_resume_total
        - before->backpressure_resume_total;
    delta_stream = after->decompression_streaming_total
        - before->decompression_streaming_total;
    delta_fb = after->decompression_fullbuffer_total
        - before->decompression_fullbuffer_total;
    delta_budget = after->decompression_budget_exceeded_total
        - before->decompression_budget_exceeded_total;
    delta_zc = after->zero_copy_output_total
        - before->zero_copy_output_total;
    delta_cp = after->copied_output_total
        - before->copied_output_total;

    switch (event) {
    case EVENT_BODY_FILTER_NGX_AGAIN:
        TEST_ASSERT(delta_bp == 1,
            "backpressure_total must +1 on NGX_AGAIN");
        TEST_ASSERT(delta_resume == 0,
            "backpressure_resume unchanged");
        TEST_ASSERT(delta_stream == 0,
            "decomp_streaming unchanged");
        TEST_ASSERT(delta_fb == 0,
            "decomp_fullbuffer unchanged");
        TEST_ASSERT(delta_budget == 0,
            "decomp_budget unchanged");
        TEST_ASSERT(delta_zc == 0,
            "zero_copy unchanged");
        TEST_ASSERT(delta_cp == 0,
            "copied unchanged");
        break;

    case EVENT_DRAIN_NGX_OK:
        TEST_ASSERT(delta_resume == 1,
            "backpressure_resume_total must +1 on drain OK");
        TEST_ASSERT(delta_bp == 0,
            "backpressure unchanged");
        TEST_ASSERT(delta_stream == 0,
            "decomp_streaming unchanged");
        TEST_ASSERT(delta_fb == 0,
            "decomp_fullbuffer unchanged");
        TEST_ASSERT(delta_budget == 0,
            "decomp_budget unchanged");
        TEST_ASSERT(delta_zc == 0,
            "zero_copy unchanged");
        TEST_ASSERT(delta_cp == 0,
            "copied unchanged");
        break;

    case EVENT_DECOMPRESSION_BUDGET_EXCEEDED:
        TEST_ASSERT(delta_budget == 1,
            "decomp_budget_exceeded must +1");
        TEST_ASSERT(delta_bp == 0,
            "backpressure unchanged");
        TEST_ASSERT(delta_resume == 0,
            "backpressure_resume unchanged");
        TEST_ASSERT(delta_stream == 0,
            "decomp_streaming unchanged");
        TEST_ASSERT(delta_fb == 0,
            "decomp_fullbuffer unchanged");
        TEST_ASSERT(delta_zc == 0,
            "zero_copy unchanged");
        TEST_ASSERT(delta_cp == 0,
            "copied unchanged");
        break;

    case EVENT_STREAMING_DECOMPRESS_ENTRY:
        TEST_ASSERT(delta_stream == 1,
            "decomp_streaming must +1 on streaming entry");
        TEST_ASSERT(delta_bp == 0,
            "backpressure unchanged");
        TEST_ASSERT(delta_resume == 0,
            "backpressure_resume unchanged");
        TEST_ASSERT(delta_fb == 0,
            "decomp_fullbuffer unchanged");
        TEST_ASSERT(delta_budget == 0,
            "decomp_budget unchanged");
        TEST_ASSERT(delta_zc == 0,
            "zero_copy unchanged");
        TEST_ASSERT(delta_cp == 0,
            "copied unchanged");
        break;

    case EVENT_FULLBUFFER_DECOMPRESS_ENTRY:
        TEST_ASSERT(delta_fb == 1,
            "decomp_fullbuffer must +1 on fullbuffer entry");
        TEST_ASSERT(delta_bp == 0,
            "backpressure unchanged");
        TEST_ASSERT(delta_resume == 0,
            "backpressure_resume unchanged");
        TEST_ASSERT(delta_stream == 0,
            "decomp_streaming unchanged");
        TEST_ASSERT(delta_budget == 0,
            "decomp_budget unchanged");
        TEST_ASSERT(delta_zc == 0,
            "zero_copy unchanged");
        TEST_ASSERT(delta_cp == 0,
            "copied unchanged");
        break;

    case EVENT_ZERO_COPY_SEND_OK:
        TEST_ASSERT(delta_zc == 1,
            "zero_copy must +1 on send OK");
        TEST_ASSERT(delta_bp == 0,
            "backpressure unchanged");
        TEST_ASSERT(delta_resume == 0,
            "backpressure_resume unchanged");
        TEST_ASSERT(delta_stream == 0,
            "decomp_streaming unchanged");
        TEST_ASSERT(delta_fb == 0,
            "decomp_fullbuffer unchanged");
        TEST_ASSERT(delta_budget == 0,
            "decomp_budget unchanged");
        TEST_ASSERT(delta_cp == 0,
            "copied unchanged");
        break;

    case EVENT_COPIED_SEND_OK:
        TEST_ASSERT(delta_cp == 1,
            "copied must +1 on copied send OK");
        TEST_ASSERT(delta_bp == 0,
            "backpressure unchanged");
        TEST_ASSERT(delta_resume == 0,
            "backpressure_resume unchanged");
        TEST_ASSERT(delta_stream == 0,
            "decomp_streaming unchanged");
        TEST_ASSERT(delta_fb == 0,
            "decomp_fullbuffer unchanged");
        TEST_ASSERT(delta_budget == 0,
            "decomp_budget unchanged");
        TEST_ASSERT(delta_zc == 0,
            "zero_copy unchanged");
        break;

    case EVENT_BODY_FILTER_NGX_OK:
        /* No metric fires on plain NGX_OK body filter */
        TEST_ASSERT(delta_bp == 0,
            "backpressure unchanged on NGX_OK");
        TEST_ASSERT(delta_resume == 0,
            "backpressure_resume unchanged");
        TEST_ASSERT(delta_stream == 0,
            "decomp_streaming unchanged");
        TEST_ASSERT(delta_fb == 0,
            "decomp_fullbuffer unchanged");
        TEST_ASSERT(delta_budget == 0,
            "decomp_budget unchanged");
        TEST_ASSERT(delta_zc == 0,
            "zero_copy unchanged");
        TEST_ASSERT(delta_cp == 0,
            "copied unchanged");
        break;

    default:
        TEST_FAIL("unknown event type in verification");
        break;
    }
}

static void
test_property9_event_isolation_random_sequences(void)
{
    perf_metrics_t metrics;
    metric_snapshot_t before, after;
    trigger_event_t event;
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 9: Each metric fires at exactly its "
        "trigger event (random sequences)");

    for (iter = 0; iter < EVENT_ITERATIONS; iter++) {
        memset(&metrics, 0, sizeof(metrics));
        prng_seed((unsigned int)(iter + 5000));

        for (j = 0; j < EVENT_SEQ_LEN; j++) {
            event = (trigger_event_t)(prng_next() % EVENT_COUNT);
            take_snapshot(&metrics, &before);
            record_metric_for_event(&metrics, event);
            take_snapshot(&metrics, &after);
            verify_single_metric_increment(
                &before, &after, event);
        }
    }

    TEST_PASS(
        "Property 9: metric isolation verified for 200 "
        "sequences × 100 events");
}

static void
test_property9_each_event_fires_correct_counter(void)
{
    perf_metrics_t metrics;
    metric_snapshot_t before, after;
    trigger_event_t event;

    TEST_SUBSECTION(
        "Property 9: Exhaustive single-event verification");

    for (event = 0; event < EVENT_COUNT; event++) {
        memset(&metrics, 0, sizeof(metrics));
        take_snapshot(&metrics, &before);
        record_metric_for_event(&metrics, event);
        take_snapshot(&metrics, &after);
        verify_single_metric_increment(&before, &after, event);
    }

    TEST_PASS(
        "Property 9: each event fires exactly one counter");
}

static void
test_property9_cumulative_counts_match(void)
{
    /*
     * After applying a random sequence of events, the sum of
     * each metric's value must equal the count of that event
     * type in the sequence.
     */
    perf_metrics_t metrics;
    trigger_event_t events[EVENT_SEQ_LEN];
    unsigned int counts[EVENT_COUNT];
    int iter;
    size_t j;

    TEST_SUBSECTION(
        "Property 9: Cumulative counts match event "
        "frequency");

    for (iter = 0; iter < EVENT_ITERATIONS; iter++) {
        memset(&metrics, 0, sizeof(metrics));
        memset(counts, 0, sizeof(counts));
        prng_seed((unsigned int)(iter + 9000));

        for (j = 0; j < EVENT_SEQ_LEN; j++) {
            events[j] = (trigger_event_t)(
                prng_next() % EVENT_COUNT);
            counts[events[j]]++;
            record_metric_for_event(&metrics, events[j]);
        }

        TEST_ASSERT(
            (ngx_atomic_uint_t) metrics.backpressure_total
                == counts[EVENT_BODY_FILTER_NGX_AGAIN],
            "backpressure_total == count of NGX_AGAIN");
        TEST_ASSERT(
            (ngx_atomic_uint_t)
                metrics.backpressure_resume_total
                == counts[EVENT_DRAIN_NGX_OK],
            "backpressure_resume_total == count of drain OK");
        TEST_ASSERT(
            (ngx_atomic_uint_t)
                metrics.decompression_budget_exceeded_total
                == counts[EVENT_DECOMPRESSION_BUDGET_EXCEEDED],
            "decomp_budget_exceeded == count of budget events");
        TEST_ASSERT(
            (ngx_atomic_uint_t)
                metrics.decompression_streaming_total
                == counts[EVENT_STREAMING_DECOMPRESS_ENTRY],
            "decomp_streaming == count of streaming entries");
        TEST_ASSERT(
            (ngx_atomic_uint_t)
                metrics.decompression_fullbuffer_total
                == counts[EVENT_FULLBUFFER_DECOMPRESS_ENTRY],
            "decomp_fullbuffer == count of fb entries");
        TEST_ASSERT(
            (ngx_atomic_uint_t) metrics.zero_copy_output_total
                == counts[EVENT_ZERO_COPY_SEND_OK],
            "zero_copy == count of zc send OK");
        TEST_ASSERT(
            (ngx_atomic_uint_t) metrics.copied_output_total
                == counts[EVENT_COPIED_SEND_OK],
            "copied == count of copied send OK");
    }

    TEST_PASS(
        "Property 9: cumulative counts verified for 200 "
        "random sequences");
}

static void
test_property9_ngx_again_never_increments_delivery(void)
{
    /*
     * Requirement 7.7: zero_copy_output_total and
     * copied_output_total SHALL NOT increment on NGX_AGAIN.
     * Only on NGX_OK.
     *
     * This test verifies that after any number of NGX_AGAIN
     * events, neither delivery counter changes.
     */
    perf_metrics_t metrics;
    int iter;
    size_t j;
    size_t seq_len;

    TEST_SUBSECTION(
        "Property 9: NGX_AGAIN never increments delivery "
        "counters");

    for (iter = 0; iter < 100; iter++) {
        memset(&metrics, 0, sizeof(metrics));
        prng_seed((unsigned int)(iter + 20000));
        seq_len = (prng_next() % 50) + 1;

        for (j = 0; j < seq_len; j++) {
            record_metric_for_event(
                &metrics, EVENT_BODY_FILTER_NGX_AGAIN);
        }

        TEST_ASSERT(
            (ngx_atomic_uint_t) metrics.zero_copy_output_total
                == 0,
            "zero_copy must be 0 after only NGX_AGAIN");
        TEST_ASSERT(
            (ngx_atomic_uint_t) metrics.copied_output_total
                == 0,
            "copied must be 0 after only NGX_AGAIN");
        TEST_ASSERT(
            (ngx_atomic_uint_t) metrics.backpressure_total
                == (ngx_atomic_uint_t) seq_len,
            "backpressure_total must equal NGX_AGAIN count");
    }

    TEST_PASS(
        "Property 9: NGX_AGAIN never touches delivery "
        "counters (100 sequences)");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "Property 8: Watermark Gauge Tracks Maximum\n"
        "Property 9: Metric Write-Site Fires at Correct Event");

    /* Property 8 tests */
    test_property8_watermark_random_sequences();
    test_property8_watermark_monotone_increasing();
    test_property8_watermark_monotone_decreasing();
    test_property8_watermark_all_same();
    test_property8_watermark_single_element();
    test_property8_watermark_zero_values();
    test_property8_watermark_spike_then_low();
    test_property8_watermark_intermediate_invariant();

    /* Property 9 tests */
    test_property9_event_isolation_random_sequences();
    test_property9_each_event_fires_correct_counter();
    test_property9_cumulative_counts_match();
    test_property9_ngx_again_never_increments_delivery();

    printf("\n");
    TEST_PASS(
        "perf_metrics_property: all property tests passed");
    return 0;
}
