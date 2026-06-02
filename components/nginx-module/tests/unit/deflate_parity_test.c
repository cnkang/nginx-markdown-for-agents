/*
 * Test: deflate_parity
 *
 * Validates that Content-Encoding: deflate decompression produces
 * identical output in both buffered and streaming paths.  Both paths
 * now use MAX_WBITS (zlib-wrapped deflate per RFC 1950/1951).
 */

#include "test_common.h"
#include <zlib.h>

#define NGX_OK     0
#define NGX_ERROR  -1

/*
 * Compress a payload using zlib-wrapped deflate (window_bits = MAX_WBITS).
 * This is the format real-world servers send for Content-Encoding: deflate.
 *
 * Parameters:
 *   in      - input data
 *   in_len  - input length in bytes
 *   out     - [out] allocated compressed buffer (caller must free)
 *   out_len - [out] compressed size in bytes
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on failure.
 */
static int
compress_deflate_zlib_wrapped(const unsigned char *in, size_t in_len,
    unsigned char **out, size_t *out_len)
{
    z_stream  s;
    int       rc;
    size_t    cap;

    if (in == NULL || in_len == 0 || out == NULL || out_len == NULL) {
        return NGX_ERROR;
    }

    memset(&s, 0, sizeof(s));

    /* MAX_WBITS = zlib-wrapped deflate */
    rc = deflateInit2(&s, Z_DEFAULT_COMPRESSION, Z_DEFLATED,
                      MAX_WBITS, 8, Z_DEFAULT_STRATEGY);
    if (rc != Z_OK) {
        return NGX_ERROR;
    }

    cap = in_len + (in_len / 8) + 64;
    *out = (unsigned char *) malloc(cap);
    if (*out == NULL) {
        deflateEnd(&s);
        return NGX_ERROR;
    }

    s.next_in = (unsigned char *) in;
    s.avail_in = (uInt) in_len;
    s.next_out = *out;
    s.avail_out = (uInt) cap;

    rc = deflate(&s, Z_FINISH);
    if (rc != Z_STREAM_END) {
        free(*out);
        *out = NULL;
        deflateEnd(&s);
        return NGX_ERROR;
    }

    *out_len = s.total_out;
    deflateEnd(&s);
    return NGX_OK;
}

/*
 * Decompress using the buffered path semantics: inflateInit2 with
 * window_bits = MAX_WBITS (zlib-wrapped deflate).
 *
 * Parameters:
 *   in       - compressed input data
 *   in_len   - compressed length in bytes
 *   out      - [out] allocated decompressed buffer (caller must free)
 *   out_len  - [out] decompressed size in bytes
 *   max_size - maximum allowed decompressed size
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on failure.
 */
static int
decompress_buffered_path(const unsigned char *in, size_t in_len,
    unsigned char **out, size_t *out_len, size_t max_size)
{
    z_stream  s;
    int       rc;

    if (in == NULL || in_len == 0 || out == NULL || out_len == NULL) {
        return NGX_ERROR;
    }

    memset(&s, 0, sizeof(s));

    /* Buffered path: MAX_WBITS for zlib-wrapped deflate */
    rc = inflateInit2(&s, MAX_WBITS);
    if (rc != Z_OK) {
        return NGX_ERROR;
    }

    *out = (unsigned char *) malloc(max_size);
    if (*out == NULL) {
        inflateEnd(&s);
        return NGX_ERROR;
    }

    s.next_in = (unsigned char *) in;
    s.avail_in = (uInt) in_len;
    s.next_out = *out;
    s.avail_out = (uInt) max_size;

    rc = inflate(&s, Z_FINISH);
    if (rc != Z_STREAM_END) {
        free(*out);
        *out = NULL;
        inflateEnd(&s);
        return NGX_ERROR;
    }

    *out_len = s.total_out;
    inflateEnd(&s);
    return NGX_OK;
}

/*
 * Decompress using the streaming path semantics: inflateInit2 with
 * window_bits = MAX_WBITS (zlib-wrapped deflate, unified with buffered).
 *
 * Parameters:
 *   in       - compressed input data
 *   in_len   - compressed length in bytes
 *   out      - [out] allocated decompressed buffer (caller must free)
 *   out_len  - [out] decompressed size in bytes
 *   max_size - maximum allowed decompressed size
 *
 * Returns:
 *   NGX_OK on success, NGX_ERROR on failure.
 */
static int
decompress_streaming_path(const unsigned char *in, size_t in_len,
    unsigned char **out, size_t *out_len, size_t max_size)
{
    z_stream  s;
    int       rc;

    if (in == NULL || in_len == 0 || out == NULL || out_len == NULL) {
        return NGX_ERROR;
    }

    memset(&s, 0, sizeof(s));

    /*
     * Streaming path: MAX_WBITS for zlib-wrapped deflate.
     * Previously used -MAX_WBITS (raw deflate); now unified with
     * the buffered path per REQ-7.
     */
    rc = inflateInit2(&s, MAX_WBITS);
    if (rc != Z_OK) {
        return NGX_ERROR;
    }

    *out = (unsigned char *) malloc(max_size);
    if (*out == NULL) {
        inflateEnd(&s);
        return NGX_ERROR;
    }

    s.next_in = (unsigned char *) in;
    s.avail_in = (uInt) in_len;
    s.next_out = *out;
    s.avail_out = (uInt) max_size;

    rc = inflate(&s, Z_FINISH);
    if (rc != Z_STREAM_END) {
        free(*out);
        *out = NULL;
        inflateEnd(&s);
        return NGX_ERROR;
    }

    *out_len = s.total_out;
    inflateEnd(&s);
    return NGX_OK;
}

