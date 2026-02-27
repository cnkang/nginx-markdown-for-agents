/*
 * Test: input_validation
 * Description: input validation
 */

#include "test_common.h"

#define Z_STREAM_END 1
#define Z_OK 0
#define Z_BUF_ERROR (-5)

static int
validate_decompress_request(const unsigned char *input, size_t input_len, size_t max_size)
{
    if (input == NULL || input_len == 0) {
        return 0;
    }
    if (max_size == 0) {
        return 0;
    }
    if (input_len > (64U * 1024U * 1024U)) {
        return 0;
    }
    return 1;
}

static int
validate_inflate_result(int inflate_rc, size_t output_size, size_t max_size)
{
    if (inflate_rc != Z_STREAM_END) {
        return 0;
    }
    if (output_size > max_size) {
        return 0;
    }
    return 1;
}

static int
safe_expand_size(size_t input_len, size_t max_size, size_t *target)
{
    size_t proposed;
    if (target == NULL || input_len == 0 || max_size == 0) {
        return 0;
    }
    if (input_len > (SIZE_MAX / 10)) {
        return 0;
    }
    proposed = input_len * 10;
    if (proposed > max_size) {
        proposed = max_size;
    }
    *target = proposed;
    return 1;
}

static void
test_request_validation(void)
{
    const unsigned char dummy[] = "abc";
    TEST_SUBSECTION("Request argument validation");

    TEST_ASSERT(validate_decompress_request(dummy, 3, 1024) == 1, "Valid request should pass");
    TEST_ASSERT(validate_decompress_request(NULL, 3, 1024) == 0, "NULL input should fail");
    TEST_ASSERT(validate_decompress_request(dummy, 0, 1024) == 0, "Empty input should fail");
    TEST_ASSERT(validate_decompress_request(dummy, 3, 0) == 0, "Zero max_size should fail");
    TEST_PASS("Request validation passed");
}

static void
test_inflate_return_code_validation(void)
{
    TEST_SUBSECTION("Inflate return-code and size validation");
    TEST_ASSERT(validate_inflate_result(Z_STREAM_END, 100, 1000) == 1, "Z_STREAM_END with valid size should pass");
    TEST_ASSERT(validate_inflate_result(Z_OK, 100, 1000) == 0, "Z_OK should fail for final decompression check");
    TEST_ASSERT(validate_inflate_result(Z_BUF_ERROR, 100, 1000) == 0, "Z_BUF_ERROR should fail");
    TEST_ASSERT(validate_inflate_result(Z_STREAM_END, 2000, 1000) == 0, "Output beyond max size should fail");
    TEST_PASS("Inflate result validation passed");
}

static void
test_overflow_safe_buffer_sizing(void)
{
    size_t target = 0;
    TEST_SUBSECTION("Overflow-safe output buffer sizing");
    TEST_ASSERT(safe_expand_size(1024, 100000, &target) == 1, "Normal sizing should pass");
    TEST_ASSERT(target == 10240, "Expected 10x growth");
    TEST_ASSERT(safe_expand_size(SIZE_MAX, 100000, &target) == 0, "Overflow-prone sizing must fail");
    TEST_ASSERT(safe_expand_size(1024, 0, &target) == 0, "Zero max_size should fail");
    TEST_PASS("Buffer sizing validation passed");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("input_validation Tests\n");
    printf("========================================\n");

    test_request_validation();
    test_inflate_return_code_validation();
    test_overflow_safe_buffer_sizing();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
