/*
 * Test: unsupported_format_fallback
 * Description: unsupported format fallback
 *
 * Validates that unsupported compression formats (e.g. compress,
 * unknown encodings) fall back to passthrough behavior: the response
 * passes through the filter unchanged without decompression or conversion.
 */

#include "test_common.h"

/*
 * Compression type enumeration.
 */
typedef enum {
    COMP_NONE = 0,
    COMP_GZIP = 1,
    COMP_UNKNOWN = 4
} compression_t;

/*
 * Compression detection context tracking state and counters.
 */
typedef struct {
    compression_t compression_type;
    int decompression_needed;
    int eligible;
    unsigned warnings;
    unsigned failures;
} ctx_t;

/*
 * Configuration for auto-decompress behavior.
 */
typedef struct {
    int auto_decompress;
} conf_t;

/*
 * Handle compression detection from Content-Encoding header.
 *
 * For COMP_UNKNOWN: marks ineligible (fail-open to passthrough),
 * increments warning counter, does not set decompression_needed.
 * For supported formats: sets decompression_needed.
 * For COMP_NONE or auto_decompress=off: no action.
 *
 * Parameters:
 *   ctx     - compression detection context
 *   conf    - configuration with auto_decompress setting
 *   detected - detected compression type
 */
static void
handle_compression_detection(ctx_t *ctx, const conf_t *conf, compression_t detected)
{
    ctx->compression_type = detected;
    ctx->decompression_needed = 0;
    ctx->eligible = 1;

    if (!conf->auto_decompress) {
        return;
    }

    if (detected == COMP_UNKNOWN) {
        ctx->warnings++;
        ctx->eligible = 0; /* fail-open to passthrough */
        return;
    }

    if (detected != COMP_NONE) {
        ctx->decompression_needed = 1;
    }
}

/*
 * Verify COMP_UNKNOWN triggers fail-open: marks ineligible, does not
 * set decompression_needed, increments warning counter.
 *
 * Expected: eligible=0, decompression_needed=0, warnings=1, failures=0.
 */
static void
test_unknown_format_triggers_fail_open(void)
{
    ctx_t ctx;
    conf_t conf;

    TEST_SUBSECTION("UNKNOWN format triggers fail-open");

    memset(&ctx, 0, sizeof(ctx));
    conf.auto_decompress = 1;
    handle_compression_detection(&ctx, &conf, COMP_UNKNOWN);

    TEST_ASSERT(ctx.eligible == 0, "UNKNOWN format should mark request ineligible for conversion");
    TEST_ASSERT(ctx.decompression_needed == 0, "UNKNOWN format should not set decompression_needed");
    TEST_ASSERT(ctx.warnings == 1, "UNKNOWN format should emit warning log");
    TEST_ASSERT(ctx.failures == 0, "UNKNOWN format should not increment failure counter");
    TEST_PASS("UNKNOWN format fallback behavior is correct");
}

/*
 * Verify supported format (gzip) sets decompression_needed and
 * remains eligible.
 *
 * Expected: eligible=1, decompression_needed=1, warnings=0.
 */
static void
test_supported_format_sets_decompression(void)
{
    ctx_t ctx;
    conf_t conf;

    TEST_SUBSECTION("Supported format still sets decompression_needed");

    memset(&ctx, 0, sizeof(ctx));
    conf.auto_decompress = 1;
    handle_compression_detection(&ctx, &conf, COMP_GZIP);

    TEST_ASSERT(ctx.eligible == 1, "Supported format should remain eligible");
    TEST_ASSERT(ctx.decompression_needed == 1, "Supported format should set decompression_needed");
    TEST_ASSERT(ctx.warnings == 0, "Supported format should not emit warning");
    TEST_PASS("Supported format handling is correct");
}

/*
 * Verify auto_decompress=off bypasses unsupported format handling:
 * even COMP_UNKNOWN does not trigger fail-open when auto_decompress is off.
 *
 * Expected: eligible=1, decompression_needed=0, warnings=0.
 */
static void
test_auto_decompress_off(void)
{
    ctx_t ctx;
    conf_t conf;

    TEST_SUBSECTION("auto_decompress=off bypasses unsupported handling");

    memset(&ctx, 0, sizeof(ctx));
    conf.auto_decompress = 0;
    handle_compression_detection(&ctx, &conf, COMP_UNKNOWN);

    TEST_ASSERT(ctx.eligible == 1, "auto_decompress off should not force fail-open");
    TEST_ASSERT(ctx.decompression_needed == 0, "auto_decompress off should not set decompression_needed");
    TEST_ASSERT(ctx.warnings == 0, "auto_decompress off should not emit unsupported warning");
    TEST_PASS("auto_decompress off behavior is correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("unsupported_format_fallback Tests\n");
    printf("========================================\n");

    test_unknown_format_triggers_fail_open();
    test_supported_format_sets_decompression();
    test_auto_decompress_off();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
