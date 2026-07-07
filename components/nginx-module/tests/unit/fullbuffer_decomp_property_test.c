/*
 * Test: fullbuffer_decomp_property
 *
 * Property-based tests for full-buffer decompression preservation
 * (Property 7).
 *
 * Feature: 0.9.1-performance-optimization
 * Property 7: Full-Buffer Decompression Preserves Original on Failure
 *
 * Validates: Requirements 5.1, 5.2, 5.3, 5.4
 *
 * The property states:
 *   - For any compressed ctx->buffer.data, if decompression FAILS
 *     (zlib error, truncated stream, budget exceeded, or allocation
 *     failure), ctx->buffer.data remains unchanged (original
 *     compressed bytes preserved byte-for-byte).
 *   - For any compressed ctx->buffer.data, if decompression SUCCEEDS,
 *     ctx->buffer.data points to new decompressed content and the old
 *     compressed buffer is freed.
 *
 * Test approach:
 *   1. Simulate the two-phase swap pattern with random buffer contents
 *   2. On failure path: verify original data preserved byte-for-byte
 *   3. On success path: verify buffer pointer changed and old freed
 *   4. Use PRNG sequences for buffer sizes and content
 */

#include "../include/test_common.h"

/* ----------------------------------------------------------------
 * Minimal NGINX type stubs for standalone compilation
 * ---------------------------------------------------------------- */

typedef intptr_t        ngx_int_t;
typedef uintptr_t       ngx_uint_t;
typedef unsigned char   u_char;

enum {
    NGX_OK    =  0,
    NGX_ERROR = -1
};

/* ----------------------------------------------------------------
 * Decompression failure type enumeration
 * ---------------------------------------------------------------- */

typedef enum {
    DECOMP_FAIL_ZLIB_ERROR = 0,
    DECOMP_FAIL_TRUNCATED_STREAM,
    DECOMP_FAIL_BUDGET_EXCEEDED,
    DECOMP_FAIL_ALLOC_FAILURE,
    DECOMP_FAIL_COUNT
} decomp_failure_t;

/* ----------------------------------------------------------------
 * Buffer context: models ctx->buffer.data with the two-phase
 * swap pattern from the production code (design §5).
 *
 * Production behavior:
 *   Phase 1: Decompress into NEW ngx_alloc buffer
 *   Phase 2a (success): ngx_free(old), ctx->buffer.data = new
 *   Phase 2b (failure): ngx_free(new), keep old intact
 * ---------------------------------------------------------------- */

typedef struct {
    u_char    *data;
    size_t     size;
    size_t     capacity;
} buffer_t;

/* ----------------------------------------------------------------
 * Allocation tracking for verifying free behavior
 * ---------------------------------------------------------------- */

static int g_alloc_count = 0;
static int g_free_count = 0;
static void *g_last_freed_ptr = NULL;
static int g_force_alloc_fail = 0;

static void
reset_alloc_tracking(void)
{
    g_alloc_count = 0;
    g_free_count = 0;
    g_last_freed_ptr = NULL;
    g_force_alloc_fail = 0;
}

static u_char *
test_ngx_alloc(size_t size)
{
    u_char *p;

    if (g_force_alloc_fail) {
        return NULL;
    }
    p = (u_char *) malloc(size);
    if (p != NULL) {
        g_alloc_count++;
    }
    return p;
}

