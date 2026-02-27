/*
 * Test: decompression_detect
 * Description: compression type detection
 */

#include "test_common.h"
#include <ctype.h>

typedef enum {
    COMPRESSION_NONE = 0,
    COMPRESSION_GZIP,
    COMPRESSION_DEFLATE,
    COMPRESSION_BROTLI,
    COMPRESSION_UNKNOWN
} compression_type_t;

static int
str_case_eq(const char *a, const char *b)
{
    size_t i;
    size_t alen;
    size_t blen;

    if (a == NULL || b == NULL) {
        return 0;
    }

    alen = strlen(a);
    blen = strlen(b);
    if (alen != blen) {
        return 0;
    }

    for (i = 0; i < alen; i++) {
        if (tolower((unsigned char) a[i]) != tolower((unsigned char) b[i])) {
            return 0;
        }
    }

    return 1;
}

static compression_type_t
detect_compression(const char *content_encoding)
{
    if (content_encoding == NULL || content_encoding[0] == '\0') {
        return COMPRESSION_NONE;
    }

    if (str_case_eq(content_encoding, "gzip")) {
        return COMPRESSION_GZIP;
    }
    if (str_case_eq(content_encoding, "deflate")) {
        return COMPRESSION_DEFLATE;
    }
    if (str_case_eq(content_encoding, "br")) {
        return COMPRESSION_BROTLI;
    }
    return COMPRESSION_UNKNOWN;
}

static void
test_known_formats(void)
{
    TEST_SUBSECTION("Known formats and case-insensitive matching");

    TEST_ASSERT(detect_compression("gzip") == COMPRESSION_GZIP, "gzip should map to GZIP");
    TEST_ASSERT(detect_compression("GZIP") == COMPRESSION_GZIP, "GZIP should map to GZIP");
    TEST_ASSERT(detect_compression("GzIp") == COMPRESSION_GZIP, "GzIp should map to GZIP");

    TEST_ASSERT(detect_compression("deflate") == COMPRESSION_DEFLATE, "deflate should map to DEFLATE");
    TEST_ASSERT(detect_compression("DEFLATE") == COMPRESSION_DEFLATE, "DEFLATE should map to DEFLATE");

    TEST_ASSERT(detect_compression("br") == COMPRESSION_BROTLI, "br should map to BROTLI");
    TEST_ASSERT(detect_compression("BR") == COMPRESSION_BROTLI, "BR should map to BROTLI");

    TEST_PASS("Known formats detection works");
}

static void
test_none_and_unknown_formats(void)
{
    TEST_SUBSECTION("NONE and UNKNOWN detection");

    TEST_ASSERT(detect_compression(NULL) == COMPRESSION_NONE, "NULL should map to NONE");
    TEST_ASSERT(detect_compression("") == COMPRESSION_NONE, "Empty string should map to NONE");

    TEST_ASSERT(detect_compression("compress") == COMPRESSION_UNKNOWN, "compress should map to UNKNOWN");
    TEST_ASSERT(detect_compression("identity") == COMPRESSION_UNKNOWN, "identity should map to UNKNOWN");
    TEST_ASSERT(detect_compression("gzip,br") == COMPRESSION_UNKNOWN, "combined value should map to UNKNOWN");
    TEST_ASSERT(detect_compression(" gzip ") == COMPRESSION_UNKNOWN, "whitespace-padded value should map to UNKNOWN");

    TEST_PASS("NONE/UNKNOWN detection works");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("decompression_detect Tests\n");
    printf("========================================\n");

    test_known_formats();
    test_none_and_unknown_formats();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
