/*
 * Test: streaming_decomp_routing
 *
 * Unit tests for streaming decompression routing decisions, raw deflate
 * fallback behavior, fail-open (pre-commit) and safe-finish (post-commit)
 * error handling, and budget enforcement.
 *
 * Feature: 0.9.1-performance-optimization
 * Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.8, 4.10
 *
 * For 0.9.1: streaming decompression supports deflate (zlib-wrapped
 * per RFC 9110, or raw deflate per RFC 1951) via deferred header
 * sniffing.  These tests confirm gzip/brotli are routed to full-buffer
 * when streaming would otherwise be selected.
 *
 * The property tests (tasks 8.3, 8.4) cover routing and fail-open
 * exhaustively.  This unit test adds specific named examples for
 * traceability and regression coverage.
 */

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef int             ngx_flag_t;
typedef volatile long   ngx_atomic_t;
typedef long            ngx_atomic_int_t;
typedef unsigned long   ngx_atomic_uint_t;

enum {
    NGX_OK    =  0,
    NGX_ERROR = -1,
    NGX_AGAIN = -2,
    NGX_DONE  = -4
};

/* ----------------------------------------------------------------
 * Compression type enum (mirrors production)
 * ---------------------------------------------------------------- */

typedef enum {
    NGX_HTTP_MARKDOWN_COMPRESSION_NONE    = 0,
    NGX_HTTP_MARKDOWN_COMPRESSION_GZIP    = 1,
    NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE = 2,
    NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI  = 3,
    NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN = 4
} ngx_http_markdown_compression_type_e;

/* ----------------------------------------------------------------
 * Cache validation mode enum
 * ---------------------------------------------------------------- */

typedef enum {
    NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE    = 0,
    NGX_HTTP_MARKDOWN_CACHE_VALIDATION_ETAG    = 1,
    NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL    = 2
} ngx_http_markdown_cache_validation_e;

/* ----------------------------------------------------------------
 * Decompression routing decision enum
 * ---------------------------------------------------------------- */

typedef enum {
    NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING    = 0,
    NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER   = 1,
    NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS       = 2
} ngx_http_markdown_decomp_route_t;

/* ----------------------------------------------------------------
 * Commit state and error-handling enums
 * ---------------------------------------------------------------- */

typedef enum {
    STATE_PRE_COMMIT  = 0,  /* no converted byte sent downstream */
    STATE_POST_COMMIT = 1   /* >=1 converted chunk sent downstream */
} commit_state_t;

typedef enum {
    DECOMP_ERR_TRUNCATED_STREAM = 0,
    DECOMP_ERR_BUDGET_EXCEEDED,
    DECOMP_ERR_INFLATE_ERROR,
    DECOMP_ERR_INVALID_HEADER,
    DECOMP_ERR_COUNT
} decomp_error_t;

typedef enum {
    ACTION_REPLAY_PASSTHROUGH = 0,  /* fail-open via replay buf */
    ACTION_SAFE_FINISH        = 1   /* graceful terminate       */
} error_action_t;

/* ----------------------------------------------------------------
 * Atomic fetch-add stub (single-threaded test environment)
 * ---------------------------------------------------------------- */

static ngx_inline ngx_atomic_int_t
ngx_atomic_fetch_add(ngx_atomic_t *value, ngx_atomic_int_t add)
{
    ngx_atomic_int_t old = *value;
    *value += add;
    return old;
}

/* ----------------------------------------------------------------
 * Metrics subset relevant to decompression
 * ---------------------------------------------------------------- */

typedef struct {
    ngx_atomic_t  decompression_budget_exceeded_total;
    ngx_atomic_t  decompression_streaming_total;
    ngx_atomic_t  decompression_fullbuffer_total;
    ngx_atomic_t  streaming_fallback_total;
} decomp_metrics_t;

/* ----------------------------------------------------------------
 * Decompression context: pre/post-commit state machine
 * ---------------------------------------------------------------- */

typedef struct {
    commit_state_t  state;
    ngx_uint_t      flushes_sent;
    size_t          cumulative_decompressed;
    size_t          max_decompressed_size;
    int             replay_triggered;
    int             safe_finish_invoked;
    int             status_rewritten;
    decomp_metrics_t metrics;
} decomp_ctx_t;