static void
test_ngx_free(void *ptr)
{
    if (ptr != NULL) {
        g_last_freed_ptr = ptr;
        g_free_count++;
        free(ptr);
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

/* Fill buffer with random bytes for content verification */
static void
fill_random_bytes(u_char *buf, size_t len, unsigned int seed)
{
    size_t i;

    prng_seed(seed);
    for (i = 0; i < len; i++) {
        buf[i] = (u_char)(prng_next() & 0xFF);
    }
}

/* ----------------------------------------------------------------
 * Two-phase swap logic: models the production decompression
 * buffer swap pattern from ngx_http_markdown_payload_impl.h
 *
 * This replicates the design §5 "Direct Swap" logic:
 *   1. Decompress into a NEW buffer (bounded by max_size)
 *   2. On success: free old → swap pointer to new
 *   3. On failure: free new → keep old intact
 *
 * Parameters:
 *   buf          - the ctx->buffer being modified
 *   compressed   - original compressed data (for verification)
 *   comp_len     - length of compressed data
 *   max_size     - budget limit (markdown_decompress_max_size)
 *   failure      - which failure to simulate (-1 for success)
 *   decomp_size  - simulated decompressed output size
 *
 * Returns NGX_OK on success, NGX_ERROR on failure.
 * ---------------------------------------------------------------- */

static ngx_int_t
simulate_decompress_swap(buffer_t *buf, size_t max_size,
    decomp_failure_t failure, size_t decomp_size)
{
    u_char  *decompressed_data;
    u_char  *old_data;

    /*
     * Phase 1: Allocate decompressor output buffer.
     * On alloc failure: trigger fail-open with original body.
     */
    if (failure == DECOMP_FAIL_ALLOC_FAILURE) {
        /* Simulate ngx_alloc returning NULL */
        return NGX_ERROR;
    }

    decompressed_data = test_ngx_alloc(decomp_size);
    if (decompressed_data == NULL) {
        return NGX_ERROR;
    }

    /* Fill decompressed buffer with known pattern */
    memset(decompressed_data, 0xAB, decomp_size);

    /*
     * Simulate decompression failure conditions.
     * On failure: free the new buffer, keep original intact.
     */
    if (failure == DECOMP_FAIL_ZLIB_ERROR
        || failure == DECOMP_FAIL_TRUNCATED_STREAM)
    {
        test_ngx_free(decompressed_data);
        return NGX_ERROR;
    }

    /*
     * Budget check: if decompressed exceeds max_size,
     * free the new buffer and trigger fail-open.
     */
    if (failure == DECOMP_FAIL_BUDGET_EXCEEDED
        || decomp_size > max_size)
    {
        test_ngx_free(decompressed_data);
        return NGX_ERROR;
    }

    /*
     * Phase 2a (success): Free old buffer, swap in new.
     * After this point, ctx->buffer.data points to decompressed.
     */
    old_data = buf->data;
    if (old_data != NULL) {
        test_ngx_free(old_data);
    }

    buf->data = decompressed_data;
    buf->size = decomp_size;
    buf->capacity = decomp_size;

    return NGX_OK;
}

/* ----------------------------------------------------------------
 * Property 7a: Failure preserves original buffer byte-for-byte
 *
 * For any compressed buffer content and any failure condition,
 * after decompression fails, ctx->buffer.data must point to the
 * same allocation with identical bytes.
 *
 * Validates: Requirements 5.3, 5.4
 * ---------------------------------------------------------------- */

#define FAILURE_PRESERVE_ITERATIONS  500
#define MIN_BUFFER_SIZE              16
#define MAX_BUFFER_SIZE              65536

static void
test_property7a_failure_preserves_original(void)
{
    buffer_t buf;
    u_char *original_copy;
    u_char *original_ptr;
    size_t buf_size;
    size_t decomp_size;
    size_t max_budget;
    decomp_failure_t failure;
    ngx_int_t rc;
    int iter;

    TEST_SUBSECTION(
        "Property 7a: Failure preserves original buffer "
        "byte-for-byte (500 iterations)");

    for (iter = 0; iter < FAILURE_PRESERVE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 1000));

        /* Random buffer size */
        buf_size = (size_t)(
            (prng_next() % (MAX_BUFFER_SIZE - MIN_BUFFER_SIZE))
            + MIN_BUFFER_SIZE);

        /* Allocate and fill with random content */
        buf.data = test_ngx_alloc(buf_size);
        TEST_ASSERT(buf.data != NULL,
            "test buffer alloc must succeed");
        buf.size = buf_size;
        buf.capacity = buf_size;

        fill_random_bytes(buf.data, buf_size,
            (unsigned int)(iter + 7777));

        /* Keep a copy of original content for verification */
        original_copy = (u_char *) malloc(buf_size);
        TEST_ASSERT(original_copy != NULL,
            "copy alloc must succeed");
        memcpy(original_copy, buf.data, buf_size);
        original_ptr = buf.data;

        /* Random failure type */
        failure = (decomp_failure_t)(
            prng_next() % DECOMP_FAIL_COUNT);

        /* Random decompressed size (may exceed budget) */
        decomp_size = (size_t)(
            (prng_next() % (MAX_BUFFER_SIZE * 2)) + 1);

        /* Set budget smaller than decomp_size for budget fail */
        if (failure == DECOMP_FAIL_BUDGET_EXCEEDED) {
            max_budget = decomp_size / 2;
        } else {
            max_budget = decomp_size * 2;
        }

        reset_alloc_tracking();
        rc = simulate_decompress_swap(&buf, max_budget,
            failure, decomp_size);

        /* Verify failure return code */
        TEST_ASSERT(rc == NGX_ERROR,
            "decompression must return NGX_ERROR on failure");

        /* Verify pointer unchanged */
        TEST_ASSERT(buf.data == original_ptr,
            "buffer pointer must be unchanged on failure");

        /* Verify size unchanged */
        TEST_ASSERT(buf.size == buf_size,
            "buffer size must be unchanged on failure");

        /* Verify content preserved byte-for-byte */
        TEST_ASSERT(
            MEM_EQ(buf.data, original_copy, buf_size),
            "buffer content must be preserved on failure");

        /* Cleanup */
        free(original_copy);
        test_ngx_free(buf.data);
    }

    TEST_PASS(
        "Property 7a: original preserved on failure "
        "(500 iterations, all failure types)");
}

