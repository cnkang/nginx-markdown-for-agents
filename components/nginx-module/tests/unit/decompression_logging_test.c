/*
 * Test: decompression_logging
 * Description: decompression logging
 */

#include "test_common.h"

#define LOG_DEBUG 7
#define LOG_INFO 6
#define LOG_ERR 4

static char g_log_msg[1024];
static int g_log_level;

static void
capture_log(int level, const char *fmt, ...)
{
    va_list args;
    g_log_level = level;
    va_start(args, fmt);
    vsnprintf(g_log_msg, sizeof(g_log_msg), fmt, args);
    va_end(args);
}

static void
log_detection(int compression_type)
{
    capture_log(LOG_DEBUG, "markdown filter: decompression detected compression type: %d",
                compression_type);
}

static void
log_success(int compression_type, size_t compressed, size_t decompressed)
{
    double ratio = (compressed == 0) ? 0.0 : ((double) decompressed / (double) compressed);
    capture_log(LOG_INFO,
                "markdown filter: decompression succeeded, compression=%d, "
                "compressed=%zu bytes, decompressed=%zu bytes, ratio=%.1fx",
                compression_type, compressed, decompressed, ratio);
}

static void
log_failure(int compression_type, const char *category)
{
    capture_log(LOG_ERR,
                "markdown filter: decompression failed, compression=%d, "
                "error=\"decompression error\", category=%s",
                compression_type, category);
}

static void
assert_contains(const char *needle, const char *message)
{
    TEST_ASSERT(strstr(g_log_msg, needle) != NULL, message);
}

static void
test_detection_log(void)
{
    TEST_SUBSECTION("Header filter compression detection log");
    log_detection(1);
    TEST_ASSERT(g_log_level == LOG_DEBUG, "Detection log level should be DEBUG");
    assert_contains("detected compression type", "Detection log should contain message");
    assert_contains("decompression", "Detection log should contain decompression keyword");
    TEST_PASS("Detection log format is correct");
}

static void
test_success_log(void)
{
    TEST_SUBSECTION("Body filter decompression success log");
    log_success(1, 1000, 8000);
    TEST_ASSERT(g_log_level == LOG_INFO, "Success log level should be INFO");
    assert_contains("decompression succeeded", "Success log should contain success marker");
    assert_contains("compression=1", "Success log should contain compression type");
    assert_contains("compressed=1000", "Success log should contain compressed size");
    assert_contains("decompressed=8000", "Success log should contain decompressed size");
    assert_contains("ratio=8.0x", "Success log should contain ratio");
    assert_contains("decompression", "Success log should contain decompression keyword");
    TEST_PASS("Success log format is correct");
}

static void
test_failure_log(void)
{
    TEST_SUBSECTION("Body filter decompression failure log");
    log_failure(2, "conversion");
    TEST_ASSERT(g_log_level == LOG_ERR, "Failure log level should be ERROR");
    assert_contains("decompression failed", "Failure log should contain failure marker");
    assert_contains("compression=2", "Failure log should contain compression type");
    assert_contains("error=", "Failure log should contain error field");
    assert_contains("category=conversion", "Failure log should contain category");
    assert_contains("decompression", "Failure log should contain decompression keyword");
    TEST_PASS("Failure log format is correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("decompression_logging Tests\n");
    printf("========================================\n");

    test_detection_log();
    test_success_log();
    test_failure_log();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
