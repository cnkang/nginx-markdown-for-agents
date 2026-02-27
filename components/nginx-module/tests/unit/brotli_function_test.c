/*
 * Test: brotli_function
 * Description: brotli decompression function
 */

#include "test_common.h"

#define NGX_OK 0
#define NGX_ERROR -1
#define NGX_DECLINED -5

#ifdef NGX_HTTP_BROTLI
#include <brotli/decode.h>
#endif

static int
decompress_brotli(const unsigned char *in, size_t in_len,
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
test_function_contract(void)
{
    unsigned char out[64];
    size_t out_len;
    int rc;
    const unsigned char invalid_brotli[] = {0x00, 0x01, 0x02, 0x03};

    TEST_SUBSECTION("Function contract and fallback behavior");

    out_len = sizeof(out);
    rc = decompress_brotli(invalid_brotli, sizeof(invalid_brotli), out, &out_len);

#ifdef NGX_HTTP_BROTLI
    TEST_ASSERT(rc == NGX_ERROR, "Invalid brotli stream should return NGX_ERROR when brotli is available");
#else
    TEST_ASSERT(rc == NGX_DECLINED, "Without brotli support function should return NGX_DECLINED");
#endif

    TEST_PASS("Function contract is correct");
}

static void
test_input_validation(void)
{
    unsigned char out[16];
    size_t out_len;

    TEST_SUBSECTION("Input validation");

    out_len = sizeof(out);
    TEST_ASSERT(decompress_brotli(NULL, 4, out, &out_len) == NGX_ERROR, "NULL input should fail");
    out_len = sizeof(out);
    TEST_ASSERT(decompress_brotli((const unsigned char *) "x", 0, out, &out_len) == NGX_ERROR, "Empty input should fail");
    out_len = sizeof(out);
    TEST_ASSERT(decompress_brotli((const unsigned char *) "x", 1, NULL, &out_len) == NGX_ERROR, "NULL output should fail");
    out_len = 0;
    TEST_ASSERT(decompress_brotli((const unsigned char *) "x", 1, out, &out_len) == NGX_ERROR, "Zero output capacity should fail");

    TEST_PASS("Input validation works");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("brotli_function Tests\n");
    printf("========================================\n");

    test_function_contract();
    test_input_validation();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