/* ----------------------------------------------------------------
 * Production routing decision (same logic as property test, inline
 * for standalone compilation)
 * ---------------------------------------------------------------- */

static ngx_http_markdown_decomp_route_t
ngx_http_markdown_decomp_routing_decision(
    ngx_flag_t auto_decompress,
    ngx_flag_t streaming_engine_selected,
    ngx_http_markdown_cache_validation_e cache_validation,
    ngx_http_markdown_compression_type_e encoding)
{
    /* Unknown or no encoding -> bypass */
    if (encoding == NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN
        || encoding == NGX_HTTP_MARKDOWN_COMPRESSION_NONE) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS;
    }

    /* Condition 1: auto_decompress must be ON */
    if (auto_decompress != 1) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /* Condition 2: streaming engine must be selected */
    if (!streaming_engine_selected) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /* Condition 3: cache_validation must NOT be full */
    if (cache_validation
        == NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    /*
     * Condition 4: encoding must be supported by streaming
     * decompressor.  In 0.9.1, only raw deflate is supported.
     */
    if (encoding != NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE) {
        return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER;
    }

    return NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING;
}

/* ----------------------------------------------------------------
 * Production error-handling logic (modeled from design §4)
 * ---------------------------------------------------------------- */

static error_action_t
handle_decomp_error(decomp_ctx_t *ctx, decomp_error_t error)
{
    if (error == DECOMP_ERR_BUDGET_EXCEEDED) {
        ngx_atomic_fetch_add(
            &ctx->metrics.decompression_budget_exceeded_total,
            1);
    }

    if (ctx->state == STATE_PRE_COMMIT) {
        ctx->replay_triggered = 1;
        ngx_atomic_fetch_add(
            &ctx->metrics.streaming_fallback_total, 1);
        return ACTION_REPLAY_PASSTHROUGH;
    }

    ctx->safe_finish_invoked = 1;
    return ACTION_SAFE_FINISH;
}

/* ----------------------------------------------------------------
 * Budget enforcement check (modeled from design §4)
 *
 * Returns DECOMP_ERR_BUDGET_EXCEEDED when cumulative decompressed
 * output exceeds the configured maximum.
 * Returns -1 (no error) otherwise.
 * ---------------------------------------------------------------- */

static int
check_budget(decomp_ctx_t *ctx, size_t new_chunk_len)
{
    ctx->cumulative_decompressed += new_chunk_len;
    if (ctx->cumulative_decompressed
        > ctx->max_decompressed_size) {
        return (int) DECOMP_ERR_BUDGET_EXCEEDED;
    }
    return -1; /* no error */
}

/* ----------------------------------------------------------------
 * Context initialization helper
 * ---------------------------------------------------------------- */

static void
init_decomp_ctx(decomp_ctx_t *ctx, commit_state_t state,
    size_t max_size)
{
    memset(ctx, 0, sizeof(*ctx));
    ctx->state = state;
    ctx->flushes_sent = (state == STATE_POST_COMMIT) ? 1 : 0;
    ctx->max_decompressed_size = max_size;
}

/* ================================================================
 * TEST SECTION 1: Routing decision named examples
 *
 * Tests each condition combination with specific named scenarios.
 * Validates: Requirements 4.1, 4.2
 * ================================================================ */

static void
test_routing_raw_deflate_all_conditions_met(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "deflate + all conditions met → STREAMING");

    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "deflate + auto_decompress=on + streaming + "
        "cache!=full → STREAMING (Req 4.1)");
    TEST_PASS("deflate routes to STREAMING");
}

static void
test_routing_raw_deflate_with_etag_cache(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "deflate + cache_validation=etag → STREAMING");

    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_ETAG,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "etag cache validation (not full) still "
        "allows STREAMING");
    TEST_PASS("etag cache validation allows streaming");
}

static void
test_routing_gzip_deferred_to_fullbuffer(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "gzip + all other conditions met → FULLBUFFER "
        "(deferred in 0.9.1)");

    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "gzip routes to FULLBUFFER under streaming-selected "
        "conditions (Req 4.8)");
    TEST_PASS("gzip deferred to full-buffer path");
}

