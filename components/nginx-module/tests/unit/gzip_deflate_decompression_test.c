/*
 * Test: gzip_deflate_decompression
 * Description: gzip and deflate decompression
 */

#include "test_common.h"
#include <zlib.h>

#define NGX_OK 0
#define NGX_ERROR -1

typedef enum {
    TYPE_GZIP = 1,
    TYPE_DEFLATE = 2
} compression_kind_t;

static int
compress_payload(const unsigned char *in, size_t in_len, compression_kind_t type,
                 unsigned char **out, size_t *out_len)
{
    z_stream s;
    int rc;
    int window_bits;
    size_t cap;

    if (in == NULL || in_len == 0 || out == NULL || out_len == NULL) {
        return NGX_ERROR;
    }

    memset(&s, 0, sizeof(s));
    window_bits = (type == TYPE_GZIP) ? (MAX_WBITS + 16) : MAX_WBITS;
    rc = deflateInit2(&s, Z_DEFAULT_COMPRESSION, Z_DEFLATED, window_bits, 8, Z_DEFAULT_STRATEGY);
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

static int
decompress_payload(const unsigned char *in, size_t in_len, compression_kind_t type,
                   unsigned char **out, size_t *out_len, size_t max_size)
{
    z_stream s;
    int rc;
    int window_bits;

    if (in == NULL || in_len == 0 || out == NULL || out_len == NULL || max_size == 0) {
        return NGX_ERROR;
    }

    memset(&s, 0, sizeof(s));
    window_bits = (type == TYPE_GZIP) ? (MAX_WBITS + 16) : MAX_WBITS;
    rc = inflateInit2(&s, window_bits);
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
    if (rc != Z_STREAM_END || s.total_out > max_size) {
        free(*out);
        *out = NULL;
        inflateEnd(&s);
        return NGX_ERROR;
    }

    *out_len = s.total_out;
    inflateEnd(&s);
    return NGX_OK;
}

static void
test_valid_roundtrip(compression_kind_t type, const char *label)
{
    const char *text;
    unsigned char *compressed;
    size_t compressed_len;
    unsigned char *decompressed;
    size_t decompressed_len;
    int rc;

    TEST_SUBSECTION(label);

    text = "Hello from nginx markdown module. This payload should round-trip correctly.";
    compressed = NULL;
    decompressed = NULL;

    rc = compress_payload((const unsigned char *) text, strlen(text), type, &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "Compression should succeed");

    rc = decompress_payload(compressed, compressed_len, type, &decompressed, &decompressed_len, 4096);
    TEST_ASSERT(rc == NGX_OK, "Decompression should succeed");
    TEST_ASSERT(decompressed_len == strlen(text), "Decompressed length should match source");
    TEST_ASSERT(MEM_EQ(decompressed, text, decompressed_len), "Decompressed payload should match source");

    free(compressed);
    free(decompressed);
    TEST_PASS("Round-trip succeeded");
}

static void
test_corrupted_data(void)
{
    const char *text;
    unsigned char *compressed;
    size_t compressed_len;
    unsigned char *decompressed;
    size_t decompressed_len;
    int rc;

    TEST_SUBSECTION("Corrupted gzip data returns error");

    text = "Corruption test payload";
    compressed = NULL;
    decompressed = NULL;

    rc = compress_payload((const unsigned char *) text, strlen(text), TYPE_GZIP, &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "Compression should succeed");
    TEST_ASSERT(compressed_len > 8, "Compressed payload should be long enough to mutate");

    compressed[compressed_len / 2] ^= 0xFF;
    rc = decompress_payload(compressed, compressed_len, TYPE_GZIP, &decompressed, &decompressed_len, 4096);
    TEST_ASSERT(rc == NGX_ERROR, "Corrupted payload should fail decompression");

    free(compressed);
    TEST_PASS("Corrupted data detected");
}

static void
test_size_limit_enforcement(void)
{
    char source[4096];
    unsigned char *compressed;
    size_t compressed_len;
    unsigned char *decompressed;
    size_t decompressed_len;
    int rc;

    TEST_SUBSECTION("Size limit enforcement");

    memset(source, 'A', sizeof(source));
    compressed = NULL;
    decompressed = NULL;

    rc = compress_payload((const unsigned char *) source, sizeof(source), TYPE_GZIP, &compressed, &compressed_len);
    TEST_ASSERT(rc == NGX_OK, "Compression should succeed");

    rc = decompress_payload(compressed, compressed_len, TYPE_GZIP, &decompressed, &decompressed_len, 128);
    TEST_ASSERT(rc == NGX_ERROR, "Decompression should fail when output exceeds size limit");

    free(compressed);
    TEST_PASS("Size limit enforcement works");
}

static void
test_empty_input_validation(void)
{
    unsigned char *out;
    size_t out_len;
    int rc;

    TEST_SUBSECTION("Empty input validation");

    out = NULL;
    out_len = 0;
    rc = decompress_payload((const unsigned char *) "", 0, TYPE_GZIP, &out, &out_len, 256);
    TEST_ASSERT(rc == NGX_ERROR, "Empty input must return error");
    TEST_PASS("Empty input validation works");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("gzip_deflate_decompression Tests\n");
    printf("========================================\n");

    test_valid_roundtrip(TYPE_GZIP, "Valid gzip round-trip");
    test_valid_roundtrip(TYPE_DEFLATE, "Valid deflate round-trip");
    test_corrupted_data();
    test_size_limit_enforcement();
    test_empty_input_validation();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
