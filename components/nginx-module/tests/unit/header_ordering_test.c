/*
 * Test: header_ordering
 *
 * Validates that response headers are always sent before any body data
 * (body-before-header is prohibited) and that header send is idempotent
 * (multiple filter entries do not repeat send_header).
 *
 * Models the filter as a state machine tracking send_header calls.
 *
 * Corresponds to tasks A02.6 and A02.7.
 *
 * Rules: Baseline (never send body before headers; header forwarding
 * state must be explicit and idempotent).
 */

#include "../include/test_common.h"


enum {
    NGX_OK    =  0,
    NGX_ERROR = -1
};

/*
 * State machine for header/body ordering.
 *
 * States:
 *   INIT        — no headers sent, no body sent
 *   HEADERS_SENT — headers sent, body may follow
 *   BODY_ACTIVE  — body output has begun
 *
 * Transitions:
 *   INIT -> HEADERS_SENT  (send_header)
 *   HEADERS_SENT -> BODY_ACTIVE (send_body)
 *   INIT -> ERROR (send_body without headers)
 */
typedef enum {
    STATE_INIT = 0,
    STATE_HEADERS_SENT,
    STATE_BODY_ACTIVE
} filter_phase_t;

typedef struct {
    filter_phase_t  phase;
    unsigned int    header_send_count;
    unsigned int    body_chunk_count;
} header_order_ctx_t;


/*
 * Attempt to send headers.  Returns NGX_OK on success.
 * Idempotent: if headers already sent, returns NGX_OK without
 * incrementing the send count.
 */
static int
do_send_header(header_order_ctx_t *ctx)
{
    if (ctx->phase == STATE_BODY_ACTIVE) {
        /* Cannot re-send headers after body has started */
        return NGX_ERROR;
    }

    if (ctx->phase == STATE_INIT) {
        ctx->header_send_count++;
        ctx->phase = STATE_HEADERS_SENT;
    }
    /* If already STATE_HEADERS_SENT, idempotent no-op */
    return NGX_OK;
}


/*
 * Attempt to send a body chunk.  Returns NGX_ERROR if headers
 * have not been sent yet (body-before-header violation).
 */
static int
do_send_body(header_order_ctx_t *ctx)
{
    if (ctx->phase == STATE_INIT) {
        /* Body before header: prohibited */
        return NGX_ERROR;
    }

    ctx->body_chunk_count++;
    ctx->phase = STATE_BODY_ACTIVE;
    return NGX_OK;
}


/* ── A02.6: Header is sent before body ─────────────────────────── */

static void
test_body_before_header_prohibited(void)
{
    header_order_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Body before header is prohibited");

    memset(&ctx, 0, sizeof(ctx));

    /* Attempt body output without sending headers first */
    rc = do_send_body(&ctx);
    TEST_ASSERT(rc == NGX_ERROR,
        "body before header must return NGX_ERROR");
    TEST_ASSERT(ctx.phase == STATE_INIT,
        "phase must remain INIT after rejected body");
    TEST_ASSERT(ctx.body_chunk_count == 0,
        "body_chunk_count must remain 0");
    TEST_ASSERT(ctx.header_send_count == 0,
        "header_send_count must remain 0");

    TEST_PASS("body-before-header correctly rejected");
}


static void
test_header_then_body_succeeds(void)
{
    header_order_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Header then body succeeds");

    memset(&ctx, 0, sizeof(ctx));

    rc = do_send_header(&ctx);
    TEST_ASSERT(rc == NGX_OK, "send_header succeeds");
    TEST_ASSERT(ctx.phase == STATE_HEADERS_SENT,
        "phase transitions to HEADERS_SENT");
    TEST_ASSERT(ctx.header_send_count == 1,
        "header_send_count is 1");

    rc = do_send_body(&ctx);
    TEST_ASSERT(rc == NGX_OK, "send_body after header succeeds");
    TEST_ASSERT(ctx.phase == STATE_BODY_ACTIVE,
        "phase transitions to BODY_ACTIVE");
    TEST_ASSERT(ctx.body_chunk_count == 1,
        "body_chunk_count is 1");

    /* Additional body chunks succeed */
    rc = do_send_body(&ctx);
    TEST_ASSERT(rc == NGX_OK, "second body chunk succeeds");
    TEST_ASSERT(ctx.body_chunk_count == 2,
        "body_chunk_count is 2");

    TEST_PASS("header-then-body ordering correct");
}


/* ── A02.7: Header send idempotency ───────────────────────────── */

static void
test_header_send_idempotent(void)
{
    header_order_ctx_t ctx;
    int rc;

    TEST_SUBSECTION("Header send idempotency");

    memset(&ctx, 0, sizeof(ctx));

    /* First call: transitions to HEADERS_SENT */
    rc = do_send_header(&ctx);
    TEST_ASSERT(rc == NGX_OK, "first send_header succeeds");
    TEST_ASSERT(ctx.header_send_count == 1,
        "header_send_count is 1 after first call");

    /* Second call: idempotent, no additional send */
    rc = do_send_header(&ctx);
    TEST_ASSERT(rc == NGX_OK, "second send_header succeeds (idempotent)");
    TEST_ASSERT(ctx.header_send_count == 1,
        "header_send_count remains 1 (not incremented)");

    /* Third call: still idempotent */
    rc = do_send_header(&ctx);
    TEST_ASSERT(rc == NGX_OK, "third send_header succeeds (idempotent)");
    TEST_ASSERT(ctx.header_send_count == 1,
        "header_send_count still 1 after three calls");

    TEST_PASS("header send is idempotent");
}


static void
test_multiple_filter_entries_single_header(void)
{
    header_order_ctx_t ctx;
    int rc;
    int i;

    TEST_SUBSECTION("Multiple filter entries produce single header send");

    memset(&ctx, 0, sizeof(ctx));

    /* Simulate 10 filter entry calls (e.g. multiple body chunks
     * each triggering the filter, which checks header state) */
    for (i = 0; i < 10; i++) {
        rc = do_send_header(&ctx);
        TEST_ASSERT(rc == NGX_OK, "filter entry send_header succeeds");
    }

    TEST_ASSERT(ctx.header_send_count == 1,
        "only one actual header send despite 10 filter entries");
    TEST_ASSERT(ctx.phase == STATE_HEADERS_SENT,
        "phase is HEADERS_SENT");

    /* Now send body chunks */
    for (i = 0; i < 5; i++) {
        rc = do_send_body(&ctx);
        TEST_ASSERT(rc == NGX_OK, "body chunk succeeds");
    }

    TEST_ASSERT(ctx.body_chunk_count == 5,
        "5 body chunks delivered");
    TEST_ASSERT(ctx.header_send_count == 1,
        "header_send_count still 1 after body output");

    TEST_PASS("multiple filter entries produce single header send");
}


int
main(void)
{
    TEST_SECTION("header_ordering");

    test_body_before_header_prohibited();
    test_header_then_body_succeeds();
    test_header_send_idempotent();
    test_multiple_filter_entries_single_header();

    TEST_PASS("header_ordering: all tests passed");
    return 0;
}