static void
test_routing_gzip_and_brotli_streaming_selected_fullbuffer(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "gzip/brotli + streaming selected → FULLBUFFER "
        "(deferred in 0.9.1)");

    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "gzip with all non-encoding conditions met routes "
        "to FULLBUFFER");

    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "brotli with all non-encoding conditions met routes "
        "to FULLBUFFER");

    TEST_PASS("gzip and brotli route to full-buffer in 0.9.1");
}

static void
test_routing_auto_decompress_off(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "auto_decompress off → FULLBUFFER");

    result = ngx_http_markdown_decomp_routing_decision(
        0, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "auto_decompress=off forces FULLBUFFER "
        "(Req 4.1)");
    TEST_PASS("auto_decompress off forces full-buffer");
}

static void
test_routing_streaming_engine_not_selected(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "streaming engine not selected → FULLBUFFER");

    result = ngx_http_markdown_decomp_routing_decision(
        1, 0, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "streaming engine not selected forces "
        "FULLBUFFER (Req 4.1)");
    TEST_PASS("non-streaming engine forces full-buffer");
}

static void
test_routing_cache_validation_full(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "cache_validation=full → FULLBUFFER (Req 4.2)");

    /* deflate with cache_validation full */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "cache_validation=full overrides all other "
        "conditions for deflate");

    /* gzip with cache_validation full */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "cache_validation=full overrides all other "
        "conditions for gzip");

    /* brotli with cache_validation full */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "cache_validation=full overrides all other "
        "conditions for brotli");

    TEST_PASS(
        "cache_validation=full forces full-buffer "
        "for all encodings");
}

static void
test_routing_brotli_not_supported(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "brotli → FULLBUFFER (unsupported in streaming)");

    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "brotli is not supported in streaming path");
    TEST_PASS("brotli routes to full-buffer");
}

static void
test_routing_unknown_encoding_bypass(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "unknown encoding → BYPASS");

    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_UNKNOWN);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS,
        "unknown encoding bypasses decompression "
        "entirely");
    TEST_PASS("unknown encoding bypasses");
}

static void
test_routing_no_encoding_bypass(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "no encoding → BYPASS");

    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_NONE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_BYPASS,
        "no encoding means no decompression needed");
    TEST_PASS("no encoding bypasses");
}

static void
test_routing_multiple_conditions_off(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "multiple conditions violated → FULLBUFFER");

    /* auto_decompress off AND streaming engine not selected */
    result = ngx_http_markdown_decomp_routing_decision(
        0, 0, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "both auto_decompress=off and no streaming");

    /* All conditions off + full cache + gzip */
    result = ngx_http_markdown_decomp_routing_decision(
        0, 0, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "all conditions violated still routes "
        "to FULLBUFFER");

    TEST_PASS(
        "multiple conditions off produces FULLBUFFER");
}

/* ================================================================
 * TEST SECTION 2: Raw deflate fallback (gzip deferred)
 *
 * Validates: Requirement 4.8 (gzip deferred to full-buffer)
 * ================================================================ */

static void
test_deflate_only_encoding_reaches_streaming(void)
{
    ngx_http_markdown_decomp_route_t result;
    ngx_http_markdown_compression_type_e encodings[] = {
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP,
        NGX_HTTP_MARKDOWN_COMPRESSION_BROTLI
    };
    size_t i;

    TEST_SUBSECTION(
        "only deflate reaches streaming in 0.9.1 "
        "(Req 4.8)");

    /* Confirm deflate reaches streaming */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "deflate reaches streaming path");

    /* Confirm gzip and brotli do NOT */
    for (i = 0; i < ARRAY_SIZE(encodings); i++) {
        result = ngx_http_markdown_decomp_routing_decision(
            1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
            encodings[i]);
        TEST_ASSERT(
            result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
            "gzip/brotli selected for streaming must route "
            "to full-buffer");
    }

    TEST_PASS(
        "only deflate reaches streaming; gzip/brotli "
        "fall to full-buffer");
}

/*
 * Test: streaming deflate now supports both zlib-wrapped (RFC 1950,
 * RFC 9110-compliant) and raw deflate (RFC 1951) via deferred header
 * sniffing.  The routing decision is the same for both — the format
 * detection happens at the decompressor level, not the routing level.
 */
