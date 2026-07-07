/*
 * Test: copy_reduction
 *
 * Unit tests for the full-buffer compressed copy reduction paths
 * (Spec 62 / Design §5).
 *
 * Feature: 0.9.1-performance-optimization
 * Validates: Requirements 5.1, 5.2, 5.3, 5.5
 *
 * Test cases:
 *   1. Contiguous buffer skip path — verify linearize copy is
 *      skipped when ctx->buffer.data is already contiguous
 *   2. Direct swap on success — verify old buffer freed, new
 *      decompressed buffer installed in ctx->buffer.data
 *   3. Failure preservation — verify original buffer bytes
 *      unchanged after decompression failure
 *   4. ngx_alloc failure — verify fail-open with original
 *      buffer (no crash, no corruption)
 *
 * This is a MODEL TEST — it exercises the logical state
 * transitions of the copy-reduction paths without calling
 * the production Rust FFI decompressor.
 *
 * Production integration is covered by:
 *   - Rust unit tests in components/rust-converter/src/decompress.rs
 *   - E2E tests via make verify-chunked-native-e2e-smoke
 *   - The production path in ngx_http_markdown_payload_impl.h
 *
 * Rules: 43 (ngx_alloc/ngx_free for resizable buffers),
 *        3 (free auxiliary buffers on all exits).
 */

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Model types and stubs
 * ---------------------------------------------------------------- */

typedef intptr_t    ngx_int_t;
typedef uintptr_t   ngx_uint_t;

enum {
    NGX_OK    =  0,
    NGX_ERROR = -1
};

/*
 * Decompression result codes (model).
 */
enum {
    DECOMP_RESULT_OK              = 0,
    DECOMP_RESULT_FORMAT_ERROR    = 1,
    DECOMP_RESULT_TRUNCATED       = 2,
    DECOMP_RESULT_BUDGET_EXCEEDED = 9,
    DECOMP_RESULT_ALLOC_FAILURE   = 10
};

/*
 * Model of ctx->buffer — the resizable payload buffer.
 * In production this is managed with ngx_alloc/ngx_free (Rule 43).
 */
typedef struct {
    unsigned char  *data;
    size_t          size;
    size_t          capacity;
} model_buffer_t;

/*
 * Decompression context model.
 *
 * Tracks allocation/free events and models the contiguity
 * invariant, direct swap, and fail-open behaviors.
 */
typedef struct {
    model_buffer_t  buffer;
    size_t          decompress_max_size;
    int             failopen_triggered;
    int             old_buffer_freed;
    int             new_buffer_freed;
    int             linearize_copy_performed;
    int             decompression_done;
} copy_reduction_ctx_t;

/*
 * Allocation tracking — counts ngx_alloc and ngx_free calls.
 */
static int g_alloc_count;
static int g_free_count;
static int g_alloc_should_fail;

static void
reset_alloc_tracking(void)
{
    g_alloc_count = 0;
    g_free_count = 0;
    g_alloc_should_fail = 0;
}

/*
 * Model ngx_alloc: allocate memory (or return NULL if failure
 * simulation is active).
 */
static unsigned char *
model_ngx_alloc(size_t size)
{
    if (g_alloc_should_fail) {
        return NULL;
    }
    g_alloc_count++;
    return (unsigned char *) malloc(size);
}

/*
 * Model ngx_free: free memory and track the call.
 */
static void
model_ngx_free(void *ptr)
{
    if (ptr != NULL) {
        g_free_count++;
        free(ptr);
    }
}

/* ----------------------------------------------------------------
 * Model functions replicating production copy-reduction logic
 * ---------------------------------------------------------------- */

/*
 * Prepare compressed chain (contiguity check).
 *
 * Models ngx_http_markdown_prepare_compressed_chain:
 *   - If buffer is already contiguous (single ngx_alloc allocation),
 *     reference it directly without copying
 *   - If buffer would need linearizing (multi-buffer chain), perform
 *     a linearize copy
 *
 * In production, after body-filter accumulation the buffer is always
 * contiguous (Rule 43 invariant), so the linearize path is defensive.
 *
 * Returns:
 *   NGX_OK on success (input_buf/input_size set)
 *   NGX_ERROR on failure
 */