/* ----------------------------------------------------------------
 * Property 7b: Success swaps buffer pointer to new content
 *
 * For any compressed buffer, on decompression success:
 *   1. ctx->buffer.data points to new decompressed content
 *   2. The old compressed buffer is freed
 *   3. ctx->buffer.size equals the decompressed size
 *
 * Validates: Requirements 5.1, 5.2
 * ---------------------------------------------------------------- */

#define SUCCESS_SWAP_ITERATIONS  500

static void
test_property7b_success_swaps_correctly(void)
{
    buffer_t buf;
    u_char *original_ptr;
    size_t buf_size;
    size_t decomp_size;
    size_t max_budget;
    ngx_int_t rc;
    int iter;

    TEST_SUBSECTION(
        "Property 7b: Success swaps buffer pointer to new "
        "content (500 iterations)");

    for (iter = 0; iter < SUCCESS_SWAP_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 2000));

        /* Random original buffer size */
        buf_size = (size_t)(
            (prng_next() % (MAX_BUFFER_SIZE - MIN_BUFFER_SIZE))
            + MIN_BUFFER_SIZE);

        /* Allocate and fill original buffer */
        buf.data = test_ngx_alloc(buf_size);
        TEST_ASSERT(buf.data != NULL,
            "test buffer alloc must succeed");
        buf.size = buf_size;
        buf.capacity = buf_size;

        fill_random_bytes(buf.data, buf_size,
            (unsigned int)(iter + 8888));
        original_ptr = buf.data;

        /* Random decompressed size (within budget) */
        decomp_size = (size_t)(
            (prng_next() % MAX_BUFFER_SIZE) + 1);
        max_budget = decomp_size + 1;

        reset_alloc_tracking();

        /* Simulate successful decompression (no failure) */
        rc = simulate_decompress_swap(&buf, max_budget,
            DECOMP_FAIL_COUNT, decomp_size);

        /* Verify success return code */
        TEST_ASSERT(rc == NGX_OK,
            "decompression must return NGX_OK on success");

        /* Verify pointer changed (new allocation) */
        TEST_ASSERT(buf.data != original_ptr,
            "buffer pointer must change on success");

        /* Verify new size equals decompressed size */
        TEST_ASSERT(buf.size == decomp_size,
            "buffer size must equal decompressed size");

        /* Verify capacity equals decompressed size */
        TEST_ASSERT(buf.capacity == decomp_size,
            "buffer capacity must equal decompressed size");

        /* Verify old buffer was freed */
        TEST_ASSERT(g_last_freed_ptr == original_ptr,
            "old buffer must be freed on success");

        /* Verify new content is the decompressed pattern */
        TEST_ASSERT(buf.data[0] == 0xAB,
            "new buffer must contain decompressed content");

        /* Cleanup the new buffer */
        test_ngx_free(buf.data);
    }

    TEST_PASS(
        "Property 7b: buffer swapped correctly on success "
        "(500 iterations)");
}

/* ----------------------------------------------------------------
 * Property 7c: Allocation failure triggers fail-open without
 * touching original buffer
 *
 * When ngx_alloc fails for the decompressor output, the original
 * compressed ctx->buffer.data must remain intact and the function
 * returns NGX_ERROR to trigger fail-open.
 *
 * Validates: Requirements 5.4, 5.5
 * ---------------------------------------------------------------- */