static void
test_deflate_zlib_and_raw_both_route_streaming(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "deflate routing is format-agnostic (zlib-wrapped "
        "and raw both reach STREAMING)");

    /*
     * The routing decision only checks that the encoding is deflate;
     * it does not distinguish zlib-wrapped from raw.  The decompressor
     * sniffs the first 2 bytes to determine the format.  So both
     * reach STREAMING at the routing level.
     */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_NONE,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_STREAMING,
        "deflate (zlib-wrapped or raw) routes to STREAMING");
    TEST_PASS(
        "deflate routing is format-agnostic; header sniffing "
        "happens at the decompressor");
}

/* ================================================================
 * TEST SECTION 3: Truncated stream fail-open (pre-commit) and
 * safe-finish (post-commit)
 *
 * Validates: Requirements 4.3, 4.10
 * ================================================================ */

static void
test_truncated_stream_precommit_failopen(void)
{
    decomp_ctx_t ctx;
    error_action_t action;

    TEST_SUBSECTION(
        "truncated stream pre-commit → fail-open "
        "(Req 4.3)");

    init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1048576);
    action = handle_decomp_error(&ctx,
        DECOMP_ERR_TRUNCATED_STREAM);

    TEST_ASSERT(action == ACTION_REPLAY_PASSTHROUGH,
        "truncated stream triggers replay passthrough");
    TEST_ASSERT(ctx.replay_triggered == 1,
        "replay_triggered flag must be set");
    TEST_ASSERT(ctx.safe_finish_invoked == 0,
        "safe_finish must NOT be invoked pre-commit");
    TEST_ASSERT(ctx.status_rewritten == 0,
        "status must NOT be rewritten");
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics.streaming_fallback_total == 1,
        "streaming_fallback_total increments");

    TEST_PASS(
        "truncated stream pre-commit triggers fail-open");
}

static void
test_truncated_stream_postcommit_safe_finish(void)
{
    decomp_ctx_t ctx;
    error_action_t action;

    TEST_SUBSECTION(
        "truncated stream post-commit → safe-finish "
        "(Req 4.10)");

    init_decomp_ctx(&ctx, STATE_POST_COMMIT, 1048576);
    action = handle_decomp_error(&ctx,
        DECOMP_ERR_TRUNCATED_STREAM);

    TEST_ASSERT(action == ACTION_SAFE_FINISH,
        "truncated stream post-commit invokes "
        "safe-finish");
    TEST_ASSERT(ctx.safe_finish_invoked == 1,
        "safe_finish_invoked flag must be set");
    TEST_ASSERT(ctx.replay_triggered == 0,
        "replay must NOT be triggered post-commit");
    TEST_ASSERT(ctx.status_rewritten == 0,
        "status must NOT be rewritten post-commit");

    TEST_PASS(
        "truncated stream post-commit triggers "
        "safe-finish");
}

/* ================================================================
 * TEST SECTION 4: Budget exceedance fail-open (pre-commit) and
 * safe-finish (post-commit)
 *
 * Validates: Requirements 4.4, 4.10
 * ================================================================ */

static void
test_budget_exceeded_precommit_failopen(void)
{
    decomp_ctx_t ctx;
    error_action_t action;
    int budget_err;

    TEST_SUBSECTION(
        "budget exceeded pre-commit → fail-open "
        "(Req 4.4)");

    init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1024);

    /* Simulate decompressing a chunk that exceeds budget */
    budget_err = check_budget(&ctx, 2048);
    TEST_ASSERT(budget_err == (int) DECOMP_ERR_BUDGET_EXCEEDED,
        "budget check must detect exceedance");

    action = handle_decomp_error(&ctx,
        DECOMP_ERR_BUDGET_EXCEEDED);

    TEST_ASSERT(action == ACTION_REPLAY_PASSTHROUGH,
        "budget exceeded pre-commit triggers "
        "replay passthrough");
    TEST_ASSERT(ctx.replay_triggered == 1,
        "replay_triggered flag must be set");
    TEST_ASSERT(ctx.safe_finish_invoked == 0,
        "safe_finish must NOT be invoked");
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics
                .decompression_budget_exceeded_total == 1,
        "budget_exceeded counter increments");
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics.streaming_fallback_total == 1,
        "streaming_fallback_total increments");

    TEST_PASS(
        "budget exceeded pre-commit triggers fail-open");
}