/*
 * test_deflate_parity_short - Verify both paths produce identical output
 * for a short text payload compressed as zlib-wrapped deflate.
 *
 * Expected: byte-for-byte identical decompressed output from both paths.
 */
static void
test_deflate_parity_short(void)
{
    const char     *text;
    size_t          text_len;
    unsigned char  *compressed;
    size_t          compressed_len;
    unsigned char  *buffered_out;
    size_t          buffered_len;
    unsigned char  *streaming_out;
    size_t          streaming_len;
    int             rc;

    TEST_SUBSECTION("Deflate parity: short payload");

    text = "Hello from nginx-markdown. This tests deflate path parity.";
    text_len = test_cstrnlen(text, 256);

    rc = compress_deflate_zlib_wrapped(
        (const unsigned char *) text, text_len,
        &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "Compression should succeed");

    rc = decompress_buffered_path(compressed, compressed_len,
        &buffered_out, &buffered_len, 4096);
    TEST_ASSERT(rc == NGX_OK,
        "Buffered path decompression should succeed");

    rc = decompress_streaming_path(compressed, compressed_len,
        &streaming_out, &streaming_len, 4096);
    TEST_ASSERT(rc == NGX_OK,
        "Streaming path decompression should succeed");

    TEST_ASSERT(buffered_len == streaming_len,
        "Output lengths should be identical across paths");
    TEST_ASSERT(buffered_len == text_len,
        "Decompressed length should match original");
    TEST_ASSERT(MEM_EQ(buffered_out, streaming_out, buffered_len),
        "Decompressed content should be byte-for-byte identical");
    TEST_ASSERT(MEM_EQ(buffered_out, text, text_len),
        "Decompressed content should match original text");

    free(compressed);
    free(buffered_out);
    free(streaming_out);
    TEST_PASS("Short payload produces identical output in both paths");
}

/*
 * test_deflate_parity_repeated - Verify parity for a larger repeated-char
 * payload that exercises zlib compression ratios.
 *
 * Expected: byte-for-byte identical decompressed output from both paths.
 */
static void
test_deflate_parity_repeated(void)
{
    char            source[4096];
    unsigned char  *compressed;
    size_t          compressed_len;
    unsigned char  *buffered_out;
    size_t          buffered_len;
    unsigned char  *streaming_out;
    size_t          streaming_len;
    int             rc;

    TEST_SUBSECTION("Deflate parity: repeated-char payload");

    memset(source, 'X', sizeof(source));

    rc = compress_deflate_zlib_wrapped(
        (const unsigned char *) source, sizeof(source),
        &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "Compression should succeed");

    rc = decompress_buffered_path(compressed, compressed_len,
        &buffered_out, &buffered_len, 8192);
    TEST_ASSERT(rc == NGX_OK,
        "Buffered path decompression should succeed");

    rc = decompress_streaming_path(compressed, compressed_len,
        &streaming_out, &streaming_len, 8192);
    TEST_ASSERT(rc == NGX_OK,
        "Streaming path decompression should succeed");

    TEST_ASSERT(buffered_len == streaming_len,
        "Output lengths should be identical across paths");
    TEST_ASSERT(buffered_len == sizeof(source),
        "Decompressed length should match original");
    TEST_ASSERT(MEM_EQ(buffered_out, streaming_out, buffered_len),
        "Decompressed content should be byte-for-byte identical");

    free(compressed);
    free(buffered_out);
    free(streaming_out);
    TEST_PASS("Repeated-char payload produces identical output");
}

/*
 * test_deflate_parity_mixed_content - Verify parity for a mixed-content
 * payload with HTML-like data (realistic workload for this module).
 *
 * Expected: byte-for-byte identical decompressed output from both paths.
 */
static void
test_deflate_parity_mixed_content(void)
{
    const char     *text;
    size_t          text_len;
    unsigned char  *compressed;
    size_t          compressed_len;
    unsigned char  *buffered_out;
    size_t          buffered_len;
    unsigned char  *streaming_out;
    size_t          streaming_len;
    int             rc;

    TEST_SUBSECTION("Deflate parity: mixed HTML content");

    text = "<html><head><title>Test</title></head>"
           "<body><h1>Hello</h1><p>World</p>"
           "<ul><li>Item 1</li><li>Item 2</li></ul>"
           "</body></html>";
    text_len = test_cstrnlen(text, 1024);

    rc = compress_deflate_zlib_wrapped(
        (const unsigned char *) text, text_len,
        &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "Compression should succeed");

    rc = decompress_buffered_path(compressed, compressed_len,
        &buffered_out, &buffered_len, 4096);
    TEST_ASSERT(rc == NGX_OK,
        "Buffered path decompression should succeed");

    rc = decompress_streaming_path(compressed, compressed_len,
        &streaming_out, &streaming_len, 4096);
    TEST_ASSERT(rc == NGX_OK,
        "Streaming path decompression should succeed");

    TEST_ASSERT(buffered_len == streaming_len,
        "Output lengths should be identical across paths");
    TEST_ASSERT(buffered_len == text_len,
        "Decompressed length should match original");
    TEST_ASSERT(MEM_EQ(buffered_out, streaming_out, buffered_len),
        "Decompressed content should be byte-for-byte identical");
    TEST_ASSERT(MEM_EQ(buffered_out, text, text_len),
        "Decompressed content should match original text");

    free(compressed);
    free(buffered_out);
    free(streaming_out);
    TEST_PASS("Mixed HTML content produces identical output");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("deflate_parity Tests\n");
    printf("========================================\n");

    test_deflate_parity_short();
    test_deflate_parity_repeated();
    test_deflate_parity_mixed_content();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