static ngx_int_t
model_prepare_compressed(copy_reduction_ctx_t *ctx,
    unsigned char **input_buf, size_t *input_size,
    int buffer_is_contiguous)
{
    if (ctx->buffer.data == NULL || ctx->buffer.size == 0) {
        return NGX_ERROR;
    }

    if (buffer_is_contiguous) {
        /*
         * Contiguous fast path: reference buffer directly.
         * No linearize copy needed (Requirement 5.1).
         */
        *input_buf = ctx->buffer.data;
        *input_size = ctx->buffer.size;
        ctx->linearize_copy_performed = 0;
        return NGX_OK;
    }

    /*
     * Non-contiguous (defensive path): perform linearize copy.
     * This path is included for completeness but should not be
     * hit in normal operation.
     */
    *input_buf = model_ngx_alloc(ctx->buffer.size);
    if (*input_buf == NULL) {
        return NGX_ERROR;
    }
    memcpy(*input_buf, ctx->buffer.data, ctx->buffer.size);
    *input_size = ctx->buffer.size;
    ctx->linearize_copy_performed = 1;
    return NGX_OK;
}

/*
 * Apply decompressed payload (direct swap).
 *
 * Models ngx_http_markdown_apply_decompressed_payload:
 *   - On success: free old ctx->buffer.data, swap in new pointer
 *   - On budget exceeded: free new buffer, keep original intact
 *   - On NULL decompressor output: trigger fail-open
 *
 * Returns:
 *   NGX_OK on success (swap completed)
 *   NGX_ERROR on failure (original buffer preserved for fail-open)
 */
static ngx_int_t
model_apply_decompressed(copy_reduction_ctx_t *ctx,
    unsigned char *decompressed_data, size_t decompressed_size,
    int decomp_result)
{
    if (decomp_result != DECOMP_RESULT_OK) {
        /*
         * Decompression failed: free the decompressed output
         * buffer (if any) and preserve original ctx->buffer.data
         * intact for fail-open passthrough (Requirement 5.3).
         */
        if (decompressed_data != NULL) {
            model_ngx_free(decompressed_data);
            ctx->new_buffer_freed = 1;
        }
        ctx->failopen_triggered = 1;
        return NGX_ERROR;
    }

    if (decompressed_data == NULL) {
        /* NULL output from decompressor — fail-open */
        ctx->failopen_triggered = 1;
        return NGX_ERROR;
    }

    /*
     * Budget check: verify decompressed size does not exceed
     * markdown_decompress_max_size before swapping
     * (Requirement 5.4).
     */
    if (decompressed_size > ctx->decompress_max_size) {
        model_ngx_free(decompressed_data);
        ctx->new_buffer_freed = 1;
        ctx->failopen_triggered = 1;
        return NGX_ERROR;
    }

    /*
     * Direct buffer swap (Requirement 5.2):
     * Free old compressed buffer, install decompressed pointer.
     */
    if (ctx->buffer.data != NULL) {
        model_ngx_free(ctx->buffer.data);
        ctx->old_buffer_freed = 1;
    }

    ctx->buffer.data = decompressed_data;
    ctx->buffer.size = decompressed_size;
    ctx->buffer.capacity = decompressed_size;
    ctx->decompression_done = 1;

    return NGX_OK;
}

/*
 * Full decompression pipeline: allocate output buffer, decompress,
 * apply result.
 *
 * Models the production flow:
 *   1. ngx_alloc for decompressor output
 *   2. Invoke decompressor
 *   3. Apply result via model_apply_decompressed
 *
 * If ngx_alloc fails, triggers fail-open (Requirement 5.5).
 */
static ngx_int_t
model_decompress_pipeline(copy_reduction_ctx_t *ctx,
    size_t decompressed_size, int decomp_result)
{
    unsigned char *output_buf;

    /* Allocate output buffer (Rule 43: ngx_alloc/ngx_free) */
    output_buf = model_ngx_alloc(decompressed_size);
    if (output_buf == NULL) {
        /* ngx_alloc failure: fail-open (Requirement 5.5) */
        ctx->failopen_triggered = 1;
        return NGX_ERROR;
    }

    /* Fill with deterministic pattern for verification */
    memset(output_buf, 0xDE, decompressed_size);

    return model_apply_decompressed(ctx, output_buf,
        decompressed_size, decomp_result);
}