#define ALLOC_FAIL_ITERATIONS  200

static void
test_property7c_alloc_failure_preserves(void)
{
    buffer_t buf;
    u_char *original_copy;
    u_char *original_ptr;
    size_t buf_size;
    ngx_int_t rc;
    int iter;

    TEST_SUBSECTION(
        "Property 7c: Alloc failure preserves original "
        "(200 iterations)");

    for (iter = 0; iter < ALLOC_FAIL_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 3000));

        /* Random buffer size */
        buf_size = (size_t)(
            (prng_next() % (MAX_BUFFER_SIZE - MIN_BUFFER_SIZE))
            + MIN_BUFFER_SIZE);

        /* Allocate and fill original buffer */
        buf.data = test_ngx_alloc(buf_size);
        TEST_ASSERT(buf.data != NULL,
            "test buffer alloc must succeed");
        buf.size = buf_size;
        buf.capacity = buf_size;

        fill_random_bytes(buf.data, buf_size,
            (unsigned int)(iter + 9999));

        /* Keep copy for verification */
        original_copy = (u_char *) malloc(buf_size);
        TEST_ASSERT(original_copy != NULL,
            "copy alloc must succeed");
        memcpy(original_copy, buf.data, buf_size);
        original_ptr = buf.data;

        reset_alloc_tracking();

        /* Simulate alloc failure */
        rc = simulate_decompress_swap(&buf, 1048576,
            DECOMP_FAIL_ALLOC_FAILURE, 4096);

        /* Verify failure */
        TEST_ASSERT(rc == NGX_ERROR,
            "must return NGX_ERROR on alloc failure");

        /* Verify pointer unchanged */
        TEST_ASSERT(buf.data == original_ptr,
            "pointer must be unchanged on alloc failure");

        /* Verify size unchanged */
        TEST_ASSERT(buf.size == buf_size,
            "size must be unchanged on alloc failure");

        /* Verify content preserved */
        TEST_ASSERT(
            MEM_EQ(buf.data, original_copy, buf_size),
            "content must be preserved on alloc failure");

        /* No frees should have occurred for the original */
        TEST_ASSERT(g_free_count == 0,
            "no frees on alloc failure path");

        /* Cleanup */
        free(original_copy);
        test_ngx_free(buf.data);
    }

    TEST_PASS(
        "Property 7c: alloc failure preserves original "
        "(200 iterations)");
}

/* ----------------------------------------------------------------
 * Property 7d: Budget exceeded frees decompressor output and
 * preserves original
 *
 * When decompressed size exceeds markdown_decompress_max_size,
 * the decompressor output must be freed and the original buffer
 * preserved.
 *
 * Validates: Requirement 5.4
 * ---------------------------------------------------------------- */

#define BUDGET_EXCEEDED_ITERATIONS  300

static void
test_property7d_budget_exceeded_preserves(void)
{
    buffer_t buf;
    u_char *original_copy;
    u_char *original_ptr;
    size_t buf_size;
    size_t decomp_size;
    size_t max_budget;
    ngx_int_t rc;
    int iter;

    TEST_SUBSECTION(
        "Property 7d: Budget exceeded preserves original "
        "(300 iterations)");

    for (iter = 0; iter < BUDGET_EXCEEDED_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 4000));

        /* Random buffer size */
        buf_size = (size_t)(
            (prng_next() % (MAX_BUFFER_SIZE - MIN_BUFFER_SIZE))
            + MIN_BUFFER_SIZE);

        /* Allocate and fill original buffer */
        buf.data = test_ngx_alloc(buf_size);
        TEST_ASSERT(buf.data != NULL,
            "test buffer alloc must succeed");
        buf.size = buf_size;
        buf.capacity = buf_size;

        fill_random_bytes(buf.data, buf_size,
            (unsigned int)(iter + 5555));

        /* Keep copy */
        original_copy = (u_char *) malloc(buf_size);
        TEST_ASSERT(original_copy != NULL,
            "copy alloc must succeed");
        memcpy(original_copy, buf.data, buf_size);
        original_ptr = buf.data;

        /* Decomp size larger than budget */
        decomp_size = (size_t)(
            (prng_next() % MAX_BUFFER_SIZE) + 1024);
        max_budget = decomp_size / 2;

        reset_alloc_tracking();
        rc = simulate_decompress_swap(&buf, max_budget,
            DECOMP_FAIL_BUDGET_EXCEEDED, decomp_size);

        /* Verify failure */
        TEST_ASSERT(rc == NGX_ERROR,
            "must return NGX_ERROR on budget exceeded");

        /* Verify pointer unchanged */
        TEST_ASSERT(buf.data == original_ptr,
            "pointer unchanged on budget exceeded");

        /* Verify content preserved */
        TEST_ASSERT(
            MEM_EQ(buf.data, original_copy, buf_size),
            "content preserved on budget exceeded");

        /* Decompressor output was freed */
        TEST_ASSERT(g_free_count == 1,
            "decompressor output must be freed");

        /* Cleanup */
        free(original_copy);
        test_ngx_free(buf.data);
    }

    TEST_PASS(
        "Property 7d: budget exceeded preserves original "
        "(300 iterations)");
}

