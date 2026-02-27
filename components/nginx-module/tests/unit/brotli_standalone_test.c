/*
 * Test: brotli_standalone
 * Description: standalone brotli decompression
 */

#include "test_common.h"

#define NGX_OK 0
#define NGX_ERROR -1
#define NGX_DECLINED -5

#ifdef NGX_HTTP_BROTLI
#include <brotli/decode.h>
#endif

static int
decompress_brotli_standalone(const unsigned char *in, size_t in_len,
                             unsigned char *out, size_t *out_len)
{
    if (in == NULL || in_len == 0 || out == NULL || out_len == NULL || *out_len == 0) {
        return NGX_ERROR;
    }

#ifdef NGX_HTTP_BROTLI
    {
        BrotliDecoderResult rc = BrotliDecoderDecompress(in_len, in, out_len, out);
        return (rc == BROTLI_DECODER_RESULT_SUCCESS) ? NGX_OK : NGX_ERROR;
    }
#else
    UNUSED(in);
    UNUSED(in_len);
    UNUSED(out);
    UNUSED(out_len);
    return NGX_DECLINED;
#endif
}

static void
test_fallback_or_decode(void)
{
    unsigned char out[64];
    size_t out_len;
    int rc;
    const unsigned char invalid_data[] = {0x10, 0x20, 0x30, 0x40};

    TEST_SUBSECTION("Brotli availability behavior");
    out_len = sizeof(out);
    rc = decompress_brotli_standalone(invalid_data, sizeof(invalid_data), out, &out_len);

#ifdef NGX_HTTP_BROTLI
    TEST_ASSERT(rc == NGX_ERROR, "With brotli enabled, invalid stream should return NGX_ERROR");
#else
    TEST_ASSERT(rc == NGX_DECLINED, "Without brotli enabled, should return NGX_DECLINED");
#endif

    TEST_PASS("Brotli availability behavior is correct");
}

static void
test_invalid_arguments(void)
{
    unsigned char out[8];
    size_t out_len;

    TEST_SUBSECTION("Invalid argument handling");
    out_len = sizeof(out);
    TEST_ASSERT(decompress_brotli_standalone(NULL, 1, out, &out_len) == NGX_ERROR, "NULL input should fail");
    out_len = sizeof(out);
    TEST_ASSERT(decompress_brotli_standalone((const unsigned char *) "x", 0, out, &out_len) == NGX_ERROR, "Zero-length input should fail");
    TEST_PASS("Invalid argument checks are correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("brotli_standalone Tests\n");
    printf("========================================\n");

    test_fallback_or_decode();
    test_invalid_arguments();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