/* ----------------------------------------------------------------
 * Helper: initialize context with test data
 * ---------------------------------------------------------------- */

static void
init_ctx(copy_reduction_ctx_t *ctx, size_t compressed_size,
    size_t max_decomp_size)
{
    memset(ctx, 0, sizeof(*ctx));
    ctx->decompress_max_size = max_decomp_size;

    if (compressed_size > 0) {
        ctx->buffer.data = model_ngx_alloc(compressed_size);
        TEST_ASSERT(ctx->buffer.data != NULL,
            "test setup: buffer alloc must succeed");
        /* Fill with recognizable pattern */
        memset(ctx->buffer.data, 0xAA, compressed_size);
        ctx->buffer.size = compressed_size;
        ctx->buffer.capacity = compressed_size;
    }
}

/* ----------------------------------------------------------------
 * Test 1: Contiguous buffer skip path
 *
 * Verify that when ctx->buffer.data is already contiguous,
 * the linearize copy is skipped and the buffer is referenced
 * directly.
 *
 * Validates: Requirement 5.1
 * ---------------------------------------------------------------- */

static void
test_contiguous_buffer_skip(void)
{
    copy_reduction_ctx_t ctx;
    unsigned char *input_buf;
    size_t input_size;
    ngx_int_t rc;

    TEST_SUBSECTION(
        "Contiguous buffer: linearize copy skipped");

    reset_alloc_tracking();
    init_ctx(&ctx, 4096, 1048576);

    rc = model_prepare_compressed(
        &ctx, &input_buf, &input_size, 1);

    TEST_ASSERT(rc == NGX_OK,
        "prepare_compressed succeeds for contiguous buffer");
    TEST_ASSERT(input_buf == ctx.buffer.data,
        "input_buf references ctx->buffer.data directly");
    TEST_ASSERT(input_size == 4096,
        "input_size equals buffer.size");
    TEST_ASSERT(ctx.linearize_copy_performed == 0,
        "linearize copy was NOT performed");

    /* Cleanup */
    model_ngx_free(ctx.buffer.data);

    TEST_PASS(
        "contiguous buffer referenced directly, "
        "no linearize copy");
}

/*
 * Contrast test: non-contiguous buffer DOES perform linearize copy.
 */
static void
test_noncontiguous_buffer_linearizes(void)
{
    copy_reduction_ctx_t ctx;
    unsigned char *input_buf;
    size_t input_size;
    ngx_int_t rc;

    TEST_SUBSECTION(
        "Non-contiguous buffer: linearize copy performed");

    reset_alloc_tracking();
    init_ctx(&ctx, 2048, 1048576);

    rc = model_prepare_compressed(
        &ctx, &input_buf, &input_size, 0);

    TEST_ASSERT(rc == NGX_OK,
        "prepare_compressed succeeds (linearized)");
    TEST_ASSERT(input_buf != ctx.buffer.data,
        "input_buf is a NEW allocation (not original)");
    TEST_ASSERT(input_size == 2048,
        "input_size equals buffer.size");
    TEST_ASSERT(ctx.linearize_copy_performed == 1,
        "linearize copy WAS performed");
    TEST_ASSERT(MEM_EQ(input_buf, ctx.buffer.data, 2048),
        "linearized content matches original");

    /* Cleanup */
    model_ngx_free(input_buf);
    model_ngx_free(ctx.buffer.data);

    TEST_PASS(
        "non-contiguous buffer linearized via copy");
}

/* ----------------------------------------------------------------
 * Test 2: Direct swap on success
 *
 * Verify that on decompression success:
 *   - Old buffer (ctx->buffer.data) is freed
 *   - New decompressed buffer is installed in ctx->buffer.data
 *   - ctx->buffer.size/capacity updated correctly
 *
 * Validates: Requirement 5.2
 * ---------------------------------------------------------------- */

