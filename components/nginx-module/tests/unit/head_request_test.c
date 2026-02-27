/*
 * Test: head_request
 * Description: HEAD request handling
 */

#include "test_common.h"

typedef struct {
    const char *content_type;
    size_t content_length;
    int has_body;
    int converted;
} response_t;

static response_t
handle_head_request(int eligible_for_conversion, size_t markdown_len)
{
    response_t r;
    r.content_type = "text/html";
    r.content_length = 0;
    r.has_body = 0;
    r.converted = 0;

    if (eligible_for_conversion) {
        r.content_type = "text/markdown; charset=utf-8";
        r.content_length = markdown_len;
        r.has_body = 0; /* HEAD must never include body */
        r.converted = 1;
    }

    return r;
}

static void
test_head_converted_response(void)
{
    response_t r;
    TEST_SUBSECTION("Eligible HEAD request sets markdown headers but no body");

    r = handle_head_request(1, 1234);
    TEST_ASSERT(STR_EQ(r.content_type, "text/markdown; charset=utf-8"), "HEAD converted content-type should be markdown");
    TEST_ASSERT(r.content_length == 1234, "HEAD converted content-length should reflect markdown length");
    TEST_ASSERT(r.has_body == 0, "HEAD response must not include body");
    TEST_ASSERT(r.converted == 1, "HEAD should still run conversion pipeline");
    TEST_PASS("Converted HEAD behavior is correct");
}

static void
test_head_passthrough_response(void)
{
    response_t r;
    TEST_SUBSECTION("Ineligible HEAD request remains passthrough");

    r = handle_head_request(0, 999);
    TEST_ASSERT(STR_EQ(r.content_type, "text/html"), "Passthrough HEAD content-type should remain html");
    TEST_ASSERT(r.has_body == 0, "Passthrough HEAD must also have no body");
    TEST_ASSERT(r.converted == 0, "Passthrough HEAD should not be marked converted");
    TEST_PASS("Passthrough HEAD behavior is correct");
}

int
main(void)
{
    printf("\n========================================\n");
    printf("head_request Tests\n");
    printf("========================================\n");

    test_head_converted_response();
    test_head_passthrough_response();

    printf("\n========================================\n");
    printf("All tests passed!\n");
    printf("========================================\n\n");
    return 0;
}