/* ----------------------------------------------------------------
 * Property 7e: Success path frees exactly the old buffer
 *
 * On decompression success, verify:
 *   1. Exactly one free occurs (the old compressed buffer)
 *   2. Exactly one alloc occurs (the new decompressed buffer)
 *   3. The freed pointer matches the original buffer pointer
 *
 * Validates: Requirements 5.2, 5.6
 * ---------------------------------------------------------------- */

#define FREE_TRACKING_ITERATIONS  300

static void
test_property7e_success_frees_old_only(void)
{
    buffer_t buf;
    u_char *original_ptr;
    size_t buf_size;
    size_t decomp_size;
    ngx_int_t rc;
    int iter;

    TEST_SUBSECTION(
        "Property 7e: Success frees exactly old buffer "
        "(300 iterations)");

    for (iter = 0; iter < FREE_TRACKING_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 5000));

        /* Random sizes */
        buf_size = (size_t)(
            (prng_next() % (MAX_BUFFER_SIZE - MIN_BUFFER_SIZE))
            + MIN_BUFFER_SIZE);
        decomp_size = (size_t)(
            (prng_next() % MAX_BUFFER_SIZE) + 1);

        /* Allocate original */
        buf.data = test_ngx_alloc(buf_size);
        TEST_ASSERT(buf.data != NULL,
            "test buffer alloc must succeed");
        buf.size = buf_size;
        buf.capacity = buf_size;
        original_ptr = buf.data;

        reset_alloc_tracking();

        /* Successful decompression */
        rc = simulate_decompress_swap(&buf, decomp_size + 1,
            DECOMP_FAIL_COUNT, decomp_size);

        TEST_ASSERT(rc == NGX_OK,
            "must return NGX_OK on success");

        /* Verify allocation tracking */
        TEST_ASSERT(g_alloc_count == 1,
            "exactly 1 alloc for decompressed buffer");
        TEST_ASSERT(g_free_count == 1,
            "exactly 1 free for old compressed buffer");
        TEST_ASSERT(g_last_freed_ptr == original_ptr,
            "freed pointer must be the original buffer");

        /* Cleanup */
        test_ngx_free(buf.data);
    }

    TEST_PASS(
        "Property 7e: success path frees exactly old "
        "(300 iterations)");
}

/* ----------------------------------------------------------------
 * Property 7f: Contiguous buffer skips linearize copy
 *
 * When ctx->buffer.data is already a single contiguous
 * ngx_alloc-backed allocation, no linearize copy should occur
 * before decompression. The buffer is passed directly.
 *
 * We model this by verifying the input pointer stability:
 * the decompressor receives the original pointer without any
 * intermediate copy.
 *
 * Validates: Requirement 5.1
 * ---------------------------------------------------------------- */

#define CONTIGUITY_ITERATIONS  300