static void
test_direct_swap_on_success(void)
{
    copy_reduction_ctx_t ctx;
    ngx_int_t rc;
    unsigned char *original_ptr;

    TEST_SUBSECTION(
        "Direct swap on success: old freed, new installed");

    reset_alloc_tracking();
    init_ctx(&ctx, 8192, 1048576);
    original_ptr = ctx.buffer.data;

    /* Simulate successful decompression producing 16KB output */
    rc = model_decompress_pipeline(&ctx, 16384, DECOMP_RESULT_OK);

    TEST_ASSERT(rc == NGX_OK,
        "pipeline succeeds");
    TEST_ASSERT(ctx.old_buffer_freed == 1,
        "old buffer was freed");
    TEST_ASSERT(ctx.buffer.data != original_ptr,
        "ctx->buffer.data points to new allocation");
    TEST_ASSERT(ctx.buffer.data != NULL,
        "ctx->buffer.data is not NULL");
    TEST_ASSERT(ctx.buffer.size == 16384,
        "buffer.size updated to decompressed size");
    TEST_ASSERT(ctx.buffer.capacity == 16384,
        "buffer.capacity updated to decompressed size");
    TEST_ASSERT(ctx.decompression_done == 1,
        "decompression_done flag set");
    TEST_ASSERT(ctx.failopen_triggered == 0,
        "fail-open NOT triggered");

    /* Verify decompressed content pattern */
    TEST_ASSERT(ctx.buffer.data[0] == 0xDE,
        "decompressed data has expected fill pattern");
    TEST_ASSERT(ctx.buffer.data[16383] == 0xDE,
        "decompressed data end has expected fill pattern");

    /* Cleanup */
    model_ngx_free(ctx.buffer.data);

    TEST_PASS(
        "direct swap: old freed, new installed with "
        "correct size/capacity");
}

/*
 * Test: direct swap with various sizes to verify consistency.
 */
static void
test_direct_swap_various_sizes(void)
{
    copy_reduction_ctx_t ctx;
    ngx_int_t rc;
    size_t compressed_sizes[] = { 256, 4096, 65536 };
    size_t decompressed_sizes[] = { 512, 8192, 131072 };
    size_t i;

    TEST_SUBSECTION(
        "Direct swap: various size combinations");

    for (i = 0; i < ARRAY_SIZE(compressed_sizes); i++) {
        reset_alloc_tracking();
        init_ctx(&ctx, compressed_sizes[i], 1048576);

        rc = model_decompress_pipeline(
            &ctx, decompressed_sizes[i], DECOMP_RESULT_OK);

        TEST_ASSERT(rc == NGX_OK,
            "pipeline succeeds for size combo");
        TEST_ASSERT(ctx.old_buffer_freed == 1,
            "old buffer freed");
        TEST_ASSERT(ctx.buffer.size == decompressed_sizes[i],
            "buffer.size matches decompressed size");

        model_ngx_free(ctx.buffer.data);
    }

    TEST_PASS(
        "direct swap correct for multiple size "
        "combinations");
}

/* ----------------------------------------------------------------
 * Test 3: Decompression failure preserving original buffer
 *
 * Verify that on decompression failure:
 *   - Original ctx->buffer.data bytes are unchanged
 *   - Decompressor output buffer is freed
 *   - fail-open is triggered
 *
 * Validates: Requirement 5.3
 * ---------------------------------------------------------------- */

