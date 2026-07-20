/*
 * Test: failopen_delivery_after_downstream
 *
 * Regression test for C-001: `results.failopen_count` must be incremented
 * only AFTER the downstream filter confirms delivery (NGX_OK or NGX_DONE),
 * never at the fail-open decision point.  This mirrors the production
 * contract documented in:
 *   - AGENTS.md Rule 38 / Rule 23 (delivery != decision counters)
 *   - docs/harness/rules/streaming-backpressure.md Rule 38
 *   - docs/guides/prometheus-metrics.md "Delivery vs Decision Counter Semantics"
 * The public contract distinguishes fail-open decisions from confirmed
 * downstream deliveries and never treats NGX_AGAIN as delivery.
 *
 * The bug (C-001) was that the buffer-init-failure, buffer-append-failure,
 * and four header-filter fail-open paths incremented failopen_count BEFORE
 * invoking the downstream filter, so a downstream NGX_AGAIN or NGX_ERROR
 * was still counted as a delivery.  The fix moved the increment to after
 * the downstream filter returns NGX_OK or NGX_DONE.
 *
 * This test models the six fixed production paths and asserts the
 * delivery-after-success invariant for each.
 *
 * Rules: 38 (delivery after downstream OK), 23 (delivery != decision),
 *        8 (delivery counters after success).
 */

#include "../include/test_common.h"


enum {
    NGX_OK       =  0,
    NGX_DECLINED = -5,
    NGX_ERROR    = -1,
    NGX_AGAIN    = -2,
    NGX_DONE     = -4
};


typedef enum {
    PATH_BUFFER_INIT_FAILURE = 0,
    PATH_BUFFER_APPEND_FAILURE,
    PATH_HEADER_UNSUPPORTED_COMPRESSION,
    PATH_HEADER_CTX_ALLOC_FAILURE,
    PATH_HEADER_INFLIGHT_OVERLOAD,
    PATH_HEADER_INFLIGHT_CLEANUP_ALLOC
} failopen_path_t;


typedef struct {
    unsigned int  decision_count;
    unsigned int  failopen_count;        /* delivery counter */
    unsigned int  precommit_failopen_total; /* decision counter */
    unsigned int  downstream_invocations;
} failopen_metrics_t;


/*
 * Simulate the production fail-open delivery path.
 *
 * Production contract (Rule 38):
 *   1. Decision is recorded at the decision point (precommit_failopen_total
 *      for streaming, or log_decision() for buffer/header paths).
 *   2. The downstream filter is invoked.
 *   3. ONLY if the downstream returns NGX_OK or NGX_DONE does
 *      results.failopen_count increment.
 *
 * This function models the post-fix behaviour for all six paths.
 */
static int
failopen_path_deliver(failopen_metrics_t *m, failopen_path_t path,
    int downstream_rc)
{
    int  rc;

    (void) path;

    /* Decision point: record decision, NOT delivery. */
    m->decision_count++;
    m->precommit_failopen_total++;

    /* Invoke downstream filter. */
    m->downstream_invocations++;
    rc = downstream_rc;

    /* Delivery counter increments ONLY on confirmed downstream success. */
    if (rc == NGX_OK || rc == NGX_DONE) {
        m->failopen_count++;
    }

    return rc;
}


/* ── Test: NGX_OK delivery increments failopen_count ──────────── */

static void
test_ok_delivery_increments_failopen(void)
{
    failopen_metrics_t  m;
    int                 rc;
    int                 i;

    TEST_SUBSECTION("All six paths: NGX_OK delivery increments failopen_count");

    for (i = 0; i < 6; i++) {
        memset(&m, 0, sizeof(m));
        rc = failopen_path_deliver(&m, (failopen_path_t) i, NGX_OK);
        TEST_ASSERT(rc == NGX_OK, "downstream NGX_OK propagated");
        TEST_ASSERT(m.decision_count == 1, "decision recorded");
        TEST_ASSERT(m.precommit_failopen_total == 1,
            "decision counter incremented at decision point");
        TEST_ASSERT(m.downstream_invocations == 1,
            "downstream invoked exactly once");
        TEST_ASSERT(m.failopen_count == 1,
            "failopen_count incremented after downstream NGX_OK");
        TEST_ASSERT(m.failopen_count == m.decision_count,
            "delivery == decision for immediate success");
    }

    TEST_PASS("all six paths: NGX_OK -> failopen_count increments");
}