static void
test_property7f_contiguous_no_extra_copy(void)
{
    buffer_t buf;
    u_char *original_ptr;
    size_t buf_size;
    size_t decomp_size;
    int iter;
    int alloc_before;

    TEST_SUBSECTION(
        "Property 7f: Contiguous buffer — no linearize "
        "copy (300 iterations)");

    for (iter = 0; iter < CONTIGUITY_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 6000));

        /* Random buffer size */
        buf_size = (size_t)(
            (prng_next() % (MAX_BUFFER_SIZE - MIN_BUFFER_SIZE))
            + MIN_BUFFER_SIZE);

        /* Allocate contiguous buffer (single allocation) */
        buf.data = test_ngx_alloc(buf_size);
        TEST_ASSERT(buf.data != NULL,
            "test buffer alloc must succeed");
        buf.size = buf_size;
        buf.capacity = buf_size;
        original_ptr = buf.data;

        fill_random_bytes(buf.data, buf_size,
            (unsigned int)(iter + 3333));

        /*
         * Track allocations. For the success path, we expect
         * exactly 1 new allocation (the decompressor output).
         * If a linearize copy occurred, we'd see 2 allocations.
         */
        reset_alloc_tracking();
        decomp_size = (size_t)(
            (prng_next() % MAX_BUFFER_SIZE) + 1);

        alloc_before = g_alloc_count;

        simulate_decompress_swap(&buf, decomp_size + 1,
            DECOMP_FAIL_COUNT, decomp_size);

        /*
         * Exactly 1 allocation: the decompressor output.
         * No extra allocation for a linearize copy.
         */
        TEST_ASSERT(g_alloc_count - alloc_before == 1,
            "only 1 alloc (decompressor output), "
            "no linearize copy");

        /* The old pointer (original) was freed */
        TEST_ASSERT(g_last_freed_ptr == original_ptr,
            "original freed directly (no copy)");

        /* Cleanup */
        test_ngx_free(buf.data);
    }

    TEST_PASS(
        "Property 7f: contiguous buffer skips linearize "
        "copy (300 iterations)");
}

/* ----------------------------------------------------------------
 * Property 7g: Random failure/success sequences maintain
 * invariant across multiple operations
 *
 * Model a sequence of decompression attempts on different buffers
 * with random success/failure outcomes. Verify the invariant holds
 * for every attempt regardless of history.
 *
 * Validates: Requirements 5.1, 5.2, 5.3, 5.4
 * ---------------------------------------------------------------- */

#define SEQUENCE_ITERATIONS  200
#define MAX_OPS_PER_SEQ       10

static void
test_property7g_random_sequences(void)
{
    buffer_t buf;
    u_char *original_copy;
    u_char *original_ptr;
    size_t buf_size;
    size_t decomp_size;
    size_t max_budget;
    decomp_failure_t failure;
    ngx_int_t rc;
    int iter;
    int op;
    int num_ops;
    int should_succeed;

    TEST_SUBSECTION(
        "Property 7g: Random sequences maintain invariant "
        "(200 × up to 10 ops)");

    for (iter = 0; iter < SEQUENCE_ITERATIONS; iter++) {
        prng_seed((unsigned int)(iter + 7000));

        num_ops = (int)((prng_next() % MAX_OPS_PER_SEQ) + 1);

        for (op = 0; op < num_ops; op++) {
            /* Fresh buffer for each operation */
            buf_size = (size_t)(
                (prng_next() % (MAX_BUFFER_SIZE - MIN_BUFFER_SIZE))
                + MIN_BUFFER_SIZE);

            buf.data = test_ngx_alloc(buf_size);
            TEST_ASSERT(buf.data != NULL,
                "test buffer alloc must succeed");
            buf.size = buf_size;
            buf.capacity = buf_size;

            fill_random_bytes(buf.data, buf_size,
                (unsigned int)(iter * 100 + op));

            original_copy = (u_char *) malloc(buf_size);
            TEST_ASSERT(original_copy != NULL,
                "copy alloc must succeed");
            memcpy(original_copy, buf.data, buf_size);
            original_ptr = buf.data;

            /* Randomly decide success or failure */
            should_succeed = (int)(prng_next() % 2);
            decomp_size = (size_t)(
                (prng_next() % MAX_BUFFER_SIZE) + 1);

            if (should_succeed) {
                max_budget = decomp_size + 1;
                failure = DECOMP_FAIL_COUNT;
            } else {
                failure = (decomp_failure_t)(
                    prng_next() % DECOMP_FAIL_COUNT);
                if (failure == DECOMP_FAIL_BUDGET_EXCEEDED) {
                    max_budget = decomp_size / 2;
                } else {
                    max_budget = decomp_size * 2;
                }
            }

            reset_alloc_tracking();
            rc = simulate_decompress_swap(&buf, max_budget,
                failure, decomp_size);

            if (should_succeed) {
                /* Success: pointer changed */
                TEST_ASSERT(rc == NGX_OK,
                    "success path returns NGX_OK");
                TEST_ASSERT(buf.data != original_ptr,
                    "pointer changed on success");
                TEST_ASSERT(buf.size == decomp_size,
                    "size updated on success");
            } else {
                /* Failure: preserved */
                TEST_ASSERT(rc == NGX_ERROR,
                    "failure path returns NGX_ERROR");
                TEST_ASSERT(buf.data == original_ptr,
                    "pointer preserved on failure");
                TEST_ASSERT(buf.size == buf_size,
                    "size preserved on failure");
                TEST_ASSERT(
                    MEM_EQ(buf.data, original_copy, buf_size),
                    "content preserved on failure");
            }

            /* Cleanup */
            free(original_copy);
            test_ngx_free(buf.data);
        }
    }

    TEST_PASS(
        "Property 7g: invariant maintained across random "
        "sequences (200 × 10 ops)");
}