static void
test_failure_preserves_original(void)
{
    copy_reduction_ctx_t ctx;
    ngx_int_t rc;
    unsigned char original_copy[4096];

    TEST_SUBSECTION(
        "Decompression failure preserves original buffer");

    reset_alloc_tracking();
    init_ctx(&ctx, 4096, 1048576);

    /* Save a copy of the original data for comparison */
    memcpy(original_copy, ctx.buffer.data, 4096);

    /* Simulate decompression failure (format error) */
    rc = model_decompress_pipeline(
        &ctx, 8192, DECOMP_RESULT_FORMAT_ERROR);

    TEST_ASSERT(rc == NGX_ERROR,
        "pipeline returns error on format failure");
    TEST_ASSERT(ctx.failopen_triggered == 1,
        "fail-open triggered");
    TEST_ASSERT(ctx.new_buffer_freed == 1,
        "decompressor output buffer freed");
    TEST_ASSERT(ctx.old_buffer_freed == 0,
        "original buffer NOT freed");
    TEST_ASSERT(ctx.buffer.data != NULL,
        "ctx->buffer.data still valid");
    TEST_ASSERT(ctx.buffer.size == 4096,
        "buffer.size unchanged");
    TEST_ASSERT(MEM_EQ(ctx.buffer.data, original_copy, 4096),
        "original buffer bytes unchanged");
    TEST_ASSERT(ctx.decompression_done == 0,
        "decompression_done NOT set");

    /* Cleanup */
    model_ngx_free(ctx.buffer.data);

    TEST_PASS(
        "decompression failure: original buffer "
        "preserved intact for fail-open");
}

/*
 * Test failure preservation for all error types.
 */
static void
test_failure_preserves_all_error_types(void)
{
    copy_reduction_ctx_t ctx;
    ngx_int_t rc;
    unsigned char original_copy[2048];
    int error_codes[] = {
        DECOMP_RESULT_FORMAT_ERROR,
        DECOMP_RESULT_TRUNCATED,
        DECOMP_RESULT_BUDGET_EXCEEDED
    };
    size_t i;

    TEST_SUBSECTION(
        "Failure preservation for all error types");

    for (i = 0; i < ARRAY_SIZE(error_codes); i++) {
        reset_alloc_tracking();
        init_ctx(&ctx, 2048, 1048576);
        memcpy(original_copy, ctx.buffer.data, 2048);

        rc = model_decompress_pipeline(
            &ctx, 4096, error_codes[i]);

        TEST_ASSERT(rc == NGX_ERROR,
            "pipeline returns error");
        TEST_ASSERT(ctx.failopen_triggered == 1,
            "fail-open triggered");
        TEST_ASSERT(ctx.buffer.data != NULL,
            "buffer.data still valid");
        TEST_ASSERT(ctx.buffer.size == 2048,
            "buffer.size unchanged");
        TEST_ASSERT(
            MEM_EQ(ctx.buffer.data, original_copy, 2048),
            "original bytes unchanged");

        model_ngx_free(ctx.buffer.data);
    }

    TEST_PASS(
        "all error types preserve original buffer");
}

/*
 * Test budget exceeded specifically: decompressed output larger
 * than decompress_max_size.
 */
static void
test_budget_exceeded_preserves_original(void)
{
    copy_reduction_ctx_t ctx;
    ngx_int_t rc;
    unsigned char original_copy[1024];

    TEST_SUBSECTION(
        "Budget exceeded preserves original buffer");

    reset_alloc_tracking();
    /* Set max_size to 4096, attempt to produce 8192 bytes */
    init_ctx(&ctx, 1024, 4096);
    memcpy(original_copy, ctx.buffer.data, 1024);

    /*
     * Pipeline with DECOMP_RESULT_OK but output exceeds budget.
     * The apply function checks the budget before swapping.
     */
    rc = model_apply_decompressed(&ctx,
        model_ngx_alloc(8192), 8192, DECOMP_RESULT_OK);

    TEST_ASSERT(rc == NGX_ERROR,
        "budget exceeded returns error");
    TEST_ASSERT(ctx.failopen_triggered == 1,
        "fail-open triggered on budget exceeded");
    TEST_ASSERT(ctx.new_buffer_freed == 1,
        "over-budget buffer freed");
    TEST_ASSERT(ctx.old_buffer_freed == 0,
        "original buffer NOT freed");
    TEST_ASSERT(ctx.buffer.size == 1024,
        "buffer.size unchanged");
    TEST_ASSERT(
        MEM_EQ(ctx.buffer.data, original_copy, 1024),
        "original bytes unchanged after budget exceeded");

    /* Cleanup */
    model_ngx_free(ctx.buffer.data);

    TEST_PASS(
        "budget exceeded: original buffer preserved, "
        "over-budget output freed");
}