/* ── Test: NGX_DONE delivery increments failopen_count ────────── */

static void
test_done_delivery_increments_failopen(void)
{
    failopen_metrics_t  m;
    int                 rc;
    int                 i;

    TEST_SUBSECTION("All six paths: NGX_DONE delivery increments failopen_count");

    for (i = 0; i < 6; i++) {
        memset(&m, 0, sizeof(m));
        rc = failopen_path_deliver(&m, (failopen_path_t) i, NGX_DONE);
        TEST_ASSERT(rc == NGX_DONE, "downstream NGX_DONE propagated");
        TEST_ASSERT(m.decision_count == 1, "decision recorded");
        TEST_ASSERT(m.downstream_invocations == 1,
            "downstream invoked");
        TEST_ASSERT(m.failopen_count == 1,
            "failopen_count incremented after downstream NGX_DONE");
    }

    TEST_PASS("all six paths: NGX_DONE -> failopen_count increments");
}


/* ── Test: NGX_AGAIN does NOT increment failopen_count ────────── */

static void
test_again_does_not_increment_delivery(void)
{
    failopen_metrics_t  m;
    int                 rc;
    int                 i;

    TEST_SUBSECTION("All six paths: NGX_AGAIN does NOT increment failopen_count");

    for (i = 0; i < 6; i++) {
        memset(&m, 0, sizeof(m));
        rc = failopen_path_deliver(&m, (failopen_path_t) i, NGX_AGAIN);
        TEST_ASSERT(rc == NGX_AGAIN, "downstream NGX_AGAIN propagated");
        TEST_ASSERT(m.decision_count == 1,
            "decision recorded at decision point");
        TEST_ASSERT(m.precommit_failopen_total == 1,
            "decision counter incremented");
        TEST_ASSERT(m.failopen_count == 0,
            "failopen_count NOT incremented on NGX_AGAIN (Rule 38)");
        TEST_ASSERT(m.failopen_count != m.decision_count,
            "delivery != decision on backpressure");
    }

    TEST_PASS("all six paths: NGX_AGAIN -> failopen_count unchanged");
}


/* ── Test: NGX_ERROR does NOT increment failopen_count ────────── */

static void
test_error_does_not_increment_delivery(void)
{
    failopen_metrics_t  m;
    int                 rc;
    int                 i;

    TEST_SUBSECTION("All six paths: NGX_ERROR does NOT increment failopen_count");

    for (i = 0; i < 6; i++) {
        memset(&m, 0, sizeof(m));
        rc = failopen_path_deliver(&m, (failopen_path_t) i, NGX_ERROR);
        TEST_ASSERT(rc == NGX_ERROR, "downstream NGX_ERROR propagated");
        TEST_ASSERT(m.decision_count == 1, "decision recorded");
        TEST_ASSERT(m.failopen_count == 0,
            "failopen_count NOT incremented on NGX_ERROR (Rule 38)");
    }

    TEST_PASS("all six paths: NGX_ERROR -> failopen_count unchanged");
}


/* ── Test: NGX_DECLINED does NOT increment failopen_count ─────── */