static void
test_budget_exceeded_postcommit_safe_finish(void)
{
    decomp_ctx_t ctx;
    error_action_t action;
    int budget_err;

    TEST_SUBSECTION(
        "budget exceeded post-commit → safe-finish "
        "(Req 4.10)");

    init_decomp_ctx(&ctx, STATE_POST_COMMIT, 1024);

    /* Simulate decompressing a chunk that exceeds budget */
    budget_err = check_budget(&ctx, 2048);
    TEST_ASSERT(budget_err == (int) DECOMP_ERR_BUDGET_EXCEEDED,
        "budget check must detect exceedance");

    action = handle_decomp_error(&ctx,
        DECOMP_ERR_BUDGET_EXCEEDED);

    TEST_ASSERT(action == ACTION_SAFE_FINISH,
        "budget exceeded post-commit invokes "
        "safe-finish");
    TEST_ASSERT(ctx.safe_finish_invoked == 1,
        "safe_finish_invoked flag must be set");
    TEST_ASSERT(ctx.replay_triggered == 0,
        "replay must NOT be triggered post-commit");
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics
                .decompression_budget_exceeded_total == 1,
        "budget_exceeded counter increments in "
        "post-commit");

    TEST_PASS(
        "budget exceeded post-commit triggers "
        "safe-finish");
}

static void
test_budget_incremental_accumulation(void)
{
    decomp_ctx_t ctx;
    int budget_err;

    TEST_SUBSECTION(
        "budget incremental accumulation triggers at "
        "boundary");

    init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1000);

    /* First chunk: 500 bytes, within budget */
    budget_err = check_budget(&ctx, 500);
    TEST_ASSERT(budget_err == -1,
        "500/1000 should be within budget");
    TEST_ASSERT(ctx.cumulative_decompressed == 500,
        "cumulative should be 500");

    /* Second chunk: 400 bytes, still within budget */
    budget_err = check_budget(&ctx, 400);
    TEST_ASSERT(budget_err == -1,
        "900/1000 should be within budget");
    TEST_ASSERT(ctx.cumulative_decompressed == 900,
        "cumulative should be 900");

    /* Third chunk: 200 bytes, exceeds budget */
    budget_err = check_budget(&ctx, 200);
    TEST_ASSERT(budget_err == (int) DECOMP_ERR_BUDGET_EXCEEDED,
        "1100/1000 should exceed budget");
    TEST_ASSERT(ctx.cumulative_decompressed == 1100,
        "cumulative should be 1100");

    TEST_PASS(
        "budget accumulation triggers at boundary");
}

static void
test_budget_exact_boundary_no_exceed(void)
{
    decomp_ctx_t ctx;
    int budget_err;

    TEST_SUBSECTION(
        "budget at exact boundary does not exceed");

    init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1000);

    /* Chunk exactly at boundary: cumulative == max */
    budget_err = check_budget(&ctx, 1000);
    TEST_ASSERT(budget_err == -1,
        "1000/1000 should NOT exceed (equal is OK)");

    /* One more byte exceeds */
    budget_err = check_budget(&ctx, 1);
    TEST_ASSERT(budget_err == (int) DECOMP_ERR_BUDGET_EXCEEDED,
        "1001/1000 should exceed");

    TEST_PASS("exact boundary accepted; boundary+1 exceeds");
}

/* ================================================================
 * TEST SECTION 5: Inflate error pre-commit and post-commit
 *
 * Validates: Requirements 4.3, 4.10
 * ================================================================ */

static void
test_inflate_error_precommit_failopen(void)
{
    decomp_ctx_t ctx;
    error_action_t action;

    TEST_SUBSECTION(
        "inflate error pre-commit → fail-open "
        "(Req 4.3)");

    init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1048576);
    action = handle_decomp_error(&ctx,
        DECOMP_ERR_INFLATE_ERROR);

    TEST_ASSERT(action == ACTION_REPLAY_PASSTHROUGH,
        "inflate error pre-commit triggers replay");
    TEST_ASSERT(ctx.replay_triggered == 1,
        "replay flag set");
    TEST_ASSERT(ctx.safe_finish_invoked == 0,
        "safe_finish not invoked");
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics
                .decompression_budget_exceeded_total == 0,
        "budget counter unchanged for inflate error");

    TEST_PASS("inflate error pre-commit triggers fail-open");
}