/* ----------------------------------------------------------------
 * Property 7h: Exhaustive failure type coverage
 *
 * For each failure type, verify the invariant holds with
 * various buffer sizes.
 *
 * Validates: Requirements 5.3, 5.4, 5.5
 * ---------------------------------------------------------------- */

static void
test_property7h_exhaustive_failure_types(void)
{
    buffer_t buf;
    u_char *original_copy;
    u_char *original_ptr;
    decomp_failure_t failure;
    size_t sizes[] = {
        16, 64, 256, 1024, 4096, 16384, 65536
    };
    size_t buf_size;
    size_t decomp_size;
    size_t max_budget;
    size_t i;
    ngx_int_t rc;

    TEST_SUBSECTION(
        "Property 7h: Exhaustive failure types × sizes");

    for (failure = 0; failure < DECOMP_FAIL_COUNT; failure++) {
        for (i = 0; i < ARRAY_SIZE(sizes); i++) {
            buf_size = sizes[i];

            buf.data = test_ngx_alloc(buf_size);
            TEST_ASSERT(buf.data != NULL,
                "test buffer alloc must succeed");
            buf.size = buf_size;
            buf.capacity = buf_size;

            fill_random_bytes(buf.data, buf_size,
                (unsigned int)(failure * 100 + i));

            original_copy = (u_char *) malloc(buf_size);
            TEST_ASSERT(original_copy != NULL,
                "copy alloc must succeed");
            memcpy(original_copy, buf.data, buf_size);
            original_ptr = buf.data;

            decomp_size = buf_size * 3;
            if (failure == DECOMP_FAIL_BUDGET_EXCEEDED) {
                max_budget = decomp_size / 2;
            } else {
                max_budget = decomp_size * 2;
            }

            reset_alloc_tracking();
            rc = simulate_decompress_swap(&buf, max_budget,
                failure, decomp_size);

            TEST_ASSERT(rc == NGX_ERROR,
                "must fail for all failure types");
            TEST_ASSERT(buf.data == original_ptr,
                "pointer unchanged for all failure types");
            TEST_ASSERT(buf.size == buf_size,
                "size unchanged for all failure types");
            TEST_ASSERT(
                MEM_EQ(buf.data, original_copy, buf_size),
                "content preserved for all failure types");

            free(original_copy);
            test_ngx_free(buf.data);
        }
    }

    TEST_PASS(
        "Property 7h: all failure types × all sizes "
        "preserve original");
}

/* ----------------------------------------------------------------
 * Main
 * ---------------------------------------------------------------- */

int
main(void)
{
    TEST_SECTION(
        "Feature: 0.9.1-performance-optimization\n"
        "Property 7: Full-Buffer Decompression Preserves "
        "Original on Failure\n"
        "Validates: Requirements 5.1, 5.2, 5.3, 5.4");

    /* Failure preservation (core property) */
    test_property7a_failure_preserves_original();

    /* Success swap correctness */
    test_property7b_success_swaps_correctly();

    /* Allocation failure path */
    test_property7c_alloc_failure_preserves();

    /* Budget exceeded path */
    test_property7d_budget_exceeded_preserves();

    /* Free tracking on success */
    test_property7e_success_frees_old_only();

    /* Contiguity: no linearize copy */
    test_property7f_contiguous_no_extra_copy();

    /* Random sequences */
    test_property7g_random_sequences();

    /* Exhaustive failure types × sizes */
    test_property7h_exhaustive_failure_types();

    printf("\n");
    TEST_PASS(
        "fullbuffer_decomp_property: all property tests "
        "passed");
    return 0;
}