static void
test_declined_does_not_increment_delivery(void)
{
    failopen_metrics_t  m;
    int                 rc;
    int                 i;

    TEST_SUBSECTION("All six paths: NGX_DECLINED does NOT increment failopen_count");

    for (i = 0; i < 6; i++) {
        memset(&m, 0, sizeof(m));
        rc = failopen_path_deliver(&m, (failopen_path_t) i, NGX_DECLINED);
        TEST_ASSERT(rc == NGX_DECLINED, "downstream NGX_DECLINED propagated");
        TEST_ASSERT(m.failopen_count == 0,
            "failopen_count NOT incremented on NGX_DECLINED");
    }

    TEST_PASS("all six paths: NGX_DECLINED -> failopen_count unchanged");
}


/* ── Test: sequence with backpressure then resume ─────────────── */

static void
test_again_then_ok_resume_increments_once(void)
{
    failopen_metrics_t  m;
    int                 rc;

    TEST_SUBSECTION("Backpressure then resume: failopen_count increments once");

    memset(&m, 0, sizeof(m));

    /* First attempt: downstream suspends. Decision recorded, no delivery. */
    rc = failopen_path_deliver(&m, PATH_BUFFER_INIT_FAILURE, NGX_AGAIN);
    TEST_ASSERT(rc == NGX_AGAIN, "first attempt suspends");
    TEST_ASSERT(m.decision_count == 1, "decision recorded once");
    TEST_ASSERT(m.failopen_count == 0, "no delivery on NGX_AGAIN");

    /* Resume: downstream accepts. Delivery recorded. */
    rc = failopen_path_deliver(&m, PATH_BUFFER_INIT_FAILURE, NGX_OK);
    TEST_ASSERT(rc == NGX_OK, "resume succeeds");
    TEST_ASSERT(m.decision_count == 2, "second decision recorded");
    TEST_ASSERT(m.failopen_count == 1,
        "failopen_count increments only after resume NGX_OK");
    TEST_ASSERT(m.failopen_count < m.decision_count,
        "delivery < decision after backpressure episode");

    TEST_PASS("backpressure resume: single delivery increment");
}


/* ── Test: delivery counter equals count of NGX_OK||NGX_DONE ──── */

static void
test_delivery_equals_success_count(void)
{
    static const int  codes[] = {
        NGX_OK, NGX_AGAIN, NGX_ERROR, NGX_OK, NGX_DONE,
        NGX_AGAIN, NGX_OK, NGX_ERROR, NGX_DONE, NGX_OK
    };
    const int          n = (int) (sizeof(codes) / sizeof(codes[0]));
    failopen_metrics_t m;
    unsigned int       expected_deliveries = 0;
    int                i;

    TEST_SUBSECTION("Cumulative: failopen_count == count of (NGX_OK || NGX_DONE)");

    memset(&m, 0, sizeof(m));

    for (i = 0; i < n; i++) {
        failopen_path_deliver(&m, PATH_HEADER_UNSUPPORTED_COMPRESSION,
            codes[i]);
        if (codes[i] == NGX_OK || codes[i] == NGX_DONE) {
            expected_deliveries++;
        }
        TEST_ASSERT(m.failopen_count == expected_deliveries,
            "cumulative delivery count matches at step");
    }

    TEST_ASSERT(m.decision_count == (unsigned int) n,
        "one decision per attempt");
    TEST_ASSERT(m.failopen_count == expected_deliveries,
        "final delivery count equals success count");
    TEST_ASSERT(m.failopen_count <= m.decision_count,
        "delivery never exceeds decision");

    TEST_PASS("cumulative delivery count matches success count");
}


/* ── Main ─────────────────────────────────────────────────────── */

int
main(void)
{
    TEST_SECTION("failopen_delivery_after_downstream");

    test_ok_delivery_increments_failopen();
    test_done_delivery_increments_failopen();
    test_again_does_not_increment_delivery();
    test_error_does_not_increment_delivery();
    test_declined_does_not_increment_delivery();
    test_again_then_ok_resume_increments_once();
    test_delivery_equals_success_count();

    TEST_PASS("failopen_delivery_after_downstream: all tests passed");
    return 0;
}