/* ----------------------------------------------------------------
 * Test 4: ngx_alloc failure triggering fail-open
 *
 * Verify that when ngx_alloc fails for the decompressor output
 * buffer:
 *   - fail-open is triggered
 *   - Original ctx->buffer.data is preserved (no crash)
 *   - No dangling pointer, no double-free
 *
 * Validates: Requirement 5.5
 * ---------------------------------------------------------------- */

static void
test_alloc_failure_failopen(void)
{
    copy_reduction_ctx_t ctx;
    ngx_int_t rc;
    unsigned char original_copy[4096];

    TEST_SUBSECTION(
        "ngx_alloc failure triggers fail-open");

    reset_alloc_tracking();
    init_ctx(&ctx, 4096, 1048576);
    memcpy(original_copy, ctx.buffer.data, 4096);

    /* Simulate ngx_alloc failure */
    g_alloc_should_fail = 1;

    rc = model_decompress_pipeline(
        &ctx, 16384, DECOMP_RESULT_OK);

    TEST_ASSERT(rc == NGX_ERROR,
        "pipeline returns error on alloc failure");
    TEST_ASSERT(ctx.failopen_triggered == 1,
        "fail-open triggered");
    TEST_ASSERT(ctx.buffer.data != NULL,
        "ctx->buffer.data still valid (not freed)");
    TEST_ASSERT(ctx.buffer.size == 4096,
        "buffer.size unchanged");
    TEST_ASSERT(
        MEM_EQ(ctx.buffer.data, original_copy, 4096),
        "original buffer bytes unchanged after "
        "alloc failure");
    TEST_ASSERT(ctx.old_buffer_freed == 0,
        "original buffer NOT freed on alloc failure");
    TEST_ASSERT(ctx.decompression_done == 0,
        "decompression_done NOT set");

    /* Cleanup */
    g_alloc_should_fail = 0;
    model_ngx_free(ctx.buffer.data);

    TEST_PASS(
        "ngx_alloc failure: fail-open with original "
        "buffer intact, no crash");
}

/*
 * Test alloc failure does not leave dangling state.
 */
static void
test_alloc_failure_no_dangling(void)
{
    copy_reduction_ctx_t ctx;
    ngx_int_t rc;

    TEST_SUBSECTION(
        "ngx_alloc failure: no dangling pointers");

    reset_alloc_tracking();
    init_ctx(&ctx, 2048, 1048576);

    g_alloc_should_fail = 1;

    rc = model_decompress_pipeline(
        &ctx, 8192, DECOMP_RESULT_OK);

    TEST_ASSERT(rc == NGX_ERROR,
        "pipeline returns error");
    TEST_ASSERT(ctx.new_buffer_freed == 0,
        "no new buffer to free (alloc failed)");
    TEST_ASSERT(ctx.old_buffer_freed == 0,
        "old buffer not freed");

    /* Verify we can still access the original buffer safely */
    TEST_ASSERT(ctx.buffer.data[0] == 0xAA,
        "original buffer readable after alloc failure");
    TEST_ASSERT(ctx.buffer.data[2047] == 0xAA,
        "original buffer end readable after alloc failure");

    g_alloc_should_fail = 0;
    model_ngx_free(ctx.buffer.data);

    TEST_PASS(
        "alloc failure leaves no dangling pointers");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "copy_reduction: Full-Buffer Compressed Copy "
        "Reduction Paths\n"
        "Validates: Requirements 5.1, 5.2, 5.3, 5.5");

    /* Test 1: Contiguous buffer skip */
    test_contiguous_buffer_skip();
    test_noncontiguous_buffer_linearizes();

    /* Test 2: Direct swap on success */
    test_direct_swap_on_success();
    test_direct_swap_various_sizes();

    /* Test 3: Failure preservation */
    test_failure_preserves_original();
    test_failure_preserves_all_error_types();
    test_budget_exceeded_preserves_original();

    /* Test 4: ngx_alloc failure fail-open */
    test_alloc_failure_failopen();
    test_alloc_failure_no_dangling();

    printf("\n");
    TEST_PASS(
        "copy_reduction: all unit tests passed");
    return 0;
}