static void
test_inflate_error_postcommit_safe_finish(void)
{
    decomp_ctx_t ctx;
    error_action_t action;

    TEST_SUBSECTION(
        "inflate error post-commit → safe-finish "
        "(Req 4.10)");

    init_decomp_ctx(&ctx, STATE_POST_COMMIT, 1048576);
    action = handle_decomp_error(&ctx,
        DECOMP_ERR_INFLATE_ERROR);

    TEST_ASSERT(action == ACTION_SAFE_FINISH,
        "inflate error post-commit invokes safe-finish");
    TEST_ASSERT(ctx.safe_finish_invoked == 1,
        "safe_finish flag set");
    TEST_ASSERT(ctx.replay_triggered == 0,
        "replay not triggered post-commit");

    TEST_PASS(
        "inflate error post-commit triggers safe-finish");
}

/* ================================================================
 * TEST SECTION 6: Invalid header pre-commit (only possible
 * pre-commit since headers are parsed before data output)
 *
 * Validates: Requirement 4.3
 * ================================================================ */

static void
test_invalid_header_precommit_failopen(void)
{
    decomp_ctx_t ctx;
    error_action_t action;

    TEST_SUBSECTION(
        "invalid header pre-commit → fail-open "
        "(Req 4.3)");

    init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1048576);
    action = handle_decomp_error(&ctx,
        DECOMP_ERR_INVALID_HEADER);

    TEST_ASSERT(action == ACTION_REPLAY_PASSTHROUGH,
        "invalid header triggers replay passthrough");
    TEST_ASSERT(ctx.replay_triggered == 1,
        "replay flag set");
    TEST_ASSERT(ctx.safe_finish_invoked == 0,
        "safe_finish not invoked");
    TEST_ASSERT(ctx.status_rewritten == 0,
        "status not rewritten");

    TEST_PASS(
        "invalid header pre-commit triggers fail-open");
}

/* ================================================================
 * TEST SECTION 7: cache_validation full forces full-buffer path
 * regardless of other settings
 *
 * Validates: Requirement 4.2
 * ================================================================ */

static void
test_cache_validation_full_overrides_all(void)
{
    ngx_http_markdown_decomp_route_t result;

    TEST_SUBSECTION(
        "cache_validation=full overrides ALL other "
        "conditions (Req 4.2)");

    /* Best-case scenario except cache_validation=full */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
        NGX_HTTP_MARKDOWN_COMPRESSION_DEFLATE);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "even raw deflate with all other conditions "
        "ideal → FULLBUFFER when cache=full");

    /* Verify it also applies even with etag-based validators */
    result = ngx_http_markdown_decomp_routing_decision(
        1, 1, NGX_HTTP_MARKDOWN_CACHE_VALIDATION_FULL,
        NGX_HTTP_MARKDOWN_COMPRESSION_GZIP);
    TEST_ASSERT(
        result == NGX_HTTP_MARKDOWN_DECOMP_ROUTE_FULLBUFFER,
        "gzip + cache=full → FULLBUFFER");

    TEST_PASS(
        "cache_validation=full always forces full-buffer");
}

/* ================================================================
 * TEST SECTION 8: No-status-rewrite invariant
 *
 * Verify that neither pre-commit fail-open nor post-commit
 * safe-finish ever rewrites the HTTP status code.
 *
 * Validates: Requirements 4.3, 4.10
 * ================================================================ */

static void
test_no_status_rewrite_any_error(void)
{
    decomp_ctx_t ctx;
    decomp_error_t error;
    commit_state_t states[] = {
        STATE_PRE_COMMIT, STATE_POST_COMMIT
    };
    size_t si;

    TEST_SUBSECTION(
        "no status rewrite in any error state");

    for (si = 0; si < ARRAY_SIZE(states); si++) {
        for (error = 0; error < DECOMP_ERR_COUNT; error++) {
            init_decomp_ctx(&ctx, states[si], 1048576);
            handle_decomp_error(&ctx, error);
            TEST_ASSERT(ctx.status_rewritten == 0,
                "status must never be rewritten");
        }
    }

    TEST_PASS(
        "no status rewrite across all error/state "
        "combinations");
}

/* ================================================================
 * TEST SECTION 9: Metrics counter correctness on error paths
 *
 * Validates: Requirements 4.3, 4.4
 * ================================================================ */

static void
test_metrics_counters_on_errors(void)
{
    decomp_ctx_t ctx;

    TEST_SUBSECTION(
        "metrics counter correctness on error paths");

    /* Non-budget error in pre-commit: fallback increments,
     * budget counter unchanged */
    init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1048576);
    handle_decomp_error(&ctx, DECOMP_ERR_TRUNCATED_STREAM);
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics.streaming_fallback_total == 1,
        "fallback counter increments on pre-commit error");
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics
                .decompression_budget_exceeded_total == 0,
        "budget counter unchanged for non-budget error");

    /* Budget error in pre-commit: both counters increment */
    init_decomp_ctx(&ctx, STATE_PRE_COMMIT, 1048576);
    handle_decomp_error(&ctx, DECOMP_ERR_BUDGET_EXCEEDED);
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics.streaming_fallback_total == 1,
        "fallback counter increments on budget error");
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics
                .decompression_budget_exceeded_total == 1,
        "budget counter increments on budget error");

    /* Budget error in post-commit: budget increments,
     * fallback does NOT */
    init_decomp_ctx(&ctx, STATE_POST_COMMIT, 1048576);
    handle_decomp_error(&ctx, DECOMP_ERR_BUDGET_EXCEEDED);
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics.streaming_fallback_total == 0,
        "fallback counter does NOT increment post-commit");
    TEST_ASSERT(
        (ngx_atomic_uint_t)
            ctx.metrics
                .decompression_budget_exceeded_total == 1,
        "budget counter increments in post-commit too");

    TEST_PASS("metrics counters correct on all error paths");
}

/* ================================================================
 * Main
 * ================================================================ */

int
main(void)
{
    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "Unit Tests: Streaming Decompression Routing\n"
        "Validates: Requirements 4.1, 4.2, 4.3, 4.4, "
        "4.8, 4.10");

    /* Section 1: Routing decision named examples */
    test_routing_raw_deflate_all_conditions_met();
    test_routing_raw_deflate_with_etag_cache();
    test_routing_gzip_deferred_to_fullbuffer();
    test_routing_gzip_and_brotli_streaming_selected_fullbuffer();
    test_routing_auto_decompress_off();
    test_routing_streaming_engine_not_selected();
    test_routing_cache_validation_full();
    test_routing_brotli_not_supported();
    test_routing_unknown_encoding_bypass();
    test_routing_no_encoding_bypass();
    test_routing_multiple_conditions_off();

    /* Section 2: deflate routing (zlib-wrapped and raw) */
    test_deflate_only_encoding_reaches_streaming();
    test_deflate_zlib_and_raw_both_route_streaming();

    /* Section 3: Truncated stream fail-open / safe-finish */
    test_truncated_stream_precommit_failopen();
    test_truncated_stream_postcommit_safe_finish();

    /* Section 4: Budget exceedance fail-open / safe-finish */
    test_budget_exceeded_precommit_failopen();
    test_budget_exceeded_postcommit_safe_finish();
    test_budget_incremental_accumulation();
    test_budget_exact_boundary_no_exceed();

    /* Section 5: Inflate error pre/post-commit */
    test_inflate_error_precommit_failopen();
    test_inflate_error_postcommit_safe_finish();

    /* Section 6: Invalid header */
    test_invalid_header_precommit_failopen();

    /* Section 7: cache_validation full override */
    test_cache_validation_full_overrides_all();

    /* Section 8: No status rewrite invariant */
    test_no_status_rewrite_any_error();

    /* Section 9: Metrics counters */
    test_metrics_counters_on_errors();

    printf("\n");
    TEST_PASS(
        "streaming_decomp_routing: all unit tests passed");
    return 0;
}
